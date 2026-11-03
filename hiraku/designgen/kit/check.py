"""One call that says what a design is like before any test: mass and centre of
gravity, ground clearance, approach and departure angles, interference at rest
and through the wheels' motion, and the occupants' fit and reach.

    from hiraku.designgen.kit import check
    report = check.check(design)          # {"quantities": {name: {value, unit, where?}}, "problems": [...]}
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

Record = dict[str, Any]

UNSPRUNG_KINDS = ("wheel",)  # bodies that stand on the ground themselves


def _q(value: float, unit: str, where: str | None = None) -> Record:
    out: Record = {"value": float(value), "unit": unit}
    if where:
        out["where"] = where
    return out


def _bottom_points(design: Record) -> list[tuple[float, float, str]]:
    """(x, lowest z, name) of every body but the wheels: frame members and part boxes, at both ends in x."""
    from hiraku.solvers.k4_geometry import bodies

    out = []
    for b in bodies(design).values():
        if b.kind in UNSPRUNG_KINDS:
            continue
        c = b.obb.corners()
        z = c[:, 2]
        for x in (float(c[:, 0].min()), float(c[:, 0].max())):
            out.append((x, float(z.min()), b.id.split(":", 1)[1]))
    return out


def _ramp_angle(points, wheel_x: float, r: float, ahead: bool) -> tuple[float, str | None]:
    """Largest ramp angle (deg) the wheel can meet before a point beyond it touches: the line
    tangent to the tyre circle (centre (wheel_x, r)) through each point, the smallest of them."""
    best, where = 90.0, None
    for x, z, name in points:
        a = (x - wheel_x) if ahead else (wheel_x - x)
        if a <= 0:
            continue
        b = r - z
        reach = math.hypot(a, b)
        if reach <= r:
            continue
        theta = math.degrees(math.asin(r / reach) - math.atan2(b, a))
        if theta < best:
            best, where = theta, name
    return best, where


def check(design: Record, loading: str = "two_cargo") -> Record:
    """Quantities of the design and the problems found (each a sentence)."""
    from hiraku.contract.loading import on_board
    from hiraku.solvers.k0_handcalc.vehicle import mass_summary
    from hiraku.solvers.k4_geometry import fit, interferences, knee_space, sweep

    on = on_board(design, loading)
    ms = mass_summary(on)
    q: Record = {"mass": _q(ms["mass_kg"], "kg"), "cg_x": _q(ms["cg_mm"][0], "mm"), "cg_y": _q(ms["cg_mm"][1], "mm"),
                 "cg_z": _q(ms["cg_mm"][2], "mm")}
    problems: list[str] = []

    pts = _bottom_points(on)
    if pts:
        x, z, name = min(pts, key=lambda p: p[1])
        q["ground_clearance"] = _q(z, "mm", name)
    sup = design.get("supports", [])
    if sup:
        xs = [s["position_mm"][0] for s in sup]
        front = [s for s in sup if s["position_mm"][0] == max(xs)]
        rear = [s for s in sup if s["position_mm"][0] == min(xs)]
        ang, where = _ramp_angle(pts, max(xs), min(float(s["radius_mm"]) for s in front), ahead=True)
        q["approach_angle"] = _q(ang, "deg", where)
        ang, where = _ramp_angle(pts, min(xs), min(float(s["radius_mm"]) for s in rear), ahead=False)
        q["departure_angle"] = _q(ang, "deg", where)

    static = interferences(on)
    q["interference_count"] = _q(len(static), "1")
    for a, b, depth in static[:5]:
        problems.append(f"{a} and {b} overlap by {depth:.0f} mm at rest")
    sw = sweep(on)
    q["motion_interference_count"] = _q(len(sw["hits"]), "1")
    for a, b, depth, cond in sw["hits"][:5]:
        problems.append(f"{a} meets {b} by {depth:.0f} mm ({cond})")
    if sw["free_steer"]:
        sid = min(sw["free_steer"], key=sw["free_steer"].get)
        q["free_steer_angle"] = _q(sw["free_steer"][sid], "deg", sid)

    groups: dict[str, tuple[float, str]] = {}
    for k, (v, where) in fit(on).items():
        g = k.split(".")[0]
        if g not in groups or v < groups[g][0]:
            groups[g] = (float(v), where)
    for g, (v, where) in groups.items():
        q[g] = _q(v, "mm", where)
        if v < 0:
            problems.append(f"{g} is short by {-v:.0f} mm ({where})")
    # hands on the controls: from the seat back to the part ahead within the grip reach of a
    # small person (AIST C3, young female 5th percentile), the arm reaching forward from the back
    from hiraku.solvers.k4_geometry.assembly import REACH_MM

    space = knee_space(on)
    if space:
        length, where = max(space.values())
        q["reach_to_controls"] = _q(REACH_MM - length, "mm", where)
        if length > REACH_MM:
            problems.append(f"the controls ({where}) are {length - REACH_MM:.0f} mm beyond a small person's grip reach")
    return {"quantities": q, "problems": problems}
