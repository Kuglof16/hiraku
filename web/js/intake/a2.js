// A2 - hearing: the missing requirements asked once (left), the requirements and where each came from (right) (N14).

import { fill, h } from "./dom.js";
import { assumptions, hearingRows, showValue, SOURCE_JA } from "./model.js";

const STATE_JA = { asking: "質問中", answered: "答えた", defaulted: "既定値のまま", open: "答えなし" };

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
        class: "in-other", type: "text", placeholder: "その他 (書く)", value: picked && !(q.choices || []).some((c) => String(c.value) === String(picked.value)) ? picked.value : "",
        oninput: (e) => { const v = e.target.value.trim(); if (v) ui.answers[q.req_key] = { value: v }; else delete ui.answers[q.req_key]; },
      })
    : null;
  return h("article", { class: `in-q ${q.state}`, dataset: { key: q.req_key } },
    h("header", {},
      h("span", { class: `in-badge ${q.ask}` }, q.ask === "must" ? "必須" : "確認"),
      h("code", {}, q.req_key),
      h("span", { class: "in-state" }, STATE_JA[q.state])),
    h("p", { class: "in-qtext" }, q.text),
    choiceButtons.length ? h("div", { class: "in-choices" }, choiceButtons) : null,
    other,
    def ? h("p", { class: "in-default" }, `答えが無ければ ${showValue(def.value, def.unit)} を使います`, h("small", {}, ` 信頼度 ${def.trust}`)) : null,
    q.ask === "must" && !q.answer ? h("p", { class: "in-default must" }, "答えが無いと先へ進めません") : null,
    q.answer
      ? h("p", { class: "in-answer" }, h("b", {}, q.answer.source === "hearing" ? "答え" : "既定値"), " ",
          choiceLabel(q, q.answer.value) || showValue(q.answer.value, q.answer.unit))
      : null);
}

function requirementRow(r) {
  const lowTrust = r.trust === 3;
  return h("tr", { class: `${r.fresh ? "fresh" : ""}${lowTrust ? " low" : ""}`, title: r.basis || "" },
    h("td", {}, h("code", {}, r.key)),
    h("td", {}, r.name),
    h("td", { class: "val" }, showValue(r.value, r.unit)),
    h("td", {}, h("span", { class: `in-src ${r.source}` }, SOURCE_JA[r.source] || r.source)),
    h("td", { class: "trust" }, h("span", { class: `in-dot t${r.trust}`, title: ["", "一次の資料", "二次の資料", "仮定 (確かめていない)"][r.trust] }, r.trust)));
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
  const asking = rows.some((r) => r.state === "asking");
  const canSend = asking && !!api.answerHearing && !ui.sending;
  const must = rows.filter((r) => r.ask === "must" && !r.answer);
  const bySource = (s) => m.requirements.filter((r) => r.source === s).length;
  fill(el,
    h("section", { class: "in-left" },
      h("h2", {}, "ヒアリング", h("small", {}, rows.length ? `${rows.length} 件を、まとめて 1 回` : "")),
      h("div", { class: "in-qs", dataset: { keepScroll: "a2-qs" } },
        rows.length ? rows.map((q) => question(q, api, ui))
          : h("p", { class: "in-empty" }, m.requirements.length ? "足りない要件はありません。要件のファイルのまま進みます" : "要件はまだありません")),
      rows.length
        ? h("div", { class: "in-composer" },
            h("textarea", { class: "in-input", rows: 2, placeholder: "ほかに伝えること", value: ui.note || "", oninput: (e) => { ui.note = e.target.value; }, disabled: !canSend }),
            ui.error ? h("p", { class: "in-warn", role: "alert" }, `受け付けられませんでした: ${ui.error}`) : null,
            h("div", { class: "in-actions" },
              h("button", { class: "in-btn", disabled: !canSend, onclick: () => submit(api, ui, { answers: {}, note: ui.note || "", useDefaults: true }) }, "既定値のまま進める"),
              h("button", { class: "in-btn primary", disabled: !canSend,
                onclick: () => submit(api, ui, { answers: ui.answers, note: ui.note || "", useDefaults: true }) }, ui.sending ? "送っています…" : "答えて進める")))
        : null,
      !rows.length && m.hearing.answered?.answers?.length
        ? h("p", { class: "in-foot" }, `既定値で足したもの: ${m.hearing.answered.answers.map((a) => `${a.key} ${a.name}`).join("・")}`)
        : null,
      m.hearing.answered?.note ? h("p", { class: "in-foot" }, `ほかに伝えたこと: ${m.hearing.answered.note}`) : null,
      must.length ? h("p", { class: "in-warn" }, `必須の ${must.map((q) => q.req_key).join("・")} に答えが無いので、先へ進めません`) : null),
    h("section", { class: "in-right" },
      h("h2", {}, "要件", h("small", {}, `${m.requirements.length} 件  ユーザー ${bySource("user")}・ヒアリング ${bySource("hearing")}・既定値 ${bySource("default")}`)),
      h("div", { class: "in-table", dataset: { keepScroll: "a2-table" } },
        m.requirements.length
          ? h("table", {},
              h("thead", {}, h("tr", {}, ["番号", "項目", "値", "出どころ", "信頼度"].map((t) => h("th", {}, t)))),
              h("tbody", {}, [...m.requirements].sort((a, b) => a.key.localeCompare(b.key)).map(requirementRow)))
          : h("p", { class: "in-empty" }, "要件が入るとここに並びます")),
      h("p", { class: "in-foot" }, `信頼度 3 は仮定です (${assumptions(m).length} 件)。報告の「確かめていないこと」に残ります。行に重ねると出どころが出ます`)));
}
