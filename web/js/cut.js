// Cut pages for the video (cut.html?cut=n05&run=<id>): HIRAKU's own drawing of a
// run's records, laid out by the promo department's rules (compose.js reads
// /layout.json; no position is written here). Time comes only from ?t= or
// window.hiraku.seek(t), so the frame at t is always the same; the exporter reads
// window.hiraku.player.duration. Elements carry data-box names for the layout
// checks (promo/layout_check.py).
//
// N05: the vehicle in the real look fills the screen, then bursts open into the
// grid of preset tests; each tile plays its test's record, squeezed to fit, with
// the squeeze in its corner. ?hero=0 leaves the vehicle out; ?only=hero draws it
// alone on a clear ground, and window.hiraku.png() returns it as a PNG.

import * as THREE from "three";
import { BAND_COLORS, OVER_COLOR } from "./colormap.js";
import { contourOf, fieldsOf } from "./fields.js";
import { clearOfBand, cubicBezier, flyIn, gridRects, loadLayout, outwardOrder, rateLabel, squeeze } from "./compose.js";
import { CutN04, CutN15 } from "./cut_grid.js";
import { esc, fmt, quantityName, resultText, unitLabel } from "./labels.js";
import { abscissa, channelsOf, extent, lerpAt, motionOf, nearestAt, xSeriesOf } from "./series.js";
import { applyEvent, initialState } from "./state.js";
import { VIEWS, Workbench3D } from "./workbench3d.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);

// What each preset tile shows (keys as in layout.json n05.tests): the test whose
// record it plays, the words on the tile (what is shown, not more), the field drawn
// as a contour and the channel drawn as a small chart.
const TILES = {
  step: { test: "P1", name: "段差越え" },
  tilt: { test: "S1", name: "傾斜台" },
  wind: { test: "F1", name: "横風" },
  rough: { test: "S5", name: "オフロード走行" },
  dlc: { test: "S3", name: "DLC" },
  brake: { test: "S4", name: "急ブレーキ" },
  long: { test: "P4", name: "長時間・電池の残量", chart: "soc" },
  launch: { test: "P2", name: "発進" },
  crash: { test: "C1", name: "衝突・応力と減速度", field: "stress", chart: "deceleration" },
  nvh: { test: "N1", name: "音振・フレームのモード", field: "mode_shape" },
  fatigue: { test: "R3", name: "疲労・疲労度", field: "damage" },
  heat: { test: "T1", name: "熱・部品の温度", field: "temperature" },
};

async function readRun(runId) {
  const events = [];
  let offset = 0;
  for (;;) {
    const res = await fetch(`/api/runs/${encodeURIComponent(runId)}/events?offset=${offset}`, { cache: "no-store" });
    const body = await res.json();
    if (!res.ok) throw new Error(body.error ?? res.status);
    events.push(...body.events);
    if (body.next_offset >= body.size || body.next_offset === offset) return events;
    offset = body.next_offset;
  }
}

function stateOf(events) {
  let s = initialState();
  for (const ev of [...events].sort((a, b) => a.seq - b.seq)) s = applyEvent(s, ev);
  return s;
}

// The newest ok analysis of the first load case (in the project's order) of a test.
function recordOf(s, testId) {
  for (const lc of s.project?.load_cases ?? []) {
    if (lc.test_id !== testId) continue;
    const ana = s.order.analyses.map((id) => s.analyses[id]).filter((a) => a.load_case_id === lc.load_case_id && a.status === "ok").at(-1);
    if (ana) return { lc, ana };
  }
  return null;
}

// The field a tile colours by: its kind, and for mode shapes a frame mode when there is one.
function fieldFor(ana, kind) {
  const fields = fieldsOf(ana, { mesh: false }).filter((f) => f.kind === kind);
  if (kind === "mode_shape") return fields.find((f) => /frame|フレーム/i.test(f.label ?? "")) ?? fields[0] ?? null;
  return fields[0] ?? null;
}

// A small chart of one channel (SVG in the tile's own pixels): against time with a
// cursor, or against another quantity (x) with that quantity's name and unit in
// its corner at textPx.
function chartSvg(s, w, h, textPx) {
  const a = abscissa(s);
  const [t0, t1] = extent(a);
  const [lo, hi] = extent(s.values);
  const X = (t) => ((t - t0) / (t1 - t0 || 1)) * w;
  const Y = (v) => h - 4 - ((v - lo) / (hi - lo || 1)) * (h - 8);
  const pts = a.map((t, i) => `${X(t).toFixed(1)},${Y(s.values[i]).toFixed(1)}`).join(" ");
  const line = `<polyline points="${pts}" fill="none" stroke="#2b59c3" stroke-width="2.2"/>`;
  if (s.x) {
    const name = `${quantityName(s.x.quantity)} [${unitLabel(s.x.unit) || "1"}]`;
    return { svg: `${line}<text x="4" y="${h - 4}" font-size="${textPx}" fill="#5c5a54">${esc(name)}</text>`, X: null };
  }
  return { svg: `${line}<line class="cur" x1="0" x2="0" y1="0" y2="${h}" stroke="#c8402a" stroke-width="1.5"/>`, X };
}

// Every value the N05 cut reads from the layout rules; a missing one is named, not guessed.
const N05_KEYS = ["duration_s", "grid", "tile", "hero", "hero_motion", "times", "ease", "fly_from", "tests"];

