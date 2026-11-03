// A5 - knowledge: categories -> rules, the parts a rule hits, and a place to drop a file of rules (N14).

import { CATEGORIES, FACTS } from "./data.js";
import { fill, h } from "./dom.js";
import { allRules, ruleNeeds, ruleState, ruleTree } from "./model.js";

const MODES = [["category", "分類から"], ["source", "出どころから"]];
const FILTERS = [["all", "すべて"], ["builtin", "基本"], ["dropped", "取り込んだ"], ["hit", "当たった"]];
const ORIGIN_JA = { builtin: "基本の制約", dropped: "取り込んだ" };
const BY_JA = { code: "文の型からコード", claude: "Claude" }; // no check_by: the formula came with the data
const TRUST_JA = ["", "一次の資料", "二次の資料", "仮定 (確かめていない)"];

function status(state, off) {
  if (off) return h("span", { class: "in-st off" }, "使わない");
  if (!state.checked) return h("span", { class: "in-st none" }, "未チェック");
  if (state.hits.length) return h("span", { class: "in-st hit" }, `当たり ${state.hits.length}`);
  if (!state.targets) return h("span", { class: "in-st none" }, "対象なし");
  if (state.notEvaluable.length === state.targets) return h("span", { class: "in-st unk" }, "評価できない");
  if (state.notEvaluable.length) return h("span", { class: "in-st ok" }, `合格 (${state.notEvaluable.length} 部品は評価できない)`);
  return h("span", { class: "in-st ok" }, "合格");
}

function leaf(n, ui, api) {
  const r = n.rule;
  return h("li", { class: `in-rule${ui.selected === r.rule_id ? " sel" : ""}${n.off ? " is-off" : ""}`, dataset: { rule: r.rule_id },
      onclick: () => { ui.selected = r.rule_id; ui.rerender(); } },
    h("input", { type: "checkbox", checked: !n.off, title: "この規則を使う",
      onclick: (e) => e.stopPropagation(),
      onchange: (e) => { if (e.target.checked) ui.disabled.delete(r.rule_id); else ui.disabled.add(r.rule_id); api.toggleRule?.(r.rule_id, e.target.checked); ui.rerender(); } }),
    h("code", { class: "in-id" }, r.rule_id),
    h("span", { class: "in-rtext" }, r.text),
    status(n.state, n.off));
}

function branch(n, ui, api, depth = 0) {
  if (n.rule) return leaf(n, ui, api);
  const key = `${depth}:${n.key}`;
  const closed = ui.collapsed.has(key);
  return h("li", { class: `in-node d${depth}` },
    h("button", { class: "in-nodehead", type: "button", onclick: () => { if (closed) ui.collapsed.delete(key); else ui.collapsed.add(key); ui.rerender(); } },
      h("span", { class: "in-caret" }, closed ? "▸" : "▾"), h("b", {}, n.label),
      h("span", { class: "in-n" }, `${n.count}`), n.hitCount ? h("span", { class: "in-st hit" }, `当たり ${n.hitCount}`) : null),
    closed ? null : h("ul", {}, n.children.map((c) => branch(c, ui, api, depth + 1))));
}

function partChip(kind, id, text) {
  return h("span", { class: `in-part ${kind}`, title: text || "" }, id);
}

