"""Load cases and scripted steps for the tests the K2 vehicle tools answer.

BUILDERS[test_id](spec, req, design) -> load_case, the shape of the builders in
this package: every condition and criterion limit is a requirement (Q key) or
read off the design, or computed here; values from elsewhere are constants
below with their source. tolerance_rel is chosen from what the hand
calculation leaves out of the tool (the reason is next to each value); a
`tolerance_rel` in the catalog spec replaces it. A missing requirement raises
BuildError naming its key.

PLANS[test_id]: one step of the scripted run, the shape of the steps in
data/catalog/scripted.yaml without load_case: the tool, a sentence, and the
predictions (K0 formula id, output, inputs as refs). A `from.<name>` ref is
read from the prediction's inputs_from(design, load_case): the K2 model's
masses, heights, rates and areas for the load case's loading (no simulation).

S5 has no criterion: no K0 formula predicts the lowest uphill wheel load on
the rough road, only the mean (the static load on the slope). Its step first
predicts with the slope taken as a lateral acceleration, expects the mismatch
that gave on the demo vehicle (the cause found by splitting the gap), and
predicts again with the side-slope balance.

Past the limit: a catalog spec with "variant": "beyond" makes BUILDERS build
the case past the design's own limit (BEYOND: F1, S4, S5; load_case_id ending
-beyond, title starting 限界の外), whose verdict is expected to fail;
PLANS_BEYOND holds the steps that differ (S5). S3 has none:
on the demo vehicle the tyres slide before a wheel lifts (no lift up to
80 km/h at friction 0.8), so a lift past the limit is not there to show.

Importing this module computes nothing; the K2 model loads on the first call.
"""

from __future__ import annotations

import math
from typing import Any, Callable

from hiraku.catalog.build import CatalogError, check_design_loading
from hiraku.catalog.builders.common import BuildError, entry, loading, number, q, text, tolerance
from hiraku.contract.loading import CARGO, LOADING_JA

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

# Past the limit (the cases that should end in a fail, for showing it): a catalog spec with
# variant "beyond" builds the case past the design's own limit, worked out by K0 here.
# 10 % past: far enough that the outcome is plain, near enough to be a case the vehicle
# could meet (HIRAKU's choice, an assumption).
BEYOND_FACTOR = 1.1
# S4: slippery gravel, friction 0.3 (an assumption; no source read in this repository).
BEYOND_MU = 0.3
# S5: the slope is turned slowly so that the wheels lift nearly as on a static slope
# (HIRAKU's choice, an assumption).
BEYOND_RAMP_S = 12.0
# S5: the tyres are held (friction 2, as the tilt table does) so that the case shows the
# uphill wheels lifting, not the vehicle sliding down first: on the demo vehicle the two
# limits are within a degree at friction 0.8 (HIRAKU's choice, an assumption).
BEYOND_HOLD_MU = 2.0

# Load-case params that only the predictions read (inputs_from), not the tool: the tool's
# record names them in its effects as params it does not read.
PREDICTION_ONLY_PARAMS = ("cargo_cg_above_deck",)  # S1: the requirement's cargo height (Q41)

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


def _loading(req: Record, design: Record, fixed: str | None = None) -> tuple[str, list[str]]:
    """The loading (Q30, or the one the test fixes) and the requirement keys it uses."""
    if fixed is None:
        return loading(req, design)
    try:
        check_design_loading(req, design, fixed)
    except CatalogError as e:
        raise BuildError(str(e)) from None
    return fixed, []


def _case(spec: Record, test_id: str, domain: str, lc_id: str, title: str, params: Record, keys: list[str],
          criterion: Record | None) -> Record:
    out = {"load_case_id": lc_id, "test_id": test_id, "domain": spec.get("domain", domain), "title": title,
           "params": params, "requirement_keys": keys, "tolerance_rel": tolerance(spec, TOLERANCE[test_id])}
    if criterion is not None:
        out["criterion"] = criterion
    return out


