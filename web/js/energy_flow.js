// The energy's flow over the 3D for a thermal analysis (the heat's showpiece): lines along the
// design's connects_to from the battery to the wheels, dashes running on them, a branch in the
// heat's colour out of each part whose loss is recorded (its name and value on a tag), and the
// parts with a temperature series coloured by it at the replay's time.
//
// Only recorded numbers are drawn. Where the record has no value nothing is put in its place:
// a link without a recorded power is a thin grey line (its width is not a value), a part
// without a recorded loss has no branch, a part no temperature series can be tied to is not
// coloured; the legend says which.
//
// Three steps, so a record with more series (one per part, varying in time) only changes the
// first:
//   flowModel(inputs)        what the record holds, tied to the design's parts (no time)
//   flowAt(model, tRec)      record time -> each line's watts and width, each part's
//                            temperature and colour (a pure function)
//   dashesAt(model, flow, t) replay time -> where the dashes are (a pure function of t and the
//                            dash's number: ?t= gives the same picture)
// and EnergyFlow puts them on the 3D (workbench3d.js's body group) and writes the legend.
//
// Series read (the unit from each series' own unit; W and kW are put on one scale):
//   temp.<part>        degC  the thermal record (now: battery, motor, controller)
//   power.<part>       W/kW  the thermal record's own (none yet), else another analysis of the
//                            same design version that has one (drive_energy: power.battery, kW)
//   loss.<part>        W     the thermal record's own (none yet), else its results
//                            loss_<part>_mean (W, where = the part)
//   heat_to_air.<part> W     the thermal record's own (none yet): written on the loss's tag
// A series is tied to a part by its part_id when it has one, else by the name after the dot:
// the part with that id, else the one part of that kind (two of the kind: neither).
// The thermal record's own series are read at the record time (they move); a value from
// another analysis, or a mean from the results, is one number for the whole run.
//
// Colours: electric and rotating lines in their own colours, heat (losses, the temperature's
// scale) in the heat colour, which is not the verdict's red. A temperature reaches the red end
// only above the part's limit (the verdict's limit, else the design's <part>_max_temperature).

