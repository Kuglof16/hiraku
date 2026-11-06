// Load cases past the limit (the contract's `beyond`, hiraku.catalog.is_beyond): their id ends
// in -beyond and their title starts with 限界の外. They are tests meant to fail (to show the
// failing side), so the cuts count them apart from the other reds, and a domain's colour is
// read without them.

import { domainVerdicts } from "./state.js";

export const BEYOND_SUFFIX = "-beyond";
export const BEYOND_TITLE = "限界の外";

export function isBeyond(lc) {
  return String(lc?.load_case_id ?? "").endsWith(BEYOND_SUFFIX) || String(lc?.title ?? "").startsWith(BEYOND_TITLE);
}

const isBeyondId = (s, lcId) => {
  const lc = (s.project?.load_cases ?? []).find((c) => c.load_case_id === lcId);
  return lc ? isBeyond(lc) : String(lcId ?? "").endsWith(BEYOND_SUFFIX);
};

// The verdicts of a domain's judgement of version v (state.js domainVerdicts), the newest per
// load case, split into those past the limit and the others.
function verdictsOf(s, domain, v) {
  const last = new Map();
  for (const x of domainVerdicts(s, domain, v)) last.set(x.load_case_id, x);
  const all = [...last.values()];
  return { beyond: all.filter((x) => isBeyondId(s, x.load_case_id)), others: all.filter((x) => !isBeyondId(s, x.load_case_id)) };
}

// The words judge.py puts in a judgement's summary when tests are left without a verdict or
// held back (hiraku/tools/judge.py compute_judgement). まだ OK にできない試験 is judge.py's word from
// design-ok-ng-words on; まだ合格にできない試験 stays for saved records written before it.
const NOT_ALL_JUDGED = ["判定の無い試験", "まだ OK にできない試験", "まだ合格にできない試験", "まだ判定していない試験"];

// A domain's tile for version v, read from the record:
//   state   the judgement's state (green / red / pending, null without one), except when the
//           record has it red only through cases past the limit (every failing verdict is past
//           the limit and the judgement counts no other red): then green when the other
//           verdicts all pass and the summary names no test left unjudged or held back, else
//           pending. Nothing else is decided here.
//   recorded the judgement's own state.
//   ng      the verdicts past the limit that failed (as meant: 狙いどおり NG).
//   passed  the verdicts past the limit that passed (meant to fail, and did not).
export function tileOf(s, domain, v) {
  const j = s.judgements[`${v}|${domain}`] ?? null;
  const recorded = j?.state ?? null;
  const { beyond, others } = verdictsOf(s, domain, v);
  const ng = beyond.filter((x) => x.pass === false);
  const passed = beyond.filter((x) => x.pass === true);
  let state = recorded;
  if (recorded === "red" && ng.length) {
    const otherReds = others.filter((x) => x.pass === false).length;
    const counted = Number.isFinite(j.red_count) ? j.red_count : otherReds + ng.length;
    if (!otherReds && counted <= ng.length) {
      const open = NOT_ALL_JUDGED.some((w) => String(j.summary ?? "").includes(w));
      state = others.length && others.every((x) => x.pass === true) && !open ? "green" : "pending";
    }
  }
  return { state, recorded, ng, passed };
}

// The words of a tile's mark for its cases past the limit ("" when it has none).
export function beyondWords(t) {
  const out = [];
  if (t.ng.length) out.push(`${BEYOND_TITLE} ${t.ng.length} (狙いどおり NG)`);
  if (t.passed.length) out.push(`${BEYOND_TITLE} ${t.passed.length}: NG のはずが OK`);
  return out;
}
