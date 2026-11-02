"""Load cases and scripted steps for the tests the K2 vehicle tools answer.

BUILDERS[test_id](spec, req, design) -> load_case, the shape of the builders in
hiraku.catalog.build: every condition and criterion limit is a requirement
(Q key) or read off the design, or computed here; values from elsewhere are
constants below with their source. tolerance_rel is chosen from what the
hand calculation leaves out of the tool (the reason is next to each value);
a `tolerance_rel` in the catalog spec replaces it.

PLANS[test_id]: one step of the scripted run, the shape of the steps in
data/catalog/scripted.yaml without load_case: the tool, a sentence, and the
predictions (K0 formula id, output, inputs as refs). A `from.<name>` ref is
read from the prediction's inputs_from(design, load_case): the K2 model's
masses, heights, rates and areas for the load case's loading (no simulation).

S4b has no step yet: no K0 formula predicts the friction a parked vehicle
needs. S5 has no criterion: no K0 formula predicts the lowest uphill wheel
load on the rough road, only the mean (the static load on the slope); its
step expects the mismatch it had on the demo vehicle, with the cause found by
splitting the gap, and stops there until K0 has a side-slope formula.

Importing this module computes nothing; the K2 model loads on the first call.
"""

from __future__ import annotations

import math
from typing import Any, Callable

from hiraku.catalog.build import CatalogError, check_design_loading, loading_requirement, requirement
from hiraku.catalog.loading import CARGO, LOADING_JA, OCCUPANTS

Record = dict[str, Any]

# Values from outside the requirements file. Sources as cited; none of them was
# read again in this repository ("not checked in this repository").
# S4: initial speed of the braking table of 細目告示 第171条 第5項 (MLIT,
# https://www.mlit.go.jp/jidosha/content/S171.pdf), borrowed: the table is not
# for this vehicle class. Not checked in this repository.
S4_SPEED_KMH = 35.0
# S1': static stability factor T/2h >= 1.0 of ROHVA 1-2011 and ANSI B71.9-2012, as
# summarized in 79 FR 68964 (2014, CPSC's proposed ROV rule,
# https://www.govinfo.gov/content/pkg/FR-2014-11-19/html/2014-26500.htm). Not
# checked in this repository.
SSF_MIN = 1.0
# S5: design speed of a class 3 forest road, 林道規程 (林野庁,
# https://www.rinya.maff.go.jp/hokkaido/keikaku/other/attach/pdf/sinrinkeikaku_taikei-32.pdf).
# Not checked in this repository.
S5_SPEED_KMH = 20.0

# tolerance_rel per test: what the hand calculation leaves out of the tool.
TOLERANCE = {
    # tilt.spring_roll_angle has the body roll on linear springs about a roll axis on
    # the ground; K2 adds the tyres' deflection and the wheel carriers rolling with the body
    "S1": 0.10,
    # T/2h from the same design: only how the CG and the track are taken differ
    "S1'": 0.05,
    # crosswind.critical_wind_speed is the rigid moment balance; K2 adds the body roll on
    # its springs and the tyre deflection, which move the CG toward the downwind wheels
    "F1": 0.10,
    # the path's peak lateral acceleration assumes the vehicle on the centre line; K2's
    # driver model lags the path and the body's acceleration carries yaw and roll transients
    "S3": 0.15,
    # both are the friction-limit stop; K2 adds rolling resistance, the tyres' slip and
    # the pitch on the springs
    "S4": 0.05,
    # with every wheel braked the hold is statically indeterminate: K2 shares the force by
    # the tyres' stiffness, the hand calculation by equal use of the friction
    "S4b": 0.15,
    # the static load on the slope against the mean of K2's run over the rough road
    "S5": 0.10,
    # the same power balance; K2 adds tyre slip and the load moving between the axles
    "P2": 0.10,
    # the same force balance integrated over speed; K2 adds tyre slip, the load moving to
    # the rear axle and its own wheel inertia
    "P3": 0.10,
}


