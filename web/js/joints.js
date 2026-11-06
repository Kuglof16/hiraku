// The joints of a design on the 3D (workbench3d.js, assembly.js): where parts and frame
// members are joined and how, read from the design only.
//
// Two sources in the design:
//   - part.connects_to [{to, via, at_mm?, axis?}]: the part is joined to `to` (a part, a frame
//     member, a frame node, a support or "<support>.<hardpoint>") by `via` (bolt, weld, bushing,
//     ...); at_mm and axis (contract 070d49c), when written, are where and along what it is drawn
//   - the frame's nodes where two or more members meet: node.joint (weld / bolt / pin), node by
//     node; a node without it is drawn by FRAME_NODE_RULE (below).
//
// One joint is one place: the entries of connects_to with the same two ends and the same via
// are one joint (a wheel "rests" on its support for load, steer and brake: one place), and a
// frame node is one joint however many members meet there. The legend's numbers count these.
//
// The marks are drawn with one InstancedMesh per shape (a few draw calls for hundreds of
// marks), in the body group (ISO mm), so they move with the body's pose.

import * as THREE from "three";
import { esc } from "./labels.js";
import { frameEnds, TUBE, TUBE_NOTE } from "./tubes.js"; // where each tube's end is cut (the bead goes there)

// ---- the table: what each kind is drawn as -------------------------------------------
// mark: the shape (MARK_SHAPES); color: its colour (the same in both looks); ja: its name
// on the screen. A via not listed here is drawn as OTHER (its own name, the common dot).
export const JOINT_KINDS = {
  weld: { ja: "溶接 (すみ肉)", mark: "bead", color: "#5e4c3e" }, // a fillet bead along the seam (WELD_BEAD)
  bolt: { ja: "ボルト", mark: "hex", color: "#2d3a4a" }, // a small hexagon (a bolt's head)
  bushing: { ja: "ブッシュ", mark: "ring", color: "#151515" }, // a rubber ring
  ball_joint: { ja: "ボールジョイント", mark: "ball", color: "#2f7fd0" }, // a ball
  // a thin rod through the joint; deep indigo, not the purple it was (#8e44c8 was 30 apart in
  // CIELAB from the packager's module colour #8e6bbf, proposals.js; this is 44 from its nearest)
  pin: { ja: "ピン", mark: "pin", color: "#1d1d9e" },
  // a short toothed shaft; spring green, not #13a08a (11 apart in CIELAB from the body module's #57b8a0; this is 51)
  spline: { ja: "スプライン", mark: "spline", color: "#00ff80" },
  rest: { ja: "載せる", mark: "dot", color: "#9a8f78" },
  friction: { ja: "摩擦", mark: "dot", color: "#c9a227" },
  // bright magenta, not #d23c8a (22 apart in CIELAB from the steering module's #e2759f; this is 55)
  wire: { ja: "電線", mark: "dot", color: "#ff1fd2" },
  hose: { ja: "ホース", mark: "dot", color: "#3fa7c9" },
};
const OTHER = { mark: "dot", color: "#8a8577" };

// The weld's bead (screen choices, no source): a fillet weld's triangle section, legs leg_mm
// (one on the face welded to, one up the welded piece's side), laid along the seam: round the
// ending member's section outline (rect: its 4 sides, round: round_segments chords) or along
// the edges of a bracket's face that lie on the member's face (weldBeadGeometry). Each piece of
// the seam is one instance of a unit bar (MARK_SHAPES.bead) stretched along its length only, so
// the section stays leg_mm; a piece runs on past its ends by end_ext x leg_mm to close the
// corners. The colour (JOINT_KINDS.weld) is a dark grey-brown (steel darkened by the heat),
// apart from the bushing's black ring. The bar stands lift_mm off both faces it lies on (and its
// material is drawn with a negative polygonOffset, BEAD_POLYGON_OFFSET): its two legs would
// otherwise share the faces' planes and the depth test would pick between them pixel by pixel.
export const WELD_BEAD = { leg_mm: 5, round_segments: 16, end_ext: 1, round_end_ext: 0.3, lift_mm: 0.2 };
const BEAD_POLYGON_OFFSET = { factor: -1, units: -4 };
// The bead's material in the real look (screen choices, no source): a dark weld metal, not a
// matt paint (metalness 0 lit the colour lighter than the painted frame it lies on).
export const BEAD_REAL = { roughness: 0.55, metalness: 0.6 };
export const BEAD_NOTE = `溶接のビードは脚 ${WELD_BEAD.leg_mm} mm の三角の断面で継ぎ目に沿う。寸法と色は画面の決め (出典なし)`;
export const kindOf = (via) => JOINT_KINDS[via] ?? { ...OTHER, ja: String(via) };

