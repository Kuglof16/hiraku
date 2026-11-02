// Cut pages for the video (cut.html?cut=n05&run=<id>): HIRAKU's own drawing of a
// run's records, laid out by the promo department's rules (compose.js reads
// /layout.json; no position is written here). Time comes only from ?t= or
// window.hiraku.seek(t), so the frame at t is always the same; the exporter reads
// window.hiraku.player.duration. Elements carry data-box names for the layout
// checks (promo/layout_check.py).
//
// N05: the vehicle in the real look fills the screen, then bursts open into the
// grid of preset tests; each tile plays its test's record, squeezed to fit, with
// the squeeze in its corner.

import * as THREE from "three";
import { BAND_COLORS } from "./colormap.js";
import { contourOf, fieldsOf } from "./fields.js";
import { clearOfBand, cubicBezier, flyIn, gridRects, loadLayout, outwardOrder, rateLabel, squeeze } from "./compose.js";
import { esc, fmt } from "./labels.js";
import { channelsOf, lerpAt, motionOf, nearestAt } from "./series.js";
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
  const fields = fieldsOf(ana).filter((f) => f.kind === kind);
  if (kind === "mode_shape") return fields.find((f) => /frame|フレーム/i.test(f.label ?? "")) ?? fields[0] ?? null;
  return fields[0] ?? null;
}

// A small chart of one channel with a cursor (SVG in the tile's own pixels).
function chartSvg(s, w, h) {
  const t0 = s.t_s[0];
  const t1 = s.t_s.at(-1);
  const lo = Math.min(...s.values);
  const hi = Math.max(...s.values);
  const X = (t) => ((t - t0) / (t1 - t0 || 1)) * w;
  const Y = (v) => h - 4 - ((v - lo) / (hi - lo || 1)) * (h - 8);
  const pts = s.t_s.map((t, i) => `${X(t).toFixed(1)},${Y(s.values[i]).toFixed(1)}`).join(" ");
  return { svg: `<polyline points="${pts}" fill="none" stroke="#2b59c3" stroke-width="2.2"/><line class="cur" x1="0" x2="0" y1="0" y2="${h}" stroke="#c8402a" stroke-width="1.5"/>`, X };
}

// Every value the N05 cut reads from the layout rules; a missing one is named, not guessed.
const N05_KEYS = ["duration_s", "grid", "tile", "hero", "hero_motion", "times", "ease", "fly_from", "tests"];

