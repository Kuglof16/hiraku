// The joints of a design on the 3D (workbench3d.js, assembly.js): where parts and frame
// members are joined and how, read from the design only.
//
// Two sources in the design:
//   - part.connects_to [{to, via}]: the part is joined to `to` (a part, a frame member, a frame
//     node, a support or "<support>.<hardpoint>") by `via` (bolt, weld, bushing, ...)
//   - the frame's nodes where two or more members meet: node.joint (weld / bolt / pin);
//     without it, FRAME_NODE_RULE (below) draws the node as welded.
//
// One joint is one place: the entries of connects_to with the same two ends and the same via
// are one joint (a wheel "rests" on its support for load, steer and brake: one place), and a
// frame node is one joint however many members meet there. The legend's numbers count these.
//
// The marks are drawn with one InstancedMesh per shape (a few draw calls for hundreds of
// marks), in the body group (ISO mm), so they move with the body's pose.

import * as THREE from "three";
import { esc } from "./labels.js";

// ---- the table: what each kind is drawn as -------------------------------------------
// mark: the shape (MARK_SHAPES); color: its colour (the same in both looks); ja: its name
// on the screen. A via not listed here is drawn as OTHER (its own name, the common dot).
export const JOINT_KINDS = {
  weld: { ja: "溶接", mark: "bead", color: "#ff6a13" }, // a bead round the joint, bright orange
  bolt: { ja: "ボルト", mark: "hex", color: "#2d3a4a" }, // a small hexagon (a bolt's head)
  bushing: { ja: "ブッシュ", mark: "ring", color: "#151515" }, // a rubber ring
  ball_joint: { ja: "ボールジョイント", mark: "ball", color: "#2f7fd0" }, // a ball
  pin: { ja: "ピン", mark: "pin", color: "#8e44c8" }, // a thin rod through the joint
  spline: { ja: "スプライン", mark: "spline", color: "#13a08a" }, // a short toothed shaft
  rest: { ja: "載せる", mark: "dot", color: "#9a8f78" },
  friction: { ja: "摩擦", mark: "dot", color: "#c9a227" },
  wire: { ja: "電線", mark: "dot", color: "#d23c8a" },
  hose: { ja: "ホース", mark: "dot", color: "#3fa7c9" },
};
const OTHER = { mark: "dot", color: "#8a8577" };
export const kindOf = (via) => JOINT_KINDS[via] ?? { ...OTHER, ja: String(via) };

// The rule for a frame node that does not say how it is joined (node.joint absent): drawn as
// welded. The contract (docs/contract.md, d9631a5) says the ladder_4w generator's frame nodes
// are welded, but a design does not record which generator made it, so the rule is used for
// every frame node without node.joint. The legend says so (NODE_RULE_NOTE).
export const FRAME_NODE_RULE = "weld";
export const NODE_RULE_NOTE = "枠の節の溶接はデータに書いていないので、決まりで溶接として描いている";
// Where a joint between two boxes is put is worked out from the boxes (PLACE below): the
// legend says so for the joints placed that way.
export const PLACE_NOTE = "部品の印はデータに場所が無いので、2 つの相手の箱のいちばん近い所の中ほどに置いている";

// How the place of a joint is found, by what `to` is (the owner is the part with connects_to):
//   node        the point on the owner's box nearest the node, and the node: halfway
//   member      the owner's box and the member's centre line: halfway between their nearest points
//   part        the two boxes (turned by rpy_deg): halfway between their nearest points
//   support     the owner's box and the support's centre (position_mm): halfway
//   hardpoint   "<support>.<point>": the owner's box and the point (suspension.hardpoints_mm): halfway
//   frame node  (node.joint or the rule) the node itself; a weld's bead round each member meeting
//               there, at the side of the widest other member (its half width from the node)
// The mark's axis: from the owner's nearest point to the other's; where they touch (inside each
// other), the normal of the owner's box face nearest the point.
// The mark's size s (mm): 0.25 of the smaller of the two ends' smallest sides, kept in
// [SIZE_MIN_MM, SIZE_MAX_MM]. A frame node's marks are sized from its members' sections.
// A frame node: two members of the same section whose directions from the node are within
// acos(THROUGH_COS) of opposite are one tube going through it (no joint between them).
export const THROUGH_COS = 0.995;
export const PLACE = { node: "node", member: "member", part: "part", support: "support", hardpoint: "hardpoint" };
export const SIZE_MIN_MM = 14;
export const SIZE_MAX_MM = 44;
const SIZE_OF_SIDE = 0.25;

