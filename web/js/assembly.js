// The assembly order (A7, and the video's cut N16), drawn from the record only:
// analysis.finished data.assembly (assembly_step, in order) and data.interferences,
// with the parts' shapes and places from design.created.
//
//   assemblyRecord(s)   the newest assembly record among the events applied
//   AssemblyStage       a Workbench3D posed at a point of the order: steps before
//                       the current one in place, the current part on its path_mm,
//                       later parts not drawn
//   stepRowsHtml        the list of steps (checks, fastenings), the current one marked
//   AssemblySeek        the bar under the 3D: one tick per step, a knob, play
//   AssemblyPanel       the A7 tab: the three together, the bar under the hand; opened by
//                       ?tabs= (or ?t= with &asm_from=) its progress follows the replay's time
//                       (PART_S a part, timedU; ?asm_from= the step it starts from)
//
// Progress u runs from 0 to N (N steps): step k goes in while u runs from k-1 to k,
// and u = k is the state with step k just in place.

import * as THREE from "three";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import { esc, fmt, keepAsides } from "./labels.js";
import { VIEWS, Workbench3D } from "./workbench3d.js";
import { mountAssemblySwitch } from "./modules.js"; // modules:
import { assemblyJointControls, JOINT_KINDS } from "./joints.js"; // joints:

export const CHECKS = [
  { key: "no_interference", ja: "干渉", title: "ほかの部品と重ならずに入る" },
  { key: "tool_clearance", ja: "工具", title: "工具の入る隙間がある" },
  { key: "hand_reach", ja: "手", title: "人の手が届く" },
];

// bushing and ball_joint: the contract's fastening kinds since dcb3a90 (fixed with their own
// through-bolt or castle nut)
export const FASTEN_JA = { bolt: "ボルト", weld: "溶接", rest: "載せる", pin: "ピン", bushing: "ブッシュ", ball_joint: "ボールジョイント" };

const CURRENT = "#e9893a";
const NG = "#c8402a";
const PATH = "#2b59c3";
// pin: joints.js's pin colour, not #8e6bbf (the packager's module colour on A7's 3D, proposals.js);
// bushing and ball_joint: joints.js's colours of the same kinds
export const FASTEN_COLOR = { bolt: "#1d1d1f", weld: "#c8402a", rest: "#6b665c", pin: "#1d1d9e", bushing: JOINT_KINDS.bushing.color, ball_joint: JOINT_KINDS.ball_joint.color };
// Parts that are not built into the vehicle (people and their load): not drawn on the bench.
const NOT_BUILT = new Set(["occupant", "cargo"]);

// The newest ok analysis (in the order applied) that carries assembly steps.
export function assemblyRecord(s) {
  const ids = s?.order?.analyses ?? [];
  for (let i = ids.length - 1; i >= 0; i -= 1) {
    const a = s.analyses[ids[i]];
    if (a?.status !== "ok" || !Array.isArray(a.assembly) || !a.assembly.length) continue;
    const design = s.designs?.[a.design_version] ?? s.designs?.[s.currentVersion] ?? null;
    const steps = [...a.assembly].sort((x, y) => x.order - y.order);
    return { ana: a, steps, interferences: a.interferences ?? [], design };
  }
  return null;
}

export function partNames(design) {
  return Object.fromEntries((design?.parts ?? []).map((p) => [p.id, p.name ?? p.id]));
}

