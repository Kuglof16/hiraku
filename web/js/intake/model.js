// The view model of the intake screens (A1, A2, A5), built from events.jsonl alone.
// Pure data: no DOM here, so it runs the same live, in a replay and under node.
//
// Events used:
//   run.started        project: want, flags, speed_class, limits, requirements, domain_picks
//   user.said / agent.said (lead, before the first design)   the chat of A1
//   domains.picked     the picked domains with reasons and tests, the ones left off   (A1)
//   hearing.asked / hearing.answered   the questions, the answers and the defaults     (A2)
//   knowledge.imported rules of a document; the last event for a doc_id wins            (A5)
//   rule_check.recorded  a rule applied to a design; the newest design version wins     (A5)
//   design.created     the chat ends, rules have a design to be applied to

import { DOMAINS, CATEGORIES } from "./data.js";

const NOTE_KINDS = new Set(["domains.picked", "hearing.asked", "hearing.answered"]); // what A1's chat tells; the knowledge has A5

export function emptyModel() {
  return {
    run: null,
    want: "",
    flags: {},
    speedClass: null,
    limits: { round: null, cost: null },
    chat: [],
    designVersion: 0,
    domains: null, // the domains_picked data, or the project's picks until one arrives
    domainsFromProject: false,
    hearing: { asked: null, answered: null },
    requirements: [], // {key, name, value, unit, source, trust, basis, note, fresh}
    docs: new Map(), // doc_id -> {doc, rules}
    checks: new Map(), // rule_id -> rule_check
    knowledgeSeq: 0,
  };
}

function putRequirement(m, r, fresh) {
  const row = { ...r, fresh };
  const i = m.requirements.findIndex((x) => x.key === r.key);
  if (i >= 0) m.requirements[i] = row;
  else m.requirements.push(row);
}

export function apply(m, e) {
  const d = e.data;
  switch (e.kind) {
    case "run.started":
      m.run = d;
      m.want = d.want;
      m.flags = d.flags || {};
      m.speedClass = d.speed_class || null;
      m.limits = { round: d.round_limit ?? null, cost: d.cost_limit_usd ?? null };
      m.requirements = [];
      for (const r of d.requirements || []) putRequirement(m, r, false);
      m.domains = {
        want: d.want,
        flags: d.flags || {},
        speed_class: d.speed_class,
        picks: (d.domain_picks || []).map((p) => ({ ...p, test_ids: p.test_ids || null })),
        off: [],
        assembly_test_ids: [],
      };
      m.domainsFromProject = true;
      m.chat.push({ who: "user", text: d.want, t: e.t });
      break;
    case "user.said":
      if (!m.designVersion && e.text) m.chat.push({ who: "user", text: e.text, t: e.t });
      break;
    case "agent.said":
      if (!m.designVersion && e.agent === "lead" && e.text) m.chat.push({ who: "lead", text: e.text, t: e.t });
      break;
    case "domains.picked":
      m.domains = d;
      m.domainsFromProject = false;
      m.flags = d.flags;
      m.speedClass = d.speed_class || m.speedClass;
      break;
    case "hearing.asked":
      m.hearing.asked = d;
      m.hearing.answered = null; // a new asking: the earlier answers are in the requirements already
      break;
    case "hearing.answered":
      m.hearing.answered = d;
      for (const r of d.answers) putRequirement(m, r, true);
      break;
    case "knowledge.imported":
      m.docs.set(d.doc.doc_id, d);
      m.knowledgeSeq = e.seq;
      break;
    case "design.created":
      m.designVersion = d.design_version;
      break;
    case "rule_check.recorded": {
      const old = m.checks.get(d.rule_id);
      if (!old || d.design_version >= old.design_version) m.checks.set(d.rule_id, d);
      break;
    }
    default:
  }
  if (NOTE_KINDS.has(e.kind) && e.text) m.chat.push({ who: "note", text: e.text, t: e.t, kind: e.kind });
}

export function buildModel(events) {
  const m = emptyModel();
  for (const e of events) apply(m, e);
  return m;
}

// ----------------------------------------------------------------------------- A1

/** One card per analysis domain, in the contract's order: lit (picked), off, or not yet decided. */
export function domainCards(m) {
  const picks = new Map((m.domains?.picks || []).map((p) => [p.domain, p]));
  const off = new Map((m.domains?.off || []).map((o) => [o.domain, o]));
  return DOMAINS.map(({ name, ja }) => {
    const p = picks.get(name);
    if (p) {
      return { domain: name, ja, state: "lit", shownName: p.shown_name, renamed: p.shown_name !== ja, reason: p.reason,
               testIds: p.test_ids, testCount: p.test_ids ? p.test_ids.length : null };
    }
    const o = off.get(name);
    if (o) return { domain: name, ja, state: "off", shownName: ja, reason: o.reason, testIds: [], testCount: 0 };
    const decided = m.domains && !(m.domainsFromProject && !m.domains.picks.length); // a project with no picks has not decided yet
    return { domain: name, ja, state: decided ? "off" : "wait", shownName: ja, reason: "", testIds: [], testCount: 0 };
  });
}

// ----------------------------------------------------------------------------- A2

export const SOURCE_JA = { user: "ユーザー", hearing: "ヒアリング", default: "既定値" };

