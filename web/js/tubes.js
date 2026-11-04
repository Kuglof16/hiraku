// The frame's tubes on the 3D (workbench3d.js memberGeometry): a member whose section has
// dims_mm is drawn hollow, as the analysis takes it (rect_tube b, h, t; round_tube d, t), and
// its ends are worked out from the frame's nodes (frameEnds):
//   through  two members of one section in one line at a node (THROUGH_COS, as joints.js): one
//            tube going on, so neither has an end face there and both end at the node
//   cut      a member butting on another (the receiver) is cut where it leaves the receiver's
//            outside (every point of its end outline out of the receiver), not run to the node
//   carrier  at a node with no tube going through, the member others butt on (the largest
//            section; on a tie the longest, then the first): it runs on past the node by the others' half
//            width along it, so the corner is closed
//   butt     a member in line with the receiver (another section): both end at the node
//   free     a node with this member alone: the tube's open end
// The receiver at a node: the tube going through (the largest section when there are two), else
// the carrier. A member without dims_mm (or of another shape) is drawn as before (a solid box or
// cylinder from node to node, frameEnds leaves it out).
//
// Screen choices (no source): a rect tube's corners are rounded, the outer radius corner_r_of_t
// times the wall (inner radius that less the wall); the arcs and the round tube in so many pieces.

import * as THREE from "three";
import { THROUGH_COS } from "./joints.js";

export const TUBE = { corner_r_of_t: 1.5, corner_segments: 4, round_segments: 24, collinear_cos: 0.95, max_trim_of_length: 0.45, cut_gap_mm: 0.3 };
export const TUBE_NOTE = `角管の角は外の半径を肉厚の ${TUBE.corner_r_of_t} 倍で丸めて描いている (画面の決め、出典なし)`;

const v3 = (p) => new THREE.Vector3(p[0], p[1], p[2]);

// A tube's section from dims_mm: { kind "rect"|"round", w, h, t, r (outer corner / round radius) },
// null when the section has no dims_mm or is not a tube (drawn as before).
export function tubeSection(design, m) {
  const s = design.sections?.[m.section];
  const d = s?.dims_mm;
  if (!d) return null;
  if (s.shape === "rect_tube" && d.b > 0 && d.h > 0 && d.t > 0 && 2 * d.t < Math.min(d.b, d.h)) {
    return { kind: "rect", w: d.b, h: d.h, t: d.t, r: Math.min(TUBE.corner_r_of_t * d.t, d.b / 2, d.h / 2) };
  }
  if (s.shape === "round_tube" && d.d > 0 && d.t > 0 && 2 * d.t < d.d) return { kind: "round", w: d.d, h: d.d, t: d.t, r: d.d / 2 };
  return null;
}

// The outline used where a member receives others (its outside): the tube's, else the section's
// sides as workbench3d.js sectionSize draws them.
function outlineOf(design, m) {
  const t = tubeSection(design, m);
  if (t) return t;
  const s = design.sections?.[m.section];
  const d = s?.dims_mm ?? {};
  if (s?.shape === "round_tube" || s?.shape === "round_solid") {
    const dia = d.d ?? d.D ?? 30;
    return { kind: "round", w: dia, h: dia, r: dia / 2 };
  }
  return { kind: "rect", w: d.b ?? d.w ?? d.h ?? 30, h: d.h ?? d.b ?? 30 };
}

// The member's axes as workbench3d.js memberBasis: x along node_a -> node_b, z toward ISO up (ISO x
// when the member is near upright), y = z x x; the section's w along y, h along z.
export function memberAxes(a, b) {
  const x = b.clone().sub(a).normalize();
  const up = Math.abs(x.z) > 0.95 ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 0, 1);
  const z = up.sub(x.clone().multiplyScalar(up.dot(x))).normalize();
  return { x, y: z.clone().cross(x), z };
}

// Points of a section's end face in (y, z) for trimming: the drawn outline (a tube's profile, else a
// rect's corners or 16 points round a circle) and a grid over the face (a face whose corners are out
// can still have its sides or middle in the receiver when it meets it at a slant).
function outlinePoints(o) {
  const pts = o.t ? profileOf(o).pts.map((p) => p.o) : [];
  const g = 8;
  for (let i = 0; i <= g; i += 1) {
    for (let j = 0; j <= g; j += 1) {
      const [u, v] = [(2 * i) / g - 1, (2 * j) / g - 1];
      if (o.kind === "round") {
        const [rr, a] = [(o.r * i) / g, (2 * Math.PI * j) / g];
        pts.push([rr * Math.cos(a), rr * Math.sin(a)]);
      } else pts.push([(u * o.w) / 2, (v * o.h) / 2]);
    }
  }
  return pts;
}