// A frame node that says how it is joined (node.joint, docs/contract.md: weld / bolt / pin) is
// drawn so, whatever made the design. Only a node without it follows the rule, drawn as welded.
// The contract (d9631a5) says the ladder_4w generator's frame nodes are welded, and
// design.archetype (070d49c) names the generator:
//   archetype "ladder_4w"      the rule for its nodes without node.joint (NODE_RULE_NOTE)
//   another archetype          no rule: a node without node.joint has no mark (counted, the legend says so)
//   no archetype (a design     the rule for its nodes without node.joint, as before; the legend says
//   Claude wrote, or one made  only that those nodes do not say how (NODE_RULE_UNWRITTEN_NOTE), not
//   before 070d49c)            that the design is old: the nodes with node.joint keep theirs
export const FRAME_NODE_RULE = "weld";
export const RULE_ARCHETYPE = "ladder_4w";
export const NODE_RULE_NOTE = "ladder_4w の型の枠の節は溶接 (契約) なので、つなぎ方の書いていない節を決まりで溶接として描いている";
export const NODE_RULE_UNWRITTEN_NOTE = "設計につなぎ方の書いていない枠の節を、決まりで溶接として描いている (書いてある節は、書いてあるとおりに描く)";
// Two members of the same section going on in one line through a node (THROUGH_COS) are one tube.
export const THROUGH_NOTE = "一直線に続く同じ断面の 2 本は 1 本の管として、つなぎ目に数えない";
// connects_to with at_mm (and axis) is drawn at that point (and along that axis); one without
// at_mm is placed from the boxes (PLACE below): the legend counts those (guessNote).
export const PLACE_NOTE = "場所がデータに無い部品の印は、2 つの相手の箱のいちばん近い所の中ほどに置いた";
// short on the legend; how they were placed (PLACE_NOTE) is its tooltip
export const guessNote = (n) => `場所はおおよそ ${n} 所 (データに場所が無いもの)`;

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
  bead: () => beadBarGeometry(WELD_BEAD.leg_mm, WELD_BEAD.lift_mm), // one piece of a seam (x: 0..1, stretched per piece)
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

