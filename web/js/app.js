// HIRAKU screen: wires events (live or replay) to the workbench panels and the 3D view.
//
// URL parameters
//   ?run=<run_id>   read runs/<run_id>/events.jsonl from the server (run=sample: the contract sample)
//   ?src=<url>      read an events.jsonl from any URL instead
//   ?t=12.5         stop at t = 12.5 s and draw that frame (for frame-by-frame video export)
//   ?video=1        video layout: keep the bottom 170 px empty for subtitles, hide the status row
//   ?status=0       hide the bottom status row only
//   ?speed=2        replay speed; ?autoplay=0 opens paused at 0; ?live=0 replays a running run instead of following it
//   ?tab=grid       open on the all-domains grid instead of the workbench (A1, A2, A5: the intake screens; A4: the tool shelf)
// window.hiraku.seek(t) returns a promise that resolves once the frame at t is drawn;
// window.hiraku.load(events, t) shows other events (already parsed) the same way.

import { DOMAINS, DOMAIN_JA, RULES, STOP_CODE_JA, agentLongName, agentName, BASE_JA, esc, fmt, fmtPct, fmtQ, fmtSigned, fmtTime, fmtUsd, fmtVU, opLabel, quantityName, unitLabel } from "./labels.js";
import { BAND_COLORS, BANDS, OVER_COLOR } from "./colormap.js";
import { EventFeed, Player, parseJsonl } from "./player.js";
import { chainFor, isSafeSide, judgementState, roundsUsed, valueFromJa } from "./state.js";
import { SeriesChart } from "./chart.js";
import { FIELD_JA, FIELD_ORDER, atFrame, contourOf, fieldsOf, frameIndex, onSharedScale, preferredField, sameField, variantName, variantOf } from "./fields.js";
import { motionOf, motionSpan, nearestAt, wheelName, xSeriesOf } from "./series.js";
import { mountIntake } from "./intake/index.js";
import { lookFromParams } from "./look.js";
import { mountShelf } from "./shelf.js";
import { liftIsLimit } from "./scene.js";
import { TileBoard } from "./tiles.js";
import { VIEWS, Workbench3D } from "./workbench3d.js";
import { AssemblyPanel } from "./assembly.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const STAGE_W = 1920;
const STAGE_H = 1080;
const SHOW_S = 5; // seconds of playback a bubble or hook warning stays
const ANIM_MAX_S = 12; // a test's motion plays in at most this many seconds of replay

const PET_COLORS = { lead: "#e9b872", designer: "#2fa7d6", packager: "#8e6bbf", system: "#c9c3b6", user: "#fbf9f4" };
const petColor = (id) => PET_COLORS[id] ?? (id?.startsWith("cae.") ? "#4cbf7a" : "#c9c3b6");

const INTAKE_TABS = ["A1", "A2", "A5"]; // the intake screens (js/intake/), shown in their own tabs
const TABS = ["bench", "grid", "A4", ...INTAKE_TABS, "A7"]; // A4: the tool shelf (js/shelf.js); A7: the assembly order (js/assembly.js)
let asmPanel = null; // mounted on first show

const ui = {
  pinnedLc: null,
  pinnedTab: TABS.includes(params.get("tab")) ? params.get("tab") : null,
  toggles: { contour: true, arrows: true, cg: true, parts: true, names: false, limit: true, graph: true },
  frozen: params.has("t"),
  // The test's motion follows the replay (auto); ▶ or dragging its bar plays it on its own (local)
  // until the replay playhead moves again.
  anim: { key: null, local: false, playing: false, tau: 0 },
  graphGroup: null,
  fieldKind: null, // contour field picked in the legend (null: the first one)
  fieldVariant: null, // among the fields of that kind: `${tool}|${variantOf}` (the analysis' tool; the beams, or a mesh and its surface)
  zoomMax: false, // the camera at a shell contour's largest element (legend: 最大の所に寄る)
  compare: false, // the previous design version beside the current one (legend: 前の版と比べる)
  stoppable: null, // the run id when this screen started the run being followed
  stopAsked: false,
};

