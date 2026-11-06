// The tyres of a driving record on the 3D (toggle タイヤ, ?on=grip): per wheel the tyre's force,
// the load, the friction circle and the contact point's track, and the forces the body takes at
// the suspension mounts. Only recorded values are drawn (K2's series, hiraku/solvers/k2_vehicle/
// record.py and shelf/mounts.py); what the record does not have is not drawn and the panel
// (grip_panel.js) says so.
//
//   gripOf(analysis, motion)  what the record has, the scales (from the record's largest values)
//   gripAt(g, motion, t)      everything at the record's time t (pure: the same t, the same picture)
//   GripLayer                 puts gripAt's frame on the workbench's 3D (ISO mm, the road's frame)
//
// Channels (per wheel <ID>): wheel_fx / wheel_fy (N, tyre axes), wheel_load (N), friction_limit
// (N, mu N), friction_use (1, |F| / mu N), sliding (0 / 1), slip_angle (deg), slip_ratio (%),
// wheel_stroke (mm, bump +), wheel_steer (deg), contact_x/y/z (m); the body's roll / yaw (deg).
// Mounts: mount_x/y/z.<support>.<mount> (N, world axes, the force the body receives), placed at
// the joint vectors' points (the instant of mount_force_peak) carried with the body.
//
// Scales: the force arrow and the friction circle share one: the record's largest of
// friction_limit and |(wheel_fx, wheel_fy)| is CIRCLE_MM long, so the arrow's tip is on the
// circle when the use is 1. The load bar: the largest wheel_load is LOAD_MM tall. The mount
// arrows: the largest |mount| is MOUNT_MM long. Drawing sizes only (not from the record).
//
// Colours: orange (the screen's caution colour) for the friction limit and a sliding tyre, never
// red (red is a failed verdict only).

