// A2 - hearing: the missing requirements asked once (left), the requirements and where each came from (right) (N14).

import { fill, h } from "./dom.js";
import { ITEM_CHOICES, ITEM_CHOICES_ALT, ITEM_HELP } from "./data.js";
import { assumptions, hearingRows, RULE_CHOICE_JA, ruleRows, showValue, SOURCE_JA, tradeoffChoiceJa, tradeoffRows, TRUST_JA } from "./model.js";

const STATE_JA = { asking: "答えを待っています", answered: "答えました", defaulted: "標準の値のまま", open: "答えなしで進みました", ended: "実行が終わりました" };

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
    q.ask === "must" && !q.answer && q.state !== "ended" ? h("p", { class: "in-default must" }, "この質問に答えるまで先へ進めません") : null,
    q.state === "ended" ? h("p", { class: "in-default must" }, "実行が終わったので、この質問への答えは使われません") : null,
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
    q.state === "open" ? h("p", { class: "in-default must" }, "答えなしで進みました") : null,
    q.state === "ended" ? h("p", { class: "in-default must" }, "実行が終わったので、この判断は使われません") : null);
}

/** Two results that pull apart: the numbers the code worked out, and what the person can prefer. */
function tradeoffQuestion(q, api, ui) {
  const picked = ui.tradeoffAnswers[q.tradeoff_id];
  const interactive = q.state === "asking" && !!api.answerHearing;
  const num = (side, word) => h("li", {}, h("b", {}, side.name), ` ${word}: `, h("span", { class: "in-num" }, `${showValue(side.now, side.unit)} → ${showValue(side.after, side.unit)}`));
  return h("article", { class: `in-q in-tq ${q.state}`, dataset: { tradeoff: q.tradeoff_id }, title: "どちらを優先するかの質問" },
    h("header", {},
      h("span", { class: "in-badge" }, "この 2 つは両立しにくいです"),
      h("span", { class: "in-state" }, STATE_JA[q.state])),
    h("p", { class: "in-qtext" }, q.text),
    h("ul", { class: "in-tq-nums" }, num(q.a, "良くなる"), num(q.b, "悪くなる")),
    h("p", { class: "in-why" }, `${q.variable} を ${q.step_pct} % 動かしたときの、HIRAKU の計算です。`),
    h("div", { class: "in-choices" }, q.choices.map((c) =>
      h("button", {
        class: `in-choice${picked && picked.choice === c ? " picked" : ""}${q.answer && q.answer.choice === c ? " chosen" : ""}`,
        type: "button", disabled: !interactive,
        onclick: () => { ui.tradeoffAnswers[q.tradeoff_id] = { tradeoff_id: q.tradeoff_id, choice: c }; ui.rerender(); },
      }, tradeoffChoiceJa(q, c)))),
    q.answer ? h("p", { class: "in-answer" }, h("b", {}, "答え"), " ", tradeoffChoiceJa(q, q.answer.choice)) : null,
    q.state === "open" ? h("p", { class: "in-default must" }, "答えなしで進みました") : null,
    q.state === "ended" ? h("p", { class: "in-default must" }, "実行が終わったので、この判断は使われません") : null);
}

/** The name of a choice as the person reads it for this product (an item's choices_if renames some for some flags). */
function choiceName(key, value, flags) {
  const alt = ITEM_CHOICES_ALT[key];
  if (alt && alt.on.every((f) => flags?.[f]) && alt.off.every((f) => !flags?.[f]) && alt.labels[String(value)]) return alt.labels[String(value)];
  return ITEM_CHOICES[key]?.[String(value)];
}

function requirementRow(r, flags) {
  const lowTrust = r.trust === 3;
  return h("tr", { class: `${r.fresh ? "fresh" : ""}${lowTrust ? " low" : ""}`, title: `番号 ${r.key}\n${r.basis || ""}` },
    h("td", {}, r.name),
    h("td", { class: "val" }, choiceName(r.key, r.value, flags) ?? showValue(r.value, r.unit)), // the name of the choice, the value itself when there is none
    h("td", {}, h("span", { class: `in-src ${r.source}` }, SOURCE_JA[r.source] || r.source)),
    h("td", { class: "trust" }, h("span", { class: `in-dot t${r.trust}`, title: TRUST_JA[r.trust] }, r.trust)));
}

/** Names of the questions that need an answer and have none yet (nothing picked, nothing answered). */
function mustLeft(rows, ui) {
  return rows.filter((r) => r.ask === "must" && r.state === "asking" && !r.answer && !ui.answers[r.req_key]).map((r) => r.name);
}

/** "Use the standard values": refused with a sentence while a question that needs an answer has none (it would end in a dead end). */
function useDefaultsOrSay(api, ui, rows) {
  const left = mustLeft(rows, ui);
  if (left.length) {
    ui.error = `答えが要る質問 (${left.join("・")}) に答えてから、押してください`;
    ui.rerender();
    return null;
  }
  return submit(api, ui, { answers: {}, note: ui.note || "", useDefaults: true });
}

/**
 * The questions that need an answer (and the rule hits), as one panel for the top of A1: the person answers where they
 * are, without moving to the hearing tab. The answers are the same ones A2 sends (ui.answers).
 */