// Where the line p + s d leaves the receiver's outside (an endless prism along its axis through c):
// the largest s inside it, -Infinity when the line is never inside.
function exitAlong(rec, p, d) {
  const q = p.clone().sub(rec.c);
  if (rec.o.kind === "round") {
    const [py, pz, dy, dz] = [q.dot(rec.y), q.dot(rec.z), d.dot(rec.y), d.dot(rec.z)];
    const a = dy * dy + dz * dz;
    const c = py * py + pz * pz - rec.o.r * rec.o.r;
    if (a < 1e-12) return c <= 0 ? Infinity : -Infinity;
    const b = 2 * (py * dy + pz * dz);
    const disc = b * b - 4 * a * c;
    return disc < 0 ? -Infinity : (-b + Math.sqrt(disc)) / (2 * a);
  }
  let lo = -Infinity;
  let hi = Infinity;
  for (const [ax, half] of [[rec.y, rec.o.w / 2], [rec.z, rec.o.h / 2]]) {
    const pq = q.dot(ax);
    const dd = d.dot(ax);
    if (Math.abs(dd) < 1e-9) {
      if (Math.abs(pq) > half) return -Infinity;
      continue;
    }
    const s1 = (-half - pq) / dd;
    const s2 = (half - pq) / dd;
    lo = Math.max(lo, Math.min(s1, s2));
    hi = Math.min(hi, Math.max(s1, s2));
  }
  return lo <= hi ? hi : -Infinity;
}

// Half the width of a member's outline seen along a direction u (across its axis frame f).
function halfAlong(o, f, u) {
  if (o.kind === "round") return o.r * Math.sqrt(Math.max(0, 1 - u.dot(f.x) ** 2));
  return (Math.abs(u.dot(f.y)) * o.w) / 2 + (Math.abs(u.dot(f.z)) * o.h) / 2;
}

const area = (o) => (o.kind === "round" ? Math.PI * o.r * o.r : o.w * o.h);

// Each tube member's two ends: { [id]: { a: end, b: end } }, end = { kind, trim (mm off the node
// toward the other end; negative: past the node), onto (the receiver's id, for cut and butt) }.
export function frameEnds(design) {
  const out = {};
  if (!design) return out;
  const nodes = Object.fromEntries((design.nodes ?? []).map((n) => [n.id, v3(n.xyz_mm)]));
  const atNode = {};
  for (const m of design.members ?? []) {
    const a = nodes[m.node_a];
    const b = nodes[m.node_b];
    if (!a || !b || a.distanceTo(b) < 1e-6) continue;
    const f = memberAxes(a, b);
    const info = { m, f, o: outlineOf(design, m), len: a.distanceTo(b), tube: !!tubeSection(design, m) };
    if (info.tube) out[m.id] = { a: { kind: "free", trim: 0 }, b: { kind: "free", trim: 0 } };
    (atNode[m.node_a] ??= []).push({ ...info, end: "a", d: f.x.clone() });
    (atNode[m.node_b] ??= []).push({ ...info, end: "b", d: f.x.clone().negate() });
  }
  const set = (e, v) => {
    if (out[e.m.id]) out[e.m.id][e.end] = v;
  };
  for (const [id, es] of Object.entries(atNode)) {
    if (es.length < 2) continue;
    const c = nodes[id];
    // the tubes going through (as joints.js: one section, opposite directions within THROUGH_COS)
    const pairs = [];
    const taken = new Set();
    for (let i = 0; i < es.length; i += 1) {
      for (let j = i + 1; j < es.length; j += 1) {
        if (taken.has(i) || taken.has(j)) continue;
        if (es[i].m.section === es[j].m.section && es[i].d.dot(es[j].d) <= -THROUGH_COS) {
          pairs.push([i, j]);
          taken.add(i).add(j);
        }
      }
    }
    let recIdx;
    if (pairs.length) {
      const best = pairs.reduce((p, q) => (area(es[q[0]].o) > area(es[p[0]].o) ? q : p));
      for (const i of best) set(es[i], { kind: "through", trim: 0 });
      recIdx = best;
    } else {
      const bigger = (q, p) => area(es[q].o) > area(es[p].o) + 1e-6 || (Math.abs(area(es[q].o) - area(es[p].o)) <= 1e-6 && es[q].len > es[p].len);
      const k = es.reduce((p, _, q) => (bigger(q, p) ? q : p), 0);
      recIdx = [k];
    }
    const r0 = es[recIdx[0]];
    const rec = { c, x: r0.f.x, y: r0.f.y, z: r0.f.z, o: r0.o };
    let ext = 0;
    for (let i = 0; i < es.length; i += 1) {
      if (recIdx.includes(i)) continue;
      const e = es[i];
      if (Math.abs(e.d.dot(rec.x)) > TUBE.collinear_cos) {
        set(e, { kind: "butt", trim: 0, onto: r0.m.id });
        continue;
      }
      let trim = 0;
      for (const [py, pz] of outlinePoints(e.o)) {
        const p = c.clone().addScaledVector(e.f.y, py).addScaledVector(e.f.z, pz);
        const s = exitAlong(rec, p, e.d);
        if (Number.isFinite(s)) trim = Math.max(trim, s);
      }
      // cut_gap off the face: the end face is not drawn in the face's plane (the two fought in the depth test)
      set(e, { kind: "cut", trim: Math.min(trim + TUBE.cut_gap_mm, TUBE.max_trim_of_length * e.len), onto: r0.m.id });
      if (!pairs.length) ext = Math.max(ext, halfAlong(e.o, e.f, r0.d));
    }
    if (!pairs.length) set(r0, { kind: "carrier", trim: -Math.min(ext, TUBE.max_trim_of_length * r0.len) });
  }
  return out;
}

