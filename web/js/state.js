// The screen state, built from events.jsonl lines only (live or replay).
// applyEvent() is the one place that reads an event; everything drawn on the
// screen comes from the state it builds. No file other than events is read.

import {
  DOMAINS,
  DOMAIN_JA,
  STOP_CODE_JA,
  agentLongName,
  agentName,
  fmt,
  fmtMargin,
  fmtPct,
  fmtQ,
  fmtSigned,
  fmtVU,
  opLabel,
  plainText,
  quantityName,
  ruleOf,
} from "./labels.js";
import { kindCountText } from "./meshkind.js";

const RECORDS = ["predictions", "analyses", "comparisons", "causes", "verdicts", "meshes"];

export function initialState() {
  const s = {
    rev: 0,
    runId: null,
    project: null,
    startTs: null,
    status: "empty", // empty | running | done | stopped | failed
    stopReason: null,
    stopCode: null, // run.finished stop_code (done, not_judged, round_limit, ...)
    reports: {}, // design version -> report (report.written)
    report: null, // the newest report
    t: 0,
    seq: -1,
    designs: {},
    versions: [],
    currentVersion: null,
    running: {}, // analysis_id -> analysis.started data (until finished)
    judgements: {}, // `${version}|${domain}` -> judgement
    tools: {},
    agents: {},
    agentOrder: [],
    log: [],
    logIdx: {}, // record id -> index of its log line
    logRev: 0, // bumped when an earlier log line is rewritten
    touched: null, // {idx, seq}: the line the current event wrote
    hooks: { denied: [], blockedStops: [] },
    cost: 0,
    focus: { screen: "A3", testId: null, loadCaseId: null, t: 0 },
    activity: null,
    said: null, // last agent.said / user.said: {agent, text, t, seq}
    denied: null, // last hook.denied: {rule, text, t, seq, tool, agent}
    unknown: 0,
    order: {},
    seqOf: {},
    firstSeqOf: {}, // record id -> seq of the event that first brought it
    tOf: {}, // record id -> t of the event that brought it (the last one when sent again)
    // What the intake events brought (drawn by js/intake/): the picked domains, the
    // hearing, the rule documents and the rule checks (the last per rule_check_id).
    intake: { picked: null, questions: [], answers: [], note: null, docs: [], rules: {}, ruleChecks: {} },
  };
  for (const r of RECORDS) {
    s[r] = {};
    s.order[r] = [];
  }
  return s;
}

function ensureAgent(s, id, extra = {}) {
  if (!id) return null;
  let a = s.agents[id];
  if (!a) {
    a = {
      id,
      role: id.startsWith("cae.") ? "cae" : id,
      domain: id.startsWith("cae.") ? id.slice(4) : null,
      prop: null,
      active: false,
      stopped: false,
      status: id === "lead" ? "見守り" : "待ち",
      lastT: null,
    };
    s.agents[id] = a;
    s.agentOrder.push(id);
  }
  Object.assign(a, extra);
  return a;
}

function put(s, kind, id, rec, ev) {
  if (!(id in s[kind])) {
    s.order[kind].push(id);
    s.firstSeqOf[id] = ev.seq;
  }
  s[kind][id] = rec;
  s.seqOf[id] = ev.seq;
  s.tOf[id] = ev.t;
}

// t is display time (the replay's clock); tRec is the recorded t shown on the line.
function log(s, ev, level, text) {
  s.log.push({ seq: ev.seq, t: ev.t, tRec: ev.t_rec ?? ev.t, kind: ev.kind, agent: ev.agent ?? null, domain: ev.domain ?? null, level, text });
  s.touched = { idx: s.log.length - 1, seq: ev.seq };
}

// One log line per record id. A record sent again with the same id (the
// contract's way to update it; the last event wins) rewrites its line in place.
// Returns true when the record was already there.
function logRecord(s, ev, id, level, text) {
  const i = s.logIdx[id];
  if (i === undefined) {
    log(s, ev, level, text);
    s.logIdx[id] = s.log.length - 1;
    s.log[s.log.length - 1].recId = id;
    return false;
  }
  Object.assign(s.log[i], { level, text, updatedT: ev.t_rec ?? ev.t });
  s.touched = { idx: i, seq: ev.seq };
  s.logRev += 1;
  return true;
}

