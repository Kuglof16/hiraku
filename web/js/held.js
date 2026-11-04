// Load cases shown, not judged: a load case with no criterion (e.g. H1-joints while the design
// generator does not join the parts in shape) gets no verdict, so its domain's tile can be green
// while its count says something is wrong. heldCases() finds, for a domain and a design version,
// each such load case whose latest ok analysis has a non-zero value of a quantity predicted for
// it (the quantities come from the record's predictions of that load case and version; nothing
// is guessed). The tiles (tiles.js) and the workbench (app.js) show them as a 保留 band.

import { DOMAIN_JA, esc, fmt, fmtVU, quantityName } from "./labels.js";

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

// [{ loadCase, domain, quantity, value, unit, text }] for the domain's load cases without a
// criterion, in the version (the latest ok analysis of each), whose predicted quantities have
// a non-zero result.
export function heldCases(s, domain, version) {
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
      if (!r || typeof r.value !== "number" || r.value === 0) continue;
      const words = COUNT_WORDS[q] && (r.unit === "1" || !r.unit) ? COUNT_WORDS[q](r.value, ana) : `${quantityName(q)} ${fmtVU(r.value, r.unit)}`;
      out.push({ loadCase: lc, domain, quantity: q, value: r.value, unit: r.unit, text: words });
    }
  }
  return out;
}

// Every held case of the current version, domain by domain (the order given).
export function heldAll(s, domains) {
  return domains.flatMap((d) => heldCases(s, d, s.currentVersion));
}

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
  return `<b class="held-tag">保留</b><span class="held-rows">${rows}</span><span class="held-why">基準がまだ無いので判定していない (緑ではない)</span>`;
}
