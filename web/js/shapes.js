// What a part looks like, by its kind, inside its box (ISO mm: x forward, y
// left, z up). The design gives only a kind and a box per part; the shapes
// here stay inside that box and add nothing the design does not list (a seat's
// backrest is part of the seat; a wheel is drawn from its support). What the
// design lists as its own part (the steering column and the pedals, the reducer
// beside the motor) is not drawn again inside another part's box.
// The suspension is drawn from the design's mounting points (support.suspension
// .hardpoints_mm): its arms and its damper with the spring, where the design puts
// them (they reach outside the part's box); without the points it is its box.
// Kinds without a shape here are drawn as their box. Thicknesses of rods and rings
// (an arm's, a ring's tube) are drawing values, not from the design.

import * as THREE from "three";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";

// One piece of a part: a geometry in the part's local frame (box centre at 0),
// and which sub-material it takes (null = the part's own).
function piece(geo, x = 0, y = 0, z = 0, sub = null) {
  geo.translate(x, y, z);
  return { geo, sub };
}

function rounded(sx, sy, sz, r) {
  const rr = Math.max(0.5, Math.min(r, sx / 2 - 0.5, sy / 2 - 0.5, sz / 2 - 0.5));
  return new RoundedBoxGeometry(sx, sy, sz, 3, rr);
}

// A cylinder whose axis is ISO y (lateral).
function cylY(r, len, seg = 32) {
  return new THREE.CylinderGeometry(r, r, len, seg);
}

// A cylinder from point a to point b (ISO mm).
function rod(a, b, r, seg = 10) {
  const va = new THREE.Vector3(...a);
  const vb = new THREE.Vector3(...b);
  const len = va.distanceTo(vb);
  const geo = new THREE.CylinderGeometry(r, r, len, seg);
  const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), vb.clone().sub(va).normalize());
  geo.applyQuaternion(q);
  const mid = va.add(vb).multiplyScalar(0.5);
  geo.translate(mid.x, mid.y, mid.z);
  return geo;
}

function seat([sx, sy, sz]) {
  const cushion = Math.max(40, sz * 0.38);
  const back = Math.max(40, sx * 0.18);
  return [
    piece(rounded(sx * 0.96, sy * 0.92, cushion, 30), 0, 0, -sz / 2 + cushion / 2),
    // the backrest at the rear, leaning back a little
    { geo: rounded(back, sy * 0.92, sz - cushion, 25).rotateY(-0.12).translate(-sx / 2 + back / 2 + 10, 0, cushion / 2), sub: null },
  ];
}

// The steering wheel: a ring filling the box across (y, z), its axis along the box's x,
// and its hub. The column and the pedals are the design's own parts.
function steering([sx, sy, sz]) {
  const tube = Math.max(6, Math.min(sx / 2, Math.min(sy, sz) * 0.05));
  const ringR = Math.max(20, Math.min(sy, sz) / 2 - tube);
  return [
    { geo: new THREE.TorusGeometry(ringR, tube, 10, 48).rotateY(Math.PI / 2), sub: null },
    { geo: new THREE.CylinderGeometry(ringR * 0.22, ringR * 0.22, Math.max(10, sx * 0.8), 24).rotateZ(Math.PI / 2), sub: null },
  ];
}

function battery([sx, sy, sz]) {
  return [piece(rounded(sx * 0.98, sy * 0.98, sz * 0.98, 12))];
}

// Motor: a drum across the vehicle with its end cover (the reducer is the design's own part).
function motor([sx, sy, sz]) {
  const r = Math.min(sx, sz) * 0.47;
  return [piece(cylY(r, sy - 12, 40), 0, 6, 0), piece(cylY(r * 0.75, 12, 32), 0, -sy / 2 + 6, 0, "hub")];
}

// A rod (a tie rod, a shaft, a column, a bar) along its box's longest side, as thick as the box.
function rodPart(size) {
  const k = size.indexOf(Math.max(...size));
  const others = size.filter((_, i) => i !== k);
  const r = Math.max(2, Math.min(...others) / 2);
  const a = [0, 0, 0];
  const b = [0, 0, 0];
  a[k] = -size[k] / 2;
  b[k] = size[k] / 2;
  return [{ geo: rod(a, b, r, 16), sub: null }];
}

// A box with cooling fins on top.
function finned([sx, sy, sz]) {
  const base = sz * 0.6;
  const out = [piece(rounded(sx, sy, base, 6), 0, 0, -sz / 2 + base / 2)];
  const n = Math.max(3, Math.round(sy / 25));
  for (let i = 0; i < n; i += 1) {
    const y = -sy / 2 + (sy * (i + 0.5)) / n;
    out.push(piece(new THREE.BoxGeometry(sx * 0.9, 4, sz - base), 0, y, -sz / 2 + base + (sz - base) / 2));
  }
  return out;
}

