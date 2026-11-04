// N12 (cut.html?cut=n12&run=<id>[&cause=<cause id>]): the hand calculation and the
// analysis disagree, the cause is found (a unit mistaken in the request), and once it is
// fixed they agree. Drawn from the run's records: the cause (?cause=, else the first one
// that a later comparison resolved, else the first), the comparison it explains (the
// mismatch) and the comparison that resolved it (resolved_by).
//
// Two boxes of layout.json n12:
// - table: the compared quantity, then the hand calculation's value and the analysis's,
//   large (the story's two numbers), and under them the difference and the allowed range
//   with "✕ 食い違い" (red, from times.mismatch_s; neutral before). From times.fixed_s the
//   comparison that resolved the cause: its values, "✓ 一致" in green.
// - cause: from times.cause_s the cause in one line. When the cause's text says
//   "<a> cm のつもりが <b> mm" and a setting of the load case came from the request as
//   written, the line is "<the requirement's name>: <a> cm のつもりが <b> mm と書かれていた";
//   otherwise the cause's first sentence with requirement ids taken out. From fixed_s the
//   fix's first sentence and "✓ OK" (when the record says resolved).
// The ✕ / ✓ are labels (data-kind), the two values numbers. No record id is shown.
// Positions, sizes, colours, the length and the times come from layout.json n12; a missing
// key is named on the page.

import { requireKeys, storyOf } from "./cut_n03.js";
import { GREEN, MUTED, RED, panel, requestedSetting, textPx } from "./cut_panels.js";
import { OK_WORD, esc, fmtPct, fmtQ, fmtSigned, quantityName } from "./labels.js";

const params = new URLSearchParams(location.search);

export const N12_KEYS = [
  "duration_s",
  "table.x", "table.y", "table.w", "table.h", "table.title_px", "table.text_px", "table.value_px",
  "cause.x", "cause.y", "cause.w", "cause.h", "cause.title_px", "cause.text_px",
  "times.lead_s", "times.mismatch_s", "times.cause_s", "times.fixed_s",
  "colors.bg", "colors.panel", "colors.rule",
];

const firstSentence = (s) => String(s ?? "").split("。")[0].trim();
const withoutIds = (s) => s.replace(/要件\s*Q\d+\s*/g, "").replace(/\bQ\d+\s*/g, "").trim();

// The cause in one line for viewers (see the top).
export function causeLine(s, cause, lc) {
  const m = /(\d+(?:\.\d+)?)\s*cm\s*のつもりが\s*(\d+(?:\.\d+)?)\s*mm/.exec(cause?.cause ?? "");
  const setting = requestedSetting(s, lc);
  if (m && setting?.name && setting.unit === "mm" && Number(m[2]) === setting.value) return `${setting.name}: ${m[1]} cm のつもりが ${m[2]} mm と書かれていた`;
  return withoutIds(firstSentence(cause?.cause));
}

export function n12Records(s) {
  const causes = s.order.causes.map((id) => s.causes[id]);
  const cause = (params.get("cause") ? s.causes[params.get("cause")] : null) ?? causes.find((c) => c.resolved_by) ?? causes[0];
  if (!cause) throw new Error("食い違いの原因の記録が無い");
  const bad = s.comparisons[cause.comparison_id];
  if (!bad) throw new Error("原因が説明する照合の記録が無い");
  const fixed = cause.resolved_by ? s.comparisons[cause.resolved_by] ?? null : null;
  const ana = s.analyses[bad.analysis_id];
  const lc = (s.project?.load_cases ?? []).find((c) => c.load_case_id === ana?.load_case_id) ?? null;
  return { cause, bad, fixed, lc };
}

export class CutN12 {
  constructor(layout, s) {
    this.spec = requireKeys(layout, "n12", N12_KEYS);
    this.layout = layout;
    this.s = s;
    this.story = storyOf(layout, "n12");
    Object.assign(this, n12Records(s));
    this.stage = document.getElementById("stage");
    this.stage.style.background = this.spec.colors.bg;
    this.build();
  }