// Each shape, made once with its axis on +z and its size 1 (scaled by s per mark).
const MARK_SHAPES = {
  bead: () => new THREE.TorusGeometry(1, 0.2, 8, 28), // scaled by the ring's radius
  hex: () => new THREE.CylinderGeometry(0.5, 0.5, 0.45, 6).rotateX(Math.PI / 2),
  ring: () => new THREE.TorusGeometry(0.42, 0.16, 10, 24),
  ball: () => new THREE.SphereGeometry(0.42, 18, 12),
  pin: () => new THREE.CylinderGeometry(0.12, 0.12, 1.6, 10).rotateX(Math.PI / 2),
  spline: () => new THREE.CylinderGeometry(0.3, 0.3, 0.9, 12).rotateX(Math.PI / 2),
  dot: () => new THREE.OctahedronGeometry(0.3, 0),
};

const Z = new THREE.Vector3(0, 0, 1);

function v3(p) {
  return new THREE.Vector3(p[0], p[1], p[2]);
}

// A part's box: centre, rotation (rpy_deg, as workbench3d.js turns the part: Euler ZYX) and half sizes.
function boxOf(part) {
  const b = part.box ?? {};
  const rpy = b.rpy_deg ?? [0, 0, 0];
  const q = new THREE.Quaternion().setFromEuler(new THREE.Euler((rpy[0] * Math.PI) / 180, (rpy[1] * Math.PI) / 180, (rpy[2] * Math.PI) / 180, "ZYX"));
  return { kind: "box", c: v3(b.center_mm ?? [0, 0, 0]), q, qi: q.clone().invert(), h: v3(b.size_mm ?? [0, 0, 0]).multiplyScalar(0.5) };
}

function closestOnBox(box, p) {
  const l = p.clone().sub(box.c).applyQuaternion(box.qi);
  l.set(Math.max(-box.h.x, Math.min(box.h.x, l.x)), Math.max(-box.h.y, Math.min(box.h.y, l.y)), Math.max(-box.h.z, Math.min(box.h.z, l.z)));
  return l.applyQuaternion(box.q).add(box.c);
}

function closestOnSegment(seg, p) {
  const ab = seg.b.clone().sub(seg.a);
  const L = ab.lengthSq();
  const t = L > 0 ? Math.max(0, Math.min(1, p.clone().sub(seg.a).dot(ab) / L)) : 0;
  return seg.a.clone().addScaledVector(ab, t);
}

function closestOn(shape, p) {
  if (shape.kind === "box") return closestOnBox(shape, p);
  if (shape.kind === "segment") return closestOnSegment(shape, p);
  return shape.p.clone();
}

// The normal of the box's face nearest a point on (or in) it.
function faceNormal(box, p) {
  const l = p.clone().sub(box.c).applyQuaternion(box.qi);
  const r = [Math.abs(l.x) / (box.h.x || 1), Math.abs(l.y) / (box.h.y || 1), Math.abs(l.z) / (box.h.z || 1)];
  const i = r.indexOf(Math.max(...r));
  const n = new THREE.Vector3();
  n.setComponent(i, Math.sign(l.getComponent(i)) || 1);
  return n.applyQuaternion(box.q);
}

// The nearest points of the owner's box and the other shape (alternating projections; both
// convex). Returns { p (on the owner), q (on the other) }.
function nearest(box, other) {
  let q = other.kind === "box" ? other.c.clone() : other.kind === "segment" ? other.a.clone().add(other.b).multiplyScalar(0.5) : other.p.clone();
  let p = closestOnBox(box, q);
  for (let i = 0; i < 8; i += 1) {
    q = closestOn(other, p);
    const p2 = closestOnBox(box, q);
    if (p2.distanceToSquared(p) < 1e-6) {
      p = p2;
      break;
    }
    p = p2;
  }
  return { p, q: closestOn(other, p) };
}

