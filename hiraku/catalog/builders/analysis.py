"""Load cases for the tests the analysis department's tools take (K1, K3, K4):
C1 crash, N1 modal, R3 fatigue, P4 drive_energy, T1 and T2 thermal, H1, H3 and
H5 assembly; and R4 (the roll bar), whose tool is not on the shelf (no PLAN).

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


def c1_calc(design: Record, load_case: Record) -> Record:
    from hiraku.contract.loading import loading_of, on_board
    from hiraku.solvers.shelf.crash import occupant_space

    space = occupant_space(on_board(design, loading_of(load_case)))
    if space is None:
        raise BuildError("no occupant on board, so there is no occupant space to keep")
    return {"space": q(space, "mm")}


_C1_ASSUME = ["前はつぶれる所のばね 1 本とみる (バンパーのゴムは入れない)", "壁は動かない"]
C1_PLAN = {
    "tool": "crash",
    "say": "前をつぶれる所のばね 1 本とみて、壁に当たったときの減速度とつぶれ、乗員の前に残る長さを先に見積もります",
    "predictions": [
        {"quantity": quantity, "formula_id": "crash.linear_spring", "output": output, "assumptions": _C1_ASSUME,
         "inputs": {"m": "load.mass", "v": "lc.speed", "k": "design.var.crush_stiffness"}}
        for quantity, output in (("peak_deceleration", "a_max"), ("max_crush", "x_max"), ("crash_energy", "E"))
    ] + [
        {"quantity": "occupant_space_margin", "formula_id": "crash.occupant_space_margin", "output": "margin",
         "assumptions": _C1_ASSUME + ["つぶれは車の前の端から後ろへ進む"],
         "inputs": {"m": "load.mass", "v": "lc.speed", "k": "design.var.crush_stiffness", "s": "from.space"},
         "inputs_from": c1_calc},
    ],
}

# ---------------------------------------------------------------- N1 modal

# The hand calculation gives the bounce of the whole vehicle on its springs;
# the lowest mode of the frame model may be pitch or roll, which sit near the
# bounce when the springs are at the axles: 15 %.
N1_TOL = 0.15
# The frame's lowest mode is kept this far above the wheels' turning rate at top speed: HIRAKU's assumption
# for the video, not checked as a general rule. The criterion is on the frequency, not on the gap: the gap's
# relative error is the frequency's times f / (f - f_wheel), about 2.3 on the demo vehicle.
N1_MARGIN = 0.20


def n1_modal(spec: Record, req: Record, design: Record) -> Record:
    import math as _m

    load, keys = loading(req, design)
    v = number(req, "Q50", "km/h")
    r = float(n1_calc(design, {})["driven_wheel_radius"]["value"])
    f_wheel = (v / 3.6) / (2.0 * _m.pi * r / 1000.0)
    return {
        "load_case_id": f"N1-{v:g}kmh",
        "test_id": "N1",
        "domain": spec["domain"],
        "title": f"固有振動数と、{v:g} km/h での車輪とモーターの回る速さ ({LOADING_JA[load]})",
        "params": {"speed": q(v, "km/h"), "loading": load},
        "requirement_keys": ["Q50", *keys],
        "criterion": {"quantity": "first_frame_mode", "op": ">=", "limit": q((1.0 + N1_MARGIN) * f_wheel, "Hz"),
                      "basis": (f"フレームのいちばん低い固有振動数を、Q50 {v:g} km/h での車輪の回転 ({f_wheel:.2f} Hz) より "
                                f"{N1_MARGIN:.0%} 上に離す (幅は HIRAKU の仮定で、一般の目安としては確かめていない。"
                                f"路面の凹凸の加振は入れていない)"),
                      "requirement_keys": ["Q50"]},
        "tolerance_rel": tolerance(spec, N1_TOL),
    }


def n1_calc(design: Record, load_case: Record) -> Record:
    sup = design["supports"]
    driven = [s for s in sup if s["actuation"]["drive"]] or sup
    return {"spring_rate_total": q(sum(float(s["suspension"].get("stiffness_N_per_mm") or 0.0) for s in sup), "N/mm"),
            "driven_wheel_radius": q(sum(s["radius_mm"] for s in driven) / len(driven), "mm")}


def n1_twist(design: Record, load_case: Record) -> Record:
    from hiraku.contract.loading import loading_of
    from hiraku.solvers.k0_handcalc.frame_inputs import ladder_twist_inputs
    from hiraku.solvers.k0_handcalc.vehicle import DesignError

    try:
        return ladder_twist_inputs(design, loading=loading_of(load_case))["inputs"]
    except (DesignError, KeyError, ValueError) as e:
        raise BuildError(f"no ladder frame for the twist hand calculation: {e}") from None


def joint_wall_calc(design: Record, load_case: Record) -> Record:
    """The inputs of frame_static.joint_wall_bending (frame_inputs.joint_wall_inputs) for a step load case (R1s):
    the joint off the wheel mounts where the side wall's plate bending is largest, at the load case's loading,
    step_height and step_supports (default the front-most supports). Quantities named as the formula's inputs."""
    from hiraku.contract.loading import loading_of
    from hiraku.solvers.k0_handcalc import units
    from hiraku.solvers.k0_handcalc.frame_inputs import joint_wall_inputs
    from hiraku.solvers.k0_handcalc.vehicle import DesignError
    from hiraku.solvers.shelf.frame_static import front_supports

    params = load_case.get("params") or {}
    step = units.to_unit(params["step_height"], "mm", "step_height") if "step_height" in params else None
    sup = [s.strip() for s in params["step_supports"].split(",")] if params.get("step_supports") else None
    if step is not None and sup is None:
        sup = front_supports(design)
    try:
        return joint_wall_inputs(design, loading=loading_of(load_case), step_mm=step, step_supports=sup)["inputs"]
    except (DesignError, KeyError, ValueError) as e:
        raise BuildError(f"no ladder joint for the joint wall hand calculation: {e}") from None


