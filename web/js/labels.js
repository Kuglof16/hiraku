// Names shown on the screen and number formatting. Formatting only rounds
// recorded values for display; it never makes a new number.

// The cuts' words for a pass and a fail (one wording on every cut).
export const OK_WORD = "✓ OK";
export const NG_WORD = "✕ NG";
// An agent's status word after a mismatch and after a red judgement (app.js marks both as alerts).
export const MISMATCH_STATUS = "NG (食い違い)";
export const RED_STATUS = "NG";

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

// The domains in the viewers' words (the video's pages; DOMAIN_JA keeps the trade's short names).
export const DOMAIN_PLAIN_JA = {
  handling: "倒れない・よけられる",
  powertrain: "走る力",
  aero: "風",
  crash: "ぶつかったとき",
  strength: "壊れない",
  nvh: "揺れ・音",
  thermal: "熱くなりすぎない",
  assembly: "組める・手が届く",
};


// The top tabs (web/js/tabs.js), in the order of use, then the two on the side. no: the step's
// number (the side ones have none); tip: what the tab is for, in one sentence (the tab's title,
// and the bubble of a long press); id: the screen's name inside HIRAKU (shown under ?dev=1).
export const TAB_FLOW = ["A1", "A2", "bench", "grid", "A7"];
export const TAB_SIDE = ["A4", "A5"];
export const TAB_JA = {
  A1: { no: "①", name: "作りたいもの", tip: "作りたいものを書くと、HIRAKU が調べる点を選びます", id: "A1" },
  A2: { no: "②", name: "確認したいこと", tip: "HIRAKU が足りない情報を聞きます。答えなくても標準の値で進めます", id: "A2" },
  bench: { no: "③", name: "解析", tip: "予測と解析を照らし合わせる様子を 3D で見ます", id: "A3 (bench)" },
  // the grid's tiles: the domains, and assembly (tiles.js TILE_ORDER); the count from DOMAINS
  grid: { no: "④", name: "解析結果", tip: `${DOMAINS.filter((d) => d !== "assembly").length} つの分野と組み立ての解析結果を一度に見ます。分野を押すと、その分野の解析を再生します`, id: "A6 (grid)" },
  A7: { no: "⑤", name: "組み立て", tip: "組み立ての順番を 3D で確かめます", id: "A7" },
  A4: { no: "", name: "ツールの棚", tip: "Claude が作ったツールと、判定の結果を見ます", id: "A4" },
  A5: { no: "", name: "守るルール", tip: "設計のチェックに使うルールを見ます。ファイルを置くと足せます", id: "A5" },
};
// A design version on the screen: v7 (one wording everywhere; null: v—).
export const verLabel = (n) => `v${n ?? "—"}`;
export const TAB_EMPTY_JA ="この実行ではまだありません";
export const TAB_FLOW_JA = "使う順"; // the flow group's name (read aloud, aria-label)
export const TAB_SIDE_JA = "脇のツール";
// A tab's name as the other screens' sentences say it (「① 作りたいもの」).
export const tabWord = (key) => `「${TAB_JA[key].no ? `${TAB_JA[key].no} ` : ""}${TAB_JA[key].name}」`;

// The trade's words in the record's sentences, and what the video's pages say instead.
const SENTENCE_PLAIN = [
  [/荷重条件の作り方/g, "試し方"],
  [/荷重条件/g, "試す条件"],
];

// A test id or a rule id in a sentence ("S2 ", "U1-04", "HU-02", "R1s").
const ID_RE = /(?<![A-Za-z0-9])(?:[A-Z]{1,3}\d*-\d+|[A-Z]\d+[a-z]?)(?![A-Za-z0-9-])/g;

// A sentence of the record for the viewers: ids taken out, the trade's words replaced.
export function plainSentence(text) {
  let s = String(text ?? "").replace(ID_RE, "").replace(/\s{2,}/g, " ");
  for (const [re, w] of SENTENCE_PLAIN) s = s.replace(re, w);
  return s.replace(/^[\s・、]+/, "").replace(/\s+([、。」])/g, "$1").trim();
}

// Why a domain is not looked at, from the record's reason (domains.picked): the sentence
// that says so (the one ending in "見ない"), else the words before a list (":"), with the
// tests' ids and the bracketed asides taken out.
export function plainReason(reason) {
  const s = String(reason ?? "");
  const said = s.split("。").map((x) => x.trim()).find((x) => x.includes("見ない"));
  const line = (said ?? s.split(/[:：]/)[0]).replace(/\s*[（(][^)）]*[)）]/g, "");
  return plainSentence(line);
}

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
  red_left: "NG の分野を残したまま終わった", // rounds were left, but the hooks' stop limit ended it with red domains
};

