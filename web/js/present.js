// The present mode (?present=1, or the 見せる / 作業台 switch): the 3D fills the screen and
// one card says what the scene is about — one number, where it comes from (a prediction or
// a result) next to its criterion, and whether the criterion was met in one sentence.
// Which number a scene shows is picked per scene (load case and analysis) from SCENES, the
// first that applies. Everything shown is a value or a judgement in the records; a scene
// whose record lacks a value leaves that line out.
//
// The words for the hooks' notices (what happened / what comes next) and whether a stopped
// action was later done are here too: the workbench uses them as well.

import { DOMAINS, DOMAIN_JA, JUDGED_JA, agentLongName, esc, fmt, fmtQ, keepAsides, fmtVU, isYesNo, judgedWords, opLabel, quantityName, ruleOf, verLabel } from "./labels.js";
import { FIELD_JA } from "./fields.js";
import { judgedVerdict, valueFromJa, judgementState } from "./state.js";
import { judgedResult, referenceResults } from "./judged.js";
import { assemblyRecord } from "./assembly.js";
import { versionChangeLine } from "./proposals.js";
import { markRecordWords } from "./benchnote.js";
import { pathCheckedWhy } from "./why_line.js";

// ---- hooks: what happened, what comes next, resolved or not ---------------------------

const HOOK_WORDS = {
  HK1: { what: "予測の前に解析しようとしたので、止めました", next: "先に手計算で予測を記録してから、解析します" },
  HK2: { what: "判定がすんでいないツールを使おうとしたので、止めました", next: "判定ずみのツールで解析します" },
  HK3: { what: "予測と解析が不一致のまま進もうとしたので、止めました", next: "不一致の原因を書いてから進みます" },
  HK4: { what: "判定がそろう前に終わろうとしたので、止めました", next: "残りの判定を出してから終わります" },
  // modules: HK6 (docs/contract.md: a designer changes only its own module; build_version is refused on a clash or a part outside its space)
  HK6: { what: "モジュールの持ち場の外を変えようとしたので、止めました", next: "自分のモジュールの中で直すか、パッケージに渡します" },
};

export function hookWords(rule) {
  const r = ruleOf(rule);
  return { id: r.id, ja: r.ja, ...(HOOK_WORDS[r.id] ?? { what: "hooks が止めました", next: "記録を見てください" }) };
}

const domainOfAgent = (agent) => (agent?.startsWith("cae.") ? agent.slice(4) : null);

// The seqs of the hooks' stops whose stopped action was done later in the records:
//   HK1  a prediction recorded after it (by the same domain when the agent says one)
//   HK2  an analysis that finished ok after it (same domain)
//   HK3  a cause recorded after it (same domain)
//   HK4  the run finished as done
export function resolvedHooks(s) {
  const out = new Set();
  const after = (kind, seq, domain, domainOf) =>
    (s.order[kind] ?? []).some((id) => s.firstSeqOf[id] > seq && (!domain || domainOf(s[kind][id]) === domain));
  const lcDomain = (id) => (s.project?.load_cases ?? []).find((c) => c.load_case_id === id)?.domain ?? null;
  for (const d of s.hooks.denied) {
    const dom = domainOfAgent(d.agent);
    const done =
      d.rule === "HK1"
        ? after("predictions", d.seq, dom, (p) => p.domain)
        : d.rule === "HK2"
          ? after("analyses", d.seq, dom, (a) => (a.status === "ok" ? lcDomain(a.load_case_id) : "—"))
          : d.rule === "HK3"
            ? after("causes", d.seq, dom, (c) => s.predictions[s.comparisons[c.comparison_id]?.prediction_id]?.domain ?? null)
            : d.rule === "HK4" && s.status === "done";
    if (done) out.add(d.seq);
  }
  for (const b of s.hooks.blockedStops) if (s.status === "done") out.add(b.seq);
  return out;
}

// ---- the cause of a mismatch, until its judgement is OK -------------------------------

