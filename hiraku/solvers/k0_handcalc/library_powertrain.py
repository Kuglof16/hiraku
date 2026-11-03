"""K0 formulas of the powertrain: the largest tractive force at the driven wheels from the motor."""

from __future__ import annotations

import sympy as sp

from .registry import Formula


def _in(unit: str, description: str, **kw) -> dict:
    return {"unit": unit, "description": description, **kw}


def _peak_tractive_force() -> Formula:
    s = {k: sp.Symbol(k, real=True) for k in ("T", "i", "eta", "rolling_radius")}
    T, i, eta, r = s["T"], s["i"], s["eta"], s["rolling_radius"]
    F = T * 1000 * i * eta / r  # N*m -> N*mm over r in mm
    return Formula(
        formula_id="launch.peak_tractive_force",
        title="モーターのトルクで決まる駆動輪の力の最大",
        domain="powertrain",
        expression="F_max = T*i*eta/rolling_radius",
        inputs={"T": _in("N*m", "largest motor torque (at the motor shaft)", min=0),
                "i": _in("1", "overall reduction ratio from the motor to the wheels", min=0),
                "eta": _in("1", "efficiency from motor shaft to wheels", min=0, max=1),
                "rolling_radius": _in("mm", "rolling radius of the driven wheels: the radius under the static wheel load "
                                                    "(the mean when they differ)", min=0)},
        outputs={"F_max": ("N", "largest tractive force at the driven wheels, all of them together", F)},
        assumptions=["the motor's largest torque reaches the wheels through the reduction ratio, less the losses (eta)",
                     "the force acts at the rolling radius (the radius under the static wheel load; the free radius only for a design without tyre stiffness)",
                     "grip not checked: the road may not take this force"],
        source="definition: wheel torque T i eta divided by the wheel radius",
        requires=[(sp.And(T > 0, i > 0, eta > 0, r > 0), "T, i, eta and rolling_radius must be positive")],
        symbols=s,
    )


def all_formulas() -> list[Formula]:
    return [_peak_tractive_force()]
