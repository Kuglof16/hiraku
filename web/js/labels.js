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

// Why a run ended (run.finished stop_code), in words.
export const STOP_CODE_JA = {
  done: "完了",
  not_judged: "まだ判定の無い分野がある",
  round_limit: "繰り返しの上限に達した",
  cost_limit: "費用の上限に達した",
  turn_limit: "ターン数の上限に達した",
  user_stop: "人が止めた",
  error: "エラーで止まった",
  red_left: "赤の分野を残したまま終わった", // rounds were left, but the hooks' stop limit ended it with red domains
};

// Rule ids come from hiraku/contract/protocols.py HOOK_RULES.
export const RULES = [
  { id: "HK1", key: "HK1_prediction_first", ja: "予測が先" },
  { id: "HK2", key: "HK2_tool_usable", ja: "照合ずみの道具だけ" },
  { id: "HK3", key: "HK3_cause_before_continue", ja: "食い違いは原因が先" },
  { id: "HK4", key: "HK4_no_stop_before_judged", ja: "判定がそろうまで終われない" },
];

export const BASE_JA = {
  K0: "手計算",
  K1: "3D の梁",
  K2: "3D の車両の物理",
  K3: "電力と熱の回路",
  K4: "形・干渉・人",
  K5: "寄与と感度",
};

// Names of the quantities the tools write (names only; a name not here is shown as written).
const QUANTITY_JA = {
  // frame (K1)
  max_stress: "最大応力",
  min_safety_factor: "最小の安全率",
  max_displacement: "最大変位",
  vehicle_mass: "車両の質量",
  frame_mass: "フレームの質量",
  // step climb
  wheel_force: "輪の力",
  front_wheel_force: "前輪の力",
  front_wheel_load: "前輪の荷重",
  front_wheel_load_static: "止まっているときの前輪の荷重",
  step_wheel_force: "段差の輪の力",
  step_wheel_load: "段差の輪の荷重",
  step_wheel_load_static: "止まっているときの段差の輪の荷重",
  force_ratio: "力の比",
  corner_distance: "段の角までの距離",
  self_climb_torque: "自分で登るトルク",
  step_margin: "段差の余裕",
  push_traction: "押す駆動力",
  required_mu_push: "押すのに要る摩擦係数",
  climbed: "登れたか",
  climb_time: "登る時間",
  peak_wheel_force: "輪の力の最大",
  peak_vertical_wheel_force: "輪の上下の力の最大",
  // crash
  peak_deceleration: "最大減速度",
  mean_deceleration: "平均減速度",
  peak_force: "最大の力",
  max_crush: "最大のつぶれ",
  crash_energy: "衝突のエネルギー",
  stop_time: "止まるまでの時間",
  occupant_space_margin: "乗員の空間の余裕",
  battery_inertia_force: "電池の慣性力",
  inertia_acceleration: "慣性の加速度",
  // fatigue
  fatigue_damage: "疲労の損傷度",
  fatigue_life: "疲労寿命",
  max_stress_range: "最大の応力範囲",
  equivalent_stress_range: "等価応力範囲",
  cycles_counted: "数えた繰り返し",
  mean_stress: "平均応力",
  // nvh
  first_body_mode: "車体の 1 次の固有振動数",
  first_frame_mode: "フレームの 1 次の固有振動数",
  wheel_rotation_frequency: "車輪の回転の周波数",
  motor_rotation_frequency: "モーターの回転の周波数",
  excitation_gap: "加振との離れ",
  // powertrain and thermal (K3)
  energy_used: "使った電力量",
  soc_end: "終わりの電池残量",
  soc_used: "使った電池残量",
  range: "航続距離",
  distance_on_battery: "電池で走れる距離",
  max_battery_power: "電池の最大電力",
  max_battery_current: "電池の最大電流",
  drive_time: "走った時間",
  mean_speed: "平均速度",
  // vehicle motion (K2)
  tilt_angle: "転倒する傾き",
  tilt_angle_right: "右へ転倒する傾き",
  tilt_angle_left: "左へ転倒する傾き",
  min_side_load: "軽い側の輪の荷重の和 (最小)",
  static_stability_factor: "静的安定係数",
  body_roll_at_tip: "転倒のときの車体のロール",
  cg_shift_at_tip: "転倒のときの重心のずれ",
  friction_demand_at_tip: "転倒のときに要る摩擦",
  slides_first: "先に滑るか",
  critical_wind_speed: "転倒する風速",
  slide_wind_speed: "滑る風速",
  side_force: "横力",
  min_upwind_wheel_load: "風上の輪の最小荷重",
  lateral_drift: "横へのずれ",
  stopping_distance: "停止距離",
  braking_distance: "制動距離",
  mean_decel: "平均減速度",
  peak_decel: "最大減速度",
  min_wheel_load: "輪の最小荷重",
  min_rear_wheel_load: "後輪の最小荷重",
  max_pitch: "最大ピッチ",
  parking_drift: "止めたときのずれ",
  time_to_speed: "速度までの時間",
  distance_to_speed: "速度までの距離",
  mean_accel: "平均加速度",
  top_speed: "最高速度",
  reached_target: "目標に届いたか",
  max_wheel_slip: "車輪の最大のすべり",
  moved_uphill: "坂を登れたか",
  wheel_lift: "輪が浮いたか",
  two_wheel_lift: "2 輪が浮いたか",
  two_wheel_lift_time: "2 輪が浮いた時間",
  max_lateral_accel: "最大横加速度",
  max_roll: "最大ロール",
  max_path_error: "進路の最大のずれ",
  max_steer: "最大の舵角",
  max_speed_no_lift: "輪が浮かない最高速度",
  min_uphill_load: "山側の最小荷重",
  rms_vertical_accel: "上下加速度の RMS",
  // assembly (K4)
  interference_count: "干渉の数",
  worst_interference: "いちばん深い干渉",
  motion_interference_count: "動いたときの干渉の数",
  worst_motion_interference: "動いたときのいちばん深い干渉",
  assembly_steps: "組み立ての手順の数",
  blocked_parts: "入らない部品の数",
  steps_without_tool_clearance: "工具が入らない手順の数",
  steps_out_of_reach: "手が届かない手順の数",
  free_steer_angle: "当たらずに切れる舵角",
  max_steer_angle: "最大の舵角",
  // the shell solver (frame_shell): the judged hot-spot stress and the half-size mesh check
  max_hot_spot_stress: "構造的ホットスポット応力 (溶接線)",
  max_hot_spot_stress_half_size: "ホットスポット応力 (要素を半分に)",
  hot_spot_change_half_size: "要素を半分にしたときの変化",
  max_stress_half_size: "要素の最大 (要素を半分に)",
  // the K&C bench (kc_bench): abscissas (series x.quantity) and channels
  travel: "ストローク",
  lateral_force: "横力",
  longitudinal_force: "前後力",
  aligning_moment: "セルフアライニングトルク",
  camber: "キャンバーの変化",
  toe: "トーの変化",
  roll_center_height: "ロールセンターの高さ",
  track_change: "輪の中心の横の動き",
  toe_lateral: "横力によるトー",
  camber_lateral: "横力によるキャンバー",
  toe_longitudinal: "前後力によるトー",
  camber_longitudinal: "前後力によるキャンバー",
  toe_aligning: "アライニングトルクによるトー",
};

