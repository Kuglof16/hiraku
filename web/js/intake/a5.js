// A5 - knowledge: categories -> rules, the parts a rule hits, and a place to drop a file of rules (N14).

import { CATEGORIES, FACTS } from "./data.js";
import { fill, h } from "./dom.js";
import { allRules, checkedRows, ruleNeeds, ruleState, ruleTree, showValue, TRUST_JA } from "./model.js";

const MODES = [["category", "種類で分ける"], ["source", "出どころで分ける"]];
const FILTERS = [["all", "すべて"], ["builtin", "最初から入っているもの"], ["dropped", "あなたが読み込んだもの"], ["hit", "引っかかったもの"]];
const ORIGIN_JA = { builtin: "最初から入っているルール", dropped: "あなたが読み込んだもの" };
const BY_JA = { code: "文の型からコード", claude: "Claude" }; // no check_by: the formula came with the data

function status(state, off, finished = false) {
  if (off) return h("span", { class: "in-st off" }, "使わない");
  if (!state.checked) return h("span", { class: "in-st none" }, finished ? "この実行では調べていません" : "まだ調べていません");
  if (state.isAdvice) return h("span", { class: "in-st adv", title: "判定には数えません" }, state.advice.length ? `助言 ${state.advice.length}` : "助言のルール (いまは当たりなし)");
  if (state.hits.length) return h("span", { class: "in-st hit" }, `引っかかった ${state.hits.length}`);
  if (!state.targets) return h("span", { class: "in-st none" }, "当てはまる部品なし");
  if (state.notEvaluable.length === state.targets) return h("span", { class: "in-st unk" }, "判定できません");
  if (state.notEvaluable.length) return h("span", { class: "in-st ok" }, `問題なし (${state.notEvaluable.length} 部品は判定できません)`);
  return h("span", { class: "in-st ok" }, "問題なし");
}

function leaf(n, ui, api, finished) {
  const r = n.rule;
  return h("li", { class: `in-rule${ui.selected === r.rule_id ? " sel" : ""}${n.off ? " is-off" : ""}`, dataset: { rule: r.rule_id },
      onclick: () => { ui.selected = r.rule_id; ui.rerender(); } },
    h("input", { type: "checkbox", checked: !n.off, title: "このルールを使う",
      onclick: (e) => e.stopPropagation(),
      onchange: (e) => { if (e.target.checked) ui.disabled.delete(r.rule_id); else ui.disabled.add(r.rule_id); api.toggleRule?.(r.rule_id, e.target.checked); ui.rerender(); } }),
    h("code", { class: "in-id" }, r.rule_id),
    h("span", { class: "in-rtext" }, r.text),
    status(n.state, n.off, finished));
}

function branch(n, ui, api, depth = 0, finished = false) {
  if (n.rule) return leaf(n, ui, api, finished);
  const key = `${depth}:${n.key}`;
  const closed = ui.collapsed.has(key);
  return h("li", { class: `in-node d${depth}` },
    h("button", { class: "in-nodehead", type: "button", onclick: () => { if (closed) ui.collapsed.delete(key); else ui.collapsed.add(key); ui.rerender(); } },
      h("span", { class: "in-caret" }, closed ? "▸" : "▾"), h("b", {}, n.label),
      h("span", { class: "in-n" }, `${n.count}`), n.hitCount ? h("span", { class: "in-st hit" }, `引っかかった ${n.hitCount}`) : null),
    closed ? null : h("ul", {}, n.children.map((c) => branch(c, ui, api, depth + 1, finished))));
}

function partChip(kind, id, text) {
  return h("span", { class: `in-part ${kind}`, title: text || "" }, id);
}

function detail(m, rule, ui) {
  if (!rule) return h("p", { class: "in-empty" }, "左のルールを選ぶと、どこから来たかと、引っかかった部品が出ます");
  const st = ruleState(m, rule);
  const cat = CATEGORIES.find((c) => c.name === rule.category)?.ja || rule.category;
  const hitIds = new Set(st.hits.map((x) => x.part_id));
  const adviceIds = new Set(st.advice.map((x) => x.part_id));
  const neIds = new Map(st.notEvaluable.map((x) => [x.part_id, x.missing]));
  const c = m.checks.get(rule.rule_id);
  const needs = ruleNeeds(rule, FACTS);
  return h("div", { class: "in-detail" },
    h("header", {}, h("code", {}, rule.rule_id), h("h3", {}, rule.text), status(st, ui.disabled.has(rule.rule_id), m.finished)),
    h("dl", {},
      h("dt", {}, "種類"), h("dd", {}, cat),
      h("dt", {}, "どこから来たか"), h("dd", {}, rule.origin === "builtin" ? "最初から入っているルール" : `あなたが読み込んだファイル (${rule.docName})`),
      h("dt", {}, "判定の式"), h("dd", {}, h("code", { class: "in-formula" }, rule.check), h("small", {}, ` 式を作った人: ${BY_JA[rule.check_by] || "ファイルに書いてあった式"}`)),
      needs.length ? [h("dt", {}, "判定に必要なデータ"), h("dd", {}, needs.map((n) => [h("code", { title: n.name }, `${n.label} (${n.name})`),
        n.inDesigns ? null : h("small", { class: "in-miss" }, " 今の設計には無いデータ"), " "]))] : null,
      h("dt", {}, "当てはまる部品"), h("dd", {}, rule.applies_to?.kinds?.join("・") || "すべての部品"),
      h("dt", {}, "根拠"), h("dd", {}, h("span", { class: `in-dot t${rule.trust}`, title: TRUST_JA[rule.trust] }, rule.trust), " ", rule.basis)),
    h("h4", {}, "今の設計での結果", st.checked ? h("small", {}, ` (設計の ${st.designVersion} 回目)`) : null),
    !st.checked
      ? h("p", { class: "in-empty" }, m.finished ? "この実行の記録には、規則を当てた結果がありません" : m.designVersion ? "まだ調べていません" : "設計がまだ無いので調べていません")
      : h("div", {},
          h("div", { class: "in-parts" },
            (c.targets || []).map((id) => hitIds.has(id) ? partChip("hit", id, st.hits.find((x) => x.part_id === id).message)
              : adviceIds.has(id) ? partChip("adv", id, st.advice.find((x) => x.part_id === id).message)
              : neIds.has(id) ? partChip("unk", id, `判定できません。設計に「${neIds.get(id).map((n) => FACTS[n]?.label || n).join("・")}」の情報がありません`)
              : partChip("ok", id, "問題なし")),
            !c.targets?.length ? h("span", { class: "in-empty" }, "この設計に、当てはまる部品がありません") : null),
          measured(m, rule),
          st.hits.length ? h("ul", { class: "in-hits" }, st.hits.map((x) => h("li", {}, h("code", {}, x.part_id), " ", x.message))) : null,
          st.advice.length ? [h("p", { class: "in-foot" }, "助言です。判定には数えず、設計も止めません。"),
            h("ul", { class: "in-hits adv" }, st.advice.map((x) => h("li", {}, h("code", {}, x.part_id), " ", x.message)))] : null,
          st.notEvaluable.length
            ? h("p", { class: "in-foot" }, "判定に必要なデータが設計に無いので、判定していません: ",
                [...new Set(st.notEvaluable.flatMap((x) => x.missing))].map((n) => h("code", {}, FACTS[n]?.label ? `${FACTS[n].label} (${n})` : n)))
            : null));
}

