"""Load cases for the tests the analysis department's tools take (K1, K3, K4):
C1 crash, N1 modal, R3 fatigue, P4 drive_energy, T1 and T2 thermal, H1, H3 and
H5 assembly.

Next to each builder: its tolerance_rel and why, and PLAN (the scripted way to
predict). A prediction's inputs_from works out by code the values its from.<name>
refs need that are not plain reads of the design.
"""

from __future__ import annotations

import math

from hiraku.catalog.builders.common import BuildError, Record, design_var, entry, loading, number, q, text, tolerance
from hiraku.catalog.loading import LOADING_JA

# ---------------------------------------------------------------- C1 crash

# The hand calculation takes the front as one linear spring (the crush zone);
# the tool runs the bumper stroke first and the crush zone after it, which
# lets the front travel further and shapes the pulse. Same mass and energy,
# a different spring path: 15 %.
C1_TOL = 0.15


def c1_crash(spec: Record, req: Record, design: Record) -> Record:
    load, keys = loading(req, design)
    if entry(req, "Q65"):
        v, vkey = number(req, "Q65", "km/h"), "Q65"
    else:  # the crash speed is the top speed unless the requirements say otherwise
        v, vkey = number(req, "Q50", "km/h"), "Q50"
    return {
        "load_case_id": f"C1-{v:g}kmh",
        "test_id": "C1",
        "domain": spec["domain"],
        "title": f"{v:g} km/h で壁に正面から当たる ({LOADING_JA[load]})",
        "params": {"speed": q(v, "km/h"), "loading": load},
        "requirement_keys": [vkey, *keys] + (["Q31"] if entry(req, "Q31") else []),
        "criterion": {"quantity": "occupant_space_margin", "op": ">=", "limit": q(0.0, "mm"),
                      "basis": "C1: the crush must stop short of the occupants (the front overhang ahead of them)",
                      "requirement_keys": [vkey]},
        "tolerance_rel": tolerance(spec, C1_TOL),
    }


C1_PLAN = {
    "tool": "crash",
    "say": "前をつぶれる所のばね 1 本とみて、壁に当たったときの減速度とつぶれを先に見積もります",
    "predictions": [
        {"quantity": quantity, "formula_id": "crash.linear_spring", "output": output,
         "assumptions": ["前はつぶれる所のばね 1 本とみる (バンパーのゴムは入れない)", "壁は動かない"],
         "inputs": {"m": "load.mass", "v": "lc.speed", "k": "design.var.crush_stiffness"}}
        for quantity, output in (("peak_deceleration", "a_max"), ("max_crush", "x_max"), ("crash_energy", "E"))
    ],
}

# ---------------------------------------------------------------- N1 modal

# The hand calculation gives the bounce of the whole vehicle on its springs;
# the lowest mode of the frame model may be pitch or roll, which sit near the
# bounce when the springs are at the axles: 15 %.
N1_TOL = 0.15


def n1_modal(spec: Record, req: Record, design: Record) -> Record:
    load, keys = loading(req, design)
    v = number(req, "Q50", "km/h")
    return {
        "load_case_id": f"N1-{v:g}kmh",
        "test_id": "N1",
        "domain": spec["domain"],
        "title": f"固有振動数と、{v:g} km/h での車輪とモーターの回る速さ ({LOADING_JA[load]})",
        "params": {"speed": q(v, "km/h"), "loading": load},
        "requirement_keys": ["Q50", *keys],
        # no criterion: no requirement sets a frequency limit
        "tolerance_rel": tolerance(spec, N1_TOL),
    }


def n1_calc(design: Record, load_case: Record) -> Record:
    sup = design["supports"]
    driven = [s for s in sup if s["actuation"]["drive"]] or sup
    return {"spring_rate_total": q(sum(float(s["suspension"].get("stiffness_N_per_mm") or 0.0) for s in sup), "N/mm"),
            "driven_wheel_radius": q(sum(s["radius_mm"] for s in driven) / len(driven), "mm")}


N1_PLAN = {
    "tool": "modal",
    "say": "車全体が 4 本のばねの上で上下に揺れるとみて最初の揺れの振動数を、車輪とモーターの回る速さも先に出します",
    "predictions": [
        {"quantity": "first_body_mode", "formula_id": "modal.spring_mass", "output": "f",
         "assumptions": ["車全体が 4 本のばねの上で上下に揺れるとみる", "タイヤは硬いとみる (フレームのモデルと同じ)"],
         "inputs": {"k": "from.spring_rate_total", "m": "load.mass"}, "inputs_from": n1_calc},
        {"quantity": "wheel_rotation_frequency", "formula_id": "modal.rotation_excitation", "output": "f_wheel",
         "assumptions": ["車輪は滑らずに回る"],
         "inputs": {"v": "lc.speed", "r": "from.driven_wheel_radius", "i": "design.var.gear_ratio"}, "inputs_from": n1_calc},
        {"quantity": "motor_rotation_frequency", "formula_id": "modal.rotation_excitation", "output": "f_motor",
         "assumptions": ["車輪は滑らずに回る"],
         "inputs": {"v": "lc.speed", "r": "from.driven_wheel_radius", "i": "design.var.gear_ratio"}, "inputs_from": n1_calc},
    ],
}

