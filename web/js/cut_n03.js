// N03 (cut.html?cut=n03&run=<id>): the long drive and the launch, in 3D.
//
// From times.lead_s to times.loop_to_s the drive-pattern records play, squeezed: the
// battery's box is coloured by the thermal record's battery temperature (T1, temp.<part>)
// at the record time, on a band from the first recorded temperature to the load case's
// upper limit (else the highest recorded one). The vehicle stands on the plain ground:
// the drive_energy (P4) and thermal (T1) records hold no positions, so no course is drawn
// and the vehicle does not move; its centre of gravity is marked. From times.accel_s to
// the end the acceleration record plays (?accel=<load case id>, else the straight run on the
// flat, P3, else the launch on the slope, P2): its motion with each wheel's road reaction
// and grip arrows and the centre of gravity. The popup charts the battery's charge and
// temperature (drive), then the speed against time (acceleration), with a cursor at the
// record time and the recorded sample nearest to it; its title names the tests charted.
// The squeeze is in the view's corner (rateLabel, as the N05 tiles).
//
// Positions, sizes, times and colours come only from layout.json n03 (N03_KEYS); a
// missing key is named on the page.

import * as THREE from "three";
import { clearOfBand, rateLabel, squeeze } from "./compose.js";
import { contourOf } from "./fields.js";
import { esc, fmt, unitLabel } from "./labels.js";
import { channelsOf, extent, motionOf, nearestAt, nearestIndex } from "./series.js";
import { VIEWS, Workbench3D } from "./workbench3d.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);

// ---- layout keys (the one place a cut's keys are read) ---------------------------

export const at = (obj, path) => path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);

const TOP_KEYS = ["min_text_px", "stage.w", "stage.h"];

// Throws one message naming every missing key: "layout.json の <name> に無い: a.b, c" and
// "layout.json に無い: <top keys>".
export function requireKeys(layout, name, keys) {
  const missing = keys.filter((k) => at(layout[name], k) === undefined);
  const top = TOP_KEYS.filter((k) => at(layout, k) === undefined);
  const why = [missing.length && `layout.json の ${name} に無い: ${missing.join(", ")}`, top.length && `layout.json に無い: ${top.join(", ")}`].filter(Boolean);
  if (why.length) throw new Error(why.join(" / "));
  return layout[name];
}

export const N03_KEYS = [
  "duration_s",
  "view.x", "view.y", "view.w", "view.h",
  "popup.x", "popup.y", "popup.w", "popup.h", "popup.title_px", "popup.text_px",
  "times.lead_s", "times.loop_to_s", "times.accel_s",
  "colors.bg", "colors.panel", "colors.rule",
];

// Not in layout.json (written here until the promo department gives keys for them): the
// view's and the popup's border and the popup's corner, the padding inside the popup and
// around the rate (a fraction of popup.text_px), and the rate's text size (popup.text_px).
export const N03_STYLE = { border_px: 1, popup_radius_px: 8, pad_of_text: 0.5, rate_of_text: 1 };

// ---- records ----------------------------------------------------------------------

const BEYOND = /-beyond$/;

// The newest ok analysis of a test's load cases (not those past the limit), in the
// project's order, that passes `pick`; or of the load case `lcId` when given.
export function newestOf(s, { test, lcId }, pick) {
  const cases = (s.project?.load_cases ?? []).filter((c) => (lcId ? c.load_case_id === lcId : c.test_id === test && !BEYOND.test(c.load_case_id)));
  for (const lc of cases) {
    const ana = s.order.analyses.map((id) => s.analyses[id]).filter((a) => a.load_case_id === lc.load_case_id && a.status === "ok" && pick(a)).at(-1);
    if (ana) return { lc, ana };
  }
  return null;
}

const LINE = "#2b59c3";
const RED = "#c8402a";

