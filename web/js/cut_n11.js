// N11 (cut.html?cut=n11&run=<id>): the hooks stop an analysis that has no prediction.
// Drawn from the run's records: the first hook.denied of HK1 (an analysis called without a
// prediction), the prediction the same agent then recorded, and the analysis that used it.
//
// Three boxes of layout.json n11:
// - result: the analysis's load case (its title), from times.plausible_s the vehicle on the
//   test in 3D, still, with the setting that came from the request as written (a load-case
//   parameter equal to a requirement, by its name: "荷の重心の高さ (荷台の床から) 62 mm");
//   from times.run_s the record's motion plays, squeezed, to the end. No value of the
//   analysis is shown (the comparison is N12's).
// - memo: the hand calculation, written line by line from times.memo_s to times.stop_s:
//   what it estimates and the prediction's inputs in words (an input without a name in
//   labels.js formulaInputName is left out). The predicted value comes when it is recorded.
// - log: the run's three steps, each at its time: at stop_s "✕ 解析を呼んだ → hooks が止めた:
//   <the rule's words>" (red), half way to run_s "予測を記録した: <quantity> <value>" (and the
//   memo's value), at run_s "✓ 予測があるので、解析が動いた" (green).
// The ✕ and ✓ are labels (data-kind), the 3D view is moving while it plays; no figure is
// emphasised. Words follow labels.js; no record id, rule id or tool name is shown.
// Positions, sizes, colours, the length and the times come from layout.json n11; a missing
// key is named on the page.

import * as THREE from "three";
import { squeeze } from "./compose.js";
import { requireKeys, storyOf } from "./cut_n03.js";
import { GREEN, PANEL_BORDER_PX, RED, panel, requestedSetting, showFrom, spread, textPx } from "./cut_panels.js";
import { esc, fmtVU, formulaInputName, quantityName, ruleOf } from "./labels.js";
import { motionOf } from "./series.js";
import { VIEWS, Workbench3D } from "./workbench3d.js";

const BOX = ["x", "y", "w", "h", "title_px", "text_px"];
export const N11_KEYS = [
  "duration_s",
  ...["memo", "log", "result"].flatMap((b) => BOX.map((k) => `${b}.${k}`)),
  "times.lead_s", "times.plausible_s", "times.memo_s", "times.stop_s", "times.run_s",
  "colors.bg", "colors.panel", "colors.rule",
];

// The records N11 is drawn from: the first HK1 denial, the next prediction of the same
// agent, the first analysis that used it and their load case.
export function n11Records(s) {
  const denied = s.hooks.denied.find((d) => d.rule === "HK1");
  if (!denied) throw new Error("予測が無くて止められた記録 (hooks の HK1) が無い");
  const preds = s.order.predictions.map((id) => s.predictions[id]);
  const pred = preds.find((p) => (s.firstSeqOf[p.prediction_id] ?? -1) > denied.seq && (!denied.agent || !p.agent || p.agent === denied.agent));
  if (!pred) throw new Error("止められたあとの予測の記録が無い");
  const ana = s.order.analyses.map((id) => s.analyses[id]).find((a) => (a.prediction_ids ?? []).includes(pred.prediction_id)) ?? null;
  const lc = (s.project?.load_cases ?? []).find((c) => c.load_case_id === pred.load_case_id) ?? null;
  return { denied, pred, ana, lc };
}

export class CutN11 {
  constructor(layout, s) {
    this.spec = requireKeys(layout, "n11", N11_KEYS);
    this.layout = layout;
    this.s = s;
    this.story = storyOf(layout, "n11");
    Object.assign(this, n11Records(s));
    this.stage = document.getElementById("stage");
    this.stage.style.background = this.spec.colors.bg;
    this.scale = 1;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(1);
    this.build();
  }