/** The hearing as screen rows: each question with its answer once there is one. */
export function hearingRows(m) {
  const asked = m.hearing.asked?.questions || [];
  const answers = new Map((m.hearing.answered?.answers || []).map((a) => [a.key, a]));
  return asked.map((q) => {
    const a = answers.get(q.req_key);
    return { ...q, answer: a || null, state: a ? (a.source === "hearing" ? "answered" : "defaulted") : m.hearing.answered ? "open" : "asking" };
  });
}

/** The rule hits the lead asked about (hearing.asked rule_questions), each with the person's answer once there is one. */
export function ruleRows(m) {
  const asked = m.hearing.asked?.rule_questions || [];
  const answers = new Map((m.hearing.answered?.rule_answers || []).map((a) => [a.rule_id, a]));
  return asked.map((q) => {
    const a = answers.get(q.rule_id) || null;
    return { ...q, choices: q.choices || ["revise_design", "drop_rule"], answer: a, state: a ? "answered" : m.hearing.answered ? "open" : "asking" };
  });
}

export const RULE_CHOICE_JA = { revise_design: "設計を直す", drop_rule: "この規則を外す" };

/** Requirements that rest on an assumption (trust 3): they stay in the report as "not checked". */
export function assumptions(m) {
  return m.requirements.filter((r) => r.trust === 3);
}

// ----------------------------------------------------------------------------- A5

export function allRules(m) {
  const out = [];
  for (const rec of m.docs.values()) for (const r of rec.rules) out.push({ ...r, docName: rec.doc.filename });
  return out;
}

/** What a rule did on the newest design: targets, hits, rules that could not be judged. */
export function ruleState(m, rule) {
  const c = m.checks.get(rule.rule_id);
  if (!c) return { checked: false, targets: 0, hits: [], notEvaluable: [], pass: null };
  return { checked: true, targets: c.targets.length, hits: c.hits, notEvaluable: c.not_evaluable || [], pass: c.pass, designVersion: c.design_version };
}

/**
 * The tree of A5. mode "category": category -> rules. mode "source": origin -> document -> category -> rules.
 * filter: "all" | "builtin" | "dropped" | "hit"; query: text to find in the rule or its formula;
 * disabled: Set of rule ids the person switched off.
 */
export function ruleTree(m, { mode = "category", filter = "all", query = "", disabled = new Set() } = {}) {
  const q = query.trim().toLowerCase();
  const rules = allRules(m).filter((r) => {
    if (filter === "builtin" && r.origin !== "builtin") return false;
    if (filter === "dropped" && r.origin !== "dropped") return false;
    if (filter === "hit" && !ruleState(m, r).hits.length) return false;
    if (q && !`${r.rule_id} ${r.text} ${r.check} ${r.basis}`.toLowerCase().includes(q)) return false;
    return true;
  });
  const node = (key, label, children, extra = {}) => ({ key, label, children, ...extra });
  const leaf = (r) => ({ key: r.rule_id, label: r.text, rule: r, state: ruleState(m, r), off: disabled.has(r.rule_id) });
  const byCategory = (list) =>
    CATEGORIES.map(({ name, ja }) => {
      const mine = list.filter((r) => r.category === name);
      return mine.length ? node(name, ja, mine.map(leaf)) : null;
    }).filter(Boolean);
  let top;
  if (mode === "source") {
    const groups = [
      ["builtin", "基本の制約"],
      ["dropped", "取り込んだファイル"],
    ];
    top = groups
      .map(([origin, label]) => {
        const mine = rules.filter((r) => r.origin === origin);
        if (!mine.length) return null;
        const docs = [...new Set(mine.map((r) => r.doc_id))].map((id) => {
          const inDoc = mine.filter((r) => r.doc_id === id);
          return node(id, inDoc[0].docName, byCategory(inDoc));
        });
        return node(origin, label, origin === "builtin" ? byCategory(mine) : docs);
      })
      .filter(Boolean);
  } else {
    top = byCategory(rules);
  }
  const count = (n) => (n.rule ? 1 : n.children.reduce((s, c) => s + count(c), 0));
  const hits = (n) => (n.rule ? (n.state.hits.length ? 1 : 0) : n.children.reduce((s, c) => s + hits(c), 0));
  const stamp = (n) => {
    if (!n.rule) n.children.forEach(stamp);
    n.count = count(n);
    n.hitCount = hits(n);
    return n;
  };
  return top.map(stamp);
}

/** A value with its unit: 50 kg. Numbers are shown as they came (the code did the arithmetic). */
/** The values a rule's check asks for, in the order they appear: [{name, label, inDesigns}].
 *  `facts` is data.js FACTS; requirement numbers (req_Qnn) and design variables (var_x) come with the design or the run. */
export function ruleNeeds(rule, facts = {}) {
  const names = [...new Set(String(rule.check || "").match(/[A-Za-z_][A-Za-z0-9_]*/g) || [])];
  return names
    .filter((n) => facts[n] || /^req_Q[0-9]{2}$/.test(n) || n.startsWith("var_"))
    .map((n) => ({ name: n, inDesigns: facts[n] ? facts[n].inDesigns !== false : true,
                   label: facts[n]?.label || (n.startsWith("req_") ? `要件 ${n.slice(4)} の数値` : "設計の変数の値") }));
}

export function showValue(v, unit) {
  if (typeof v === "number") return unit && unit !== "1" ? `${+v.toFixed(4)} ${unit}` : `${+v.toFixed(4)}`;
  return unit && unit !== "1" ? `${v} ${unit}` : String(v);
}
