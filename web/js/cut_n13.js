// N13 (cut.html?cut=n13&run=<id>[&tool=<name>]): a tool the shelf does not have is
// written by Claude during the run and checked against known answers; it ends on the shelf
// with the new card in it. What the analyses made with the tool judged is not shown (the
// cut stops before the tool is used).
// Drawn from the run's records: the tool (?tool=, else the last one made by Claude
// (tool.checked origin "claude") that is usable and that an analysis used), the tools on
// the shelf before it (the other usable tools checked before it, and the ones the run named
// as on the shelf but not checked: the system's agent.said "…確かめない道具: a、b" that
// hiraku/tools/shelf.py check_shelf writes) and its code (the run's tools/<name>-<n>.py,
// n = its attempt: the tool.checked events of that name up to the usable one).
//
// Four boxes of layout.json n13:
// - want: from times.lead_s the maker's want in one line (domains.picked want; its last
//   sentence when it has more than one: the wish, after the product's description).
// - shelf: from times.gap_s the shelf: a plain card for each tool already on it and a dotted
//   card for the missing one, with one line naming what is missing. The cards are in as few
//   rows as keep them readable. The dotted card carries the new tool's name as its tag (a
//   label, under it and flush with its right edge: cardName, e.g. "転倒の守りを確かめる道具");
//   the other cards have no names. From write_s the dotted card fills as the code is written,
//   and once every check has passed it is solid, shows "照合 ✓" and its tag turns green: the
//   green says the tool passed its checks, never that the design passed (the run's verdicts
//   are not shown). The story's label limit (4) counts the ticks, the tag and "照合 ✓"; when
//   they would be more, "照合 ✓" joins the tag as one label. The shelf stays so to the end
//   (times.shelf_s is the end of the ticks).
// - code: from times.write_s a few lines of the tool's code (from its analyse / run
//   method), typed in up to times.check_s; small, a glimpse (data-kind "note"); the caret
//   is what moves.
// - check: from times.check_s the known-answer checks (tool.checked checks): the check's
//   quantity in words, the known answer and the tool's, and a short source; each is ticked
//   (✓, or ✕ when it failed) one after another up to times.shelf_s.
// The ticks, the new card's name and its "照合 ✓" are labels (data-kind); no number is emphasised. No
// record id, test id or tool name is shown (the code is the tool's own text).
// Positions, sizes, colours, the length and the times come from layout.json n13; a missing
// key is named on the page.

import { requireKeys, storyOf } from "./cut_n03.js";
import { GREEN, MUTED, RED, panel, showFrom, spread, textPx, toolTitle } from "./cut_panels.js";
import { checkSourceShort, esc, plainName, plainToolName, plainValue } from "./labels.js";

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
// The shelf's cards: the widest, the narrowest that still reads as a card (fewer rows are
// used while the cards are at least this wide), the gaps, the height for a width, and the
// most rows.
const CARD_MAX_W = 140;
const CARD_MIN_W = 90;
const CARD_GAP_PX = 18;
const CARD_ASPECT = 1.36;
const MAX_ROWS = 4;
// The shelf box's padding sideways (as .cut-panel in cut.html) and its border.
const PAD_X_PX = 22;
const BORDER_PX = 2;

// The system's line naming the shelf's tools the run did not check (hiraku/tools/shelf.py).
const UNCHECKED_RE = /には使わないので確かめない道具: (.+)$/;

// The records N13 is drawn from (see the top).
export function n13Records(s, events) {
  const checked = events.filter((e) => e.kind === "tool.checked" && e.data?.name).sort((a, b) => a.seq - b.seq);
  const usedBy = (name) => s.order.analyses.map((id) => s.analyses[id]).filter((a) => a.tool === name && a.status === "ok");
  const made = checked.filter((e) => e.data.origin === "claude" && e.data.usable && usedBy(e.data.name).length);
  const ev = params.get("tool") ? checked.filter((e) => e.data.name === params.get("tool") && e.data.usable).at(-1) : made.at(-1);
  if (!ev) throw new Error(params.get("tool") ? `使える道具 ${params.get("tool")} の照合の記録が無い` : "Claude が作って解析に使った道具の記録が無い");
  const tool = ev.data;
  const attempt = checked.filter((e) => e.data.name === tool.name && e.seq <= ev.seq).length;
  const usable = checked.filter((e) => e.seq < ev.seq && e.data.usable).map((e) => e.data.name);
  const unchecked = events
    .filter((e) => e.kind === "agent.said" && e.agent === "system" && e.seq < ev.seq)
    .flatMap((e) => String(e.text ?? "").match(UNCHECKED_RE)?.[1].split("、").map((x) => x.trim()).filter(Boolean) ?? []);
  const before = [...new Set([...usable, ...unchecked])].filter((n) => n !== tool.name);
  return { tool, attempt, before };
}

// The last sentence of a want with more than one (the wish, after the product's description).
export function wantLine(want) {
  const parts = String(want ?? "").split("。").map((x) => x.trim()).filter(Boolean);
  return parts.length > 1 ? parts.at(-1) : String(want ?? "").trim();
}

// The new card's name: the viewers' name for the tool (labels.js), else the tool's title
// before any bracket, and when it says "<a>の<b>の<c>" the first part (the product's name,
// "介護ベッドの") left out: "使う回数の疲労".
export function cardName(tool) {
  const plain = plainToolName(tool?.name);
  if (plain) return plain;
  const t = toolTitle(tool) ?? "";
  const parts = t.split("の");
  return parts.length >= 3 ? parts.slice(1).join("の") : t;
}

