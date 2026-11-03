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
// and grip arrows and the centre of gravity. The popup charts one graph at a time (the
// story's limit): the battery's temperature (drive), then the speed against time
// (acceleration), with a cursor at the record time; its title is in words only (the
// tests' ids are for the makers, in data-tests). The battery's chart has the load case's
// limit as a dashed line that says "上限 <value> <unit>", and when every recorded value
// keeps to it, "✓ 上限より下" by the reading from loop_to_s (the drive has played out).
// The representative shown large is the recorded sample nearest to the cursor, under the
// title (the one emphasised number): the message is "長く走っても熱くならず、どこまで
// 速いか", so the value itself is what is read; the graph under it is the small context.
// The squeeze is in the view's corner (rateLabel, as the N05 tiles), as a note
// (data-kind "note": for the makers, not counted).
//
// The drive's record time is played from lead_s to loop_to_s along loop.ease ("in-out":
// slow at the head and the tail, 0.5 - 0.5 cos(pi p); "linear"), so the battery's colour
// and the cursor come to rest by loop_to_s; the corner says the mean squeeze. The vehicle
// stands still in the drive (its records hold no positions), so no course or arc is shown.
// "✓ 上限より下" shows from times.ok_s; through the acceleration (from accel_s, when the
// popup charts the speed) the same check stays as "✓ 電池の温度は上限より下" under the speed.
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

// ---- story (layout.json story: what a cut may show at one time) ---------------------
//
// Elements say what they are with data-kind: label (name tags, ✓ / ✕), graph (plots),
// legend (colour bars), number (emphasised figures), moving (things in motion). The page
// (cut.js) counts those shown at each time against the cut's limits, and text inside an
// element with a kind is at least limits.body_text_px; other text at least min_text_px.

export const KINDS = ["label", "graph", "legend", "number", "moving"];

// The cut's message and limits (story.defaults, overridden by story.cuts.<name>.limits).
// Without a story section there are no limits, and text to be read keeps min_text_px.
export function storyOf(layout, name) {
  const st = layout.story;
  if (st === undefined) return { message: null, limits: null, bodyPx: layout.min_text_px };
  const missing = [...KINDS, "body_text_px"].filter((k) => typeof st?.defaults?.[k] !== "number");
  if (missing.length) throw new Error(`layout.json の story.defaults に無い: ${missing.join(", ")}`);
  const own = st.cuts?.[name] ?? {};
  const limits = {};
  for (const k of [...KINDS, "body_text_px"]) limits[k] = typeof own.limits?.[k] === "number" ? own.limits[k] : st.defaults[k];
  return { message: own.message ?? null, limits, bodyPx: limits.body_text_px };
}

// The keys of the one tile shown large (feature) and of the small list (list), in a cut's
// layout section (the story: show one representative large).
export const FEATURE_KEYS = [
  "feature.x", "feature.y", "feature.w", "feature.h",
  "list.x", "list.y", "list.w", "list.h", "list.cols", "list.rows", "list.gap_x", "list.gap_y",
];

// The slots of a list {x, y, w, h, cols, rows, gap_x, gap_y}, row by row from the left:
// each (w - (cols - 1) gap_x) / cols wide and (h - (rows - 1) gap_y) / rows high.
export function listRects(L) {
  const w = (L.w - (L.cols - 1) * L.gap_x) / L.cols;
  const h = (L.h - (L.rows - 1) * L.gap_y) / L.rows;
  const out = [];
  for (let r = 0; r < L.rows; r += 1) {
    for (let c = 0; c < L.cols; c += 1) out.push({ x: Math.round(L.x + c * (w + L.gap_x)), y: Math.round(L.y + r * (h + L.gap_y)), w: Math.floor(w), h: Math.floor(h) });
  }
  return out;
}

export const N03_KEYS = [
  "duration_s",
  "view.x", "view.y", "view.w", "view.h",
  "popup.x", "popup.y", "popup.w", "popup.h", "popup.title_px", "popup.text_px", "popup.reading_of_title", "popup.ok_of_reading",
  "times.lead_s", "times.loop_to_s", "times.accel_s", "times.ok_s", "loop.ease",
  "colors.bg", "colors.panel", "colors.rule",
];

// The eases loop.ease may name: the share of the drive played at the share p of its time.
export const LOOP_EASES = { "in-out": (p) => 0.5 - 0.5 * Math.cos(Math.PI * p), linear: (p) => p };