// The newest cause (cause.recorded, the words Claude wrote) still open on the records applied so
// far: open until a verdict of the same load case that passed is recorded after the cause was
// first recorded. Null when none is open. { id, key, text, domain, version, lcTitle }
export function openCause(s) {
  const predOf = (c) => s.predictions?.[s.comparisons?.[c.comparison_id]?.prediction_id] ?? null;
  const passedAfter = (lc, seq) =>
    (s.order.verdicts ?? []).some((id) => s.firstSeqOf[id] > seq && s.verdicts[id]?.pass === true && s.verdicts[id]?.load_case_id === lc);
  for (const id of [...(s.order.causes ?? [])].reverse()) {
    const c = s.causes[id];
    const p = predOf(c);
    const lc = p?.load_case_id ?? null;
    if (lc && passedAfter(lc, s.firstSeqOf[id])) continue;
    const lcRec = (s.project?.load_cases ?? []).find((x) => x.load_case_id === lc);
    // who wrote it: Claude, or the fixed text of a scripted run (project.driver "scripted")
    return { id, key: `${id}|${s.seqOf[id]}`, seq: s.seqOf[id], text: roundLongDecimals(judgedWords(c.cause ?? "")), domain: p?.domain ?? lcRec?.domain ?? null,
      scripted: s.project?.driver === "scripted",
      // the load case's title up to its first ":" or bracket (the short name)
      version: p?.design_version ?? null, lcTitle: lcRec?.title ? String(lcRec.title).split(/[:：(（]/)[0].trim() || null : null };
  }
  return null;
}

export function causeNoteHtml(c) {
  const where = [c.domain ? DOMAIN_JA[c.domain] ?? c.domain : null, c.version != null ? `v${c.version}` : null, c.lcTitle].filter(Boolean).join("・");
  const who = c.scripted ? "決まった手順の文" : "Claude が書いた";
  // (the record's ids and English words in span.rec: ③'s strip leaves them to くわしく, benchnote.js)
  return `<small>不一致の原因 (${who})${where ? `・${esc(where)}` : ""}</small><div class="tx">${markRecordWords(c.text)}</div>`;
}

// ---- a verdict in words --------------------------------------------------------------

const limitWord = (op) => (op === "<=" || op === "<" ? "許容" : op === ">=" || op === ">" ? "必要" : "基準");

// 解析 123.6 MPa ≤ 許容 156.7 MPa → OK
export function verdictLine(verdict) {
  if (!verdict) return "";
  return `${valueFromJa(verdict)} ${fmtQ(verdict.value)} ${opLabel(verdict.op)} ${limitWord(verdict.op)} ${fmtQ(verdict.limit)} → ${verdict.pass ? "OK" : "NG"}`;
}

export function verdictSentence(verdict) {
  if (!verdict) return null;
  return { ok: !!verdict.pass, text: verdict.pass ? "OK" : "NG" };
}

// 基準: 最大応力 ≤ 156.7 MPa (the load case's criterion, before a verdict)
export function criterionLine(lc) {
  const c = lc?.criterion;
  if (!c?.limit) return "";
  return `基準: ${quantityName(c.quantity)} ${opLabel(c.op)} ${limitWord(c.op)} ${fmtQ(c.limit)}`;
}

// ---- what is going on now, in one sentence --------------------------------------------

export function nowSentence(s, chain, toolTitle) {
  if (!s.project) return "";
  const run = chain?.running ?? Object.values(s.running)[0] ?? null;
  if (run) {
    const lc = (s.project.load_cases ?? []).find((c) => c.load_case_id === run.load_case_id);
    return `いま: ${toolTitle(run.tool)} で「${lc?.title ?? run.load_case_id}」を解析しています`;
  }
  if (s.status === "done") return "実行が終わりました";
  if (s.status === "stopped") return "実行が途中で止まりました";
  if (s.status === "failed") return "実行が失敗しました";
  const act = s.activity;
  return act ? `いま: ${agentLongName(act.agent) || "HIRAKU"} — ${roundLongDecimals(act.text)}` : "";
}

// A number an agent wrote with 4 or more decimals (knee room -69.25456961280076 mm) to 4
// significant digits (-69.25); its whole part is kept as it is (12345.678 -> 12346). Only
// the record's number rounded; the words around it as written.
const LONG_DECIMAL = /(?<![\w.-])-?\d+\.\d{4,}(?![\d.])/g; // (not inside an id such as pred-0017)
export function roundLongDecimals(text) {
  return String(text ?? "").replace(LONG_DECIMAL, (m) => {
    const x = Number(m);
    if (!Number.isFinite(x)) return m;
    if (Math.abs(x) >= 1000) return String(Math.round(x));
    const r = Number(x.toPrecision(4));
    return Math.abs(r) >= 1e-4 || r === 0 ? String(r) : m;
  });
}

// ---- the scenes ------------------------------------------------------------------------

const resultOf = (ana, q) => (ana?.results ?? []).find((r) => r.quantity === q) ?? null;

function stepHeightOf(lc) {
  const p = lc?.params?.step_height;
  if (p === undefined || p === null) return null;
  return typeof p === "number" ? { value: p, unit: "m" } : p;
}

// The ok analyses of a load case, oldest first.
const analysesOf = (s, lcId) => (s.order.analyses ?? []).map((id) => s.analyses[id]).filter((a) => a.status === "ok" && a.load_case_id === lcId);

// The largest wheel force recorded for a load case: a front_wheel_force result, else the
// largest reaction vector of the shown analysis (|F| of its recorded components).
function stepForce(s, chain) {
  const anas = analysesOf(s, chain.loadCase?.load_case_id);
  for (const a of [...anas].reverse()) {
    const r = resultOf(a, "front_wheel_force");
    if (r) return { what: "前輪を押す力", value: r.unit === "N" ? `${fmt(r.value / 1000, 3)} kN` : fmtVU(r.value, r.unit), tool: a.tool };
  }
  let best = null;
  for (const v of chain.shown?.vectors ?? []) {
    if (v.kind !== "reaction" || !Array.isArray(v.vector_N)) continue;
    const mag = Math.hypot(...v.vector_N);
    if (!best || mag > best.mag) best = { mag, at: v.at };
  }
  return best ? { what: `段差から受ける反力${best.at ? ` (${best.at})` : ""}`, value: `${fmt(best.mag / 1000, 3)} kN`, tool: chain.shown.tool } : null;
}

// climbed (1 / 0) from the newest analysis of the load case that has it.
function climbedOf(s, lcId) {
  for (const a of analysesOf(s, lcId).reverse()) {
    const r = resultOf(a, "climbed");
    if (r) return r.value >= 0.5;
  }
  return null;
}

// The verdict a scene's load case is judged by in the version shown: the one the domain's
// judgement lists, else the newest of that version (state.js judgedVerdict), else the chain's.
function sceneVerdict(c) {
  const id = c.chain.loadCase?.load_case_id;
  return (id ? judgedVerdict(c.s, id, c.s.currentVersion) : null) ?? c.chain.verdict ?? null;
}

// The card's large number when there is a verdict: the verdict's value, named by the quantity
// its comparison compared (frame_shell: the hot-spot stress for a max_stress criterion).
function judgedNumber(c, verdict) {
  const cmp = c.s.comparisons[verdict.comparison_id];
  const ana = c.s.analyses[cmp?.analysis_id];
  const from = valueFromJa(verdict) === "予測" ? "予測 (手計算)" : `解析の結果${ana ? `・${c.toolTitle(ana.tool)}` : ""}`;
  return {
    what: `${JUDGED_JA.number}: ${quantityName(cmp?.quantity ?? verdict.quantity)}`,
    value: fmtQ(verdict.value),
    src: `${from}${ana?.design_version != null ? `・${verLabel(ana.design_version)}` : ""}`,
    tool: ana?.tool ?? null,
  };
}

// The verdict's line, with the tool it was made from when that is not the tool of the
// number shown (e.g. the beam analysis' verdict beside the shell's largest value).
function verdictCard(c, numTool = null) {
  const verdict = sceneVerdict(c);
  const { loadCase } = c.chain;
  if (!verdict) return { crit: criterionLine(loadCase), verdict: null };
  const tool = c.s.analyses[c.s.comparisons[verdict.comparison_id]?.analysis_id]?.tool;
  const from = tool && numTool && tool !== numTool ? `判定 (${c.toolTitle(tool)}): ` : "";
  // basis path_checked: why the tool's value decides, in one line (why_line.js)
  const why = pathCheckedWhy(c.s, verdict, c.formulaTitle)?.text ?? null;
  return { crit: from + verdictLine(verdict), verdict: verdictSentence(verdict), why };
}

// The quantity a verdict judged: its comparison's (frame_shell: the hot-spot stress for a
// max_stress criterion), else its own.
export function judgedQuantityOf(s, verdict) {
  return s.comparisons?.[verdict?.comparison_id]?.quantity ?? verdict?.quantity ?? null;
}

// A record id in words (chg-0013, ver-0077, ana-0083, ...): such words are shown only under ?dev=1.
const RECORD_ID = /\b[a-z]+-\d{3,}\b/;

const SCENES = [
  {
    key: "grid",
    title: "解析結果 (分野ごと)",
    when: (c) => c.tab === "grid",
    card(c) {
      const picked = DOMAINS.map((d) => [d, judgementState(c.s, d)]).filter(([, j]) => j.picked);
      if (!picked.length) return null;
      const green = picked.filter(([, j]) => j.state === "green");
      const red = picked.filter(([, j]) => j.state === "red");
      const wait = picked.length - green.length - red.length;
      const names = (list) => list.map(([d]) => DOMAIN_JA[d]).join("・");
      return {
        what: `判定が OK の分野 (${verLabel(c.s.currentVersion)})`,
        value: `${green.length} / ${picked.length}`,
        src: "各分野の判定の記録",
        crit: red.length ? `NG の分野: ${names(red)}` : wait ? `判定を待っている分野: ${wait}` : "",
        verdict: wait && !red.length ? null : { ok: !red.length, text: red.length ? `NG ${red.length} 分野` : "全分野 OK" },
      };
    },
  },
  {
    key: "assembly",
    title: "組み立て",
    when: (c) => c.tab === "A7" || (Array.isArray(c.chain.shown?.assembly) && c.chain.shown.assembly.length > 0),
    card(c) {
      const rec = assemblyRecord(c.s);
      if (!rec) return null;
      const n = rec.interferences.length;
      const lc = (c.s.project?.load_cases ?? []).find((x) => x.load_case_id === rec.ana.load_case_id);
      // OK / NG only from a verdict on the number shown (the interference count); a verdict of the
      // load case on another quantity (e.g. the gap count) is not put beside it
      const v = Object.values(c.s.verdicts ?? {}).filter((x) => x.load_case_id === rec.ana.load_case_id && judgedQuantityOf(c.s, x) === "interference_count").at(-1) ?? null;
      const critLc = lc?.criterion?.quantity === "interference_count" ? lc : null;
      return {
        what: "部品どうしの干渉",
        value: `${n} か所`,
        src: `解析の結果・手順 ${rec.steps.length}`,
        crit: v ? verdictLine(v) : criterionLine(critLc),
        verdict: verdictSentence(v) ?? { ok: null, text: n === 0 ? "干渉は記録されていない" : `干渉が ${n} か所 記録された` },
        extra: rec.interferences.slice(0, 2).map((x) => `${x.a} × ${x.b}・${fmt(x.depth_mm, 3)} mm`),
      };
    },
  },
  {
    key: "shell",
    title: "シェルのつなぎ目",
    when: (c) => !!c.contour?.mesh,
    card(c) {
      const ct = c.contour;
      const name = ct.field.label ?? FIELD_JA[ct.kind] ?? ct.kind;
      const v = ct.compare ? ct.compare.nowVersion : c.chain.shown?.design_version;
      const fieldMax = fmtVU(ct.frameMax ?? ct.fieldMax, ct.field.unit);
      const maxima = ct.compare
        ? [`補強の前 (${verLabel(ct.compare.version)}): 最大 ${fmtVU(ct.compare.before.fieldMax, ct.field.unit)}`, `補強の後 (${verLabel(ct.compare.nowVersion)}): 最大 ${fmtVU(ct.fieldMax, ct.field.unit)}`]
        : [];
      // what the version changed, from the records' values (proposals.js versionChangeLine); the
      // packager's own words (change_reason, with record ids) only under ?dev=1
      // (a version with no recorded change, e.g. parts added by a designer: its change_reason as
      // written when it names no record id)
      const saidRaw = ct.compare ? c.s.designs?.[ct.compare.nowVersion]?.change_reason : null;
      const said = saidRaw ? judgedWords(saidRaw) : saidRaw;
      const why = ct.compare ? versionChangeLine(c.s, ct.compare.nowVersion) ?? (said && !RECORD_ID.test(said) ? said : null) : null;
      const dev = said && said !== why ? [`${verLabel(ct.compare.nowVersion)} の記録の文: ${said}`] : [];
      // The large number is the judged quantity: the verdict's value, else (no verdict yet) the
      // shown analysis' judged result. The coloured field's largest value goes under 参考 unless
      // the field is the judged quantity itself (judged.js fieldRole).
      const verdict = sceneVerdict(c);
      const r = verdict ? null : judgedResult(c.s.analyses[ct.analysisId], c.chain.loadCase?.criterion);
      const num = verdict
        ? judgedNumber(c, verdict)
        : r
          ? { what: `${JUDGED_JA.number}: ${quantityName(r.quantity)}`, value: fmtVU(r.value, r.unit), src: `解析の結果・${ct.toolTitle ?? ""}・${verLabel(v)}`, tool: ct.tool }
          : null;
      if (num && ct.role !== "judged") {
        return { ...num, ...verdictCard(c, num.tool), ref: [`${name} の最大 ${fieldMax}`, ...maxima], extra: why ? [`変えたこと: ${why}`] : [], dev };
      }
      return {
        ...(num ?? { what: `${name} の最大`, value: fieldMax, src: `解析の結果・${ct.toolTitle ?? ""}・${verLabel(v)}` }),
        ...verdictCard(c, num?.tool ?? ct.tool),
        extra: [...maxima, ...(why ? [`変えたこと: ${why}`] : [])],
        dev,
      };
    },
  },
  {
    key: "step",
    title: "段差",
    when: (c) => stepHeightOf(c.chain.loadCase) !== null,
    card(c) {
      const f = stepForce(c.s, c.chain);
      const h = stepHeightOf(c.chain.loadCase);
      const climbed = climbedOf(c.s, c.chain.loadCase.load_case_id);
      const hText = h.unit === "m" ? `${fmt(h.value * 1000, 4)} mm` : fmtQ(h);
      // a verdict on a number (not a yes / no such as climbed): its value is the large number,
      // the wheel's force beside it for reference
      const verdict = sceneVerdict(c);
      const num = verdict && !isYesNo(verdict.quantity) ? judgedNumber(c, verdict) : null;
      const base = verdictCard(c, num ? num.tool : f?.tool);
      // the wheel's force as the large number is not what the verdict judged (climbed, a yes / no):
      // it says 参考 when a verdict is shown beside it
      const refWhat = (w) => (base.verdict || climbed !== null ? `${JUDGED_JA.refTag}: ${w}` : w);
      return {
        ...(num ?? { what: refWhat(f?.what ?? ""), value: f?.value ?? "", src: f ? `解析の結果・${c.toolTitle(f.tool)}` : "" }),
        ref: num && f ? [`${f.what} ${f.value}`] : [],
        ...base,
        // the climb, when a record says it; otherwise the verdict's sentence
        verdict: climbed === null ? base.verdict : { ok: climbed, text: `段差 ${hText} を${climbed ? "越えられる" : "越えられない"}` },
        extra: climbed !== null && base.verdict ? [`判定: ${base.verdict.text}`] : [],
      };
    },
  },
  {
    key: "result",
    title: "結果",
    when: (c) => !!c.chain.loadCase,
    card(c) {
      const { shown, pred } = c.chain;
      const verdict = sceneVerdict(c);
      // before a verdict: the judged quantity's result when the analysis has it, else its first
      // result under 参考 (not the number the criterion is held to)
      const jr = judgedResult(shown, c.chain.loadCase?.criterion);
      const r = jr ?? shown?.results?.[0];
      const num = verdict
        ? judgedNumber(c, verdict)
        : r
          ? { what: jr ? `${JUDGED_JA.number}: ${quantityName(r.quantity)}` : `${JUDGED_JA.refTag}: ${quantityName(r.quantity)}`, value: fmtVU(r.value, r.unit), src: `解析の結果・${c.toolTitle(shown.tool)}` }
          : pred
            ? { what: quantityName(pred.quantity), value: fmtVU(pred.value, pred.unit), src: "予測 (手計算)" }
            : null;
      if (!num) return null;
      return { ...num, ...verdictCard(c) };
    },
  },
];

// ctx: { s, chain, contour, tab, toolTitle } -> { key, title, card } (card null: nothing to say yet)
export function presentScene(ctx) {
  const scene = SCENES.find((x) => x.when(ctx));
  if (!scene) return { key: "none", title: "", card: null };
  const card = scene.card(ctx);
  return { key: scene.key, title: scene.title, card: card && LOAD_CASE_SCENES.has(scene.key) ? withNotJudged(ctx, card) : card };
}

const LOAD_CASE_SCENES = new Set(["shell", "step", "result"]);

// A load case's card gets, under 参考, the analyses' numbers of the criterion's quantity that the
// verdict does not use (frame_shell's element max_stress: judged.js referenceResults), from the
// verdict's analysis and the one shown; and on a strength load case, the line that the mounts
// are not judged (labels.js JUDGED_JA.mountsNotJudged).
function withNotJudged(c, card) {
  const lc = c.chain.loadCase;
  const verdict = sceneVerdict(c);
  const anas = [c.s.analyses[c.s.comparisons[verdict?.comparison_id]?.analysis_id], c.contour ? c.s.analyses[c.contour.analysisId] : null, c.chain.shown];
  const seen = new Set();
  const lines = [];
  for (const a of anas) {
    if (!a || seen.has(a.analysis_id)) continue;
    seen.add(a.analysis_id);
    for (const r of referenceResults(a, lc?.criterion)) lines.push(`${quantityName(r.quantity)} (${c.toolTitle(a.tool)}) ${fmtVU(r.value, r.unit)}`);
  }
  const out = lines.length ? { ...card, ref: [...(card.ref ?? []), ...lines] } : card;
  return lc?.domain === "strength" ? { ...out, note: JUDGED_JA.mountsNotJudged } : out;
}

// opts.dev (?dev=1): the card's record words with ids (card.dev) folded under くわしく; else not shown.
export function presentCardHtml(scene, now, opts = {}) {
  const c = scene.card;
  const head = `<div class="pc-now">${keepAsides(now)}</div>`;   // 「… = 1 (shape example)」: not broken inside the brackets
  if (!c) return head;
  const num = c.value
    ? `<div class="pc-num" data-kind="number"><div class="pc-what">${esc(c.what)}</div><div class="pc-val">${esc(c.value)}</div>${c.src ? `<div class="pc-src">${esc(c.src)}</div>` : ""}</div>`
    : "";
  return (
    head +
    `<div class="pc-scene">${esc(scene.title)}</div>` +
    num +
    (c.crit ? `<div class="pc-crit">${esc(c.crit)}</div>` : "") +
    // ok null: a statement that is not a verdict (no colour)
    (c.verdict ? `<div class="pc-verdict ${c.verdict.ok == null ? "plain" : c.verdict.ok ? "ok" : "bad"}">${esc(c.verdict.text)}</div>` : "") +
    (c.why ? `<div class="pc-why" data-role="why">${esc(c.why)}</div>` : "") +
    // values shown beside the judged one that the verdict does not use, small and under their heading
    (c.ref?.length ? `<div class="pc-ref" data-role="reference"><div class="pc-ref-h">${esc(JUDGED_JA.ref)}</div><ul>${c.ref.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>` : "") +
    (c.note ? `<div class="pc-note" data-role="not-judged">${esc(c.note)}</div>` : "") +
    (c.extra?.length ? `<ul class="pc-extra">${c.extra.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "") +
    (opts.dev && c.dev?.length ? `<details class="more pc-dev" data-role="dev"><summary>くわしく</summary>${c.dev.map((x) => `<div class="sub">${esc(x)}</div>`).join("")}</details>` : "")
  );
}