// The front of the body: a nose that slopes down to the front, and a bumper bar.
function nose([sx, sy, sz]) {
  const shape = new THREE.Shape();
  shape.moveTo(-sx / 2, -sz / 2);
  shape.lineTo(sx / 2 - 30, -sz / 2);
  shape.lineTo(sx / 2 - 30, -sz / 2 + sz * 0.45);
  shape.lineTo(sx / 2 - sx * 0.35, sz / 2);
  shape.lineTo(-sx / 2, sz / 2);
  shape.closePath();
  const geo = new THREE.ExtrudeGeometry(shape, { depth: sy * 0.94, bevelEnabled: true, bevelSize: 8, bevelThickness: 8, bevelSegments: 2 });
  // extruded along +z in the shape's frame: shape x -> ISO x, shape y -> ISO z, depth -> ISO y
  geo.rotateX(Math.PI / 2).translate(0, sy * 0.47, 0);
  return [{ geo, sub: null }];
}

// The corner radius of a box drawn with rounded edges (the real look), from its
// thinnest side: 0.35 of it, at most 40 mm. A 1.2 mm panel gets 0.42 mm, a 100 mm
// cushion 35 mm. These are values for the picture (how the edges catch the light), not
// the parts' radii; the design gives none.
export const ROUND = { share: 0.35, max_mm: 40 };
export function roundRadius(size) {
  return Math.min(ROUND.max_mm, ROUND.share * Math.min(...size));
}

// A plain box of the part's size: in the real look (round) with rounded edges. The
// radius is kept in geometry.userData.round_mm (0 for a sharp box).
export function boxGeometry([sx, sy, sz], round = false) {
  if (!round) {
    const g = new THREE.BoxGeometry(sx, sy, sz);
    g.userData.round_mm = 0;
    return g;
  }
  const r = roundRadius([sx, sy, sz]);
  // RoundedBoxGeometry needs the radius under half of every side
  const g = new RoundedBoxGeometry(sx, sy, sz, r < 2 ? 1 : 3, Math.min(r, Math.min(sx, sy, sz) / 2 - 1e-3));
  g.userData.round_mm = r;
  return g;
}

// A plate (floor, deck, a body panel): a box whose thinnest side is its thickness.
function plate(size, round) {
  return [{ geo: boxGeometry(size, round), sub: null }];
}

// A box is a plate when its thinnest side is a tenth of the middle one or less.
function isPlate(size) {
  const s = [...size].sort((a, b) => a - b);
  return s[0] <= s[1] * 0.1;
}

// Cargo: crates in a grid that fills the box, with a gap between them.
function crates([sx, sy, sz]) {
  const nx = Math.max(1, Math.round(sx / 450));
  const ny = Math.max(1, Math.round(sy / 450));
  const gap = 20;
  const cx = (sx - gap * (nx - 1)) / nx;
  const cy = (sy - gap * (ny - 1)) / ny;
  const out = [];
  for (let i = 0; i < nx; i += 1) {
    for (let j = 0; j < ny; j += 1) {
      out.push(piece(rounded(cx, cy, sz, 8), -sx / 2 + cx / 2 + i * (cx + gap), -sy / 2 + cy / 2 + j * (cy + gap), 0));
    }
  }
  return out;
}

// The support a suspension part belongs to: the one whose id ends its id ("susp_FL" -> "FL"),
// else the nearest to its box.
function supportOf(part, design) {
  const sups = (design?.supports ?? []).filter((s) => s.suspension?.hardpoints_mm);
  const byId = sups.find((s) => new RegExp(`(^|[_.:-])${s.id}$`).test(part.id));
  if (byId) return byId;
  const c = part.box.center_mm;
  let best = null;
  for (const s of sups) {
    const d = Math.hypot(s.position_mm[0] - c[0], s.position_mm[1] - c[1], s.position_mm[2] - c[2]);
    if (!best || d < best.d) best = { s, d };
  }
  return best?.s ?? null;
}