def _crit(quantity: str, op: str, limit: Record, basis: str, keys: list[str]) -> Record:
    out = {"quantity": quantity, "op": op, "limit": limit, "basis": basis}
    if keys:
        out["requirement_keys"] = keys
    return out


def _k0(formula_id: str, output: str, inputs: Record) -> float:
    from hiraku import solvers
    return solvers.formula_registry().evaluate(formula_id, inputs)["outputs"][output]["value"]


def _summary(design: Record, on: str) -> tuple[Record, Record]:
    """The design summary and the loading's part of it (mass, CG)."""
    from hiraku.catalog.summary import design_summary
    s = design_summary(design)
    return s, s["loadings"][on]


def _cg_y(design: Record, on: str) -> float:
    from hiraku.solvers.k2_vehicle.drive import on_board
    from hiraku.solvers.k2_vehicle.model import build_vehicle
    return float(build_vehicle(on_board(design, on)).cg_total[1])


# ------------------------------------------------------------------ builders
def _s1_tilt(spec: Record, req: Record, design: Record) -> Record:
    on, keys = _loading(req, design)
    lim = number(req, "Q60", "deg")
    params = {"loading": on, "direction": "both"}
    keys = keys + ["Q60"]
    if entry(req, "Q41") is not None and CARGO[on]:
        # the cargo's CG height above its floor as the requirement has it: the prediction places
        # the cargo there; the tool solves the cargo where the design puts it (and does not read it)
        params["cargo_cg_above_deck"] = q(number(req, "Q41", "mm"), "mm")
        keys.append("Q41")
    return _case(spec, "S1", "handling", f"S1-tilt-{on}", f"傾斜台で倒れない ({LOADING_JA[on]})", params, keys,
                 _crit("tilt_angle", ">=", q(lim, "deg"), f"Q60 {lim:g} deg (for the loading of Q30)", ["Q60"]))


def _s1p_ssf(spec: Record, req: Record, design: Record) -> Record:
    on, keys = _loading(req, design, fixed="empty")
    return _case(spec, "S1'", "handling", "S1'-ssf-empty", "静的安定係数 T/2h (空車)",
                 {"loading": on, "direction": "right"}, keys,
                 _crit("static_stability_factor", ">=", q(SSF_MIN, "1"),
                       "T/2h >= 1.0 (ROHVA 1-2011, ANSI B71.9-2012 as summarized in 79 FR 68964; not checked)", []))


def _f1_wind(spec: Record, req: Record, design: Record) -> Record:
    on, keys = _loading(req, design)
    gust = number(req, "Q18", "m/s")
    speed = number(req, "Q50", "km/h")
    # the wind pushes the CG further over when it is off the centre line on the downwind side
    wind_from = "left" if _cg_y(design, on) <= 0.0 else "right"
    return _case(spec, "F1", "aero", f"F1-wind{gust:g}", f"瞬間 {gust:g} m/s の横風で風上の輪が浮かない ({LOADING_JA[on]})",
                 {"loading": on, "wind_speed": q(gust, "m/s"), "speed": q(speed, "km/h"), "wind_from": wind_from},
                 keys + ["Q18", "Q50"],
                 _crit("critical_wind_speed", ">=", q(gust, "m/s"), f"Q18 gust {gust:g} m/s", ["Q18"]))


def _s3_dlc(spec: Record, req: Record, design: Record) -> Record:
    from hiraku.solvers.k2_vehicle.shelf import DLC_OFFSET  # the ISO 3888-1 layout, as cited there
    on, keys = _loading(req, design)
    speed = number(req, "Q50", "km/h")
    return _case(spec, "S3", "handling", f"S3-dlc{speed:g}",
                 f"ダブルレーンチェンジを {speed:g} km/h で、片側の 2 輪が浮かない ({LOADING_JA[on]}、ISO の速さではない)",
                 {"loading": on, "speed": q(speed, "km/h"), "offset": q(DLC_OFFSET, "m"), "find_limit": False},
                 keys + ["Q50"],
                 _crit("min_side_load", ">", q(0.0, "N"),
                       "both wheels of one side lift when their loads together reach 0", []))


