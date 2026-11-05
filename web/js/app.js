// HIRAKU screen: wires events (live or replay) to the workbench panels and the 3D view.
//
// URL parameters
//   ?run=<run_id>   read runs/<run_id>/events.jsonl from the server (run=sample: the contract sample)
//   ?src=<url>      read an events.jsonl from any URL instead
//   ?t=12.5         stop at t = 12.5 s and draw that frame (for frame-by-frame video export; the intake
//                   screens get the time too, intake.setTime, so their lit cards' glow is the same at the same t)
//   ?video=1        video layout: keep the bottom 170 px empty for subtitles, hide the status row
//   ?status=0       hide the bottom status row only
//   ?speed=2        replay speed; ?autoplay=0 opens paused at 0; ?live=0 replays a running run instead of following it
//   ?on=joints      turn overlays on at load (joints: the joint marks, off by default; A7 follows it too)
//   ?tab=grid       open on the all-domains grid instead of the workbench (A1, A2, A5: the intake screens; A4: the tool shelf)
//   ?tabs=A1:0,A2:44.5,bench:80
//                   the tabs on the replay's time: from t seconds, that tab (so one URL films ① → ② → ③;
//                   ?t= and seek, back or forth, show the tab of that time). Before the first entry the tab
//                   is chosen as without it; an unknown tab name is left out; wins over ?tab=; a tab
//                   pressed by hand ends it. Names as ?tab= (bench, grid, A1, A2, A4, A5, A7)
//   ?list=1         the run list; ?dev=1 shows it (and keeps it on the links) as the developers see it
//   ?present=1      the present mode (js/present.js): the 3D on the whole screen and one card; the
//                   見せる / 作業台 switch at the top right turns it on and off
// window.hiraku.seek(t) returns a promise that resolves once the frame at t is drawn;
// window.hiraku.load(events, t) shows other events (already parsed) the same way.

import { TAB_JA, tabWord, DEMO_LINE_JA, DOMAINS, DOMAIN_JA, DRIVEN_JA, DRIVER_JA, PICK_DEMO_ID, runSummaryJa, isOlderRecord, RUN_STATE_JA, runState, RULES, STOP_CODE_JA, agentLongName, agentName, BASE_JA, caseShortNames, esc, keepAsides, fmt, fmtMargin, fmtPct, fmtQ, fmtSigned, fmtTime, fmtUsd, fmtVU, opLabel, formulaInputName, plainToolName, quantityName, unitLabel } from "./labels.js";
import { BAND_COLORS, BANDS, OVER_COLOR } from "./colormap.js";
import { EventFeed, Player, parseJsonl } from "./player.js";
import { chainFor, isSafeSide, judgementState, roundsUsed, valueFromJa } from "./state.js";
import { SeriesChart } from "./chart.js";
import { FIELD_JA, FIELD_ORDER, atFrame, contourOf, convergenceLine, fieldsOf, frameIndex, onSharedScale, preferredField, sameField, variantName, variantOf } from "./fields.js";
import { motionOf, motionSpan, nearestAt, wheelName, xSeriesOf } from "./series.js";
import { mountIntake } from "./intake/index.js";
import { lookFromParams, qualityFromParams } from "./look.js";
import { mountShelf } from "./shelf.js";
import { liftIsLimit } from "./scene.js";
import { TileBoard } from "./tiles.js";
import { VIEWS, Workbench3D } from "./workbench3d.js";
import { AssemblyPanel } from "./assembly.js";
import { renderJointLegend } from "./joints.js"; // joints:
import { MODE_JA, MODE_ORDER, drawableMetrics, METRIC_JA, modeCardHtml, modeLegendParts, qualityContourOf, qualityOf, setupContourOf, setupOf, thicknessContourOf } from "./quality.js";
import { AgentPets, PET_PAINTS, petRowHtml, stopWhyHtml } from "./agent_pets.js";
import { reportNextHtml } from "./report_next.js";
import { hookWords, nowSentence, presentCardHtml, presentScene, resolvedHooks, verdictLine, verdictSentence } from "./present.js";
import { judgedContour, referenceResults } from "./judged.js";
import { JUDGED_JA } from "./labels.js";
import { heldAll, heldBandHtml, heldCases } from "./held.js"; // held: load cases shown, not judged
import { ModuleLayer, hasModules, loadModuleNames, renderModuleLegend, renderRoundCard } from "./modules.js"; // modules:
import { moduleColor, roundCardHtml } from "./proposals.js"; // modules:
import { TabContent, mountTabs } from "./tabs.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
// ?dev=1: the run list as the developers see it (files, run ids, cut numbers, cost lines); kept on the links
// (and the tabs' titles add the screens' names inside HIRAKU)
const DEV = params.get("dev") === "1";
const tabs = mountTabs($("tabs"), { dev: DEV });
const tabContent = new TabContent();
const withDev = (href) => (DEV ? `${href}${href.includes("?") ? "&" : "?"}dev=1` : href);
const STAGE_W = 1920;
const STAGE_H = 1080;
const SHOW_S = 5; // seconds of playback a bubble or hook warning stays
const ANIM_MAX_S = 12; // a test's motion plays in at most this many seconds of replay

const PET_COLORS = { lead: "#e9b872", designer: "#2fa7d6", packager: "#8e6bbf", system: "#c9c3b6", user: "#fbf9f4" };
const petColor = (id) => PET_COLORS[id] ?? (id?.startsWith("cae.") ? "#4cbf7a" : id?.startsWith("designer.") ? moduleColor(id.slice(9)) : "#c9c3b6"); // modules: designer.<module>

const INTAKE_TABS = ["A1", "A2", "A5"]; // the intake screens (js/intake/), shown in their own tabs
const TABS = ["bench", "grid", "A4", ...INTAKE_TABS, "A7"]; // A4: the tool shelf (js/shelf.js); A7: the assembly order (js/assembly.js)
let asmPanel = null; // mounted on first show

// ?tabs=A1:0,A2:44.5,bench:80: from t seconds of the replay, that tab (sorted by t; an unknown tab or
// time is left out). Before the first entry the tab is chosen as without it.
function parseTabPlan(text) {
  const plan = [];
  for (const item of (text ?? "").split(",")) {
    const at = item.lastIndexOf(":");
    if (at < 1) continue;
    const tab = item.slice(0, at).trim();
    const t = Number(item.slice(at + 1));
    if (TABS.includes(tab) && item.slice(at + 1).trim() !== "" && Number.isFinite(t)) plan.push({ t, tab });
  }
  return plan.sort((a, b) => a.t - b.t);
}

const ui = {
  pinnedLc: null,
  pinnedTab: TABS.includes(params.get("tab")) ? params.get("tab") : null,
  tabPlan: parseTabPlan(params.get("tabs")), // ?tabs= (wins over ?tab=); a tab pressed by hand ends it
  toggles: { contour: true, arrows: true, cg: true, parts: true, names: false, limit: true, graph: true, joints: false, modules: false, spaces: false }, // joints: off at first; modules: modules, spaces (modules.js)
  frozen: params.has("t"),
  // The test's motion follows the replay (auto); ▶ or dragging its bar plays it on its own (local)
  // until the replay playhead moves again.
  anim: { key: null, local: false, playing: false, tau: 0 },
  graphGroup: null,
  fieldKind: null, // contour field picked in the legend (null: the first one)
  fieldVariant: null, // among the fields of that kind: `${tool}|${variantOf}` (the analysis' tool; the beams, or a mesh and its surface)
  zoomMax: false, // the camera at a shell contour's largest element (legend: 最大の所に寄る)
  compare: false, // the previous design version beside the current one (legend: 前の版と比べる)
  meshMode: null, // the mesh's own view in place of a field (quality.js MODE_ORDER), or null
  qualityMetric: null, // the mesh check's metric the elements are coloured by (null: the first)
  stoppable: null, // the run id when this screen started the run being followed
  stopAsked: false,
  present: params.get("present") === "1", // the present mode
  detail: false, // in the present mode: the log, the chain and the hooks opened (詳しく)
};

// ?off=contour,arrows,cg,graph turns those overlays off at load (e.g. the bare vehicle for a title shot).
for (const key of (params.get("off") ?? "").split(",")) if (key in ui.toggles) ui.toggles[key] = false;
// joints, modules: ?on=joints,modules,spaces turns those on at load (modules and spaces take the contour's place)
for (const key of (params.get("on") ?? "").split(",")) if (key in ui.toggles) ui.toggles[key] = true;
if (ui.toggles.modules || ui.toggles.spaces) ui.toggles.contour = false;

if (params.get("video") === "1") document.body.classList.add("video");
if (params.get("status") === "0") document.body.classList.add("no-status");

// ---- stage scaling -------------------------------------------------------
const stage = $("stage");
let stageScale = 1;
function fitStage() {
  stageScale = Math.min(window.innerWidth / STAGE_W, window.innerHeight / STAGE_H);
  const left = (window.innerWidth - STAGE_W * stageScale) / 2;
  const top = (window.innerHeight - STAGE_H * stageScale) / 2;
  stage.style.transform = `translate(${left}px, ${top}px) scale(${stageScale})`;
  agentPets?.setPixel((window.devicePixelRatio || 1) * stageScale);
  resize3d();
}

// ---- 3D -------------------------------------------------------------------
const viewport = $("viewport");
const look = lookFromParams(params); // ?look=real: the photograph-like look (default: the diagram)
document.body.classList.add(`look-${look}`);
const quality = qualityFromParams(params); // ?quality=high / medium / low: the real look's cost (look.js QUALITY)
const bench = new Workbench3D(viewport, look, { quality });
const modLayer = new ModuleLayer(bench); // modules:
function resize3d() {
  const half = viewport.classList.contains("compare"); // before / after side by side: each view takes half
  const w = half ? Math.floor(viewport.clientWidth / 2) : viewport.clientWidth;
  const h = viewport.clientHeight;
  if (w > 0 && h > 0) {
    bench.resize(w, h, (window.devicePixelRatio || 1) * stageScale);
    if (half && cmpView) cmpView.bench.resize(w, h, (window.devicePixelRatio || 1) * stageScale);
  }
}
new ResizeObserver(resize3d).observe(viewport);
window.addEventListener("resize", fitStage);

// ---- the agents' pets (js/agent_pets.js) ------------------------------------
// promo/pets/pet3d.js and layout.json's n00 camera; ?pets=0, or either missing: circles and names.
let agentPets = null;
const petsReady = (params.get("pets") === "0" ? Promise.resolve(null) : AgentPets.load().catch(() => null)).then((p) => {
  agentPets = p;
  document.documentElement.dataset.agentPets = p ? "on" : "none";
  if (p) p.setPixel((window.devicePixelRatio || 1) * stageScale);
  drawn.rev = -1;
});

// ---- player ---------------------------------------------------------------
const player = new Player();
if (params.has("speed")) player.speed = Number(params.get("speed")) || 1;

// Pacing (?pace=0 turns it off, ?gapmax=3 sets the longest gap). Seconds each
// kind of event stays on screen before the next one at least.
const PACE_MIN = {
  "run.started": 1.2,
  "design.created": 1.6,
  "agent.said": 2.2,
  "user.said": 2.2,
  "hook.denied": 2.5,
  "hook.blocked_stop": 2.0,
  "prediction.recorded": 1.6,
  "analysis.started": 1.0,
  "analysis.finished": 1.6,
  "comparison.recorded": 1.6,
  "cause.recorded": 2.0,
  "verdict.recorded": 1.4,
  "judgement.updated": 0.8,
  "tool.checked": 0.4,
  "agent.started": 0.4,
  "agent.stopped": 0.3,
  "screen.focus": 0.3,
  "cost.updated": 0.1,
  "run.finished": 0.5,
};
const motionSpans = new Map(); // analysis_id -> seconds of motion (or null)
function motionSpanOf(ev) {
  const id = ev.data?.analysis_id;
  if (!motionSpans.has(id)) motionSpans.set(id, ev.data?.series ? motionSpan(ev.data) : null);
  return motionSpans.get(id);
}
function paceAfter(ev) {
  let s = PACE_MIN[ev.kind] ?? 0.5;
  if (ev.kind === "agent.said" || ev.kind === "user.said") s = Math.min(4.5, Math.max(s, 0.8 + (ev.text?.length ?? 0) * 0.07));
  if (ev.kind === "hook.denied") s = Math.min(5, Math.max(s, 1 + (ev.text?.length ?? 0) * 0.05));
  if (ev.kind === "analysis.finished") {
    const span = motionSpanOf(ev);
    if (span !== null) s = Math.max(s, Math.min(span, ANIM_MAX_S)); // let the test's motion play out
  }
  return s;
}
player.pacer = paceAfter;
player.pacing = params.get("pace") !== "0";
if (params.has("gapmax")) player.maxGap = Math.max(0.1, Number(params.get("gapmax")) || 3);
let feed = null;

// ---- panels ---------------------------------------------------------------
let drawn = { rev: -1, lc: undefined, tab: undefined, toggles: "", logLen: 0, logFirstSeq: null, logRev: 0, resolved: "" };
let contour = null;

function selectedLc(s) {
  const cases = s.project?.load_cases ?? [];
  const id = ui.pinnedLc ?? s.focus.loadCaseId ?? cases[0]?.load_case_id ?? null;
  return id;
}

function plannedTab(t) {
  let tab = null;
  for (const p of ui.tabPlan) if (p.t <= t + 1e-9) tab = p.tab;
  return tab;
}

function currentTab(s) {
  const planned = ui.tabPlan.length ? plannedTab(player.playhead) : null;
  if (planned) return planned;
  if (ui.pinnedTab) return ui.pinnedTab;
  if (INTAKE_TABS.includes(s.focus.screen) || s.focus.screen === "A4" || s.focus.screen === "A7") return s.focus.screen;
  return s.focus.screen === "A6" ? "grid" : "bench";
}

// ---- the intake screens (A1, A2, A5) ------------------------------------------
// Mounted on first show. They draw from the events applied so far (the replay's
// playhead, or the live run); listeners hear when that changes (a rewind too).
const intakeListeners = new Set();
let intakeApplied = null; // run id, events applied and own-run when the listeners last heard
let intake = null;
const intakeApi = {
  events: () => (draft ? draftEvents() : player.events.slice(0, player.applied)),
  onEvent(fn) {
    intakeListeners.add(fn);
    return () => intakeListeners.delete(fn);
  },
  // Only while the watched run is going (runGoing; a replay stays read-only). The
  // answer goes to the run's folder as hearing.answer.json, which the run waits for; a
  // dropped file goes to its knowledge/ with a request in its inbox/ (the run imports it).
  get answerHearing() {
    if (draft) return draft.state && !draft.busy && !draft.starting ? draftAnswer : undefined;
    return runGoing() ? answerHearing : undefined;
  },
  get dropFile() {
    return runGoing() ? dropFile : undefined;
  },
  // a new request (?new=1): A1 sends and changes it, A2 answers it, all on the server's draft
  get send() {
    return draft && !draft.busy && !draft.starting ? draftSend : undefined;
  },
  get editFlags() {
    return draft?.state && !draft.busy && !draft.starting ? (flags) => draftChange({ flags }) : undefined;
  },
  get exclude() {
    return draft?.state && !draft.busy && !draft.starting ? (domain) => draftChange({ exclude: [...draft.excluded, domain] }) : undefined;
  },
  get setLimits() {
    return draft?.state && !draft.busy && !draft.starting ? (limits) => draftChange({ limits }) : undefined;
  },
};

