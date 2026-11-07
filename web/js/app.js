// HIRAKU screen: wires events (live or replay) to the workbench panels and the 3D view.
//
// URL parameters
//   ?run=<run_id>   read runs/<run_id>/events.jsonl from the server (run=sample: the contract sample)
//   ?src=<url>      read an events.jsonl from any URL instead
//   ?t=12.5         stop at t = 12.5 s and draw that frame (for frame-by-frame video export; the intake
//                   screens get the time too, intake.setTime, so their lit cards' glow is the same at the same t)
//   ?video=1        video layout: keep the bottom 170 px empty for subtitles, hide the status row; ③'s tests
//                   are the one-line row of chips over the 3D (?board=1: the board of every test, as without video=1)
//   ?status=0       hide the bottom status row only
//   ?speed=2        replay speed; ?autoplay=0 opens paused at 0; ?live=0 replays a running run instead of following it
//                   (a finished run opened with none of ?t=, ?tab=, ?tabs=, ?autoplay=, ?domain=, ?play=, ?video=1
//                   opens paused at its end, every verdict in; ▶ plays it from its start: opensAtEnd)
//   ?on=modules     turn overlays on at load (modules, spaces: off by default)
//   ?off=joints     turn overlays off at load (joints: the joint marks, on by default; A7 follows it too)
//   ?tab=grid       open on ④ 解析結果 (the domains' grid) instead of the workbench (A1, A2, A5: the intake screens; A4: the tool shelf)
//   ?tab=grid&domain=strength
//                   ④ with that domain's analyses replaying in it (the 3D and the 予測 → 解析 → 比べる → 判定 cards,
//                   the play bar from its first prediction to its last comparison); a tile pressed does
//                   the same and sets it, 「格子に戻る」 drops it. With ?t= that frame (t as the whole replay's)
//   ?tabs=A1:0,A2:44.5,bench:80
//                   the tabs on the replay's time: from t seconds, that tab (so one URL films ① → ② → ③;
//                   ?t= and seek, back or forth, show the tab of that time). Before the first entry the tab
//                   is chosen as without it; an unknown tab name is left out; wins over ?tab=; a tab
//                   pressed by hand ends it. Names as ?tab= (bench, grid, A1, A2, A4, A5, A7)
//                   A7 so shown (or ?t= with &asm_from=) puts its parts in on the replay's time, one each
//                   0.4 s from when it came up (assembly.js PART_S); &asm_from=-6 starts with
//                   the last 6 parts, &asm_from=<step> at that step (the ones before in place)
//   ?list=1         the run list; ?dev=1 shows it (and keeps it on the links) as the developers see it
//   ?present=1      the present mode (js/present.js): the 3D on the whole screen and one card; the
//                   見せる / 作業台 switch at the top right turns it on and off
// window.hiraku.seek(t) returns a promise that resolves once the frame at t is drawn;
// window.hiraku.load(events, t) shows other events (already parsed) the same way.

import { TAB_JA, tabWord, verLabel, judgedWords, roleNames, MISMATCH_STATUS, RED_STATUS, DEMO_LINE_JA, DOMAINS, DOMAIN_JA, DRIVEN_JA, DRIVER_JA, PICK_DEMO_ID, runSummaryJa, stopWhyJa, isOlderRecord, RUN_STATE_JA, runState, RULES, STOP_CODE_JA, DESIGNER_ROLES, designerRole, agentLongName, agentName, BASE_JA, caseShortNames, esc, keepAsides, fmt, fmtMargin, fmtPct, fmtQ, fmtSigned, fmtTime, fmtUsd, fmtVU, opLabel, formulaInputName, plainToolName, quantityName, unitLabel } from "./labels.js";
import { BAND_COLORS, BANDS, OVER_COLOR } from "./colormap.js";
import { EventFeed, Player, parseJsonl } from "./player.js";
import { pathCheckedWhy } from "./why_line.js";
import { applyEvent, chainFor, domainVerdicts, initialState, isSafeSide, judgementState, roundsUsed, valueFromJa } from "./state.js";
import { SeriesChart } from "./chart.js";
import { FIELD_JA, FIELD_ORDER, atFrame, contourOf, convergenceLine, fieldLabel, fieldsOf, frameIndex, onSharedScale, preferredField, sameField, variantName, variantOf } from "./fields.js";
import { motionOf, motionSpan, nearestAt, wheelName, xSeriesOf } from "./series.js";
import { NodeMotionView, nodeMotionAt, nodeMotionOf, nodeMotionPlaySpan, nodeMotionTau } from "./node_motion.js";
import { mountIntake } from "./intake/index.js";
import { REPLY_STEP_S } from "./intake/a1.js";
import { lookFromParams, looksReady, qualityFromParams } from "./look.js";
import { DUMMY_NOTE_JA } from "./hybrid3.js"; // the workbench's くわしく under a dummy
import { mountShelf } from "./shelf.js";
import { parseZoom, zoomTransform } from "./zoom.js";
import { liftIsLimit } from "./scene.js";
import { TileBoard, TILE_ORDER, headline, onlyTestsLine } from "./tiles.js";
import { TileViews } from "./tile3d.js";
import { VIEWS, Workbench3D } from "./workbench3d.js";
import { AssemblyPanel, assemblyRecord } from "./assembly.js";
import { renderJointLegend } from "./joints.js"; // joints:
import { MODE_JA, MODE_ORDER, drawableMetrics, METRIC_JA, modeCardHtml, modeLegendParts, qualityContourOf, qualityOf, setupContourOf, setupOf, thicknessContourOf } from "./quality.js";
import { AgentPets, PET_PAINTS, glRendererName, isSoftwareRenderer, petRowHtml, stopWhyHtml } from "./agent_pets.js";
import { IntakePets } from "./intake_pets.js"; // the CAE agents' pets on A1, the asker's on A2
import { hearingRounds, plainHearingEvents, roundAt, roundLine, roundTitle } from "./hearing_rounds.js"; // ②: when each hearing was
import { reportNextHtml } from "./report_next.js";
import { analysisEdge, assumptionText, edgeFlips, edgeLineHtml, edgeRaw, edgeText, nearLineHtml } from "./edge_note.js";
import { causeNoteHtml, hookWords, nowSentence, openCause, presentCardHtml, presentScene, resolvedHooks, verdictLine, verdictSentence } from "./present.js";
import { judgedContour, judgedResult, referenceResults } from "./judged.js";
import { JUDGED_JA } from "./labels.js";
import { heldAll, heldBandHtml, heldCases } from "./held.js"; // held: load cases shown, not judged
import { ModuleLayer, hasModules, loadModuleNames, renderModuleLegend, renderRoundCard } from "./modules.js"; // modules:
import { designerColor, roundCardHtml } from "./proposals.js"; // modules:
import { TabContent, mountTabs } from "./tabs.js";
import { GripView } from "./grip_panel.js"; // grip: the tyres of a driving record (toggle タイヤ, ?on=grip)
import { hasGrip } from "./grip_view.js"; // grip:
import { kcFor } from "./kc_view.js"; // grip:
import { EnergyFlow } from "./energy_flow.js"; // heat: the energy's flow over the 3D
import { VIEWER_WORDS, extraUrl, loadViewerInfo, viewerButtonHtml, viewerExtras, viewerInfo, viewerProvisional, viewerRunId, viewerTarget, viewerUrl } from "./viewer_link.js"; // 「ビューアで見る」
import { clampToSpan, domainLcAt, domainSpans } from "./domain_replay.js";
import { loopFromParams } from "./anim_loop.js"; // ?play=loop&analysis=: one analysis' motion over and over
import { STATIC, getUrl } from "./source.js"; // a static export: its files in place of the server's routes, nothing POSTed
import { domainIconSvg } from "./domain_icons.js";
import { footerActivity, hookLineText, mountHookLine, withoutRuleIds } from "./hookline.js";
import { mountBenchNote, noteKind } from "./benchnote.js"; // ③'s sentence card under the 3D
import { filmWords, isFilmWords, markRecordWords, plainLineHtml, plainWords, shownText } from "./plain.js"; // plain words: the card's, the footer's and the log's
import { roundSeq } from "./proposals.js";
import { boardHtml } from "./test_board.js"; // ③'s tests at a glance
import { askAll, heavyPending, heavyReady, heavyWaiting, onHeavy, prefetch, quietly, tracked } from "./heavy.js"; // a static export's heavy parts, read when drawn

const $ = (id) => document.getElementById(id);

// ---- while the record is read: the page is one line and a pet (index.html #boot) --------------
// No frame (tabs, the top band, an empty 3D) until the record is read and drawn: the middle of the
// page says 「記録を読み込んでいます NN %」 beside the lead's pet (its circle until pet3d.js is in).
// A read that fails says why in one line (no red); one that takes BOOT_TIMEOUT_MS fails as 時間切れ.
// index.html's own script does the same when this module never runs (window.hirakuBootTimer).
const BOOT_TIMEOUT_MS = 60000;
clearTimeout(window.hirakuBootTimer);
const boot = { on: document.documentElement.classList.contains("booting"), pct: -1, timer: null, failed: false, petTimer: null };
function bootLine(text) {
  const el = $("boot-line");
  if (el && el.textContent !== text) el.textContent = text;
}
function bootProgress(f) {
  if (!boot.on || boot.failed) return;
  const pct = Math.max(0, Math.min(99, Math.floor(f * 100)));
  if (pct === boot.pct) return;
  boot.pct = pct;
  bootLine(`記録を読み込んでいます ${pct} %`);
  $("boot")?.setAttribute("data-pct", String(pct));
}
function bootDone() {
  if (!boot.on || boot.failed) return;
  boot.on = false;
  clearTimeout(boot.timer);
  clearInterval(boot.petTimer);
  document.documentElement.classList.remove("booting");
  $("boot")?.remove();
}
// why: the one line; links: [[text, href]] under it (開き直す, 一覧へ)
function bootFailed(why, links = []) {
  boot.failed = true;
  clearTimeout(boot.timer);
  document.documentElement.classList.add("booting");
  const el = $("boot");
  if (!el) return;
  el.classList.add("failed");
  el.dataset.failed = "1";
  bootLine(why);
  el.querySelector(".boot-links")?.remove();
  if (links.length) {
    const p = document.createElement("p");
    p.className = "boot-links";
    p.innerHTML = links.map(([t, href]) => `<a href="${esc(href)}">${esc(t)}</a>`).join(" ");
    el.append(p);
  }
}
// A static export opened with no parameters starts at ① of the run its export picked (data/static.json
// first: a saved run whose record has the request, the picked domains and the hearing), stopped at the
// request's moment (requestMoment: the domains picked, no design yet), so the top row, the footer and
// ① tell one moment; 「この記録を再生する」 plays it (STATIC_REPLAY). Without such a run: the run list,
// as before.
async function staticFirst() {
  try {
    return (await (await fetch(getUrl("/data/static.json"))).json()).first ?? null;
  } catch {
    return null;
  }
}
const FIRST = STATIC && !location.search ? await staticFirst() : null;
// (the first screen: the run as it ended, on ④ — what was judged and what the versions fixed —, every
// tab at that moment; 「▶ 再生して見る」 plays it from its first design. ①'s request's moment stays for
// ?tab=A1)
const params = new URLSearchParams(FIRST ? `run=${encodeURIComponent(FIRST.run)}` : location.search);
// ?dev=1: the run list as the developers see it (files, run ids, cut numbers, cost lines); kept on the links
// (and the tabs' titles add the screens' names inside HIRAKU)
const DEV = params.get("dev") === "1";
const tabs = mountTabs($("tabs"), { dev: DEV });
// A static export: the run list is 「☰ 一覧へ」 at the top left (#back-link), as on the server
if (STATIC) {
  document.documentElement.classList.add("static-export"); // (app.css: no box to write or send in on ① / ②)
}
const hookLine = mountHookLine($("stage"), { dev: DEV, showS: 5, avoid: () => [$("tests")] }); // the hooks' stop in one line (kept clear of ③'s board); the footer's list folded
$("back-link").href = DEV ? "?list=1&dev=1" : "?list=1";
document.body.classList.toggle("dev", DEV); // (③'s strip shows the record's ids and English words)
filmWords(params.get("video") === "1"); // (?video=1: the film's words as filmed, plain.js)
const benchNote = mountBenchNote($("benchnote"), $("viewport"));
const tabContent = new TabContent();
const withDev = (href) => (DEV ? `${href}${href.includes("?") ? "&" : "?"}dev=1` : href);
const STAGE_W = 1920;
const STAGE_H = 1080;
const SHOW_S = 5; // seconds of playback a bubble or hook warning stays
const ANIM_MAX_S = 12; // a test's motion plays in at most this many seconds of replay

const PET_COLORS = { lead: "#e9b872", designer: "#2fa7d6", packager: "#8e6bbf", system: "#c9c3b6", user: "#fbf9f4" };
// (a design role designer.structure its own colour, an older record's designer.<module> its module's)
const petColor = (id) => PET_COLORS[id] ?? (id?.startsWith("cae.") ? "#4cbf7a" : designerColor(id) ?? "#c9c3b6");

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
  pinnedLc: null, // ③'s own pick (its test buttons); ④'s replay keeps its own (gridLc)
  // ④ 解析結果: the domain whose analyses replay inside the tab (a tile pressed, or ?domain=), its
  // own pick among the domain's tests, and where the replay was when it opened (closing goes back)
  // (the video only: elsewhere ④ is the run as it ended, and a tile opens the viewer, GRID_FIXED)
  gridDomain: params.get("video") === "1" && DOMAINS.includes(params.get("domain")) ? params.get("domain") : null,
  gridLc: null,
  gridBack: null,
  // ④ as the run ended (GRID_FIXED): the version picked in the head (null: the last), the analysis
  // open in the viewer inside the tab ({ ana, field, url }), the line of an analysis with no viewer
  gridVersion: null,
  gridViewer: null,
  gridNote: null,
  pinnedTab: TABS.includes(params.get("tab")) ? params.get("tab") : FIRST ? "grid" : null,
  tabPlan: parseTabPlan(params.get("tabs")), // ?tabs= (wins over ?tab=); a tab pressed by hand ends it
  toggles: { contour: true, arrows: true, cg: true, parts: true, names: false, limit: true, graph: true, joints: true, modules: false, spaces: false, heat: true, grip: false }, // joints: on at first (?off=joints); modules: modules, spaces (modules.js); heat: the energy's flow on a thermal analysis; grip: タイヤ (grip_panel.js)
  frozen: params.has("t"),
  moment: null, // ① opened at the request's moment (requestMoment): its time, while the replay stays there
  // The test's motion follows the replay (auto); ▶ or dragging its bar plays it on its own (local)
  // until the replay playhead moves again.
  anim: { key: null, local: false, playing: false, tau: 0 },
  graphGroup: null,
  fieldKind: null, // contour field picked in the legend (null: the first one)
  fieldVariant: null, // among the fields of that kind: `${tool}|${variantOf}` (the analysis' tool; the beams, or a mesh and its surface)
  zoomMax: false, // the camera at a shell contour's largest element (legend: 最大の所に寄る)
  compare: false, // the previous design version beside the current one (legend: 1 つ前の設計と比べる)
  meshMode: null, // the mesh's own view in place of a field (quality.js MODE_ORDER), or null
  qualityMetric: null, // the mesh check's metric the elements are coloured by (null: the first)
  stoppable: null, // the run id when this screen started the run being followed
  stopAsked: false,
  followed: false, // this page followed the run while it was written (live)
  childGone: false, // the run this screen started is no longer running (GET /api/launch), run.finished or not
  present: params.get("present") === "1", // the present mode
  detail: false, // in the present mode: the log, the chain and the hooks opened (詳しく)
  // the agents' bundles opened (renderAgents): closed by default, all open with ?agents=open
  openGroups: new Set(params.get("agents") === "open" ? ["designer", "cae"] : []),
};

// ?off=contour,arrows,cg,graph turns those overlays off at load (e.g. the bare vehicle for a title shot).
for (const key of (params.get("off") ?? "").split(",")) if (key in ui.toggles) ui.toggles[key] = false;
// ?off=cause: the card of a mismatch's cause is not shown (it has no button of its own)
const CAUSE_OFF = (params.get("off") ?? "").split(",").includes("cause");
// modules: ?on=modules,spaces turns those on at load (?on=joints is still read, joints being on at first) (modules and spaces take the contour's place)
for (const key of (params.get("on") ?? "").split(",")) if (key in ui.toggles) ui.toggles[key] = true;
if (ui.toggles.modules || ui.toggles.spaces) ui.toggles.contour = false;
// heat: ?on=heat shows the energy's flow with any analysis picked (the newest thermal one of the design shown)
const HEAT_FORCED = (params.get("on") ?? "").split(",").includes("heat");
// ?play=loop&analysis=<id>: the playhead held where it arrived, its motion looped at 1x (anim_loop.js)
const animLoop = loopFromParams(params);
if (animLoop && !ui.pinnedTab && !ui.tabPlan.length) ui.pinnedTab = "bench";
if (animLoop) $("ab-play").style.display = "none"; // (the loop's clock moves the motion, not ▶)

if (params.get("video") === "1") document.body.classList.add("video");
if (params.get("status") === "0") document.body.classList.add("no-status");

// ---- stage scaling -------------------------------------------------------
const stage = $("stage");
let stageScale = 1;
let stageBase = ""; // the stage's fit to the window (?zoom= adds its own part after it, js/zoom.js)
const ZOOM = parseZoom(params.get("zoom"));
function fitStage() {
  stageScale = Math.min(window.innerWidth / STAGE_W, window.innerHeight / STAGE_H);
  const left = (window.innerWidth - STAGE_W * stageScale) / 2;
  const top = (window.innerHeight - STAGE_H * stageScale) / 2;
  stage.style.transform = stageBase = `translate(${left}px, ${top}px) scale(${stageScale})`;
  agentPets?.setPixel((window.devicePixelRatio || 1) * stageScale);
  resize3d();
}

// ---- 3D -------------------------------------------------------------------
const viewport = $("viewport");
const look = lookFromParams(params); // the photograph-like look by default; ?look=plain: the diagram
document.body.classList.add(`look-${look}`);
// joints: the real look opens with the joint marks off (they make the parts see-through, joints.js
// partLook), so the vehicle is seen as it is; ?on=joints or the 接合 button shows them
if (look === "real" && !(params.get("on") ?? "").split(",").includes("joints")) ui.toggles.joints = false;
// a software renderer (the WebGL driver's name, agent_pets.js): the real look's cheapest quality
// unless ?quality= says otherwise (look.js qualityFromParams)
function softwareGL() {
  const gl = document.createElement("canvas").getContext("webgl2");
  const name = glRendererName(gl);
  gl?.getExtension("WEBGL_lose_context")?.loseContext();
  return isSoftwareRenderer(name);
}
const quality = qualityFromParams(params, softwareGL()); // ?quality=high / medium / low: the real look's cost (look.js QUALITY)
// sky: the real look shows the sky with its horizon behind the vehicle (look.js Look.sky)
const bench = new Workbench3D(viewport, look, { quality, sky: true });
// ?words=1: every arrow's words as before (not only where chosen); the present mode does the same,
// and so does ?video=1 (the filmed frames keep their arrows' words, present=0 included)
const WORDS_ALL = params.get("words") === "1" || params.get("video") === "1";
bench.autoWords = WORDS_ALL || params.get("present") === "1";
const modLayer = new ModuleLayer(bench); // modules:
const grip = new GripView(bench, viewport); // grip:
const heat = new EnergyFlow(bench, viewport, { dev: DEV }); // heat: ids in its legend only with ?dev=1
const nodeView = new NodeMotionView(bench, viewport); // node_motion: the nodes' motion (js/node_motion.js)
function resize3d() {
  const half = viewport.classList.contains("compare"); // before / after side by side: each view takes half
  const w = half ? Math.floor(viewport.clientWidth / 2) : viewport.clientWidth;
  const h = viewport.clientHeight;
  if (w > 0 && h > 0) {
    const aspect = bench.camera.aspect;
    bench.resize(w, h, (window.devicePixelRatio || 1) * stageScale);
    grip.resize(w, h); // grip:
    if (half && cmpView) cmpView.bench.resize(w, h, (window.devicePixelRatio || 1) * stageScale);
    if (bench.camera.aspect !== aspect) refitForSize();
  }
}
// ③'s framing left of the legend (Workbench3D.fit with fitWidth: by the outline's points in the
// view's free width) depends on the view's shape: framed again when the 3D's size changes (the
// strip under the 3D, benchnote.js, comes after the first framing), so the same t gives the same
// picture however the page got there. Not a camera turned by hand, a focus, or the compare view.
function refitForSize() {
  if (!bench.fitWidth || !(bench.reserveRight > 0) || !bench.design || bench.focused || bench.handMoved) return;
  if (viewport.classList.contains("compare")) return;
  if (grip.reserved) grip.reserve(grip.reserved, true); // (its own view from higher up)
  else bench.fit(VIEWS[bench.view ?? "default"]);
}
new ResizeObserver(resize3d).observe(viewport);
window.addEventListener("resize", fitStage);

