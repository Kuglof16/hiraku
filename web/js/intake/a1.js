// A1 - a new design: the request and the reply, the product's properties as chips, the domain cards (N09).

import { FLAGS, SPEED_CLASSES } from "./data.js";
import { fill, h } from "./dom.js";
import { askingPanel } from "./a2.js";
import { DOMAIN_PLAIN, domainCards, saidOf } from "./model.js";

const GLOW_S = 0.5; // seconds the glow of a newly lit card lasts
export const REPLY_STEP_S = 0.5; // with a playback time, the domains answer one after another, this many seconds apart
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

/** The place of a domain's pet: one element per domain for the life of the screen (the card is drawn again, this is not), so
 *  what the host mounted in it stays. data-state: wait | lit | off | draft; a change is also sent as `in-pet-state`
 *  ({ domain, state, litAt }) on the slot, for the host to make the pet hop when its domain is picked. */
function petSlot(c, ui) {
  let slot = ui.petSlots.get(c.domain);
  if (!slot) {
    const name = h("span", { class: "in-pet-name" }, c.shownName);
    slot = h("div", { class: "in-pet", dataset: { slot: "pet", domain: c.domain } }, name);
    slot.nameEl = name;
    ui.petSlots.set(c.domain, slot);
  }
  slot.nameEl.textContent = c.shownName;
  const was = slot.dataset.state;
  slot.dataset.state = c.state;
  if (was !== c.state && typeof CustomEvent === "function" && slot.dispatchEvent) {
    slot.dispatchEvent(new CustomEvent("in-pet-state", { bubbles: true, detail: { domain: c.domain, state: c.state, was: was || null, litAt: c.litAt } }));
  }
  return slot;
}

function card(c, api, ui) {
  const lit = c.state === "lit";
  const timed = ui.time !== null && ui.time !== undefined;
  const just = lit && !timed && !ui.litSeen.has(c.domain); // live: the glow plays once, not at every redraw
  if (lit) ui.litSeen.add(c.domain);
  // with a playback time the glow is a function of it (GLOW_S seconds from the moment Claude picked the domain)
  const glow = lit && timed && c.litAt !== null ? Math.max(0, Math.min(1, 1 - (ui.time - c.litAt) / GLOW_S)) : 0;
  const said = saidOf(c);
  return h("article", { class: `in-card ${c.state}${just ? " just" : ""}`, dataset: { domain: c.domain },
    style: glow > 0 ? { boxShadow: `0 0 0 ${(6 * glow).toFixed(2)}px var(--i-yellow)` } : null },
    petSlot(c, ui),
    h("div", { class: "in-bub" },
      h("header", {},
        h("h3", {}, c.shownName),
        DOMAIN_PLAIN[c.domain] ? h("small", { class: "in-plain" }, DOMAIN_PLAIN[c.domain]) : null,
        c.renamed ? h("small", { class: "in-was" }, `← ${c.ja}`) : null,
        lit ? h("span", { class: "in-mark" }, "調べる") : null),
      c.state === "draft" ? h("p", { class: "in-draft" }, "製品の性質から当てた候補です (まだ決めていません)") : null,
      said.text ? h("p", { class: "in-reason", title: said.full || "" }, said.text) : null,
      lit && c.testIds?.length ? h("details", { class: "in-tests" }, h("summary", {}, "くわしく"),
        c.testCount !== null ? h("span", { class: "in-count", title: "荷重などの条件を作って確かめられる確認の数" }, `確認 ${c.testCount} 件`) : null,
        c.testIds.map((t) => h("code", {}, t)),
        said.full && said.full !== said.text ? h("p", { class: "in-full" }, said.full) : null) : null,
      lit && api.exclude ? h("button", { class: "in-btn small", onclick: () => api.exclude(c.domain) }, "調べない") : null));
}

const ORDER = { lit: 0, draft: 0, off: 1, wait: 2 };

/** With a playback time (a replay, a film) the pets answer in turn: the first picked domain at the time the record picked them,
 *  the next REPLY_STEP_S later, then the ones left off; until its turn a domain is still thinking. Live, all at once.
 *  (From the time of the record's pick on; earlier the cards stay as the model has them.) */
export function inTurn(cards, time, step = REPLY_STEP_S) {
  if (time === null || time === undefined) return cards;
  const base = cards.find((c) => c.state === "lit" && c.litAt !== null)?.litAt ?? null;
  if (base === null || time < base) return cards; // before the record picked them the cards are as the model has them (the glow test)
  let k = 0;
  return cards.map((c) => {
    if (c.state !== "lit" && c.state !== "off") return c;
    const at = base + k++ * step;
    if (time >= at) return c.state === "lit" ? { ...c, litAt: at } : c;
    return { ...c, state: "wait", reason: "", testIds: [], testCount: 0, litAt: null };
  });
}

export function renderA1(el, m, api, ui) {
  const cards = inTurn(domainCards(m).sort((a, b) => ORDER[a.state] - ORDER[b.state]), ui.replyInTurn ? ui.time : null); // the ones that light first (stable: domain order inside)
  const lit = cards.filter((c) => c.state === "lit").length;
  const draftN = cards.filter((c) => c.state === "draft").length;
  const assembly = m.domains?.assembly_test_ids || [];
  const reasons = Object.entries(m.domains?.flag_reasons || {});
  const offN = cards.filter((c) => c.state === "off").length;
  const waitN = cards.filter((c) => c.state === "wait").length;
  const decided = !!m.domains && !m.domainsFromProject;
  const headline = draftN ? { title: "HIRAKU が、何を調べるか考えています", note: `候補 ${draftN} / 7 (製品の性質から。選ぶのはこれから)` }
    : decided && !waitN ? { title: "HIRAKU が調べることを選びました", note: `${lit} 個を調べ${offN ? `、${offN} 個は今回は見ません` : ""}` }
    : m.domains && !waitN ? { title: "調べることは、決まっている内容からです", note: `7 つのうち ${lit} 個` }
    : { title: "HIRAKU が、何を調べるか考えています", note: lit ? `${lit} / 7` : "" };
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
        h("h2", {}, headline.title, h("small", {}, headline.note)),
        h("div", { class: "in-cards" }, cards.map((c) => card(c, api, ui))),
        assembly.length ? h("p", { class: "in-foot" }, "組み立てやすさ・人への当たりは、上の 7 つとは別に見ます ",
          h("details", { class: "in-tests" }, h("summary", {}, "くわしく"), assembly.map((t) => h("code", {}, t)))) : null),
      limitPills.length // not shown until they are decided (a dash says nothing)
        ? h("div", { class: "in-block limits" }, limitPills,
            api.setLimits ? h("button", { class: "in-btn small", onclick: () => api.setLimits(m.limits) }, "上限を変える") : null)
        : null));
}