const minSide = (shape) => (shape.kind === "box" ? 2 * Math.min(shape.h.x, shape.h.y, shape.h.z) : shape.side);
const clampSize = (x) => Math.max(SIZE_MIN_MM, Math.min(SIZE_MAX_MM, x));

function sectionSides(design, m) {
  const s = design.sections?.[m.section];
  const d = s?.dims_mm ?? {};
  if (s?.shape === "round_tube" || s?.shape === "round_solid") {
    const dia = d.d ?? d.D ?? 30;
    return [dia, dia];
  }
  return [d.b ?? d.w ?? d.h ?? 30, d.h ?? d.b ?? 30];
}

// ---- reading the design ---------------------------------------------------------------
// Every joint of a design: { joints: [...], counts: {kind: n}, frameRule: n, unplaced: n }.
// A joint: { kind (the via or node joint), source ("part" | "node"), owner, to, toKind,
// rule (true: the frame node rule), marks: [{shape, at [x,y,z], axis [x,y,z], scale, gap}] }.
export function readJoints(design) {
  const out = { joints: [], counts: {}, frameRule: 0, unplaced: 0 };
  if (!design) return out;
  const parts = Object.fromEntries((design.parts ?? []).map((p) => [p.id, p]));
  const nodes = Object.fromEntries((design.nodes ?? []).map((n) => [n.id, n]));
  const members = Object.fromEntries((design.members ?? []).map((m) => [m.id, m]));
  const supports = Object.fromEntries((design.supports ?? []).map((s) => [s.id, s]));
  const atNode = {};
  for (const m of design.members ?? []) {
    for (const end of [m.node_a, m.node_b]) if (nodes[end]) (atNode[end] ??= []).push(m);
  }
  const nodeSide = (id) => {
    const ms = atNode[id] ?? [];
    return ms.length ? Math.min(...ms.map((m) => Math.min(...sectionSides(design, m)))) : 40;
  };
  const count = (k) => (out.counts[k] = (out.counts[k] ?? 0) + 1);

  // what `to` names, as a shape
  const shapeOf = (to) => {
    if (parts[to]) return { toKind: PLACE.part, shape: boxOf(parts[to]) };
    if (members[to]) {
      const m = members[to];
      const a = nodes[m.node_a];
      const b = nodes[m.node_b];
      if (a && b) return { toKind: PLACE.member, shape: { kind: "segment", a: v3(a.xyz_mm), b: v3(b.xyz_mm), side: Math.min(...sectionSides(design, m)) } };
    }
    if (nodes[to]) return { toKind: PLACE.node, shape: { kind: "point", p: v3(nodes[to].xyz_mm), side: nodeSide(to) } };
    if (supports[to]) {
      const s = supports[to];
      return { toKind: PLACE.support, shape: { kind: "point", p: v3(s.position_mm), side: s.width_mm ?? 2 * (s.radius_mm ?? 40) } };
    }
    const dot = String(to).indexOf(".");
    const hp = dot > 0 ? supports[to.slice(0, dot)]?.suspension?.hardpoints_mm?.[to.slice(dot + 1)] : null;
    if (Array.isArray(hp)) return { toKind: PLACE.hardpoint, shape: { kind: "point", p: v3(hp), side: 40 } };
    return null;
  };

  // part.connects_to: one joint per (two ends, via)
  const seen = new Set();
  for (const p of design.parts ?? []) {
    for (const c of p.connects_to ?? []) {
      const key = `${c.via}|${[p.id, c.to].sort().join("|")}`;
      if (seen.has(key)) continue;
      seen.add(key);
      count(c.via);
      const kind = kindOf(c.via);
      const target = shapeOf(c.to);
      const joint = { kind: c.via, source: "part", owner: p.id, to: c.to, toKind: target?.toKind ?? null, rule: false, marks: [] };
      out.joints.push(joint);
      if (!target || !p.box) {
        out.unplaced += 1;
        continue;
      }
      const box = boxOf(p);
      const { p: a, q: b } = nearest(box, target.shape);
      const gap = a.distanceTo(b);
      const axis = gap > 1e-3 ? b.clone().sub(a).normalize() : faceNormal(box, a);
      const s = clampSize(SIZE_OF_SIDE * Math.min(minSide(box), target.shape.kind === "box" ? minSide(target.shape) : target.shape.side));
      const at = a.clone().add(b).multiplyScalar(0.5);
      joint.marks.push({ shape: kind.mark, at: at.toArray(), axis: axis.toArray(), scale: kind.mark === "bead" ? s * 0.6 : s, gap });
    }
  }

  // the frame's nodes where members meet (THROUGH_COS: two members of one section in one line are
  // one tube going through the node, not a joint; a node is a joint when, so counted, two or more
  // pieces meet there, or when node.joint says how)
  for (const n of design.nodes ?? []) {
    const ms = atNode[n.id] ?? [];
    if (ms.length < 2) continue;
    const c = v3(n.xyz_mm);
    const dirs = ms.map((m) => {
      const other = nodes[m.node_a === n.id ? m.node_b : m.node_a];
      return other ? v3(other.xyz_mm).sub(c).normalize() : Z.clone();
    });
    const through = new Set();
    for (let i = 0; i < ms.length; i += 1) {
      for (let j = i + 1; j < ms.length; j += 1) {
        if (through.has(i) || through.has(j)) continue;
        if (ms[i].section === ms[j].section && dirs[i].dot(dirs[j]) <= -THROUGH_COS) through.add(i).add(j);
      }
    }
    const pieces = ms.length - through.size / 2;
    if (pieces < 2 && !n.joint) continue;
    const rule = !n.joint;
    const via = n.joint ?? FRAME_NODE_RULE;
    count(via);
    if (rule) out.frameRule += 1;
    const joint = { kind: via, source: "node", owner: "frame", to: n.id, toKind: "frame_node", rule, marks: [] };
    out.joints.push(joint);
    const sides = ms.map((m) => sectionSides(design, m));
    const big = Math.max(...sides.flat());
    if (kindOf(via).mark === "bead") {
      // a bead round each member that ends at the node (not a tube going through), where it meets
      // the widest of the others; all of them when every member goes through (a butt joint written in the data)
      const ending = ms.map((_, i) => i).filter((i) => !through.has(i));
      for (const i of ending.length ? ending : ms.map((_, k) => k)) {
        const [w, h] = sides[i];
        const off = Math.max(...sides.filter((_, j) => j !== i).flat()) / 2;
        const r = 0.5 * Math.max(w, h) * 1.06 + 2; // round the tube's corners
        joint.marks.push({ shape: "bead", at: c.clone().addScaledVector(dirs[i], off).toArray(), axis: dirs[i].toArray(), scale: r, gap: 0 });
      }
    } else {
      // one mark at the node, across the plane of its members, as big as its widest member
      let axis = dirs[0].clone().cross(dirs.find((d) => Math.abs(d.dot(dirs[0])) < THROUGH_COS) ?? dirs[1]);
      if (axis.lengthSq() < 1e-6) axis = Z.clone();
      joint.marks.push({ shape: kindOf(via).mark, at: c.toArray(), axis: axis.normalize().toArray(), scale: big * 1.2, gap: 0 });
    }
  }
  return out;
}