// Why a role stopped (agent.stopped stop_code, contract a31e520), in words. run_ended: the run
// ended while the role was still working (the run's own reason is run.finished's stop_code).
export const AGENT_STOP_JA = {
  done: "自分の仕事を終えた",
  run_ended: "動いている間に実行が終わった",
  user_stop: "人が止めた",
  error: "エラーで終わった",
};
// The same, short, for the line under the role's name in the agents' pane (the name is on the row
// above, so no 「…が手を止めた」). done is the plain end (drawn faint); the others stand out (app.css .why).
export const AGENT_STOP_SHORT_JA = {
  done: "仕事を終えた",
  run_ended: "動いている間に実行が終わった",
  user_stop: "人が止めた",
  error: "エラーで終わった",
};
export const AGENT_STOP_NOTE_CHARS = 40; // a stop_reason up to this long is shown as it is; a longer one goes under くわしく / into the title

// agent.stopped's data said for people: { code, words, reason } (words: AGENT_STOP_JA of stop_code;
// reason: stop_reason, trimmed), or null when the record says neither (records from before a31e520).
export function agentStopWhy(d) {
  const code = typeof d?.stop_code === "string" ? d.stop_code : null;
  const reason = typeof d?.stop_reason === "string" && d.stop_reason.trim() ? d.stop_reason.trim() : null;
  const words = AGENT_STOP_JA[code] ?? null;
  if (!words && !reason) return null;
  return { code, words, reason };
}