// ?only=hero: the big vehicle alone, at rest in its box on a clear ground (window.hiraku.png()
// gives it as one PNG); ?hero=0: the cut without it (the promo page lays its own over the tiles).
const ONLY_HERO = params.get("only") === "hero";
const WITH_HERO = ONLY_HERO || params.get("hero") !== "0";

class Cut {
  constructor(layout, s) {
    const missing = N05_KEYS.filter((k) => layout.n05?.[k] === undefined);
    if (missing.length) throw new Error(`layout.json の n05 に無い: ${missing.join(", ")}`);
    this.layout = layout;
    this.s = s;
    this.spec = layout.n05;
    this.stage = $("stage");
    this.stage.style.background = layout.colors?.night ?? "#131311";
    if (ONLY_HERO) for (const el of [document.documentElement, document.body, this.stage]) el.style.background = "transparent";
    this.scale = 1;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(1);
    this.views = [];
    this.build();
  }

  build() {
    const sp = this.spec;
    const design = this.s.designs[this.s.currentVersion];
    // hero: the vehicle in the real look over the stage's own background
    this.hero = WITH_HERO ? this.makeHero(design) : null;
    if (ONLY_HERO) {
      this.tiles = [];
      return;
    }

    // tiles: the grid from the rules, opening outward from fly_from
    const rects = gridRects(sp.grid);
    const order = outwardOrder(rects, sp.fly_from);
    const ease = cubicBezier(...sp.ease.tile);
    const T = sp.times;
    this.tiles = sp.tests.map((key, i) => {
      const r = rects[i];
      const def = TILES[key] ?? { name: key };
      const el = document.createElement("div");
      el.className = "tile";
      el.dataset.box = `n05.tile.${key}`;
      const st = sp.tile;
      Object.assign(el.style, {
        left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px`,
        background: this.layout.colors?.tile_bg ?? "#f7f4ec",
        border: `${st.border_px}px solid ${this.layout.colors?.tile_border ?? "#3b3934"}`,
        borderRadius: `${st.radius_px}px`,
      });
      const inner = { w: r.w - 2 * st.border_px, h: r.h - 2 * st.border_px };
      el.innerHTML =
        `<canvas></canvas>` +
        `<b data-box="n05.tile.${key}.label" style="left:${st.label.x}px;top:${st.label.y}px;font-size:${st.label.size_px}px">${esc(def.name)}</b>` +
        // the rate and the chart keep the label's padding from the tile's edges
        `<span class="rate" data-box="n05.tile.${key}.rate" style="right:${st.label.x}px;bottom:${st.label.y}px;font-size:${st.rate?.size_px ?? 18}px"></span>`;
      this.stage.append(el);
      const rec = recordOf(this.s, def.test);
      const tile = { key, def, el, rect: r, inner, d: order[i], rec, canvas: el.querySelector("canvas"), rateEl: el.querySelector(".rate") };
      tile.start = T.tiles_s + tile.d * T.tile_stagger_s;
      tile.land = tile.start + T.tile_in_s;
      tile.ease = ease;
      if (rec) this.makeView(tile, design);
      else el.insertAdjacentHTML("beforeend", `<span class="none">記録がまだ無い (${esc(def.test)})</span>`);
      if (!clearOfBand(r, this.layout)) el.dataset.outside = "1";
      return tile;
    });
  }

  makeHero(design) {
    const sp = this.spec;
    const el = document.createElement("canvas");
    el.className = "hero";
    el.dataset.box = "n05.hero";
    Object.assign(el.style, { left: `${sp.hero.x}px`, top: `${sp.hero.y}px`, width: `${sp.hero.w}px`, height: `${sp.hero.h}px` });
    this.stage.append(el);
    const view = new Workbench3D(null, "real", { renderer: this.renderer, ground: "shadow" });
    view.setDesign(design);
    view.setLoading("two_cargo");
    view.setScene(null, null);
    view.setToggles({ parts: true, contour: false, arrows: false, cg: false, names: false, limit: false });
    view.resize(sp.hero.w, sp.hero.h);
    view.fitPoints(view.contentPoints(), [0.95, 0.8, 0.42], 1.02);
    return { el, view };
  }

  // The big vehicle as a PNG (data URL) of its box at the page's scale, as last drawn.
  png() {
    return this.hero ? this.hero.el.toDataURL("image/png") : null;
  }

  makeView(tile, design) {
    const { lc, ana } = tile.rec;
    const v = new Workbench3D(null, params.get("tilelook") === "real" ? "real" : "zu", { renderer: this.renderer });
    v.setDesign(this.s.designs[ana.design_version] ?? design);
    v.setLoading(lc.params?.loading ?? null);
    const motion = motionOf(ana);
    v.setMotion(motion);
    v.setScene(lc, ana);
    const field = tile.def.field ? fieldFor(ana, tile.def.field) : null;
    const ct = field ? contourOf(ana, field, lc.criterion) : null;
    v.setContour(ct);
    v.setVectors(null);
    v.setToggles({ parts: true, contour: true, arrows: false, cg: true, names: false, limit: false });
    v.resize(tile.inner.w, tile.inner.h);
    // the camera takes in the vehicle and what the scene puts beside it (a step's edge, a wall's face)
    const pts = v.contentPoints();
    const d = v.sceneDesc;
    if (d?.step) pts.push(new THREE.Vector3(d.step.x0 + 400, 0, d.step.h), new THREE.Vector3(d.step.x0, 0, 0));
    if (d?.wall) pts.push(new THREE.Vector3(d.wall.x + 300, 0, 1200));
    for (const o of d?.objects ?? []) if (o.kind === "wall" || o.kind === "step") pts.push(new THREE.Vector3(o.position_mm[0] - o.size_mm[0] / 2, o.position_mm[1], o.position_mm[2]));
    v.fitPoints(pts, VIEWS[v.view ?? "default"], 1.06);
    tile.view = v;
    tile.motion = motion;
    // the record's time span: the motion, else the chart's channel (when it is against time)
    const chart = tile.def.chart ? channelsOf(ana).get(tile.def.chart) ?? xSeriesOf(ana).get(tile.def.chart) ?? null : null;
    tile.span = motion ? [motion.t0, motion.t1] : chart?.t_s ? [chart.t_s[0], chart.t_s.at(-1)] : null;
    if (chart) {
      const ch = { w: Math.round(tile.inner.w * 0.42), h: Math.round(tile.inner.h * 0.36) };
      const { svg, X } = chartSvg(chart, ch.w, ch.h, this.spec.tile.rate?.size_px ?? this.layout.min_text_px ?? 18);
      // below the name's line, at the right, with the name's padding
      const pad = this.spec.tile.label;
      const top = pad.y + pad.size_px + pad.y / 2;
      tile.el.insertAdjacentHTML("beforeend", `<svg class="chart" data-box="n05.tile.${tile.key}.chart" style="right:${pad.x}px;top:${top}px" width="${ch.w}" height="${ch.h}" viewBox="0 0 ${ch.w} ${ch.h}">${svg}</svg>`);
      tile.chart = { s: chart, X, cur: tile.el.querySelector(".chart .cur") };
    }
    if (ct?.frequency) tile.el.querySelector("b").insertAdjacentHTML("beforeend", ` <small>${fmt(ct.frequency, 3)} Hz</small>`);
  }

  resize(scale) {
    this.scale = scale;
    const sp = this.spec;
    // the hero's box is the largest drawn through the shared renderer (also without the hero)
    this.renderer.setSize(Math.ceil(sp.hero.w * scale), Math.ceil(sp.hero.h * scale), false);
    const size = (c, w, h) => {
      c.width = Math.round(w * scale);
      c.height = Math.round(h * scale);
    };
    if (this.hero) size(this.hero.el, sp.hero.w, sp.hero.h);
    for (const t of this.tiles) size(t.canvas, t.inner.w, t.inner.h);
  }

  draw(t) {
    const sp = this.spec;
    const T = sp.times;
    const H = sp.hero_motion;
    // hero: a slow push until the burst, then it swells and fades (alone, it stays at rest)
    const he = this.hero?.el;
    if (ONLY_HERO) {
      this.hero.view.renderInto(he.getContext("2d"), he.width, he.height, this.scale);
      return;
    }
    if (he) {
      const burst = Math.max(0, Math.min(1, (t - T.burst_s) / H.burst_dur_s));
      const push = 1 + (H.push_scale - 1) * Math.min(1, t / T.burst_s);
      const k = cubicBezier(...H.ease)(burst);
      he.style.transform = `scale(${t < T.burst_s ? push : H.push_scale + (H.burst_scale - H.push_scale) * k})`;
      he.style.opacity = String(1 - k);
      he.style.visibility = burst >= 1 ? "hidden" : "visible";
      if (burst < 1) this.hero.view.renderInto(he.getContext("2d"), he.width, he.height, this.scale);
    }

    for (const tile of this.tiles) {
      const f = flyIn(tile.rect, sp.fly_from, t, tile.start, T.tile_in_s, tile.ease);
      tile.el.style.transform = `translate(${f.dx}px, ${f.dy}px) scale(${f.scale})`;
      tile.el.style.opacity = String(f.opacity);
      tile.el.style.visibility = f.p > 0 ? "visible" : "hidden";
      if (!tile.view || f.p <= 0) continue;
      // the record plays from the landing to the end, squeezed to fit
      let tau = 0;
      if (tile.span) {
        const q = squeeze(t, tile.land, sp.duration_s, tile.span[0], tile.span[1]);
        tau = q.tau;
        tile.rateEl.textContent = rateLabel(q.rate);
      }
      if (tile.motion) tile.view.setFrame(tile.motion.frame(tau));
      tile.view.animate(t, false); // a mode shape swings with the stage clock
      if (tile.chart?.cur) tile.chart.cur.setAttribute("transform", `translate(${tile.chart.X(tau).toFixed(1)} 0)`);
      tile.view.renderInto(tile.canvas.getContext("2d"), tile.canvas.width, tile.canvas.height, this.scale);
    }
  }
}

// ---- N01: a step climbed, and the case past the limit over it in translucent red ----
//
// The main view plays the climb of the lower step (K2 motion) with the frame coloured by
// the K1 static analysis of the same load case (the stress at the push, said on the
// card), the reaction and grip arrows and the centre of gravity. The higher step's motion
// runs on the same clock as a red see-through overlay. Cases: ?ok= and ?over= (load case
// ids), else the P1 load cases with the lowest and the highest step.

const N01_KEYS = ["duration_s", "view", "pane", "card", "times", "zoom", "overlay", "colors"];
const TOP_KEYS = ["min_text_px"];

function stepHeightOf(lc) {
  const p = lc?.params?.step_height;
  return typeof p === "number" ? p * 1000 : p?.unit === "m" ? p.value * 1000 : p?.value ?? NaN;
}

// The newest ok analysis of a load case by a test of `pick` (motion: has series; still: has a stress field).
function analysisOf(s, lcId, pick) {
  const list = s.order.analyses.map((id) => s.analyses[id]).filter((a) => a.load_case_id === lcId && a.status === "ok");
  return list.filter(pick).at(-1) ?? null;
}

const resultOf = (ana, q) => (ana?.results ?? []).find((r) => r.quantity === q) ?? null;

class CutN01 {
  constructor(layout, s) {
    const missing = [...N01_KEYS.filter((k) => layout.n01?.[k] === undefined), ...TOP_KEYS.filter((k) => layout[k] === undefined)];
    if (missing.length) throw new Error(`layout.json の n01 に無い: ${missing.join(", ")}`);
    this.layout = layout;
    this.s = s;
    this.spec = layout.n01;
    this.stage = $("stage");
    this.stage.style.background = this.spec.colors.bg;
    this.scale = 1;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(1);
    const steps = (s.project?.load_cases ?? []).filter((c) => c.test_id === "P1").sort((a, b) => stepHeightOf(a) - stepHeightOf(b));
    const byId = (id) => steps.find((c) => c.load_case_id === id);
    this.ok = this.caseOf(byId(params.get("ok")) ?? steps[0]);
    this.over = this.caseOf(byId(params.get("over")) ?? steps.at(-1));
    if (!this.ok?.motion) throw new Error("段差の動きの記録 (P1 の step_climb_dynamic) が無い");
    this.build();
  }

  caseOf(lc) {
    if (!lc) return null;
    const dyn = analysisOf(this.s, lc.load_case_id, (a) => (a.series ?? []).length > 0);
    const still = analysisOf(this.s, lc.load_case_id, (a) => fieldsOf(a, { mesh: false }).some((f) => f.kind === "stress"));
    return { lc, dyn, still, motion: dyn ? motionOf(dyn) : null };
  }

  view(c, tint) {
    const design = this.s.designs[c.dyn.design_version] ?? this.s.designs[this.s.currentVersion];
    const v = new Workbench3D(null, "zu", { renderer: this.renderer });
    v.setDesign(design);
    v.setLoading(c.lc.params?.loading ?? null);
    v.setMotion(c.motion);
    v.setScene(c.lc, c.dyn);
    if (tint) {
      v.setToggles({ parts: true, contour: false, arrows: false, cg: false, names: false, limit: false });
      v.tint(tint.color, tint.opacity);
    } else {
      // The colour band's top is the allowable stress when the run judges stress (R1), so the
      // two cases are read on one scale; otherwise the field's own maximum.
      const field = c.still ? fieldsOf(c.still, { mesh: false }).find((f) => f.kind === "stress") : null;
      const lim = field ? this.allowableCriterion(field.unit) : null;
      this.contour = field ? contourOf(c.still, field, lim ?? c.lc.criterion) : null;
      v.setContour(this.contour);
      v.setToggles({ parts: true, contour: true, arrows: true, cg: true, names: false, limit: false });
    }
    return v;
  }

  allowableCriterion(unit) {
    return (this.s.project?.load_cases ?? []).find((c) => c.criterion?.quantity === "max_stress" && c.criterion?.limit?.unit === unit)?.criterion ?? null;
  }

  build() {
    const sp = this.spec;
    const V = sp.view;
    const box = document.createElement("div");
    box.className = "n01-view";
    box.dataset.box = "n01.view";
    Object.assign(box.style, { left: `${V.x}px`, top: `${V.y}px`, width: `${V.w}px`, height: `${V.h}px`, background: sp.colors.panel, border: `${sp.card.border_px}px solid ${sp.colors.rule}`, borderRadius: `${sp.card.radius_px}px` });
    box.innerHTML =
      `<canvas class="main"></canvas><canvas class="over"></canvas>` +
      `<span class="rate" data-box="n01.view.rate" style="right:${sp.card.pad}px;bottom:${sp.card.pad}px;font-size:${sp.card.text_px}px"></span>`;
    this.stage.append(box);
    this.rateEl = box.querySelector(".rate");
    this.inner = { w: V.w - 2 * sp.card.border_px, h: V.h - 2 * sp.card.border_px };
    this.main = { el: box.querySelector("canvas.main"), view: this.view(this.ok, null) };
    this.ghost = this.over?.motion ? { el: box.querySelector("canvas.over"), view: this.view(this.over, sp.overlay) } : null;
    for (const v of [this.main.view, this.ghost?.view].filter(Boolean)) v.resize(this.inner.w, this.inner.h);
    // what the camera takes in: the vehicle and the step's edge
    this.points = this.main.view.contentPoints();
    for (const o of this.main.view.sceneDesc?.objects ?? []) if (o.kind === "step") this.points.push(new THREE.Vector3(o.position_mm[0] - o.size_mm[0] / 2, o.position_mm[1], o.position_mm[2]));
    this.span = Math.max(...[this.ok, this.over].filter((c) => c?.motion).map((c) => c.motion.duration));
    this.buildPane();
  }

  // The frame's allowable stress: the limit of the run's load case judged on max_stress
  // (the strength test R1), in the stress's own unit; null when the run has none.
  allowable(unit) {
    const lc = (this.s.project?.load_cases ?? []).find((c) => c.criterion?.quantity === "max_stress" && c.criterion?.limit?.unit === unit);
    return lc ? { value: lc.criterion.limit.value, op: lc.criterion.op, test: lc.test_id } : null;
  }

  card(c, kind) {
    const sp = this.spec;
    const h = stepHeightOf(c.lc);
    const climbed = resultOf(c.dyn, "climbed");
    const time = resultOf(c.dyn, "climb_time");
    const force = resultOf(c.dyn, "front_wheel_force");
    const stress = resultOf(c.still, "max_stress");
    const ok = climbed ? climbed.value >= 1 : null;
    // the stress against the allowable, when the run gives one (the words say which way it falls)
    const lim = stress ? this.allowable(stress.unit) : null;
    const over = lim ? (lim.op === "<" ? stress.value >= lim.value : stress.value > lim.value) : false;
    const climb = ok === null ? "" : ok ? `<span class="ok">✓ 登れた${time ? ` (${fmt(time.value, 3)} 秒)` : ""}</span>` : `<span class="miss">✕ 登れなかった</span>`;
    const mark = climb + (over ? `<br><span class="miss">✕ フレームの応力が許容を超える</span>` : "");
    // small notes are drawn at the layout's smallest text size
    const small = `font-size:${this.layout.min_text_px}px`;
    const vs = lim ? ` <small class="${over ? "miss" : "ok"}" style="${small}">(許容 ${esc(fmt(lim.value, 4))} MPa ${over ? "を超える" : "以内"})</small>` : "";
    const rows = [
      force && `<tr><td>前輪を押す力</td><td>${esc(fmt(force.value / 1000, 3))} kN</td></tr>`,
      stress && `<tr class="${over ? "miss" : ""}"><td>フレームの最大応力</td><td>${esc(fmt(stress.value, 4))} MPa${vs}</td></tr>`,
    ].filter(Boolean).join("");
    const head = kind === "over" ? `<small style="${small}">限界の外の条件: 段差 ${fmt(h, 3)} mm</small>段差 ${fmt(h, 3)} mm` : `段差 ${fmt(h, 3)} mm`;
    return (
      `<div class="n01-card ${kind}" data-box="n01.card.${kind}" style="border-color:${kind === "over" ? sp.overlay.color : sp.colors.rule};border-width:${sp.card.border_px}px;border-radius:${sp.card.radius_px}px;padding:${sp.card.pad}px;margin-bottom:${sp.card.gap}px">` +
      `<h3 style="font-size:${sp.card.title_px}px">${head}</h3>${mark ? `<p style="font-size:${sp.card.text_px}px">${mark}</p>` : ""}` +
      `<table style="font-size:${sp.card.text_px}px">${rows}</table></div>`
    );
  }

  buildPane() {
    const sp = this.spec;
    const P = sp.pane;
    const pane = document.createElement("div");
    pane.className = "n01-pane";
    pane.dataset.box = "n01.pane";
    Object.assign(pane.style, { left: `${P.x}px`, top: `${P.y}px`, width: `${P.w}px`, height: `${P.h}px` });
    const ct = this.contour;
    // the band says whose stress it colours, and what its top is (the allowable or the maximum)
    const top = ct?.limitKnown ? `許容 ${fmt(ct.max, 4)}` : fmt(ct?.max, 3);
    const legend = ct
      ? `<div class="n01-legend" data-box="n01.legend" style="font-size:${sp.card.text_px}px"><span>応力 [MPa]・段差 ${fmt(stepHeightOf(this.ok.lc), 3)} mm</span>` +
        `<i style="background:linear-gradient(90deg, ${BAND_COLORS.join(",")})"></i><span>${fmt(ct.min, 3)}</span><span>${top}</span></div>` +
        (ct.limitKnown ? `<p class="n01-note" style="font-size:${sp.card.text_px}px;margin-bottom:${sp.card.gap}px"><i style="display:inline-block;width:14px;height:14px;background:${OVER_COLOR};vertical-align:-2px"></i> 許容を超えた部材</p>` : "")
      : "";
    pane.innerHTML =
      this.card(this.ok, "ok") +
      (this.over && this.over !== this.ok ? this.card(this.over, "over") : "") +
      legend +
      `<p class="n01-note" data-box="n01.note" style="font-size:${sp.card.text_px}px">フレームの色: 応力は段を押して登る瞬間の静的な解析 (K1)。半透明の赤: 限界の外の条件の動き</p>`;
    this.stage.append(pane);
  }

  resize(scale) {
    this.scale = scale;
    this.renderer.setSize(Math.ceil(this.inner.w * scale), Math.ceil(this.inner.h * scale), false);
    for (const c of [this.main, this.ghost].filter(Boolean)) {
      c.el.width = Math.round(this.inner.w * scale);
      c.el.height = Math.round(this.inner.h * scale);
    }
  }

  draw(t) {
    const sp = this.spec;
    const { tau, rate } = squeeze(t, sp.times.lead_s, sp.duration_s, 0, this.span);
    this.rateEl.textContent = rateLabel(rate);
    // the camera comes closer over the first zoom_s seconds
    const z = Math.min(1, t / sp.times.zoom_s);
    const margin = sp.zoom.from + (sp.zoom.to - sp.zoom.from) * (1 - (1 - z) ** 3);
    const mv = this.main.view;
    mv.fitPoints(this.points, VIEWS[mv.view ?? "side"], margin);
    const m = this.ok.motion;
    mv.setFrame(m.frame(Math.min(m.t1, m.t0 + tau)));
    mv.renderInto(this.main.el.getContext("2d"), this.main.el.width, this.main.el.height, this.scale);
    if (this.ghost) {
      const g = this.over.motion;
      this.ghost.view.setFrame(g.frame(Math.min(g.t1, g.t0 + tau)));
      this.ghost.view.copyCamera(mv);
      this.ghost.view.renderInto(this.ghost.el.getContext("2d"), this.ghost.el.width, this.ghost.el.height, this.scale);
    }
  }
}

// ---- N02: five tests side by side, each with its case past the limit over it ----
//
// One pane per test in the layout's grid (and one for the legend): the motion of the
// test's load case with the centre of gravity and the reaction and grip arrows; the
// load case of the same test whose id ends in -beyond (the K2 team's "限界の外"
// conditions) runs on the same clock as a red see-through overlay. The result pane
// lists the first results of each record as they were written.

const N02_KEYS = ["duration_s", "grid", "panes", "pane", "result", "times", "overlay", "colors"];
const N02_TESTS = {
  tilt: { test: "S1", name: "傾斜台" },
  wind: { test: "F1", name: "横風" },
  rough: { test: "S5", name: "凸凹の横斜面" },
  dlc: { test: "S3", name: "ダブルレーンチェンジ" },
  brake: { test: "S4", name: "急ブレーキ" },
};
const BEYOND = /-beyond$/;

class CutN02 {
  constructor(layout, s) {
    const missing = N02_KEYS.filter((k) => layout.n02?.[k] === undefined);
    if (missing.length) throw new Error(`layout.json の n02 に無い: ${missing.join(", ")}`);
    this.layout = layout;
    this.s = s;
    this.spec = layout.n02;
    this.stage = $("stage");
    this.stage.style.background = this.spec.colors.bg;
    this.scale = 1;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(1);
    this.build();
  }

  // The test's case and its case past the limit (the newest motion of each).
  casesOf(testId) {
    const lcs = (this.s.project?.load_cases ?? []).filter((c) => c.test_id === testId);
    const motionCase = (lc) => {
      if (!lc) return null;
      const ana = analysisOf(this.s, lc.load_case_id, (a) => (a.series ?? []).length > 0);
      return ana ? { lc, ana, motion: motionOf(ana) } : null;
    };
    return { ok: motionCase(lcs.find((c) => !BEYOND.test(c.load_case_id))), over: motionCase(lcs.find((c) => BEYOND.test(c.load_case_id))) };
  }

  makeView(c, tint, w, h) {
    const v = new Workbench3D(null, "zu", { renderer: this.renderer });
    v.setDesign(this.s.designs[c.ana.design_version] ?? this.s.designs[this.s.currentVersion]);
    v.setLoading(c.lc.params?.loading ?? null);
    v.setMotion(c.motion);
    v.setScene(c.lc, c.ana);
    if (tint) {
      v.setToggles({ parts: true, contour: false, arrows: false, cg: false, names: false, limit: false });
      v.tint(tint.color, tint.opacity);
    } else {
      v.setContour(null);
      v.setToggles({ parts: true, contour: false, arrows: true, cg: true, names: false, limit: true });
    }
    v.resize(w, h);
    return v;
  }

  build() {
    const sp = this.spec;
    const st = sp.pane;
    const rects = gridRects(sp.grid);
    this.panes = sp.panes.map((key, i) => {
      const r = rects[i];
      const el = document.createElement("div");
      el.className = "n02-pane";
      el.dataset.box = `n02.pane.${key}`;
      Object.assign(el.style, {
        left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px`,
        background: sp.colors.panel, border: `${st.border_px}px solid ${sp.colors.rule}`, borderRadius: `${st.radius_px}px`,
      });
      this.stage.append(el);
      const inner = { w: r.w - 2 * st.border_px, h: r.h - 2 * st.border_px };
      if (key === "legend") {
        el.innerHTML = this.legendHtml();
        return { key, el, inner };
      }
      const def = N02_TESTS[key] ?? { name: key };
      el.innerHTML =
        `<canvas class="main"></canvas><canvas class="over"></canvas>` +
        `<b data-box="n02.pane.${key}.label" style="left:${st.label.x}px;top:${st.label.y}px;font-size:${st.label.size_px}px">${esc(def.name)}</b>` +
        `<span class="rate" data-box="n02.pane.${key}.rate" style="right:${st.label.x}px;bottom:${st.label.y}px;font-size:${st.rate.size_px}px"></span>`;
      const { ok, over } = def.test ? this.casesOf(def.test) : {};
      const pane = { key, def, el, inner, ok, over, main: el.querySelector("canvas.main"), ghostEl: el.querySelector("canvas.over") };
      if (!ok) {
        el.insertAdjacentHTML("beforeend", `<span class="none">記録がまだ無い (${esc(def.test)})</span>`);
        return pane;
      }
      pane.view = this.makeView(ok, null, inner.w, inner.h);
      pane.ghost = over ? this.makeView(over, sp.overlay, inner.w, inner.h) : null;
      pane.span = Math.max(ok.motion.duration, over?.motion?.duration ?? 0);
      // a case past the limit that stays within a metre of where it starts (a slope tilted under it)
      const path = over?.motion?.path;
      pane.overStill = !!path && Math.hypot(path.maxX - path.minX, path.maxY - path.minY) < 1000;
      // the camera takes in the vehicle at rest from the scene's own direction
      pane.view.fitPoints(pane.view.contentPoints(), VIEWS[pane.view.view ?? "default"], 1.12);
      // the pane says only that the red is the case past the limit; its conditions are in the result pane
      if (over) el.insertAdjacentHTML("beforeend", `<span class="beyond" data-box="n02.pane.${key}.beyond" style="left:${st.label.x}px;bottom:${st.label.y}px;font-size:${st.rate.size_px}px;color:${sp.overlay.color}">限界の外の条件</span>`);
      return pane;
    });
    this.buildResult();
  }

