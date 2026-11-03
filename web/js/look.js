// The two looks of the 3D view, chosen with ?look= (the default is "zu" on the workbench;
// on the video's cut pages the layout's look key of the cut, "real" without it: cutLookOf):
//   zu    the diagram: unbleached paper and black ink, grey members, pale parts,
//         a ruled ground
//   real  a photograph-like view: outdoor sky light (image-based), sun shadows,
//         physically based materials (painted steel, aluminium, galvanised steel,
//         rubber, fabric, resin, wood, earth, concrete)
//
// The real look's textures are drawn in code on a canvas. Poly Haven files (CC0)
// listed in ASSETS are used in their place when they are in web/assets/polyhaven/
// (put there by hand, see NOTES.md there; the server says which are there, /api/assets).
// Without them the sky light is three.js' RoomEnvironment and the ground and the
// concrete are the canvas drawings.
//
// ?quality= (high / medium / low) picks the cost of the real look (QUALITY).
//
// The video's cut pages draw their views as a stage (Look opts.stage): the real look
// there also shows the sky with its horizon (an HDRI) behind the vehicle, a haze that
// melts the ground's far edge into it, a gravel road with dirt stains, and a sun that
// stands behind the vehicle so its shadow falls toward the viewer. Which look a cut takes
// is the layout's `look` key of that cut ("real" when it has none; cutLookOf).

import * as THREE from "three";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { RGBELoader } from "three/addons/loaders/RGBELoader.js";
import { GRAY } from "./colormap.js";

// ---- assets ----------------------------------------------------------------
// The Poly Haven assets the real look reads, by role: id, the file of each map, and
// for a texture the side of the square it covers (mm). The sides are the assets'
// "dimensions" on api.polyhaven.com/info/<id> (read 2026-11-03).
// Files go to web/assets/polyhaven/<id>/<file>.
export const ASSET_DIR = "assets/polyhaven";
export const ASSETS = {
  // 1k: the 2k file is 5.45 MB (api.polyhaven.com/files), over the 5 MB a file may take here
  sky: { id: "kloofendal_48d_partly_cloudy_puresky", kind: "hdri", files: { hdr: "kloofendal_48d_partly_cloudy_puresky_1k.hdr" } },
  dirt: {
    id: "dirt_floor",
    kind: "texture",
    side_mm: 2070,
    files: { map: "dirt_floor_diff_1k.jpg", normal: "dirt_floor_nor_gl_1k.jpg", arm: "dirt_floor_arm_1k.jpg" },
  },
  // the cut views (stage): the sky seen behind the vehicle, with its horizon (1k: the 2k
  // file is 6.48 MB, api.polyhaven.com/files, over the 5 MB a file may take here)
  horizon: { id: "kloofendal_48d_partly_cloudy", kind: "hdri", files: { hdr: "kloofendal_48d_partly_cloudy_1k.hdr" } },
  // the cut views (stage): the road under the vehicle
  gravel: {
    id: "gravel_road",
    kind: "texture",
    side_mm: 2000,
    files: { map: "gravel_road_diff_1k.jpg", normal: "gravel_road_nor_gl_1k.jpg", arm: "gravel_road_arm_1k.jpg" },
  },
  concrete: {
    id: "brushed_concrete",
    kind: "texture",
    side_mm: 2500,
    files: { map: "brushed_concrete_diff_1k.jpg", normal: "brushed_concrete_nor_gl_1k.jpg", arm: "brushed_concrete_arm_1k.jpg" },
  },
};

export function assetPath(role, file) {
  const a = ASSETS[role];
  return `${ASSET_DIR}/${a.id}/${a.files[file]}`;
}

// The asset files there are (paths under web/assets/polyhaven/), asked once per page.
let presentFiles = null;
export function assetsPresent() {
  presentFiles ??= fetch("api/assets")
    .then((r) => (r.ok ? r.json() : { files: [] }))
    .then((d) => new Set((d.files ?? []).map((f) => `${ASSET_DIR}/${f}`)))
    .catch(() => new Set());
  return presentFiles;
}

// The roles whose every file is there.
async function rolesPresent() {
  const have = await assetsPresent();
  return Object.keys(ASSETS).filter((role) => Object.keys(ASSETS[role].files).every((f) => have.has(assetPath(role, f))));
}

// ---- quality ---------------------------------------------------------------
// ao: ambient occlusion (GTAO) at the view's resolution times `scale`, or none;
// msaa: samples of the render target the picture is drawn in (the antialiasing);
// shadow: the sun's shadow map side (px) and filter.
export const QUALITY = {
  high: { ao: { scale: 1 }, msaa: 4, shadow: { size: 2048, type: "pcfsoft" } },
  medium: { ao: { scale: 0.5 }, msaa: 4, shadow: { size: 2048, type: "pcfsoft" } },
  low: { ao: null, msaa: 0, shadow: { size: 1024, type: "pcf" } },
};
export const DEFAULT_QUALITY = "high";

export function qualityFromParams(params) {
  const q = params.get("quality");
  return q in QUALITY ? q : DEFAULT_QUALITY;
}

