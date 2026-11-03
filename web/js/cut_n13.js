// N13 (cut.html?cut=n13&run=<id>[&tool=<name>]): a tool the shelf does not have is
// written by Claude during the run, checked against known answers, and then used.
// Drawn from the run's records: the tool (?tool=, else the last one made by Claude
// (tool.checked origin "claude") that is usable and that an analysis used), the tools on
// the shelf before it (the other usable tools checked before it), its code (the run's
// tools/<name>-<n>.py, n = its attempt: the tool.checked events of that name up to the
// usable one) and the newest ok analysis made with it.
//
// Four boxes of layout.json n13:
// - want: from times.lead_s the maker's want in one line (domains.picked want).
// - shelf: from times.gap_s the shelf: a plain card for each tool already on it and a dotted
//   card for the missing one, with one line naming what is missing (the tool's title before
//   any bracket). The dotted card carries the new tool's name as its tag (a label, under it
//   and flush with its right edge: cardName, the title without the product's name before
//   it); the other cards have no names (the story's label limit 4: the three ticks and this
//   name). From write_s the dotted card fills as the code is written, and it is solid (its
//   tag green) once every check has passed. From times.shelf_s the box shows the analysis made with
//   the tool instead: one figure (its count of uses, cycles_counted, when it has one) and
//   what the run judged of it ("壊れる目安より小さい" when its verdicts pass).
// - code: from times.write_s a few lines of the tool's code (from its analyse / run
//   method), typed in up to times.check_s; small, a glimpse (data-kind "note"); the caret
//   is what moves.
// - check: from times.check_s the known-answer checks (tool.checked checks): the check's
//   quantity in words, the known answer and the tool's, and a short source; each is ticked
//   (✓, or ✕ when it failed) one after another up to times.shelf_s.
// The ticks and the new card's name are labels (data-kind), the analysis's figure a number. No record id, test id
// or tool name is shown (the code is the tool's own text).
// Positions, sizes, colours, the length and the times come from layout.json n13; a missing
// key is named on the page.

import { requireKeys, storyOf } from "./cut_n03.js";
import { GREEN, MUTED, RED, panel, showFrom, spread, textPx, toolTitle } from "./cut_panels.js";
import { checkSourceShort, esc, plainName, plainValue } from "./labels.js";

const params = new URLSearchParams(location.search);

export const N13_KEYS = [
  "duration_s",
  "want.x", "want.y", "want.w", "want.h", "want.text_px",
  "shelf.x", "shelf.y", "shelf.w", "shelf.h", "shelf.title_px", "shelf.text_px",
  "code.x", "code.y", "code.w", "code.h", "code.text_px",
  "check.x", "check.y", "check.w", "check.h", "check.title_px", "check.text_px",
  "times.lead_s", "times.gap_s", "times.write_s", "times.check_s", "times.shelf_s",
  "colors.bg", "colors.panel", "colors.rule",
];

// How many lines of code are shown when the box's height is not given.
const CODE_LINES = 10;
// The code box's padding (top and bottom, as .cut-panel in cut.html) and line height.
const PAD_Y_PX = 16;
const CODE_LINE_HEIGHT = 1.25;

// The records N13 is drawn from (see the top).
export function n13Records(s, events) {
  const checked = events.filter((e) => e.kind === "tool.checked" && e.data?.name).sort((a, b) => a.seq - b.seq);
  const usedBy = (name) => s.order.analyses.map((id) => s.analyses[id]).filter((a) => a.tool === name && a.status === "ok");
  const made = checked.filter((e) => e.data.origin === "claude" && e.data.usable && usedBy(e.data.name).length);
  const ev = params.get("tool") ? checked.filter((e) => e.data.name === params.get("tool") && e.data.usable).at(-1) : made.at(-1);
  if (!ev) throw new Error(params.get("tool") ? `使える道具 ${params.get("tool")} の照合の記録が無い` : "Claude が作って解析に使った道具の記録が無い");
  const tool = ev.data;
  const attempt = checked.filter((e) => e.data.name === tool.name && e.seq <= ev.seq).length;
  const before = [...new Set(checked.filter((e) => e.seq < ev.seq && e.data.usable && e.data.name !== tool.name).map((e) => e.data.name))];
  const ana = usedBy(tool.name).at(-1) ?? null;
  return { tool, attempt, before, ana };
}

// The new card's name: the tool's title before any bracket, and when it says "<a>の<b>の<c>"
// the first part (the product's name, "介護ベッドの") left out: "使う回数の疲労".
export function cardName(tool) {
  const t = toolTitle(tool) ?? "";
  const parts = t.split("の");
  return parts.length >= 3 ? parts.slice(1).join("の") : t;
}