// Suspension from the design's mounting points (ISO mm, the design's frame): each wishbone
// from its two inner points to its outer one (lca_*, uca_*), and the damper from
// damper_lower to damper_upper with its spring round it. A pair that is not in the points
// is not drawn; with none of them the part is its box (null).
function suspension(size, center, part, design) {
  const hp = supportOf(part, design)?.suspension?.hardpoints_mm;
  if (!hp) return null;
  const local = (name) => (Array.isArray(hp[name]) ? hp[name].map((v, i) => v - center[i]) : null);
  const r = 9; // an arm's radius (mm), a drawing value
  const out = [];
  for (const arm of ["lca", "uca"]) {
    const tip = local(`${arm}_outer`);
    if (!tip) continue;
    for (const end of ["front", "rear"]) {
      const inner = local(`${arm}_${end}`);
      if (inner) out.push({ geo: rod(inner, tip, r), sub: null });
    }
  }
  const lo = local("damper_lower");
  const hi = local("damper_upper");
  if (lo && hi) {
    out.push({ geo: rod(lo, hi, r * 1.6, 14), sub: "hub" });
    // the spring: rings along the damper (their size and count are drawing values)
    const a = new THREE.Vector3(...lo);
    const dir = new THREE.Vector3(...hi).sub(a);
    const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, 1), dir.clone().normalize());
    for (let i = 1; i < 8; i += 1) {
      const p = a.clone().addScaledVector(dir, 0.15 + (0.7 * i) / 8);
      out.push({ geo: new THREE.TorusGeometry(r * 3.2, r * 0.55, 6, 18).applyQuaternion(q).translate(p.x, p.y, p.z), sub: "spring" });
    }
  }
  return out.length ? out : null;
}

const SHAPES = {
  seat,
  steering,
  battery,
  motor,
  controller: finned,
  electrical: finned,
  cargo: crates,
  suspension,
  tie_rod: rodPart,
  driveshaft: rodPart,
  steering_column: rodPart,
  steering_shaft: rodPart,
  steering_rack: rodPart,
  // rollbar: its box (the cage's tubes are frame members; the part's box keeps the cage's finish)
};

// A seat given as its pieces (seat_R_back, seat_R_cushion, ...): each is drawn as its box,
// so the seat shape (a cushion and a backrest in one box) is not drawn twice.
const SEAT_PIECE = /_(back|backrest|cushion|base|bottom|headrest)$/i;

// Pieces of a part in its local frame (box centre at the origin, box rotation not applied).
// Every kind without a shape is its plain box (rounded when `round`, the real look); so is
// a suspension without mounting points. design: the part's design (the suspension reads
// its support's mounting points).
export function partPieces(part, { round = false, design = null } = {}) {
  const size = part.box.size_mm;
  const kind = part.kind;
  if (kind === "body") return isPlate(size) ? plate(size, round) : nose(size);
  const make = kind === "seat" && SEAT_PIECE.test(part.id ?? "") ? null : SHAPES[kind];
  return (make ? make(size, part.box.center_mm, part, design) : null) ?? [{ geo: boxGeometry(size, round), sub: null }];
}

// An occupant given in body segments (designgen: "<person>" the torso, "<person>.<segment>"
// the head, neck, arms, hands, legs and feet; contract/loading.py person_of): the person of a
// part is its id up to the first ".".
export function personOf(part) {
  return String(part.id ?? "").split(".", 1)[0];
}

// The segment of an occupant part: its id after the first "." (null for the torso, or for an
// occupant drawn as one box).
export function segmentOf(part) {
  const id = String(part.id ?? "");
  const dot = id.indexOf(".");
  return dot < 0 ? null : id.slice(dot + 1);
}

// One segment of an occupant as one rounded shape inside its box, in the part's local frame
// (box centre at 0, the box's rpy_deg applied by the caller, as for every part):
// - the head ("<person>.head"): a ball stretched to the box (an ellipsoid, its three diameters
//   the box's three sides), so it meets the neck at the box's face;
// - a segment whose two short sides are about equal (within OCCUPANT_ROUND_SECTION: the
//   arms, forearms, thighs, shanks): a capsule along the box's longest side, its diameter the
//   smaller short side;
// - any other (the torso, the neck, a hand, a foot): the box with its edges rounded,
//   OCCUPANT_ROUND_SHARE of its smallest side.
// The shapes never reach outside the box. The share and the section ratio are values for the
// picture, not from the design.
export const OCCUPANT_ROUND_SECTION = 0.8;
export const OCCUPANT_ROUND_SHARE = 0.45;
export function occupantSegmentPieces(part) {
  const size = part.box.size_mm.map(Number);
  if (segmentOf(part) === "head") {
    // a unit ball stretched to the box's three sides: it touches all six faces (the neck's top too)
    const g = new THREE.SphereGeometry(1, 20, 14).scale(size[0] / 2, size[1] / 2, size[2] / 2);
    g.userData.occupant = "ball";
    g.userData.semi_axes_mm = size.map((s) => s / 2);
    return [{ geo: g, sub: null }];
  }
  const axis = size.indexOf(Math.max(...size)); // 0 x, 1 y, 2 z of the box
  const [a, b] = size.filter((_, i) => i !== axis);
  if (Math.min(a, b) / Math.max(a, b) >= OCCUPANT_ROUND_SECTION) {
    const r = Math.min(a, b) / 2;
    const g = new THREE.CapsuleGeometry(r, Math.max(0, size[axis] - 2 * r), 6, 16); // axis: local y
    if (axis === 0) g.rotateZ(-Math.PI / 2);
    else if (axis === 2) g.rotateX(Math.PI / 2);
    g.userData.occupant = "capsule";
    g.userData.axis = axis;
    return [{ geo: g, sub: null }];
  }
  const r = OCCUPANT_ROUND_SHARE * Math.min(...size);
  // RoundedBoxGeometry needs the radius under half of every side
  const g = new RoundedBoxGeometry(size[0], size[1], size[2], 4, Math.min(r, Math.min(...size) / 2 - 1e-3));
  g.userData.occupant = "rounded_box";
  return [{ geo: g, sub: null }];
}