// ?off=contour,arrows,cg,graph turns those overlays off at load (e.g. the bare vehicle for a title shot).
for (const key of (params.get("off") ?? "").split(",")) if (key in ui.toggles) ui.toggles[key] = false;

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
  resize3d();
}

// ---- 3D -------------------------------------------------------------------
const viewport = $("viewport");
const look = lookFromParams(params); // ?look=real: the photograph-like look (default: the diagram)
document.body.classList.add(`look-${look}`);
const bench = new Workbench3D(viewport, look);
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
let drawn = { rev: -1, lc: undefined, tab: undefined, toggles: "", logLen: 0, logFirstSeq: null, logRev: 0 };
let contour = null;

function selectedLc(s) {
  const cases = s.project?.load_cases ?? [];
  const id = ui.pinnedLc ?? s.focus.loadCaseId ?? cases[0]?.load_case_id ?? null;
  return id;
}

function currentTab(s) {
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
  events: () => player.events.slice(0, player.applied),
  onEvent(fn) {
    intakeListeners.add(fn);
    return () => intakeListeners.delete(fn);
  },
  // Only for a run this screen started while it runs (a replay stays read-only). The
  // answer goes to the run's folder as hearing.answer.json, which the run waits for; a
  // dropped file goes to its knowledge/ with a request in its inbox/ (the run imports it).
  get answerHearing() {
    return ownRunGoing() ? answerHearing : undefined;
  },
  get dropFile() {
    return ownRunGoing() ? dropFile : undefined;
  },
};

function ownRunGoing() {
  const s = player.state;
  return !!ui.stoppable && ui.stoppable === s.runId && s.status === "running";
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
  intake ??= mountIntake($("view-intake"), intakeApi, { nav: false, screen: tab });
  if (intake.screen !== tab) intake.show(tab);
  // (and when the run turns out to be this screen's own, which offers the answer and the drop)
  const now = `${player.state.runId}|${player.applied}|${ownRunGoing()}`;
  if (now !== intakeApplied) {
    intakeApplied = now;
    const last = player.events[player.applied - 1] ?? null;
    for (const fn of intakeListeners) fn(last);
  }
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
  $("pill-cost").textContent = `費用 ${fmtUsd(s.cost)} / ${fmtUsd(s.project?.cost_limit_usd)}`;
  const live = player.live;
  const status = { running: "実行中", done: "完了", stopped: "途中で止まった", failed: "失敗", empty: "" }[s.status] ?? s.status;
  $("mode").innerHTML =
    `<span class="badge ${live ? "live" : ""}">${live ? "● 実行中を追いかけ" : "再生"}${status && !live ? `・${esc(status)}` : ""}</span>` +
    `<a class="runid" href="?list=1" title="実行の一覧へ">${esc(s.runId ?? "")}</a>` +
    // a run this screen started can be stopped while it goes
    (ui.stoppable && ui.stoppable === s.runId && s.status === "running"
      ? `<button class="stop-btn" data-stop="${esc(s.runId)}" ${ui.stopAsked ? "disabled" : ""}>${ui.stopAsked ? "止めています…" : "■ 止める"}</button>`
      : `<a class="launch-link" href="?list=1" title="台本か Claude で、新しく走らせる">▶ その場で走らせる</a>`);
}

function renderChips(s, lcId) {
  const focusDomain = (s.project?.load_cases ?? []).find((c) => c.load_case_id === lcId)?.domain;
  $("chips").innerHTML = DOMAINS.map((d) => {
    const j = judgementState(s, d);
    const title = j.judgement?.summary ?? (j.picked ? "まだ判定が無い" : "この実行では選んでいない");
    return `<span class="chip ${j.state} ${d === focusDomain ? "focus" : ""}" title="${esc(title)}">${esc(DOMAIN_JA[d])}</span>`;
  }).join("");
}