// A chart of one channel against time (SVG in its own pixels): its name and unit at the
// top left, the recorded value nearest to the cursor at the top right, a dashed line at
// `limit` when given. Returns the SVG text and the functions that move the cursor.
function chartSvg(s, w, h, textPx, name, limit) {
  const [t0, t1] = extent(s.t_s);
  let [lo, hi] = extent(s.values);
  if (limit !== null) [lo, hi] = [Math.min(lo, limit), Math.max(hi, limit)];
  const top = textPx + 8;
  const bottom = h - 4;
  const X = (t) => ((t - t0) / (t1 - t0 || 1)) * w;
  const Y = (v) => bottom - ((v - lo) / (hi - lo || 1)) * (bottom - top);
  const step = Math.max(1, Math.ceil(s.t_s.length / 300));
  const pts = [];
  for (let k = 0; k < s.t_s.length; k += step) pts.push(`${X(s.t_s[k]).toFixed(1)},${Y(s.values[k]).toFixed(1)}`);
  const unit = unitLabel(s.unit) || "1";
  const svg =
    `<text x="0" y="${textPx}" font-size="${textPx}" fill="#5c5a54">${esc(name)} [${esc(unit)}]</text>` +
    `<text class="now" x="${w}" y="${textPx}" font-size="${textPx}" fill="#1b1a17" text-anchor="end"></text>` +
    (limit !== null ? `<line x1="0" x2="${w}" y1="${Y(limit).toFixed(1)}" y2="${Y(limit).toFixed(1)}" stroke="${RED}" stroke-width="1.5" stroke-dasharray="6 4"/>` : "") +
    `<polyline points="${pts.join(" ")}" fill="none" stroke="${LINE}" stroke-width="2.2"/>` +
    `<line class="cur" x1="0" x2="0" y1="${top}" y2="${bottom}" stroke="${RED}" stroke-width="1.5"/>`;
  return { svg, X };
}

// An upper limit of a criterion in a unit (the load case's own), else null.
function upperLimit(criterion, unit) {
  const lim = criterion?.limit;
  return lim && lim.unit === unit && Number.isFinite(lim.value) && (criterion.op === "<=" || criterion.op === "<") ? lim.value : null;
}

export class CutN03 {
  constructor(layout, s) {
    this.spec = requireKeys(layout, "n03", N03_KEYS);
    this.layout = layout;
    this.s = s;
    this.stage = $("stage");
    this.stage.style.background = this.spec.colors.bg;
    this.scale = 1;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(1);
    const has = (...names) => (a) => names.every((n) => channelsOf(a).has(n));
    this.drive = newestOf(s, { test: "P4" }, has("soc"));
    this.heat = newestOf(s, { test: "T1" }, (a) => [...channelsOf(a).keys()].some((n) => n.startsWith("temp.")));
    // the straight run on the flat (P3) when it was recorded, else the launch on the slope (P2)
    const accel = params.get("accel");
    const moving = (a) => !!motionOf(a);
    this.launch = accel ? newestOf(s, { lcId: accel }, moving) : newestOf(s, { test: "P3" }, moving) ?? newestOf(s, { test: "P2" }, moving);
    this.build();
  }