// ---- the marks in a Workbench3D ---------------------------------------------------------
export class JointMarks {
  constructor(bench) {
    this.bench = bench;
    this.group = new THREE.Group();
    this.group.name = "joints";
    this.group.visible = false;
    bench.body.add(this.group);
    this.on = false;
    this.info = readJoints(null);
    this.meshes = {}; // shape -> InstancedMesh
    this.slots = []; // per instance: { shape, i, joint, mark, matrix }
  }

  setDesign(design) {
    for (const m of Object.values(this.meshes)) {
      m.geometry.dispose();
      m.material.dispose();
      this.group.remove(m);
    }
    this.meshes = {};
    this.slots = [];
    this.info = readJoints(design);
    const byShape = {};
    for (const j of this.info.joints) for (const mk of j.marks) (byShape[mk.shape] ??= []).push({ j, mk });
    const real = this.bench.look.real;
    const color = new THREE.Color();
    for (const [shape, list] of Object.entries(byShape)) {
      const mat = real
        ? new THREE.MeshStandardMaterial({ color: "#ffffff", roughness: 0.45, metalness: 0.15 })
        : new THREE.MeshLambertMaterial({ color: "#ffffff" });
      const mesh = new THREE.InstancedMesh(MARK_SHAPES[shape](), mat, list.length);
      mesh.frustumCulled = false; // the instances spread over the vehicle
      mesh.renderOrder = 3; // after the see-through parts (renderOrder 2)
      mesh.userData.shape = shape;
      const q = new THREE.Quaternion();
      list.forEach(({ j, mk }, i) => {
        q.setFromUnitVectors(Z, v3(mk.axis).normalize());
        const matrix = new THREE.Matrix4().compose(v3(mk.at), q, new THREE.Vector3(mk.scale, mk.scale, mk.scale));
        mesh.setMatrixAt(i, matrix);
        mesh.setColorAt(i, color.set(kindOf(j.kind).color));
        this.slots.push({ shape, i, joint: j, mark: mk, matrix, shown: true });
      });
      this.meshes[shape] = mesh;
      this.group.add(mesh);
    }
    this.sync();
  }

