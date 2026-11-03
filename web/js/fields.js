// Fields of an analysis (stress, fatigue damage, temperature, mode shape,
// displacement) turned into contours: the recorded values per member, node and
// part on a [min, max] scale. The scale ends come from the record (the load
// case's criterion limit when it has the field's unit, else the field's own
// range); nothing here makes a new value to show.

// error_estimate: the estimated discretisation error of the stress per element (%), coloured from 0
// like a stress, showing where the mesh should be finer.
export const FIELD_ORDER = ["stress", "damage", "temperature", "mode_shape", "displacement", "error_estimate"];
export const FIELD_JA = { stress: "応力", damage: "疲労度", temperature: "温度", mode_shape: "モードの形", displacement: "変位", error_estimate: "誤差の目安" };
// A shell's surfaces. The contract lists mesh elements counter-clockwise seen from the
// outer side, so the element normal points out and "top" is the outer face.
export const SURFACE_JA = { top: "外面", mid: "中面", bottom: "内面", max: "外面と内面の大きい方" };
const SURFACE_ORDER = ["max", "top", "mid", "bottom"];

function numbers(obj) {
  return Object.fromEntries(Object.entries(obj ?? {}).filter(([, v]) => typeof v === "number" && Number.isFinite(v)));
}

function vectors(obj) {
  return Object.fromEntries(Object.entries(obj ?? {}).filter(([, v]) => Array.isArray(v) && v.length === 3 && v.every(Number.isFinite)));
}

// opts.mesh false leaves out the fields on a shell mesh (views that draw the beams only).
export function fieldsOf(analysis, { mesh = true } = {}) {
  if (!analysis || analysis.status !== "ok") return [];
  return (analysis.fields ?? [])
    .filter((f) => FIELD_ORDER.includes(f.kind) && (mesh || !f.mesh_id))
    .sort((a, b) => FIELD_ORDER.indexOf(a.kind) - FIELD_ORDER.indexOf(b.kind));
}

// One field among those of its kind: the beam drawing, or a mesh and its surface.
export function variantOf(field) {
  return `${field.mesh_id ?? ""}|${field.surface ?? ""}|${field.label ?? ""}`;
}

// The words on a field's button among the fields of its kind (sameKind); a label is
// added only where two would read the same.
export function variantName(field, sameKind) {
  const base = (f) => (f.mesh_id ? ["メッシュ", f.surface ? SURFACE_JA[f.surface] ?? f.surface : null] : ["梁"]).filter(Boolean).join("・");
  const own = base(field);
  const twins = sameKind.filter((f) => base(f) === own).length > 1;
  return twins && field.label ? `${own}・${field.label}` : own;
}

// The field of a kind shown first: on a mesh whose record has come (the larger surface
// first), else the beam drawing.
export function preferredField(sameKind, meshes) {
  const onMesh = sameKind.filter((f) => f.mesh_id && meshes?.[f.mesh_id]);
  const rank = (f) => (SURFACE_ORDER.includes(f.surface) ? SURFACE_ORDER.indexOf(f.surface) : SURFACE_ORDER.length);
  return [...onMesh].sort((a, b) => rank(a) - rank(b))[0] ?? sameKind.find((f) => !f.mesh_id) ?? sameKind[0] ?? null;
}

// The same field in another analysis (e.g. another design version's): the same kind,
// both on a mesh or both on the beams, the same surface and label. The mesh_id is left
// out, as each version has its own mesh.
export function sameField(a, b) {
  return !!a && !!b && a.kind === b.kind && !!a.mesh_id === !!b.mesh_id && (a.surface ?? null) === (b.surface ?? null) && (a.label ?? null) === (b.label ?? null);
}

// One colour scale for several contours of the same field (e.g. before and after a
// change): the allowable value when every contour's top is it, else the largest
// recorded value; the bottom is the lowest bottom. Returns the contours on that scale.
export function onSharedScale(cts) {
  const all = cts.filter(Boolean);
  if (!all.length) return cts;
  const min = Math.min(...all.map((c) => c.min));
  const byLimit = all.every((c) => c.limitKnown) && new Set(all.map((c) => c.max)).size === 1;
  let max = byLimit ? all[0].max : Math.max(...all.map((c) => c.fieldMax));
  if (!(max > min)) max = min + (Math.abs(min) || 1);
  return cts.map((c) => (c ? { ...c, key: `${c.key}|scale ${min} ${max}`, min, max, limitKnown: byLimit && c.limitKnown, crit: byLimit ? c.crit : null } : c));
}

// An upper limit from the criterion, when it is about this field's unit.
function upperLimit(field, criterion) {
  const lim = criterion?.limit;
  return lim && lim.unit === field.unit && Number.isFinite(lim.value) && (criterion.op === "<=" || criterion.op === "<") ? lim.value : null;
}

