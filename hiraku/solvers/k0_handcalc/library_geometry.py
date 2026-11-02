"""K0 formulas for geometry: how far a steered wheel turns before it meets the
frame, and the room in front of a seated person's knees."""

from __future__ import annotations

import math

import sympy as sp

from .registry import Formula


def _in(unit: str, description: str, **kw) -> dict:
    return {"unit": unit, "description": description, **kw}


def _free_steer(v: dict) -> dict:
    """The inner corner of a w x l half-size rectangle turned by a about its centre moves
    inward by w cos(a) + l sin(a) - w; free until that equals the room c."""
    w, l, c = v["w"], v["l"], v["c"]
    reach = math.hypot(w, l)
    if w + c >= reach:  # the corner never gets that far in
        return {"theta": 90.0}
    return {"theta": math.degrees(math.asin((w + c) / reach) - math.atan2(w, l))}


def all_formulas() -> list[Formula]:
    s = {k: sp.Symbol(k, real=True) for k in ("L", "L_bk")}
    L, L_bk = s["L"], s["L_bk"]
    c = {k: sp.Symbol(k, real=True) for k in ("n", "n_out")}
    return [
        Formula(
            formula_id="assembly.part_count",
            title="組み立てる部品の数 (どれも入れられるなら、組み立ての手順の数)",
            domain="assembly",
            expression="steps = n - n_out",
            inputs={"n": _in("1", "parts in the design", min=0),
                    "n_out": _in("1", "parts that are not assembled (occupants, cargo)", min=0)},
            outputs={"steps": ("1", "assembly steps if every part can go in", c["n"] - c["n_out"])},
            assumptions=["one step per part", "every part can be put in along some direction"],
            source="counting",
            symbols=c,
        ),
        Formula(
            formula_id="geometry.free_steer_angle",
            title="ハンドルを切ったとき、車輪の内側の角がフレームに当たるまでの角度",
            domain="assembly",
            expression="w cos(theta) + l sin(theta) - w = c  ->  theta = asin((w + c) / sqrt(w^2 + l^2)) - atan2(w, l)",
            inputs={"w": _in("mm", "half the tyre width", min=0),
                    "l": _in("mm", "half the tyre length in plan view (the wheel radius)", min=0),
                    "c": _in("mm", "room between the tyre's inner face and the frame beside it", min=0)},
            outputs={"theta": ("deg", "steer angle at which the inner corner reaches the frame (90 if never)", None)},
            assumptions=["the wheel is a rectangle in plan view (diameter x width)",
                         "it turns about the vertical axis through its centre",
                         "the frame face beside it runs along x"],
            source="plane geometry of a rotated rectangle",
            requires=[(lambda v: v["w"] > 0 and v["l"] > 0 and v["c"] >= 0, "w and l must be positive and c not negative")],
            fn=_free_steer,
        ),
        Formula(
            formula_id="occupant.knee_room",
            title="座った人の膝の前の余裕",
            domain="assembly",
            expression="room = L - L_bk",
            inputs={"L": _in("mm", "from the seat back to the nearest part ahead at knee height"),
                    "L_bk": _in("mm", "buttock-knee length of the person (sitting)", min=0)},
            outputs={"room": ("mm", "room in front of the knees (negative: the knees hit)", L - L_bk)},
            assumptions=["the person sits against the seat back", "thighs level, along x"],
            source="difference of lengths (geometry)",
            symbols=s,
        ),
    ]