function renderAgents(s) {
  const order = [...s.agentOrder].sort((a, b) => rank(a) - rank(b));
  $("agents").classList.toggle("many", order.length > 6);
  $("agents").innerHTML = order
    .map((id) => {
      const a = s.agents[id];
      const cls = a.status === "✓" ? "done" : ["止められた", "食い違い", "赤", "解析が通らない"].includes(a.status) ? "alert" : a.active ? "active" : "";
      return `<li class="${cls}"><span class="pet" style="--pet:${petColor(id)}"></span><span>${esc(agentName(id))}</span><span class="st">${esc(a.status)}</span></li>`;
    })
    .join("");
}

function rank(id) {
  const fixed = ["lead", "designer", "packager"];
  if (fixed.includes(id)) return fixed.indexOf(id);
  if (id.startsWith("cae.")) return 3 + DOMAINS.indexOf(id.slice(4));
  return 20;
}

function logItem(e) {
  const who = e.level === "said" ? `<span class="who">${esc(agentLongName(e.agent))}:</span> ` : "";
  const note = e.note ? `<span class="note">${esc(e.note)}</span>` : "";
  const upd = e.updatedT != null ? `<span class="tm">・更新 ${fmtTime(e.updatedT)}s</span>` : "";
  // What an agent said can run long: the line shows its first lines, the record keeps all of it.
  const text = e.level === "said" ? `<span class="tx" title="${esc(e.text)}">${esc(e.text)}</span>` : esc(e.text);
  return `<li class="${e.level}">${who}${text}<span class="tm" title="記録の t (実行が始まってからの秒)">${fmtTime(e.tRec ?? e.t)}s</span>${upd}${note}</li>`;
}

function renderLog(s) {
  const ol = $("log");
  const first = s.log[0]?.seq ?? null;
  if (s.log.length < drawn.logLen || first !== drawn.logFirstSeq || s.logRev !== drawn.logRev) {
    ol.innerHTML = "";
    drawn.logLen = 0;
  }
  if (s.log.length > drawn.logLen) {
    ol.insertAdjacentHTML("beforeend", s.log.slice(drawn.logLen).map(logItem).join(""));
    ol.scrollTop = ol.scrollHeight;
  }
  drawn.logLen = s.log.length;
  drawn.logFirstSeq = first;
  drawn.logRev = s.logRev;
}