  build() {
    const L = this.layout;
    const sp = this.spec;
    const st = this.story;
    const Tb = sp.table;
    const px = textPx(L, Tb, st);
    const big = Math.max(Tb.value_px, st.bodyPx);
    this.table = panel(this.stage, L, "n12", "table", `手計算と解析を照らし合わせる: ${quantityName(this.bad.quantity)}`, st);
    this.table.insertAdjacentHTML(
      "beforeend",
      `<div class="n12-cols" style="font-size:${px}px">` +
        `<div class="n12-col"><p>手計算 (先に書いた予測)</p><b class="n12-num pred" data-kind="number" style="font-size:${big}px"></b><small class="n12-again"></small></div>` +
        `<div class="n12-col"><p>解析</p><b class="n12-num ana" data-kind="number" style="font-size:${big}px"></b><small class="n12-pad"></small></div>` + // the pad keeps the two values level
        `</div>` +
        `<p class="n12-diff" style="font-size:${px}px"><span class="diff"></span> <span class="mark" data-kind="label"></span></p>`,
    );
    this.el = {
      pred: this.table.querySelector(".n12-num.pred"),
      ana: this.table.querySelector(".n12-num.ana"),
      again: this.table.querySelector(".n12-again"),
      diff: this.table.querySelector(".n12-diff .diff"),
      mark: this.table.querySelector(".n12-diff .mark"),
    };
    const C = sp.cause;
    const cx = textPx(L, C, st);
    this.causeBox = panel(this.stage, L, "n12", "cause", "原因", st);
    const fix = withoutIds(firstSentence(this.cause.fix));
    this.causeBox.insertAdjacentHTML(
      "beforeend",
      `<p class="n12-why" style="font-size:${cx}px">${esc(causeLine(this.s, this.cause, this.lc))}</p>` +
        (this.fixed && fix ? `<p class="n12-fix" style="font-size:${cx}px">直し方: ${esc(fix)}${this.cause.resolved ? ` <span class="mark" data-kind="label" style="color:${GREEN}">${OK_WORD}</span>` : ""}</p>` : ""),
    );
    this.causeEl = this.causeBox.querySelector(".n12-why");
    this.fixEl = this.causeBox.querySelector(".n12-fix");
  }

  resize() {}

  draw(t) {
    const T = this.spec.times;
    const fixed = !!this.fixed && t >= T.fixed_s;
    const c = fixed ? this.fixed : this.bad;
    const judged = t >= T.mismatch_s;
    const ok = c.status === "match";
    this.el.pred.textContent = fmtQ(c.predicted, 3);
    this.el.ana.textContent = fmtQ(c.analyzed, 3);
    this.el.again.textContent = fixed && c.prediction_id !== this.bad.prediction_id ? "直してから予測し直した" : "";
    this.el.diff.textContent = judged ? `差 ${fmtSigned(c.diff_rel)} (許す幅 ±${fmtPct(c.tolerance_rel, 0)})` : "";
    this.el.mark.textContent = judged ? (ok ? "✓ 一致" : "✕ 食い違い") : "";
    this.el.mark.style.display = judged ? "" : "none";
    const tone = judged ? (ok ? GREEN : RED) : MUTED;
    this.el.mark.style.color = tone;
    for (const n of [this.el.pred, this.el.ana]) n.style.color = judged ? tone : "#1b1a17";
    this.table.dataset.state = judged ? (ok ? "match" : "mismatch") : "";
    this.table.style.borderColor = judged ? tone : this.spec.colors.rule;
    this.causeEl.style.visibility = t >= T.cause_s ? "visible" : "hidden";
    if (this.fixEl) this.fixEl.style.visibility = fixed ? "visible" : "hidden";
    this.now = { t, fixed, judged, comparison: c.comparison_id };
  }
}