// ---- a new request (?new=1): A1 and A2 before a run --------------------------------------
// The server keeps nothing between the calls (hiraku.intake.request): each answer carries the
// draft, sent back with the next change. A1 and A2 draw the draft's intake events (with the
// request as the person's words, and the server's warnings as the lead's). When the hearing is
// settled the request's files are saved and 始める starts a run on them (--intake-events).
const draft = params.has("new")
  ? { state: null, want: "", events: [], warnings: [], done: null, busy: "", error: "", useClaude: false, excluded: [], confirm: false, prefill: "", demoTitle: "" }
  : null;

function draftEvents() {
  const list = [
    ...(draft.want ? [{ kind: "user.said", agent: "user", text: draft.want }] : []),
    ...draft.events,
    ...draft.warnings.map((w) => ({ kind: "agent.said", agent: "lead", text: w })),
  ];
  return list.map((e, i) => ({ seq: i, t: 0, ts: "", run_id: "request", ...e }));
}

// A refused call throws an Error with the server's English message, its status and body (for
// the screen to say it in Japanese; the English stays for the record, e.g. in a title).
async function postRequest(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const got = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(got.error ?? `${res.status}`), { status: res.status, body: got });
  return got;
}

// 409 from /api/launch (launch.py: a run started here is still going): the words for people, with
// a link to that run when the server knows its id yet (null until the run prints its folder).
function goingHtml(e) {
  if (e?.status !== 409 || !("running_run_id" in (e.body ?? {}))) return null;
  const rid = e.body.running_run_id;
  const link = rid ? ` <a class="going-run" href="${withDev(`?run=${encodeURIComponent(rid)}`)}">その運転を開く</a>` : ` <a class="going-run" href="${withDev("?list=1")}">一覧で見る</a>`;
  return `<span class="err" title="${esc(e.message)}">いま走っている運転があります。終わってから始めてください。${link}</span>`;
}

// One call at a time; the bar says what is going on, and an error stays there in its words.
async function draftCall(busy, fn) {
  Object.assign(draft, { busy, error: "", errorHtml: "" });
  renderDraft();
  try {
    await fn();
  } catch (e) {
    draft.error = e.message ?? String(e);
  }
  draft.busy = "";
  renderDraft();
}

function takeDraft(got) {
  draft.state = got.draft;
  draft.events = got.events ?? [];
  draft.warnings = got.warnings ?? [];
}

function draftSend(text) {
  return draftCall(draft.useClaude ? "Claude が分野を選んでいます (数十秒かかります)…" : "分野を選んでいます…", async () => {
    draft.want = text;
    draft.done = null;
    draft.excluded = [];
    takeDraft(await postRequest("/api/request", { want: text, claude: draft.useClaude }));
  });
}

function draftChange(change) {
  return draftCall("選び直しています…", async () => {
    if (change.exclude) draft.excluded = change.exclude;
    draft.done = null;
    takeDraft(await postRequest("/api/request", { draft: draft.state, ...change }));
  });
}

function draftAnswer({ answers, note }) {
  return draftCall("答えを確かめています…", async () => {
    const got = await postRequest("/api/request/answer", { draft: draft.state, answers: answers ?? {}, note: note ?? "" });
    draft.state = got.draft ?? draft.state;
    draft.events = got.events ?? draft.events;
    draft.done = got;
  });
}

async function draftStart(mode) {
  if (mode === "claude" && !draft.confirm) {
    draft.confirm = true;
    renderDraft();
    return;
  }
  draft.confirm = false;
  if (draft.starting) return; // one start at a time (the buttons are disabled meanwhile)
  // /api/launch returns once the run has printed its folder, after the tools it uses are
  // checked; until then the bar says so, counts the seconds, and the buttons cannot be pressed.
  // On success they stay so while the page moves to the run.
  Object.assign(draft, { starting: { mode, at: performance.now() }, error: "", errorHtml: "" });
  renderDraft();
  const tick = setInterval(renderStartingSeconds, 1000);
  try {
    const out = await postRequest("/api/launch", { request: draft.done.request_id, mode });
    location.href = withDev(`?run=${encodeURIComponent(out.run_id)}`); // follow it live
  } catch (e) {
    clearInterval(tick);
    draft.starting = null;
    draft.errorHtml = goingHtml(e) ?? "";
    draft.error = draft.errorHtml ? "" : `始められませんでした: ${e.message ?? e}`;
    renderDraft();
  }
}

function startingSeconds() {
  return draft?.starting ? Math.floor((performance.now() - draft.starting.at) / 1000) : 0;
}
function renderStartingSeconds() {
  const el = document.querySelector("#draftbar .starting .secs");
  if (el) el.textContent = `${startingSeconds()} 秒`;
}

// The bar above A1/A2 in a new request: what is going on, and 始める once the hearing is settled.
function renderDraft() {
  const bar = $("draftbar");
  if (!draft || !bar) return;
  const d = draft.done;
  const rf = d?.requirements_file ?? {};
  const usd = typeof rf.cost_limit_usd === "number" ? fmtUsd(rf.cost_limit_usd) : "—";
  const keyed = !!draft.claudeReady;
  let body;
  if (draft.busy) body = `<span class="busy">${esc(draft.busy)}</span>`;
  else if (d?.ready && d.designable === false) body = `<span class="err">この製品の設計の作り方がまだ無いので、走らせられません</span>`;
  else if (d?.ready) {
    // while a start is on its way (draftStart) both buttons stay, disabled, with what is going on
    const st = draft.starting;
    const off = st ? " disabled" : "";
    // what each way is, in one line (the run list's words for them: DRIVEN_JA)
    const scripted =
      `<span class="way"><button data-start="scripted"${off}>台本で始める</button><span class="why">${DRIVEN_JA.scripted}します。Claude は呼ばないので費用はかかりません</span>` +
      (d.scripted_plan ? "" : `<span class="why">この製品には台本の計画が無いので、台本の運転は手順が少ない</span>`) +
      `</span>`;
    const claudeWhy = `<span class="why">Claude が考えて進めます。費用がかかります</span>`;
    const claude = !keyed
      ? `<span class="way"><button disabled title="${esc(`${draft.keyName ?? "ANTHROPIC_API_KEY"} が HIRAKU を起こした窓に無い`)}">Claude で始める</button>${claudeWhy}<span class="why">Claude で始めるには準備が要ります (<a class="readme" href="${README_RUN}" target="_blank" rel="noopener">くわしく</a>)</span></span>`
      : draft.confirm && !st
        ? `<span class="confirm">Claude を呼びます。費用の上限は ${usd}、繰り返しは ${esc(rf.round_limit ?? "—")} 回まで。始めますか?</span><button class="go" data-start="claude">始める</button><button data-start-cancel="1">やめる</button>`
        : `<span class="way"><button data-start="claude"${off}>Claude で始める (費用の上限 ${usd})</button>${claudeWhy}</span>`;
    const starting = st
      ? `<span class="starting" role="status"><b>${st.mode === "claude" ? "Claude で" : "台本で"}運転を始めています。</b>最初は、使うツールの確かめに時間がかかります (数十秒)。<span class="secs">${startingSeconds()} 秒</span></span>`
      : "";
    body = `<b>要件がそろいました</b>${scripted}${claude}${starting}`;
  } else if (d) body = `<span>${esc(d.next ?? "答えの足りない項目があります")}</span>`;
  else if (draft.state) body = `<span>分野が決まりました。${tabWord("A2")}のタブで答えるか、標準の値のまま進めてください</span>`;
  else
    body =
      (draft.demoTitle
        ? `<span>デモ「${esc(draft.demoTitle)}」の依頼の文を入れました。直してもかまいません。送ると分野を選びます</span>`
        : `<span>${tabWord("A1")}のタブで、作りたいものを書いて送ってください</span>`) +
      (keyed ? `<label><input type="checkbox" data-use-claude="1" ${draft.useClaude ? "checked" : ""}> Claude に分野と理由を書かせる (費用は最大 $0.5)</label>` : "");
  bar.innerHTML = body + (draft.errorHtml || (draft.error ? `<span class="err">${esc(draft.error)}</span>` : ""));
  bar.classList.toggle("starting", !!draft.starting);
  markTabs(player.state);
  intake?.update();
  fillPrefill();
}

// The demo's want goes into A1's box through intake.setWant (not sent: the person presses 送る).
// Once, when the intake is mounted.
function fillPrefill() {
  if (!draft?.prefill || !intake) return;
  intake.setWant(draft.prefill);
  draft.prefill = "";
}

function ownRunGoing() {
  const s = player.state;
  return !!ui.stoppable && ui.stoppable === s.runId && s.status === "running";
}

// The run being watched is going: started here (launch), or followed live while its record says
// running (a run started elsewhere, e.g. from a Claude Code session with the plugin). A replay
// (?live=0, a dropped file, a finished run) is not. The server checks again (Launcher.going_dir).
function runGoing() {
  return ownRunGoing() || (player.live && player.state.status === "running");
}

async function postLaunch(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ run_id: player.state.runId, ...body }) });
  const got = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(got.error ?? `${res.status}`);
  return got;
}

const answerHearing = (body) => postLaunch("/api/launch/hearing", { answer: body });

// A5 shows the returned line next to the file.
async function dropFile(name, text) {
  try {
    const got = await postLaunch("/api/launch/knowledge", { name, text });
    return `渡しました (knowledge/${got.file})。実行が読んで規則にします`;
  } catch (e) {
    return `渡せませんでした: ${e.message}`;
  }
}

function renderIntake(tab) {
  if (!intake) {
    // in a new request the bar (renderDraft) sits above the screens
    $("view-intake").innerHTML = `${draft ? `<div class="draftbar" id="draftbar"></div>` : ""}<div class="intake-host" id="intake-host"></div>`;
    intake = mountIntake($("intake-host"), intakeApi, { nav: false, screen: tab, onRuleFocus });
    renderDraft();
  }
  if (intake.screen !== tab) intake.show(tab);
  syncIntakeTime();
  // (and when the run turns out to be going, which offers the answer and the drop)
  const now = `${player.state.runId}|${player.applied}|${runGoing()}`;
  if (now !== intakeApplied) {
    intakeApplied = now;
    const last = player.events[player.applied - 1] ?? null;
    for (const fn of intakeListeners) fn(last);
  }
}

// The replay's time goes to the intake screens (intake.setTime): a lit card's glow follows it, so the
// frame at ?t= (or seek, load) is the same picture every time. Live (a new request, or following the
// running run at its edge) passes null: the glow plays once as a CSS animation. setTime draws the
// screen again by itself; the model does not read the time, so no intake.update() is needed (the
// events reach it through intakeApi's listeners). While playing, a new time is passed only when it
// can change the picture: near a lit card's moment (INTAKE_GLOW_NEAR seconds after it, wider than
// the glow) or on the other side of one, so the screen is not drawn again at every frame for
// nothing; a stopped frame (?t=, seek, load) always gets its exact time.
const INTAKE_GLOW_NEAR = 2;
let intakeTimeSent;
function intakeTimeNow() {
  if (draft || (player.live && player.liveAttached && !ui.frozen)) return null;
  return player.playhead;
}
// where t stands against the lit cards' moments: "n" when near one, else how many are still ahead
function intakeGlowPlace(t) {
  let ahead = 0;
  for (const at of intake?.model?.litAt?.values?.() ?? []) {
    if (t < at) ahead += 1;
    else if (t - at <= INTAKE_GLOW_NEAR) return "n";
  }
  return ahead;
}
function syncIntakeTime() {
  if (!intake) return;
  const t = intakeTimeNow();
  if (t === intakeTimeSent) return;
  if (!ui.frozen && t !== null && typeof intakeTimeSent === "number") {
    const place = intakeGlowPlace(t);
    if (place !== "n" && place === intakeGlowPlace(intakeTimeSent)) return;
  }
  intakeTimeSent = t;
  intake.setTime(t);
}

// A rule hit pointed at in A2 (its part ids; [] when the person leaves it): the workbench
// lights them (Workbench3D.setRuleFocus), and since A2 hides the workbench, a small 3D view
// (the peek) shows the same lit design in the corner of the intake screen while something is
// pointed at. Ids with nothing in the 3D (and a design not made yet) show no peek.
let ruleFocus = [];
let rulePeek = null; // { el, cap, host, bench, key }
const PEEK_W = 440;
const PEEK_H = 300;
function onRuleFocus(ids) {
  ruleFocus = Array.isArray(ids) ? ids.filter((x) => typeof x === "string") : [];
  bench.setRuleFocus(ruleFocus);
  cmpView?.bench.setRuleFocus(ruleFocus);
}

