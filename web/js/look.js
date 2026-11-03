// The two looks of the 3D view, chosen with ?look= (the default is "zu"):
//   zu    the diagram: unbleached paper and black ink, grey members, pale parts,
//         a ruled ground
//   real  a photograph-like view: outdoor sky light (image-based), sun shadows,
//         physically based materials (painted steel, body paint, black-coated steel,
//         aluminium, diamond plate, galvanised steel, rubber, fabric, vinyl, resin,
//         wood, earth, concrete), and part boxes with rounded edges (shapes.js)
//
// The real look's textures are drawn in code on a canvas. Poly Haven files (CC0)
// listed in ASSETS are used in their place when they are in web/assets/polyhaven/
// (put there by hand, see NOTES.md there; the server says which are there, /api/assets).
// Without them the sky light is three.js' RoomEnvironment and the ground and the
// concrete are the canvas drawings.
//
// ?quality= (high / medium / low) picks the cost of the real look (QUALITY).

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
  // a body panel's paint (painted sheet steel or moulded plastic): one calm colour, a thin clearcoat
  body_paint: { type: "physical", color: "#40535f", roughness: 0.42, metalness: 0, clearcoat: 0.35, clearcoatRoughness: 0.2 },
  // brackets: steel in a black satin coat
  black_steel: { type: "physical", color: "#1b1c1e", roughness: 0.55, metalness: 0, clearcoat: 0.15, clearcoatRoughness: 0.4 },
  // a seat's cover: vinyl (the fabric finish is the woven one)
  vinyl: { type: "physical", color: "#242528", roughness: 0.6, metalness: 0, clearcoat: 0.1, clearcoatRoughness: 0.5, sheen: 0.3, sheenRoughness: 0.5, sheenColor: "#4a4c52", normal: "fabric", normalScale: 0.15 },
  // a floor: anti-slip diamond plate (aluminium)
  tread_plate: { type: "standard", color: "#a9aeb3", roughness: 0.4, metalness: 1, normal: "plate", normalScale: 1 },
  aluminium: { type: "standard", color: "#b9bec4", roughness: 0.32, metalness: 1 },
  galvanized: { type: "standard", color: "#a3a9ad", roughness: 0.48, metalness: 1 },
  rubber: { type: "standard", color: "#1d1e20", roughness: 0.88, metalness: 0, normal: "tread", normalScale: 1 },
  fabric: { type: "physical", color: "#2a2b2f", roughness: 0.95, metalness: 0, sheen: 0.6, sheenRoughness: 0.6, sheenColor: "#5a5c63", normal: "fabric", normalScale: 0.6 },
  resin: { type: "physical", color: "#2b2e33", roughness: 0.42, metalness: 0, clearcoat: 0.3, clearcoatRoughness: 0.35 },
  skin: { type: "standard", color: "#d8c7b0", roughness: 0.85, metalness: 0 },
  wood: { type: "standard", color: "#ffffff", roughness: 0.85, metalness: 0, map: "wood" },
  concrete: { type: "standard", color: "#ffffff", roughness: 0.95, metalness: 0, surface: "concrete" },
};