// meshes: mesh records by mesh_id (state.meshes), for a field on a mesh.
export function contourOf(analysis, field, criterion, meshes = null) {
  if (!analysis || !field) return null;
  if (field.mesh_id) return meshContourOf(analysis, field, criterion, meshes?.[field.mesh_id]);
  let members = numbers(field.per_member);
  const parts = numbers(field.per_part);
  const frames = framesOf(field);
  if (frames && !Object.keys(members).length) members = numbers(frames.list[0]);
  let nodes = numbers(field.per_node);
  let modes = null;
  if (field.kind === "mode_shape" || field.kind === "displacement") {
    const v = vectors(field.per_node);
    if (Object.keys(v).length) {
      modes = v;
      // Colour by how far each node moves (the recorded vector's length).
      nodes = { ...nodes, ...Object.fromEntries(Object.entries(v).map(([k, d]) => [k, Math.hypot(...d)])) };
    }
  }
  const all = [...Object.values(members), ...Object.values(nodes), ...Object.values(parts)];
  // with frames the scale holds every moment, so the colours of two moments compare
  for (const m of frames?.list ?? []) all.push(...Object.values(numbers(m)));
  if (!all.length) return null;
  const fieldMin = Math.min(...all);
  const fieldMax = Math.max(...all);
  let limit = upperLimit(field, criterion);
  // Values per part only (e.g. part temperatures): the criterion is one part's limit, the
  // part where the result of the criterion's quantity was recorded (T1: temp_battery_max
  // at the battery). Only that part is held to it; the scale is the field's own range, so
  // no other part is judged by another part's limit.
  let partLimit = null;
  if (limit !== null && Object.keys(parts).length && !Object.keys(members).length && !Object.keys(nodes).length) {
    const at = (analysis.results ?? []).find((r) => r.quantity === criterion.quantity)?.where;
    partLimit = at && at in parts ? { part: at, value: limit, crit: criterion } : null;
    limit = null;
  }
  const min = field.kind === "temperature" ? fieldMin : Math.min(0, fieldMin);
  let max = limit ?? fieldMax;
  if (!(max > min)) max = min + (Math.abs(min) || 1);
  const result = (analysis.results ?? []).find((r) => r.where && r.unit === field.unit) ?? null;
  return {
    key: `${analysis.analysis_id}|${field.kind}|${field.label ?? ""}`,
    kind: field.kind,
    field,
    values: members,
    nodes,
    parts,
    modes,
    min,
    max,
    limitKnown: limit !== null,
    crit: limit !== null ? criterion : null,
    partLimit,
    fieldMin,
    fieldMax,
    where: result?.where,
    result,
    analysisId: analysis.analysis_id,
    frequency: field.frequency_Hz ?? null,
    frames,
  };
}

// Whether a part's value is over its allowable value: its own limit when the contour holds
// one part to the criterion, else the scale's top when that is the allowable value.
export function overOf(ct, partId, v) {
  if (typeof v !== "number" || !ct) return false;
  if (ct.partLimit) return partId === ct.partLimit.part && v > ct.partLimit.value;
  return ct.limitKnown && v > ct.max;
}

// A field on a shell mesh: one value per element (per_element) or per mesh node
// (per_mesh_node; a vec3 there is a displacement or mode shape, coloured by its
// length). Values that do not match the mesh's element or node count are not drawn.
function meshContourOf(analysis, field, criterion, mesh) {
  if (!mesh) return null; // the mesh record has not come (yet)
  const nEl = mesh.elements.length;
  const nNode = mesh.nodes_mm.length;
  const frames = framesOf(field, nEl);
  const elem = Array.isArray(field.per_element) && field.per_element.length === nEl ? field.per_element : frames?.list[0] ?? null;
  let node = null;
  let modes = null;
  const pn = field.per_mesh_node;
  if (!elem && Array.isArray(pn) && pn.length === nNode) {
    if (pn.every((d) => Array.isArray(d) && d.length === 3)) {
      modes = pn;
      node = pn.map((d) => Math.hypot(...d));
    } else if (pn.every((v) => typeof v === "number")) node = pn;
  }
  const vals = elem ?? node;
  if (!vals) return null;
  let fieldMin = Infinity;
  let fieldMax = -Infinity;
  let at = -1;
  vals.forEach((v, i) => {
    if (!Number.isFinite(v)) return;
    fieldMin = Math.min(fieldMin, v);
    if (v > fieldMax) [fieldMax, at] = [v, i];
  });
  if (at < 0) return null;
  for (const list of frames?.list ?? []) for (const v of list) if (Number.isFinite(v)) [fieldMin, fieldMax] = [Math.min(fieldMin, v), Math.max(fieldMax, v)];
  const limit = upperLimit(field, criterion);
  const min = field.kind === "temperature" ? fieldMin : Math.min(0, fieldMin);
  let max = limit ?? fieldMax;
  if (!(max > min)) max = min + (Math.abs(min) || 1);
  return {
    key: `${analysis.analysis_id}|${field.kind}|${variantOf(field)}`,
    kind: field.kind,
    field,
    values: {},
    nodes: {},
    parts: {},
    modes: null,
    mesh: { rec: mesh, elem, node, modes, maxAt: elem ? { elem: at } : { node: at } },
    min,
    max,
    limitKnown: limit !== null,
    crit: limit !== null ? criterion : null,
    fieldMin,
    fieldMax,
    where: null,
    result: null,
    analysisId: analysis.analysis_id,
    frequency: field.frequency_Hz ?? null,
    frames,
  };
}