import * as THREE from "three";
import { CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import { caseShortNames, esc, fmt } from "./labels.js";
import { channelsOf, lerpAt } from "./series.js";
import { verdictVersion } from "./state.js";

export const ELEC_COLOR = "#2f7fd0"; // electric power (via wire)
export const ROT_COLOR = "#7a5cc2"; // rotating power (via spline: torque)
// heat: losses and the temperature's scale. A vivid red-orange, told apart from the verdict's red
// (#c8402a, CIE76 dE 25) and from the caution / slip orange (#e9893a, dE 31) and the seats (#e9b872, dE 58)
export const HEAT_COLOR = "#ff5a1f";
export const CAUTION_ORANGE = "#e9893a"; // (colormap.js, chart.js: not used here; the tests compare with it)
export const VERDICT_RED = "#c8402a"; // css --red: only for a temperature above its limit
const DASH_TINT = { elec: "#eaf3ff", rot: "#f1ecff", heat: "#fff4e3" };
// the temperature's scale: from the lowest recorded value to the limit (the last stop at the limit)
const TEMP_STOPS = ["#fdf1ea", "#fcc8ae", "#fd9468", HEAT_COLOR];

export const HEAT_PLAY_S = 12; // a thermal record's run plays in this many seconds of the replay (as a test's motion)
const MIN_W = 3; // px: a line without a value, and the thinnest valued one
const MAX_W = 14; // px: the largest value in the record (over the whole run)
const DASH_MM = 90; // a dash's length
const DASH_GAP_MM = 260; // dashes' spacing along a line
const RUN_MM_S = 900; // a dash's speed on the largest value (mm per replay second); sqrt of the share below
const IDLE_MM_S = 180; // on a line without a value (the way the energy goes, not how much)
const BRANCH_MM = 420; // a loss branch goes this far up from its part's top
const UNIT_W = { W: 1, kW: 1000 };

const short = (p) => String(p?.name ?? p?.id ?? "").replace(/\s*[（(].*$/, "") || p?.id;

// [lo, hi] of an array (a loop: long series)
function extent(a) {
  let lo = Infinity;
  let hi = -Infinity;
  for (const v of a) {
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  return [lo, hi];
}

// time-weighted mean of a series (trapezoids over its own times)
export function timeMean(s) {
  const t = s.t_s;
  const v = s.values;
  if (t.length < 2 || !(t.at(-1) > t[0])) return v[0];
  let area = 0;
  for (let i = 1; i < t.length; i += 1) area += ((v[i] + v[i - 1]) / 2) * (t[i] - t[i - 1]);
  return area / (t.at(-1) - t[0]);
}

// The part a series or a result belongs to: its part_id, else the part with the name's id,
// else the one part of the name's kind. { id } or { id: null, why } when none (or two).
export function partFor(design, name, partId = null) {
  const parts = design?.parts ?? [];
  if (partId) return parts.some((p) => p.id === partId) ? { id: partId } : { id: null, why: `部品 ${partId} が設計に無い` };
  if (parts.some((p) => p.id === name)) return { id: name };
  const kind = parts.filter((p) => p.kind === name);
  if (kind.length === 1) return { id: kind[0].id };
  return { id: null, why: kind.length ? `${kind.map((p) => p.id).join("・")} のどれか分からない` : `${name} の部品が無い` };
}

// The power and torque links on a way from a battery to a wheel, in the design's direction.
export function flowLinks(design) {
  const parts = design?.parts ?? [];
  const byId = new Map(parts.map((p) => [p.id, p]));
  const edges = [];
  const seen = new Set();
  for (const p of parts) {
    for (const c of p.connects_to ?? []) {
      if (!(c.carries === "power" || c.carries === "torque") || !byId.has(c.to) || c.to === p.id) continue;
      const key = `${p.id}>${c.to}`;
      if (seen.has(key)) continue;
      seen.add(key);
      edges.push({ from: p.id, to: c.to, carries: c.carries, via: c.via ?? null });
    }
  }
  const reach = (starts, next) => {
    const out = new Set(starts);
    const todo = [...starts];
    while (todo.length) {
      const id = todo.pop();
      for (const n of next(id)) if (!out.has(n)) out.add(n), todo.push(n);
    }
    return out;
  };
  const fwd = reach(parts.filter((p) => p.kind === "battery").map((p) => p.id), (id) => edges.filter((e) => e.from === id).map((e) => e.to));
  const back = reach(parts.filter((p) => p.kind === "wheel").map((p) => p.id), (id) => edges.filter((e) => e.to === id).map((e) => e.from));
  return edges.filter((e) => fwd.has(e.from) && back.has(e.to));
}

const center = (p) => p.box.center_mm;
const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);

// A value's source: { kind: "series", series, k } read at t, or { kind: "mean", w } (W).
function seriesSource(s, from) {
  const k = UNIT_W[s.unit];
  if (k === undefined) return null;
  const [lo, hi] = extent(s.values);
  return { kind: "series", series: s, k, peak: Math.max(Math.abs(lo), Math.abs(hi)) * k, from, channel: s.channel, unit: s.unit };
}
const sourceW = (src, t) => (src.kind === "series" ? lerpAt(src.series, t) * src.k : src.w);

// The temperature's colour: the scale's stops from lo to the limit; above the limit (by the
// verdict's op) the verdict's red; no limit: the scale from lo to the series' highest value.
export function tempColor(T, lo, limit, op = "<=") {
  if (!Number.isFinite(T)) return null;
  if (limit != null && (op === "<" ? T >= limit : T > limit)) return VERDICT_RED;
  const f = limit != null && limit > lo ? (T - lo) / (limit - lo) : 0;
  const x = Math.max(0, Math.min(1, f)) * (TEMP_STOPS.length - 1);
  const i = Math.min(TEMP_STOPS.length - 2, Math.floor(x));
  return lerpHex(TEMP_STOPS[i], TEMP_STOPS[i + 1], x - i);
}
function lerpHex(a, b, f) {
  const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16));
  const pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16));
  return "#" + pa.map((v, k) => Math.round(v + (pb[k] - v) * f).toString(16).padStart(2, "0")).join("");
}
export const TEMP_SCALE = TEMP_STOPS;

