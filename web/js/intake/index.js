// The intake screens (A1 new design, A2 hearing, A5 knowledge) as one module for the main screen to mount.
//
//   import { mountIntake } from "./intake/index.js";
//   const intake = mountIntake(hostElement, api, { nav: true, screen: "A1", want: "ギターを作りたい" });
//   intake.show("A5"); intake.update(); intake.setWant("介護ベッドを作りたい"); intake.destroy();
//
// options: onRuleFocus(partIds) is called when a person points at a rule hit in A2 (its part ids; [] when they leave), so
// the host can light those parts in its 3D view.
// options: nav (false hides the tab bar), screen (the first one shown), want (a request text put in A1's
// input box, not sent: the person reads it and presses 送る). setWant(text) does the same later; "" empties the box.
//
// api (all optional except events/onEvent):
//   events()             every event so far (the screens draw only from events)
//   onEvent(fn)          call fn(event) for each new event; returns an unsubscribe function
//   send(text)           A1: the person wrote a request
//   editFlags(flags)     A1: the person changed a chip; the code picks the domains again
//   exclude(domain)      A1: the person took a domain card off
//   setLimits(limits)    A1: change the round / cost limits
//   answerHearing({answers, note, useDefaults})   A2
//   dropFile(name, text) A5: a file of rules was dropped; may return a promise of a status line
//   toggleRule(id, on)   A5: a rule was switched on or off
//   recheck()            A5: apply the rules to the design again
// Without a callback the matching control is shown disabled, so a replay of a saved run is read-only.

import { fill, h } from "./dom.js";
import { buildModel } from "./model.js";
import { renderA1 } from "./a1.js";
import { renderA2 } from "./a2.js";
import { renderA5 } from "./a5.js";

const SCREENS = [["A1", "依頼", renderA1], ["A2", "ヒアリング", renderA2], ["A5", "ナレッジ", renderA5]];

function ensureCss() {
  const href = new URL("./intake.css", import.meta.url).href;
  if ([...document.querySelectorAll("link[rel=stylesheet]")].some((l) => l.href === href)) return;
  document.head.append(h("link", { rel: "stylesheet", href }));
}

export function mountIntake(host, api, options = {}) {
  ensureCss();
  const ui = {
    screen: options.screen || "A1",
    draft: typeof options.want === "string" ? options.want : "", note: "", answers: {}, ruleAnswers: {},
    onRuleFocus: typeof options.onRuleFocus === "function" ? options.onRuleFocus : null, picksFinal: !!options.picksFinal, time: null, litSeen: new Set(), error: "", sending: false,
    mode: "category", filter: "all", query: "", selected: null, collapsed: new Set(), disabled: new Set(), dropped: [],
    rerender: null,
  };
  const nav = h("nav", { class: "in-nav", hidden: options.nav === false });
  const panes = Object.fromEntries(SCREENS.map(([id]) => [id, h("div", { class: `in-pane ${id.toLowerCase()}`, dataset: { screen: id }, hidden: true })]));
  const root = h("div", { class: "in-root-host", dataset: { intake: "1" } }, nav, ...Object.values(panes));
  fill(host, root);

  let seen = api.events();
  let model = buildModel(seen, ui);
  let pending = false;

  function draw(opts = {}) {
    for (const [id, , render] of SCREENS) {
      panes[id].hidden = id !== ui.screen;
      if (id === ui.screen) {
        const focus = opts.keepFocus ? panes[id].querySelector(opts.keepFocus) : null;
        const pos = focus ? [focus.selectionStart, focus.selectionEnd] : null;
        render(panes[id], model, api, ui);
        if (opts.keepFocus) {
          const again = panes[id].querySelector(opts.keepFocus);
          if (again) { again.focus(); if (pos) again.setSelectionRange(...pos); }
        }
      }
    }
    fill(nav, SCREENS.map(([id, label]) => h("button", { class: id === ui.screen ? "on" : "", onclick: () => show(id) }, `${id} ${label}`)));
  }
  ui.rerender = (opts) => draw(opts);

  function show(id) {
    if (!panes[id]) throw new Error(`unknown intake screen ${id}`);
    ui.screen = id;
    draw();
  }

  function update() {
    const events = api.events();
    if (events.length < seen.length) ui.litSeen.clear(); // a rewind or another run: let the cards glow again
    seen = events;
    model = buildModel(events, ui);
    draw();
  }

  // drop a file anywhere on A5
  const drop = (e) => {
    if (ui.screen !== "A5" || !e.dataTransfer?.files?.length) return;
    e.preventDefault();
    root.classList.remove("dragging");
    ui.onFiles?.([...e.dataTransfer.files]);
  };
  root.addEventListener("dragover", (e) => { if (ui.screen === "A5") { e.preventDefault(); root.classList.add("dragging"); } });
  root.addEventListener("dragleave", (e) => { if (e.target === root || !root.contains(e.relatedTarget)) root.classList.remove("dragging"); });
  root.addEventListener("drop", drop);

  const off = api.onEvent?.(() => {
    if (pending) return;
    pending = true;
    queueMicrotask(() => { pending = false; update(); });
  });
  // put a request text in A1's input box without sending it (the box keeps what the person is typing otherwise)
  function setWant(text) {
    ui.draft = typeof text === "string" ? text : "";
    if (ui.screen === "A1") draw();
  }

  // playback time (seconds): the glow of a lit card follows it, so a frame at time t always looks the same
  // (null = live: the glow plays once as a CSS animation)
  function setTime(t) {
    ui.time = typeof t === "number" && Number.isFinite(t) ? t : null;
    draw();
  }

  draw();
  return { show, update, setWant, setTime, destroy: () => { off?.(); fill(host); }, get screen() { return ui.screen; }, get model() { return model; } };
}
