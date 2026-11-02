"""Ways in and out for a part: straight along one of the six directions, or, when
none of those is clear, two moves (slide along one direction, then out along
another), found by trying slide lengths on a grid.

A route is a list of moves (direction name, distance mm) taking the part OUT
from where it is fitted; path_in() turns it into the centre's path going in,
from outside to the fitted position.
"""

from __future__ import annotations

import numpy as np

from .assembly import DIRECTIONS, blocker  # assembly imports this module inside its functions
from .model import Body

SLIDE_STEP_MM = 25.0  # slide lengths tried for two-move routes
PREFERRED = ("+z", "+y", "-y", "+x", "-x", "-z")  # out upward first (assembly from above), then sideways
DIRECTION_JA = {"-z": "上から下ろし", "+z": "下から上げ", "-x": "前から後ろへ入れ", "+x": "後ろから前へ入れ",
                "-y": "左から右へ入れ", "+y": "右から左へ入れ"}  # keyed by the move going IN
SLIDE_JA = {"-z": "下へずらし", "+z": "上へずらし", "-x": "後ろへずらし", "+x": "前へずらし", "-y": "右へずらし",
            "+y": "左へずらし"}


def straight(part: Body, others: list[Body], travel: float) -> list[str]:
    """The directions it can leave along in one move, in the preferred order."""
    return [d for d in PREFERRED if blocker(part, others, DIRECTIONS[d], travel) is None]


def two_moves(part: Body, others: list[Body], travel: float) -> list[tuple[str, float]] | None:
    """The shortest slide then straight-out route, or None. The slide must itself be clear."""
    best = None
    for d1 in PREFERRED:
        v1 = np.asarray(DIRECTIONS[d1], float)
        t = SLIDE_STEP_MM
        while t <= travel and blocker(part, others, v1, t) is None:
            moved = Body(part.id, part.obb.moved(v1 * t), part.kind, part.support, part.nodes)
            for d2 in PREFERRED:
                if d2[1] == d1[1]:
                    continue
                if blocker(moved, others, DIRECTIONS[d2], travel) is None:
                    if best is None or t < best[0][1]:
                        best = [(d1, t), (d2, travel)]
                    break
            if best is not None and best[0][1] <= t:
                break
            t += SLIDE_STEP_MM
    return best


def path_in(center, moves: list[tuple[str, float]]) -> list[list[float]]:
    """The centre's path going in (outside first, fitted position last) for a route out."""
    pts = [np.asarray(center, float)]
    for d, dist in moves:
        pts.append(pts[-1] + np.asarray(DIRECTIONS[d], float) * dist)
    return [[float(v) for v in p] for p in reversed(pts)]


def reverse(direction: str) -> str:
    return ("-" if direction[0] == "+" else "+") + direction[1]


def text_in(moves: list[tuple[str, float]]) -> str:
    """How it goes in, in Japanese: the moves in the order they are made going in."""
    going_in = [reverse(d) for d, _ in reversed(moves)]
    return "、".join([DIRECTION_JA[going_in[0]]] + [SLIDE_JA[d] for d in going_in[1:]])