function drawRulePeek(s, shown) {
  const design = shown ? s.designs[s.currentVersion] ?? null : null;
  if (!ruleFocus.length || !design) {
    if (rulePeek) rulePeek.el.hidden = true;
    return;
  }
  if (!rulePeek) {
    const el = document.createElement("div");
    el.className = "rule-peek";
    el.id = "rule-peek";
    el.innerHTML = `<div class="rule-peek-cap"></div><div class="rule-peek-gl"></div>`;
    stage.append(el);
    const host = el.querySelector(".rule-peek-gl");
    rulePeek = { el, cap: el.firstChild, host, bench: new Workbench3D(host, look, { quality }), key: null, size: "" };
    window.hiraku.peekBench = rulePeek.bench;
  }
  const p = rulePeek;
  const b = p.bench;
  b.setDesign(design);
  const lit = b.setRuleFocus(ruleFocus);
  p.el.hidden = lit.length === 0;
  if (p.el.hidden) return;
  // the lower right corner of the intake screen (in stage pixels)
  const sr = stage.getBoundingClientRect();
  const vr = $("view-intake").getBoundingClientRect();
  p.el.style.left = `${(vr.right - sr.left) / stageScale - PEEK_W - 14}px`;
  p.el.style.top = `${(vr.bottom - sr.top) / stageScale - PEEK_H - 44}px`; // above the screen's last line (A2's note)
  const w = p.host.clientWidth;
  const h = p.host.clientHeight;
  const size = `${w}x${h}x${stageScale}`;
  if (size !== p.size && w > 0 && h > 0) {
    p.size = size;
    b.resize(w, h, (window.devicePixelRatio || 1) * stageScale);
    p.key = null;
  }
  const key = `${design.design_version}|${lit.join(",")}`;
  if (key !== p.key) {
    p.key = key;
    b.fitPoints(b.contentPoints(), VIEWS.default, 1.08);
    const said = (id) => {
      if (id === "design") return "設計の全体";
      if (id.startsWith("support:")) return `接地点 ${id.slice(8)}`;
      const part = (design.parts ?? []).find((x) => x.id === id);
      return part ? String(part.name ?? id).replace(/\s*[（(].*$/, "") || id : id;
    };
    p.cap.textContent = `規則の当たり: ${lit.map(said).join("・")}`;
    p.el.dataset.lit = lit.join(",");
  }
  b.setToggles({ ...ui.toggles, names: false, arrows: false, contour: false });
  b.animate(player.playhead, false);
  b.render();
}

// The tabs with nothing in this run drawn faint (tabs.js TAB_NEEDS): on the events applied so far,
// or in a new request on the draft's (where ① is the box to write in, so it always has something).
function markTabs(s) {
  if (draft) {
    const evs = draftEvents();
    tabs.mark(new TabContent().has(s, evs, evs.length, ["A1"]));
  } else tabs.mark(tabContent.has(s, player.events, player.applied));
}

// A4: mounted on first show; it asks the server for the shelf and lays the run's tool.checked over it.
let shelf = null;
function renderShelf() {
  shelf ??= mountShelf($("view-shelf"), { tools: () => player.state.tools, runId: () => player.state.runId });
  shelf.render();
}

function renderTop(s) {
  $("want").textContent = s.project?.want ?? "";
  const v = s.currentVersion;
  const limit = s.project?.round_limit;
  $("pill-version").textContent = `版 ${v ?? "—"}・繰り返し ${roundsUsed(s)} / ${limit ?? "—"} 回`;
  // s.cost null: the run's cost is not known to HIRAKU (the user's own Claude Code pays), never $0.
  // A scripted run (project.driver) calls no Claude: no cost, said so rather than $0.00.
  const scripted = s.project?.driver === "scripted";
  $("pill-cost").textContent = scripted ? "費用なし (台本の再生)" : `費用 ${s.cost === null ? "不明" : fmtUsd(s.cost)} / ${fmtUsd(s.project?.cost_limit_usd)}`;
  const live = player.live;
  // a replay says where the record is at the playhead (not that something runs now)
  const status = { running: "記録の途中", done: "完了", stopped: "途中で止まった", failed: "失敗", empty: "" }[s.status] ?? s.status;
  $("mode").innerHTML =
    `<span class="badge ${live ? "live" : ""}">${live ? "● 実行中を追いかけ" : scripted ? "台本の再生" : "再生"}${status && !live ? `・${esc(status)}` : ""}</span>` +
    // the run's id only under ?dev=1 (people go back to the list by 「☰ 一覧へ」)
    (DEV ? `<a class="runid" href="${withDev("?list=1")}" title="実行の一覧へ">${esc(s.runId ?? "")}</a>` : "") +
    // a run this screen started can be stopped while it goes
    (ui.stoppable && ui.stoppable === s.runId && s.status === "running"
      ? `<button class="stop-btn" data-stop="${esc(s.runId)}" ${ui.stopAsked ? "disabled" : ""}>${ui.stopAsked ? "止めています…" : "■ 止める"}</button>`
      : DEV
        ? `<a class="launch-link" href="${withDev("?list=1")}" title="決まった手順か Claude で、新しく走らせる">▶ その場で走らせる</a>`
        : `<a class="launch-link" href="?list=1" title="ほかのデモを見る・新しく実行する">☰ 一覧へ</a>`);
}

function renderChips(s, lcId) {
  const focusDomain = (s.project?.load_cases ?? []).find((c) => c.load_case_id === lcId)?.domain;
  $("chips").innerHTML = DOMAINS.map((d) => {
    const j = judgementState(s, d);
    const title = j.judgement?.summary ?? (j.picked ? "まだ判定が無い" : "この実行では選んでいない");
    const held = heldCases(s, d, s.currentVersion).length ? " held" : ""; // held: a count not judged (held.js)
    return `<span class="chip ${j.state}${held} ${d === focusDomain ? "focus" : ""}" title="${esc(title)}">${esc(DOMAIN_JA[d])}</span>`;
  }).join("");
  // held: the workbench's 保留 band above the 3D (every domain's load cases without a criterion whose count is not 0)
  let band = $("heldband");
  if (!band) {
    band = document.createElement("div");
    band.id = "heldband";
    band.className = "heldband bench";
    $("viewport").before(band);
  }
  const html = heldBandHtml(heldAll(s, DOMAINS), { withDomain: true });
  band.hidden = !html;
  if (band.innerHTML !== html) band.innerHTML = html;
}

const agentOrder = (s) => [...s.agentOrder].sort((a, b) => rank(a) - rank(b));

// The agents: with pets (js/agent_pets.js), each its 3D pet, name, status and bubble; without
// (no /pets/pet3d.js), or for an agent whose role has no pet, a circle and the name.
function renderAgents(s) {
  const order = agentOrder(s);
  const ul = $("agents");
  ul.classList.toggle("many", order.length > 6);
  ul.classList.toggle("lots", order.length > 8); // modules: the six module designers come on top of the others
  ul.classList.toggle("pets", !!agentPets);
  ul.innerHTML = order
    .map((id) => {
      const a = s.agents[id];
      const cls = a.status === "✓" ? "done" : ["止められた", "食い違い", "赤", "解析が通らない"].includes(a.status) ? "alert" : a.active ? "active" : "";
      // the role's name in full (操安の CAE 役), not the domain alone
      if (agentPets?.has(id)) return petRowHtml(id, a.status, { cls, name: agentLongName(id), why: a.stopWhy });
      return `<li class="${cls}"><span class="pet" style="--pet:${petColor(id)}"></span><span>${esc(agentLongName(id))}</span><span class="st">${esc(a.status)}</span>${stopWhyHtml(a.stopWhy)}</li>`;
    })
    .join("");
  agentPets?.mount(ul, "bench");
}

function rank(id) {
  const fixed = ["lead", "designer", "packager"];
  if (fixed.includes(id)) return fixed.indexOf(id);
  if (id.startsWith("designer.")) return 1.5; // modules: the module designers after the designer, in the record's order
  if (id.startsWith("cae.")) return 3 + DOMAINS.indexOf(id.slice(4));
  return 20;
}

// A hooks line: what happened and what comes next in short words, 解決済み once the stopped
// action was done later in the records; the recorded words are folded under it.
function hookLogItem(e, hook, resolved) {
  const w = hookWords(hook?.rule);
  const mark = resolved ? `<span class="resolved">解決済み</span>` : "";
  return (
    `<li class="hook${resolved ? " done" : ""}" data-seq="${e.seq}">${mark}<b>${esc(w.what)}</b><span class="next">次に: ${esc(w.next)}</span>` +
    `<details><summary title="${esc(`${w.id} ${w.ja}`)}">詳しい記録 (${esc(w.id)})<span class="tm">${fmtTime(e.tRec ?? e.t)}s</span></summary>${esc(e.text)}</details></li>`
  );
}

// Escaped text with each short aside in brackets kept in one piece (labels.js keepAsides).

function logItem(e, ctx = null) {
  if (e.level === "hook" && ctx) return hookLogItem(e, ctx.hooks.get(e.seq), ctx.resolved.has(e.seq));
  const who = e.level === "said" ? `<span class="who">${esc(agentLongName(e.agent))}:</span> ` : "";
  // more: words too long for a note (an agent.stopped's stop_reason), folded under くわしく
  const note =
    (e.note ? `<span class="note">${keepAsides(e.note)}</span>` : "") +
    (e.more ? `<details class="more"><summary>くわしく</summary><span class="note">${keepAsides(e.more)}</span></details>` : "");
  const upd = e.updatedT != null ? `<span class="tm">・更新 ${fmtTime(e.updatedT)}s</span>` : "";
  // What an agent said can run long: the line shows its first lines, the record keeps all of it.
  // A short aside in brackets stays on one line: not "(形の見 / 本)".
  // (an agent's own words, of any length, in the narrow log: an aside longer than the line has to
  // break somewhere, so the lint's one-letter and bracket checks are let off there: data-lint)
  const text = e.level === "said" ? `<span class="tx" data-lint="lonely" title="${esc(e.text)}">${keepAsides(e.text)}</span>` : keepAsides(e.text);
  return `<li class="${e.level}">${who}${text}<span class="tm" title="記録の t (実行が始まってからの秒)">${fmtTime(e.tRec ?? e.t)}s</span>${upd}${note}</li>`;
}

function renderLog(s, resolved) {
  const ol = $("log");
  const first = s.log[0]?.seq ?? null;
  const resolvedKey = [...resolved].join(",");
  if (s.log.length < drawn.logLen || first !== drawn.logFirstSeq || s.logRev !== drawn.logRev || resolvedKey !== drawn.resolved) {
    ol.innerHTML = "";
    drawn.logLen = 0;
  }
  if (s.log.length > drawn.logLen) {
    const ctx = { resolved, hooks: new Map([...s.hooks.denied, ...s.hooks.blockedStops].map((h) => [h.seq, h])) };
    ol.insertAdjacentHTML("beforeend", s.log.slice(drawn.logLen).map((e) => logItem(e, ctx)).join(""));
    ol.scrollTop = ol.scrollHeight;
  }
  drawn.logLen = s.log.length;
  drawn.logFirstSeq = first;
  drawn.logRev = s.logRev;
  drawn.resolved = resolvedKey;
}

// Many load cases: the chips show the id (and, for an id shared by several, a short name from the
// title) and the row scrolls sideways, the chosen one kept in sight; the whole title is the tooltip.
const MANY_TESTS = 12;
function renderTests(s, lcId) {
  const cases = s.project?.load_cases ?? [];
  const shorts = caseShortNames(cases);
  const auto = `<button class="auto ${ui.pinnedLc ? "" : "on"}" data-lc="">自動</button>`;
  const el = $("tests");
  const html =
    auto +
    cases
      .map((c) => {
        const short = shorts.get(c.load_case_id);
        return `<button class="test ${c.load_case_id === lcId ? "on" : ""}" data-lc="${esc(c.load_case_id)}" title="${esc(c.load_case_id)}: ${esc(c.title)}"><span class="tid">${esc(c.test_id)}${short ? `<small>${esc(short)}</small>` : ""}</span><span class="ttl">${esc(c.title)}</span></button>`;
      })
      .join("");
  if (html === drawn.tests) return;
  drawn.tests = html;
  el.classList.toggle("many", cases.length > MANY_TESTS);
  // (a row that scrolls sideways on purpose: not a text running out of its box)
  if (cases.length > MANY_TESTS) el.dataset.lint = "overflow";
  else delete el.dataset.lint;
  el.innerHTML = html;
  el.querySelector(".test.on")?.scrollIntoView({ block: "nearest", inline: "nearest" });
}

// The K0 formulas' Japanese titles (GET /api/formulas), once; until then (or without the route)
// a prediction shows its formula as written.
const formulaTitles = new Map();
fetch("/api/formulas", { cache: "no-store" })
  .then((r) => (r.ok ? r.json() : { formulas: [] }))
  .then((got) => {
    for (const f of got.formulas ?? []) if (f.title) formulaTitles.set(f.formula_id, f.title);
    drawn.rev = -1;
  })
  .catch(() => {});

// A "くわしく" (details[data-keep]) stays open or shut across the cards' redraws.
const openMore = new Set();
document.addEventListener(
  "toggle",
  (ev) => {
    const d = ev.target;
    if (!(d instanceof HTMLDetailsElement) || !d.dataset.keep) return;
    if (d.open) openMore.add(d.dataset.keep);
    else openMore.delete(d.dataset.keep);
  },
  true,
);
function keepOpen(el) {
  for (const d of el.querySelectorAll("details[data-keep]")) d.open = openMore.has(d.dataset.keep);
}

function renderChain(s, chain) {
  const { loadCase, pred, ana, running, cmp, verdict, cause } = chain;
  // 1. prediction
  const p = $("card-pred");
  if (pred) {
    const inputs = Object.entries(pred.inputs ?? {})
      .slice(0, 5)
      .map(([k, q]) => `<span>${esc(k)}</span><span>${esc(fmtQ(q))}</span>`)
      .join("");
    const terms = (pred.terms ?? []).filter((t) => typeof t.share === "number");
    // What a bar is (hiraku/solvers/k0_handcalc/registry.py _terms): a formula with additive
    // terms gives each term's value / the prediction; one without gives each input's elasticity
    // (how many % the prediction moves for 1 % more of that input). The record does not say
    // which: terms that are all the inputs, at the inputs' values, are elasticities.
    const sameAsInput = (t) => { const q = pred.inputs?.[t.name]; return !!q && (t.value === undefined || Math.abs(t.value - q.value) <= 1e-9 * Math.max(1, Math.abs(q.value))); };
    const elastic = terms.length > 0 && terms.every(sameAsInput);
    const what = quantityName(pred.quantity);
    const caption = elastic
      ? `棒: その入力を 1 % 大きくすると、${what}が何 % 変わるか`
      : `棒: ${what}のうち、その項が占める割合 (項の値 ÷ ${what})`;
    const termName = (t) => formulaInputName(pred.formula_id, t.name) ?? t.name;
    const termsHtml = terms.length
      ? `<div class="terms-cap" data-terms="${elastic ? "elasticity" : "share"}">${esc(caption)}</div><div class="terms">${terms
          .slice(0, 4)
          .map((t) => `<span title="${esc(t.name)}">${esc(termName(t))}</span><span class="barw"><i style="width:${Math.max(0, Math.min(1, Math.abs(t.share))) * 100}%"></i></span><span>${elastic ? fmtSigned(t.share, 0) : fmtPct(t.share, 0)}</span>`)
          .join("")}</div>`
      : "";
    const asm = (pred.assumptions ?? []).slice(0, 3).map((a) => `<li>${esc(a)}</li>`).join("");
    p.className = "card";
    // the formula's title in words when the catalogue has one; the formula itself and its inputs
    // folded under くわしく (without a title: as written, as before)
    const title = formulaTitles.get(pred.formula_id);
    const formula = `<div class="expr">${esc(pred.expression)}</div><div class="kv">${inputs}</div>`;
    p.innerHTML =
      `<h3>1. 予測<small>手計算の段 (K0)・${esc(agentLongName(pred.agent))}</small></h3>` +
      (title
        ? `<div class="sub">${esc(quantityName(pred.quantity))}</div><div class="fl-title" data-formula="${esc(pred.formula_id)}">${keepAsides(title)}</div>` +
          `<details class="more" data-keep="pred-formula"><summary>くわしく (式と入力)</summary><div class="sub">${esc(pred.formula_id)}</div>${formula}</details>`
        : `<div class="sub">${esc(quantityName(pred.quantity))}・${esc(pred.formula_id)}</div>${formula}`) +
      `<div class="big">= ${esc(fmtVU(pred.value, pred.unit))}</div>` +
      termsHtml +
      (asm ? `<ul class="asm">${asm}</ul>` : "");
    keepOpen(p);
  } else {
    p.className = "card wait";
    p.innerHTML = `<h3>1. 予測<small>手計算の段</small></h3><div>${loadCase ? `${esc(loadCase.test_id)} の予測はまだ。hooks は予測の無い解析を止める (HK1)` : "試験がまだ無い"}</div>`;
  }
  // 2. analysis
  const a = $("card-ana");
  if (ana) {
    // the name wraps, the value and its unit do not (app.css .card td); the record's own name in the title
    // (a short aside in brackets at the name's end, 横・左の角, kept on one line)
    const nameHtml = (q) => esc(quantityName(q)).replace(/ \(([^()]{1,30})\)$/, ` <span class="nw">($1)</span>`);
    // the criterion's quantity where the tool is judged on another (frame_shell's element max_stress):
    // a row of its own under 参考, small, so it does not read as the judged value against the allowable
    const refs = referenceResults(ana, loadCase?.criterion);
    const top = (ana.results ?? []).slice(0, 3);
    const isRef = (r) => refs.includes(r);
    const res = [...top, ...refs.filter((r) => !top.includes(r))].map((r) => `<tr class="${isRef(r) ? "ref" : ""}" title="${esc(r.quantity + (r.where ? ` @ ${r.where}` : ""))}"><td>${isRef(r) ? `<small class="ref-tag">${esc(JUDGED_JA.ref)}</small> ` : ""}${nameHtml(r.quantity)}${r.where ? ` <span class="sub">@ ${esc(r.where)}</span>` : ""}</td><td>${esc(fmtVU(r.value, r.unit))}</td></tr>`).join("");
    a.className = ana.status === "failed" ? "card bad" : "card";
    a.innerHTML =
      `<h3>2. 解析<small>CAE の段 (${esc(ana.base)} ${esc(BASE_JA[ana.base] ?? "")})</small></h3>` +
      `<div class="sub" title="${esc(ana.tool)}">${esc(toolShort(s, ana.tool))}・${esc(ana.load_case_id)}${typeof ana.wall_time_s === "number" ? `・${fmt(ana.wall_time_s, 2)} 秒` : ""}</div>` +
      (ana.status === "failed" ? `<div class="err">通らなかった: ${esc(ana.error ?? "")}</div>` : `<table>${res}</table>`) +
      (ana.mesh_convergence ? `<div class="conv">${esc(convergenceLine(ana.mesh_convergence))}</div>` : "") +
      (loadCase?.domain === "strength" ? `<div class="not-judged" data-role="not-judged">${esc(JUDGED_JA.mountsNotJudged)}</div>` : "");
  } else if (running) {
    a.className = "card busy";
    a.innerHTML = `<h3>2. 解析<small>CAE の段</small></h3><div title="${esc(running.tool)}">${esc(toolShort(s, running.tool))} で解析中…</div>`;
  } else {
    a.className = "card wait";
    a.innerHTML = `<h3>2. 解析<small>CAE の段</small></h3><div>${pred ? "予測を受けて解析を待っている" : "予測のあとに走る"}</div>`;
  }
  // 3. comparison (+ verdict)
  const c = $("card-cmp");
  if (cmp) {
    const ok = cmp.status === "match";
    const safe = isSafeSide(verdict);
    c.className = `card ${ok ? "ok" : "bad"}`;
    let v = "";
    if (verdict) {
      // A verdict from a safe-side mismatch carries its own mark and says which value it used.
      const how = safe ? `<span class="safe-mark" title="一致ではなく、説明された安全側の食い違いから">安全側で判定</span>` : "";
      const big = verdictSentence(verdict);
      // the pass or fail in one sentence, then the value (a prediction or a result) beside its criterion
      v =
        `<div class="verdict ${verdict.pass ? "ok" : "bad"}${safe ? " safe" : ""}">${how}<div class="verdict-big">${esc(big.text)}</div>` +
        // the quantity compared and judged (frame_shell: the hot-spot stress for a max_stress criterion)
        `判定 (${valueFromJa(verdict)}の値): ${esc(quantityName(cmp.quantity ?? verdict.quantity))}<div class="vline">${esc(verdictLine(verdict))}</div>` +
        `<span class="sub">余裕 ${esc(fmtMargin(verdict))}${loadCase?.title ? `・${esc(loadCase.title)}` : ""}</span>` +
        // the criterion's basis is the record's own words (English): folded
        (loadCase?.criterion?.basis ? `<details class="more" data-keep="cmp-basis"><summary>くわしく (基準の根拠)</summary><div class="sub basis">${esc(loadCase.criterion.basis)}</div></details>` : "") +
        `</div>`;
    } else if (ok && loadCase?.criterion) {
      v = `<div class="sub">基準: ${esc(quantityName(loadCase.criterion.quantity))} ${esc(opLabel(loadCase.criterion.op))} ${esc(fmtQ(loadCase.criterion.limit))} (判定を待っている)</div>`;
    }
    // Long words are cut to a few lines here; the log keeps them whole.
    const safeWhy = cause?.safe_side ? `<div class="safe-why clamp" title="${esc(cause.safe_side_reason ?? "")}"><b>安全側で見られている:</b> ${esc(cause.safe_side_reason ?? "")}</div>` : "";
    const cz = cause
      ? `<div class="cause"><div class="clamp" title="${esc(cause.cause)}">原因: ${esc(cause.cause)}</div><div class="clamp c2" title="${esc(cause.fix)}">直し方: ${esc(cause.fix)}${cause.resolved ? " (解決)" : ""}</div>${safeWhy}</div>`
      : ok
        ? ""
        : `<div class="cause">原因が書かれるまで、この分野は先に進めない (HK3)</div>`;
    // The row whose value the verdict used.
    const used = verdict ? (valueFromJa(verdict) === "予測" ? "pred" : "ana") : null;
    const tag = `<small class="used">判定の値</small>`;
    c.innerHTML =
      `<h3>3. 照合</h3>` +
      `<table><tr class="${used === "pred" ? "used" : ""}"><td>予測${used === "pred" ? tag : ""}</td><td>${esc(fmtQ(cmp.predicted))}</td></tr>` +
      `<tr class="${used === "ana" ? "used" : ""}"><td>解析${used === "ana" ? tag : ""}</td><td>${esc(fmtQ(cmp.analyzed))}</td></tr>` +
      `<tr><td>差 (許す幅 ${fmtPct(cmp.tolerance_rel, 0)})</td><td>${fmtSigned(cmp.diff_rel)}</td></tr></table>` +
      `<span class="badge ${ok ? "ok" : "bad"}">${ok ? "一致 ✓" : "食い違い ✗"}</span>` +
      cz +
      v;
    keepOpen(c);
  } else {
    c.className = "card wait";
    const tol = loadCase ? `許す幅 ${fmtPct(loadCase.tolerance_rel, 0)} (荷重条件で決まっている)` : "";
    c.innerHTML = `<h3>3. 照合</h3><div>予測と解析がそろうと照らし合わせる。${tol}</div>`;
  }
}

const toolTitle = (s, tool) => s.tools[tool]?.title ?? tool;
// short, as the log says it (state.js toolWord): labels.js's name, else the title up to its bracket, else the name
const toolShort = (s, tool) => plainToolName(tool) ?? (String(toolTitle(s, tool)).replace(/\s*[（(].*$/, "") || tool);

// The ok analyses of the shown one's load case and design version, the newest of each
// tool, in the order their tools first came (e.g. the beam analysis, then the shell one).
function analysesBeside(s, shown) {
  if (shown?.status !== "ok") return [];
  const byTool = new Map(); // keeps the order of first insertion when a tool comes again
  for (const id of s.order.analyses) {
    const a = s.analyses[id];
    if (a.status === "ok" && a.load_case_id === shown.load_case_id && a.design_version === shown.design_version) byTool.set(a.tool, a);
  }
  return [...byTool.values()];
}

// The same field of the same tool and load case in the newest earlier design version that
// has it (drawable), or null.
function previousOf(s, ana, field) {
  const earlier = s.order.analyses
    .map((id) => s.analyses[id])
    .filter((a) => a.status === "ok" && a.tool === ana.tool && a.load_case_id === ana.load_case_id && a.design_version < ana.design_version);
  for (const a of earlier.sort((x, y) => y.design_version - x.design_version || s.order.analyses.indexOf(y.analysis_id) - s.order.analyses.indexOf(x.analysis_id))) {
    const f = fieldsOf(a).find((g) => sameField(g, field) && (!g.mesh_id || s.meshes[g.mesh_id]));
    if (f) return { ana: a, field: f };
  }
  return null;
}

// The contour on the workbench: the field the viewer picked (ui.fieldKind, then
// ui.fieldVariant: the tool and, among its fields of that kind, the beams or a shell
// mesh and its surface) among the fields of every analysis beside the shown one (the
// beam and the shell analyses of the same load case and version), else the shown
// analysis' first of stress, damage, temperature, mode shape, displacement, on a mesh
// when there is one. With ui.compare, the same field of the previous design version
// comes along on one shared colour scale (contour.compare).
// In its place, the mesh's own views (ui.meshMode, quality.js): the mesh check of the
// field's mesh, its wall thicknesses, and the setup of the field's analysis. With no
// field to show (e.g. a mesh the solver refused), the newest mesh of the version.
function buildContour(s, chain, withCompare = ui.compare) {
  const base = fieldContour(s, chain, withCompare);
  const ana = (base && s.analyses[base.analysisId]) || chain.shown || null;
  const version = ana?.design_version ?? s.currentVersion;
  const newest = s.order.meshes.map((id) => s.meshes[id]).filter((m) => m.design_version === version).at(-1) ?? null;
  const rec = base ? base.mesh?.rec ?? null : setupOf(ana, s.meshes)?.rec ?? newest;
  const design = s.designs[rec?.design_version ?? version] ?? null;
  const thick = rec ? thicknessContourOf(base?.analysisId ?? ana?.analysis_id ?? null, rec, design) : null;
  const setup = setupContourOf(ana, s.meshes);
  const modes = MODE_ORDER.filter((m) => (m === "quality" ? !!qualityOf(rec) : m === "thickness" ? !!thick : !!setup));
  let mode = modes.includes(ui.meshMode) ? ui.meshMode : null;
  if (!base && !mode && modes.includes("quality")) mode = "quality";
  if (!mode) return base ? { ...base, meshModes: modes } : null;
  const metrics = mode === "quality" ? drawableMetrics(rec) : [];
  const metric = metrics.includes(ui.qualityMetric) ? ui.qualityMetric : metrics[0] ?? null;
  const ct = mode === "quality" ? qualityContourOf(base?.analysisId ?? ana?.analysis_id ?? null, rec, metric, design) : mode === "thickness" ? thick : setup;
  return { ...ct, kinds: base?.kinds ?? [], fieldKind: base?.kind ?? null, variants: [], meshModes: modes, metrics, canCompare: false, compare: null, toolTitle: base?.toolTitle ?? "" };
}

// (a field is held to the allowable only when it is the judged quantity: judged.js judgedContour)
const judgedContourOf = (s, ana, field, lcId) => judgedContour(s, ana, field, lcId, s.meshes);

function fieldContour(s, chain, withCompare = ui.compare) {
  const anas = analysesBeside(s, chain.shown);
  const all = anas.flatMap((a) => fieldsOf(a).map((f) => ({ a, f })));
  const kinds = FIELD_ORDER.filter((k) => all.some((x) => x.f.kind === k));
  const kind = kinds.includes(ui.fieldKind) ? ui.fieldKind : fieldsOf(chain.shown)[0]?.kind ?? kinds[0];
  const drawable = all.filter((x) => x.f.kind === kind && (!x.f.mesh_id || s.meshes[x.f.mesh_id]));
  const keyOf = (x) => `${x.a.tool}|${variantOf(x.f)}`;
  const ownPick = preferredField(drawable.filter((x) => x.a === chain.shown).map((x) => x.f), s.meshes);
  const pick =
    drawable.find((x) => keyOf(x) === ui.fieldVariant) ??
    drawable.find((x) => x.f === ownPick) ??
    drawable.find((x) => x.f === preferredField(drawable.map((y) => y.f), s.meshes)) ??
    null;
  if (!pick) return null;
  const lcId = chain.loadCase?.load_case_id ?? null;
  let ct = judgedContourOf(s, pick.a, pick.f, lcId);
  if (!ct) return null;
  const groups = anas.filter((a) => drawable.some((x) => x.a === a));
  const many = groups.length > 1;
  const variants = groups.map((a) => {
    const own = drawable.filter((x) => x.a === a);
    return {
      ana: a.analysis_id,
      buttons: own.map((x) => {
        const name = variantName(x.f, own.map((y) => y.f));
        return { key: keyOf(x), name: many ? `${toolTitle(s, a.tool)}・${name}` : name, on: x === pick };
      }),
    };
  });
  const prev = previousOf(s, pick.a, pick.f);
  let compare = null;
  if (prev && withCompare) {
    const before = judgedContourOf(s, prev.ana, prev.field, lcId);
    if (before) {
      const [after, was] = onSharedScale([ct, before]);
      ct = { ...after, key: `${after.key}|compare ${prev.ana.analysis_id}` };
      compare = { before: was, ana: prev.ana, version: prev.ana.design_version, nowVersion: pick.a.design_version };
    }
  }
  return { ...ct, kinds, variants, tool: pick.a.tool, toolTitle: toolTitle(s, pick.a.tool), canCompare: !!prev, compare };
}

// A contour with frames (the same field at moments of a time history) at the moment shown: the
// one nearest to the motion's time while the analysis that gave its loads plays, else the moment
// with the largest value. The legend says which moment.
function framedContour() {
  if (!contour?.frames) return contour;
  const playing = shownMotion && contour.frames.source && shownMotion.ana?.analysis_id === contour.frames.source;
  const k = frameIndex(contour, playing ? shownMotion.tau : null);
  const t = $("legend-t");
  if (t) t.textContent = `t = ${fmt(contour.frames.t_s[k], 3)} s${playing ? "" : " (いちばん大きい時刻)"}`;
  return atFrame(contour, k);
}

// The colour bar's bands, top (max) to bottom (min), every other one with its value.
function bandRows(ct, relative) {
  const rows = [];
  for (let i = BANDS - 1; i >= 0; i -= 1) {
    const top = ct.min + ((ct.max - ct.min) * (i + 1)) / BANDS;
    rows.push(`<span class="sw${i === 0 ? " end" : ""}" style="background:${BAND_COLORS[i]}"></span><span class="tk">${i % 2 === 1 && !relative ? fmt(top, 3) : ""}</span>`);
  }
  rows.push(`<span></span><span class="tk">${relative ? "" : fmt(ct.min, 3)}</span>`);
  return rows.join("");
}

// The card beside the legend: the list of the mesh's own view (quality.js), else hidden.
let meshCard = null;
function renderMeshCard(ct) {
  const html = ui.toggles.contour && ct?.mode ? modeCardHtml(ct) : "";
  if (!meshCard) {
    if (!html) return;
    meshCard = document.createElement("div");
    meshCard.className = "meshcard";
    meshCard.id = "meshcard";
    viewport.append(meshCard);
  }
  meshCard.hidden = !html;
  meshCard.innerHTML = html;
}

// motion / shown: the test's motion and the analysis it plays (the colours may come from another).
function renderLegend(ct, s = null, shown = null, motion = null) {
  const el = $("legend");
  if (!ct?.key || !ui.toggles.contour) {
    el.hidden = true;
    return;
  }
  el.hidden = false;
  const unit = unitLabel(ct.field.unit);
  const relative = ct.kind === "mode_shape";
  const fieldOn = (k) => !ct.mode && k === ct.kind;
  // the field kinds, then the mesh's own views (quality.js), in one row
  const modes = ct.meshModes ?? [];
  const kindRow =
    ct.kinds.length + modes.length > 1
      ? `<div class="kinds">${ct.kinds.map((k) => `<button data-field="${k}" class="${fieldOn(k) ? "on" : ""}">${FIELD_JA[k]}</button>`).join("")}` +
        `${modes.map((m) => `<button data-mode="${m}" class="${ct.mode === m ? "on" : ""}">${MODE_JA[m]}</button>`).join("")}</div>`
      : "";
  const zoomAct = ct.mesh?.maxAt
    ? `<button data-act="zoom" class="${ui.zoomMax ? "on" : ""}">${ui.zoomMax ? "元の見え方に戻る" : ct.mode === "quality" ? "いちばん悪い要素に寄る" : "最大の所に寄る"}</button>`
    : "";
  if (ct.mode) {
    const p = modeLegendParts(ct);
    const metricRow = ct.metrics?.length
      ? `<div class="kinds metrics">${ct.metrics.map((k) => `<button data-metric="${esc(k)}" class="${k === ct.metric ? "on" : ""}">${esc(METRIC_JA[k] ?? k)}</button>`).join("")}</div>`
      : "";
    el.innerHTML = kindRow + metricRow + (zoomAct ? `<div class="acts">${zoomAct}</div>` : "") + `<div class="ttl">${p.title}</div>` + (p.bar ?? `<div class="bar">${bandRows(ct, false)}</div>`) + p.under + `<div class="src">${p.src}</div>`;
    return;
  }
  const swatch = `<span style="display:inline-block;width:12px;height:12px;background:${OVER_COLOR};margin-right:6px"></span>`;
  // a field that is not the judged quantity: no allowable line; the bar says whose quantity is judged
  const over = ct.reference
    ? `<div class="ref-note" data-role="reference">${esc(JUDGED_JA.otherQuantity)}: ${esc(quantityName(ct.reference.quantity))}</div>`
    : ct.limitKnown
    ? `<div class="lim">${swatch}許容 ${fmt(ct.max, 4)} ${esc(unit)} を超えた所</div>`
    : ct.partLimit
      ? `<div class="lim">${swatch}${esc(ct.partLimit.part)} の許容 ${fmt(ct.partLimit.value, 4)} ${esc(unit)} を超えたとき</div>`
      : "";
  // one row of buttons per analysis (its tool's title is on each button when there are several)
  const nButtons = ct.variants.reduce((n, g) => n + g.buttons.length, 0);
  const chips =
    kindRow +
    (nButtons > 1
      ? ct.variants
          .map((g) => `<div class="kinds variants" data-analysis="${esc(g.ana)}">${g.buttons.map((v) => `<button data-variant="${esc(v.key)}" class="${v.on ? "on" : ""}">${esc(v.name)}</button>`).join("")}</div>`)
          .join("")
      : "");
  const acts =
    zoomAct +
    (ct.canCompare ? `<button data-act="compare" class="${ct.compare ? "on" : ""}">${ct.compare ? "比べるのをやめる" : "前の版と比べる"}</button>` : "");
  const mesh = ct.mesh ? `・${esc(ct.mesh.rec.mesh_id)} (要素 ${ct.mesh.rec.elements.length})` : "";
  const title = relative
    ? `${esc(ct.field.label ?? FIELD_JA[ct.kind])}${ct.frequency ? ` ${fmt(ct.frequency, 4)} Hz` : ""}<br><small>動きの大きさ (相対)・揺れは見せるための速さ</small>`
    : `${ct.reference ? `${esc(JUDGED_JA.refBar)}: ` : ""}${esc(ct.field.label ?? FIELD_JA[ct.kind])} [${esc(unit || "1")}]`;
  // before / after: each version's largest recorded value on the one scale above
  const cmp = ct.compare
    ? `<div class="cmp" data-compare="${ct.compare.version}-${ct.compare.nowVersion}">` +
      `<div>版 ${ct.compare.version} (左): 最大 ${esc(fmtVU(ct.compare.before.fieldMax, ct.field.unit))}</div>` +
      `<div>版 ${ct.compare.nowVersion} (右): 最大 ${esc(fmtVU(ct.fieldMax, ct.field.unit))}</div></div>`
    : "";
  // While a motion plays, colours from another analysis (a static one of the same load case) are that
  // analysis' one moment, pasted on the moving vehicle: the legend says so, with the model it ran on.
  const from = s?.analyses?.[ct.analysisId];
  const still = motion && from && from.analysis_id !== shown?.analysis_id && !from.series?.length && !ct.frames;
  const when = ct.frames
    ? `<div class="when">色は <span id="legend-t"></span> の値です。目盛りは全部の時刻で同じです。</div>`
    : still
    ? `<div class="when">色は ${esc(ct.toolTitle ?? from.tool)} (${esc(BASE_JA[from.base] ?? from.base ?? "")}) の、1 つの瞬間の値です。動きの再生の間も変わりません。</div>`
    : "";
  const conv = from?.mesh_convergence && from.analysis_id !== shown?.analysis_id ? `<div class="conv">${esc(convergenceLine(from.mesh_convergence))}</div>` : "";
  el.innerHTML =
    chips +
    (acts ? `<div class="acts">${acts}</div>` : "") +
    `<div class="ttl">${title}</div>` +
    when +
    conv +
    `<div class="bar">${bandRows(ct, relative)}</div>` +
    over +
    cmp +
    `<div class="src">色の元: ${esc(ct.analysisId)}・${esc(ct.toolTitle ?? "")}${mesh}${ct.limitKnown ? `・上端 = 許容 (${esc(ct.crit.basis)})` : relative ? "" : ct.compare ? "・上端 = 2 つの版の最大値" : "・上端 = 最大値"}</div>`;
}

// ---- before / after (the previous version's same field beside the current one) ----
// Side by side, not one after the other in time: the colours of the same place are
// compared at one glance, without holding one picture in memory while the other
// shows; both views look through the same camera (the right one's), so turning or
// zooming the right view moves the left with it.
let cmpView = null; // { el, bench, tags, key }
function setCompare(s, chain, ct) {
  const c = ct?.compare ?? null;
  const was = viewport.classList.contains("compare");
  viewport.classList.toggle("compare", !!c);
  if (cmpView) cmpView.el.hidden = !c;
  if (c) {
    if (!cmpView) {
      const el = document.createElement("div");
      el.className = "cmp-view";
      const tags = document.createElement("div");
      tags.className = "cmp-tags";
      viewport.prepend(el);
      viewport.append(tags);
      cmpView = { el, tags, bench: new Workbench3D(el, look, { quality }) };
      window.hiraku.cmpBench = cmpView.bench;
    }
    const b = cmpView.bench;
    b.setDesign(s.designs[c.ana.design_version] ?? null);
    b.setLoading(chain.loadCase?.params?.loading ?? null);
    b.setScene(chain.loadCase, c.ana);
    b.setContour(c.before);
    b.setToggles(ui.toggles);
    cmpView.tags.innerHTML = `<span data-kind="label" data-version="${c.version}">版 ${c.version}</span><span data-kind="label" data-version="${c.nowVersion}">版 ${c.nowVersion}</span>`;
  }
  cmpView?.tags && (cmpView.tags.hidden = !c);
  // The right view keeps clear the strip under the legend (from the legend's left edge to
  // the viewport's right, and a gap), so its vehicle is framed in the rest of its half; the
  // left view looks through the same camera, so its vehicle sits the same way in its half.
  const lg = $("legend");
  const room = c && !lg.hidden && lg.parentElement === viewport ? viewport.clientWidth - lg.offsetLeft + LEGEND_GAP_PX : 0; // (in the present mode the legend is beside the view)
  if (was !== !!c || room !== (bench.reserveRight ?? 0)) {
    if (was !== !!c) resize3d();
    bench.setReserve(room);
    // the half-wide (or whole) view: frame the vehicle again, or the focus for the new shape
    if (bench.design && !bench.focused) bench.fitPoints(bench.contentPoints(), VIEWS[bench.view ?? "default"], 1.08);
    bench.focusKey = undefined;
  }
}
const LEGEND_GAP_PX = 12;

// A motion's wheel lift as a passed limit (not on a step, where climbing lifts a wheel).
const liftLimit = (motion, lc) => motion?.liftT != null && liftIsLimit(lc);

// graphs: the analysis has series to chart without a motion (series against x).
function renderToggles(motion, lc, graphs = false, design = null) {
  const t = ui.toggles;
  const btn = (key, label, on) => `<button data-toggle="${key}" class="${on ? "on" : ""}">${label}</button>`;
  $("toggles").innerHTML =
    // modules: the two switches only for a design split into modules
    (hasModules(design) ? btn("modules", "モジュール", t.modules) + btn("spaces", "モジュールの空間", t.spaces) : "") +
    btn("shape", "形状", !t.contour) +
    btn("contour", "コンター", t.contour) +
    btn("arrows", "力の矢印", t.arrows) +
    btn("cg", "重心", t.cg) +
    btn("parts", "部品", t.parts) +
    btn("names", "名前", t.names) +
    btn("joints", "接合", t.joints) + // joints:
    (liftLimit(motion, lc) ? btn("limit", "限界の重ね", t.limit) : "") +
    (motion || graphs ? btn("graph", "グラフ", t.graph) : "");
}

// ---- the test's motion (series) -------------------------------------------
let motionMemo = { ana: null, motion: null };
function motionFor(ana) {
  if (ana !== motionMemo.ana) motionMemo = { ana, motion: ana && ana.status === "ok" ? motionOf(ana) : null };
  return motionMemo.motion;
}

const animRate = (motion) => Math.max(1, motion.duration / ANIM_MAX_S);

// Animation time (the series' own seconds). By default it starts when the
// analysis arrived in the replay and runs at animRate, so a frozen ?t= frame
// always shows the same pose.
function animTau(s, ana, motion) {
  if (ui.anim.local && ui.anim.key === ana.analysis_id) return ui.anim.tau;
  const tf = s.tOf[ana.analysis_id] ?? 0;
  return motion.t0 + Math.max(0, Math.min(motion.duration, (player.playhead - tf) * animRate(motion)));
}

const chart = new SeriesChart($("graph"), (key) => {
  ui.graphGroup = key;
});
let shownMotion = null; // { motion, ana, tau } of the last frame, for the bar's handlers

// The channels the graph offers for an analysis: the motion's (against time) and the
// series against another quantity, one Map per analysis (the chart redraws on a new one).
let graphMemo = { ana: null, channels: null };
function graphChannels(ana, motion) {
  if (ana !== graphMemo.ana) graphMemo = { ana, channels: new Map([...(motion?.channels ?? []), ...xSeriesOf(ana)]) };
  return graphMemo.channels;
}

function renderMotion(s, chain, motion) {
  const bar = $("animbar");
  viewport.classList.toggle("has-anim", !!motion);
  if (!motion) {
    bar.hidden = true;
    $("limitnote").hidden = true;
    shownMotion = null;
    // series against another quantity (a K&C bench) are charted without a motion
    const channels = chain.shown?.status === "ok" ? graphChannels(chain.shown, null) : null;
    const graph = $("graph");
    graph.hidden = !channels?.size || !ui.toggles.graph;
    if (!graph.hidden) ui.graphGroup = chart.set(channels, ui.graphGroup);
    return;
  }
  const ana = chain.shown;
  const tau = animTau(s, ana, motion);
  if (bench.frame?.t !== tau) bench.setFrame(motion.frame(tau));
  shownMotion = { motion, ana, tau };

  bar.hidden = false;
  const tool = s.tools[ana.tool]?.title ?? ana.tool;
  $("ab-title").textContent = `${chain.loadCase?.test_id ?? ""} ${String(tool).replace(/\s*[（(].*$/, "")}`;
  const f = motion.duration > 0 ? (tau - motion.t0) / motion.duration : 1;
  $("ab-fill").style.width = `${f * 100}%`;
  const lim = $("ab-lim");
  const limit = liftLimit(motion, chain.loadCase);
  lim.hidden = !limit;
  if (limit) lim.style.left = `${((motion.liftT - motion.t0) / (motion.duration || 1)) * 100}%`;
  $("ab-time").textContent = `${(tau - motion.t0).toFixed(1)} / ${motion.duration.toFixed(1)} 秒${animRate(motion) > 1 ? ` (×${fmt(animRate(motion), 2)})` : ""}`;
  $("ab-play").textContent = ui.anim.local && ui.anim.playing ? "❚❚" : "▶";
  const notes = [];
  const table = motion.channels.get("table_angle");
  if (table) notes.push(`台 ${fmt(nearestAt(table, tau), 3)} ${unitLabel(table.unit) === "deg" ? "°" : unitLabel(table.unit)}`);
  const speed = motion.channels.get("speed");
  if (speed) notes.push(`${fmt(nearestAt(speed, tau), 3)} ${unitLabel(speed.unit)}`);
  $("ab-note").textContent = notes.join("・");

  const note = $("limitnote");
  note.hidden = !limit || !ui.toggles.limit;
  if (!note.hidden) {
    const lifted = Object.entries(motion.liftAt)
      .filter(([, t]) => t <= tau)
      .sort((a, b) => a[1] - b[1]);
    const now = lifted.length ? `<br><b>${lifted.map(([id]) => esc(wheelName(id))).join("・")} が浮いた</b> (t = ${fmt(lifted[0][1], 4)} s)` : "";
    note.innerHTML = `半透明の赤 = 限界を超えたあとの姿勢 (輪が浮く)${now}`;
  }

  const graph = $("graph");
  graph.hidden = !ui.toggles.graph;
  if (!graph.hidden) {
    const marks = motion.liftT !== null ? [{ t: motion.liftT, label: "浮いた" }] : [];
    ui.graphGroup = chart.set(graphChannels(ana, motion), ui.graphGroup, marks);
    chart.cursor(tau);
  }
}

function animSeekFromPointer(ev) {
  if (!shownMotion) return;
  const { motion, ana } = shownMotion;
  const rect = $("ab-track").getBoundingClientRect();
  const f = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width));
  ui.anim = { key: ana.analysis_id, local: true, playing: false, tau: motion.t0 + f * motion.duration };
}

