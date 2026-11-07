// Why a verdict made on the tool's value after the load path was checked by hand (basis
// path_checked: hiraku.schema.json verdict.basis, cause.tool_decides) is OK or NG, in one line:
//   手計算は上限のみ (9.85 mm)。力の道がつながっていることを確かめ (手計算でつながらない部品 0、
//   解析で支えの無い荷重 0)、解析の値 0.925 mm を基準 5 mm と比べて OK
// Every number is read from the records the verdict points at (its comparison, the comparison's
// prediction and analysis, the version's load_path.free_parts prediction and the analyses'
// unsupported_load, as hiraku/tools/judge.py path_check reads them). A clause whose record is
// not there is left out; nothing is filled in. null for any other basis.

import { fmtQ } from "./labels.js";

const PATH_FORMULA = "load_path.free_parts"; // judge.py PATH_FORMULA
// A K0 formula whose title says it bounds the value from above (library_loadpath.py grip_bound:
// 「…を上から押さえる値」). The prediction record has no field for it: the formula's title does.
const BOUND_TITLE = /上から押さえる値/;

const resultOf = (a, q) => (a?.results ?? []).find((r) => r.quantity === q) ?? null;

// The version's hand check of the load path (judge.py path_check (1)): a load_path.free_parts
// prediction of the load case (or of every part: quantity free_parts), the newest one of 0 when
// there is one, else the newest.
function handPath(s, verdict, version) {
  const hand = (s.order.predictions ?? [])
    .map((id) => s.predictions[id])
    .filter((p) => p && p.formula_id === PATH_FORMULA && p.design_version === version && (p.load_case_id === verdict.load_case_id || p.quantity === "free_parts"));
  return hand.filter((p) => p.value === 0).at(-1) ?? hand.at(-1) ?? null;
}

// The analyses' unsupported_load (judge.py path_check (2)): the deciding analysis', else the
// newest ok analysis of the load case in the version that gives it.
function toolPath(s, verdict, version, deciding) {
  const own = resultOf(deciding, "unsupported_load");
  if (own) return own;
  const seen = (s.order.analyses ?? [])
    .map((id) => s.analyses[id])
    .filter((a) => a && a.status === "ok" && a.design_version === version && a.load_case_id === verdict.load_case_id && resultOf(a, "unsupported_load"));
  return resultOf(seen.at(-1), "unsupported_load");
}

// s: the replay's state; verdict: a verdict record; titleOf(formula_id): the formula's title or
// undefined. Returns { text, parts } (parts: the numbers used, for tests) or null.
export function pathCheckedWhy(s, verdict, titleOf = () => undefined) {
  if (verdict?.basis !== "path_checked") return null;
  const cmp = s.comparisons?.[verdict.comparison_id] ?? null;
  const pred = cmp ? s.predictions?.[cmp.prediction_id] ?? null : null;
  const ana = cmp ? s.analyses?.[cmp.analysis_id] ?? null : null;
  const version = ana?.design_version ?? pred?.design_version ?? null;
  const out = [];
  const parts = {};
  // 1. the hand calculation does not reach the value: its prediction (and whether it is a bound)
  const predicted = cmp?.predicted ?? (pred ? { value: pred.value, unit: pred.unit } : null);
  if (predicted && typeof predicted.value === "number") {
    const bound = pred && BOUND_TITLE.test(titleOf(pred.formula_id) ?? "");
    parts.predicted = predicted.value;
    parts.bound = !!bound;
    out.push(bound ? `手計算は上限のみ (${fmtQ(predicted)})` : `手計算 (${fmtQ(predicted)}) は値に届かない`);
  }
  // 2. the load path, checked by hand and by the tool
  if (version != null) {
    const hand = handPath(s, verdict, version);
    const tool = toolPath(s, verdict, version, ana);
    const said = [];
    if (hand && typeof hand.value === "number") {
      parts.freeParts = hand.value;
      said.push(`手計算でつながらない部品 ${fmtQ({ value: hand.value, unit: "" })}`);
    }
    if (tool && typeof tool.value === "number") {
      parts.unsupported = tool.value;
      said.push(`解析で支えの無い荷重 ${fmtQ({ value: tool.value, unit: "" })}`);
    }
    // (the line says the path joins only when every number read says so)
    if (said.length && (parts.freeParts ?? 0) === 0 && (parts.unsupported ?? 0) === 0) out.push(`力の道がつながっていることを確かめ (${said.join("、")})`);
    else if (said.length) out.push(`力の道: ${said.join("、")}`);
  }
  // 3. the tool's value against the criterion
  if (verdict.value && verdict.limit) {
    parts.value = verdict.value.value;
    parts.limit = verdict.limit.value;
    out.push(`解析の値 ${fmtQ(verdict.value)} を基準 ${fmtQ(verdict.limit)} と比べて ${verdict.pass ? "OK" : "NG"}`);
  }
  if (!out.length) return null;
  // 手計算は…。力の道…、解析の値…
  const [first, ...rest] = out;
  const text = parts.predicted !== undefined && rest.length ? `${first}。${rest.join("、")}` : out.join("、");
  return { text, parts };
}
