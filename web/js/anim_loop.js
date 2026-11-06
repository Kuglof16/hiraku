// ?play=loop&analysis=<id>: one analysis' motion on the workbench, played at 1x over and
// over, with the replay's playhead held at the time that analysis arrived (its load case and
// design version are the ones shown).
//
// The loop's clock c (seconds from the loop's start) is ?loop_t= when given, else the page's
// t (?t=, or window.hiraku.seek(t)), else the page's own clock. The motion's time is
//   tau = t0 + (c mod duration)
// so one c always gives the same picture (a film steps t and shoots each frame).
//
// ?cam=follow (default): the camera keeps on the vehicle (Workbench3D.follow, which shifts
// the camera and its target by how far the vehicle moved since the motion's first time).
// ?cam=side: the camera stands still beside the step's edge (else the middle of the path),
// framing the vehicle there from the side.
// ?cam=wide: the camera stands still from the side, drawn back so the vehicle's whole path
// (where the motion starts to where it ends, with the step) is in the frame: the vehicle drives
// across it.

import { VIEWS } from "./workbench3d.js";

export const LOOP_CAMS = ["follow", "side", "wide"];

function num(params, key) {
  if (!params.has(key) || params.get(key).trim() === "") return null;
  const v = Number(params.get(key));
  return Number.isFinite(v) ? v : null;
}

export function loopFromParams(params) {
  if (params.get("play") !== "loop" || !params.get("analysis")) return null;
  return new AnimLoop({
    analysisId: params.get("analysis"),
    cam: LOOP_CAMS.includes(params.get("cam")) ? params.get("cam") : "follow",
    clock: num(params, "loop_t") ?? num(params, "t"),
    clockFromT: num(params, "loop_t") === null, // hiraku.seek(t) sets the clock unless ?loop_t= fixed it
  });
}

// The motion's time for a loop clock c: t0 + (c mod duration) (a motion without length: t0).
export function loopTau(motion, c) {
  const d = motion.duration;
  if (!(d > 0) || !Number.isFinite(c)) return motion.t0;
  return motion.t0 + (((c % d) + d) % d);
}

export class AnimLoop {
  constructor({ analysisId, cam = "follow", clock = null, clockFromT = true }) {
    this.analysisId = analysisId;
    this.cam = cam;
    this.clock = clock ?? 0;
    this.fixed = clock !== null; // a fixed clock does not run with the page
    this.clockFromT = clockFromT;
    this.at = null; // the replay time the playhead is held at (the analysis' arrival), once found
    this.sideAim = null; // { target, position } the side camera was put at
  }

  // The display time of the analysis' arrival (its analysis.finished), or null.
  findArrival(events) {
    const ev = events.find((e) => e.kind === "analysis.finished" && e.data?.analysis_id === this.analysisId);
    this.at = ev ? ev.t : null;
    return this.at;
  }

  // The page's t (a seek): the loop's clock, when ?loop_t= did not fix it.
  seekClock(t) {
    if (!this.clockFromT || t === null || t === undefined) return;
    this.clock = Number.isFinite(t) ? t : 0;
    this.fixed = true;
  }

  tick(dt) {
    if (!this.fixed) this.clock += dt;
  }

  covers(ana) {
    return !!ana && ana.analysis_id === this.analysisId;
  }

  tau(motion) {
    return loopTau(motion, this.clock);
  }

  // The chain shown: the analysis' own load case and version, with this analysis on the 3D.
  chain(s, chainFor) {
    const ana = s.analyses?.[this.analysisId];
    if (!ana) return null;
    const chain = chainFor(s, ana.load_case_id, ana.design_version);
    return chain.shown === ana ? chain : { ...chain, shown: ana };
  }

  // Before each render: the side camera stands where it was put (anything that framed the view
  // again, e.g. a resize, is undone); follow leaves the camera to Workbench3D.follow.
  aim(bench) {
    const still = this.cam === "side" || this.cam === "wide";
    bench.noFollow = still;
    if (!still || !bench.design || !bench.motion) return;
    const a = this.sideAim;
    const moved = !a || a.key !== bench.sceneKey || a.w !== bench.renderer?.domElement?.width || bench.controls.target.distanceToSquared(a.target) > 1e-12 || bench.camera.position.distanceToSquared(a.position) > 1e-12;
    if (!moved) return;
    const pts = this.cam === "wide" ? widePoints(bench) : sidePoints(bench);
    if (!pts.length) return;
    bench.fitPoints(pts, VIEWS.side, 1.12);
    this.sideAim = { key: bench.sceneKey, w: bench.renderer?.domElement?.width, target: bench.controls.target.clone(), position: bench.camera.position.clone() };
  }
}

// Where the side camera looks (ISO mm along x): the step's edge (the recorded scene's step
// object, or the step drawn from the load case), else the middle of the vehicle's path.
export function sideFocusX(desc, motion) {
  const obj = (desc?.objects ?? []).find((o) => o.kind === "step" && Array.isArray(o.position_mm) && Array.isArray(o.size_mm));
  if (obj) return obj.position_mm[0] - obj.size_mm[0] / 2;
  if (desc?.step) return desc.step.x0;
  if (motion?.path) return (motion.path.minX + motion.path.maxX) / 2;
  return null;
}

// The vehicle's points (ISO mm) moved so their middle is at the focus, standing on the road
// and on top of the step (so both fit).
function sidePoints(bench) {
  const pts = bench.contentPoints();
  const fx = sideFocusX(bench.sceneDesc, bench.motion);
  if (!pts.length || fx === null) return [];
  let lo = Infinity;
  let hi = -Infinity;
  for (const p of pts) {
    lo = Math.min(lo, p.x);
    hi = Math.max(hi, p.x);
  }
  const dx = fx - (lo + hi) / 2;
  const h = stepHeight(bench.sceneDesc);
  const out = [];
  for (const p of pts) {
    out.push(p.clone().setX(p.x + dx));
    if (h > 0) out.push(p.clone().setX(p.x + dx).setZ(p.z + h));
  }
  return out;
}

// The vehicle's points (ISO mm) at the start and at the end of its recorded path (x only),
// standing on the road and on top of the step: the frame holds the whole run.
function widePoints(bench) {
  const pts = bench.contentPoints();
  const path = bench.motion?.path;
  if (!pts.length || !path || !Number.isFinite(path.minX) || !Number.isFinite(path.maxX)) return sidePoints(bench);
  let lo = Infinity;
  let hi = -Infinity;
  for (const p of pts) {
    lo = Math.min(lo, p.x);
    hi = Math.max(hi, p.x);
  }
  const mid = (lo + hi) / 2;
  const h = stepHeight(bench.sceneDesc);
  const out = [];
  for (const x of [path.minX, path.maxX]) {
    const dx = x - mid;
    for (const p of pts) {
      out.push(p.clone().setX(p.x + dx));
      if (h > 0) out.push(p.clone().setX(p.x + dx).setZ(p.z + h));
    }
  }
  return out;
}

function stepHeight(desc) {
  const obj = (desc?.objects ?? []).find((o) => o.kind === "step" && Array.isArray(o.size_mm));
  if (obj) return obj.size_mm[2];
  return desc?.step?.h ?? 0;
}