import * as THREE from "three";
import { CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import { ANGLE, FORCE, LENGTH, lerpAt, nearestAt, nearestIndex, wheelName } from "./series.js";
import { esc, fmt } from "./labels.js";

export const GRIP_COLORS = {
  limit: "#e9893a", // the friction circle and a sliding tyre (the caution orange)
  force: "#8e6bbf", // the tyre's force (the workbench's grip colour)
  load: "#2b59c3", // the wheel load (the workbench's reaction colour)
  mount: "#1f6f6b", // the force at a mount (the workbench's joint colour)
  track: "#6b665c",
  ink: "#1d1d1f",
};
export const CIRCLE_MM = 450;
export const LOAD_MM = 600;
export const MOUNT_MM = 420;
const LOAD_BESIDE_MM = 260; // the load bar stands this far outboard of the contact point (drawing only)
const OVERLAY = 1; // workbench3d.js OVERLAY: drawn after the picture

// ---- what the record has ---------------------------------------------------

function unitOk(s, table) {
  return !!s && table[s.unit] !== undefined;
}

// mount_force_peak's where: "<support>.<mount> at t <s> s".
function peakOf(ana) {
  const r = (ana?.results ?? []).find((x) => x.quantity === "mount_force_peak");
  const m = r && /^(\S+)\s+at t\s+([-\d.eE+]+)\s*s/.exec(r.where ?? "");
  return m ? { at: m[1], t: Number(m[2]), value: r.value, unit: r.unit } : null;
}

// R = Rz(yaw) Ry(pitch) Rx(roll), rows; applied to a column vector.
function rotation([r, p, y]) {
  const [cr, sr, cp, sp, cy, sy] = [Math.cos(r), Math.sin(r), Math.cos(p), Math.sin(p), Math.cos(y), Math.sin(y)];
  return [
    [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
    [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
    [-sp, cp * sr, cp * cr],
  ];
}
const mul = (R, v) => R.map((row) => row[0] * v[0] + row[1] * v[1] + row[2] * v[2]);
const mulT = (R, v) => [0, 1, 2].map((j) => R[0][j] * v[0] + R[1][j] * v[1] + R[2][j] * v[2]);

// Curves against slip: tyre_<fy|fx>_curve.<ID>.<Fz N>, by wheel, sorted by load.
function curvesOf(ana, kind) {
  const out = {};
  const re = new RegExp(`^tyre_${kind}_curve\\.([^.]+)\\.([-\\d.]+)$`);
  for (const s of ana?.series ?? []) {
    const m = re.exec(s.channel ?? "");
    if (!m || !Array.isArray(s.x?.values) || s.x.values.length !== s.values?.length) continue;
    (out[m[1]] ??= []).push({ fz: Number(m[2]), xs: s.x.values, ys: s.values, xUnit: s.x.unit, unit: s.unit });
  }
  for (const k of Object.keys(out)) out[k].sort((a, b) => a.fz - b.fz);
  return out;
}

// Whether a motion has the tyres' forces (the toggle タイヤ is offered then).
export function hasGrip(motion) {
  return !!motion && motion.wheelIds.some((id) => unitOk(motion.channels.get(`wheel_fx.${id}`), FORCE) && unitOk(motion.channels.get(`wheel_fy.${id}`), FORCE));
}

export function gripOf(ana, motion) {
  if (!hasGrip(motion)) return null;
  const ch = motion.channels;
  const ids = motion.wheelIds.filter((id) => unitOk(ch.get(`wheel_fx.${id}`), FORCE) && unitOk(ch.get(`wheel_fy.${id}`), FORCE) && ch.has(`contact_x.${id}`) && ch.has(`contact_y.${id}`));
  const every = (base, table) => ids.length > 0 && ids.every((id) => (table ? unitOk(ch.get(`${base}.${id}`), table) : ch.has(`${base}.${id}`)));
  const has = {
    friction: every("friction_limit", FORCE) && every("friction_use") && every("sliding"),
    load: every("wheel_load", FORCE),
    slip: every("slip_angle", ANGLE),
    slipRatio: every("slip_ratio"),
    stroke: every("wheel_stroke", LENGTH),
  };
  const fN = (name) => {
    const s = ch.get(name);
    return s ? s.values.map((v) => v * FORCE[s.unit]) : [];
  };
  // the shared scale of the arrows and circles; the load's; the use's peak (the most sliding moment)
  let fmax = 0;
  let loadMax = 0;
  let use = { v: -1, t: null, id: null };
  for (const id of ids) {
    const fx = ch.get(`wheel_fx.${id}`);
    const fy = ch.get(`wheel_fy.${id}`);
    for (let i = 0; i < fx.t_s.length; i += 1) fmax = Math.max(fmax, Math.hypot(fx.values[i] * FORCE[fx.unit], lerpAt(fy, fx.t_s[i]) * FORCE[fy.unit]));
    if (has.friction) for (const v of fN(`friction_limit.${id}`)) fmax = Math.max(fmax, v);
    if (has.load) for (const v of fN(`wheel_load.${id}`)) loadMax = Math.max(loadMax, v);
    if (has.friction) {
      const s = ch.get(`friction_use.${id}`);
      s.values.forEach((v, i) => {
        if (v > use.v) use = { v, t: s.t_s[i], id };
      });
    }
  }
  // the tracks: the recorded contact points with the sliding flag of the nearest sample
  const tracks = {};
  for (const id of ids) {
    const sx = ch.get(`contact_x.${id}`);
    const sy = ch.get(`contact_y.${id}`);
    const sz = ch.get(`contact_z.${id}`);
    const k = LENGTH[sx.unit] ?? 1000;
    const sl = has.friction ? ch.get(`sliding.${id}`) : null;
    tracks[id] = sx.t_s.map((t, i) => ({ t, p: [sx.values[i] * k, lerpAt(sy, t) * k, sz ? lerpAt(sz, t) * k : 0], slide: sl ? nearestAt(sl, t) >= 0.5 : false }));
  }
  // mounts: the series, and their points in the body's frame (the joint vectors at the peak's instant)
  const peak = peakOf(ana);
  const mounts = [];
  let mountMax = 0;
  for (const [name, sx] of ch) {
    if (!name.startsWith("mount_x.")) continue;
    const rest = name.slice("mount_x.".length);
    const sy = ch.get(`mount_y.${rest}`);
    const sz = ch.get(`mount_z.${rest}`);
    if (!sy || !sz || !unitOk(sx, FORCE)) continue;
    const k = FORCE[sx.unit];
    sx.values.forEach((v, i) => (mountMax = Math.max(mountMax, Math.hypot(v, sy.values[i], sz.values[i]) * k)));
    const vec = (ana.vectors ?? []).find((v) => v.kind === "joint" && v.at === rest && Array.isArray(v.origin_mm));
    let local = null;
    if (vec && peak) {
      const f = motion.frame(peak.t);
      local = mulT(rotation(f.rpy), vec.origin_mm.map((c, j) => c - f.body[j]));
    }
    mounts.push({ id: rest, sx, sy, sz, k, local });
  }
  const mt = mounts[0]?.sx.t_s ?? null;
  const peakIndex = peak && mt ? mt.reduce((best, t, i) => (Math.abs(t - peak.t) < Math.abs(mt[best] - peak.t) ? i : best), 0) : null;
  const rollS = unitOk(ch.get("roll"), ANGLE) ? ch.get("roll") : null;
  let rollMax = 0;
  for (const v of rollS?.values ?? []) rollMax = Math.max(rollMax, Math.abs(v * ANGLE[rollS.unit]) * (180 / Math.PI));
  return {
    analysisId: ana.analysis_id,
    tool: ana.tool,
    ids,
    has: { ...has, fyCurve: Object.keys(curvesOf(ana, "fy")).length > 0, fxCurve: Object.keys(curvesOf(ana, "fx")).length > 0, mounts: mounts.length > 0, mountPlaces: mounts.some((m) => m.local) },
    fmax,
    loadMax,
    K: fmax > 0 ? CIRCLE_MM / fmax : 0,
    KL: loadMax > 0 ? LOAD_MM / loadMax : 0,
    tracks,
    curves: { fy: curvesOf(ana, "fy"), fx: curvesOf(ana, "fx") },
    mounts,
    mountMax,
    peak,
    peakIndex,
    rollS,
    rollMax,
    mostUse: use.t === null ? null : use,
  };
}

// ---- everything at the record's time t (pure) --------------------------------

export function gripAt(g, motion, t) {
  const ch = motion.channels;
  const at = (name, table = null) => {
    const s = ch.get(name);
    if (!s) return null;
    return lerpAt(s, t) * (table ? table[s.unit] ?? 1 : 1);
  };
  const near = (name, table = null) => {
    const s = ch.get(name);
    if (!s) return null;
    return nearestAt(s, t) * (table ? table[s.unit] ?? 1 : 1);
  };
  const yaw = at("yaw", ANGLE) ?? 0;
  const wheels = g.ids.map((id) => {
    const fx = at(`wheel_fx.${id}`, FORCE);
    const fy = at(`wheel_fy.${id}`, FORCE);
    const k = LENGTH[ch.get(`contact_x.${id}`).unit] ?? 1000;
    const contact = [at(`contact_x.${id}`) * k, at(`contact_y.${id}`) * k, (at(`contact_z.${id}`) ?? 0) * k];
    // the tyre's axes on the road: the body's yaw and the wheel's steer
    const heading = yaw + (at(`wheel_steer.${id}`, ANGLE) ?? 0);
    const c = Math.cos(heading);
    const s = Math.sin(heading);
    const world = [fx * c - fy * s, fx * s + fy * c];
    const tip = [contact[0] + world[0] * g.K, contact[1] + world[1] * g.K, contact[2]];
    const track = g.tracks[id];
    let n = 0;
    while (n < track.length && track[n].t <= t) n += 1;
    return {
      id,
      contact,
      heading,
      fx,
      fy,
      force: Math.hypot(fx, fy),
      tip,
      load: g.has.load ? at(`wheel_load.${id}`, FORCE) : null,
      loadText: g.has.load ? near(`wheel_load.${id}`, FORCE) : null,
      limit: g.has.friction ? at(`friction_limit.${id}`, FORCE) : null,
      use: g.has.friction ? at(`friction_use.${id}`) : null,
      useText: g.has.friction ? near(`friction_use.${id}`) : null,
      sliding: g.has.friction ? near(`sliding.${id}`) >= 0.5 : false,
      radius: g.has.friction ? at(`friction_limit.${id}`, FORCE) * g.K : null,
      slip: g.has.slip ? at(`slip_angle.${id}`) * (ch.get(`slip_angle.${id}`).unit === "rad" ? 180 / Math.PI : 1) : null,
      slipRatio: g.has.slipRatio ? at(`slip_ratio.${id}`) * (ch.get(`slip_ratio.${id}`).unit === "1" ? 100 : 1) : null,
      stroke: g.has.stroke ? at(`wheel_stroke.${id}`, LENGTH) : null,
      trailN: n, // the recorded contact points up to t (then the point at t)
    };
  });
  const body = motion.frame(t);
  const R = rotation(body.rpy);
  const mounts = g.mounts.map((m) => {
    const f = [lerpAt(m.sx, t) * m.k, lerpAt(m.sy, t) * m.k, lerpAt(m.sz, t) * m.k];
    return { id: m.id, f, mag: Math.hypot(...f), at: m.local ? mul(R, m.local).map((c, j) => c + body.body[j]) : null };
  });
  const peakOn = g.peak && g.mounts.length ? nearestIndex(g.mounts[0].sx, t) === g.peakIndex : false;
  return {
    t,
    roll: g.rollS ? lerpAt(g.rollS, t) * ANGLE[g.rollS.unit] * (180 / Math.PI) : null,
    rollText: g.rollS ? nearestAt(g.rollS, t) * ANGLE[g.rollS.unit] * (180 / Math.PI) : null,
    wheels,
    mounts,
    peakOn,
  };
}

// ---- on the 3D ------------------------------------------------------------------

function overlay(obj) {
  obj.traverse((o) => {
    o.layers.set(OVERLAY);
    o.renderOrder = 9;
  });
  return obj;
}

const UP = new THREE.Vector3(0, 1, 0);

function arrow(color) {
  const mat = new THREE.MeshBasicMaterial({ color, depthTest: false, transparent: true, toneMapped: false });
  const g = new THREE.Group();
  g.add(new THREE.Mesh(new THREE.CylinderGeometry(10, 10, 1, 10), mat), new THREE.Mesh(new THREE.ConeGeometry(30, 1, 14), mat));
  return overlay(g);
}

function placeArrow(g, from, to) {
  const d = new THREE.Vector3(to[0] - from[0], to[1] - from[1], to[2] - from[2]);
  const len = d.length();
  g.visible = len > 15;
  if (!g.visible) return;
  const head = Math.min(80, len * 0.4);
  const [shaft, cone] = g.children;
  shaft.scale.y = len - head;
  shaft.position.y = (len - head) / 2;
  cone.scale.y = head;
  cone.position.y = len - head / 2;
  g.quaternion.setFromUnitVectors(UP, d.normalize());
  g.position.set(...from);
}

function unitCircle(n = 72) {
  const pts = [];
  for (let i = 0; i < n; i += 1) {
    const a = (i / n) * Math.PI * 2;
    const b = ((i + 1) / n) * Math.PI * 2;
    pts.push(Math.cos(a), Math.sin(a), 0, Math.cos(b), Math.sin(b), 0);
  }
  return pts;
}

function tag(cls) {
  const el = document.createElement("div");
  el.className = `grip-tag ${cls}`;
  el.dataset.kind = "label";
  return new CSS2DObject(el);
}

export class GripLayer {
  constructor(parent) {
    this.group = new THREE.Group();
    this.group.visible = false;
    parent.add(this.group);
    this.limitMat = new LineMaterial({ color: GRIP_COLORS.limit, linewidth: 2.6, depthTest: false, transparent: true });
    this.trackMat = new LineMaterial({ vertexColors: true, linewidth: 2.2, depthTest: false, transparent: true });
    this.fill = new THREE.MeshBasicMaterial({ color: GRIP_COLORS.limit, transparent: true, opacity: 0.32, depthTest: false, depthWrite: false, side: THREE.DoubleSide, toneMapped: false });
    this.dotMat = new THREE.MeshBasicMaterial({ color: GRIP_COLORS.ink, depthTest: false, transparent: true, toneMapped: false });
    this.dotSlide = new THREE.MeshBasicMaterial({ color: GRIP_COLORS.limit, depthTest: false, transparent: true, toneMapped: false });
    this.loadMat = new THREE.MeshBasicMaterial({ color: GRIP_COLORS.load, depthTest: false, transparent: true, opacity: 0.8, toneMapped: false });
    this.shared = new Set([this.limitMat, this.trackMat, this.fill, this.dotMat, this.dotSlide, this.loadMat]);
    this.g = null;
    this.objs = {};
    this.mountObjs = [];
  }

  resize(w, h) {
    this.limitMat.resolution.set(w, h);
    this.trackMat.resolution.set(w, h);
  }

  clear() {
    this.group.traverse((o) => {
      if (o.geometry) o.geometry.dispose();
      if (o.material && !this.shared.has(o.material)) o.material.dispose();
      if (o.isCSS2DObject) o.element.remove();
    });
    this.group.clear();
    this.objs = {};
    this.mountObjs = [];
  }

  set(g) {
    if (g === this.g) return;
    this.clear();
    this.g = g;
    if (!g) return;
    for (const id of g.ids) {
      const o = {};
      if (g.has.friction) {
        o.circle = overlay(new LineSegments2(new LineSegmentsGeometry().setPositions(unitCircle()), this.limitMat));
        o.disc = overlay(new THREE.Mesh(new THREE.CircleGeometry(1, 48), this.fill));
        o.slideTag = tag("grip-slide");
        o.slideTag.element.textContent = "滑り";
        this.group.add(o.circle, o.disc, o.slideTag);
      }
      o.force = arrow(GRIP_COLORS.force);
      o.dot = overlay(new THREE.Mesh(new THREE.CircleGeometry(38, 20), this.dotMat));
      o.track = overlay(new LineSegments2(new LineSegmentsGeometry(), this.trackMat));
      this.group.add(o.force, o.dot, o.track);
      if (g.has.load) {
        o.load = overlay(new THREE.Mesh(new THREE.BoxGeometry(60, 60, 1).translate(0, 0, 0.5), this.loadMat));
        this.group.add(o.load);
      }
      this.objs[id] = o;
    }
    for (const m of g.mounts) {
      if (!m.local) continue;
      const a = arrow(GRIP_COLORS.mount);
      this.group.add(a);
      this.mountObjs.push({ id: m.id, a });
    }
    if (g.peak && g.has.mountPlaces) {
      this.peakTag = tag("grip-peak");
      this.group.add(this.peakTag);
    } else this.peakTag = null;
  }

  // Place a frame of gripAt (null: hide).
  place(f) {
    const g = this.g;
    this.group.visible = !!(g && f);
    if (!this.group.visible) {
      for (const o of Object.values(this.objs)) if (o.slideTag) o.slideTag.visible = false;
      if (this.peakTag) this.peakTag.visible = false;
      return;
    }
    const orange = new THREE.Color(GRIP_COLORS.limit);
    const gray = new THREE.Color(GRIP_COLORS.track);
    for (const w of f.wheels) {
      const o = this.objs[w.id];
      if (!o) continue;
      const z = w.contact[2] + 8;
      if (o.circle) {
        const r = Math.max(1, w.radius);
        o.circle.position.set(w.contact[0], w.contact[1], z);
        o.circle.scale.set(r, r, 1);
        o.disc.position.copy(o.circle.position);
        o.disc.scale.set(r, r, 1);
        o.disc.visible = w.sliding;
        o.slideTag.visible = w.sliding;
        o.slideTag.position.set(w.contact[0], w.contact[1], z + 140);
      }
      placeArrow(o.force, [w.contact[0], w.contact[1], z + 4], [w.tip[0], w.tip[1], z + 4]);
      o.dot.position.set(w.tip[0], w.tip[1], z + 6);
      o.dot.material = w.sliding ? this.dotSlide : this.dotMat;
      if (o.load) {
        // the bar stands beside the wheel, outboard (away from the vehicle's middle line)
        const side = Math.sign(w.contact[1] - (f.wheels.reduce((s, x) => s + x.contact[1], 0) / f.wheels.length)) || 1;
        const h = Math.max(1, (w.load ?? 0) * g.KL);
        o.load.position.set(w.contact[0], w.contact[1] + side * LOAD_BESIDE_MM, z);
        o.load.scale.set(1, 1, h);
        o.load.visible = (w.load ?? 0) > 0;
      }
      // the track: the recorded points up to t, then the point at t; sliding stretches in orange
      const tr = g.tracks[w.id];
      const pos = [];
      const col = [];
      const pts = tr.slice(0, w.trailN).map((p) => [...p.p, p.slide]);
      pts.push([...w.contact, w.sliding]);
      for (let i = 0; i + 1 < pts.length; i += 1) {
        const a = pts[i];
        const b = pts[i + 1];
        pos.push(a[0], a[1], a[2] + 4, b[0], b[1], b[2] + 4);
        const c = b[3] ? orange : gray;
        col.push(c.r, c.g, c.b, c.r, c.g, c.b);
      }
      o.track.visible = pos.length > 0;
      if (pos.length) {
        o.track.geometry.dispose();
        o.track.geometry = new LineSegmentsGeometry().setPositions(pos).setColors(col);
      }
    }
    for (const { id, a } of this.mountObjs) {
      const m = f.mounts.find((x) => x.id === id);
      if (!m?.at || !(g.mountMax > 0)) {
        a.visible = false;
        continue;
      }
      const len = (MOUNT_MM * m.mag) / g.mountMax;
      const s = m.mag > 0 ? len / m.mag : 0;
      placeArrow(a, m.at, m.at.map((c, j) => c + m.f[j] * s));
    }
    if (this.peakTag) {
      const m = f.mounts.find((x) => x.id === g.peak.at);
      this.peakTag.visible = !!(f.peakOn && m?.at);
      if (this.peakTag.visible) {
        this.peakTag.position.set(m.at[0], m.at[1], m.at[2] + 160);
        const [sup, ...rest] = g.peak.at.split(".");
        const text = `取付点の最大 ${fmt(g.peak.value / 1000, 3)} kN<small>${esc(wheelName(sup))} ${esc(rest.join("."))}</small>`;
        if (this.peakTag.element.innerHTML !== text) this.peakTag.element.innerHTML = text;
      }
    }
  }
}
