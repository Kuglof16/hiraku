// The 3D view of the workbench (A3): frame members, wheels, part boxes, the
// centre-of-gravity mark, stress contour and force arrows. Everything is
// placed in design coordinates (mm, ISO 8855: x forward, y left, z up) inside
// one group whose matrix maps them to three.js (m, y up).

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import { GRAY, OVER_COLOR, colorOf } from "./colormap.js";
import { esc, fmt, fmtVU, vectorName } from "./labels.js";

const INK = "#1d1d1f";
const MUTED = "#6b665c";
const RULE = "#e2dccf";
const RULE_MAJOR = "#c9c3b6";
const WHEEL_FILL = "#fbf9f4";
const VECTOR_COLORS = { reaction: "#2b59c3", grip: "#8e6bbf", load: "#c8402a", wind: "#2fa7d6", inertia: "#e9893a" };
const PART_COLORS = { battery: "#e8c547", seat: "#e9b872", occupant: "#e9b872", motor: "#2fa7d6", controller: "#4cbf7a", cargo: "#c9c3b6" };

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

function pushEdges(out, geometry, matrix, threshold = 20) {
  const edges = new THREE.EdgesGeometry(geometry, threshold);
  const pos = edges.getAttribute("position");
  const p = new THREE.Vector3();
  for (let i = 0; i < pos.count; i += 1) {
    p.fromBufferAttribute(pos, i).applyMatrix4(matrix);
    out.push(p.x, p.y, p.z);
  }
  edges.dispose();
}

