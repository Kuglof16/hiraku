// The 3D pets in the corners of the N04 / N15 tiles (layout.json <cut>.tile.pet: the
// box at the tile's bottom right, tile.pet.feature its size in the large tile), and the
// drawing they share with the app's agents (PetStage; js/agent_pets.js).
//
// The pets are the promo department's promo/pets/pet3d.js, served read only by the
// screen's server at /pets/pet3d.js (hiraku.webserve --pets). It is a plain script that
// sets window.HirakuPets; without it (404) the cut draws no pets and works as before.
// One tile's pet is the role of its domain: cae.<domain>, packager for assembly.
//
// Drawing (as promo/screens/n00.html): one WebGL renderer off the page draws each pet
// alone, through a camera fitted to pet3d's FRAME box (n00.camera's field of view,
// azimuth and elevation), and the frame is copied into that pet's own 2D canvas, so no
// pet reaches past its box. A pet's pose depends only on the cut time t and its state.
//
// How they move: a pet holds still (idle, one fixed pose) and reacts only for a short
// time when its tile changes colour: pass when it turns green, mismatch when it turns
// red, for n00.motion.pass_dur_s from the change, its clock shifted so the pass hop
// starts from the ground (n00.motion.hop_period_s). A tile mid iteration (its domain
// judged pending in the version shown) has its pet working. The pets are not counted
// as moving (the story's limits say why), so their canvases carry no data-kind.

import * as THREE from "three";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

export const PETS_URL = "/pets/pet3d.js";

// Keys read from layout.json besides the cut's tile.pet box (dotted paths).
export const PET_KEYS = ["n00.camera.fov_deg", "n00.camera.azimuth_deg", "n00.camera.elevation_deg", "n00.motion.pass_dur_s", "n00.motion.hop_period_s", "n00.motion.idle_shift_s"];

const at = (obj, path) => path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);

// The role of a tile's domain (pet3d.js ROLES ids).
export const roleOf = (domain) => (domain === "assembly" ? "packager" : `cae.${domain}`);

let loading = null;
// window.HirakuPets once /pets/pet3d.js has run, else null (the file is not served).
export function loadPets() {
  if (window.HirakuPets) return Promise.resolve(window.HirakuPets);
  loading ??= new Promise((resolve) => {
    const s = document.createElement("script");
    s.src = PETS_URL;
    s.onload = () => resolve(window.HirakuPets ?? null);
    s.onerror = () => resolve(null);
    document.head.append(s);
  });
  return loading;
}

// The state of a tile's pet at t from its tile's colour over time: changes is a list of
// { at, state } (the time the tile turns to that colour state, in order; the first is its
// colour from the start), react the length of a reaction (s). Returns { state, since }:
// pass / mismatch for react seconds after a change to green / red, working while the
// colour is pending, else idle; since is when the current reaction started.
export function petStateAt(changes, t, react) {
  let cur = null;
  for (const c of changes) if (c.at <= t) cur = c;
  if (!cur) return { state: "idle", since: null };
  if (cur.state === "pending") return { state: "working", since: cur.at };
  if (cur.at === null || t - cur.at >= react) return { state: "idle", since: null };
  if (cur.state === "green") return { state: "pass", since: cur.at };
  if (cur.state === "red") return { state: "mismatch", since: cur.at };
  return { state: "idle", since: null };
}

// The pet3d time of a pose: idle, one fixed pose idleT; otherwise the state's own clock
// from since, shifted to whole hop periods so a pass hop starts from the ground at since.
export function poseTime(state, since, t, idleT, hop) {
  return state === "idle" ? idleT : Math.ceil(since / hop + 1) * hop + (t - since);
}