// The pieces of one part (or wheel) that take the same material (sub) as one geometry each:
// one draw call per material instead of one per piece (a view through a shared renderer is
// drawn many times a frame). The pieces' geometries are used up (disposed).
export function mergePieces(pieces) {
  const bySub = new Map();
  for (const pc of pieces) {
    if (!bySub.has(pc.sub)) bySub.set(pc.sub, []);
    bySub.get(pc.sub).push(pc.geo);
  }
  const out = [];
  for (const [sub, geos] of bySub) {
    if (geos.length === 1) {
      out.push({ geo: geos[0], sub });
      continue;
    }
    // the same attributes on each (position, normal, uv), none indexed, so they merge
    const flat = geos.map((g) => {
      const f = g.index ? g.toNonIndexed() : g.clone();
      for (const name of Object.keys(f.attributes)) if (!["position", "normal", "uv"].includes(name)) f.deleteAttribute(name);
      if (!f.attributes.normal) f.computeVertexNormals();
      if (!f.attributes.uv) f.setAttribute("uv", new THREE.Float32BufferAttribute(new Float32Array(f.attributes.position.count * 2), 2));
      f.clearGroups();
      return f;
    });
    const merged = mergeGeometries(flat, false);
    if (geos[0].userData.round_mm !== undefined) merged.userData.round_mm = geos[0].userData.round_mm;
    for (const g of [...geos, ...flat]) g.dispose();
    out.push({ geo: merged, sub });
  }
  return out;
}

// A wheel (tyre with a rounded tread, rim, hub and spokes) around ISO y, centre at 0.
// Returns pieces like partPieces, with subs "tyre", "rim" and "hub".
export function wheelPieces(r, w) {
  const rimR = r * 0.62;
  const round = Math.min(w * 0.3, (r - rimR) * 0.5);
  const pts = [];
  const half = w / 2;
  pts.push(new THREE.Vector2(rimR, -half * 0.92));
  pts.push(new THREE.Vector2(r - round, -half));
  for (let i = 1; i < 6; i += 1) {
    const a = -Math.PI / 2 + (i / 6) * (Math.PI / 2);
    pts.push(new THREE.Vector2(r - round + round * Math.cos(a), -half + round + round * Math.sin(a)));
  }
  pts.push(new THREE.Vector2(r, -half + round), new THREE.Vector2(r, half - round));
  for (let i = 1; i < 6; i += 1) {
    const a = (i / 6) * (Math.PI / 2);
    pts.push(new THREE.Vector2(r - round + round * Math.cos(a), half - round + round * Math.sin(a)));
  }
  pts.push(new THREE.Vector2(r - round, half), new THREE.Vector2(rimR, half * 0.92));
  // LatheGeometry turns the profile (x = radius, y = axial) around its y axis, which is ISO y here.
  const tyre = new THREE.LatheGeometry(pts, 64);
  const out = [{ geo: tyre, sub: "tyre" }];
  out.push({ geo: new THREE.CylinderGeometry(rimR, rimR, w * 0.86, 48, 1, true), sub: "rim" });
  for (const side of [-1, 1]) {
    out.push({ geo: new THREE.CylinderGeometry(rimR * 0.3, rimR * 0.3, 10, 24).translate(0, (side * w * 0.36), 0), sub: "hub" });
    // five spokes from the hub to the rim, on each face
    for (let k = 0; k < 5; k += 1) {
      const a = (k / 5) * Math.PI * 2;
      out.push({ geo: rod([0, (side * w) * 0.36, 0], [rimR * Math.cos(a), (side * w) * 0.36, rimR * Math.sin(a)], Math.max(5, r * 0.035), 8), sub: "rim" });
    }
  }
  return out;
}
