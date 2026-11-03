"""Long thin parts (shafts, rods, columns) between two known points, and the points
themselves for K4 (bodies(design, ends=link_ends)).

    p = put_link(lay, Item("tie_rod_FL", "前左のタイロッド", "tie_rod", (0, 0, 0), 0.6), a, b, radius=8.0)

The part's box runs from a to b and a radius beyond each end, 2 x radius across; when
a-b lies along x, y or z the box is not turned, otherwise it carries rpy_deg (yaw, then
pitch). K4 makes the capsule of such a box (capsule.from_obb) with its axis ends at a
and b, the same capsule link_ends gives from the joints.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

import numpy as np

from hiraku.designgen.kit.package import Item, Layout

Record = dict[str, Any]
AXIS_TOL = 1e-9


def link_box(a, b, radius: float) -> tuple[list[float], list[float], list[float] | None]:
    """(centre, size, rpy_deg or None) of the box of a link from a to b."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = b - a
    length = float(np.linalg.norm(d))
    if length <= 0:
        raise ValueError("a link needs two different end points")
    centre = ((a + b) / 2.0).tolist()
    along = [i for i in range(3) if abs(d[i]) > AXIS_TOL * length]
    if len(along) == 1:
        size = [2 * radius] * 3
        size[along[0]] = length + 2 * radius
        return centre, size, None
    yaw = math.degrees(math.atan2(d[1], d[0]))
    pitch = math.degrees(math.atan2(-d[2], math.hypot(d[0], d[1])))
    return centre, [length + 2 * radius, 2 * radius, 2 * radius], [0.0, pitch, yaw]


def put_link(lay: Layout, item: Item, a, b, radius: float, carried_by: list[str] | str | None = "auto") -> Record:
    """Place a link part from a to b (item.size is replaced by the link's box)."""
    centre, size, rpy = link_box(a, b, radius)
    p = lay.put(replace(item, size=tuple(size)), centre[0], centre[1], centre[2], carried_by)
    if rpy is not None:
        p["box"]["rpy_deg"] = rpy
    return p


def link_ends(design: Record, part: Record):
    """The two joints of a long thin part from what fixes them, or None (K4 then takes the box):
    a driveshaft from the inner joint (driveshaft_inner) to its wheel's centre, a tie rod from
    tie_inner to tie_outer and the rack between the front tie_inner points (K2 hard points of the
    nearest support), a steering column from the steering wheel's centre down to its lower end and
    the intermediate shaft from there to the rack's line (steering.column_ends, shaft_ends)."""
    from hiraku.designgen.kit import drivetrain, steering

    kind = part["kind"]
    if kind == "driveshaft":
        sid = _nearest_support(design, part)
        return drivetrain.driveshaft_joints(design, sid)
    if kind == "tie_rod":
        hp = _hardpoints(design, _nearest_support(design, part))
        return hp["tie_inner"], hp["tie_outer"]
    if kind == "steering_rack":
        return steering.rack_ends(design)
    if kind == "steering_column":
        return steering.column_ends(design, part)
    if kind == "steering_shaft":
        return steering.shaft_ends(design, part)
    return None


def _nearest_support(design: Record, part: Record) -> str:
    c = np.asarray(part["box"]["center_mm"], float)
    return min(design["supports"], key=lambda s: float(np.linalg.norm(np.asarray(s["position_mm"], float) - c)))["id"]


def _hardpoints(design: Record, sid: str) -> Record:
    from hiraku.solvers.k2_vehicle.model import hardpoints_of

    return hardpoints_of(design, sid)
