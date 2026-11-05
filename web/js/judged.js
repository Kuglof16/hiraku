// Which numbers on the screen are the judged quantity of a load case, and which are shown only
// for reference. A verdict is made on one quantity (docs/contract.md, 判定に使う量): the
// criterion's own, except where a tool is judged on another of its results (frame_shell judges
// a max_stress criterion on max_hot_spot_stress, the weld toe's structural stress, since its
// element max_stress keeps rising at sharp corners as the mesh is refined). A field drawn in
// colour is the judged quantity when its largest value is the analysis' judged result; another
// field in the same unit (e.g. the shell's element von Mises beside the hot-spot stress) is
// reference only, and is not held to the allowable value.

import { JUDGED_JA, fmtVU } from "./labels.js";
import { contourOf } from "./fields.js";
import { judgedVerdict, loadCaseIn } from "./state.js";

// The tools judged on another result than the criterion's quantity (the same table as
// hiraku/tools/judge.py JUDGED_QUANTITY: tool -> criterion quantity -> judged result).
const JUDGED_AS = { frame_shell: { max_stress: "max_hot_spot_stress" } };

export function judgedQuantity(tool, criterionQuantity) {
  return JUDGED_AS[tool]?.[criterionQuantity] ?? criterionQuantity;
}

const LARGER = new Set(["<=", "<"]);

// The analysis' result of its judged quantity (the whole result, not a per-part one such as
// max_hot_spot_stress.rail_L_FA); of several, the one nearest failing. Null when none.
export function judgedResult(ana, criterion) {
  if (!ana || !criterion?.quantity) return null;
  const q = judgedQuantity(ana.tool, criterion.quantity);
  const rs = (ana.results ?? []).filter((r) => r.quantity === q && typeof r.value === "number");
  if (!rs.length) return null;
  const larger = LARGER.has(criterion.op);
  return rs.reduce((a, b) => ((larger ? b.value > a.value : b.value < a.value) ? b : a));
}

// The analysis' results of the criterion's own quantity when the tool is judged on another one
// (frame_shell's element max_stress beside its max_hot_spot_stress): numbers in the criterion's
// unit that may pass the allowable but are not judged. Empty when the tool is judged on the
// criterion's quantity.
export function referenceResults(ana, criterion) {
  if (!ana || !criterion?.quantity) return [];
  const q = judgedQuantity(ana.tool, criterion.quantity);
  if (q === criterion.quantity) return [];
  return (ana.results ?? []).filter((r) => r.quantity === criterion.quantity && typeof r.value === "number");
}

// Two values that are the same number as recorded (fields keep 2-3 decimals, results all).
export const sameRecorded = (a, b) => Math.abs(a - b) <= Math.max(5e-3 * Math.abs(b), 1e-2);

// Whether a field of an analysis, whose largest value is fieldMax, is the judged quantity
// ("judged"), another quantity in the criterion's unit ("reference"), or cannot be told
// (null: no criterion in the field's unit, or no judged result recorded).
export function fieldRole(ana, field, fieldMax, criterion) {
  const unit = criterion?.limit?.unit;
  if (!field || !unit || field.unit !== unit || typeof fieldMax !== "number") return null;
  const r = judgedResult(ana, criterion);
  if (!r || r.unit !== unit) return null;
  return sameRecorded(fieldMax, r.value) ? "judged" : "reference";
}

// The criterion a load case is judged by in a design version, with the verdict's own op and
// limit when there is a verdict (the colour bar's allowable is then the verdict's), and the
// verdict. The load case as that version has it (its limit may differ from version 1's).
export function judgedCriterion(s, loadCaseId, version) {
  const lc = loadCaseIn(s, loadCaseId, version);
  const verdict = judgedVerdict(s, loadCaseId, version);
  const base = lc?.criterion ?? null;
  if (!verdict?.limit) return { criterion: base, verdict, loadCase: lc };
  return { criterion: { ...(base ?? {}), quantity: base?.quantity ?? verdict.quantity, op: verdict.op, limit: verdict.limit }, verdict, loadCase: lc };
}

// A field's contour (fields.js contourOf), held to the allowable only when the field is the
// judged quantity: the criterion of the analysis' design version, with the verdict's limit when
// there is one. A field in the criterion's unit that is another quantity (the shell's element
// von Mises beside the hot-spot stress a frame_shell verdict is made on) is drawn on its own
// range with no allowable, and says it is for reference (ct.reference; a mesh's largest-value
// tag too). A contour that holds one part to its limit (partLimit, e.g. the battery's
// temperature) is left as it is. ct.role: "judged", "reference" or null (cannot be told).
// ct.pass: the verdict of the load case in the analysis' version (true / false), null when there
// is none yet.
export function judgedContour(s, ana, field, loadCaseId, meshes = null) {
  if (!ana || !field) return null;
  const { criterion, verdict } = judgedCriterion(s, loadCaseId, ana.design_version ?? s.currentVersion);
  const ct = contourOf(ana, field, criterion, meshes);
  if (!ct || ct.partLimit) return ct;
  const role = fieldRole(ana, field, ct.fieldMax, criterion);
  const pass = typeof verdict?.pass === "boolean" ? verdict.pass : null;
  const key = `${ct.key}|${role ?? "-"} ${criterion?.limit?.value ?? "-"} ${pass ?? "-"}`;
  if (role !== "reference") return { ...ct, key, role, pass };
  const own = ct.limitKnown ? contourOf(ana, field, null, meshes) : ct;
  const tag = own.mesh && !own.frames ? `${JUDGED_JA.refTag} 最大 ${fmtVU(own.fieldMax, field.unit)}` : null;
  return { ...own, key, role, pass, reference: { quantity: judgedQuantity(ana.tool, criterion.quantity) }, mesh: tag ? { ...own.mesh, tag } : own.mesh };
}

// The one rule for the colour of a contour's largest-value tag on the 3D (workbench3d.js):
// red only when the value is the judged quantity (ct.role "judged") and the version's verdict
// failed (ct.pass false); the muted reference colour for a value the verdict does not use
// (ct.role "reference"); else the plain ink (passed, no verdict yet, or cannot be told, e.g. a
// contour not made by judgedContour).
export function maxTagClass(ct) {
  if (ct?.role === "judged" && ct.pass === false) return "lbl-max lbl-fail";
  if (ct?.role === "reference") return "lbl-max lbl-ref";
  return "lbl-max";
}