// One WebGL renderer off the page and the paper setting of promo/screens/n00.html: draws
// one pet at a time through a camera fitted to pet3d's FRAME box and copies the frame into
// that pet's own 2D canvas. Shared by the cut's tiles (TilePets) and the app's agents
// (agent_pets.js). cam: layout.json n00.camera; size: the largest canvas { W, H } in px
// at scale 1; shadow: the key light's shadow map (px a side; small pets need less); lite:
// pet3d.js v9's light pets; antialias: the renderer's (the cut keeps both defaults).
export class PetStage {
  constructor(HP, cam, size, { shadow = 1024, lite = false, antialias = true } = {}) {
    this.HP = HP;
    this.cam = cam;
    this.size = { W: size.W, H: size.H };
    this.scale = 1;
    this.lite = lite;
    const r = new THREE.WebGLRenderer({ antialias, alpha: true, preserveDrawingBuffer: true });
    r.setPixelRatio(1);
    r.setClearColor(0x000000, 0);
    r.outputColorSpace = THREE.SRGBColorSpace;
    r.toneMapping = THREE.NeutralToneMapping;
    r.shadowMap.enabled = true;
    r.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer = r;
    // the paper setting of promo/screens/n00.html
    const scene = new THREE.Scene();
    const pmrem = new THREE.PMREMGenerator(r);
    scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    scene.environmentIntensity = 0.75;
    scene.add(new THREE.HemisphereLight(0xfffaf0, 0xd8d2c4, 0.6));
    const key = new THREE.DirectionalLight(0xfff6e8, 2.1);
    key.position.set(-2.2, 8.0, 3.6);
    key.castShadow = true;
    key.shadow.mapSize.set(shadow, shadow);
    Object.assign(key.shadow.camera, { left: -2.6, right: 2.6, top: 3.6, bottom: -1.6, near: 1, far: 16 });
    key.shadow.radius = 8;
    key.shadow.bias = -0.0006;
    key.shadow.normalBias = 0.02;
    key.target.position.set(0.2, 1.0, 0);
    scene.add(key, key.target);
    const rim = new THREE.DirectionalLight(0xdfe8ff, 1.3);
    rim.position.set(3.5, 3.0, -4.5);
    scene.add(rim);
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(30, 30), new THREE.ShadowMaterial({ color: 0x1b1a17, opacity: 0.13 }));
    ground.rotation.x = -Math.PI / 2;
    ground.receiveShadow = true;
    scene.add(ground);
    this.scene = scene;
  }

  // A pet of a role (pet3d.js ROLES id), hidden until it is drawn.
  add(role) {
    const pet = this.HP.create(THREE, this.lite ? { role, theme: "paper", lite: true } : { role, theme: "paper" });
    pet.group.visible = false;
    this.scene.add(pet.group);
    return pet;
  }

  // The off-page canvas holds the largest canvas: size times scale.
  setScale(scale) {
    this.scale = scale;
    this.renderer.setSize(Math.ceil(this.size.W * scale), Math.ceil(this.size.H * scale), false);
  }

  // Make room for a canvas of w x h (at scale 1).
  grow(w, h) {
    if (w <= this.size.W && h <= this.size.H) return;
    this.size = { W: Math.max(w, this.size.W), H: Math.max(h, this.size.H) };
    this.setScale(this.scale);
  }

  // A camera for a box of aspect a, fitted to pet3d's FRAME (n00.html / viewer.html).
  cameraFor(a) {
    const F = this.HP.FRAME;
    const C = this.cam;
    const half = Math.tan((C.fov_deg * Math.PI) / 360);
    const dist = Math.max(F.h / 2 / half, F.w / 2 / (half * a)) * 1.04 + 0.6;
    const cam = new THREE.PerspectiveCamera(C.fov_deg, a, 0.1, 80);
    const az = (C.azimuth_deg * Math.PI) / 180;
    const el = (C.elevation_deg * Math.PI) / 180;
    const home = new THREE.Vector3(F.cx, F.cy, 0);
    cam.position.set(home.x + dist * Math.sin(az) * Math.cos(el), home.y + dist * Math.sin(el), dist * Math.cos(az) * Math.cos(el));
    cam.lookAt(home);
    cam.updateMatrixWorld();
    return { cam, dist, half };
  }

  // Draw a pet at pose (pet3d's time) in a pet3d state into the whole of a 2D canvas (its
  // context ctx), which must fit in the off-page canvas. o: pet3d's update options (blocked's since).
  paint(pet, ctx, pose, state, o = undefined) {
    const r = this.renderer;
    const gl = r.domElement;
    const w = ctx.canvas.width;
    const h = ctx.canvas.height;
    const { cam, dist, half } = this.cameraFor(w / h);
    pet.setPixelScale(h / (2 * dist * half));
    pet.group.visible = true;
    if (o) pet.update(pose, state, cam, o);
    else pet.update(pose, state, cam);
    // into the top left w x h of the off-page canvas (the viewport's y is from the bottom)
    r.setViewport(0, gl.height - h, w, h);
    r.setScissor(0, gl.height - h, w, h);
    r.setScissorTest(true);
    r.clear();
    r.render(this.scene, cam);
    r.setScissorTest(false);
    pet.group.visible = false;
    ctx.clearRect(0, 0, w, h);
    ctx.drawImage(gl, 0, 0, w, h, 0, 0, w, h);
  }
}