  build() {
    const L = this.layout;
    const sp = this.spec;
    const T = sp.times;
    const st = this.story;

    // result: the load case, the vehicle on its test and the setting from the request
    const R = sp.result;
    this.result = panel(this.stage, L, "n11", "result", this.lc?.title ?? quantityName(this.pred.quantity), st);
    const setting = requestedSetting(this.s, this.lc);
    this.inner = { w: R.w - 2 * PANEL_BORDER_PX, h: R.h - 2 * PANEL_BORDER_PX };
    this.result.insertAdjacentHTML(
      "beforeend",
      `<canvas class="n11-view"></canvas>` +
        (setting ? `<p class="n11-setting" style="font-size:${textPx(L, R, st)}px">設定 (要件から): ${esc(setting.name ?? "")} ${esc(fmtVU(setting.value, setting.unit, 4))}</p>` : ""),
    );
    this.canvas = this.result.querySelector("canvas");
    this.setting = this.result.querySelector(".n11-setting");
    this.view = this.ana ? this.makeView() : null;

    // memo: what is estimated, the inputs, then the value when it is recorded
    const M = sp.memo;
    this.memo = panel(this.stage, L, "n11", "memo", "手計算のメモ", st);
    const px = textPx(L, M, st);
    const rows = Object.entries(this.pred.inputs ?? {})
      .map(([k, q]) => ({ name: formulaInputName(this.pred.formula_id, k), q }))
      .filter((x) => x.name && x.q && typeof x.q.value === "number");
    const lines = [`<p class="n11-what">${esc(quantityName(this.pred.quantity))}を、解析の前に見積もる</p>`, ...rows.map((x) => `<p class="n11-in"><span>${esc(x.name)}</span><span>${esc(fmtVU(x.q.value, x.q.unit, 4))}</span></p>`)];
    this.memo.insertAdjacentHTML("beforeend", `<div class="n11-lines" style="font-size:${px}px">${lines.join("")}</div>` +
      `<p class="n11-value" style="font-size:${px}px">→ ${esc(quantityName(this.pred.quantity))} ${esc(fmtVU(this.pred.value, this.pred.unit, 3))}</p>`);
    this.memoLines = [...this.memo.querySelectorAll(".n11-lines p")];
    this.memoAt = spread(T.memo_s, T.stop_s, this.memoLines.length);
    this.memoValue = this.memo.querySelector(".n11-value");

    // log: the stop, the prediction recorded, the analysis run
    const G = sp.log;
    this.log = panel(this.stage, L, "n11", "log", "HIRAKU の記録", st);
    this.recordAt = (T.stop_s + T.run_s) / 2;
    const rule = ruleOf(this.denied.rule);
    const steps = [
      { at: T.stop_s, html: `<span class="mark" data-kind="label" style="color:${RED}">✕</span> 解析を呼んだ → hooks が止めた: ${esc(rule.ja)}`, tone: "stop" },
      { at: this.recordAt, html: `予測を記録した: ${esc(quantityName(this.pred.quantity))} ${esc(fmtVU(this.pred.value, this.pred.unit, 3))}`, tone: "pred" },
      { at: T.run_s, html: `<span class="mark" data-kind="label" style="color:${GREEN}">✓</span> 予測があるので、解析が動いた`, tone: "run" },
    ];
    this.log.insertAdjacentHTML("beforeend", `<div class="n11-log" style="font-size:${textPx(L, G, st)}px">${steps.map((x) => `<p data-step="${x.tone}">${x.html}</p>`).join("")}</div>`);
    this.steps = steps.map((x, i) => ({ ...x, el: this.log.querySelectorAll(".n11-log p")[i] }));
  }

  makeView() {
    const v = new Workbench3D(null, "zu", { renderer: this.renderer });
    v.setDesign(this.s.designs[this.ana.design_version] ?? this.s.designs[this.s.currentVersion]);
    v.setLoading(this.lc?.params?.loading ?? null);
    this.motion = motionOf(this.ana);
    v.setMotion(this.motion);
    v.setScene(this.lc, this.ana);
    v.setContour(null);
    v.setVectors(null);
    v.setToggles({ parts: true, contour: false, arrows: false, cg: true, names: false, limit: false });
    v.resize(this.inner.w, this.inner.h);
    const pts = v.contentPoints();
    for (const o of v.sceneDesc?.objects ?? []) if (o.size_mm) pts.push(new THREE.Vector3(o.position_mm[0], o.position_mm[1] - o.size_mm[1] / 2, o.position_mm[2]), new THREE.Vector3(o.position_mm[0], o.position_mm[1] + o.size_mm[1] / 2, o.position_mm[2]));
    v.fitPoints(pts, VIEWS[v.view ?? "default"], 1.1);
    return v;
  }

  resize(scale) {
    this.scale = scale;
    this.renderer.setSize(Math.ceil(this.inner.w * scale), Math.ceil(this.inner.h * scale), false);
    this.canvas.width = Math.round(this.inner.w * scale);
    this.canvas.height = Math.round(this.inner.h * scale);
  }

  draw(t) {
    const sp = this.spec;
    const T = sp.times;
    // result: shown from plausible_s, plays from run_s
    const shown = showFrom(this.canvas, t, T.plausible_s);
    if (this.setting) showFrom(this.setting, t, T.plausible_s);
    const plays = !!this.motion && t >= T.run_s && t < sp.duration_s;
    if (plays) this.canvas.dataset.kind = "moving";
    else delete this.canvas.dataset.kind;
    if (this.view && shown) {
      if (this.motion) {
        const { tau } = squeeze(t, T.run_s, sp.duration_s, 0, this.motion.duration);
        this.view.setFrame(this.motion.frame(Math.min(this.motion.t1, this.motion.t0 + tau)));
      }
      this.view.renderInto(this.canvas.getContext("2d"), this.canvas.width, this.canvas.height, this.scale);
    }
    // memo: line by line, the value once recorded
    this.memoLines.forEach((el, i) => showFrom(el, t, this.memoAt[i]));
    showFrom(this.memoValue, t, this.recordAt);
    // log: each step at its time
    for (const x of this.steps) showFrom(x.el, t, x.at);
    this.now = { t, plays, lines: this.memoLines.filter((el) => el.style.visibility === "visible").length, steps: this.steps.filter((x) => t >= x.at).map((x) => x.tone) };
  }
}
