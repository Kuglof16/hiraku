"""K0 formulas for a frontal impact into a rigid wall."""

from __future__ import annotations

import sympy as sp

from .library_dynamics import G, G_NOTE
from .registry import Formula


def _sym(*names: str) -> dict[str, sp.Symbol]:
    return {n: sp.Symbol(n, real=True) for n in names}


def _in(unit: str, description: str, **kw) -> dict:
    return {"unit": unit, "description": description, **kw}


def all_formulas() -> list[Formula]:
    out = []
    s = _sym("m", "v", "k")
    m, v, k = s["m"], s["v"], s["k"]
    vm = v / 3.6
    km = k * 1000  # N/mm -> N/m
    out.append(Formula(
        formula_id="crash.linear_spring",
        title="線形のばねで壁に当たったときの最大の減速度とつぶれ",
        domain="crash",
        expression="a_max = v sqrt(k/m); x_max = v sqrt(m/k); t_stop = (pi/2) sqrt(m/k); E = m v^2 / 2",
        inputs={"m": _in("kg", "mass of the vehicle with its load", min=0),
                "v": _in("km/h", "impact speed", min=0),
                "k": _in("N/mm", "stiffness of the front against the wall", min=0)},
        outputs={"a_max": ("g", "largest deceleration", vm * sp.sqrt(km / m) / G),
                 "x_max": ("mm", "largest crush", vm * sp.sqrt(m / km) * 1000),
                 "t_stop": ("s", "time from contact to standstill", sp.pi / 2 * sp.sqrt(m / km)),
                 "E": ("J", "kinetic energy taken by the front", m * vm**2 / 2)},
        assumptions=["one mass on one linear spring", "rigid wall", "no damping, no rebound counted", G_NOTE],
        source="undamped mass-spring impact, a quarter period of free vibration (textbook dynamics)",
        requires=[(sp.And(m > 0, k > 0), "m and k must be positive")],
        symbols=s,
    ))
    s = _sym("m", "v", "s")
    m, v, sd = s["m"], s["v"], s["s"]
    vm = v / 3.6
    out.append(Formula(
        formula_id="crash.mean_deceleration",
        title="つぶれる距離から出す平均の減速度",
        domain="crash",
        expression="a_mean = v^2 / (2 s); F_mean = m a_mean; E = m v^2 / 2",
        inputs={"m": _in("kg", "mass of the vehicle with its load", min=0),
                "v": _in("km/h", "impact speed", min=0),
                "s": _in("mm", "crush distance", min=0)},
        outputs={"a_mean": ("g", "mean deceleration over the crush", vm**2 / (2 * sd / 1000) / G),
                 "F_mean": ("N", "mean wall force", m * vm**2 / (2 * sd / 1000)),
                 "E": ("J", "kinetic energy taken by the front", m * vm**2 / 2)},
        assumptions=["constant deceleration over the crush distance", "rigid wall", G_NOTE],
        source="work-energy: F s = m v^2 / 2 (textbook dynamics)",
        requires=[(sd > 0, "the crush distance s must be positive")],
        symbols=s,
    ))
    return out