// inputs: { design, thermal (analysis), energy (another analysis with power.*, or null),
// verdicts (the thermal domain's of the thermal record's version) }
export function flowModel({ design, thermal, energy = null, verdicts = [] }) {
  const byId = new Map((design?.parts ?? []).map((p) => [p.id, p]));
  const own = channelsOf(thermal);
  const other = energy ? channelsOf(energy) : new Map();
  const notes = []; // with the record's ids and channel names (?dev=1, the tests)
  const notesJa = []; // the same in words only (the legend)

  // power by part: the thermal record's own series, else the other analysis' (its time mean)
  const power = new Map();
  for (const [chs, isOwn] of [[own, true], [other, false]]) {
    for (const [name, s] of chs) {
      if (!name.startsWith("power.")) continue;
      const at = partFor(design, name.slice(6), s.part_id ?? null);
      if (!at.id || power.has(at.id)) continue;
      const src = seriesSource(s, isOwn ? thermal.analysis_id : energy.analysis_id);
      if (!src) continue;
      power.set(at.id, isOwn ? src : { kind: "mean", w: timeMean(s) * src.k, from: energy.analysis_id, channel: name, unit: s.unit, peak: Math.abs(timeMean(s) * src.k) });
    }
  }
  const links = flowLinks(design).map((e, i) => ({ ...e, i, a: center(byId.get(e.from)), b: center(byId.get(e.to)), fromName: short(byId.get(e.from)) }));
  for (const l of links) {
    const outs = links.filter((m) => m.from === l.from).length;
    l.source = outs === 1 ? power.get(l.from) ?? null : null; // a part's power on its one way out
    l.kind = l.carries === "power" ? "elec" : "rot";
    l.length = dist(l.a, l.b);
  }

  // losses by part: the thermal record's loss.<part> series, else its results loss_<part>_mean
  const losses = new Map();
  for (const [name, s] of own) {
    if (!name.startsWith("loss.")) continue;
    const at = partFor(design, name.slice(5), s.part_id ?? null);
    const src = at.id && seriesSource(s, thermal.analysis_id);
    if (src) losses.set(at.id, src);
  }
  for (const r of thermal?.results ?? []) {
    const m = /^loss_(.+)_mean$/.exec(r.quantity ?? "");
    if (!m || UNIT_W[r.unit] === undefined || !Number.isFinite(r.value)) continue;
    const at = partFor(design, r.where ?? m[1]);
    if (at.id && !losses.has(at.id)) losses.set(at.id, { kind: "mean", w: r.value * UNIT_W[r.unit], from: thermal.analysis_id, quantity: r.quantity, unit: r.unit, peak: Math.abs(r.value * UNIT_W[r.unit]) });
  }
  const toAir = new Map();
  for (const [name, s] of own) {
    if (!name.startsWith("heat_to_air.")) continue;
    const at = partFor(design, name.slice(12), s.part_id ?? null);
    const src = at.id && seriesSource(s, thermal.analysis_id);
    if (src) toAir.set(at.id, src);
  }
  const branches = [...losses].filter(([id]) => byId.has(id)).map(([id, source], i) => {
    const p = byId.get(id);
    const a = center(p);
    const b = [a[0], a[1], a[2] + p.box.size_mm[2] / 2 + BRANCH_MM];
    return { part: id, name: short(p), source, air: toAir.get(id) ?? null, a, b, length: dist(a, b), i };
  });

  // temperatures by part
  const temps = [];
  const tempIds = new Set();
  for (const [name, s] of own) {
    if (!name.startsWith("temp.") || s.unit !== "degC") continue;
    const key = name.slice(5);
    const at = partFor(design, key, s.part_id ?? null);
    if (!at.id) {
      notes.push(`${name} は部品につなげないので塗らない: ${at.why}`);
      notesJa.push("温度の記録の一つは部品につなげないので塗らない");
      continue;
    }
    const p = byId.get(at.id);
    const v = verdicts.filter((x) => x.quantity === `temp_${key}_max` && x.limit?.unit === "degC").at(-1) ?? null;
    const variable = design?.variables?.[`${key}_max_temperature`];
    const limit = v ? { value: v.limit.value, op: v.op, from: "判定", pass: v.pass } : variable?.unit === "degC" && Number.isFinite(variable.value) ? { value: variable.value, op: "<=", from: "設計の上限", pass: null } : null;
    const [lo, hi] = extent(s.values);
    temps.push({ part: at.id, name: short(p), key, series: s, lo, hi, limit, box: p.box });
    tempIds.add(at.id);
  }
  // parts of a coloured part's kind that no series is tied to (a second battery)
  for (const t of temps) {
    const kind = byId.get(t.part).kind;
    for (const p of design.parts) {
      if (p.kind !== kind || tempIds.has(p.id)) continue;
      notes.push(`${p.id}・${short(p)}は温度の記録とつなげないので塗らない`);
      notesJa.push(`${short(p)}は温度の記録とつなげないので塗らない`);
    }
  }

  // the parts on the way whose loss is not recorded
  // (parts of one kind in one word: the first one's name and how many)
  const onWay = [...new Set(links.flatMap((l) => [l.from, l.to]))].filter((id) => !losses.has(id));
  const byKind = new Map();
  for (const id of onWay) {
    const k = byId.get(id).kind;
    if (!byKind.has(k)) byKind.set(k, []);
    byKind.get(k).push(id);
  }
  const names = [...byKind.values()].map((ids) => (ids.length > 1 ? `${short(byId.get(ids[0]))} など ${ids.length} 個` : short(byId.get(ids[0]))));
  for (const n of [names.length ? `${names.join("・")}の損失は記録に無いので、枝を描かない` : null, links.some((l) => !l.source) ? "細く薄い線は電力の記録が無い所" : null]) {
    if (n) notes.push(n), notesJa.push(n);
  }

  // the scale: the largest value of any line or branch over the whole run (W)
  const ref = Math.max(1e-9, ...links.filter((l) => l.source).map((l) => l.source.peak), ...branches.map((b) => b.source.peak));
  return { analysisId: thermal.analysis_id, energyId: energy?.analysis_id ?? null, version: thermal.design_version, links, branches, temps, notes, notesJa, ref, t0: Math.min(...[...own.values()].map((s) => s.t_s[0])), t1: Math.max(...[...own.values()].map((s) => s.t_s.at(-1))) };
}

