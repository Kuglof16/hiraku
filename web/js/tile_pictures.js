// ④ 解析結果: what each domain's tile picture shows, so the eight pictures differ at a glance.
// Pure functions of the record (no 3D here; tile3d.js draws what pictureOf returns). Each
// domain looks for its own material in its analyses of the version shown, and says which
// material it used and which it did not find (the tile's くわしく, the tests):
//
//   strength    the stress colours (a field: stress, else any coloured field)
//   crash       the deformed frame at its last frame (field node_motion), else the wall
//               (the analysis' scene, or the crash load case's wall) with the stress colours at
//               the moment of the largest value
//   nvh         the judged mode's shape, swung out and held (field mode_shape: the one whose
//               frequency is a verdict's value, else the first)
//   thermal     the parts in their temperature's colour (energy_flow.js: temp.<part> series,
//               the heat colour, red only above the limit) at the hottest recorded moment
//   handling    the test's scene (tilt table, road, course) and the car at the moment it is
//               most tilted (series roll / table_angle)
//   powertrain  the step or slope (scene) and the car at the moment it is most pitched on it
//               (series pitch); without series, the static step and its stress colours
//   aero        the wind's arrows (series wind_speed, scene.js) and the tyres' recorded forces
//               (series reaction_* / grip_*) at the moment the car is most rolled in the wind
//   assembly    the design as built (no colours)
//
// A domain whose material is not in the record falls back to the stress colours if it has any
// field, else the vehicle alone; `missing` names what was looked for and not found.

import { atFrame, fieldsOf, frameIndex } from "./fields.js";
import { judgedContour } from "./judged.js";
import { channelsOf, motionOf } from "./series.js";
import { nodeMotionAt, nodeMotionOf } from "./node_motion.js";
import { HEAT_PLAY_S, heatInputs } from "./energy_flow.js";

// what each domain's picture is made of, in words (the tile's くわしく)
export const MATERIAL_JA = {
  stress: "応力の色",
  node_motion: "骨組みの変形 (最後のコマ)",
  wall: "壁",
  mode_shape: "判定のモードの形",
  temperature: "部品の温度の色",
  scene: "試験の場面",
  pose: "車の姿勢 (いちばん傾いた瞬間)",
  pitch: "段に乗った瞬間の姿勢",
  wind: "横風の矢印",
  forces: "記録の力の矢印",
  built: "組み上がり",
};

// what each domain looks for, best first (missing: the ones not found)
const WANT = {
  strength: ["stress"],
  crash: ["node_motion", "wall", "stress"],
  nvh: ["mode_shape"],
  thermal: ["temperature"],
  handling: ["scene", "pose"],
  powertrain: ["scene", "pitch"],
  aero: ["wind", "forces"],
  assembly: ["built"],
};

const isMotionChannel = (n) => ["body_x", "body_y", "body_z", "roll", "pitch", "yaw"].includes(n) || n.startsWith("wheel_center_");

// The material an analysis carries (a set of MATERIAL_JA keys), from its record only.
export function materialOf(s, ana, loadCase) {
  const out = new Set();
  if (!ana || ana.status !== "ok") return out;
  const kinds = new Set((ana.fields ?? []).map((f) => f.kind));
  const ch = channelsOf(ana);
  if (kinds.has("stress")) out.add("stress");
  if (nodeMotionOf(ana)) out.add("node_motion");
  if (kinds.has("mode_shape")) out.add("mode_shape");
  if ([...ch.keys()].some((n) => n.startsWith("temp.")) || kinds.has("temperature")) out.add("temperature");
  const scene = Array.isArray(ana.scene) ? ana.scene : [];
  if (scene.length) out.add("scene");
  if (scene.some((o) => o.kind === "wall") || (ana.tool === "crash" || loadCase?.domain === "crash")) out.add("wall");
  const moving = [...ch.keys()].some(isMotionChannel);
  if (moving && (ch.has("roll") || ch.has("table_angle"))) out.add("pose");
  if (moving && ch.has("pitch")) out.add("pitch");
  if (moving && ch.has("wind_speed")) out.add("wind");
  if ((ana.vectors ?? []).length || [...ch.keys()].some((n) => /^(reaction|grip)_[xyz]\./.test(n))) out.add("forces");
  out.add("built");
  return out;
}