  // On / off (the toggle 接合): the marks, and the parts see-through so the marks inside show.
  setOn(on) {
    on = !!on;
    if (on === this.on) return this.sync();
    this.on = on;
    this.group.visible = on;
    this.bench.applyPartLook(); // ends with partLook()
    this.sync();
  }

  // The parts' see-through while the joints are on (called at the end of applyPartLook).
  // Diagram look: at most SEE_ZU of opacity; real look: SEE_REAL, not writing depth.
  partLook() {
    const real = this.bench.look.real;
    for (const o of Object.values(this.bench.partObjs)) {
      o.group.traverse((x) => {
        const m = x.isMesh ? x.material : null;
        if (!m || m.userData.baseColor === undefined) return;
        if (this.on) {
          if (real) {
            m.userData.jointsWas ??= { transparent: m.transparent, opacity: m.opacity, depthWrite: m.depthWrite };
            if (!m.transparent) m.needsUpdate = true;
            m.transparent = true;
            m.depthWrite = false;
            m.opacity = SEE_REAL;
          } else m.opacity = Math.min(m.opacity, SEE_ZU);
        } else if (m.userData.jointsWas) {
          const w = m.userData.jointsWas;
          if (m.transparent !== w.transparent) m.needsUpdate = true;
          Object.assign(m, w);
          delete m.userData.jointsWas;
        }
      });
    }
  }

  // Which marks are shown: a part's with the part (the parts' toggle, off board, a step of the
  // assembly not yet come), the frame's with the frame. withOffset (the assembly): a part's
  // marks also move with its group (a part coming in on its path).
  sync(withOffset = false) {
    if (!this.on) return;
    const b = this.bench;
    const zero = new THREE.Matrix4().makeScale(0, 0, 0);
    const tmp = new THREE.Matrix4();
    const dirty = new Set();
    for (const s of this.slots) {
      const owner = s.joint.owner;
      const po = b.partObjs[owner];
      let shown;
      let off = null;
      if (owner === "frame") shown = b.model.visible;
      else if (po) {
        shown = b.parts.visible && po.group.visible;
        if (withOffset && po.group.position.lengthSq() > 0) off = po.group.position;
      } else shown = b.wheels.visible; // a wheel listed as a part, drawn from its support
      const m = !shown ? zero : off ? tmp.copy(s.matrix).premultiply(new THREE.Matrix4().makeTranslation(off.x, off.y, off.z)) : s.matrix;
      s.shown = shown;
      s.now = shown ? [m.elements[12], m.elements[13], m.elements[14]] : null;
      this.meshes[s.shape].setMatrixAt(s.i, m);
      dirty.add(s.shape);
    }
    for (const k of dirty) this.meshes[k].instanceMatrix.needsUpdate = true;
  }

