// The hooks on the screen, kept short:
//  - the moment a hook stops something (hook.denied / hook.blocked_stop, state.js s.denied), one
//    line near the middle of the screen saying what was stopped and what the agent is made to do
//    instead; it stays SHOW_S seconds of the replay's clock and goes (the same t gives the same
//    picture). The rule's id (HK1 ...) only under ?dev=1.
//  - the footer's list of the rules (HK1 予測が先 ✓ … ×2) folded under 「くわしく」 (open by
//    default under ?dev=1); opened, it is the whole list as before (app.js renderHooks).
//  - the footer's activity line says what is going on in short words: a verdict's numbers are on
//    the right panel's card, so the footer does not repeat them, and a hook's stop is said without
//    the rule's id.

import { agentLongName, esc } from "./labels.js";
import { hookWords } from "./present.js";

// What was stopped / what the agent is made to do (the rules' meaning: present.js HOOK_WORDS).
const HOOK_LINE = {
  HK1: "予測を書く前の解析を止めました。先に予測を書かせます",
  HK2: "判定がすんでいないツールでの解析を止めました。判定ずみのツールを使わせます",
  HK3: "不一致のまま先へ進むのを止めました。先に原因を書かせます",
  HK4: "判定がそろう前に終わろうとしたのを止めました。残りの判定を出させます",
  HK5: "軽い計算が終わる前の重い計算を止めました。軽い計算から確かめさせます",
  HK6: "モジュールの持ち場の外の変更を止めました。自分のモジュールの中で直させます",
};

export function hookLineText(rule, { dev = false } = {}) {
  const w = hookWords(rule);
  const text = HOOK_LINE[w.id] ?? "hooks が止めました";
  return dev ? `${w.id} ${text}` : text;
}

// 0..1: how much of the line shows dt seconds after the stop (in fast, out slow)
export function hookLineOpacity(dt, showS) {
  if (!(dt >= 0) || dt > showS) return 0;
  return Math.min(1, dt / 0.25, (showS - dt) / 0.6);
}

export const HOOKLINE_TOP = 150; // the line's top in the stage's px (topbar.css .hookline)
export const HOOKLINE_GAP = 12; // stage px kept between the line and a box it keeps clear of

// Puts the line's element on the stage and folds the footer's list. Returns { render(s, playhead) }.
// avoid: () => the elements the line must not cover (③'s board of tests): while one of them shows
// and reaches below the line's usual top, the line goes just under it.
export function mountHookLine(stage, { dev = false, showS = 5, avoid = () => [] } = {}) {
  const line = document.createElement("div");
  line.className = "hookline";
  line.id = "hookline";
  line.setAttribute("role", "status");
  line.hidden = true;
  stage.append(line);

  // the footer: 「くわしく」 opens the rules' list (#hooks, filled by app.js renderHooks)
  const hooks = stage.querySelector("#hooks");
  const row = stage.querySelector("#statusrow");
  let more = null;
  if (hooks && row) {
    more = document.createElement("button");
    more.type = "button";
    more.className = "hooks-more";
    more.id = "hooks-more";
    hooks.before(more);
    const set = (open) => {
      row.classList.toggle("hooks-open", open);
      hooks.hidden = !open;
      more.setAttribute("aria-expanded", String(open));
      more.textContent = open ? "hooks を閉じる" : "くわしく";
      more.title = open ? "hooks の一覧をたたむ" : "hooks の一覧 (どの決まりが何回止めたか) を開く";
    };
    set(dev);
    more.addEventListener("click", () => set(hooks.hidden));
  }

  function render(s, playhead) {
    const den = s?.denied ?? null;
    const a = den ? hookLineOpacity(playhead - den.t, showS) : 0;
    line.hidden = a <= 0;
    line.style.opacity = a > 0 ? a.toFixed(3) : "0";
    if (a <= 0) return;
    const key = `${den.seq}`;
    if (line.dataset.seq !== key) {
      line.dataset.seq = key;
      line.dataset.rule = hookWords(den.rule).id;
      line.innerHTML = `<span class="hl-mark" aria-hidden="true">!</span>${esc(hookLineText(den.rule, { dev }))}`;
    }
    place();
  }
  // the line's top: its usual place, or under the lowest box to keep clear of that is on the screen
  // (the stage is scaled to the window: rects / k are the stage's px)
  function place() {
    const sr = stage.getBoundingClientRect();
    const k = stage.offsetWidth ? sr.width / stage.offsetWidth : 1;
    let top = HOOKLINE_TOP;
    for (const el of avoid()) {
      if (!el || el.hidden || !el.offsetParent) continue;
      const r = el.getBoundingClientRect();
      if (r.height <= 0 || r.width <= 0) continue;
      const bottom = (r.bottom - sr.top) / k;
      if (bottom + HOOKLINE_GAP > top && (r.top - sr.top) / k < top + 60) top = Math.ceil(bottom + HOOKLINE_GAP);
    }
    const want = `${top}px`;
    if (line.style.top !== want) line.style.top = want;
  }
  return { el: line, more, render };
}

// A sentence without the rules' ids in brackets ("hooks が止めた (HK3 不一致は原因が先)" -> "hooks が止めた"); as is under ?dev=1.
export function withoutRuleIds(text, { dev = false } = {}) {
  return dev ? text : String(text ?? "").replace(/\s*[(（]HK\d+[^)）]*[)）]/g, "");
}

// The footer's one line: who does what now, short.
export function footerActivity(s, { dev = false } = {}) {
  const act = s?.activity;
  if (!act) return "";
  const who = agentLongName(act.agent) || "HIRAKU";
  const isVerdict = (s.order?.verdicts ?? []).some((id) => s.seqOf?.[id] === act.seq);
  if (isVerdict) return `${who}: 判定を出しました`;
  const den = s.denied;
  if (den && den.seq === act.seq && !dev) return `${who}: hooks が止めました`;
  return `${who}: ${act.text}`;
}
