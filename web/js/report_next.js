// The report's next changes on the real screen (the grid, in the workbench and in the
// present mode): "次に変えるもの", one row per entry of the newest report's next
// (report.written data.next, docs/contract.md: domain, change, reason, optional rule_id).
// The domain in DOMAIN_JA, the change large, the reason (and the rule it answers) small
// under くわしく. All words are the record's own; none is made here. No next: no block.

import { DOMAIN_JA, esc } from "./labels.js";

export function reportNextHtml(report) {
  const next = Array.isArray(report?.next) ? report.next.filter((n) => n && String(n.change ?? "").trim()) : [];
  if (!next.length) return "";
  const rows = next
    .map((n, i) => {
      const dom = DOMAIN_JA[n.domain] ?? n.domain ?? "";
      const why = String(n.reason ?? "").trim();
      const rule = n.rule_id ? `<span class="rn-rule">決まり ${esc(n.rule_id)} への答え</span>` : "";
      const more = why || rule ? `<details class="more" data-keep="report-next-${i}"><summary>くわしく</summary>${why ? `<div class="rn-why">${esc(why)}</div>` : ""}${rule}</details>` : "";
      return `<li data-domain="${esc(n.domain ?? "")}"><b class="rn-dom">${esc(dom)}</b><div class="rn-body"><span class="rn-change">${esc(n.change)}</span>${more}</div></li>`;
    })
    .join("");
  const v = Number.isInteger(report.design_version) ? `<span class="rn-src">版 ${report.design_version} の報告から</span>` : "";
  return `<h3>次に変えるもの${v}</h3><ul>${rows}</ul>`;
}
