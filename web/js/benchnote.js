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
// The words shown are plain words (plain.js): a record's id (chg-0006, rule_check, side_post_LS2_1,
// battery.battery2), an aside or a sentence of English record words (goes 21.4 mm into floor ...) is
// wrapped in span.rec, which the strip hides (shown under ?dev=1 and in くわしく).

// (the rule is plain.js's, the same as the footer's line and the log's; kept here under the names
// the card's callers import)
export { hasRecordWords, markRecordWords, plainWords } from "./plain.js";

export const NOTE_IDS = ["now", "roundcard", "causenote", "bubble", "hooknote"];

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