def _s4_brake(spec: Record, req: Record, design: Record) -> Record:
    on, keys = _loading(req, design)
    mu = number(req, "Q11", "1")
    stop = number(req, "Q52", "m")
    return _case(spec, "S4", "handling", f"S4-brake{S4_SPEED_KMH:g}",
                 f"{S4_SPEED_KMH:g} km/h から急制動、μ {mu:g} で {stop:g} m 以内に止まる ({LOADING_JA[on]})",
                 {"loading": on, "speed": q(S4_SPEED_KMH, "km/h"), "mu": q(mu, "1"), "reaction_time": q(0.0, "s")},
                 keys + ["Q11", "Q52"],
                 _crit("stopping_distance", "<=", q(stop, "m"),
                       f"Q52 {stop:g} m from {S4_SPEED_KMH:g} km/h (細目告示 第171条 第5項, borrowed)", ["Q52"]))


def _s4b_park(spec: Record, req: Record, design: Record) -> Record:
    on, keys = _loading(req, design, fixed="empty")
    mu = number(req, "Q11", "1")
    grade = number(req, "Q53", "%")
    return _case(spec, "S4b", "handling", f"S4b-park{grade:g}", f"{grade:g} % の坂にブレーキで止まっていられる (空車)",
                 {"loading": on, "parking_slope": q(grade, "%"), "mu": q(mu, "1")}, keys + ["Q11", "Q53"],
                 _crit("parking_friction_demand", "<=", q(mu, "1"),
                       f"the braked wheels need at most the road friction Q11 {mu:g}", ["Q11"]))


def _s5_slope(spec: Record, req: Record, design: Record) -> Record:
    on, keys = _loading(req, design)
    cls = text(req, "Q12", tuple("ABCDEFGH"))
    beta = number(req, "Q14", "deg")
    # the uphill side: the one the CG is off toward is the less stable one downhill
    slope_up = "left" if _cg_y(design, on) <= 0.0 else "right"
    return _case(spec, "S5", "handling", f"S5-slope{beta:g}",
                 f"ISO 8608 クラス {cls} の路面、横 {beta:g}° の斜面を {S5_SPEED_KMH:g} km/h で ({LOADING_JA[on]})",
                 {"loading": on, "road_class": cls, "side_slope": q(beta, "deg"), "slope_up": slope_up,
                  "speed": q(S5_SPEED_KMH, "km/h")}, keys + ["Q12", "Q14"], None)


def _p2_grade(spec: Record, req: Record, design: Record) -> Record:
    on, keys = _loading(req, design)
    grade = number(req, "Q13", "%")
    return _case(spec, "P2", "powertrain", f"P2-grade{grade:g}", f"{grade:g} % の坂を発進して登る ({LOADING_JA[on]})",
                 {"loading": on, "grade": q(grade, "%"), "until": "top_speed", "max_time": q(120.0, "s")},
                 keys + ["Q13"],
                 _crit("top_speed", ">", q(0.0, "km/h"), f"climbs Q13 {grade:g} %: a speed above 0 from a standstill",
                       ["Q13"]))


def _p3_accel(spec: Record, req: Record, design: Record) -> Record:
    on, keys = _loading(req, design)
    target = number(req, "Q50", "km/h")
    limit = number(req, "Q51", "s")
    return _case(spec, "P3", "powertrain", f"P3-accel{target:g}",
                 f"止まった所から {target:g} km/h まで {limit:g} s 以内 ({LOADING_JA[on]})",
                 {"loading": on, "target_speed": q(target, "km/h"), "grade": q(0.0, "%")}, keys + ["Q50", "Q51"],
                 _crit("time_to_speed", "<=", q(limit, "s"), f"Q51 {limit:g} s to Q50 {target:g} km/h", ["Q51"]))


