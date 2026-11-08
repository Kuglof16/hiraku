// The shelf's 材質 view: the material cards (data/materials/cards/<id>.json, the contract's
// material_card) from GET /api/materials, as a list (by kind of material) and the chosen card.
//
// The values are the cards' as written: nothing is computed and nothing is filled in. A value the
// card does not have has no row. What is made here are words and counts only: the card's tag
// (読んで確かめた when every value's checked is true, else 読んでいない値あり with the counts), and
// the first line, put together from the values' kind and checked.
//
// Colours: red is for an NG judgement only, so a value not read is orange, never red.
// Words for people first; the terms (ヤング率 ...), the sources (English, as the card has them),
// the standard's full name and the condition are under くわしく. The ids, the property keys and the
// card's file only under ?dev=1; a repository path inside a source too (else it reads
// 「HIRAKU の中のファイル」).

import { fill, h } from "./intake/dom.js";
import { getUrl } from "./source.js";

const URLQ = new URLSearchParams(globalThis.location?.search ?? "");
const DEV = URLQ.get("dev") === "1";

// a property: [words for people, the term (under くわしく), unit as shown ("" none)]
export const PROPS = {
  E_MPa: ["変形のしにくさ", "ヤング率 (縦弾性係数) E", "MPa"],
  nu: ["引っぱったときの横の縮み", "ポアソン比 ν", ""],
  density_kg_m3: ["重さ (1 立方メートルあたり)", "密度 ρ", "kg/m³"],
  yield_MPa: ["元に戻らなくなる強さ", "降伏点・耐力", "MPa"],
  tensile_MPa: ["ちぎれる手前の強さ", "引張強さ", "MPa"],
  elongation_pct: ["ちぎれるまでに伸びる割合", "伸び (破断伸び)", "%"],
  fatigue_limit_MPa: ["くり返しの力に耐える強さ", "疲れ限度 (疲労強度)", "MPa"],
  thermal_conductivity_W_mK: ["熱の伝わりやすさ", "熱伝導率", "W/(m·K)"],
  specific_heat_J_kgK: ["温まりにくさ", "比熱", "J/(kg·K)"],
  thermal_expansion_per_K: ["温まったときの伸び", "線膨張係数", "1/K"],
  max_service_degC: ["使ってよい温度の上限", "最高使用温度", "°C"],
  resistivity_ohm_m: ["電気の通しにくさ", "体積抵抗率", "Ω·m"],
};
// the contract's order of the properties (hiraku.schema.json material_card.properties)
const ORDER = Object.keys(PROPS);
const STRENGTH = ["yield_MPa", "tensile_MPa", "fatigue_limit_MPa"];

export const KIND_JA = {
  standard_minimum: "規格の最小値",
  typical: "代表的な値",
  computed: "ほかの値から計算した値",
  assumption: "仮に置いた値",
};
const KIND_TITLE = {
  standard_minimum: "規格が「少なくともこれだけある」と決めている値",
  typical: "この材料ならふつうこのくらい、という値 (保証された値ではない)",
  computed: "カードのほかの値から計算した値",
  assumption: "出典で決まった値ではなく、理由をつけて仮に置いた値",
};

export const CATEGORY_JA = {
  steel: "鉄の材料 (鋼)",
  aluminium: "アルミの材料",
  copper: "銅",
  polymer: "樹脂 (プラスチック)",
  rubber: "ゴム",
  other: "そのほか",
};
const CATEGORIES = Object.keys(CATEGORY_JA);

const props = (card) => ORDER.filter((k) => Object.hasOwn(card.properties ?? {}, k)).map((k) => [k, card.properties[k]]);
const isSecondary = (p) => /^secondary\b/i.test(p.source ?? "");

/** {checked, total} of a card's values, from the records' checked. */
export function checkedCount(card) {
  const all = props(card);
  return { checked: all.filter(([, p]) => p.checked === true).length, total: all.length };
}

/** The card's name for people: name_ja when it has one, else name. */
export const cardName = (card) => card.name_ja || card.name;

/** The tag of a card: "ok" (every value read in its source) or "unread" (some value not), and its words. */
export function cardTag(card) {
  const { checked, total } = checkedCount(card);
  if (checked === total) return { cls: "ok", text: "読んで確かめた", title: `${total} 個の値を、どれも出典を読んで確かめた` };
  return { cls: "unread", text: checked ? `読んでいない値あり (${total - checked} / ${total})` : "読んでいない",
    title: `${total} 個の値のうち ${total - checked} 個は、出典を読んで確かめていない` };
}