export function askingPanel(m, api, ui) {
  const rows = hearingRows(m).filter((r) => r.state === "asking");
  const rules = ruleRows(m).filter((r) => r.state === "asking");
  const trades = tradeoffRows(m).filter((r) => r.state === "asking");
  if (!rows.length && !rules.length && !trades.length) return null;
  const must = rows.filter((r) => r.ask === "must");
  const rest = rows.length - must.length;
  const canSend = !!api.answerHearing && !ui.sending;
  const need = must.length + rules.length + trades.length;
  const actions = h("div", { class: "in-actions" },
    h("button", { class: "in-btn", type: "button", disabled: !canSend, onclick: () => useDefaultsOrSay(api, ui, rows) }, "標準の値のまま進める"),
    h("button", { class: "in-btn primary", type: "button", disabled: !canSend,
      onclick: () => submit(api, ui, { answers: ui.answers, ruleAnswers: Object.values(ui.ruleAnswers), ...tradeoffBody(ui), note: ui.note || "", useDefaults: true }) },
    ui.sending ? "送っています…" : "答えて進める"));
  return h("section", { class: "in-ask-top" },
    h("div", { class: "in-ask-head" },  // the buttons sit beside the title, so they are in sight however many questions there are
      h("h2", {}, "HIRAKU からの質問", h("small", {}, need ? `答えが要るもの ${need} 件` : `答えなくても進めます (${rows.length} 件)`)),
      actions),
    ui.error ? h("p", { class: "in-warn", role: "alert" }, `受け付けられませんでした。${ui.error}`) : null,
    need ? h("div", { class: "in-ask-list" }, [...must.map((q) => question(q, api, ui)), ...rules.map((q) => ruleQuestion(q, api, ui)), ...trades.map((q) => tradeoffQuestion(q, api, ui))]) : null,
    rest && need ? h("p", { class: "in-foot" }, `答えなくても進める質問が、ほかに ${rest} 件あります (答えなければ標準の値を使います)。`,
      ui.show ? h("button", { class: "in-btn small", type: "button", onclick: () => ui.show("A2") }, "ほかの質問を見る") : null) : null,
    !need && ui.show ? h("p", { class: "in-foot" }, "答えなければ標準の値を使います。",
      h("button", { class: "in-btn small", type: "button", onclick: () => ui.show("A2") }, "質問を見る")) : null);
}

/** The trade-off answers of the person, only when there are some (the body stays as it was without trade-offs). */
function tradeoffBody(ui) {
  const a = Object.values(ui.tradeoffAnswers || {});
  return a.length ? { tradeoffAnswers: a } : {};
}

/** Send the answers; a refusal (the host's promise rejects with the reason) is shown under the buttons. */
export async function submit(api, ui, body) {
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
  // the questions that need an answer come first (and keep their order among themselves)
  const rows = hearingRows(m).map((r, i) => [r, i]).sort((a, b) => (b[0].ask === "must" && b[0].state === "asking") - (a[0].ask === "must" && a[0].state === "asking") || a[1] - b[1]).map(([r]) => r);
  const rules = ruleRows(m);
  const trades = tradeoffRows(m);
  // another hearing (a new run, other questions): what was picked for the earlier one is not sent with this one
  const sig = [...rows.map((r) => r.req_key), ...rules.map((r) => r.rule_id), ...trades.map((r) => r.tradeoff_id)].join(",");
  if (ui.askedSig !== sig) {
    if (ui.askedSig !== undefined) { ui.answers = {}; ui.ruleAnswers = {}; ui.tradeoffAnswers = {}; }
    ui.askedSig = sig;
  }
  const asking = [...rows, ...rules, ...trades].some((r) => r.state === "asking");
  const answersBody = () => ({ answers: ui.answers, ruleAnswers: Object.values(ui.ruleAnswers), ...tradeoffBody(ui), note: ui.note || "", useDefaults: true });
  const canSend = asking && !!api.answerHearing && !ui.sending;
  const must = rows.filter((r) => r.ask === "must" && !r.answer && r.state !== "ended");
  const bySource = (s) => m.requirements.filter((r) => r.source === s).length;
  fill(el,
    h("section", { class: "in-left" },
      ui.petsSlot || null,
      h("h2", {}, "HIRAKU からの質問", h("small", {}, rows.length || rules.length || trades.length ? `${[rows.length ? `確認したいこと ${rows.length} 件` : "", rules.length ? `ルール ${rules.length} 件` : "", trades.length ? `優先の相談 ${trades.length} 件` : ""].filter(Boolean).join("・")}` : "")),
      h("div", { class: "in-qs", dataset: { keepScroll: "a2-qs" } },
        rows.length || rules.length || trades.length ? [...rows.map((q) => question(q, api, ui)), ...rules.map((q) => ruleQuestion(q, api, ui)), ...trades.map((q) => tradeoffQuestion(q, api, ui))]
          : h("p", { class: "in-empty" }, m.requirements.length ? "足りない情報はないので、そのまま進みます" : "いまは聞くことがありません")),
      rows.length || rules.length || trades.length
        ? h("div", { class: "in-composer" },
            h("textarea", { class: "in-input", rows: 2, placeholder: "ほかに伝えたいこと", value: ui.note || "", oninput: (e) => { ui.note = e.target.value; }, disabled: !canSend }),
            ui.error ? h("p", { class: "in-warn", role: "alert" }, `受け付けられませんでした。${ui.error}`) : null,
            h("div", { class: "in-actions" },
              h("button", { class: "in-btn", disabled: !canSend, onclick: () => useDefaultsOrSay(api, ui, rows) }, "標準の値のまま進める"),
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
              h("tbody", {}, [...m.requirements].sort((a, b) => a.key.localeCompare(b.key)).map((r) => requirementRow(r, m.flags))))
          : h("p", { class: "in-empty" }, "決まったことが入るとここに並びます")),
      h("p", { class: "in-foot" }, `仮の値が ${assumptions(m).length} 件あります。最後の報告に「確かめていないこと」として載ります。行に重ねると、根拠が出ます`),
      h("p", { class: "in-foot in-legend" }, "確かさ: ", [1, 2, 3].map((n) => h("span", {}, h("span", { class: `in-dot t${n}` }, n), ` ${TRUST_JA[n]}  `)))));
}
