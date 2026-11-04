// The present mode (?present=1, or the 見せる / 作業台 switch): the 3D fills the screen and
// one card says what the scene is about — one number, where it comes from (a prediction or
// a result) next to its criterion, and whether the criterion was met in one sentence.
// Which number a scene shows is picked per scene (load case and analysis) from SCENES, the
// first that applies. Everything shown is a value or a judgement in the records; a scene
// whose record lacks a value leaves that line out.
//
// The words for the hooks' notices (what happened / what comes next) and whether a stopped
// action was later done are here too: the workbench uses them as well.

import { DOMAINS, DOMAIN_JA, agentLongName, esc, fmt, fmtQ, fmtVU, opLabel, quantityName, ruleOf } from "./labels.js";
import { FIELD_JA } from "./fields.js";
import { valueFromJa, judgementState } from "./state.js";
import { assemblyRecord } from "./assembly.js";

// ---- hooks: what happened, what comes next, resolved or not ---------------------------

const HOOK_WORDS = {
  HK1: { what: "予測の前に解析しようとしたので、止めました", next: "先に手計算で予測を記録してから、解析します" },
  HK2: { what: "照合がすんでいないツールを使おうとしたので、止めました", next: "照合ずみのツールで解析します" },
  HK3: { what: "予測と解析が食い違ったまま進もうとしたので、止めました", next: "食い違いの原因を書いてから進みます" },
  HK4: { what: "判定がそろう前に終わろうとしたので、止めました", next: "残りの判定を出してから終わります" },
  // modules: HK6 (docs/contract.md: a designer changes only its own module; build_version is refused on a clash or a part outside its space)
  HK6: { what: "モジュールの持ち場の外を変えようとしたので、止めました", next: "自分のモジュールの中で直すか、パッケージ役に渡します" },
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

// ---- a verdict in words --------------------------------------------------------------

const limitWord = (op) => (op === "<=" || op === "<" ? "許容" : op === ">=" || op === ">" ? "必要" : "基準");

// 解析 123.6 MPa ≤ 許容 156.7 MPa → 合格
export function verdictLine(verdict) {
  if (!verdict) return "";
  return `${valueFromJa(verdict)} ${fmtQ(verdict.value)} ${opLabel(verdict.op)} ${limitWord(verdict.op)} ${fmtQ(verdict.limit)} → ${verdict.pass ? "合格" : "不合格"}`;
}

export function verdictSentence(verdict) {
  if (!verdict) return null;
  return { ok: !!verdict.pass, text: verdict.pass ? "基準を満たした" : "基準を満たさない" };
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
  return act ? `いま: ${agentLongName(act.agent) || "HIRAKU"} — ${act.text}` : "";
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

// The verdict's line, with the tool it was made from when that is not the tool of the
// number shown (e.g. the beam analysis' verdict beside the shell's largest value).
function verdictCard(c, numTool = null) {
  const { verdict, loadCase } = c.chain;
  if (!verdict) return { crit: criterionLine(loadCase), verdict: null };
  const tool = c.s.analyses[c.s.comparisons[verdict.comparison_id]?.analysis_id]?.tool;
  const from = tool && numTool && tool !== numTool ? `判定 (${c.toolTitle(tool)}): ` : "";
  return { crit: from + verdictLine(verdict), verdict: verdictSentence(verdict) };
}

const SCENES = [
  {
    key: "grid",
    title: "全分野の格子",
    when: (c) => c.tab === "grid",
    card(c) {
      const picked = DOMAINS.map((d) => [d, judgementState(c.s, d)]).filter(([, j]) => j.picked);
      if (!picked.length) return null;
      const green = picked.filter(([, j]) => j.state === "green");
      const red = picked.filter(([, j]) => j.state === "red");
      const wait = picked.length - green.length - red.length;
      const names = (list) => list.map(([d]) => DOMAIN_JA[d]).join("・");
      return {
        what: `判定が合格の分野 (版 ${c.s.currentVersion ?? "—"})`,
        value: `${green.length} / ${picked.length}`,
        src: "各分野の判定の記録",
        crit: red.length ? `基準を満たさない分野: ${names(red)}` : wait ? `判定を待っている分野: ${wait}` : "",
        verdict: wait && !red.length ? null : { ok: !red.length, text: red.length ? "基準を満たさない分野がある" : "全分野で基準を満たした" },
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
      const v = Object.values(c.s.verdicts ?? {}).filter((x) => x.load_case_id === rec.ana.load_case_id).at(-1) ?? null;
      return {
        what: "部品どうしの干渉",
        value: `${n} か所`,
        src: `解析の結果・手順 ${rec.steps.length}`,
        crit: v ? verdictLine(v) : criterionLine(lc),
        verdict: verdictSentence(v) ?? { ok: n === 0, text: n === 0 ? "干渉は記録されていない" : `干渉が ${n} か所 記録された` },
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
      const extra = [];
      if (ct.compare) {
        extra.push(`補強の前 (版 ${ct.compare.version}): 最大 ${fmtVU(ct.compare.before.fieldMax, ct.field.unit)}`);
        extra.push(`補強の後 (版 ${ct.compare.nowVersion}): 最大 ${fmtVU(ct.fieldMax, ct.field.unit)}`);
        const why = c.s.designs?.[ct.compare.nowVersion]?.change_reason;
        if (why) extra.push(`変えたこと: ${why}`);
      }
      return {
        what: `${name} の最大`,
        value: fmtVU(ct.frameMax ?? ct.fieldMax, ct.field.unit),
        src: `解析の結果・${ct.toolTitle ?? ""}・版 ${v ?? "—"}`,
        ...verdictCard(c, ct.tool),
        extra,
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
      const base = verdictCard(c, f?.tool);
      return {
        what: f?.what ?? "",
        value: f?.value ?? "",
        src: f ? `解析の結果・${c.toolTitle(f.tool)}` : "",
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
      const { verdict, shown, pred } = c.chain;
      const r = shown?.results?.[0];
      const num = verdict
        ? { what: quantityName(verdict.quantity), value: fmtQ(verdict.value), src: `${valueFromJa(verdict)}の値` }
        : r
          ? { what: quantityName(r.quantity), value: fmtVU(r.value, r.unit), src: `解析の結果・${c.toolTitle(shown.tool)}` }
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
  return { key: scene.key, title: scene.title, card: scene.card(ctx) };
}

export function presentCardHtml(scene, now) {
  const c = scene.card;
  const head = `<div class="pc-now">${esc(now)}</div>`;
  if (!c) return head;
  const num = c.value
    ? `<div class="pc-num" data-kind="number"><div class="pc-what">${esc(c.what)}</div><div class="pc-val">${esc(c.value)}</div>${c.src ? `<div class="pc-src">${esc(c.src)}</div>` : ""}</div>`
    : "";
  return (
    head +
    `<div class="pc-scene">${esc(scene.title)}</div>` +
    num +
    (c.crit ? `<div class="pc-crit">${esc(c.crit)}</div>` : "") +
    (c.verdict ? `<div class="pc-verdict ${c.verdict.ok ? "ok" : "bad"}">${esc(c.verdict.text)}</div>` : "") +
    (c.extra?.length ? `<ul class="pc-extra">${c.extra.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "")
  );
}