# ------------------------------------------------------------------ past the limit
def _f1_beyond(spec: Record, req: Record, design: Record) -> Record:
    on, keys = _loading(req, design)
    speed = number(req, "Q50", "km/h")
    s, load = _summary(design, on)
    w = wind_inputs(design, {"params": {"loading": on}})
    crit = _k0("crosswind.critical_wind_speed", "v_crit",
               {"m": load["mass"], "t": s["track_front"], "C_y": w["C_y"], "A": w["A"], "h_cp": w["h_cp"], "rho": w["rho"]})
    gust = BEYOND_FACTOR * crit
    wind_from = "left" if _cg_y(design, on) <= 0.0 else "right"
    return _case(spec, "F1", "aero", f"F1-gust{gust:.0f}-beyond",
                 f"限界の外: 転倒する風速 {crit:.1f} m/s (手計算) の {BEYOND_FACTOR:g} 倍、{gust:.1f} m/s の横風 "
                 f"({LOADING_JA[on]})",
                 {"loading": on, "wind_speed": q(gust, "m/s"), "speed": q(speed, "km/h"), "wind_from": wind_from},
                 keys + ["Q50"],
                 _crit("critical_wind_speed", ">=", q(gust, "m/s"),
                       f"the gust of this case, {BEYOND_FACTOR:g} x the hand-calculated tipping wind speed", []))


def _s4_beyond(spec: Record, req: Record, design: Record) -> Record:
    on, keys = _loading(req, design)
    stop = number(req, "Q52", "m")
    return _case(spec, "S4", "handling", f"S4-mu{BEYOND_MU:g}-beyond",
                 f"限界の外: 滑りやすい砂利 (μ {BEYOND_MU:g} は仮定) で {S4_SPEED_KMH:g} km/h から急制動、"
                 f"{stop:g} m 以内に止まる ({LOADING_JA[on]})",
                 {"loading": on, "speed": q(S4_SPEED_KMH, "km/h"), "mu": q(BEYOND_MU, "1"), "reaction_time": q(0.0, "s")},
                 keys + ["Q52"],
                 _crit("stopping_distance", "<=", q(stop, "m"),
                       f"Q52 {stop:g} m from {S4_SPEED_KMH:g} km/h (細目告示 第171条 第5項, borrowed)", ["Q52"]))


def _s5_beyond(spec: Record, req: Record, design: Record) -> Record:
    on, keys = _loading(req, design)
    cls = text(req, "Q12", tuple("ABCDEFGH"))
    s, load = _summary(design, on)
    ti = tilt_inputs(design, {"params": {"loading": on}})
    tip = _k0("tilt.spring_roll_angle", "theta", dict(ti, t=s["track_front"], m=load["mass"]))
    beta = BEYOND_FACTOR * tip
    slope_up = "left" if _cg_y(design, on) <= 0.0 else "right"
    return _case(spec, "S5", "handling", f"S5-slope{beta:.0f}-beyond",
                 f"限界の外: 上の輪が浮く傾き {tip:.1f}° (手計算) の {BEYOND_FACTOR:g} 倍、横 {beta:.1f}° の斜面 "
                 f"(タイヤは滑らないものとして μ {BEYOND_HOLD_MU:g}、{LOADING_JA[on]})",
                 {"loading": on, "road_class": cls, "side_slope": q(beta, "deg"), "slope_up": slope_up,
                  "speed": q(S5_SPEED_KMH, "km/h"), "length": q(20.0, "m"), "slope_ramp_time": q(BEYOND_RAMP_S, "s"),
                  "mu": q(BEYOND_HOLD_MU, "1")},
                 keys + ["Q12"],
                 _crit("tip_slope_angle", ">", q(beta, "deg"),
                       "the uphill wheels must stay down up to this slope: the slope at which they lift must be steeper",
                       []))


