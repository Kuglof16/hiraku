// node_motion: where the nodes of a deforming model are over time (a field of kind node_motion,
// e.g. the soft-body crash). Two halves, so a later replay (driving into the wall, arrows) can
// use the first without the second:
//   nodeMotionOf / nodeMotionAt / nodeMotionTau: pure functions, time -> node positions and the
//     members broken by then (the recorded values, linearly between two kept frames; no scaling:
//     the positions are drawn at their real size)
//   NodeMotionView: puts that on the workbench's 3D (lines between the nodes, the broken members
//     as grey dashes) with a small tag
// Nothing here makes a value up: positions and broken lists are the record's, the time is the
// record's t_s.

import * as THREE from "three";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import { esc } from "./labels.js";

// The analysis' whole time (0 .. 94.9 ms in the demo crash) is played over this many seconds of
// the replay, from when the analysis arrived in the record; after that the last frame stays.
export const NODE_MOTION_PLAY_S = 4;

const INTACT_COLOR = "#1d1d1f"; // the workbench's ink
// a broken member: grey and dashed (red is kept for NG judgements only)
export const BROKEN_COLOR = "#8a857b";

const finite3 = (p) => Array.isArray(p) && p.length === 3 && p.every(Number.isFinite);

// The node_motion field of an analysis, checked: one position per node in every frame, t_s
// rising. Null when there is none or its shape is off. Kept per analysis object.
const memo = new WeakMap();
export function nodeMotionOf(analysis) {
  if (!analysis || analysis.status !== "ok") return null;
  if (memo.has(analysis)) return memo.get(analysis);
  memo.set(analysis, null);
  const field = (analysis.fields ?? []).find((f) => f.kind === "node_motion");
  const fr = field?.frames;
  const t = fr?.t_s;
  const pos = fr?.positions_mm;
  if (!Array.isArray(field?.nodes) || !Array.isArray(t) || !t.length || !Array.isArray(pos) || pos.length !== t.length) return null;
  const n = field.nodes.length;
  if (!t.every(Number.isFinite) || t.some((v, i) => i > 0 && v < t[i - 1])) return null;
  if (!pos.every((list) => Array.isArray(list) && list.length === n && list.every(finite3))) return null;
  const index = new Map(field.nodes.map((id, i) => [id, i]));
  const members = (field.members ?? [])
    .filter((m) => Array.isArray(m) && index.has(m[0]) && index.has(m[1]))
    .map((m) => ({ a: index.get(m[0]), b: index.get(m[1]), id: String(m[2] ?? "") }));
  const broken = Array.isArray(fr.broken) && fr.broken.length === t.length ? fr.broken.map((b) => (Array.isArray(b) ? b.map(String) : [])) : null;
  const nm = { analysisId: analysis.analysis_id, field, nodes: field.nodes, members, t_s: t, positions: pos, broken, t0: t[0], t1: t[t.length - 1], span: t[t.length - 1] - t[0] };
  memo.set(analysis, nm);
  return nm;
}

// The motion at time tau (the analysis' own seconds): every node's position (mm, in nodes order)
// linearly between the kept frames around tau (held at the first / last frame outside them), and
// the members broken by then (the broken list of the last kept frame at or before tau).
export function nodeMotionAt(nm, tau) {
  const t = nm.t_s;
  const last = t.length - 1;
  const at = Math.max(nm.t0, Math.min(nm.t1, Number.isFinite(tau) ? tau : nm.t0));
  let k = 0;
  while (k < last && t[k + 1] <= at) k += 1;
  const u = k < last && t[k + 1] > t[k] ? (at - t[k]) / (t[k + 1] - t[k]) : 0;
  const a = nm.positions[k];
  const b = nm.positions[Math.min(k + 1, last)];
  const positions = a.map((p, i) => (u === 0 ? [p[0], p[1], p[2]] : [p[0] + (b[i][0] - p[0]) * u, p[1] + (b[i][1] - p[1]) * u, p[2] + (b[i][2] - p[2]) * u]));
  return { tau: at, k, u, positions, broken: new Set(nm.broken?.[k] ?? []) };
}