  legendHtml() {
    const sp = this.spec;
    const st = sp.pane;
    const C = { reaction: "#2b59c3", grip: "#8e6bbf" };
    const arrow = (c) => `<svg width="34" height="20" viewBox="0 0 34 20"><line x1="2" y1="10" x2="24" y2="10" stroke="${c}" stroke-width="4"/><path d="M22 3 L33 10 L22 17 Z" fill="${c}"/></svg>`;
    const cg = `<svg width="22" height="22" viewBox="0 0 22 22"><circle cx="11" cy="11" r="9" fill="#fbf9f4" stroke="#1d1d1f" stroke-width="2"/><path d="M11 11 L11 2 A9 9 0 0 1 20 11 Z M11 11 L11 20 A9 9 0 0 1 2 11 Z" fill="#1d1d1f"/></svg>`;
    const ghost = `<i style="display:inline-block;width:30px;height:16px;background:${sp.overlay.color};opacity:${Math.min(1, sp.overlay.opacity * 2.5)};border-radius:3px"></i>`;
    const rows = [
      [cg, "重心"],
      [arrow(C.reaction), "路面の反力 (輪ごと)"],
      [arrow(C.grip), "グリップ (タイヤの横・前後の力)"],
      [ghost, "限界の外の条件の動き (止まったまま傾ける試験は、車両に重ねる)"],
    ];
    return (
      `<b data-box="n02.pane.legend.label" style="left:${st.label.x}px;top:${st.label.y}px;font-size:${st.label.size_px}px">凡例</b>` +
      `<ul class="n02-legend" style="left:${st.label.x}px;top:${st.label.y * 2 + st.label.size_px}px;font-size:${sp.result.text_px}px">${rows.map(([i, t]) => `<li>${i}<span>${t}</span></li>`).join("")}</ul>`
    );
  }

