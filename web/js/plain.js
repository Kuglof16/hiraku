// Plain words: a record's ids and English record words taken out of a sentence shown to people.
// One rule for ③'s sentence card under the 3D (benchnote.js), the footer's activity line
// (hookline.js footerActivity, app.js renderStatus) and the left column's log (app.js logItem).
//
// What counts as record words (hasRecordWords): an id, letters joined by _ or . (rule_check,
// build_version, side_post_LS2_1, step_climb.pushed_wheel_load), a record's number (chg-0006,
// ver-0048, cmp-0027, ana-0083), a load case's id (R1-step100, H7-handhold), or a lower-case English
// word of three letters or more that is not the screen's own (OWN_WORDS).
//
// What is taken out (wrapped in span.rec, which a page hides where it wants plain words; under
// ?dev=1 it shows, and the whole text goes to くわしく or a title):
//   1. an aside in brackets whose words (its own asides taken out first) hold record words, with
//      the space before it;
//   2. a sentence (up to 。 or a line break) whose words still hold three English words in a row
//      (goes 21.4 mm into floor ...);
//   3. each record word left on its own (an id, an English word): said by its Japanese name when
//      it has one (a quantity: labels.js quantityName, max_utilisation -> 応力と許しの比 (最大);
//      a page may pass more names: opts.name), else taken out with a list's separator after it
//      (、・,) and an " = " after it (lighter_side_load = 756 N -> 756 N).
// The name put in is in span.rn, which ?dev=1 hides (the record's own word shows there).
// Numbers and units, OK / NG and vN are never record words, so they stay.

import { BASE_JA, esc, plainToolName, quantityName, roleNames } from "./labels.js";

// Lower-case words of the screen's own, not a record's English: units and names said as they are.
const OWN_WORDS = new Set(["von", "hooks", "deg", "rpm", "rad"]);
// an id: letters joined by _ or . (rule_check, battery.battery2, side_post_LS2_1), a record's number
// (chg-0006, cmp-0027, pred-0017), a load case's id (R1-step100, H7-handhold, S1-edge1350N), a hooks
// rule's id (HK3) or a calculation's step by its number (K2: said by its name, labels.js BASE_JA)
const ID_SRC = String.raw`[A-Za-z][A-Za-z0-9]*(?:[_.][A-Za-z0-9]+)+|\b[a-z]{2,8}-\d{2,}\b|\b[A-Z]{1,3}\d+[a-z]?-[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*|\b(?:HK\d|K[0-5])\b`;
const ID = new RegExp(ID_SRC);
const IDS = new RegExp(ID_SRC, "g");
const WORD = /\b[a-z]{3,}\b/g;
// a token to take out: an id (with a "part:" before it), or a lower-case word
const TOKEN = new RegExp(String.raw`(?:\b[a-z]+:)?(?:${ID_SRC})|\b[a-z]{3,}\b`, "g");
// three English words in a row: lower-case words (two letters or more) with only ASCII between them
const ENGLISH_RUN = /\b[a-z]{2,}\b(?:[ -~]*?\b[a-z]{2,}\b){2,}/;
// a record's number (chg-0006, ver-0048, pred-0119)
const RECORD_NO = /\b[a-z]{2,8}-\d{3,}\b/;
const RECORD_NOS = /\b[a-z]{2,8}-\d{3,}\b/g;
const SEP_AFTER =/^\s*(?:[=＝]\s*|[、・,，/]\s*)?/;

// The text holds a record's id or an English word (three or more lower-case letters, not the screen's own).
export function hasRecordWords(text) {
  const t = String(text ?? "");
  if (ID.test(t)) return true;
  return (t.match(WORD) ?? []).some((w) => !OWN_WORDS.has(w));
}

function englishRun(text) {
  const m = String(text).replace(IDS, " ").match(ENGLISH_RUN);
  if (!m) return false;
  return (m[0].match(/\b[a-z]{2,}\b/g) ?? []).filter((w) => !OWN_WORDS.has(w)).length >= 3;
}

