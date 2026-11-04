// Cut pages for the video (cut.html?cut=n05&run=<id>): HIRAKU's own drawing of a
// run's records, laid out by the promo department's rules (compose.js reads
// /layout.json; no position is written here). Time comes only from ?t= or
// window.hiraku.seek(t), so the frame at t is always the same; the exporter reads
// window.hiraku.player.duration. Elements carry data-box names for the layout
// checks (promo/layout_check.py).
//
// N05: the vehicle in the real look fills the screen, then bursts open into the
// grid of preset tests; each tile plays its test's record, squeezed to fit, under its
// name and nothing else (the story: pictures, no figures). ?hero=0 leaves the vehicle
// out; ?only=hero draws it alone on a clear ground, and window.hiraku.png() returns it
// as a PNG.
//
// Every cut's elements say what they are with data-kind (cut_n03.js storyOf); after each
// frame the page counts them against layout.json story and says in a corner when the cut
// shows more than its limits (checkStory).

import * as THREE from "three";
import { BAND_COLORS, OVER_COLOR } from "./colormap.js";
import { contourOf, fieldsOf } from "./fields.js";
import { clearOfBand, cubicBezier, flyIn, gridRects, loadLayout, outwardOrder, rateLabel, squeeze } from "./compose.js";
import { CutN04, CutN15 } from "./cut_grid.js";
import { NG_WORD, OK_WORD, esc, fmt } from "./labels.js";
import { motionOf } from "./series.js";
import { applyEvent, initialState } from "./state.js";
import { VIEWS, Workbench3D, cutView, cutViews } from "./workbench3d.js";
import { looksReady } from "./look.js";
import { CutN16 } from "./cut_n16.js";
import { CutN07 } from "./cut_n07.js";
import { CutN03, KINDS, storyOf } from "./cut_n03.js";
import { CutN06 } from "./cut_n06.js";
import { CutN17 } from "./cut_n17.js";
import { CutN09 } from "./cut_n09.js";
import { CutN14 } from "./cut_n14.js";
import { CutN11 } from "./cut_n11.js";
import { CutN12 } from "./cut_n12.js";
import { CutN13 } from "./cut_n13.js";
import { PACE_LIMIT_DEG_S, pacer } from "./pace.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);

// What each preset tile shows (keys as in layout.json n05.tests): the test whose
// record it plays, the words on the tile (what is shown, not more) and the field drawn
// as a contour.
const TILES = {
  step: { test: "P1", name: "段差越え" },
  tilt: { test: "S1", name: "傾斜台" },
  wind: { test: "F1", name: "横風" },
  rough: { test: "S5", name: "オフロード走行" },
  dlc: { test: "S3", name: "DLC" },
  brake: { test: "S4", name: "急ブレーキ" },
  long: { test: "P4", name: "長時間" },
  launch: { test: "P2", name: "発進" },
  crash: { test: "C1", name: "衝突", field: "stress" },
  nvh: { test: "N1", name: "音振", field: "mode_shape" },
  fatigue: { test: "R3", name: "疲労", field: "damage" },
  heat: { test: "T1", name: "熱", field: "temperature" },
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
    this.story = storyOf(layout, "n05");
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
      const px = Math.max(st.label.size_px, this.story.bodyPx);
      el.innerHTML =
        `<canvas></canvas>` +
        `<b data-box="n05.tile.${key}.label" data-kind="label" style="left:${st.label.x}px;top:${st.label.y}px;font-size:${px}px">${esc(def.name)}</b>`;
      this.stage.append(el);
      const rec = recordOf(this.s, def.test);
      const tile = { key, def, el, rect: r, inner, d: order[i], rec, canvas: el.querySelector("canvas") };
      tile.start = T.tiles_s + tile.d * T.tile_stagger_s;
      tile.land = tile.start + T.tile_in_s;
      tile.ease = ease;
      if (rec) this.makeView(tile, design);
      else el.insertAdjacentHTML("beforeend", `<span class="none" style="font-size:${this.layout.min_text_px}px">記録がまだ無い (${esc(def.test)})</span>`);
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
    const v = cutView(this, "n05");
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
    tile.span = motion ? [motion.t0, motion.t1] : null;
    // after landing it still moves when it has a motion or a mode shape swinging
    tile.moves = !!motion || field?.kind === "mode_shape";
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
      he.dataset.kind = "moving";
      if (burst < 1) this.hero.view.renderInto(he.getContext("2d"), he.width, he.height, this.scale);
    }

    for (const tile of this.tiles) {
      const f = flyIn(tile.rect, sp.fly_from, t, tile.start, T.tile_in_s, tile.ease);
      tile.el.style.transform = `translate(${f.dx}px, ${f.dy}px) scale(${f.scale})`;
      tile.el.style.opacity = String(f.opacity);
      tile.el.style.visibility = f.p > 0 ? "visible" : "hidden";
      // what moves is the tile's picture (the words on it are not moving text)
      if (f.p > 0 && (f.p < 1 || tile.moves)) tile.canvas.dataset.kind = "moving";
      else delete tile.canvas.dataset.kind;
      if (!tile.view || f.p <= 0) continue;
      // the record plays from the landing to the end, squeezed to fit
      const tau = tile.span ? squeeze(t, tile.land, sp.duration_s, tile.span[0], tile.span[1]).tau : 0;
      if (tile.motion) tile.view.setFrame(tile.motion.frame(tau));
      tile.view.animate(t, false); // a mode shape swings with the stage clock
      tile.view.renderInto(tile.canvas.getContext("2d"), tile.canvas.width, tile.canvas.height, this.scale);
    }
  }
}