const board = new TileBoard($("grid-head"), $("tiles"));

// A6: the tiles, then a banner when the run stopped at its round limit with reds left.
function renderGrid(s) {
  const { reds, atLimit } = board.render(s, agentPets ? { has: (id) => agentPets.has(id), order: agentOrder(s) } : null);
  agentPets?.mount($("view-grid"), "grid");
  const banner = $("grid-banner");
  banner.hidden = !(atLimit && reds.length);
  if (!banner.hidden) {
    // stop_code says why in words; a record from before stop_code only has stop_reason
    // (round_limit / cost_limit, or the Claude runner's English "cost limit ..." / "turn limit ...").
    const code = s.stopCode ?? (/^cost limit\b/.test(s.stopReason ?? "") ? "cost_limit" : /^turn limit\b/.test(s.stopReason ?? "") ? "turn_limit" : s.stopReason);
    const words = code === "round_limit" ? `繰り返しの上限 (${s.project?.round_limit ?? "—"} 回) に達した` : STOP_CODE_JA[code];
    const why = words ?? (s.stopReason ? `止まった理由: ${esc(s.stopReason)}` : `繰り返しの上限 (${s.project?.round_limit ?? "—"} 回)`);
    banner.innerHTML = `<b>${why}。赤が ${reds.length} つ残っている: ${reds.map((d) => esc(DOMAIN_JA[d])).join("・")}</b><span>新しく走らせるときは、右上の「その場で走らせる」から</span>`;
  }
  // the newest report's next changes (none written yet, or no next: no block)
  const nx = $("report-next");
  const html = reportNextHtml(s.report);
  if (nx.dataset.html !== html) {
    nx.dataset.html = html;
    nx.innerHTML = html;
    keepOpen(nx);
  }
  nx.hidden = !html;
}