// The analysis' time shown at a replay time: the analysis' whole span over NODE_MOTION_PLAY_S
// seconds of the replay from when it arrived (arrivedAt), held at the ends. Only the replay's
// time goes in, so ?t= gives the same picture each time.
export function nodeMotionTau(nm, playhead, arrivedAt) {
  const f = Math.max(0, Math.min(1, (playhead - (arrivedAt ?? 0)) / NODE_MOTION_PLAY_S));
  return nm.t0 + f * nm.span;
}

// Seconds of replay the motion needs after its analysis arrives (null: no node motion), so the
// replay can be made long enough to play it to its end.
export function nodeMotionPlaySpan(analysis) {
  return nodeMotionOf(analysis) ? NODE_MOTION_PLAY_S : null;
}

function lineMat(color, width, dashed) {
  const m = new LineMaterial({ color, linewidth: width, dashed, dashSize: 40, gapSize: 30 });
  m.userData.shared = true;
  return m;
}

// The motion on a Workbench3D: the members as lines in its iso group (mm, the design's frame), the
// design's own frame, parts, wheels and centre of gravity hidden while it shows (they do not move with it), and a tag
// in host (the 3D's viewport) saying it is the nodes' motion at real size, the time and the broken
// members. set(null) takes it away and shows the design again.
export class NodeMotionView {
  constructor(bench, host) {
    this.bench = bench;
    this.group = new THREE.Group();
    this.intactMat = lineMat(INTACT_COLOR, 2.4, false);
    this.brokenMat = lineMat(BROKEN_COLOR, 2.2, true);
    this.intact = new LineSegments2(new LineSegmentsGeometry(), this.intactMat);
    this.brokenLines = new LineSegments2(new LineSegmentsGeometry(), this.brokenMat);
    this.group.add(this.intact, this.brokenLines);
    this.group.visible = false;
    bench.iso.add(this.group);
    this.tag = document.createElement("div");
    this.tag.className = "nm-tag";
    this.tag.hidden = true;
    host.appendChild(this.tag);
    this.key = null;
    this.state = null; // what is drawn: { analysisId, tau, k, u, broken: [...] } (read by the tests)
  }

  set(nm, at) {
    const on = !!nm && !!at;
    if (this.group.visible !== on) {
      this.bench.body.visible = !on;
      this.bench.wheels.visible = !on;
      this.group.visible = on;
      this.tag.hidden = !on;
    }
    if (!on) {
      this.key = null;
      this.state = null;
      return;
    }
    this.bench.cg.visible = false; // the design's centre of gravity does not move with it either
    const { w, h } = this.bench.size;
    this.intactMat.resolution.set(w, h);
    this.brokenMat.resolution.set(w, h);
    const key = `${nm.analysisId}|${at.tau}`;
    if (key !== this.key) {
      this.key = key;
      const intact = [];
      const broken = [];
      for (const m of nm.members) {
        const out = at.broken.has(m.id) ? broken : intact;
        out.push(...at.positions[m.a], ...at.positions[m.b]);
      }
      this.fill(this.intact, intact);
      this.fill(this.brokenLines, broken);
      const nBroken = at.broken.size;
      this.tag.innerHTML =
        `<b>節の動き・実寸</b> <span class="nm-t">t = ${(at.tau * 1000).toFixed(2)} ms</span>` +
        `<br><i class="nm-key"></i>灰色の破線 = 折れた梁 <span class="nm-n">${nBroken}</span> 本` +
        (nm.field.label ? `<br><small>${esc(nm.field.label)}</small>` : "");
      this.state = { analysisId: nm.analysisId, tau: at.tau, k: at.k, u: at.u, broken: [...at.broken].sort() };
    }
    this.placeTag();
  }

  fill(lines, xyz) {
    lines.geometry.dispose();
    lines.geometry = new LineSegmentsGeometry();
    if (xyz.length) lines.geometry.setPositions(xyz);
    lines.visible = xyz.length > 0;
    if (lines.material.dashed && xyz.length) lines.computeLineDistances();
  }

  // Above the colour legend when that shows (both sit at the 3D's lower right).
  placeTag() {
    const lg = this.tag.parentElement?.querySelector(":scope > .legend");
    const shown = lg && !lg.hidden && lg.offsetHeight > 0;
    this.tag.style.bottom = shown ? `${parseFloat(getComputedStyle(lg).bottom) + lg.offsetHeight + 8}px` : "";
  }
}