// The analysis a domain's picture is drawn from: among the domain's analyses (status ok) of the
// version, the one with the most of the domain's material (the earlier in WANT the more it
// counts); among equals the latest. null when the version has none.
export function pictureAnalysis(s, domain, version) {
  const cases = (s.project?.load_cases ?? []).filter((c) => c.domain === domain);
  const byId = new Map(cases.map((c) => [c.load_case_id, c]));
  const want = WANT[domain] ?? ["built"];
  let best = null;
  for (const id of s.order?.analyses ?? []) {
    const a = s.analyses[id];
    if (!a || a.status !== "ok" || a.design_version !== version || !byId.has(a.load_case_id)) continue;
    const lc = byId.get(a.load_case_id);
    const have = materialOf(s, a, lc);
    // weights 2^(n-1-i): the first wanted outweighs all the others together
    const score = want.reduce((sum, m, i) => sum + (have.has(m) ? 2 ** (want.length - 1 - i) : 0), 0) + (fieldsOf(a, { mesh: false }).length ? 0.5 : 0);
    if (!best || score >= best.score) best = { ana: a, loadCase: lc, have, score };
  }
  return best;
}

// The index of the sample of channel name where f(value) is largest (null: no such channel).
function argmaxT(ch, name, f = Math.abs) {
  const s = ch.get(name);
  if (!s) return null;
  let k = 0;
  for (let i = 1; i < s.values.length; i += 1) if (f(s.values[i]) > f(s.values[k])) k = i;
  return s.t_s[k];
}

// The moment of the motion drawn (the series' own seconds) for the domain.
export function momentOf(domain, ana, motion, ct) {
  const ch = motion.channels;
  if (ct?.frames && ct.frames.source === ana.analysis_id) return ct.frames.t_s[frameIndex(ct, null)];
  let t = null;
  if (domain === "powertrain") t = argmaxT(ch, "pitch") ?? argmaxT(ch, "body_z", (v) => v);
  else if (domain === "aero") t = argmaxT(ch, "roll") ?? argmaxT(ch, "wind_speed");
  else if (ch.has("table_angle")) {
    // a tilt table: the most tilted moment the vehicle still stands (a wheel lifting is the limit)
    t = motion.liftT ?? argmaxT(ch, "table_angle");
  } else t = argmaxT(ch, "roll");
  return t ?? motion.liftT ?? motion.t1;
}

// The mode shape a picture shows: the field whose frequency is a verdict's value (Hz) for the
// analysis' load case and version, else the first mode_shape field.
function judgedMode(s, ana) {
  const modes = (ana.fields ?? []).filter((f) => f.kind === "mode_shape");
  if (!modes.length) return null;
  const hz = Object.values(s.verdicts ?? {})
    .filter((v) => v.load_case_id === ana.load_case_id && (v.value?.unit ?? v.unit) === "Hz")
    .map((v) => (typeof v.value === "object" ? v.value.value : v.value))
    .filter(Number.isFinite);
  const near = (f) => Math.min(...hz.map((x) => Math.abs((f.frequency_Hz ?? NaN) - x)));
  const hit = hz.length ? modes.filter((f) => Number.isFinite(f.frequency_Hz)).sort((a, b) => near(a) - near(b))[0] : null;
  return hit && near(hit) <= 1e-6 * Math.max(1, Math.abs(hit.frequency_Hz)) ? hit : modes[0];
}

// The hottest recorded moment of a thermal analysis' temp.<part> series (its own seconds), as a
// replay time for EnergyFlow.show (energy_flow.js recordTime run backwards). null: no series.
function hottestPlayhead(inputs) {
  const temps = [...channelsOf(inputs.thermal)].filter(([n, x]) => n.startsWith("temp.") && x.unit === "degC").map(([, x]) => x);
  if (!temps.length) return null;
  const all = [...channelsOf(inputs.thermal).values()];
  const t0 = Math.min(...all.map((x) => x.t_s[0]));
  const t1 = Math.max(...all.map((x) => x.t_s.at(-1)));
  let best = { v: -Infinity, t: t1 };
  for (const x of temps) x.values.forEach((v, i) => v > best.v && (best = { v, t: x.t_s[i] }));
  const rate = Math.max(1, (t1 - t0) / HEAT_PLAY_S);
  return inputs.arrived + (best.t - t0) / rate;
}