// The gap's line: the viewers' name (a name of the tool, "…を確かめる道具") is not on the
// shelf, else what the title says has no tool to check it.
export function missingLine(tool) {
  const plain = plainToolName(tool?.name);
  return plain ? `点線: 「${plain}」が棚に無い` : `点線: 「${toolTitle(tool) ?? ""}」を確かめる道具が無い`;
}

// The new card's mark once every check has passed (green: the tool passed its checks, not
// the design).
export const CHECKED_MARK = "照合 ✓";

// The cards in rows: { rows, perRow, w, h }, the fewest rows whose cards are at least
// CARD_MIN_W wide and fit the height given, else the rows with the widest cards that fit.
export function cardRows(n, width, height) {
  const fit = [];
  for (let rows = 1; rows <= MAX_ROWS; rows++) {
    const perRow = Math.ceil(n / rows);
    const w = Math.min(CARD_MAX_W, Math.floor((width - (perRow - 1) * CARD_GAP_PX) / perRow));
    const h = Math.round(w * CARD_ASPECT);
    if (w > 0 && rows * h + (rows - 1) * CARD_GAP_PX <= height) fit.push({ rows, perRow, w, h });
  }
  if (!fit.length) return { rows: 1, perRow: n, w: Math.max(1, Math.floor((width - (n - 1) * CARD_GAP_PX) / n)), h: 1 };
  return fit.find((x) => x.w >= CARD_MIN_W) ?? fit.reduce((a, b) => (b.w > a.w ? b : a));
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

    // want
    const want = wantLine(this.s.intake?.picked?.want ?? this.s.project?.want ?? "");
    this.want = panel(this.stage, L, "n13", "want", null, st);
    this.want.insertAdjacentHTML("beforeend", `<p class="n13-want" style="font-size:${textPx(L, sp.want, st)}px">やりたいこと: ${esc(want)}</p>`);

    // shelf: the tools on it and the missing one
    const S = sp.shelf;
    const sx = textPx(L, S, st);
    this.shelf = panel(this.stage, L, "n13", "shelf", "道具の棚", st);
    const n = this.before.length + 1;
    // under the title: the cards, the new card's tag (a line and its gap) and one line of text
    const titlePx = Math.max(L.min_text_px, S.title_px, st.bodyPx);
    const room = S.h - 2 * BORDER_PX - 2 * PAD_Y_PX - titlePx * 1.6 - sx * 1.2 * 2 - 10 - 4 - sx * 1.6 - sx * 1.2;
    const fit = cardRows(n, S.w - 2 * BORDER_PX - 2 * PAD_X_PX, room);
    // the mark "照合 ✓" is a label of its own on the new card while the ticks, the name and it
    // keep within the story's label limit; else it joins the name (one label)
    const nChecks = (this.tool.checks ?? []).length;
    this.markJoined = nChecks + 2 > (st.limits?.label ?? Infinity);
    const card = (cls, inner = "") => `<i class="n13-card${cls}" style="width:${fit.w}px;height:${fit.h}px"><b></b>${inner}</i>`;
    const mark = this.markJoined ? "" : `<span class="n13-checked" data-kind="label" style="font-size:${sx}px">${CHECKED_MARK.replace(" ", "<br>")}</span>`;
    const joined = this.markJoined ? `<span class="n13-tag-mark"> ・ ${esc(CHECKED_MARK)}</span>` : "";
    const tag = `<span class="n13-tag" data-kind="label" style="font-size:${sx}px">${esc(cardName(this.tool))}${joined}</span>`;
    // the name may be wider than the card: its column keeps the card's width, the name reaches left
    const cards = this.before.map(() => card("")).join("") + `<span class="n13-newwrap" style="width:${fit.w}px">${card(" new", mark)}${tag}</span>`;
    const rowW = fit.perRow * fit.w + (fit.perRow - 1) * CARD_GAP_PX;
    this.shelf.insertAdjacentHTML(
      "beforeend",
      `<div class="n13-shelf"><div class="n13-cards" style="width:${rowW}px">${cards}</div>` +
        `<p class="n13-gap" style="font-size:${sx}px">${esc(missingLine(this.tool))}</p>` +
        `<p class="n13-made" style="font-size:${sx}px">Claude がその場で書き、答えの分かっている例で確かめた</p></div>`,
    );
    this.cardFit = fit;
    this.newCard = this.shelf.querySelector(".n13-card.new");
    this.fill = this.newCard.querySelector("b");
    this.tag = this.shelf.querySelector(".n13-tag");
    this.markEl = this.shelf.querySelector(".n13-checked") ?? this.shelf.querySelector(".n13-tag-mark");
    this.gapEl = this.shelf.querySelector(".n13-gap");
    this.madeEl = this.shelf.querySelector(".n13-made");

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
    this.shelf.style.visibility = t >= T.gap_s ? "visible" : "hidden";
    const typed = Math.max(0, Math.min(1, (t - T.write_s) / Math.max(1e-9, T.check_s - T.write_s)));
    const ticked = this.tickAt.filter((x) => t >= x).length;
    const solid = this.allPass && ticked >= this.rows.length;
    this.fill.style.height = `${(solid ? 1 : typed * 0.85) * 100}%`;
    this.newCard.dataset.state = solid ? "solid" : typed > 0 ? "writing" : "missing";
    this.newCard.style.borderStyle = solid ? "solid" : "dashed";
    this.tag.style.color = solid ? GREEN : RED;
    this.tag.style.borderColor = solid ? GREEN : RED;
    this.markEl.style.display = solid ? "" : "none";
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
    this.now = { t, typed, ticked, solid, codeLines: n };
  }
}