// the standard's short name (JIS G 3101) when the card has a short one; else null
function standardShort(card) {
  const m = /^[^(;,]+/.exec(card.standard ?? "");
  const s = m ? m[0].trim() : "";
  return s && s.length <= 24 ? s : null;
}

/** The first line of a card, for someone new: what its values are and whether they were read. */
export const leadLine = (card) => leadParts(card).join("");

/** The first line in its two phrases (what the values are, whether they were read): each is drawn
 *  as one piece, so a line never ends with a few letters of the sentence alone. */
export function leadParts(card) {
  const all = props(card);
  const strong = all.filter(([k]) => STRENGTH.includes(k));
  const pick = strong.length ? strong : all;
  const subject = strong.length ? "この材料の強さの値" : "この材料の値";
  const kinds = [...new Set(pick.map(([, p]) => p.kind))];
  const kindWords = (k) => (k === "standard_minimum" && standardShort(card) ? `${standardShort(card)} の最小値` : KIND_JA[k] ?? k);
  const what = kinds.length === 1 ? `${kindWords(kinds[0])}で` : `${kinds.map(kindWords).join("と")}が混ざっていて`;
  const read = pick.filter(([, p]) => p.checked === true);
  let how;
  if (!read.length) how = "出典をまだ読んで確かめていない";
  else if (read.length < pick.length) how = "出典を読んで確かめた値と、まだ読んでいない値がある";
  else if (read.every(([, p]) => isSecondary(p))) how = "もとの文書を引いた資料で読んで確かめた";
  else if (read.some(([, p]) => isSecondary(p))) how = "出典を読んで確かめた (一部は、もとの文書を引いた資料で)";
  else how = "出典を読んで確かめた";
  // (a space between the Japanese and a Latin word, as the screen's other lines: 「値は JIS G 3101 の」)
  return [`${subject}は${/^[A-Za-z0-9]/.test(what) ? " " : ""}${what}、`, `${how}。`];
}

// a repository path inside a source: shown only under ?dev=1
const REPO_PATH_RE = /\b(?:hiraku|data|web|tests|docs|viewer|promo)\/[\w./-]+/g;
const sourceText = (s) => (DEV ? s : String(s).replace(REPO_PATH_RE, "HIRAKU の中のファイル"));
const URL_RE = /https?:\/\/[^\s;,)]+[^\s;,).]/g;

