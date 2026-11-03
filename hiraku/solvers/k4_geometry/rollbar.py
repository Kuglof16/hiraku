"""The occupants' space against a roll bar, before or after it is crushed (roll-over, R4).

    from hiraku.solvers.k4_geometry import rollbar
    rollbar.occupant_spaces(design)                      # [{"occupant", "lo_mm", "hi_mm", "basis"}]
    rollbar.occupant_clearance(design)                   # as built
    rollbar.occupant_clearance(design, displacements={"LHT": [0, -150, -40], ...})   # crushed
    # -> {"clearance_mm", "member", "occupant", "pairs": [...], ...}; clearance < 0: the bar is inside

The space of each occupant is an upright box (code, from the design and the
AIST values in people.py):
- along x: the occupant part's box (the mass-equivalent box the design
  generator places on the seat)
- across y: centred on the occupant part, as wide as the larger of its box
  and the shoulder breadth of a young male 95th percentile (AIST D2)
- up z: from the seat's box top (the occupant's box bottom when there is no
  seat under it) to that plus the sitting height of a young male 95th
  percentile (AIST I1), or the occupant box top when that is higher.
The arms, the legs ahead of the box and a margin around the head are not in
it (no source for a margin is used).

Each member is a capsule between the (moved) positions of its end nodes: the
radius is d / 2 for a round tube and half the diagonal sqrt(b^2 + h^2) / 2 for
a rect tube (it covers the corners, so it is on the safe side). A member
straight between its end nodes does not see its own bending between them:
pass `curves` (the deformed points along a member, e.g. from a beam model's
sub-nodes) for that.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..k0_handcalc.vehicle import node_table
from .capsule import Capsule, capsule_box_overlap
from .obb import OBB
from .people import AIST, SOURCE as AIST_SOURCE

Record = dict[str, Any]

ROLLBAR_PREFIX = "rollbar_"  # members of the roll bar (hiraku.designgen.ladder_4w.build_rollbar)
SPACE_BASIS = ("occupant box along x; across y the wider of the occupant box and AIST D2 shoulder breadth (male 95th "
               "percentile); up from the seat top by AIST I1 sitting height (male 95th percentile). " + AIST_SOURCE)


def _box(p: Record) -> tuple[np.ndarray, np.ndarray]:
    c = np.asarray(p["box"]["center_mm"], float)
    h = np.asarray(p["box"]["size_mm"], float) / 2.0
    return c - h, c + h


def occupant_spaces(design: Record) -> list[Record]:
    """The space of each occupant (one per person, people.occupants: the trunk of a body in segments):
    {"occupant", "lo_mm", "hi_mm", "basis"} (an upright box)."""
    from .people import occupants

    parts = design.get("parts", [])
    seats = [p for p in parts if p["kind"] == "seat"]
    out = []
    for o in occupants(design):
        lo, hi = _box(o)
        c = np.asarray(o["box"]["center_mm"], float)
        under = [s for s in seats if _box(s)[0][1] < c[1] < _box(s)[1][1] and _box(s)[0][0] < c[0] < _box(s)[1][0]]
        z0 = max(_box(s)[1][2] for s in under) if under else float(lo[2])
        half_y = max(float(hi[1] - lo[1]) / 2.0, AIST["shoulder_breadth_m95"] / 2.0)
        z1 = max(z0 + AIST["sitting_height_m95"], float(hi[2]))
        out.append({"occupant": o["id"], "lo_mm": [float(lo[0]), float(c[1] - half_y), float(z0)],
                    "hi_mm": [float(hi[0]), float(c[1] + half_y), float(z1)], "basis": SPACE_BASIS})
    return out


def member_radius(section: Record) -> float:
    """Capsule radius of a tube section (mm): d / 2, or half the diagonal of b x h."""
    d = section.get("dims_mm", {})
    if "d" in d:
        return float(d["d"]) / 2.0
    if "b" in d and "h" in d:
        return math.hypot(float(d["b"]), float(d["h"])) / 2.0
    raise ValueError(f"section {section.get('shape')!r} has no d or b and h to size a capsule")


def occupant_clearance(design: Record, displacements: dict | None = None, positions: dict | None = None,
                       members: list[str] | None = None, curves: dict | None = None) -> Record:
    """Smallest clearance (mm) between the members and the occupants' spaces; negative: a member is that deep
    inside a space.

    displacements: {node id: [dx, dy, dz] mm} added to the design's node positions; positions: {node id:
    [x, y, z] mm} the moved positions (wins over displacements); curves: {member id: [[x, y, z], ...]} the
    deformed points along a member from node_a to node_b (wins over both for that member). members: the
    members to check (default: those whose id starts with rollbar_, else every member).

    Returns {"clearance_mm", "member", "occupant", "pairs": [{"member", "occupant", "clearance_mm"}] (every
    pair, smallest first), "spaces": occupant_spaces(design), "radius_mm": {member: capsule radius}}."""
    nodes = node_table(design)
    moved = {k: v.copy() for k, v in nodes.items()}
    for k, d in (displacements or {}).items():
        if k not in moved:
            raise ValueError(f"displacement given for node {k!r}, which is not in design.nodes")
        moved[k] = nodes[k] + np.asarray(d, float)
    for k, p in (positions or {}).items():
        if k not in moved:
            raise ValueError(f"position given for node {k!r}, which is not in design.nodes")
        moved[k] = np.asarray(p, float)
    all_members = {m["id"]: m for m in design.get("members", [])}
    if members is None:
        members = [k for k in all_members if k.startswith(ROLLBAR_PREFIX)] or list(all_members)
    missing = [k for k in members if k not in all_members]
    if missing:
        raise ValueError(f"no member(s) {', '.join(missing)} in the design")
    spaces = occupant_spaces(design)
    if not spaces:
        raise ValueError("the design has no occupant parts, so there is no occupant space to keep")
    boxes = {s["occupant"]: OBB((np.asarray(s["lo_mm"]) + np.asarray(s["hi_mm"])) / 2.0, np.eye(3),
                                (np.asarray(s["hi_mm"]) - np.asarray(s["lo_mm"])) / 2.0, s["occupant"]) for s in spaces}
    radius = {}
    pairs = []
    for mid in members:
        m = all_members[mid]
        r = member_radius(design["sections"][m["section"]])
        radius[mid] = r
        pts = np.asarray(curves[mid], float) if curves and mid in curves else np.vstack([moved[m["node_a"]], moved[m["node_b"]]])
        for oid, box in boxes.items():
            gap = min(-capsule_box_overlap(Capsule(a, b, r, mid), box) for a, b in zip(pts, pts[1:]))
            pairs.append({"member": mid, "occupant": oid, "clearance_mm": float(gap)})
    pairs.sort(key=lambda p: p["clearance_mm"])
    worst = pairs[0]
    return {"clearance_mm": worst["clearance_mm"], "member": worst["member"], "occupant": worst["occupant"],
            "pairs": pairs, "spaces": spaces, "radius_mm": radius}