const widthOf = (w, ref) => (w == null ? MIN_W : MIN_W + (MAX_W - MIN_W) * Math.sqrt(Math.min(1, Math.abs(w) / ref)));
const speedOf = (w, ref) => (w == null ? IDLE_MM_S : Math.sign(w) * RUN_MM_S * Math.sqrt(Math.min(1, Math.abs(w) / ref)));

// Record time -> each line's and branch's watts and width, each part's temperature and colour.
export function flowAt(model, tRec) {
  return {
    t: tRec,
    links: model.links.map((l) => {
      const w = l.source ? sourceW(l.source, tRec) : null;
      return { from: l.from, to: l.to, kind: l.kind, recorded: !!l.source, w, width: widthOf(w, model.ref), speed: speedOf(w, model.ref), mean: l.source?.kind === "mean" };
    }),
    branches: model.branches.map((b) => {
      const w = sourceW(b.source, tRec);
      return { part: b.part, name: b.name, w, width: widthOf(w, model.ref), speed: speedOf(w, model.ref), mean: b.source.kind === "mean", air: b.air ? sourceW(b.air, tRec) : null };
    }),
    temps: model.temps.map((p) => {
      const T = lerpAt(p.series, tRec);
      const color = tempColor(T, p.lo, p.limit?.value ?? null, p.limit?.op);
      return { part: p.part, name: p.name, T, color, over: color === VERDICT_RED, limit: p.limit?.value ?? null };
    }),
  };
}

// Replay time -> the dashes of each line and branch: [s0, s1] fractions along it, dash k of n
// at (k / n + t * speed / length) mod 1. Only t and k: the same t gives the same dashes.
export function dashesAt(model, flow, t) {
  const one = (length, speed) => {
    const n = Math.max(1, Math.round(length / DASH_GAP_MM));
    const d = Math.min(0.5, DASH_MM / length);
    const phase = (t * speed) / length;
    const out = [];
    for (let k = 0; k < n; k += 1) {
      const s = (((k / n + phase) % 1) + 1) % 1;
      out.push([s, Math.min(1, s + d)]);
    }
    return out;
  };
  return {
    links: model.links.map((l, i) => one(l.length, flow.links[i].speed)),
    branches: model.branches.map((b, i) => one(b.length, flow.branches[i].speed)),
  };
}

// the record time a replay time shows: the run plays from the analysis' arrival in HEAT_PLAY_S
export function recordTime(model, playhead, arrived) {
  const dur = model.t1 - model.t0;
  const rate = Math.max(1, dur / HEAT_PLAY_S);
  return model.t0 + Math.max(0, Math.min(dur, (playhead - arrived) * rate));
}