// ---- the agents' pets (js/agent_pets.js) ------------------------------------
// promo/pets/pet3d.js and layout.json's n00 camera; ?pets=0, or either missing: circles and names.
let agentPets = null;
let intakePets = null; // js/intake_pets.js, made on the first intake frame once the pets are loaded
const petsReady = (params.get("pets") === "0" ? Promise.resolve(null) : AgentPets.load().catch(() => null)).then((p) => {
  agentPets = p;
  setTimeout(bootPet, 0); // (a task of its own: making the pet is long enough already)
  document.documentElement.dataset.agentPets = p ? "on" : "none";
  if (p) p.setPixel((window.devicePixelRatio || 1) * stageScale);
  drawn.rev = -1;
});

// The lead's pet beside the boot's line, waiting (BOOT_PET_FPS: the page is busy reading; a software
// renderer (agentPets.still): one picture).
const BOOT_PET_FPS = 5;
function bootPet() {
  const slot = $("boot-pet");
  if (!agentPets || !boot.on || !slot || !agentPets.has("lead")) return;
  const el = agentPets.box("lead", "boot");
  const p = agentPets.pets.get("lead");
  const b = p.boxes.boot;
  const t0 = performance.now();
  // (its shaders compiled off the page's thread where the browser can: the first picture would
  // otherwise hold the page while they are)
  const r = agentPets.stage.renderer;
  p.pet.group.visible = true;
  const compiled = r.compileAsync ? r.compileAsync(agentPets.stage.scene, agentPets.stage.cameraFor(1).cam).catch(() => {}) : Promise.resolve();
  p.pet.group.visible = false;
  compiled.then(() => {
    if (!boot.on) return;
    slot.append(el);
    slot.classList.add("has-pet");
    start();
  });
  function start() {
    const paint = () => {
      if (!boot.on || !b.el.isConnected) return clearInterval(boot.petTimer);
      const px = Math.max(1, Math.round(b.el.clientWidth * (window.devicePixelRatio || 1)));
      if (b.canvas.width !== px) {
        b.canvas.width = b.canvas.height = px;
        agentPets.stage.grow(px, px);
      }
      agentPets.stage.paint(p.pet, b.ctx, (performance.now() - t0) / 1000, "waiting", null);
      b.el.dataset.petDraws = String(Number(b.el.dataset.petDraws) + 1);
    };
    paint();
    if (!agentPets.still) boot.petTimer = setInterval(paint, 1000 / BOOT_PET_FPS);
  }
}

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
  // (a static export's light record carries it, worked out as here: its series are read later)
  if (!motionSpans.has(id) && ev._lite && "pace" in ev._lite) motionSpans.set(id, ev._lite.pace);
  if (!motionSpans.has(id)) motionSpans.set(id, (ev.data?.series ? motionSpan(ev.data) : null) ?? nodeMotionPlaySpan(ev.data));
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

// The event clock (the default; js/player.js): each event's seconds on screen, the recorded gaps
// dropped. key: what the run is about (a new design version, a mismatch, an NG, a cause, a hook,
// the start and the end, a turn to another screen), shortened last when a long record is fitted
// into player.fitS; the routine ones (an analysis that matched, an OK, a prediction) first. An
// analysis with a motion gets the motion's length (1.5 to 3 s) and its motion plays in that time
// (animTau). The last event keeps its own time too (player.duration). Bookkeeping records
// (judgement.updated, rule_check.recorded, agent.started/stopped, cost.updated, project.updated)
// take no time.
const EVENT_S = {
  "run.started": [1.5, true],
  "hearing.asked": [1.0, true],
  "hearing.answered": [1.0, true],
  "domains.picked": [1.0, true],
  "load_cases.created": [0.6, false],
  "knowledge.imported": [0.2, false],
  "mesh.created": [0.4, false],
  "tool.checked": [0.3, false],
  "design.created": [2.0, true],
  "design.change_proposed": [0.8, false],
  "prediction.recorded": [0.1, false],
  "analysis.started": [0.15, false],
  "analysis.finished": [0.9, false],
  "comparison.recorded": [0.35, false],
  "verdict.recorded": [0.35, false],
  "cause.recorded": [1.6, true],
  "hook.denied": [1.5, true],
  "hook.blocked_stop": [1.2, true],
  "report.written": [1.5, true],
  "run.finished": [1.0, true],
  "screen.focus": [1.5, true], // the run turns to another screen (④ at the end): it is seen
};
const MOTION_EVENT_S = [1.5, 3]; // an analysis with a motion: its motion's seconds, within these
function eventDwell(ev) {
  const [s, key] = EVENT_S[ev.kind] ?? [0, false];
  if (ev.kind === "comparison.recorded" && ev.data?.status === "mismatch") return { s: 1.0, key: true };
  if (ev.kind === "verdict.recorded" && ev.data?.pass === false) return { s: 0.9, key: true };
  if (ev.kind === "agent.said" || ev.kind === "user.said") return { s: Math.min(1.2, 0.3 + (ev.text?.length ?? 0) * 0.01), key: false };
  if (ev.kind === "analysis.finished") {
    const span = motionSpanOf(ev);
    if (span !== null) return { s: Math.min(MOTION_EVENT_S[1], Math.max(MOTION_EVENT_S[0], span)), key: false };
  }
  return { s, key };
}
player.dweller = eventDwell;
// ?pace=real (or ?pace=0): the recorded times; ?pace=paced: the gaps stretched and cut (?gapmax=3
// sets the longest gap), the default before the event clock; else the event clock
// ?video=1 (the filming) without ?pace=: the paced clock, so the ?t= values taken on it give the
// same frames; ?pace=event asks for the event clock there
const PACE_PARAM = params.get("pace") ?? (params.get("video") === "1" ? "paced" : null);
player.clock = PACE_PARAM === "0" || PACE_PARAM === "real" ? "real" : PACE_PARAM === "paced" ? "paced" : "event";
if (params.has("fit")) player.fitS = Math.max(5, Number(params.get("fit")) || player.fitS); // ?fit=110: the event clock's length
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

// A7 on the replay's clock (assembly.js timedU): with ?tabs= showing A7, from the time that run of
// A7 entries began; else with ?t= and &asm_from=, from its record's arrival (from: null); else none
// (its own bar; ?t= alone opens it on the vehicle built, as before).
function asmClock() {
  const at = player.playhead;
  let from = null;
  for (const p of ui.tabPlan) {
    if (p.t > at + 1e-9) break;
    from = p.tab === "A7" ? from ?? p.t : null;
  }
  if (from !== null) return { at, from };
  return params.has("t") && params.has("asm_from") ? { at, from: null } : null;
}

function currentTab(s) {
  const planned = ui.tabPlan.length ? plannedTab(player.playhead) : null;
  if (planned) return planned;
  if (ui.pinnedTab) return ui.pinnedTab;
  if (INTAKE_TABS.includes(s.focus.screen) || s.focus.screen === "A4" || s.focus.screen === "A7") return s.focus.screen;
  return s.focus.screen === "A6" ? "grid" : "bench";
}

// ---- ④ 解析結果: one domain's analyses replayed in the tab (js/domain_replay.js) ----------------
let spanMemo = { n: -1, last: null, spans: {} };
function spansNow() {
  if (spanMemo.n !== player.events.length || spanMemo.last !== player.events.at(-1)) {
    spanMemo = { n: player.events.length, last: player.events.at(-1), spans: domainSpans(player.events) };
  }
  return spanMemo.spans;
}
// The open domain's span when ④ shows it (null: the grid as it is).
function openSpan(tab) {
  return tab === "grid" && ui.gridDomain ? spansNow()[ui.gridDomain] ?? null : null;
}
// What the main area shows: the tab's screen, but ④ with a domain open shows the 3D and the cards.
const viewOf = (tab, span) => (span ? "bench" : tab);

function setDomainParam(d) {
  const url = new URL(location.href);
  if (d) url.searchParams.set("domain", d);
  else url.searchParams.delete("domain");
  history.replaceState(null, "", url);
}

// A tile pressed: its domain replays from its first prediction (the replay's place is kept for 戻る).
function openDomain(d) {
  const span = spansNow()[d];
  if (!span) return;
  ui.gridBack = { t: player.playhead, playing: player.playing };
  ui.gridDomain = d;
  ui.gridLc = null;
  setDomainParam(d);
  ui.frozen = false;
  if (player.live) player.liveAttached = false;
  player.seek(span.t0);
  player.playing = true;
  drawn.rev = -2;
}

function closeDomain() {
  const back = ui.gridBack;
  ui.gridDomain = null;
  ui.gridLc = null;
  ui.gridBack = null;
  setDomainParam(null);
  if (back) {
    player.seek(back.t);
    player.playing = back.playing;
  }
  drawn.rev = -2;
}

// The whole record's state (every event read so far applied, not only those up to the playhead):
// the top row draws it while a domain replays in ④, so the run's version and status stay as they
// are and do not seem to go back with that domain's replay. New events are applied on top; a
// record that changed under it (another run, events put in between) is built again.
let wholeMemo = { n: 0, last: null, state: initialState() };
function wholeState() {
  const evs = player.events;
  const kept = wholeMemo.n <= evs.length && (wholeMemo.n === 0 || evs[wholeMemo.n - 1] === wholeMemo.last);
  if (!kept) wholeMemo = { n: 0, last: null, state: initialState(), gen: (wholeMemo.gen ?? 0) + 1 };
  for (let i = wholeMemo.n; i < evs.length; i++) applyEvent(wholeMemo.state, evs[i]);
  wholeMemo.n = evs.length;
  wholeMemo.last = evs.at(-1) ?? null;
  return wholeMemo.state;
}

// ---- ④ 解析結果 as the run ended (not the video) ------------------------------------------------
// ④ is looked at once the run is done: it draws the whole record (wholeState), whatever the
// replay's time, at the version picked in its head (the last by default). A tile, or a test's row in
// it, opens that analysis in the viewer (viewer_link.js) inside the tab; an analysis the viewer has
// no field for says its value and why in one line on its tile. The video (?video=1) keeps ④ on the
// replay's clock and its domain replay, as filmed before.
const GRID_FIXED = params.get("video") !== "1";
let gridMemo = { w: null, n: -1, v: null, s: null };
function gridState() {
  const w = wholeState();
  const v = ui.gridVersion != null && w.versions.includes(ui.gridVersion) ? ui.gridVersion : w.currentVersion;
  if (v === w.currentVersion) return w;
  if (gridMemo.w !== w || gridMemo.n !== wholeMemo.n || gridMemo.v !== v) gridMemo = { w, n: wholeMemo.n, v, s: { ...w, currentVersion: v } };
  return gridMemo.s;
}
let gridDrawnKey = null;
const gridKey = (gs) => `${wholeMemo.gen ?? 0}|${wholeMemo.n}|${gs.currentVersion}|${viewerInfo().loaded}`;

const anaOfVerdict = (s, x) => s.analyses[x.analysis_id ?? s.comparisons[x.comparison_id]?.analysis_id] ?? null;
const lastInRecord = (s, list) => {
  const at = new Map(s.order.analyses.map((id, i) => [id, i]));
  return list.filter(Boolean).reduce((a, b) => (a && (at.get(a.analysis_id) ?? -1) > (at.get(b.analysis_id) ?? -1) ? a : b), null);
};
// The analysis a tile opens: the last one its verdicts were judged from, else the domain's last
// analysis of the version (ok first), else of any version.
function tileAnalysis(s, d) {
  const v = s.currentVersion;
  // the analysis of the tile's large number (tiles.js headline: 強度 R1s → its frame_shell, the plates' mesh),
  // so the 3D that opens is the one the number comes from
  const big = headline(domainVerdicts(s, d, v));
  const bigAna = big ? s.analyses[big.analysis_id ?? s.comparisons[big.comparison_id]?.analysis_id] : null;
  if (bigAna) return bigAna;
  const judged = lastInRecord(s, domainVerdicts(s, d, v).map((x) => anaOfVerdict(s, x)));
  if (judged) return judged;
  const lcs = new Set((s.project?.load_cases ?? []).filter((c) => c.domain === d).map((c) => c.load_case_id));
  const all = s.order.analyses.map((id) => s.analyses[id]).filter((a) => a && lcs.has(a.load_case_id));
  const mine = all.filter((a) => a.design_version === v);
  return mine.filter((a) => a.status === "ok").at(-1) ?? mine.at(-1) ?? all.at(-1) ?? null;
}

// The one line of an analysis the viewer cannot show: its verdict as recorded (the quantity, the
// value, the limit, OK / NG), then why there is no viewer.
function noViewerLine(s, ana, reason) {
  const vs = Object.values(s.verdicts).filter((x) => anaOfVerdict(s, x)?.analysis_id === ana?.analysis_id);
  const x = headline(vs);
  const tid = (s.project?.load_cases ?? []).find((c) => c.load_case_id === ana?.load_case_id)?.test_id ?? ana?.load_case_id ?? "";
  const value = x
    ? `${x.test_id ?? x.load_case_id} ${quantityName(s.comparisons[x.comparison_id]?.quantity ?? x.quantity)} ${fmtQ(x.value)} ${opLabel(x.op)} ${fmtQ(x.limit)} ${x.pass === false ? "NG" : "OK"}`
    : `${tid} 判定は記録に無い`;
  return { value, why: `ビューアは無い: ${reason}` };
}

function openGridAnalysis(s, d, ana) {
  const run = viewerRunId(s);
  const t = viewerTarget(ana, run);
  if (ana && t.field && run) {
    ui.gridNote = null;
    ui.gridViewer = { ana: ana.analysis_id, domain: d, field: t.field, url: viewerUrl(run, ana.analysis_id, t.field) };
  } else {
    ui.gridViewer = null;
    ui.gridNote = { domain: d, ana: ana?.analysis_id ?? null, ...noViewerLine(s, ana, t.reason ?? VIEWER_WORDS.noField) };
  }
  showGridViewer(s);
  showGridNote();
}

// A record kept for the run's design (viewer_link.js VIEWER_EXTRAS), pressed on its tile
function openGridExtra(s, d, e) {
  ui.gridNote = null;
  ui.gridViewer = { extra: e.id, title: `${DOMAIN_JA[d] ?? d}・${e.label}`, note: e.note, domain: d, field: e.field, url: extraUrl(e) };
  showGridViewer(s);
  showGridNote();
}

function closeGridViewer() {
  ui.gridViewer = null;
  showGridViewer(null);
}

// The viewer over ④'s list (an iframe of viewer/index.html), with 「← 解析結果の一覧へ」 (Esc too).
function showGridViewer(s) {
  let el = $("grid-viewer");
  if (!el) {
    el = document.createElement("div");
    el.id = "grid-viewer";
    el.className = "grid-viewer";
    el.dataset.role = "grid-viewer";
    el.innerHTML =
      `<div class="gv-head"><button type="button" class="gv-back" data-act="grid-back">← 解析結果の一覧へ</button>` +
      `<b class="gv-title"></b><span class="gv-sample"></span><span class="gv-esc">Esc でも戻る</span></div><iframe class="gv-frame" title="ビューア"></iframe>`;
    $("view-grid").append(el);
    // Esc pressed in the viewer (its own page, the same origin) goes back too
    el.querySelector("iframe").addEventListener("load", (ev) => {
      try {
        ev.currentTarget.contentWindow.addEventListener("keydown", (k) => k.key === "Escape" && closeGridViewer());
      } catch {
        // (another origin: Esc on the page outside it still goes back)
      }
    });
  }
  const g = ui.gridViewer;
  el.hidden = !g;
  const frame = el.querySelector("iframe");
  if (!g) {
    if (frame.getAttribute("src")) frame.removeAttribute("src");
    delete el.dataset.ana;
    return;
  }
  if (g.extra) {
    el.querySelector(".gv-title").textContent = g.title;
    el.querySelector(".gv-sample").textContent = g.note;
    delete el.dataset.ana;
    el.dataset.extra = g.extra;
  } else {
    const ana = s?.analyses?.[g.ana];
    const lc = (s?.project?.load_cases ?? []).find((c) => c.load_case_id === ana?.load_case_id);
    const tid = lc?.test_id ?? ana?.load_case_id ?? g.ana;
    // the test's id and the tool's id under ?dev=1 (and in the film); else the test's title and the tool's name
    const ids = DEV || VIDEO_LAYOUT;
    const test = ids ? tid : plainWords(String(lc?.title ?? "").split(/\s*[（(:：]/)[0]) || "";
    const tool = ids ? ana?.tool ?? "" : ana?.tool ? toolShort(s, ana.tool) : "";
    el.querySelector(".gv-title").textContent = [DOMAIN_JA[g.domain] ?? g.domain, test, tool].filter(Boolean).join("・") + ` (${verLabel(ana?.design_version ?? "")})`;
    el.querySelector(".gv-sample").textContent = viewerProvisional(viewerRunId(s)) ? VIEWER_WORDS.sample : "";
    el.dataset.ana = g.ana;
    delete el.dataset.extra;
  }
  el.dataset.field = g.field;
  if (frame.getAttribute("src") !== g.url) frame.setAttribute("src", g.url);
}

// The rows of the records kept for the run's design (viewer_link.js viewerExtras), on their tile
function showGridExtras(s) {
  const run = viewerRunId(s);
  for (const tile of $("tiles").querySelectorAll(".tile[data-domain]")) {
    tile.querySelector('[data-role="viewer-extras"]')?.remove();
    const list = viewerExtras(run, tile.dataset.domain);
    tile.classList.toggle("has-extras", list.length > 0);
    if (!list.length) continue;
    // (a button that reads as one: ▶ (app.css) and the name, as the picture's 「▶ 押すとビューアで見られます」)
    const rows = list.map((e) => `<li data-extra="${esc(e.id)}" role="button" title="押すと、ビューアで見ます${e.note ? ` (${esc(e.note)})` : ""}"><b>${esc(e.label)}</b></li>`).join("");
    // in the tile's head, after the domain's name: off the 3D picture, and no height taken from it
    const name = tile.querySelector(".th h3");
    const html = `<ul class="vextras" data-role="viewer-extras">${rows}</ul>`;
    if (name) name.insertAdjacentHTML("afterend", html);
    else tile.insertAdjacentHTML("beforeend", html);
  }
}

// The line of an analysis with no viewer, on its tile (put back each time the tiles are drawn).
function showGridNote() {
  for (const el of $("tiles").querySelectorAll('[data-role="viewer-none"]')) el.remove();
  const n = ui.gridNote;
  if (!n) return;
  const tile = $("tiles").querySelector(`.tile[data-domain="${n.domain}"]`) ?? $("tiles").querySelector(`.offchip[data-domain="${n.domain}"]`)?.parentElement;
  if (!tile) return;
  tile.insertAdjacentHTML("beforeend", `<div class="vnone" data-role="viewer-none" data-ana="${esc(n.ana ?? "")}"><b>${esc(n.value)}</b><span>${esc(n.why)}</span></div>`);
}

// The head over the 3D while a domain replays in ④: what it is, its time, and 格子に戻る.
function renderDomainHead(span) {
  let el = $("domain-head");
  if (!el) {
    el = document.createElement("div");
    el.id = "domain-head";
    el.className = "domain-head";
    $("view-bench").prepend(el);
  }
  el.hidden = !span;
  if (!span) return;
  const html =
    `<span class="dh-tab">${esc(TAB_JA.grid.no)} ${esc(TAB_JA.grid.name)}</span><span class="dh-sep">›</span>` +
    `<b class="dh-dom">${esc(DOMAIN_JA[span.domain] ?? span.domain)}</b><span class="dh-what">の解析を再生 (予測 → 解析 → 比べる → 判定)</span>` +
    `<button class="dh-close" type="button" data-act="close-domain">× 格子に戻る</button>`;
  if (el.dataset.html !== html) {
    el.dataset.html = html;
    el.innerHTML = html;
    el.dataset.domain = span.domain;
  }
}

// ---- the intake screens (A1, A2, A5) ------------------------------------------
// Mounted on first show. They draw from the events applied so far (the replay's
// playhead, or the live run); listeners hear when that changes (a rewind too).
const intakeListeners = new Set();
let intakeApplied = null; // run id, events applied and own-run when the listeners last heard
let intake = null;
// The record's events with the hearings' sentences in the screen's words (hearing_rounds.js: v2,
// a requirement's name, a part's name), made again when the record changes; the video's frames
// keep the record's words.
let plainMemo = { n: -1, last: null, list: [] };
function plainEvents() {
  if (VIDEO_LAYOUT) return player.events;
  if (plainMemo.n !== player.events.length || plainMemo.last !== player.events.at(-1)) {
    plainMemo = { n: player.events.length, last: player.events.at(-1), list: plainHearingEvents(player.events) };
  }
  return plainMemo.list;
}
const intakeApi = {
  events: () => (draft ? draftEvents() : plainEvents().slice(0, player.applied)),
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
  // ②'s rule cards name the parts a rule hit: the part's name in the design as it is now (the
  // current version, else the newest one there is); null when no design has that id
  partName(id) {
    const s = player.state;
    const designs = s?.designs ?? {};
    const vs = Object.keys(designs).map(Number).filter(Number.isFinite);
    const v = designs[s?.currentVersion] ? s.currentVersion : vs.length ? Math.max(...vs) : null;
    const part = (designs[v]?.parts ?? []).find((p) => p.id === id);
    return part?.name ?? null;
  },
};

// ---- a new request (?new=1): A1 and A2 before a run --------------------------------------
// The server keeps nothing between the calls (hiraku.intake.request): each answer carries the
// draft, sent back with the next change. A1 and A2 draw the draft's intake events (with the
// request as the person's words, and the server's warnings as the lead's). When the hearing is
// settled the request's files are saved and 始める starts a run on them (--intake-events).
// (a static export has no server to send a request to: ?new=1 opens the run list there)
const draft = params.has("new") && !STATIC
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
  // /api/launch answers at once (202); the run has a folder only after the tools it uses are
  // checked (followStart). Until then the bar says so, counts the seconds, and the buttons cannot be
  // pressed. On success they stay so while the page moves to the run.
  Object.assign(draft, { starting: { mode, at: performance.now(), line: "" }, error: "", errorHtml: "" });
  renderDraft();
  const tick = setInterval(renderStartingSeconds, 1000);
  try {
    await postRequest("/api/launch", { request: draft.done.request_id, mode });
    const runId = await followStart((l) => {
      draft.starting.line = startLineJa(l);
      renderStartingSeconds();
    });
    location.href = withDev(`?run=${encodeURIComponent(runId)}`); // follow it live
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
  const line = document.querySelector("#draftbar .starting .step");
  if (line && draft?.starting) line.textContent = draft.starting.line || START_WAIT_JA;
}

// ---- following a start (POST /api/launch answers 202 at once) ------------------------------------
// GET /api/launch's "launch" (launch.py Launcher.begin): state starting, then started with the
// run_id once the child printed its folder, or failed (error_status 500 / 504, error). onTick gets
// the info while it starts.
const START_WAIT_JA = "運転の準備をしています…";
// the child's output lines known here, in words; any other line: START_WAIT_JA (the child prints
// nothing else before its folder unless it fails; hiraku/__main__.py _cmd_run)
const START_LINES_JA = [[/^run folder:/, "記録を開いています…"]];
function startLineJa(l) {
  const line = l?.last_line ?? "";
  return START_LINES_JA.find(([re]) => re.test(line))?.[1] ?? START_WAIT_JA;
}
async function followStart(onTick) {
  for (;;) {
    await new Promise((r) => setTimeout(r, 500));
    let l;
    try {
      l = (await (await fetch("/api/launch", { cache: "no-store" })).json()).launch;
    } catch (e) {
      continue; // asked again in a moment
    }
    if (!l) continue;
    if (l.state === "started" && l.run_id) return l.run_id;
    if (l.state === "failed") throw Object.assign(new Error(l.error ?? String(l.error_status)), { status: l.error_status, body: l });
    onTick?.(l);
  }
}

// A product that no kind of design (設計の型) suits: Claude writes the design itself (docs/contract.md
// Claude が書く設計); the run list's lead says the same (FREE_DESIGN_JA).
const WRITTEN_DESIGN_JA = "この製品に合う設計の型が無いので、Claude が設計そのもの (骨組み・部品・支え) を書いて進めます";
const FREE_DESIGN_JA = "決まった設計の型が無いものでも、Claude が設計そのものを書いて始められます。";

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
  else if (d?.ready) {
    // a product no kind of design suits starts too: Claude writes its design itself (server.py
    // _request: design_way "written"); the scripted run has no way to write one (agent/scripted.py)
    const written = d.design_way === "written";
    // while a start is on its way (draftStart) both buttons stay, disabled, with what is going on
    const st = draft.starting;
    const off = st ? " disabled" : "";
    // what each way is, in one line (the run list's words for them: DRIVEN_JA)
    const scripted =
      `<span class="way"><button data-start="scripted"${off}>決まった手順で始める</button><span class="why">${DRIVEN_JA.scripted}します。Claude は呼ばないので費用はかかりません</span>` +
      (written
        ? `<span class="why" data-why="scripted-written">決まった手順は設計を書けないので、設計を作る前で止まります</span>`
        : d.scripted_plan ? "" : `<span class="why">この製品には決まった手順の計画が無いので、手順が少ない</span>`) +
      `</span>`;
    const claudeWhy =
      `<span class="why">Claude が考えて進めます。費用がかかります</span>` +
      (written ? `<span class="why" data-why="written">${WRITTEN_DESIGN_JA}</span>` : "");
    const claude = !keyed
      ? `<span class="way"><button disabled title="${esc(`${draft.keyName ?? "ANTHROPIC_API_KEY"} が HIRAKU を起こした窓に無い`)}">Claude で始める</button>${claudeWhy}<span class="why">Claude で始めるには準備が要ります (<a class="readme" href="${README_RUN}" target="_blank" rel="noopener">くわしく</a>)</span></span>`
      : draft.confirm && !st
        ? `<span class="confirm">Claude を呼びます。費用の上限は ${usd}、繰り返しは ${esc(rf.round_limit ?? "—")} 回まで。始めますか?</span><button class="go" data-start="claude">始める</button><button data-start-cancel="1">やめる</button>`
        : `<span class="way"><button data-start="claude"${off}>Claude で始める (費用の上限 ${usd})</button>${claudeWhy}</span>`;
    const starting = st
      ? `<span class="starting" role="status"><b>${st.mode === "claude" ? "Claude で" : "決まった手順で"}運転を始めています。</b><span class="step">${esc(st.line || START_WAIT_JA)}</span> 最初は、使うツールの確かめに時間がかかります。<span class="secs">${startingSeconds()} 秒</span></span>`
      : "";
    body = `<b>要件がそろいました</b>${scripted}${claude}${starting}`;
  } else if (d) body = `<span>${esc(d.next ?? "答えの足りない項目があります")}</span>`;
  // the domains are picked, the hearing waits: the intake's own question box (質問に答える / 標準の
  // 値のまま進める, on A1's top and on A2) has the buttons; the bar has them only while the screen
  // shown has none (syncDraftAsk)
  else if (draft.state)
    body =
      `<span class="draft-ask"><button data-draft-ask="questions">質問に答える</button>` +
      `<button data-draft-ask="defaults">標準の値のまま進める</button></span>`;
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
  syncDraftAsk();
}