const vec3OK = (a) => Array.isArray(a) && a.length === 3 && a.every((x) => Number.isFinite(x));

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
// Every joint of a design: { joints: [...], counts: {kind: n}, frameRule: n, unplaced: n, byPart: n }
// (byPart: joints by via ball_joint whose one end is a part of kind ball_joint: counted, no mark).
// A joint: { kind (the via or node joint), source ("part" | "node"), owner, to, toKind,
// rule (true: the frame node rule), marks: [{shape, at [x,y,z], axis [x,y,z], scale, gap}] }.
export function readJoints(design) {
  const out = { joints: [], counts: {}, frameRule: 0, unplaced: 0, byPart: 0, guessed: 0, fromData: 0, unstated: 0, ruleBasis: null, through: 0 };
  if (!design) return out;
  // the frame node rule, for the nodes without node.joint only: by the archetype (ladder_4w), or,
  // in a design without one, as the nodes' joint is not written ("unwritten")
  const archetype = typeof design.archetype === "string" && design.archetype ? design.archetype : null;
  const useRule = archetype == null || archetype === RULE_ARCHETYPE;
  out.ruleBasis = archetype == null ? "unwritten" : useRule ? "archetype" : null;
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
      // the place and axis written in the data (contract 070d49c), when they are
      const dataAt = vec3OK(c.at_mm) ? v3(c.at_mm) : null;
      const dataAxis = vec3OK(c.axis) && v3(c.axis).lengthSq() > 1e-12 ? v3(c.axis).normalize() : null;
      if (!dataAt && (!target || !p.box)) {
        out.unplaced += 1;
        continue;
      }
      // a ball joint that is a part of the design (kind ball_joint, drawn as a ball by shapes.js):
      // the part is its mark, no second ball (counted all the same)
      if (c.via === "ball_joint" && (p.kind === "ball_joint" || parts[c.to]?.kind === "ball_joint")) {
        joint.byPart = c.to in parts && parts[c.to].kind === "ball_joint" ? c.to : p.id;
        out.byPart += 1;
        continue;
      }
      const box = p.box ? boxOf(p) : null;
      const sides = [box ? minSide(box) : null, target ? (target.shape.kind === "box" ? minSide(target.shape) : target.shape.side) : null].filter((x) => x != null);
      const s = sides.length ? clampSize(SIZE_OF_SIDE * Math.min(...sides)) : SIZE_MIN_MM;
      let at;
      let axis = dataAxis;
      let gap = 0;
      if (dataAt) {
        at = dataAt;
        out.fromData += 1;
        if (!axis && box && target) {
          const { p: a, q: b } = nearest(box, target.shape);
          axis = a.distanceTo(b) > 1e-3 ? b.clone().sub(a).normalize() : faceNormal(box, closestOnBox(box, at));
        }
      } else {
        const { p: a, q: b } = nearest(box, target.shape);
        gap = a.distanceTo(b);
        at = a.clone().add(b).multiplyScalar(0.5);
        axis ??= gap > 1e-3 ? b.clone().sub(a).normalize() : faceNormal(box, a);
        out.guessed += 1;
      }
      axis ??= Z.clone();
      joint.placed = dataAt ? "data" : "guess";
      joint.marks.push({ shape: kind.mark, at: at.toArray(), axis: axis.toArray(), scale: kind.mark === "bead" ? s * 0.6 : s, gap, placed: joint.placed, axisFrom: dataAxis ? "data" : "guess" });
    }
  }

  const tubeEnds = frameEnds(design);
  out.tubes = Object.keys(tubeEnds).length; // members drawn as hollow tubes (the legend's TUBE_NOTE)
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
    if (through.size) out.through += through.size / 2;
    if (pieces < 2 && !n.joint) continue;
    const rule = !n.joint;
    if (rule && !useRule) {
      // another archetype, and the node does not say how it is joined: no mark, counted for the legend
      out.unstated += 1;
      continue;
    }
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
      // (tubes.js frameEnds: a tube cut on the receiver's face has its bead at that cut, trim from
      // the node; the carrier, which the others butt on, has none)
      const endOf = (i) => tubeEnds[ms[i].id]?.[ms[i].node_a === n.id ? "a" : "b"] ?? null;
      const ending = ms.map((_, i) => i).filter((i) => !through.has(i) && endOf(i)?.kind !== "carrier");
      for (const i of ending.length ? ending : ms.map((_, k) => k)) {
        const [w, h] = sides[i];
        const cut = endOf(i);
        // a cut end stands TUBE.cut_gap_mm off the receiver's face: the bead sits on the face itself
        // (lifted by WELD_BEAD.lift_mm only), its leg over that gap, not cut_gap + lift away from it
        const off = cut && (cut.kind === "cut" || cut.kind === "butt") ? cut.trim - (cut.kind === "cut" ? TUBE.cut_gap_mm : 0) : Math.max(...sides.filter((_, j) => j !== i).flat()) / 2;
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
  weldBeadGeometry(design, out);
  return out;
}