const hasHeat = (a) => a?.status === "ok" && ([...channelsOf(a).keys()].some((n) => n.startsWith("temp.")) || (a.results ?? []).some((r) => /^loss_.+_mean$/.test(r.quantity ?? "")));
const isThermal = (s, a) => !!a && (a.tool === "thermal" || s.tools?.[a.tool]?.domain === "thermal") && hasHeat(a);

// What the flow is drawn from on the bench: the shown analysis when it is a thermal one, else
// (force: ?on=heat) the newest thermal analysis of the design shown. null: nothing to draw.
export function heatInputs(s, shown, design, force = false) {
  let thermal = isThermal(s, shown) ? shown : null;
  if (!thermal && force && design) thermal = Object.values(s.analyses ?? {}).filter((a) => a.design_version === design.design_version && isThermal(s, a)).at(-1) ?? null;
  if (!thermal || !design) return null;
  const v = thermal.design_version;
  const energy = Object.values(s.analyses ?? {}).filter((a) => a !== thermal && a.status === "ok" && a.design_version === v && [...channelsOf(a).keys()].some((n) => n.startsWith("power."))).at(-1) ?? null;
  const verdicts = Object.values(s.verdicts ?? {}).filter((x) => x.domain === "thermal" && verdictVersion(s, x) === v);
  // the load cases in words: the test's id and, for an id several cases share, the chips' short name (T2 制御器)
  const cases = s.project?.load_cases ?? [];
  const shorts = caseShortNames(cases);
  const caseWord = (id) => {
    const c = cases.find((x) => x.load_case_id === id);
    return c ? `${c.test_id}${shorts.get(id) ? ` ${shorts.get(id)}` : ""}` : "";
  };
  return { design, thermal, energy, verdicts, arrived: s.tOf?.[thermal.analysis_id] ?? 0, loadCase: thermal.load_case_id, energyCase: energy?.load_case_id ?? null,
    caseJa: caseWord(thermal.load_case_id), energyCaseJa: energy ? caseWord(energy.load_case_id) : null };
}

// ---- on the 3D -------------------------------------------------------------------------------

const KIND_COLOR = { elec: ELEC_COLOR, rot: ROT_COLOR, heat: HEAT_COLOR };

export class EnergyFlow {
  // opts.dev: the record's ids and channel names in the legend (?dev=1)
  constructor(bench, host, opts = {}) {
    this.dev = !!opts.dev;
    this.bench = bench;
    this.host = host;
    this.group = new THREE.Group();
    this.group.renderOrder = 20;
    this.group.visible = false;
    bench.body.add(this.group);
    this.key = null;
    this.model = null;
    this.last = null; // what was drawn last (the tests read it)
    this.legend = document.createElement("div");
    this.legend.className = "heat-legend";
    this.legend.dataset.kind = "legend";
    this.legend.hidden = true;
    host.append(this.legend);
  }

  // heatInputs, worked out again only when the state, the shown analysis or the design changed
  inputs(s, shown, design, force = false) {
    const m = this.memo;
    if (!m || m.s !== s || m.rev !== s.rev || m.shown !== shown || m.design !== design || m.force !== force) this.memo = { s, rev: s.rev, shown, design, force, out: heatInputs(s, shown, design, force) };
    return this.memo.out;
  }

  // inputs: heatInputs(...) or null (nothing). playhead: the replay's time.
  show(inputs, playhead) {
    const key = inputs ? `${inputs.thermal.analysis_id}|${inputs.energy?.analysis_id ?? ""}|${inputs.design.design_version}|${inputs.verdicts.map((v) => v.verdict_id).join(",")}` : null;
    if (key !== this.key) this.build(inputs, key);
    this.group.visible = !!this.model;
    this.legend.hidden = !this.model;
    this.host.classList.toggle("heat-on", !!this.model); // (css: no buttons over it in the video layout)
    if (!this.model) {
      this.last = null;
      return false;
    }
    const tRec = recordTime(this.model, playhead, inputs.arrived);
    const flow = flowAt(this.model, tRec);
    const dashes = dashesAt(this.model, flow, playhead);
    this.place(flow, dashes);
    this.writeLegend(inputs, flow);
    this.last = { playhead, tRec, flow, dashes, ref: this.model.ref, notes: this.model.notes, analysisId: this.model.analysisId, energyId: this.model.energyId };
    return true;
  }

