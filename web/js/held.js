// Load cases shown, not judged: a load case with no criterion gets no verdict, so its domain's
// tile can be green while one of its quantities says something. Each predicted quantity of such
// a load case (the quantities come from the record's predictions of that load case and version;
// nothing is guessed) is put in one of four kinds by NO_CRITERION_KINDS below:
//
//   held      保留          a count of what is missing (unit 1, the name a count: COUNT_NAME),
//                           not 0. The orange band (tiles.js, the workbench in app.js).
//   relay     渡すだけ       the load case hands its results to others (load_case.feeds).
//   compare   比べるだけ     a quantity only compared with its prediction (S5's mean uphill load).
//   reference 参考           anything else.
//
// The last three are not coloured (not green, red or orange): the tiles list them as 参考
// (判定しない) rows. A count of 0 is not held (nothing missing) and not shown.

import { DOMAIN_JA, esc, fmt, fmtVU, quantityName } from "./labels.js";

// A quantity that counts what is missing (held when not 0): its unit is 1 (or none) and its name
// has one of these words. The names come from the assembly tool's results (joint_gap_count,
// joint_gaps, unjoined_parts, envelope_outside_count: hiraku/solvers/shelf/assembly.py at 7fc0ffc;
// blocked_parts; unsupported_load).
export const COUNT_NAME = /(^|_)(count|gaps?|blocked|unsupported|unjoined|outside|missing|open)(_|$)/;
export const isCountQuantity = (q, unit) => (unit === "1" || !unit) && COUNT_NAME.test(String(q));

// The kinds, in the order they are tried, each with how it is told and where that comes from.
export const NO_CRITERION_KINDS = [
  {
    kind: "held",
    ja: "保留",
    why: "基準がまだ無いので判定していない (OK ではない)",
    source: "量の名前と単位 (held.js COUNT_NAME)",
    test: ({ quantity, unit, value }) => isCountQuantity(quantity, unit) && value !== 0,
  },
  {
    kind: "relay",
    ja: "渡すだけ",
    why: "ほかの荷重条件に力を渡すだけ (OK / NG の基準は無い)",
    source: "荷重条件の feeds (hiraku/contract/hiraku.schema.json の load_case.feeds)",
    test: ({ lc }) => Array.isArray(lc.feeds) && lc.feeds.length > 0,
  },
  {
    kind: "compare",
    ja: "比べるだけ",
    why: "予測と照らし合わせる量 (OK / NG の基準は無い)",
    // hiraku/catalog/builders/k2_tests.py lines 17-21: "S5 has no criterion"; its case past the
    // limit (load_case_id ending -beyond) has one and is judged
    source: "試験 S5 で -beyond で終わらない荷重条件 (hiraku/catalog/builders/k2_tests.py 17〜21 行)",
    test: ({ lc }) => lc.test_id === "S5" && !String(lc.load_case_id).endsWith("-beyond"),
  },
  {
    kind: "reference",
    ja: "参考",
    why: "判定しない量",
    source: "ほかのどれでもない",
    test: () => true,
  },
];
export const KIND_OF = Object.fromEntries(NO_CRITERION_KINDS.map((k) => [k.kind, k]));

// How a count reads in the band (else: the quantity's name and its value).
// joint_gap_count = joint_gaps + unjoined_parts (hiraku/solvers/shelf/assembly.py at 7fc0ffc).
const COUNT_WORDS = {
  joint_gap_count: (n, ana) => {
    const gaps = resultOf(ana, "joint_gaps");
    const none = resultOf(ana, "unjoined_parts");
    if (gaps && none) {
      const said = [];
      if (gaps.value) said.push(`つながり ${fmt(gaps.value)} か所が離れている`);
      if (none.value) said.push(`つながりの無い部品 ${fmt(none.value)} 個`);
      return said.join("・");
    }
    return `つながり ${fmt(n)} か所が離れている (つながりの無い部品を含む)`;
  },
  envelope_outside_count: (n) => `モジュールの空間からはみ出した部品 ${fmt(n)} 個`,
  interference_count: (n) => `干渉 ${fmt(n)} 組`,
};