function renderTests(s, lcId) {
  const cases = s.project?.load_cases ?? [];
  const auto = `<button class="auto ${ui.pinnedLc ? "" : "on"}" data-lc="">自動</button>`;
  $("tests").innerHTML =
    auto +
    cases
      .map((c) => `<button class="test ${c.load_case_id === lcId ? "on" : ""}" data-lc="${esc(c.load_case_id)}" title="${esc(c.load_case_id)}: ${esc(c.title)}"><span class="tid">${esc(c.test_id)}</span>${esc(c.title)}</button>`)
      .join("");
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
    const termsHtml = terms.length
      ? `<div class="terms">${terms
          .slice(0, 4)
          .map((t) => `<span>${esc(t.name)}</span><span class="barw"><i style="width:${Math.max(0, Math.min(1, Math.abs(t.share))) * 100}%"></i></span><span>${fmtPct(t.share, 0)}</span>`)
          .join("")}</div>`
      : "";
    const asm = (pred.assumptions ?? []).slice(0, 3).map((a) => `<li>${esc(a)}</li>`).join("");
    p.className = "card";
    p.innerHTML =
      `<h3>1. 予測<small>手計算の段 (K0)・${esc(agentLongName(pred.agent))}</small></h3>` +
      `<div class="sub">${esc(quantityName(pred.quantity))}・${esc(pred.formula_id)}</div>` +
      `<div class="expr">${esc(pred.expression)}</div>` +
      `<div class="kv">${inputs}</div>` +
      `<div class="big">= ${esc(fmtVU(pred.value, pred.unit))}</div>` +
      termsHtml +
      (asm ? `<ul class="asm">${asm}</ul>` : "");
  } else {
    p.className = "card wait";
    p.innerHTML = `<h3>1. 予測<small>手計算の段</small></h3><div>${loadCase ? `${esc(loadCase.test_id)} の予測はまだ。hooks は予測の無い解析を止める (HK1)` : "試験がまだ無い"}</div>`;
  }
  // 2. analysis
  const a = $("card-ana");
  if (ana) {
    const res = (ana.results ?? []).slice(0, 3).map((r) => `<tr><td>${esc(quantityName(r.quantity))}${r.where ? ` <span class="sub">@ ${esc(r.where)}</span>` : ""}</td><td>${esc(fmtVU(r.value, r.unit))}</td></tr>`).join("");
    a.className = ana.status === "failed" ? "card bad" : "card";
    a.innerHTML =
      `<h3>2. 解析<small>CAE の段 (${esc(ana.base)} ${esc(BASE_JA[ana.base] ?? "")})</small></h3>` +
      `<div class="sub">${esc(ana.tool)}・${esc(ana.load_case_id)}${typeof ana.wall_time_s === "number" ? `・${fmt(ana.wall_time_s, 2)} 秒` : ""}</div>` +
      (ana.status === "failed" ? `<div class="err">通らなかった: ${esc(ana.error ?? "")}</div>` : `<table>${res}</table>`);
  } else if (running) {
    a.className = "card busy";
    a.innerHTML = `<h3>2. 解析<small>CAE の段</small></h3><div>${esc(running.tool)} で解析中…</div>`;
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
      v =
        `<div class="verdict ${verdict.pass ? "ok" : "bad"}${safe ? " safe" : ""}">${how}判定 (${valueFromJa(verdict)}の値): ${esc(quantityName(verdict.quantity))} ${esc(fmtQ(verdict.value))} ${esc(opLabel(verdict.op))} ${esc(fmtQ(verdict.limit))} → ${verdict.pass ? "合格 ✓" : "不合格 ✗"}` +
        `<span class="sub" title="${esc(loadCase?.criterion?.basis ?? "")}">余裕 ${fmt(verdict.margin_pct, 3)} %${loadCase?.criterion?.basis ? `・基準 ${esc(loadCase.criterion.basis)}` : ""}</span></div>`;
    } else if (ok && loadCase?.criterion) {
      v = `<div class="sub">基準: ${esc(loadCase.criterion.quantity)} ${esc(opLabel(loadCase.criterion.op))} ${esc(fmtQ(loadCase.criterion.limit))} (判定を待っている)</div>`;
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
  } else {
    c.className = "card wait";
    const tol = loadCase ? `許す幅 ${fmtPct(loadCase.tolerance_rel, 0)} (荷重条件で決まっている)` : "";
    c.innerHTML = `<h3>3. 照合</h3><div>予測と解析がそろうと照らし合わせる。${tol}</div>`;
  }
}

