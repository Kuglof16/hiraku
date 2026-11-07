"""Load case for F2 (air drag): the drag force at the top speed (Q50), with the loading of Q30.

No criterion yet: the limit is not decided, so the load case reports the quantity drag_force (N) only (a load
case without a criterion, as S5 in k2_tests.py). The proposal, not decided:
    drive force available at Q50 - rolling resistance >= drag force

F2_PLAN predicts drag_force with drag.force from the design variables drag_coefficient, frontal_area and
air_density, at the load case's speed (still air). It names no tool: no tool on the shelf takes F2 yet (the CFD
department's tool comes later), so it is not in PLANS; when that tool exists, PLANS["F2"] is F2_PLAN with the
tool's name. No expected_mismatch: the gap between this hand value and the CFD value is not explained yet.
"""

from __future__ import annotations

from hiraku.catalog.builders.common import Record, loading, number, q, tolerance
from hiraku.catalog.loading import LOADING_JA

# The hand calculation and a tool that takes the design's drag_coefficient and frontal_area share
# 1/2 rho C_d A v^2; 10 % (HIRAKU's choice, as F1's crosswind) leaves room for how the tool takes the
# area and the speed. Provisional until the CFD department's tool and check: a catalog tolerance_rel wins.
F2_TOL = 0.10


def f2_drag(spec: Record, req: Record, design: Record) -> Record:
    """F2: driving at Q50 in still air; the quantity is the drag force (no criterion)."""
    load, keys = loading(req, design)
    v = number(req, "Q50", "km/h")
    # proposed criterion (not decided): drive force available at Q50 - rolling resistance >= drag force
    return {
        "load_case_id": f"F2-drag{v:g}kmh",
        "test_id": "F2",
        "domain": spec.get("domain", "aero"),
        "title": f"最高速度 {v:g} km/h で受ける空気抵抗の力 (無風、{LOADING_JA[load]}。基準はまだ無い)",
        "params": {"speed": q(v, "km/h"), "loading": load},
        "requirement_keys": ["Q50", *keys],
        "tolerance_rel": tolerance(spec, F2_TOL),
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