  buildResult() {
    const sp = this.spec;
    const R = sp.result;
    const el = document.createElement("div");
    el.className = "n02-result";
    el.dataset.box = "n02.result";
    Object.assign(el.style, { left: `${R.x}px`, top: `${R.y}px`, width: `${R.w}px`, height: `${R.h}px` });
    // The first results of a record, a value repeated with the same unit shown once. For the
    // case past the limit, the results that differ from the test's own case come first (a
    // property that does not change with the conditions says nothing new there).
    const line = (c, base = null) => {
      const seen = new Set();
      const rows = [];
      const same = (r) => (base?.ana?.results ?? []).some((b) => b.quantity === r.quantity && b.value === r.value && b.unit === r.unit);
      const results = [...(c?.ana?.results ?? [])];
      if (base) results.sort((a, b) => same(a) - same(b));
      for (const r of results) {
        const key = `${r.value}|${r.unit}`;
        if (seen.has(key)) continue;
        seen.add(key);
        rows.push(`<tr><td>${esc(quantityName(r.quantity))}</td><td>${esc(resultText(r))}</td></tr>`);
        if (rows.length >= R.rows) break;
      }
      return rows.join("");
    };
    // ✓ / ✕ from the load case's verdicts when the run judged it; a case past the limit is ✕ by its conditions.
    const mark = (lc) => {
      const vs = Object.values(this.s.verdicts).filter((v) => v.load_case_id === lc.load_case_id);
      if (!vs.length) return "";
      return vs.every((v) => v.pass) ? `<span class="ok">✓</span> ` : `<span class="miss">✕</span> `;
    };
    el.innerHTML =
      `<h3 style="font-size:${R.title_px}px">限界の値</h3>` +
      this.panes
        .filter((p) => p.ok)
        .map(
          (p) =>
            `<div class="n02-res" data-box="n02.result.${p.key}" style="margin-bottom:${R.row_gap}px;font-size:${R.text_px}px"><b>${mark(p.ok.lc)}${esc(p.def.name)}</b>` +
            `<table>${line(p.ok)}</table>` +
            (p.over ? `<div class="over" style="color:${sp.overlay.color}">✕ ${esc(p.over.lc.title ?? "限界の外の条件")}</div><table>${line(p.over, p.ok)}</table>` : "") +
            `</div>`,
        )
        .join("");
    this.stage.append(el);
    // The conditions of the cases past the limit are their load cases' titles as recorded and
    // can run to several lines: the rows' text steps down a pixel at a time until they end
    // inside the box, not below layout.min_text_px.
    const blocks = [...el.querySelectorAll(".n02-res")];
    const bottom = () => Math.max(0, ...blocks.map((b) => b.offsetTop + b.offsetHeight));
    let px = R.text_px;
    while (bottom() > R.h && px > (this.layout.min_text_px ?? R.text_px)) {
      px -= 1;
      for (const b of blocks) b.style.fontSize = `${px}px`;
    }
    el.dataset.textPx = String(px);
  }

