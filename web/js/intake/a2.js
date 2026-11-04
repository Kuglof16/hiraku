// A2 - hearing: the missing requirements asked once (left), the requirements and where each came from (right) (N14).

import { fill, h } from "./dom.js";
import { ITEM_CHOICES, ITEM_HELP } from "./data.js";
import { assumptions, hearingRows, RULE_CHOICE_JA, ruleRows, showValue, SOURCE_JA, TRUST_JA } from "./model.js";

const STATE_JA = { asking: "答えを待っています", answered: "答えました", defaulted: "標準の値のまま", open: "答えなしで進みました" };

function choiceLabel(q, value) {
  const c = (q.choices || []).find((x) => String(x.value) === String(value));
  return c ? c.label : null;
}

function question(q, api, ui) {
  const picked = ui.answers[q.req_key];
  const interactive = q.state === "asking" && !!api.answerHearing;
  const def = q.default;
  const choiceButtons = (q.choices || []).map((c) =>
    h("button", {
      class: `in-choice${picked && String(picked.value) === String(c.value) ? " picked" : ""}${q.answer && String(q.answer.value) === String(c.value) ? " chosen" : ""}`,
      type: "button", disabled: !interactive,
      onclick: () => { ui.answers[q.req_key] = { value: c.value, ...(c.unit ? { unit: c.unit } : {}) }; ui.rerender(); },
    }, c.label));
  const other = interactive
    ? h("input", {
        class: "in-other", type: "text", placeholder: "ほかの答え (書いてください)", value: picked && !(q.choices || []).some((c) => String(c.value) === String(picked.value)) ? picked.value : "",
        oninput: (e) => { const v = e.target.value.trim(); if (v) ui.answers[q.req_key] = { value: v }; else delete ui.answers[q.req_key]; },
      })
    : null;
  const help = ITEM_HELP[q.req_key];
  return h("article", { class: `in-q ${q.state}`, dataset: { key: q.req_key }, title: `番号 ${q.req_key}` },
    h("header", {},
      h("span", { class: `in-badge ${q.ask}` }, q.ask === "must" ? "答えが要ります" : "答えなくても進めます"),
      h("span", { class: "in-state" }, STATE_JA[q.state])),
    h("p", { class: "in-qtext" }, q.text),
    help ? h("p", { class: "in-why" }, `なぜ要るか: ${help.why}`) : null,
    help ? h("p", { class: "in-eg" }, `例: ${help.example}`) : null,
    choiceButtons.length ? h("div", { class: "in-choices" }, choiceButtons) : null,
    other,
    def ? h("p", { class: "in-default" }, `答えなければ ${showValue(def.value, def.unit)} を使います`, h("small", {}, def.trust === 3 ? " (仮の値です)" : ` (${TRUST_JA[def.trust]})`)) : null,
    q.ask === "must" && !q.answer ? h("p", { class: "in-default must" }, "この質問に答えるまで先へ進めません") : null,
    q.answer
      ? h("p", { class: "in-answer" }, h("b", {}, q.answer.source === "hearing" ? "答え" : "標準の値"), " ",
          choiceLabel(q, q.answer.value) || showValue(q.answer.value, q.answer.unit))
      : null);
}

/** A rule that hit: the parts, the sentence, and the two things a person can decide. Hovering a part tells the host. */
function ruleQuestion(q, api, ui) {
  const picked = ui.ruleAnswers[q.rule_id];
  const interactive = q.state === "asking" && !!api.answerHearing;
  const focus = (ids) => ui.onRuleFocus?.(ids);
  return h("article", { class: `in-q in-rq ${q.state}`, dataset: { rule: q.rule_id }, title: `ルール ${q.rule_id}`,
      onmouseenter: () => focus(q.parts), onmouseleave: () => focus([]), onfocusin: () => focus(q.parts), onfocusout: () => focus([]) },
    h("header", {},
      h("span", { class: "in-badge" }, "ルールに引っかかりました"),
      h("span", { class: "in-state" }, STATE_JA[q.state])),
    h("p", { class: "in-qtext" }, q.text),
    q.parts?.length ? h("p", { class: "in-parts-hit" }, "どの部品か: ", q.parts.map((p) => h("code", {}, p))) : null,
    h("p", { class: "in-why" }, "守るなら設計を直します。守らなくてよい理由があれば、このルールを使わないことにできます。"),
    h("div", { class: "in-choices" }, q.choices.map((c) =>
      h("button", {
        class: `in-choice${picked && picked.choice === c ? " picked" : ""}${q.answer && q.answer.choice === c ? " chosen" : ""}`,
        type: "button", disabled: !interactive,
        onclick: () => { ui.ruleAnswers[q.rule_id] = { rule_id: q.rule_id, choice: c, ...(ui.ruleAnswers[q.rule_id]?.note ? { note: ui.ruleAnswers[q.rule_id].note } : {}) }; ui.rerender(); },
      }, RULE_CHOICE_JA[c] || c))),
    interactive && picked?.choice === "drop_rule"
      ? h("input", { class: "in-other", type: "text", placeholder: "使わない理由 (書くと報告に残ります)", value: picked.note || "",
          oninput: (e) => { const v = e.target.value.trim(); if (v) ui.ruleAnswers[q.rule_id].note = v; else delete ui.ruleAnswers[q.rule_id].note; } })
      : null,
    q.answer
      ? h("p", { class: "in-answer" }, h("b", {}, "答え"), " ", RULE_CHOICE_JA[q.answer.choice] || q.answer.choice,
          q.answer.note ? h("small", {}, ` (${q.answer.note})`) : null)
      : null,
    q.state === "open" ? h("p", { class: "in-default must" }, "答えなしで進みました") : null);
}

