// Reading `series` (time histories) of an analysis for the driving animation
// and the graph popup; series against another quantity (x) are only charted. Positions and angles are only converted between units
// for drawing; numbers shown as text are the recorded sample nearest to the
// time (nearestAt), never an interpolated value.
//
// Channel names follow the K2 vehicle tool (provisional until the lead
// confirms them):
//   body_x, body_y, body_z  (m)    position of the design origin carried by the body
//   roll, pitch, yaw        (deg)  body attitude, R = Rz(yaw) Ry(pitch) Rx(roll)
//   cg_x, cg_y, cg_z        (m)    centre of mass of the whole vehicle
//   table_angle             (deg)  tilt table, left side up when positive
//   wheel_load.<ID>         (N)    road normal force (0 = lifted)
//   wheel_center_{x,y,z}.<ID> (m), wheel_steer.<ID> (deg), wheel_spin.<ID> (deg)
//   contact_{x,y,z}.<ID>    (m)    contact point; arrows start here
//   reaction_{x,y,z}.<ID>, grip_{x,y,z}.<ID> (N)  force vectors on the tyre
// All positions are in the road frame (x along the road, y left, z up).

import { channelName, channelPartName, quantityName } from "./labels.js";

// Factors to mm, rad and N.
export const LENGTH = { mm: 1, m: 1000, km: 1e6 };
export const ANGLE = { deg: Math.PI / 180, rad: 1 };
export const FORCE = { N: 1, kN: 1000 };

export function channelsOf(analysis) {
  const out = new Map();
  for (const s of analysis?.series ?? []) {
    if (!Array.isArray(s?.t_s) || !Array.isArray(s?.values) || !s.t_s.length || s.t_s.length !== s.values.length) continue;
    out.set(s.channel, s);
  }
  return out;
}

// Series against another quantity than time (x: {quantity, unit, values}, e.g. a K&C
// bench's camber against wheel travel), by channel. They are charted, not animated.
export function xSeriesOf(analysis) {
  const out = new Map();
  for (const s of analysis?.series ?? []) {
    const xv = s?.x?.values;
    if (!Array.isArray(xv) || !Array.isArray(s.values) || !xv.length || xv.length !== s.values.length) continue;
    out.set(s.channel, s);
  }
  return out;
}

// A series' abscissa: its x values, else its times.
export function abscissa(s) {
  return s.x?.values ?? s.t_s;
}

// [smallest, largest] of an array (a loop: long series overflow a spread).
export function extent(a) {
  let lo = Infinity;
  let hi = -Infinity;
  for (const v of a) {
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  return [lo, hi];
}

// [i, f]: x lies between t[i] and t[i+1] at fraction f.
function segment(t, x) {
  const last = t.length - 1;
  if (x <= t[0]) return [0, 0];
  if (x >= t[last]) return [last, 0];
  let lo = 0;
  let hi = last;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (t[mid] <= x) lo = mid;
    else hi = mid;
  }
  const span = t[hi] - t[lo];
  return [lo, span > 0 ? (x - t[lo]) / span : 0];
}

export function lerpAt(s, x) {
  const [i, f] = segment(s.t_s, x);
  const v = s.values;
  return f > 0 ? v[i] + (v[i + 1] - v[i]) * f : v[i];
}

export function nearestAt(s, x) {
  const [i, f] = segment(s.t_s, x);
  return f > 0.5 ? s.values[i + 1] : s.values[i];
}

export function nearestIndex(s, x) {
  const [i, f] = segment(s.t_s, x);
  return f > 0.5 ? i + 1 : i;
}

const WHEEL_JA = { FL: "左前", FR: "右前", RL: "左後", RR: "右後", F: "前", R: "後" };
export function wheelName(id) {
  return WHEEL_JA[id] ?? id;
}

// A channel's name in a chart's legend: what follows the dot (the wheel, the part, the mount:
// mount_x.FL.lca_front -> 左前 下アーム前), or for a channel without a dot its own name.
export function seriesName(channel) {
  const [base, ...rest] = String(channel).split(".");
  if (!rest.length) return channelName(base);
  return rest.map((r) => (WHEEL_JA[r] ? WHEEL_JA[r] : channelPartName(r))).join(" ");
}

