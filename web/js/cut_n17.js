// N17: the report on one page. One line of result (the report's summary) over a long
// explanation per domain: the line of its verdicts, the terms that weigh most (the
// report's effects, as bars of their share), the sensitivities (K5, as bars of their
// elasticity) when the report has them, the assumptions and the mismatches on the way.
// At the bottom, "次に変えるなら" (the report's next); at times.light_s it lights up,
// with the strongest sensitivity bar. Every word and number comes from the newest
// report.written (state.report); positions, sizes, colours and times from layout.json
// n17. A missing key is named on the page.
//
// times: before lead_s the page is not shown; from light_s the next step is lit.
//
// The story (layout.json story): the next step is the one place to read. Its block is the
// cut's one tag and holds one change (the report's first next), its sentence large (as
// large as fits the page's width on one line, up to page.change_of_title x title_px)
// and its reason at the story's body text size; from light_s everything else on the page
// is drawn pale (layout.json n17.dim_opacity). Before / after figures of the change are not
// shown: the report's next holds only the change and its reason in words, and the
// effects and sensitivities hold the present values only (no value after the change), so
// a figure after the change would be a guess.

import { clearOfBand } from "./compose.js";
import { storyOf } from "./cut_n03.js";
import { DOMAIN_JA, esc, fmt, fmtVU, isYesNo, opLabel, plainName } from "./labels.js";

// The words of the viewers (the review of the first cuts: the page was mostly English
// internal names and test ids). A domain's line is made here from its verdicts (the
// report's verdict_ids): the quantity's plain name, the recorded value, the sign that
// holds between it and the limit, and the limit; no test id. The report's own line is
// used only when its verdicts are not in the record. Effects are named by EFFECT_JA (an
// effect it does not know is left out of the page; the report keeps it), assumptions
// are those written in Japanese (the tools write each also in English), and mismatches on
// the way are counted without their ids.
const EFFECT_JA = [
  [/^rolling$/, "転がる抵抗"],
  [/^climbing$/, "坂を登る"],
  [/^aero$/, "空気の抵抗"],
  [/^energy taken by the crush zone$/, "つぶれる所が吸うエネルギー"],
  [/^bending_vertical\b/, "上下の曲げ"],
  [/^bending_sideways\b/, "横の曲げ"],
  [/^bending\b/, "曲げ"],
  [/^torsion\b/, "ねじり"],
  [/^axial\b/, "引っぱり・押し"],
  [/^wall bending part\b/, "板の曲げの分"],
  [/^membrane part\b/, "板の伸び縮みの分"],
];
const RANGE = /^stress ranges (\d+(?:\.\d+)?)-(\d+(?:\.\d+)?|up) MPa$/;
export function effectName(name) {
  const n = String(name ?? "");
  if (n.startsWith("beam: ")) return null; // the beam's check of the same term, for the makers
  const r = RANGE.exec(n);
  if (r) return r[2] === "up" ? `応力の振れ幅 ${r[1]} MPa 以上` : `応力の振れ幅 ${r[1]}〜${r[2]} MPa`;
  for (const [re, ja] of EFFECT_JA) if (re.test(n)) return ja;
  return null;
}
const JAPANESE = /[぀-ヿ一-鿿]/;
const NOT_OP = { "<=": ">", "<": "≥", ">=": "<", ">": "≤" };

// One verdict in words: "<name> <value> <sign that holds> <limit>" (a yes-or-no quantity:
// "<name>: はい / いいえ").
export function verdictText(v) {
  if (!v?.value || !v.limit) return null;
  const name = plainName(v.quantity);
  if (isYesNo(v.quantity)) return `${name}: ${v.value.value >= 0.5 ? "はい" : "いいえ"}`;
  const a = v.value.value;
  const L = v.limit.value;
  const holds = { "<=": a <= L, "<": a < L, ">=": a >= L, ">": a > L }[v.op];
  if (holds === undefined || v.value.unit !== v.limit.unit) return `${name} ${fmtVU(a, v.value.unit, 4)}`;
  return `${name} ${fmtVU(a, v.value.unit, 4)} ${holds ? opLabel(v.op) : NOT_OP[v.op]} ${fmtVU(L, v.limit.unit, 4)}`;
}


const $ = (id) => document.getElementById(id);

// Every value the cut reads (nested ones by their path); the top's min_text_px too.
const N17_KEYS = [
  "duration_s",
  "dim_opacity",
  "page.x", "page.y", "page.w", "page.h", "page.title_px", "page.text_px", "page.change_of_title",
  "times.lead_s", "times.light_s",
  "colors.bg", "colors.panel", "colors.rule",
];

const get = (obj, path) => path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);

export function missingN17Keys(layout) {
  const out = N17_KEYS.filter((k) => get(layout.n17, k) === undefined);
  if (layout.min_text_px === undefined) out.push("min_text_px");
  return out;
}