export function lookFromParams(params) {
  return params.get("look") === "real" ? "real" : "zu";
}

// The look of a cut: ?look= when it is given (to compare the two), else the layout's
// `look` key of the cut, else "real".
export function cutLookOf(layout, name, params = null) {
  const q = params?.get("look");
  if (q === "real" || q === "zu") return q;
  return layout?.[name]?.look === "zu" ? "zu" : "real";
}

// Every look's assets loaded (the cut pages wait for this before they say they are ready,
// so a frame at ?t= is the same every time).
const PENDING = new Set();
export async function looksReady() {
  while (PENDING.size) await Promise.all([...PENDING]);
}

// ---- canvas textures ---------------------------------------------------------
// A small deterministic random (the same frame every time).
function rng(seed) {
  let s = seed >>> 0 || 1;
  return () => {
    s ^= s << 13;
    s ^= s >>> 17;
    s ^= s << 5;
    return ((s >>> 0) % 100000) / 100000;
  };
}

function drawCanvas(w, h, draw) {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  draw(c.getContext("2d"), w, h);
  return c;
}

function canvasTexture(canvas, repeatU = 1, repeatV = repeatU) {
  const tex = new THREE.CanvasTexture(canvas);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(repeatU, repeatV);
  tex.anisotropy = 4;
  return tex;
}

// A tangent-space normal map (OpenGL convention: green = +v) from a height canvas
// (light = raised): the slope by central differences, wrapping at the edges.
function normalMapFrom(height, strength) {
  const w = height.width;
  const h = height.height;
  const src = height.getContext("2d").getImageData(0, 0, w, h).data;
  const H = (x, y) => src[(((y + h) % h) * w + ((x + w) % w)) * 4] / 255;
  const out = drawCanvas(w, h, () => {});
  const g = out.getContext("2d");
  const img = g.createImageData(w, h);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const du = (H(x + 1, y) - H(x - 1, y)) * strength;
      // canvas rows run down while v runs up (the texture is flipped on upload)
      const dv = (H(x, y - 1) - H(x, y + 1)) * strength;
      const len = Math.hypot(du, dv, 1);
      const i = (y * w + x) * 4;
      img.data[i] = Math.round((-du / len) * 127.5 + 127.5);
      img.data[i + 1] = Math.round((-dv / len) * 127.5 + 127.5);
      img.data[i + 2] = Math.round((1 / len) * 127.5 + 127.5);
      img.data[i + 3] = 255;
    }
  }
  g.putImageData(img, 0, 0);
  return out;
}