const toolTitle = (s, tool) => s.tools[tool]?.title ?? tool;

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
function buildContour(s, chain) {
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
  let ct = contourOf(pick.a, pick.f, chain.loadCase?.criterion, s.meshes);
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
  if (prev && ui.compare) {
    const before = contourOf(prev.ana, prev.field, chain.loadCase?.criterion, s.meshes);
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
  const rows = [];
  for (let i = BANDS - 1; i >= 0; i -= 1) {
    const top = ct.min + ((ct.max - ct.min) * (i + 1)) / BANDS;
    rows.push(`<span class="sw${i === 0 ? " end" : ""}" style="background:${BAND_COLORS[i]}"></span><span class="tk">${i % 2 === 1 && !relative ? fmt(top, 3) : ""}</span>`);
  }
  rows.push(`<span></span><span class="tk">${relative ? "" : fmt(ct.min, 3)}</span>`);
  const swatch = `<span style="display:inline-block;width:12px;height:12px;background:${OVER_COLOR};margin-right:6px"></span>`;
  const over = ct.limitKnown
    ? `<div class="lim">${swatch}許容 ${fmt(ct.max, 4)} ${esc(unit)} を超えた所</div>`
    : ct.partLimit
      ? `<div class="lim">${swatch}${esc(ct.partLimit.part)} の許容 ${fmt(ct.partLimit.value, 4)} ${esc(unit)} を超えたとき</div>`
      : "";
  // one row of buttons per analysis (its tool's title is on each button when there are several)
  const nButtons = ct.variants.reduce((n, g) => n + g.buttons.length, 0);
  const chips =
    (ct.kinds.length > 1 ? `<div class="kinds">${ct.kinds.map((k) => `<button data-field="${k}" class="${k === ct.kind ? "on" : ""}">${FIELD_JA[k]}</button>`).join("")}</div>` : "") +
    (nButtons > 1
      ? ct.variants
          .map((g) => `<div class="kinds variants" data-analysis="${esc(g.ana)}">${g.buttons.map((v) => `<button data-variant="${esc(v.key)}" class="${v.on ? "on" : ""}">${esc(v.name)}</button>`).join("")}</div>`)
          .join("")
      : "");
  const acts =
    (ct.mesh ? `<button data-act="zoom" class="${ui.zoomMax ? "on" : ""}">${ui.zoomMax ? "元の見え方に戻る" : "最大の所に寄る"}</button>` : "") +
    (ct.canCompare ? `<button data-act="compare" class="${ct.compare ? "on" : ""}">${ct.compare ? "比べるのをやめる" : "前の版と比べる"}</button>` : "");
  const mesh = ct.mesh ? `・${esc(ct.mesh.rec.mesh_id)} (要素 ${ct.mesh.rec.elements.length})` : "";
  const title = relative
    ? `${esc(ct.field.label ?? FIELD_JA[ct.kind])}${ct.frequency ? ` ${fmt(ct.frequency, 4)} Hz` : ""}<br><small>動きの大きさ (相対)・揺れは見せるための速さ</small>`
    : `${esc(ct.field.label ?? FIELD_JA[ct.kind])} [${esc(unit || "1")}]`;
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
  el.innerHTML =
    chips +
    (acts ? `<div class="acts">${acts}</div>` : "") +
    `<div class="ttl">${title}</div>` +
    when +
    `<div class="bar">${rows.join("")}</div>` +
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
      cmpView = { el, tags, bench: new Workbench3D(el, look) };
      window.hiraku.cmpBench = cmpView.bench;
    }
    const b = cmpView.bench;
    b.setDesign(s.designs[c.ana.design_version] ?? null);
    b.setLoading(chain.loadCase?.params?.loading ?? null);
    b.setScene(chain.loadCase, c.ana);
    b.setContour(c.before);
    b.setToggles(ui.toggles);
    cmpView.tags.innerHTML = `<span data-version="${c.version}">版 ${c.version}</span><span data-version="${c.nowVersion}">版 ${c.nowVersion}</span>`;
  }
  cmpView?.tags && (cmpView.tags.hidden = !c);
  // The right view keeps clear the strip under the legend (from the legend's left edge to
  // the viewport's right, and a gap), so its vehicle is framed in the rest of its half; the
  // left view looks through the same camera, so its vehicle sits the same way in its half.
  const lg = $("legend");
  const room = c && !lg.hidden ? viewport.clientWidth - lg.offsetLeft + LEGEND_GAP_PX : 0;
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
function renderToggles(motion, lc, graphs = false) {
  const t = ui.toggles;
  const btn = (key, label, on) => `<button data-toggle="${key}" class="${on ? "on" : ""}">${label}</button>`;
  $("toggles").innerHTML =
    btn("shape", "形状", !t.contour) +
    btn("contour", "コンター", t.contour) +
    btn("arrows", "力の矢印", t.arrows) +
    btn("cg", "重心", t.cg) +
    btn("parts", "部品", t.parts) +
    btn("names", "名前", t.names) +
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
  const { reds, atLimit } = board.render(s);
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
}

