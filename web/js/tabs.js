// The top tabs: the steps in the order of use (① to ⑤, with a small arrow between) and, apart
// from them, the two on the side (the tool shelf, the rules). Each tab has a line icon drawn
// here (inline SVG in currentColor, as high as the letters), a title saying what it is for, and
// the same sentence in a small bubble on a long press (fingers have no hover). A tab whose
// screen has nothing in this run is drawn faint and says so; it can still be pressed (its screen
// says what would be there).
//
// What makes a tab have something (TAB_NEEDS): the kinds of the events applied so far (the
// playhead, or the live run's end); ⑤ also needs the analysis to carry the assembly steps
// (assembly.js assemblyRecord, which reads the state).

import { TAB_EMPTY_JA, TAB_FLOW, TAB_FLOW_JA, TAB_JA, TAB_SIDE, TAB_SIDE_JA, esc } from "./labels.js";
import { assemblyRecord } from "./assembly.js";

// The event kinds that give a tab something to show (any one of them).
export const TAB_NEEDS = {
  A1: ["run.started", "user.said", "domains.picked"], // the request and the picked domains
  A2: ["hearing.asked", "hearing.answered"], // the questions and the answers
  bench: ["design.created"], // a design to show in 3D
  grid: ["analysis.finished", "verdict.recorded", "judgement.updated"], // a result in a tile
  A7: ["analysis.finished"], // ... with data.assembly (status ok): assemblyRecord
  A4: ["tool.checked"], // a tool checked in this run
  A5: ["knowledge.imported", "rule_check.recorded"], // rules given, or applied to a design
};