function ringPoints(out, center, radius, axis, n = 40) {
  // A circle in the plane normal to `axis` (ISO y for wheels).
  const pts = [];
  for (let i = 0; i <= n; i += 1) {
    const a = (i / n) * Math.PI * 2;
    if (axis === "y") pts.push([center.x + radius * Math.cos(a), center.y, center.z + radius * Math.sin(a)]);
  }
  for (let i = 0; i < n; i += 1) out.push(...pts[i], ...pts[i + 1]);
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

    this.ground = new THREE.Group();
    this.scene.add(this.ground);
    this.iso = new THREE.Group();
    this.iso.matrixAutoUpdate = false;
    this.iso.matrix.copy(ISO_TO_THREE);
    this.scene.add(this.iso);

    this.model = new THREE.Group();
    this.parts = new THREE.Group();
    this.arrows = new THREE.Group();
    this.overGhosts = new THREE.Group();
    this.names = new THREE.Group();
    this.markers = new THREE.Group();
    this.iso.add(this.model, this.parts, this.arrows, this.overGhosts, this.names, this.markers);

    this.inkMat = new LineMaterial({ color: INK, linewidth: 1.7 });
    this.mutedMat = new LineMaterial({ color: MUTED, linewidth: 1.1 });
    for (const m of [this.inkMat, this.mutedMat]) m.userData.shared = true;

    this.cg = new THREE.Sprite(new THREE.SpriteMaterial({ map: cgTexture(), depthTest: false, transparent: true }));
    this.cg.renderOrder = 10;
    this.cg.scale.setScalar(150);
    this.cg.visible = false;
    this.iso.add(this.cg);

    this.design = null;
    this.members = {}; // id -> { mesh, length, node_a, node_b, mid }
    this.fitted = false;
    this.keys = { contour: null, vectors: null, toggles: null };
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
    for (const g of [this.model, this.parts, this.names, this.overGhosts, this.markers]) disposeTree(g);
    this.members = {};
    this.keys.contour = null;
    if (!design) return;

    const nodes = Object.fromEntries((design.nodes ?? []).map((n) => [n.id, v3(n.xyz_mm)]));
    const ink = [];
    const muted = [];
    const box = new THREE.Box3();

    for (const m of design.members ?? []) {
      const a = nodes[m.node_a];
      const b = nodes[m.node_b];
      if (!a || !b) continue;
      const length = a.distanceTo(b);
      if (length < 1e-6) continue;
      const { round, width, height } = sectionSize(design.sections?.[m.section]);
      const geo = round ? new THREE.CylinderGeometry(width / 2, width / 2, length, 20).rotateZ(-Math.PI / 2) : new THREE.BoxGeometry(length, width, height);
      const mesh = new THREE.Mesh(geo, new THREE.MeshLambertMaterial({ color: GRAY }));
      const mid = a.clone().add(b).multiplyScalar(0.5);
      mesh.matrixAutoUpdate = false;
      mesh.matrix.copy(memberBasis(a, b)).setPosition(mid);
      mesh.userData = { id: m.id };
      this.model.add(mesh);
      pushEdges(ink, geo, mesh.matrix, round ? 40 : 20);
      this.members[m.id] = { mesh, length, mid, node_a: m.node_a, node_b: m.node_b, width, height, round };
      box.expandByPoint(a).expandByPoint(b);
      const tag = label(esc(m.id), "lbl-id");
      tag.position.copy(mid);
      this.names.add(tag);
    }
    for (const [id, p] of Object.entries(nodes)) {
      const tag = label(esc(id), "lbl-node");
      tag.position.copy(p);
      this.names.add(tag);
    }

    for (const s of design.supports ?? []) {
      const c = v3(s.position_mm);
      const r = s.radius_mm ?? 100;
      const w = s.width_mm ?? Math.max(40, r * 0.4);
      let geo;
      if (s.kind === "leg") geo = new THREE.CylinderGeometry(w / 2, w / 2, Math.max(r, 50), 16).rotateX(Math.PI / 2);
      else if (s.kind === "caster") geo = new THREE.SphereGeometry(r, 20, 12);
      else geo = new THREE.CylinderGeometry(r, r, w, 48);
      const mesh = new THREE.Mesh(geo, new THREE.MeshLambertMaterial({ color: WHEEL_FILL }));
      mesh.position.copy(c);
      mesh.updateMatrix();
      this.model.add(mesh);
      if (s.kind === "wheel") {
        pushEdges(ink, geo, mesh.matrix, 30);
        for (const side of [-1, 1]) ringPoints(ink, new THREE.Vector3(c.x, c.y + (side * w) / 2, c.z), r * 0.45, "y");
      } else {
        pushEdges(ink, geo, mesh.matrix, 30);
      }
      const fn = s.frame_node && nodes[s.frame_node];
      if (fn) muted.push(c.x, c.y, c.z, fn.x, fn.y, fn.z);
      box.expandByPoint(c.clone().addScalar(r)).expandByPoint(c.clone().addScalar(-r));
      const tag = label(esc(s.id), "lbl-id");
      tag.position.copy(c).add(new THREE.Vector3(0, 0, r + 60));
      this.names.add(tag);
    }

    for (const p of design.parts ?? []) {
      const c = v3(p.box.center_mm);
      const sz = p.box.size_mm;
      const geo = new THREE.BoxGeometry(sz[0], sz[1], sz[2]);
      const mat = new THREE.MeshLambertMaterial({
        color: PART_COLORS[p.kind] ?? "#c9c3b6",
        transparent: true,
        opacity: 0.32,
        depthWrite: false,
      });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.position.copy(c);
      const rpy = p.box.rpy_deg ?? [0, 0, 0];
      mesh.rotation.set((rpy[0] * Math.PI) / 180, (rpy[1] * Math.PI) / 180, (rpy[2] * Math.PI) / 180, "ZYX");
      mesh.updateMatrix();
      mesh.renderOrder = 2;
      this.parts.add(mesh);
      const lines = [];
      pushEdges(lines, geo, mesh.matrix);
      const lg = new LineSegmentsGeometry().setPositions(lines);
      this.parts.add(new LineSegments2(lg, this.mutedMat));
      const tag = label(`${esc(p.name)}<small>${fmt(p.mass_kg)} kg</small>`, "lbl-part");
      tag.position.copy(c).add(new THREE.Vector3(0, 0, sz[2] / 2 + 40));
      this.parts.add(tag);
      box.expandByPoint(c.clone().add(v3(sz).multiplyScalar(0.5))).expandByPoint(c.clone().sub(v3(sz).multiplyScalar(0.5)));
    }

    if (ink.length) this.model.add(new LineSegments2(new LineSegmentsGeometry().setPositions(ink), this.inkMat));
    if (muted.length) this.model.add(new LineSegments2(new LineSegmentsGeometry().setPositions(muted), this.mutedMat));

    this.bboxIso = box.isEmpty() ? new THREE.Box3(new THREE.Vector3(-1000, -1000, 0), new THREE.Vector3(1000, 1000, 1000)) : box;
    this.cgIso = this.displayCg(design);
    this.cg.position.copy(this.cgIso ?? new THREE.Vector3());
    this.buildGround();
    this.buildAxes();
    if (!this.fitted) {
      this.fit();
      this.fitted = true;
    }
  }

  // Centre of gravity used only to place the mark: mass-weighted centre of the
  // part boxes and the frame members (density x section area x length).
  displayCg(design) {
    let m = 0;
    const sum = new THREE.Vector3();
    for (const p of design.parts ?? []) {
      if (!(p.mass_kg > 0)) continue;
      sum.addScaledVector(v3(p.box.center_mm), p.mass_kg);
      m += p.mass_kg;
    }
    const nodes = Object.fromEntries((design.nodes ?? []).map((n) => [n.id, v3(n.xyz_mm)]));
    for (const mem of design.members ?? []) {
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

  buildGround() {
    disposeTree(this.ground);
    const b = this.bboxIso;
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
      g.setAttribute("position", new THREE.Float32BufferAttribute(arr.map((v, i) => v), 3));
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

  fit() {
    const b = this.bboxIso;
    const center = isoToThree(b.getCenter(new THREE.Vector3()).toArray());
    const radius = b.getSize(new THREE.Vector3()).length() * 0.0005;
    const fov = (this.camera.fov * Math.PI) / 180;
    const dist = (radius / Math.sin(fov / 2)) * 1.0;
    const dir = isoToThree([0.85, 0.95, 0.62]).normalize(); // front-left, from above
    this.controls.target.copy(center);
    this.camera.position.copy(center).addScaledVector(dir, dist);
    this.camera.near = dist / 50;
    this.camera.far = dist * 20;
    this.camera.updateProjectionMatrix();
    this.controls.update();
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
          new THREE.MeshBasicMaterial({ color: "#c8402a", transparent: true, opacity: 0.22, depthWrite: false }),
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
      const head = Math.min(90, len * 0.4);
      const color = VECTOR_COLORS[v.kind] ?? INK;
      const mat = new THREE.MeshBasicMaterial({ color, depthTest: false, transparent: true });
      const g = new THREE.Group();
      const shaft = new THREE.Mesh(new THREE.CylinderGeometry(9, 9, len - head, 12), mat);
      shaft.position.y = (len - head) / 2;
      const cone = new THREE.Mesh(new THREE.ConeGeometry(26, head, 16), mat);
      cone.position.y = len - head / 2;
      g.add(shaft, cone);
      g.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir);
      const origin = v3(v.origin_mm);
      // Loads push on their point (head at the origin); reactions and grip start there.
      const start = v.kind === "load" ? origin.clone().addScaledVector(dir, -len) : origin;
      g.position.copy(start);
      g.renderOrder = 8;
      for (const m of g.children) m.renderOrder = 8;
      this.arrows.add(g);
      const tip = start.clone().addScaledVector(dir, len + 40);
      const tag = label(`${esc(vectorName(v.kind))}${v.at ? ` ${esc(v.at)}` : ""}<small>|F| ${fmt(mag / 1000, 3)} kN</small>`, `lbl-vec lbl-${v.kind}`);
      tag.position.copy(tip);
      this.arrows.add(tag);
    });
  }

  setToggles(t) {
    this.parts.visible = !!t.parts;
    this.arrows.visible = !!t.arrows;
    this.cg.visible = !!t.cg && !!this.cgIso;
    this.names.visible = !!t.names;
    this.markers.visible = !!t.names;
    this.overGhosts.visible = !!t.contour;
  }

  // Time-driven effects use the playhead only, so a frozen ?t= frame is repeatable.
  animate(playhead, running) {
    const k = running ? 0.18 * (1 + Math.sin(playhead * Math.PI * 2)) : 0;
    for (const info of Object.values(this.members)) info.mesh.material.emissive?.setScalar(k * 0.5);
  }

  render() {
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

