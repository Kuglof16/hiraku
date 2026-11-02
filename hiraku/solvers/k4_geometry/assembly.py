"""Assembly order by taking the vehicle apart.

The frame stays (it is the fixture). A part can come out along one of the
six directions (+x, -x, +y, -y, +z, -z) when the box it sweeps on the way out
meets nothing still in place. Bodies it already overlaps where it is bolted
(boxes are rough) do not block it if a small move along the direction does
not make that overlap worse. Parts are taken out one at a time, preferring
the one with the most free directions; the reverse is the assembly order.
Parts that cannot come out are blocked by each other: "this one has to go in
first, or the other cannot get in afterwards". Occupants and cargo are not
assembled and are left out.
"""

from __future__ import annotations

import numpy as np

from .model import TOUCH_MM, Body, bodies
from .obb import OBB, overlap

DIRECTIONS = {"+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0), "+z": (0, 0, 1), "-z": (0, 0, -1)}
NOT_ASSEMBLED = ("occupant", "cargo")
NUDGE_MM = 20.0


def swept(obb: OBB, direction, travel: float) -> OBB:
    """Box covering obb slid by `travel` along `direction`: exact when a box axis is along
    the direction, otherwise the world-aligned box around the sweep (larger, safe side)."""
    d = np.asarray(direction, float)
    k = int(np.argmax(np.abs(obb.axes @ d)))
    if abs(float(obb.axes[k] @ d)) > 1.0 - 1e-9:
        half = obb.half.copy()
        half[k] += travel / 2.0
        return OBB(obb.center + d * travel / 2.0, obb.axes, half, obb.name)
    pts = np.vstack([obb.corners(), obb.corners() + d * travel])
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    return OBB((lo + hi) / 2.0, np.eye(3), (hi - lo) / 2.0, obb.name)


def blocker(part: Body, others: list[Body], direction, travel: float) -> str | None:
    """The first body that stops `part` leaving along `direction`, or None."""
    d = np.asarray(direction, float)
    sweep_box = swept(part.obb.moved(d * NUDGE_MM), d, travel)
    nudged = part.obb.moved(d * NUDGE_MM)
    for o in others:
        start = overlap(part.obb, o.obb)
        if start > TOUCH_MM:  # already overlapping (bolted): moving away must not make it worse
            if overlap(nudged, o.obb) > start + TOUCH_MM:
                return o.id
            continue
        if overlap(sweep_box, o.obb) > TOUCH_MM:
            return o.id
    return None


def disassemble(design: dict, bs: dict[str, Body] | None = None) -> dict:
    bs = bs or bodies(design)
    members = [b for b in bs.values() if b.kind == "member"]
    remaining = {k: b for k, b in bs.items() if b.kind not in NOT_ASSEMBLED and b.kind != "member"}
    pts = np.vstack([b.obb.corners() for b in bs.values()])
    travel = float(np.max(np.ptp(pts, axis=0))) * 1.5
    removed: list[tuple[str, str]] = []
    while remaining:
        best = None
        for k, b in sorted(remaining.items()):
            others = members + [o for kk, o in remaining.items() if kk != k]
            free = [name for name, dv in DIRECTIONS.items() if blocker(b, others, dv, travel) is None]
            if free and (best is None or len(free) > len(best[1])):
                best = (k, free)
        if best is None:
            break
        k, free = best
        removed.append((k, _preferred(free)))
        del remaining[k]
    blocked = {}
    for k, b in remaining.items():
        others = members + [o for kk, o in remaining.items() if kk != k]
        blocked[k] = sorted({blocker(b, others, dv, travel) for dv in DIRECTIONS.values()} - {None})
    order = [(k, _reverse(d)) for k, d in reversed(removed)]
    return {"order": order, "blocked": blocked}


def _preferred(free: list[str]) -> str:
    """Out upward if possible (assembly from above), then sideways, then the rest."""
    for d in ("+z", "+y", "-y", "+x", "-x", "-z"):
        if d in free:
            return d
    return free[0]


def _reverse(direction: str) -> str:
    """The way the part moves going in, when it came out along `direction`."""
    return ("-" if direction[0] == "+" else "+") + direction[1]