// ---- the weld beads: the seam of each weld mark (WELD_BEAD) ------------------------------------
// A unit bar of a seam: x along the seam 0..1; the triangle's right angle on the seam line, one leg
// along +y (out, on the face welded to) and one along +z (n, up the welded piece's side); the
// whole section moved by lift off both faces (+y and +z).
function beadBarGeometry(leg, lift) {
  const P = [[0, 0, 0], [0, leg, 0], [0, 0, leg], [1, 0, 0], [1, leg, 0], [1, 0, leg]].map(([x, y, z]) => [x, y + lift, z + lift]);
  const T = [[0, 2, 1], [3, 4, 5], [0, 1, 4], [0, 4, 3], [0, 3, 5], [0, 5, 2], [1, 2, 5], [1, 5, 4]];
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(T.flat().flatMap((i) => P[i]), 3));
  g.computeVertexNormals();
  return g;
}

// A frame member as a box, as workbench3d.js draws it (memberBasis: x along node_a -> node_b, z
// toward ISO up (ISO x when the member is near upright), the section's b along y and h along z).
function memberFrame(design, m, nodes) {
  const a = nodes[m.node_a];
  const b = nodes[m.node_b];
  if (!a || !b) return null;
  const A = v3(a.xyz_mm);
  const x = v3(b.xyz_mm).sub(A);
  const len = x.length();
  if (len < 1e-6) return null;
  x.normalize();
  const up = Math.abs(x.dot(Z)) > 0.95 ? new THREE.Vector3(1, 0, 0) : Z.clone();
  const z = up.sub(x.clone().multiplyScalar(up.dot(x))).normalize();
  const y = z.clone().cross(x);
  const sec = design.sections?.[m.section];
  const [w, h] = sectionSides(design, m);
  return { c: A.addScaledVector(x, len / 2), axes: [x, y, z], h: [len / 2, w / 2, h / 2], sw: w, sh: h, round: sec?.shape === "round_tube" || sec?.shape === "round_solid" };
}

// A part's box (boxOf) in the same form.
function frameOfBox(box) {
  return { c: box.c, axes: [new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 1, 0), Z.clone()].map((a) => a.applyQuaternion(box.q)), h: [box.h.x, box.h.y, box.h.z] };
}

// A seam piece: { a, b (the ends on the seam line), out (the leg on the face), n (the leg up the
// piece), ext (how far it runs on past each end, mm) }.
const seam = (a, b, out, n, ext) => ({ a, b, out, n, ext });

// Round a circle of radius r about P across n (a round tube's end, or a mark not placed on a face).
function circleSeam(P, n, r) {
  const u = Math.abs(n.x) < 0.9 ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 1, 0);
  u.addScaledVector(n, -u.dot(n)).normalize();
  const v = n.clone().cross(u);
  const k = WELD_BEAD.round_segments;
  const pt = (i) => P.clone().addScaledVector(u, r * Math.cos((2 * Math.PI * i) / k)).addScaledVector(v, r * Math.sin((2 * Math.PI * i) / k));
  const out = [];
  for (let i = 0; i < k; i += 1) {
    const a = pt(i);
    const b = pt(i + 1);
    out.push(seam(a, b, a.clone().add(b).multiplyScalar(0.5).sub(P).normalize(), n.clone(), WELD_BEAD.leg_mm * WELD_BEAD.round_end_ext));
  }
  return out;
}

// Round the ending member's section outline (f: memberFrame) in the plane through P across n.
function sectionSeam(P, f, n) {
  if (f.round) return circleSeam(P, n, f.sw / 2);
  const [, y, z] = f.axes;
  const c = (sy, sz) => P.clone().addScaledVector(y, (sy * f.sw) / 2).addScaledVector(z, (sz * f.sh) / 2);
  const ext = WELD_BEAD.leg_mm * WELD_BEAD.end_ext;
  return [
    seam(c(-1, 1), c(1, 1), z.clone(), n.clone(), ext),
    seam(c(1, -1), c(-1, -1), z.clone().negate(), n.clone(), ext),
    seam(c(1, 1), c(1, -1), y.clone(), n.clone(), ext),
    seam(c(-1, -1), c(-1, 1), y.clone().negate(), n.clone(), ext),
  ];
}

