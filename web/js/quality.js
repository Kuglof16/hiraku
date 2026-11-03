// The mesh's own views on the workbench, beside the fields of an analysis: the mesh
// check made before the solve (mesh.quality: metrics, topology, verdict), the wall
// thickness of each element (mesh.member_thickness_mm by element_member) and what the
// solver put on the model (analysis.setup: constraints, loads and their spread,
// checks). Every number shown is a recorded value or a count / ratio made here from
// the records (free edges from the elements, elements past a limit, the mass ratio).

import { BANDS, BAND_COLORS, GRAY, OVER_COLOR } from "./colormap.js";
import { esc, fmt, fmtSigned, fmtVU, unitLabel } from "./labels.js";

export const MODE_JA = { quality: "メッシュの品質", thickness: "板厚と材料", setup: "解く前の設定" };
export const MODE_ORDER = ["quality", "thickness", "setup"];

export const METRIC_JA = {
  aspect_ratio: "縦横比",
  warp_deg: "反り",
  skew_deg: "ゆがみ",
  min_angle_deg: "最小の角",
  max_angle_deg: "最大の角",
  jacobian_ratio: "ヤコビアン比",
  taper: "テーパー",
};
export const TOPOLOGY_JA = {
  free_edges: "自由縁",
  coincident_nodes: "重なった節",
  unconnected_groups: "つながっていない組",
  flipped_normals: "裏返った法線",
  negative_volume: "負の体積",
};
const TOPOLOGY_ORDER = Object.keys(TOPOLOGY_JA);
export const VERDICT_JA = { ok: "良い", warn: "注意 (限度を超えた要素がある)", fail: "不可 (ソルバーはこのメッシュを解かない)" };
export const CHECK_JA = {
  equilibrium_residual: "釣り合いの残り",
  model_mass: "モデルの質量",
  design_mass: "設計の質量",
  applied_load_total: "かけた力の合計",
};
export const FREE_EDGE_COLOR = "#d61f9c";
export const FIX_T_COLOR = "#1f6f6b"; // a stopped translation
export const FIX_R_COLOR = "#8e6bbf"; // a stopped rotation
export const LOAD_COLOR = "#c8402a";

const metricName = (k) => METRIC_JA[k] ?? k;
// A metric's unit from its name (the contract's names end in _deg for angles).
export const metricUnit = (k) => (k.endsWith("_deg") ? "deg" : "1");

export function qualityOf(rec) {
  const q = rec?.quality;
  return q && typeof q.verdict === "string" && q.metrics && typeof q.metrics === "object" ? q : null;
}

// The per_element metrics that can be drawn on this mesh (one value per element).
export function drawableMetrics(rec) {
  const q = qualityOf(rec);
  const n = rec?.elements?.length ?? 0;
  return Object.keys(q?.per_element ?? {}).filter((k) => Array.isArray(q.per_element[k]) && q.per_element[k].length === n);
}

// Whether the small end of a metric is the bad one (e.g. the smallest corner angle):
// the recorded worst is below p95, else it is the smallest of the per-element values,
// else the name says it (min_...).
export function lowIsBad(name, m, values) {
  if (Number.isFinite(m?.worst) && Number.isFinite(m?.p95) && m.worst !== m.p95) return m.worst < m.p95;
  if (Number.isFinite(m?.worst) && values?.length) {
    let lo = Infinity;
    let hi = -Infinity;
    for (const v of values) if (Number.isFinite(v)) [lo, hi] = [Math.min(lo, v), Math.max(hi, v)];
    if (m.worst <= lo && m.worst < hi) return true;
    if (m.worst >= hi) return false;
  }
  return name.startsWith("min_");
}

// Edges used by one element only, as [a, b] node pairs, counted from mesh.elements.
export function freeEdges(rec) {
  const n = rec.nodes_mm.length;
  const uses = new Map();
  for (const e of rec.elements) {
    for (let i = 0; i < e.length; i += 1) {
      const a = e[i];
      const b = e[(i + 1) % e.length];
      const key = Math.min(a, b) * n + Math.max(a, b);
      uses.set(key, (uses.get(key) ?? 0) + 1);
    }
  }
  const out = [];
  for (const [key, c] of uses) if (c === 1) out.push([Math.floor(key / n), key % n]);
  return out;
}

