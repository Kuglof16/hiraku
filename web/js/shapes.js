// What a part looks like, by its kind, inside its box (ISO mm: x forward, y
// left, z up). The design gives only a kind and a box per part; the shapes
// here stay inside that box and add nothing the design does not list (a seat's
// backrest is part of the seat; a wheel is drawn from its support).
// Kinds without a shape here are drawn as their box.

import * as THREE from "three";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";

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

// Steering wheel and column at the rear top of the box, pedals at the front bottom.
function steering([sx, sy, sz]) {
  const ringR = Math.max(40, Math.min(sy, sz) * 0.45);
  const tube = Math.max(6, ringR * 0.12);
  const hub = [-sx / 2 + ringR * 0.5 + tube, 0, sz / 2 - ringR - tube];
  const ring = new THREE.TorusGeometry(ringR, tube, 10, 40).rotateY(Math.PI / 2 - 0.45).translate(...hub);
  const out = [{ geo: ring, sub: null }];
  out.push({ geo: rod(hub, [sx / 2 - 20, 0, -sz / 2 + sz * 0.35], Math.max(8, tube * 0.9)), sub: null });
  for (const y of [-sy * 0.22, sy * 0.22]) out.push(piece(new THREE.BoxGeometry(20, Math.min(70, sy * 0.3), 90).rotateY(-0.3), sx / 2 - 40, y, -sz / 2 + 60));
  return out;
}

function battery([sx, sy, sz]) {
  const out = [piece(rounded(sx * 0.98, sy * 0.98, sz * 0.9, 12), 0, 0, -sz * 0.05)];
  for (const [y, sub] of [[-sy * 0.3, "rim"], [-sy * 0.18, "rim"]]) {
    out.push(piece(new THREE.CylinderGeometry(14, 14, sz * 0.1, 16).rotateX(Math.PI / 2), sx * 0.32, y, sz * 0.45 - sz * 0.05, sub));
  }
  return out;
}

// Motor (a drum across the vehicle) and its reduction gear (a box beside it).
function motor([sx, sy, sz]) {
  const r = Math.min(sx, sz) * 0.47;
  const len = sy * 0.62;
  return [
    piece(cylY(r, len, 40), 0, sy / 2 - len / 2, 0),
    piece(cylY(r * 0.75, 12, 32), 0, sy / 2 - len - 6, 0, "hub"),
    piece(rounded(sx * 0.7, sy - len - 12, sz * 0.8, 10), 0, -sy / 2 + (sy - len - 12) / 2, 0),
  ];
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
  return [
    { geo, sub: null },
    piece(new THREE.CylinderGeometry(22, 22, sy, 20), sx / 2 - 22, 0, -sz / 2 + sz * 0.2, "hub"),
  ];
}

// Floor and deck plates.
function plate([sx, sy, sz]) {
  return [piece(new THREE.BoxGeometry(sx, sy, sz), 0, 0, 0, "floor")];
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

// Suspension between the frame (inner side) and the wheel (outer side): two
// wishbone arms and a damper with its spring, all inside the box. The side
// that faces the wheel is the side farther from the vehicle's centre line.
function suspension([sx, sy, sz], center) {
  const out = center[1] >= 0 ? 1 : -1; // +y is the outer side on the left
  const yi = (-out * sy) / 2;
  const yo = (out * sy) / 2;
  const r = Math.max(6, Math.min(sx, sz) * 0.05);
  const arms = [];
  for (const z of [-sz * 0.32, sz * 0.22]) {
    const tip = [0, yo, z];
    arms.push({ geo: rod([-sx * 0.42, yi, z], tip, r), sub: null }, { geo: rod([sx * 0.42, yi, z], tip, r), sub: null });
  }
  const lo = [0, yo - out * sy * 0.15, -sz * 0.3];
  const hi = [0, yi + out * sy * 0.1, sz * 0.48];
  arms.push({ geo: rod(lo, hi, r * 1.6, 14), sub: "hub" });
  // the coil: rings along the damper
  const a = new THREE.Vector3(...lo);
  const b = new THREE.Vector3(...hi);
  const dir = b.clone().sub(a);
  const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, 1), dir.clone().normalize());
  for (let i = 1; i < 8; i += 1) {
    const p = a.clone().addScaledVector(dir, 0.15 + (0.7 * i) / 8);
    const ring = new THREE.TorusGeometry(r * 3.2, r * 0.55, 6, 18).applyQuaternion(q).translate(p.x, p.y, p.z);
    arms.push({ geo: ring, sub: "spring" });
  }
  return arms;
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
};

// Pieces of a part in its local frame (box centre at the origin, box rotation not applied).
// Returns null for a kind drawn as its plain box.
export function partPieces(part) {
  const size = part.box.size_mm;
  const kind = part.kind;
  if (kind === "body") return size[2] <= Math.min(size[0], size[1]) * 0.1 ? plate(size) : nose(size);
  const make = SHAPES[kind];
  return make ? make(size, part.box.center_mm) : null;
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