// The name before a bracket ("後右の車輪 (タイヤ…)" -> "後右の車輪").
export const shortName = (name) => String(name).replace(/\s*[（(].*$/, "") || String(name);

// A step's part by name: the design's part, else a group built first (the contract's
// "corner:FL" for a suspension corner) as "前左の足まわり (先に組んだ組)" — the corner's
// position in the words the design's part names use (前 / 後, 左 / 右) — else the id.
const CORNER_JA = { FL: "前左", FR: "前右", RL: "後左", RR: "後右" };
export function nameOf(names, id) {
  if (names[id]) return names[id];
  const m = /^corner:(.+)$/.exec(String(id));
  if (!m) return String(id);
  return CORNER_JA[m[1]] ? `${CORNER_JA[m[1]]}の足まわり (先に組んだ組)` : `${m[1]} の角の組`;
}

// "node LB" -> "節点 LB", "member x" -> its member.name (memberNames), else "部材 x",
// "part susp_RR" -> that part's name.
export function fastenTarget(to, names, memberNames = {}) {
  const [head, ...rest] = String(to).split(" ");
  const id = rest.join(" ");
  if (head === "node") return `節点 ${id}`;
  if (head === "member") return memberNames[id] ? shortName(memberNames[id]) : `部材 ${id}`;
  if (head === "part") return shortName(names[id] ?? id);
  return String(to);
}

export function fastenText(step, names, memberNames = {}) {
  const fs = step.fastenings ?? [];
  if (!fs.length) return "";
  // each target once, with how many fastenings go there (a corner bolted 9 times at node RB:
  // "節点 RB (9 か所)")
  const byKind = new Map();
  for (const f of fs) {
    if (!byKind.has(f.kind)) byKind.set(f.kind, new Map());
    const t = fastenTarget(f.to, names, memberNames);
    const m = byKind.get(f.kind);
    m.set(t, (m.get(t) ?? 0) + 1);
  }
  return [...byKind].map(([k, ts]) => `${FASTEN_JA[k] ?? k}: ${[...ts].map(([t, n]) => (n > 1 ? `${t} (${n} か所)` : t)).join("・")}`).join(" / ");
}

export const stepOk = (step) => CHECKS.every((c) => step[c.key] === true);
// The checks whose failing is drawn in red on A7: a part that does not fit. Tool room and hand
// reach failing are cautions (the caution colour), as on N16.
export const HARD_CHECKS = ["no_interference"];

// One interference of the record in words: each side by its name in the design ("member x" as a
// fastening's target, a part by its name, else the id as written), how deep, and when. The
// condition is shown only when it says more than the state ("at rest" / "in motion" do not).
const SAME_AS_STATE = /^(at rest|rest|in motion|motion)$/i;
export function interferenceRow(x, design) {
  const names = partNames(design);
  const memberNames = Object.fromEntries((design?.members ?? []).filter((m) => m.name).map((m) => [m.id, m.name]));
  const side = (id) => {
    const s = String(id);
    if (/^(member|part|node) /.test(s)) return fastenTarget(s, names, memberNames);
    return names[s] ? shortName(names[s]) : s;
  };
  const when = x.state === "motion" ? "動きの中" : "止まった状態";
  const cond = x.condition && !SAME_AS_STATE.test(String(x.condition).trim()) ? ` (${esc(x.condition)})` : "";
  return `<li title="${esc(`${x.a} × ${x.b}`)}">${esc(side(x.a))} × ${esc(side(x.b))}・${esc(fmt(x.depth_mm, 3))} mm・${when}${cond}</li>`;
}

// The step being shown at progress u (1-based) and how far it has gone in (0..1).
export function stepAt(u, n) {
  if (n <= 0) return { current: 0, frac: 0 };
  const v = Math.max(0, Math.min(n, u));
  const current = Math.max(1, Math.ceil(v));
  return { current, frac: v - (current - 1) };
}

// The point at fraction f of a polyline's length.
export function pointOnPath(path, f) {
  const pts = path ?? [];
  if (pts.length === 0) return null;
  if (pts.length === 1) return [...pts[0]];
  const lens = [];
  let total = 0;
  for (let i = 0; i + 1 < pts.length; i += 1) {
    const l = Math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1], pts[i + 1][2] - pts[i][2]);
    lens.push(l);
    total += l;
  }
  if (total <= 0) return [...pts.at(-1)];
  let d = Math.max(0, Math.min(1, f)) * total;
  for (let i = 0; i < lens.length; i += 1) {
    if (d <= lens[i] || i === lens.length - 1) {
      const k = lens[i] > 0 ? Math.min(1, d / lens[i]) : 1;
      return [0, 1, 2].map((j) => pts[i][j] + (pts[i + 1][j] - pts[i][j]) * k);
    }
    d -= lens[i];
  }
  return [...pts.at(-1)];
}

// Ease in and out over a step (a part starts and stops gently).
const ease = (f) => 0.5 - 0.5 * Math.cos(Math.PI * Math.max(0, Math.min(1, f)));

function boxEdges(size) {
  const geo = new THREE.BoxGeometry(size[0], size[1], size[2]);
  const edges = new THREE.EdgesGeometry(geo);
  geo.dispose();
  const pos = edges.getAttribute("position");
  const out = [];
  for (let i = 0; i < pos.count; i += 1) out.push(pos.getX(i), pos.getY(i), pos.getZ(i));
  edges.dispose();
  return out;
}