// Where a box (a bracket, a gusset) sits on another box (a member, a part): a face of each, facing
// each other and within CONTACT_MM of one plane, with the box's sides along the other face's sides.
// The seam: the box face's edges that lie on the other face (within EDGE_MM of it), clipped to the
// part of the face they share. A box touching several members gets the edges on each.
const CONTACT_MM = 1;
const EDGE_MM = 0.5;
function contactSeams(box, other) {
  const out = [];
  const ext = WELD_BEAD.leg_mm * WELD_BEAD.end_ext;
  for (let i = 0; i < 3; i += 1) {
    for (const s of [-1, 1]) {
      const nb = box.axes[i].clone().multiplyScalar(s);
      const cb = box.c.clone().addScaledVector(nb, box.h[i]);
      const [i1, i2] = [0, 1, 2].filter((k) => k !== i);
      for (let j = 0; j < 3; j += 1) {
        for (const t of [-1, 1]) {
          const no = other.axes[j].clone().multiplyScalar(t);
          if (nb.dot(no) > -0.999) continue;
          const co = other.c.clone().addScaledVector(no, other.h[j]);
          const d = cb.clone().sub(co);
          if (Math.abs(d.dot(no)) > CONTACT_MM) continue;
          const [j1, j2] = [0, 1, 2].filter((k) => k !== j);
          const u = other.axes[j1];
          const v = other.axes[j2];
          const par = (p, q) => Math.abs(p.dot(q)) > 0.999;
          let hu;
          let hv;
          if (par(box.axes[i1], u) && par(box.axes[i2], v)) [hu, hv] = [box.h[i1], box.h[i2]];
          else if (par(box.axes[i2], u) && par(box.axes[i1], v)) [hu, hv] = [box.h[i2], box.h[i1]];
          else continue; // turned on the face: no seam worked out
          const [A, B, cu, cv] = [other.h[j1], other.h[j2], d.dot(u), d.dot(v)];
          const U = [Math.max(cu - hu, -A), Math.min(cu + hu, A)];
          const V = [Math.max(cv - hv, -B), Math.min(cv + hv, B)];
          if (U[1] - U[0] < 1 || V[1] - V[0] < 1) continue;
          const pt = (x, y) => co.clone().addScaledVector(u, x).addScaledVector(v, y);
          for (const sg of [-1, 1]) {
            const eu = cu + sg * hu;
            if (Math.abs(eu) <= A + EDGE_MM) out.push(seam(pt(eu, V[0]), pt(eu, V[1]), u.clone().multiplyScalar(sg), no.clone(), ext));
            const ev = cv + sg * hv;
            if (Math.abs(ev) <= B + EDGE_MM) out.push(seam(pt(U[0], ev), pt(U[1], ev), v.clone().multiplyScalar(sg), no.clone(), ext));
          }
        }
      }
    }
  }
  return out;
}

