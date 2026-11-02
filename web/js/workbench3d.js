// The 3D view of the workbench (A3): frame members, wheels, part boxes, the
// centre-of-gravity mark, stress contour, force arrows and the driving
// animation. Everything is placed in design / road coordinates (mm, ISO 8855:
// x forward, y left, z up) inside one group whose matrix maps them to
// three.js (m, y up).
//
//   scene
//     world            turned about x by the tilt table's angle (gravity stays down on screen)
//       ground         the road (or the table top) grid
//       iso            ISO mm -> three.js m
//         body         frame members, parts, names, stress ghosts (moves with the body pose)
//         wheels       one group per support (centre, steer, spin)
//         links        support -> frame node lines
//         arrows       vectors of the analysis (a still)
//         motionArrows reaction / grip per wheel from the series (animated)
//         ghost        translucent red copy at the pose beyond the limit
//         cg, markers

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import { GRAY, colorOf } from "./colormap.js";
import { esc, fmt, fmtVU, vectorName } from "./labels.js";
import { wheelName } from "./series.js";

const INK = "#1d1d1f";
const MUTED = "#6b665c";
const RULE = "#e2dccf";
const RULE_MAJOR = "#c9c3b6";
const WHEEL_FILL = "#fbf9f4";
const LIMIT_RED = "#c8402a";
const VECTOR_COLORS = { reaction: "#2b59c3", grip: "#8e6bbf", load: "#c8402a", wind: "#2fa7d6", inertia: "#e9893a" };
const PART_COLORS = { battery: "#e8c547", seat: "#e9b872", occupant: "#e9b872", motor: "#2fa7d6", controller: "#4cbf7a", cargo: "#c9c3b6" };
const UP = new THREE.Vector3(0, 1, 0);

// ISO 8855 mm -> three.js m: X = x, Y = z, Z = -y (a proper rotation, then a scale).
export const ISO_TO_THREE = new THREE.Matrix4()
  .set(1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1)
  .multiply(new THREE.Matrix4().makeScale(0.001, 0.001, 0.001));

export function isoToThree(p) {
  return new THREE.Vector3(p[0], p[1], p[2]).applyMatrix4(ISO_TO_THREE);
}

function v3(p) {
  return new THREE.Vector3(p[0], p[1], p[2]);
}

// Local frame of a member: x along the member, z toward ISO up (or ISO x when vertical).
function memberBasis(a, b) {
  const x = b.clone().sub(a).normalize();
  let up = new THREE.Vector3(0, 0, 1);
  if (Math.abs(x.dot(up)) > 0.95) up = new THREE.Vector3(1, 0, 0);
  const z = up.sub(x.clone().multiplyScalar(up.dot(x))).normalize();
  const y = z.clone().cross(x);
  return new THREE.Matrix4().makeBasis(x, y, z);
}

function sectionSize(section) {
  const d = section?.dims_mm ?? {};
  const shape = section?.shape ?? "custom";
  if (shape === "round_tube" || shape === "round_solid") {
    const dia = d.d ?? d.D ?? 30;
    return { round: true, width: dia, height: dia };
  }
  return { round: false, width: d.b ?? d.w ?? d.h ?? 30, height: d.h ?? d.b ?? 30 };
}

function memberGeometry(design, m, nodes) {
  const a = nodes[m.node_a];
  const b = nodes[m.node_b];
  if (!a || !b) return null;
  const length = a.distanceTo(b);
  if (length < 1e-6) return null;
  const { round, width, height } = sectionSize(design.sections?.[m.section]);
  const geo = round ? new THREE.CylinderGeometry(width / 2, width / 2, length, 20).rotateZ(-Math.PI / 2) : new THREE.BoxGeometry(length, width, height);
  const mid = a.clone().add(b).multiplyScalar(0.5);
  const matrix = memberBasis(a, b).setPosition(mid);
  return { geo, matrix, length, mid, width, height, round };
}

function supportGeometry(s) {
  const r = s.radius_mm ?? 100;
  const w = s.width_mm ?? Math.max(40, r * 0.4);
  if (s.kind === "leg") return { geo: new THREE.CylinderGeometry(w / 2, w / 2, Math.max(r, 50), 16).rotateX(Math.PI / 2), r, w };
  if (s.kind === "caster") return { geo: new THREE.SphereGeometry(r, 20, 12), r, w };
  return { geo: new THREE.CylinderGeometry(r, r, w, 48), r, w };
}

