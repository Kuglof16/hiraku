// A1 - a new design: the request and the reply, the product's properties as chips, the domain cards (N09).

import { FLAGS, SPEED_CLASSES } from "./data.js";
import { fill, h } from "./dom.js";
import { askingPanel } from "./a2.js";
import { domainCards } from "./model.js";

const GLOW_S = 0.5; // seconds the glow of a newly lit card lasts
const PLACEHOLDER = "作りたいものを書いてください。例: 林道を走れる小さな 2 人乗りの電動車";

function bubble(msg) {
  if (msg.who === "note") return h("div", { class: "in-note" }, msg.text);
  return h("div", { class: `in-msg ${msg.who}` }, h("div", { class: "in-who" }, msg.who === "user" ? "あなた" : "HIRAKU"), h("p", {}, msg.text));
}

function composer(api, ui) {
  const box = h("textarea", {
    class: "in-input", rows: 3, placeholder: PLACEHOLDER, value: ui.draft || "",
    oninput: (e) => { ui.draft = e.target.value; },
  });
  const send = h("button", {
    class: "in-btn primary", disabled: !api.send, title: api.send ? "" : "この画面では送れません (記録の再生中です)",
    onclick: () => {
      const text = (ui.draft || "").trim();
      if (!text) return;
      ui.draft = "";
      box.value = "";
      api.send(text);
    },
  }, "送る");
  return h("div", { class: "in-composer" }, box, send);
}

function chips(m, api) {
  const why = m.domains?.flag_reasons || {};
  const edit = !!api.editFlags;
  return FLAGS.map((f) => {
    const on = !!m.flags[f.name];
    const tip = [`${f.symbol}: ${f.question}`, why[f.name]].filter(Boolean).join("\n");
    return h("button", {
      class: `in-chip ${on ? "on" : "off"}${edit ? " editable" : ""}`, title: tip, type: "button",
      disabled: !edit, onclick: () => api.editFlags({ ...m.flags, [f.name]: !on }),
    }, h("b", {}, f.symbol), h("span", {}, f.label));
  });
}

function speedChips(m) {
  return SPEED_CLASSES.map((s) =>
    h("span", { class: `in-chip speed ${m.speedClass === s.name ? "on" : "off"}`, title: s.meaning }, h("b", {}, s.symbol), h("span", {}, s.meaning.split(" (")[0])));
}

function card(c, api, ui) {
  const lit = c.state === "lit";
  const timed = ui.time !== null && ui.time !== undefined;
  const just = lit && !timed && !ui.litSeen.has(c.domain); // live: the glow plays once, not at every redraw
  if (lit) ui.litSeen.add(c.domain);
  // with a playback time the glow is a function of it (GLOW_S seconds from the moment Claude picked the domain)
  const glow = lit && timed && c.litAt !== null ? Math.max(0, Math.min(1, 1 - (ui.time - c.litAt) / GLOW_S)) : 0;
  return h("article", { class: `in-card ${c.state}${just ? " just" : ""}`, dataset: { domain: c.domain },
    style: glow > 0 ? { boxShadow: `0 0 0 ${(6 * glow).toFixed(2)}px var(--i-yellow)` } : null },
    h("header", {},
      h("h3", {}, c.shownName),
      c.renamed ? h("small", { class: "in-was" }, `← ${c.ja}`) : null,
      lit && c.testCount !== null ? h("span", { class: "in-count", title: "荷重などの条件を作って確かめられる確認の数" }, `確認 ${c.testCount} 件`) : null),
    c.reason ? h("p", { class: "in-reason", title: c.reason }, c.reason) : null,
    lit && c.testIds?.length ? h("details", { class: "in-tests" }, h("summary", {}, "くわしく"), c.testIds.map((t) => h("code", {}, t))) : null,
    lit && api.exclude ? h("button", { class: "in-btn small", onclick: () => api.exclude(c.domain) }, "調べない") : null,
    c.state === "draft" ? h("p", { class: "in-draft" }, "製品の性質から当てた候補 (まだ決めていません)") : null,
    c.state === "wait" ? h("p", { class: "in-reason" }, "まだ決めていません") : null);
}