# ---------------------------------------------------------------- R3 fatigue

# Miner damage goes with the cube of the stress range (S-N slope 3), so 15 %
# on the stress is about 50 % on the damage.
R3_TOL = 0.5


def r3_fatigue(spec: Record, req: Record, design: Record) -> Record:
    load, keys = loading(req, design)
    d = number(req, "Q90", "km")
    cls = text(req, "Q12", tuple("ABCDEFGH"))
    v = number(req, "Q50", "km/h")  # the top speed: rougher input per metre, the safe side
    return {
        "load_case_id": f"R3-{d:g}km-{cls}",
        "test_id": "R3",
        "domain": spec["domain"],
        "title": f"ISO 8608 の {cls} 級の悪路を {v:g} km/h で {d:g} km 走ったときの溶接の疲労 ({LOADING_JA[load]})",
        "params": {"distance": q(d, "km"), "road_class": cls, "speed": q(v, "km/h"), "loading": load},
        "requirement_keys": ["Q90", "Q12", "Q50", *keys],
        "criterion": {"quantity": "fatigue_damage", "op": "<", "limit": q(1.0, "1"),
                      "basis": f"Miner's rule: the welds last Q90 {d:g} km while the damage stays below 1",
                      "requirement_keys": ["Q90"]},
        "tolerance_rel": tolerance(spec, R3_TOL),
    }


# ---------------------------------------------------------------- P4, T1, T2 on the forest loop

# The hand calculation leaves out the accelerations and braking, the
# auxiliary load and the battery's own resistance, which the K3 circuit has:
# 10 %.
P4_TOL = 0.10
# Thermal: one lumped temperature per part on both sides; the hand side takes
# a mean loss where the circuit follows the loss along the loop: 15 %.
T_TOL = 0.15


def _loop(spec: Record, req: Record, distance_key: str) -> tuple[Record, list[str]]:
    """The forest loop: the steep grades are Q13 and the flat parts are driven at Q50; the
    gentle grades and the speed on the steep ones are the test's params grade_low and
    speed_low in the catalog, else half of Q13 and of Q50 (an assumption of this load case)."""
    from hiraku.solvers.k0_handcalc import units

    d = number(req, distance_key, "km")
    grade = number(req, "Q13", "%")
    v = number(req, "Q50", "km/h")
    given = spec.get("params", {})

    def from_spec(name: str, unit: str, fallback: float) -> Record:
        if name not in given:
            return q(fallback, unit)
        try:
            return q(units.to_unit(given[name], unit, name), unit)
        except ValueError as e:
            raise BuildError(f"catalog params of {spec['id']}: {e}") from None

    params = {"distance": q(d, "km"), "grade_low": from_spec("grade_low", "%", grade / 2.0), "grade_high": q(grade, "%"),
              "speed_low": from_spec("speed_low", "km/h", v / 2.0), "speed_high": q(v, "km/h")}
    if not (0 <= params["grade_low"]["value"] <= grade and 0 < params["speed_low"]["value"] <= v):
        raise BuildError(f"{spec['id']}: grade_low must lie within 0..Q13 and speed_low within 0..Q50")
    return params, [distance_key, "Q13", "Q50"]


def p4_drive_energy(spec: Record, req: Record, design: Record) -> Record:
    load, keys = loading(req, design)
    params, loop_keys = _loop(spec, req, "Q54")
    d = params["distance"]["value"]
    return {
        "load_case_id": f"P4-{d:g}km",
        "test_id": "P4",
        "domain": spec["domain"],
        "title": f"林道の周回を {d:g} km 走って電池が残るか ({LOADING_JA[load]})",
        "params": {**params, "loading": load},
        "requirement_keys": [*loop_keys, *keys],
        "criterion": {"quantity": "soc_end", "op": ">", "limit": q(0.0, "%"),
                      "basis": f"P4: charge is left at the end of Q54 {d:g} km", "requirement_keys": ["Q54"]},
        "tolerance_rel": tolerance(spec, P4_TOL),
    }


