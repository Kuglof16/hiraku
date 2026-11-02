"""Occupant fit against body-size statistics (H3).

Source of the values (mm): percentiles of the young adults (18-29 years) in
the AIST anthropometric database 1991-92 (Makiko Kouchi and Masaaki Mochimaru,
2005: AIST Anthropometric Database, National Institute of Advanced Industrial
Science and Technology, H16PRO 287), read from the item tables at
https://www.airc.aist.go.jp/dhrt/91-92/data/list.html on 2026-11-03 (item ID
and name as in that list). Only statistics are used (no individual data).
"""

from __future__ import annotations

import numpy as np

AIST = {
    "sitting_height_m95": 985.2,  # source: I1 Sitting height, male 95th percentile
    "sitting_height_f5": 824.1,  # source: I1 Sitting height, female 5th percentile
    "shoulder_breadth_m95": 495.3,  # source: D2 Bideltoid breadth, male 95th percentile
    "hip_breadth_seated_f95": 391.0,  # source: J2 Hip breadth, sitting, feet supported, female 95th percentile
    "buttock_knee_m95": 616.4,  # source: J5 Buttock-knee length, sitting, male 95th percentile
}
SOURCE = ("AIST anthropometric database 1991-92 (Kouchi and Mochimaru 2005, AIST H16PRO 287), young adults; items I1, D2, "
          "J2, J5 read from https://www.airc.aist.go.jp/dhrt/91-92/data/list.html on 2026-11-03")


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
        seat = _seat_of(o, seats)
        seat_top = _box(seat)[1][2] if seat else lo[2]
        head = seat_top + AIST["sitting_height_m95"]
        above = [p for p in parts if p is not o and p["kind"] not in ("occupant",)
                 and _box(p)[0][2] >= seat_top and _overlaps_xy(p, o)]
        if above:
            roof = min(_box(p)[0][2] for p in above)
            out[f"head_clearance.{o['id']}"] = (roof - head, o["id"])
    for oid, (length, obstacle) in knee_space(design).items():
        out[f"knee_room.{oid}"] = (length - AIST["buttock_knee_m95"], obstacle)
    return out


KNEE_BAND_MM = 300.0  # parts this far above the seat top are at knee height (assumption)


def _seat_of(o: dict, seats: list[dict]) -> dict | None:
    return min(seats, key=lambda s: abs(s["box"]["center_mm"][1] - o["box"]["center_mm"][1])) if seats else None


def knee_space(design: dict) -> dict[str, tuple[float, str]]:
    """For each occupant: (length mm from the seat back to the nearest part ahead at knee
    height, that part's id). Occupants with nothing ahead are left out."""
    parts = design.get("parts", [])
    seats = [p for p in parts if p["kind"] == "seat"]
    out = {}
    for o in (p for p in parts if p["kind"] == "occupant"):
        lo, _ = _box(o)
        seat = _seat_of(o, seats)
        seat_top = _box(seat)[1][2] if seat else lo[2]
        seat_back = _box(seat)[0][0] if seat else lo[0]
        ahead = [p for p in parts if p is not o and p is not seat and p["kind"] not in ("occupant", "seat")
                 and _box(p)[0][0] > seat_back and _box(p)[0][2] < seat_top + KNEE_BAND_MM and _box(p)[1][2] > seat_top
                 and _overlaps_y(p, o)]
        if ahead:
            first = min(ahead, key=lambda p: _box(p)[0][0])
            out[o["id"]] = (float(_box(first)[0][0] - seat_back), first["id"])
    return out


def _overlaps_y(p, o) -> bool:
    a0, a1 = _box(p)
    b0, b1 = _box(o)
    return a0[1] < b1[1] and b0[1] < a1[1]


def _overlaps_xy(p, o) -> bool:
    a0, a1 = _box(p)
    b0, b1 = _box(o)
    return _overlaps_y(p, o) and a0[0] < b1[0] and b0[0] < a1[0]