// ---- 3D ---------------------------------------------------------------------------
// A Workbench3D posed at a point of the assembly order. The view is passed in (A7 gives
// one with its own renderer, the cut one on its shared renderer).
export class AssemblyStage {
  constructor(view) {
    this.view = view;
    this.rec = null;
    this.overlay = new THREE.Group();
    view.iso.add(this.overlay);
    this.lineMats = [];
    this.u = null;
  }

  lineMat(color, width) {
    const m = new LineMaterial({ color, linewidth: width, transparent: true, depthTest: false });
    this.lineMats.push(m);
    return m;
  }

  // Line widths are in stage pixels: the view's size in stage pixels.
  lineResolution(w, h) {
    for (const m of this.lineMats) m.resolution.set(w, h);
  }

  setRecord(rec) {
    if (rec === this.rec) return;
    this.rec = rec;
    const v = this.view;
    for (const o of [...this.overlay.children]) {
      o.traverse((x) => {
        x.geometry?.dispose?.();
        x.material?.dispose?.();
      });
    }
    this.overlay.clear();
    this.lineMats = [];
    this.u = null;
    v.setDesign(rec?.design ?? null);
    if (!rec?.design) return;
    v.setLoading("empty");
    v.setScene(null, null);
    v.setToggles({ parts: true, contour: false, arrows: false, cg: false, names: false, limit: false });
    v.links.visible = false; // the support-to-frame lines would stand without their wheels

    // What each step moves: a part's group, a wheel drawn from its support, or the frame.
    const supports = rec.design.supports ?? [];
    const partById = Object.fromEntries((rec.design.parts ?? []).map((p) => [p.id, p]));
    const stepped = new Set(rec.steps.map((s) => s.part));
    this.movers = rec.steps.map((step) => {
      const p = partById[step.part];
      const end = step.path_mm?.length ? step.path_mm.at(-1) : p?.box?.center_mm ?? [0, 0, 0];
      if (v.partObjs[step.part]) return { step, kind: "part", obj: v.partObjs[step.part], part: p, end };
      if (p) {
        const c = p.box.center_mm;
        const sup = supports.find((sp) => Math.hypot(sp.position_mm[0] - c[0], sp.position_mm[1] - c[1], sp.position_mm[2] - c[2]) < 50);
        const w = sup ? v.wheelObjs[sup.id] : null;
        if (w) return { step, kind: "wheel", obj: w, base: w.group.matrix.clone(), part: p, end };
      }
      if (step.part === "frame") return { step, kind: "frame", obj: null, part: null, end };
      return { step, kind: "none", obj: null, part: p ?? null, end };
    });
    this.frameStep = this.movers.find((m) => m.kind === "frame") ?? null;
    // parts no step brings in: built parts stay in place, people and load are left out
    this.still = Object.entries(v.partObjs)
      .filter(([id]) => !stepped.has(id))
      .map(([id, o]) => ({ o, shown: !NOT_BUILT.has(o.kind) }));

    // the current step's path, its outline and where it is fastened
    this.pathLine = new LineSegments2(new LineSegmentsGeometry(), this.lineMat(PATH, 2.5));
    this.pathLine.material.dashed = false;
    this.pathLine.material.opacity = 0.75;
    this.pathLine.renderOrder = 12;
    this.outline = new LineSegments2(new LineSegmentsGeometry(), this.lineMat(CURRENT, 3));
    this.outline.renderOrder = 12;
    this.fastens = new THREE.Group();
    this.overlay.add(this.pathLine, this.outline, this.fastens);
    this.outlineFor = null;
    this.fastenFor = null;
  }

  // The points the camera should take in: the vehicle and every path from outside.
  points() {
    const pts = this.view.contentPoints();
    for (const s of this.rec?.steps ?? []) for (const p of s.path_mm ?? []) pts.push(new THREE.Vector3(p[0], p[1], p[2]));
    return pts;
  }

  // padBelow: room under the vehicle, as a fraction of its height with the paths (for a
  // bar laid over the view's lower edge).
  fit(margin = 1.05, dir = VIEWS.default, padBelow = 0) {
    if (!this.rec?.design) return;
    const pts = this.points();
    const box = new THREE.Box3().setFromPoints(pts);
    if (padBelow > 0) {
      const c = box.getCenter(new THREE.Vector3());
      pts.push(new THREE.Vector3(c.x, c.y, box.min.z - padBelow * (box.max.z - box.min.z)));
    }
    this.view.fitPoints(pts, dir, margin);
  }