// The bar's 質問に答える / 標準の値のまま進める are hidden while the intake screen shown has its own
// (the question box: the same two ways, not to be seen twice).
function syncDraftAsk() {
  const ask = document.querySelector("#draftbar .draft-ask");
  if (!ask) return;
  const own = [...document.querySelectorAll("#intake-host button")].some((b) => b.textContent === "標準の値のまま進める" && b.checkVisibility());
  ask.hidden = own;
}

$("view-intake").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-draft-ask]");
  if (!b || !intake) return;
  if (b.dataset.draftAsk === "questions") {
    // the questions are on A1's top: the screen's tab goes there, then the intake scrolls to them
    ui.pinnedTab = "A1";
    ui.tabPlan = [];
    drawn.rev = -1;
    requestAnimationFrame(() => requestAnimationFrame(() => intake.showQuestions()));
  } else intake.useDefaults();
});

// The demo's want goes into A1's box through intake.setWant (not sent: the person presses 送る).
// Once, when the intake is mounted.
function fillPrefill() {
  if (!draft?.prefill || !intake) return;
  intake.setWant(draft.prefill);
  draft.prefill = "";
}

// The run this page followed is over: its run.finished has been read (the playhead may still be
// behind it: a followed run's gaps are paced, so the replay lags the record), or the run started
// here is no longer running (a child that ended without writing run.finished).
function runOver() {
  return ui.followed && (player.finished || ui.childGone);
}

function ownRunGoing() {
  const s = player.state;
  return !!ui.stoppable && ui.stoppable === s.runId && s.status === "running" && !runOver();
}

// The run being watched is going: started here (launch), or followed live while its record says
// running (a run started elsewhere, e.g. from a Claude Code session with the plugin). A replay
// (?live=0, a dropped file, a finished run) is not. The server checks again (Launcher.going_dir).
function runGoing() {
  return ownRunGoing() || (player.live && player.state.status === "running" && !runOver());
}

// A run started here, followed: asks GET /api/launch until the child is gone; then reads what is
// left of the record once and, without run.finished, stops following (what was read plays out).
const CHILD_POLL_MS = 1500;
function watchChild(runId) {
  const timer = setInterval(async () => {
    if (player.finished || player.state.runId !== runId) return clearInterval(timer);
    let info;
    try {
      info = await (await fetch("/api/launch", { cache: "no-store" })).json();
    } catch {
      return; // (asked again on the next turn)
    }
    if (info.running?.run_id === runId) return;
    clearInterval(timer);
    try {
      await feed?.readAll();
    } catch {
      /* what was read stays */
    }
    ui.childGone = true;
    if (!player.finished) {
      feed?.stop();
      player.live = false;
      player.liveAttached = false;
      player.playing = player.playhead < player.duration;
    }
    drawn.rev = -1;
  }, CHILD_POLL_MS);
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

// A static export (STATIC) on ① / ②: the record's request and one button, 「この記録を再生する」, which
// plays the run on ③ from its first design (the request itself cannot be sent there: no server).
function renderReplayBar() {
  const bar = $("replaybar");
  if (!bar) return;
  const want = player.state.project?.want ?? "";
  const html =
    `<span class="rb-want"><b>この記録の依頼</b> ${want ? esc(want) : "—"}</span>` +
    `<button class="go" type="button" data-static-replay="1">この記録を再生する</button>`;
  if (bar.dataset.html !== html) {
    bar.innerHTML = html;
    bar.dataset.html = html;
  }
}

// ② 確認したいこと: over its questions (in the place the intake leaves for the host, intake.petsSlot),
// the pet of the agent that asked them (intake_pets.js, into .a2-askers), a heading that says when
// they were asked and by whom (the newest hearing at the replay's time), and the record's hearings
// in time order, the first one (before the first design) said so, the one shown marked, the ones
// still ahead of the replay faint. Nothing of it in the video's frames (its pets as before).
const a2When = { host: null, el: null, html: "" };
// A record whose hearings asked nothing (each went on with the standard values): not on the film
function noQuestions() {
  if (VIDEO_LAYOUT) return false;
  const rounds = hearingRounds(player.events);
  return rounds.length > 0 && rounds.every((r) => !r.asked);
}
function renderA2When(tab) {
  const slot = intake?.petsSlot;
  if (!slot || VIDEO_LAYOUT || draft) return;
  if (!a2When.host || a2When.host.parentElement !== slot) {
    a2When.host = document.createElement("div");
    a2When.host.className = "a2-askers";
    a2When.el = document.createElement("div");
    a2When.el.className = "a2-when";
    a2When.el.id = "a2-when";
    a2When.html = "";
    slot.replaceChildren(a2When.host, a2When.el);
  }
  if (tab !== "A2") return;
  // (a record that asked nothing: the intake's box says so; no heading and no 「聞いた順」 saying it again)
  const rounds = noQuestions() ? [] : hearingRounds(player.events);
  const shown = roundAt(rounds, player.playhead);
  const items = rounds.map((r) => {
    const cls = r === shown ? "shown" : r.t > player.playhead + 1e-9 ? "later" : "past";
    const tail = r === shown ? " ← 下に出ているもの" : cls === "later" ? " (再生がこの時刻になると出ます)" : "";
    return `<li class="${cls}" data-version="${r.version ?? ""}">${esc(roundLine(r))}${tail}</li>`;
  });
  const html = rounds.length
    ? `<h3 class="a2-when-title">${esc(shown ? roundTitle(shown) : "HIRAKU はまだ何も聞いていません")}</h3>` +
      `<div class="a2-when-sub">聞いた順</div><ol class="a2-rounds">${items.join("")}</ol>`
    : "";
  if (html !== a2When.html) {
    a2When.html = html;
    a2When.el.innerHTML = html;
  }
  slot.classList.toggle("has-when", !!html);
  a2When.el.hidden = !html;
}

function staticReplay() {
  const t0 = player.events.find((e) => e.kind === "design.created")?.t ?? 0;
  if (ui.gridDomain) closeDomain();
  ui.pinnedTab = "bench";
  ui.tabPlan = [];
  ui.frozen = false;
  player.seek(t0);
  player.play();
  drawn.rev = -1;
}

$("view-intake").addEventListener("click", (ev) => {
  if (ev.target.closest("[data-static-replay]")) staticReplay();
});

// (A static export: what HIRAKU is and 「▶ 最初から再生して見る」 are the judges' card, js/judge_intro.js,
// which export_static adds to the exported index.html.)

// The picked domains are the final ones (the intake's picksFinal: its cards say 調べる, not
// 候補 / 考えています, at any time of the replay) when the record is over and no later choice of
// Claude's (a domains.picked with the flags' reasons) replaced the code's picks. The video's frames
// as before.
function picksFinal() {
  if (VIDEO_LAYOUT || draft || player.live || !player.finished) return false;
  return !player.events.some((e) => e.kind === "domains.picked" && Object.keys(e.data?.flag_reasons ?? {}).length);
}
let intakeFinal = false;

function renderIntake(tab) {
  if (intake && intakeFinal !== picksFinal()) {
    // (the record turned out to be over: mounted again with the picks as the final ones)
    intake.destroy();
    intake = null;
    intakeTimeSent = undefined;
  }
  if (!intake) {
    // in a new request the bar (renderDraft) sits above the screens
    $("view-intake").innerHTML =
      `${draft ? `<div class="draftbar" id="draftbar"></div>` : ""}${STATIC ? `<div class="draftbar replaybar" id="replaybar"></div>` : ""}` +
      `<div class="tab-head" id="tab-head-intake"></div><div class="intake-host" id="intake-host"></div>`;
    // (mounted with the events applied so far: not drawn again at once for the same events)
    intakeFinal = picksFinal();
    intake = mountIntake($("intake-host"), intakeApi, { nav: false, screen: tab, onRuleFocus, picksFinal: intakeFinal });
    intakeApplied = `${player.state.runId}|${player.applied}|${runGoing()}`;
    renderDraft();
  }
  if (intake.screen !== tab) intake.show(tab);
  renderReplayBar();
  renderA2When(tab);
  syncDraftAsk();
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
    else if (t - at <= INTAKE_GLOW_NEAR + REPLY_STEP_S * DOMAINS.length) return "n"; // (A1's cards answer REPLY_STEP_S apart from it)
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
  // (each domain's head carries the domain's icon, domain_icons.js; shelf.js draws it)
  shelf ??= mountShelf($("shelf-host"), { tools: () => player.state.tools, runId: () => player.state.runId });
  shelf.render();
}

// s: the state the row shows (the whole record's while ④ replays a domain); domainAt: that domain
// and its version at the playhead, said in one line beside the status (null: none)
function renderTop(s, domainAt = null) {
  $("want").textContent = s.project?.want ?? "";
  // the version and cost pills only once there is a run (none on a new request, or before run.started)
  const hasRun = !!s.project;
  $("pill-version").hidden = !hasRun;
  $("pill-cost").hidden = !hasRun;
  const v = s.currentVersion;
  const limit = s.project?.round_limit;
  // (no design yet: said in words, not v—, but under ?dev=1 and in the film)
  $("pill-version").textContent = `${v == null && !DEV && !VIDEO_LAYOUT ? "設計はまだ" : verLabel(v)}・繰り返し ${roundsUsed(s)} / ${limit ?? "—"} 回`;
  // No cost to show (s.cost null: the user's own Claude Code pays, or a driver that gives none; a
  // scripted run, project.driver, calls no Claude): a dash only, no limit, never $0.00.
  const scripted = s.project?.driver === "scripted";
  const costKnown = !scripted && s.cost !== null && s.cost !== undefined;
  $("pill-cost").textContent = costKnown ? `費用 ${fmtUsd(s.cost)} / ${fmtUsd(s.project?.cost_limit_usd)}` : "費用 —";
  // a record with no cost (a scripted run, the plugin's run): no pill at all (the dash stays under ?dev=1 and in the film)
  if (!costKnown && !DEV && !VIDEO_LAYOUT) $("pill-cost").hidden = true;
  const live = player.live;
  // a replay says where the record is at the playhead (not that something runs now)
  // (a followed run that is over while the replay is still behind its end: said so, not 記録の途中)
  // (a replay before the record's end says the moment it shows: 依頼の時点 before the first design,
  // else 「v3 の時点」; a record that ends with no run.finished says so, with the last version it has;
  // the video's frames keep 記録の途中)
  const status = s.status === "running" && runOver() ? "実行は終わった"
    : s.status === "running" && !live && !VIDEO_LAYOUT ? (player.finished ? (s.currentVersion == null ? "依頼の時点" : `${verLabel(s.currentVersion)} の時点`) : `この記録は途中で終わっています (${verLabel(wholeState().currentVersion)} まで)`)
    : ({ running: "記録の途中", done: "完了", stopped: "途中で止まった", failed: "失敗", empty: "" }[s.status] ?? s.status);
  $("mode").innerHTML =
    (domainAt
      ? `<span class="domain-playing" id="domain-playing" data-domain="${esc(domainAt.domain)}">${esc(TAB_JA.grid.no)} で <b>${esc(DOMAIN_JA[domainAt.domain] ?? domainAt.domain)}</b> を再生中 (${esc(verLabel(domainAt.version))})</span>`
      : "") +
    // (the run list's words: 決まった手順, DRIVEN_JA / labels.js runSummaryJa)
    // (data-driver: a scripted run's replay and a Claude run's replay look apart; large with ?video=1)
    `<span class="badge ${live ? "live" : ""}" data-driver="${live ? "" : scripted ? "scripted" : s.project?.driver ? "claude" : ""}">${live ? "● 実行中を追いかけ" : scripted ? "決まった手順の再生" : s.project?.driver ? "Claude の実行の再生" : "再生"}${status && !live ? `・${esc(status)}` : ""}</span>` +
    // the run's id only under ?dev=1 (people go back to the list by 「☰ 一覧へ」 at the top left, #back-link)
    (DEV ? `<a class="runid" href="${withDev("?list=1")}" title="実行の一覧へ">${esc(s.runId ?? "")}</a>` : "") +
    // a run this screen started can be stopped while it goes
    (ownRunGoing()
      ? `<button class="stop-btn" data-stop="${esc(s.runId)}" ${ui.stopAsked ? "disabled" : ""}>${ui.stopAsked ? "止めています…" : "■ 止める"}</button>`
      : STATIC
        ? "" // (the way back to the list: #back-link at the top left)
        : DEV
          ? `<a class="launch-link" href="${withDev("?list=1")}" title="決まった手順か Claude で、新しく走らせる">▶ その場で走らせる</a>`
          : "");
}

// lcId: the load case shown on the 3D (null on the screens without it: no chip ringed); openDomain:
// ④'s domain replaying in the tab (ringed whichever of its load cases shows)
function renderChips(s, lcId, openDomain = null) {
  const focusDomain = openDomain ?? (s.project?.load_cases ?? []).find((c) => c.load_case_id === lcId)?.domain;
  $("chips").innerHTML = DOMAINS.map((d) => {
    const j = judgementState(s, d);
    const title = j.judgement?.summary != null ? judgedWords(j.judgement.summary) : (j.picked ? "まだ判定が無い" : onlyTestsLine(s) ?? "この実行では選んでいない");
    const held = heldCases(s, d, s.currentVersion).length ? " held" : ""; // held: a count not judged (held.js)
    return `<span class="chip ${j.state}${held} ${d === focusDomain ? "focus" : ""}" title="${esc(title)}">${domainIconSvg(d, "chip-ic")}${esc(DOMAIN_JA[d])}</span>`;
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
// The agents of one role kind, bundled into one row in the list when there are two or more:
// the module designers (and the designer) as 設計, the CAE roles as CAE (labels.js roleNames: no 「…役」).
const AGENT_GROUP_JA = { designer: "設計", cae: "CAE" };
const agentGroupOf = (id) => (id === "designer" || id.startsWith("designer.") ? "designer" : id.startsWith("cae.") ? "cae" : null);
const AGENT_ALERT = [RED_STATUS, "解析が通らない"];
const AGENT_HEED = ["止められた", MISMATCH_STATUS]; // attention (orange), not a NG judgement
const agentCls = (a) => (a.status === "✓" ? "done" : AGENT_ALERT.includes(a.status) ? "alert" : AGENT_HEED.includes(a.status) ? "heed" : a.active ? "active" : "");

// The list's entries in the agents' order: one agent, or a bundle at its first member's place.
function agentEntries(order) {
  const groups = new Map();
  for (const id of order) {
    const g = agentGroupOf(id);
    if (g) groups.set(g, [...(groups.get(g) ?? []), id]);
  }
  const out = [];
  const placed = new Set();
  for (const id of order) {
    const g = agentGroupOf(id);
    if (!g || groups.get(g).length < 2) out.push({ id });
    else if (!placed.has(g)) {
      placed.add(g);
      out.push({ group: g, ids: groups.get(g) });
    }
  }
  return out;
}

function agentRowHtml(s, id, extra = "") {
  const a = s.agents[id];
  const cls = `${agentCls(a)}${extra}`;
  // the role's name in full (操安の CAE), not the domain alone
  if (agentPets?.has(id)) return petRowHtml(id, a.status, { cls, name: agentLongName(id), why: a.stopWhy });
  return `<li class="${cls}"><span class="pet" style="--pet:${petColor(id)}"></span><span>${esc(agentLongName(id))}</span><span class="st">${esc(a.status)}</span>${stopWhyHtml(a.stopWhy)}</li>`;
}

// A bundle's row: 「設計 ×6」, pressed to show each one (ui.openGroups; closed by default, so the
// picture at ?t= does not depend on what was pressed). Its look and status follow the members: an
// alert first, then the ones at work (the same look as a single row at work), else all done, else
// the status they share. Closed, it carries the pet (and bubble) of its first member at work, else
// of its first member.
function agentGroupHtml(s, g, ids) {
  const open = ui.openGroups.has(g);
  const as = ids.map((id) => s.agents[id]);
  // (alert: a NG judgement, red; else heed: stopped by hooks or a mismatch, orange)
  const alert = as.find((a) => agentCls(a) === "alert") ?? as.find((a) => agentCls(a) === "heed");
  const going = ids.filter((id) => agentCls(s.agents[id]) === "active");
  const cls = alert ? agentCls(alert) : going.length ? "active" : as.every((a) => a.status === "✓") ? "done" : "";
  const st = alert ? alert.status
    : going.length ? `${s.agents[going[0]].status}${going.length > 1 ? ` ほか ${going.length - 1}` : ""}`
    : as.every((a) => a.status === as[0].status) ? as[0].status : "";
  const rep = going[0] ?? ids[0];
  const btn = `<button class="ag-toggle" type="button" data-agent-group="${g}" aria-expanded="${open}" title="${open ? "たたむ" : "1 人ずつ見る"}">` +
    `<span class="caret">${open ? "▾" : "▸"}</span>${AGENT_GROUP_JA[g]} <span class="cnt">×${ids.length}</span></button>`;
  const head = agentPets?.has(rep)
    ? `<li class="petrow agroup ${cls}${open ? " open" : ""}" data-group="${g}"${open ? "" : " data-say-host"}>` +
      (open ? `<span class="petslot"></span>` : `<span class="petslot" data-pet-slot="${esc(rep)}"></span>`) +
      `<span class="nm">${btn}</span><span class="st">${esc(st)}</span>${open ? "" : `<span class="say" data-say="${esc(rep)}"></span>`}</li>`
    : `<li class="agroup ${cls}${open ? " open" : ""}" data-group="${g}"><span class="pet" style="--pet:${petColor(rep)}"></span><span>${btn}</span><span class="st">${esc(st)}</span></li>`;
  return head + (open ? ids.map((id) => agentRowHtml(s, id, " member")).join("") : "");
}

function renderAgents(s) {
  const order = agentOrder(s);
  const entries = agentEntries(order);
  const rows = entries.reduce((n, e) => n + (e.group && ui.openGroups.has(e.group) ? 1 + e.ids.length : 1), 0);
  const ul = $("agents");
  ul.classList.toggle("many", rows > 6);
  ul.classList.toggle("lots", rows > 8); // modules: the module designers (an older record's six) come on top of the others
  ul.classList.toggle("pets", !!agentPets);
  ul.innerHTML = entries.map((e) => (e.group ? agentGroupHtml(s, e.group, e.ids) : agentRowHtml(s, e.id))).join("");
  agentPets?.mount(ul, "bench");
}

$("agents").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-agent-group]");
  if (!b) return;
  const g = b.dataset.agentGroup;
  if (ui.openGroups.has(g)) ui.openGroups.delete(g);
  else ui.openGroups.add(g);
  drawn.rev = -1;
});

function rank(id) {
  const fixed = ["lead", "designer", "packager"];
  if (fixed.includes(id)) return fixed.indexOf(id);
  // modules: the module designers after the designer: the design roles in the contract's order
  // (structure, mechanism, electric), an older record's designer.<module> in the record's order
  if (designerRole(id)) return 1.5 + DESIGNER_ROLES.indexOf(designerRole(id)) / 10;
  if (id.startsWith("designer.")) return 1.5;
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
    `<details><summary title="${esc(DEV || VIDEO_LAYOUT ? `${w.id} ${w.ja}` : w.ja)}">詳しい記録${DEV || VIDEO_LAYOUT ? ` (${esc(w.id)})` : ""}<span class="tm">${fmtTime(e.tRec ?? e.t)}s</span></summary>${esc(e.text)}</details></li>`
  );
}