# variant "beyond" of the catalog spec; PLANS_BEYOND has the steps that differ from PLANS
BEYOND: dict[str, Callable[[Record, Record, Record], Record]] = {"F1": _f1_beyond, "S4": _s4_beyond, "S5": _s5_beyond}


def _variant(test_id: str, fn):
    def build(spec: Record, req: Record, design: Record) -> Record:
        if spec.get("variant") == "beyond":
            if test_id not in BEYOND:
                raise BuildError(f"test {test_id} has no case past the limit (variant beyond): only "
                                 + ", ".join(BEYOND))
            return BEYOND[test_id](spec, req, design)
        return fn(spec, req, design)
    build.__name__ = fn.__name__
    build.__doc__ = fn.__doc__
    return build


BUILDERS: dict[str, Callable[[Record, Record, Record], Record]] = {
    tid: _variant(tid, fn) for tid, fn in {
        "S1": _s1_tilt, "S1'": _s1p_ssf, "F1": _f1_wind, "S3": _s3_dlc, "S4": _s4_brake, "S4b": _s4b_park,
        "S5": _s5_slope, "P2": _p2_grade, "P3": _p3_accel}.items()
}


# ------------------------------------------------------------------ inputs read off the design
def _vehicle(design: Record, load_case: Record):
    """The K2 model of the design for the load case's loading. With params
    cargo_cg_above_deck (the requirement Q41), the cargo parts' centres are put that high
    above the floor they stand on (the bottom of their boxes) first."""
    import copy

    from hiraku.solvers.k2_vehicle.drive import on_board
    from hiraku.solvers.k2_vehicle.model import build_vehicle
    from hiraku.solvers.k2_vehicle.units import si
    lp = load_case.get("params", {})
    if "cargo_cg_above_deck" in lp:
        above = si(lp["cargo_cg_above_deck"], "m") * 1000.0
        design = copy.deepcopy(design)
        for p in design.get("parts", []):
            if p.get("kind") == "cargo":
                box = p["box"]
                box["center_mm"][2] = box["center_mm"][2] - box["size_mm"][2] / 2.0 + above
    veh = build_vehicle(on_board(design, lp.get("loading")))
    ground = min(float(c.pos[2]) - c.radius for c in veh.corners)
    return veh, ground


def _height(veh, ground: float) -> Record:
    return q((float(veh.cg_total[2]) - ground) * 1000.0, "mm")


def tilt_inputs(design: Record, load_case: Record) -> Record:
    """h, e (toward the side it tips to first) of the whole vehicle; m_s, h_s (above the
    ground: the roll axis of vertical supports) of the sprung part; k, the mean wheel rate;
    n, the number of axles. With params cargo_cg_above_deck the cargo is placed as the
    requirement says (see _vehicle)."""
    veh, ground = _vehicle(design, load_case)
    sprung = [c for c in veh.corners if c.dof]
    return {"h": _height(veh, ground), "e": q(abs(float(veh.cg_total[1])) * 1000.0, "mm"),
            "m_s": q(veh.m_s, "kg"), "h_s": q((float(veh.cg_s[2]) - ground) * 1000.0, "mm"),
            "k": q(sum(c.k_w for c in sprung) / max(len(sprung), 1) / 1000.0, "N/mm"),
            "n": q(len({round(float(c.pos[0]), 6) for c in veh.corners}), "1")}


def tilt_inputs_as_built(design: Record, load_case: Record) -> Record:
    """tilt_inputs with the cargo where the design has it (params cargo_cg_above_deck, the
    requirement Q41, left out): the inputs of a prediction made again after Q41 was found wrong."""
    params = {k: v for k, v in (load_case.get("params") or {}).items() if k != "cargo_cg_above_deck"}
    return tilt_inputs(design, dict(load_case, params=params))