// Rule ids come from hiraku/contract/protocols.py HOOK_RULES.
export const RULES = [
  { id: "HK1", key: "HK1_prediction_first", ja: "予測が先" },
  { id: "HK2", key: "HK2_tool_usable", ja: "OK のツールだけ" },
  { id: "HK3", key: "HK3_cause_before_continue", ja: "食い違いは原因が先" },
  { id: "HK4", key: "HK4_no_stop_before_judged", ja: "判定がそろうまで終われない" },
  { id: "HK5", key: "HK5_light_first", ja: "軽い計算から" },
  { id: "HK6", key: "HK6_module_scope", ja: "持ち場" }, // modules: contract d9631a5
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
  // roll-over protection (ROPS): tools the CAE agents wrote in the runs (rops_frame_pushover, rops_pushover_frame)
  rops_occupant_clearance: "ロールバーと乗員の空間の隙間",
  rops_clearance_side: "横から押したときの乗員の空間との隙間",
  rops_clearance_rear: "後ろから押したときの乗員の空間との隙間",
  rops_clearance_top: "上から押したときの乗員の空間との隙間",
  rops_side_deflection: "横から押した点の動き",
  rops_rear_deflection: "後ろから押した点の動き",
  rops_top_deflection: "上から押した点の動き",
  rops_side_collapse_load: "横から押して崩れる荷重",
  rops_rear_collapse_load: "後ろから押して崩れる荷重",
  rops_top_collapse_load: "上から押して崩れる荷重",
  rops_collapse_load_side: "横から押して崩れる荷重",
  rops_collapse_load_rear: "後ろから押して崩れる荷重",
  rops_collapse_load_top: "上から押して崩れる荷重",
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
  // held: the joints and the modules' spaces (hiraku/solvers/shelf/assembly.py at 7fc0ffc)
  joint_gap_count: "つながりの不足の数",
  joint_gaps: "離れたつながりの数",
  unjoined_parts: "つながりの無い部品の数",
  worst_joint_gap: "いちばん離れたつながり",
  envelope_outside_count: "モジュールの空間からはみ出した部品の数",
  worst_envelope_outside: "いちばん大きいはみ出し",
  free_steer_angle: "当たらずに切れる舵角",
  max_steer_angle: "最大の舵角",
  // the shell solver (frame_shell): the judged hot-spot stress
  max_hot_spot_stress: "構造的ホットスポット応力 (溶接線)",
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
  // judged quantities of the scripted video runs (report lines)
  temp_battery_max: "電池のいちばん高い温度",
  temp_controller_max: "制御器のいちばん高い温度",
  knee_room: "ひざの前のすき間",
  tip_slope_angle: "山側の輪が浮く斜面の角度",
  climbable: "段差を登れるか",
  // the quantities of the N15 v5 record without a name before (one per id; a dotted id reads as
  // its base's name and what follows the dot, quantityName)
  mean_uphill_load: "山側の輪の平均の荷重",
  free_steer_angle_geometric: "当たらずに切れる舵角 (平面の幾何)",
  free_parts: "枠につながっていない部品の数",
  free_part: "枠につながっていない部品",
  unsupported_load: "支えの無い荷重",
  grip_deflection: "握る点の動き",
  pedal_deflection: "ペダルの面の動き",
  occupant_push_deflection: "乗員を押したときの外側の動き",
  occupant_push_open: "外側に何も無い押す点の数",
  max_utilisation: "応力と許しの比 (最大)",
  allowable_stress: "許される応力",
  max_part_deflection: "部品の最大の動き",
  temp_motor_max: "モーターのいちばん高い温度",
  temp_battery_end: "終わりの電池の温度",
  temp_controller_end: "終わりの制御器の温度",
  temp_motor_end: "終わりのモーターの温度",
  loss_battery_mean: "電池の平均の損失",
  loss_controller_mean: "制御器の平均の損失",
  loss_motor_mean: "モーターの平均の損失",
  wheel_load_FL: "前左の輪の荷重",
  wheel_load_FR: "前右の輪の荷重",
  wheel_load_RL: "後左の輪の荷重",
  wheel_load_RR: "後右の輪の荷重",
  min_rear_axle_load: "後軸の最小荷重",
  peak_tractive_force: "最大の駆動力",
  top_speed_power: "出力で決まる最高速度",
  mean_decel_g: "平均減速度 (g)",
  max_lateral_accel_g: "最大横加速度 (g)",
  speed_at_stop: "止まったときの速さ",
  head_clearance: "頭の上のすき間",
  hip_room: "腰まわりのすき間",
  shoulder_room: "肩まわりのすき間",
  leg_clearance: "脚まわりのすき間",
  foot_room: "足元のすき間",
  knee_angle_room: "ひざの角度の余り",
  pedal_reach: "ペダルへの届き",
  spray_reaching_occupant: "乗員に届く巻き上げの線の数",
  spray_uncovered_arc: "覆われていない巻き上げの弧",
  first_rollbar_mode: "ロールバーの 1 次の固有振動数",
  driveshaft_bending_mode: "ドライブシャフトの曲げの固有振動数",
  driveshaft_torsion_mode: "ドライブシャフトのねじりの固有振動数",
  fatigue_damage: "疲労の損傷度",
  max_stress_axial: "軸力の応力 (最大)",
  max_stress_beam: "梁の応力 (最大)",
  max_stress_bending_sideways: "横の曲げの応力 (最大)",
  max_stress_bending_vertical: "縦の曲げの応力 (最大)",
  max_stress_torsion: "ねじりの応力 (最大)",
  tipped: "倒れたか",
  lifted_by_push: "押して浮いたか",
  // the quantities of the shelf's known-answer checks (tool_check.quantity; the meaning from each check's case
  // and source in hiraku/solvers). Checks that write a sentence as the quantity are left as written.
  // K2 vehicle checks (k2_vehicle/checks.py and the K2 shelf tools)
  wheel_load: "輪にかかる荷重",
  wheel_load_sum: "輪の荷重の和",
  wheel_load_difference: "左右の輪の荷重の差",
  total_wheel_load: "全部の輪の荷重の和",
  front_axle_load: "前の軸の荷重",
  uphill_load: "山側の輪の荷重",
  aligning_moment_sign: "セルフアライニングモーメントの向き (合えば 1)",
  cornering_stiffness: "コーナリングスティフネス",
  slip_stiffness: "スリップの剛性 (前後)",
  peak_lateral_force: "横力の最大",
  peak_longitudinal_force: "前後力の最大",
  friction_ellipse: "摩擦楕円に対する比 (最大)",
  fall_error: "自由落下の計算のずれ",
  parked_drift: "坂に止めたときのずれ",
  ride_frequency: "ばね上の上下の固有振動数",
  yaw: "ヨー角",
  yaw_rate: "ヨーレート",
  yaw_mirror_error: "左右に切ったときのヨー角の対称のずれ",
  lateral_mirror_error: "左右に切ったときの横位置の対称のずれ",
  yaw_rate_mirror_error: "左右に曲がったときのヨーレートの対称のずれ",
  tilt_angle_difference: "転倒する傾きの左右の差",
  critical_wind_speed_difference: "転倒する風速の左右の差",
  psd_ratio: "作った路面の凹凸と規格の比 (PSD)",
  G_d_at_n0: "基準の波数での路面の凹凸の大きさ (G_d(n0))",
  damper_force: "ダンパーの力",
  upper_arm_force: "アッパーアームの力",
  mount_force_sum_error: "取付点の力の和のずれ",
  compliance_ratio_error: "剛性を 2 倍にしたときのたわみやすさの比のずれ",
  toe_per_aligning_moment: "アライニングモーメントあたりのトーの変化",
  // K1 frame and shell
  root_stress: "根元の応力",
  von_mises: "ミーゼス応力",
  mid_deflection: "中央のたわみ",
  tip_deflection: "先端のたわみ",
  tip_twist: "先端のねじれ角",
  centre_deflection: "板の中央のたわみ",
  Kt_gross: "穴のまわりの応力集中係数 (総断面)",
  shell_weight: "シェルの重さ",
  hold_reaction: "押さえの点の反力 (重さとの比)",
  force_imbalance: "力のつり合いのずれ (重さとの比)",
  first_frequency: "1 次の固有振動数",
  bounce_frequency: "上下に揺れる固有振動数",
  // load path (load_path.py)
  box_deflection: "箱を押したときのたわみ",
  top_deflection: "積んだ箱のいちばん上のたわみ",
  plate_middle_deflection: "板の中央のたわみ",
  linear_deflection: "張った線の線形のたわみ",
  sag: "張った線のたわみ",
  post1_load: "柱 1 の荷重",
  post2_load: "柱 2 の荷重",
  held_box_free: "支えのある箱を浮いているとしたか",
  max_utilisation_is_the_tip: "いちばん厳しいのが先端の部材か",
  // crash
  deceleration: "減速度",
  energy_absorbed: "吸収したエネルギー",
  // fatigue (fatigue.py, bed_use.py)
  damage: "疲労の損傷度",
  damage_frame: "フレームの溶接の損傷度",
  damage_lug_back: "後ろの耳 (ラグ) の溶接の損傷度",
  damage_worst: "いちばん大きい溶接の損傷度",
  lug_root_stress: "耳 (ラグ) の根元の応力",
  cycles_at_FAT: "FAT の応力範囲での繰り返し数",
  cycles_at_half_the_knee_range: "折れ点の半分の応力範囲での繰り返し数",
  sum_of_half_ranges: "レインフローの範囲の和 (半分)",
  count_of_range_3: "レインフローで数えた範囲 3 の回数",
  count_of_range_4: "レインフローで数えた範囲 4 の回数",
  count_of_range_6: "レインフローで数えた範囲 6 の回数",
  count_of_range_8: "レインフローで数えた範囲 8 の回数",
  count_of_range_9: "レインフローで数えた範囲 9 の回数",
  // care bed (bed_tools.py, bed_use.py)
  support_load_A: "支点 A の荷重",
  support_load_B: "支点 B の荷重",
  support_load_C: "支点 C の荷重",
  support_load_A_plus_B: "支点 A と B の荷重の和",
  force_at_45deg: "45° 上げたときのアクチュエーターの力",
  force_flat: "水平のときのアクチュエーターの力",
  stroke: "アクチュエーターのストローク",
  lever_stroke: "アクチュエーターのストローク (45° まで)",
  duty: "1 時間のうちアクチュエーターが動く割合",
  run_time: "続けて動く時間 (1 ストローク)",
  // K3 power and heat
  current: "電流",
  heat_in: "入れた熱量",
  rise_at_tau: "時定数の時刻の温度上昇",
  steady_rise: "落ち着いたときの温度上昇",
  // K4 geometry, entry, spray
  overlap: "箱どうしの重なり (負は隙間)",
  capsule_overlap: "丸棒の重なり (負は隙間)",
  lid_after_content: "中身の後にふたを組むか",
  entry_opening_width: "乗り口の幅",
  entry_opening_height: "乗り口の高さ",
  entry_step_height: "乗り口の最初の足場の高さ",
  entry_blocked: "乗り口をふさぐ部材の数",
  entry_handhold: "乗り口の手がかりの数",
  t_enter: "線が箱に入るまでの距離",
  hits: "線が当たったか",
};