class Cut {
  constructor(layout, s) {
    const missing = N05_KEYS.filter((k) => layout.n05?.[k] === undefined);
    if (missing.length) throw new Error(`layout.json の n05 に無い: ${missing.join(", ")}`);
    this.layout = layout;
    this.s = s;
    this.spec = layout.n05;
    this.stage = $("stage");
    this.stage.style.background = layout.colors?.night ?? "#131311";
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
    const hero = document.createElement("canvas");
    hero.className = "hero";
    hero.dataset.box = "n05.hero";
    Object.assign(hero.style, { left: `${sp.hero.x}px`, top: `${sp.hero.y}px`, width: `${sp.hero.w}px`, height: `${sp.hero.h}px` });
    this.stage.append(hero);
    const hv = new Workbench3D(null, "real", { renderer: this.renderer, ground: "shadow" });
    hv.setDesign(design);
    hv.setLoading("two_cargo");
    hv.setScene(null, null);
    hv.setToggles({ parts: true, contour: false, arrows: false, cg: false, names: false, limit: false });
    hv.resize(sp.hero.w, sp.hero.h);
    hv.fitPoints(hv.contentPoints(), [0.95, 0.8, 0.42], 1.02);
    this.hero = { el: hero, view: hv };

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
    // the record's time span: the motion, else the chart's channel
    const chart = tile.def.chart ? channelsOf(ana).get(tile.def.chart) : null;
    tile.span = motion ? [motion.t0, motion.t1] : chart ? [chart.t_s[0], chart.t_s.at(-1)] : null;
    if (chart) {
      const ch = { w: Math.round(tile.inner.w * 0.42), h: Math.round(tile.inner.h * 0.36) };
      const { svg, X } = chartSvg(chart, ch.w, ch.h);
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
    this.renderer.setSize(Math.ceil(sp.hero.w * scale), Math.ceil(sp.hero.h * scale), false);
    const size = (c, w, h) => {
      c.width = Math.round(w * scale);
      c.height = Math.round(h * scale);
    };
    size(this.hero.el, sp.hero.w, sp.hero.h);
    for (const t of this.tiles) size(t.canvas, t.inner.w, t.inner.h);
  }

  draw(t) {
    const sp = this.spec;
    const T = sp.times;
    const H = sp.hero_motion;
    // hero: a slow push until the burst, then it swells and fades
    const he = this.hero.el;
    const burst = Math.max(0, Math.min(1, (t - T.burst_s) / H.burst_dur_s));
    const push = 1 + (H.push_scale - 1) * Math.min(1, t / T.burst_s);
    const k = cubicBezier(...H.ease)(burst);
    he.style.transform = `scale(${t < T.burst_s ? push : H.push_scale + (H.burst_scale - H.push_scale) * k})`;
    he.style.opacity = String(1 - k);
    he.style.visibility = burst >= 1 ? "hidden" : "visible";
    if (burst < 1) this.hero.view.renderInto(he.getContext("2d"), he.width, he.height, this.scale);

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
      if (tile.chart) tile.chart.cur.setAttribute("transform", `translate(${tile.chart.X(tau).toFixed(1)} 0)`);
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
    const missing = N01_KEYS.filter((k) => layout.n01?.[k] === undefined);
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
    const still = analysisOf(this.s, lc.load_case_id, (a) => fieldsOf(a).some((f) => f.kind === "stress"));
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
      const field = c.still ? fieldsOf(c.still).find((f) => f.kind === "stress") : null;
      this.contour = field ? contourOf(c.still, field, c.lc.criterion) : null;
      v.setContour(this.contour);
      v.setToggles({ parts: true, contour: true, arrows: true, cg: true, names: false, limit: false });
    }
    return v;
  }

  build() {
    const sp = this.spec;
    const V = sp.view;
    const box = document.createElement("div");
    box.className = "n01-view";
    box.dataset.box = "n01.view";
    Object.assign(box.style, { left: `${V.x}px`, top: `${V.y}px`, width: `${V.w}px`, height: `${V.h}px`, background: sp.colors.panel, border: `${sp.card.border_px}px solid ${sp.colors.rule}`, borderRadius: `${sp.card.radius_px}px` });
    box.innerHTML = `<canvas class="main"></canvas><canvas class="over"></canvas>`;
    this.stage.append(box);
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

  card(c, kind) {
    const sp = this.spec;
    const h = stepHeightOf(c.lc);
    const climbed = resultOf(c.dyn, "climbed");
    const time = resultOf(c.dyn, "climb_time");
    const force = resultOf(c.dyn, "front_wheel_force");
    const stress = resultOf(c.still, "max_stress");
    const ok = climbed ? climbed.value >= 1 : null;
    const mark = ok === null ? "" : ok ? `<span class="ok">✓ 登れた${time ? ` (${fmt(time.value, 3)} 秒)` : ""}</span>` : `<span class="miss">✕ 登れなかった</span>`;
    const rows = [
      force && `<tr><td>前輪を押す力</td><td>${esc(fmt(force.value / 1000, 3))} kN</td></tr>`,
      stress && `<tr><td>フレームの最大応力</td><td>${esc(fmt(stress.value, 4))} MPa</td></tr>`,
    ].filter(Boolean).join("");
    const head = `段差 ${fmt(h, 3)} mm${kind === "over" ? `<small>限界の外の条件</small>` : ""}`;
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
    const legend = ct
      ? `<div class="n01-legend" data-box="n01.legend" style="font-size:${sp.card.text_px}px"><span>応力 [MPa]</span><i style="background:linear-gradient(90deg, ${BAND_COLORS.join(",")})"></i><span>${fmt(ct.min, 3)}</span><span>${fmt(ct.max, 3)}</span></div>`
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
    const { tau } = squeeze(t, sp.times.lead_s, sp.duration_s, 0, this.span);
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

const CUTS = { n05: Cut, n01: CutN01 };

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