def _q(value: float, unit: str) -> Record:
    return {"value": float(value), "unit": unit}


def _text_requirement(req: Record, key: str) -> Record:
    for r in req["requirements"]:
        if r["key"] == key:
            if not isinstance(r.get("value"), str):
                raise CatalogError(f"requirement {key} needs a text value, got {r.get('value')!r}")
            return r
    raise CatalogError(f"requirement {key} is missing from the requirements file")


def _loading(req: Record, design: Record, fixed: str | None = None) -> tuple[str, list[str]]:
    """The loading (Q30, or the one the test fixes) and the requirement keys it uses."""
    loading = fixed or loading_requirement(req)
    check_design_loading(req, design, loading)
    keys = ([] if fixed else ["Q30"]) + (["Q33"] if OCCUPANTS[loading] else []) + (["Q40"] if CARGO[loading] else [])
    return loading, keys


def _case(spec: Record, test_id: str, domain: str, lc_id: str, title: str, params: Record, keys: list[str],
          criterion: Record | None) -> Record:
    out = {"load_case_id": lc_id, "test_id": test_id, "domain": spec.get("domain", domain), "title": title,
           "params": params, "requirement_keys": keys,
           "tolerance_rel": float(spec.get("tolerance_rel", TOLERANCE[test_id]))}
    if criterion is not None:
        out["criterion"] = criterion
    return out


def _crit(quantity: str, op: str, limit: Record, basis: str, keys: list[str]) -> Record:
    out = {"quantity": quantity, "op": op, "limit": limit, "basis": basis}
    if keys:
        out["requirement_keys"] = keys
    return out


def _cg_y(design: Record, loading: str) -> float:
    from hiraku.solvers.k2_vehicle.drive import on_board
    from hiraku.solvers.k2_vehicle.model import build_vehicle
    return float(build_vehicle(on_board(design, loading)).cg_total[1])


# ------------------------------------------------------------------ builders
def _s1_tilt(spec: Record, req: Record, design: Record) -> Record:
    loading, keys = _loading(req, design)
    lim = requirement(req, "Q60", "deg")["value"]
    return _case(spec, "S1", "handling", f"S1-tilt-{loading}", f"傾斜台で倒れない ({LOADING_JA[loading]})",
                 {"loading": loading, "direction": "both"}, keys + ["Q60"],
                 _crit("tilt_angle", ">=", _q(lim, "deg"), f"Q60 {lim:g} deg (for the loading of Q30)", ["Q60"]))


def _s1p_ssf(spec: Record, req: Record, design: Record) -> Record:
    loading, keys = _loading(req, design, fixed="empty")
    return _case(spec, "S1'", "handling", "S1'-ssf-empty", "静的安定係数 T/2h (空車)",
                 {"loading": loading, "direction": "right"}, keys,
                 _crit("static_stability_factor", ">=", _q(SSF_MIN, "1"),
                       "T/2h >= 1.0 (ROHVA 1-2011, ANSI B71.9-2012 as summarized in 79 FR 68964; not checked)", []))


def _f1_wind(spec: Record, req: Record, design: Record) -> Record:
    loading, keys = _loading(req, design)
    gust = requirement(req, "Q18", "m/s")["value"]
    speed = requirement(req, "Q50", "km/h")["value"]
    # the wind pushes the CG further over when it is off the centre line on the downwind side
    wind_from = "left" if _cg_y(design, loading) <= 0.0 else "right"
    return _case(spec, "F1", "aero", f"F1-wind{gust:g}", f"瞬間 {gust:g} m/s の横風で風上の輪が浮かない ({LOADING_JA[loading]})",
                 {"loading": loading, "wind_speed": _q(gust, "m/s"), "speed": _q(speed, "km/h"), "wind_from": wind_from},
                 keys + ["Q18", "Q50"],
                 _crit("critical_wind_speed", ">=", _q(gust, "m/s"), f"Q18 gust {gust:g} m/s", ["Q18"]))


