// The report's next changes on the real screen (the grid, in the workbench and in the
// present mode): "次に変えるもの", one row per entry of the newest report's next
// (report.written data.next, docs/contract.md: domain, change, reason, optional rule_id).
// The domain in DOMAIN_JA, the change large, the reason (and the rule it answers) small
// under くわしく. All words are the record's own, except the design's numbers named by their
// paths (variables.motor_peak_torque, sections.<id>.dims_mm.t, parts.<id>.mass_kg ...): those
// are said by their names (labels.js designNumberName); the path itself only under ?dev=1.
// No next: no block.

import { DOMAIN_JA, designNumberName, esc, judgedWords, variableName, verLabel } from "./labels.js";
import { plainWords } from "./plain.js";

// A word of the record in a change said by the design's own name (people's screen, not ?dev=1): a
// variable (cross_t), a member (cross_B) or a part by its name up to its bracket, a load case
// (R1-step100) by its title up to its bracket; null: none (plain.js takes the word out).
function nameIn(design, cases) {
  const short = (x) => (x ? String(x).split(/\s*[（(:：]/)[0].trim() || null : null);
  return (w) =>
    variableName(w, design) ??
    short((design?.members ?? []).find((m) => m.id === w)?.name) ??
    short((design?.parts ?? []).find((m) => m.id === w)?.name) ??
    (short((cases ?? []).find((c) => c.load_case_id === w)?.title) ? `「${short(cases.find((c) => c.load_case_id === w).title)}」` : null);
}

// a design number's path as the reports write it (K5's paths)
const PATH = /\b(?:variables|sections|parts|materials)\.[A-Za-z0-9_.\-]*[A-Za-z0-9_]/g;
const UNNAMED_JA = "設計の値";
const SCRIPT_MARK = /^\s*台本\s*[:：]\s*/;

// A change's words with each path said by its name (escaped HTML). dev: the path after it, small.
// plain (people's screen: not ?dev=1, not the film): the record's other words too (ids, English),
// by the design's names or taken out (plain.js), in text without the paths' spans.
const IS_PATH = new RegExp(`^${PATH.source}$`);
export function changeWords(text, design = null, dev = false, { cases = null, plain = false } = {}) {
  let out = "";
  let at = 0;
  text = judgedWords(text); // the record's words in the screen's (OK / NG, 判定, vN)
  if (plain) {
    const name = nameIn(design, cases);
    return esc(plainWords(text, { name: (w) => (IS_PATH.test(w) ? designNumberName(w, design) ?? UNNAMED_JA : name(w)) }));
  }
  for (const m of text.matchAll(PATH)) {
    const name = designNumberName(m[0], design) ?? UNNAMED_JA;
    out += esc(text.slice(at, m.index)) + `<span class="rn-name" data-path="${esc(m[0])}">${esc(name)}</span>` + (dev ? ` <small class="rn-path">(${esc(m[0])})</small>` : "");
    at = m.index + m[0].length;
  }
  return out + esc(String(text ?? "").slice(at));
}

export function reportNextHtml(report, { design = null, dev = false, cases = null, plain = false } = {}) {
  const next = Array.isArray(report?.next) ? report.next.filter((n) => n && String(n.change ?? "").trim()) : [];
  if (!next.length) return "";
  const rows = next
    .map((n, i) => {
      const dom = DOMAIN_JA[n.domain] ?? n.domain ?? "";
      const why = String(n.reason ?? "").trim();
      const rule = n.rule_id ? `<span class="rn-rule">決まり ${esc(n.rule_id)} への答え</span>` : "";
      const more = why || rule ? `<details class="more" data-keep="report-next-${i}"><summary>くわしく</summary>${why ? `<div class="rn-why">${changeWords(why, design, dev)}</div>` : ""}${rule}</details>` : "";
      // a scripted run's report starts its lines with 「台本:」 (the script's own words): a judge reads
      // the change without it (?dev=1 keeps it)
      const change = dev ? n.change : String(n.change).replace(SCRIPT_MARK, "");
      return `<li data-domain="${esc(n.domain ?? "")}"><b class="rn-dom">${esc(dom)}</b><div class="rn-body"><span class="rn-change">${changeWords(change, design, dev, { cases, plain })}</span>${more}</div></li>`;
    })
    .join("");
  const v = Number.isInteger(report.design_version) ? `<span class="rn-src">${verLabel(report.design_version)} の報告から</span>` : "";
  return `<h3>次に変えるもの${v}</h3><ul>${rows}</ul>`;
}