  build(inputs, key) {
    this.key = key;
    for (const o of [...this.group.children]) {
      o.traverse((x) => {
        x.geometry?.dispose();
        if (x.material && !x.material.userData.keep) x.material.dispose();
      });
      if (o.isCSS2DObject) o.element.remove();
      this.group.remove(o);
    }
    this.model = inputs ? flowModel(inputs) : null;
    this.items = { links: [], branches: [], boxes: [] };
    if (!this.model) return;
    const line = (color, width, opacity = 1) => {
      const m = new LineMaterial({ color, linewidth: width, transparent: true, opacity, depthTest: false, depthWrite: false });
      const o = new LineSegments2(new LineSegmentsGeometry().setPositions([0, 0, 0, 0, 0, 1]), m);
      o.renderOrder = 21;
      o.frustumCulled = false;
      this.group.add(o);
      return o;
    };
    for (const l of this.model.links) {
      // the energy's kind from the design (carries); a line without a recorded value thin and pale
      const base = line(KIND_COLOR[l.kind], MIN_W, l.source ? 0.95 : 0.5);
      base.geometry.setPositions([...l.a, ...l.b]);
      const dash = line(DASH_TINT[l.kind], MIN_W, l.source ? 1 : 0.8);
      dash.renderOrder = 22;
      this.items.links.push({ base, dash, l });
    }
    for (const b of this.model.branches) {
      const base = line(HEAT_COLOR, MIN_W, 0.95);
      base.geometry.setPositions([...b.a, ...b.b]);
      const dash = line(DASH_TINT.heat, MIN_W);
      dash.renderOrder = 22;
      const el = document.createElement("div");
      el.className = "lbl lbl-heat lbl-key lbl-out";
      el.dataset.kind = "label";
      el.dataset.part = b.part;
      const tag = new CSS2DObject(el);
      tag.position.set(...b.b);
      this.group.add(tag);
      this.items.branches.push({ base, dash, tag, b });
    }
    for (const t of this.model.temps) {
      const sz = t.box.size_mm.map((v) => v * 1.06 + 12);
      const mesh = new THREE.Mesh(new THREE.BoxGeometry(...sz), new THREE.MeshBasicMaterial({ color: "#ffffff", transparent: true, opacity: 0.62, depthWrite: false, toneMapped: false, fog: false }));
      mesh.position.set(...t.box.center_mm);
      const rpy = t.box.rpy_deg ?? [0, 0, 0];
      mesh.rotation.set((rpy[0] * Math.PI) / 180, (rpy[1] * Math.PI) / 180, (rpy[2] * Math.PI) / 180, "ZYX");
      mesh.renderOrder = 19;
      mesh.userData.part = t.part;
      this.group.add(mesh);
      this.items.boxes.push({ mesh, t });
    }
  }

  place(flow, dashes) {
    const w = this.bench.size?.w ?? 1;
    const h = this.bench.size?.h ?? 1;
    const seg = (a, b, parts) => {
      const out = [];
      for (const [s0, s1] of parts) for (const s of [s0, s1]) out.push(a[0] + (b[0] - a[0]) * s, a[1] + (b[1] - a[1]) * s, a[2] + (b[2] - a[2]) * s);
      return out.length ? out : [0, 0, 0, 0, 0, 0];
    };
    this.items.links.forEach(({ base, dash, l }, i) => {
      const f = flow.links[i];
      base.material.linewidth = f.width;
      dash.material.linewidth = Math.max(1.5, f.width * 0.55);
      dash.geometry.setPositions(seg(l.a, l.b, dashes.links[i]));
      for (const m of [base.material, dash.material]) m.resolution.set(w, h);
    });
    this.items.branches.forEach(({ base, dash, tag, b }, i) => {
      const f = flow.branches[i];
      base.material.linewidth = f.width;
      dash.material.linewidth = Math.max(1.5, f.width * 0.55);
      dash.geometry.setPositions(seg(b.a, b.b, dashes.branches[i]));
      for (const m of [base.material, dash.material]) m.resolution.set(w, h);
      const air = f.air != null ? `<small>空気へ ${esc(fmt(f.air, 3))} W</small>` : "";
      const html = `${esc(f.name)}<small>損失 ${f.mean ? "平均 " : ""}${esc(fmt(f.w, 3))} W</small>${air}`;
      if (tag.element.dataset.html !== html) {
        tag.element.dataset.html = html;
        tag.element.innerHTML = html;
      }
      tag.element.dataset.w = String(f.w);
    });
    this.items.boxes.forEach(({ mesh }, i) => {
      const c = flow.temps[i].color;
      mesh.visible = !!c;
      if (c) mesh.material.color.set(c);
    });
  }