// Names of the series channels (the name before the dot: wheel_camber.FL -> wheel_camber) that
// series.js GROUPS does not name, for the graph's chips and the tiles' small charts. Names only;
// a channel not here is shown as written.
const CHANNEL_JA = {
  wheel_camber: "キャンバー",
  wheel_toe: "トー",
  wheel_torque: "輪のトルク",
  mount: "取付点の力",
  temp: "温度",
  frequency_response: "周波数応答",
  deceleration: "減速度",
  decel: "減速度",
  crush: "つぶれ",
  wall_force: "壁の力",
  soc: "電池残量",
  power: "出力",
  current: "電流",
  grade: "勾配",
  slope_angle: "斜面の角度",
  wind_speed: "風速",
  path_y: "横の位置",
  body_torque: "車体にかかるトルク",
  stress: "応力",
  stress_ranges: "応力の幅",
  sn_curve: "S-N 線図",
  inertia_force: "慣性力",
  inertia_moment: "慣性力のモーメント",
  table_angle: "台の角度",
  steer: "操舵角",
  accel: "加速度",
  cg: "重心",
  roll: "ロール",
  pitch: "ピッチ",
  sprung_accel: "ばね上の加速度",
  sprung_ang_vel: "ばね上の角速度",
  sprung_cg: "ばね上の重心",
  sprung_roll: "ばね上のロール",
  sprung_pitch: "ばね上のピッチ",
  sprung_yaw: "ばね上のヨー",
  speed: "速さ",
  yaw: "ヨー",
};
// The names after the dot that are not a wheel (series.js wheelName): the parts a quantity is of
// (temp.battery), the suspension's mounts (mount_x.FL.lca_front). Not here: shown as written.
const CHANNEL_PART_JA = {
  battery: "電池",
  motor: "モーター",
  controller: "制御器",
  damper_upper: "ダンパー上",
  lca_front: "下アーム前",
  lca_rear: "下アーム後",
  uca_front: "上アーム前",
  uca_rear: "上アーム後",
  tie_inner: "タイロッド内",
};
// The axis at the end of a name (inertia_force_x): the road frame's (x along the road, y left, z up).
const AXIS_JA = { x: "前後", y: "左右", z: "上下" };
export function channelName(base) {
  if (CHANNEL_JA[base]) return CHANNEL_JA[base];
  const m = /^(.+)_([xyz])$/.exec(base ?? "");
  if (m && CHANNEL_JA[m[1]]) return `${CHANNEL_JA[m[1]]} (${AXIS_JA[m[2]]})`;
  return base;
}
export function channelPartName(id) {
  return CHANNEL_PART_JA[id] ?? id;
}