/** el: where to draw; onread: called once the cards are read (the shelf's bar shows their counts). */
export function mountMaterials(el, onread = () => {}) {
  const ui = { selected: URLQ.get("material") || null };
  const got = { asked: false, cards: [], skipped: [], error: null, ready: false };
  const opened = new Set();
  let done;
  const loaded = new Promise((r) => (done = r));

  function fold(key, cls, label, ...kids) {
    return h("details", { class: cls, open: opened.has(key), dataset: { fold: key },
        ontoggle: (e) => { if (e.target.open) opened.add(key); else opened.delete(key); } },
      h("summary", {}, label), ...kids);
  }

  const note = h("p", { class: "sh-note" });
  const list = h("ul", { class: "sh-tree mt-list", dataset: { keepScroll: "a4-materials" } });
  const detailBox = h("section", { class: "sh-right mt-right" });
  fill(el, h("div", { class: "sh-body mt-body" }, h("section", { class: "sh-left" }, note, list), detailBox));

  async function ask() {
    got.asked = true;
    try {
      const res = await fetch(getUrl("/api/materials"), { cache: "no-store" });
      if (!res.ok) throw new Error(`${res.status}`);
      const body = await res.json();
      Object.assign(got, { cards: body.cards ?? [], skipped: body.skipped ?? [], error: null });
    } catch (e) {
      got.error = String(e.message ?? e);
    }
    got.ready = true;
    done();
    render();
    onread();
  }

  function tag(card) {
    const t = cardTag(card);
    return h("span", { class: `mt-tag ${t.cls}`, title: t.title }, t.text);
  }

  function row(card) {
    return h("li", { class: `sh-tool mt-row${ui.selected === card.id ? " sel" : ""}`, dataset: { material: card.id },
        onclick: () => { ui.selected = card.id; render(); } },
      h("div", { class: "sh-name" }, h("b", {}, cardName(card)), DEV ? h("code", {}, card.id) : null, tag(card)));
  }

  function branch(cat, cards) {
    return h("li", { class: "sh-node", dataset: { category: cat } },
      h("div", { class: "sh-head mt-head" }, h("b", {}, CATEGORY_JA[cat] ?? cat), h("span", { class: "sh-n" }, `${cards.length} 種`)),
      h("ul", {}, cards.map(row)));
  }

  function valueRow(card, key, p) {
    const [words, term, unit] = PROPS[key];
    const k = `${card.id}|${key}`;
    const urls = [...String(p.source).matchAll(URL_RE)].map((m) => m[0]);
    return h("tr", { class: p.checked ? "ok" : "unread", dataset: { prop: key } },
      h("td", { class: "mt-what" }, words,
        fold(k, "more sh-more mt-more", "くわしく",
          h("div", { class: "mt-term" }, term, DEV ? h("code", {}, key) : null),
          h("div", { class: "mt-src" }, h("span", { class: "sh-dim" }, "出典: "), sourceText(p.source)),
          urls.map((u) => h("div", { class: "mt-url" }, h("a", { href: u, target: "_blank", rel: "noopener" }, u))),
          p.checked && p.checked_on ? h("div", { class: "sh-dim" }, `読んで確かめた日: ${p.checked_on}`) : null)),
      h("td", { class: "num mt-val", dataset: { value: String(p.value) } }, String(p.value)),
      h("td", { class: "mt-unit" }, unit),
      h("td", { class: "mt-kind", title: KIND_TITLE[p.kind] ?? "" }, KIND_JA[p.kind] ?? p.kind),
      h("td", {}, h("span", { class: `mt-tag ${p.checked ? "ok" : "unread"}` }, p.checked ? "読んで確かめた" : "読んでいない")));
  }

  function detail(card) {
    if (!card) return h("p", { class: "sh-dim" }, "左の材料を選ぶと、カードに書いてある値と、その出どころが出ます");
    const { checked, total } = checkedCount(card);
    const std = standardShort(card);
    return h("div", { class: "sh-detail mt-detail", dataset: { material: card.id } },
      h("header", {}, h("h3", {}, cardName(card)), tag(card)),
      h("p", { class: "mt-lead" }, leadParts(card).map((x) => h("span", { class: "mt-ph" }, x))),
      h("dl", {},
        h("dt", {}, "材料の種類"), h("dd", {}, CATEGORY_JA[card.category] ?? card.category),
        std ? [h("dt", {}, "規格"), h("dd", {}, std)] : null,
        h("dt", {}, "読んで確かめた"), h("dd", { class: "mt-count" }, `${total} 個の値のうち ${checked} 個`)),
      fold(`${card.id}|card`, "more sh-more mt-more mt-card-more", "くわしく",
        card.standard ? h("div", {}, h("span", { class: "sh-dim" }, "規格: "), card.standard) : null,
        card.condition ? h("div", {}, h("span", { class: "sh-dim" }, "値が成り立つ条件: "), sourceText(card.condition)) : null,
        card.name_ja && card.name ? h("div", {}, h("span", { class: "sh-dim" }, "英語の名前: "), card.name) : null,
        DEV ? h("div", {}, h("code", {}, `data/materials/cards/${card.id}.json`)) : null),
      h("h4", {}, `カードにある値 (${total} 個)`),
      h("table", { class: "sh-checks mt-values" },
        h("thead", {}, h("tr", {}, ["何の値か", "値", "単位", "どんな値か", "出典を読んだか"].map((x) => h("th", {}, x)))),
        h("tbody", {}, props(card).map(([key, p]) => valueRow(card, key, p)))));
  }

  function render() {
    const { cards } = got;
    note.hidden = got.ready && !got.error && !(DEV && got.skipped.length);
    note.className = `sh-note${got.error ? " sh-err" : ""}`;
    note.textContent = got.error ? `材料カードを読めませんでした (${got.error})`
      : !got.ready ? "材料カードを読んでいます…"
      : DEV && got.skipped.length ? `読めなかったカード: ${got.skipped.map((s) => `${s.file} (${s.error})`).join("、")}` : "";
    const cats = [...CATEGORIES, ...[...new Set(cards.map((c) => c.category))].filter((c) => !CATEGORIES.includes(c))];
    const branches = cats.map((c) => [c, cards.filter((x) => x.category === c)]).filter(([, xs]) => xs.length);
    fill(list, branches.length ? branches.map(([c, xs]) => branch(c, xs)) : got.ready && !got.error ? h("li", { class: "sh-dim" }, "材料カードがありません") : null);
    fill(detailBox, detail(cards.find((c) => c.id === ui.selected) ?? null));
  }

  return {
    /** Draw again; asks the server for the cards once. */
    render() {
      if (!got.asked) ask();
      render();
    },
    /** The cards' counts for the bar: {cards, ok} (ok: every value read), or null before they are read. */
    summary() {
      if (!got.ready || got.error) return null;
      return { cards: got.cards.length, ok: got.cards.filter((c) => cardTag(c).cls === "ok").length };
    },
    /** Resolves once the cards are read (or could not be). */
    async settled() {
      if (!got.asked) ask();
      await loaded;
      render();
    },
  };
}