// ---- N01: a step climbed, and the case past the limit over it in translucent red ----
//
// The main view plays the climb of the lower step (K2 motion) with the frame coloured by
// the K1 static analysis of the same load case (the stress at the push), the reaction and
// grip arrows and the centre of gravity. The higher step's motion runs on the same clock
// as a red see-through overlay. Cases: ?ok= and ?over= (load case ids), else the P1 load
// cases with the lowest and the highest step. The pane says only what the story asks: per
// case its step's height (one number a card) and, large, "✓ OK" or "✕ NG" (verdict),
// and the colour bar of the stress.

// The large word of a card or pane: "✓ OK" when every check the records allow passes,
// "✕ NG" when one fails, nothing when the records say nothing. ok: true / false / null.
function verdictHtml(ok) {
  if (ok === null) return "";
  return ok ? `<span class="ok">${OK_WORD}</span>` : `<span class="miss">${NG_WORD}</span>`;
}

// Whether a recorded result meets a criterion ({quantity, op, limit}); null when it cannot say.
function meetsCriterion(criterion, ana) {
  const r = (ana?.results ?? []).find((x) => x.quantity === criterion?.quantity);
  const lim = criterion?.limit;
  if (!r || !lim || r.unit !== lim.unit) return null;
  return { "<=": r.value <= lim.value, "<": r.value < lim.value, ">=": r.value >= lim.value, ">": r.value > lim.value }[criterion.op] ?? null;
}

// The text steps down a pixel at a time from its size until it is no wider than room (not
// below least): the large words are as large as their box allows.
function fitWidth(el, room, least) {
  let px = parseFloat(el.style.fontSize);
  el.style.whiteSpace = "nowrap";
  while (el.scrollWidth > room && px > least) {
    px -= 1;
    el.style.fontSize = `${px}px`;
  }
}

