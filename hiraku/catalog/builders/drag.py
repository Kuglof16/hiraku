"""Load case for F2 (air drag): the drag force at the top speed (Q50), with the loading of Q30.

Criterion (the practitioner's, decided 2026-11-07): drive force available at Q50 - rolling resistance >= drag
force, i.e. drag_force <= F_drive(Q50) - m g C_rr on a level road in still air (drive_margin: the launch tool's
motor, gear, efficiency and driven wheels' loaded radius, the loaded mass).

F2_PLAN predicts drag_force with drag.force from the design variables drag_coefficient, frontal_area and
air_density, at the load case's speed (still air). It names no tool: no tool on the shelf takes F2 yet (the CFD
department's tool comes later), so it is not in PLANS; when that tool exists, PLANS["F2"] is F2_PLAN with the
tool's name. No expected_mismatch: the gap between this hand value and the CFD value is not explained yet.
"""

from __future__ import annotations

from hiraku.catalog.builders.common import BuildError, Record, loading, number, q, tolerance
from hiraku.catalog.loading import LOADING_JA

# The hand calculation and a tool that takes the design's drag_coefficient and frontal_area share
# 1/2 rho C_d A v^2; 10 % (HIRAKU's choice, as F1's crosswind) leaves room for how the tool takes the
# area and the speed. Provisional until the CFD department's tool and check: a catalog tolerance_rel wins.
F2_TOL = 0.10


def drive_margin(design: Record, load: str, v_kmh: float) -> dict:
    """The drive force left for the air at v on a level road: F_drive(v) - m g C_rr, where F_drive(v) =
    min(T i eta / r, eta P / v) (torque-limited, then power-limited: library_dynamics' launch formulas) and T, i,
    eta, r, P, C_rr are the launch tool's (scene_inputs.launch_inputs, the driven wheels' loaded radius), m the
    vehicle with the loading on board (k0_handcalc.vehicle.mass_summary)."""
    from hiraku.contract.loading import on_board
    from hiraku.solvers.k0_handcalc.scene_inputs import launch_inputs
    from hiraku.solvers.k0_handcalc.units import STANDARD_GRAVITY
    from hiraku.solvers.k0_handcalc.vehicle import DesignError, mass_summary

    try:
        li = launch_inputs(design, load)["inputs"]
        m = float(mass_summary(on_board(design, load))["mass_kg"])
    except (DesignError, KeyError, ValueError) as e:
        raise BuildError(f"no drive force for F2's criterion: {e}") from None
    T, i, eta = li["T"]["value"], li["i"]["value"], li["eta"]["value"]
    r, P, crr = li["rolling_radius"]["value"] / 1000.0, li["P"]["value"] * 1000.0, li["C_rr"]["value"]
    v = v_kmh / 3.6
    f_torque, f_power = T * i * eta / r, eta * P / v
    f_roll = m * STANDARD_GRAVITY * crr
    return {"limit_N": min(f_torque, f_power) - f_roll, "drive_N": min(f_torque, f_power), "torque_N": f_torque,
            "power_N": f_power, "roll_N": f_roll, "m_kg": m, "P_kW": P / 1000.0, "eta": eta, "C_rr": crr}


def f2_drag(spec: Record, req: Record, design: Record) -> Record:
    """F2: driving at Q50 in still air; the drag force within the drive force left at Q50 (the practitioner's
    criterion, decided 2026-11-07: drive force available at Q50 - rolling resistance >= drag force)."""
    load, keys = loading(req, design)
    v = number(req, "Q50", "km/h")
    dm = drive_margin(design, load, v)
    if dm["limit_N"] <= 0:
        raise BuildError(f"at Q50 {v:g} km/h the rolling resistance ({dm['roll_N']:.0f} N) takes the whole drive "
                         f"force ({dm['drive_N']:.0f} N): nothing is left for the air")
    which = "出力" if dm["power_N"] < dm["torque_N"] else "トルク"
    basis = (f"Q50 {v:g} km/h で使える駆動力 {dm['drive_N']:.0f} N ({which}で決まる: 出力 {dm['P_kW']:g} kW、"
             f"効率 {dm['eta']:g}) から、転がり抵抗 {dm['roll_N']:.0f} N (質量 {dm['m_kg']:.1f} kg、C_rr {dm['C_rr']:g}) を"
             f"引いた {dm['limit_N']:.0f} N。平らな道で無風 (作者の決め、2026-11-07)")
    return {
        "load_case_id": f"F2-drag{v:g}kmh",
        "test_id": "F2",
        "domain": spec.get("domain", "aero"),
        "title": f"最高速度 {v:g} km/h で受ける空気抵抗の力が、駆動力の余りの中にあるか (無風、{LOADING_JA[load]})",
        "params": {"speed": q(v, "km/h"), "loading": load},
        "requirement_keys": ["Q50", *keys],
        "tolerance_rel": tolerance(spec, F2_TOL),
        "criterion": {"quantity": "drag_force", "op": "<=", "limit": q(dm["limit_N"], "N"), "basis": basis},
    }


F2_PLAN: Record = {
    "say": "最高速度で受ける空気抵抗の力を、抗力係数と前から見た面積から先に見積もります",
    "predictions": [{
        "quantity": "drag_force", "formula_id": "drag.force", "output": "F",
        "assumptions": ["無風で、空気の速さは走る速さ", "抗力係数と前から見た面積は設計の変数 (drag_coefficient, frontal_area)"],
        "inputs": {"C_d": "design.var.drag_coefficient", "A": "design.var.frontal_area", "v": "lc.speed",
                   "rho": "design.var.air_density"}}],
}

BUILDERS = {"F2": f2_drag}
PLANS: dict[str, Record] = {}  # F2_PLAN joins when a tool takes F2 (see the module's doc)