  pose(u) {
    if (!this.rec?.design || u === this.u) return;
    this.u = u;
    const n = this.movers.length;
    const { current, frac } = stepAt(u, n);
    const f = ease(frac);
    const v = this.view;
    v.model.visible = !this.frameStep || this.frameStep.step.order <= this.movers[current - 1]?.step.order;
    for (const s of this.still) s.o.group.visible = s.shown;
    let cur = null;
    this.movers.forEach((m, i) => {
      const idx = i + 1;
      const shown = idx <= current;
      const moving = idx === current && frac < 1;
      const at = moving ? pointOnPath(m.step.path_mm ?? [m.end], f) : m.end;
      const off = [at[0] - m.end[0], at[1] - m.end[1], at[2] - m.end[2]];
      if (m.kind === "part") {
        m.obj.group.visible = shown === true;
        m.obj.group.position.set(off[0], off[1], off[2]);
      } else if (m.kind === "wheel") {
        m.obj.group.visible = shown === true;
        m.obj.group.matrix.copy(m.base);
        m.obj.group.matrix.elements[12] += off[0];
        m.obj.group.matrix.elements[13] += off[1];
        m.obj.group.matrix.elements[14] += off[2];
        m.obj.group.matrixWorldNeedsUpdate = true;
      }
      if (idx === current) cur = { m, at };
    });
    this.markCurrent(cur);
    v.joints.sync(true); // joints: the marks of the parts in place, moving with the part coming in
  }

  markCurrent(cur) {
    // this.ngChecks (keys of CHECKS): only these failing make the outline red (N16: only a
    // part that does not fit; a step hard for a tool or a hand stays in the current colour)
    const ok = cur ? (this.ngChecks ? this.ngChecks.every((k) => cur.m.step[k] === true) : stepOk(cur.m.step)) : true;
    // the path from outside to the place
    const path = cur?.m.step.path_mm ?? [];
    const segs = [];
    for (let i = 0; i + 1 < path.length; i += 1) segs.push(...path[i], ...path[i + 1]);
    this.pathLine.geometry.dispose();
    this.pathLine.geometry = new LineSegmentsGeometry().setPositions(segs.length ? segs : [0, 0, 0, 0, 0, 0]);
    this.pathLine.visible = segs.length > 0;
    // the outline of the part where it is now (red when a check fails)
    const p = cur?.m.part;
    if (p?.box?.size_mm) {
      if (this.outlineFor !== p.id) {
        this.outline.geometry.dispose();
        this.outline.geometry = new LineSegmentsGeometry().setPositions(boxEdges(p.box.size_mm.map((x) => x + 30)));
        const rpy = p.box.rpy_deg ?? [0, 0, 0];
        this.outline.rotation.set((rpy[0] * Math.PI) / 180, (rpy[1] * Math.PI) / 180, (rpy[2] * Math.PI) / 180, "ZYX");
        this.outlineFor = p.id;
      }
      const c = p.box.center_mm;
      const off = [cur.at[0] - cur.m.end[0], cur.at[1] - cur.m.end[1], cur.at[2] - cur.m.end[2]];
      this.outline.position.set(c[0] + off[0], c[1] + off[1], c[2] + off[2]);
      this.outline.material.color.set(ok ? CURRENT : NG);
      this.outline.visible = true;
    } else {
      this.outline.visible = false;
    }
    // where it is fastened (a small ball per fastening that gives a point)
    const key = cur ? cur.m.step.order : null;
    if (key !== this.fastenFor) {
      this.fastenFor = key;
      for (const o of [...this.fastens.children]) {
        o.geometry.dispose();
        o.material.dispose();
      }
      this.fastens.clear();
      for (const fz of cur?.m.step.fastenings ?? []) {
        if (!fz.at_mm) continue;
        const ball = new THREE.Mesh(new THREE.SphereGeometry(28, 16, 10), new THREE.MeshBasicMaterial({ color: FASTEN_COLOR[fz.kind] ?? "#1d1d1f", depthTest: false, transparent: true, opacity: 0.9 }));
        ball.position.set(fz.at_mm[0], fz.at_mm[1], fz.at_mm[2]);
        ball.renderOrder = 13;
        this.fastens.add(ball);
      }
    }
  }
}

