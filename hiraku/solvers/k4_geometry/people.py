"""Occupant fit against body-size statistics (H3).

Values (mm) are the AIST anthropometric database 1991-92 statistics as quoted
in the requirements catalog (H3 row): the catalog marks stature, weight and
sitting height as checked there; the other values are not checked in this
repository. Only statistics are used (no individual data).
"""

from __future__ import annotations

import numpy as np

AIST = {
    "sitting_height_m95": 985.0,  # young male 95th percentile (checked in the catalog)
    "sitting_height_f5": 824.0,  # young female 5th percentile (checked in the catalog)
    "shoulder_breadth_m95": 495.0,  # not checked here
    "hip_breadth_seated_f95": 391.0,  # not checked here
    "buttock_knee_m95": 616.0,  # not checked here
}
SOURCE = ("AIST anthropometric database 1991-92 (Kouchi & Mochimaru 2005, AIST H16PRO 287), statistics as quoted in the "
          "requirements catalog; sitting height checked there, the others not checked in this repository")


def _box(p):
    c = np.asarray(p["box"]["center_mm"], float)
    h = np.asarray(p["box"]["size_mm"], float) / 2.0
    return c - h, c + h


def fit(design: dict) -> dict:
    """Margins (mm, negative = does not fit) for the occupants and seats in the design."""
    parts = design.get("parts", [])
    occ = [p for p in parts if p["kind"] == "occupant"]
    seats = [p for p in parts if p["kind"] == "seat"]
    out: dict[str, tuple[float, str]] = {}
    if not occ:
        return out
    ys = [p["box"]["center_mm"][1] + s * p["box"]["size_mm"][1] / 2 for p in parts for s in (-1, 1)]
    ys += [n["xyz_mm"][1] for n in design.get("nodes", [])]
    width = max(ys) - min(ys)
    out["shoulder_room"] = (width - len(occ) * AIST["shoulder_breadth_m95"], "vehicle")
    for seat in seats:
        out[f"hip_room.{seat['id']}"] = (seat["box"]["size_mm"][1] - AIST["hip_breadth_seated_f95"], seat["id"])
    for o in occ:
        lo, hi = _box(o)
        seat = min(seats, key=lambda s: abs(s["box"]["center_mm"][1] - o["box"]["center_mm"][1])) if seats else None
        seat_top = _box(seat)[1][2] if seat else lo[2]
        seat_back = _box(seat)[0][0] if seat else lo[0]
        head = seat_top + AIST["sitting_height_m95"]
        above = [p for p in parts if p is not o and p["kind"] not in ("occupant",)
                 and _box(p)[0][2] >= seat_top and _overlaps_xy(p, o)]
        if above:
            roof = min(_box(p)[0][2] for p in above)
            out[f"head_clearance.{o['id']}"] = (roof - head, o["id"])
        knee_x = seat_back + AIST["buttock_knee_m95"]
        ahead = [p for p in parts if p is not o and p is not seat and p["kind"] not in ("occupant", "seat")
                 and _box(p)[0][0] > seat_back and _box(p)[0][2] < seat_top + 300.0 and _box(p)[1][2] > seat_top
                 and _overlaps_y(p, o)]
        if ahead:
            front = min(_box(p)[0][0] for p in ahead)
            out[f"knee_room.{o['id']}"] = (front - knee_x, min(ahead, key=lambda p: _box(p)[0][0])["id"])
    return out


def _overlaps_y(p, o) -> bool:
    a0, a1 = _box(p)
    b0, b1 = _box(o)
    return a0[1] < b1[1] and b0[1] < a1[1]


def _overlaps_xy(p, o) -> bool:
    a0, a1 = _box(p)
    b0, b1 = _box(o)
    return _overlaps_y(p, o) and a0[0] < b1[0] and b0[0] < a1[0]