// Names for viewers who do not know CAE (the video's pages): the trade's words left out.
// A quantity not listed here reads as quantityName.
const PLAIN_JA = {
  max_hot_spot_stress: "つなぎ目の応力",
  min_side_load: "軽い側の輪の重さ",
  first_frame_mode: "フレームのいちばん低い揺れの速さ",
  fatigue_damage: "くり返しの力による傷み",
  // the known-answer checks of the use-count fatigue tool (N13) and its results
  cycles_at_FAT: "基準の強さで壊れるまでの回数",
  seat_stress_range: "人が乗る・降りるときの枠の応力の幅",
  cycles_counted: "くり返しの回数",
  // the known-answer checks of the roll bar tool (N13, rops_pushover): a cantilever's tip
  // deflection and the side load that collapses a fixed-base portal
  tip_deflection: "片持ちの管の先のたわみ",
  collapse_load: "門形の枠が横に崩れる力",
};

// Names for viewers of tools Claude wrote in a run (tool.checked name; their titles are
// sentences for the makers). A tool not listed has none (null).
const PLAIN_TOOL_JA = {
  // title: "K1 の骨組で、ロールバーに塑性ヒンジを順に入れて押す (…) と、乗員の空間との隙間"
  // (run 20261103-234711-offroad-4w, test R4: the roll-over protective structure)
  rops_pushover: "転倒の守りを確かめるツール",
};

export function plainToolName(name) {
  return PLAIN_TOOL_JA[name] ?? null;
}

export function plainName(q) {
  return PLAIN_JA[q] ?? quantityName(q);
}

// Counts read with 回 after them (their unit is 1).
const COUNT_QUANTITIES = new Set(["cycles_counted", "cycles_at_FAT"]);

// A value for viewers: its unit's sign, or 回 for a count.
export function plainValue(q, value, unit, sig = 3) {
  // a count is written out in full (2,000,000 回, not 2.00e+6), rounded to a whole number
  if (COUNT_QUANTITIES.has(q) && (unit === "1" || !unit) && Number.isFinite(value)) return `${Math.round(value).toLocaleString("ja-JP")} 回`;
  return fmtVU(value, unit, sig);
}

// The inputs of a K0 formula in the viewers' words (prediction.inputs, by formula_id). An
// input not listed has no name (null): a page leaves it out rather than show the symbol.
const FORMULA_INPUT_JA = {
  "tilt.spring_roll_angle": {
    t: "左右の輪の間隔",
    h: "重心の高さ",
    e: "重心の横のずれ",
    m: "全体の重さ",
    m_s: "ばねの上の重さ",
    h_s: "ばねの上の重心の高さ",
    k: "ばねの硬さ",
    n: "ばねの数",
  },
  "tilt.rigid_ssf": { t: "左右の輪の間隔", h: "重心の高さ" },
};

export function formulaInputName(formulaId, symbol) {
  return FORMULA_INPUT_JA[formulaId]?.[symbol] ?? null;
}

// A known-answer check's source, short, for viewers (tool.checked checks[].source is a
// sentence for the makers): the kinds of source it names; null when none is recognised.
export function checkSourceShort(source) {
  const s = String(source ?? "");
  const out = [];
  if (/range\s*=|I\s*=|simply supported/i.test(s)) out.push("梁の手計算の式");
  if (/\bIIW\b/.test(s)) out.push("国際溶接学会 (IIW) の疲労の線");
  if (/\bNeal\b|Plastic Methods/.test(s)) out.push("骨組が崩れる力の教科書 (Neal)");
  return out.length ? out.join("・") : null;
}

// The design's numbers that the reports' next steps and sensitivities name, in the viewers'
// words (K5's paths: variables.<name>, sections.<id>.dims_mm.<dim>, parts.<id>.<field>,
// materials.<id>.<field>). The record has no names for variables or sections; a number not
// listed here has no name (null), and a page leaves it out rather than show the path.
const VARIABLE_JA = {
  motor_peak_torque: "モーターの最大トルク",
  motor_peak_power: "モーターの最大出力",
  gear_ratio: "減速比",
  battery_capacity: "電池の容量",
  battery_x: "電池の前後の位置",
  wheelbase: "ホイールベース",
  track: "トレッド",
  // modules: the variables a module designer changes in the sample records
  front_spring_rate: "前のばねの硬さ",
  rear_spring_rate: "後ろのばねの硬さ",
  cargo_height: "荷の高さ",
  rail_t: "レールの板厚",
  seat_x: "座席の前後の位置",
  max_steer_angle: "最大の舵角",
};

