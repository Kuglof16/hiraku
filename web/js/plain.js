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

import { esc, plainToolName, quantityName } from "./labels.js";

// Lower-case words of the screen's own, not a record's English: units and names said as they are.
const OWN_WORDS = new Set(["von", "hooks", "deg", "rpm", "rad"]);
// an id: letters joined by _ or . (rule_check, battery.battery2, side_post_LS2_1), a record's number
// (chg-0006, cmp-0027, pred-0017), or a load case's id (R1-step100, H7-handhold, S1-edge1350N)
const ID_SRC = String.raw`[A-Za-z][A-Za-z0-9]*(?:[_.][A-Za-z0-9]+)+|\b[a-z]{2,8}-\d{2,}\b|\b[A-Z]{1,3}\d+[a-z]?-[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*`;
const ID = new RegExp(ID_SRC);
const IDS = new RegExp(ID_SRC, "g");
const WORD = /\b[a-z]{3,}\b/g;
// a token to take out: an id (with a "part:" before it), or a lower-case word
const TOKEN = new RegExp(String.raw`(?:\b[a-z]+:)?(?:${ID_SRC})|\b[a-z]{3,}\b`, "g");
// three English words in a row: lower-case words (two letters or more) with only ASCII between them
const ENGLISH_RUN = /\b[a-z]{2,}\b(?:[ -~]*?\b[a-z]{2,}\b){2,}/;
const SEP_AFTER = /^\s*(?:[=＝]\s*|[、・,，/]\s*)?/;

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

// What is taken out: one flag per letter of s (true where it is out), and the names put in at a
// record word's start (index -> name). name: opts.name, or false for no step 3 (a name's own asides).
function maskOf(text, name = null) {
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
  // 1. the asides, the innermost first (in the order they close)
  const open = [];
  for (let i = 0; i < s.length; i++) {
    if (s[i] === "(" || s[i] === "（") open.push(i);
    else if ((s[i] === ")" || s[i] === "）") && open.length) {
      const a = open.pop();
      if (!hasRecordWords(visible(a, i + 1))) continue;
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
    for (const m of s.matchAll(TOKEN)) {
      const [from, to] = [m.index, m.index + m[0].length];
      if (visible(from, to) !== m[0]) continue; // (part of it already out)
      if (!ID.test(m[0]) && OWN_WORDS.has(m[0])) continue;
      hide(from, to);
      const said = (name && name(m[0])) || quantityWord(m[0]);
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
      if (lost && (/^[\s・「『(（\-–—]*[でがはをにとのへも](?![a-zA-Z0-9])/.test(v) || /[→=<>≤≥]\s*$/.test(v))) hide(a, end);
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
export const recordMask = (text, opts = {}) => maskOf(text, opts.name).out;

// The text holds record words that plain words take out.
export const hasHidden = (text, opts = {}) => recordMask(text, opts).some(Boolean);

// Escaped HTML of a text with its record words in span.rec and the names put in in span.rn.
// opts.name: (record word) => its name for people, or null (beside labels.js's quantity names)
// opts.asides: as labels.js keepAsides, each aside in brackets of up to that many letters (both
//   brackets shown) in one piece (span.aside), so a line does not break inside it
// opts.empty: words put in (span.rn) when nothing but the "who:" before the first colon is left
export function markRecordWords(text, opts = {}) {
  if (FILM) return filmMarks(text);
  const { s, out, names, text: plain } = maskOf(text, opts.name);
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

// A line of the log or the footer (app.js): a tool's id by its short name (labels.js's name, else
// the title in tool.checked up to its bracket, as state.js toolWord), short asides in one piece, and
// ENGLISH_ONLY when nothing but who said it is left.
export const ENGLISH_ONLY = "英語で書かれた記録";
export function plainLineHtml(s, text) {
  const name = (w) => {
    const own = plainToolName(w);
    const title = s?.tools?.[w]?.title;
    const t = own ?? (title ? String(title).replace(/\s*[（(].*$/, "") : null);
    return t && t !== w && !hasRecordWords(t) ? t : null;
  };
  return markRecordWords(text, { name, asides: 40, empty: ENGLISH_ONLY });
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
export const plainWords = (text, opts = {}) => maskOf(text, opts.name).text;
