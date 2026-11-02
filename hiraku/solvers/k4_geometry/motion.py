"""Wheels through their suspension travel and steering range against everything else.

The wheels (unsprung parts other than kind suspension) move up and down by
half the suspension travel each way and, on steered supports, turn about the
vertical axis through the wheel centre by up to the design variable
max_steer_angle. Suspension arms pivot at the frame, so a rigid box would
overstate their motion; their boxes are not swept.
"""

from __future__ import annotations

import math

import numpy as np

from ..k0_handcalc import units
from .model import TOUCH_MM, Body, bodies, mounted
from .obb import overlap

STEPS = 7


def _rz(angle_deg: float) -> np.ndarray:
    a = math.radians(angle_deg)
    return np.array([[math.cos(a), -math.sin(a), 0.0], [math.sin(a), math.cos(a), 0.0], [0.0, 0.0, 1.0]])


def max_steer(design: dict) -> float:
    q = design.get("variables", {}).get("max_steer_angle")
    return units.to_unit(q, "deg", "max_steer_angle") if q is not None else 0.0


def _hits(design: dict, bs: dict[str, Body], sid: str, dz: float, steer: float) -> list[tuple[str, str, float]]:
    s = next(x for x in design["supports"] if x["id"] == sid)
    c = np.asarray(s["position_mm"], float)
    group = {k: b for k, b in bs.items() if b.support == sid}
    R = _rz(steer)
    out = []
    for k, b in group.items():
        if b.kind == "suspension":
            continue
        o = (b.obb.turned(R, c) if steer else b.obb).moved([0.0, 0.0, dz])
        for k2, other in bs.items():
            if k2 in group or mounted(b, other, design):
                continue
            ov = overlap(o, other.obb)
            if ov > TOUCH_MM:
                out.append((k, k2, ov))
    return out


def sweep(design: dict, bs: dict[str, Body] | None = None) -> dict:
    """Worst overlap over the travel and steering grid for every support, and the free steer angle."""
    bs = bs or bodies(design)
    steer_max = max_steer(design)
    hits: dict[tuple[str, str], float] = {}
    free = {}
    for s in design["supports"]:
        travel = float(s.get("suspension", {}).get("travel_mm") or 0.0)
        dzs = np.linspace(-travel / 2.0, travel / 2.0, STEPS) if travel > 0 else [0.0]
        steers = np.linspace(-steer_max, steer_max, STEPS) if s["actuation"]["steer"] and steer_max > 0 else [0.0]
        for dz in dzs:
            for st in steers:
                for a, b, ov in _hits(design, bs, s["id"], float(dz), float(st)):
                    hits[(a, b)] = max(hits.get((a, b), 0.0), ov)
        if s["actuation"]["steer"] and steer_max > 0:
            free[s["id"]] = free_steer(design, bs, s["id"], steer_max, dzs)
    return {"hits": sorted(((a, b, o) for (a, b), o in hits.items()), key=lambda t: -t[2]), "free_steer": free}


def free_steer(design: dict, bs: dict[str, Body], sid: str, limit: float, dzs) -> float:
    """Largest steer angle (deg, both ways, whole travel) that adds no contact to what the
    straight-ahead wheel already has, by bisection."""
    base = {}
    for dz in dzs:
        for a, b, o in _hits(design, bs, sid, float(dz), 0.0):
            base[(a, b)] = max(base.get((a, b), 0.0), o)

    def clear(ang: float) -> bool:
        for dz in dzs:
            for sgn in (1, -1):
                for a, b, o in _hits(design, bs, sid, float(dz), sgn * ang):
                    if o > base.get((a, b), 0.0) + TOUCH_MM:
                        return False
        return True
    if clear(limit):
        return limit
    lo, hi = 0.0, limit
    for _ in range(20):
        mid = (lo + hi) / 2.0
        lo, hi = (mid, hi) if clear(mid) else (lo, mid)
    return lo