def wind_inputs(design: Record, load_case: Record) -> Record:
    """C_y, A, h_cp and rho as the crosswind tool takes them (load case, else the design
    variables, else the side projection of the part boxes)."""
    from hiraku.solvers.k2_vehicle.shelf import CrosswindTool
    veh, ground = _vehicle(design, load_case)
    tool = CrosswindTool()
    tool._vars = design.get("variables") or {}
    aero = tool.aero(veh, load_case.get("params", {}))
    return {"C_y": q(aero.cy, "1"), "A": q(aero.side_area, "m^2"), "rho": q(aero.rho, "kg/m^3"),
            "h_cp": q((aero.side_cp[2] + float(veh.cg_s[2]) - ground) * 1000.0, "mm"), "h": _height(veh, ground)}


def dlc_inputs(design: Record, load_case: Record) -> Record:
    """X, the shorter lane change of the K2 course; h, the CG height."""
    from hiraku.solvers.k2_vehicle.shelf import DLC_SECTIONS
    veh, ground = _vehicle(design, load_case)
    return {"X": q(min(DLC_SECTIONS[1], DLC_SECTIONS[3]) * 1000.0, "mm"), "h": _height(veh, ground)}


def park_inputs(design: Record, load_case: Record) -> Record:
    """h, the CG height; braking.parking_friction's mu_all needs every wheel braked."""
    veh, ground = _vehicle(design, load_case)
    if not all(c.brake for c in veh.corners):
        raise BuildError("S4b predicts with mu_all, which takes every wheel braked; this design brakes only "
                         + ", ".join(c.id for c in veh.corners if c.brake) + " (use mu_front or mu_rear)")
    return {"h": _height(veh, ground)}


def lock_inputs(design: Record, load_case: Record) -> Record:
    """mu of a locked wheel: the K2 tyre (Magic Formula, peak mu = the load case's mu) at
    slip -1, sum |Fx| / sum Fz over the wheels at their static loads."""
    from hiraku.solvers.k2_vehicle.drive import on_board
    from hiraku.solvers.k2_vehicle.model import build_vehicle
    from hiraku.solvers.k2_vehicle.units import si
    lp = load_case.get("params", {})
    veh = build_vehicle(on_board(design, lp.get("loading")), {"mu": si(lp["mu"], "1")})
    wheels = [c for c in veh.corners if c.mf is not None]
    mu = sum(abs(c.mf.forces(c.static_load, -1.0, 0.0)[0]) for c in wheels) / sum(c.static_load for c in wheels)
    return {"mu": q(mu, "1")}


def slope_inputs(design: Record, load_case: Record) -> Record:
    """a_y = tan(side_slope) in g (the slope taken as a lateral acceleration); h; e, the CG's
    offset toward the downhill side."""
    from hiraku.solvers.k2_vehicle.units import si
    veh, ground = _vehicle(design, load_case)
    lp = load_case["params"]
    beta = si(lp["side_slope"], "rad")
    y = float(veh.cg_total[1]) * 1000.0
    return {"a_y": q(math.tan(beta), "g"), "h": _height(veh, ground),
            "e": q(-y if lp.get("slope_up", "left") == "left" else y, "mm")}


def launch_inputs(design: Record, load_case: Record) -> Record:
    """The launch tool's motor and road load (hiraku.solvers.k0_handcalc.scene_inputs.launch_inputs):
    T, i, eta, r for launch.peak_tractive_force (F_max = T i eta / r of the driven wheels), and
    P, eta, C_rr, C_d, A (front) and rho."""
    from hiraku.solvers.k0_handcalc import scene_inputs
    lp = load_case.get("params", {})
    return scene_inputs.launch_inputs(design, lp.get("loading"), lp)["inputs"]


# ------------------------------------------------------------------ scripted steps
# lambda is the design's mass_factor (K2 itself takes the wheels as discs of the unsprung
# mass, about 1 % more on the demo vehicle)
# F_max is the prediction of launch.peak_tractive_force made first in the same step (pred.<quantity>).
_LAUNCH_REFS = {"m": "load.mass", "F_max": "pred.peak_tractive_force", "P": "from.P", "eta": "from.eta",
                "C_rr": "from.C_rr", "C_d": "from.C_d", "A": "from.A", "grade": "lc.grade", "rho": "from.rho",
                "lambda": "design.var.mass_factor"}