function renderStatus(s) {
  const act = s.activity;
  $("activity").innerHTML = act ? `<span class="dot"></span>${esc(agentLongName(act.agent) || "HIRAKU")}: ${esc(act.text)}` : "";
}

// A rule whose stops were all done later says 解決済み (resolvedHooks), apart from one still open.
function renderHooks(s, playhead, resolved) {
  const counts = {};
  const open = {};
  const recent = {};
  for (const d of [...s.hooks.denied, ...s.hooks.blockedStops]) {
    counts[d.rule] = (counts[d.rule] ?? 0) + 1;
    if (!resolved.has(d.seq)) open[d.rule] = true;
    if (playhead - d.t < SHOW_S) recent[d.rule] = true;
  }
  const state = (r) => (!counts[r.id] ? " ✓" : ` ×${counts[r.id]}${open[r.id] ? "" : `<span class="resolved">解決済み</span>`}`);
  const html = `hooks: ` + RULES.map((r) => `<span class="rule ${recent[r.id] ? "hit" : ""} ${counts[r.id] && !open[r.id] ? "done" : ""}" title="${esc(r.key)}"><span class="hk">${r.id}</span> ${esc(r.ja)}${state(r)}</span>`).join("");
  if (html !== hooksHtml) $("hooks").innerHTML = hooksHtml = html;
}
let hooksHtml = "";