  resize(scale) {
    this.scale = scale;
    const r = gridRects(this.spec.grid)[0];
    this.renderer.setSize(Math.ceil(r.w * scale), Math.ceil(r.h * scale), false);
    for (const p of this.panes) {
      for (const c of [p.main, p.ghostEl].filter(Boolean)) {
        c.width = Math.round(p.inner.w * scale);
        c.height = Math.round(p.inner.h * scale);
      }
    }
  }

  draw(t) {
    const sp = this.spec;
    for (const p of this.panes) {
      if (!p.view) continue;
      const q = squeeze(t, sp.times.lead_s, sp.duration_s, 0, p.span);
      p.el.querySelector(".rate").textContent = rateLabel(q.rate);
      const m = p.ok.motion;
      const mf = m.frame(Math.min(m.t1, m.t0 + q.tau));
      p.view.setFrame(mf);
      p.view.renderInto(p.main.getContext("2d"), p.main.width, p.main.height, this.scale);
      if (p.ghost) {
        // The case past the limit at its own place on the road, seen through the pane's
        // camera (it runs past or slides away: that is what happened). One that stays where
        // it starts (a slope tilted under it) is laid over the vehicle to compare attitudes.
        const g = p.over.motion;
        const gf = g.frame(Math.min(g.t1, g.t0 + q.tau));
        p.ghost.setFrame(gf);
        if (p.overStill) p.ghost.world.position.copy(p.view.anchorOf(mf).sub(p.ghost.anchorOf(gf)));
        p.ghost.copyCamera(p.view);
        p.ghost.renderInto(p.ghostEl.getContext("2d"), p.ghostEl.width, p.ghostEl.height, this.scale);
      }
    }
  }
}