// Seconds of motion in an analysis (without building it), or null when it has none.
export function motionSpan(analysis) {
  const ch = channelsOf(analysis);
  const names = [...ch.keys()];
  if (!names.some((n) => ["body_x", "body_y", "body_z", "roll", "pitch", "yaw"].includes(n) || n.startsWith("wheel_center_"))) return null;
  const all = names.map((n) => ch.get(n));
  return Math.max(...all.map((s) => s.t_s.at(-1))) - Math.min(...all.map((s) => s.t_s[0]));
}

// The motion in an analysis, or null when it has no body or wheel positions.
export function motionOf(analysis) {
  const ch = channelsOf(analysis);
  const names = [...ch.keys()];
  const poseNames = ["body_x", "body_y", "body_z", "roll", "pitch", "yaw"].filter((n) => ch.has(n));
  const wheelIds = [...new Set(names.filter((n) => /^(wheel_load|wheel_center_x|contact_x)\./.test(n)).map((n) => n.slice(n.indexOf(".") + 1)))];
  if (!poseNames.length && !names.some((n) => n.startsWith("wheel_center_"))) return null;

  const timed = names.map((n) => ch.get(n));
  const t0 = Math.min(...timed.map((s) => s.t_s[0]));
  const t1 = Math.max(...timed.map((s) => s.t_s.at(-1)));

  const conv = (name, table, x) => {
    const s = ch.get(name);
    if (!s) return null;
    const k = table[s.unit];
    return k === undefined ? null : lerpAt(s, x) * k;
  };
  const vec = (prefix, id, table, x) => {
    const v = ["x", "y", "z"].map((a) => conv(`${prefix}_${a}.${id}`, table, x));
    return v.every((c) => c !== null && Number.isFinite(c)) ? v : null;
  };
  const loadS = Object.fromEntries(wheelIds.map((id) => [id, ch.get(`wheel_load.${id}`)]).filter(([, s]) => s && FORCE[s.unit]));

  // First time each wheel carries no load after it carried some (a recorded 0).
  const liftAt = {};
  for (const [id, s] of Object.entries(loadS)) {
    let loaded = false;
    for (let i = 0; i < s.values.length; i += 1) {
      if (s.values[i] > 0) loaded = true;
      else if (loaded) {
        liftAt[id] = s.t_s[i];
        break;
      }
    }
  }
  const lifts = Object.values(liftAt);
  const liftT = lifts.length ? Math.min(...lifts) : null;

  // Arrow scale: the largest recorded normal force.
  let fref = 0;
  for (const s of Object.values(loadS)) for (const v of s.values) fref = Math.max(fref, Math.abs(v) * FORCE[s.unit]);

  const frame = (x) => ({
    t: x,
    body: ["body_x", "body_y", "body_z"].map((n) => conv(n, LENGTH, x) ?? 0),
    rpy: ["roll", "pitch", "yaw"].map((n) => conv(n, ANGLE, x) ?? 0),
    cg: (() => {
      const v = ["cg_x", "cg_y", "cg_z"].map((n) => conv(n, LENGTH, x));
      return v.every((c) => c !== null) ? v : null;
    })(),
    table: conv("table_angle", ANGLE, x),
    wheels: wheelIds.map((id) => {
      const ls = loadS[id];
      return {
        id,
        center: vec("wheel_center", id, LENGTH, x),
        steer: conv(`wheel_steer.${id}`, ANGLE, x) ?? 0,
        spin: conv(`wheel_spin.${id}`, ANGLE, x) ?? 0,
        load: ls ? nearestAt(ls, x) * FORCE[ls.unit] : null,
        contact: vec("contact", id, LENGTH, x),
        reaction: vec("reaction", id, FORCE, x),
        grip: vec("grip", id, FORCE, x),
      };
    }),
  });

  const path = ch.has("body_x") && ch.has("body_y") && LENGTH[ch.get("body_x").unit] && LENGTH[ch.get("body_y").unit]
    ? (() => {
        const sx = ch.get("body_x");
        const sy = ch.get("body_y");
        const kx = LENGTH[sx.unit];
        const ky = LENGTH[sy.unit];
        const xs = sx.values.map((v) => v * kx);
        const ys = sy.values.map((v) => v * ky);
        return { minX: Math.min(...xs), maxX: Math.max(...xs), minY: Math.min(...ys), maxY: Math.max(...ys) };
      })()
    : null;

  return { channels: ch, wheelIds, t0, t1, duration: t1 - t0, liftT, liftAt, fref, frame, path, hasTable: ch.has("table_angle") };
}