function fade(dt) {
  if (dt < 0 || dt > SHOW_S) return 0;
  return Math.min(1, dt / 0.25, (SHOW_S - dt) / 0.6);
}

function renderTransient(s, playhead, chain, resolved) {
  const said = s.said;
  const b = $("bubble");
  const sa = said ? fade(playhead - said.t) : 0;
  b.style.opacity = sa;
  if (said && b.dataset.seq !== String(said.seq)) {
    b.dataset.seq = String(said.seq);
    b.innerHTML = `<div class="who"><span class="pet" style="--pet:${petColor(said.agent)}"></span>${esc(agentLongName(said.agent))}</div><div class="tx">${esc(said.text)}</div>`;
  }
  const den = s.denied;
  const h = $("hookflash");
  h.style.opacity = den ? fade(playhead - den.t) : 0;
  // what happened and what comes next, in short words (the recorded words are in the log)
  const done = den ? resolved.has(den.seq) : false;
  if (den && (h.dataset.seq !== String(den.seq) || h.dataset.done !== String(done))) {
    h.dataset.seq = String(den.seq);
    h.dataset.done = String(done);
    const w = hookWords(den.rule);
    h.innerHTML = `<small>hooks (${esc(w.id)} ${esc(w.ja)})${done ? `<span class="resolved">解決済み</span>` : ""}</small><b>${esc(w.what)}</b><span class="next">次に: ${esc(w.next)}</span>`;
  }
  const run = chain.running ?? Object.values(s.running)[0] ?? null;
  const r = $("running");
  r.style.opacity = run ? 1 : 0;
  if (run) {
    const pulse = 0.35 + 0.65 * (0.5 + 0.5 * Math.sin(playhead * Math.PI * 2));
    // the tool by the record's title up to its bracket (the log's words, state.js toolWord); the id under the pointer
    const tool = toolShort(s, run.tool);
    r.title = `${run.tool} (${run.load_case_id})`;
    r.innerHTML = `<span class="dot" style="opacity:${pulse.toFixed(2)}"></span>${esc(tool)} で解析中`;
  }
  renderHooks(s, playhead, resolved);
}

// ---- play bar ----------------------------------------------------------------
let tickKey = "";
function renderPlaybar() {
  const dur = Math.max(player.duration, 1e-6);
  const f = Math.max(0, Math.min(1, player.playhead / dur));
  $("pb-fill").style.width = `${f * 100}%`;
  $("pb-knob").style.left = `${f * 100}%`;
  $("pb-time").textContent = `${fmtTime(player.playhead)} / ${fmtTime(player.duration)} 秒`;
  $("pb-play").textContent = player.playing ? "❚❚" : "▶";
  $("pb-speed").textContent = `${player.speed}×`;
  const live = $("pb-live");
  live.classList.toggle("show", player.live);
  live.classList.toggle("on", player.live && player.liveAttached);
  const key = `${player.events.length}|${player.duration.toFixed(1)}`;
  if (key !== tickKey) {
    tickKey = key;
    $("pb-ticks").innerHTML = player.events
      .filter((e) => e.kind !== "cost.updated")
      .map((e) => {
        const cls = e.kind.startsWith("hook.") ? "hook" : e.kind === "verdict.recorded" || e.kind === "judgement.updated" ? "ok" : "";
        return `<i class="${cls}" style="left:${(e.t / dur) * 100}%"></i>`;
      })
      .join("");
  }
}

function seekFromPointer(ev) {
  const rect = $("pb-track").getBoundingClientRect();
  const f = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width));
  player.seek(f * player.duration);
}

// ---- the present mode -----------------------------------------------------------
// The colour bar and the graph move beside the view (into the rail, under the card), so
// neither lies over the 3D; the panels the mode hides (css: body.present) come back over
// the view with 詳しく.
function applyPresent() {
  const on = ui.present;
  document.body.classList.toggle("present", on);
  document.body.classList.toggle("present-detail", on && ui.detail);
  $("present-rail").hidden = !on;
  // (both are placed absolutely in the view, so where they go back among its children does not matter)
  (on ? $("present-rail") : viewport).append($("legend"), $("graph"));
  for (const b of document.querySelectorAll("#viewswitch [data-view]")) b.classList.toggle("on", (b.dataset.view === "present") === on);
  $("detail-btn").textContent = ui.detail ? "たたむ" : "詳しく";
  $("detail-btn").classList.toggle("on", ui.detail);
  const url = new URL(location.href);
  if (on) url.searchParams.set("present", "1");
  else url.searchParams.delete("present");
  history.replaceState(null, "", url);
  drawn.rev = -2;
  // the view changed size: frame the vehicle again in it
  requestAnimationFrame(() => {
    resize3d();
    if (bench.design && !bench.focused) bench.fitPoints(bench.contentPoints(), VIEWS[bench.view ?? "default"], 1.08);
    bench.focusKey = undefined;
  });
}
let resolvedMemo = { rev: -1, s: null, set: new Set() };

// ---- frame ----------------------------------------------------------------
function draw() {
  const s = player.state;
  const lcId = selectedLc(s);
  const tab = currentTab(s);
  const togglesKey = JSON.stringify(ui.toggles);
  const chain = chainFor(s, lcId, s.currentVersion);
  const motion = motionFor(chain.shown);
  if (resolvedMemo.rev !== s.rev || resolvedMemo.s !== s) resolvedMemo = { rev: s.rev, s, set: resolvedHooks(s) };
  const resolved = resolvedMemo.set;
  if (s.rev !== drawn.rev || lcId !== drawn.lc || tab !== drawn.tab || togglesKey !== drawn.toggles || ui.present !== drawn.present) {
    renderTop(s);
    renderChips(s, lcId);
    renderAgents(s);
    renderLog(s, resolved);
    renderTests(s, lcId);
    renderChain(s, chain);
    renderStatus(s);
    renderToggles(motion, chain.loadCase, chain.shown?.status === "ok" && xSeriesOf(chain.shown).size > 0, s.designs[chain.pred?.design_version ?? s.currentVersion] ?? null);
    const intakeTab = INTAKE_TABS.includes(tab);
    $("view-bench").hidden = tab !== "bench";
    $("view-grid").hidden = tab !== "grid";
    $("view-intake").hidden = !intakeTab;
    $("view-shelf").hidden = tab !== "A4";
    $("view-asm").hidden = tab !== "A7";
    $("main").classList.toggle("wide", tab === "grid" || tab === "A4" || tab === "A7" || intakeTab); // A6, A4, A7 and the intake screens use the whole width
    for (const b of document.querySelectorAll(".tab")) b.classList.toggle("on", b.dataset.tab === tab);
    markTabs(s);
    if (tab === "grid") renderGrid(s);
    if (intakeTab) renderIntake(tab);
    if (tab === "A4") renderShelf();

    const design = s.designs[chain.pred?.design_version ?? s.currentVersion] ?? null;
    bench.setDesign(design);
    bench.setLoading(chain.loadCase?.params?.loading ?? null);
    bench.setMotion(motion);
    bench.setScene(chain.loadCase, chain.shown);
    let ct = buildContour(s, chain);
    // the present mode shows a shell's before / after side by side when there is an earlier version
    if (ui.present && tab === "bench" && ct?.mesh && ct.canCompare && !ct.compare) ct = buildContour(s, chain, true);
    if ((ct?.key ?? null) !== (contour?.key ?? null)) contour = ct;
    bench.setContour(ui.toggles.contour ? framedContour() : null);
    if (!contour?.mesh?.maxAt) ui.zoomMax = false;
    renderLegend(contour, s, chain.shown, motion); // before setCompare, which keeps the legend's strip clear
    renderMeshCard(contour);
    setCompare(s, chain, ui.toggles.contour && tab === "bench" ? contour : null);
    bench.setFocus(ui.zoomMax && ui.toggles.contour ? contour : null);
    cmpView?.bench.setCloseUp(!!bench.focused);
    // With a motion the arrows come from the series, frame by frame.
    bench.setVectors(chain.shown?.status === "ok" && !motion ? chain.shown.vectors ?? null : null);
    bench.setToggles(ui.toggles);
    // modules: the module colours and spaces on the 3D, and their legend
    modLayer.set({ colors: ui.toggles.modules, spaces: ui.toggles.spaces });
    renderModuleLegend(viewport, bench.design, { colors: ui.toggles.modules, spaces: ui.toggles.spaces }, modLayer);
    renderJointLegend(viewport, bench.joints, !!ui.toggles.joints && tab === "bench"); // joints:
    // what is going on, in one sentence; and the present mode's card for this scene
    const now = nowSentence(s, chain, (tool) => toolTitle(s, tool));
    $("now").textContent = now;
    $("now").title = now;
    $("now").hidden = !now;
    // the line keeps left of the toggles (their row grows with the switches a design offers)
    // (too little room there: on a row of its own under the toggles, as wide as the 3D)
    const tg = $("toggles");
    const nowEl = $("now");
    if (!nowEl.hidden && tg.offsetWidth) {
      const room = tg.offsetLeft - nowEl.offsetLeft - 12;
      const under = room < 360;
      nowEl.style.top = under ? `${tg.offsetTop + tg.offsetHeight + 8}px` : "";
      nowEl.style.maxWidth = `${under ? viewport.clientWidth - 2 * nowEl.offsetLeft : room}px`;
    }
    const scene = presentScene({ s, chain, contour, tab, toolTitle: (tool) => toolTitle(s, tool) });
    document.body.dataset.presentScene = scene.key;
    if (ui.present) $("present-card").innerHTML = presentCardHtml(scene, now, { dev: DEV });
    drawn = { ...drawn, rev: s.rev, lc: lcId, tab, toggles: togglesKey, present: ui.present };
  }
  renderMotion(s, chain, motion);
  // modules: the round's proposals (or the version built from them) at the replay's time, under the now line
  const rc = renderRoundCard(viewport, tab === "bench" ? roundCardHtml(s, player.playhead) : "");
  if (!rc.hidden) rc.style.top = `${$("now").hidden ? 10 : $("now").offsetTop + $("now").offsetHeight + 8}px`;
  if (contour?.frames && ui.toggles.contour) bench.setContour(framedContour());
  renderTransient(s, player.playhead, chain, resolved);
  renderPlaybar();
  bench.animate(player.playhead, !!chain.running);
  if (tab === "bench") {
    bench.render();
    if (cmpView && !cmpView.el.hidden) {
      cmpView.bench.animate(player.playhead, false);
      cmpView.bench.copyCamera(bench);
      cmpView.bench.render();
    }
  }
  else if (tab === "grid") board.tick(player.playhead);
  // the pets of the list (the workbench; hidden in the present mode) or of the grid
  // (playing: PET_PAINTS pets a frame, so the 3D keeps its pace; a frame stopped at t: all of them)
  if (agentPets && (tab === "bench" || tab === "grid")) agentPets.tick(s, player.events, player.playhead, tab, ui.frozen ? Infinity : PET_PAINTS);
  else if (tab === "A7") (asmPanel ??= window.hiraku.asm = new AssemblyPanel($("view-asm"))).render(s);
  if (INTAKE_TABS.includes(tab)) syncIntakeTime();
  drawRulePeek(s, INTAKE_TABS.includes(tab));
}

let lastTs = null;
let lastPlayhead = null;
function loop(ts) {
  const dt = lastTs === null ? 0 : Math.min(0.25, (ts - lastTs) / 1000);
  lastTs = ts;
  if (!ui.frozen) player.tick(dt);
  // Moving the replay hands the test's motion back to the replay clock.
  if (lastPlayhead !== null && player.playhead !== lastPlayhead) ui.anim.local = false;
  lastPlayhead = player.playhead;
  if (ui.anim.local && ui.anim.playing && shownMotion) {
    const { motion } = shownMotion;
    ui.anim.tau = Math.min(motion.t1, ui.anim.tau + dt * animRate(motion));
    if (ui.anim.tau >= motion.t1) ui.anim.playing = false;
  }
  draw();
  requestAnimationFrame(loop);
}

const nextFrame = () => new Promise((r) => requestAnimationFrame(() => r()));

// A time the bar shows as the end (fmtTime: 0.1 s) is the end: ?t=2656 on a record whose last
// event is at 2656.009 shows it finished, as the bar's "2656.0 / 2656.0" says.
const END_SNAP_S = 0.05;

async function seekAndDraw(t) {
  ui.frozen = true;
  player.pause();
  player.seek(t >= player.duration - END_SNAP_S ? player.duration : t);
  draw();
  await nextFrame();
  draw();
  await nextFrame();
  document.documentElement.dataset.t = String(player.playhead);
  return player.playhead;
}

let readyResolve;
const ready = new Promise((r) => (readyResolve = r));
window.hiraku = {
  // a new request's steps, for the tests (the screen's own controls call the same)
  request: draft ? { send: (t) => draftSend(t), answer: (a) => draftAnswer(a), start: (m) => draftStart(m), get state() { return draft; } } : null,
  player,
  ready,
  seek: seekAndDraw,
  state: () => player.state,
  bench,
  modules: modLayer, // modules: what the module layer draws (for the tests)
  pets: () => agentPets,
  // Replace what is shown with these events (parsed objects) and stop at t.
  load: (events, t = 0) => {
    feed?.stop();
    resetPlayer();
    addEvents(events);
    return seekAndDraw(t);
  },
};