// A field's frames (the same field at chosen moments of a time history), checked: one entry per
// t_s, per_member objects, or per_element lists of `count` (on a mesh). Null when absent or off.
function framesOf(field, count = null) {
  const f = field.frames;
  const n = f?.t_s?.length;
  if (!n) return null;
  const list = count !== null ? f.per_element : f.per_member;
  if (!Array.isArray(list) || list.length !== n) return null;
  if (count !== null && !list.every((a) => Array.isArray(a) && a.length === count)) return null;
  return { t_s: f.t_s, list, max: Array.isArray(f.max) && f.max.length === n ? f.max : null, source: f.source_analysis_id ?? null };
}

// The frame of a contour nearest to time t (the source analysis' own seconds), or, with t null,
// the one with the largest value (as recorded in max, else counted from its values).
export function frameIndex(ct, t) {
  const f = ct?.frames;
  if (!f) return null;
  if (t === null || t === undefined) {
    const peak = (k) => f.max?.[k] ?? Math.max(...(Array.isArray(f.list[k]) ? f.list[k] : Object.values(numbers(f.list[k]))));
    let best = 0;
    for (let k = 1; k < f.t_s.length; k += 1) if (peak(k) > peak(best)) best = k;
    return best;
  }
  let best = 0;
  for (let k = 1; k < f.t_s.length; k += 1) if (Math.abs(f.t_s[k] - t) < Math.abs(f.t_s[best] - t)) best = k;
  return best;
}

// The contour at frame k: that moment's values, its largest one and where it is, on the scale of
// all the moments. Kept per k, so the same frame is the same object (the 3D view skips it).
export function atFrame(ct, k) {
  const f = ct?.frames;
  if (!f || k === null) return ct;
  ct.atFrames ??= new Map();
  if (ct.atFrames.has(k)) return ct.atFrames.get(k);
  const out = { ...ct, key: `${ct.key}|frame${k}`, frameIndex: k, frameT: f.t_s[k] };
  if (ct.mesh) {
    const elem = f.list[k];
    let at = 0;
    elem.forEach((v, i) => { if (v > elem[at]) at = i; });
    out.mesh = { ...ct.mesh, elem, maxAt: { elem: at } };
    out.frameMax = f.max?.[k] ?? elem[at];
  } else {
    const values = numbers(f.list[k]);
    const where = Object.keys(values).reduce((a, b) => (a === null || values[b] > values[a] ? b : a), null);
    out.values = values;
    out.frameMax = f.max?.[k] ?? (where !== null ? values[where] : null);
    out.where = where;
    out.result = where !== null ? { quantity: ct.kind, value: out.frameMax, unit: ct.field.unit, where } : null;
  }
  ct.atFrames.set(k, out);
  return out;
}

// analysis.mesh_convergence in one line: the judged value solved again on finer meshes. Singular
// (it keeps rising as the mesh gets finer) is said so, and that value is not judged.
export function convergenceLine(mc) {
  if (!mc?.levels?.length) return "";
  const sizes = mc.levels.map((l) => `${+l.element_size_mm.toPrecision(3)}`);
  if (mc.verdict === "singular") return `メッシュ ${sizes.join(" → ")} mm: 細かくするほど上がる (特異点): この値では判定しない`;
  const change = typeof mc.change_pct === "number" ? `${+mc.change_pct.toPrecision(2)} %` : "—";
  const limit = typeof mc.limit_pct === "number" ? ` (限度 ${+mc.limit_pct.toPrecision(2)} %)` : "";
  return `メッシュ ${sizes.slice(-2).join(" → ")} mm で ${change}${limit}: ${mc.verdict === "converged" ? "収まった" : "まだ動く"}`;
}