const bandOf = (u) => BAND_COLORS[Math.min(BANDS - 1, Math.max(0, Math.floor(u * BANDS)))];

// A metric of the mesh check as a contour on the mesh. The scale runs from the good end
// of the recorded values (min) to the limit when the record gives one, else to the bad
// end of the values (max); for a metric whose small end is bad, max < min. Elements past
// the limit take OVER_COLOR. mesh.maxAt is the worst element (the record's
// worst_element, else the worst value counted here).
export function qualityContourOf(analysisId, rec, metric, design = null) {
  const q = qualityOf(rec);
  if (!q) return null;
  if (!drawableMetrics(rec).includes(metric)) {
    // no value per element to colour by: the mesh in gray, the free edges and the list
    return {
      key: `quality|${rec.mesh_id}|`,
      mode: "quality",
      kind: "quality",
      metric: null,
      field: { unit: "", label: "メッシュの品質" },
      mesh: { rec, elem: null, colors: rec.elements.map(() => GRAY), maxAt: null },
      values: {},
      nodes: {},
      parts: {},
      modes: null,
      min: 0,
      max: 1,
      limitKnown: false,
      analysisId,
      quality: q,
      freeEdges: freeEdges(rec),
      design,
    };
  }
  const values = q.per_element[metric];
  const m = q.metrics[metric] ?? {};
  const low = lowIsBad(metric, m, values);
  let fieldMin = Infinity;
  let fieldMax = -Infinity;
  for (const v of values) if (Number.isFinite(v)) [fieldMin, fieldMax] = [Math.min(fieldMin, v), Math.max(fieldMax, v)];
  if (!(fieldMax >= fieldMin)) return null;
  const limit = Number.isFinite(m.limit) ? m.limit : null;
  const good = low ? fieldMax : fieldMin;
  const bad = limit ?? (low ? fieldMin : fieldMax);
  const past = (v) => limit !== null && (low ? v < limit : v > limit);
  const colors = new Array(values.length);
  let over = 0;
  let worst = -1;
  values.forEach((v, i) => {
    if (!Number.isFinite(v)) {
      colors[i] = GRAY;
      return;
    }
    if (past(v)) {
      over += 1;
      colors[i] = OVER_COLOR;
    } else colors[i] = bandOf(bad === good ? 0 : (v - good) / (bad - good));
    if (worst < 0 || (low ? v < values[worst] : v > values[worst])) worst = i;
  });
  const rw = m.worst_element;
  const at = Number.isInteger(rw) && rw >= 0 && rw < values.length ? rw : worst;
  const unit = metricUnit(metric);
  const edges = freeEdges(rec);
  return {
    key: `quality|${rec.mesh_id}|${metric}`,
    mode: "quality",
    kind: "quality",
    metric,
    low,
    limit,
    overCount: over,
    field: { unit, label: metricName(metric) },
    mesh: { rec, elem: values, colors, maxAt: { elem: at }, tag: `いちばん悪い ${fmtVU(values[at], unit)}` },
    values: {},
    nodes: {},
    parts: {},
    modes: null,
    min: good,
    max: bad,
    limitKnown: false,
    fieldMin,
    fieldMax,
    analysisId,
    quality: q,
    freeEdges: edges,
    design,
  };
}

// Distinct thicknesses as colours spread along the band colours (one in the middle).
function thicknessColors(list) {
  const k = list.length;
  return Object.fromEntries(list.map((t, j) => [t, BAND_COLORS[k === 1 ? Math.floor(BANDS / 2) : Math.round((j * (BANDS - 1)) / (k - 1))]]));
}