/** The values a check used, part by part; a failing part says what it has and what the rule asks. */
function measured(m, rule) {
  const rows = checkedRows(m, rule);
  if (!rows.length) return null;
  const label = (n) => FACTS[n]?.label || n;
  const unit = (n) => FACTS[n]?.unit || "";
  return h("div", { class: "in-vals" },
    h("h4", {}, "判定に使った値", rows.some((r) => r.unchecked) ? h("small", { class: "in-miss" }, " 確かめていない値を使っています") : null),
    h("table", {},
      h("tbody", {}, rows.map((r) => h("tr", { class: r.hit ? "hit" : "ok" },
        h("td", {}, h("code", {}, r.part_id), r.hit ? h("span", { class: "in-st hit" }, "引っかかった") : r.advice ? h("span", { class: "in-st adv" }, "助言") : null),
        h("td", {}, r.values.map((v) => h("span", { class: `in-val${v.unchecked ? " unchecked" : ""}`, title: v.unchecked ? "確かめていない値です" : "" },
          `${label(v.name)} ${showValue(v.value, unit(v.name))}`, v.unchecked ? " (確かめていない値)" : "", " "))),
        h("td", {}, r.need && r.hit ? `今 ${showValue(r.need.now, unit(r.need.name))} → 要る ${showValue(r.need.limit, unit(r.need.name))} ${r.need.opJa}` : r.need ? `要る ${showValue(r.need.limit, unit(r.need.name))} ${r.need.opJa}` : ""))))));
}

function dropzone(api, ui) {
  const onFiles = async (files) => {
    for (const f of files) {
      const entry = { name: f.name, size: f.size, state: api.dropFile ? "読んでいます…" : "この画面では受け取れません" };
      ui.dropped.unshift(entry);
      ui.rerender();
      if (!api.dropFile) continue;
      try {
        entry.state = (await api.dropFile(f.name, await f.text())) || "受け取りました";
      } catch (e) {
        entry.state = `読めませんでした: ${e.message || e}`;
      }
      ui.rerender();
    }
  };
  ui.onFiles = onFiles;
  const input = h("input", { type: "file", multiple: true, accept: ".md,.markdown,.txt,.csv,.tsv", hidden: true, onchange: (e) => onFiles([...e.target.files]) });
  return h("div", { class: "in-drop", onclick: () => input.click() },
    input,
    h("b", {}, "ファイルをここに置く"), h("span", {}, "文章や表 (Markdown・CSV・テキスト) のファイル。1 行が 1 つのルールになります"),
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
      h("input", { class: "in-search", type: "search", placeholder: "ルールを探す (言葉で探せます)", value: ui.query, oninput: (e) => { ui.query = e.target.value; ui.rerender({ keepFocus: ".in-search" }); } }),
      h("div", { class: "in-seg" }, MODES.map(([k, t]) => h("button", { class: ui.mode === k ? "on" : "", onclick: () => { ui.mode = k; ui.rerender(); } }, t))),
      h("div", { class: "in-seg" }, FILTERS.map(([k, t]) => h("button", { class: ui.filter === k ? "on" : "", onclick: () => { ui.filter = k; ui.rerender(); } }, t))),
      h("span", { class: "in-sum" }, `ルール ${rules.length} 件${checked ? `・引っかかった ${hitRules}` : ""}`),
      api.recheck ? h("button", { class: "in-btn primary", onclick: () => api.recheck() }, "もう一度チェックする") : null),
    h("div", { class: "in-body" },
      h("section", { class: "in-tree" },
        dropzone(api, ui),
        h("ul", { class: "in-root", dataset: { keepScroll: "a5-tree" } },
          tree.length ? tree.map((n) => branch(n, ui, api, 0, m.finished)) : h("li", { class: "in-empty" }, rules.length ? "条件に合うルールがありません" : "ルールはまだありません。ファイルを置くと、ここに並びます"))),
      h("section", { class: "in-right" }, detail(m, selected, ui))));
}