const STATE_MARK = { green: ["ok", "✓"], red: ["miss", "✕"], pending: ["wait", "…"] };
const finite = (x) => typeof x === "number" && Number.isFinite(x);

// The strongest sensitivity term (largest |share|) of the report: in the first next
// step's domain when that domain has one, else over all domains. null when none.
export function strongestSensitivity(report) {
  const pick = (domains) => {
    let best = null;
    for (const d of domains) {
      (d.sensitivity ?? []).forEach((t, i) => {
        if (finite(t.share) && (!best || Math.abs(t.share) > Math.abs(best.term.share))) best = { domain: d.domain, index: i, term: t };
      });
    }
    return best;
  };
  const doms = report.domains ?? [];
  const first = report.next?.[0]?.domain;
  return pick(doms.filter((d) => d.domain === first)) ?? pick(doms);
}

// "max_stress to members.rail_R.section.t_mm" (K5's names) reads as the quantity's plain
// name and the design number in words: a part by its name in the design, a member as 部材
// (members have no names in the record), the number by what it is (板厚, 重さ, ...);
// no ids. A number it does not know keeps its last word.
const NUMBER_JA = { t_mm: "板厚", h_mm: "断面の高さ", w_mm: "断面の幅", d_mm: "直径", mass_kg: "重さ", length_mm: "長さ" };
function sensitivityName(name, names = {}) {
  const m = /^(\S+) to (.+)$/.exec(name ?? "");
  if (!m) return name ?? "";
  const path = m[2].split(".");
  const what = NUMBER_JA[path.at(-1)] ?? path.at(-1);
  const whose = path[0] === "parts" ? names[path[1]] ?? "部品" : path[0] === "members" ? "部材" : "";
  return `${plainName(m[1])} ← ${whose ? `${String(whose).replace(/\s*[（(].*$/, "")}の` : ""}${what}`;
}

export class CutN17 {
  constructor(layout, s) {
    const missing = missingN17Keys(layout);
    if (missing.length) throw new Error(`layout.json の n17 に無い: ${missing.join(", ")}`);
    this.layout = layout;
    this.spec = layout.n17;
    this.minPx = layout.min_text_px;
    this.story = storyOf(layout, "n17");
    this.report = s.report;
    this.s = s;
    if (!this.report) throw new Error("報告の記録がまだ無い (report.written)");
    this.stage = $("stage");
    this.stage.style.background = this.spec.colors.bg;
    this.build();
  }

  build() {
    const sp = this.spec;
    const P = sp.page;
    const rep = this.report;
    const el = document.createElement("div");
    el.className = "n17-page";
    el.dataset.box = "n17.page";
    Object.assign(el.style, {
      left: `${P.x}px`, top: `${P.y}px`, width: `${P.w}px`, height: `${P.h}px`,
      background: sp.colors.panel, borderColor: sp.colors.rule, padding: `${P.text_px}px ${P.text_px * 1.5}px`,
    });
    if (!clearOfBand(P, this.layout)) el.dataset.outside = "1";
    this.strongest = strongestSensitivity(rep);
    const chips = (rep.domains ?? [])
      .map((d) => {
        const [cls, mark] = STATE_MARK[d.state] ?? STATE_MARK.pending;
        return `<span class="${cls}">${mark} ${esc(DOMAIN_JA[d.domain] ?? d.domain)}</span>`;
      })
      .join("");
    const body = this.story.bodyPx;
    el.innerHTML =
      `<div class="n17-head" data-box="n17.result">` +
      `<div class="n17-kicker" style="font-size:${body}px">報告・設計の版 ${esc(String(rep.design_version))}</div>` +
      `<h2>${esc(rep.summary)}</h2>` +
      (chips ? `<div class="n17-chips" style="font-size:${body}px">${chips}</div>` : "") +
      `</div>` +
      `<div class="n17-body" data-box="n17.body">${(rep.domains ?? []).map((d) => this.domainHtml(d)).join("")}${this.uncheckedHtml()}</div>` +
      this.nextHtml();
    this.stage.append(el);
    this.el = el;
    // the change's sentence on one line across the next block, not below the body text size
    const change = el.querySelector(".n17-change");
    if (change) {
      const room = el.querySelector(".n17-next ul").clientWidth;
      let px = parseFloat(change.style.fontSize);
      change.style.whiteSpace = "nowrap";
      while (change.scrollWidth > room && px > this.story.bodyPx) {
        px -= 1;
        change.style.fontSize = `${px}px`;
      }
      change.style.whiteSpace = "";
    }
    this.fit();
  }

  domainHtml(d) {
    const [cls, mark] = STATE_MARK[d.state] ?? STATE_MARK.pending;
    const key = `n17.domain.${d.domain}`;
    const effects = (d.effects ?? []).filter((t) => finite(t.share) && effectName(t.name));
    const sens = d.sensitivity ?? [];
    // effects: the share of the total, a bar as long as the share
    const effRows = effects
      .map((t) => {
        const w = Math.max(0, Math.min(1, Math.abs(t.share))) * 100;
        const val = finite(t.value) ? ` <small>${esc(t.unit && t.unit !== "1" ? fmtVU(t.value, t.unit, 3) : fmt(t.value, 3))}</small>` : "";
        return `<div class="n17-bar"><span class="n17-name">${esc(effectName(t.name))}${val}</span><i><b style="width:${w.toFixed(1)}%"></b></i><span class="n17-num">${esc((t.share * 100).toFixed(0))} %</span></div>`;
      })
      .join("");
    // sensitivities: elasticity (1 % more of the number moves the quantity by share %); bars scaled to the largest
    const top = Math.max(0, ...sens.filter((t) => finite(t.share)).map((t) => Math.abs(t.share)));
    const senRows = sens
      .map((t, i) => {
        const strong = this.strongest && this.strongest.domain === d.domain && this.strongest.index === i;
        if (!finite(t.share)) return `<div class="n17-bar none"><span class="n17-name">${esc(sensitivityName(t.name, this.partNames()))}</span><i></i><span class="n17-num">—</span></div>`;
        const w = top > 0 ? (Math.abs(t.share) / top) * 50 : 0;
        const side = t.share >= 0 ? `left:50%;width:${w.toFixed(1)}%` : `left:${(50 - w).toFixed(1)}%;width:${w.toFixed(1)}%`;
        return (
          `<div class="n17-bar sens${strong ? " strongest" : ""}" data-box="${key}.sens.${i}"${strong ? ' data-strongest="1"' : ""}>` +
          `<span class="n17-name">${esc(sensitivityName(t.name, this.partNames()))}</span><i class="mid"><b style="${side}"></b></i>` +
          `<span class="n17-num">${t.share > 0 ? "+" : ""}${esc(fmt(t.share, 2))}</span></div>`
        );
      })
      .join("");
    // mismatches on the way: how many, and how many have their cause recorded (no ids)
    const ds = d.differences ?? [];
    const withCause = ds.filter((x) => x.cause_id).length;
    const diffs = ds.length ? `途中で手計算と食い違った ${ds.length} 件 (原因を書いたもの ${withCause} 件)` : "";
    const assumptions = (d.assumptions ?? []).filter((a) => JAPANESE.test(a));
    return (
      `<section class="n17-domain" data-box="${key}" data-domain="${esc(d.domain)}">` +
      `<h3 style="font-size:${this.story.bodyPx}px"><span class="${cls}">${mark} ${esc(DOMAIN_JA[d.domain] ?? d.domain)}</span> <span class="n17-line">${esc(this.lineOf(d))}</span></h3>` +
      `<div class="n17-detail" data-kind="note">` +
      (effRows ? `<div class="n17-group" data-box="${key}.effects"><h4>効いている項 (全体に占める割合)</h4>${effRows}</div>` : "") +
      (senRows ? `<div class="n17-group" data-box="${key}.sensitivity"><h4>感度 (その数を 1 % 変えると、合否の量が何 % 動くか)</h4>${senRows}</div>` : "") +
      (assumptions.length ? `<p class="n17-note">仮定: ${assumptions.map(esc).join("、")}</p>` : "") +
      (diffs ? `<p class="n17-note">${esc(diffs)}</p>` : "") +
      `</div></section>`
    );
  }

  // The design's parts by name (the report's version, else the newest design).
  partNames() {
    const d = this.s.designs?.[this.report.design_version] ?? this.s.designs?.[this.s.currentVersion];
    return Object.fromEntries((d?.parts ?? []).map((p) => [p.id, p.name ?? p.id]));
  }

  // A domain's line in words from its verdicts (verdictText), joined by " / "; the
  // report's own line when none of its verdicts is in the record.
  lineOf(d) {
    const parts = (d.verdict_ids ?? []).map((id) => verdictText(this.s.verdicts?.[id])).filter(Boolean);
    return parts.length ? parts.join(" / ") : d.line ?? "";
  }

  uncheckedHtml() {
    const u = this.report.unchecked ?? [];
    if (!u.length) return "";
    // the requirement's key (Q11) is the makers'; the item's words stay
    return `<p class="n17-note n17-unchecked" data-box="n17.unchecked" data-kind="note">出典を確かめていない値 ${u.length} 件: ${u.map((x) => esc(String(x.item ?? "").replace(/^Q\d+\s*/, ""))).join("、")}</p>`;
  }

  nextHtml() {
    const next = this.report.next ?? [];
    const n = next[0];
    const big = Math.round(this.spec.page.title_px * this.spec.page.change_of_title);
    const rows = n
      ? `<li><b>${esc(DOMAIN_JA[n.domain] ?? n.domain)}</b><span class="n17-change" style="font-size:${big}px">${esc(n.change)}</span><small>${esc(n.reason)}</small></li>`
      : `<li class="none">報告に「次」が書かれていない</li>`;
    const px = Math.max(this.spec.page.text_px, this.story.bodyPx);
    return `<div class="n17-next" data-box="n17.next" data-kind="label" style="font-size:${px}px"><h3>次に変えるなら</h3><ul>${rows}</ul></div>`;
  }

  // The text steps down a pixel at a time (title and the rest together, neither below
  // min_text_px) until the explanation ends inside the page. If it still does not fit at
  // the smallest size: the unchecked values are given by their count only; then the
  // domains other than the ones the next step names (and the strongest sensitivity's)
  // keep only their line, from the last up; then those lines are left out and named.
  fit() {
    const P = this.spec.page;
    const el = this.el;
    const body = el.querySelector(".n17-body");
    const over = () => body.scrollHeight > body.clientHeight + 0.5 || el.scrollHeight > el.clientHeight + 0.5;
    // the notes (data-kind "note": effects, assumptions, the unchecked values) step down to
    // min_text_px; what is read (the result, the chips, each domain's line) stays at the
    // story's body text size or more
    let px = Math.max(this.minPx, P.text_px);
    let title = Math.max(this.story.bodyPx, P.title_px);
    const apply = () => {
      el.style.fontSize = `${px}px`;
      el.querySelector(".n17-head h2").style.fontSize = `${title}px`;
    };
    apply();
    while (over() && (px > this.minPx || title > this.story.bodyPx)) {
      px = Math.max(this.minPx, px - 1);
      title = Math.max(this.story.bodyPx, title - 1);
      apply();
    }
    // then the list of values not checked keeps only its count
    const unchecked = body.querySelector(".n17-unchecked");
    if (over() && unchecked) {
      unchecked.textContent = `出典を確かめていない値 ${(this.report.unchecked ?? []).length} 件 (一覧は報告の記録に)`;
      el.dataset.uncheckedShort = "1";
    }
    const keep = new Set([...(this.report.next ?? []).map((n) => n.domain), this.strongest?.domain].filter(Boolean));
    const others = [...body.querySelectorAll(".n17-domain")].filter((s) => !keep.has(s.dataset.domain));
    const folded = [];
    for (const s of [...others].reverse()) {
      if (!over()) break;
      s.querySelector(".n17-detail")?.remove();
      folded.unshift(s.dataset.domain);
    }
    const dropped = [];
    const sections = [...others];
    while (over() && sections.length) {
      const s = sections.pop();
      dropped.unshift(s.dataset.domain);
      s.remove();
      let note = body.querySelector(".n17-dropped");
      if (!note) {
        note = document.createElement("p");
        note.className = "n17-note n17-dropped";
        note.dataset.kind = "note";
        body.prepend(note);
      }
      note.textContent = `入りきらない分野 (報告の記録にはある): ${dropped.map((d) => DOMAIN_JA[d] ?? d).join("、")}`;
    }
    // still over: the kept domains' notes (assumptions, mismatches) go, then their effects
    // (their sensitivities, the reason of the change, stay), then the change's sentence
    // steps down, not below page.title_px (it stays the largest text on the page)
    for (const n of [...body.querySelectorAll(".n17-domain .n17-note, .n17-domain [data-box$='.effects']")].reverse()) {
      if (!over()) break;
      n.remove();
    }
    const change = el.querySelector(".n17-change");
    let cp = change ? parseFloat(change.style.fontSize) : 0;
    const least = Math.max(this.story.bodyPx, this.spec.page.title_px);
    while (over() && change && cp > least) {
      cp -= 1;
      change.style.fontSize = `${cp}px`;
    }
    el.dataset.textPx = String(px);
    el.dataset.titlePx = String(title);
    if (folded.length) el.dataset.folded = folded.join(",");
    if (dropped.length) el.dataset.dropped = dropped.join(",");
  }

  resize() {}

  draw(t) {
    const T = this.spec.times;
    this.el.style.visibility = t >= T.lead_s ? "visible" : "hidden";
    const lit = t >= T.light_s;
    this.el.querySelector(".n17-next").classList.toggle("lit", lit);
    this.el.querySelector(".n17-bar.strongest")?.classList.toggle("lit", lit);
    for (const part of this.el.querySelectorAll(".n17-head, .n17-body")) part.style.opacity = lit ? String(this.spec.dim_opacity) : "";
    document.documentElement.dataset.n17Lit = lit ? "1" : "0";
  }
}