// The size the large words start from: the box's width shared by seven characters (the
// size the cards were laid out for; "✕ NG" is shorter and stays at that size).
const VERDICT_CHARS = 7;

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
    this.story = storyOf(layout, "n01");
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
    const v = cutView(this, "n01");
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
      `<canvas class="main" data-kind="moving"></canvas><canvas class="over" data-kind="moving"></canvas>` +
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

  // A case's verdict from its records: it climbed (the climbed result) and the frame's stress
  // at the push (the still's max_stress) meets the allowable; null when neither is recorded.
  verdictOf(c) {
    const climbed = resultOf(c.dyn, "climbed");
    const stress = resultOf(c.still, "max_stress");
    const crit = stress ? this.allowableCriterion(stress.unit) : null;
    const checks = [climbed ? climbed.value >= 1 : null, crit ? meetsCriterion(crit, c.still) : null].filter((x) => x !== null);
    return checks.length ? checks.every(Boolean) : null;
  }

  // A case's card: its step's height (the card's one number) and, large, its verdict; in
  // the overlay's red for the higher step.
  card(c, kind) {
    const sp = this.spec;
    const h = stepHeightOf(c.lc);
    const ok = this.verdictOf(c);
    const px = Math.max(sp.card.text_px, this.story.bodyPx);
    const big = Math.floor((sp.pane.w - 2 * sp.card.pad - 2 * sp.card.border_px) / VERDICT_CHARS);
    return (
      `<div class="n01-card ${kind}" data-box="n01.card.${kind}" data-ok="${ok === null ? "" : ok ? "1" : "0"}" style="border-color:${kind === "over" ? sp.overlay.color : sp.colors.rule};border-width:${sp.card.border_px}px;border-radius:${sp.card.radius_px}px;padding:${sp.card.pad}px;margin-bottom:${sp.card.gap}px">` +
      `<h3 style="font-size:${px}px">段差 <span class="num" data-kind="number">${esc(fmt(h, 3))} mm</span></h3>` +
      (ok === null ? "" : `<p class="verdict" data-kind="label" style="font-size:${Math.max(big, this.story.bodyPx)}px">${verdictHtml(ok)}</p>`) +
      `</div>`
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
    const px = Math.max(sp.card.text_px, this.story.bodyPx);
    const small = this.layout.min_text_px;
    // the band says whose stress it colours, and what its top is (the allowable or the maximum)
    const top = ct?.limitKnown ? `許容 ${fmt(ct.max, 4)}` : fmt(ct?.max, 3);
    const legend = ct
      ? `<div class="n01-legend" data-box="n01.legend" data-kind="legend" style="font-size:${px}px"><span>フレームの応力 [MPa]</span>` +
        `<i style="background:linear-gradient(90deg, ${BAND_COLORS.join(",")})"></i><span>${fmt(ct.min, 3)}</span><span>${top}</span></div>` +
        (ct.limitKnown ? `<p class="n01-note" style="font-size:${small}px;margin-bottom:${sp.card.gap}px"><i style="display:inline-block;width:14px;height:14px;background:${OVER_COLOR};vertical-align:-2px"></i> 許容を超えた部材</p>` : "")
      : "";
    pane.innerHTML =
      this.card(this.ok, "ok") +
      (this.over && this.over !== this.ok ? this.card(this.over, "over") : "") +
      legend +
      `<p class="n01-note" data-box="n01.note" style="font-size:${small}px">半透明の赤: 高い方の段差での動き</p>`;
    this.stage.append(pane);
    const room = sp.pane.w - 2 * sp.card.pad - 2 * sp.card.border_px;
    for (const v of pane.querySelectorAll(".verdict")) fitWidth(v, room, this.story.bodyPx);
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
// test's load case with the centre of gravity; the load case of the same test whose id
// ends in -beyond (the K2 team's "限界の外" conditions) runs on the same clock as a red
// see-through overlay. Each pane has one tag: the test's name and, large under it, the
// verdict of its load case ("✓ OK" / "✕ NG": the run's verdicts when it judged the
// case, else the record's result against the load case's criterion; nothing when neither
// says). No figures (the story: which test it falls in, at a glance).
//
// With the story's limit on things moving at once (layout.json story), the panes play one
// after another: from times.lead_s to the end is cut in n + m - 1 equal steps (n panes
// with a record, m = limits.moving) and pane k plays over m of them from step k, holding
// its first pose before and its last after; so no more than m move at one time. Without a
// story they all play from lead_s to the end. Inside its turn a pane's records are paced
// (pace.js): where the vehicle or the case past the limit turns fast (a roll-over) they play
// slower and the rest faster, so nothing turns faster than 90 deg/s on the screen.

const N02_KEYS = ["duration_s", "grid", "panes", "pane", "pane.verdict.of_label", "times", "overlay", "colors"];
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
    const missing = N02_KEYS.filter((k) => k.split(".").reduce((o, x) => (o == null ? undefined : o[x]), layout.n02) === undefined);
    if (missing.length) throw new Error(`layout.json の n02 に無い: ${missing.join(", ")}`);
    this.layout = layout;
    this.story = storyOf(layout, "n02");
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
    const v = cutView(this, "n02");
    v.setDesign(this.s.designs[c.ana.design_version] ?? this.s.designs[this.s.currentVersion]);
    v.setLoading(c.lc.params?.loading ?? null);
    v.setMotion(c.motion);
    v.setScene(c.lc, c.ana);
    if (tint) {
      v.setToggles({ parts: true, contour: false, arrows: false, cg: false, names: false, limit: false });
      v.tint(tint.color, tint.opacity);
    } else {
      v.setContour(null);
      v.setToggles({ parts: true, contour: false, arrows: false, cg: true, names: false, limit: true });
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
      const { ok, over } = def.test ? this.casesOf(def.test) : {};
      const px = Math.max(st.label.size_px, this.story.bodyPx);
      const verdict = ok ? this.verdictOf(ok) : null;
      // the verdict fills the pane's width up to pane.verdict.of_label x the pane's name, so it stays above the vehicle
      const big = Math.max(this.story.bodyPx, Math.min(Math.round(st.verdict.of_label * px), Math.floor((r.w - 2 * st.border_px - 2 * st.label.x) / VERDICT_CHARS)));
      el.dataset.ok = verdict === null ? "" : verdict ? "1" : "0";
      el.innerHTML =
        `<canvas class="main"></canvas><canvas class="over"></canvas>` +
        `<b data-box="n02.pane.${key}.label" data-kind="label" style="left:${st.label.x}px;top:${st.label.y}px;font-size:${px}px">${esc(def.name)}` +
        (verdict === null ? "" : `<span class="verdict" style="font-size:${big}px">${verdictHtml(verdict)}</span>`) +
        `</b>`;
      const pane = { key, def, el, inner, ok, over, main: el.querySelector("canvas.main"), ghostEl: el.querySelector("canvas.over") };
      const v = el.querySelector(".verdict");
      if (v) fitWidth(v, r.w - 2 * st.border_px - 2 * st.label.x, this.story.bodyPx);
      if (!ok) {
        el.insertAdjacentHTML("beforeend", `<span class="none" style="font-size:${this.layout.min_text_px}px">記録がまだ無い (${esc(def.test)})</span>`);
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
      return pane;
    });
    // when each pane plays (see the top): its order among the panes with a record
    const playing = this.panes.filter((p) => p.view);
    const T = sp.times;
    const m = this.story.limits ? Math.min(playing.length, this.story.limits.moving) : null;
    const d = m ? (sp.duration_s - T.lead_s) / Math.max(1, playing.length + m - 1) : 0;
    playing.forEach((p, k) => {
      p.from = m ? T.lead_s + k * d : T.lead_s;
      p.to = m ? p.from + m * d : sp.duration_s;
      // the case past the limit that stays where it starts is laid over the vehicle (its turn shows as is)
      p.pace = pacer([{ motion: p.ok.motion, view: p.view }, p.ghost ? { motion: p.over.motion, view: p.ghost } : null], p.to - p.from, PACE_LIMIT_DEG_S);
    });
  }

  // A case's verdict: the load case's verdicts when the run judged it, else its record's
  // result against the load case's criterion; null when neither says.
  verdictOf(c) {
    const vs = Object.values(this.s.verdicts).filter((v) => v.load_case_id === c.lc.load_case_id);
    if (vs.length) return vs.every((v) => v.pass);
    return meetsCriterion(c.lc.criterion, c.ana);
  }

  legendHtml() {
    const sp = this.spec;
    const st = sp.pane;
    const cg = `<svg width="22" height="22" viewBox="0 0 22 22"><circle cx="11" cy="11" r="9" fill="#fbf9f4" stroke="#1d1d1f" stroke-width="2"/><path d="M11 11 L11 2 A9 9 0 0 1 20 11 Z M11 11 L11 20 A9 9 0 0 1 2 11 Z" fill="#1d1d1f"/></svg>`;
    const ghost = `<i style="display:inline-block;width:30px;height:16px;background:${sp.overlay.color};opacity:${Math.min(1, sp.overlay.opacity * 2.5)};border-radius:3px"></i>`;
    const rows = [
      [cg, "重心"],
      [ghost, "半透明の赤: 限界の外の条件の動き"],
    ];
    return `<ul class="n02-legend" style="left:${st.label.x}px;top:${st.label.y}px;right:${st.label.x}px;font-size:${Math.max(this.layout.min_text_px, st.rate.size_px)}px">${rows.map(([i, t]) => `<li>${i}<span>${t}</span></li>`).join("")}</ul>`;
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
      const q = { tau: p.pace((t - p.from) / Math.max(1e-9, p.to - p.from)) };
      if (t >= p.from && t < p.to) p.main.dataset.kind = "moving";
      else delete p.main.dataset.kind;
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

// N04 and N15 (the grid of all domains and assembly) are in cut_grid.js; N16 (the assembly order) in cut_n16.js;
// N11 (the hooks stop), N12 (the comparison and its cause) and N13 (a tool made in the run) in cut_n11.js .. cut_n13.js.
const CUTS = { n05: Cut, n01: CutN01, n02: CutN02, n04: CutN04, n15: CutN15, n16: CutN16, n07: CutN07, n03: CutN03, n06: CutN06, n17: CutN17, n09: CutN09, n11: CutN11, n12: CutN12, n13: CutN13, n14: CutN14 };

// ---- story check ---------------------------------------------------------------
// After each frame: the elements with a data-kind shown now, counted by kind against the
// cut's limits (layout.json story), and every text shown against its smallest size (the
// story's body text inside an element with a kind, min_text_px elsewhere and inside a
// note: data-kind "note" marks notes and sources, not counted). What is over is
// said in small text in the stage's top left corner, above layout.safe.

let story = null; // { limits, bodyPx, minPx, note, last }

function shownNow(el) {
  if (!el.checkVisibility({ opacityProperty: true, visibilityProperty: true })) return false;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}

function initStory(layout, name) {
  const st = storyOf(layout, name);
  const note = document.createElement("p");
  note.className = "story-over";
  const safe = layout.safe ?? { x: 0, y: 0 };
  Object.assign(note.style, { left: `${safe.x}px`, top: `${Math.max(0, safe.y - layout.min_text_px * 1.5)}px`, fontSize: `${layout.min_text_px}px`, display: "none" });
  $("stage").append(note);
  story = { limits: st.limits, bodyPx: st.bodyPx, minPx: layout.min_text_px, note, last: null };
}

function checkStory() {
  if (!story) return null;
  const stage = $("stage");
  const counts = Object.fromEntries(KINDS.map((k) => [k, 0]));
  for (const el of stage.querySelectorAll("[data-kind]")) if (KINDS.includes(el.dataset.kind) && shownNow(el)) counts[el.dataset.kind] += 1;
  const over = story.limits ? KINDS.filter((k) => counts[k] > story.limits[k]).map((k) => `${k} ${counts[k]} > ${story.limits[k]}`) : [];
  const small = [];
  for (const el of stage.querySelectorAll("*")) {
    if (el === story.note || el.classList.contains("err")) continue;
    if (![...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) continue;
    if (!shownNow(el)) continue;
    const px = parseFloat(getComputedStyle(el).fontSize);
    // notes and sources (data-kind "note", as promo/layout_check.py) are not body text
    const min = el.closest("[data-kind]") && !el.closest('[data-kind~="note"]') ? story.bodyPx : story.minPx;
    if (px < min - 0.01) small.push(`${el.textContent.trim().slice(0, 12)} ${px} px < ${min}`);
  }
  if (small.length) over.push(`字 ${small.join("、")}`);
  story.note.textContent = over.length ? `上限を越えた: ${over.join(" / ")}` : "";
  story.note.style.display = over.length ? "" : "none";
  document.documentElement.dataset.storyOver = over.length ? "1" : "0";
  story.last = { counts, over };
  return story.last;
}

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
  checkStory();
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
  // the cut's 3D views (workbench3d.js cutView), for the tests
  views: () => cutViews(),
  // what the story check found at the last frame drawn: { counts, over } (null before)
  story: () => story?.last ?? null,
};

async function main() {
  try {
    const name = params.get("cut") ?? "n05";
    if (!CUTS[name]) throw new Error(`no cut named ${name}`);
    const [layout, events] = await Promise.all([loadLayout(), readRun(params.get("run") ?? "sample")]);
    // the events too, in seq order, for the cuts that read them as they came (N09, N13, N14)
    cut = new CUTS[name](layout, stateOf(events), [...events].sort((a, b) => a.seq - b.seq));
    if (cut.loaded) await cut.loaded; // what a cut loads after it is built (N04 / N15: the pets)
    await looksReady(); // the real look's Poly Haven files, so a frame at ?t= is the same every time
    window.hiraku.cut = cut;
    if (!ONLY_HERO) initStory(layout, name);
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
        checkStory();
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