  // What the tests and the legend read.
  summary() {
    return {
      on: this.on,
      counts: { ...this.info.counts },
      frameRule: this.info.frameRule,
      unplaced: this.info.unplaced,
      instances: Object.fromEntries(Object.entries(this.meshes).map(([k, m]) => [k, m.count])),
      marks: this.slots.map((s) => ({ kind: s.joint.kind, source: s.joint.source, owner: s.joint.owner, to: s.joint.to, toKind: s.joint.toKind, rule: s.joint.rule, shape: s.shape, shown: s.shown, now: s.now ?? null, ...s.mark })),
    };
  }
}
const SEE_REAL = 0.28;
const SEE_ZU = 0.14;

// ---- the legend -----------------------------------------------------------------------
// The kinds in the table's order, then any other via; each with its swatch, name and count.
export function jointLegendHtml(info) {
  const kinds = [...Object.keys(JOINT_KINDS).filter((k) => info.counts[k]), ...Object.keys(info.counts).filter((k) => !JOINT_KINDS[k]).sort()];
  const total = Object.values(info.counts).reduce((a, b) => a + b, 0);
  const rows = kinds
    .map((k) => {
      const kd = kindOf(k);
      const extra = k === "weld" && info.frameRule ? `<small>枠の節 ${info.frameRule}</small>` : "";
      return `<span class="jl-sw jl-${kd.mark}" style="--jc:${kd.color}"></span><span class="jl-nm" data-kind="${esc(k)}">${esc(kd.ja)}${extra}</span><span class="jl-n" data-count="${esc(k)}">${info.counts[k]}</span>`;
    })
    .join("");
  const notes = [info.frameRule ? `<div class="jl-note">※ ${esc(NODE_RULE_NOTE)}</div>` : "", total - info.frameRule > 0 ? `<div class="jl-note">${esc(PLACE_NOTE)}</div>` : ""];
  if (info.unplaced) notes.push(`<div class="jl-note">相手の見つからない接合 ${info.unplaced} は印なし</div>`);
  return `<div class="jl-ttl">接合 <small>${total} 所</small></div><div class="jl-rows">${rows}</div>${notes.join("")}`;
}

// The legend's box in a host (the bench's viewport, the A7 3D), shown while the joints are on.
export function renderJointLegend(host, marks, on) {
  let el = host.querySelector(":scope > .joint-legend");
  if (!el) {
    if (!on) return null;
    el = document.createElement("div");
    el.className = "joint-legend";
    el.dataset.kind = "joint-legend";
    host.append(el);
  }
  el.hidden = !on;
  if (on) {
    const key = JSON.stringify(marks.info.counts) + marks.info.frameRule;
    if (el.dataset.key !== key) {
      el.innerHTML = jointLegendHtml(marks.info);
      el.dataset.key = key;
    }
  }
  return el;
}

// The A7 tab's own switch (its 3D has no toggle row): a 接合 button and the legend in the 3D's box.
export function assemblyJointControls(host, view, onChange = null) {
  const btn = document.createElement("button");
  btn.className = "asm-joints-btn";
  btn.dataset.toggle = "joints";
  btn.textContent = "接合";
  host.append(btn);
  const set = (on) => {
    btn.classList.toggle("on", on);
    view.joints.setOn(on);
    view.joints.sync(true);
    renderJointLegend(host, view.joints, on);
    onChange?.(on);
  };
  btn.addEventListener("click", () => set(!view.joints.on));
  const want = new URLSearchParams(location.search).get("on")?.split(",").includes("joints") ?? false;
  return { set, want, refresh: () => renderJointLegend(host, view.joints, view.joints.on) };
}
