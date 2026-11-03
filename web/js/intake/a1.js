// A1 - a new design: the request and the reply, the product's properties as chips, the domain cards (N09).

import { FLAGS, SPEED_CLASSES } from "./data.js";
import { fill, h } from "./dom.js";
import { domainCards } from "./model.js";

const GLOW_S = 0.5; // seconds the glow of a newly lit card lasts
const PLACEHOLDER = "作りたいものを書いてください (例: ギターを作りたい)";

function bubble(msg) {
  if (msg.who === "note") return h("div", { class: "in-note" }, msg.text);
  return h("div", { class: `in-msg ${msg.who}` }, h("div", { class: "in-who" }, msg.who === "user" ? "あなた" : "まとめ役"), h("p", {}, msg.text));
}

function composer(api, ui) {
  const box = h("textarea", {
    class: "in-input", rows: 3, placeholder: PLACEHOLDER, value: ui.draft || "",
    oninput: (e) => { ui.draft = e.target.value; },
  });
  const send = h("button", {
    class: "in-btn primary", disabled: !api.send, title: api.send ? "" : "この画面では送れません (記録の再生中)",
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
      lit && c.testCount !== null ? h("span", { class: "in-count", title: "当てはまる試験のうち、荷重条件を作れるものの数" }, `試験 ${c.testCount}`) : null),
    c.reason ? h("p", { class: "in-reason", title: c.reason }, c.reason) : null,
    lit && c.testIds?.length ? h("div", { class: "in-tests" }, c.testIds.map((t) => h("code", {}, t))) : null,
    lit && api.exclude ? h("button", { class: "in-btn small", onclick: () => api.exclude(c.domain) }, "外す") : null,
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
  fill(el,
    h("section", { class: "in-chat" },
      h("h2", {}, "依頼と返事"),
      h("div", { class: "in-msgs", dataset: { keepScroll: "a1-msgs" } },
        m.chat.length ? m.chat.map(bubble) : h("p", { class: "in-empty" }, "作りたいものを書くと、まとめ役が返事をしながら、使う分野を選びます")),
      composer(api, ui)),
    h("section", { class: "in-side", dataset: { keepScroll: "a1-side" } },
      h("div", { class: "in-block" },
        h("h2", {}, "製品の性質", h("small", {}, api.editFlags ? "押すと直せます" : "読み取った性質")),
        h("div", { class: "in-chips" }, chips(m, api), h("span", { class: "in-sep" }), speedChips(m)),
        reasons.length
          ? h("ul", { class: "in-why" }, reasons.map(([f, why]) => h("li", {}, h("b", {}, FLAGS.find((x) => x.name === f)?.symbol || f), why)))
          : null),
      h("div", { class: "in-block" },
        h("h2", {}, "分野", h("small", {}, draftN ? `候補 ${draftN} / 7 (製品の性質から。選ぶのはこれから)` : m.domains && !m.domainsFromProject ? `${lit} / 7 が灯りました` : m.domains ? `${lit} / 7 (要件のファイルから)` : "")),
        h("div", { class: "in-cards" }, cards.map((c) => card(c, api, ui))),
        assembly.length ? h("p", { class: "in-foot" }, "組み立て・人 (分野には数えない): ", assembly.map((t) => h("code", {}, t))) : null),
      h("div", { class: "in-block limits" },
        h("span", { class: "in-pill" }, `繰り返し ${m.limits.round ?? "—"} 回まで`),
        h("span", { class: "in-pill" }, `費用 ${m.limits.cost ?? "—"} USD まで`),
        api.setLimits ? h("button", { class: "in-btn small", onclick: () => api.setLimits(m.limits) }, "上限を変える") : null)));
}