def _s3_dlc(spec: Record, req: Record, design: Record) -> Record:
    from hiraku.solvers.k2_vehicle.shelf import DLC_OFFSET  # the ISO 3888-1 layout, as cited there
    loading, keys = _loading(req, design)
    speed = requirement(req, "Q50", "km/h")["value"]
    return _case(spec, "S3", "handling", f"S3-dlc{speed:g}",
                 f"ダブルレーンチェンジを {speed:g} km/h で、片側の 2 輪が浮かない ({LOADING_JA[loading]}、ISO の速さではない)",
                 {"loading": loading, "speed": _q(speed, "km/h"), "offset": _q(DLC_OFFSET, "m"), "find_limit": False},
                 keys + ["Q50"],
                 _crit("min_side_load", ">", _q(0.0, "N"),
                       "both wheels of one side lift when their loads together reach 0", []))


def _s4_brake(spec: Record, req: Record, design: Record) -> Record:
    loading, keys = _loading(req, design)
    mu = requirement(req, "Q11", "1")["value"]
    stop = requirement(req, "Q52", "m")["value"]
    return _case(spec, "S4", "handling", f"S4-brake{S4_SPEED_KMH:g}",
                 f"{S4_SPEED_KMH:g} km/h から急制動、μ {mu:g} で {stop:g} m 以内に止まる ({LOADING_JA[loading]})",
                 {"loading": loading, "speed": _q(S4_SPEED_KMH, "km/h"), "mu": _q(mu, "1"),
                  "reaction_time": _q(0.0, "s")}, keys + ["Q11", "Q52"],
                 _crit("stopping_distance", "<=", _q(stop, "m"),
                       f"Q52 {stop:g} m from {S4_SPEED_KMH:g} km/h (細目告示 第171条 第5項, borrowed)", ["Q52"]))


def _s4b_park(spec: Record, req: Record, design: Record) -> Record:
    loading, keys = _loading(req, design, fixed="empty")
    mu = requirement(req, "Q11", "1")["value"]
    grade = requirement(req, "Q53", "%")["value"]
    return _case(spec, "S4b", "handling", f"S4b-park{grade:g}", f"{grade:g} % の坂にブレーキで止まっていられる (空車)",
                 {"loading": loading, "parking_slope": _q(grade, "%"), "mu": _q(mu, "1")}, keys + ["Q11", "Q53"],
                 _crit("parking_friction_demand", "<=", _q(mu, "1"),
                       f"the braked wheels need at most the road friction Q11 {mu:g}", ["Q11"]))


def _s5_slope(spec: Record, req: Record, design: Record) -> Record:
    loading, keys = _loading(req, design)
    cls = _text_requirement(req, "Q12")["value"]
    beta = requirement(req, "Q14", "deg")["value"]
    # the uphill side: the one the CG is off toward is the less stable one downhill
    slope_up = "left" if _cg_y(design, loading) <= 0.0 else "right"
    return _case(spec, "S5", "handling", f"S5-slope{beta:g}",
                 f"ISO 8608 クラス {cls} の路面、横 {beta:g}° の斜面を {S5_SPEED_KMH:g} km/h で ({LOADING_JA[loading]})",
                 {"loading": loading, "road_class": cls, "side_slope": _q(beta, "deg"), "slope_up": slope_up,
                  "speed": _q(S5_SPEED_KMH, "km/h")}, keys + ["Q12", "Q14"], None)


def _p2_grade(spec: Record, req: Record, design: Record) -> Record:
    loading, keys = _loading(req, design)
    grade = requirement(req, "Q13", "%")["value"]
    return _case(spec, "P2", "powertrain", f"P2-grade{grade:g}", f"{grade:g} % の坂を発進して登る ({LOADING_JA[loading]})",
                 {"loading": loading, "grade": _q(grade, "%"), "until": "top_speed", "max_time": _q(120.0, "s")},
                 keys + ["Q13"],
                 _crit("top_speed", ">", _q(0.0, "km/h"), f"climbs Q13 {grade:g} %: a speed above 0 from a standstill",
                       ["Q13"]))