// Each element in the colour of its member's wall thickness; rows of the list: one per
// thickness with its members, element count and the design's material names.
export function thicknessContourOf(analysisId, rec, design) {
  const th = rec?.member_thickness_mm ?? {};
  const em = rec?.element_member ?? [];
  if (!rec || em.length !== rec.elements.length || !Object.keys(th).length) return null;
  const values = em.map((id) => (Number.isFinite(th[id]) ? th[id] : null));
  const list = [...new Set(values.filter((v) => v !== null))].sort((a, b) => a - b);
  const col = thicknessColors(list);
  const colors = values.map((v) => (v === null ? GRAY : col[v]));
  const members = Object.fromEntries((design?.members ?? []).map((m) => [m.id, m]));
  const matName = (id) => {
    const key = members[id]?.material;
    return key ? design?.materials?.[key]?.name ?? key : null;
  };
  const rows = list.map((t) => {
    const ids = Object.keys(th).filter((id) => th[id] === t);
    return {
      t,
      color: col[t],
      members: ids,
      elements: values.filter((v) => v === t).length,
      materials: [...new Set(ids.map(matName).filter(Boolean))],
    };
  });
  const none = values.filter((v) => v === null).length;
  return {
    key: `thickness|${rec.mesh_id}`,
    mode: "thickness",
    kind: "thickness",
    field: { unit: "mm", label: "板厚" },
    mesh: { rec, elem: null, colors, maxAt: null },
    values: {},
    nodes: {},
    parts: {},
    modes: null,
    min: 0,
    max: 1,
    limitKnown: false,
    analysisId,
    rows,
    none,
  };
}

// The analysis' setup when its mesh record has come.
export function setupOf(analysis, meshes) {
  const st = analysis?.setup;
  const rec = st?.mesh_id ? meshes?.[st.mesh_id] : null;
  return st && rec ? { setup: st, rec } : null;
}

// What the solver put on the model, drawn on its mesh (plain gray).
export function setupContourOf(analysis, meshes) {
  const got = setupOf(analysis, meshes);
  if (!got) return null;
  const { setup, rec } = got;
  const n = rec.nodes_mm.length;
  const okNode = (i) => Number.isInteger(i) && i >= 0 && i < n;
  const constraints = (setup.constraints ?? []).filter((c) => okNode(c.node) && Array.isArray(c.dofs));
  const loads = (setup.loads ?? []).filter((l) => Array.isArray(l.origin_mm) && Array.isArray(l.vector_N));
  const checks = setup.checks ?? {};
  const mm = checks.model_mass;
  const dm = checks.design_mass;
  // (model - design) / design, when both masses are in one unit
  const massDiff = mm && dm && mm.unit === dm.unit && Number.isFinite(mm.value) && Number.isFinite(dm.value) && dm.value !== 0 ? (mm.value - dm.value) / dm.value : null;
  return {
    key: `setup|${analysis.analysis_id}|${rec.mesh_id}`,
    mode: "setup",
    kind: "setup",
    field: { unit: "", label: "解く前の設定" },
    mesh: { rec, elem: null, colors: rec.elements.map(() => GRAY), maxAt: null },
    values: {},
    nodes: {},
    parts: {},
    modes: null,
    min: 0,
    max: 1,
    limitKnown: false,
    analysisId: analysis.analysis_id,
    setup: { constraints, loads, inertiaRelief: setup.inertia_relief ?? null, checks, massDiff, okNode },
  };
}

const TRANS = ["ux", "uy", "uz"];
const ROT = ["rx", "ry", "rz"];
export const dofCount = (constraints) => ({
  t: constraints.reduce((k, c) => k + c.dofs.filter((d) => TRANS.includes(d)).length, 0),
  r: constraints.reduce((k, c) => k + c.dofs.filter((d) => ROT.includes(d)).length, 0),
});

// ---- legend and card ---------------------------------------------------------

// The title, the limit line and the source line of a mode's legend (app.js draws the bar).
const box = (color) => `<span style="display:inline-block;width:12px;height:12px;background:${color};margin-right:6px"></span>`;
const stroke = (color) => `<span style="display:inline-block;width:14px;height:4px;background:${color};margin:0 6px 3px 0"></span>`;

