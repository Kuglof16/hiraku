"""K0 formulas for a double-wishbone corner on a K&C bench: the front-view roll centre and the
toe from an aligning moment through the tie rod (written by the K2 team; K2's kc_bench measures
both)."""

from __future__ import annotations

import math

import sympy as sp

from .registry import Formula


def _in(unit: str, description: str, **kw) -> dict:
    return {"unit": unit, "description": description, **kw}


def _roll_centre(v: dict) -> dict:
    """The arms' front-view instant centre (where the lines through each arm's inner pivot and
    ball joint cross), then the line from the contact point through it, at y = 0."""
    p1, d1 = (v["y_li"], v["z_li"]), (v["y_lo"] - v["y_li"], v["z_lo"] - v["z_li"])
    q1, d2 = (v["y_ui"], v["z_ui"]), (v["y_uo"] - v["y_ui"], v["z_uo"] - v["z_ui"])
    den = d1[0] * d2[1] - d1[1] * d2[0]
    yc, zc = v["y_c"], v["z_c"]
    if abs(den) < 1e-12 * max(1.0, abs(d1[0] * d2[1])):
        # parallel arms: the instant centre is at infinity along them
        return {"h_rc": zc + (0.0 - yc) * d1[1] / d1[0]}
    t = ((q1[0] - p1[0]) * d2[1] - (q1[1] - p1[1]) * d2[0]) / den
    yi, zi = p1[0] + t * d1[0], p1[1] + t * d1[1]
    return {"h_rc": zc + (0.0 - yc) * (zi - zc) / (yi - yc)}


def all_formulas() -> list[Formula]:
    s = {k: sp.Symbol(k, real=True) for k in ("M", "k_z", "a", "k_t")}
    M, k_z, a, k_t = s["M"], s["k_z"], s["a"], s["k_t"]
    pt = "(front view, the vehicle's centre line at y = 0, the ground at z = 0)"
    return [
        Formula(
            formula_id="kc.roll_center_height",
            title="ダブルウィッシュボーンのロールセンターの高さ (正面から見た瞬間中心の作図)",
            domain="handling",
            expression="IC = (lower arm line) x (upper arm line); h_rc = z_c + (0 - y_c) (z_IC - z_c) / (y_IC - y_c)",
            inputs={"y_li": _in("mm", "lower arm inner pivot, y " + pt), "z_li": _in("mm", "lower arm inner pivot, z"),
                    "y_lo": _in("mm", "lower ball joint, y"), "z_lo": _in("mm", "lower ball joint, z"),
                    "y_ui": _in("mm", "upper arm inner pivot, y"), "z_ui": _in("mm", "upper arm inner pivot, z"),
                    "y_uo": _in("mm", "upper ball joint, y"), "z_uo": _in("mm", "upper ball joint, z"),
                    "y_c": _in("mm", "contact point, y"), "z_c": _in("mm", "contact point, z")},
            outputs={"h_rc": ("mm", "roll centre height above the ground at the design travel", None)},
            assumptions=["the arms' pivot axes run along x, so the front view shows each arm as a line",
                         "small motion about the design travel", "the tyre is rigid at the contact point"],
            source="kinematics of a planar four-bar: the contact point moves about the arms' instant centre, so the "
                   "body rolls about where that line crosses the centre line",
            requires=[(lambda v: abs(v["y_c"]) > 0.0, "the contact point must be off the centre line")],
            fn=_roll_centre,
        ),
        Formula(
            formula_id="kc.aligning_steer",
            title="セルフアライニングトルクで、タイロッドが伸びて車輪が向きを変える角度 (左の輪、トーイン +)",
            domain="handling",
            expression="theta = M (k . z) / (k_t a^2) about the kingpin axis k; toe = -theta (k . z)  (M about +z)",
            inputs={"M": _in("N*m", "aligning moment about the vertical, + turns the wheel's nose to the left"),
                    "k_z": _in("1", "vertical component of the unit kingpin axis (lower to upper ball joint)",
                               min=0, max=1),
                    "a": _in("mm", "arm of the tie rod's force about the kingpin axis", min=0),
                    "k_t": _in("N/mm", "tie rod stiffness along its length", min=0)},
            outputs={"toe": ("deg", "toe change of a left wheel (toe-in +)",
                             -180.0 / sp.pi * 1000.0 * M * k_z ** 2 / (k_t * a ** 2))},
            assumptions=["only the tie rod gives way: the arm bushings and ball joints are stiff",
                         "the knuckle turns about the kingpin axis through the ball joints",
                         "small angles"],
            source="statics of the knuckle about the kingpin axis: the moment's component M (k . z) is held by the "
                   "tie rod force F a, which stretches the rod F / k_t = theta a",
            requires=[(sp.And(k_t > 0, a > 0), "k_t and a must be positive")],
            symbols=s,
        ),
    ]