// The log's and the footer's words in plain words (plain.js: the record's ids and English words in
// span.rec, hidden but under ?dev=1 and ?video=1; a tool's id by its short name), with each short
// aside in brackets kept in one piece (as labels.js keepAsides).
// (kept per text: the log is drawn again whole when a line is rewritten; the tools' names come
// with tool.checked, so a new tool starts the cache again)
const plainCache = { tools: -1, html: new Map() };
function plainLine(s, text) {
  if (isFilmWords()) return keepAsides(text);
  const tools = Object.keys(s.tools ?? {}).length;
  if (plainCache.tools !== tools || plainCache.html.size > 20000) {
    plainCache.tools = tools;
    plainCache.html.clear();
  }
  let html = plainCache.html.get(text);
  if (html === undefined) {
    html = plainLineHtml(s, text);
    plainCache.html.set(text, html);
  }
  return html;
}
const plainHid = (s, text) => !!text && plainLine(s, text).includes('class="rec"');

function logItem(e, ctx = null) {
  if (e.level === "hook" && ctx) return hookLogItem(e, ctx.hooks.get(e.seq), ctx.resolved.has(e.seq));
  const s = ctx?.s ?? {};
  const who = e.level === "said" ? `<span class="who">${esc(agentLongName(e.agent))}:</span> ` : "";
  // more: words too long for a note (an agent.stopped's stop_reason), folded under くわしく (as recorded)
  const note =
    (e.note ? `<span class="note">${plainLine(s, e.note)}</span>` : "") +
    (e.more ? `<details class="more"><summary>くわしく</summary><span class="note">${keepAsides(e.more)}</span></details>` : "");
  // the whole line as recorded under the pointer when plain words took something out
  const hid = e.level !== "said" && !isFilmWords() && (plainHid(s, e.text) || plainHid(s, e.note));
  const tip = hid ? ` title="${esc(e.text + (e.note ? `\n${e.note}` : ""))}"` : "";
  const upd = e.updatedT != null ? `<span class="tm">・更新 ${fmtTime(e.updatedT)}s</span>` : "";
  // What an agent said can run long: the line shows its first lines, the record keeps all of it.
  // A short aside in brackets stays on one line: not "(形の見 / 本)".
  // (an agent's own words, of any length, in the narrow log: an aside longer than the line has to
  // break somewhere, so the lint's one-letter and bracket checks are let off there: data-lint)
  const text = e.level === "said" ? `<span class="tx" data-lint="lonely" title="${esc(e.text)}">${plainLine(s, e.text)}</span>` : plainLine(s, e.text);
  return `<li class="${e.level}"${tip}>${who}${text}<span class="tm" title="記録の t (実行が始まってからの秒)">${fmtTime(e.tRec ?? e.t)}s</span>${upd}${note}</li>`;
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
    const ctx = { s, resolved, hooks: new Map([...s.hooks.denied, ...s.hooks.blockedStops].map((h) => [h.seq, h])) };
    ol.insertAdjacentHTML("beforeend", s.log.slice(drawn.logLen).map((e) => logItem(e, ctx)).join(""));
    ol.scrollTop = ol.scrollHeight;
  }
  drawn.logLen = s.log.length;
  drawn.logFirstSeq = first;
  drawn.logRev = s.logRev;
  drawn.resolved = resolvedKey;
}

// The tests over the 3D: per domain its pet and its tests' boxes, each with its state (test_board.js).
// ?video=1 (without ?board=1): the one-line row of chips instead, so the 3D keeps its height in the video.
// span: ④'s open domain (its tests only, its own 自動 / pick: ui.gridLc, not ③'s ui.pinnedLc)
const TEST_CHIPS = params.get("video") === "1" && params.get("board") !== "1";
function renderTests(s, lcId, span = null) {
  const all = s.project?.load_cases ?? [];
  const cases = span ? all.filter((c) => c.domain === span.domain) : all;
  if (TEST_CHIPS) return renderTestChips(cases, lcId, span);
  // the board of every test at a glance (js/test_board.js)
  const pets = agentPets ? { has: (id) => agentPets.has(id) } : null;
  const { html } = boardHtml(s, cases, { lcId, auto: !(span ? ui.gridLc : ui.pinnedLc), pets, withRest: !span });
  if (html === drawn.tests) return;
  drawn.tests = html;
  const el = $("tests");
  el.classList.add("board");
  el.innerHTML = html;
  agentPets?.mount(el, "board");
}

// The row of chips (?video=1): many load cases show the id (and, for an id shared by several, a
// short name from the title) and the row scrolls sideways, the chosen one kept in sight; the whole
// title is the tooltip.
const MANY_TESTS = 12;
function renderTestChips(cases, lcId, span) {
  const shorts = caseShortNames(cases);
  const auto = `<button class="auto ${(span ? ui.gridLc : ui.pinnedLc) ? "" : "on"}" data-lc="">自動</button>`;
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
  const el = $("tests");
  el.classList.remove("board");
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
const formulaTitleOf = (id) => formulaTitles.get(id);
const formulasIn = fetch(getUrl("/api/formulas"), { cache: "no-store" })
  .then((r) => (r.ok ? r.json() : { formulas: [] }))
  .then((got) => {
    for (const f of got.formulas ?? []) if (f.title) formulaTitles.set(f.formula_id, f.title);
    drawn.rev = -1;
  })
  .catch(() => {});

// A "くわしく" (details[data-keep]) stays open or shut across the cards' redraws.
// ?more=1 opens the right column's くわしく (the cards under it) and the tabs' folds; the small
// くわしく inside the cards (式と入力, 基準の根拠, ...) stay shut until opened by hand, so a
// prediction's formula does not fill the column.
const MORE_ALL = params.get("more") === "1";
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
// (?zoom= on a card folded under the chain's くわしく, e.g. cmp: that くわしく open, so the card is there to move in on)
// (?video=1: the cards always shown, as before the fold: its くわしく is hidden, app.css)
const VIDEO_LAYOUT = params.get("video") === "1";
if (VIDEO_LAYOUT || MORE_ALL || (ZOOM && $("chain-more").querySelector(ZOOM.selector))) $("chain-more").open = true;

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
    const asm = (pred.assumptions ?? []).slice(0, 3).map((a) => `<li>${esc(judgedWords(a))}</li>`).join("");
    p.className = "card";
    // the formula's title in words when the catalogue has one; the formula itself and its inputs
    // folded under くわしく (without a title: as written, as before)
    const title = formulaTitles.get(pred.formula_id);
    const formula = `<div class="expr">${esc(pred.expression)}</div><div class="kv">${inputs}</div>`;
    p.innerHTML =
      `<h3>1. 予測<small>手計算の段${DEV || VIDEO_LAYOUT ? " (K0)" : ""}・${esc(agentLongName(pred.agent))}</small></h3>` +
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
    p.innerHTML = `<h3>1. 予測<small>手計算の段</small></h3><div>${loadCase ? `${esc(loadCase.test_id)} の予測はまだ。hooks は予測の無い解析を止める${DEV || VIDEO_LAYOUT ? " (HK1)" : ""}` : "試験がまだ無い"}</div>`;
  }
  // 2. analysis (with 「ビューアで見る」, viewer_link.js)
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
    // the row of the quantity the criterion is held to says so; the others carry no mark (no OK / NG here)
    const jr = judgedResult(ana, loadCase?.criterion);
    const rows = [...top, ...refs.filter((r) => !top.includes(r))];
    if (jr && !rows.includes(jr)) rows.push(jr);
    const res = rows.map((r) => `<tr class="${isRef(r) ? "ref" : r === jr ? "judged" : ""}" title="${esc(r.quantity + (r.where ? ` @ ${r.where}` : ""))}"><td>${isRef(r) ? `<small class="ref-tag">${esc(JUDGED_JA.ref)}</small> ` : r === jr ? `<small class="used">${esc(JUDGED_JA.number)}</small> ` : ""}${nameHtml(r.quantity)}${r.where ? ` <span class="sub">@ ${esc(r.where)}</span>` : ""}</td><td>${esc(fmtVU(r.value, r.unit))}</td></tr>`).join("");
    const anaDesign = s.designs[ana.design_version] ?? null;
    const anaAsm = (ana.assumptions ?? []).map((x) => `<li>${esc(assumptionText(x, anaDesign))}</li>`).join("");
    a.className = ana.status === "failed" ? "card bad" : "card";
    a.innerHTML =
      `<h3>2. 解析<small>CAE の段 (${DEV || VIDEO_LAYOUT ? `${esc(ana.base)} ` : ""}${esc(BASE_JA[ana.base] ?? "")})</small></h3>` +
      `<div class="sub" title="${esc(DEV ? ana.tool : "")}">${esc(toolShort(s, ana.tool))}${DEV || VIDEO_LAYOUT ? `・${esc(ana.load_case_id)}` : ""}${typeof ana.wall_time_s === "number" ? `・${fmt(ana.wall_time_s, 2)} 秒` : ""}</div>` +
      (ana.status === "failed" ? `<div class="err">通らなかった: ${esc(ana.error ?? "")}</div>` : `<table>${res}</table>`) +
      (ana.mesh_convergence ? `<div class="conv">${esc(convergenceLine(ana.mesh_convergence))}</div>` : "") +
      (loadCase?.domain === "strength" ? `<div class="not-judged" data-role="not-judged">${esc(JUDGED_JA.mountsNotJudged)}</div>` : "") +
      `<div class="vrow">${viewerButtonHtml(viewerRunId(s), ana)}</div>` +
      // the analysis' assumptions (the record's words, English as written; the break angle's note
      // as one line: edge_note.js), folded
      (anaAsm ? `<details class="more" data-keep="ana-asm"><summary>くわしく (解析の前提)</summary><ul class="asm">${anaAsm}</ul></details>` : "");
    keepOpen(a);
  } else if (running) {
    a.className = "card busy";
    a.innerHTML = `<h3>2. 解析<small>CAE の段</small></h3><div title="${esc(running.tool)}">${esc(toolShort(s, running.tool))} で解析中…</div>`;
  } else {
    a.className = "card wait";
    a.innerHTML = `<h3>2. 解析<small>CAE の段</small></h3><div>${pred ? "予測を受けて解析を待っている" : "予測のあとに走る"}</div>`;
  }
  // 3. comparison (the prediction against the analysis: 予想通り / 不一致) and 4. verdict (the
  // criterion's OK / NG), one card: the verdict is its own step under the comparison
  const c = $("card-cmp");
  const step4 = (body) => `<div class="step4" data-step="verdict"><h3>4. 判定</h3>${body}</div>`;
  if (cmp) {
    const ok = cmp.status === "match";
    const safe = isSafeSide(verdict);
    c.className = `card ${ok ? "ok" : "warn"}`; // a mismatch: attention (orange); red only for a NG verdict
    let v = "";
    if (verdict) {
      // A verdict from a safe-side mismatch carries its own mark and says which value it used.
      const how = safe ? `<span class="safe-mark" title="予想通りではなく、説明された安全側の不一致から">安全側で判定</span>` : "";
      const big = verdictSentence(verdict);
      // the crash tool's note on the break angle (edge_note.js): 境目 when it names this verdict's
      // quantity as changing inside the band; 参考 for a member a little under the break angle
      const vAna = s.analyses[verdict.analysis_id ?? cmp.analysis_id] ?? ana;
      const edge = analysisEdge(vAna);
      const edgeLines = edge
        ? (edgeFlips(edge, verdict.quantity) || edgeFlips(edge, cmp.quantity) ? edgeLineHtml(edge, edgeRaw(vAna)) : "") +
          nearLineHtml(edge, s.designs[vAna?.design_version] ?? null)
        : "";
      // the pass or fail in one sentence, then the value (a prediction or a result) beside its criterion
      v =
        `<div class="verdict ${verdict.pass ? "ok" : "bad"}${safe ? " safe" : ""}">${how}<div class="verdict-big">${esc(big.text)}</div>` +
        // the quantity compared and judged (frame_shell: the hot-spot stress for a max_stress criterion)
        `判定 (${valueFromJa(verdict)}の値): ${esc(quantityName(cmp.quantity ?? verdict.quantity))}<div class="vline">${esc(verdictLine(verdict))}</div>` +
        `<span class="sub">余裕 ${esc(fmtMargin(verdict))}${loadCase?.title ? `・${esc(loadCase.title)}` : ""}</span>` +
        edgeLines +
        // the criterion's basis is the record's own words (English): folded
        (loadCase?.criterion?.basis ? `<details class="more" data-keep="cmp-basis"><summary>くわしく (基準の根拠)</summary><div class="sub basis">${esc(loadCase.criterion.basis)}</div></details>` : "") +
        `</div>`;
    } else if (loadCase?.criterion) {
      v = `<div class="sub">基準: ${esc(quantityName(loadCase.criterion.quantity))} ${esc(opLabel(loadCase.criterion.op))} ${esc(fmtQ(loadCase.criterion.limit))} (判定を待っている)</div>`;
    } else {
      v = `<div class="sub">この試験には基準が無い (判定しない)</div>`;
    }
    // Long words are cut to a few lines here; the log keeps them whole.
    const safeWhy = cause?.safe_side ? `<div class="safe-why clamp" title="${esc(judgedWords(cause.safe_side_reason ?? ""))}"><b>安全側で見られている:</b> ${esc(judgedWords(cause.safe_side_reason ?? ""))}</div>` : "";
    const cz = cause
      // (the cause and the fix in plain words, as the log's: record ids by their kind, parts by their names)
      ? `<div class="cause"><div class="clamp" title="${esc(judgedWords(cause.cause))}">原因: ${plainLine(s, judgedWords(cause.cause))}</div><div class="clamp c2" title="${esc(judgedWords(cause.fix))}">直し方: ${plainLine(s, judgedWords(cause.fix))}${cause.resolved ? " (解決)" : ""}</div>${safeWhy}</div>`
      : ok
        ? ""
        : `<div class="cause">原因が書かれるまで、この分野は先に進めない (HK3)</div>`;
    // The row whose value the verdict used.
    const used = verdict ? (valueFromJa(verdict) === "予測" ? "pred" : "ana") : null;
    const tag = `<small class="used">判定の値</small>`;
    c.innerHTML =
      `<h3>3. 比べる<small>予測と解析</small></h3>` +
      `<table><tr class="${used === "pred" ? "used" : ""}"><td>予測${used === "pred" ? tag : ""}</td><td>${esc(fmtQ(cmp.predicted))}</td></tr>` +
      `<tr class="${used === "ana" ? "used" : ""}"><td>解析${used === "ana" ? tag : ""}</td><td>${esc(fmtQ(cmp.analyzed))}</td></tr>` +
      `<tr><td>差 (許す幅 ${fmtPct(cmp.tolerance_rel, 0)})</td><td>${fmtSigned(cmp.diff_rel)}</td></tr></table>` +
      `<span class="badge ${ok ? "ok" : "warn"}">${ok ? "予想通り ✓" : "不一致 !"}</span>` +
      cz +
      step4(v);
    keepOpen(c);
  } else {
    c.className = "card wait";
    const tol = loadCase ? `許す幅 ${fmtPct(loadCase.tolerance_rel, 0)} (荷重条件で決まっている)` : "";
    c.innerHTML = `<h3>3. 比べる<small>予測と解析</small></h3><div>予測と解析がそろうと照らし合わせる。${tol}</div>` +
      step4(`<div class="sub">比べたあと、基準で OK / NG を決める</div>`);
  }
  renderChainHead(s, chain);
}