// ---- the list of steps -----------------------------------------------------------
// (the step's sentence: each aside in brackets one piece, labels.js keepAsides)
const mark = (ok) => (ok ? `<span class="ok">✓</span>` : `<span class="ng">✕</span>`);

// opts.compact: one line per step (the video's list); otherwise the step's sentence,
// its fastenings and what blocks it when a check fails. opts.box: data-box prefix.
export function stepRowsHtml(rec, current, opts = {}) {
  const names = partNames(rec.design);
  const memberNames = Object.fromEntries((rec.design?.members ?? []).filter((m) => m.name).map((m) => [m.id, m.name]));
  const memberIds = new Set((rec.design?.members ?? []).map((m) => m.id));
  return rec.steps
    .map((s) => {
      const name = shortName(nameOf(names, s.part));
      const checks = CHECKS.map((c) => `<span class="ck ck-${c.key} ${s[c.key] ? "is-ok" : "is-ng"}" title="${esc(c.title)}">${esc(c.ja)}${mark(s[c.key] === true)}</span>`).join("");
      // what blocks it: a part by its name, a member by its name (else "部材 <id>"), else as written
      const blockName = (b) => (names[b] ? shortName(names[b]) : memberIds.has(b) ? fastenTarget(`member ${b}`, names, memberNames) : b);
      const blocked = !stepOk(s) && (s.blocked_by ?? []).length ? `<div class="blk">止めているもの: ${esc(s.blocked_by.map(blockName).join("・"))}</div>` : "";
      const cls = `${s.order === current ? "cur" : ""} ${stepOk(s) ? "" : "has-ng"} ${s.order < current ? "done" : ""}`;
      const box = opts.box ? ` data-box="${opts.box}.${s.order}"` : "";
      if (opts.compact) {
        return `<li class="${cls}" data-order="${s.order}"${box}><span class="no">${s.order}</span><span class="nm">${esc(name)}</span><span class="cks">${checks}</span></li>`;
      }
      const fasten = fastenText(s, names, memberNames);
      return (
        `<li class="${cls}" data-order="${s.order}"${box}>` +
        `<div class="row1"><span class="no">${s.order}</span><span class="nm" title="${esc(names[s.part] ?? s.part)}">${esc(name)}</span><span class="cks">${checks}</span></div>` +
        (s.text ? `<div class="tx">${keepAsides(s.text)}</div>` : "") +
        (fasten ? `<div class="fz">留め方 ${esc(fasten)}</div>` : "") +
        blocked +
        `</li>`
      );
    })
    .join("");
}

// One line of totals from the steps: how many, and how many fail each check.
export function summaryText(rec) {
  const n = rec.steps.length;
  const fails = CHECKS.map((c) => [c, rec.steps.filter((s) => s[c.key] !== true).length]);
  const parts = fails.map(([c, k]) => `${c.ja} ${k ? `✕ ${k}` : "✓"}`);
  return `手順 ${n}・${parts.join("・")}・干渉の記録 ${rec.interferences.length} 件`;
}

// ---- the seek bar ---------------------------------------------------------------
// el: an empty element. interactive: the bar follows the hand and has a play button
// (A7); otherwise it only shows where the order is (the video).
export class AssemblySeek {
  constructor(el, { interactive = true, onSeek = null, onPlay = null, textPx = null } = {}) {
    this.el = el;
    this.n = 0;
    el.classList.add("asm-seek");
    el.innerHTML =
      (interactive ? `<button class="as-play" title="組み立てを再生">▶</button>` : "") +
      `<div class="as-track"><div class="as-fill"></div><div class="as-ticks"></div><div class="as-knob"></div></div>` +
      `<span class="as-label"></span>`;
    if (textPx) el.style.fontSize = `${textPx}px`;
    this.track = el.querySelector(".as-track");
    this.play = el.querySelector(".as-play");
    if (interactive) {
      // A drag lands on whole steps: the state with that step in place.
      const at = (ev) => {
        const r = this.track.getBoundingClientRect();
        const f = Math.max(0, Math.min(1, (ev.clientX - r.left) / Math.max(1, r.width)));
        onSeek?.(Math.round(f * this.n));
      };
      this.track.addEventListener("pointerdown", (ev) => {
        this.track.setPointerCapture?.(ev.pointerId);
        at(ev);
        const move = (e) => at(e);
        this.track.addEventListener("pointermove", move);
        this.track.addEventListener("pointerup", () => this.track.removeEventListener("pointermove", move), { once: true });
      });
      this.play.addEventListener("click", () => onPlay?.());
    }
  }