export function modeLegendParts(ct) {
  if (ct.mode === "quality") {
    const u = unitLabel(ct.field.unit);
    const sw = box(OVER_COLOR);
    const fe = stroke(FREE_EDGE_COLOR);
    const fr = `<div class="fe" data-free="${ct.freeEdges.length}">${fe}自由縁 ${ct.freeEdges.length} 本 (要素から数えた)</div>`;
    if (!ct.metric) return { title: "メッシュの品質<br><small>要素ごとの値の記録が無い</small>", bar: "", under: fr, src: `${esc(ct.mesh.rec.mesh_id)} (要素 ${ct.mesh.rec.elements.length})` };
    const lim =
      ct.limit !== null
        ? `<div class="lim" data-over="${ct.overCount}">${sw}限度 ${fmt(ct.limit, 4)}${u ? ` ${esc(u)}` : ""} ${ct.low ? "より小さい" : "を超えた"}要素 ${ct.overCount}</div>`
        : "";
    return {
      title: `${esc(ct.field.label)}${u ? ` [${esc(u)}]` : ""}<br><small>${ct.low ? "小さいほど悪い" : "大きいほど悪い"}・上端 = ${ct.limit !== null ? "限度" : "いちばん悪い値"}</small>`,
      under: lim + fr,
      src: `色の元: ${esc(ct.mesh.rec.mesh_id)} の品質 (要素 ${ct.mesh.rec.elements.length})`,
    };
  }
  if (ct.mode === "thickness") {
    const rows = ct.rows
      .map((r) => `<span class="sw end" style="background:${r.color};border-top:1.5px solid var(--ink)"></span><span class="tk0" data-t="${r.t}">${fmt(r.t, 3)} mm</span>`)
      .join("");
    return {
      title: "板厚 [mm]",
      bar: `<div class="cats">${rows}</div>`,
      under: "",
      src: `色の元: ${esc(ct.mesh.rec.mesh_id)} の member_thickness_mm`,
    };
  }
  const key =
    `<div class="keyrow">${box(FIX_T_COLOR)}並進を止めた向き (円すいの先が節)</div>` +
    `<div class="keyrow">${box(FIX_R_COLOR)}回転を止めた軸 (輪)</div>` +
    `<div class="keyrow">${box(LOAD_COLOR)}荷重 (矢印)・モーメント (回る矢印)</div>` +
    `<div class="keyrow">${stroke(LOAD_COLOR)}分けた先の節 (濃いほど重い)</div>`;
  return { title: "解く前の設定", bar: key, under: "", src: `${esc(ct.analysisId)}・${esc(ct.mesh.rec.mesh_id)}` };
}

const td = (v) => `<td>${v}</td>`;

function qualityCard(ct) {
  const q = ct.quality;
  const v = ["ok", "warn", "fail"].includes(q.verdict) ? q.verdict : "warn";
  const head =
    `<h3>メッシュの品質 <small>${esc(ct.mesh.rec.mesh_id)}</small></h3>` +
    `<div class="verdict v-${v}" data-verdict="${esc(q.verdict)}">${esc(VERDICT_JA[q.verdict] ?? q.verdict)}</div>` +
    (q.verdict === "fail" ? `<div class="unsolved">解かれていない</div>` : "");
  const metrics = Object.entries(q.metrics)
    .map(([k, m]) => {
      const u = metricUnit(k);
      const f = (x) => (Number.isFinite(x) ? esc(fmtVU(x, u)) : "—");
      return `<tr data-metric="${esc(k)}" class="${m.over_limit > 0 ? "bad" : ""}">${td(esc(metricName(k)))}${td(f(m.worst))}${td(f(m.p95))}${td(f(m.limit))}${td(Number.isInteger(m.over_limit) ? m.over_limit : "—")}</tr>`;
    })
    .join("");
  const table = `<table class="qm"><tr><th></th><th>最悪</th><th>p95</th><th>限度</th><th>超え</th></tr>${metrics}</table>`;
  const topo = q.topology ?? {};
  const counted = ct.freeEdges.length;
  const topoRows = TOPOLOGY_ORDER.filter((k) => Number.isInteger(topo[k]))
    .map((k) => {
      if (k !== "free_edges") return `<tr data-topology="${k}">${td(TOPOLOGY_JA[k])}${td(topo[k])}</tr>`;
      const same = topo[k] === counted;
      return `<tr data-topology="${k}" class="${same ? "" : "bad"}">${td(TOPOLOGY_JA[k])}${td(same ? `${topo[k]}` : `記録 ${topo[k]}・要素から数えて ${counted}`)}</tr>`;
    })
    .join("");
  const freeOnly = Number.isInteger(topo.free_edges) ? "" : `<tr data-topology="free_edges">${td("自由縁 (要素から数えた)")}${td(counted)}</tr>`;
  return (
    head +
    table +
    `<table class="qt">${topoRows}${freeOnly}</table>` +
    (q.notes ? `<div class="notes">${esc(q.notes)}</div>` : "")
  );
}