// The right column's first lines: what is going on now, the verdict (quantity, value, criterion,
// OK / NG; before one, the comparison's 予想通り / 不一致), what comes next. One line each; the
// cards under くわしく hold the rest.
function nextStep(chain) {
  const { loadCase, pred, ana, running, cmp, verdict, cause } = chain;
  if (!loadCase) return "試験が決まるのを待つ";
  if (!pred) return "手計算で予測を書く (予測の無い解析は hooks が止める)";
  if (!ana) return running ? "解析が終わるのを待つ" : "解析を走らせる";
  if (ana.status === "failed") return "解析が通らなかった: 条件を直して走らせ直す";
  if (!cmp) return "予測と解析を比べる";
  if (cmp.status !== "match" && !cause) return "不一致の原因を書く (書くまでこの分野は進めない)";
  if (cmp.status !== "match" && cause && !cause.resolved && cause.fix) return `直し方: ${shownText(plainLine(player.state, judgedWords(cause.fix)))}`;
  if (!verdict) return loadCase.criterion ? "基準で OK / NG を決める" : "この試験には基準が無い (判定しない)";
  return verdict.pass ? "次の試験へ" : "設計を直して次の v へ";
}
function renderChainHead(s, chain) {
  const { loadCase, cmp, verdict, ana } = chain;
  const now = nowSentence(s, chain, (tool) => toolTitle(s, tool)) || "記録を待っている";
  let judged;
  if (verdict) {
    const q = quantityName(cmp?.quantity ?? verdict.quantity);
    judged = `<span class="ch-q">${esc(q)}</span> <span class="ch-v">${esc(verdictLine(verdict).replace(/ → (OK|NG)$/, ""))}</span>` +
      `<b class="ch-verdict ${verdict.pass ? "ok" : "bad"}">${verdict.pass ? "OK" : "NG"}</b>`;
  } else if (cmp) {
    const ok = cmp.status === "match";
    judged = `<span class="ch-q">判定はまだ</span> <span class="badge ${ok ? "ok" : "warn"}">${ok ? "予想通り" : MISMATCH_STATUS}</span>`;
  } else {
    const crit = loadCase?.criterion?.limit != null ? `基準: ${quantityName(loadCase.criterion.quantity)} ${opLabel(loadCase.criterion.op)} ${fmtQ(loadCase.criterion.limit)}` : "";
    judged = `<span class="ch-q">判定はまだ</span>${crit ? ` <span class="ch-v">${esc(crit)}</span>` : ""}`;
  }
  // the 境目 line (edge_note.js) right under the verdict's row, so it shows with the tab folded too
  // (the same test as the 4. 判定 card: the note names this verdict's quantity as changing)
  const vAna = verdict ? s.analyses[verdict.analysis_id ?? cmp?.analysis_id] ?? ana : null;
  const edge = vAna ? analysisEdge(vAna) : null;
  const edgeRow = edge && (edgeFlips(edge, verdict.quantity) || edgeFlips(edge, cmp?.quantity))
    ? `<div class="ch-row ch-edge" data-role="edge-head" title="${esc(edgeRaw(vAna))}"><small></small>${esc(edgeText(edge))}</div>`
    : "";
  // why a verdict on the tool's value after the load path was checked by hand is OK / NG (why_line.js)
  const why = verdict ? pathCheckedWhy(s, verdict, formulaTitleOf) : null;
  const whyRow = why ? `<div class="ch-row ch-why" data-role="why" title="${esc(why.text)}"><small></small>${esc(why.text)}</div>` : "";
  const html =
    `<div class="ch-row ch-now clamp1" title="${esc(now)}"><small>いま</small>${esc(now.replace(/^いま: /, ""))}</div>` +
    `<div class="ch-row ch-judged" data-role="verdict"><small>判定</small>${judged}</div>` +
    whyRow +
    edgeRow +
    `<div class="ch-row ch-next clamp1" title="${esc(nextStep(chain))}"><small>次</small>${esc(nextStep(chain))}</div>`;
  if (chainHeadHtml !== html) $("chain-head").innerHTML = chainHeadHtml = html;
}
let chainHeadHtml = "";

// The other tabs' first lines, as ③'s: what is there now, the one thing that matters, what comes
// next (one line each), and a くわしく that unfolds the rest of the tab (css: .folded hides it;
// ?more=1 opens them all; a new request, ?new, starts open: its screens are filled in by hand).
// Each returns [いま, 大事 (label, text, tone: "ok" | "warn" | ""), 次].
const listJa = (xs, n = 4) => (xs.length > n ? `${xs.slice(0, n).join("・")} ほか ${xs.length - n}` : xs.join("・"));
const TAB_HEADS = {
  A1(s) {
    const picks = s.intake.picked?.picks ?? [];
    const names = picks.map((p) => p.shown_name ?? DOMAIN_JA[p.domain] ?? p.domain);
    const seven = picks.filter((p) => DOMAINS.includes(p.domain) && p.domain !== "assembly").length; // (組み立て is looked at apart from the 7)
    const asm = picks.some((p) => p.domain === "assembly");
    const of7 = seven >= 7 ? "7 分野すべて" : `7 分野のうち ${seven}`;
    return [
      // (the request itself is in the bar above ① and in the chat: not a third time here)
      picks.length ? `調べる分野が決まった (${of7}${asm ? "、ほかに組み立て" : ""})` : "作りたいものを待っている",
      ["調べる分野", picks.length ? `${listJa(names)}` : "まだ決まっていない", ""],
      !picks.length ? "作りたいものから、調べる分野を選ぶ" : s.intake.questions.length ? "② 確認したいことで、足りない値に答える" : "③ 解析で、試験ごとに予測と解析を比べる",
    ];
  },
  A2(s) {
    // the question cards as the screen has them (the requirements' questions and the rules' hits)
    const cards = $("intake-host")?.querySelectorAll(".in-pane.a2 .in-q") ?? [];
    const qs = cards.length;
    const open = [...cards].filter((c) => c.classList.contains("open")).length;
    const byDefault = s.intake.answers.filter((a) => a.source === "default").length;
    // (the questions shown were answered and the run went on: which versions it made after them;
    // the video's frames as before)
    const shown = VIDEO_LAYOUT ? null : roundAt(hearingRounds(player.events), player.playhead);
    const after = shown?.answered && s.currentVersion != null && s.currentVersion > (shown.version ?? 0)
      ? `答えのあと ${shown.version == null ? verLabel(1) : verLabel(shown.version + 1)}${s.currentVersion > (shown.version ?? 0) + 1 ? ` から ${verLabel(s.currentVersion)}` : ""} を作った`
      : null;
    return [
      qs ? `決まっていないことを ${qs} つ聞きます${open ? ` (まだ答えていないもの ${open})` : ""}` : "聞くことは無い",
      ["標準の値", byDefault ? `分からない所 ${byDefault} つは、標準の値で進めました (確かめていない値として報告に載ります)` : "どれにも答えがあります", byDefault ? "warn" : ""],
      after ?? (!qs ? "③ 解析へ" : open ? "質問に答える (答えなければ標準の値で進めます)" : "③ 解析へ (答えはそろった)"),
    ];
  },
  A5(s) {
    const rules = Object.keys(s.intake.rules).length;
    // each rule's newest check (the design as it is now), not the earlier versions' hits
    const newest = new Map();
    for (const c of Object.values(s.intake.ruleChecks)) if (!newest.has(c.rule_id) || (c.design_version ?? 0) >= (newest.get(c.rule_id).design_version ?? 0)) newest.set(c.rule_id, c);
    const hits = [...newest.values()].flatMap((c) => (c.hits ?? []).map((h) => `${c.rule_id ?? ""} ${h.part_id ?? ""}`.trim()));
    return [
      rules ? `ルール ${rules} 件 (読み込んだ束 ${s.intake.docs.length})` : "ルールを待っている",
      ["引っかかった", hits.length ? listJa(hits, 3) : "無い", hits.length ? "warn" : "ok"],
      hits.length ? "② 確認したいことで、設計を直すかルールを外すかを決める" : "ルールのファイルを置けば足せる",
    ];
  },
  A4() {
    const host = $("shelf-host");
    const sum = host.querySelector(".sh-sum")?.textContent.trim() ?? "";
    // the shelf's 材質 view (js/materials.js): the material cards, and how many have values not read
    const mt = host.querySelector(".mt-host:not([hidden])");
    if (mt) {
      const unread = mt.querySelectorAll(".mt-row .mt-tag.unread").length;
      return [
        sum ? `材質: ${sum}` : "材質カードを読んでいる",
        ["出典", unread ? `読んでいない値のある材質 ${unread}` : sum ? "どの材質の値も、出典を読んで確かめた" : "—", unread ? "warn" : sum ? "ok" : ""],
        "材質を選ぶと、カードの値と出どころが右に出る",
      ];
    }
    const bad = host.querySelectorAll(".sh-chk.bad, .sh-chk.noval").length;
    return [
      sum ? `棚: ${sum}` : "棚を読んでいる",
      ["確かめ", bad ? `通っていないツール ${bad}` : sum ? "どのツールも、答えの分かっている問題で確かめた" : "—", bad ? "warn" : sum ? "ok" : ""],
      "ツールを選ぶと、確かめた問題と結果が右に出る",
    ];
  },
  A7(s) {
    const host = $("asm-host");
    const cap = host.querySelector(".asm-caption")?.textContent.trim() ?? "";
    const sum = host.querySelector(".asm-sum")?.textContent.trim() ?? "";
    // the steps whose part does not fit (干渉 ✕), and the steps with only a tool's or a hand's ✕: those
    // two are cautions that go into a verdict only when the record judges their quantity
    // (steps_without_tool_clearance / steps_out_of_reach; the showcase runs judge neither)
    const rows = [...host.querySelectorAll(".asm-steps li.has-ng")];
    const hard = rows.filter((li) => li.querySelector(".ck-no_interference.is-ng")).length;
    const reach = rows.length - hard;
    const judgedReach = Object.values(s.verdicts ?? {}).some((v) => /^steps_(without_tool_clearance|out_of_reach)$/.test(v.quantity ?? ""));
    const next = hard
      ? `部品が入らない手順 ${hard}: 干渉しないように設計を直す${reach ? `・工具や手が届きにくい手順 ${reach}` : ""}`
      : reach
        ? judgedReach
          ? `工具や手が届きにくい手順 ${reach}: 工具・手が届くように設計を直す`
          : `工具や手が届きにくい手順 ${reach} (判定には入らない注意)`
        : sum
          ? "④ 解析結果で、ほかの分野と並べて見る"
          : "組み立ての解析が終わるのを待つ";
    return [cap || "組み立ての記録を待っている", ["組み立て", sum || "—", hard || (reach && judgedReach) ? "warn" : reach ? "" : sum ? "ok" : ""], next];
  },
};
const TAB_HEAD_EL = { A1: "tab-head-intake", A2: "tab-head-intake", A5: "tab-head-intake", A4: "tab-head-A4", A7: "tab-head-A7" };
// (?video=1: every tab whole, as before the fold; its band is hidden, app.css)
const tabOpen = new Set(MORE_ALL || draft || VIDEO_LAYOUT ? Object.keys(TAB_HEADS) : []);
const tabHeadHtml = {};
function renderTabHead(s, tab) {
  const el = $(TAB_HEAD_EL[tab]);
  if (!el) return;
  const view = el.parentElement;
  // ② of a record that asked nothing: the intake's one box says it (the standard values used); no rows over
  // it saying the same, and the tab not folded under them (the film as before)
  const quiet = tab === "A2" && noQuestions();
  el.hidden = quiet;
  const open = quiet || tabOpen.has(tab);
  view.classList.toggle("folded", !open);
  view.dataset.tab = tab;
  if (quiet) return;
  const [now, [label, text, tone], next] = TAB_HEADS[tab](s);
  const html =
    `<div class="ch-row clamp1" title="${esc(now)}"><small>いま</small>${esc(now)}</div>` +
    `<div class="ch-row clamp1" data-role="key" title="${esc(text)}"><small>${esc(label)}</small><span class="th-key ${tone}">${esc(text)}</span></div>` +
    `<div class="ch-row clamp1" title="${esc(next)}"><small>次</small>${esc(next)}</div>` +
    `<button class="th-more" type="button" data-tab-more="${tab}" aria-expanded="${open}">${open ? "たたむ" : "くわしく"}</button>`;
  if (tabHeadHtml[tab] !== html || el.dataset.tab !== tab) {
    el.innerHTML = tabHeadHtml[tab] = html;
    el.dataset.tab = tab;
  }
}
document.addEventListener("click", (ev) => {
  const b = ev.target.closest?.("[data-tab-more]");
  if (!b) return;
  const tab = b.dataset.tabMore;
  if (tabOpen.has(tab)) tabOpen.delete(tab);
  else tabOpen.add(tab);
  drawn.rev = -2;
  renderTabHead(player.state, tab);
});

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
        const name = variantName(x.f, own.map((y) => y.f), DEV);
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
  // one row of buttons per analysis (its tool's title is on each button when there are several),
  // in a box that scrolls when the list is longer than the room under the toggles (fitOverlays)
  const nButtons = ct.variants.reduce((n, g) => n + g.buttons.length, 0);
  const chips =
    kindRow +
    (nButtons > 1
      ? `<div class="fields">${ct.variants
          .map((g) => `<div class="kinds variants" data-analysis="${esc(g.ana)}">${g.buttons.map((v) => `<button data-variant="${esc(v.key)}" class="${v.on ? "on" : ""}" title="${esc(v.name)}">${esc(v.name)}</button>`).join("")}</div>`)
          .join("")}</div>`
      : "");
  const acts =
    zoomAct +
    (ct.canCompare ? `<button data-act="compare" class="${ct.compare ? "on" : ""}">${ct.compare ? "比べるのをやめる" : "1 つ前の設計と比べる"}</button>` : "");
  const mesh = ct.mesh ? `・${DEV || VIDEO_LAYOUT ? `${esc(ct.mesh.rec.mesh_id)} ` : "メッシュ "}(要素 ${ct.mesh.rec.elements.length})` : "";
  const title = relative
    ? `${esc(fieldLabel(ct.field, DEV))}${ct.frequency ? ` ${fmt(ct.frequency, 4)} Hz` : ""}<br><small>動きの大きさ (相対)・揺れは見せるための速さ</small>`
    : `${ct.reference ? `${esc(JUDGED_JA.refBar)}: ` : ""}${esc(fieldLabel(ct.field, DEV))} [${esc(unit || "1")}]`;
  // before / after: each version's largest recorded value on the one scale above
  const cmp = ct.compare
    ? `<div class="cmp" data-compare="${ct.compare.version}-${ct.compare.nowVersion}">` +
      `<div>${verLabel(ct.compare.version)} (左): 最大 ${esc(fmtVU(ct.compare.before.fieldMax, ct.field.unit))}</div>` +
      `<div>${verLabel(ct.compare.nowVersion)} (右): 最大 ${esc(fmtVU(ct.fieldMax, ct.field.unit))}</div></div>`
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
    `<div class="src">色の元: ${DEV || VIDEO_LAYOUT ? `${esc(ct.analysisId)}・` : ""}${esc(DEV || VIDEO_LAYOUT ? ct.toolTitle ?? "" : plainWords(ct.toolTitle ?? ""))}${mesh}${ct.limitKnown ? `・上端 = 許容 (${esc(ct.crit.basis)})` : relative ? "" : ct.compare ? "・上端 = 2 つの設計の最大値" : "・上端 = 最大値"}</div>`;
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
      // sky: as the right view's (bench, above): without it the left view's ground stops at 20 m
      // and its clear colour shows above as a flat band, where the right view has the sky
      cmpView = { el, tags, bench: new Workbench3D(el, look, { quality, sky: true }) };
      window.hiraku.cmpBench = cmpView.bench;
    }
    const b = cmpView.bench;
    b.setDesign(s.designs[c.ana.design_version] ?? null);
    b.setLoading(chain.loadCase?.params?.loading ?? null);
    b.setScene(chain.loadCase, c.ana);
    b.setContour(c.before);
    b.setToggles(ui.toggles);
    cmpView.tags.innerHTML = `<span data-kind="label" data-version="${c.version}">${verLabel(c.version)}</span><span data-kind="label" data-version="${c.nowVersion}">${verLabel(c.nowVersion)}</span>`;
  }
  cmpView?.tags && (cmpView.tags.hidden = !c);
  // The right view keeps clear the strip under the legend (from the legend's left edge to
  // the viewport's right, and a gap), so its vehicle is framed in the rest of its half; the
  // left view looks through the same camera, so its vehicle sits the same way in its half.
  keepLegendRoom(was);
}

// One view too: the vehicle is framed left of the legend, not under it (in steps of
// LEGEND_ROOM_STEP_PX, so a legend a few px wider does not move the camera). Also each frame (the
// heat's legend comes after the record is drawn). was: the view was in halves before (setCompare).
function keepLegendRoom(was = null) {
  const c = viewport.classList.contains("compare");
  if (was === null) was = c;
  // (the colour legend, or the heat's legend in its place: the legends in the right half of the view)
  let legendLeft = Infinity;
  for (const e of viewport.querySelectorAll(':scope > [data-kind="legend"]'))
    if (!e.hidden && e.offsetWidth && getComputedStyle(e).display !== "none" && e.offsetLeft > viewport.clientWidth / 2) legendLeft = Math.min(legendLeft, e.offsetLeft);
  const legendRoom = legendLeft < Infinity ? Math.ceil((viewport.clientWidth - legendLeft + LEGEND_GAP_PX) / LEGEND_ROOM_STEP_PX) * LEGEND_ROOM_STEP_PX : 0;
  // (the present mode and the video (?video=1): framed as before, the legend's strip in the compare
  // view only; in the present mode the legend is beside the view; grip: the tyre panel's strip)
  const lg = $("legend");
  const room = ui.present || VIDEO_LAYOUT
    ? c && !lg.hidden && lg.parentElement === viewport ? viewport.clientWidth - lg.offsetLeft + LEGEND_GAP_PX : c ? 0 : grip.reserved
    : c ? legendRoom : Math.max(legendRoom, grip.reserved ?? 0);
  bench.fitWidth = !ui.present && !VIDEO_LAYOUT;
  if (was !== c || room !== (bench.reserveRight ?? 0)) {
    if (was !== c) resize3d();
    bench.setReserve(room);
    // the half-wide (or whole) view: frame the vehicle again, or the focus for the new shape
    if (bench.design && !bench.focused) bench.fitPoints(bench.contentPoints(), VIEWS[bench.view ?? "default"], 1.08);
    bench.focusKey = undefined;
  }
}
const LEGEND_GAP_PX = 12;
const LEGEND_ROOM_STEP_PX = 40;

// The boxes over the 3D keep apart (tests/web/test_overlay_fit_browser.py), in the stage's px:
// - the colour legend starts under the toggles' row and the full-screen button; its list of
//   fields scrolls inside it (the chosen one scrolled into sight), the colour bar stays;
// - the いま: line keeps left of the toggles (their row grows with the switches a design offers);
//   with too little room there, on a row of its own under them. Where it would reach the legend
//   it stops short of it, on one line, cut (the whole sentence is its title);
// - the cause note keeps left of the legend and the tyre panel, and goes up above the graph at the
//   bottom left when the two would cross (fitCauseNote).
// Each style is written only when it changes (this runs on every frame).
const setStyle = (el, key, v) => {
  if (el.style[key] !== v) el.style[key] = v;
};
function fitOverlays() {
  const lg = $("legend");
  const tg = $("toggles");
  const ex = $("expand-btn");
  const lgIn = !lg.hidden && lg.parentElement === viewport;
  if (lgIn) {
    let top = 10;
    for (const e of [tg, ex]) if (e.offsetParent === viewport && e.offsetWidth) top = Math.max(top, e.offsetTop + e.offsetHeight + 8);
    const bottom = parseFloat(getComputedStyle(lg).bottom) || 0;
    setStyle(lg, "maxHeight", `${Math.max(120, viewport.clientHeight - bottom - top)}px`);
    // the line of where the colours come from is the first to give way when the legend does not fit
    // (its words then the legend's tooltip), so no line is cut by the legend's frame (the film as before)
    const src = lg.querySelector(":scope > .src");
    if (src && !VIDEO_LAYOUT) {
      if (src.hidden) src.hidden = false;
      lg.classList.remove("squeeze");
      // (measured without the list's minimum of the last fit: with it the column shrinks the list below its
      // own height, the legend seems to fit, and the list then comes back past the frame; 1280 x 720)
      const fl = lg.querySelector(".fields");
      if (fl) setStyle(fl, "minHeight", "");
      const tight = lg.scrollHeight > lg.clientHeight + 1;
      src.hidden = tight;
      const tip = tight ? src.textContent : "";
      if (lg.title !== tip) lg.title = tip;
      // still too tall without it: the list of fields is the next to give way (it scrolls, app.css .squeeze)
      if (tight && lg.scrollHeight > lg.clientHeight + 1) lg.classList.add("squeeze");
    }
    const f = lg.querySelector(".fields");
    const on = f?.querySelector("button.on");
    // squeezed, the list keeps the height of the chosen button at least (52: app.css .squeeze's most), so it
    // can be scrolled into sight whole (160725 at 1440 x 900: the list had 19 px for a 21 px button)
    if (f) setStyle(f, "minHeight", on && lg.classList.contains("squeeze") ? `${Math.min(52, on.offsetHeight + 2)}px` : "");
    // (but never past the legend's frame: what the minimum adds over the frame comes off it again; 1280 x 720)
    if (f && f.style.minHeight && lg.scrollHeight > lg.clientHeight + 1) {
      const over = lg.scrollHeight - lg.clientHeight;
      setStyle(f, "minHeight", `${Math.max(0, Math.floor(parseFloat(f.style.minHeight) - over))}px`);
    }
    // (again when the box's height changes: squeezed after the first scroll, the button was left out of sight)
    const shownKey = on ? `${on.dataset.variant}:${f.clientHeight}` : "";
    if (f && on && f.dataset.shown !== shownKey) {
      f.dataset.shown = shownKey;
      if (f.scrollHeight > f.clientHeight) f.scrollTop = Math.max(0, on.offsetTop - f.offsetTop - (f.clientHeight - on.offsetHeight) / 2);
    }
  } else setStyle(lg, "maxHeight", "");
  // the mesh's list (above the legend, app.css .meshcard) ends above the legend's top, whatever the
  // legend's height (its Japanese title may take another line): the list scrolls instead
  if (meshCard && !meshCard.hidden) {
    const room = lgIn && lg.offsetWidth ? lg.offsetTop - meshCard.offsetTop - 6 : null;
    // (in px, as the browser keeps it: setStyle compares the string; 520: the CSS's calc(100% - 520px))
    setStyle(meshCard, "maxHeight", room != null ? `${Math.max(0, Math.floor(Math.min(viewport.clientHeight - 520, room)))}px` : "");
  }

  const nowEl = $("now");
  if (!nowEl.hidden && tg.offsetWidth && nowEl.parentElement === viewport) {
    const room = tg.offsetLeft - nowEl.offsetLeft - 12;
    const under = room < 360;
    // (under: below the toggles and the full-screen button, which is the taller; 10: .now's own top)
    const exBottom = ex.offsetParent === viewport ? ex.offsetTop + ex.offsetHeight : 0;
    const top = under ? Math.max(tg.offsetTop + tg.offsetHeight, exBottom) + 8 : 10;
    let width = under ? viewport.clientWidth - 2 * nowEl.offsetLeft : room;
    // the legend beside it (across its width, and within two lines' height below its top)
    const beside = lgIn && lg.offsetLeft < nowEl.offsetLeft + width + 12 && lg.offsetTop < top + 80;
    if (beside) width = Math.min(width, lg.offsetLeft - nowEl.offsetLeft - 12);
    setStyle(nowEl, "top", under ? `${top}px` : "");
    setStyle(nowEl, "maxWidth", `${width}px`);
    nowEl.classList.toggle("one-line", beside);
  }

  // the present card: what does not fit is left out whole, never cut in half by the card's frame:
  // the reference values first (last first), then the note, then the extra lines (last first)
  const pc = $("present-card");
  if (ui.present && pc && pc.scrollHeight > pc.clientHeight + 1) {
    const order = [...pc.querySelectorAll(".pc-ref li")].reverse().concat([...pc.querySelectorAll(".pc-note")], [...pc.querySelectorAll(".pc-extra li")].reverse());
    for (const e of order) {
      if (pc.scrollHeight <= pc.clientHeight + 1) break;
      e.hidden = true;
      const list = e.closest(".pc-ref, .pc-extra");
      if (list && !list.querySelector("li:not([hidden])")) list.hidden = true;
    }
  }

  fitCauseNote(lgIn ? lg : null);
}