// N04 and N15 (the grid of all domains and assembly) are in cut_grid.js.
const CUTS = { n05: Cut, n01: CutN01, n02: CutN02, n04: CutN04, n15: CutN15 };

// ---- page -------------------------------------------------------------------
const STAGE_W = 1920;
const STAGE_H = 1080;
let cut = null;
let playhead = Number(params.get("t") ?? 0) || 0;

function fit() {
  const scale = Math.min(window.innerWidth / STAGE_W, window.innerHeight / STAGE_H);
  const left = (window.innerWidth - STAGE_W * scale) / 2;
  const top = (window.innerHeight - STAGE_H * scale) / 2;
  $("stage").style.transform = `translate(${left}px, ${top}px) scale(${scale})`;
  cut?.resize(scale * (window.devicePixelRatio || 1));
  cut?.draw(playhead);
}

const nextFrame = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
let readyResolve;
const ready = new Promise((r) => (readyResolve = r));
const player = { duration: 5, playhead: 0 };

async function drawAt(t) {
  if (!cut) return null; // the cut could not be built; the page says why
  playhead = Math.max(0, Math.min(player.duration, Number(t) || 0));
  player.playhead = playhead;
  cut.draw(playhead);
  await nextFrame();
  document.documentElement.dataset.t = String(playhead);
  return playhead;
}