function renderStatus(s) {
  const act = s.activity;
  $("activity").innerHTML = act ? `<span class="dot"></span>${esc(agentLongName(act.agent) || "HIRAKU")}: ${esc(act.text)}` : "";
}

function renderHooks(s, playhead) {
  const counts = {};
  const recent = {};
  for (const d of s.hooks.denied) {
    counts[d.rule] = (counts[d.rule] ?? 0) + 1;
    if (playhead - d.t < SHOW_S) recent[d.rule] = true;
  }
  for (const b of s.hooks.blockedStops) {
    counts[b.rule] = (counts[b.rule] ?? 0) + 1;
    if (playhead - b.t < SHOW_S) recent[b.rule] = true;
  }
  const html = `hooks: ` + RULES.map((r) => `<span class="rule ${recent[r.id] ? "hit" : ""}" title="${esc(r.key)}"><span class="hk">${r.id}</span> ${esc(r.ja)}${counts[r.id] ? ` ×${counts[r.id]}` : " ✓"}</span>`).join("");
  if (html !== hooksHtml) $("hooks").innerHTML = hooksHtml = html;
}
let hooksHtml = "";

function fade(dt) {
  if (dt < 0 || dt > SHOW_S) return 0;
  return Math.min(1, dt / 0.25, (SHOW_S - dt) / 0.6);
}