// A record word's Japanese name: a quantity's (labels.js), its asides of record words taken out;
// null when it has none.
function quantityWord(w) {
  const n = quantityName(w);
  if (!n || n === w) return null;
  const plain = maskOf(n, false).text;
  return plain && !hasRecordWords(plain) ? plain : null;
}

// The words of a page's own list (a design's part and member ids: battery2, rail_L) as a pattern
// that takes them as record words too, the longest first, each whole (not inside a longer id).
const termsCache = new WeakMap();
function termsToken(terms) {
  if (!terms?.length) return TOKEN;
  if (termsCache.has(terms)) return termsCache.get(terms);
  const alt = [...new Set(terms)].filter((t) => /^[A-Za-z][\w.]*$/.test(t)).sort((a, b) => b.length - a.length).map((t) => t.replace(/\./g, "\\.")).join("|");
  const re = alt ? new RegExp(String.raw`(?<![\w.])(?:${alt})(?![\w])|${TOKEN.source}`, "g") : TOKEN;
  termsCache.set(terms, re);
  return re;
}

// What is taken out: one flag per letter of s (true where it is out), and the names put in at a
// record word's start (index -> name). name: opts.name, or false for no step 3 (a name's own asides).
// terms: more words to take as record words (termsToken). A name the same as the words right
// before it (「解析 ana-0091」) is not said twice: the word is taken out without it.
function maskOf(text, name = null, terms = null) {
  const s = String(text ?? "");
  const out = new Array(s.length).fill(false);
  const names = new Map();
  const named = new Set(); // the letters of a record word said by its name
  const gone = new Set(); // the letters of a record word taken out without a name
  const visible = (a, b) => {
    let t = "";
    for (let i = a; i < b; i++) if (!out[i]) t += s[i];
    return t;
  };
  const hide = (a, b) => {
    for (let i = a; i < b; i++) out[i] = true;
  };
  // 1. the asides, the innermost first (in the order they close); with a page's names (name a
  //    function), an aside whose record words all have a name stays (they are said by it in 3)
  const tokenRe = termsToken(terms);
  const named1 = typeof name === "function"
    ? (t) => t.replace(tokenRe, (w) => name(w) || quantityWord(w) || w)
    : (t) => t;
  const open = [];
  for (let i = 0; i < s.length; i++) {
    if (s[i] === "(" || s[i] === "（") open.push(i);
    else if ((s[i] === ")" || s[i] === "）") && open.length) {
      const a = open.pop();
      const inside = visible(a, i + 1);
      // (an aside of nothing but records' numbers, 「(ver-0002)」「(判定、比べ、原因)」, goes as before)
      const onlyNumbers = RECORD_NO.test(inside) && !inside.replace(RECORD_NOS, "").replace(/[\s()（）、・,，/]/g, "");
      if (!onlyNumbers && !hasRecordWords(named1(inside))) continue;
      let from = a;
      while (from > 0 && /\s/.test(s[from - 1]) && !out[from - 1]) from--;
      let to = i + 1;
      if (from < a && s[to] === " ") to++; // (「値 (ana-0076 の …) は」 reads 「値は」)
      hide(from, to);
    }
  }
  // 2. the sentences still in English
  let a = 0;
  for (let i = 0; i <= s.length; i++) {
    if (i < s.length && !/[。．！？\n]/.test(s[i])) continue;
    const end = Math.min(s.length, i + 1);
    if (englishRun(visible(a, end))) hide(a, end);
    a = end;
  }
  // 3. the record words left one by one: by their name, else out (with the separator after them)
  if (name !== false) {
    const own = new Set(terms ?? []);
    for (const m of s.matchAll(termsToken(terms))) {
      const [from, to] = [m.index, m.index + m[0].length];
      if (visible(from, to) !== m[0]) continue; // (part of it already out)
      if (!ID.test(m[0]) && !own.has(m[0]) && OWN_WORDS.has(m[0])) continue;
      hide(from, to);
      // (judge-words: the stage names K0..K5 by BASE_JA; 7c6: not the same words twice in a row)
      let said = (name && name(m[0])) || BASE_JA[m[0]] || quantityWord(m[0]);
      // (the same words just before it, as shown with the names put in so far: not twice)
      if (said) {
        let before = "";
        for (let i = 0; i < from; i++) {
          if (names.has(i)) before += names.get(i);
          if (!out[i]) before += s[i];
        }
        if (before.trimEnd().endsWith(said)) said = null;
      }
      if (said) {
        names.set(from, said);
        for (let i = from; i < to; i++) named.add(i);
        continue;
      }
      // (the separator after it and after the asides taken out with it: 「R4-rops (…) / R7-…」)
      let k = to;
      while (k < s.length && out[k]) k++;
      for (let i = from; i < to; i++) gone.add(i);
      const sep = s.slice(k).match(SEP_AFTER)[0];
      if (sep && visible(k, k + sep.length) === sep) hide(k, k + sep.length);
    }
  }
  // 3b. a sentence or line that, with a record word taken out (not said by a name), starts with a
  //     particle or ends with an arrow: it had the word as its subject or its end, and reads broken
  //     without it (「・frame_static.x で曲げを入れる」 -> 「・で曲げを入れる」; 「そのあと run_x →」)
  if (gone.size) {
    let a = 0;
    for (let i = 0; i <= s.length; i++) {
      if (i < s.length && !/[。．！？\n]/.test(s[i])) continue;
      const end = Math.min(s.length, i + 1);
      let lost = false;
      let v = "";
      for (let k = a; k < end; k++) {
        if (gone.has(k)) lost = true;
        if (names.has(k)) v += names.get(k);
        if (!out[k]) v += s[k];
      }
      v = v.replace(/[。．！？\n]$/, "");
      if (lost && (/^[\s・「『(（\-–—]*[でがはをにとのへも](?![a-zA-Z0-9])/.test(v) || /[→=<>≤≥]\s*$/.test(v))) {
        hide(a, end);
        for (let k = a; k < end; k++) names.delete(k), named.delete(k); // (the names put in inside it go with it)
      }
      a = end;
    }
  }
  // 4. a mark left right after another (「。。」「: 、」「試験: / / 。」) or at the start
  if (out.some(Boolean)) {
    let prev = ""; // the last letter shown on the line (not a space)
    let cut = false; // something taken out since it
    for (let i = 0; i < s.length; i++) {
      if (names.has(i)) {
        prev = "x";
        cut = false;
      }
      if (s[i] === "\n") {
        prev = "";
        cut = false;
        continue;
      }
      if (out[i] && !named.has(i)) cut = true;
      if (out[i] || /\s/.test(s[i])) continue;
      // (a mark at the line's start only when what was before it was taken out: a list's ・ stays)
      // (「NG: 。S2」: the 。 stays, the colon goes in 5)
      const end = /[。．！？]/.test(s[i]) && /[:：]/.test(prev);
      if (/[、。・/,，]/.test(s[i]) && cut && !end && (!prev || /[、。・/,，:：(（「]/.test(prev))) {
        hide(i, i + 1);
        continue;
      }
      prev = s[i];
      cut = false;
    }
  }
  // 5. a colon left with nothing after it (up to 。 or the end), or right after another colon
  //    (「まだ判定していない試験: R7-hand-300N、…」 -> 「まだ判定していない試験」)
  if (out.some(Boolean)) {
    for (let i = 0; i < s.length; i++) {
      if (out[i] || !/[:：]/.test(s[i])) continue;
      let j = i + 1;
      let rest = "";
      while (j < s.length && !/[。\n]/.test(s[j])) {
        if (!out[j] || names.has(j)) rest += names.has(j) ? names.get(j) : s[j];
        j++;
      }
      let k = i - 1;
      while (k >= 0 && (out[k] || /\s/.test(s[k])) && !names.has(k)) k--;
      const afterColon = k >= 0 && /[:：]/.test(s[k]) && !out[k];
      if (!rest.trim() || afterColon) hide(i, i + 1);
    }
  }
  let t = "";
  for (let i = 0; i < s.length; i++) {
    if (names.has(i)) t += names.get(i);
    if (!out[i]) t += s[i];
  }
  return { s, out, names, text: t.replace(/[ \t]{2,}/g, " ").replace(/[ \t]+([、。:：])/g, "$1").trim() };
}

// ?video=1 (the film's frames as they were filmed, app.js): ③'s card marks as before this rule
// (each aside in brackets, then each sentence, that holds record words; no names put in), and the
// log and the footer are as recorded (app.js).
let FILM = false;
export function filmWords(on) {
  FILM = !!on;
}
export const isFilmWords = () => FILM;
function filmMarks(text) {
  const s = String(text ?? "");
  const pieces = []; // [text, rec]
  const ASIDE = /\s*[(（][^()（）]*[)）]\s?/g;
  let at = 0;
  for (const m of s.matchAll(ASIDE)) {
    if (!filmRecordWords(m[0])) continue;
    pieces.push([s.slice(at, m.index), false], [m[0], true]);
    at = m.index + m[0].length;
  }
  pieces.push([s.slice(at), false]);
  const out = [];
  let sentence = [];
  const flush = () => {
    if (!sentence.length) return;
    const plain = sentence.filter(([, r]) => !r).map(([x]) => x).join("");
    const rec = filmRecordWords(plain);
    for (const [x, r] of sentence) out.push(r || rec ? `<span class="rec">${esc(x)}</span>` : esc(x));
    sentence = [];
  };
  for (const [x, r] of pieces) {
    if (r) {
      sentence.push([x, true]);
      continue;
    }
    for (const part of x.split(/(?<=[。．！？])/)) {
      if (!part) continue;
      sentence.push([part, false]);
      if (/[。．！？]$/.test(part)) flush();
    }
  }
  flush();
  return out.join("");
}
// (the film's: without the load cases' ids, R1-step100)
const FILM_ID = /[A-Za-z][A-Za-z0-9]*(?:[_.][A-Za-z0-9]+)+|\b[a-z]{2,8}-\d{2,}\b/;
function filmRecordWords(text) {
  const t = String(text ?? "");
  if (FILM_ID.test(t)) return true;
  return (t.match(WORD) ?? []).some((w) => !OWN_WORDS.has(w));
}

// One flag per letter of the text: true where it is taken out.
export const recordMask = (text, opts = {}) => maskOf(text, opts.name, opts.terms).out;

// The text holds record words that plain words take out.
export const hasHidden = (text, opts = {}) => recordMask(text, opts).some(Boolean);

// Escaped HTML of a text with its record words in span.rec and the names put in in span.rn.
// opts.name: (record word) => its name for people, or null (beside labels.js's quantity names)
// opts.asides: as labels.js keepAsides, each aside in brackets of up to that many letters (both
//   brackets shown) in one piece (span.aside), so a line does not break inside it
// opts.empty: words put in (span.rn) when nothing but the "who:" before the first colon is left
export function markRecordWords(text, opts = {}) {
  // (the agents by their plain names, labels.js roleNames: 設計 / まとめ / パッケージ / CAE, not 「…役」)
  text = roleNames(text);
  if (FILM) return filmMarks(text);
  const { s, out, names, text: plain } = maskOf(text, opts.name, opts.terms);
  const opens = new Set();
  const closes = new Set();
  if (opts.asides) {
    // (and a short quote in 「」: a load case's title, 「急ブレーキ 35 km/h」)
    const re = new RegExp(String.raw`\(((?:[^()]|\([^()]*\)){1,${opts.asides}})\)|「[^「」\n]{1,${Math.min(opts.asides, 24)}}」`, "g");
    for (const m of s.matchAll(re)) {
      const b = m.index + m[0].length - 1;
      if (out[m.index] || out[b]) continue;
      opens.add(m.index);
      closes.add(b + 1);
    }
  }
  const cut = (j) => names.has(j) || opens.has(j) || closes.has(j);
  let html = "";
  for (let i = 0; i < s.length; ) {
    if (closes.has(i)) html += "</span>";
    if (opens.has(i)) html += '<span class="aside">';
    let j = i + 1;
    while (j < s.length && out[j] === out[i] && !cut(j)) j++;
    const piece = esc(s.slice(i, j));
    html += out[i] ? `<span class="rec">${piece}</span>` : piece;
    if (names.has(i)) html += `<span class="rn">${esc(names.get(i))}</span>`;
    i = j;
  }
  if (closes.has(s.length)) html += "</span>";
  if (opts.empty && out.some(Boolean)) {
    const who = s.match(/^[^:：]{1,40}[:：]/)?.[0] ?? "";
    const left = plain.replace(/[:：]$/, "").trim();
    if (!left || left === who.replace(/[:：]$/, "").trim()) html += `<span class="rn">${who ? ": " : ""}${esc(opts.empty)}</span>`;
  }
  return html;
}

// A record's number by the kind of record it is (pred-0119 -> 予測), so the words around it still
// read (「力の道は pred-0119 と ana-0075 の …」 -> 「力の道は 予測 と 解析 の …」). The others
// (chg-, rchk-, if-, mesh-) have no such name and are taken out.
const RECORD_KIND_JA = { pred: "予測", ana: "解析", cmp: "比べ", ver: "判定", cause: "原因" };
export function recordKindName(w) {
  const m = /^([a-z]+)-\d{3,}$/.exec(w);
  return m ? RECORD_KIND_JA[m[1]] ?? null : null;
}

// A design's part or member by its name (the design's parts[].name / members[].name, before its
// bracket: 「左のレール (前の端から…)」 -> 「左のレール」), from the version shown, else the
// newest version that has the id; null when no design names it.
function designs(s) {
  const vs = Object.keys(s?.designs ?? {}).map(Number).sort((a, b) => b - a);
  const cur = s?.currentVersion;
  return (cur != null && s.designs[cur] ? [cur, ...vs.filter((v) => v !== cur)] : vs).map((v) => s.designs[v]);
}
export function designThingName(s, id) {
  for (const d of designs(s)) {
    const x = (d?.parts ?? []).find((p) => p.id === id) ?? (d?.members ?? []).find((m) => m.id === id);
    if (!x) continue;
    const n = x.name ? String(x.name).replace(/\s*[（(].*$/, "").trim() : "";
    return n && n !== id && !hasRecordWords(n) ? n : null;
  }
  return null;
}
// The ids a run's designs give their parts and members (all versions), for markRecordWords' terms.
const idsCache = new WeakMap();
function designIds(s) {
  const ds = designs(s).filter(Boolean);
  const key = s?.designs;
  if (!key || !ds.length) return null;
  const hit = idsCache.get(key);
  if (hit && hit.n === ds.length) return hit.ids;
  const ids = [...new Set(ds.flatMap((d) => [...(d.parts ?? []), ...(d.members ?? [])].map((x) => x.id).filter((x) => typeof x === "string")))];
  idsCache.set(key, { n: ds.length, ids });
  return ids;
}

// A line of the log or the footer (app.js): a tool's id by its short name (labels.js's name, else
// the title in tool.checked up to its bracket, as state.js toolWord), a design's part or member by
// its name (designThingName), a record's number by its kind (recordKindName), short asides in one
// piece, and ENGLISH_ONLY when nothing but who said it is left. An id with no name is taken out.
export const ENGLISH_ONLY = "英語で書かれた記録";
export function plainLineHtml(s, text) {
  const name = (w) => {
    const own = plainToolName(w);
    const title = s?.tools?.[w]?.title;
    const t = own ?? (title ? String(title).replace(/\s*[（(].*$/, "") : null);
    if (t && t !== w && !hasRecordWords(t)) return t;
    // (「part:wheel_FL x member:rail_L_…」: the id after its kind)
    const bare = w.replace(/^[a-z]+:/, "");
    return designThingName(s, bare) ?? recordKindName(bare);
  };
  return markRecordWords(text, { name, asides: 40, empty: ENGLISH_ONLY, terms: designIds(s) });
}

// The words a page shows of such HTML where span.rec is hidden and span.rn shown (for tests).
export function shownText(html) {
  const d = document.createElement("div");
  d.innerHTML = html;
  for (const r of d.querySelectorAll(".rec")) r.remove();
  return d.textContent;
}

// The text as shown without its record words (the names put in; spaces left over made one); for
// titles and tests.
export const plainWords = (text, opts = {}) => maskOf(text, opts.name, opts.terms).text;