// ---- input ------------------------------------------------------------------
$("pb-play").addEventListener("click", () => {
  ui.frozen = false;
  player.toggle();
});
$("pb-speed").addEventListener("click", () => player.cycleSpeed());
$("pb-live").addEventListener("click", () => {
  ui.frozen = false;
  player.attachLive();
});
$("pb-track").addEventListener("pointerdown", (ev) => {
  const track = ev.currentTarget;
  track.setPointerCapture(ev.pointerId);
  seekFromPointer(ev);
  const move = (e) => seekFromPointer(e);
  track.addEventListener("pointermove", move);
  track.addEventListener("pointerup", () => track.removeEventListener("pointermove", move), { once: true });
});
$("ab-play").addEventListener("click", () => {
  if (!shownMotion) return;
  const { motion, ana, tau } = shownMotion;
  if (ui.anim.local && ui.anim.key === ana.analysis_id) {
    ui.anim.playing = !ui.anim.playing;
    if (ui.anim.playing && ui.anim.tau >= motion.t1) ui.anim.tau = motion.t0;
  } else {
    ui.anim = { key: ana.analysis_id, local: true, playing: true, tau: tau >= motion.t1 ? motion.t0 : tau };
  }
});
$("ab-track").addEventListener("pointerdown", (ev) => {
  const track = ev.currentTarget;
  track.setPointerCapture(ev.pointerId);
  animSeekFromPointer(ev);
  track.addEventListener("pointermove", animSeekFromPointer);
  track.addEventListener("pointerup", () => track.removeEventListener("pointermove", animSeekFromPointer), { once: true });
});
$("tests").addEventListener("click", (ev) => {
  const b = ev.target.closest("button[data-lc]");
  if (!b) return;
  ui.pinnedLc = b.dataset.lc || null;
  drawn.rev = -2;
});
$("legend").addEventListener("click", (ev) => {
  const b = ev.target.closest("button[data-field], button[data-variant], button[data-act], button[data-mode], button[data-metric]");
  if (!b) return;
  if (b.dataset.act === "zoom") ui.zoomMax = !ui.zoomMax;
  else if (b.dataset.act === "compare") ui.compare = !ui.compare;
  else if (b.dataset.mode) ui.meshMode = b.dataset.mode;
  else if (b.dataset.metric) ui.qualityMetric = b.dataset.metric;
  else if (b.dataset.field) [ui.fieldKind, ui.meshMode] = [b.dataset.field, null];
  else [ui.fieldVariant, ui.meshMode] = [b.dataset.variant, null];
  drawn.rev = -2;
});
$("toggles").addEventListener("click", (ev) => {
  const b = ev.target.closest("button[data-toggle]");
  if (!b) return;
  const k = b.dataset.toggle;
  if (k === "shape") ui.toggles.contour = false;
  else ui.toggles[k] = !ui.toggles[k];
  // modules: the module colours or spaces and the contour take turns (one legend in its place)
  if ((k === "modules" || k === "spaces") && ui.toggles[k]) ui.toggles.contour = false;
  if (k === "contour" && ui.toggles.contour) ui.toggles.modules = ui.toggles.spaces = false;
});
$("viewswitch").addEventListener("click", (ev) => {
  const b = ev.target.closest("button");
  if (!b) return;
  if (b.dataset.view) ui.present = b.dataset.view === "present";
  else if (b.id === "detail-btn") ui.detail = !ui.detail;
  if (!ui.present) ui.detail = false;
  applyPresent();
});
// A click (not a drag) near a wheel's arrows picks that wheel: its forces get the words.
let pressAt = null;
viewport.addEventListener("pointerdown", (ev) => {
  pressAt = ev.target === bench.renderer.domElement ? { x: ev.clientX, y: ev.clientY } : null;
});
viewport.addEventListener("pointerup", (ev) => {
  if (!pressAt || Math.hypot(ev.clientX - pressAt.x, ev.clientY - pressAt.y) > 5) return;
  const r = bench.renderer.domElement.getBoundingClientRect();
  if (bench.pickAt((ev.clientX - r.left) / stageScale, (ev.clientY - r.top) / stageScale)) drawn.rev = -2;
  pressAt = null;
});
$("tabs").addEventListener("click", (ev) => {
  const b = ev.target.closest("button[data-tab]");
  if (!b) return;
  ui.pinnedTab = b.dataset.tab;
  ui.tabPlan = []; // a tab pressed by hand ends ?tabs=
});
window.addEventListener("keydown", (ev) => {
  if (ev.target.closest("input, textarea")) return;
  if (ev.code === "Space") {
    ev.preventDefault();
    ui.frozen = false;
    player.toggle();
  } else if (ev.code === "ArrowRight") player.step(1);
  else if (ev.code === "ArrowLeft") player.step(-1);
  else if (ev.code === "Home") player.seek(0);
  else if (ev.code === "End") player.seek(player.duration);
});

// Drop an events.jsonl onto the page to replay it.
window.addEventListener("dragover", (ev) => {
  ev.preventDefault();
  $("dropzone").hidden = false;
});
window.addEventListener("dragleave", (ev) => {
  if (ev.target === $("dropzone")) $("dropzone").hidden = true;
});
window.addEventListener("drop", async (ev) => {
  ev.preventDefault();
  $("dropzone").hidden = true;
  const file = ev.dataTransfer?.files?.[0];
  if (!file) return;
  feed?.stop();
  loadEvents(parseJsonl(await file.text()), { live: false });
});

// ---- loading ------------------------------------------------------------------
function resetPlayer() {
  player.clear();
  motionSpans.clear();
  player.live = false;
  player.liveAttached = false;
  player.playhead = 0;
  player.minEnd = 0;
  ui.anim = { key: null, local: false, playing: false, tau: 0 };
  player.rebuild();
  drawn = { rev: -1, lc: undefined, tab: undefined, toggles: "", logLen: 0, logFirstSeq: null, logRev: 0, resolved: "" };
  $("runlist").hidden = true;
}

function startPlayback() {
  // the pets first (a page opened at ?t= draws them in its first frame)
  Promise.all([petsReady, loadModuleNames()]).then(play); // modules: the module names before the first frame
}
function play() {
  if (params.has("t")) {
    seekAndDraw(Number(params.get("t")) || 0).then(() => readyResolve(player.playhead));
    return;
  }
  ui.frozen = false;
  if (player.live) player.attachLive();
  else if (params.get("autoplay") !== "0") player.play();
  readyResolve(player.playhead);
}

// Add events to the player; a test's motion lengthens the replay so it can play
// to its end (in display time: the player's copy of the event).
function addEvents(events) {
  const from = player.events.length;
  player.append(events);
  for (const ev of player.events.slice(from)) {
    if (ev.kind !== "analysis.finished") continue;
    const span = motionSpanOf(ev);
    if (span !== null) player.minEnd = Math.max(player.minEnd, ev.t + Math.min(span, ANIM_MAX_S));
  }
}

function loadEvents(events, { live }) {
  resetPlayer();
  addEvents(events);
  player.live = live;
  startPlayback();
}

// While a record is read (all of it, before it plays): a notice with how much is read.
function loadingNote(body) {
  let el = $("loading-note");
  if (!body) return el?.remove();
  if (!el) {
    el = document.createElement("div");
    el.id = "loading-note";
    el.className = "loading-note";
    document.body.append(el);
  }
  const mb = (n) => fmt(n / 1e6, 2);
  // (before the first part comes back the size is not known yet: the note shows at once, so the
  // screen is never an empty bench with nothing to press)
  const done = body.size ? ` ${mb(body.next_offset ?? 0)} / ${mb(body.size)} MB` : "";
  el.innerHTML = `<div>記録を読み込んでいます…${esc(done)}</div>` +
    `<div class="ln-sub">読み終わると再生が始まります。 <a href="${withDev("?list=1")}">☰ 一覧へ</a></div>`;
}

// Why a record could not be read, in words (the status and the address only under ?dev=1).
function loadFailedWhy(e) {
  if (DEV) return String(e?.message ?? e);
  if (e?.status === 404) return "この記録は見つかりませんでした。消されたか、名前が違います。";
  if (e?.status) return `サーバーが記録を返せませんでした (${e.status})。`;
  if (e instanceof SyntaxError) return "サーバーの返事が読めませんでした。";
  return "サーバーにつながりませんでした。HIRAKU が動いているか確かめてください。";
}

// A record that could not be read: why, and what to press next (open it again, or the list).
function loadFailedNote(runId, e) {
  loadingNote(null);
  const el = document.createElement("div");
  el.id = "loading-note";
  el.className = "loading-note failed";
  el.innerHTML = `<div>記録を読み込めませんでした</div><div class="ln-sub why">${esc(loadFailedWhy(e))}</div>` +
    `<div class="ln-sub"><a href="${withDev(`?run=${encodeURIComponent(runId)}`)}">↻ 開き直す</a> <a href="${withDev("?list=1")}">☰ 一覧へ</a></div>`;
  document.body.append(el);
}

async function openRun(runId) {
  resetPlayer();
  feed = new EventFeed(runId, (evs, body) => {
    addEvents(evs);
    if (!feed?.loaded && body && body.next_offset < body.size) loadingNote(body);
  });
  loadingNote({});
  try {
    await feed.readAll();
  } catch (e) {
    loadFailedNote(runId, e);
    return;
  }
  feed.loaded = true;
  loadingNote(null);
  const follow = !player.finished && params.get("live") !== "0" && !params.has("t");
  player.live = follow;
  if (follow) {
    feed.follow(() => player.finished);
    // started from this screen? then it can be stopped from here
    fetch("/api/launch", { cache: "no-store" })
      .then((r) => r.json())
      .then((info) => {
        if (info.running?.run_id === runId) {
          ui.stoppable = runId;
          drawn.rev = -1;
        }
      })
      .catch(() => {});
  }
  startPlayback();
}

// A text of the run list (a want, a demo row's line), escaped, with each short aside in brackets
// kept on one line (the run list's .nw): not "(無料・ / Claude なし)".
const prose = (s) => esc(s).replace(/\(([^()]{1,40})\)/g, '<span class="nw">($1)</span>');

async function fetchRuns() {
  return (await fetch("/api/runs", { cache: "no-store" })).json();
}

// The list for people on a narrow window (a phone): out of the 1920 x 1080 stage, which would scale it
// down to a fifth, onto the page at its own size. The stage keeps it everywhere else (and under ?dev=1).
const NARROW_LIST_PX = 760;
function placeRunList() {
  const box = $("runlist");
  const narrow = !DEV && window.innerWidth < NARROW_LIST_PX;
  const home = narrow ? document.body : stage;
  if (box.parentElement !== home) home.insertBefore(box, narrow ? null : $("dropzone"));
  box.classList.toggle("narrow", narrow);
}
window.addEventListener("resize", placeRunList);

// A want, short for a row of the list: its first sentence without the asides in brackets (the
// whole want is the row's title, on hover).
const wantShort = (s) => (s.split("。")[0].replace(/\s*[(（][^()（）]*[)）]/g, "").trim() || s);

async function showRunList(message = "", runs = null) {
  const box = $("runlist");
  box.hidden = false;
  placeRunList();
  if (!runs) {
    try {
      runs = await fetchRuns();
    } catch (e) {
      runs = [];
      message = message || `実行の一覧を読めなかった (${e})。python -m hiraku.webserve で開いてください`;
    }
  }
  listRuns = runs;
  box.classList.toggle("plain", !DEV);
  // what drove the run (project.driver); nothing for older records without it
  const drvOf = (r) => (DRIVER_JA[r.driver] ? ` <span class="drv" data-driver="${esc(r.driver)}">${DRIVER_JA[r.driver]}</span>` : "");
  if (!DEV) {
    // for people: what replaying the record shows (how it was driven, how far the design went), how it
    // ended, and one verb; the run ids and the contract sample under ?dev=1
    const row = (r) => {
      const href = `?run=${encodeURIComponent(r.run_id)}`;
      const state = runState(r);
      // under the title: when, and the want, short (what is not in the record is left out)
      const meta = [
        r.when ? `<span class="nw when">${esc(r.when)}</span>` : "",
        r.want ? `<span class="rwant" title="${esc(r.want)}">依頼: ${esc(wantShort(r.want))}</span>` : "",
      ].filter(Boolean);
      return (
        `<tr data-run="${esc(r.run_id)}"><td><div class="rsum">${esc(runSummaryJa(r) || "記録")}</div><div class="rmeta">${meta.join('<span class="sep"> · </span>')}</div></td>` +
        `<td class="state" data-state="${state}">${RUN_STATE_JA[state]}</td><td><a href="${href}">見る</a></td></tr>`
      );
    };
    const newest = (a, b) => (a.run_id < b.run_id ? 1 : a.run_id > b.run_id ? -1 : 0);
    const people = runs.filter((r) => !r.sample);
    const shown = people.filter((r) => !isOlderRecord(r)).sort(newest);
    const older = people.filter((r) => isOlderRecord(r)).sort(newest);
    box.innerHTML =
      `<h2>HIRAKU でできること</h2><p class="lead">作りたいものを書くと、Claude が確かめる解析を選び、計算して、設計を直します。</p>` +
      `${message ? `<p class="err">${esc(message)}</p>` : ""}<div id="launch"></div>` +
      (shown.length ? `<h3>記録を見る</h3><table class="runs">${shown.map(row).join("")}</table>` : "") +
      (older.length ? `<details class="older"><summary>この PC の記録 (${older.length})</summary><table class="runs">${older.map(row).join("")}</table></details>` : "");
    renderLaunch();
    return;
  }
  const row = (r) => {
    const st = r.sample ? "形の見本 (数値は計算結果ではない)" : r.finished ? "終わった" : "書き込み中";
    const href = withDev(`?run=${encodeURIComponent(r.run_id)}`);
    const links = r.finished ? `<a href="${href}">再生</a><a href="${href}&video=1">動画の配置で</a>` : `<a href="${href}">追いかける</a>`;
    const mark = r.default ? `<span class="def">おすすめ</span>` : "";
    return `<tr data-run="${esc(r.run_id)}"><td>${prose(r.want)}<div class="rid">${esc(r.run_id)}${drvOf(r)}</div></td><td><span class="state">${esc(st)}</span>${mark}</td><td class="acts"><div class="links">${links}</div></td></tr>`;
  };
  const table = (title, list) => (list.length ? `<h3>${title}</h3><table><tr><th>依頼</th><th>状態</th><th></th></tr>${list.map(row).join("")}</table>` : "");
  box.innerHTML =
    `<h2>実行の一覧</h2>${message ? `<p class="err">${esc(message)}</p>` : ""}` +
    `<div id="launch"></div>` +
    table("保存した実行", runs.filter((r) => r.saved)) +
    table("この PC の実行 (runs/)", runs.filter((r) => !r.saved && !r.sample)) +
    table("契約の形の見本", runs.filter((r) => r.sample)) +
    `<p class="note">events.jsonl をこの画面に落としても再生できます。</p>`;
  renderLaunch();
}

// ---- starting a run (the second way in, next to the saved run) -------------
// A scripted run can always start; a Claude run only when the server's window
// has the API key (the screen never asks for it), and only after its cost limit
// was shown and confirmed.
let launchConfirm = null; // the requirements file waiting for the Claude confirmation
let listRuns = []; // /api/runs as the run list last showed it (the recommendation picks from it)

async function renderLaunch(message = "") {
  const box = $("launch");
  if (!box) return;
  let info;
  try {
    info = await (await fetch("/api/launch", { cache: "no-store" })).json();
  } catch (e) {
    info = { available: false };
  }
  if (!DEV) {
    box.innerHTML = plainLaunch(info, message);
    return;
  }
  if (info.available === false) {
    box.innerHTML = "";
    return;
  }
  const busy = !!info.running;
  const rows = (info.requirements ?? [])
    .map((r) => {
      if (r.error) return `<div class="lreq"><div>${esc(r.file)}<div class="err">読めなかった: ${esc(r.error)}</div></div></div>`;
      return `<div class="lreq"><div>${prose(r.want)}<div class="rid">${esc(r.file)}</div></div>${runButtons(info, r, r.file, busy)}</div>`;
    })
    .join("");
  const rid = info.running?.run_id ?? "";
  const stop = info.running?.stop_requested ? `<span class="why">止めるよう伝えた</span>` : `<button data-launch-stop="${esc(rid)}">止める</button>`;
  const running = busy ? `<p>走っている: <a href="${withDev(`?run=${encodeURIComponent(rid)}`)}">${esc(rid)} を追いかける</a> ${rid ? stop : ""}</p>` : "";
  // without the key in the server's window, only scripted runs and replays are there (the key is never asked for)
  const keyless = info.claude_ready
    ? ""
    : `<p class="keyless">HIRAKU を起こした窓に ${esc(info.key_name)} が無いので、使えるのは決まった手順の再現と、記録の再生だけです。Claude で走らせるには、キーを入れた窓から起こし直してください。</p>`;
  // a new request first: the way in for someone who brings their own product
  const fresh = `<p class="fresh-top"><a class="new-request" href="${withDev("?new=1")}">＋ 新しく依頼する</a> <span class="why">${TAB_JA.A1.name}を書く → 分野 → ${TAB_JA.A2.name}に答える → 始める</span></p>`;
  box.innerHTML =
    fresh +
    demoSection(info, busy) +
    `<h3>その場で走らせる</h3>${keyless}${message ? `<p class="err">${esc(message)}</p>` : ""}${running}${rows}`;
}