export class TilePets {
  // tiles: [{ key, box (the .pet element), w, h, changes }]
  constructor(HP, layout, tiles) {
    this.HP = HP;
    this.motion = layout.n00.motion;
    // room for the largest box (every tile may be the large one: N04's follows the voice)
    const big = layout.n04?.tile?.pet?.feature ?? {};
    const W = Math.max(...tiles.map((x) => x.w), big.w ?? 0);
    const H = Math.max(...tiles.map((x) => x.h), big.h ?? 0);
    this.stage = new PetStage(HP, layout.n00.camera, { W, H });
    this.renderer = this.stage.renderer;
    this.pets = tiles.map((x, i) => {
      const pet = this.stage.add(roleOf(x.key));
      const canvas = document.createElement("canvas");
      canvas.className = "pet3d";
      Object.assign(canvas.style, { position: "absolute", left: "0", top: "0", width: "100%", height: "100%" });
      x.box.append(canvas);
      x.box.dataset.role = pet.role.id;
      x.box.dataset.petState = "idle";
      return { ...x, pet, canvas, ctx: canvas.getContext("2d"), idleT: this.motion.idle_shift_s * i, last: null };
    });
  }

  resize(scale) {
    this.stage.setScale(scale);
    for (const p of this.pets) {
      p.canvas.width = Math.round(p.w * scale);
      p.canvas.height = Math.round(p.h * scale);
      p.last = null;
    }
  }

  // A tile's pet box changed size (N04: the large tile follows the voice).
  setBox(key, w, h) {
    const p = this.pets.find((x) => x.key === key);
    if (!p || (p.w === w && p.h === h)) return;
    p.w = w;
    p.h = h;
    this.stage.grow(w, h);
    p.canvas.width = Math.round(w * this.stage.scale);
    p.canvas.height = Math.round(h * this.stage.scale);
    p.last = null;
  }

  // Draw every pet at cut time t (a pet is drawn again only when its pose changes).
  draw(t) {
    const M = this.motion;
    for (const p of this.pets) {
      const { state, since } = petStateAt(p.changes, t, M.pass_dur_s);
      const pose = poseTime(state, since, t, p.idleT, M.hop_period_s);
      p.box.dataset.petState = state;
      const k = `${state}|${pose.toFixed(4)}|${p.canvas.width}`;
      if (k === p.last) continue;
      p.last = k;
      p.box.dataset.petDraws = String(Number(p.box.dataset.petDraws ?? 0) + 1);
      this.stage.paint(p.pet, p.ctx, pose, state);
    }
  }
}

// Keys of the pets' layout that are missing (for the cut's error).
export function missingPetKeys(layout) {
  return PET_KEYS.filter((k) => at(layout, k) === undefined);
}