// Earth: speckles and soft blotches of brown and grey.
function earthTexture() {
  const tex = canvasTexture(
    drawCanvas(512, 512, (g, n) => {
      const r = rng(7);
      g.fillStyle = "#8a7a62";
      g.fillRect(0, 0, n, n);
      for (let i = 0; i < 260; i += 1) {
        const x = r() * n;
        const y = r() * n;
        const rad = 10 + r() * 50;
        const grad = g.createRadialGradient(x, y, 0, x, y, rad);
        const tone = r() < 0.5 ? "120,104,80" : "150,136,112";
        grad.addColorStop(0, `rgba(${tone},0.35)`);
        grad.addColorStop(1, `rgba(${tone},0)`);
        g.fillStyle = grad;
        g.fillRect(x - rad, y - rad, rad * 2, rad * 2);
      }
      for (let i = 0; i < 9000; i += 1) {
        const v = 70 + Math.floor(r() * 110);
        g.fillStyle = `rgba(${v},${v - 8},${v - 22},${0.25 + r() * 0.4})`;
        g.fillRect(r() * n, r() * n, 1 + r() * 2, 1 + r() * 2);
      }
    }),
  );
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

// Concrete: a pale grey with fine grit and faint trowel marks.
function concreteTexture() {
  const tex = canvasTexture(
    drawCanvas(256, 256, (g, n) => {
      const r = rng(31);
      g.fillStyle = "#a7a49c";
      g.fillRect(0, 0, n, n);
      for (let i = 0; i < 60; i += 1) {
        const x = r() * n;
        const y = r() * n;
        const rad = 8 + r() * 30;
        const grad = g.createRadialGradient(x, y, 0, x, y, rad);
        const tone = r() < 0.5 ? "140,137,130" : "185,182,174";
        grad.addColorStop(0, `rgba(${tone},0.3)`);
        grad.addColorStop(1, `rgba(${tone},0)`);
        g.fillStyle = grad;
        g.fillRect(x - rad, y - rad, rad * 2, rad * 2);
      }
      for (let i = 0; i < 5000; i += 1) {
        const v = 120 + Math.floor(r() * 90);
        g.fillStyle = `rgba(${v},${v},${v - 6},${0.15 + r() * 0.3})`;
        g.fillRect(r() * n, r() * n, 1, 1);
      }
    }),
  );
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

// Dirt stains over the road (the stage's ground): soft blotches of one value (red =
// how much stain), drawn once and laid over the road's own texture at a large scale, so
// the road's tiles do not repeat to the eye. Look values, not measured.
function stainTexture() {
  const tex = canvasTexture(
    drawCanvas(256, 256, (g, n) => {
      const r = rng(41);
      g.fillStyle = "#000000";
      g.fillRect(0, 0, n, n);
      for (let i = 0; i < 70; i += 1) {
        const x = r() * n;
        const y = r() * n;
        const rad = 12 + r() * 46;
        // drawn again across the edges, so the texture tiles
        for (const dx of [-n, 0, n]) {
          for (const dy of [-n, 0, n]) {
            const grad = g.createRadialGradient(x + dx, y + dy, 0, x + dx, y + dy, rad);
            grad.addColorStop(0, `rgba(255,255,255,${0.25 + r() * 0.3})`);
            grad.addColorStop(1, "rgba(255,255,255,0)");
            g.fillStyle = grad;
            g.fillRect(x + dx - rad, y + dy - rad, rad * 2, rad * 2);
          }
        }
      }
    }),
  );
  tex.colorSpace = THREE.NoColorSpace;
  return tex;
}

// Yellow and black stripes at 45 degrees (the painted edge of a step): bands 16 px wide
// every 32 px, so the 128 px square tiles. Look values.
function hazardTexture() {
  const tex = canvasTexture(
    drawCanvas(128, 128, (g, n) => {
      g.fillStyle = "#e8b81c";
      g.fillRect(0, 0, n, n);
      g.fillStyle = "#1b1b1b";
      for (let x = -n; x < 2 * n; x += 32) {
        g.beginPath();
        g.moveTo(x, 0);
        g.lineTo(x + 16, 0);
        g.lineTo(x + 16 + n, n);
        g.lineTo(x + n, n);
        g.closePath();
        g.fill();
      }
    }),
  );
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

// Tyre tread height: blocks around the tyre (u) in a chevron across its width (v).
function treadHeight() {
  return drawCanvas(256, 128, (g, w, h) => {
    g.fillStyle = "#ffffff";
    g.fillRect(0, 0, w, h);
    g.fillStyle = "#000000";
    const blocks = 8;
    for (let k = 0; k < blocks; k += 1) {
      const x = (k / blocks) * w;
      const gap = w / blocks;
      // a groove from each shoulder to the centre line, leaning the way the tyre turns
      for (const side of [-1, 1]) {
        g.beginPath();
        g.moveTo(x, h / 2 + side * h * 0.5);
        g.lineTo(x + gap * 0.45, h / 2 + side * h * 0.04);
        g.lineTo(x + gap * 0.62, h / 2 + side * h * 0.04);
        g.lineTo(x + gap * 0.17, h / 2 + side * h * 0.5);
        g.closePath();
        g.fill();
      }
    }
    g.fillRect(0, h / 2 - 2, w, 4); // the centre groove
  });
}

// Woven fabric height: over-under threads.
function fabricHeight() {
  return drawCanvas(64, 64, (g, n) => {
    const r = rng(11);
    g.fillStyle = "#808080";
    g.fillRect(0, 0, n, n);
    for (let y = 0; y < n; y += 4) {
      for (let x = 0; x < n; x += 4) {
        const up = ((x + y) / 4) % 2 === 0;
        const grad = up ? g.createLinearGradient(x, 0, x + 4, 0) : g.createLinearGradient(0, y, 0, y + 4);
        grad.addColorStop(0, "#606060");
        grad.addColorStop(0.5, "#d0d0d0");
        grad.addColorStop(1, "#606060");
        g.fillStyle = grad;
        g.fillRect(x, y, 4, 4);
      }
    }
    for (let i = 0; i < 300; i += 1) {
      g.fillStyle = `rgba(${r() < 0.5 ? "0,0,0" : "255,255,255"},0.1)`;
      g.fillRect(r() * n, r() * n, 1, 1);
    }
  });
}

// Diamond plate height (floors and the deck).
function plateHeight() {
  return drawCanvas(128, 128, (g, n) => {
    g.fillStyle = "#404040";
    g.fillRect(0, 0, n, n);
    g.fillStyle = "#ffffff";
    for (let y = 0; y < 8; y += 1) {
      for (let x = 0; x < 8; x += 1) {
        g.save();
        g.translate((x + (y % 2) * 0.5) * (n / 8), (y + 0.5) * (n / 8));
        g.rotate(((y % 2 ? 1 : -1) * Math.PI) / 4);
        g.fillRect(-5, -1.5, 10, 3);
        g.restore();
      }
    }
  });
}

// Planks: long grain lines with knots.
function woodTexture() {
  const tex = canvasTexture(
    drawCanvas(256, 256, (g, n) => {
      const r = rng(23);
      g.fillStyle = "#a27b4f";
      g.fillRect(0, 0, n, n);
      for (let i = 0; i < 70; i += 1) {
        const y = r() * n;
        g.strokeStyle = `rgba(${90 + r() * 40},${60 + r() * 25},${30 + r() * 20},0.35)`;
        g.lineWidth = 1 + r() * 2;
        g.beginPath();
        g.moveTo(0, y);
        g.bezierCurveTo(n * 0.3, y + (r() - 0.5) * 8, n * 0.7, y + (r() - 0.5) * 8, n, y);
        g.stroke();
      }
      for (let i = 0; i < 4; i += 1) {
        g.strokeStyle = "rgba(80,50,25,0.5)";
        g.beginPath();
        g.ellipse(r() * n, r() * n, 6 + r() * 6, 3 + r() * 3, 0, 0, Math.PI * 2);
        g.stroke();
      }
      g.fillStyle = "rgba(60,40,20,0.6)";
      for (const y of [0, n / 2]) g.fillRect(0, y, n, 2);
    }),
  );
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

// The canvas textures by name; normal maps are made from a height drawing.
const TEXTURES = {
  earth: () => earthTexture(),
  stain: () => stainTexture(),
  hazard: () => hazardTexture(),
  concrete: () => concreteTexture(),
  wood: () => woodTexture(),
  tread: () => canvasTexture(normalMapFrom(treadHeight(), 6), 6, 1), // 48 blocks round the tyre
  fabric: () => canvasTexture(normalMapFrom(fabricHeight(), 3), 8),
  plate: () => canvasTexture(normalMapFrom(plateHeight(), 4), 8),
};

// ---- materials -------------------------------------------------------------
// Finishes of the real look. These are look choices (how the surface reads in the
// picture), not measured properties of the parts.
//   type: "physical" (MeshPhysicalMaterial) or "standard" (MeshStandardMaterial)
//   normal: a canvas normal map (TEXTURES) and its strength
//   surface: a ground-like texture that a Poly Haven asset can replace (ASSETS)
const FINISH = {
  painted_steel: { type: "physical", color: "#3d4840", roughness: 0.5, metalness: 0, clearcoat: 1, clearcoatRoughness: 0.12 },
  aluminium: { type: "standard", color: "#b9bec4", roughness: 0.32, metalness: 1 },
  galvanized: { type: "standard", color: "#a3a9ad", roughness: 0.48, metalness: 1 },
  rubber: { type: "standard", color: "#1d1e20", roughness: 0.88, metalness: 0, normal: "tread", normalScale: 1 },
  fabric: { type: "physical", color: "#2a2b2f", roughness: 0.95, metalness: 0, sheen: 0.6, sheenRoughness: 0.6, sheenColor: "#5a5c63", normal: "fabric", normalScale: 0.6 },
  resin: { type: "physical", color: "#2b2e33", roughness: 0.42, metalness: 0, clearcoat: 0.3, clearcoatRoughness: 0.35 },
  skin: { type: "standard", color: "#d8c7b0", roughness: 0.85, metalness: 0 },
  wood: { type: "standard", color: "#ffffff", roughness: 0.85, metalness: 0, map: "wood" },
  concrete: { type: "standard", color: "#ffffff", roughness: 0.95, metalness: 0, surface: "concrete" },
};

// What each part (by kind, or by the piece's sub: tyre, rim, hub, spring, floor)
// and each frame member (by its material's name) is made of: one table. A colour
// here overrides the finish's own.
export const MATERIAL_OF = {
  kind: {
    frame: { finish: "painted_steel" },
    rollbar: { finish: "painted_steel" },
    suspension: { finish: "painted_steel", color: "#2f3438" },
    spring: { finish: "painted_steel", color: "#8e2f22" },
    hub: { finish: "galvanized", color: "#7e8489" },
    tie_rod: { finish: "galvanized" },
    knuckle: { finish: "galvanized", color: "#8a9094" },
    driveshaft: { finish: "galvanized" },
    steering_column: { finish: "galvanized" },
    steering_shaft: { finish: "galvanized" },
    steering_rack: { finish: "aluminium" },
    motor: { finish: "aluminium", color: "#a9b0b6" },
    reducer: { finish: "aluminium" },
    controller: { finish: "aluminium", color: "#5d6369" },
    brake_caliper: { finish: "aluminium", color: "#8d2a24" },
    brake_master_cylinder: { finish: "aluminium" },
    accelerator_pedal: { finish: "aluminium" },
    brake_pedal: { finish: "aluminium" },
    rim: { finish: "aluminium" },
    floor: { finish: "galvanized", color: "#9aa0a6", normal: "plate" },
    tyre: { finish: "rubber" },
    wheel: { finish: "rubber" },
    seat: { finish: "fabric" },
    seat_belt: { finish: "fabric", color: "#1e1f22" },
    body: { finish: "resin", color: "#59664a" },
    battery: { finish: "resin", color: "#2e3a48" },
    electrical: { finish: "resin", color: "#3a3e44" },
    switch: { finish: "resin", color: "#b33a2a" },
    steering: { finish: "resin", color: "#1f2124" },
    occupant: { finish: "skin" },
    cargo: { finish: "wood" },
    default: { finish: "painted_steel", color: "#7d828a" },
  },
  // a frame member's material, by words in its name in design.materials (first match)
  member: [
    { match: /alumin|A\d{4}\b/i, finish: "aluminium" },
    { match: /galvani|亜鉛|SGCC|ZAM/i, finish: "galvanized" },
    { match: /steel|STK|SS\d{3}|STKR|STKM|鋼/i, finish: "painted_steel" },
  ],
  memberDefault: { finish: "painted_steel" },
};

// The table's entry for a member (design.materials[m.material].name).
export function memberEntry(materialName) {
  const hit = MATERIAL_OF.member.find((e) => e.match.test(materialName ?? ""));
  return hit ? { finish: hit.finish } : MATERIAL_OF.memberDefault;
}

// Colours of the parts in the diagram look (pale, see-through).
const ZU_PART = { battery: "#e8c547", seat: "#e9b872", occupant: "#e9b872", motor: "#2fa7d6", controller: "#4cbf7a", cargo: "#c9c3b6" };

const ENV = new WeakMap(); // renderer -> RoomEnvironment texture (the fall-back sky light)
const HDRI = new WeakMap(); // renderer -> Map(role -> Promise of { tex, el, az, haze } or null)
const TEX = new Map(); // url -> Promise of the loaded texture (one source per file, shared by every view)

function loadTexture(url) {
  if (!TEX.has(url)) TEX.set(url, new THREE.TextureLoader().loadAsync(url));
  return TEX.get(url);
}

// From an HDRI's own pixels: its brightest pixel (the sun) as an elevation and an azimuth
// (rad; the azimuth as three.js' equirectangular lookup reads it, atan2(z, x)), and the mean
// colour (linear) of its horizon line, the rows from 1 degree below it to 2 above (the far
// land and the sky over it: the colour the far ground melts into where the photograph's
// own horizon takes over).
// RGBELoader flips the rows (the file's first row, the top, is v = 1: up).
export function readHdri(image) {
  const { data, width: W, height: H } = image;
  const half = data instanceof Uint16Array;
  const val = half ? (i) => THREE.DataUtils.fromHalfFloat(data[i]) : (i) => data[i];
  const ch = Math.round(data.length / (W * H));
  let best = -1;
  let brightest = 0;
  for (let k = 0; k < W * H; k += 1) {
    const i = k * ch;
    const lum = 0.2126 * val(i) + 0.7152 * val(i + 1) + 0.0722 * val(i + 2);
    if (lum > best) {
      best = lum;
      brightest = k;
    }
  }
  const row = Math.floor(brightest / W);
  const col = brightest % W;
  const sum = [0, 0, 0];
  let n = 0;
  for (let r = 0; r < H; r += 1) {
    const deg = (0.5 - (r + 0.5) / H) * 180;
    if (deg < -1 || deg > 2) continue;
    for (let c = 0; c < W; c += 1) {
      const i = (r * W + c) * ch;
      sum[0] += val(i);
      sum[1] += val(i + 1);
      sum[2] += val(i + 2);
      n += 1;
    }
  }
  return {
    el: (0.5 - (row + 0.5) / H) * Math.PI,
    az: ((col + 0.5) / W - 0.5) * 2 * Math.PI,
    haze: sum.map((v) => v / Math.max(1, n)),
  };
}

// An HDRI as an environment (PMREM) texture, once per renderer and file; the horizon's is
// also read (readHdri) for the stage.
function hdriOf(renderer, role) {
  let m = HDRI.get(renderer);
  if (!m) HDRI.set(renderer, (m = new Map()));
  if (!m.has(role)) {
    m.set(
      role,
      new RGBELoader()
        .loadAsync(assetPath(role, "hdr"))
        .then((hdr) => {
          const read = role === "horizon" ? readHdri(hdr.image) : null;
          const pmrem = new THREE.PMREMGenerator(renderer);
          const tex = pmrem.fromEquirectangular(hdr).texture;
          pmrem.dispose();
          hdr.dispose();
          return { tex, ...read };
        })
        .catch(() => null),
    );
  }
  return m.get(role);
}

// The stage (the cut views). Look values, chosen for the picture:
//   SUN_ASIDE   the sun's azimuth from straight behind the vehicle (as the camera sees it):
//               its shadow falls toward the viewer and a little to the side
//   SUN_EL      the sun's height without the HDRI (with it: the HDRI's own sun, kept within SUN_EL_RANGE)
//   HAZE        the haze (three.js Fog) from near_m past the camera's target to far_m past it
//   GROUND_MM   half the side of the ground (ISO mm round the vehicle), to where the haze has hidden it
//   STAIN_SIDE  the side (mm) of the dirt stains' square on the road
const SUN_ASIDE = (40 * Math.PI) / 180;
const SUN_EL = (50 * Math.PI) / 180;
const SUN_EL_RANGE = [(35 * Math.PI) / 180, (65 * Math.PI) / 180];
export const HAZE = { near_m: 8, far_m: 70 };
export const GROUND_MM = 150000;
const STAIN_SIDE_MM = 9000;
// the side (mm) of the hazard stripes' square: four yellow and black pairs, 100 mm each
const HAZARD_SIDE_MM = 400;
// how far (mm) the paint reaches down a step's face and back over its top (look values)
export const HAZARD_BAND_MM = 100;
// the stain's colour (it multiplies the road's) where the stain texture is full, and its
// second, coarser layer's scale and offset (so the pattern does not repeat to the eye)
const STAIN_GLSL = `
  float stainK = clamp(texture2D(stainMap, vStainUv).r * 0.7 + texture2D(stainMap, vStainUv * 0.37 + 0.31).r * 0.6, 0.0, 1.0);
  diffuseColor.rgb *= mix(vec3(1.0), vec3(0.62, 0.53, 0.42), stainK);
`;

export class Look {
  // opts.stage: the cut views' stage (see the top of the file)
  constructor(name, quality = DEFAULT_QUALITY, opts = {}) {
    this.name = name === "real" ? "real" : "zu";
    this.real = this.name === "real";
    this.stage = !!opts.stage && this.real;
    this.quality = QUALITY[quality] ? quality : DEFAULT_QUALITY;
    this.q = QUALITY[this.quality];
    this.textures = {};
    this.surfaces = []; // { role, material, span_mm: [u, v] } waiting for an asset
    // in use; the stage's ground is the gravel road and its sky the horizon
    this.groundRole = this.stage ? "gravel" : "dirt";
    this.assets = this.stage ? { sky: false, horizon: false, gravel: false, concrete: false } : { sky: false, dirt: false, concrete: false };
    this.backdrop = null; // the stage's sky: { tex, el, az, haze } once the HDRI is loaded
    this.fog = null;
    this.assetsReady = Promise.resolve();
  }

  texture(key) {
    const make = TEXTURES[key];
    if (!make) return null;
    this.textures[key] ??= make();
    return this.textures[key];
  }

  // Renderer, lights and environment of this look.
  setUp(renderer, scene) {
    if (!this.real) {
      scene.add(new THREE.HemisphereLight(0xffffff, 0xd9d2c3, 2.4));
      const sun = new THREE.DirectionalLight(0xffffff, 1.4);
      sun.position.set(3, 6, 2);
      scene.add(sun);
      return null;
    }
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = this.q.shadow.type === "pcfsoft" ? THREE.PCFSoftShadowMap : THREE.PCFShadowMap;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    // one environment per renderer, shared by every view drawn through it
    if (!ENV.has(renderer)) {
      const pmrem = new THREE.PMREMGenerator(renderer);
      ENV.set(renderer, pmrem.fromScene(new RoomEnvironment(), 0.04).texture);
      pmrem.dispose();
    }
    scene.environment = ENV.get(renderer);
    scene.environmentIntensity = 0.55;
    scene.add(new THREE.HemisphereLight(0xdfe8f2, 0x6b5b45, 1.1));
    const sun = new THREE.DirectionalLight(0xfff4e5, 2.6);
    sun.castShadow = true;
    sun.shadow.mapSize.set(this.q.shadow.size, this.q.shadow.size);
    scene.add(sun, sun.target);
    const ready = this.loadAssets(renderer, scene);
    this.assetsReady = ready;
    PENDING.add(ready);
    ready.finally(() => PENDING.delete(ready));
    return sun;
  }

  // The Poly Haven files that are there take the place of the drawn ones. On the stage the
  // HDRI with the horizon lights the scene and is seen behind it (else the sky only lights it).
  async loadAssets(renderer, scene) {
    const roles = await rolesPresent();
    for (const role of this.stage ? ["horizon", "sky"] : ["sky"]) {
      if (!roles.includes(role)) continue;
      const got = await hdriOf(renderer, role);
      if (!got) continue;
      scene.environment = got.tex;
      scene.environmentIntensity = 0.8;
      this.assets[role] = true;
      if (role === "horizon") {
        this.backdrop = got;
        this.fog = new THREE.Fog(new THREE.Color().setRGB(...got.haze, THREE.LinearSRGBColorSpace), HAZE.near_m, HAZE.far_m);
      }
      break;
    }
    for (const role of [this.groundRole, "concrete"]) {
      if (!roles.includes(role)) continue;
      try {
        const [map, normal, arm] = await Promise.all(["map", "normal", "arm"].map((f) => loadTexture(assetPath(role, f))));
        map.colorSpace = THREE.SRGBColorSpace;
        for (const t of [map, normal, arm]) {
          t.wrapS = t.wrapT = THREE.RepeatWrapping;
          t.anisotropy = 8;
        }
        this.textures[`asset:${role}`] = { map, normal, arm };
        this.assets[role] = true;
        for (const s of this.surfaces) if (s.role === role) this.applyAsset(s);
      } catch {
        // keep the drawn texture
      }
    }
  }

  // The stage at each frame (camera and target in three.js world, m): the sky behind and
  // the haze (not on a see-through overlay view, tinted), the sky turned so that its sun
  // stands where the sun light is. Returns the sun's direction (unit, from the target).
  stageFrame(scene, camera, target, tinted = false) {
    const b = this.backdrop;
    scene.background = !tinted && b ? b.tex : null;
    scene.fog = !tinted && b ? this.fog : null;
    const away = target.clone().sub(camera.position);
    away.y = 0;
    if (away.lengthSq() < 1e-12) away.set(-1, 0, 0);
    const az = Math.atan2(away.z, away.x) + SUN_ASIDE;
    const el = b ? Math.min(SUN_EL_RANGE[1], Math.max(SUN_EL_RANGE[0], b.el)) : SUN_EL;
    if (b) {
      // three.js turns a direction by the rotation's opposite before it looks the sky up, so a
      // turn of (the sky's sun azimuth - the wanted azimuth) about y brings the sky's sun there
      scene.backgroundRotation.set(0, b.az - az, 0);
      scene.environmentRotation.set(0, b.az - az, 0);
    }
    if (scene.fog) {
      const d = camera.position.distanceTo(target);
      scene.fog.near = d + HAZE.near_m;
      scene.fog.far = d + HAZE.far_m;
    }
    return new THREE.Vector3(Math.cos(az) * Math.cos(el), Math.sin(el), Math.sin(az) * Math.cos(el));
  }

  // The stage's road: dirt stains laid over its texture (STAIN_GLSL) at their own scale.
  addStain(material, span_mm) {
    const stain = this.texture("stain");
    const repeat = new THREE.Vector2(span_mm[0] / STAIN_SIDE_MM, span_mm[1] / STAIN_SIDE_MM);
    material.onBeforeCompile = (sh) => {
      sh.uniforms.stainMap = { value: stain };
      sh.uniforms.stainRepeat = { value: repeat };
      sh.vertexShader = sh.vertexShader
        .replace("#include <common>", "#include <common>\nuniform vec2 stainRepeat;\nvarying vec2 vStainUv;")
        .replace("#include <uv_vertex>", "#include <uv_vertex>\nvStainUv = uv * stainRepeat;");
      sh.fragmentShader = sh.fragmentShader
        .replace("#include <common>", "#include <common>\nuniform sampler2D stainMap;\nvarying vec2 vStainUv;")
        .replace("#include <map_fragment>", "#include <map_fragment>\n" + STAIN_GLSL);
    };
    material.customProgramCacheKey = () => "stain";
    material.userData.stain = true;
  }

  // Put an asset's maps on a surface material (arm: ambient occlusion, roughness,
  // metalness in r, g, b, as three.js reads them).
  applyAsset(s) {
    const set = this.textures[`asset:${s.role}`];
    if (!set) return;
    const side = ASSETS[s.role].side_mm;
    const m = s.material;
    const rep = (t) => {
      const c = t.clone();
      c.repeat.set(s.span_mm[0] / side, s.span_mm[1] / side);
      c.needsUpdate = true;
      return c;
    };
    m.map = rep(set.map);
    m.normalMap = rep(set.normal);
    m.aoMap = rep(set.arm);
    m.roughnessMap = m.aoMap;
    m.metalnessMap = m.aoMap;
    m.color.set("#ffffff");
    m.roughness = 1;
    m.metalness = 1;
    m.needsUpdate = true;
  }

  // A ground-like surface (earth or concrete) whose uv [0, 1] spans span_mm: the drawn
  // texture now, the asset's maps once they are loaded.
  surfaceMaterial(role, span_mm, drawnSide_mm = 2500) {
    const drawn = this.texture(role === "concrete" ? "concrete" : "earth").clone();
    drawn.repeat.set(span_mm[0] / drawnSide_mm, span_mm[1] / drawnSide_mm);
    drawn.needsUpdate = true;
    const material = new THREE.MeshStandardMaterial({ map: drawn, roughness: role === "concrete" ? 0.95 : 1, metalness: 0 });
    if (this.stage && role === this.groundRole) this.addStain(material, span_mm);
    this.addSurface({ role, material, span_mm });
    return material;
  }

  // A surface waits for its asset until its material is disposed.
  addSurface(s) {
    this.surfaces.push(s);
    s.material.addEventListener("dispose", () => {
      this.surfaces = this.surfaces.filter((x) => x !== s);
    });
    if (this.assets[s.role]) this.applyAsset(s);
  }

  // The sun follows the vehicle and its shadow box is fitted to the vehicle: the
  // points (three.js world, m) seen from the sun give the box's sides; its depth runs
  // from in front of them to the ground behind them. The biases follow the box's
  // texel: the normal offset is 1.5 texels, the depth bias 2 texels of depth.
  // dir: the sun's direction (unit, three.js world) when the stage sets it (stageFrame).
  aimSun(sun, center, points, dir = null) {
    if (!sun) return;
    sun.position.copy(center).add(dir ? dir.clone().multiplyScalar(7.4) : new THREE.Vector3(2.5, 6, 3.5));
    sun.target.position.copy(center);
    sun.target.updateMatrixWorld();
    const cam = sun.shadow.camera;
    cam.position.copy(sun.position);
    cam.lookAt(center);
    cam.updateMatrixWorld();
    const inv = cam.matrixWorldInverse;
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity, zNear = Infinity, zFar = -Infinity;
    const p = new THREE.Vector3();
    for (const q of points) {
      p.copy(q).applyMatrix4(inv);
      x0 = Math.min(x0, p.x);
      x1 = Math.max(x1, p.x);
      y0 = Math.min(y0, p.y);
      y1 = Math.max(y1, p.y);
      zNear = Math.min(zNear, -p.z);
      zFar = Math.max(zFar, -p.z);
    }
    const pad = 0.1; // m round the vehicle (the shadow's soft edge)
    cam.left = x0 - pad;
    cam.right = x1 + pad;
    cam.bottom = y0 - pad;
    cam.top = y1 + pad;
    cam.near = Math.max(0.05, zNear - 1);
    // the ground under the vehicle lies behind it along the sun's rays: as far again as
    // the vehicle's own depth from the sun (sun height 6 m above the target)
    cam.far = zFar + 8;
    cam.updateProjectionMatrix();
    const texel = Math.max(cam.right - cam.left, cam.top - cam.bottom) / sun.shadow.mapSize.x;
    sun.shadow.normalBias = 1.5 * texel;
    sun.shadow.bias = -(2 * texel) / (cam.far - cam.near);
  }

  // A material from the table's entry ({ finish, color?, normal? }).
  fromEntry(entry) {
    const f = FINISH[entry.finish] ?? FINISH.painted_steel;
    const params = { color: entry.color ?? f.color, roughness: f.roughness, metalness: f.metalness };
    let m;
    if (f.type === "physical") {
      m = new THREE.MeshPhysicalMaterial({
        ...params,
        clearcoat: f.clearcoat ?? 0,
        clearcoatRoughness: f.clearcoatRoughness ?? 0,
        sheen: f.sheen ?? 0,
        sheenRoughness: f.sheenRoughness ?? 1,
        sheenColor: new THREE.Color(f.sheenColor ?? "#000000"),
      });
    } else {
      m = new THREE.MeshStandardMaterial(params);
    }
    if (f.map) m.map = this.texture(f.map);
    const normal = entry.normal ?? f.normal;
    if (normal) {
      m.normalMap = this.texture(normal);
      const s = f.normalScale ?? 1;
      m.normalScale.set(s, s);
    }
    if (f.surface) {
      m.map = this.texture(f.surface);
      this.addSurface({ role: f.surface, material: m, span_mm: [ASSETS[f.surface].side_mm, ASSETS[f.surface].side_mm] });
    }
    m.userData.finish = entry.finish;
    return m;
  }

  // A material from a spec of the scene's props ({ color, roughness, metalness, bump? }
  // or { finish }).
  standard(spec) {
    if (spec.finish) return this.fromEntry(spec);
    const m = new THREE.MeshStandardMaterial({ color: spec.color, roughness: spec.roughness, metalness: spec.metalness });
    if (spec.map) m.map = this.texture(spec.map);
    if (spec.bump) {
      m.normalMap = this.texture(spec.bump);
      m.normalScale.set(0.8, 0.8);
    }
    return m;
  }

  // Frame members (the contour colours them; in the real look the paint is the colour).
  memberMaterial(materialName = null) {
    if (!this.real) return new THREE.MeshLambertMaterial({ color: GRAY });
    return this.fromEntry(memberEntry(materialName));
  }

  // A part's material by kind (sub picks a piece: "spring", "tyre", "rim", "hub", "floor").
  partMaterial(kind, sub = null) {
    if (this.real) {
      const entry = MATERIAL_OF.kind[sub ?? kind] ?? MATERIAL_OF.kind.default;
      const m = this.fromEntry(entry);
      m.userData.baseColor = "#" + m.color.getHexString();
      return m;
    }
    if (sub === "tyre" || sub === "rim" || sub === "hub") return new THREE.MeshLambertMaterial({ color: "#fbf9f4" });
    const color = ZU_PART[kind] ?? "#c9c3b6";
    const opacity = kind === "occupant" ? 0.8 : 0.32;
    const m = new THREE.MeshLambertMaterial({ color, transparent: true, opacity, depthWrite: false });
    m.userData.baseOpacity = opacity;
    m.userData.baseColor = color;
    return m;
  }

  // The painted edge of a step (hazardTexture's yellow and black stripes): a material whose
  // uv [0, 1] spans span_mm. Look values (paint for the picture, not from the record).
  hazardMaterial(span_mm) {
    const tex = this.texture("hazard").clone();
    tex.repeat.set(span_mm[0] / HAZARD_SIDE_MM, span_mm[1] / HAZARD_SIDE_MM);
    tex.needsUpdate = true;
    const m = new THREE.MeshStandardMaterial({ map: tex, roughness: 0.6, metalness: 0, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 });
    m.userData.paint = "hazard";
    return m;
  }

  // A ground that shows only the shadow falling on it (the page's background shows through).
  shadowCatcher(x0, x1, y0, y1) {
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(x1 - x0, y1 - y0), new THREE.ShadowMaterial({ opacity: 0.45 }));
    mesh.position.set((x0 + x1) / 2, (y0 + y1) / 2, -1);
    mesh.receiveShadow = true;
    return mesh;
  }

  // The ground plane of the real look (a dirt road; on the stage a gravel road), spanning the
  // box [x0, x1] x [y0, y1] in ISO mm.
  earth(x0, x1, y0, y1) {
    const mat = this.surfaceMaterial(this.groundRole, [x1 - x0, y1 - y0]);
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(x1 - x0, y1 - y0), mat);
    mesh.position.set((x0 + x1) / 2, (y0 + y1) / 2, -1);
    mesh.receiveShadow = true;
    return mesh;
  }
}