_TWIST = ("E", "nu", "J_r", "J_c", "n_c", "w", "L", "I0", "r_x", "k_f", "k_r", "t_f", "t_r", "d_f", "d_r")
N1_PLAN = {
    "tool": "modal",
    "say": ("車全体が 4 本のばねの上で上下に揺れる振動数と、はしご形のフレームが左右のレールを逆に動かしてねじれる "
            "振動数を、車輪とモーターの回る速さとともに先に出します"),
    "predictions": [
        {"quantity": "first_frame_mode", "formula_id": "modal.ladder_twist_mode", "output": "f",
         "assumptions": ["フレームのいちばん低い揺れは、はしご形のねじれ (左右のレールが逆に動く)",
                         "ねじれの角は前後に直線とみる (レイリー法。高めに出る)", "部品の重さは載せている節に等分"],
         "inputs": {k: f"from.{k}" for k in _TWIST}, "inputs_from": n1_twist},
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


def _quarter_car(design: Record, summ: Record, loads: Record, sid: str) -> tuple[float, float, float, float]:
    """(static load N, unsprung mass kg, wheel rate N/mm, damping ratio) of the quarter car at a support."""
    import math as _m

    from hiraku.solvers.k0_handcalc.units import STANDARD_GRAVITY

    W = float(loads[sid]["force_N"][2])
    m_u = sum(it.mass_kg for it in summ["items"] if it.carrier == "support" and it.carried_by[0] == sid)
    s = next(x for x in design["supports"] if x["id"] == sid)
    k = float(s["suspension"].get("stiffness_N_per_mm") or 0.0)
    c = float(s["suspension"].get("damping_Ns_per_mm") or 0.0)
    m_s = W / STANDARD_GRAVITY - m_u
    if k <= 0 or m_s <= 0:
        raise BuildError(f"support {sid} has no spring or carries no sprung mass")
    return W, m_u, k, c * 1e3 / (2.0 * _m.sqrt(k * 1e3 * m_s))


def r3_calc(design: Record, load_case: Record) -> Record:
    """The rear overhang and its end cross member (frame_inputs.end_cross_member_inputs), the front and
    rear quarter cars (static load, unsprung mass, spring, damping ratio of the left wheels), the
    wheelbase and the road class's roughness."""
    from hiraku.contract.loading import loading_of, on_board
    from hiraku.solvers.k0_handcalc.frame_inputs import end_cross_member_inputs
    from hiraku.solvers.k0_handcalc.vehicle import DesignError, mass_summary, support_loads
    from hiraku.solvers.shelf.fatigue import ROAD_CLASSES

    loading_name = loading_of(load_case)
    try:
        ec = end_cross_member_inputs(design, loading=loading_name)
    except (DesignError, KeyError, ValueError) as e:
        raise BuildError(f"no rear overhang with an end cross member for the hand calculation: {e}") from None
    on = on_board(design, loading_name)
    summ = mass_summary(on)
    loads = support_loads(on, summary=summ)["supports"]
    rear_a, _, front_a, _ = ec["supports"]
    out = dict(ec["inputs"])
    for tag, sid in (("f", front_a), ("r", rear_a)):
        W, m_u, k, zeta = _quarter_car(design, summ, loads, sid)
        out.update({f"W_{tag}": q(W, "N"), f"m_u{tag}": q(m_u, "kg"), f"k_s{tag}": q(k, "N/mm"), f"zeta_{tag}": q(zeta, "1")})
    x = {s["id"]: float(s["position_mm"][0]) for s in design["supports"]}
    out["L"] = q(abs(x[front_a] - x[rear_a]), "mm")
    out["G_d"] = q(ROAD_CLASSES[load_case["params"]["road_class"]] * 1e9, "mm^3")
    return out


_END = {k: f"from.{k}" for k in ("e", "a", "w", "L1", "E", "nu", "b", "h", "t", "b_c", "h_c", "t_c", "b_j", "h_j", "t_j",
                                 "misfit_rear", "misfit_front", "give_per_kNm")}
_END_ASSUME = ["輪はレールより外にあり、輪の力はレールをそのずれの分ねじる",
               "左右そろった分は、後ろの継ぎ手で 1 回だけモーメントを分ける",
               "左右逆向きの分は、はしごの左右逆向きの半分を梁の鎖 (レールのねじりと逆向きの曲げ、横の部材の S 字の曲げとねじり。"
               "回す力は質量の慣性が受ける) で解く。端の横の部材が曲がらないとしたときのレールの端のずれを、その部材とフレームの"
               "残りが直列のばねのように分けて受ける",
               "端の横の部材は上下の曲げだけを数え、横の曲げ (慣性の横向きの力による) は入れない"]
_WHEELS = (("rear_a", "sigma_ra", "左の後輪"), ("rear_b", "sigma_rb", "右の後輪"), ("front_a", "sigma_fa", "左の前輪"),
           ("front_b", "sigma_fb", "右の前輪"))
# Why the end cross member (the analysis department's check of the demo vehicle, 2026-11-03): the fatigue tool's
# worst weld is at cross_E, and its stress per kN at each wheel (K1 with inertia relief) splits into the wheel
# patterns as warp 7.7, roll 3.7, heave 2.4 and pitch 2.1 MPa of spread (9.1 in all, class C at 40 km/h): left
# and right differ, not the body's heave and pitch. The wheels stand 200 mm outboard of the rails, so their
# forces twist the rails, and cross_E holds the overhang's rails by bending. The earlier plan (one quarter car
# at the rail joint) gave 0.0003 against 0.080. With the ladder's twist from a warping-torsion beam the plan
# gave 0.057 against 0.080 (version 1) and 0.081 against 0.134 (track 1220 mm with 4.5 mm rails): cross_E's
# antisymmetric moment came out low and fell as the rails stiffened, while K1's stayed. In K1 that moment is
# the redundant of the misfit at the rail ends against cross_E's double-curvature give in series with the
# rest of the frame's give, which is as large as the overhang's own (the joint's cross member bending, the
# ladder ahead warping), and where a moment enters matters: as a turn of the rails' own axes (the wheels'
# offset, the masses' inertia at the nodes) or as a chord couple. A beam chain of the ladder's antisymmetric
# half carries both, and gives 0.084 against 0.080 (version 1), 0.096 against 0.094 (4.5 mm rails), 0.133
# against 0.117 (track 1220 mm) and 0.140 against 0.134 (both). Per kN on one wheel at the weld corner the
# symmetric part is within 1 % (10 % low with 4.5 mm rails), the antisymmetric part 5-7 % low at the rear and
# 16-19 % high at the front: the gap is cross_E's sideways bending from the masses' sideways inertia (0.8-1.0
# MPa, the same at the rear and the front), which the hand calculation leaves out.
R3_PLAN = {
    "tool": "fatigue",
    "say": ("輪はレールより外にあるので、輪の力はレールをねじり、後ろの張り出しの端の横の部材を曲げます。4 輪それぞれ 1 kN "
            "あたりのその応力を出し、4 輪の 1/4 車が悪路で受ける力のゆれから、疲労の損傷を先に見積もります"),
    "predictions": [
        *({"quantity": f"end_cross_member_stress_per_kN_{tag}", "formula_id": "frame_static.end_cross_member_stress",
           "output": out, "assumptions": [f"{name}に 1 kN", *_END_ASSUME], "inputs": _END, "inputs_from": r3_calc}
          for tag, out, name in _WHEELS),
        {"quantity": "fatigue_damage", "formula_id": "fatigue.road_damage_four_wheels", "output": "D",
         "assumptions": ["4 輪それぞれ 1/4 車。溶接の応力は 4 輪の力のゆれに比例する", "左右の道は別、後輪は前輪と同じ道を後から通る",
                         "繰り返しはレイリー分布 (狭い帯域。広い帯域の実際より損傷を大きめに出す)"],
         "inputs": {**{out: f"pred.end_cross_member_stress_per_kN_{tag}" for tag, out, _ in _WHEELS},
                    **{k: f"from.{k}" for k in ("W_f", "m_uf", "k_sf", "zeta_f", "W_r", "m_ur", "k_sr", "zeta_r", "L", "G_d")},
                    "k_t": "design.var.tyre_stiffness", "v": "lc.speed", "d": "lc.distance",
                    "FAT": "design.var.fatigue_fat_class", "m": "design.var.fatigue_slope",
                    "N_knee": "design.var.fatigue_knee_cycles", "m2": "design.var.fatigue_slope_after_knee"},
         "inputs_from": r3_calc},
    ],
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
    """The forest loop: the flat parts are driven at Q50. The steep grades are the test's
    param grade_high in the catalog (the grade driven up for a long way), else Q13; the gentle
    grades and the speed on the steep ones are the params grade_low and speed_low, else half
    of the steep grade and of Q50 (an assumption of this load case)."""
    from hiraku.solvers.k0_handcalc import units

    d = number(req, distance_key, "km")
    v = number(req, "Q50", "km/h")
    given = spec.get("params", {})

    def from_spec(name: str, unit: str) -> float | None:
        if name not in given:
            return None
        try:
            return units.to_unit(given[name], unit, name)
        except ValueError as e:
            raise BuildError(f"catalog params of {spec['id']}: {e}") from None

    keys = [distance_key, "Q50"]
    grade = from_spec("grade_high", "%")
    if grade is None:
        grade = number(req, "Q13", "%")
        keys.insert(1, "Q13")
    low = from_spec("grade_low", "%")
    slow = from_spec("speed_low", "km/h")
    params = {"distance": q(d, "km"), "grade_low": q(grade / 2.0 if low is None else low, "%"), "grade_high": q(grade, "%"),
              "speed_low": q(v / 2.0 if slow is None else slow, "km/h"), "speed_high": q(v, "km/h")}
    if not (0 <= params["grade_low"]["value"] <= grade and 0 < params["speed_low"]["value"] <= v):
        raise BuildError(f"{spec['id']}: grade_low must lie within 0..grade_high and speed_low within 0..Q50")
    return params, keys


def p4_drive_energy(spec: Record, req: Record, design: Record) -> Record:
    load, keys = loading(req, design)
    params, loop_keys = _loop(spec, req, "Q54")
    d = params["distance"]["value"]
    return {
        "load_case_id": f"P4-{d:g}km",
        "test_id": "P4",
        "domain": spec["domain"],
        "title": f"林道の周回を {d:g} km 走って電池が残るか ({LOADING_JA[load]})",
        "params": {**params, "soc_start": q(100.0, "%"), "loading": load},  # starting with a full battery
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
    ] + [
        {"quantity": "soc_end", "formula_id": "drive_energy.soc_end", "output": "soc_end",
         "assumptions": ["使える電力量は電圧 x 容量"],
         "inputs": {"E": "pred.energy_used", "E_batt": "from.battery_energy", "soc_start": "lc.soc_start"},
         "inputs_from": p4_calc},
    ],
}


T_BEYOND_K = 5.0  # the case past the limit: an ambient where the hand-calculated part temperature is this far over its limit


def _hand_temperature(design: Record, load_case: Record, output: str) -> float:
    """thermal.loop_steady_temperature with the plan's inputs (_LOOP_INPUTS) for this load case."""
    from hiraku.solvers import formula_registry
    from hiraku.tools.summary import design_summary

    vals = {}
    for name, ref in _LOOP_INPUTS.items():
        head, _, rest = ref.partition(".")
        if head == "load":
            vals[name] = design_summary(design)["loadings"][load_case["params"]["loading"]][rest]
        elif head == "lc":
            vals[name] = load_case["params"][rest]
        else:  # design.var.<name>
            vals[name] = design["variables"][rest.split(".", 1)[1]]
    return formula_registry().evaluate("thermal.loop_steady_temperature", vals)["outputs"][output]["value"]


def _thermal(test_id: str, what: str, quantity: str, limit_var: str, op: str, output: str):
    """T1 / T2: the loop at the hot end of Q19; the limit is the part's own (a design variable
    with its source in hiraku.designgen); without it, no criterion. Variant "beyond": the ambient
    where the hand calculation puts the part T_BEYOND_K over its limit (the loop's losses lift the
    part the same amount above any ambient)."""
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
        if spec.get("variant") == "beyond":
            if limit is None:
                raise BuildError(f"{test_id} has no case past the limit without the design variable {limit_var}")
            rise = _hand_temperature(design, out, output) - t_amb
            hot = limit - rise + T_BEYOND_K
            out.update({
                "load_case_id": f"{test_id}-{hot:.0f}C-beyond",
                "title": (f"限界の外: 気温 {hot:.1f} ℃ (仮定。{what}の温度が手計算で限界 {limit:g} ℃ を {T_BEYOND_K:g} K 超える"
                          f"気温) で林道の周回を {d:g} km 走ったときの{what}の温度 ({LOADING_JA[load]})"),
                "params": {**out["params"], "ambient_temperature": q(hot, "degC")},
                "requirement_keys": [k for k in out["requirement_keys"] if k != "Q19"],
            })
        return out
    build.__name__ = f"{test_id.lower()}_thermal"
    return build


t1_thermal = _thermal("T1", "電池", "temp_battery_max", "battery_max_temperature", "<=", "T_battery")
t2_thermal = _thermal("T2", "モーターと制御器", "temp_controller_max", "controller_max_temperature", "<", "T_controller")

_LOOP_INPUTS = {
    "m": "load.mass", "C_rr": "design.var.rolling_resistance", "C_d": "design.var.drag_coefficient",
    "A": "design.var.frontal_area", "rho": "design.var.air_density", "eta_d": "design.var.drivetrain_efficiency",
    "eta_m": "design.var.motor_efficiency", "eta_c": "design.var.controller_efficiency",
    "eta_regen": "design.var.regen_efficiency", "P_aux": "design.var.aux_power", "V": "design.var.battery_voltage",
    "R_b": "design.var.battery_resistance", "d": "lc.distance", "grade_low": "lc.grade_low", "grade_high": "lc.grade_high",
    "v_low": "lc.speed_low", "v_high": "lc.speed_high", "T_amb": "lc.ambient_temperature",
    **{f"{k}_{part}": f"design.var.{part}_{name}" for part in ("battery", "motor", "controller")
       for k, name in (("R", "thermal_resistance"), ("C", "heat_capacity"))},
}
_T_ASSUME = ["周回の区間ごとに目標の速さで一定に走るとみる (加速・減速は入れない)", "損失は走る時間の平均",
             "部品は温度 1 つの熱容量と、空気への熱抵抗"]


def _t_plan(what: str, quantity: str, output: str) -> Record:
    return {
        "tool": "thermal",
        "say": f"周回を区間ごとの一定の速さで走るとみて損失を出し、{what}の温度の上がり方を先に見積もります",
        "predictions": [
            {"quantity": quantity, "formula_id": "thermal.loop_steady_temperature", "output": output,
             "assumptions": _T_ASSUME, "inputs": dict(_LOOP_INPUTS)},
            {"quantity": "drive_time", "formula_id": "thermal.loop_steady_temperature", "output": "t",
             "assumptions": _T_ASSUME, "inputs": dict(_LOOP_INPUTS)},
        ],
    }


T1_PLAN = _t_plan("電池", "temp_battery_max", "T_battery")
T2_PLAN = _t_plan("制御器", "temp_controller_max", "T_controller")

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
    from hiraku.contract.loading import loading_of, on_board
    from hiraku.solvers.k4_geometry import AIST, knee_space

    space = knee_space(on_board(design, loading_of(load_case)))
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


NOT_ASSEMBLED = ("occupant", "cargo")  # people and load ride in, they are not assembled (as in k4_geometry)
# Parts welded into the frame (gussets, through pieces, the roll bar's part) are in place before
# assembly, as the frame is (k4_geometry.model.WELDED): not assembly steps either.
NOT_ASSEMBLED += ("frame", "gusset", "rollbar")


def _assembled(design: Record) -> int:
    return sum(1 for p in design.get("parts", []) if p["kind"] not in NOT_ASSEMBLED)


def h5_assembly(spec: Record, req: Record, design: Record) -> Record:
    load, keys = loading(req, design)
    n = _assembled(design)
    return {
        "load_case_id": "H5-order",
        "test_id": "H5",
        "domain": spec["domain"],
        "title": "部品を付ける順番があるか (先に付けないと入らない部品)",
        "params": {"loading": load},
        "requirement_keys": (["Q82"] if entry(req, "Q82") else []) + keys,
        "criterion": {"quantity": "assembly_steps", "op": ">=", "limit": q(float(n), "1"),
                      "basis": f"H5: all {n} parts of the design go in, each along some direction in some order"},
        "tolerance_rel": tolerance(spec, H_TOL),
    }


def h5_calc(design: Record, load_case: Record) -> Record:
    n_all = len(design.get("parts", []))
    return {"parts": q(float(n_all), "1"), "not_assembled": q(float(n_all - _assembled(design)), "1")}


H5_PLAN = {
    "tool": "assembly",
    "say": "どの部品もどこかの向きから入れられるとみて、組み立ての手順の数を先に数えます",
    "predictions": [
        {"quantity": "assembly_steps", "formula_id": "assembly.part_count", "output": "steps",
         "assumptions": ["部品 1 つに手順 1 つ", "どの部品もどこかの向きから入れられる", "乗員と荷は組み立てない"],
         "inputs": {"n": "from.parts", "n_out": "from.not_assembled"}, "inputs_from": h5_calc},
    ],
}


# ---------------------------------------------------------------- R4 roll-over protective structure (ROPS)

# The loads are those of the OECD static test of tractor roll-over protective structures, borrowed for this
# vehicle (the code is for tractors of at least 600 kg unballasted, section 2.1; this is not a certification):
# source: OECD Standard Code for the Official Testing of Protective Structures on Agricultural and Forestry
# Tractors (Static Test), Code 4, February 2026 edition, read from
# https://www.oecd.org/content/dam/oecd/en/topics/policy-sub-issues/tractors/04-Code4-EN.pdf on 2026-11-03:
# 3.5.2.1 longitudinal loading E_IL1 = 1.4 M, 3.5.3.1 side loading E_IS = 1.75 M, 3.6 crushing F = 20 M (M the
# reference mass, kg; E in J, F in N), 3.5.2 the longitudinal load on the uppermost transverse member at one
# sixth of the width of the top inwards from the outside corner, 3.5.3 the side load at the upper extremity,
# 3.6.1 the crushing force across the rear uppermost members with its resultant in the median plane, 3.5.2.2 and
# 3.5.3.2 the loading stopped when the structure infringes on the clearance zone.
R4_CODE4 = {"rear": 1.4, "side": 1.75, "crush": 20.0}  # J/kg, J/kg, N/kg
# Reference mass: 500 kg, the mass for which the catalog's values (side 875 J, rear 700 J, from above 10 kN) are
# the Code 4 formulas (875 / 1.75 = 700 / 1.4 = 10 000 / 20 = 500). Not the demo vehicle's mass; Code 4 sets M
# from the tractor (an assumption of this catalog, kept as given).
R4_REFERENCE_MASS_KG = 500.0
# Code 4 3.5.2: one sixth of the top's width in from the outside corner. Here the width is taken as the posts'
# centre-to-centre span (assumption: Code 4 measures it to the outside of the structure).
R4_REAR_FRACTION = 1.0 / 6.0
# A tool's crushed roll bar against the hand calculation's bilinear portal: the deflection at the required energy
# is long on the plastic plateau and sensitive to the collapse load (the hinges form one by one, not together),
# and the clearance is a difference of that deflection and the gap: 25 % (assumption).
R4_TOL = 0.25

# No PLAN: the tool that loads the roll bar and judges it is not on the shelf (HIRAKU's Claude writes it in the run).
# K0 formulas for its prediction, inputs from hiraku.solvers.k0_handcalc.frame_inputs.portal_frame_inputs(design)
# (its "side", "rear" and "top" dicts are every input but these from the load case):
#   frame_static.portal_frame_side  E_in = lc.side_energy           -> delta_E (sideways at the top), H_c, k
#   frame_static.portal_frame_rear  E_in = lc.rear_energy, f = lc.rear_fraction -> delta_E (along the vehicle), P_c
#   frame_static.portal_frame_top   F = lc.crush_force               -> delta (down at mid-span), F_c (collapse)
# and the occupant space: hiraku.solvers.k4_geometry.occupant_clearance(design, displacements=...) with the roll
# bar's top nodes (LHT, RHT; designgen.ladder_4w.build_rollbar) moved by those deflections.


def r4_rops(spec: Record, req: Record, design: Record) -> Record:
    """R4: the side, rear and crushing loads of OECD Code 4 (borrowed), on the roll bar; the occupants' space
    must stay clear. A catalog param side_energy, rear_energy, crush_force or rear_fraction wins over the value
    from the code and the reference mass. Q64 (ROPS) and Q31 (the body sizes the space is made for) are listed
    when the requirements have them; Q64 saying there is none is an error."""
    from hiraku.solvers.k0_handcalc import units

    load, keys = loading(req, design)
    q64 = entry(req, "Q64")
    if q64 is not None and str(q64.get("value")).strip().lower() in ("なし", "no", "none", "false", "0"):
        raise BuildError(f"requirement Q64 says there is no roll-over protective structure ({q64.get('value')!r}); "
                         "R4 does not apply")
    given = spec.get("params", {})

    def param(name: str, unit: str, default: float) -> float:
        if name not in given:
            return default
        try:
            return units.to_unit(given[name], unit, name)
        except ValueError as e:
            raise BuildError(f"catalog params of {spec['id']}: {e}") from None

    M = R4_REFERENCE_MASS_KG
    side = param("side_energy", "J", R4_CODE4["side"] * M)
    rear = param("rear_energy", "J", R4_CODE4["rear"] * M)
    crush = param("crush_force", "N", R4_CODE4["crush"] * M)
    frac = param("rear_fraction", "1", R4_REAR_FRACTION)
    if not 0.0 <= frac <= 0.5 or min(side, rear, crush) <= 0:
        raise BuildError(f"{spec['id']}: the energies and the force must be positive and rear_fraction within 0..0.5")
    req_keys = (["Q64"] if q64 is not None else []) + (["Q31"] if entry(req, "Q31") else [])
    return {
        "load_case_id": "R4-rops",
        "test_id": "R4",
        "domain": spec["domain"],
        "title": (f"横転の保護枠: 横から {side:g} J・後ろから {rear:g} J を吸わせ、上から {crush / 1000:g} kN で押す "
                  f"(OECD Code 4 の静的試験を借りる、{LOADING_JA[load]})"),
        "params": {"side_energy": q(side, "J"), "rear_energy": q(rear, "J"), "crush_force": q(crush, "N"),
                   "rear_fraction": q(frac, "1"), "loading": load},
        "requirement_keys": req_keys + keys,
        "criterion": {"quantity": "rops_occupant_clearance", "op": ">=", "limit": q(0.0, "mm"),
                      "basis": ("R4 (OECD Code 4, 3.5.2.2, 3.5.3.2 and 3.8, borrowed): the roll bar, deflected by "
                                "each loading, must not enter the occupants' space; the smallest gap over the loadings "
                                "between the roll bar's members and the space of k4_geometry.occupant_spaces (the "
                                "occupant box, widened to AIST D2 and raised to AIST I1 of a young male 95th "
                                "percentile) stays at 0 mm or more. The code's second condition (the clearance zone "
                                "left unprotected under the ground plane) is not in this quantity"),
                      "requirement_keys": req_keys},
        "tolerance_rel": tolerance(spec, R4_TOL),
    }


BUILDERS = {
    "C1": c1_crash,
    "N1": n1_modal,
    "R3": r3_fatigue,
    "R4": r4_rops,
    "P4": p4_drive_energy,
    "T1": t1_thermal,
    "T2": t2_thermal,
    "H1": h1_interference,
    "H3": h3_people,
    "H5": h5_assembly,
}
PLANS = {"C1": C1_PLAN, "N1": N1_PLAN, "R3": R3_PLAN, "P4": P4_PLAN, "T1": T1_PLAN, "T2": T2_PLAN, "H1": H1_PLAN,
         "H3": H3_PLAN, "H5": H5_PLAN}
FEEDS: dict[str, list[str]] = {}  # none of these tests takes another's results