// The cause note (centred at the bottom of the 3D) keeps clear of the boxes at the right of the
// 3D (the colour legend, the tyre panel): narrower and moved left so that it ends a gap short of
// them; and of the graph at the bottom left: lifted above it when the two would cross. It is
// first put back where it stands by itself, then moved in the same frame (no paint between).
const NOTE_GAP_PX = 12;
function fitCauseNote(lg) {
  const cn = $("causenote");
  for (const k of ["bottom", "left", "maxWidth"]) setStyle(cn, k, "");
  if (cn.hidden || cn.parentElement !== viewport) return;
  const shown = (e) => e && !e.hidden && e.parentElement === viewport && e.offsetWidth > 0;
  const W = viewport.clientWidth;
  const bottom = () => cn.offsetTop + cn.offsetHeight;
  // the right-hand boxes that reach down beside it (it only ever moves up)
  let right = W - 20;
  for (const s of [lg, viewport.querySelector(".grip-panel")]) if (shown(s) && s.offsetTop < bottom()) right = Math.min(right, s.offsetLeft - NOTE_GAP_PX);
  if (cn.offsetLeft + cn.offsetWidth / 2 > right) {
    // (translateX(-50%): offsetLeft is its centre)
    const max = parseFloat(getComputedStyle(cn).maxWidth) || W;
    setStyle(cn, "maxWidth", `${Math.max(240, Math.min(max, right - 20))}px`);
    setStyle(cn, "left", `${Math.max(20 + cn.offsetWidth / 2, Math.min(W / 2, right - cn.offsetWidth / 2))}px`);
  }
  const gr = $("graph");
  if (shown(gr)) {
    const left = cn.offsetLeft - cn.offsetWidth / 2;
    const cross = left < gr.offsetLeft + gr.offsetWidth && gr.offsetLeft < left + cn.offsetWidth && bottom() > gr.offsetTop; // (beside it or under it: above it)
    if (cross) setStyle(cn, "bottom", `${viewport.clientHeight - gr.offsetTop + 8}px`);
  }
}

// A motion's wheel lift as a passed limit (not on a step, where climbing lifts a wheel).
const liftLimit = (motion, lc) => motion?.liftT != null && liftIsLimit(lc);

// graphs: the analysis has series to chart without a motion (series against x).
function renderToggles(motion, lc, graphs = false, design = null, heatAvail = false) {
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
    (hasGrip(motion) ? btn("grip", "タイヤ", t.grip) : "") + // grip:
    (heatAvail ? btn("heat", "熱の流れ", t.heat) : "") + // heat:
    (liftLimit(motion, lc) ? btn("limit", "限界の重ね", t.limit) : "") +
    (motion || graphs ? btn("graph", "グラフ", t.graph) : "");
}

// ---- the test's motion (series) -------------------------------------------
let motionMemo = { ana: null, motion: null };
function motionFor(ana) {
  if (ana !== motionMemo.ana || heavyPending(ana)) motionMemo = { ana, motion: ana && ana.status === "ok" ? motionOf(ana) : null };
  return motionMemo.motion;
}

// The display seconds the analysis' own event has (the event clock), by analysis id.
let dwellMemo = { events: null, n: -1, map: new Map() };
function analysisDwell(id) {
  if (dwellMemo.events !== player.events || dwellMemo.n !== player.events.length || dwellMemo.clock !== player.clock) {
    const map = new Map();
    for (const e of player.events) if (e.kind === "analysis.finished" && e.data?.analysis_id) map.set(e.data.analysis_id, e.dwell);
    dwellMemo = { events: player.events, n: player.events.length, clock: player.clock, map };
  }
  return dwellMemo.map.get(id) ?? null;
}

// Series seconds per display second: on the event clock the motion fills its analysis' event;
// else at most ANIM_MAX_S of display time. (ana: the analysis shown; its own ▶ plays at the same rate)
function animRate(motion, ana = shownMotion?.ana) {
  if (player.clock === "event" && ana) {
    const d = analysisDwell(ana.analysis_id);
    if (d) return motion.duration / Math.max(0.3, d);
  }
  return Math.max(1, motion.duration / ANIM_MAX_S);
}

// Animation time (the series' own seconds). By default it starts when the
// analysis arrived in the replay and runs at animRate, so a frozen ?t= frame
// always shows the same pose.
function animTau(s, ana, motion) {
  if (animLoop?.covers(ana)) return animLoop.tau(motion);
  if (ui.anim.local && ui.anim.key === ana.analysis_id) return ui.anim.tau;
  const tf = s.tOf[ana.analysis_id] ?? 0;
  return motion.t0 + Math.max(0, Math.min(motion.duration, (player.playhead - tf) * animRate(motion, ana)));
}

const chart = new SeriesChart($("graph"), (key) => {
  ui.graphGroup = key;
});
let shownMotion = null; // { motion, ana, tau } of the last frame, for the bar's handlers

// The channels the graph offers for an analysis: the motion's (against time) and the
// series against another quantity, one Map per analysis (the chart redraws on a new one).
let graphMemo = { ana: null, channels: null };
function graphChannels(ana, motion) {
  if (ana !== graphMemo.ana || heavyPending(ana)) graphMemo = { ana, channels: new Map([...(motion?.channels ?? []), ...xSeriesOf(ana)]) };
  return graphMemo.channels;
}