  // ngChecks (keys of CHECKS, else all): the checks whose failing marks a step's tick red
  setSteps(steps, ngChecks = null) {
    this.n = steps.length;
    const ok = (s) => (ngChecks ? ngChecks.every((k) => s[k] === true) : stepOk(s));
    this.el.querySelector(".as-ticks").innerHTML = steps
      .map((s, i) => `<i class="${ok(s) ? "" : "ng"}" style="left:${(this.n ? (i + 1) / this.n : 0) * 100}%" title="手順 ${s.order}"></i>`)
      .join("");
  }

  set(u, playing = false) {
    const f = this.n ? Math.max(0, Math.min(1, u / this.n)) : 0;
    this.el.querySelector(".as-fill").style.width = `${f * 100}%`;
    this.el.querySelector(".as-knob").style.left = `${f * 100}%`;
    const { current } = stepAt(u, this.n);
    this.el.querySelector(".as-label").textContent = `手順 ${current} / ${this.n}`;
    if (this.play) this.play.textContent = playing ? "❚❚" : "▶";
    this.el.dataset.u = String(u);
  }
}

// ---- A7 on the replay's clock -----------------------------------------------------------
// A7 opened by ?tabs= (or ?t= with &asm_from=) takes its progress from the replay's time: from the time
// A7 came up, one part per PART_S seconds. The record has no times for the assembly, so PART_S
// is the screen's pace of showing it (a choice of the screen, not a value from the record).
export const PART_S = 0.4;

// ?asm_from=-6: start with the last 6 parts going in; ?asm_from=k: start with step k (its
// order). The parts before are in place from the start. The u the order starts from (0..n).
export function startU(steps, from) {
  const n = steps.length;
  const k = from === null || from === undefined || String(from).trim() === "" ? NaN : Number(from);
  if (!Number.isFinite(k) || k === 0) return 0;
  if (k < 0) return Math.max(0, n + Math.round(k));
  const i = steps.findIndex((s) => Number(s.order) === k);
  return i >= 0 ? i : Math.max(0, Math.min(n, Math.round(k) - 1));
}

// The progress after `elapsed` seconds in A7 (the same time and arguments give the same u).
export function timedU(steps, from, elapsed, partS = PART_S) {
  return Math.min(steps.length, startU(steps, from) + Math.max(0, elapsed) / partS);
}

// ---- A7 -------------------------------------------------------------------------------
const PLAY_STEP_S = 1.2; // seconds per step when the tab plays the order
const ASM_FROM = new URLSearchParams(globalThis.location?.search ?? "").get("asm_from");

export class AssemblyPanel {
  constructor(host) {
    this.host = host;
    host.innerHTML =
      `<div class="asm-head"><h2>組み立て</h2><span class="asm-sum"></span></div>` +
      `<p class="asm-none" hidden>組み立ての記録がまだ無い</p>` +
      `<div class="asm-body">` +
      `<div class="asm-3d"><div class="asm-gl"></div><div class="asm-caption"></div><div class="asm-bar"></div></div>` +
      `<div class="asm-side"><ol class="asm-steps"></ol><div class="asm-intf"></div></div>` +
      `</div>`;
    this.q = (sel) => host.querySelector(sel);
    this.gl = this.q(".asm-gl");
    this.view = new Workbench3D(this.gl, "zu");
    this.stage = new AssemblyStage(this.view);
    // red only for a part that does not fit (as N16); tool room and hand reach are cautions, not red
    this.stage.ngChecks = HARD_CHECKS;
    this.modules = mountAssemblySwitch(this); // modules: the module colours and spaces (modules.js)
    this.jointCtl = assemblyJointControls(this.q(".asm-3d"), this.view); // joints: its own 接合 switch and legend
    this.seek = new AssemblySeek(this.q(".asm-bar"), {
      interactive: true,
      onSeek: (u) => {
        this.playing = false;
        this.u = u;
        this.byHand = true; // the hand takes the order off the replay's clock
      },
      onPlay: () => {
        if (!this.rec) return;
        this.byHand = true;
        this.playing = !this.playing;
        if (this.playing && this.u >= this.rec.steps.length) this.u = 0;
      },
    });
    this.q(".asm-steps").addEventListener("click", (ev) => {
      const li = ev.target.closest("li[data-order]");
      if (!li || !this.rec) return;
      const i = this.rec.steps.findIndex((s) => String(s.order) === li.dataset.order);
      if (i >= 0) {
        this.playing = false;
        this.u = i + 1;
        this.byHand = true;
      }
    });
    // video=1: the camera stays at the bench's default view (no orbit under a stray pointer)
    if (document.body.classList.contains("video") && "enabled" in this.view.controls) this.view.controls.enabled = false;
    this.rec = null;
    this.u = 0;
    this.byHand = false;
    this.playing = false;
    this.last = null;
    this.drawnCurrent = null;
    new ResizeObserver(() => this.resize()).observe(this.gl);
  }