def _p3_accel(spec: Record, req: Record, design: Record) -> Record:
    loading, keys = _loading(req, design)
    target = requirement(req, "Q50", "km/h")["value"]
    limit = requirement(req, "Q51", "s")["value"]
    return _case(spec, "P3", "powertrain", f"P3-accel{target:g}",
                 f"止まった所から {target:g} km/h まで {limit:g} s 以内 ({LOADING_JA[loading]})",
                 {"loading": loading, "target_speed": _q(target, "km/h"), "grade": _q(0.0, "%")}, keys + ["Q50", "Q51"],
                 _crit("time_to_speed", "<=", _q(limit, "s"), f"Q51 {limit:g} s to Q50 {target:g} km/h", ["Q51"]))


BUILDERS: dict[str, Callable[[Record, Record, Record], Record]] = {
    "S1": _s1_tilt, "S1'": _s1p_ssf, "F1": _f1_wind, "S3": _s3_dlc, "S4": _s4_brake, "S4b": _s4b_park,
    "S5": _s5_slope, "P2": _p2_grade, "P3": _p3_accel,
}


# ------------------------------------------------------------------ inputs read off the design
def _vehicle(design: Record, load_case: Record):
    from hiraku.solvers.k2_vehicle.drive import on_board
    from hiraku.solvers.k2_vehicle.model import build_vehicle
    veh = build_vehicle(on_board(design, load_case.get("params", {}).get("loading")))
    ground = min(float(c.pos[2]) - c.radius for c in veh.corners)
    return veh, ground


def tilt_inputs(design: Record, load_case: Record) -> Record:
    """h, e of the whole vehicle; m_s, h_s (above the ground: the roll axis of vertical
    supports) of the sprung part; k, the mean wheel rate; n, the number of axles."""
    veh, ground = _vehicle(design, load_case)
    sprung = [c for c in veh.corners if c.dof]
    return {"h": _q((float(veh.cg_total[2]) - ground) * 1000.0, "mm"), "e": _q(abs(float(veh.cg_total[1])) * 1000.0, "mm"),
            "m_s": _q(veh.m_s, "kg"), "h_s": _q((float(veh.cg_s[2]) - ground) * 1000.0, "mm"),
            "k": _q(sum(c.k_w for c in sprung) / max(len(sprung), 1) / 1000.0, "N/mm"),
            "n": _q(len({round(float(c.pos[0]), 6) for c in veh.corners}), "1")}


def wind_inputs(design: Record, load_case: Record) -> Record:
    """C_y, A, h_cp and rho as the crosswind tool takes them (load case, else the design
    variables, else the side projection of the part boxes)."""
    from hiraku.solvers.k2_vehicle.shelf import CrosswindTool
    veh, ground = _vehicle(design, load_case)
    tool = CrosswindTool()
    tool._vars = design.get("variables") or {}
    lp = load_case.get("params", {})
    aero = tool.aero(veh, lp)
    return {"C_y": _q(aero.cy, "1"), "A": _q(aero.side_area, "m^2"), "rho": _q(aero.rho, "kg/m^3"),
            "h_cp": _q((aero.side_cp[2] + float(veh.cg_s[2]) - ground) * 1000.0, "mm"),
            "h": _q((float(veh.cg_total[2]) - ground) * 1000.0, "mm")}


def dlc_inputs(design: Record, load_case: Record) -> Record:
    """X, the shorter lane change of the K2 course; h, the CG height."""
    from hiraku.solvers.k2_vehicle.shelf import DLC_SECTIONS
    veh, ground = _vehicle(design, load_case)
    return {"X": _q(min(DLC_SECTIONS[1], DLC_SECTIONS[3]) * 1000.0, "mm"),
            "h": _q((float(veh.cg_total[2]) - ground) * 1000.0, "mm")}


def slope_inputs(design: Record, load_case: Record) -> Record:
    """a_y = tan(side_slope) in g (the slope taken as a lateral acceleration); h."""
    from hiraku.solvers.k2_vehicle.units import si
    veh, ground = _vehicle(design, load_case)
    beta = si(load_case["params"]["side_slope"], "rad")
    return {"a_y": _q(math.tan(beta), "g"), "h": _q((float(veh.cg_total[2]) - ground) * 1000.0, "mm")}