// A design variable's name for viewers: design.variable_names (written by the generator,
// contract b615e2c) when it names it, else VARIABLE_JA; null when neither does.
export function variableName(name, design = null) {
  const own = design?.variable_names?.[name];
  if (typeof own === "string" && own.trim()) return own;
  return VARIABLE_JA[name] ?? null;
}

// modules: module id -> name (filled from the server's /api/modules, the contract's MODULE_NAMES_JA;
// empty when the server has no such route: the id is shown as written)
export const MODULE_JA = {};
export const moduleName = (m) => MODULE_JA[m] ?? m ?? "";
const DIM_JA = { t: "板厚", h: "高さ", b: "幅", d: "直径" };
const PART_FIELD_JA = { mass_kg: "重さ" };
const MATERIAL_FIELD_JA = { E_MPa: "材料の硬さ (ヤング率)" };

// "variables.motor_peak_torque" -> "モーターの最大トルク"; "sections.rt40x80x4.5.dims_mm.t"
// -> "フレームの管の板厚"; "parts.battery.mass_kg" -> "電池の重さ" (the part's name in the
// design, before any bracket); null when it has no name here.
export function designNumberName(path, design = null) {
  const p = String(path ?? "").split(".");
  // a variable: the design's own name for it (design.variable_names, contract b615e2c), else this table
  if (p[0] === "variables") return variableName(p[1], design);
  if (p[0] === "sections" && p.at(-2) === "dims_mm") return DIM_JA[p.at(-1)] ? `フレームの管の${DIM_JA[p.at(-1)]}` : null;
  if (p[0] === "parts" && PART_FIELD_JA[p.at(-1)]) {
    const part = (design?.parts ?? []).find((x) => x.id === p[1]);
    const name = part?.name ? String(part.name).replace(/\s*[（(].*$/, "") : null;
    return name ? `${name}の${PART_FIELD_JA[p.at(-1)]}` : null;
  }
  if (p[0] === "materials") return MATERIAL_FIELD_JA[p.at(-1)] ?? null;
  return null;
}

const VECTOR_JA = { reaction: "反力", grip: "グリップ", load: "荷重", wind: "風", inertia: "慣性力", joint: "取付点の力" };

export function vectorName(kind) {
  return VECTOR_JA[kind] ?? kind;
}

// Results that answer yes or no (recorded as 1 or 0).
const YES_NO = new Set(["slides_first", "wheel_lift", "two_wheel_lift", "climbed", "climbable", "reached_target", "moved_uphill"]);
export const isYesNo = (q) => YES_NO.has(q);

// A result's value with its unit, or はい / いいえ for a yes-or-no result.
export function resultText(r, sig = 3) {
  if (YES_NO.has(r?.quantity)) return r.value >= 0.5 ? "はい" : "いいえ";
  return fmtVU(r?.value, r?.unit, sig);
}

export function quantityName(q) {
  if (q == null) return "";
  if (QUANTITY_JA[q]) return QUANTITY_JA[q];
  // max_stress.rail_L_FA, pedal_deflection.pedal_brake: the base's name, then what it is of
  const dot = String(q).indexOf(".");
  if (dot > 0 && QUANTITY_JA[q.slice(0, dot)]) {
    const base = q.slice(0, dot), part = q.slice(dot + 1);
    return `${QUANTITY_JA[base]} (${QUANTITY_PART_JA[base]?.[part] ?? part})`;
  }
  return q;
}
// The words that tell the judged number from a value shown for reference (the present card,
// the workbench's comparison card, the colour bar and the 3D's largest-value tag).
export const JUDGED_JA = {
  number: "判定の量", // before the judged quantity's name, over the card's large number
  ref: "参考 (判定に使わない)", // over the values shown beside it that the verdict does not use
  refTag: "参考", // before 最大 on the 3D's tag of a field the verdict does not use
  refBar: "参考の色", // before a colour bar's title when the field is not the judged quantity
  otherQuantity: "判定は別の量", // under such a colour bar, before the judged quantity's name
  // Shown wherever strength is judged (the present card, the workbench's analysis card, the grid's
  // strength tile): the suspension and wheel mounts are not judged. The records say it only in the
  // analyses' free-text assumptions (no field marks the excluded elements), so it is always shown
  // when the run has a strength load case.
  // 取付点の荷重条件が入ったら消す (remove once a load case judges the mounts).
  mountsNotJudged: "取付点 (金具・溶接・ボルト) は判定していない。取付の範囲は、金具が補強する仮定で判定から外している",
};

// The parts after the dot that a tool writes for one quantity, in words (a part not here is shown as written).
// rops_occupant_clearance.<part>: rops_frame_pushover pushes the roll bar from the left top corner (side),
// the right one (side_mirror), backwards near the left (rear) or the right (rear_mirror), and down (top).
const QUANTITY_PART_JA = {
  rops_occupant_clearance: { side: "横・左の角", side_mirror: "横・右の角", rear: "後ろ・左寄り", rear_mirror: "後ろ・右寄り", top: "上" },
};

// Load cases that share a test id (R8 x 3, T2 x 2, ...): a short name for each from its title, to
// tell them apart beside the id. The words their titles share at the start and at the end are
// left out, then the rest up to its first bracket or comma, at most SHORT_MAX letters.
// -> Map load_case_id -> short name (only for ids that repeat).
const SHORT_MAX = 9;
export function caseShortNames(cases) {
  const byTest = new Map();
  for (const c of cases ?? []) {
    if (!byTest.has(c.test_id)) byTest.set(c.test_id, []);
    byTest.get(c.test_id).push(c);
  }
  const out = new Map();
  for (const list of byTest.values()) {
    if (list.length < 2) continue;
    const titles = list.map((c) => [...String(c.title ?? c.load_case_id)]);
    let pre = 0;
    while (titles.every((t) => pre < t.length && t[pre] === titles[0][pre])) pre += 1;
    let suf = 0;
    while (titles.every((t) => suf < t.length - pre && t[t.length - 1 - suf] === titles[0][titles[0].length - 1 - suf])) suf += 1;
    list.forEach((c, i) => {
      let s = titles[i].slice(pre, titles[i].length - suf).join("").replace(/^[\s、・のをで]+/, "");
      s = s.split(/\s*[（(、,]/)[0].trim();
      const letters = [...s];
      s = letters.length > SHORT_MAX ? `${letters.slice(0, SHORT_MAX).join("")}…` : s;
      out.set(c.load_case_id, s || c.load_case_id);
    });
  }
  return out;
}

export function agentName(id) {
  if (!id) return "";
  if (ROLE_JA[id]) return ROLE_JA[id];
  if (id.startsWith("cae.")) return DOMAIN_JA[id.slice(4)] ?? id;
  if (id.startsWith("designer.")) return `${moduleName(id.slice(9))}の設計役`; // modules: designer.<module>
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

// project.driver: what drove the run (決まった手順 = scripted, no Claude; API キー = HIRAKU's agent loop
// with the user's ANTHROPIC_API_KEY; Claude Code = the user's own Claude Code with the HIRAKU plugin).
export const DRIVER_JA = { scripted: "決まった手順", agent_sdk: "Claude (API キー)", claude_code_plugin: "Claude Code" };

// How a run is driven, in A1's words for its start buttons: 決まった手順で再現 (scripted) or Claude で実行.
export const DRIVEN_JA = { scripted: "決まった手順で再現", agent_sdk: "Claude で実行", claude_code_plugin: "Claude で実行" };
export const SCRIPTED_CUT_JA = `${DRIVEN_JA.scripted} (Claude なし)`; // the video cuts' corner tag on a scripted run (cut.js)

// a run that ended at the domain picks (its stop_reason says so; stop_after "picks" in newer records)
const PICKS_ONLY = /分野を選ぶところまで|分野選びまで/;

// The run list's state column (for people): how the run ended in one word, from /api/runs' summary
// (finished, end_status of run.finished). A run that stops after the picks as asked is 完了.
export const RUN_STATE_JA = { done: "完了", stopped: "途中で止まった", failed: "エラーで止まった", going: "実行中" };
export function runState(r) {
  if (!r.finished) return "going";
  if (r.stop_after === "picks" || PICKS_ONLY.test(r.stop_reason || "")) return "done";
  return r.end_status === "stopped" || r.end_status === "failed" ? r.end_status : "done";
}

// The run list's row title for people (no ?dev=1): what replaying the record shows, from /api/runs'
// summary (hiraku/webserve/server.py _run_summary: driver, finished, end_status, stop_reason,
// stop_after, last_version, judged {green, red, pending}) — how it was driven, then how far the design went (last_version) and how it was judged.
// The state column says 完了 / 途中で止まった, so a stopped row here says only how far it got
// (and a red domain left over): 「決まった手順で最後まで・v5 で全部 OK」
// 「Claude の運転・v4 まで」 (the version as verLabel writes it).
const HOW_JA = { scripted: "決まった手順", agent_sdk: "Claude の運転", claude_code_plugin: "Claude の運転" };
export function runSummaryJa(r) {
  const how = HOW_JA[r.driver] ?? "";
  const v = Number.isInteger(r.last_version) ? r.last_version : null;
  if (r.stop_after === "picks" || PICKS_ONLY.test(r.stop_reason || "")) return [how, "分野を選ぶところまで"].filter(Boolean).join("・");
  if (!r.finished) return [how, v ? `いま ${verLabel(v)}` : ""].filter(Boolean).join("・");
  const state = runState(r);
  const j = r.judged;
  let far = "";
  if (state === "done") {
    const head = how ? `${how}で最後まで` : "";
    if (v && j && j.green > 0 && !j.red && !j.pending) far = `${verLabel(v)} で全部 OK`;
    else if (v && j) far = `${verLabel(v)} で` + [j.green ? `OK ${j.green}` : "", j.red ? `NG ${j.red}` : "", j.pending ? `判定の出ていない分野 ${j.pending}` : ""].filter(Boolean).join("・");
    else if (v) far = `${verLabel(v)} まで`;
    return [head, far].filter(Boolean).join("・");
  }
  if (v) far = `${verLabel(v)} まで` + (j?.red ? `・NG が残った` : "");
  return [how, far].filter(Boolean).join("・");
}

// A record the list for people keeps under the closed 「この PC の記録」: one this PC ran that has no
// project.driver (made before the field, while HIRAKU was being built), no design version, or that
// ended at the domain picks. Saved records always stay on the list.
export function isOlderRecord(r) {
  if (r.saved) return false;
  if (r.sample) return true;
  if (!r.driver) return true;
  if (r.stop_after === "picks" || PICKS_ONLY.test(r.stop_reason || "")) return true;
  return r.finished && !Number.isInteger(r.last_version);
}

// The run list for people (no ?dev=1): one line per demo run of data/catalog/demo_runs.yaml, by its id,
// saying what it lets you do. The yaml's title and shows are written for the developers; an id not here
// shows its title.
export const DEMO_LINE_JA = {
  "n09-electric-guitar": "ギターを作るなら、何を確かめる？ — Claude が作るものに合わせて解析を選びます",
  "n09-care-bed": "介護ベッドを作るなら、何を確かめる？ — Claude が作るものに合わせて解析を選びます",
  "n09-serving-robot": "料理を運ぶロボットを作るなら、何を確かめる？ — Claude が作るものに合わせて解析を選びます",
  "n11-n12": "要件の書き間違いを見つける — 手計算と解析の食い違いから、cm と mm の取り違えを見つけて直します",
  n13: "手元に無い計算を作る — 横転したときの計算のツールを Claude がその場で書き、確かめてから使います",
  n14: "メモを渡して決まりにする — 途中で渡した設計のメモが決まりになり、当てはまる部品について聞き返します",
  video: "小さなオフロード車を試験して直す — NG の所を直してもう一度確かめます。限界を超える試験も入っています",
  demo: "小さなオフロード車の 11 の試験 — NG の所を 4 回直して、v5 で全部 OK になります",
};
// The demo run recommended at the top of the run list when no saved run is there (its scripted run).
export const PICK_DEMO_ID = "video";

// A verdict's margin: margin_pct (% of the limit), or, when the record gives none (the
// limit is 0, so margin_pct is null), the margin in its own unit.
export function fmtMargin(verdict, sig = 3) {
  const p = verdict?.margin_pct;
  if (typeof p === "number" && Number.isFinite(p)) return `${fmt(p, sig)} %`;
  return verdict?.margin ? fmtQ(verdict.margin, sig) : "—";
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

// Escaped text with each aside in brackets of up to max letters (one bracket inside allowed:
// "(電池を下げた (形の見本))") made one piece (span.aside: wraps only within itself when longer
// than the line), so a line does not break inside a short aside.
export function keepAsides(s, max = 40) {
  const re = new RegExp(`\\(((?:[^()]|\\([^()]*\\)){1,${max}})\\)`, "g");
  return esc(s).replace(re, '<span class="aside">($1)</span>');
}

// Words from the record (a judgement's summary written by hiraku/tools/judge.py, what an agent
// or the scripted driver said, a proposal's reason) in the screen's words: OK / NG for 合格 /
// 不合格 and for 緑 / 赤 meaning a verdict, 判定 for 照合 and 合否, vN for 版 N. The record keeps
// its own words (beyond.js reads them as written); only what is shown changes. 赤 / 緑 as a
// colour (赤い, 赤色, 半透明の赤 ...) are kept.
const NOT_ASCII = "[^\\s\\x00-\\x7f]";
export function judgedWords(text) {
  return String(text ?? "")
    .replace(/不合格|合格/g, (m) => (m === "不合格" ? "NG" : "OK"))
    .replace(/照合|合否/g, "判定")
    .replace(/(?<!半透明の)([赤緑])(?![いく色字み])/g, (m, c) => (c === "赤" ? "NG" : "OK"))
    .replace(/版\s*(\d+)/g, "v$1")
    .replace(new RegExp(`(${NOT_ASCII})(OK|NG|v\\d)`, "g"), "$1 $2")
    .replace(new RegExp(`(OK|NG)(${NOT_ASCII})`, "g"), (m, w, c) => ("、。・)）:：".includes(c) ? m : `${w} ${c}`));
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