  writeLegend(inputs, flow) {
    const m = this.model;
    const dur = m.t1 - m.t0;
    const rows = flow.temps.map((p, i) => {
      const t = m.temps[i];
      const lim = t.limit?.value ?? null;
      // the bar from the lowest recorded value to a little past the limit (or the highest value)
      const top = lim != null ? lim + (lim - t.lo) * 0.12 : t.hi;
      const at = (v) => `${(Math.max(0, Math.min(1, (v - t.lo) / (top - t.lo || 1))) * 100).toFixed(1)}%`;
      const grad = lim != null ? `linear-gradient(90deg, ${TEMP_STOPS.map((c, k) => `${c} calc(${at(lim)} * ${k / (TEMP_STOPS.length - 1)})`).join(", ")}, ${VERDICT_RED} ${at(lim)}, ${VERDICT_RED} 100%)` : `linear-gradient(90deg, ${TEMP_STOPS.join(", ")})`;
      const limTx = lim != null ? `<span class="hl-lim">上限 ${esc(fmt(lim, 3))} ℃<small>${esc(t.limit.from)}</small></span>` : `<span class="hl-lim">上限は記録に無い</span>`;
      return `<div class="hl-row" data-part="${esc(p.part)}"><span class="hl-nm"><i class="hl-sw" style="background:${p.color}"></i>${esc(p.name)}</span><span class="hl-v${p.over ? " over" : ""}">${esc(fmt(p.T, 3))} ℃</span>` +
        `<div class="hl-bar" style="background:${grad}">${lim != null ? `<i class="hl-limline" style="left:${at(lim)}"></i>` : ""}<i class="hl-now" style="left:${at(p.T)}"></i></div>${limTx}</div>`;
    });
    const link = flow.links.find((l, i) => m.links[i].source);
    const src = link ? m.links[flow.links.indexOf(link)].source : null;
    // the record's ids and channel names only with ?dev=1; else words
    const dev = this.dev;
    const from = link ? m.links[flow.links.indexOf(link)].fromName : "";
    const powerNote = !src
      ? "電力の記録が無い"
      : dev
        ? `電力 ${esc(fmt(link.w / 1000, 3))} kW: ${src.kind === "mean" ? `${esc(inputs.energyCase ?? "")} (${esc(m.energyId)}) の ${esc(src.channel)} の時間平均` : `${esc(src.channel)} (この記録)`}`
        : `${esc(from)}の電力 ${esc(fmt(link.w / 1000, 3))} kW: ${src.kind === "mean" ? `${esc(inputs.energyCaseJa ?? "")} の走行の電力の記録の平均` : "この記録の値"}`;
    const lossMean = flow.branches.some((b) => b.mean);
    const head = dev ? `${esc(inputs.loadCase ?? "")} (${esc(m.analysisId)})` : `${esc(inputs.caseJa ?? "")} の熱の解析`;
    const html =
      `<div class="hl-ttl">エネルギーの流れ<small>${head}</small></div>` +
      `<div class="hl-time">走って ${esc(fmt(flow.t - m.t0, 4))} s / ${esc(fmt(dur, 4))} s</div>` +
      `<div class="hl-keys"><span><i class="hl-ln" style="background:${ELEC_COLOR}"></i>電気</span><span><i class="hl-ln" style="background:${ROT_COLOR}"></i>回転</span><span><i class="hl-ln" style="background:${HEAT_COLOR}"></i>熱 (損失)</span></div>` +
      `<div class="hl-sub">太さ = √W (${esc(fmt(m.ref, 3))} W で ${MAX_W} px)</div>` +
      `<div class="hl-sub">箱の色 = 温度 (上限を超えたときだけ赤)</div>` +
      rows.join("") +
      `<div class="hl-notes"><div>${powerNote}</div>${lossMean ? `<div>損失: 熱の解析の平均${dev ? " (results)" : ""}</div>` : ""}${(dev ? m.notes : m.notesJa).map((n) => `<div>${esc(n)}</div>`).join("")}</div>`;
    if (this.legend.dataset.html !== html) {
      this.legend.dataset.html = html;
      this.legend.innerHTML = html;
    }
  }
}