// The picture of a domain's tile for a version: { domain, design, ana, loadCase, ct, motion,
// tau, vectors, nm, nmAt, heat, heatAt, modePhase, used, missing, key }. design: the version's.
export function pictureOf(s, domain, version, design) {
  const want = WANT[domain] ?? ["built"];
  const pick = design ? pictureAnalysis(s, domain, version) : null;
  const base = { domain, design, ana: null, loadCase: null, ct: null, motion: null, tau: null, vectors: null, nm: null, nmAt: null, heat: null, heatAt: null, modePhase: 0 };
  if (!pick) {
    const lc = (s.project?.load_cases ?? []).find((c) => c.domain === domain) ?? null;
    const used = domain === "assembly" ? ["built"] : [];
    return { ...base, loadCase: lc, used, missing: want.filter((m) => !used.includes(m)), key: `${version}|${domain}|-` };
  }
  const { ana, loadCase, have } = pick;
  const used = [];
  const lcId = loadCase?.load_case_id ?? ana.load_case_id;
  const contour = (field) => {
    const whole = field ? judgedContour(s, ana, field, lcId) : null;
    return whole?.frames ? atFrame(whole, frameIndex(whole, null)) : whole;
  };
  const out = { ...base, ana, loadCase };
  const firstField = fieldsOf(ana, { mesh: false })[0] ?? null;
  const stressField = fieldsOf(ana, { mesh: false }).find((f) => f.kind === "stress") ?? null;

  if (domain === "assembly") used.push("built");
  else if (domain === "nvh" && have.has("mode_shape")) {
    out.ct = contour(judgedMode(s, ana));
    out.modePhase = 0.25; // workbench3d.js animate: sin(2π 0.25) = 1, the mode swung fully out
    used.push("mode_shape");
  } else if (domain === "thermal" && have.has("temperature")) {
    const inputs = heatInputs(s, ana, design);
    const at = inputs ? hottestPlayhead(inputs) : null;
    if (inputs && at !== null) {
      out.heat = inputs;
      out.heatAt = at;
    } else out.ct = contour(fieldsOf(ana, { mesh: false }).find((f) => f.kind === "temperature") ?? null);
    used.push("temperature");
  } else if (domain === "crash" && have.has("node_motion")) {
    out.nm = nodeMotionOf(ana);
    out.nmAt = nodeMotionAt(out.nm, out.nm.t1);
    used.push("node_motion");
  } else {
    // the colours: the domain's own field kind first (stress), else any
    // (handling and aero show their pose and forces: no colours over them)
    out.ct = contour(stressField ?? (["handling", "aero"].includes(domain) ? null : firstField));
    if (out.ct && stressField) used.push("stress");
  }
  if (domain === "crash" && !used.includes("node_motion") && have.has("wall")) used.push("wall");

  // the motion at its moment (handling, powertrain, aero; any domain whose analysis moves)
  if (!out.nm && ["handling", "powertrain", "aero", "crash", "strength"].includes(domain)) {
    const m = motionOf(ana);
    if (m) {
      out.motion = m;
      out.tau = momentOf(domain, ana, m, out.ct);
      if (domain === "handling" && have.has("pose")) used.push("pose");
      if (domain === "powertrain" && have.has("pitch")) used.push("pitch");
      if (domain === "aero" && have.has("wind")) used.push("wind");
      if (domain === "aero" && have.has("forces")) used.push("forces");
    }
  }
  if (["handling", "powertrain"].includes(domain) && have.has("scene")) used.unshift("scene");
  // a scene without series (a static step): its recorded forces as arrows
  if (!out.motion && ["powertrain", "aero"].includes(domain) && (ana.vectors ?? []).length) {
    out.vectors = ana.vectors;
    if (domain === "aero") used.push("forces");
  }
  const missing = want.filter((m) => !used.includes(m));
  out.used = [...new Set(used)];
  out.missing = missing;
  out.key = `${version}|${domain}|${ana.analysis_id}|${out.ct?.key ?? "-"}|${out.tau ?? "-"}|${out.heatAt ?? "-"}|${out.nmAt?.tau ?? "-"}`;
  return out;
}
