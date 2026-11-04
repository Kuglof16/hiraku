"""K0 formulas for spray off the wheels (test H6): the arc of the tread a fender leaves uncovered, and the lines
of spray that reach an occupant.

The arc to cover (a_f ahead of the top, a_r behind it, from straight above the axle) is the requirements' Q87 and
Q88: a public-road rule for other traffic (道路運送車両の保安基準の細目を定める告示 第178条 第2項 第1号, as the
intake department read it), borrowed; applying it to the occupants is an assumption.
"""

from __future__ import annotations

import math

import sympy as sp

from .registry import Formula

BORROWED = ("the arc a_f ahead of the top and a_r behind it is the requirements' Q87 and Q88, borrowed from a "
            "public-road rule for other traffic (告示 第178条 第2項 第1号); applying it to the occupants is an assumption")


def _in(unit: str, description: str, **kw) -> dict:
    return {"unit": unit, "description": description, **kw}


def _uncovered(v: dict) -> dict:
    a_f, a_r, f, r, R, g = v["a_f"], v["a_r"], v["f"], v["r"], v["R"], v["g"]
    alpha = math.degrees(math.acos(R / (R + g)))
    lo, hi = -f + alpha, r + alpha  # tread points whose tangent line meets the fender
    covered = max(0.0, min(a_r, hi) - max(-a_f, lo)) if f + r > 0 else 0.0
    return {"arc": a_f + a_r - covered, "arc_radial": max(0.0, a_f - f) + max(0.0, a_r - r), "alpha": alpha}


def all_formulas() -> list[Formula]:
    n = {k: sp.Symbol(k, real=True) for k in ("n_aim", "n_cover")}
    return [
        Formula(
            formula_id="spray.uncovered_arc",
            title="フェンダーが覆わない踏面の弧 (車軸の真上から前 a_f・後ろ a_r の範囲で、接線に飛ぶ物が当たらない所)",
            domain="assembly",
            expression=("alpha = acos(R / (R + g)); covered = |[-a_f, a_r] ∩ [-f + alpha, r + alpha]| (0 when f + r = 0); "
                        "arc = a_f + a_r - covered; arc_radial = max(0, a_f - f) + max(0, a_r - r)"),
            inputs={"a_f": _in("deg", "arc to cover ahead of the top (Q87)", min=0),
                    "a_r": _in("deg", "arc to cover behind the top (Q88)", min=0),
                    "f": _in("deg", "how far the fender reaches ahead of the top (0: no fender)", min=0),
                    "r": _in("deg", "how far the fender reaches behind the top (0: no fender)", min=0),
                    "R": _in("mm", "tyre radius"),
                    "g": _in("mm", "the fender's inner face over the tyre (0: no fender)", min=0)},
            outputs={"arc": ("deg", "arc of [-a_f, a_r] whose tangent lines miss the fender", None),
                     "arc_radial": ("deg", "arc of [-a_f, a_r] not under the fender seen from the axle", None),
                     "alpha": ("deg", "how far ahead of its start a tangent line meets the fender's circle", None)},
            assumptions=[BORROWED,
                         "angles at the wheel centre from straight above the axle (the ladder_4w fender angles)",
                         "what leaves the tread flies straight along the tangent, the way the tread moves going forward "
                         "(seen from the vehicle: toward the front); no gravity, no air, not in reverse",
                         "the fender is a circular arc of radius R + g from f ahead of the top to r behind, at least as "
                         "wide as the tyre; nothing else covers the tread",
                         "a tangent line from the point theta meets that circle at theta - alpha (it leaves toward the "
                         "front), so the fender covers the points from -f + alpha to r + alpha",
                         "arc_radial (max(0, a_f - f) + max(0, a_r - r)) reads the fender as seen from the axle, but "
                         "what flies along the tangent from the points between -f and -f + alpha passes the fender's "
                         "front end; it is kept as an output only (with the default fender, 30 and 50 deg and a 40 mm "
                         "gap, it gives 0 deg where the lines leave 28 deg of the arc uncovered)"],
            source="plane geometry of a tangent to a circle (a line tangent at radius R meets the circle R + g at "
                   "the angle acos(R / (R + g)))",
            requires=[(lambda v: v["R"] > 0, "R must be positive")],
            fn=_uncovered,
        ),
        Formula(
            formula_id="spray.reaching_lines",
            title="踏面から飛ぶ線のうち、乗員に届く数 (乗員の体の部分とフェンダーだけを見て)",
            domain="assembly",
            expression="n = n_aim - n_cover",
            inputs={"n_aim": _in("1", "lines off the arc that meet an occupant's body part (nothing else in the way)",
                                 min=0),
                    "n_cover": _in("1", "of those, lines that meet the fender first", min=0)},
            outputs={"n": ("1", "lines that reach an occupant", n["n_aim"] - n["n_cover"])},
            assumptions=[BORROWED,
                         "only the occupants' body-part boxes and the fender (a circular arc from the design's fender "
                         "angles, gap and width) are in the scene: no other part, no frame member, no ground, so no "
                         "fewer lines reach than in the full scene",
                         "the tread points and lines as hiraku.solvers.k0_handcalc.spray_inputs.tread_lines (the load "
                         "case's arc, arc_step, lines_across, line_length)"],
            source="counting (spray_inputs.spray_reach_inputs counts the lines)",
            requires=[(n["n_cover"] <= n["n_aim"], "n_cover cannot exceed n_aim")],
            symbols=n,
        ),
    ]
