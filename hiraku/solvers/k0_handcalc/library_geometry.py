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


def _hip_ankle(l1: float, l2: float, angle_deg: float) -> float:
    return math.sqrt(l1 * l1 + l2 * l2 - 2.0 * l1 * l2 * math.cos(math.radians(angle_deg)))


def _knee_angle_room(v: dict) -> dict:
    rooms = []
    for k in ("big", "small"):
        l1, l2, th = v[f"l1_{k}"], v[f"l2_{k}"], v[f"theta_{k}"]
        now = _hip_ankle(l1, l2, th)
        rooms.append(min(now - _hip_ankle(l1, l2, v["theta_lo"]), _hip_ankle(l1, l2, v["theta_hi"]) - now))
    return {"room": min(rooms), "room_big": rooms[0], "room_small": rooms[1]}


def all_formulas() -> list[Formula]:
    s = {k: sp.Symbol(k, real=True) for k in ("L", "L_bk")}
    L, L_bk = s["L"], s["L_bk"]
    c = {k: sp.Symbol(k, real=True) for k in ("n", "n_out")}
    k = {n: sp.Symbol(n, real=True) for n in ("n_rest", "n_slide", "n_wheel", "n_rod", "n_blocked")}
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
            formula_id="assembly.overlap_count",
            title="止まっているときに重なっている物の組の数 (軸に揃えた箱で数える)",
            domain="assembly",
            expression="n = n_rest + n_slide",
            inputs={"n_rest": _in("1", "pairs of boxes overlapping at rest by more than the rule allows", min=0),
                    "n_slide": _in("1", "pairs with the driver's seat moved to either end of its slide", min=0)},
            outputs={"n": ("1", "overlapping pairs", k["n_rest"] + k["n_slide"])},
            assumptions=["parts not turned (no rpy_deg) as their boxes, members along x, y or z as the box of their "
                         "length and section; turned parts and slanted members left out (their axis-aligned box is far "
                         "bigger than they are), so an overlap of one of them can be missed, never added",
                         "two boxes overlap by the smallest of their overlaps along x, y and z",
                         "what may overlap how far is the tool's rule (k4_geometry.model.allowance): the definition of "
                         "an interference, not a calculation"],
            source="counting (assembly_inputs.overlap_inputs)",
            symbols=k,
        ),
        Formula(
            formula_id="assembly.motion_count",
            title="車輪が上下しハンドルを切ったときに当たる物の組の数 (車輪の箱とタイロッドの棒で数える)",
            domain="assembly",
            expression="n = n_wheel + n_rod",
            inputs={"n_wheel": _in("1", "pairs of a wheel (its box turned through the steering's range every 1 deg, "
                                        "its height range grown by the travel) and a box", min=0),
                    "n_rod": _in("1", "pairs of a tie rod (a straight rod of its section's radius, the inner end in "
                                      "place, the outer end with the wheel) and a box", min=0)},
            outputs={"n": ("1", "pairs that meet in motion", k["n_wheel"] + k["n_rod"])},
            assumptions=["only the wheels and the tie rods move (the tool also moves the knuckle, the brake and the "
                         "other links of the corner)",
                         "the wheel turns about the vertical axis through the support's position, through "
                         "-max_steer_angle to +max_steer_angle; it moves up and down by half the travel each way",
                         "the other bodies as in assembly.overlap_count (turned parts and slanted members left out); "
                         "what is mounted to the moving part is the tool's rule (k4_geometry.model.mounted)"],
            source="plane geometry of a turned rectangle and of a line (assembly_inputs.motion_inputs)",
            symbols=k,
        ),
        Formula(
            formula_id="assembly.blocked_count",
            title="6 方向のどれからも入らない部品の数 (枠と溶接の物だけを相手に)",
            domain="assembly",
            expression="n = n_blocked",
            inputs={"n_blocked": _in("1", "parts whose box pushed along +-x, +-y and +-z meets the frame every way",
                                     min=0)},
            outputs={"n": ("1", "parts that cannot go in", k["n_blocked"])},
            assumptions=["only the frame stops a part: its members and the welded pieces (kinds frame, gusset, "
                         "rollbar, bracket), there before any part; parts put in earlier are not counted (the tool "
                         "also tries orders, and slides a part first)",
                         "a part goes straight along one axis; a piece already overlapping it at rest is "
                         "H1-interference's and does not stop it",
                         "boxes as in assembly.overlap_count; turned parts are not counted"],
            source="counting (assembly_inputs.blocked_inputs)",
            symbols=k,
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
            formula_id="geometry.free_steer_angle_limited",
            title="ハンドルの止め (max_steer_angle) までで、車輪がフレームに当たらずに切れる角度",
            domain="assembly",
            expression="theta = min(theta_free, theta_stop)",
            inputs={"theta_free": _in("deg", "steer angle at which the wheel reaches the frame (geometry.free_steer_angle)",
                                      min=0),
                    "theta_stop": _in("deg", "the steering's stop (the design's max_steer_angle)", min=0)},
            outputs={"theta": ("deg", "steer angle reached without touching the frame", None)},
            assumptions=["the steering stops at theta_stop: the wheel is not turned further, so the angle checked "
                         "is not larger than it"],
            source="definition (the smaller of two angles)",
            fn=lambda v: {"theta": min(v["theta_free"], v["theta_stop"])},
        ),
        Formula(
            formula_id="occupant.knee_angle_room",
            title="ペダルを踏む脚の膝の角度が、運転の姿勢の楽な範囲にあるか (腰から足首までの長さで)",
            domain="assembly",
            expression=("d(theta) = sqrt(l1^2 + l2^2 - 2 l1 l2 cos theta); for each body size: "
                        "min(d(theta) - d(theta_lo), d(theta_hi) - d(theta)); room = the smaller of the two"),
            inputs={**{f"{n}_{k}": _in(u, f"{what} of the {who} person's leg", min=0)
                       for k, who in (("big", "large (male 95th percentile)"), ("small", "small (female 5th percentile)"))
                       for n, u, what in (("l1", "mm", "hip to knee (thigh)"), ("l2", "mm", "knee to ankle (shank)"),
                                          ("theta", "deg", "knee angle (between thigh and shank) as seated"))},
                    "theta_lo": _in("deg", "comfortable knee angle, lower end", min=0),
                    "theta_hi": _in("deg", "comfortable knee angle, upper end", min=0)},
            outputs={"room": ("mm", "the smaller margin of the two people (negative: a knee out of the range)", None),
                     "room_big": ("mm", "the large person's margin", None),
                     "room_small": ("mm", "the small person's margin", None)},
            assumptions=["the leg is two straight links hinged at the knee (side view); the hip-ankle distance grows "
                         "with the knee angle, so a margin is negative exactly when the angle is out of the range",
                         "the knee angles are those of K4's seated poses (each person at their best place in the "
                         "seat's slide): not an independent estimate of the angle, only of its margin"],
            source="law of cosines (geometry); the range from Porter and Gyi 1998 as k4_geometry.people.KNEE_COMFORT_DEG",
            requires=[(lambda v: v["theta_lo"] < v["theta_hi"], "theta_lo must be below theta_hi")],
            fn=_knee_angle_room,
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