// The section's outline round its axis, counter-clockwise in (y, z): [{ o: [y, z] outer, i: [y, z]
// inner, n: [ny, nz] outward normal }], and the indices of the rect's corner middles (for ink lines).
function profileOf(sec) {
  const pts = [];
  const mids = [];
  if (sec.kind === "round") {
    const n = TUBE.round_segments;
    for (let k = 0; k < n; k += 1) {
      const a = (2 * Math.PI * k) / n;
      const [cs, sn] = [Math.cos(a), Math.sin(a)];
      pts.push({ o: [sec.r * cs, sec.r * sn], i: [(sec.r - sec.t) * cs, (sec.r - sec.t) * sn], n: [cs, sn] });
    }
    return { pts, mids };
  }
  const seg = TUBE.corner_segments;
  const ri = Math.max(sec.r - sec.t, 0);
  [[1, 1], [-1, 1], [-1, -1], [1, -1]].forEach(([sy, sz], q) => {
    const cy = sy * (sec.w / 2 - sec.r);
    const cz = sz * (sec.h / 2 - sec.r);
    for (let s = 0; s <= seg; s += 1) {
      const a = (q * Math.PI) / 2 + ((s / seg) * Math.PI) / 2;
      const [cs, sn] = [Math.cos(a), Math.sin(a)];
      if (s === seg / 2) mids.push(pts.length);
      pts.push({ o: [cy + sec.r * cs, cz + sec.r * sn], i: [cy + ri * cs, cz + ri * sn], n: [cs, sn] });
    }
  });
  return { pts, mids };
}

// A hollow tube along x from 0 to 1 (stretched per member by its local matrix), with an end face
// at x = 0 (capA) and x = 1 (capB) or open there (a tube going on). Returns { geo, edges } with the
// ink lines (pairs of points, in the same unit frame): the rect's corners along the tube and the
// outlines of each end face.
export function tubeGeometry(sec, capA, capB) {
  const { pts, mids } = profileOf(sec);
  const n = pts.length;
  const pos = [];
  const nor = [];
  const idx = [];
  const vert = (x, [y, z], [nx, ny, nz]) => {
    pos.push(x, y, z);
    nor.push(nx, ny, nz);
    return pos.length / 3 - 1;
  };
  // the outer and inner walls (smooth round the profile)
  for (const inner of [false, true]) {
    const base = pos.length / 3;
    for (const p of pts) {
      const at = inner ? p.i : p.o;
      const nm = inner ? [0, -p.n[0], -p.n[1]] : [0, p.n[0], p.n[1]];
      vert(0, at, nm);
      vert(1, at, nm);
    }
    for (let k = 0; k < n; k += 1) {
      const [A, D] = [base + 2 * k, base + 2 * k + 1];
      const [B, C] = [base + 2 * ((k + 1) % n), base + 2 * ((k + 1) % n) + 1];
      if (inner) idx.push(A, C, B, A, D, C);
      else idx.push(A, B, C, A, C, D);
    }
  }
  // the end faces (rings between the walls)
  for (const [x, cap] of [[0, capA], [1, capB]]) {
    if (!cap) continue;
    const base = pos.length / 3;
    for (const p of pts) {
      vert(x, p.o, [x ? 1 : -1, 0, 0]);
      vert(x, p.i, [x ? 1 : -1, 0, 0]);
    }
    for (let k = 0; k < n; k += 1) {
      const [O0, I0] = [base + 2 * k, base + 2 * k + 1];
      const [O1, I1] = [base + 2 * ((k + 1) % n), base + 2 * ((k + 1) % n) + 1];
      if (x) idx.push(O0, O1, I1, O0, I1, I0);
      else idx.push(O0, I1, O1, O0, I0, I1);
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  geo.setAttribute("normal", new THREE.Float32BufferAttribute(nor, 3));
  geo.setIndex(idx);
  // the end outlines through every other point of the profile (half the wide ink segments; the
  // round's 12-gon and the corners' 2 chords still read as the outline at the bench's sizes)
  const edges = [];
  for (const k of mids) edges.push(0, ...pts[k].o, 1, ...pts[k].o);
  const every = pts.map((_, k) => k).filter((k) => (sec.kind === "round" ? k % 2 === 0 : (k % (TUBE.corner_segments + 1)) % 2 === 0));
  for (const [x, cap] of [[0, capA], [1, capB]]) {
    if (!cap) continue;
    for (const ring of ["o", "i"]) {
      for (let j = 0; j < every.length; j += 1) edges.push(x, ...pts[every[j]][ring], x, ...pts[every[(j + 1) % every.length]][ring]);
    }
  }
  return { geo, edges };
}