// Chart groups: channels with the same meaning and unit drawn together.
const GROUPS = [
  { key: "wheel_load", ja: "輪の荷重", test: (n) => n.startsWith("wheel_load.") },
  { key: "table", ja: "台の角度", test: (n) => n === "table_angle" },
  { key: "attitude", ja: "姿勢", test: (n) => ["roll", "pitch", "yaw"].includes(n) },
  { key: "speed", ja: "速さ", test: (n) => n === "speed" },
  { key: "accel", ja: "加速度", test: (n) => n === "accel_x" || n === "accel_y" },
  { key: "fy", ja: "横の力", test: (n) => n.startsWith("wheel_fy.") },
  { key: "fx", ja: "前後の力", test: (n) => n.startsWith("wheel_fx.") },
  { key: "stroke", ja: "サスの動き", test: (n) => n.startsWith("wheel_stroke.") },
  { key: "steer", ja: "舵角", test: (n) => n.startsWith("wheel_steer.") || n === "steer" },
];
const NOT_CHARTED = /^(body_|cg_|wheel_center_|contact_|reaction_|grip_|wheel_spin\.)/;

// Series against another quantity (x) are grouped by the channel's name before the dot,
// the abscissa and the unit; such a group carries x {quantity, unit}.
export function chartGroups(channels) {
  const all = [...channels.keys()];
  const names = all.filter((n) => !channels.get(n).x);
  const out = [];
  const used = new Set();
  for (const g of GROUPS) {
    const list = names.filter((n) => g.test(n));
    if (!list.length) continue;
    list.forEach((n) => used.add(n));
    // One unit per chart: keep the channels that share the first one's unit.
    const unit = channels.get(list[0]).unit;
    out.push({ key: g.key, ja: g.ja, unit, channels: list.filter((n) => channels.get(n).unit === unit) });
  }
  // any other channel: with the others of its name before the dot (wheel_camber.FL, .FR, ...)
  // and its unit in one chart, named from labels.js channelName (else as written)
  const rest = new Map();
  for (const n of names) {
    if (used.has(n) || NOT_CHARTED.test(n)) continue;
    const base = n.includes(".") ? n.slice(0, n.indexOf(".")) : n;
    const unit = channels.get(n).unit;
    const key = rest.has(base) && rest.get(base).unit !== unit ? `${base}[${unit}]` : base;
    if (!rest.has(key)) rest.set(key, { key, ja: channelName(base), unit, channels: [] });
    rest.get(key).channels.push(n);
  }
  out.push(...rest.values());
  const against = new Map();
  for (const n of all.filter((m) => channels.get(m).x)) {
    const s = channels.get(n);
    const base = n.includes(".") ? n.slice(0, n.indexOf(".")) : n;
    const key = `${base}@${s.x.quantity}[${s.x.unit}]${s.unit}`;
    // named from the quantities' table (labels.js quantityName), else the channels' (channelName)
    const ja = quantityName(base) !== base ? quantityName(base) : channelName(base);
    if (!against.has(key)) against.set(key, { key, ja, unit: s.unit, x: { quantity: s.x.quantity, unit: s.x.unit }, channels: [] });
    against.get(key).channels.push(n);
  }
  return [...out, ...against.values()];
}