// The icons: 20 × 20, strokes only (drawn in the tab's own colour).
const ICON = {
  // a pencil writing a line
  A1: `<path d="M5 14.5 6 11 13.5 3.5 16.5 6.5 9 14Z"/><path d="M12 5 15 8"/><path d="M3 17.5H12"/>`,
  // a speech bubble with a question mark
  A2: `<path d="M3 3.5H17V13H9L5.5 16V13H3Z"/><path d="M8.3 6.8A1.8 1.8 0 1 1 10.8 8.4C10.3 8.7 10 9 10 9.7"/><path d="M10 11.2V11.3"/>`,
  // a box seen from a corner (the 3D of the design)
  bench: `<path d="M10 2.5 16.5 6.2V13.8L10 17.5 3.5 13.8V6.2Z"/><path d="M3.5 6.2 10 10 16.5 6.2"/><path d="M10 10V17.5"/>`,
  // tiles side by side (the grid)
  grid: `<rect x="3" y="3" width="6" height="6" rx="1"/><rect x="11" y="3" width="6" height="6" rx="1"/><rect x="3" y="11" width="6" height="6" rx="1"/><rect x="11" y="11" width="6" height="6" rx="1"/>`,
  // a block set onto another (the arrow down between)
  A7: `<rect x="6" y="2.5" width="8" height="4.5" rx="0.8"/><path d="M7.8 9.4 10 11.4 12.2 9.4"/><rect x="3" y="13" width="14" height="4.5" rx="0.8"/>`,
  // a shelf with things on it
  A4: `<path d="M3 2.5V17.5M17 2.5V17.5M3 9.5H17M3 17H17"/><rect x="5" y="4.5" width="3" height="5"/><rect x="9.5" y="6" width="3.5" height="3.5"/><rect x="5.5" y="12.5" width="5" height="4.5"/>`,
  // a shield with a tick
  A5: `<path d="M10 2.5 16 4.8V9.8C16 13.6 13.4 16.2 10 17.5 6.6 16.2 4 13.6 4 9.8V4.8Z"/><path d="M7.2 10 9.2 12 12.9 8.2"/>`,
};
const svg = (body, cls) =>
  `<svg class="${cls}" viewBox="0 0 20 20" width="1em" height="1em" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
const ARROW = svg(`<path d="M7.5 5 12.5 10 7.5 15"/>`, "tab-arrow");

export const tabIconSvg = (key) => svg(ICON[key], "tab-ic");

function tabHtml(key) {
  const t = TAB_JA[key];
  return (
    `<button class="tab" data-tab="${key}" type="button">${tabIconSvg(key)}` +
    (t.no ? `<span class="tab-no">${t.no}</span>` : "") +
    `<span class="tab-name">${esc(t.name)}</span></button>`
  );
}

// What a tab says on hover and on a long press: the empty note first, then what it is for
// (and on ?dev=1 the screen's name inside HIRAKU).
export function tabTip(key, has, dev) {
  const t = TAB_JA[key];
  return [has ? null : TAB_EMPTY_JA, t.tip, dev ? `(${t.id})` : null].filter(Boolean).join("\n");
}

// Counts the event kinds applied so far, carried on from the last call while the events only grow.
export class TabContent {
  constructor() {
    this.events = null;
    this.n = 0;
    this.kinds = new Set();
  }
  // events: the list, applied: how many of them count; always: the keys that have something anyway
  has(s, events, applied = events.length, always = []) {
    if (events !== this.events || applied < this.n) {
      this.events = events;
      this.n = 0;
      this.kinds = new Set();
    }
    for (; this.n < applied; this.n += 1) this.kinds.add(events[this.n]?.kind);
    const out = {};
    for (const key of [...TAB_FLOW, ...TAB_SIDE]) out[key] = always.includes(key) || TAB_NEEDS[key].some((k) => this.kinds.has(k));
    out.A7 = always.includes("A7") || (out.A7 && !!assemblyRecord(s));
    return out;
  }
}

const LONG_MS = 450; // a press held this long shows the bubble
const TIP_MS = 3000; // and it stays this long after the finger goes

// Fills the tabs' host (#tabs) and keeps their marks. Returns { mark(has), tip }.
export function mountTabs(host, { dev = false } = {}) {
  host.innerHTML =
    `<div class="tabflow" role="group" aria-label="${TAB_FLOW_JA}">${TAB_FLOW.map(tabHtml).join(ARROW)}</div>` +
    `<div class="tabside" role="group" aria-label="${TAB_SIDE_JA}">${TAB_SIDE.map(tabHtml).join("")}</div>`;
  const row = host.closest(".tabsrow") ?? host;
  const tip = document.createElement("div");
  tip.className = "tabtip";
  tip.id = "tabtip";
  tip.setAttribute("role", "tooltip");
  tip.hidden = true;
  row.append(tip);
  const buttons = [...host.querySelectorAll("button.tab")];
  let has = Object.fromEntries(buttons.map((b) => [b.dataset.tab, true]));
  let timer = 0;
  let hideTimer = 0;
  let swallow = false; // the click that ends a long press does not change the tab

  function showTip(b) {
    tip.textContent = tabTip(b.dataset.tab, has[b.dataset.tab], dev);
    tip.dataset.tab = b.dataset.tab;
    tip.hidden = false;
    // offsetLeft/Top are in the stage's own pixels (the stage's scale does not touch them)
    const left = b.offsetLeft + b.offsetWidth / 2 - tip.offsetWidth / 2;
    tip.style.left = `${Math.max(4, Math.min(left, row.clientWidth - tip.offsetWidth - 4))}px`;
    tip.style.top = `${b.offsetTop + b.offsetHeight + 6}px`;
  }
  const hideTip = () => {
    tip.hidden = true;
    delete tip.dataset.tab;
  };
  const clear = () => {
    clearTimeout(timer);
    timer = 0;
  };

  host.addEventListener("pointerdown", (ev) => {
    const b = ev.target.closest("button.tab");
    clear();
    clearTimeout(hideTimer);
    hideTip(); // a new press closes the bubble of the last one
    if (!b || ev.pointerType === "mouse") return; // the mouse has the title
    swallow = false;
    timer = setTimeout(() => {
      timer = 0;
      swallow = true;
      showTip(b);
      clearTimeout(hideTimer);
    }, LONG_MS);
  });
  const end = () => {
    clear();
    if (!tip.hidden) {
      clearTimeout(hideTimer);
      hideTimer = setTimeout(hideTip, TIP_MS);
    }
  };
  host.addEventListener("pointerup", end);
  host.addEventListener("pointercancel", end);
  host.addEventListener("pointerleave", clear);
  host.addEventListener("contextmenu", (ev) => {
    if (ev.target.closest("button.tab")) ev.preventDefault(); // a long press is the bubble, not the menu
  });
  // before app.js's click on #tabs: a long press only shows the bubble
  host.addEventListener(
    "click",
    (ev) => {
      if (!swallow) return;
      swallow = false;
      ev.stopImmediatePropagation();
      ev.preventDefault();
    },
    true,
  );
  document.addEventListener("pointerdown", (ev) => {
    if (!tip.hidden && !host.contains(ev.target)) hideTip();
  });

  function mark(next) {
    has = { ...has, ...next };
    for (const b of buttons) {
      const key = b.dataset.tab;
      b.classList.toggle("empty", !has[key]);
      b.title = tabTip(key, has[key], dev);
      b.dataset.empty = has[key] ? "0" : "1";
    }
    if (!tip.hidden && tip.dataset.tab) tip.textContent = tabTip(tip.dataset.tab, has[tip.dataset.tab], dev);
  }
  mark(has);
  return { mark, tip, showTip: (key) => showTip(buttons.find((b) => b.dataset.tab === key)), hideTip };
}
