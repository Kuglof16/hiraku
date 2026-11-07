// ③'s sentence card, out of the 3D: one strip under the 3D (#benchnote) holds one card at a time,
// two lines at most, and a 「くわしく」 that opens the whole card under it (out of the 3D too).
//
// The cards are the screen's own elements, moved into the strip on the workbench (not in the
// present mode, where they stay over the 3D as the film has them):
//   now        the いま line (present.js nowSentence)
//   roundcard  the round's proposals, or the version built from them (proposals.js roundCardHtml)
//   causenote  the cause of a mismatch Claude wrote (present.js openCause)
//   bubble     what an agent said
//   hooknote   a hooks' stop (hookline.js hookLineText; the stage's line is not drawn on ③)
// Which one shows: the card of the newest event applied (s.seq), else the いま line. The next event
// takes the card away (two cards never stay side by side).
//
// The words shown are plain words: an aside in brackets or a sentence that holds a record's id
// (chg-0006, rule_check, side_post_LS2_1, battery.battery2) or English record words (goes 21.4 mm
// into floor ...) is wrapped in span.rec, which the strip hides (shown under ?dev=1 and in くわしく).

import { esc } from "./labels.js";

export const NOTE_IDS = ["now", "roundcard", "causenote", "bubble", "hooknote"];

// Lower-case words of the screen's own, not a record's English: units and names said as they are.
const OWN_WORDS = new Set(["von", "hooks", "deg", "rpm", "rad"]);
// an id: letters joined by _ or . (rule_check, battery.battery2, side_post_LS2_1), or a record's
// number (chg-0006, cmp-0027, pred-0017)
const ID = /[A-Za-z][A-Za-z0-9]*(?:[_.][A-Za-z0-9]+)+|\b[a-z]{2,8}-\d{2,}\b/;
const WORD = /\b[a-z]{3,}\b/g;

// The text holds a record's id or an English word (three or more lower-case letters, not the screen's own).
export function hasRecordWords(text) {
  const t = String(text ?? "");
  if (ID.test(t)) return true;
  return (t.match(WORD) ?? []).some((w) => !OWN_WORDS.has(w));
}

// Escaped HTML of a text with its record words in span.rec: first each innermost aside in
// brackets that holds them (with the space before it), then each sentence (up to 。) that still does.
export function markRecordWords(text) {
  const s = String(text ?? "");
  const pieces = []; // [text, rec]
  // (with the spaces round it: 「値 (ana-0076 の …) は」 reads 「値は」 without it)
  const ASIDE = /\s*[(（][^()（）]*[)）]\s?/g;
  let at = 0;
  for (const m of s.matchAll(ASIDE)) {
    if (!hasRecordWords(m[0])) continue;
    pieces.push([s.slice(at, m.index), false], [m[0], true]);
    at = m.index + m[0].length;
  }
  pieces.push([s.slice(at), false]);
  // a sentence: the plain pieces' text between 。 (and the asides kept or wrapped in it)
  const out = [];
  let sentence = [];
  const flush = () => {
    if (!sentence.length) return;
    const plain = sentence.filter(([, r]) => !r).map(([x]) => x).join("");
    const rec = hasRecordWords(plain);
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

// The text as the strip shows it (record words taken out); for tests and titles.
export function plainWords(text) {
  const d = document.createElement("div");
  d.innerHTML = markRecordWords(text);
  for (const r of d.querySelectorAll(".rec")) r.remove();
  return d.textContent.replace(/\s{2,}/g, " ").trim();
}

// Which card: the one whose event is the newest applied (s.seq); else the いま line.
// cause: present.js openCause (with its seq); round: the seq of the round card's newest record (proposals.js roundSeq).
export function noteKind(s, { cause = null, round = null, hasSaid = true } = {}) {
  const top = s?.seq;
  if (top == null) return "now";
  if (s.denied && s.denied.seq === top) return "hooknote";
  if (cause && cause.seq === top) return "causenote";
  if (round != null && round === top) return "roundcard";
  if (hasSaid && s.said && s.said.seq === top) return "bubble";
  return "now";
}

// The strip: mount once; place(on) each frame moves the cards in (on: the workbench, not the present
// mode) or back over the 3D; pick(kind) shows that card only.
export function mountBenchNote(strip, viewport) {
  const card = strip.querySelector("#bn-card");
  const more = strip.querySelector("#bn-more");
  const full = strip.querySelector("#bn-full");
  const home = new Map(); // id -> the element's place over the 3D (its next sibling there)
  let open = false;
  const setOpen = (v) => {
    open = v;
    strip.classList.toggle("open", open);
    full.hidden = !open;
    more.setAttribute("aria-expanded", String(open));
    more.textContent = open ? "閉じる" : "くわしく";
  };
  setOpen(false);
  more.addEventListener("click", () => setOpen(!open));
  let shown = null;
  return {
    el: strip,
    place(on) {
      strip.hidden = !on;
      for (const id of NOTE_IDS) {
        const el = document.getElementById(id);
        if (!el) continue;
        if (on && el.parentElement !== card) {
          if (el.parentElement !== viewport) continue; // (made later over the 3D: moved once it is there)
          home.set(id, el.nextSibling);
          card.append(el);
        } else if (!on && el.parentElement === card && home.has(id)) {
          // (the hooks' card has no place over the 3D: it stays in the strip, hidden with it)
          const next = home.get(id);
          viewport.insertBefore(el, next && next.parentElement === viewport ? next : null);
          el.classList.remove("bn-pick");
        }
      }
      if (!on) shown = null;
    },
    // kind: one of NOTE_IDS; the card shows when its element has something (not hidden, not empty)
    pick(kind) {
      let el = document.getElementById(kind);
      if (el && (el.hidden || !el.textContent.trim())) el = null;
      if (!el && kind !== "now") {
        el = document.getElementById("now");
        if (el && (el.hidden || !el.textContent.trim())) el = null;
      }
      for (const id of NOTE_IDS) document.getElementById(id)?.classList.toggle("bn-pick", document.getElementById(id) === el);
      strip.dataset.kind = el ? el.id : "";
      const key = el ? `${el.id}|${el.innerHTML}` : "";
      if (key !== shown) {
        shown = key;
        full.innerHTML = el ? el.innerHTML : "";
        more.hidden = !el;
        if (!el) setOpen(false);
      }
      return el;
    },
  };
}