// Not in layout.json (written here until the promo department gives keys for them): the
// view's and the popup's border and the popup's corner, the padding inside the popup and
// around the rate (a fraction of popup.text_px), and the rate's text size (popup.text_px).
// The reading's largest size is layout.json n03.popup.reading_of_title x title_px (it is
// also held to the popup's width); "✓ 上限より下" is popup.ok_of_reading x the reading's size.
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
const OK_GREEN = "#2f7d4f"; // the pages' colour of a pass (as .ok in cut.html)

// Round steps for an axis from lo to hi: about four, of 1, 2 or 5 times a power of ten.
function niceTicks(lo, hi) {
  const raw = (hi - lo) / 4;
  if (!(raw > 0)) return [lo];
  const p = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((k) => k * p).find((s) => s >= raw);
  const out = [];
  for (let v = Math.ceil(lo / step - 1e-9) * step; v <= hi + 1e-9; v += step) out.push(Number(v.toFixed(9)));
  return out;
}

// A chart of one channel against time (SVG in its own pixels): its name and unit at the
// top left, the recorded value nearest to the cursor at the top right, a dashed line at
// `limit` when given. The axis runs from `floor` (the case's surroundings, e.g. the
// ambient temperature, else 0) or the lowest value if lower, to the limit or the highest
// value, with round steps labelled at the left (light lines), so the room left under the
// limit reads as a number. The cursor is gray (red is the colour of a limit).
// Returns the SVG text and the functions that move the cursor.
function chartSvg(s, w, h, textPx, name, limit, floor = 0) {
  const [t0, t1] = extent(s.t_s);
  let [lo, hi] = extent(s.values);
  if (Number.isFinite(floor)) lo = Math.min(lo, floor);
  if (limit !== null) [lo, hi] = [Math.min(lo, limit), Math.max(hi, limit)];
  const top = textPx * 2 + 8; // under the title, and room for the limit's words above its line
  const bottom = h - 4;
  const ticks = niceTicks(lo, hi);
  const tickW = Math.ceil(Math.max(...ticks.map((v) => fmt(v, 3).length)) * textPx * 0.62) + 8;
  const X = (t) => tickW + ((t - t0) / (t1 - t0 || 1)) * (w - tickW);
  const Y = (v) => bottom - ((v - lo) / (hi - lo || 1)) * (bottom - top);
  const grid = ticks
    .map((v) => `<line x1="${tickW}" x2="${w}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" stroke="#e2ddd2" stroke-width="1"/>` +
      `<text class="tick" x="${tickW - 6}" y="${(Y(v) + textPx * 0.35).toFixed(1)}" text-anchor="end" font-size="${textPx}" fill="#5c5a54">${esc(fmt(v, 3))}</text>`)
    .join("");
  const step = Math.max(1, Math.ceil(s.t_s.length / 300));
  const pts = [];
  for (let k = 0; k < s.t_s.length; k += step) pts.push(`${X(s.t_s[k]).toFixed(1)},${Y(s.values[k]).toFixed(1)}`);
  const unit = unitLabel(s.unit) || "1";
  // the limit's line says what it is and its value (the load case's criterion), at its
  // right end, above the line when there is room under the title, else below it
  const ly = limit !== null ? Y(limit) : 0;
  const above = ly - 6 - textPx >= textPx + 4; // clear of the title
  const svg =
    `<text x="0" y="${textPx}" font-size="${textPx}" fill="#5c5a54">${esc(name)} [${esc(unit)}]</text>` +
    grid +
    (limit !== null
      ? `<line x1="${tickW}" x2="${w}" y1="${ly.toFixed(1)}" y2="${ly.toFixed(1)}" stroke="${RED}" stroke-width="1.5" stroke-dasharray="6 4"/>` +
        `<text class="limit" x="${w}" y="${(above ? ly - 6 : ly + 6 + textPx).toFixed(1)}" text-anchor="end" font-size="${textPx}" fill="${RED}">${esc(`上限 ${fmt(limit, 3)} ${unitLabel(s.unit)}`.trim())}</text>`
      : "") +
    `<polyline points="${pts.join(" ")}" fill="none" stroke="${LINE}" stroke-width="2.2"/>` +
    `<line class="cur" x1="0" x2="0" y1="${top}" y2="${bottom}" stroke="#8a857a" stroke-width="1.5"/>`;
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
    this.ease = LOOP_EASES[this.spec.loop.ease];
    if (!this.ease) throw new Error(`layout.json の n03 の loop.ease が分からない: ${this.spec.loop.ease} (${Object.keys(LOOP_EASES).join(" か ")})`);
    this.layout = layout;
    this.story = storyOf(layout, "n03");
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
    // the popup's title and the graph's words are read: at least the story's body text size
    const titlePx = Math.max(P.title_px, this.story.bodyPx);
    const textPx = Math.max(P.text_px, this.story.bodyPx);
    const pad = Math.round(P.text_px * S.pad_of_text);
    const border = `${S.border_px}px solid ${sp.colors.rule}`;
    this.inner = { w: V.w - 2 * S.border_px, h: V.h - 2 * S.border_px };

    const box = document.createElement("div");
    box.className = "n03-view";
    box.dataset.box = "n03.view";
    Object.assign(box.style, { left: `${V.x}px`, top: `${V.y}px`, width: `${V.w}px`, height: `${V.h}px`, background: sp.colors.panel, border });
    box.innerHTML =
      `<canvas data-kind="moving"></canvas>` +
      // the squeeze of the record's time is a note for the makers, not for the viewers
      `<span class="rate" data-box="n03.view.rate" data-kind="note" style="right:${pad}px;bottom:${pad}px;font-size:${Math.round(P.text_px * S.rate_of_text)}px"></span>`;
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
    const page = (key, title, charts, also = null) => {
      const el = document.createElement("div");
      el.className = "n03-page";
      el.dataset.page = key;
      const list = charts.filter((c) => c.s);
      const tests = [...new Set(list.map((c) => c.test))];
      const h3 = document.createElement("h3");
      h3.dataset.box = "n03.popup.title";
      h3.dataset.kind = "label";
      h3.dataset.tests = tests.join(" ");
      Object.assign(h3.style, { fontSize: `${titlePx}px`, marginBottom: `${pad}px` });
      // the viewers do not know the tests' ids: the title only (the ids stay in data-tests)
      h3.textContent = title;
      el.append(h3);
      pop.append(el);
      if (!list.length) {
        el.insertAdjacentHTML("beforeend", `<p class="none" style="font-size:${P.text_px}px">記録がまだ無い (${esc(charts.map((c) => c.test).join("・"))})</p>`);
        return { el, charts: [] };
      }
      // the reading, large: as large as the widest value the record holds fits the popup's width
      const unitOf = (c) => unitLabel(c.s.unit) || "";
      const readings = list.map((c) => {
        const r = document.createElement("div");
        r.className = "now n03-reading";
        r.dataset.kind = "number";
        r.dataset.box = `n03.popup.${c.key}.reading`;
        const [lo, hi] = extent(c.s.values);
        r.textContent = [lo, hi].map((v) => `${fmt(v, 3)} ${unitOf(c)}`).sort((a, b) => b.length - a.length)[0];
        el.append(r);
        let px = Math.round(P.title_px * P.reading_of_title);
        r.style.fontSize = `${px}px`;
        while (r.scrollWidth > chartW && px > this.story.bodyPx) {
          px -= 1;
          r.style.fontSize = `${px}px`;
        }
        r.style.marginBottom = `${pad}px`;
        r.dataset.px = String(px);
        return r;
      });
      // a chart with a limit: whether every recorded value keeps to it (the criterion's op),
      // said with "✓ 上限より下" on its own line under the reading, about half the
      // reading's size (it is shown for under a second: loop_to_s .. accel_s), from
      // loop_to_s (the drive has played out); its room is kept while it is hidden
      const keeps = (c) => {
        if (c.limit === null || c.limit === undefined) return null;
        const test = { "<=": (v) => v <= c.limit, "<": (v) => v < c.limit }[c.op];
        return test ? c.s.values.every(test) : null;
      };
      const oks = list.map((c, i) => {
        if (!keeps(c)) return null;
        const ok = document.createElement("div");
        ok.className = "n03-ok";
        ok.dataset.box = `n03.popup.${c.key}.ok`;
        ok.dataset.kind = "label";
        const px = Math.max(textPx, Math.round(Number(readings[i].dataset.px) * P.ok_of_reading));
        Object.assign(ok.style, { fontSize: `${px}px`, color: OK_GREEN, visibility: "hidden", fontWeight: "700", lineHeight: "1.1", marginBottom: `${pad}px` });
        ok.textContent = "✓ 上限より下";
        readings[i].after(ok);
        return ok;
      });
      // a check carried over from another page (the battery's, through the acceleration)
      if (also && readings[0]) {
        const ok = document.createElement("div");
        ok.className = "n03-ok";
        ok.dataset.box = `n03.popup.${key}.${also.key}`;
        ok.dataset.kind = "label";
        let px = Math.max(textPx, Math.round(Number(readings[0].dataset.px) * P.ok_of_reading));
        Object.assign(ok.style, { fontSize: `${px}px`, color: OK_GREEN, visibility: "hidden", fontWeight: "700", lineHeight: "1.1", marginBottom: `${pad}px`, whiteSpace: "nowrap" });
        ok.textContent = also.text;
        readings[0].after(ok);
        while (ok.scrollWidth > chartW && px > textPx) {
          px -= 1;
          ok.style.fontSize = `${px}px`;
        }
        oks[0] = oks[0] ?? null;
        oks.push(ok);
        this.carried = ok;
      }
      const room = P.h - 2 * pad - 2 * S.border_px - h3.offsetHeight - pad - readings.reduce((a, r) => a + r.offsetHeight + pad, 0) - oks.reduce((a, o) => a + (o ? o.offsetHeight + pad : 0), 0);
      const h = Math.floor((room - (list.length - 1) * pad) / list.length);
      return {
        el,
        charts: list.map((c, i) => {
          const { svg, X } = chartSvg(c.s, chartW, h, textPx, c.name, c.limit ?? null, c.floor ?? 0);
          el.insertAdjacentHTML("beforeend", `<svg class="chart" data-box="n03.popup.${c.key}" data-kind="graph" width="${chartW}" height="${h}" viewBox="0 0 ${chartW} ${h}" style="margin-top:${i ? pad : 0}px">${svg}</svg>`);
          const node = el.lastElementChild;
          return { ...c, X, cur: node.querySelector(".cur"), now: readings[i], unit: unitOf(c), ok: oks[i] };
        }),
      };
    };
    const heatCh = this.heat ? channelsOf(this.heat.ana) : new Map();
    const launchCh = this.launch ? channelsOf(this.launch.ana) : new Map();
    this.battery = this.batteryChannel(heatCh, design);
    // one graph at a time (story): the battery's temperature on the long drive, then the speed
    const drive = page("drive", "長く走ったときの電池の温度", [
      { key: "temp", name: "電池の温度", s: this.battery?.s, test: "T1", limit: this.battery?.limit ?? null, op: this.heat?.lc.criterion?.op ?? null,
        floor: this.ambient(this.battery?.s?.unit) },
    ]);
    const kept = drive.charts.some((c) => c.ok);
    this.carried = null;
    this.pages = {
      drive,
      launch: page("launch", "加速の速さ", [{ key: "speed", name: "速さ", s: launchCh.get("speed"), test: this.launch?.lc.test_id ?? "P3・P2" }],
        kept ? { key: "battery_ok", text: "✓ 電池の温度は上限より下" } : null),
    };

    // the record time spans the two phases play
    const spans = [this.drive, this.heat].filter(Boolean).flatMap((r) => [...channelsOf(r.ana).values()].flatMap((c) => [c.t_s[0], c.t_s.at(-1)]));
    this.driveSpan = spans.length ? [Math.min(...spans), Math.max(...spans)] : null;
    this.launchSpan = this.launch ? [this.launchView.motion.t0, this.launchView.motion.t1] : null;
  }

  // The surroundings' temperature of the thermal load case (its params.ambient_temperature,
  // in the channel's unit), else 0: the bottom of the battery chart's axis.
  ambient(unit) {
    const a = this.heat?.lc.params?.ambient_temperature;
    return a && a.unit === unit && Number.isFinite(a.value) ? a.value : 0;
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
      // eased (loop.ease) from lead_s to loop_to_s; the corner says the mean squeeze
      const [a, b] = this.driveSpan;
      const share = Math.max(0, Math.min(1, (t - T.lead_s) / Math.max(1e-9, T.loop_to_s - T.lead_s)));
      tau = a + this.ease(share) * (b - a);
      rate = (b - a) / Math.max(1e-9, T.loop_to_s - T.lead_s);
    } else if (phase === "launch" && this.launchSpan) {
      ({ tau, rate } = squeeze(t, T.accel_s, this.spec.duration_s, this.launchSpan[0], this.launchSpan[1]));
    }
    this.rateEl.textContent = rateLabel(rate);
    this.box.dataset.tau = tau === null ? "" : String(tau);
    for (const c of this.pages[phase].charts) {
      if (tau === null) continue;
      c.cur.setAttribute("transform", `translate(${c.X(tau).toFixed(1)} 0)`);
      c.now.firstChild.nodeValue = `${fmt(nearestAt(c.s, tau), 3)} ${c.unit}`.trim();
    }
    // the ✓ from ok_s when the long drive kept to the limit (on the drive's page only)
    for (const c of this.pages.drive.charts) if (c.ok) c.ok.style.visibility = phase === "drive" && t >= T.ok_s ? "visible" : "hidden";
    // and through the acceleration, under the speed
    if (this.carried) this.carried.style.visibility = phase === "launch" && t >= T.ok_s ? "visible" : "hidden";
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