// 決まった手順で再現 / Claude で走らせる (?dev=1) for one requirements file r ({file, cost_limit_usd, round_limit}).
// ask: what launchConfirm holds while this row's Claude confirmation is open. A Claude run
// needs the key in the server's window, and the cost limit is shown and confirmed first.
// r.modes (when the demo runs list the file): only those buttons. r.estimate_usd (a demo row):
// the estimate of a real Claude run, shown with the limit; when it is over the limit, the button
// and the confirmation say the run stops at the limit (it can still be started).
function runButtons(info, r, ask, busy) {
  const usd = (v) => (typeof v === "number" ? fmtUsd(v) : "—");
  const file = esc(r.file);
  const modes = r.modes ?? ["scripted", "claude"];
  const demo = "estimate_usd" in r;
  const over = demo && overLimit(r);
  const overNote = over ? `<span class="over">見込みが上限を超えるので、上限 ${usd(r.cost_limit_usd)} で止まる</span>` : "";
  let claude = ""; // (none when the demo runs list the file without claude)
  if (!modes.includes("claude")) claude = "";
  else if (!info.claude_ready) claude = `<button disabled>Claude で走らせる</button><span class="why">サーバーを立ち上げた窓に ${esc(info.key_name)} が無いので押せない</span>${overNote}`;
  else if (launchConfirm === ask)
    claude =
      `<span class="confirm">Claude を呼びます。${demo ? `見込みは ${usd(r.estimate_usd)}、` : ""}費用の上限は ${usd(r.cost_limit_usd)}、繰り返しは ${esc(r.round_limit ?? "—")} 回まで。` +
      `${over ? `見込みが上限を超えるので、上限 ${usd(r.cost_limit_usd)} で止まります。` : ""}走らせますか?</span>` +
      `<button class="go" data-launch="claude" data-req="${file}">走らせる</button><button data-launch-cancel="1">やめる</button>`;
  else {
    const label = demo ? `見込み ${usd(r.estimate_usd)}・上限 ${usd(r.cost_limit_usd)}` : `費用の上限 ${usd(r.cost_limit_usd)}`;
    claude = `<button data-launch-ask="${esc(ask)}" ${busy ? "disabled" : ""}>Claude で走らせる (${label})</button>${overNote}`;
  }
  const scripted = modes.includes("scripted")
    ? `<button data-launch="scripted" data-req="${file}" ${busy ? "disabled" : ""}>決まった手順で再現 (Claude なし)</button>`
    : "";
  return `<div class="lbtns">${scripted}${claude}</div>`;
}

// The two values as written (no estimate or no limit: not over).
const overLimit = (r) => typeof r.estimate_usd === "number" && typeof r.cost_limit_usd === "number" && r.estimate_usd > r.cost_limit_usd;

// デモの実行: the rows of data/requirements/demo_runs.yaml (the design department's), in its order.
// A file row runs like the files below; a request row opens A1 (?new=1&demo=<id>) with the
// product's want written in, to be sent by the person.
function demoSection(info, busy) {
  if (info.demo_runs_error) return `<h3>デモの実行</h3><p class="err">デモの一覧を読めなかった: ${esc(info.demo_runs_error)}</p>`;
  const list = info.demo_runs ?? [];
  if (!list.length) return "";
  // The title first; what the developers read (what it shows, with its tab names and hooks; the
  // cut tags; the cost line; the file) under くわしく, after it.
  const rows = list.map((d) => {
    const cuts = (d.cuts ?? []).map((c) => `<span class="cut">${esc(c)}</span>`).join("");
    const head = `<div class="dhead"><b>${esc(d.title || d.id)}</b></div>`;
    const detail = [d.shows ? `<div class="shows">${prose(d.shows)}</div>` : "", cuts ? `<div class="cuts">${cuts}</div>` : ""];
    let left = "", right = "";
    if (d.error) left = `<div class="err">使えない: ${esc(d.error)}${d.file ? ` (${esc(d.file)})` : ""}</div>`;
    else if (d.file) {
      // (a file row carries its requirements' product too; file is what tells the two apart)
      const usd = (v) => (typeof v === "number" ? fmtUsd(v) : "—");
      detail.push(
        `<div class="cost${overLimit(d) ? " over" : ""}">本物の Claude の運転: 見込み ${usd(d.estimate_usd)}・上限 ${usd(d.cost_limit_usd)}</div>` +
          `<div class="rid">${esc(d.file)}</div>`,
      );
      right = runButtons(info, d, `demo:${d.id}`, busy);
    } else {
      detail.push(`<div class="rid">${prose(d.want)} <span class="nw">(products/${esc(d.product)}.yaml)</span></div>`);
      right =
        `<div class="lbtns"><a class="demo-a1" href="${withDev(`?new=1&demo=${encodeURIComponent(d.id)}`)}">依頼する</a>` +
        `<span class="why">依頼の文が入った A1 が開く。送るのは押してから</span></div>`;
    }
    const more = `<details class="more"><summary>くわしく</summary>${detail.join("")}</details>`;
    return `<div class="lreq demo" data-demo="${esc(d.id)}"><div>${head}${left}${more}</div>${right}</div>`;
  });
  return `<h3>デモの実行</h3><div class="demo-runs">${rows.join("")}</div>`;
}

// ---- the run list for people (no ?dev=1) --------------------------------------
// One line per row saying what it lets you do, one verb per button: 見る (replay a record),
// 再現する (the scripted run, no Claude), 実行する (a Claude run, after its confirmation),
// 試す (A1 with the request written in). The files, cut numbers and cost lines are inside each
// row's くわしく, or under ?dev=1; without the key, one short word beside the 実行する that cannot be pressed.
const README_RUN = "https://github.com/Kuglof16/hiraku#%E5%8B%95%E3%81%8B%E3%81%97%E6%96%B9"; // README の「動かし方」 (A1's くわしく)
const NEEDS_KEY_JA = "Claude のキーが要ります";

// おすすめ: the server's default saved run (else the newest saved one) as a replay; with no saved run,
// the scripted run of the demo row PICK_DEMO_ID. Nothing when neither is there.
function pickHtml(info, busy) {
  const saved = listRuns.filter((r) => r.saved && r.finished);
  const run = saved.find((r) => r.default) ?? saved[0];
  const head = `<span class="tag">おすすめ</span>`;
  if (run)
    return (
      `<div class="pick" data-pick="run" data-run="${esc(run.run_id)}">${head}<div class="pline"><b>まずはこれ</b> — HIRAKU が設計を試験して、不合格の所を直すまでを、保存した記録で見ます` +
      `${run.want ? `<div class="sub">依頼: ${esc(run.want)}</div>` : ""}</div><a class="go" href="?run=${encodeURIComponent(run.run_id)}">見る</a></div>`
    );
  const d = (info.demo_runs ?? []).find((r) => r.id === PICK_DEMO_ID && r.file && !r.error && (r.modes ?? []).includes("scripted"));
  if (!d) return "";
  return (
    `<div class="pick" data-pick="demo" data-demo="${esc(d.id)}">${head}<div class="pline"><b>まずはこれ</b> — 小さなオフロード車を試験して、不合格の所を直すまでを、決まった手順で再現します <span class="nw">(無料・Claude なし)</span>` +
    `${d.want ? `<div class="sub">依頼: ${prose(d.want)}</div>` : ""}</div><button class="go" data-launch="scripted" data-req="${esc(d.file)}" ${busy ? "disabled" : ""}>再現する</button></div>`
  );
}

// What ?dev=1 shows for a demo row, inside its くわしく.
function demoDetail(d) {
  const usd = (v) => (typeof v === "number" ? fmtUsd(v) : "—");
  const cuts = (d.cuts ?? []).map((c) => `<span class="cut">${esc(c)}</span>`).join("");
  const lines = [`<div class="dhead"><b>${esc(d.title || d.id)}</b>${cuts}</div>`];
  if (d.shows) lines.push(`<div class="shows">${prose(d.shows)}</div>`);
  if (d.error) lines.push(`<div class="err">使えない: ${esc(d.error)}</div>`);
  if (d.file) {
    lines.push(`<div class="cost${overLimit(d) ? " over" : ""}">本物の Claude の運転: 見込み ${usd(d.estimate_usd)}・上限 ${usd(d.cost_limit_usd)}${overLimit(d) ? " (見込みが上限を超えるので、上限で止まる)" : ""}</div>`);
    lines.push(`<div class="rid">${esc(d.file)}</div>`);
  } else if (d.product) lines.push(`<div class="rid">${prose(d.want ?? "")} <span class="nw">(products/${esc(d.product)}.yaml)</span></div>`);
  return lines.join("");
}

// 再現する / 実行する for a demo file row; the Claude confirmation shows the cost limit before anything starts.
function plainButtons(info, d, busy) {
  const modes = d.modes ?? ["scripted", "claude"];
  const ask = `demo:${d.id}`;
  const file = esc(d.file);
  const scripted = modes.includes("scripted") ? `<button data-launch="scripted" data-req="${file}" ${busy ? "disabled" : ""}>再現する</button>` : "";
  let claude = "";
  if (!modes.includes("claude")) claude = "";
  else if (!info.claude_ready) claude = `<button class="claude" disabled>実行する</button><span class="why needs-key">${NEEDS_KEY_JA}</span>`;
  else if (launchConfirm === ask) {
    const est = typeof d.estimate_usd === "number" ? `見込みは ${fmtUsd(d.estimate_usd)}、` : "";
    const over = overLimit(d) ? "見込みが上限を超えるので、途中で止まります。" : "";
    claude =
      `<span class="confirm">Claude で実行します。${est}費用の上限は ${fmtUsd(d.cost_limit_usd)} で、そこで止まります。${over}実行しますか？</span>` +
      `<button class="go claude" data-launch="claude" data-req="${file}">実行する</button><button data-launch-cancel="1">やめる</button>`;
  } else claude = `<button class="claude" data-launch-ask="${esc(ask)}" ${busy ? "disabled" : ""}>実行する</button>`;
  return `<div class="lbtns">${scripted}${claude}</div>`;
}

function plainLaunch(info, message) {
  const busy = !!info.running;
  const pick = pickHtml(info, busy);
  if (info.available === false) return pick; // (no server to send a request to)
  const rid = info.running?.run_id ?? "";
  const stop = info.running?.stop_requested ? `<span class="why">止めるよう伝えました</span>` : `<button data-launch-stop="${esc(rid)}">止める</button>`;
  const running = busy ? `<p class="going">いま実行しています。<a href="?run=${encodeURIComponent(rid)}">見る</a> ${rid ? stop : ""}</p>` : "";
  const rows = info.demo_runs_error
    ? [`<p class="err">デモの一覧を読めませんでした</p>`]
    : (info.demo_runs ?? []).map((d) => {
        const line = DEMO_LINE_JA[d.id] ?? (d.title || d.id);
        let btns;
        if (d.error) btns = `<div class="lbtns"><span class="why">いまは使えません</span></div>`;
        else if (d.file) btns = plainButtons(info, d, busy);
        else btns = `<div class="lbtns"><a class="demo-a1" href="?new=1&demo=${encodeURIComponent(d.id)}">試す</a></div>`;
        return `<div class="lreq demo" data-demo="${esc(d.id)}"><div class="dline">${prose(line)}<details class="more"><summary>くわしく</summary>${demoDetail(d)}</details></div>${btns}</div>`;
      });
  // a new request first (above the recommendation and the demos)
  const fresh = `<div class="fresh-top"><a class="new-request" href="?new=1">＋ 新しく依頼する</a></div>`;
  return (
    fresh + pick + (message ? `<p class="err">${esc(message)}</p>` : "") + running +
    `<h3>デモ</h3><div class="demo-runs">${rows.join("")}</div>`
  );
}

async function launch(file, mode) {
  launchConfirm = null;
  for (const b of $("launch").querySelectorAll("button")) b.disabled = true;
  try {
    const res = await fetch("/api/launch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ requirements: file, mode }) });
    const out = await res.json();
    if (!res.ok) throw Object.assign(new Error(out.error ?? String(res.status)), { status: res.status, body: out });
    location.href = withDev(`?run=${encodeURIComponent(out.run_id)}`); // follow it live
  } catch (e) {
    // a run started here still going (409): in words, the list's own line links to it
    const going = e.status === 409 && "running_run_id" in (e.body ?? {});
    renderLaunch(going ? "いま走っている運転があります。終わってから始めてください。" : `走らせられなかった: ${e.message ?? e}`);
  }
}

// Ask the run started from this screen to stop (it ends as stopped, stop_code user_stop).
async function stopRun(runId) {
  const res = await fetch("/api/launch/stop", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ run_id: runId }) });
  const out = await res.json();
  if (!res.ok) throw new Error(out.error ?? String(res.status));
  return out;
}

$("mode").addEventListener("click", (ev) => {
  const b = ev.target.closest("button[data-stop]");
  if (!b) return;
  ui.stopAsked = true;
  drawn.rev = -1;
  stopRun(b.dataset.stop).catch(() => {
    ui.stopAsked = false;
    drawn.rev = -1;
  });
});

$("runlist").addEventListener("click", (ev) => {
  const b = ev.target.closest("button");
  if (!b) return;
  if (b.dataset.launchStop) {
    b.disabled = true;
    stopRun(b.dataset.launchStop)
      .then(() => renderLaunch())
      .catch((e) => renderLaunch(`止められなかった: ${e.message ?? e}`));
  } else if (b.dataset.launch) launch(b.dataset.req, b.dataset.launch);
  else if (b.dataset.launchAsk) {
    launchConfirm = b.dataset.launchAsk;
    renderLaunch();
  } else if (b.dataset.launchCancel) {
    launchConfirm = null;
    renderLaunch();
  }
});

// A new request: the intake tabs on the draft (whether Claude can be asked comes from the launcher).
async function startDraft() {
  ui.pinnedTab = "A1";
  ui.tabPlan = []; // no replay to follow
  try {
    const info = await (await fetch("/api/launch", { cache: "no-store" })).json();
    Object.assign(draft, { claudeReady: !!info.claude_ready, keyName: info.key_name });
    const demoId = params.get("demo");
    if (demoId) {
      // a request row of デモの実行: its product's want goes into A1 (sent only when 送る is pressed)
      const d = (info.demo_runs ?? []).find((r) => r.id === demoId);
      if (d && !d.file && d.product && d.want && !d.error) Object.assign(draft, { prefill: d.want, demoTitle: d.title || d.id });
      else draft.error = `デモの実行 ${demoId} の依頼の文が見つからなかった`;
    }
  } catch (e) {
    draft.error = `サーバーに聞けなかった: ${e.message ?? e}`;
  }
  drawn.rev = -1;
  renderDraft();
}

$("view-intake").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-start], [data-start-cancel]");
  if (!b || !draft) return;
  if (b.dataset.startCancel) {
    draft.confirm = false;
    renderDraft();
  } else draftStart(b.dataset.start);
});
$("view-intake").addEventListener("change", (ev) => {
  if (draft && ev.target.matches("[data-use-claude]")) draft.useClaude = ev.target.checked;
});

fitStage();
if (ui.present) applyPresent();
else $("viewswitch").querySelector('[data-view="bench"]').classList.add("on");
requestAnimationFrame(loop);
if (params.get("run")) openRun(params.get("run"));
else if (params.get("src")) {
  fetch(params.get("src"), { cache: "no-store" })
    .then((r) => r.text())
    .then((text) => loadEvents(parseJsonl(text), { live: false }))
    .catch((e) => showRunList(`読めなかった: ${e}`));
} else if (params.get("list")) showRunList();
else if (draft) startDraft();
// no run, no file, no draft: the run list too (a saved run is one 見る away, from おすすめ or its row)
else showRunList();