const ORDER = { lit: 0, draft: 0, off: 1, wait: 2 };

export function renderA1(el, m, api, ui) {
  const cards = domainCards(m).sort((a, b) => ORDER[a.state] - ORDER[b.state]); // the ones that light first (stable: domain order inside)
  const lit = cards.filter((c) => c.state === "lit").length;
  const draftN = cards.filter((c) => c.state === "draft").length;
  const assembly = m.domains?.assembly_test_ids || [];
  const reasons = Object.entries(m.domains?.flag_reasons || {});
  const ask = askingPanel(m, api, ui);
  el.classList?.[ask ? "add" : "remove"]("has-ask");
  const want = m.want || m.domains?.want || [...m.chat].reverse().find((c) => c.who === "user")?.text || "";
  const nothingRead = !!m.domains && !m.domainsFromProject && FLAGS.every((f) => !m.flags?.[f.name]) && !!want;
  const limitPills = [
    m.limits.round != null ? h("span", { class: "in-pill" }, `やり直しは ${m.limits.round} 回まで`) : null,
    m.limits.cost != null ? h("span", { class: "in-pill" }, `費用は ${m.limits.cost} USD まで`) : null,
  ].filter(Boolean);
  fill(el,
    ask,
    h("section", { class: "in-chat" },
      h("h2", {}, "作りたいものを伝える"),
      h("div", { class: "in-msgs", dataset: { keepScroll: "a1-msgs" } },
        m.chat.length ? m.chat.map(bubble) : h("p", { class: "in-empty" }, "作りたいものを書くと、HIRAKU が調べる点を選びます")),
      composer(api, ui)),
    h("section", { class: "in-side", dataset: { keepScroll: "a1-side" } },
      h("div", { class: "in-block" },
        h("h2", {}, "読み取ったこと", h("small", {}, api.editFlags ? "違っていたら押して直せます" : "")),
        nothingRead
          ? h("p", { class: "in-warn soft", role: "status" }, `言葉から読み取れたことがありません。Claude を使わないときは、決まった言葉でしか読めません。「${want.length > 24 ? want.slice(0, 24) + "…" : want}」からは読み取れなかったので、下の印から、当てはまるものを選んでください。`)
          : null,
        h("div", { class: "in-chips" }, chips(m, api), h("span", { class: "in-sep" }), speedChips(m)),
        reasons.length
          ? h("ul", { class: "in-why" }, reasons.map(([f, why]) => h("li", {}, h("b", {}, FLAGS.find((x) => x.name === f)?.symbol || f), why)))
          : null),
      h("div", { class: "in-block" },
        h("h2", {}, "分野 (どんな点を調べるか)", h("small", {}, draftN ? `候補 ${draftN} / 7 (製品の性質から。選ぶのはこれから)` : m.domains && !m.domainsFromProject ? `7 つのうち ${lit} 個を調べます` : m.domains ? `7 つのうち ${lit} 個 (決まっている内容から)` : "")),
        h("div", { class: "in-cards" }, cards.map((c) => card(c, api, ui))),
        assembly.length ? h("p", { class: "in-foot" }, "組み立てやすさ・人への当たりは、上の 7 つとは別に見ます ",
          h("details", { class: "in-tests" }, h("summary", {}, "くわしく"), assembly.map((t) => h("code", {}, t)))) : null),
      limitPills.length // not shown until they are decided (a dash says nothing)
        ? h("div", { class: "in-block limits" }, limitPills,
            api.setLimits ? h("button", { class: "in-btn small", onclick: () => api.setLimits(m.limits) }, "上限を変える") : null)
        : null));
}
