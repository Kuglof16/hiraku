// The two looks of the 3D view, chosen with ?look= (the default is "zu"):
//   zu    the diagram: unbleached paper and black ink, grey members, pale parts,
//         a ruled ground
//   real  a photograph-like view: soft sky light, sun shadows, materials (painted
//         steel, rubber, fabric, wood, earth)
// Every texture here is drawn in code on a canvas; no image is loaded.

import * as THREE from "three";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { GRAY } from "./colormap.js";

const ENV = new WeakMap(); // renderer -> environment texture

export function lookFromParams(params) {
  return params.get("look") === "real" ? "real" : "zu";
}

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

function canvasTexture(size, draw, repeat = 1) {
  const c = document.createElement("canvas");
  c.width = c.height = size;
  draw(c.getContext("2d"), size);
  const tex = new THREE.CanvasTexture(c);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(repeat, repeat);
  tex.anisotropy = 4;
  return tex;
}

// Earth: speckles and soft blotches of brown and grey.
function earthTexture() {
  const tex = canvasTexture(512, (g, n) => {
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
  });
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

// Tyre tread: chevron blocks across the width (a bump map: light = raised).
function treadTexture() {
  return canvasTexture(256, (g, n) => {
    g.fillStyle = "#202020";
    g.fillRect(0, 0, n, n);
    g.fillStyle = "#d8d8d8";
    for (let row = 0; row < 8; row += 1) {
      const y = (row / 8) * n;
      g.beginPath();
      g.moveTo(n * 0.06, y + n * 0.02);
      g.lineTo(n * 0.46, y + n * 0.07);
      g.lineTo(n * 0.46, y + n * 0.1);
      g.lineTo(n * 0.06, y + n * 0.1);
      g.closePath();
      g.fill();
      g.beginPath();
      g.moveTo(n * 0.94, y + n * 0.02);
      g.lineTo(n * 0.54, y + n * 0.07);
      g.lineTo(n * 0.54, y + n * 0.1);
      g.lineTo(n * 0.94, y + n * 0.1);
      g.closePath();
      g.fill();
    }
  });
}

// Woven fabric: fine cross lines with a little noise (a bump map).
function fabricTexture() {
  return canvasTexture(128, (g, n) => {
    const r = rng(11);
    g.fillStyle = "#808080";
    g.fillRect(0, 0, n, n);
    for (let i = 0; i < n; i += 4) {
      g.fillStyle = "rgba(255,255,255,0.35)";
      g.fillRect(0, i, n, 1);
      g.fillStyle = "rgba(0,0,0,0.3)";
      g.fillRect(i, 0, 1, n);
    }
    for (let i = 0; i < 1500; i += 1) {
      g.fillStyle = `rgba(${r() < 0.5 ? "0,0,0" : "255,255,255"},0.12)`;
      g.fillRect(r() * n, r() * n, 1, 1);
    }
  }, 6);
}

// Planks: long grain lines with knots.
function woodTexture() {
  const tex = canvasTexture(256, (g, n) => {
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
  });
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

// Diamond plate for floors and the deck (a bump map).
function plateTexture() {
  return canvasTexture(128, (g, n) => {
    g.fillStyle = "#606060";
    g.fillRect(0, 0, n, n);
    g.fillStyle = "#e0e0e0";
    for (let y = 0; y < 8; y += 1) {
      for (let x = 0; x < 8; x += 1) {
        g.save();
        g.translate((x + (y % 2) * 0.5) * (n / 8), (y + 0.5) * (n / 8));
        g.rotate(((y % 2 ? 1 : -1) * Math.PI) / 4);
        g.fillRect(-5, -1.5, 10, 3);
        g.restore();
      }
    }
  }, 8);
}

// Colours of the parts in the diagram look (pale, see-through).
const ZU_PART = { battery: "#e8c547", seat: "#e9b872", occupant: "#e9b872", motor: "#2fa7d6", controller: "#4cbf7a", cargo: "#c9c3b6" };

// Materials of the parts in the real look, by kind.
const REAL_PART = {
  battery: { color: "#2e3a48", roughness: 0.55, metalness: 0.2 },
  motor: { color: "#a9b0b6", roughness: 0.35, metalness: 0.85 },
  controller: { color: "#3b4046", roughness: 0.45, metalness: 0.6 },
  electrical: { color: "#4a4f55", roughness: 0.5, metalness: 0.4 },
  body: { color: "#59664a", roughness: 0.5, metalness: 0.25 },
  floor: { color: "#9aa0a6", roughness: 0.4, metalness: 0.8, bump: "plate" },
  steering: { color: "#1f2124", roughness: 0.6, metalness: 0.1 },
  seat: { color: "#2a2b2f", roughness: 0.95, metalness: 0, bump: "fabric" },
  occupant: { color: "#d8c7b0", roughness: 0.85, metalness: 0 },
  cargo: { color: "#ffffff", roughness: 0.85, metalness: 0, map: "wood" },
  suspension: { color: "#2f3438", roughness: 0.45, metalness: 0.7 },
  spring: { color: "#8e2f22", roughness: 0.4, metalness: 0.5 },
  frame: { color: "#3d4840", roughness: 0.5, metalness: 0.3 },
  tyre: { color: "#1d1e20", roughness: 0.92, metalness: 0, bump: "tread" },
  rim: { color: "#b7bcc2", roughness: 0.3, metalness: 0.9 },
  hub: { color: "#3a3d41", roughness: 0.5, metalness: 0.7 },
  default: { color: "#7d828a", roughness: 0.6, metalness: 0.3 },
};

export class Look {
  constructor(name) {
    this.name = name === "real" ? "real" : "zu";
    this.real = this.name === "real";
    this.textures = {};
    this.cache = {};
  }

  texture(key) {
    const make = { earth: earthTexture, tread: treadTexture, fabric: fabricTexture, wood: woodTexture, plate: plateTexture }[key];
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
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
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
    sun.shadow.mapSize.set(2048, 2048);
    sun.shadow.bias = -0.0004;
    sun.shadow.normalBias = 0.02;
    sun.shadow.radius = 4;
    scene.add(sun, sun.target);
    return sun;
  }

  // The sun follows the vehicle so its shadow box stays small (sharp shadows).
  aimSun(sun, center, radius) {
    if (!sun) return;
    sun.position.copy(center).add(new THREE.Vector3(2.5, 6, 3.5));
    sun.target.position.copy(center);
    const cam = sun.shadow.camera;
    const r = Math.max(1.5, radius * 1.4);
    cam.left = cam.bottom = -r;
    cam.right = cam.top = r;
    cam.near = 0.5;
    cam.far = 30;
    cam.updateProjectionMatrix();
  }

  standard(spec) {
    const m = new THREE.MeshStandardMaterial({ color: spec.color, roughness: spec.roughness, metalness: spec.metalness });
    if (spec.map) m.map = this.texture(spec.map);
    if (spec.bump) {
      m.bumpMap = this.texture(spec.bump);
      m.bumpScale = spec.bump === "tread" ? 6 : 1.5;
    }
    return m;
  }

  // Frame members (the contour colours them; in the real look the paint is the colour).
  memberMaterial() {
    if (!this.real) return new THREE.MeshLambertMaterial({ color: GRAY });
    return this.standard(REAL_PART.frame);
  }

  // A part's material by kind (sub picks a piece: "spring", "tyre", "rim", "hub", "floor").
  partMaterial(kind, sub = null) {
    if (this.real) {
      const spec = REAL_PART[sub ?? kind] ?? REAL_PART.default;
      const m = this.standard(spec);
      m.userData.baseColor = spec.color;
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

  // The ground plane of the real look (earth), spanning the box [x0, x1] x [y0, y1] in ISO mm.
  earth(x0, x1, y0, y1) {
    const tex = this.texture("earth").clone();
    tex.needsUpdate = true;
    tex.repeat.set((x1 - x0) / 2500, (y1 - y0) / 2500);
    const mat = new THREE.MeshStandardMaterial({ map: tex, roughness: 1, metalness: 0 });
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(x1 - x0, y1 - y0), mat);
    mesh.position.set((x0 + x1) / 2, (y0 + y1) / 2, -1);
    mesh.receiveShadow = true;
    return mesh;
  }
}
