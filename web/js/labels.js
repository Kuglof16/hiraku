// Names shown on the screen and number formatting. Formatting only rounds
// recorded values for display; it never makes a new number.

export const DOMAINS = ["handling", "powertrain", "aero", "crash", "strength", "nvh", "thermal", "assembly"];

export const DOMAIN_JA = {
  handling: "操安",
  powertrain: "動力",
  aero: "空力",
  crash: "衝突",
  strength: "強度信頼性",
  nvh: "音振",
  thermal: "熱",
  assembly: "組み立て",
};

export const ROLE_JA = {
  lead: "まとめ役",
  designer: "設計役",
  packager: "パッケージ役",
  system: "HIRAKU",
  user: "あなた",
};

export const PROP_JA = {
  binder: "バインダー",
  pencil: "鉛筆",
  spanner: "スパナ",
  steering_wheel: "ハンドル",
  battery: "電池",
  windsock: "吹き流し",
  crash_target: "衝突試験の的",
  i_beam: "I 形の梁",
  tuning_fork: "音叉",
  thermometer: "温度計",
};

// Rule ids come from hiraku/contract/protocols.py HOOK_RULES.
export const RULES = [
  { id: "H1", key: "H1_prediction_first", ja: "予測が先" },
  { id: "H2", key: "H2_tool_usable", ja: "照合ずみの道具だけ" },
  { id: "H3", key: "H3_cause_before_continue", ja: "食い違いは原因が先" },
  { id: "H4", key: "H4_no_stop_before_judged", ja: "判定がそろうまで終われない" },
];

export const BASE_JA = {
  K0: "手計算",
  K1: "3D の梁",
  K2: "3D の車両の物理",
  K3: "電力と熱の回路",
  K4: "形・干渉・人",
  K5: "寄与と感度",
};

const QUANTITY_JA = {
  max_stress: "最大応力",
  front_wheel_force: "前輪の力",
  wheel_force: "輪の力",
  max_displacement: "最大変位",
};

const VECTOR_JA = { reaction: "反力", grip: "グリップ", load: "荷重", wind: "風", inertia: "慣性力" };

export function vectorName(kind) {
  return VECTOR_JA[kind] ?? kind;
}

export function quantityName(q) {
  return QUANTITY_JA[q] ?? q ?? "";
}

export function agentName(id) {
  if (!id) return "";
  if (ROLE_JA[id]) return ROLE_JA[id];
  if (id.startsWith("cae.")) return DOMAIN_JA[id.slice(4)] ?? id;
  return id;
}

export function agentLongName(id) {
  if (id && id.startsWith("cae.")) return `${agentName(id)}の CAE 役`;
  return agentName(id);
}

export function ruleOf(rule) {
  const id = String(rule ?? "").split("_")[0];
  return RULES.find((r) => r.id === id) ?? { id: id || "?", key: rule, ja: rule ?? "" };
}

const UNIT_LABEL = { 1: "", degC: "℃", "m/s^2": "m/s²", "N*mm": "N·mm", "N*m": "N·m", "%": "%" };

export function unitLabel(u) {
  return u in UNIT_LABEL ? UNIT_LABEL[u] : u ?? "";
}

// Up to 4 significant digits, no exponent for everyday magnitudes.
export function fmt(v, sig = 4) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  if (typeof v !== "number") return String(v);
  if (v === 0) return "0";
  const a = Math.abs(v);
  if (a >= 1e6 || a < 1e-3) return v.toExponential(2);
  const digits = Math.max(0, sig - 1 - Math.floor(Math.log10(a)));
  return Number(v.toFixed(Math.min(digits, 6))).toLocaleString("ja-JP", { maximumFractionDigits: 6 });
}

export function fmtQ(q, sig) {
  if (!q) return "—";
  const u = unitLabel(q.unit);
  return u ? `${fmt(q.value, sig)} ${u}` : fmt(q.value, sig);
}

export function fmtVU(value, unit, sig) {
  return fmtQ({ value, unit }, sig);
}

export function fmtPct(rel, digits = 1) {
  if (rel === null || rel === undefined) return "—";
  return `${(rel * 100).toFixed(digits)} %`;
}

export function fmtSigned(rel, digits = 1) {
  if (rel === null || rel === undefined) return "—";
  const s = (rel * 100).toFixed(digits);
  return `${rel > 0 ? "+" : ""}${s} %`;
}

const OP = { "<=": "≤", ">=": "≥", "<": "<", ">": ">" };

export function opLabel(op) {
  return OP[op] ?? op ?? "";
}

export function fmtTime(t) {
  return (t ?? 0).toFixed(1);
}

export function fmtUsd(v) {
  return v === null || v === undefined ? "—" : `$${v.toFixed(2)}`;
}

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
}
