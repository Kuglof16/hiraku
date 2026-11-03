"""Wheels through their suspension travel and steering range against everything else.

The wheels (unsprung parts other than kind suspension) move up and down by
half the suspension travel each way and, on steered supports, turn about the
vertical axis through the wheel centre by up to the design variable
max_steer_angle. Suspension arms pivot at the frame, so a rigid box would
overstate their motion; their boxes are not swept. Links (model.LINK_KINDS: driveshafts, tie
rods) keep their inner end in place and move their outer end with the wheel.
"""

from __future__ import annotations

import math

import numpy as np

from ..k0_handcalc import units
from .model import LINK_KINDS, TOUCH_MM, Body, bodies, mounted, shape_overlap

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
        if b.kind in LINK_KINDS and b.capsule is not None:
            o = _link_moved(b, R, c, dz, steer)
        else:
            o = (b.turned(R, c) if steer else b).moved([0.0, 0.0, dz])
        for k2, other in bs.items():
            if k2 in group or mounted(b, other, design):
                continue
            ov = shape_overlap(o, other)
            if ov > TOUCH_MM:
                out.append((k, k2, ov))
    return out


def _link_moved(b: Body, R: np.ndarray, c: np.ndarray, dz: float, steer: float) -> Body:
    """A link with the inner end (nearer the centre line) in place and the outer end moved with the wheel."""
    from dataclasses import replace

    from .capsule import Capsule

    a, e = b.capsule.a, b.capsule.b
    inner, outer = (a, e) if abs(a[1]) <= abs(e[1]) else (e, a)
    moved = (c + R @ (outer - c) if steer else outer) + np.array([0.0, 0.0, dz])
    return replace(b, capsule=Capsule(inner, moved, b.capsule.r, b.capsule.name))


def sweep(design: dict, bs: dict[str, Body] | None = None) -> dict:
    """Worst overlap over the travel and steering grid for every support (with where it happens),
    and the free steer angle: {"hits": [(a, b, overlap, condition)], "free_steer": {support: deg}}."""
    bs = bs or bodies(design)
    steer_max = max_steer(design)
    hits: dict[tuple[str, str], tuple[float, str]] = {}
    free = {}
    for s in design["supports"]:
        travel = float(s.get("suspension", {}).get("travel_mm") or 0.0)
        dzs = np.linspace(-travel / 2.0, travel / 2.0, STEPS) if travel > 0 else [0.0]
        steers = np.linspace(-steer_max, steer_max, STEPS) if s["actuation"]["steer"] and steer_max > 0 else [0.0]
        for dz in dzs:
            for st in steers:
                for a, b, ov in _hits(design, bs, s["id"], float(dz), float(st)):
                    if ov > hits.get((a, b), (0.0, ""))[0]:
                        hits[(a, b)] = (ov, f"steer {st:+.0f} deg, wheel travel {dz:+.0f} mm")
        if s["actuation"]["steer"] and steer_max > 0:
            free[s["id"]] = free_steer(design, bs, s["id"], steer_max, dzs)
    return {"hits": sorted(((a, b, o, c) for (a, b), (o, c) in hits.items()), key=lambda t: -t[2]), "free_steer": free}


def steer_room(design: dict, bs: dict[str, Body] | None = None) -> dict[str, dict]:
    """Inputs of the hand calculation geometry.free_steer_angle for each steered support:
    half width w and half length l of its wheel (kind wheel) and the room c between the
    wheel's inner face and the nearest frame member beside it (within the wheel's length and
    height), plus TOUCH_MM (touching is not contact). Supports with no member beside them
    are left out."""
    bs = bs or bodies(design)
    out = {}
    for s in design["supports"]:
        if not s["actuation"]["steer"]:
            continue
        wheel = next((b for b in bs.values() if b.support == s["id"] and b.kind == "wheel"), None)
        if wheel is None:
            continue
        c = np.asarray(s["position_mm"], float)
        corners = wheel.obb.corners()
        w = float(np.ptp(corners[:, 1])) / 2.0
        l = float(np.ptp(corners[:, 0])) / 2.0
        h = float(np.ptp(corners[:, 2])) / 2.0
        side = 1.0 if c[1] >= 0 else -1.0
        inner = side * c[1] - w  # distance of the inner face from the centre line
        best = None
        for b in bs.values():
            if b.kind != "member":
                continue
            m = b.obb.corners()
            if m[:, 0].max() < c[0] - l or m[:, 0].min() > c[0] + l or m[:, 2].max() < c[2] - h or m[:, 2].min() > c[2] + h:
                continue
            face = float((side * m[:, 1]).max())  # the member's face toward the wheel
            if face <= inner and (best is None or face > best[0]):
                best = (face, b.id.split(":", 1)[1])
        if best is not None:
            out[s["id"]] = {"w": w, "l": l, "c": float(inner - best[0] + TOUCH_MM), "member": best[1]}
    return out


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
