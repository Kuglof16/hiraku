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
# Access for each step, from the side the part comes in. The opening for two hands
# (200 x 125 mm) is from MIL-STD-1472H (Department of Defense Design Criteria
# Standard: Human Engineering), figure 57, not checked in this repository. The reach
# (606 mm) is item C3 Grip reach from back, young adult female 5th percentile, of the
# AIST anthropometric database 1991-92 (see people.py), read from
# https://www.airc.aist.go.jp/dhrt/91-92/data/list.html on 2026-11-03.
TOOL_OPENING_MM = (200.0, 125.0)
TOOL_DEPTH_MM = 300.0  # assumption: room for a hand and a short tool in front of the part
REACH_MM = 606.0  # source: AIST item C3 (above)


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


def steps(design: dict, bs: dict[str, Body] | None = None) -> list[dict]:
    """The assembly order as contract `assembly_step` records, with access checks per step:
    no_interference (the way in is clear), tool_clearance (an opening of TOOL_OPENING_MM,
    TOOL_DEPTH_MM deep, in front of the part on the side it comes from), hand_reach (that face
    within REACH_MM of the outside of what is already built), depends_on (parts already in
    place it touches)."""
    bs = bs or bodies(design)
    asm = disassemble(design, bs)
    members = [b for b in bs.values() if b.kind == "member"]
    placed: list[Body] = []
    out = []
    for i, (k, d_in) in enumerate(asm["order"]):
        part = bs[k]
        d_from = -np.asarray(DIRECTIONS[d_in], float)  # the hands work from where the part comes from
        tool_ok, reach_ok, reach = _access(part, members + placed, d_from)
        touching = sorted(o.id.split(":", 1)[1] for o in placed if overlap(part.obb, o.obb) > -TOUCH_MM)
        step = {"order": i + 1, "part": k.split(":", 1)[1], "direction": d_in, "depends_on": touching,
                "no_interference": True, "tool_clearance": tool_ok, "hand_reach": reach_ok,
                "note": f"face {reach:.0f} mm in from the outside of what is built"}
        out.append(step)
        placed.append(part)
    n = len(out)
    for j, (k, blockers) in enumerate(sorted(asm["blocked"].items())):
        out.append({"order": n + j + 1, "part": k.split(":", 1)[1], "direction": "none", "no_interference": False,
                    "tool_clearance": False, "hand_reach": False,
                    "blocked_by": [b.split(":", 1)[1] for b in blockers],
                    "note": "cannot go in along any of the six directions"})
    return out


def _access(part: Body, placed: list[Body], d_from: np.ndarray) -> tuple[bool, bool, float]:
    reach_out = float(np.sum(part.obb.half * np.abs(part.obb.axes @ d_from)))
    face = part.obb.center + d_from * reach_out
    k = int(np.argmax(np.abs(d_from)))
    others = [i for i in range(3) if i != k]
    axes = np.eye(3)[[k, *others]]
    half = np.array([TOOL_DEPTH_MM / 2.0, TOOL_OPENING_MM[0] / 2.0, TOOL_OPENING_MM[1] / 2.0])
    corridor = OBB(face + d_from * (TOOL_DEPTH_MM / 2.0 + TOUCH_MM), axes, half)
    tool_ok = all(overlap(corridor, o.obb) <= TOUCH_MM for o in placed)
    if placed:
        pts = np.vstack([o.obb.corners() for o in placed])
        edge = float(np.max(pts @ d_from))
        depth = max(edge - float(face @ d_from), 0.0)
    else:
        depth = 0.0
    return tool_ok, depth <= REACH_MM, depth


def _preferred(free: list[str]) -> str:
    """Out upward if possible (assembly from above), then sideways, then the rest."""
    for d in ("+z", "+y", "-y", "+x", "-x", "-z"):
        if d in free:
            return d
    return free[0]


def _reverse(direction: str) -> str:
    """The way the part moves going in, when it came out along `direction`."""
    return ("-" if direction[0] == "+" else "+") + direction[1]
