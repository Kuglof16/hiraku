// Fields of an analysis (stress, fatigue damage, temperature, mode shape,
// displacement) turned into contours: the recorded values per member, node and
// part on a [min, max] scale. The scale ends come from the record (the load
// case's criterion limit when it has the field's unit, else the field's own
// range); nothing here makes a new value to show.

export const FIELD_ORDER = ["stress", "damage", "temperature", "mode_shape", "displacement"];
export const FIELD_JA = { stress: "応力", damage: "疲労度", temperature: "温度", mode_shape: "モードの形", displacement: "変位" };
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

// An upper limit from the criterion, when it is about this field's unit.
function upperLimit(field, criterion) {
  const lim = criterion?.limit;
  return lim && lim.unit === field.unit && Number.isFinite(lim.value) && (criterion.op === "<=" || criterion.op === "<") ? lim.value : null;
}

// meshes: mesh records by mesh_id (state.meshes), for a field on a mesh.
export function contourOf(analysis, field, criterion, meshes = null) {
  if (!analysis || !field) return null;
  if (field.mesh_id) return meshContourOf(analysis, field, criterion, meshes?.[field.mesh_id]);
  const members = numbers(field.per_member);
  const parts = numbers(field.per_part);
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
  if (!all.length) return null;
  const fieldMin = Math.min(...all);
  const fieldMax = Math.max(...all);
  const limit = upperLimit(field, criterion);
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
    fieldMin,
    fieldMax,
    where: result?.where,
    result,
    analysisId: analysis.analysis_id,
    frequency: field.frequency_Hz ?? null,
  };
}

// A field on a shell mesh: one value per element (per_element) or per mesh node
// (per_mesh_node; a vec3 there is a displacement or mode shape, coloured by its
// length). Values that do not match the mesh's element or node count are not drawn.
function meshContourOf(analysis, field, criterion, mesh) {
  if (!mesh) return null; // the mesh record has not come (yet)
  const nEl = mesh.elements.length;
  const nNode = mesh.nodes_mm.length;
  const elem = Array.isArray(field.per_element) && field.per_element.length === nEl ? field.per_element : null;
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
  };
}