  build() {
    const sp = this.spec;
    const V = sp.view;
    const P = sp.popup;
    const S = N03_STYLE;
    const pad = Math.round(P.text_px * S.pad_of_text);
    const border = `${S.border_px}px solid ${sp.colors.rule}`;
    this.inner = { w: V.w - 2 * S.border_px, h: V.h - 2 * S.border_px };

    const box = document.createElement("div");
    box.className = "n03-view";
    box.dataset.box = "n03.view";
    Object.assign(box.style, { left: `${V.x}px`, top: `${V.y}px`, width: `${V.w}px`, height: `${V.h}px`, background: sp.colors.panel, border });
    box.innerHTML =
      `<canvas></canvas>` +
      `<span class="rate" data-box="n03.view.rate" style="right:${pad}px;bottom:${pad}px;font-size:${Math.round(P.text_px * S.rate_of_text)}px"></span>`;
    if (!clearOfBand({ x: V.x, y: V.y, w: V.w, h: V.h }, this.layout)) box.dataset.outside = "1";
    this.stage.append(box);
    this.box = box;
    this.canvas = box.querySelector("canvas");
    this.rateEl = box.querySelector(".rate");

    const design = this.s.designs[this.s.currentVersion];
    // the long drive: the design at rest, the battery coloured by its temperature
    this.driveView = this.makeDriveView(design);
    // the launch: its motion, arrows and centre of gravity
    this.launchView = this.launch ? this.makeLaunchView() : null;

    // the popup over the view
    const pop = document.createElement("div");
    pop.className = "n03-popup";
    pop.dataset.box = "n03.popup";
    Object.assign(pop.style, { left: `${P.x}px`, top: `${P.y}px`, width: `${P.w}px`, height: `${P.h}px`, background: sp.colors.panel, border, borderRadius: `${S.popup_radius_px}px`, padding: `${pad}px` });
    if (!clearOfBand({ x: P.x, y: P.y, w: P.w, h: P.h }, this.layout)) pop.dataset.outside = "1";
    this.stage.append(pop);
    this.popup = pop;
    const chartW = P.w - 2 * pad - 2 * S.border_px;
    // a page of the popup: its title with the tests whose records it charts, then the charts
    const page = (key, title, charts) => {
      const el = document.createElement("div");
      el.className = "n03-page";
      el.dataset.page = key;
      const list = charts.filter((c) => c.s);
      const tests = [...new Set(list.map((c) => c.test))];
      const h3 = document.createElement("h3");
      h3.dataset.box = "n03.popup.title";
      h3.dataset.tests = tests.join(" ");
      Object.assign(h3.style, { fontSize: `${P.title_px}px`, marginBottom: `${pad}px` });
      h3.textContent = tests.length ? `${title} (${tests.join("・")})` : title;
      el.append(h3);
      pop.append(el);
      if (!list.length) {
        el.insertAdjacentHTML("beforeend", `<p class="none" style="font-size:${P.text_px}px">記録がまだ無い (${esc(charts.map((c) => c.test).join("・"))})</p>`);
        return { el, charts: [] };
      }
      const room = P.h - 2 * pad - 2 * S.border_px - h3.offsetHeight - pad;
      const h = Math.floor((room - (list.length - 1) * pad) / list.length);
      return {
        el,
        charts: list.map((c, i) => {
          const { svg, X } = chartSvg(c.s, chartW, h, P.text_px, c.name, c.limit ?? null);
          el.insertAdjacentHTML("beforeend", `<svg class="chart" data-box="n03.popup.${c.key}" width="${chartW}" height="${h}" viewBox="0 0 ${chartW} ${h}" style="margin-top:${i ? pad : 0}px">${svg}</svg>`);
          const node = el.lastElementChild;
          return { ...c, X, cur: node.querySelector(".cur"), now: node.querySelector(".now") };
        }),
      };
    };
    const driveCh = this.drive ? channelsOf(this.drive.ana) : new Map();
    const heatCh = this.heat ? channelsOf(this.heat.ana) : new Map();
    const launchCh = this.launch ? channelsOf(this.launch.ana) : new Map();
    this.battery = this.batteryChannel(heatCh, design);
    this.pages = {
      drive: page("drive", "電池の残量と温度", [
        { key: "soc", name: "電池の残量", s: driveCh.get("soc"), test: "P4" },
        { key: "temp", name: "電池の温度", s: this.battery?.s, test: "T1", limit: this.battery?.limit ?? null },
      ]),
      launch: page("launch", "加速の速さと時間", [{ key: "speed", name: "速さ", s: launchCh.get("speed"), test: this.launch?.lc.test_id ?? "P3・P2" }]),
    };

    // the record time spans the two phases play
    const spans = [this.drive, this.heat].filter(Boolean).flatMap((r) => [...channelsOf(r.ana).values()].flatMap((c) => [c.t_s[0], c.t_s.at(-1)]));
    this.driveSpan = spans.length ? [Math.min(...spans), Math.max(...spans)] : null;
    this.launchSpan = this.launch ? [this.launchView.motion.t0, this.launchView.motion.t1] : null;
  }

  // The thermal record's channel of the battery (temp.<id> of a battery part of the design),
  // with the band it is coloured on: from its first value to the load case's upper limit in
  // its unit, else its highest value.
  batteryChannel(ch, design) {
    const ids = (design?.parts ?? []).filter((p) => p.kind === "battery" || p.id === "battery").map((p) => p.id);
    const id = ids.find((x) => ch.has(`temp.${x}`));
    if (!id) return null;
    const s = ch.get(`temp.${id}`);
    const limit = upperLimit(this.heat.lc.criterion, s.unit);
    const [lo, hi] = extent(s.values);
    const max = limit !== null && limit > lo ? limit : hi;
    return { id, s, limit, min: Math.min(s.values[0], lo), max };
  }