function setActivity(s, ev, text) {
  s.activity = { agent: ev.agent ?? null, domain: ev.domain ?? null, text, t: ev.t, seq: ev.seq };
}

function focusLoadCase(s, loadCaseId, t) {
  if (!loadCaseId) return;
  s.focus.loadCaseId = loadCaseId;
  const lc = (s.project?.load_cases ?? []).find((c) => c.load_case_id === loadCaseId);
  if (lc) s.focus.testId = lc.test_id;
  s.focus.t = t;
}

function who(ev) {
  return agentName(ev.agent) || "HIRAKU";
}

// Apply one event (mutates and returns s). Unknown kinds are counted, not fatal.
export function applyEvent(s, ev) {
  const d = ev.data ?? {};
  s.rev += 1;
  s.seq = ev.seq;
  s.t = ev.t;
  if (ev.run_id && !s.runId) s.runId = ev.run_id;
  const agent = ev.agent && ev.agent !== "system" && ev.agent !== "user" ? ensureAgent(s, ev.agent) : null;
  if (agent) agent.lastT = ev.t;

  switch (ev.kind) {
    case "run.started": {
      s.project = d;
      s.runId = d.run_id ?? ev.run_id;
      s.startTs = ev.ts ?? null;
      s.status = "running";
      // null: HIRAKU cannot know the cost (driver claude_code_plugin); kept as null, not 0.
      // A record without the field (older runs) starts at 0 as before.
      s.cost = d.cost_usd === undefined ? 0 : d.cost_usd;
      for (const a of d.agents ?? []) ensureAgent(s, a.agent_id, { role: a.role, domain: a.domain ?? null, prop: a.prop });
      log(s, ev, "info", `実行を始めた: ${d.want ?? ""}`);
      setActivity(s, ev, "依頼を読んで分野と試験を決めた");
      break;
    }
    case "project.updated": {
      // A run whose designer makes version 1: once it exists, the load cases, the picked
      // domains and the roles are sent again; this project replaces the one of run.started.
      s.project = d;
      for (const a of d.agents ?? []) ensureAgent(s, a.agent_id, { role: a.role, domain: a.domain ?? null, prop: a.prop });
      log(s, ev, "info", `試験と分野を決めた: 荷重条件 ${(d.load_cases ?? []).length}・分野 ${(d.domain_picks ?? []).length}`);
      setActivity(s, ev, "版 1 から試験と分野を決めた");
      break;
    }
    case "domains.picked": {
      s.intake.picked = d;
      const picks = d.picks ?? [];
      const tests = new Set(picks.flatMap((p) => p.test_ids ?? []));
      const names = picks.map((p) => p.shown_name ?? DOMAIN_JA[p.domain] ?? p.domain).join("・");
      const txt = `分野を選んだ: ${names || "なし"} (当てはまる試験 ${tests.size}・選ばなかった分野 ${(d.off ?? []).length})`;
      log(s, ev, "info", `${who(ev)}: ${txt}`);
      setActivity(s, ev, txt);
      break;
    }
    case "hearing.asked": {
      const qs = d.questions ?? [];
      s.intake.questions = qs;
      if (agent) agent.status = "答え待ち";
      const must = qs.filter((q) => q.ask === "must").length;
      const txt = `足りない要件を ${qs.length} 個聞いた (値がまだ無い ${must}・既定値を確かめる ${qs.length - must})`;
      log(s, ev, "step", `${who(ev)}: ${txt}`);
      setActivity(s, ev, txt);
      break;
    }
    case "hearing.answered": {
      const ans = d.answers ?? [];
      s.intake.answers = ans;
      s.intake.note = d.note ?? null;
      if (agent) agent.status = "見守り";
      const byDefault = ans.filter((a) => a.source === "default").length;
      const txt = `答えがそろった: ${ans.length} 個 (既定値のまま ${byDefault})`;
      log(s, ev, "step", `${who(ev)}: ${txt}`);
      setActivity(s, ev, txt);
      break;
    }
    case "knowledge.imported": {
      const doc = d.doc ?? {};
      const rules = d.rules ?? [];
      // The same document imported again replaces its rules.
      const i = s.intake.docs.findIndex((x) => x.doc?.doc_id === doc.doc_id);
      if (i >= 0) s.intake.docs[i] = d;
      else s.intake.docs.push(d);
      for (const r of rules) s.intake.rules[r.rule_id] = r;
      const skipped = (doc.skipped ?? []).length;
      const txt = `規則の束「${doc.filename ?? doc.doc_id ?? ""}」を入れた: 規則 ${rules.length}${skipped ? `・規則にしなかった行 ${skipped}` : ""}`;
      log(s, ev, "info", `${who(ev)}: ${txt}`);
      setActivity(s, ev, txt);
      break;
    }
    case "rule_check.recorded": {
      s.intake.ruleChecks[d.rule_check_id] = d;
      const hits = d.hits ?? [];
      const notEval = (d.not_evaluable ?? []).length;
      const checked = (d.targets ?? []).length - notEval;
      const parts = [];
      if (hits.length) parts.push(`守れていない部品 ${hits.length} (${hits.map((h) => h.part_id).join("・")})`);
      else if (checked > 0) parts.push(`当てた部品 ${checked} は守れている`);
      if (notEval) parts.push(`値が無くて当てられない部品 ${notEval}`);
      const txt = `規則 ${d.rule_id} を版 ${d.design_version} に当てた: ${parts.join("・")}`;
      if (agent) agent.status = "規則を当てた";
      logRecord(s, ev, d.rule_check_id, hits.length ? "bad" : notEval ? "warn" : "ok", `${who(ev)}: ${txt}`);
      setActivity(s, ev, txt);
      break;
    }
    case "report.written": {
      // The report of a design version (the A8 screen's source); the newest one is kept per version.
      s.reports[d.design_version] = d;
      s.report = d;
      const reds = (d.domains ?? []).filter((x) => x.state === "red").length;
      log(s, ev, "ok", `版 ${d.design_version} の報告を書いた (分野 ${(d.domains ?? []).length}・赤 ${reds}・まだ確かめていない ${(d.unchecked ?? []).length})`);
      setActivity(s, ev, `報告: ${String(d.summary ?? "").split("\n")[0]}`);
      break;
    }
    case "run.finished": {
      s.status = d.status ?? "done";
      for (const a of Object.values(s.agents)) a.active = false;
      s.stopReason = d.stop_reason ?? null;
      s.stopCode = d.stop_code ?? null;
      const said = STOP_CODE_JA[d.stop_code] ?? d.stop_reason;
      const why = said && d.stop_code !== "done" ? `・${said}` : "";
      log(s, ev, d.status === "failed" ? "bad" : "info", `実行が終わった (${d.status ?? ""}${why})${ev.text ? ": " + ev.text : ""}`);
      setActivity(s, ev, ev.text || "実行が終わった");
      break;
    }
    case "agent.started": {
      const a = ensureAgent(s, d.agent_id ?? ev.agent);
      if (a) Object.assign(a, { active: true, stopped: false, status: "作業中" });
      log(s, ev, "muted", `${agentLongName(d.agent_id ?? ev.agent)}が動き始めた`);
      break;
    }
    case "agent.stopped": {
      const a = ensureAgent(s, d.agent_id ?? ev.agent);
      if (a) Object.assign(a, { active: false, stopped: true, status: "終わり" });
      log(s, ev, "muted", `${agentLongName(d.agent_id ?? ev.agent)}が手を止めた`);
      break;
    }
    case "agent.said":
    case "user.said": {
      const text = plainText(ev.text);
      s.said = { agent: ev.kind === "user.said" ? "user" : ev.agent, text, t: ev.t, seq: ev.seq };
      log(s, ev, "said", text);
      setActivity(s, ev, text.split("\n")[0]);
      break;
    }
    case "design.created": {
      const v = d.design_version;
      s.designs[v] = d;
      if (!s.versions.includes(v)) s.versions.push(v);
      s.currentVersion = Math.max(...s.versions);
      ensureAgent(s, ev.agent ?? "designer");
      if (agent) agent.status = `版 ${v}`;
      log(s, ev, "info", `${who(ev)}: 版 ${v} を作った${d.change_reason ? ` (${d.change_reason})` : ""}`);
      setActivity(s, ev, `版 ${v} の設計を作った`);
      break;
    }
    case "mesh.created": {
      // A mesh (shells, solids or both) of one design version; fields name it by mesh_id.
      put(s, "meshes", d.mesh_id, d, ev);
      const els = d.elements ?? [];
      const kinds = kindCountText({ elements: els, element_kind: d.element_kind });
      const size = d.element_size_mm ? `・要素の大きさ ${fmt(d.element_size_mm, 3)} mm` : "";
      const txt = `版 ${d.design_version} のメッシュを作った: 節 ${(d.nodes_mm ?? []).length}・要素 ${els.length}${kinds ? ` (${kinds})` : ""}${size}`;
      logRecord(s, ev, d.mesh_id, "info", `${who(ev)}: ${txt}`);
      setActivity(s, ev, txt);
      break;
    }
    case "prediction.recorded": {
      put(s, "predictions", d.prediction_id, d, ev);
      focusLoadCase(s, d.load_case_id, ev.t);
      if (agent) agent.status = "予測した";
      const txt = `予測を記録した: ${quantityName(d.quantity)} = ${fmtVU(d.value, d.unit)} (${d.formula_id})`;
      logRecord(s, ev, d.prediction_id, "step", `${who(ev)}: ${txt}`);
      setActivity(s, ev, txt);
      break;
    }
    case "analysis.started": {
      s.running[d.analysis_id] = { ...d, agent: ev.agent, t: ev.t, design_version: ev.design_version ?? null };
      focusLoadCase(s, d.load_case_id, ev.t);
      if (agent) agent.status = "解析中";
      const txt = `${d.tool} で解析を始めた (${d.load_case_id})`;
      log(s, ev, "step", `${who(ev)}: ${txt}`);
      setActivity(s, ev, txt);
      break;
    }
    case "analysis.finished": {
      delete s.running[d.analysis_id];
      put(s, "analyses", d.analysis_id, d, ev);
      focusLoadCase(s, d.load_case_id, ev.t);
      if (d.status === "failed") {
        if (agent) agent.status = "解析が通らない";
        logRecord(s, ev, d.analysis_id, "bad", `${who(ev)}: ${d.tool} の解析が通らなかった: ${d.error ?? ""}`);
        setActivity(s, ev, `解析が通らなかった: ${d.error ?? ""}`);
      } else {
        if (agent) agent.status = "解析した";
        const r = (d.results ?? [])[0];
        const txt = r
          ? `解析が終わった: ${quantityName(r.quantity)} = ${fmtVU(r.value, r.unit)}${r.where ? ` (${r.where})` : ""}`
          : "解析が終わった";
        logRecord(s, ev, d.analysis_id, "step", `${who(ev)}: ${txt}`);
        setActivity(s, ev, txt);
      }
      break;
    }
    case "comparison.recorded": {
      const again = d.comparison_id in s.comparisons;
      put(s, "comparisons", d.comparison_id, d, ev);
      const ok = d.status === "match";
      const txt = ok
        ? `照合: 差 ${fmtSigned(d.diff_rel)} (許す幅 ${fmtPct(d.tolerance_rel, 0)}) で一致`
        : `照合: 差 ${fmtSigned(d.diff_rel)} が許す幅 ${fmtPct(d.tolerance_rel, 0)} を超えた。` +
          (d.cause_id ? `原因は ${d.cause_id}` : "原因を書くまで先に進めない");
      logRecord(s, ev, d.comparison_id, ok ? "ok" : "bad", `${who(ev)}: ${txt}`);
      if (!again) {
        if (agent) agent.status = ok ? "照合した" : "食い違い";
        setActivity(s, ev, txt);
      }
      break;
    }
    case "cause.recorded": {
      const before = s.causes[d.cause_id];
      put(s, "causes", d.cause_id, d, ev);
      const done = d.resolved ? ` — 直った${d.resolved_by ? ` (${d.resolved_by} で一致)` : ""}` : "";
      const safe = d.safe_side ? ` — 安全側で見られている: ${d.safe_side_reason ?? ""}` : "";
      logRecord(s, ev, d.cause_id, d.resolved ? "ok" : "warn", `${who(ev)}: 原因「${d.cause}」→ 直し方「${d.fix}」${done}${safe}`);
      if (!before) setActivity(s, ev, d.safe_side ? `食い違いの原因を書いた。安全側で見られている` : `食い違いの原因を書いた: ${d.cause}`);
      else if (d.resolved && !before.resolved) setActivity(s, ev, `原因 ${d.cause_id} は直った`);
      break;
    }
    case "verdict.recorded": {
      put(s, "verdicts", d.verdict_id, d, ev);
      const how = isSafeSide(d) ? ` (安全側で判定: ${valueFromJa(d)}の値・${d.cause_id ?? ""})` : "";
      const txt = `判定${how}: ${quantityName(d.quantity)} ${fmtQ(d.value)} ${opLabel(d.op)} ${fmtQ(d.limit)} → ${d.pass ? "合格" : "不合格"} (余裕 ${fmtMargin(d)})`;
      logRecord(s, ev, d.verdict_id, d.pass ? "ok" : "bad", `${who(ev)}: ${txt}`);
      setActivity(s, ev, txt);
      break;
    }
    case "judgement.updated": {
      s.judgements[`${d.design_version}|${d.domain}`] = { ...d, t: ev.t };
      const state = { green: "緑", red: "赤", pending: "まだ" }[d.state] ?? d.state;
      const a = s.agents[`cae.${d.domain}`];
      if (a && d.state === "green") a.status = "✓";
      if (a && d.state === "red") a.status = "赤";
      log(s, ev, d.state === "green" ? "ok" : d.state === "red" ? "bad" : "muted", `${DOMAIN_JA[d.domain] ?? d.domain}: 版 ${d.design_version} の判定は${state}${d.summary ? ` — ${d.summary}` : ""}`);
      break;
    }
    case "tool.checked": {
      s.tools[d.name] = d;
      log(s, ev, d.usable ? "ok" : "warn", `道具「${d.title ?? d.name}」の照合: ${d.usable ? "使える" : "まだ使えない"}`);
      break;
    }
    case "hook.denied": {
      const r = ruleOf(d.rule);
      const item = { rule: r.id, ruleJa: r.ja, text: ev.text ?? "", tool: d.tool, hook: d.hook, agent: ev.agent ?? null, t: ev.t, seq: ev.seq };
      s.hooks.denied.push(item);
      s.denied = item;
      if (agent) agent.status = "止められた";
      log(s, ev, "hook", `hooks (${r.id} ${r.ja}): ${ev.text ?? ""}`);
      setActivity(s, ev, `hooks が止めた (${r.id} ${r.ja})`);
      break;
    }
    case "hook.blocked_stop": {
      const r = ruleOf(d.rule ?? "HK4");
      const text = ev.text || "判定がそろっていないので終われない";
      const count = typeof d.count === "number" && typeof d.limit === "number" ? ` (${d.count} / ${d.limit} 回)` : "";
      const item = { rule: r.id, text, t: ev.t, seq: ev.seq, count: d.count ?? null, limit: d.limit ?? null };
      s.hooks.blockedStops.push(item);
      s.denied = { rule: r.id, ruleJa: r.ja, text: text + count, t: ev.t, seq: ev.seq };
      log(s, ev, "hook", `hooks (${r.id} ${r.ja}): ${text}${count}`);
      break;
    }
    case "screen.focus": {
      s.focus.screen = d.screen ?? s.focus.screen;
      if (d.test_id) {
        s.focus.testId = d.test_id;
        const lc = (s.project?.load_cases ?? []).find((c) => c.test_id === d.test_id);
        if (lc) s.focus.loadCaseId = lc.load_case_id;
      }
      if (d.load_case_id) focusLoadCase(s, d.load_case_id, ev.t);
      s.focus.t = ev.t;
      break;
    }
    case "cost.updated": {
      if (d.cost_usd !== undefined) s.cost = d.cost_usd; // null stays null (unknown)
      break;
    }
    default:
      s.unknown += 1;
      log(s, ev, "muted", `(知らない event: ${ev.kind})`);
  }
  if (ev.text && s.touched?.seq === ev.seq && !["agent.said", "user.said", "hook.denied", "hook.blocked_stop", "run.finished"].includes(ev.kind)) {
    // Extra words on a record event are shown as is under its log line.
    s.log[s.touched.idx].note = ev.text;
    if (s.touched.idx !== s.log.length - 1) s.logRev += 1;
  }
  return s;
}