function renderTransient(s, playhead, chain) {
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
  if (den && h.dataset.seq !== String(den.seq)) {
    h.dataset.seq = String(den.seq);
    h.innerHTML = `<b>hooks が止めた (${esc(den.rule)} ${esc(den.ruleJa ?? "")})</b>${esc(den.text)}`;
  }
  const run = chain.running ?? Object.values(s.running)[0] ?? null;
  const r = $("running");
  r.style.opacity = run ? 1 : 0;
  if (run) {
    const pulse = 0.35 + 0.65 * (0.5 + 0.5 * Math.sin(playhead * Math.PI * 2));
    r.innerHTML = `<span class="dot" style="opacity:${pulse.toFixed(2)}"></span>${esc(run.tool)} で解析中 (${esc(run.load_case_id)})`;
  }
  renderHooks(s, playhead);
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

// ---- frame ----------------------------------------------------------------
function draw() {
  const s = player.state;
  const lcId = selectedLc(s);
  const tab = currentTab(s);
  const togglesKey = JSON.stringify(ui.toggles);
  const chain = chainFor(s, lcId, s.currentVersion);
  const motion = motionFor(chain.shown);
  if (s.rev !== drawn.rev || lcId !== drawn.lc || tab !== drawn.tab || togglesKey !== drawn.toggles) {
    renderTop(s);
    renderChips(s, lcId);
    renderAgents(s);
    renderLog(s);
    renderTests(s, lcId);
    renderChain(s, chain);
    renderStatus(s);
    renderToggles(motion, chain.loadCase, chain.shown?.status === "ok" && xSeriesOf(chain.shown).size > 0);
    const intakeTab = INTAKE_TABS.includes(tab);
    $("view-bench").hidden = tab !== "bench";
    $("view-grid").hidden = tab !== "grid";
    $("view-intake").hidden = !intakeTab;
    $("view-shelf").hidden = tab !== "A4";
    $("view-asm").hidden = tab !== "A7";
    $("main").classList.toggle("wide", tab === "grid" || tab === "A4" || tab === "A7" || intakeTab); // A6, A4, A7 and the intake screens use the whole width
    for (const b of document.querySelectorAll(".tab")) b.classList.toggle("on", b.dataset.tab === tab);
    if (tab === "grid") renderGrid(s);
    if (intakeTab) renderIntake(tab);
    if (tab === "A4") renderShelf();

    const design = s.designs[chain.pred?.design_version ?? s.currentVersion] ?? null;
    bench.setDesign(design);
    bench.setLoading(chain.loadCase?.params?.loading ?? null);
    bench.setMotion(motion);
    bench.setScene(chain.loadCase, chain.shown);
    const ct = buildContour(s, chain);
    if ((ct?.key ?? null) !== (contour?.key ?? null)) contour = ct;
    bench.setContour(ui.toggles.contour ? framedContour() : null);
    if (!contour?.mesh) ui.zoomMax = false;
    renderLegend(contour, s, chain.shown, motion); // before setCompare, which keeps the legend's strip clear
    setCompare(s, chain, ui.toggles.contour && tab === "bench" ? contour : null);
    bench.setFocus(ui.zoomMax && ui.toggles.contour ? contour : null);
    cmpView?.bench.setCloseUp(!!bench.focused);
    // With a motion the arrows come from the series, frame by frame.
    bench.setVectors(chain.shown?.status === "ok" && !motion ? chain.shown.vectors ?? null : null);
    bench.setToggles(ui.toggles);
    drawn = { ...drawn, rev: s.rev, lc: lcId, tab, toggles: togglesKey };
  }
  renderMotion(s, chain, motion);
  if (contour?.frames && ui.toggles.contour) bench.setContour(framedContour());
  renderTransient(s, player.playhead, chain);
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
  else if (tab === "A7") (asmPanel ??= window.hiraku.asm = new AssemblyPanel($("view-asm"))).render(s);
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

async function seekAndDraw(t) {
  ui.frozen = true;
  player.pause();
  player.seek(t);
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
  player,
  ready,
  seek: seekAndDraw,
  state: () => player.state,
  bench,
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
  const b = ev.target.closest("button[data-field], button[data-variant], button[data-act]");
  if (!b) return;
  if (b.dataset.act === "zoom") ui.zoomMax = !ui.zoomMax;
  else if (b.dataset.act === "compare") ui.compare = !ui.compare;
  else if (b.dataset.field) ui.fieldKind = b.dataset.field;
  else ui.fieldVariant = b.dataset.variant;
  drawn.rev = -2;
});
$("toggles").addEventListener("click", (ev) => {
  const b = ev.target.closest("button[data-toggle]");
  if (!b) return;
  const k = b.dataset.toggle;
  if (k === "shape") ui.toggles.contour = false;
  else ui.toggles[k] = !ui.toggles[k];
});
$("tabs").addEventListener("click", (ev) => {
  const b = ev.target.closest("button[data-tab]");
  if (!b) return;
  ui.pinnedTab = b.dataset.tab;
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
  drawn = { rev: -1, lc: undefined, tab: undefined, toggles: "", logLen: 0, logFirstSeq: null, logRev: 0 };
  $("runlist").hidden = true;
}

function startPlayback() {
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

async function openRun(runId) {
  resetPlayer();
  feed = new EventFeed(runId, addEvents);
  try {
    await feed.readAll();
  } catch (e) {
    showRunList(`読めなかった: ${e}`);
    return;
  }
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

async function fetchRuns() {
  return (await fetch("/api/runs", { cache: "no-store" })).json();
}

async function showRunList(message = "", runs = null) {
  const box = $("runlist");
  box.hidden = false;
  if (!runs) {
    try {
      runs = await fetchRuns();
    } catch (e) {
      runs = [];
      message = message || `実行の一覧を読めなかった (${e})。python -m hiraku.webserve で開いてください`;
    }
  }
  const row = (r) => {
    const st = r.sample ? "形の見本 (数値は計算結果ではない)" : r.finished ? "終わった" : "書き込み中";
    const href = `?run=${encodeURIComponent(r.run_id)}`;
    const links = r.finished ? `<a href="${href}">再生</a> <a href="${href}&video=1">動画の配置で</a>` : `<a href="${href}">追いかける</a>`;
    const mark = r.default ? `<span class="def">画面を開くと再生</span>` : "";
    return `<tr><td>${esc(r.want)}<div class="rid">${esc(r.run_id)}</div></td><td>${esc(st)}${mark}</td><td>${links}</td></tr>`;
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

async function renderLaunch(message = "") {
  const box = $("launch");
  if (!box) return;
  let info;
  try {
    info = await (await fetch("/api/launch", { cache: "no-store" })).json();
  } catch (e) {
    box.innerHTML = "";
    return;
  }
  if (info.available === false) {
    box.innerHTML = "";
    return;
  }
  const busy = !!info.running;
  const usd = (v) => (typeof v === "number" ? fmtUsd(v) : "—");
  const rows = (info.requirements ?? [])
    .map((r) => {
      if (r.error) return `<div class="lreq"><div>${esc(r.file)}<div class="err">読めなかった: ${esc(r.error)}</div></div></div>`;
      const file = esc(r.file);
      let claude;
      if (!info.claude_ready) claude = `<button disabled>Claude で走らせる</button><span class="why">サーバーを立ち上げた窓に ${esc(info.key_name)} が無いので押せない</span>`;
      else if (launchConfirm === r.file)
        claude =
          `<span class="confirm">Claude を呼びます。費用の上限は ${usd(r.cost_limit_usd)}、繰り返しは ${esc(r.round_limit ?? "—")} 回まで。走らせますか?</span>` +
          `<button class="go" data-launch="claude" data-req="${file}">走らせる</button><button data-launch-cancel="1">やめる</button>`;
      else claude = `<button data-launch-ask="${file}" ${busy ? "disabled" : ""}>Claude で走らせる (費用の上限 ${usd(r.cost_limit_usd)})</button>`;
      return (
        `<div class="lreq"><div>${esc(r.want)}<div class="rid">${file}</div></div>` +
        `<div class="lbtns"><button data-launch="scripted" data-req="${file}" ${busy ? "disabled" : ""}>台本で走らせる (Claude は呼ばない)</button>${claude}</div></div>`
      );
    })
    .join("");
  const rid = info.running?.run_id ?? "";
  const stop = info.running?.stop_requested ? `<span class="why">止めるよう伝えた</span>` : `<button data-launch-stop="${esc(rid)}">止める</button>`;
  const running = busy ? `<p>走っている: <a href="?run=${encodeURIComponent(rid)}">${esc(rid)} を追いかける</a> ${rid ? stop : ""}</p>` : "";
  // without the key in the server's window, only scripted runs and replays are there (the key is never asked for)
  const keyless = info.claude_ready
    ? ""
    : `<p class="keyless">HIRAKU を起こした窓に ${esc(info.key_name)} が無いので、使えるのは台本の運転と、記録の再生だけです。Claude で走らせるには、キーを入れた窓から起こし直してください。</p>`;
  box.innerHTML = `<h3>その場で走らせる</h3>${keyless}${message ? `<p class="err">${esc(message)}</p>` : ""}${running}${rows}`;
}

async function launch(file, mode) {
  launchConfirm = null;
  for (const b of $("launch").querySelectorAll("button")) b.disabled = true;
  try {
    const res = await fetch("/api/launch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ requirements: file, mode }) });
    const out = await res.json();
    if (!res.ok) throw new Error(out.error ?? String(res.status));
    location.href = `?run=${encodeURIComponent(out.run_id)}`; // follow it live
  } catch (e) {
    renderLaunch(`走らせられなかった: ${e.message ?? e}`);
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

// No run in the URL: open the server's default run (a saved run), else the run list.
async function openDefault() {
  let runs;
  try {
    runs = await fetchRuns();
  } catch (e) {
    showRunList();
    return;
  }
  const def = runs.find((r) => r.default);
  if (def) openRun(def.run_id);
  else showRunList("", runs);
}

fitStage();
requestAnimationFrame(loop);
if (params.get("run")) openRun(params.get("run"));
else if (params.get("src")) {
  fetch(params.get("src"), { cache: "no-store" })
    .then((r) => r.text())
    .then((text) => loadEvents(parseJsonl(text), { live: false }))
    .catch((e) => showRunList(`読めなかった: ${e}`));
} else if (params.get("list")) showRunList();
else openDefault();
