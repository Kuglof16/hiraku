// A speed limit for turning on the screen: the pace a record's motion is played at, so the
// vehicle never turns faster than a limit (degrees per second of the stage) while the camera
// holds still. The record's values are not changed; only how much stage time each part of it
// gets: where the body turns fast (a roll-over) it plays slower, the rest a little faster, and
// all of it still fits the stage time it was given. When even that cannot fit (the turns alone
// need more time than there is), it plays as far as the time allows and holds there.
//
// pacer(plays, stageS, limitDegS) -> (share of the stage time 0..1) -> record time from t0
// (seconds, 0..the longest motion's duration); the motions are played together from their t0.
// A play is a motion, or { motion, view } with the Workbench3D it is drawn in: then the world's
// turn is the view's own (worldAngle: the tilt table, or the slope the scene turns in time).

import * as THREE from "three";

const STEPS = 600;

// The pages' limit: no view turns faster than 90 deg/s on the screen (the promo department,
// 2026-11-03); the pace aims a little under it, so a 0.05 s sampling stays under 90.
export const TURN_LIMIT_DEG_S = 90;
export const PACE_LIMIT_DEG_S = 80;

// The body's orientation at a frame: the world's turn about x (the view's worldAngle, else the
// tilt table) and the body's roll, pitch and yaw (as workbench3d.js bodyMatrix, Euler ZYX).
function orientation(frame, view) {
  const q = new THREE.Quaternion().setFromEuler(new THREE.Euler(frame.rpy[0], frame.rpy[1], frame.rpy[2], "ZYX"));
  const world = view?.worldAngle ? view.worldAngle(frame) : frame.table ?? 0;
  if (world) q.premultiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), world));
  return q;
}

// How far (deg) each record step turns, the largest over the motions.
export function turnSteps(plays, span, n = STEPS) {
  const du = span / n;
  const out = new Array(n).fill(0);
  for (const { motion: m, view } of plays) {
    let prev = orientation(m.frame(m.t0), view);
    for (let k = 0; k < n; k += 1) {
      const q = orientation(m.frame(Math.min(m.t1, m.t0 + (k + 1) * du)), view);
      out[k] = Math.max(out[k], THREE.MathUtils.radToDeg(prev.angleTo(q)));
      prev = q;
    }
  }
  return out;
}

export function pacer(plays, stageS, limitDegS, n = STEPS) {
  const ms = plays.filter(Boolean).map((p) => (p.motion ? p : { motion: p, view: null })).filter((p) => p.motion);
  const span = Math.max(0, ...ms.map((p) => p.motion.duration));
  if (!(span > 0) || !(stageS > 0)) return () => 0;
  const turns = turnSteps(ms, span, n);
  const need = turns.map((d) => d / limitDegS); // the least stage time each step may take
  const total = need.reduce((a, b) => a + b, 0);
  let steps;
  if (total >= stageS) {
    // cannot fit: the turns at the limit, as far as the time goes
    steps = need.map((x) => Math.max(x, 1e-9));
  } else {
    // each step at least `c`, at least its need, all together the stage time (c by bisection)
    let lo = 0;
    let hi = stageS / n;
    for (let i = 0; i < 60; i += 1) {
      const c = (lo + hi) / 2;
      if (need.reduce((a, x) => a + Math.max(c, x), 0) > stageS) hi = c;
      else lo = c;
    }
    steps = need.map((x) => Math.max(lo, x));
  }
  // cumulative stage time at each step's end
  const at = [0];
  for (const s of steps) at.push(at.at(-1) + s);
  const du = span / n;
  return (share) => {
    const s = Math.max(0, Math.min(1, share)) * stageS;
    if (s >= at.at(-1)) return Math.min(span, (at.length - 1) * du);
    let k = 0;
    while (k < n - 1 && at[k + 1] < s) k += 1;
    return Math.min(span, (k + (s - at[k]) / Math.max(1e-12, steps[k])) * du);
  };
}