function list(s, kind) {
  return s.order[kind].map((id) => s[kind][id]);
}

// What the right pane and the 3D show for one load case of one design version:
// one prediction, the analyses that used it, their comparison, verdict and cause.
// A load case may have several predictions (one per quantity); the one followed
// is the one of the newest comparison, unless predictions came after that
// comparison: then the first of those, which waits for its analysis.
export function chainFor(s, loadCaseId, version) {
  const loadCase = (s.project?.load_cases ?? []).find((c) => c.load_case_id === loadCaseId) ?? null;
  const sameCase = (r) => r.load_case_id === loadCaseId && (version == null || r.design_version === version);
  const preds = list(s, "predictions").filter(sameCase);
  const predIds = new Set(preds.map((p) => p.prediction_id));
  const lastCmp = list(s, "comparisons").filter((c) => predIds.has(c.prediction_id)).at(-1) ?? null;
  const waiting = lastCmp ? preds.filter((p) => s.firstSeqOf[p.prediction_id] > s.firstSeqOf[lastCmp.comparison_id]) : preds;
  const pred = waiting[0] ?? (lastCmp ? s.predictions[lastCmp.prediction_id] : null) ?? null;
  const anas = list(s, "analyses").filter(sameCase);
  const usedPred = (a) => pred && (a.prediction_ids ?? []).includes(pred.prediction_id);
  const ana = anas.filter(usedPred).at(-1) ?? null;
  const running = Object.values(s.running).find((r) => r.load_case_id === loadCaseId && (!pred || (r.prediction_ids ?? []).includes(pred.prediction_id))) ?? null;
  const cmp = ana ? list(s, "comparisons").filter((c) => c.analysis_id === ana.analysis_id && c.prediction_id === pred.prediction_id).at(-1) ?? null : null;
  const verdict = cmp ? list(s, "verdicts").filter((v) => v.comparison_id === cmp.comparison_id).at(-1) ?? null : null;
  const cause = cmp ? (cmp.cause_id && s.causes[cmp.cause_id]) || list(s, "causes").filter((c) => c.comparison_id === cmp.comparison_id).at(-1) || null : null;
  // The 3D keeps the last finished analysis of this case even when a newer prediction waits for its own.
  const shown = ana ?? anas.at(-1) ?? null;
  return { loadCase, preds, pred, ana, shown, running, cmp, verdict, cause };
}