  makeDriveView(design) {
    const v = new Workbench3D(null, "zu", { renderer: this.renderer });
    v.setDesign(design);
    v.setLoading((this.drive ?? this.heat)?.lc.params?.loading ?? null);
    v.setScene(null, null);
    v.setToggles({ parts: true, contour: true, arrows: false, cg: true, names: false, limit: false });
    v.resize(this.inner.w, this.inner.h);
    v.fitPoints(v.contentPoints(), VIEWS.default, 1.15);
    this.contours = new Map();
    return v;
  }

  makeLaunchView() {
    const { lc, ana } = this.launch;
    const v = new Workbench3D(null, "zu", { renderer: this.renderer });
    v.setDesign(this.s.designs[ana.design_version] ?? this.s.designs[this.s.currentVersion]);
    v.setLoading(lc.params?.loading ?? null);
    const motion = motionOf(ana);
    v.setMotion(motion);
    v.setScene(lc, ana);
    v.setContour(null);
    v.setToggles({ parts: true, contour: false, arrows: true, cg: true, names: false, limit: false });
    v.resize(this.inner.w, this.inner.h);
    v.setFrame(motion.frame(motion.t0));
    v.fitPoints(v.contentPoints(), VIEWS.default, 1.3);
    return v;
  }

  // The battery's colour at a record time: a contour of the battery part alone (the
  // recorded sample nearest to the time), on the band of batteryChannel.
  batteryContour(tau) {
    const b = this.battery;
    if (!b) return null;
    const i = nearestIndex(b.s, tau);
    if (!this.contours.has(i)) {
      const field = { kind: "temperature", label: "battery", unit: b.s.unit, per_part: { [b.id]: b.s.values[i] } };
      const ct = contourOf({ ...this.heat.ana, analysis_id: `${this.heat.ana.analysis_id}@${i}` }, field, this.heat.lc.criterion);
      this.contours.set(i, ct ? { ...ct, min: b.min, max: b.max } : null);
    }
    return this.contours.get(i);
  }

  resize(scale) {
    this.scale = scale;
    this.renderer.setSize(Math.ceil(this.inner.w * scale), Math.ceil(this.inner.h * scale), false);
    this.canvas.width = Math.round(this.inner.w * scale);
    this.canvas.height = Math.round(this.inner.h * scale);
  }

  draw(t) {
    const T = this.spec.times;
    const launch = t >= T.accel_s && !!this.launchView;
    const phase = t >= T.accel_s ? "launch" : "drive";
    document.documentElement.dataset.phase = phase;
    for (const [k, p] of Object.entries(this.pages)) p.el.style.display = k === phase ? "" : "none";
    let tau = null;
    let rate = 0;
    if (phase === "drive" && this.driveSpan) {
      ({ tau, rate } = squeeze(t, T.lead_s, T.loop_to_s, this.driveSpan[0], this.driveSpan[1]));
    } else if (phase === "launch" && this.launchSpan) {
      ({ tau, rate } = squeeze(t, T.accel_s, this.spec.duration_s, this.launchSpan[0], this.launchSpan[1]));
    }
    this.rateEl.textContent = rateLabel(rate);
    this.box.dataset.tau = tau === null ? "" : String(tau);
    for (const c of this.pages[phase].charts) {
      if (tau === null) continue;
      c.cur.setAttribute("transform", `translate(${c.X(tau).toFixed(1)} 0)`);
      c.now.textContent = fmt(nearestAt(c.s, tau), 3);
    }
    let view;
    if (launch) {
      view = this.launchView;
      const m = view.motion;
      view.setFrame(m.frame(Math.min(m.t1, tau ?? m.t0)));
    } else {
      view = this.driveView;
      view.setContour(tau === null ? null : this.batteryContour(tau));
    }
    this.box.dataset.view = launch ? "launch" : "drive";
    view.renderInto(this.canvas.getContext("2d"), this.canvas.width, this.canvas.height, this.scale);
  }
}