  resize() {
    const w = this.gl.clientWidth;
    const h = this.gl.clientHeight;
    if (!(w > 0 && h > 0)) return;
    // the page's stage is scaled: draw at the pixels it takes on screen
    const scale = this.gl.getBoundingClientRect().width / w || 1;
    this.view.resize(w, h, (window.devicePixelRatio || 1) * scale);
    this.stage.lineResolution(w, h);
    if (this.rec && this.sized !== `${w}x${h}`) {
      this.sized = `${w}x${h}`;
      this.stage.fit(1.05, VIEWS.default, 0.25);
    }
  }

  // Called each frame while the tab is shown, with the events applied so far. clock (app.js):
  // { at: the replay's time, from: when A7 came up in ?tabs= (null: when its record arrived) },
  // or null for the tab's own bar and play.
  render(s, clock = null) {
    const rec = assemblyRecord(s);
    if (!this.started || rec?.ana !== this.rec?.ana || rec?.design !== this.rec?.design) {
      this.started = true;
      this.setRecord(rec);
    }
    const now = performance.now();
    const dt = this.last === null ? 0 : Math.min(0.25, (now - this.last) / 1000);
    this.last = now;
    if (!this.rec) return;
    const n = this.rec.steps.length;
    if (clock && !this.byHand) {
      const from = clock.from ?? s.tOf?.[this.rec.ana.analysis_id] ?? 0;
      this.u = timedU(this.rec.steps, ASM_FROM, clock.at - from);
      this.playing = false;
    } else if (this.playing) {
      this.u = Math.min(n, this.u + dt / PLAY_STEP_S);
      if (this.u >= n) this.playing = false;
    }
    this.stage.pose(this.u);
    this.seek.set(this.u, this.playing);
    const { current } = stepAt(this.u, n);
    if (current !== this.drawnCurrent) this.drawList(current);
    this.modules.update(); // modules:
    this.view.render();
  }

  setRecord(rec) {
    this.rec = rec;
    this.q(".asm-none").hidden = !!rec;
    this.q(".asm-body").hidden = !rec;
    this.q(".asm-sum").textContent = rec ? summaryText(rec) : "";
    this.drawnCurrent = null;
    this.playing = false;
    this.stage.setRecord(rec);
    if (this.jointCtl.want) this.jointCtl.set(true), (this.jointCtl.want = false); // joints: ?on=joints
    this.jointCtl.refresh(); // joints:
    if (!rec) return;
    this.u = rec.steps.length; // opens on the vehicle built, the last step marked
    this.seek.setSteps(rec.steps, HARD_CHECKS);
    this.sized = null;
    this.resize();
    this.q(".asm-intf").innerHTML = rec.interferences.length
      ? `<h3>干渉の記録</h3><ul>${rec.interferences.map((x) => interferenceRow(x, rec.design)).join("")}</ul>`
      : "";
  }

  drawList(current) {
    this.drawnCurrent = current;
    const ol = this.q(".asm-steps");
    ol.innerHTML = stepRowsHtml(this.rec, current, { box: "a7.step" });
    const step = this.rec.steps[current - 1];
    this.q(".asm-caption").innerHTML = step ? `<b>手順 ${step.order}</b> ${esc(step.text ?? shortName(nameOf(partNames(this.rec.design), step.part)))}` : "";
    const li = ol.querySelector("li.cur");
    if (li) {
      const top = li.offsetTop - ol.clientHeight / 2 + li.offsetHeight / 2;
      ol.scrollTop = Math.max(0, top);
    }
  }
}