// The seam of every weld mark (mk.bead = { how, seams }): how is "section" (a frame node: round
// the ending member's section outline, on the plane of the mark), "contact" (a part: along its
// box's edges on the faces it touches) or "as_is" (no face worked out: a ring of the mark's size
// where the mark is). out.beadSeams counts the marks by how.
export function weldBeadGeometry(design, out) {
  out.beadSeams = { section: 0, contact: 0, as_is: 0 };
  if (!design) return out;
  const parts = Object.fromEntries((design.parts ?? []).map((p) => [p.id, p]));
  const nodes = Object.fromEntries((design.nodes ?? []).map((n) => [n.id, n]));
  const members = Object.fromEntries((design.members ?? []).map((m) => [m.id, m]));
  const atNode = {};
  for (const m of design.members ?? []) for (const end of [m.node_a, m.node_b]) (atNode[end] ??= []).push(m);
  for (const j of out.joints) {
    for (const mk of j.marks) {
      if (mk.shape !== "bead") continue;
      const at = v3(mk.at);
      const axis = v3(mk.axis).normalize();
      let seams = [];
      let how = "as_is";
      if (j.source === "node" && nodes[j.to]) {
        const c = v3(nodes[j.to].xyz_mm);
        const m = (atNode[j.to] ?? []).find((x) => {
          const o = nodes[x.node_a === j.to ? x.node_b : x.node_a];
          return o && v3(o.xyz_mm).sub(c).normalize().dot(axis) > 0.999;
        });
        const f = m ? memberFrame(design, m, nodes) : null;
        if (f) [seams, how] = [sectionSeam(at, f, axis), "section"];
      } else if (j.source === "part" && parts[j.owner]?.box) {
        const others = parts[j.to]?.box
          ? [frameOfBox(boxOf(parts[j.to]))]
          : (members[j.to] ? [members[j.to]] : atNode[j.to] ?? []).map((m) => memberFrame(design, m, nodes)).filter(Boolean);
        const box = frameOfBox(boxOf(parts[j.owner]));
        for (const o of others) seams.push(...contactSeams(box, o));
        if (seams.length) how = "contact";
      }
      if (!seams.length) seams = circleSeam(at, axis, mk.scale);
      mk.bead = { how, seams: seams.map((s) => ({ a: s.a.toArray(), b: s.b.toArray(), out: s.out.toArray(), n: s.n.toArray(), ext: s.ext })) };
      out.beadSeams[how] += 1;
    }
  }
  return out;
}

// The instance matrix of a seam piece: the unit bar stretched along a -> b (and ext past each end).
function seamMatrix(s) {
  const a = v3(s.a);
  const t = v3(s.b).sub(a);
  const len = t.length();
  t.normalize();
  const out = v3(s.out).addScaledVector(t, -v3(s.out).dot(t)).normalize();
  const n = v3(s.n);
  n.addScaledVector(t, -n.dot(t)).addScaledVector(out, -n.dot(out)).normalize();
  return new THREE.Matrix4().makeBasis(t.clone().multiplyScalar(len + 2 * s.ext), out, n).setPosition(a.addScaledVector(t, -s.ext));
}