// A verdict made from an explained safe-side mismatch, not from a match (basis absent = match).
export function isSafeSide(verdict) {
  return verdict?.basis === "explained_safe_side";
}

// Which value a verdict was made from: the analysis (a match) or the prediction (safe side).
export function valueFromJa(verdict) {
  return (verdict?.value_from ?? (isSafeSide(verdict) ? "prediction" : "analysis")) === "prediction" ? "予測" : "解析";
}

// The verdicts behind a domain's judgement of one version: the judgement's verdict_ids
// when it has them, else the domain's verdicts whose prediction belongs to that version.
export function domainVerdicts(s, domain, version) {
  const j = s.judgements[`${version}|${domain}`];
  if (j?.verdict_ids) return j.verdict_ids.map((id) => s.verdicts[id]).filter(Boolean);
  return list(s, "verdicts").filter((v) => {
    if (v.domain !== domain) return false;
    const pred = s.predictions[s.comparisons[v.comparison_id]?.prediction_id];
    return pred?.design_version === version;
  });
}

export function judgementState(s, domain) {
  const picked = (s.project?.domain_picks ?? []).some((p) => p.domain === domain);
  const v = s.currentVersion;
  const j = v != null ? s.judgements[`${v}|${domain}`] : null;
  if (j) return { state: j.state, judgement: j, picked };
  return { state: picked ? "pending" : "off", judgement: null, picked };
}

export function roundsUsed(s) {
  return Math.max(0, s.versions.length - 1);
}

export { DOMAINS };