// A few lines of a tool's code: from its analyse (or run) method, else from the top.
export function codeGlimpse(text, n = CODE_LINES) {
  const lines = String(text ?? "").replace(/\r\n?/g, "\n").split("\n");
  const at = lines.findIndex((l) => /^\s*def (analyse|analyze|run)\(/.test(l));
  return lines.slice(Math.max(0, at), Math.max(0, at) + n);
}

export class CutN13 {
  constructor(layout, s, events = []) {
    this.spec = requireKeys(layout, "n13", N13_KEYS);
    this.layout = layout;
    this.s = s;
    this.story = storyOf(layout, "n13");
    Object.assign(this, n13Records(s, events));
    this.stage = document.getElementById("stage");
    this.stage.style.background = this.spec.colors.bg;
    this.build();
    this.loaded = this.loadCode();
  }

  build() {
    const L = this.layout;
    const sp = this.spec;
    const st = this.story;
    const name = toolTitle(this.tool) ?? "";

    // want
    const want = this.s.intake?.picked?.want ?? this.s.project?.want ?? "";
    this.want = panel(this.stage, L, "n13", "want", null, st);
    this.want.insertAdjacentHTML("beforeend", `<p class="n13-want" style="font-size:${textPx(L, sp.want, st)}px">やりたいこと: ${esc(want)}</p>`);

    // shelf: the tools on it and the missing one; then the analysis made with the new tool
    const S = sp.shelf;
    const sx = textPx(L, S, st);
    this.shelf = panel(this.stage, L, "n13", "shelf", "道具の棚", st);
    // one row of cards across the box: the tools on the shelf and the missing one
    const gap = 18;
    const n = this.before.length + 1;
    const cw = Math.min(140, Math.floor((S.w - 2 * 2 - 2 * 22 - (n - 1) * gap) / n));
    const card = (cls) => `<i class="n13-card${cls}" style="width:${cw}px;height:${Math.round(cw * 1.36)}px"><b></b></i>`;
    const tag = `<span class="n13-tag" data-kind="label" style="font-size:${sx}px">${esc(cardName(this.tool))}</span>`;
    // the name may be wider than the card: its column keeps the card's width, the name reaches left
    const cards = this.before.map(() => card("")).join("") + `<span class="n13-newwrap" style="width:${cw}px">${card(" new")}${tag}</span>`;
    this.shelf.insertAdjacentHTML(
      "beforeend",
      `<div class="n13-shelf"><div class="n13-cards">${cards}</div>` +
        `<p class="n13-gap" style="font-size:${sx}px">点線: 「${esc(name)}」を確かめる道具が無い</p>` +
        `<p class="n13-made" style="font-size:${sx}px">Claude がその場で書き、答えの分かっている例で確かめた</p></div>`,
    );
    this.shelfBody = this.shelf.querySelector(".n13-shelf");
    this.newCard = this.shelf.querySelector(".n13-card.new");
    this.fill = this.newCard.querySelector("b");
    this.tag = this.shelf.querySelector(".n13-tag");
    this.gapEl = this.shelf.querySelector(".n13-gap");
    this.madeEl = this.shelf.querySelector(".n13-made");
    this.useEl = this.buildUse(name, sx);

    // code: filled in by loadCode
    const C = sp.code;
    this.code = panel(this.stage, L, "n13", "code", null, st);
    const cpx = Math.max(L.min_text_px, C.text_px);
    this.code.insertAdjacentHTML("beforeend", `<p class="n13-code-head" data-kind="note" style="font-size:${cpx}px">Claude が書いたコード (一部)</p><pre class="n13-lines" data-kind="note" style="font-size:${cpx}px"></pre><i class="n13-caret"></i>`);
    this.pre = this.code.querySelector("pre");
    this.caret = this.code.querySelector(".n13-caret");
    this.codeLines = [];

    // check: the known answers, ticked one by one
    const K = sp.check;
    const kx = textPx(L, K, st);
    this.check = panel(this.stage, L, "n13", "check", "答えの分かっている例で確かめる", st);
    const rows = (this.tool.checks ?? []).map((c) => {
      const src = checkSourceShort(c.source);
      return (
        `<div class="n13-row" data-pass="${c.pass ? "1" : "0"}">` +
        `<p class="n13-row-name"><span>${esc(plainName(c.quantity))}</span><span class="mark" data-kind="label" style="color:${c.pass ? GREEN : RED}">${c.pass ? "✓" : "✕"}</span></p>` +
        `<p class="n13-row-val">答え ${esc(plainValue(c.quantity, c.expected, c.unit, 4))} ・ 道具 ${esc(plainValue(c.quantity, c.got, c.unit, 4))}</p>` +
        (src ? `<p class="n13-src" data-kind="note" style="font-size:${L.min_text_px}px">出典: ${esc(src)}</p>` : "") +
        `</div>`
      );
    });
    this.check.insertAdjacentHTML("beforeend", `<div class="n13-rows" style="font-size:${kx}px">${rows.join("")}</div>`);
    this.rows = [...this.check.querySelectorAll(".n13-row")].map((el) => ({ el, mark: el.querySelector(".mark") }));
    const T = sp.times;
    this.tickAt = spread(T.check_s, T.shelf_s, this.rows.length + 1).slice(1);
    this.allPass = (this.tool.checks ?? []).length > 0 && this.tool.checks.every((c) => c.pass);
  }

  // The analysis made with the tool: its count of uses (one figure) and what the run judged.
  buildUse(name, px) {
    const ana = this.ana;
    const el = document.createElement("div");
    el.className = "n13-use";
    if (!ana) {
      el.innerHTML = `<p style="font-size:${px}px">この道具を使った解析の記録が無い</p>`;
      this.shelf.append(el);
      return el;
    }
    const n = (ana.results ?? []).find((r) => r.quantity === "cycles_counted");
    const verdicts = Object.values(this.s.verdicts).filter((v) => this.s.comparisons[v.comparison_id]?.analysis_id === ana.analysis_id);
    const passes = verdicts.length ? verdicts.every((v) => v.pass) : null;
    const judged = verdicts[0];
    const big = Math.round(px * 3.5);
    el.innerHTML =
      `<p class="n13-use-head" style="font-size:${px}px">棚に入った「${esc(name)}」で、設計を解析した</p>` +
      (n ? `<p class="n13-use-num" style="font-size:${px}px">${esc(plainName(n.quantity))}<br><b data-kind="number" style="font-size:${big}px">${esc(plainValue(n.quantity, n.value, n.unit, 4))}</b></p>` : "") +
      (passes === null ? "" : `<p class="n13-use-verdict" style="font-size:${px}px;color:${passes ? GREEN : RED}">${esc(plainName(judged.quantity))}は、${passes ? "壊れる目安より小さい" : "壊れる目安を越えた"}</p>`);
    el.dataset.pass = passes === null ? "" : passes ? "1" : "0";
    this.shelf.append(el);
    return el;
  }

  async loadCode() {
    const run = params.get("run") ?? "";
    try {
      const res = await fetch(`/api/runs/${encodeURIComponent(run)}/tools/${encodeURIComponent(this.tool.name)}-${this.attempt}.py`, { cache: "no-store" });
      if (!res.ok) throw new Error(String(res.status));
      // as many lines as the box holds under its head
      const C = this.spec.code;
      const px = Math.max(this.layout.min_text_px, C.text_px);
      const room = C.h - 2 * 2 - 2 * PAD_Y_PX - px * (1.3 + 0.3);
      this.codeLines = codeGlimpse(await res.text(), Math.max(1, Math.floor(room / (px * CODE_LINE_HEIGHT))));
    } catch {
      this.codeLines = [];
    }
    if (!this.codeLines.length) this.code.insertAdjacentHTML("beforeend", `<p class="n13-none" data-kind="note" style="font-size:${this.layout.min_text_px}px">道具のコードのファイルが無い</p>`);
  }

  resize() {}

  draw(t) {
    const sp = this.spec;
    const T = sp.times;
    showFrom(this.want, t, T.lead_s);
    // shelf until shelf_s, then the analysis
    const using = t >= T.shelf_s;
    this.shelf.style.visibility = t >= T.gap_s ? "visible" : "hidden";
    this.shelfBody.style.display = using ? "none" : "";
    this.useEl.style.display = using ? "" : "none";
    const typed = Math.max(0, Math.min(1, (t - T.write_s) / Math.max(1e-9, T.check_s - T.write_s)));
    const ticked = this.tickAt.filter((x) => t >= x).length;
    const solid = this.allPass && ticked >= this.rows.length;
    this.fill.style.height = `${(solid ? 1 : typed * 0.85) * 100}%`;
    this.newCard.dataset.state = solid ? "solid" : typed > 0 ? "writing" : "missing";
    this.newCard.style.borderStyle = solid ? "solid" : "dashed";
    this.tag.style.color = solid ? GREEN : RED;
    this.tag.style.borderColor = solid ? GREEN : RED;
    this.gapEl.style.display = solid ? "none" : "";
    this.madeEl.style.display = solid ? "" : "none";
    // code: typed line by line from write_s to check_s; the caret moves while it types
    this.code.style.visibility = t >= T.write_s ? "visible" : "hidden";
    const n = Math.ceil(typed * this.codeLines.length);
    this.pre.textContent = this.codeLines.slice(0, n).join("\n");
    const typing = t >= T.write_s && typed < 1 && this.codeLines.length > 0;
    this.caret.style.display = typing ? "" : "none";
    if (typing) this.caret.dataset.kind = "moving";
    else delete this.caret.dataset.kind;
    // check: the rows from check_s, ticked one by one
    this.check.style.visibility = t >= T.check_s ? "visible" : "hidden";
    this.rows.forEach((r, i) => {
      r.mark.style.visibility = i < ticked && t >= T.check_s ? "visible" : "hidden";
      r.el.style.color = i < ticked ? "#1b1a17" : MUTED;
    });
    this.now = { t, using, typed, ticked, solid, codeLines: n };
  }
}
