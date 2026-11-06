"""K0 formulas for the turning circle of a vehicle with Ackermann steering (written by the K2 team; K2's
turning_circle drives it): the radius of the outer front wheel at full lock, and the inner wheel angle
a radius limit needs (H1-steer)."""

from __future__ import annotations

import sympy as sp

from .registry import Formula


def _in(unit: str, description: str, **kw) -> dict:
    return {"unit": unit, "description": description, **kw}


ASSUMPTIONS = ["Ackermann steering: every wheel rolls about one centre on the line of the unsteered axle",
               "no tyre slip (a slow turn); the tyre turns about its contact point (no scrub radius)",
               "full lock: the inner front wheel at its largest angle, the outer one at its Ackermann angle"]
SOURCE = ("Ackermann geometry: the steered wheels' axes meet on the line of the unsteered axle, so "
          "cot(delta_o) - cot(delta_i) = t / L, and the outer wheel turns about that point at L / sin(delta_o)")


def all_formulas() -> list[Formula]:
    s = {k: sp.Symbol(k, real=True) for k in ("L", "t", "delta_i", "R_max")}
    L, t, di, r_max = s["L"], s["t"], s["delta_i"], s["R_max"]
    deg = sp.pi / 180
    # full lock: the outer wheel's angle from the inner one's
    d_o = sp.atan(1 / (sp.cot(di * deg) + t / L))
    # a radius limit: the outer wheel's angle it needs, then the inner one's
    d_o_req = sp.asin(L / r_max)
    d_i_req = sp.atan(1 / (sp.cot(d_o_req) - t / L))
    base = {"L": _in("m", "wheelbase: from the steered axle to the unsteered axle", min=0),
            "t": _in("m", "track of the steered axle (between the wheels' contact centres)", min=0)}
    return [
        Formula(
            formula_id="turning.min_radius",
            title="最小回転半径 (全切りで回るときの、外側の前輪の接地の中心の円の半径。アッカーマン)",
            domain="handling",
            expression="cot(delta_o) = cot(delta_i) + t / L; R = L / sin(delta_o)",
            inputs={**base, "delta_i": _in("deg", "the inner front wheel's largest steer angle (max_steer_angle)",
                                           min=0, max=90)},
            outputs={"R": ("m", "radius of the outer front wheel's contact centre at full lock", L / sp.sin(d_o)),
                     "delta_o": ("deg", "the outer front wheel's angle at full lock", d_o / deg)},
            assumptions=list(ASSUMPTIONS),
            source=SOURCE,
            requires=[(sp.And(L > 0, t > 0, di > 0, di < 90), "L and t must be positive and delta_i between 0 and 90 deg")],
            symbols={k: s[k] for k in ("L", "t", "delta_i")},
        ),
        Formula(
            formula_id="turning.required_steer",
            title="回転半径の上限を守るのに要る切れ角 (内側の前輪。アッカーマン)",
            domain="handling",
            expression="delta_o = asin(L / R_max); cot(delta_i) = cot(delta_o) - t / L",
            inputs={**base, "R_max": _in("m", "the largest turning radius allowed (outer front wheel)", min=0)},
            outputs={"delta_i": ("deg", "the inner front wheel's angle the limit needs", d_i_req / deg),
                     "delta_o": ("deg", "the outer front wheel's angle the limit needs", d_o_req / deg)},
            assumptions=list(ASSUMPTIONS),
            source=SOURCE,
            requires=[(sp.And(L > 0, t > 0, r_max > L, sp.cot(sp.asin(L / r_max)) > t / L),
                       "R_max must be larger than L, and large enough that the inner wheel's angle stays below 90 deg")],
            symbols={k: s[k] for k in ("L", "t", "R_max")},
        ),
    ]
