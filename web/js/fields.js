// Fields of an analysis (stress, fatigue damage, temperature, mode shape,
// displacement) turned into contours: the recorded values per member, node and
// part on a [min, max] scale. The scale ends come from the record (the load
// case's criterion limit when it has the field's unit, else the field's own
// range); nothing here makes a new value to show.

export const FIELD_ORDER = ["stress", "damage", "temperature", "mode_shape", "displacement"];
export const FIELD_JA = { stress: "応力", damage: "疲労度", temperature: "温度", mode_shape: "モードの形", displacement: "変位" };

function numbers(obj) {
  return Object.fromEntries(Object.entries(obj ?? {}).filter(([, v]) => typeof v === "number" && Number.isFinite(v)));
}

function vectors(obj) {
  return Object.fromEntries(Object.entries(obj ?? {}).filter(([, v]) => Array.isArray(v) && v.length === 3 && v.every(Number.isFinite)));
}

export function fieldsOf(analysis) {
  if (!analysis || analysis.status !== "ok") return [];
  return (analysis.fields ?? [])
    .filter((f) => FIELD_ORDER.includes(f.kind))
    .sort((a, b) => FIELD_ORDER.indexOf(a.kind) - FIELD_ORDER.indexOf(b.kind));
}

// An upper limit from the criterion, when it is about this field's unit.
function upperLimit(field, criterion) {
  const lim = criterion?.limit;
  return lim && lim.unit === field.unit && Number.isFinite(lim.value) && (criterion.op === "<=" || criterion.op === "<") ? lim.value : null;
}

export function contourOf(analysis, field, criterion) {
  if (!analysis || !field) return null;
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