// What each part and each frame member is made of in the real look: one table. An
// entry is { finish (a FINISH key), color? }; a colour here overrides the finish's own.
//
// A part's entry is decided in this order (partEntry):
//   1. part.material (a material's name, e.g. "SPCC", "A5052", "PP"): the rows of
//      `material` (first match) give the finish. A steel row (painted steel) keeps the
//      entry of step 3 when that is a paint too (a steel hood stays the body colour).
//   2. part.finish (a FINISH key), when given, replaces the finish of step 1 or 3.
//   3. kind and id: the first row of `id[kind]` whose pattern matches the id, else
//      `kind[kind]`, else `kind.default`.
// part.material and part.finish are not in the contract yet (2026-11-03); without them
// kind and id decide. A piece of a part's shape (shapes.js: tyre, rim, hub, spring)
// takes `sub[piece]`, whatever the part.
// A frame member's entry (memberEntry): `kind[member.part]` (e.g. "rollbar"), else
// `kind.frame`, then its material's name (design.materials[m.material].name) through the
// same `material` rows, by the same rule as step 1.
export const MATERIAL_OF = {
  sub: {
    tyre: { finish: "rubber" },
    rim: { finish: "aluminium" },
    hub: { finish: "galvanized", color: "#7e8489" },
    spring: { finish: "painted_steel", color: "#8e2f22" },
  },
  id: {
    body: [
      { match: /floor|deck|step/i, finish: "tread_plate" },
      { match: /mat\b|_mat/i, finish: "rubber", color: "#232426" },
      // hood, fender_FL / FR, side_panel_L / R, bulkhead, door, cowl and the rest: the paint
      { match: /./, finish: "body_paint" },
    ],
  },
  kind: {
    frame: { finish: "painted_steel" },
    // the roll cage (members of part "rollbar" and the rollbar part): painted tube, a colour apart from the frame
    rollbar: { finish: "painted_steel", color: "#c4c1b8" },
    bracket: { finish: "black_steel" },
    suspension: { finish: "painted_steel", color: "#2f3438" },
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
    wheel: { finish: "rubber" },
    // the backrest and the cushion are separate parts of kind seat: the same cover
    seat: { finish: "vinyl" },
    seat_belt: { finish: "fabric", color: "#1e1f22" },
    body: { finish: "body_paint" },
    battery: { finish: "resin", color: "#2e3a48" },
    electrical: { finish: "resin", color: "#3a3e44" },
    switch: { finish: "resin", color: "#b33a2a" },
    steering: { finish: "resin", color: "#1f2124" },
    occupant: { finish: "skin" },
    cargo: { finish: "wood" },
    default: { finish: "painted_steel", color: "#7d828a" },
  },
  // a material's name (part.material, or a member's design.materials name): words in it (first match)
  material: [
    { match: /alumin|A\d{4}\b/i, finish: "aluminium" },
    { match: /galvani|亜鉛|SGCC|ZAM/i, finish: "galvanized" },
    { match: /steel|STK|SS\d{3}|STKR|STKM|SPCC|SPHC|SAPH|鋼/i, finish: "painted_steel" },
    { match: /plastic|resin|樹脂|\bPP\b|\bABS\b|\bPE\b|\bPC\b|FRP|GFRP|CFRP|nylon|\bPA\d*\b/i, finish: "resin" },
    { match: /rubber|ゴム|EPDM/i, finish: "rubber" },
    { match: /vinyl|PVC|ビニル|leather|革/i, finish: "vinyl" },
    { match: /fabric|cloth|布|polyester/i, finish: "fabric" },
    { match: /wood|木|plywood|合板/i, finish: "wood" },
  ],
};

// The finishes that are a coat of paint on steel.
const PAINTS = new Set(["painted_steel", "body_paint", "black_steel"]);

// Step 1 and 2 of the order above, over the entry of kind and id (or of a member's part).
function refine(base, materialName, finish) {
  let entry = base;
  const row = materialName ? MATERIAL_OF.material.find((e) => e.match.test(materialName)) : null;
  if (row && !(row.finish === "painted_steel" && PAINTS.has(base.finish))) entry = { finish: row.finish, color: row.color };
  if (finish && FINISH[finish] && finish !== entry.finish) entry = { finish };
  return entry;
}

// The table's entry for a part ({ id, kind, material?, finish? }).
export function partEntry(part) {
  const rows = MATERIAL_OF.id[part.kind] ?? [];
  const byId = rows.find((r) => r.match.test(part.id ?? ""));
  const base = byId ? { finish: byId.finish, color: byId.color } : (MATERIAL_OF.kind[part.kind] ?? MATERIAL_OF.kind.default);
  return refine(base, part.material ?? null, part.finish ?? null);
}

// The table's entry for a frame member: its part ("frame", "rollbar") and its material's name.
export function memberEntry(materialName, part = null) {
  return refine(MATERIAL_OF.kind[part] ?? MATERIAL_OF.kind.frame, materialName ?? null, null);
}