def launch_inputs(design: Record, load_case: Record) -> Record:
    """The launch tool's motor and road load: F_max = T i eta / r of the driven wheels,
    P, eta, C_rr, C_d, A (front), rho, and lambda = 1 + sum(J / r^2) / m."""
    from hiraku.solvers.k2_vehicle.shelf import LaunchTool
    veh, _ = _vehicle(design, load_case)
    tool = LaunchTool()
    tool._vars = design.get("variables") or {}
    lp = load_case.get("params", {})
    motor = tool.motor(lp)
    aero = tool.aero(veh, lp)
    driven = [c for c in veh.corners if c.drive]
    r = sum(c.radius for c in driven) / len(driven)
    lam = 1.0 + sum(c.j_w / c.radius ** 2 for c in veh.corners) / veh.m_total
    return {"F_max": _q(motor.torque * motor.gear * motor.efficiency / r, "N"), "P": _q(motor.power / 1000.0, "kW"),
            "eta": _q(motor.efficiency, "1"), "C_rr": _q(veh.corners[0].c_rr, "1"), "C_d": _q(aero.cd, "1"),
            "A": _q(aero.front_area, "m^2"), "rho": _q(aero.rho, "kg/m^3"), "lambda": _q(lam, "1")}


# ------------------------------------------------------------------ scripted steps
_LAUNCH_REFS = {"m": "load.mass", "F_max": "from.F_max", "P": "from.P", "eta": "from.eta", "C_rr": "from.C_rr",
                "C_d": "from.C_d", "A": "from.A", "grade": "lc.grade", "rho": "from.rho", "lambda": "from.lambda"}

