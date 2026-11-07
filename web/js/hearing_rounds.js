// ② 確認したいこと: when each hearing of a record happened, and its sentences in the screen's words.
//
// hearingRounds(events): the record's hearings in time order, one per hearing.asked (with the
//   hearing.answered that follows it) or per hearing.answered that came with nothing asked (the
//   run went on with the request's values: the first hearing of a saved run, before its first
//   design). Each has the design version at that time (null: before the first design) and the
//   agent that asked (the record's agent).
// roundWhen / roundTitle / roundLine: what the A2 heading says of a round (app.js renderA2When).
// plainHearingEvents(events): the same events with the hearing's sentences as the screen says
//   them: 「版 2」 → 「v2」, a requirement's number (Q54) → its name, a part's id → its name (from
//   the design of that time). The parts' ids in the data (the 3D lights them) are left as they are.

import { agentName } from "./labels.js";
import { RULE_CHOICE_JA, tradeoffChoiceJa } from "./intake/model.js";

export function hearingRounds(events) {
  const rounds = [];
  let version = null;
  for (const e of events ?? []) {
    if (e.kind === "design.created") version = e.data?.design_version ?? version;
    else if (e.kind === "hearing.asked") rounds.push({ t: e.t, asked: e, answered: null, version, agent: e.agent ?? "lead" });
    else if (e.kind === "hearing.answered") {
      const last = rounds.at(-1);
      if (last && last.asked && !last.answered) last.answered = e;
      else rounds.push({ t: e.t, asked: null, answered: e, version, agent: e.agent ?? "lead" });
    }
  }
  return rounds;
}

// The round on screen at t: the newest one that began by then (null: none yet).
export function roundAt(rounds, t) {
  let at = null;
  for (const r of rounds) if (r.t <= t + 1e-9) at = r;
  return at;
}

export const isFirstHearing = (r) => r.version === null;
export const roundWhen = (r) => (r.version === null ? "実行の前" : `v${r.version} の途中`);

// The heading over the questions: when they were asked and by whom.
export function roundTitle(r) {
  const who = agentName(r.agent) || "HIRAKU";
  if (!r.asked) return `${roundWhen(r)}に決めたこと${isFirstHearing(r) ? " (最初のヒアリング)" : ""}: 聞かずに進めました`;
  return `${roundWhen(r)}で ${who} が聞いたこと${isFirstHearing(r) ? " (最初のヒアリング)" : ""}`;
}

// One line: what was asked and what was answered, short.
export function roundLine(r) {
  const d = r.asked?.data ?? {};
  const asked = [
    d.questions?.length ? `確認 ${d.questions.length} 件` : "",
    d.rule_questions?.length ? `ルール ${d.rule_questions.length} 件` : "",
    d.tradeoff_questions?.length ? `優先の相談 ${d.tradeoff_questions.length} 件` : "",
  ].filter(Boolean).join("・");
  const a = r.answered?.data ?? null;
  let answered = "";
  if (a) {
    const own = (a.answers ?? []).filter((x) => x.source === "hearing").length;
    const std = (a.answers ?? []).filter((x) => x.source === "default").length;
    const rules = (a.rule_answers ?? []).map((x) => RULE_CHOICE_JA[x.choice] ?? x.choice);
    const trades = (a.tradeoff_answers ?? []).map((x) => {
      const q = (d.tradeoff_questions ?? []).find((y) => y.tradeoff_id === x.tradeoff_id);
      return q ? tradeoffChoiceJa(q, x.choice) : x.choice;
    });
    answered = [own ? `答え ${own} 件` : "", std ? `標準の値 ${std} 件` : "", ...rules, ...trades].filter(Boolean).join("・");
  }
  const head = r.asked ? asked || "質問" : "質問なし";
  return `${roundWhen(r)}${isFirstHearing(r) ? " (最初のヒアリング)" : ""}: ${head}${a ? ` → ${answered || "答え無し"}` : " → 答えを待っている"}`;
}

// ---- the sentences in the screen's words -----------------------------------------------------
const shortName = (name, id) => String(name ?? id).replace(/\s*[（(].*$/, "") || id;
const escRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// (a name in Japanese takes no space between it and the Japanese beside it, as an id or a number did)
const JA = "[^\\x00-\\x7F]";
const bare = (word, tail) => new RegExp(`(?:(?<=${JA}) )?(?<![A-Za-z0-9_])${word}${tail}(?: (?=${JA}))?`, "g");

export function plainHearingText(text, ctx) {
  if (typeof text !== "string" || !text) return text;
  let out = text.replace(/版\s*(\d+)/g, "v$1");
  for (const [key, name] of ctx.reqs) {
    out = out.replace(new RegExp(`${escRe(name)}\\s*${key}(?![0-9])(?: (?=${JA}))?`, "g"), () => name);
    out = out.replace(bare(key, "(?![0-9])"), () => name);
  }
  for (const [id, name] of ctx.parts) {
    out = out.replace(new RegExp(`\\s*[（(]${escRe(id)}[)）]`, "g"), "");
    out = out.replace(bare(escRe(id), "(?![A-Za-z0-9_])"), () => name);
  }
  return out;
}

const withText = (list, ctx) => (Array.isArray(list) ? list.map((q) => (q && typeof q.text === "string" ? { ...q, text: plainHearingText(q.text, ctx) } : q)) : list);

// Every event as it was, the hearings' (and their notes') sentences in the screen's words.
export function plainHearingEvents(events) {
  const reqs = new Map(); // Qnn -> the requirement's name
  let parts = new Map(); // part id -> its short name, in the design of the time (longest ids first)
  const out = [];
  for (const e of events ?? []) {
    const d = e.data;
    if (e.kind === "run.started") for (const r of d?.requirements ?? []) if (/^Q\d{2}$/.test(r.key ?? "") && r.name) reqs.set(r.key, r.name);
    if (e.kind === "hearing.answered") for (const r of d?.answers ?? []) if (/^Q\d{2}$/.test(r.key ?? "") && r.name && !reqs.has(r.key)) reqs.set(r.key, r.name);
    if (e.kind === "design.created") {
      const list = (d?.parts ?? []).filter((p) => typeof p?.id === "string" && p.id).map((p) => [p.id, shortName(p.name, p.id)]);
      parts = new Map(list.sort((a, b) => b[0].length - a[0].length));
    }
    if (e.kind === "hearing.asked") {
      for (const q of d?.questions ?? []) if (/^Q\d{2}$/.test(q.req_key ?? "") && q.name && !reqs.has(q.req_key)) reqs.set(q.req_key, q.name);
      const ctx = { reqs, parts };
      out.push({ ...e, text: plainHearingText(e.text, ctx), data: { ...d, questions: withText(d?.questions, ctx), rule_questions: withText(d?.rule_questions, ctx), tradeoff_questions: withText(d?.tradeoff_questions, ctx) } });
      continue;
    }
    if (e.kind === "hearing.answered" && typeof e.text === "string") {
      out.push({ ...e, text: plainHearingText(e.text, { reqs, parts }) });
      continue;
    }
    out.push(e);
  }
  return out;
}