function detail(m, rule, ui) {
  if (!rule) return h("p", { class: "in-empty" }, "左の規則を選ぶと、出どころと当たった部品が出ます");
  const st = ruleState(m, rule);
  const cat = CATEGORIES.find((c) => c.name === rule.category)?.ja || rule.category;
  const hitIds = new Set(st.hits.map((x) => x.part_id));
  const neIds = new Map(st.notEvaluable.map((x) => [x.part_id, x.missing]));
  const c = m.checks.get(rule.rule_id);
  const needs = ruleNeeds(rule, FACTS);
  return h("div", { class: "in-detail" },
    h("header", {}, h("code", {}, rule.rule_id), h("h3", {}, rule.text), status(st, ui.disabled.has(rule.rule_id))),
    h("dl", {},
      h("dt", {}, "分類"), h("dd", {}, cat),
      h("dt", {}, "出どころ"), h("dd", {}, rule.origin === "builtin" ? "基本の制約" : `取り込んだファイル ${rule.docName}`),
      h("dt", {}, "式"), h("dd", {}, h("code", { class: "in-formula" }, rule.check), h("small", {}, ` 式の出どころ: ${BY_JA[rule.check_by] || "データに書いてあった式"}`)),
      needs.length ? [h("dt", {}, "要る値"), h("dd", {}, needs.map((n) => [h("code", { title: n.name }, `${n.label} (${n.name})`),
        n.inDesigns ? null : h("small", { class: "in-miss" }, " 今の設計は持っていない値"), " "]))] : null,
      h("dt", {}, "対象"), h("dd", {}, rule.applies_to?.kinds?.join("・") || "すべての部品"),
      h("dt", {}, "根拠"), h("dd", {}, h("span", { class: `in-dot t${rule.trust}`, title: TRUST_JA[rule.trust] }, rule.trust), " ", rule.basis)),
    h("h4", {}, "設計に当てた結果", st.checked ? h("small", {}, ` 版 ${st.designVersion}`) : null),
    !st.checked
      ? h("p", { class: "in-empty" }, m.designVersion ? "まだ当てていません" : "設計がまだ無いので当てていません")
      : h("div", {},
          h("div", { class: "in-parts" },
            (c.targets || []).map((id) => hitIds.has(id) ? partChip("hit", id, st.hits.find((x) => x.part_id === id).message)
              : neIds.has(id) ? partChip("unk", id, `評価できない: ${neIds.get(id).map((n) => FACTS[n]?.label || n).join("・")} が設計に無い`)
              : partChip("ok", id, "合格")),
            !c.targets?.length ? h("span", { class: "in-empty" }, "この設計に対象の部品がありません") : null),
          st.hits.length ? h("ul", { class: "in-hits" }, st.hits.map((x) => h("li", {}, h("code", {}, x.part_id), " ", x.message))) : null,
          st.notEvaluable.length
            ? h("p", { class: "in-foot" }, "式に要る値が設計に無いので判定していません: ",
                [...new Set(st.notEvaluable.flatMap((x) => x.missing))].map((n) => h("code", {}, FACTS[n]?.label ? `${FACTS[n].label} (${n})` : n)))
            : null));
}

function dropzone(api, ui) {
  const onFiles = async (files) => {
    for (const f of files) {
      const entry = { name: f.name, size: f.size, state: api.dropFile ? "読んでいます…" : "この画面では受け取れません" };
      ui.dropped.unshift(entry);
      ui.rerender();
      if (!api.dropFile) continue;
      try {
        entry.state = (await api.dropFile(f.name, await f.text())) || "渡しました";
      } catch (e) {
        entry.state = `失敗: ${e.message || e}`;
      }
      ui.rerender();
    }
  };
  ui.onFiles = onFiles;
  const input = h("input", { type: "file", multiple: true, accept: ".md,.markdown,.txt,.csv,.tsv", hidden: true, onchange: (e) => onFiles([...e.target.files]) });
  return h("div", { class: "in-drop", onclick: () => input.click() },
    input,
    h("b", {}, "ファイルをここに落とす"), h("span", {}, "Markdown・表 (CSV/TSV)・テキスト。1 行が 1 つの規則になります"),
    ui.dropped.length ? h("ul", {}, ui.dropped.slice(0, 4).map((d) => h("li", {}, h("code", {}, d.name), ` ${(d.size / 1024).toFixed(1)} KB  ${d.state}`))) : null);
}

export function renderA5(el, m, api, ui) {
  const tree = ruleTree(m, { mode: ui.mode, filter: ui.filter, query: ui.query, disabled: ui.disabled });
  const rules = allRules(m);
  const selected = rules.find((r) => r.rule_id === ui.selected) || rules.find((r) => ruleState(m, r).hits.length) || null;
  const hitRules = rules.filter((r) => ruleState(m, r).hits.length).length;
  const checked = rules.some((r) => m.checks.has(r.rule_id));
  fill(el,
    h("div", { class: "in-bar" },
      h("input", { class: "in-search", type: "search", placeholder: "規則・番号・式を探す", value: ui.query, oninput: (e) => { ui.query = e.target.value; ui.rerender({ keepFocus: ".in-search" }); } }),
      h("div", { class: "in-seg" }, MODES.map(([k, t]) => h("button", { class: ui.mode === k ? "on" : "", onclick: () => { ui.mode = k; ui.rerender(); } }, t))),
      h("div", { class: "in-seg" }, FILTERS.map(([k, t]) => h("button", { class: ui.filter === k ? "on" : "", onclick: () => { ui.filter = k; ui.rerender(); } }, t))),
      h("span", { class: "in-sum" }, `${rules.length} 規則${checked ? `・当たり ${hitRules}` : ""}`),
      api.recheck ? h("button", { class: "in-btn primary", onclick: () => api.recheck() }, "チェックのやり直し") : null),
    h("div", { class: "in-body" },
      h("section", { class: "in-tree" },
        dropzone(api, ui),
        h("ul", { class: "in-root", dataset: { keepScroll: "a5-tree" } },
          tree.length ? tree.map((n) => branch(n, ui, api)) : h("li", { class: "in-empty" }, rules.length ? "条件に合う規則がありません" : "規則がまだありません"))),
      h("section", { class: "in-right" }, detail(m, selected, ui))));
}