function renderMotion(s, chain, motion) {
  const bar = $("animbar");
  viewport.classList.toggle("has-anim", !!motion);
  if (!motion) {
    grip.hide(); // grip:
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
  grip.update(!!ui.toggles.grip && !ui.present, ana, motion, kcFor(s, ana), tau); // grip: the same clock as the motion
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
  const rate = animRate(motion, ana);
  $("ab-time").textContent = `${(tau - motion.t0).toFixed(1)} / ${motion.duration.toFixed(1)} 秒${Math.abs(rate - 1) > 0.005 ? ` (×${fmt(rate, 2)})` : ""}`;
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
board.formulaTitle = formulaTitleOf;
// ④'s 3D pictures (tile3d.js): one renderer for the tiles, always in the real look (black rubber
// tyres, opaque parts) whichever look the workbench has
const tileViews = new TileViews("real", () => (drawn.rev = -2));
const TILE_PIC_WAIT_MS = 150;
let tilePicTimer = null;

// ④'s 「直して OK になった試験」 (tiles.js .vstory) on one line: the tests that do not fit are left out
// and named by 「ほか n」 at the end (their lines in its title). Not on the film (its ④ as before).
function fitStory() {
  const ul = $("grid-head").querySelector(".vstory");
  if (!ul || VIDEO_LAYOUT) return;
  ul.querySelector(".vmore")?.remove();
  const items = [...ul.children];
  for (const li of items) li.hidden = false;
  const room = ul.clientWidth - (parseFloat(getComputedStyle(ul).paddingRight) || 0);
  if (!room) return; // (not laid out: ④ not shown)
  const out = (li) => li.offsetLeft + li.offsetWidth > room;
  let hidden = items.filter(out);
  if (!hidden.length) return;
  for (const li of hidden) li.hidden = true;
  const more = document.createElement("li");
  more.className = "vmore";
  ul.appendChild(more);
  for (;;) {
    more.textContent = `ほか ${hidden.length}`;
    more.title = hidden.map((li) => li.textContent).join("\n");
    const shown = items.filter((li) => !li.hidden);
    if (!out(more) || shown.length <= 1) break;
    const last = shown[shown.length - 1];
    last.hidden = true;
    hidden = [last, ...hidden];
  }
}
window.addEventListener("resize", () => fitStory());

// A6: the tiles, then a banner when the run stopped at its round limit with reds left.
function renderGrid(s) {
  const { reds, atLimit } = board.render(s, agentPets ? { has: (id) => agentPets.has(id), order: agentOrder(s) } : null);
  fitStory();
  // the pictures kept are copied at once; new ones are drawn once the grid has settled and the
  // page has loaded, one a task (a record loading renders the grid at each event, and a picture
  // costs frames of the software renderer)
  // (a frame fixed at t, ?t= or a seek as the video's export does: all of them now, so the frame
  // filmed has its pictures whichever redraw came last)
  clearTimeout(tilePicTimer);
  tilePicTimer = null;
  if (!board.drawPictures(tileViews, (window.devicePixelRatio || 1) * stageScale, ui.frozen ? Infinity : 0)) {
    const step = () => {
      const loaded = document.readyState === "complete";
      tilePicTimer = loaded && board.drawPictures(tileViews, (window.devicePixelRatio || 1) * stageScale, 1) ? null : setTimeout(step, loaded ? 0 : TILE_PIC_WAIT_MS);
    };
    tilePicTimer = setTimeout(step, TILE_PIC_WAIT_MS);
  }
  agentPets?.mount($("view-grid"), "grid");
  // the fixed ④: a tile (or a chip of a domain not picked) with an analysis opens it in the viewer
  // (pressed: openGridAnalysis), the version's boxes in the head pick the version shown
  if (GRID_FIXED) {
    for (const el of $("tiles").querySelectorAll(".tile[data-domain], .offchip[data-domain]")) {
      const ana = tileAnalysis(s, el.dataset.domain);
      el.classList.toggle("can-open", !!ana);
      if (ana) el.title = `押すと、${DOMAIN_JA[el.dataset.domain] ?? el.dataset.domain}の解析 (${ana.tool}) をビューアで見ます`;
    }
    for (const b of $("grid-head").querySelectorAll(".vbox[data-ver]")) b.classList.add("pick");
    showGridNote();
    showGridExtras(s);
  }
  // a tile (or a chip of a domain not picked) whose domain has analysed something by now can
  // replay it in the tab (pressed: openDomain)
  const spans = GRID_FIXED ? {} : spansNow();
  for (const el of $("tiles").querySelectorAll(".tile[data-domain], .offchip[data-domain]")) {
    const sp = spans[el.dataset.domain];
    const ok = !!sp && sp.t0 <= player.playhead + 1e-6;
    el.classList.toggle("can-replay", ok);
    if (ok) {
      el.title = `押すと、${DOMAIN_JA[el.dataset.domain] ?? el.dataset.domain}の解析 (予測 → 解析 → 比べる → 判定) をここで再生します`;
      if (el.classList.contains("offchip")) el.insertAdjacentHTML("beforeend", ` <span class="replay-tag" data-role="replay">▶</span>`);
      else el.querySelector(".th h3")?.insertAdjacentHTML("afterend", `<span class="replay-tag" data-role="replay">▶ 解析を再生</span>`);
    }
  }
  const banner = $("grid-banner");
  banner.hidden = !(atLimit && reds.length);
  if (!banner.hidden) {
    // stop_code says why in words; a record from before stop_code only has stop_reason
    // (round_limit / cost_limit, or the Claude runner's English "cost limit ..." / "turn limit ...").
    const code = s.stopCode ?? (/^cost limit\b/.test(s.stopReason ?? "") ? "cost_limit" : /^turn limit\b/.test(s.stopReason ?? "") ? "turn_limit" : s.stopReason);
    const words = code === "round_limit" ? `繰り返しの上限 (${s.project?.round_limit ?? "—"} 回) に達した` : STOP_CODE_JA[code];
    const why = words ?? (s.stopReason ? `止まった理由: ${esc(judgedWords(s.stopReason))}` : `繰り返しの上限 (${s.project?.round_limit ?? "—"} 回)`);
    banner.innerHTML = `<b>${why}。NG が ${reds.length} つ残っている: ${reds.map((d) => esc(DOMAIN_JA[d])).join("・")}</b><span>${
      // 「▶ その場で走らせる」 is at the top right only under ?dev=1 with a server (renderTopbar)
      DEV && !STATIC
        ? "新しく走らせるときは、右上の「その場で走らせる」から"
        : `分野を押すと、その解析をビューアで見られます${STATIC ? "。「▶ 再生して見る」で、最初の設計から追えます" : ""}`
    }</span>`;
  }
  // the newest report's next changes (none written yet, or no next: no block)
  const nx = $("report-next");
  // (the design numbers' paths said by their names, of the report's version; the paths under ?dev=1)
  // (folded to one line for the judges; open as before under ?dev=1 and on the film)
  const html = reportNextHtml(s.report, { design: s.designs[s.report?.design_version] ?? s.designs[s.currentVersion] ?? null, dev: DEV, cases: s.project?.load_cases, plain: !DEV && !VIDEO_LAYOUT, fold: !VIDEO_LAYOUT && !DEV });
  if (nx.dataset.html !== html) {
    nx.dataset.html = html;
    nx.innerHTML = html;
    keepOpen(nx);
  }
  nx.hidden = !html;
}

// A record that stopped before its first design: over the 3D (which has no vehicle to show), why it
// stopped (labels.js stopWhyJa, from the record's run.finished). Not on the film.
function renderStoppedNote(s) {
  let el = document.getElementById("stopped-note");
  const why =
    !VIDEO_LAYOUT && s.currentVersion == null && (s.status === "stopped" || s.status === "failed")
      ? stopWhyJa({ finished: true, end_status: s.status, stop_code: s.stopCode, stop_reason: s.stopReason, stop_after: s.project?.stop_after, last_version: null })
      : "";
  if (!why) {
    if (el) el.hidden = true;
    return;
  }
  if (!el) {
    el = document.createElement("div");
    el.id = "stopped-note";
    el.className = "stopped-note";
    el.setAttribute("role", "status");
    viewport.append(el);
  }
  el.hidden = false;
  if (el.textContent !== why) el.textContent = why;
}

// ① opened at the request's moment and still there (ui.moment): no footer line (what an agent did
// last belongs to a later moment of the run, not to the request)
const atMoment = () => ui.moment !== null && !player.playing && Math.abs(player.playhead - ui.moment) < 1e-6;
function renderStatus(s) {
  const act = atMoment() ? null : s.activity;
  // short (hookline.js), in plain words (plain.js); the line as recorded under the pointer
  const line = act ? footerActivity(s, { dev: DEV }) : "";
  const el = $("activity");
  const html = act ? `<span class="dot"></span>${plainLine(s, line)}` : "";
  if (el.dataset.html !== html) {
    el.dataset.html = html;
    el.innerHTML = html;
  }
  if (act && !isFilmWords() && plainHid(s, line)) el.title = line;
  else el.removeAttribute("title");
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
  // the rules' ids (HK1 ...) and keys only under ?dev=1 (and in the film); else the rules in words
  const ids = DEV || VIDEO_LAYOUT;
  const html = (ids ? `hooks: ` : `守るための決まり (${RULES.length} つ): `) +
    RULES.map((r) => `<span class="rule ${recent[r.id] ? "hit" : ""} ${counts[r.id] && !open[r.id] ? "done" : ""}"${ids ? ` title="${esc(r.key)}"` : ""}>${ids ? `<span class="hk">${r.id}</span> ` : ""}${esc(r.ja)}${state(r)}</span>`).join("");
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
    b.innerHTML = `<div class="who"><span class="pet" style="--pet:${petColor(said.agent)}"></span>${esc(agentLongName(said.agent))}</div><div class="tx">${markRecordWords(said.text)}</div>`;
  }
  // the hooks' stop as ③'s strip card (benchnote.js; over the other tabs the stage's line, hookline.js)
  const den = s.denied;
  const hn = $("hooknote");
  hn.hidden = !den;
  if (den && hn.dataset.seq !== String(den.seq)) {
    hn.dataset.seq = String(den.seq);
    hn.innerHTML = `<span class="hn-head">hooks が止めた</span> ${esc(hookLineText(den.rule, { dev: DEV }))}`;
  }
  // the hooks' stop: one line near the middle of the screen on every tab (hookline.js; the recorded words are in the log)
  // (not over ④ and ⑤: their words fill the stage where the line goes; the film as before)
  hookLine.render(s, playhead, { off: !VIDEO_LAYOUT && ["grid", "A7"].includes(currentTab(s)) });
  renderStoppedNote(s);
  // the cause of a mismatch Claude wrote, until a verdict of its load case is OK (present.js openCause)
  // (?off=cause: not shown, e.g. a shot where the card would cover the vehicle)
  const cause = CAUSE_OFF ? null : openCause(s);
  const cn = $("causenote");
  cn.hidden = !cause;
  if (cause && cn.dataset.key !== cause.key) {
    cn.dataset.key = cause.key;
    cn.innerHTML = causeNoteHtml(cause);
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
// The bar's range: the whole replay, or ④'s open domain's span (its first prediction to its last comparison).
const barRange = (span) => (span ? { lo: span.t0, hi: span.t1 } : { lo: 0, hi: player.duration });
function renderPlaybar(span = null) {
  const { lo, hi } = barRange(span);
  const dur = Math.max(hi - lo, 1e-6);
  const f = Math.max(0, Math.min(1, (player.playhead - lo) / dur));
  $("pb-fill").style.width = `${f * 100}%`;
  $("pb-knob").style.left = `${f * 100}%`;
  $("pb-time").textContent = `${fmtTime(Math.max(0, player.playhead - lo))} / ${fmtTime(hi - lo)} 秒`;
  $("playbar").classList.toggle("domain", !!span);
  $("playbar").dataset.domain = span?.domain ?? "";
  $("pb-play").textContent = player.playing ? "❚❚" : "▶";
  $("pb-speed").textContent = `${player.speed}×`;
  const live = $("pb-live");
  live.classList.toggle("show", player.live);
  live.classList.toggle("on", player.live && player.liveAttached);
  // the recorded time (from the run's start) of the event on screen, small beside the bar's own
  const at = player.applied > 0 ? player.events[player.applied - 1] : null;
  $("pb-rec").textContent = player.clock === "real" || !at ? "" : `実時間 ${clockText(at.t_rec)}`;
  showClock();
  $("pb-next-verdict").disabled = !player.nextWhere(isVerdictEv);
  $("pb-next-mismatch").disabled = !player.nextWhere(isMismatchEv);
  const key = `${player.events.length}|${player.duration.toFixed(1)}|${span?.domain ?? ""}|${player.clock}`;
  if (key !== tickKey) {
    tickKey = key;
    // (a domain's bar: the marks of its own events only)
    const lcs = span ? new Set(span.marks.map((m) => m.lc)) : null;
    const inBar = (e) => e.t >= lo - 1e-9 && e.t <= hi + 1e-9 && (!lcs || !e.data?.load_case_id || lcs.has(e.data.load_case_id));
    const pos = (e) => `left:${(((e.t - lo) / dur) * 100).toFixed(3)}%`;
    const marks = [];
    for (const e of player.events) {
      if (!inBar(e)) continue;
      const kind = markKind(e);
      if (!kind) continue;
      marks.push(`<i class="m-${kind}" style="${pos(e)}" title="${esc(MARK_JA[kind])}"></i>`);
      if (kind === "v") {
        // the version's name beside its mark (its own box: right of the mark, left of it near the end)
        const f = (e.t - lo) / dur;
        const at = f > 0.9 ? `right:calc(${((1 - f) * 100).toFixed(3)}% + 3px)` : `left:calc(${(f * 100).toFixed(3)}% + 3px)`;
        marks.push(`<b class="m-vl" style="${at}">${esc(verLabel(e.data?.design_version ?? ""))}</b>`);
      }
    }
    $("pb-marks").innerHTML = marks.join("");
    $("pb-ticks").innerHTML = player.events
      .filter((e) => e.kind.startsWith("hook.") && inBar(e))
      .map((e) => `<i class="hook" style="${pos(e)}"></i>`)
      .join("");
  }
}

// The kinds the bar marks (above the track, one row each), in the order a run goes through them.
const MARK_JA = { ana: "解析", cmp: "比べる (予想通り)", ok: "判定 OK", ng: "判定 NG", mis: "不一致", cause: "原因", v: "次の v" };
const isVerdictEv = (e) => e.kind === "verdict.recorded";
const isMismatchEv = (e) => e.kind === "comparison.recorded" && e.data?.status === "mismatch";
function markKind(e) {
  if (e.kind === "analysis.finished") return "ana";
  if (e.kind === "comparison.recorded") return e.data?.status === "mismatch" ? "mis" : "cmp";
  if (e.kind === "verdict.recorded") return e.data?.pass === false ? "ng" : "ok";
  if (e.kind === "cause.recorded") return "cause";
  if (e.kind === "design.created") return "v";
  return null;
}
// seconds from the run's start as h:mm:ss (m:ss under an hour)
function clockText(s) {
  const n = Math.max(0, Math.floor(s ?? 0));
  const h = Math.floor(n / 3600);
  const mm = String(Math.floor((n % 3600) / 60)).padStart(h ? 2 : 1, "0");
  const ss = String(n % 60).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function seekFromPointer(ev) {
  const rect = $("pb-track").getBoundingClientRect();
  const f = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width));
  const { lo, hi } = barRange(openSpan(currentTab(player.state)));
  player.seek(lo + f * (hi - lo));
}

// ---- the present mode -----------------------------------------------------------
// The 3D made large: the corner button (#expand-btn: corners out to make it large, in to go back)
// or Esc; ?present=1 opens the page so (the films and the tests use it).
const EXPAND_SVG = {
  out: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>`,
  in: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5"/></svg>`,
};
function setPresent(on) {
  ui.present = on;
  if (!on) ui.detail = false;
  applyPresent();
}
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
  const exp = $("expand-btn");
  exp.innerHTML = on ? EXPAND_SVG.in : EXPAND_SVG.out;
  exp.title = on ? "元の大きさに戻す (Esc)" : "大きく見る";
  exp.setAttribute("aria-label", exp.title);
  exp.setAttribute("aria-pressed", String(on));
  $("detail-btn").textContent = ui.detail ? "たたむ" : "詳しく";
  $("detail-btn").classList.toggle("on", ui.detail);
  if (on && ui.detail) $("chain-more").open = true; // 詳しく asked for the contents: the cards open
  // the arrows' words: only where chosen (pressed or pointed at), except the present mode (its one
  // key label, as the filming has it) and ?words=1
  bench.setAutoWords(on || WORDS_ALL);
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
// The views that draw a mesh, a field or a motion; on the others (① ② ⑤ and the shelf) the heavy
// parts of a static export are not asked for (heavy.js quietly).
function drawsHeavy(tab) {
  return ui.present || tab === "bench" || tab === "grid" || tab === "A7";
}
function draw() {
  const tab = currentTab(player.state);
  if (drawsHeavy(tab)) drawNow();
  else quietly(drawNow);
  heavyNote(drawsHeavy(tab) ? viewOf(tab, openSpan(tab)) : null);
}

// A static export's heavy parts asked for and not read yet: one line on the 3D (or the grid).
function heavyNote(view) {
  const n = view === "bench" || view === "grid" || view === "A7" ? heavyWaiting() : 0;
  let el = $("heavy-note");
  if (!n) {
    if (el) el.hidden = true;
    return;
  }
  if (!el) {
    el = document.createElement("div");
    el.id = "heavy-note";
    el.className = "heavy-note";
    document.body.append(el);
  }
  el.hidden = false;
  const text = `解析の結果を読み込んでいます (残り ${n})`;
  if (el.textContent !== text) el.textContent = text;
}

// A static export: does what fn reads have parts not read yet? They are asked for (first); the
// caller draws it once they are in (onHeavy).
function waitsForHeavy(fn) {
  const [, recs] = tracked(fn);
  const pending = [...recs].filter(heavyPending);
  for (const d of pending) askAll(d, true);
  return pending.length > 0;
}

// A heavy part read: what was drawn from it before (nothing yet) is drawn again.
onHeavy(() => {
  motionMemo = { ana: null, motion: null };
  graphMemo = { ana: null, channels: null };
  contour = null;
  if (board) board.key = null;
  gridDrawnKey = null;
  drawn.rev = -1;
});

// A replay coming to events whose heavy parts are not read: they are asked for PREFETCH_S ahead,
// and the replay waits at an event until its parts are in (so it shows as with the whole record).
const PREFETCH_S = 15;
function holdForHeavy(dt) {
  if (!STATIC || !player.playing || !drawsHeavy(currentTab(player.state))) return false;
  const ahead = [];
  for (let i = player.applied; i < player.events.length && player.events[i].t <= player.playhead + PREFETCH_S; i++) ahead.push(player.events[i]);
  prefetch(ahead);
  const next = player.events[player.applied];
  if (!next || heavyReady(next) || next.t > player.playhead + dt * player.speed + 1e-9) return false;
  prefetch([next], true);
  return true;
}

function drawNow() {
  const s = player.state;
  const tab = currentTab(s);
  // ④ with a domain open: the 3D and the cards of that domain's load case at the playhead (or the one
  // picked there), apart from ③'s own pick
  const span = openSpan(tab);
  const view = viewOf(tab, span);
  const loopChain = animLoop?.chain(s, chainFor) ?? null; // play=loop: that analysis' load case and version
  const lcId = loopChain ? loopChain.shown.load_case_id : span ? ui.gridLc ?? domainLcAt(span, player.playhead) : selectedLc(s);
  const togglesKey = JSON.stringify(ui.toggles);
  const chain = loopChain ?? chainFor(s, lcId, s.currentVersion);
  const motion = motionFor(chain.shown);
  if (resolvedMemo.rev !== s.rev || resolvedMemo.s !== s) resolvedMemo = { rev: s.rev, s, set: resolvedHooks(s) };
  const resolved = resolvedMemo.set;
  // heat: the energy's flow (a thermal analysis shown, or ?on=heat); it takes the contour's place
  const heatIn = tab === "bench" ? heat.inputs(s, chain.shown, s.designs[chain.pred?.design_version ?? s.currentVersion] ?? null, HEAT_FORCED) : null;
  const heatOn = !!heatIn && ui.toggles.heat;
  const over = runOver(); // (the top's words and the stop button change when the followed run ends)
  // ④ as the run ended (GRID_FIXED): the whole record at the version picked, not the playhead's state
  const gs = GRID_FIXED && view === "grid" ? gridState() : null;
  if (!gs) gridDrawnKey = null;
  if ((gs && gridKey(gs) !== gridDrawnKey) || s.rev !== drawn.rev || lcId !== drawn.lc || tab !== drawn.tab || togglesKey !== drawn.toggles || ui.present !== drawn.present || over !== drawn.over || view !== drawn.view || (span?.domain ?? null) !== drawn.dom) {
    // (④ replaying a domain: the top row keeps the whole run's version and status, and says in a line
    // which domain plays at which version; the video's frames as before)
    if (span && !VIDEO_LAYOUT) renderTop(wholeState(), { domain: span.domain, version: s.currentVersion });
    else renderTop(gs ? wholeState() : s);
    // (the chips ring ③'s load case on ③ only; on ④ the domain open there, else none)
    renderChips(gs ?? s, view === "bench" ? lcId : null, span?.domain);
    // the side panels and the 3D only while the workbench shows (on ① ②, ④'s grid, the shelf and A7
    // they are hidden; showing the workbench again draws everything, the tab or view having changed)
    const benchShown = view === "bench" || ui.present;
    if (benchShown) {
      renderAgents(s);
      renderLog(s, resolved);
      renderTests(s, lcId, span);
      renderChain(s, chain);
    }
    renderDomainHead(span);
    renderStatus(s);
    if (benchShown) renderToggles(motion, chain.loadCase, chain.shown?.status === "ok" && xSeriesOf(chain.shown).size > 0, s.designs[chain.pred?.design_version ?? s.currentVersion] ?? null, !!heatIn);
    const intakeTab = INTAKE_TABS.includes(tab);
    $("view-bench").hidden = view !== "bench";
    $("view-grid").hidden = view !== "grid";
    $("view-intake").hidden = !intakeTab;
    document.body.dataset.intakeTab = intakeTab ? tab : ""; // css: no present card over A1 / A2 in the video
    $("view-shelf").hidden = tab !== "A4";
    $("view-asm").hidden = tab !== "A7";
    $("main").classList.toggle("wide", view === "grid" || tab === "A4" || tab === "A7" || intakeTab); // A6, A4, A7 and the intake screens use the whole width
    $("main").classList.toggle("domain-replay", !!span);
    // the tab marked is the tab's own (④ stays marked while a domain replays in it)
    for (const b of document.querySelectorAll(".tab")) b.classList.toggle("on", b.dataset.tab === tab);
    markTabs(s);
    // (the fixed ④: drawn again only when the record or the version picked changed, so a replay
    // going on behind it does not redraw the tiles, nor close a くわしく opened)
    if (gs) {
      if (gridKey(gs) !== gridDrawnKey) renderGrid(gs);
      gridDrawnKey = gridKey(gs);
    } else if (view === "grid") renderGrid(s);
    document.body.classList.toggle("grid-fixed", !!gs);
    if (!gs && ui.gridViewer) closeGridViewer();
    if (intakeTab) renderIntake(tab);
    if (tab === "A4") renderShelf();

    // the contour (the present mode shows a shell's before / after side by side when there is an earlier version)
    const contourNow = () => {
      const c = buildContour(s, chain);
      return ui.present && view === "bench" && c?.mesh && c.canCompare && !c.compare ? buildContour(s, chain, true) : c;
    };
    // a static export: the 3D is drawn from the shown analysis and what its contour reads (its meshes,
    // the version before) once every part of them is read (heavy.js); until then the design alone
    let waitHeavy = false;
    let ctReady = null;
    if (benchShown && STATIC) {
      const [c, recs] = tracked(contourNow);
      if (heavyPending(chain.shown)) recs.add(chain.shown);
      const pending = [...recs].filter(heavyPending);
      for (const d of pending) askAll(d, true);
      waitHeavy = pending.length > 0;
      ctReady = c;
    }
    if (benchShown) {
      const design = s.designs[chain.pred?.design_version ?? s.currentVersion] ?? null;
      bench.setDesign(design);
      // the workbench's くわしく: what the real look's dummy is drawn from (hybrid3.js)
      $("bench-more").hidden = !bench.dummyDrawn;
      if ($("bench-dummy-note").textContent !== DUMMY_NOTE_JA) $("bench-dummy-note").textContent = DUMMY_NOTE_JA;
      bench.setLoading(chain.loadCase?.params?.loading ?? null);
    }
    if (benchShown && !waitHeavy) {
      bench.setMotion(motion);
      bench.setScene(chain.loadCase, chain.shown);
      const ct = STATIC ? ctReady : contourNow();
      if ((ct?.key ?? null) !== (contour?.key ?? null)) contour = ct;
      bench.setContour(ui.toggles.contour && !heatOn ? framedContour() : null);
      if (!contour?.mesh?.maxAt) ui.zoomMax = false;
      // (before setCompare, which keeps the legend's strip clear; only where the 3D shows a contour,
      // ③ and ④: not on A7, whose view has no colours, though the present mode draws the workbench under it)
      renderLegend(heatOn || !(view === "bench" || view === "grid") ? null : contour, s, chain.shown, motion);
      renderMeshCard(contour);
      setCompare(s, chain, ui.toggles.contour && !heatOn && view === "bench" ? contour : null);
      bench.setFocus(ui.zoomMax && ui.toggles.contour ? contour : null);
      cmpView?.bench.setCloseUp(!!bench.focused);
      // With a motion the arrows come from the series, frame by frame.
      bench.setVectors(chain.shown?.status === "ok" && !motion ? chain.shown.vectors ?? null : null);
      bench.setToggles(ui.toggles);
      // modules: the module colours and spaces on the 3D, and their legend
      modLayer.set({ colors: ui.toggles.modules, spaces: ui.toggles.spaces });
      renderModuleLegend(viewport, bench.design, { colors: ui.toggles.modules, spaces: ui.toggles.spaces }, modLayer);
      renderJointLegend(viewport, bench.joints, !!ui.toggles.joints && view === "bench"); // joints:
    }
    // what is going on, in one sentence; and the present mode's card for this scene
    const now = withoutRuleIds(nowSentence(s, chain, (tool) => toolTitle(s, tool)), { dev: DEV });
    // (the record's ids and English words in span.rec: ③'s strip leaves them to くわしく)
    // the いま line telling the verdict just recorded (its activity is the verdict's event) on the
    // tool's value after the load path was checked by hand: why it is OK / NG, under it (why_line.js)
    // (as nowSentence: not while an analysis runs, nor once the run has ended)
    const saysActivity = !chain.running && !Object.keys(s.running ?? {}).length && !["done", "stopped", "failed"].includes(s.status);
    const nowVerdict = saysActivity && s.activity ? Object.values(s.verdicts).find((v) => s.firstSeqOf[v.verdict_id] === s.activity.seq) : null;
    const justJudged = nowVerdict ? pathCheckedWhy(s, nowVerdict, formulaTitleOf) : null;
    // (the space before the why: the line's text and its tooltip read the same)
    const nowHtml = markRecordWords(now) + (justJudged ? ` <span class="now-why" data-role="why">${esc(justJudged.text)}</span>` : "");
    if ($("now").innerHTML !== nowHtml) $("now").innerHTML = nowHtml;
    $("now").title = justJudged ? `${now} ${justJudged.text}` : now;   // the whole of what the line says
    $("now").hidden = !now;
    const scene = presentScene({ s, chain, contour, tab: view, toolTitle: (tool) => toolTitle(s, tool), formulaTitle: formulaTitleOf });
    document.body.dataset.presentScene = scene.key;
    if (ui.present) $("present-card").innerHTML = presentCardHtml(scene, now, { dev: DEV });
    drawn = { ...drawn, rev: s.rev, lc: lcId, tab, toggles: togglesKey, present: ui.present, over, view, dom: span?.domain ?? null };
  }
  renderMotion(s, chain, motion);
  const nm = nodeMotionOf(chain.shown); // node_motion: at the replay's time (nodeMotionTau)
  nodeView.set(nm, nm ? nodeMotionAt(nm, nodeMotionTau(nm, player.playhead, s.tOf[nm.analysisId])) : null);
  // modules: the round's proposals (or the version built from them) at the replay's time, under the now line
  const rc = renderRoundCard(viewport, tab === "bench" ? roundCardHtml(s, player.playhead) : "");
  if (!rc.hidden && rc.parentElement === viewport) rc.style.top = `${$("now").hidden || $("now").parentElement !== viewport ? 10 : $("now").offsetTop + $("now").offsetHeight + 8}px`;
  if (contour?.frames && ui.toggles.contour && !heatOn) bench.setContour(framedContour());
  heat.show(heatOn ? heatIn : null, player.playhead); // heat:
  if (view === "bench" && !ui.present && !VIDEO_LAYOUT) keepLegendRoom();
  renderTransient(s, player.playhead, chain, resolved);
  // ③'s workbench (not the present mode): the sentence cards out of the 3D, one at a time
  // (not in the video, ?video=1: its frames are filmed as they were)
  const notesOn = view === "bench" && !ui.present && !VIDEO_LAYOUT;
  document.body.classList.toggle("bench-notes", notesOn);
  benchNote.place(notesOn);
  if (notesOn) benchNote.pick(noteKind(s, { cause: CAUSE_OFF ? null : openCause(s), round: tab === "bench" ? roundSeq(s, player.playhead) : null }));
  fitOverlays();
  if (ZOOM) zoomTransform(stage, stageBase, ZOOM, player.playhead, STAGE_W, STAGE_H); // ?zoom=
  renderPlaybar(span);
  bench.animate(player.playhead, !!chain.running);
  if (view === "bench") {
    animLoop?.aim(bench); // play=loop: ?cam=side stands still beside the step
    bench.render();
    if (cmpView && !cmpView.el.hidden) {
      cmpView.bench.animate(player.playhead, false);
      cmpView.bench.copyCamera(bench);
      cmpView.bench.render();
    }
  }
  else if (view === "grid") board.tick(player.playhead);
  // the pets of the list (the workbench; hidden in the present mode) or of the grid
  // (playing: PET_PAINTS pets a frame, so the 3D keeps its pace; a frame stopped at t: all of them)
  if (agentPets && (view === "bench" || view === "grid")) agentPets.tick(s, player.events, player.playhead, view, ui.frozen ? Infinity : PET_PAINTS);
  if (agentPets && view === "bench") agentPets.tick(s, player.events, player.playhead, "board", ui.frozen ? Infinity : PET_PAINTS); // test_board.js
  // (a static export: once the assembly shown is read)
  if (tab === "A7" && !(STATIC && waitsForHeavy(() => assemblyRecord(s)))) (asmPanel ??= window.hiraku.asm = new AssemblyPanel($("asm-host"))).render(s, asmClock());
  if (INTAKE_TABS.includes(tab)) syncIntakeTime();
  // (② outside the video: the asker's pet beside renderA2When's heading)
  if (agentPets && intake && INTAKE_TABS.includes(tab)) (intakePets ??= new IntakePets(agentPets)).tick(intake, player.events, player.playhead, ui.frozen ? Infinity : PET_PAINTS, VIDEO_LAYOUT || draft ? null : a2When.host);
  if (TAB_HEADS[tab]) renderTabHead(s, tab);
  drawRulePeek(s, INTAKE_TABS.includes(tab));
}

let lastTs = null;
let lastPlayhead = null;
function loop(ts) {
  if (ui.building) {
    // (the state is being built in steps: play)
    lastTs = null;
    requestAnimationFrame(loop);
    return;
  }
  const dt = lastTs === null ? 0 : Math.min(0.25, (ts - lastTs) / 1000);
  lastTs = ts;
  if (!ui.frozen && !holdForHeavy(dt)) {
    player.tick(dt);
    // ④'s domain replay stays in its span: it stops at its last comparison
    const span = openSpan(currentTab(player.state));
    if (span && player.playhead > span.t1 + 1e-9) {
      player.seek(span.t1);
      player.pause();
    } else if (span && player.playhead < span.t0 - 1e-9) player.seek(span.t0);
  }
  // Moving the replay hands the test's motion back to the replay clock.
  if (lastPlayhead !== null && player.playhead !== lastPlayhead) ui.anim.local = false;
  lastPlayhead = player.playhead;
  animLoop?.tick(dt); // (runs only without a fixed clock: ?loop_t=, ?t= or a seek fix it)
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
  // play=loop: t is the loop's clock, the playhead stays where the analysis arrived (null: clock kept)
  if (animLoop?.at != null) {
    animLoop.seekClock(t);
    t = animLoop.at;
  }
  ui.frozen = true;
  player.pause();
  player.seek(t >= player.duration - END_SNAP_S ? player.duration : t);
  // the formulas' titles in (or failed), so the frame at t names them every time (the prediction's
  // title, the path_checked line's 上限: why_line.js)
  await formulasIn;
  draw();
  await nextFrame();
  draw();
  // ④'s grid: the frame at t has its tiles' pictures (renderGrid draws new ones on a timer, one a
  // task, which a frame filmed right after the seek would come before)
  if (!$("view-grid").hidden) {
    clearTimeout(tilePicTimer);
    tilePicTimer = null;
    board.drawPictures(tileViews, (window.devicePixelRatio || 1) * stageScale);
  }
  await nextFrame();
  // the real look's Poly Haven files (sky, ground, wall) still loading: the frame at t once they
  // are in, so a page at ?t= draws the same picture every time (as cut.js waits for them)
  if (await looksReady()) {
    draw();
    await nextFrame();
  }
  document.documentElement.dataset.t = String(player.playhead);
  return player.playhead;
}

// ④ shown: its tile pictures once the real look's files are in (a picture drawn before them is
// drawn again: TileViews' onReady), all at once
async function tilesInTheirLook() {
  if ($("view-grid").hidden) return;
  await looksReady();
  draw();
  clearTimeout(tilePicTimer);
  tilePicTimer = null;
  board.drawPictures(tileViews, (window.devicePixelRatio || 1) * stageScale);
  await nextFrame();
}

let readyResolve;
const ready = new Promise((r) => (readyResolve = r));
window.hiraku = {
  // a new request's steps, for the tests (the screen's own controls call the same)
  request: draft ? { send: (t) => draftSend(t), answer: (a) => draftAnswer(a), start: (m) => draftStart(m), get state() { return draft; } } : null,
  player,
  ready,
  grip, // grip: the tyre view (its frame: grip.frame), for the tests
  seek: seekAndDraw,
  state: () => player.state,
  bench,
  animLoop, // play=loop (anim_loop.js), else null
  modules: modLayer, // modules: what the module layer draws (for the tests)
  heat, // heat: what the energy's flow draws (heat.last, for the tests)
  nodeMotion: nodeView, // node_motion: what the nodes' motion draws (for the tests)
  pets: () => agentPets,
  // Replace what is shown with these events (parsed objects) and stop at t.
  load: (events, t = 0) => {
    feed?.stop();
    resetPlayer();
    addEvents(events);
    animLoop?.findArrival(player.events);
    return seekAndDraw(t);
  },
};

// ---- input ------------------------------------------------------------------
$("pb-play").addEventListener("click", () => {
  ui.frozen = false;
  const span = openSpan(currentTab(player.state));
  // ④'s domain: ▶ at its end plays it again from its first prediction
  if (span && !player.playing && player.playhead >= span.t1 - 1e-6) {
    player.seek(span.t0);
    player.playing = true;
  } else player.toggle();
});
$("pb-speed").addEventListener("click", () => player.cycleSpeed());
// previous / next event, the next verdict, the next mismatch: the replay stops on it
const jumpTo = (go) => {
  ui.frozen = false;
  player.pause();
  go();
};
$("pb-prev").addEventListener("click", () => jumpTo(() => player.step(-1)));
$("pb-next").addEventListener("click", () => jumpTo(() => player.step(1)));
$("pb-next-verdict").addEventListener("click", () => jumpTo(() => player.seekNext(isVerdictEv)));
$("pb-next-mismatch").addEventListener("click", () => jumpTo(() => player.seekNext(isMismatchEv)));
$("pb-clock").addEventListener("click", () => setClock(player.clock === "real" ? "event" : "real"));
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
  // ④'s domain replay keeps its own pick: ③'s stays as it was
  if (openSpan(currentTab(player.state))) ui.gridLc = b.dataset.lc || null;
  else ui.pinnedLc = b.dataset.lc || null;
  drawn.rev = -2;
});
$("tiles").addEventListener("click", (ev) => {
  if (GRID_FIXED) {
    // ④ as the run ended: a test's row opens its analysis, the tile (or a chip) the domain's
    const s = gridState();
    const extra = ev.target.closest('[data-role="viewer-extras"] [data-extra]');
    if (extra) {
      const d = extra.closest(".tile[data-domain]")?.dataset.domain;
      const e = viewerExtras(viewerRunId(s), d).find((x) => x.id === extra.dataset.extra);
      if (e) openGridExtra(s, d, e);
      return;
    }
    const row = ev.target.closest('[data-role="test-row"][data-ana], .num[data-ana]');
    const tile = ev.target.closest(".tile[data-domain], .offchip[data-domain]");
    if (!tile || ev.target.closest("a, summary, [data-role=viewer-none]")) return;
    if (!row && ev.target.closest("details")) return;
    const ana = row ? s.analyses[row.dataset.ana] : tileAnalysis(s, tile.dataset.domain);
    if (ana) openGridAnalysis(s, tile.dataset.domain, ana);
    return;
  }
  const tile = ev.target.closest(".tile.can-replay[data-domain], .offchip.can-replay[data-domain]");
  if (!tile || ev.target.closest("a, details, summary")) return;
  openDomain(tile.dataset.domain);
});
$("view-bench").addEventListener("click", (ev) => {
  if (ev.target.closest("button[data-act=close-domain]")) closeDomain();
});
// the fixed ④: 「← 解析結果の一覧へ」 closes the viewer; a version's box in the head picks it
$("view-grid").addEventListener("click", (ev) => {
  if (ev.target.closest("button[data-act=grid-back]")) closeGridViewer();
});
$("grid-head").addEventListener("click", (ev) => {
  const b = ev.target.closest(".vbox[data-ver]");
  if (!b || !GRID_FIXED) return;
  ui.gridVersion = Number(b.dataset.ver);
  ui.gridNote = null;
  closeGridViewer();
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
  if (b.id === "detail-btn") ui.detail = !ui.detail;
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
// The pointer near an arrow (no button held): that arrow's words while it stays.
viewport.addEventListener("pointermove", (ev) => {
  if (ev.buttons) return;
  const r = bench.renderer.domElement.getBoundingClientRect();
  const on = ev.target === bench.renderer.domElement;
  if (bench.hoverAt(on ? (ev.clientX - r.left) / stageScale : null, (ev.clientY - r.top) / stageScale)) drawn.rev = -2;
});
viewport.addEventListener("pointerleave", () => {
  if (bench.hoverAt(null, null)) drawn.rev = -2;
});
$("tabs").addEventListener("click", (ev) => {
  const b = ev.target.closest("button[data-tab]");
  if (!b) return;
  // a tab pressed (④ again, or another) ends ④'s domain replay: the replay goes back where it was
  if (ui.gridDomain) closeDomain();
  ui.pinnedTab = b.dataset.tab;
  ui.tabPlan = []; // a tab pressed by hand ends ?tabs=
});
$("expand-btn").addEventListener("click", () => setPresent(!ui.present));
window.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && (ui.gridViewer || ui.gridNote) && !$("view-grid").hidden) {
    if (ui.gridViewer) closeGridViewer();
    else {
      ui.gridNote = null;
      showGridNote();
    }
    return;
  }
  if (ev.key === "Escape" && ui.present) {
    setPresent(false);
    return;
  }
  if (ev.target.closest("input, textarea")) return;
  if (ev.code === "Space") {
    ev.preventDefault();
    ui.frozen = false;
    player.toggle();
  } else if (ev.code === "ArrowRight") player.step(1);
  else if (ev.code === "ArrowLeft") player.step(-1);
  else if (ev.code === "Home") player.seek(barRange(openSpan(currentTab(player.state))).lo);
  else if (ev.code === "End") player.seek(barRange(openSpan(currentTab(player.state))).hi);
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
// A saved run that is over, opened with nothing saying where or what to show (no ?t=, ?tab=, ?tabs=,
// ?autoplay=, ?video=1): it opens at its end, where ④ has every domain's verdict and ⑤ the vehicle
// built. The static export's first screen (FIRST, ?tab=A1: opensAtRequest) and a followed run are not.
function opensAtEnd() {
  if (player.live || !player.finished || VIDEO_LAYOUT) return false;
  return !["t", "tab", "tabs", "autoplay", "domain", "play"].some((k) => params.has(k));
}
// ① opened as it is (?tab=A1, or the static export's first screen) on a replay: it stops at the
// request's moment, so the top row (v —, the status of then), the footer (none), ①'s いま and its
// domain cards (picked) tell one moment; ▶ or 「この記録を再生する」 goes on from there.
function opensAtRequest() {
  if (player.live || VIDEO_LAYOUT || params.get("tab") !== "A1") return false;
  return !["t", "tabs", "autoplay", "domain", "play"].some((k) => params.has(k));
}
// The request's moment: the domains picked and the run's first hearing in, no design yet (just
// before the first design.created; at the last domains.picked when there is none). null: the record
// has no domains.picked before its first design.
function requestMoment() {
  const evs = player.events;
  const k = evs.findIndex((e) => e.kind === "design.created");
  const pick = evs.findLast((e, i) => e.kind === "domains.picked" && (k < 0 || i < k));
  if (!pick) return null;
  return k < 0 ? pick.t : Math.max(pick.t, evs[k].t - 1e-6);
}

async function play() {
  if (animLoop?.findArrival(player.events) != null) {
    seekAndDraw(params.has("t") ? Number(params.get("t")) || 0 : null).then(() => {
      bootDone();
      readyResolve(player.playhead);
    });
    return;
  }
  if (params.has("t")) {
    // (the state up to t built in steps first, nothing drawn meanwhile: a long record does not hold the page)
    const t = Number(params.get("t")) || 0;
    ui.building = true;
    await player.seekInSteps(t >= player.duration - END_SNAP_S ? player.duration : t);
    ui.building = false;
    // (A4 shown: ready once the shelf no longer changes by itself, so the frame is the same each time)
    seekAndDraw(t).then(() => (shelf && !$("view-shelf").hidden ? shelf.settled() : null))
      .then(() => {
        bootDone();
        readyResolve(player.playhead);
      });
    return;
  }
  ui.frozen = false;
  const span = openSpan(currentTab(player.state));
  if (span) {
    // ?tab=grid&domain=: that domain's analyses from its first prediction
    player.seek(span.t0);
    player.playing = params.get("autoplay") !== "0";
  } else if (player.live) player.attachLive();
  else if (opensAtRequest() && requestMoment() !== null) {
    const t = requestMoment();
    ui.building = true;
    await player.seekInSteps(t);
    ui.building = false;
    player.pause();
    ui.moment = player.playhead;
    document.documentElement.dataset.openedAt = "request";
  } else if (opensAtEnd()) {
    // a finished run opened as it is: at its end, every verdict in (▶ plays it from its start).
    // The frame is fixed there as at ?t= (seekAndDraw: ④'s tile pictures drawn at once, now and
    // when ④ is pressed), and ready once ④'s pictures have the real look's files (looksReady)
    ui.building = true;
    await player.seekInSteps(player.duration);
    ui.building = false;
    await seekAndDraw(player.duration);
    await tilesInTheirLook();
    document.documentElement.dataset.openedAt = "end";
    bootDone();
    readyResolve(player.playhead);
    return;
  } else if (params.get("autoplay") !== "0") player.play();
  bootDone();
  readyResolve(player.playhead);
}

// Add events to the player; a test's motion lengthens the replay so it can play
// to its end (in display time: the player's copy of the event).
// (the event clock gives the motion its own event's time: nothing past the last event)
function addEvents(events) {
  const from = player.events.length;
  player.append(events);
  motionEnd(player.events.length < from ? 0 : from);
}
function motionEnd(from = 0) {
  if (from === 0) player.minEnd = 0;
  if (player.clock === "event") return;
  for (const ev of player.events.slice(from)) {
    if (ev.kind !== "analysis.finished") continue;
    const span = motionSpanOf(ev);
    if (span !== null) player.minEnd = Math.max(player.minEnd, ev.t + Math.min(span, ANIM_MAX_S));
  }
}

// The clock switch on the play bar (and ?pace=): the event on screen stays on screen.
// the clock's button: its word and data-clock (also at once on a press, not only on the next frame)
function showClock() {
  $("pb-clock").textContent = player.clock === "real" ? "実時間" : player.clock === "paced" ? "間を詰めた" : "イベント";
  $("pb-clock").dataset.clock = player.clock;
}
function setClock(clock) {
  player.setClock(clock);
  showClock();
  motionEnd(0);
  ui.anim.local = false;
  tickKey = "";
}

function loadEvents(events, { live }) {
  bootDone();
  resetPlayer();
  addEvents(events);
  player.live = live;
  startPlayback();
}

// Why a record could not be read, in one line: 「記録を読み込めませんでした (what)。what to do」 (the
// status and the address after it under ?dev=1). kind: "timeout", "broken", or none (from e).
function loadFailedWhy(e, kind = null) {
  let what, todo = "ページを読み込み直してください";
  if (kind === "timeout") what = "時間切れ";
  else if (kind === "broken" || e?.broken || e instanceof SyntaxError) what = "記録が壊れています";
  else if (e?.status === 404) [what, todo] = ["見つかりません", "消されたか、名前が違います。一覧から選び直してください"];
  else if (e?.status) what = `サーバー ${e.status}`;
  else if (STATIC) what = "ネットワーク";
  else [what, todo] = ["ネットワーク", "HIRAKU が動いているか確かめて、ページを読み込み直してください"];
  const line = `記録を読み込めませんでした (${what})。${todo}`;
  return DEV && e ? `${line} — ${e?.message ?? e}` : line;
}

// A record that could not be read: why in one line, and what to press next (open it again, or the list).
function loadFailedNote(runId, e, kind = null) {
  bootFailed(loadFailedWhy(e, kind), [["↻ 開き直す", withDev(`?run=${encodeURIComponent(runId)}`)], ["☰ 一覧へ", withDev("?list=1")]]);
}

async function openRun(runId) {
  resetPlayer();
  boot.timer = setTimeout(() => loadFailedNote(runId, null, "timeout"), BOOT_TIMEOUT_MS);
  let bad = 0;
  feed = new EventFeed(runId, (evs, body) => {
    addEvents(evs);
    bad += body?.bad_lines ?? 0;
  }, { onProgress: bootProgress });
  try {
    await feed.readAll();
  } catch (e) {
    if (!boot.failed) loadFailedNote(runId, e);
    return;
  }
  if (boot.failed) return; // (時間切れ said already)
  if (!player.events.length && bad) {
    loadFailedNote(runId, null, "broken");
    return;
  }
  bootProgress(1);
  feed.loaded = true;
  // (a static export holds finished records only: nothing to follow)
  const follow = !STATIC && !player.finished && params.get("live") !== "0" && !params.has("t");
  player.live = follow;
  ui.followed = follow;
  ui.childGone = false;
  if (follow) {
    feed.follow(() => player.finished || ui.childGone);
    // started from this screen? then it can be stopped from here (until it is over)
    fetch("/api/launch", { cache: "no-store" })
      .then((r) => r.json())
      .then((info) => {
        if (info.running?.run_id === runId) {
          ui.stoppable = runId;
          drawn.rev = -1;
          watchChild(runId);
        }
      })
      .catch(() => {});
  }
  startPlayback();
}

// A text of the run list (a want, a demo row's line), escaped, with each short aside in brackets
// kept on one line (the run list's .nw): not "(無料・ / Claude なし)".
// (the agents by their plain names: judgedWords' roleNames)
const prose = (s) => esc(roleNames(s)).replace(/\(([^()]{1,40})\)/g, '<span class="nw">($1)</span>');

async function fetchRuns() {
  return (await fetch(getUrl("/api/runs"), { cache: "no-store" })).json();
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
  bootDone();
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
      // a run that stopped: why, in one line (labels.js stopWhyJa; the whole as the tooltip)
      const why = stopWhyJa(r);
      return (
        `<tr data-run="${esc(r.run_id)}"><td><div class="rsum">${esc(runSummaryJa(r) || "記録")}</div><div class="rmeta">${meta.join('<span class="sep"> · </span>')}</div>` +
        `${why ? `<div class="rwhy" data-role="stop-why" title="${esc(why)}">${esc(why)}</div>` : ""}</td>` +
        `<td class="state" data-state="${state}">${RUN_STATE_JA[state]}</td><td><a href="${href}">見る</a></td></tr>`
      );
    };
    const newest = (a, b) => (a.run_id < b.run_id ? 1 : a.run_id > b.run_id ? -1 : 0);
    const people = runs.filter((r) => !r.sample);
    const shown = people.filter((r) => !isOlderRecord(r)).sort(newest);
    const older = people.filter((r) => isOlderRecord(r)).sort(newest);
    box.innerHTML =
      `<h2>HIRAKU でできること</h2><p class="lead">作りたいものを書くと、Claude が確かめる解析を選び、計算して、設計を直します。${FREE_DESIGN_JA}</p>` +
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
    info = await (await fetch(getUrl("/api/launch"), { cache: "no-store" })).json();
  } catch (e) {
    info = { available: false };
  }
  if (STATIC) {
    // replays only: the recommendation (people's list), then one line to the README for the rest
    box.innerHTML = (DEV ? "" : pickHtml(info, false)) + LOCAL_RUN_HTML + (await staticExtrasHtml());
    return;
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
  // (a run still starting has no id yet: no link until it has)
  const running = !busy ? "" : rid
    ? `<p>走っている: <a href="${withDev(`?run=${encodeURIComponent(rid)}`)}">${esc(rid)} を追いかける</a> ${stop}</p>`
    : `<p>${START_WAIT_JA}</p>`;
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
// A static export (source.js STATIC) only replays: in place of the buttons that start, stop or send
// something, one line to the README's section on running HIRAKU (heading 「動かし方」, README_RUN).
const LOCAL_RUN_HTML =
  `<p class="local-run">手元で動かすには (再現する・実行する・新しく依頼する): <a class="readme" href="${README_RUN}" target="_blank" rel="noopener">README の「動かし方」</a></p>`;
// The export's other pages (data/static.json extras: [{href, title}], e.g. N10's answers), one line each.
async function staticExtrasHtml() {
  try {
    const got = await (await fetch(getUrl("/data/static.json"))).json();
    const rows = (got.extras ?? []).map((x) => `<p class="extra"><a href="${esc(x.href)}">${esc(x.title)}</a></p>`);
    return rows.join("");
  } catch {
    return "";
  }
}

// おすすめ: the server's default saved run (else the newest saved one) as a replay; with no saved run,
// the scripted run of the demo row PICK_DEMO_ID. Nothing when neither is there.
function pickHtml(info, busy) {
  const saved = listRuns.filter((r) => r.saved && r.finished);
  const run = saved.find((r) => r.default) ?? saved[0];
  const head = `<span class="tag">おすすめ</span>`;
  if (run)
    return (
      `<div class="pick" data-pick="run" data-run="${esc(run.run_id)}">${head}<div class="pline"><b>まずはこれ</b> — HIRAKU が設計を試験して、NG の所を直すまでを、保存した記録で見ます` +
      `${run.want ? `<div class="sub">依頼: ${esc(run.want)}</div>` : ""}</div><a class="go" href="?run=${encodeURIComponent(run.run_id)}">見る</a></div>`
    );
  const d = (info.demo_runs ?? []).find((r) => r.id === PICK_DEMO_ID && r.file && !r.error && (r.modes ?? []).includes("scripted"));
  if (!d) return "";
  return (
    `<div class="pick" data-pick="demo" data-demo="${esc(d.id)}">${head}<div class="pline"><b>まずはこれ</b> — 小さなオフロード車を試験して、NG の所を直すまでを、決まった手順で再現します <span class="nw">(無料・Claude なし)</span>` +
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
  // (a run still starting has no id yet: no link until it has)
  const running = !busy ? "" : rid
    ? `<p class="going">いま実行しています。<a href="?run=${encodeURIComponent(rid)}">見る</a> ${stop}</p>`
    : `<p class="going">${START_WAIT_JA}</p>`;
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

// While a start from the list is on its way: a note on top of the list at once (what the child is
// at, the seconds), until the page moves to the run.
function launchStartingNote(mode, at) {
  let el = $("launch-starting");
  if (!el) {
    el = document.createElement("p");
    el.id = "launch-starting";
    el.className = "starting";
    el.setAttribute("role", "status");
    $("launch").prepend(el);
  }
  const how = mode === "claude" ? "Claude で" : "決まった手順で";
  return (line) => {
    el.innerHTML = `<b>${how}運転を始めています。</b><span class="step">${esc(line || START_WAIT_JA)}</span> ` +
      `最初は、使うツールの確かめに時間がかかります。<span class="secs">${Math.floor((performance.now() - at) / 1000)} 秒</span>`;
  };
}

async function launch(file, mode) {
  launchConfirm = null;
  for (const b of $("launch").querySelectorAll("button")) b.disabled = true;
  const at = performance.now();
  let line = "";
  const show = launchStartingNote(mode, at);
  show(line);
  const tick = setInterval(() => show(line), 1000);
  try {
    const res = await fetch("/api/launch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ requirements: file, mode }) });
    const out = await res.json();
    if (!res.ok) throw Object.assign(new Error(out.error ?? String(res.status)), { status: res.status, body: out });
    const runId = await followStart((l) => {
      line = startLineJa(l);
      show(line);
    });
    location.href = withDev(`?run=${encodeURIComponent(runId)}`); // follow it live
  } catch (e) {
    clearInterval(tick);
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
// the module names (an older record's designer.<module> → 「枠の設計」) from the start, so a record still being read
// does not show the ids; drawn again once they are in
loadModuleNames().then(() => (drawn.rev = -1));
// the viewer's answers provisional or not, and the fields with no sample (viewer_link.js); drawn again once in
loadViewerInfo(() => (drawn.rev = -1));
if (ui.present) applyPresent();
else $("expand-btn").innerHTML = EXPAND_SVG.out;
requestAnimationFrame(loop);
if (params.get("run")) openRun(params.get("run"));
else if (params.get("src")) {
  fetch(params.get("src"), { cache: "no-store" })
    .then((r) => r.text())
    .then((text) => loadEvents(parseJsonl(text), { live: false }))
    .catch((e) => showRunList(`読めなかった: ${e}`));
} else if (params.get("list")) showRunList();
else if (draft) {
  bootDone();
  startDraft();
}
// no run, no file, no draft: the run list too (a saved run is one 見る away, from おすすめ or its row)
else showRunList();