def p4_calc(design: Record, load_case: Record) -> Record:
    from hiraku.solvers.k3_power_thermal import forest_loop, repeat
    from hiraku.solvers.k0_handcalc import units

    p = load_case["params"]

    def val(name, unit):
        return units.to_unit(p[name], unit, name)

    segs = repeat(forest_loop(val("grade_low", "1"), val("grade_high", "1"), val("speed_low", "m/s"), val("speed_high", "m/s")),
                  val("distance", "m"))
    length = sum(s.length for s in segs)
    climb = sum(s.length * math.sin(math.atan(s.grade)) for s in segs if s.grade > 0)
    rms = math.sqrt(sum(s.length * s.speed**2 for s in segs) / length)
    eta = 1.0
    for name in ("drivetrain_efficiency", "motor_efficiency", "controller_efficiency"):
        eta *= design_var(design, name, "1")
    return {"loop_climb": q(climb, "m"), "loop_rms_speed": q(rms * 3.6, "km/h"), "drive_efficiency": q(eta, "1"),
            "battery_energy": q(design_var(design, "battery_voltage", "V") * design_var(design, "battery_capacity", "Ah") / 1000.0,
                                "kWh")}


P4_PLAN = {
    "tool": "drive_energy",
    "say": "転がり・空気・登りの仕事の和を効率で割って、周回で使う電力量を先に見積もります",
    "predictions": [
        {"quantity": quantity, "formula_id": "drive_energy.loop_energy", "output": output,
         "assumptions": ["一定の速さで走るとみる (加速・減速は入れない)", "補機の電力は入れない",
                         "空気抵抗は周回の速さの二乗平均で出す", "下りの位置エネルギーは regen_efficiency の分だけ戻る"],
         "inputs": {"m": "load.mass", "d": "lc.distance", "v": "from.loop_rms_speed", "C_rr": "design.var.rolling_resistance",
                    "C_d": "design.var.drag_coefficient", "A": "design.var.frontal_area", "H": "from.loop_climb",
                    "eta": "from.drive_efficiency", "eta_regen": "design.var.regen_efficiency", "E_batt": "from.battery_energy",
                    "rho": "design.var.air_density"},
         "inputs_from": p4_calc}
        for quantity, output in (("energy_used", "E"), ("soc_used", "soc_used"))
    ],
}


def _thermal(test_id: str, what: str, quantity: str, limit_var: str, op: str):
    """T1 / T2: the loop at the hot end of Q19; the limit is the part's own (a design variable
    with its source in hiraku.designgen); without it, no criterion."""
    def build(spec: Record, req: Record, design: Record) -> Record:
        load, keys = loading(req, design)
        params, loop_keys = _loop(spec, req, "Q54")
        t_amb = number(req, "Q19", "degC")  # the hot end of the range
        d = params["distance"]["value"]
        out = {
            "load_case_id": f"{test_id}-{t_amb:g}C",
            "test_id": test_id,
            "domain": spec["domain"],
            "title": f"気温 {t_amb:g} ℃ で林道の周回を {d:g} km 走ったときの{what}の温度 ({LOADING_JA[load]})",
            "params": {**params, "ambient_temperature": q(t_amb, "degC"), "loading": load},
            "requirement_keys": ["Q19", *loop_keys, *keys],
            "tolerance_rel": tolerance(spec, T_TOL),
        }
        limit = design_var(design, limit_var, "degC")
        if limit is not None:
            out["criterion"] = {"quantity": quantity, "op": op, "limit": q(limit, "degC"),
                                "basis": f"{test_id}: the design variable {limit_var} (the part's limit; its source is "
                                         f"in the design generator), against the lumped temperature of the part"}
        return out
    build.__name__ = f"{test_id.lower()}_thermal"
    return build


t1_thermal = _thermal("T1", "電池", "temp_battery_max", "battery_max_temperature", "<=")
t2_thermal = _thermal("T2", "モーターと制御器", "temp_controller_max", "controller_max_temperature", "<")

# ---------------------------------------------------------------- H1, H3, H5 assembly

# The hand calculation and the tool read the same boxes; the tool finds the
# steer angle by bisection over the travel: 5 %.
H_TOL = 0.05


def h1_interference(spec: Record, req: Record, design: Record) -> Record:
    load, keys = loading(req, design)
    steer = design_var(design, "max_steer_angle", "deg")
    if steer:
        criterion = {"quantity": "free_steer_angle", "op": ">=", "limit": q(steer, "deg"),
                     "basis": f"H1: the front wheels turn to the design's max_steer_angle ({steer:g} deg) "
                              "without touching the frame"}
    else:
        criterion = {"quantity": "interference_count", "op": "<=", "limit": q(0.0, "1"),
                     "basis": "H1: no two parts overlap at rest"}
    return {
        "load_case_id": "H1-interference",
        "test_id": "H1",
        "domain": spec["domain"],
        "title": "止まっているときと、車輪が上下しハンドルを切ったときの干渉",
        "params": {"loading": load},
        "requirement_keys": keys,
        "criterion": criterion,
        "tolerance_rel": tolerance(spec, H_TOL),
    }