// Colours of the parts in the diagram look (pale, see-through).
const ZU_PART = { battery: "#e8c547", seat: "#e9b872", occupant: "#e9b872", motor: "#2fa7d6", controller: "#4cbf7a", cargo: "#c9c3b6" };

const ENV = new WeakMap(); // renderer -> RoomEnvironment texture (the fall-back sky light)
const SKY = new WeakMap(); // renderer -> Promise of the HDRI's environment texture (or null)

export class Look {
  constructor(name, quality = DEFAULT_QUALITY) {
    this.name = name === "real" ? "real" : "zu";
    this.real = this.name === "real";
    this.quality = QUALITY[quality] ? quality : DEFAULT_QUALITY;
    this.q = QUALITY[this.quality];
    this.textures = {};
    this.surfaces = []; // { role, material, span_mm: [u, v] } waiting for an asset
    this.assets = { sky: false, dirt: false, concrete: false }; // in use
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
    this.assetsReady = this.loadAssets(renderer, scene);
    return sun;
  }

  // The Poly Haven files that are there take the place of the drawn ones.
  async loadAssets(renderer, scene) {
    const roles = await rolesPresent();
    if (roles.includes("sky")) {
      if (!SKY.has(renderer)) {
        SKY.set(
          renderer,
          new RGBELoader()
            .loadAsync(assetPath("sky", "hdr"))
            .then((hdr) => {
              const pmrem = new THREE.PMREMGenerator(renderer);
              const tex = pmrem.fromEquirectangular(hdr).texture;
              pmrem.dispose();
              hdr.dispose();
              return tex;
            })
            .catch(() => null),
        );
      }
      const tex = await SKY.get(renderer);
      if (tex) {
        scene.environment = tex;
        scene.environmentIntensity = 0.8;
        this.assets.sky = true;
      }
    }
    const loader = new THREE.TextureLoader();
    for (const role of ["dirt", "concrete"]) {
      if (!roles.includes(role)) continue;
      try {
        const files = ASSETS[role].files;
        const [map, normal, arm] = await Promise.all([files.map, files.normal, files.arm].map((_, i) => loader.loadAsync(assetPath(role, ["map", "normal", "arm"][i]))));
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
    const drawn = this.texture(role === "dirt" ? "earth" : "concrete").clone();
    drawn.repeat.set(span_mm[0] / drawnSide_mm, span_mm[1] / drawnSide_mm);
    drawn.needsUpdate = true;
    const material = new THREE.MeshStandardMaterial({ map: drawn, roughness: role === "dirt" ? 1 : 0.95, metalness: 0 });
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
  aimSun(sun, center, points) {
    if (!sun) return;
    sun.position.copy(center).add(new THREE.Vector3(2.5, 6, 3.5));
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
  memberMaterial(materialName = null, part = null) {
    if (!this.real) return new THREE.MeshLambertMaterial({ color: GRAY });
    return this.fromEntry(memberEntry(materialName, part));
  }

  // A part's material: `part` is the design's part (or just a kind); sub picks a piece
  // of its shape ("spring", "tyre", "rim", "hub").
  partMaterial(part, sub = null) {
    const p = typeof part === "string" ? { kind: part } : part;
    const kind = p.kind;
    if (this.real) {
      const entry = (sub && MATERIAL_OF.sub[sub]) || partEntry(p);
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

  // A ground that shows only the shadow falling on it (the page's background shows through).
  shadowCatcher(x0, x1, y0, y1) {
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(x1 - x0, y1 - y0), new THREE.ShadowMaterial({ opacity: 0.45 }));
    mesh.position.set((x0 + x1) / 2, (y0 + y1) / 2, -1);
    mesh.receiveShadow = true;
    return mesh;
  }

  // The ground plane of the real look (a dirt road), spanning the box [x0, x1] x [y0, y1] in ISO mm.
  earth(x0, x1, y0, y1) {
    const mat = this.surfaceMaterial("dirt", [x1 - x0, y1 - y0]);
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(x1 - x0, y1 - y0), mat);
    mesh.position.set((x0 + x1) / 2, (y0 + y1) / 2, -1);
    mesh.receiveShadow = true;
    return mesh;
  }
}
