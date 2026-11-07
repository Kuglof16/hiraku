// ③'s tests at a glance (#tests over the workbench's 3D): one group per domain, its head the
// domain's name and its agent's pet (agent_pets.js, place "board": its pose says whether that agent
// is at work now), and the domain's tests as small boxes. A box shows the test's id, a short name
// (the title before its first bracket, cut short; for an id several load cases share, the part of
// the title that tells them apart, labels.js caseShortNames) and where it stands, in a word and a
// colour (CASE_STATE): まだ, 計算中, 予想通り / 不一致 (the prediction against the analysis), OK / NG
// (the criterion's verdict). Red is for NG only; a mismatch is orange.
//
// The groups wrap side by side (css/test_board.css): a group's boxes take at most MAX_COLS columns,
// so 8 domains with a few tests each fit on one screen with the 3D (no row scrolls sideways). The
// domains without a test in this run are one line at the end. Pressing a box picks its load case
// (app.js: the same as the old chips, ui.pinnedLc); 自動 follows the replay again. 「ビューアで見る」
// is not here: it is on the analysis card (#card-ana, viewer_link.js) of the test shown.
//
// Everything comes from the record at the replay's time (state.js chainFor): ?t= draws the same.

import { DOMAINS, DOMAIN_JA, caseShortNames, esc } from "./labels.js";
import { chainFor, judgementState } from "./state.js";

export const MAX_COLS = 6; // a group's boxes: at most this many side by side
export const NAME_MAX = 7; // letters of a test's short name (then …)
export const NAME_LONG = 14; // the same off the film: on up to 2 lines

// The states a test can be in, in the order they come; ja: the word in its box.
export const CASE_STATE = {
  wait: { ja: "まだ", title: "予測もまだ" },
  busy: { ja: "計算中", title: "予測が出て、解析を待っている・解析している" },
  match: { ja: "予想通り", title: "予測と解析が許す幅の中で合った (判定はまだ)" },
  mismatch: { ja: "不一致", title: "予測と解析が許す幅を超えて違った" },
  ok: { ja: "OK", title: "基準の判定が OK" },
  ng: { ja: "NG", title: "基準の判定が NG" },
};

// The agent whose pet stands at a domain's head (as agent_pets.js gridPlaceOf, the other way).
export const boardAgent = (d) => (d === "assembly" ? "packager" : `cae.${d}`);

// Where a load case stands in the current design version: { key (CASE_STATE), cmp: "match" |
// "mismatch" | null (the comparison under the verdict) }.
export function caseState(s, lcId, version = s.currentVersion) {
  const ch = chainFor(s, lcId, version);
  const cmp = ch.cmp ? (ch.cmp.status === "match" ? "match" : "mismatch") : null;
  if (ch.verdict) return { key: ch.verdict.pass ? "ok" : "ng", cmp };
  if (cmp) return { key: cmp, cmp };
  if (ch.running || ch.pred) return { key: "busy", cmp: null };
  return { key: "wait", cmp: null };
}

// A test's short name: the title up to its first bracket or comma, at most NAME_MAX letters.
export function shortCaseName(lc, max = NAME_MAX) {
  const s = String(lc?.title ?? lc?.load_case_id ?? "").split(/\s*[（(、,]/)[0].trim() || String(lc?.load_case_id ?? "");
  const letters = [...s];
  return letters.length > max ? `${letters.slice(0, max - 1).join("")}…` : s;
}

// The board's HTML for the load cases (all of the run's, or ④'s open domain's).
// lcId: the one shown (ringed); auto: no pick (自動 lit); pets: null, or { has(id) }; withRest: the
// line of the domains without a test (③; not in ④'s one domain).
export function boardHtml(s, cases, { lcId = null, auto = true, pets = null, withRest = true } = {}) {
  const shorts = caseShortNames(cases);
  // the film's frames (?video=1, body.video) as filmed: names of NAME_MAX letters on one line; else a
  // name of up to NAME_LONG letters on up to 2 lines under the id (test_board.css), not cut short
  const film = typeof document !== "undefined" && !!document.body?.classList.contains("video");
  const order = [...DOMAINS, ...new Set(cases.map((c) => c.domain).filter((d) => !DOMAINS.includes(d)))];
  const groups = order.map((d) => ({ d, list: cases.filter((c) => c.domain === d) })).filter((g) => g.list.length);
  const html = groups
    .map(({ d, list }) => {
      const agent = boardAgent(d);
      const a = s.agents?.[agent];
      const pet = pets?.has(agent) ? `<span class="petslot tb-pet" data-pet-slot="${esc(agent)}"></span>` : `<span class="tb-pet none"></span>`;
      const j = judgementState(s, d).state;
      const cells = list
        .map((c) => {
          const st = caseState(s, c.load_case_id);
          const word = CASE_STATE[st.key];
          const dup = shorts.get(c.load_case_id);
          const name = film ? dup ?? shortCaseName(c) : shortCaseName(c, NAME_LONG);
          const safe = st.key === "ok" && st.cmp === "mismatch" ? " safe" : "";
          const title = `${c.load_case_id}: ${c.title ?? ""}\n${word.ja} (${word.title})${safe ? "・予想とは不一致 (安全側で判定)" : ""}`;
          return (
            `<button class="test st-${st.key}${safe}${c.load_case_id === lcId ? " on" : ""}" type="button" data-lc="${esc(c.load_case_id)}" data-state="${st.key}" data-lint="wrap" title="${esc(title)}">` +
            `<span class="tid"><b>${esc(c.test_id ?? c.load_case_id)}</b>${film && dup ? `<small class="tnm">${esc(dup)}</small>` : ""}</span>` +
            `<span class="tst">${esc(word.ja)}</span>${film && dup ? "" : `<small class="tnm${film ? "" : " two"}">${esc(name)}</small>`}</button>`
          );
        })
        .join("");
      return (
        `<div class="tb-group dom-${j}${a?.active ? " at-work" : ""}" data-domain="${esc(d)}" data-agent="${esc(agent)}" data-active="${a?.active ? "1" : "0"}">` +
        `<div class="tb-head" title="${esc(`${DOMAIN_JA[d] ?? d}・試験 ${list.length}`)}">${pet}<span class="tb-name">${esc(DOMAIN_JA[d] ?? d)}</span></div>` +
        `<div class="tb-cells" style="--cols:${Math.min(MAX_COLS, list.length)}">${cells}</div></div>`
      );
    })
    .join("");
  // the domains without a test here: one line (only on ③, not in ④'s one domain)
  const shown = new Set(groups.map((g) => g.d));
  const rest = withRest ? DOMAINS.filter((d) => !shown.has(d)) : [];
  const restLine = rest.length
    ? `<span class="tb-rest" data-role="no-tests" title="${esc(rest.map((d) => `${DOMAIN_JA[d]}: ${judgementState(s, d).picked ? "選んだが試験がまだ無い" : "この実行では選んでいない"}`).join("\n"))}">試験なし: ${rest.map((d) => esc(DOMAIN_JA[d])).join("・")}</span>`
    : "";
  const tail =
    `<div class="tb-tail"><button class="auto${auto ? " on" : ""}" type="button" data-lc="" title="再生に合わせて試験を選ぶ">自動</button>` +
    `${restLine}</div>`;
  return { html: html + tail, groups: groups.length };
}