PLANS: dict[str, Record] = {
    "S1": {"tool": "tilt", "say": "傾斜台で倒れる角度を、ばねで車体がロールする分を入れた式で先に見積もります",
           "predictions": [{
               "quantity": "tilt_angle", "formula_id": "tilt.spring_roll_angle", "output": "theta",
               "assumptions": ["車体はばねの上でロールし、ロールの軸は地面にある", "タイヤはたわまない"],
               "inputs": {"t": "summary.track_front", "h": "from.h", "e": "from.e", "m": "load.mass", "m_s": "from.m_s",
                          "h_s": "from.h_s", "k": "from.k", "n": "from.n"},
               "inputs_from": tilt_inputs}]},
    "S1'": {"tool": "tilt", "say": "空車の静的安定係数 T/2h を、輪距と重心の高さから先に出します",
            "predictions": [{
                "quantity": "static_stability_factor", "formula_id": "tilt.rigid_angle", "output": "ssf",
                "assumptions": ["剛体として、低い側の接地点を結ぶ線のまわりに倒れる"],
                "inputs": {"t": "summary.track_front", "h": "from.h", "e": "from.e"},
                "inputs_from": tilt_inputs}]},
    "F1": {"tool": "crosswind", "say": "風上の輪が浮く風の速さを、風下の接地点のまわりのモーメントの釣り合いで先に見積もります",
           "predictions": [{
               "quantity": "critical_wind_speed", "formula_id": "crosswind.critical_wind_speed", "output": "v_crit",
               "assumptions": ["車体は剛体、止まっている", "横の力は風圧の中心にかかる"],
               "inputs": {"m": "load.mass", "t": "summary.track_front", "C_y": "from.C_y", "A": "from.A",
                          "h_cp": "from.h_cp", "rho": "from.rho"},
               "inputs_from": wind_inputs}]},
    "S3": {"tool": "lane_change", "say": "コースの線から横の加速度の山を出し、そのときの内側の輪の荷重を先に見積もります",
           "predictions": [
               {"quantity": "max_lateral_accel_g", "formula_id": "lane_change.peak_lateral_acceleration",
                "output": "a_y_peak", "assumptions": ["車はコースの中心の線の上を走る (余弦の形の車線変更)"],
                "inputs": {"v": "lc.speed", "Y": "lc.offset", "X": "from.X"}, "inputs_from": dlc_inputs},
               {"quantity": "min_side_load", "formula_id": "lane_change.inner_wheel_load", "output": "W_inner",
                "assumptions": ["横の加速度の山で、定常の荷重の移り"],
                "inputs": {"m": "load.mass", "a_y": "pred.max_lateral_accel_g", "h": "from.h", "t": "summary.track_front"},
                "inputs_from": dlc_inputs}]},
    "S4": {"tool": "braking", "say": "摩擦の限りで止まるとみて、止まるまでの距離を先に見積もります",
           "predictions": [{
               "quantity": "stopping_distance", "formula_id": "braking.stopping_distance", "output": "s",
               "assumptions": ["全輪が摩擦の限りで減速する", "空走の時間は荷重条件の reaction_time"],
               "inputs": {"v": "lc.speed", "mu": "lc.mu", "t_r": "lc.reaction_time"}}]},
    "S5": {"tool": "offroad_roll", "say": "横の斜面に止まっているときの、上の側の輪の荷重を先に見積もります",
           "predictions": [{
               "quantity": "mean_uphill_load", "formula_id": "lane_change.inner_wheel_load", "output": "W_inner",
               "assumptions": ["斜面を横の加速度 tan β とみる (法線の荷重は 1/cos β だけ大きく出る)",
                               "凸凹の路面の上下の揺れは平均では消える"],
               "inputs": {"m": "load.mass", "a_y": "from.a_y", "h": "from.h", "t": "summary.track_front"},
               "inputs_from": slope_inputs}],
           # Run on the demo vehicle (two_cargo, 15 deg): K0 2061.6 N, K2 1835.7 N (-11.0 %). Split by K2
           # runs on the smooth slope: the exact rigid balance W (cos b (t/2 - e) - sin b h) / t gives
           # 1970.7 N and K2 with rigid supports 1968.9 N; K2 on its springs 1823.4 N; the rough road
           # moves the mean by +0.7 % only.
           "expected_mismatch": {
               "quantity": "mean_uphill_load",
               "source": "the K2 department's split of the gap on the demo vehicle (rigid and sprung K2 runs on "
                         "the smooth slope against the exact moment balance), 2026-11-03",
               "cause": "式は斜面を横の加速度 tan β とみて、重さ全部を法線の荷重にしている (15° で 3.5 % 大きい)。"
                        "重心が下の側へ 4 mm ずれている分 (1 %) も入っていない。さらに、車体がばねの上で下の側へ"
                        "ロールして重心が下の側へ動く分 (7.4 %) が入っていない。路面の凸凹は平均を 0.7 % しか変えない",
               "fix": "cos β と重心の横のずれと、ばねのロールを入れた斜面の式で予測し直す (解析部に頼んでいる)",
               "stop": "斜面の式がまだ K0 にないので、ここで止めます"}},
    "P2": {"tool": "launch", "say": "坂の上で、出力と走る抵抗が釣り合う速さを先に見積もります",
           "predictions": [{
               "quantity": "top_speed", "formula_id": "launch.top_speed", "output": "v_top",
               "assumptions": ["出力で速さが決まる (モーターの回転の上限は入れない)", "坂の勾配は一定"],
               "inputs": {k: v for k, v in _LAUNCH_REFS.items() if k not in ("F_max", "lambda")},
               "inputs_from": launch_inputs}]},
    "P3": {"tool": "launch", "say": "トルクと出力の線で、目標の速さまでの時間を先に見積もります",
           "predictions": [{
               "quantity": "time_to_speed", "formula_id": "launch.time_to_speed", "output": "t",
               "assumptions": ["発進から全力、輪は滑らない", "トルクで決まり、その先は出力で決まる"],
               "inputs": dict(_LAUNCH_REFS, v="lc.target_speed"), "inputs_from": launch_inputs}]},
}