window.hiraku = {
  ready,
  player,
  async seek(t) {
    await ready;
    return drawAt(t);
  },
  png: () => cut?.png?.() ?? null,
};

async function main() {
  try {
    const name = params.get("cut") ?? "n05";
    if (!CUTS[name]) throw new Error(`no cut named ${name}`);
    const [layout, events] = await Promise.all([loadLayout(), readRun(params.get("run") ?? "sample")]);
    cut = new CUTS[name](layout, stateOf(events));
    window.hiraku.cut = cut;
    player.duration = layout[name].duration_s;
    window.addEventListener("resize", fit);
    fit();
    if (params.has("t")) await drawAt(playhead);
    else {
      // a preview: play the cut over and over
      let last = null;
      const loop = (ms) => {
        if (last !== null) playhead = (playhead + (ms - last) / 1000) % player.duration;
        last = ms;
        player.playhead = playhead;
        cut.draw(playhead);
        requestAnimationFrame(loop);
      };
      requestAnimationFrame(loop);
    }
    readyResolve({ t: playhead }); // an object, so a waiter that tests the value sees it ready at t = 0
  } catch (e) {
    $("stage").insertAdjacentHTML("beforeend", `<p class="err">${esc(String(e))}</p>`);
    readyResolve({ error: String(e) });
  }
}
main();