function requirementRow(r) {
  const lowTrust = r.trust === 3;
  return h("tr", { class: `${r.fresh ? "fresh" : ""}${lowTrust ? " low" : ""}`, title: `番号 ${r.key}\n${r.basis || ""}` },
    h("td", {}, r.name),
    h("td", { class: "val" }, ITEM_CHOICES[r.key]?.[String(r.value)] ?? showValue(r.value, r.unit)), // the name of the choice, the value itself when there is none
    h("td", {}, h("span", { class: `in-src ${r.source}` }, SOURCE_JA[r.source] || r.source)),
    h("td", { class: "trust" }, h("span", { class: `in-dot t${r.trust}`, title: TRUST_JA[r.trust] }, r.trust)));
}

/** Send the answers; a refusal (the host's promise rejects with the reason) is shown under the buttons. */
async function submit(api, ui, body) {
  ui.error = "";
  ui.sending = true;
  ui.rerender();
  try {
    await api.answerHearing(body);
  } catch (e) {
    ui.error = e?.message || String(e);
  }
  ui.sending = false;
  ui.rerender();
}

export function renderA2(el, m, api, ui) {
  const rows = hearingRows(m);
  const rules = ruleRows(m);
  const asking = [...rows, ...rules].some((r) => r.state === "asking");
  const answersBody = () => ({ answers: ui.answers, ruleAnswers: Object.values(ui.ruleAnswers), note: ui.note || "", useDefaults: true });
  const canSend = asking && !!api.answerHearing && !ui.sending;
  const must = rows.filter((r) => r.ask === "must" && !r.answer);
  const bySource = (s) => m.requirements.filter((r) => r.source === s).length;
  fill(el,
    h("section", { class: "in-left" },
      h("h2", {}, "HIRAKU からの質問", h("small", {}, rows.length || rules.length ? `${[rows.length ? `確認したいこと ${rows.length} 件` : "", rules.length ? `ルール ${rules.length} 件` : ""].filter(Boolean).join("・")}` : "")),
      h("div", { class: "in-qs", dataset: { keepScroll: "a2-qs" } },
        rows.length || rules.length ? [...rows.map((q) => question(q, api, ui)), ...rules.map((q) => ruleQuestion(q, api, ui))]
          : h("p", { class: "in-empty" }, m.requirements.length ? "足りない情報はないので、そのまま進みます" : "いまは聞くことがありません")),
      rows.length || rules.length
        ? h("div", { class: "in-composer" },
            h("textarea", { class: "in-input", rows: 2, placeholder: "ほかに伝えたいこと", value: ui.note || "", oninput: (e) => { ui.note = e.target.value; }, disabled: !canSend }),
            ui.error ? h("p", { class: "in-warn", role: "alert" }, `受け付けられませんでした。${ui.error}`) : null,
            h("div", { class: "in-actions" },
              h("button", { class: "in-btn", disabled: !canSend, onclick: () => submit(api, ui, { answers: {}, note: ui.note || "", useDefaults: true }) }, "標準の値のまま進める"),
              h("button", { class: "in-btn primary", disabled: !canSend,
                onclick: () => submit(api, ui, answersBody()) }, ui.sending ? "送っています…" : "答えて進める")))
        : null,
      !rows.length && m.hearing.answered?.answers?.length
        ? h("p", { class: "in-foot" }, `標準の値で足したもの: ${m.hearing.answered.answers.map((a) => a.name).join("・")}`)
        : null,
      m.hearing.answered?.note ? h("p", { class: "in-foot" }, `ほかに伝えたこと: ${m.hearing.answered.note}`) : null,
      must.length ? h("p", { class: "in-warn" }, `答えが要る質問 (${must.map((q) => q.name).join("・")}) に答えがないので、先へ進めません`) : null),
    h("section", { class: "in-right" },
      h("h2", {}, "決まっていること", h("small", {}, `${m.requirements.length} 件  あなたが指定 ${bySource("user")}・あなたが答えた ${bySource("hearing")}・標準の値 ${bySource("default")}`)),
      h("div", { class: "in-table", dataset: { keepScroll: "a2-table" } },
        m.requirements.length
          ? h("table", {},
              h("thead", {}, h("tr", {}, ["項目", "値", "どうやって決めたか", "確かさ"].map((t) => h("th", {}, t)))),
              h("tbody", {}, [...m.requirements].sort((a, b) => a.key.localeCompare(b.key)).map(requirementRow)))
          : h("p", { class: "in-empty" }, "決まったことが入るとここに並びます")),
      h("p", { class: "in-foot" }, `仮の値が ${assumptions(m).length} 件あります。最後の報告に「確かめていないこと」として載ります。行に重ねると、根拠が出ます`)));
}