const VECTOR_JA = { reaction: "反力", grip: "グリップ", load: "荷重", wind: "風", inertia: "慣性力", joint: "取付点の力" };

export function vectorName(kind) {
  return VECTOR_JA[kind] ?? kind;
}

// Results that answer yes or no (recorded as 1 or 0).
const YES_NO = new Set(["slides_first", "wheel_lift", "two_wheel_lift", "climbed", "reached_target", "moved_uphill"]);

// A result's value with its unit, or はい / いいえ for a yes-or-no result.
export function resultText(r, sig = 3) {
  if (YES_NO.has(r?.quantity)) return r.value >= 0.5 ? "はい" : "いいえ";
  return fmtVU(r?.value, r?.unit, sig);
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

// A rule by its full key, its id ("HK1") or, for records written before the
// ids were renamed (H1_prediction_first), by the words after the id.
export function ruleOf(rule) {
  const key = String(rule ?? "");
  const [id, ...rest] = key.split("_");
  const tail = rest.join("_");
  return (
    RULES.find((r) => r.key === key || r.id === id) ??
    (tail ? RULES.find((r) => r.key.endsWith(`_${tail}`)) : null) ?? { id: id || "?", key: rule, ja: rule ?? "" }
  );
}

const UNIT_LABEL = {
  1: "",
  degC: "℃",
  deg: "°",
  "m/s^2": "m/s²",
  "N*mm": "N·mm",
  "N*m": "N·m",
  "%": "%",
  "mm^2": "mm²",
  "mm^3": "mm³",
  "mm^4": "mm⁴",
  "m^2": "m²",
  "kg/m^3": "kg/m³",
  "kg*m^2": "kg·m²",
  ohm: "Ω",
};

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

// What an agent said, as plain text: Markdown marks (bold, headings, code,
// links, list bullets) taken off, line breaks kept (blank lines dropped).
export function plainText(md) {
  return String(md ?? "")
    .replace(/\r\n?/g, "\n")
    .replace(/^```[^\n]*\n?/gm, "")
    .replace(/`([^`\n]*)`/g, "$1")
    .replace(/\*\*([^*\n]+)\*\*/g, "$1")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/^\s*[-*]\s+/gm, "・")
    .replace(/\[([^\]\n]+)\]\([^)\n]*\)/g, "$1")
    .replace(/\n\s*\n+/g, "\n")
    .trim();
}