function pushEdges(out, geometry, matrix, threshold = 20) {
  const edges = new THREE.EdgesGeometry(geometry, threshold);
  const pos = edges.getAttribute("position");
  const p = new THREE.Vector3();
  for (let i = 0; i < pos.count; i += 1) {
    p.fromBufferAttribute(pos, i);
    if (matrix) p.applyMatrix4(matrix);
    out.push(p.x, p.y, p.z);
  }
  edges.dispose();
}

// A circle of radius r in the x-z plane at y (a wheel's side), as line segments.
function ringPoints(out, y, r, n = 40) {
  for (let i = 0; i < n; i += 1) {
    const a0 = (i / n) * Math.PI * 2;
    const a1 = ((i + 1) / n) * Math.PI * 2;
    out.push(r * Math.cos(a0), y, r * Math.sin(a0), r * Math.cos(a1), y, r * Math.sin(a1));
  }
}

function cgTexture() {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d");
  const r = 56;
  g.translate(64, 64);
  g.fillStyle = "#fbf9f4";
  g.beginPath();
  g.arc(0, 0, r, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = INK;
  for (const start of [0, Math.PI]) {
    g.beginPath();
    g.moveTo(0, 0);
    g.arc(0, 0, r, start - Math.PI / 2, start);
    g.closePath();
    g.fill();
  }
  g.lineWidth = 7;
  g.strokeStyle = INK;
  g.beginPath();
  g.arc(0, 0, r, 0, Math.PI * 2);
  g.stroke();
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function label(text, cls) {
  const el = document.createElement("div");
  el.className = `lbl ${cls ?? ""}`;
  el.innerHTML = text;
  return new CSS2DObject(el);
}

function disposeTree(obj) {
  obj.traverse((o) => {
    o.geometry?.dispose?.();
    if (o.material && !o.material.userData?.shared) o.material.dispose?.();
    if (o.isCSS2DObject) o.element.remove();
  });
  obj.clear();
}

// Arrow along +y of its group: a unit shaft and cone, stretched by placeArrow.
function makeArrow(color) {
  const mat = new THREE.MeshBasicMaterial({ color, depthTest: false, transparent: true });
  const g = new THREE.Group();
  const shaft = new THREE.Mesh(new THREE.CylinderGeometry(9, 9, 1, 12), mat);
  const cone = new THREE.Mesh(new THREE.ConeGeometry(26, 1, 16), mat);
  g.add(shaft, cone);
  for (const o of [g, shaft, cone]) o.renderOrder = 8;
  return g;
}

function placeArrow(g, start, dir, len) {
  const head = Math.min(90, len * 0.4);
  const [shaft, cone] = g.children;
  shaft.scale.y = len - head;
  shaft.position.y = (len - head) / 2;
  cone.scale.y = head;
  cone.position.y = len - head / 2;
  g.quaternion.setFromUnitVectors(UP, dir);
  g.position.copy(start);
}

// R = Rz(yaw) Ry(pitch) Rx(roll), the K2 convention.
function bodyMatrix(body, rpy) {
  const m = new THREE.Matrix4().makeRotationFromEuler(new THREE.Euler(rpy[0], rpy[1], rpy[2], "ZYX"));
  return m.setPosition(body[0], body[1], body[2]);
}

function wheelMatrix(base, steer, spin) {
  return base
    .clone()
    .multiply(new THREE.Matrix4().makeRotationZ(steer))
    .multiply(new THREE.Matrix4().makeRotationY(spin));
}

export class Workbench3D {
  constructor(host) {
    this.host = host;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.domElement.className = "gl";
    host.appendChild(this.renderer.domElement);
    this.labelRenderer = new CSS2DRenderer();
    this.labelRenderer.domElement.className = "gl-labels";
    host.appendChild(this.labelRenderer.domElement);

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(28, 1, 0.05, 200);
    this.camera.position.set(4, 3, 4);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = false;
    this.controls.screenSpacePanning = true;

    this.scene.add(new THREE.HemisphereLight(0xffffff, 0xd9d2c3, 2.4));
    const sun = new THREE.DirectionalLight(0xffffff, 1.4);
    sun.position.set(3, 6, 2);
    this.scene.add(sun);

    this.world = new THREE.Group();
    this.scene.add(this.world);
    this.ground = new THREE.Group();
    this.iso = new THREE.Group();
    this.iso.matrixAutoUpdate = false;
    this.iso.matrix.copy(ISO_TO_THREE);
    this.world.add(this.ground, this.iso);

    this.body = new THREE.Group();
    this.body.matrixAutoUpdate = false;
    this.model = new THREE.Group();
    this.parts = new THREE.Group();
    this.overGhosts = new THREE.Group();
    this.names = new THREE.Group();
    this.body.add(this.model, this.parts, this.overGhosts, this.names);

    this.wheels = new THREE.Group();
    this.arrows = new THREE.Group();
    this.motionArrows = new THREE.Group();
    this.ghost = new THREE.Group();
    this.markers = new THREE.Group();
    this.iso.add(this.body, this.wheels, this.arrows, this.motionArrows, this.ghost, this.markers);

    this.inkMat = new LineMaterial({ color: INK, linewidth: 1.7 });
    this.mutedMat = new LineMaterial({ color: MUTED, linewidth: 1.1 });
    for (const m of [this.inkMat, this.mutedMat]) m.userData.shared = true;
    this.links = new LineSegments2(new LineSegmentsGeometry(), this.mutedMat);
    this.iso.add(this.links);

    this.cg = new THREE.Sprite(new THREE.SpriteMaterial({ map: cgTexture(), depthTest: false, transparent: true }));
    this.cg.renderOrder = 10;
    this.cg.scale.setScalar(150);
    this.cg.visible = false;
    this.iso.add(this.cg);

    this.design = null;
    this.members = {}; // id -> { mesh, length, node_a, node_b, mid, width, height }
    this.wheelObjs = {}; // support id -> { group, base (design position), ring, tag, r, frameNode }
    this.partObjs = {};
    this.motion = null;
    this.frame = null;
    this.view = null; // camera preset in use: null (front left, above) or "table"
    this.toggles = {};
    this.fitted = false;
    this.keys = { contour: null, vectors: null };
    this.size = { w: 1, h: 1 };
  }

  resize(w, h, pixelRatio) {
    this.size = { w, h };
    this.renderer.setPixelRatio(pixelRatio);
    this.renderer.setSize(w, h, true);
    this.labelRenderer.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    for (const m of [this.inkMat, this.mutedMat]) m.resolution.set(w, h);
  }

  setDesign(design) {
    if (design === this.design) return;
    this.design = design;
    for (const g of [this.model, this.parts, this.names, this.overGhosts, this.markers, this.wheels]) disposeTree(g);
    this.members = {};
    this.wheelObjs = {};
    this.partObjs = {};
    this.keys.contour = null;
    this.motion = null;
    this.frame = undefined;
    disposeTree(this.ghost);
    disposeTree(this.motionArrows);
    if (!design) return;

    const nodes = Object.fromEntries((design.nodes ?? []).map((n) => [n.id, v3(n.xyz_mm)]));
    this.nodes = nodes;
    const ink = [];
    const box = new THREE.Box3();

    for (const m of design.members ?? []) {
      const g = memberGeometry(design, m, nodes);
      if (!g) continue;
      const mesh = new THREE.Mesh(g.geo, new THREE.MeshLambertMaterial({ color: GRAY }));
      mesh.matrixAutoUpdate = false;
      mesh.matrix.copy(g.matrix);
      mesh.userData = { id: m.id };
      this.model.add(mesh);
      pushEdges(ink, g.geo, mesh.matrix, g.round ? 40 : 20);
      this.members[m.id] = { mesh, node_a: m.node_a, node_b: m.node_b, ...g };
      box.expandByPoint(nodes[m.node_a]).expandByPoint(nodes[m.node_b]);
      const tag = label(esc(m.id), "lbl-id");
      tag.position.copy(g.mid);
      this.names.add(tag);
    }
    for (const [id, p] of Object.entries(nodes)) {
      const tag = label(esc(id), "lbl-node");
      tag.position.copy(p);
      this.names.add(tag);
    }
    if (ink.length) this.model.add(new LineSegments2(new LineSegmentsGeometry().setPositions(ink), this.inkMat));

    for (const s of design.supports ?? []) {
      const c = v3(s.position_mm);
      const { geo, r, w } = supportGeometry(s);
      const group = new THREE.Group();
      group.matrixAutoUpdate = false;
      group.add(new THREE.Mesh(geo, new THREE.MeshLambertMaterial({ color: WHEEL_FILL })));
      const lines = [];
      pushEdges(lines, geo, null, 30);
      if (s.kind === "wheel") {
        for (const side of [-1, 1]) ringPoints(lines, (side * w) / 2, r * 0.45);
        // A spoke on each side so the wheel's turning shows.
        for (const side of [-1, 1]) lines.push(0, (side * w) / 2, 0, r * 0.92, (side * w) / 2, 0);
      }
      group.add(new LineSegments2(new LineSegmentsGeometry().setPositions(lines), this.inkMat));
      const ring = new THREE.Mesh(
        new THREE.TorusGeometry(r * 1.06, Math.max(12, r * 0.07), 8, 48).rotateX(Math.PI / 2),
        new THREE.MeshBasicMaterial({ color: LIMIT_RED, transparent: true, opacity: 0.55, depthWrite: false }),
      );
      ring.visible = false;
      group.add(ring);
      const tag = label(esc(s.id), "lbl-id");
      tag.position.set(0, 0, r + 60);
      group.add(tag);
      this.wheels.add(group);
      this.wheelObjs[s.id] = { group, base: c, ring, tag, r, frameNode: s.frame_node ?? null };
      box.expandByPoint(c.clone().addScalar(r)).expandByPoint(c.clone().addScalar(-r));
    }

    // Wheels listed as parts are drawn from the supports instead.
    const wheelAt = (c) => (design.supports ?? []).some((sp) => v3(sp.position_mm).distanceTo(c) < 50);
    let seat = 0;
    for (const p of design.parts ?? []) {
      const c = v3(p.box.center_mm);
      const sz = p.box.size_mm;
      box.expandByPoint(c.clone().add(v3(sz).multiplyScalar(0.5))).expandByPoint(c.clone().sub(v3(sz).multiplyScalar(0.5)));
      if (p.kind === "wheel" && wheelAt(c)) continue;
      const g = new THREE.Group();
      const color = PART_COLORS[p.kind] ?? "#c9c3b6";
      let edges = null;
      if (p.kind === "occupant") {
        const mat = new THREE.MeshLambertMaterial({ color, transparent: true, opacity: 0.8, depthWrite: false });
        mat.userData.baseOpacity = 0.8;
        const r = Math.min(sz[0], sz[1]) * 0.26;
        const head = Math.min(sz[0], sz[1]) * 0.2;
        const torsoLen = Math.max(10, sz[2] - 2 * head - 2 * r);
        const torso = new THREE.Mesh(new THREE.CapsuleGeometry(r, torsoLen, 6, 16), mat);
        torso.rotation.x = Math.PI / 2; // capsule axis (local y) -> ISO z
        torso.position.set(c.x, c.y, c.z - sz[2] / 2 + r + torsoLen / 2);
        const ball = new THREE.Mesh(new THREE.SphereGeometry(head, 20, 14), mat);
        ball.position.set(c.x, c.y, c.z + sz[2] / 2 - head);
        g.add(torso, ball);
      } else {
        const geo = new THREE.BoxGeometry(sz[0], sz[1], sz[2]);
        const mat = new THREE.MeshLambertMaterial({ color, transparent: true, opacity: 0.32, depthWrite: false });
        mat.userData.baseOpacity = 0.32;
        const mesh = new THREE.Mesh(geo, mat);
        mesh.position.copy(c);
        const rpy = p.box.rpy_deg ?? [0, 0, 0];
        mesh.rotation.set((rpy[0] * Math.PI) / 180, (rpy[1] * Math.PI) / 180, (rpy[2] * Math.PI) / 180, "ZYX");
        mesh.updateMatrix();
        mesh.renderOrder = 2;
        const lines = [];
        pushEdges(lines, geo, mesh.matrix);
        edges = new LineSegments2(new LineSegmentsGeometry().setPositions(lines), this.mutedMat);
        g.add(mesh, edges);
      }
      // Short name on the tag (the part before a bracket); the full name is the tooltip.
      const short = String(p.name ?? p.id).replace(/\s*[（(].*$/, "") || p.id;
      const tag = label(`${esc(short)}<small>${fmt(p.mass_kg)} kg</small>`, "lbl-part");
      tag.element.title = p.name ?? p.id;
      tag.position.copy(c).add(new THREE.Vector3(0, 0, sz[2] / 2 + 40));
      g.add(tag);
      this.parts.add(g);
      this.partObjs[p.id] = { group: g, edges, tag, kind: p.kind, seat: p.kind === "occupant" ? seat++ : null, onBoard: true };
    }

    this.bboxIso = box.isEmpty() ? new THREE.Box3(new THREE.Vector3(-1000, -1000, 0), new THREE.Vector3(1000, 1000, 1000)) : box;
    this.loading = undefined;
    this.setLoading(null);
    this.buildGround();
    this.buildAxes();
    this.setFrame(null);
    if (!this.fitted) {
      this.fit();
      this.fitted = true;
    }
  }

  // Who is on board for a load case's params.loading (empty / driver / two /
  // two_cargo; none means two_cargo). Occupants board in the order they are
  // listed and cargo rides only with two_cargo, as the solvers do. Parts that
  // stay off are drawn faint and left out of the centre of gravity.
  setLoading(loading) {
    if (loading === this.loading) return;
    this.loading = loading;
    const seats = { empty: 0, driver: 1, two: 2, two_cargo: 2 }[loading ?? "two_cargo"] ?? 2;
    const cargo = (loading ?? "two_cargo") === "two_cargo";
    for (const o of Object.values(this.partObjs)) {
      o.onBoard = o.kind === "occupant" ? o.seat < seats : o.kind === "cargo" ? cargo : true;
      o.group.traverse((x) => {
        if (x.isMesh && x.material.userData.baseOpacity !== undefined) x.material.opacity = o.onBoard ? x.material.userData.baseOpacity : 0.07;
      });
      if (o.edges) o.edges.visible = o.onBoard;
      o.tag.element.classList.toggle("off", !o.onBoard);
    }
    this.cgDesign = this.design ? this.displayCg(this.design) : null;
    this.placeCg();
  }

  // Centre of gravity used only to place the mark when the analysis gives none:
  // mass-weighted centre of the parts on board and the frame members (density x
  // section area x length). Members are left out when the design lists the
  // frame as a part with a mass.
  displayCg(design) {
    let m = 0;
    const sum = new THREE.Vector3();
    for (const p of design.parts ?? []) {
      if (!(p.mass_kg > 0) || this.partObjs[p.id]?.onBoard === false) continue;
      sum.addScaledVector(v3(p.box.center_mm), p.mass_kg);
      m += p.mass_kg;
    }
    const nodes = this.nodes ?? {};
    const frameAsPart = (design.parts ?? []).some((p) => p.kind === "frame" && p.mass_kg > 0);
    for (const mem of frameAsPart ? [] : design.members ?? []) {
      const a = nodes[mem.node_a];
      const b = nodes[mem.node_b];
      const sec = design.sections?.[mem.section];
      const mat = design.materials?.[mem.material];
      if (!a || !b || !sec || !(mat?.density_kg_m3 > 0)) continue;
      const area = sec.A_mm2 ?? sectionArea(sec);
      if (!(area > 0)) continue;
      const mass = mat.density_kg_m3 * area * 1e-6 * a.distanceTo(b) * 1e-3;
      sum.addScaledVector(a.clone().add(b).multiplyScalar(0.5), mass);
      m += mass;
    }
    return m > 0 ? sum.multiplyScalar(1 / m) : null;
  }

  // The recorded centre of mass when the frame has one, else the design's, carried by the body.
  placeCg() {
    const f = this.frame;
    if (f?.cg) this.cgPos = v3(f.cg);
    else if (this.cgDesign) this.cgPos = this.cgDesign.clone().applyMatrix4(this.body.matrix);
    else this.cgPos = null;
    if (this.cgPos) this.cg.position.copy(this.cgPos);
    this.cg.visible = !!this.toggles.cg && !!this.cgPos;
  }

  buildGround() {
    disposeTree(this.ground);
    const b = this.bboxIso.clone();
    const p = this.motion?.path;
    if (p) b.expandByPoint(new THREE.Vector3(p.minX, p.minY, 0)).expandByPoint(new THREE.Vector3(p.maxX, p.maxY, 0));
    const pad = 1500;
    const x0 = Math.floor((b.min.x - pad) / 1000) * 1000;
    const x1 = Math.ceil((b.max.x + pad) / 1000) * 1000;
    const y0 = Math.floor((b.min.y - pad) / 1000) * 1000;
    const y1 = Math.ceil((b.max.y + pad) / 1000) * 1000;
    const minor = [];
    const major = [];
    for (let x = x0; x <= x1; x += 100) (x % 1000 === 0 ? major : minor).push(x, y0, 0, x, y1, 0);
    for (let y = y0; y <= y1; y += 100) (y % 1000 === 0 ? major : minor).push(x0, y, 0, x1, y, 0);
    const mk = (arr, color) => {
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(arr, 3));
      const l = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.9 }));
      l.matrixAutoUpdate = false;
      l.matrix.copy(ISO_TO_THREE);
      return l;
    };
    this.ground.add(mk(minor, RULE), mk(major, RULE_MAJOR));
  }

  buildAxes() {
    const L = 300;
    const pts = [0, 0, 0, L, 0, 0, 0, 0, 0, 0, L, 0, 0, 0, 0, 0, 0, L];
    this.markers.add(new LineSegments2(new LineSegmentsGeometry().setPositions(pts), this.mutedMat));
    for (const [txt, p] of [["x 前", [L + 60, 0, 0]], ["y 左", [0, L + 60, 0]], ["z 上", [0, 0, L + 60]]]) {
      const t = label(txt, "lbl-axis");
      t.position.set(...p);
      this.markers.add(t);
    }
  }

  // Frame the design. The default view is from the front left, above; a tilt
  // table is seen from the front, low, so the table's turn reads as a turn on screen.
  fit(view = [0.85, 0.95, 0.62]) {
    const b = this.bboxIso;
    const center = isoToThree(b.getCenter(new THREE.Vector3()).toArray());
    const radius = b.getSize(new THREE.Vector3()).length() * 0.0005;
    const fov = (this.camera.fov * Math.PI) / 180;
    const dist = (radius / Math.sin(fov / 2)) * 1.0;
    const dir = isoToThree(view).normalize();
    this.controls.target.copy(center);
    this.camera.position.copy(center).addScaledVector(dir, dist);
    this.camera.near = dist / 50;
    this.camera.far = dist * 20;
    this.camera.updateProjectionMatrix();
    this.controls.update();
    this.followBase = null;
    this.followApplied = null;
  }

  // contour: { values: {memberId: v}, nodes: {nodeId: v}, max, where, result } or null for gray.
  setContour(contour) {
    if (contour === this.keys.contour) return;
    this.keys.contour = contour;
    disposeTree(this.overGhosts);
    for (const [id, info] of Object.entries(this.members)) {
      const mat = info.mesh.material;
      const geo = info.mesh.geometry;
      mat.vertexColors = false;
      if (!contour) {
        mat.color.set(GRAY);
        mat.needsUpdate = true;
        continue;
      }
      const v = contour.values?.[id];
      const va = contour.nodes?.[info.node_a];
      const vb = contour.nodes?.[info.node_b];
      if (typeof v === "number") {
        mat.color.set(colorOf(v, contour.max));
      } else if (typeof va === "number" && typeof vb === "number") {
        const pos = geo.getAttribute("position");
        const cols = new Float32Array(pos.count * 3);
        const c = new THREE.Color();
        for (let i = 0; i < pos.count; i += 1) {
          const u = pos.getX(i) / info.length + 0.5;
          c.set(colorOf(va + (vb - va) * u, contour.max));
          cols.set([c.r, c.g, c.b], i * 3);
        }
        geo.setAttribute("color", new THREE.BufferAttribute(cols, 3));
        mat.color.set("#ffffff");
        mat.vertexColors = true;
      } else {
        mat.color.set(GRAY);
      }
      mat.needsUpdate = true;
      const over = typeof v === "number" ? v > contour.max : Math.max(va ?? -Infinity, vb ?? -Infinity) > contour.max;
      if (contour.limitKnown && over) {
        const s = Math.max(info.width, info.height) * 2.2;
        const ghost = new THREE.Mesh(
          new THREE.BoxGeometry(info.length + s * 0.4, s, s),
          new THREE.MeshBasicMaterial({ color: LIMIT_RED, transparent: true, opacity: 0.22, depthWrite: false }),
        );
        ghost.matrixAutoUpdate = false;
        ghost.matrix.copy(info.mesh.matrix);
        this.overGhosts.add(ghost);
      }
    }
    if (contour?.where && this.members[contour.where] && contour.result) {
      const tag = label(`最大 ${esc(fmtVU(contour.result.value, contour.result.unit))}`, "lbl-max");
      tag.position.copy(this.members[contour.where].mid).add(new THREE.Vector3(0, 0, 120));
      this.overGhosts.add(tag);
    }
  }

  // The analysis' vectors (a still: one moment). Hidden while a motion is shown.
  setVectors(vectors) {
    if (vectors === this.keys.vectors) return;
    this.keys.vectors = vectors;
    disposeTree(this.arrows);
    const list = (vectors ?? []).filter((v) => Array.isArray(v.vector_N) && Array.isArray(v.origin_mm));
    const mags = list.map((v) => Math.hypot(...v.vector_N));
    const fmax = Math.max(1e-9, ...mags);
    list.forEach((v, i) => {
      const mag = mags[i];
      if (!(mag > 0)) return;
      const dir = v3(v.vector_N).normalize();
      const len = 160 + 640 * (mag / fmax);
      const origin = v3(v.origin_mm);
      // Loads push on their point (head at the origin); reactions and grip start there.
      const start = v.kind === "load" ? origin.clone().addScaledVector(dir, -len) : origin;
      const g = makeArrow(VECTOR_COLORS[v.kind] ?? INK);
      placeArrow(g, start, dir, len);
      this.arrows.add(g);
      const tag = label(`${esc(vectorName(v.kind))}${v.at ? ` ${esc(v.at)}` : ""}<small>|F| ${fmt(mag / 1000, 3)} kN</small>`, `lbl-vec lbl-${v.kind}`);
      tag.position.copy(start.clone().addScaledVector(dir, len + 40));
      this.arrows.add(tag);
    });
  }

  // A motion from series.js (or null). Builds the arrow set, the ghost at the
  // last pose when a wheel lifted (the limit was passed), and widens the ground
  // to the path.
  setMotion(motion) {
    if (motion === this.motion) return;
    this.motion = motion;
    disposeTree(this.ghost);
    disposeTree(this.motionArrows);
    this.motionArrowObjs = {};
    // Take back the follow shift of the previous motion before starting a new one.
    if (this.followApplied) {
      this.camera.position.sub(this.followApplied);
      this.controls.target.sub(this.followApplied);
      this.controls.update();
    }
    this.followBase = null;
    this.followApplied = null;
    this.buildGround();
    const view = motion?.hasTable ? "table" : null;
    if (view !== this.view && this.design) {
      this.fit(view === "table" ? [1, 0.14, 0.2] : undefined);
      this.view = view;
    }
    if (!motion || !this.design) {
      this.setFrame(null);
      return;
    }
    for (const id of motion.wheelIds) {
      const reaction = makeArrow(VECTOR_COLORS.reaction);
      const grip = makeArrow(VECTOR_COLORS.grip);
      const tag = label("", "lbl-vec lbl-reaction");
      this.motionArrows.add(reaction, grip, tag);
      this.motionArrowObjs[id] = { reaction, grip, tag, text: null };
    }
    if (motion.liftT !== null) this.buildGhost(motion.frame(motion.t1));
  }

  buildGhost(frame) {
    const design = this.design;
    // Frame and wheels only: big part boxes would turn the ghost into a red blob.
    const mat = new THREE.MeshBasicMaterial({ color: LIMIT_RED, transparent: true, opacity: 0.14, depthWrite: false });
    const body = new THREE.Group();
    body.matrixAutoUpdate = false;
    body.matrix.copy(bodyMatrix(frame.body, frame.rpy));
    for (const m of design.members ?? []) {
      const g = memberGeometry(design, m, this.nodes);
      if (!g) continue;
      const mesh = new THREE.Mesh(g.geo, mat);
      mesh.matrixAutoUpdate = false;
      mesh.matrix.copy(g.matrix);
      body.add(mesh);
    }
    this.ghost.add(body);
    for (const s of design.supports ?? []) {
      const w = frame.wheels.find((x) => x.id === s.id);
      const mesh = new THREE.Mesh(supportGeometry(s).geo, mat);
      mesh.matrixAutoUpdate = false;
      mesh.matrix.copy(this.wheelPose(s.id, w, body.matrix));
      this.ghost.add(mesh);
    }
  }

  wheelPose(id, w, bodyM) {
    const base = this.wheelObjs[id]?.base ?? new THREE.Vector3();
    const rot = new THREE.Matrix4().extractRotation(bodyM);
    const at = w?.center ? v3(w.center) : base.clone().applyMatrix4(bodyM);
    return wheelMatrix(rot.setPosition(at), w?.steer ?? 0, w?.spin ?? 0);
  }

  // Pose everything for one frame of the motion, or the design pose (frame = null).
  setFrame(frame) {
    this.frame = frame;
    const bodyM = frame ? bodyMatrix(frame.body, frame.rpy) : new THREE.Matrix4();
    this.body.matrix.copy(bodyM);
    this.body.matrixWorldNeedsUpdate = true;
    const byId = Object.fromEntries((frame?.wheels ?? []).map((w) => [w.id, w]));
    const links = [];
    for (const [id, o] of Object.entries(this.wheelObjs)) {
      const w = byId[id];
      o.group.matrix.copy(this.wheelPose(id, w, bodyM));
      o.group.matrixWorldNeedsUpdate = true;
      const lifted = !!(frame && w && w.load !== null && w.load <= 0 && this.motion?.liftAt?.[id] !== undefined && frame.t >= this.motion.liftAt[id]);
      o.ring.visible = lifted;
      o.tag.element.innerHTML = lifted ? `${esc(id)} 浮いた` : esc(id);
      o.tag.element.classList.toggle("lbl-lift", lifted);
      const fn = o.frameNode && this.nodes?.[o.frameNode];
      if (fn) {
        const c = new THREE.Vector3().setFromMatrixPosition(o.group.matrix);
        const n = fn.clone().applyMatrix4(bodyM);
        links.push(c.x, c.y, c.z, n.x, n.y, n.z);
      }
    }
    this.links.geometry.dispose();
    this.links.geometry = new LineSegmentsGeometry().setPositions(links.length ? links : [0, 0, 0, 0, 0, 0]);
    this.links.visible = links.length > 0;
    this.world.rotation.x = frame?.table ?? 0;
    this.placeCg();
    this.placeMotionArrows(frame);
    this.ghost.visible = !!frame && !!this.toggles.limit;
  }

  placeMotionArrows(frame) {
    const objs = this.motionArrowObjs ?? {};
    const fref = Math.max(1e-9, this.motion?.fref ?? 0);
    const show = !!frame && !!this.toggles.arrows;
    this.motionArrows.visible = show;
    if (!show) return;
    for (const w of frame.wheels) {
      const o = objs[w.id];
      if (!o) continue;
      for (const kind of ["reaction", "grip"]) {
        const f = w[kind];
        const mag = f ? Math.hypot(...f) : 0;
        const g = o[kind];
        g.visible = !!w.contact && mag > fref * 1e-3;
        if (g.visible) placeArrow(g, v3(w.contact), v3(f).normalize(), 120 + 680 * Math.min(1, mag / fref));
      }
      // The label shows the recorded normal force (wheel_load) at the nearest sample.
      o.tag.visible = !!w.contact && w.load !== null && w.load > 0;
      if (o.tag.visible) {
        const text = `${esc(wheelName(w.id))}<small>${fmt(w.load / 1000, 3)} kN</small>`;
        if (text !== o.text) o.tag.element.innerHTML = o.text = text;
        const f = w.reaction ?? [0, 0, 1];
        const len = 120 + 680 * Math.min(1, Math.hypot(...f) / fref);
        o.tag.position.copy(v3(w.contact)).addScaledVector(v3(f).normalize(), len + 60);
      }
    }
  }

  // Where a frame's centre of mass (or body origin) is on screen space, in three.js metres.
  anchorOf(frame) {
    const p = v3(frame.cg ?? frame.body).applyMatrix4(ISO_TO_THREE);
    return p.applyAxisAngle(new THREE.Vector3(1, 0, 0), frame.table ?? 0);
  }

  // Keep the camera on the moving vehicle: camera and target are shifted by how
  // far the anchor is from where it was at the motion's first time. The shift
  // depends on that time only, so any seek order gives the same view.
  follow() {
    if (!this.frame || !this.motion) return;
    this.followBase ??= this.anchorOf(this.motion.frame(this.motion.t0));
    const want = this.anchorOf(this.frame).sub(this.followBase);
    const d = want.clone().sub(this.followApplied ?? new THREE.Vector3());
    if (d.lengthSq() < 1e-12) return;
    this.camera.position.add(d);
    this.controls.target.add(d);
    this.followApplied = want;
    this.controls.update();
  }

  setToggles(t) {
    this.toggles = { ...t };
    this.parts.visible = !!t.parts;
    this.arrows.visible = !!t.arrows && !this.frame;
    this.names.visible = !!t.names;
    this.markers.visible = !!t.names;
    for (const o of Object.values(this.partObjs)) o.tag.visible = !!t.names;
    for (const o of Object.values(this.wheelObjs)) o.tag.visible = !!t.names || o.ring.visible;
    this.overGhosts.visible = !!t.contour;
    this.ghost.visible = !!this.frame && !!t.limit;
    this.cg.visible = !!t.cg && !!this.cgPos;
    this.placeMotionArrows(this.frame);
  }

  // Time-driven effects use the playhead only, so a frozen ?t= frame is repeatable.
  animate(playhead, running) {
    const k = running ? 0.18 * (1 + Math.sin(playhead * Math.PI * 2)) : 0;
    for (const info of Object.values(this.members)) info.mesh.material.emissive?.setScalar(k * 0.5);
  }

  render() {
    this.follow();
    this.renderer.render(this.scene, this.camera);
    this.labelRenderer.render(this.scene, this.camera);
  }
}

function sectionArea(sec) {
  const d = sec.dims_mm ?? {};
  switch (sec.shape) {
    case "rect_tube":
      return d.b * d.h - Math.max(0, d.b - 2 * d.t) * Math.max(0, d.h - 2 * d.t);
    case "rect_solid":
      return d.b * d.h;
    case "round_tube":
      return (Math.PI / 4) * (d.d ** 2 - Math.max(0, d.d - 2 * d.t) ** 2);
    case "round_solid":
      return (Math.PI / 4) * d.d ** 2;
    default:
      return NaN;
  }
}