def h1_calc(design: Record, load_case: Record) -> Record:
    from hiraku.solvers.k0_handcalc.library_geometry import _free_steer
    from hiraku.solvers.k4_geometry import steer_room

    rooms = steer_room(design)
    if not rooms:
        raise BuildError("no steered wheel has a frame member beside it")
    sid = min(rooms, key=lambda k: _free_steer(rooms[k])["theta"])
    r = rooms[sid]
    return {"steer_w": q(r["w"], "mm"), "steer_l": q(r["l"], "mm"), "steer_c": q(r["c"], "mm")}


H1_PLAN = {
    "tool": "assembly",
    "say": "前輪を上から見た長方形とみて、内側の角がレールに届くまでのハンドルの角度を先に出します",
    "predictions": [
        {"quantity": "free_steer_angle", "formula_id": "geometry.free_steer_angle", "output": "theta",
         "assumptions": ["車輪は上から見て直径 x 幅の長方形", "車輪の中心を通る縦の軸で回る", "レールの面は前後に真っすぐ"],
         "inputs": {"w": "from.steer_w", "l": "from.steer_l", "c": "from.steer_c"}, "inputs_from": h1_calc},
    ],
}


def h3_people(spec: Record, req: Record, design: Record) -> Record:
    load, keys = loading(req, design)
    return {
        "load_case_id": "H3-body-size",
        "test_id": "H3",
        "domain": spec["domain"],
        "title": f"体格の範囲の両端で座れるか・膝と頭が当たらないか ({LOADING_JA[load]})",
        "params": {"loading": load},
        "requirement_keys": (["Q31"] if entry(req, "Q31") else []) + keys,
        "criterion": {"quantity": "knee_room", "op": ">=", "limit": q(0.0, "mm"),
                      "basis": "H3: the knees of a young male 95th percentile (AIST J5 buttock-knee length) "
                               "clear the part ahead"},
        "tolerance_rel": tolerance(spec, H_TOL),
    }


def h3_calc(design: Record, load_case: Record) -> Record:
    from hiraku.solvers.k0_handcalc.vehicle import parts_on_board
    from hiraku.solvers.k4_geometry import AIST, knee_space

    space = knee_space(parts_on_board(design, load_case["params"].get("loading", "two_cargo")))
    if not space:
        raise BuildError("no occupant has a part ahead of the knees")
    return {"knee_length": q(min(length for length, _ in space.values()), "mm"),
            "buttock_knee": q(AIST["buttock_knee_m95"], "mm")}


H3_PLAN = {
    "tool": "assembly",
    "say": "座席の背から前の部品までの長さから、膝の長さを引いて、膝の前の余裕を先に出します",
    "predictions": [
        {"quantity": "knee_room", "formula_id": "occupant.knee_room", "output": "room",
         "assumptions": ["背もたれに付けて座る", "膝の長さは AIST の若年男性 95 パーセンタイル (J5)"],
         "inputs": {"L": "from.knee_length", "L_bk": "from.buttock_knee"}, "inputs_from": h3_calc},
    ],
}


def h5_assembly(spec: Record, req: Record, design: Record) -> Record:
    load, keys = loading(req, design)
    return {
        "load_case_id": "H5-order",
        "test_id": "H5",
        "domain": spec["domain"],
        "title": "部品を付ける順番があるか (先に付けないと入らない部品)",
        "params": {"loading": load},
        "requirement_keys": (["Q82"] if entry(req, "Q82") else []) + keys,
        "criterion": {"quantity": "blocked_parts", "op": "<=", "limit": q(0.0, "1"),
                      "basis": "H5: every part can go in along some direction in some order"},
        "tolerance_rel": tolerance(spec, H_TOL),
    }


BUILDERS = {
    "C1": c1_crash,
    "N1": n1_modal,
    "R3": r3_fatigue,
    "P4": p4_drive_energy,
    "T1": t1_thermal,
    "T2": t2_thermal,
    "H1": h1_interference,
    "H3": h3_people,
    "H5": h5_assembly,
}
PLANS = {"C1": C1_PLAN, "N1": N1_PLAN, "P4": P4_PLAN, "H1": H1_PLAN, "H3": H3_PLAN}
FEEDS: dict[str, list[str]] = {}  # none of these tests takes another's results
# R3, T1, T2 and H5 have no scripted plan: their hand calculations need a loss
# or a stress history the scripted run cannot work out without the tool.