const resultOf = (ana, q) => (ana?.results ?? []).find((r) => r.quantity === q) ?? null;

// [{ loadCase, domain, quantity, value, unit, text, kind }] for the domain's load cases without
// a criterion, in the version (the latest ok analysis of each), one per predicted quantity with a
// number in the results; a count of 0 is left out.
export function noCriterionCases(s, domain, version) {
  if (version == null) return [];
  const out = [];
  const cases = (s.project?.load_cases ?? []).filter((c) => c.domain === domain && !c.criterion);
  for (const lc of cases) {
    const anas = s.order.analyses.map((id) => s.analyses[id]).filter((a) => a.status === "ok" && a.load_case_id === lc.load_case_id && a.design_version === version);
    const ana = anas.at(-1);
    if (!ana) continue;
    const quantities = [
      ...new Set(
        s.order.predictions
          .map((id) => s.predictions[id])
          .filter((p) => p.load_case_id === lc.load_case_id && p.design_version === version)
          .map((p) => p.quantity),
      ),
    ];
    for (const q of quantities) {
      const r = resultOf(ana, q);
      if (!r || typeof r.value !== "number") continue;
      if (r.value === 0 && isCountQuantity(q, r.unit)) continue;
      const kind = NO_CRITERION_KINDS.find((k) => k.test({ lc, quantity: q, unit: r.unit, value: r.value })).kind;
      const words = kind === "held" && COUNT_WORDS[q] ? COUNT_WORDS[q](r.value, ana) : `${quantityName(q)} ${fmtVU(r.value, r.unit)}`;
      out.push({ loadCase: lc, domain, quantity: q, value: r.value, unit: r.unit, text: words, kind });
    }
  }
  return out;
}

// The held ones only (the orange band).
export function heldCases(s, domain, version) {
  return noCriterionCases(s, domain, version).filter((h) => h.kind === "held");
}

// The ones not coloured (渡すだけ・比べるだけ・参考).
export function referenceCases(s, domain, version) {
  return noCriterionCases(s, domain, version).filter((h) => h.kind !== "held");
}

// Every held case of the current version, domain by domain (the order given).
export function heldAll(s, domains) {
  return domains.flatMap((d) => heldCases(s, d, s.currentVersion));
}

const caseName = (lc) => `<span class="held-id" title="${esc(lc.load_case_id)}">${esc(lc.title ? shortTitle(lc.title) : lc.load_case_id)}</span>`;
// A load case's title, short: before its first bracket.
export const shortTitle = (t) => String(t ?? "").replace(/\s*[（(].*$/, "") || String(t ?? "");

// The band: 保留 and what the count says, then why it is not green (no criterion yet).
export function heldBandHtml(list, { withDomain = false } = {}) {
  if (!list.length) return "";
  const rows = list
    .map(
      (h) =>
        `<span class="held-row" data-held="${esc(h.loadCase.load_case_id)}" data-quantity="${esc(h.quantity)}" data-value="${h.value}">` +
        `${withDomain ? `${esc(DOMAIN_JA[h.domain] ?? h.domain)} ` : ""}<span class="held-id">${esc(h.loadCase.load_case_id)}</span> ${esc(h.text)}</span>`,
    )
    .join("");
  return `<b class="held-tag">保留</b><span class="held-rows">${rows}</span><span class="held-why">${esc(KIND_OF.held.why)}</span>`;
}

// The rows not coloured: 参考 (判定しない), each with its kind's word and why.
export function referenceRowsHtml(list) {
  if (!list.length) return "";
  const rows = list
    .map((h) => {
      const k = KIND_OF[h.kind];
      return (
        `<li class="ref-row" data-ref="${esc(h.loadCase.load_case_id)}" data-kind="${h.kind}" data-quantity="${esc(h.quantity)}" data-value="${h.value}" title="${esc(`${h.loadCase.load_case_id}: ${k.why} (見分け方: ${k.source})`)}">` +
        `<span class="ref-kind">${esc(k.ja)}</span>${caseName(h.loadCase)} ${esc(h.text)}</li>`
      );
    })
    .join("");
  return `<div class="refrows"><span class="ref-tag">参考 (判定しない)</span><ul>${rows}</ul></div>`;
}