_TRACTIVE_FORCE = {
    "quantity": "peak_tractive_force", "formula_id": "launch.peak_tractive_force", "output": "F_max",
    "assumptions": ["モーターの最大のトルクが減速比と伝達の効率を通って駆動輪に出る",
                    "駆動輪の半径は荷重でたわんだ半径 (K2 の車輪の自由半径 − 止まっているときの輪の荷重 / タイヤの縦のばね "
                    "tyre_stiffness、駆動輪の平均)。設計に tyre_stiffness が無いときは自由半径 (F_max を小さめに出す)"],
    "inputs": {"T": "from.T", "i": "from.i", "eta": "from.eta", "r": "from.r"}, "inputs_from": launch_inputs}

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
               "inputs": {"v": "lc.speed", "mu": "lc.mu", "t_r": "lc.reaction_time"}}],
           # Run on the demo vehicle (two_cargo, 35 km/h, mu 0.6) with the Magic Formula tyre: K0 8.03 m,
           # K2 10.75 m (+33.9 %); K0 with the locked-wheel mu 0.442 of lock_inputs: 10.90 m (K2 -1.4 %).
           # The braking tool's own check (locked wheels from 40 km/h) agrees with that closed form to -1.5 %.
           "expected_mismatch": {
               "quantity": "stopping_distance",
               "source": "the K2 department's run on the demo vehicle with the Magic Formula tyre (two_cargo, "
                         "35 km/h, mu 0.6) and the braking tool's locked-wheel own check, 2026-11-03",
               "cause": "ブレーキは全輪をロックする (ABS なし)。ロックした輪はすべり率 -1 で滑り、タイヤの式 "
                        "(Magic Formula) ではそのときの摩擦が頂点の μ より小さい (デモの車で μ 0.6 に対して約 0.44)。"
                        "式は頂点の μ で減速するとみている",
               "fix": "ロックした輪の μ (タイヤの式のすべり率 -1 の値) で予測し直す",
               "retry": {
                   "say": "ロックした輪の摩擦で、止まるまでの距離を予測し直します",
                   "predictions": [{
                       "quantity": "stopping_distance", "formula_id": "braking.stopping_distance", "output": "s",
                       "assumptions": ["全輪がロックして滑る (すべり率 -1 のタイヤの摩擦)",
                                       "空走の時間は荷重条件の reaction_time"],
                       "inputs": {"v": "lc.speed", "mu": "from.mu", "t_r": "lc.reaction_time"},
                       "inputs_from": lock_inputs}]}}},
    "S4b": {"tool": "braking", "say": "坂に止めておくのに要る摩擦を、坂の力と輪の荷重の釣り合いで先に見積もります",
            "predictions": [{
                "quantity": "parking_friction_demand", "formula_id": "braking.parking_friction", "output": "mu_all",
                "assumptions": ["全輪にブレーキ、どの輪も同じ割合で摩擦を使う", "坂の上り向きに止める"],
                "inputs": {"a": "load.cg_from_front_axle", "L": "summary.wheelbase", "h": "from.h", "grade": "lc.parking_slope"},
                "inputs_from": park_inputs}]},
    "S5": {"tool": "offroad_roll", "say": "横の斜面に止まっているときの、上の側の輪の荷重を先に見積もります",
           "predictions": [{
               "quantity": "mean_uphill_load", "formula_id": "lane_change.inner_wheel_load", "output": "W_inner",
               "assumptions": ["斜面を横の加速度 tan β とみる", "凸凹の路面の上下の揺れは平均では消える"],
               "inputs": {"m": "load.mass", "a_y": "from.a_y", "h": "from.h", "t": "summary.track_front"},
               "inputs_from": slope_inputs}],
           # Run on the demo vehicle (two_cargo, 15 deg, double wishbones on the builtin K&C table):
           # K0 2061.6 N, K2 1849.5 N (-10.3 %). Split by K2 runs on the smooth slope: the exact rigid
           # balance W (cos b (t/2 - e) - sin b h) / t gives 1970.7 N and K2 with rigid supports
           # 1968.9 N; K2 on its suspension 1841.8 N; the rough road moves the mean by +0.4 % only.
           "expected_mismatch": {
               "quantity": "mean_uphill_load",
               "source": "the K2 department's split of the gap on the demo vehicle (rigid and sprung K2 runs on "
                         "the smooth slope against the exact moment balance), 2026-11-03, again with the double "
                         "wishbones of the builtin K&C table",
               "cause": "式は斜面を横の加速度 tan β とみて、重さ全部を法線の荷重にしている (15° で 3.5 % 大きい)。"
                        "重心が下の側へ 4 mm ずれている分 (1 %) も入っていない。さらに、車体がサスペンションの上で"
                        "下の側へロールして重心が下の側へ動く分 (6.5 %) が入っていない。路面の凸凹は平均を 0.4 % "
                        "しか変えない",
               "fix": "cos β と重心の横のずれを入れた斜面の釣り合い tilt.side_slope_uphill_load で予測し直す "
                      "(ばねのロールの分は式に無いので、その分は残る)",
               "retry": {
                   "say": "斜面の釣り合いの式で、上の側の輪の荷重を予測し直します",
                   "predictions": [{
                       "quantity": "mean_uphill_load", "formula_id": "tilt.side_slope_uphill_load", "output": "W_up",
                       "assumptions": ["剛体として斜面の上で釣り合う (ばねのロールは入れない)",
                                       "凸凹の路面の上下の揺れは平均では消える"],
                       "inputs": {"m": "load.mass", "t": "summary.track_front", "h": "from.h", "e": "from.e",
                                  "beta": "lc.side_slope"},
                       "inputs_from": slope_inputs}]}}},
    "P2": {"tool": "launch", "say": "坂の上で、出力と走る抵抗が釣り合う速さを先に見積もります",
           "predictions": [{
               "quantity": "top_speed", "formula_id": "launch.top_speed", "output": "v_top",
               "assumptions": ["出力で速さが決まる (モーターの回転の上限は入れない)", "坂の勾配は一定"],
               "inputs": {k: v for k, v in _LAUNCH_REFS.items() if k not in ("F_max", "lambda")},
               "inputs_from": launch_inputs}]},
    "P3": {"tool": "launch", "say": "トルクと出力の線で、目標の速さまでの時間を先に見積もります",
           "predictions": [_TRACTIVE_FORCE, {
               "quantity": "time_to_speed", "formula_id": "launch.time_to_speed", "output": "t",
               "assumptions": ["発進から全力、輪は滑らない", "トルクで決まり、その先は出力で決まる"],
               "inputs": dict(_LAUNCH_REFS, v="lc.target_speed"), "inputs_from": launch_inputs}]},
}

# The steps of the cases past the limit that differ from PLANS (F1 and S4 predict as PLANS).
PLANS_BEYOND: dict[str, Record] = {
    "S5": {"tool": "offroad_roll", "say": "上の輪が浮く横の傾きを、ばねで車体がロールする分を入れた式で先に見積もります",
           "predictions": [{
               "quantity": "tip_slope_angle", "formula_id": "tilt.spring_roll_angle", "output": "theta",
               "assumptions": ["止まった斜面は傾斜台と同じ", "車体はばねの上でロールし、ロールの軸は地面にある"],
               "inputs": {"t": "summary.track_front", "h": "from.h", "e": "from.e", "m": "load.mass", "m_s": "from.m_s",
                          "h_s": "from.h_s", "k": "from.k", "n": "from.n"},
               "inputs_from": tilt_inputs}]},
}