function thicknessCard(ct) {
  const rows = ct.rows
    .map((r) => `<tr data-t="${r.t}">${td(`<span class="dot" style="background:${r.color}"></span>${fmt(r.t, 3)} mm`)}${td(r.members.length)}${td(r.elements)}${td(esc(r.materials.join("、") || "—"))}</tr>`)
    .join("");
  return (
    `<h3>板厚と材料 <small>${esc(ct.mesh.rec.mesh_id)}</small></h3>` +
    `<table class="qm"><tr><th>板厚</th><th>部材</th><th>要素</th><th>材料</th></tr>${rows}</table>` +
    (ct.none ? `<div class="notes">厚さの記録が無い要素 ${ct.none} (灰色)</div>` : "")
  );
}

function setupCard(ct) {
  const st = ct.setup;
  const n = dofCount(st.constraints);
  const cons = st.constraints
    .map((c) => `<tr data-node="${c.node}">${td(`節 ${c.node}`)}${td(esc(c.dofs.join(" ")))}</tr>`)
    .join("");
  const loads = st.loads
    .map((l, i) => {
      const f = Math.hypot(...l.vector_N);
      const mo = Array.isArray(l.moment_Nmm) ? Math.hypot(...l.moment_Nmm) : 0;
      const sp = Array.isArray(l.spread_to) && l.spread_to.length ? `${l.spread_to.length} 節に分けた` : "1 点";
      return `<tr data-load="${i}">${td(esc(l.label ?? `荷重 ${i + 1}`))}${td(`${fmt(f / 1000, 3)} kN${mo > 0 ? `・${fmt(mo / 1000, 3)} N·m` : ""}`)}${td(sp)}</tr>`;
    })
    .join("");
  const checks = Object.entries(st.checks)
    .map(([k, q]) => `<tr data-check="${esc(k)}">${td(esc(CHECK_JA[k] ?? k))}${td(esc(fmtVU(q?.value, q?.unit)))}</tr>`)
    .join("");
  const diff = st.massDiff !== null ? `<div class="massdiff" data-rel="${st.massDiff}">モデルと設計の質量の差 ${fmtSigned(st.massDiff, 2)} (設計に対して)</div>` : "";
  const ir = st.inertiaRelief === true ? `<div class="ir">慣性リリーフ: 釣り合わない力は慣性で受ける (拘束では受けない)</div>` : "";
  return (
    `<h3>解く前の設定 <small>${esc(ct.analysisId)}・${esc(ct.mesh.rec.mesh_id)}</small></h3>` +
    `<div class="sub">拘束 ${st.constraints.length} 点 (並進 ${n.t}・回転 ${n.r})</div><table class="qt">${cons}</table>` +
    `<div class="sub">荷重 ${st.loads.length}</div><table class="qt">${loads}</table>` +
    ir +
    (checks ? `<div class="sub">点検</div><table class="qt">${checks}</table>` : "") +
    diff
  );
}

export function modeCardHtml(ct) {
  if (ct?.mode === "quality") return qualityCard(ct);
  if (ct?.mode === "thickness") return thicknessCard(ct);
  if (ct?.mode === "setup") return setupCard(ct);
  return "";
}