// ---- the marks in a Workbench3D ---------------------------------------------------------
const PICK_PX = 12; // how near the pointer a mark is picked (screen choice)

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
    this.hoverTag();
  }

  // Pointing at a mark: a tag saying what kind of joint it is and which two things it joins.
  hoverTag() {
    const canvas = this.bench.renderer?.domElement;
    const host = canvas?.parentElement;
    if (!canvas || !host) return;
    const tag = document.createElement("div");
    tag.className = "joint-tag";
    tag.hidden = true;
    host.append(tag);
    let queued = null;
    canvas.addEventListener("pointermove", (ev) => {
      if (queued) return;
      queued = requestAnimationFrame(() => {
        queued = null;
        const hit = this.on ? this.pick(ev.clientX, ev.clientY) : null;
        tag.hidden = !hit;
        if (!hit) return;
        const r = host.getBoundingClientRect();
        tag.textContent = this.describe(hit.joint);
        tag.dataset.owner = hit.joint.owner;
        tag.dataset.to = hit.joint.to;
        tag.style.left = `${ev.clientX - r.left + 14}px`;
        tag.style.top = `${ev.clientY - r.top + 10}px`;
      });
    });
    canvas.addEventListener("pointerleave", () => (tag.hidden = true));
  }

  // The shown mark nearest to a point of the window (within PICK_PX), or null.
  pick(x, y) {
    const cam = this.bench.camera;
    const rect = this.bench.renderer.domElement.getBoundingClientRect();
    this.group.updateWorldMatrix(true, false);
    let best = null;
    const p = new THREE.Vector3();
    for (const s of this.slots) {
      if (!s.shown) continue;
      p.copy(v3(s.mark.at)).applyMatrix4(this.group.matrixWorld).project(cam);
      if (p.z > 1) continue;
      const d = Math.hypot(rect.left + ((p.x + 1) / 2) * rect.width - x, rect.top + ((1 - p.y) / 2) * rect.height - y);
      if (d < PICK_PX && (!best || d < best.d)) best = { d, slot: s };
    }
    return best ? best.slot : null;
  }

  // "溶接: 前の隔壁 と 部材 rail_L_FA": the kind, then the owner and what it is joined to by name.
  describe(j) {
    const parts = Object.fromEntries((this.bench.design?.parts ?? []).map((p) => [p.id, String(p.name ?? p.id).replace(/\s*[（(].*$/, "") || p.id]));
    const members = Object.fromEntries((this.bench.design?.members ?? []).filter((m) => m.name).map((m) => [m.id, m.name]));
    const name = (id, kind) =>
      parts[id] ? parts[id] : kind === PLACE.member || members[id] ? members[id] ?? `部材 ${id}` : kind === PLACE.node || kind === "frame_node" ? `節点 ${id}` : String(id);
    // a frame node's joint: the members that meet there, at that node
    if (j.source === "node") return `${kindOf(j.kind).ja}: 枠の節点 ${j.to} (そこで会う部材どうし)`;
    return `${kindOf(j.kind).ja}: ${name(j.owner, PLACE.part)} と ${name(j.to, j.toKind)}`;
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
      // a weld mark is one instance per piece of its seam (mk.bead.seams); every other mark is one instance
      const bead = shape === "bead";
      const n = bead ? list.reduce((a, { mk }) => a + mk.bead.seams.length, 0) : list.length;
      const mat = real
        ? new THREE.MeshStandardMaterial({ color: "#ffffff", roughness: bead ? BEAD_REAL.roughness : 0.45, metalness: bead ? BEAD_REAL.metalness : 0.15 })
        : new THREE.MeshLambertMaterial({ color: "#ffffff" });
      // In the see-through pass (opacity 1, so its colour is its own): three.js draws every opaque
      // thing before every see-through one whatever its renderOrder, so an opaque mark was drawn
      // first and the see-through parts (renderOrder 2, SEE_REAL / SEE_ZU) laid over it, a few
      // layers of a light part tinting a dark bead to a light ochre. Here renderOrder 3 holds.
      mat.transparent = true;
      if (bead) {
        mat.side = THREE.DoubleSide; // the bar's pieces are mirrored on some seams
        mat.polygonOffset = true; // drawn a little toward the eye (WELD_BEAD: off the faces' planes)
        mat.polygonOffsetFactor = BEAD_POLYGON_OFFSET.factor;
        mat.polygonOffsetUnits = BEAD_POLYGON_OFFSET.units;
      }
      const mesh = new THREE.InstancedMesh(MARK_SHAPES[shape](), mat, n);
      mesh.frustumCulled = false; // the instances spread over the vehicle
      mesh.renderOrder = 3; // after the see-through parts (renderOrder 2)
      mesh.userData.shape = shape;
      const q = new THREE.Quaternion();
      let k = 0;
      list.forEach(({ j, mk }) => {
        q.setFromUnitVectors(Z, v3(mk.axis).normalize());
        const matrix = new THREE.Matrix4().compose(v3(mk.at), q, new THREE.Vector3(mk.scale, mk.scale, mk.scale));
        const subs = bead ? mk.bead.seams.map((s) => ({ i: k++, matrix: seamMatrix(s) })) : [{ i: k++, matrix }];
        for (const s of subs) {
          mesh.setMatrixAt(s.i, s.matrix);
          mesh.setColorAt(s.i, color.set(kindOf(j.kind).color));
        }
        this.slots.push({ shape, i: subs[0]?.i ?? -1, subs, joint: j, mark: mk, matrix, shown: true });
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
      const place = (mx) => (!shown ? zero : off ? tmp.copy(mx).premultiply(new THREE.Matrix4().makeTranslation(off.x, off.y, off.z)) : mx);
      for (const sub of s.subs) this.meshes[s.shape].setMatrixAt(sub.i, place(sub.matrix));
      const m = place(s.matrix);
      s.shown = shown;
      s.now = shown ? [m.elements[12], m.elements[13], m.elements[14]] : null;
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
      byPart: this.info.byPart,
      guessed: this.info.guessed,
      fromData: this.info.fromData,
      unstated: this.info.unstated,
      ruleBasis: this.info.ruleBasis,
      through: this.info.through,
      beadSeams: { ...this.info.beadSeams },
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
  const ruleNote = info.ruleBasis === "unwritten" ? NODE_RULE_UNWRITTEN_NOTE : NODE_RULE_NOTE;
  const nodeJoints = info.joints ? info.joints.filter((j) => j.source === "node").length : 0;
  const notes = [info.frameRule ? `<div class="jl-note" data-note="rule">※ ${esc(ruleNote)}</div>` : ""];
  if (info.unstated) notes.push(`<div class="jl-note" data-note="unstated">枠の節 ${info.unstated} 所はつなぎ方がデータに無いので印なし (型 ${esc(RULE_ARCHETYPE)} でない)</div>`);
  if (info.through || nodeJoints) notes.push(`<div class="jl-note" data-note="through">${esc(THROUGH_NOTE)}</div>`);
  if (info.guessed) notes.push(`<div class="jl-note" data-note="guess" data-n="${info.guessed}" title="${esc(PLACE_NOTE)}">${esc(guessNote(info.guessed))}</div>`);
  if (info.unplaced) notes.push(`<div class="jl-note">相手の見つからない接合 ${info.unplaced} は印なし</div>`);
  if (info.byPart) notes.push(`<div class="jl-note">ボールジョイントのうち ${info.byPart} は部品の球がそのまま印</div>`);
  if (info.counts.weld) notes.push(`<div class="jl-note" data-note="bead">${esc(BEAD_NOTE)}</div>`);
  if (info.tubes) notes.push(`<div class="jl-note" data-note="tube">${esc(TUBE_NOTE)}</div>`);
  if (info.beadSeams?.as_is) notes.push(`<div class="jl-note" data-note="bead-as-is">溶接の印のうち ${info.beadSeams.as_is} は触れる面が出せず、印の場所に輪で描いている</div>`);
  // the notes folded (the legend stays small over the 3D); open to read them
  const shown = notes.filter(Boolean);
  const more = shown.length ? `<details class="jl-more"><summary>注 ${shown.length}</summary>${shown.join("")}</details>` : "";
  return `<div class="jl-ttl">接合 <small>${total} 所</small></div><div class="jl-rows">${rows}</div>${more}`;
}

// The joints' legend and the modules' legend (modules.js .modlegend) in one host: when their
// boxes would cross, the joints' moves left of the modules' (side by side, not one over the other).
export function keepLegendsApart(host) {
  const jl = host.querySelector(":scope > .joint-legend");
  const ml = host.querySelector(":scope > .modlegend");
  if (!jl) return;
  jl.style.right = "";
  if (jl.hidden || !ml || ml.hidden) return;
  const a = jl.getBoundingClientRect();
  const b = ml.getBoundingClientRect();
  if (a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top) {
    jl.style.right = `${host.clientWidth - ml.offsetLeft + 10}px`;
  }
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
    const i = marks.info;
    const key = JSON.stringify([i.counts, i.frameRule, i.guessed, i.unstated, i.ruleBasis, i.through]);
    if (el.dataset.key !== key) {
      el.innerHTML = jointLegendHtml(marks.info);
      el.dataset.key = key;
    }
  }
  keepLegendsApart(host);
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
  // on at first, as the workbench's toggle (app.js); ?off=joints turns it off at load
  const want = !(new URLSearchParams(location.search).get("off")?.split(",").includes("joints") ?? false);
  return { set, want, refresh: () => renderJointLegend(host, view.joints, view.joints.on) };
}
