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
    """{"order": [(body id, direction going in)], "routes": {body id: moves out}, "blocked": {body id: [blockers]}}.
    A part leaves straight along one of the six directions when it can; when no part can, the
    next one leaves by two moves (route.two_moves)."""
    from .route import straight, two_moves

    bs = bs or bodies(design)
    members = [b for b in bs.values() if b.kind == "member"]
    remaining = {k: b for k, b in bs.items() if b.kind not in NOT_ASSEMBLED and b.kind != "member"}
    pts = np.vstack([b.obb.corners() for b in bs.values()])
    travel = float(np.max(np.ptp(pts, axis=0))) * 1.5
    removed: list[tuple[str, list[tuple[str, float]]]] = []
    while remaining:
        best = None
        for k, b in sorted(remaining.items()):
            others = members + [o for kk, o in remaining.items() if kk != k]
            free = straight(b, others, travel)
            if free and (best is None or len(free) > len(best[1])):
                best = (k, free)
        if best is None:  # nothing comes out straight: the shortest two-move route
            for k, b in sorted(remaining.items()):
                others = members + [o for kk, o in remaining.items() if kk != k]
                moves = two_moves(b, others, travel)
                if moves and (best is None or moves[0][1] < best[1][0][1]):
                    best = (k, moves)
            if best is None:
                break
            k, moves = best
        else:
            k, moves = best[0], [(best[1][0], travel)]
        removed.append((k, moves))
        del remaining[k]
    blocked = {}
    for k, b in remaining.items():
        others = members + [o for kk, o in remaining.items() if kk != k]
        blocked[k] = sorted({blocker(b, others, dv, travel) for dv in DIRECTIONS.values()} - {None})
    order = [(k, _reverse(moves[0][0])) for k, moves in reversed(removed)]
    return {"order": order, "routes": dict(removed), "blocked": blocked}


def steps(design: dict, bs: dict[str, Body] | None = None) -> list[dict]:
    """The assembly order as contract `assembly_step` records, with access checks per step:
    no_interference (the way in is clear), tool_clearance (an opening of TOOL_OPENING_MM,
    TOOL_DEPTH_MM deep, in front of the part on the side it comes from), hand_reach (that face
    within REACH_MM of the outside of what is already built), depends_on (parts already in
    place it touches)."""
    from .fastening import part_fastenings, text_of
    from .route import path_in, text_in

    bs = bs or bodies(design)
    asm = disassemble(design, bs)
    members = [b for b in bs.values() if b.kind == "member"]
    names = {p["id"]: p.get("name", p["id"]) for p in design.get("parts", [])}
    extent = np.vstack([b.obb.corners() for b in bs.values()])
    allowed = _step_fields()
    placed: list[Body] = []
    out = []
    for i, (k, d_in) in enumerate(asm["order"]):
        part = bs[k]
        pid = k.split(":", 1)[1]
        d_from = -np.asarray(DIRECTIONS[d_in], float)  # the hands work from where the part comes from
        tool_ok, reach_ok, reach, in_way = _access(part, members + placed, d_from)
        touching = sorted(o.id.split(":", 1)[1] for o in placed if overlap(part.obb, o.obb) > -TOUCH_MM)
        step = {"order": i + 1, "part": pid, "direction": d_in, "depends_on": touching,
                "no_interference": True, "tool_clearance": tool_ok, "hand_reach": reach_ok,
                "note": f"face {reach:.0f} mm in from the outside of what is built"}
        if in_way:  # the part goes in, but the tool's opening in front of it is taken (blocked_by is for parts that cannot go in)
            step["note"] += f"; the tool's opening is taken by {', '.join(b.split(':', 1)[1] for b in in_way)}"
        moves = _clear_of(part, asm["routes"][k], extent)
        fast = part_fastenings(design, pid, bs)
        extra = {"fastenings": fast, "path_mm": path_in(part.obb.center, moves),
                 "text": f"{names.get(pid, pid)}を{text_in(moves)}、{text_of(fast, names)}"}
        step.update({key: v for key, v in extra.items() if key in allowed})
        out.append(step)
        placed.append(part)
    n = len(out)
    for j, (k, blockers) in enumerate(sorted(asm["blocked"].items())):
        out.append({"order": n + j + 1, "part": k.split(":", 1)[1], "direction": "none", "no_interference": False,
                    "tool_clearance": False, "hand_reach": False,
                    "blocked_by": [b.split(":", 1)[1] for b in blockers],
                    "note": "cannot go in along any of the six directions, nor by sliding first and then one of them"})
    return out


PATH_MARGIN_MM = 100.0  # the path starts this far outside everything (display)


def _clear_of(part: Body, moves: list[tuple[str, float]], extent: np.ndarray) -> list[tuple[str, float]]:
    """The route with its last move only as long as it takes the part this margin clear of everything."""
    pos = part.obb
    for d, dist in moves[:-1]:
        pos = pos.moved(np.asarray(DIRECTIONS[d], float) * dist)
    d, _ = moves[-1]
    v = np.asarray(DIRECTIONS[d], float)
    gap = float(np.max(extent @ v) - np.min(pos.corners() @ v)) + PATH_MARGIN_MM
    return list(moves[:-1]) + [(d, gap)]


def _step_fields() -> set[str]:
    """The assembly_step fields the contract has (new ones appear when the contract gets them)."""
    from hiraku import contract

    return set(contract.schema()["$defs"]["assembly_step"]["properties"])


def _access(part: Body, placed: list[Body], d_from: np.ndarray) -> tuple[bool, bool, float, list[str]]:
    """(tool fits, hand reaches, face depth mm, ids of what is in the tool's opening)."""
    reach_out = float(np.sum(part.obb.half * np.abs(part.obb.axes @ d_from)))
    face = part.obb.center + d_from * reach_out
    k = int(np.argmax(np.abs(d_from)))
    others = [i for i in range(3) if i != k]
    axes = np.eye(3)[[k, *others]]
    half = np.array([TOOL_DEPTH_MM / 2.0, TOOL_OPENING_MM[0] / 2.0, TOOL_OPENING_MM[1] / 2.0])
    corridor = OBB(face + d_from * (TOOL_DEPTH_MM / 2.0 + TOUCH_MM), axes, half)
    in_way = sorted(o.id for o in placed if overlap(corridor, o.obb) > TOUCH_MM)
    if placed:
        pts = np.vstack([o.obb.corners() for o in placed])
        edge = float(np.max(pts @ d_from))
        depth = max(edge - float(face @ d_from), 0.0)
    else:
        depth = 0.0
    return not in_way, depth <= REACH_MM, depth, in_way


def _reverse(direction: str) -> str:
    """The way the part moves going in, when it came out along `direction`."""
    return ("-" if direction[0] == "+" else "+") + direction[1]
