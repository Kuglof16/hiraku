"""The head restraints against the seated heads: for each occupied seat and each body size (people.BIG, the AIST
young adult male 95th percentile, and people.SMALL, the female 5th percentile; people.SOURCE, people.BODY_SOURCE),
how far the restraint's top is above the head and how far behind the back of the head its front face is.

The head (as the designgen kit/body.py draws it): its top at the sitting height (I1 Sitting height) above the seat's
cushion top, its box A36 Total head height tall, so its centre of gravity (the box's middle, assumption) at I1 less
half A36; its back at the seat back (the seat box's rear face; assumption of kit/body.py: the occiput over the seat
back). Each person sits where K4 seats them on the seat's slide (people.poses: the offset along x from the middle of
the travel); the seat's restraint moves with the seat, so the gap along x does not depend on the offset, the heights
do not depend on it either (the slide is level).

Only measured here; the criteria (a rear-impact standard's values) are not in this module.

    head_restraints(design) -> {occupant id: {"restraint": part id, "m95": {...}, "f5": {...}}}
"""

from __future__ import annotations

from . import people

RESTRAINT_KINDS = ("head_restraint",)


def restraint_of(design: dict, seat: dict) -> dict | None:
    """The head restraint of a seat (people.seat_back_parts), None without one."""
    return next((p for p in people.seat_back_parts(design, seat) if p["kind"] in RESTRAINT_KINDS), None)


def head(seat_back_x: float, seat_top_z: float, size: str) -> dict[str, float]:
    """The head of a person of `size` on a seat whose back is at seat_back_x and cushion top at seat_top_z:
    {"top", "cg" (z, mm), "back" (x, mm)}."""
    s = people.SIZES[size]
    top = seat_top_z + s["sitting_height"]
    return {"top": top, "cg": top - s["head_height"] / 2.0, "back": seat_back_x}


def head_restraints(design: dict, ps: dict | None = None) -> dict[str, dict]:
    """For each occupant on a seat with a head restraint: {"restraint": its id, BIG: m, SMALL: m}, each m
    {"top_margin": restraint top - head top, "cg_margin": restraint top - head's centre of gravity, "gap": back of
    the head - the restraint's front face (along x), "offset": the seat's offset on its slide} (mm). ps: people.poses'
    result when the caller has it (the offsets on the driver's slide); occupants it leaves out sit at the middle."""
    parts = design.get("parts", [])
    seats = [p for p in parts if p["kind"] == "seat"]
    if not any(p["kind"] in RESTRAINT_KINDS for p in parts):
        return {}
    if ps is None and people.seat_slide(design) > 0.0:
        ps = people.poses(design)
    out = {}
    for o in people.occupants(design):
        seat = people._seat_of(o, seats)
        if seat is None:
            continue
        r = restraint_of(design, seat)
        if r is None:
            continue
        lo, hi = people._box(seat)
        rlo, rhi = people._extent(r)
        offsets = ((ps or {}).get(o["id"]) or {}).get("offset", {})
        row = {"restraint": r["id"]}
        for size, key in ((people.BIG, "big"), (people.SMALL, "small")):
            s = float(offsets.get(key) or 0.0)
            h = head(float(lo[0]) + s, float(hi[2]), size)
            front = float(rhi[0]) + s  # the restraint moves with the seat
            row[size] = {"top_margin": float(rhi[2]) - h["top"], "cg_margin": float(rhi[2]) - h["cg"],
                         "gap": h["back"] - front, "offset": s}
        out[o["id"]] = row
    return out


MEASURES = (("headrest_top_margin", "top_margin", min), ("headrest_cg_margin", "cg_margin", min),
            ("headrest_gap", "gap", max))  # (quantity, key of head_restraints' rows, the worse of two)


def restraint_results(design: dict, ps: dict | None = None) -> dict[str, tuple[float, str]]:
    """{quantity: (value mm, the restraint's part id)}, each quantity once:
    - <q>: the worst over the occupants and both body sizes (headrest_top_margin and headrest_cg_margin the
      smallest, headrest_gap the largest);
    - <q>.<restraint id>: the worst of both body sizes at that restraint;
    - <q>_<size>.<restraint id> (size people.BIG "m95" or people.SMALL "f5"): that body size at that restraint.
    {} without head restraints."""
    rows = head_restraints(design, ps)
    out: dict[str, tuple[float, str]] = {}

    def keep(name: str, v: float, where: str, worse) -> None:
        if name not in out or worse(v, out[name][0]) != out[name][0]:
            out[name] = (float(v), where)

    for row in rows.values():
        rid = row["restraint"]
        for size in (people.BIG, people.SMALL):
            for q, key, worse in MEASURES:
                v = row[size][key]
                keep(q, v, rid, worse)
                keep(f"{q}.{rid}", v, rid, worse)
                out[f"{q}_{size}.{rid}"] = (float(v), rid)
    for rid, m in standard_measures(design).items():
        for q, (key, worse) in STANDARD.items():
            keep(q, m[key], rid, worse)
            out[f"{q}.{rid}"] = (float(m[key]), rid)
    return out


# The head restraint as the requirements Q35-Q38 measure it (UN R17 5.6.2, 5.8, 5.11, 6.5.4, 6.6.1, 6.7.1;
# 49 CFR 571.202a S3, S4.2.1-S4.2.3, as the intake dept read them), reproduced on the design's parts with these
# simplifications (each stated in the tool's assumptions):
# - R point: the large person's hip point (people.hip_point, people.BIG): the standards' R point is the design
#   H point of the 50th percentile male manikin (SAE J826), which K4 does not model; the m95 hip point is higher than
#   the 50th's by the difference of their trochanterion heights, so the height above it is on the low (strict) side.
# - The torso reference line: the seatback is upright in the design (its box is vertical: back angle 0), so the
#   distance along the torso line is the vertical distance from the R point to the restraint's top.
# - Width 65 mm below the top: the restraint is a box, so its width there is the box's width across the car.
# - The gap between the seatback and the restraint: the vertical gap between the seatback's top and the
#   restraint's bottom (0 when they overlap); the 165 mm sphere of R17 6.7.1 is not reproduced.
# - Backset: the horizontal distance from the back of the large person's head to the restraint's front face (the
#   standard's 50th percentile male head is not modelled; the head's back is at the seat back for every size here).
STANDARD = {"headrest_height_above_R": ("height_above_R", min), "headrest_width": ("width", min),
            "headrest_backset": ("backset", max), "headrest_seatback_gap": ("seatback_gap", max)}
WIDTH_BELOW_TOP_MM = 65.0  # R17 6.6.1 / 202a S4.2.2, as read by the intake dept


def standard_measures(design: dict) -> dict[str, dict[str, float]]:
    """{restraint id: {"height_above_R", "width", "backset", "seatback_gap"}} (mm) for each seat with a head
    restraint (the simplifications above)."""
    out = {}
    for seat in (p for p in design.get("parts", []) if p["kind"] == "seat"):
        r = restraint_of(design, seat)
        if r is None:
            continue
        lo, hi = people._box(seat)
        rlo, rhi = people._extent(r)
        back = next((p for p in people.seat_back_parts(design, seat) if p["kind"] not in RESTRAINT_KINDS), None)
        r_point_z = float(people.hip_point(float(lo[0]), float(hi[2]), people.BIG)[1])
        head_back = head(float(lo[0]), float(hi[2]), people.BIG)["back"]
        gap = 0.0
        if back is not None:
            gap = max(0.0, float(rlo[2]) - float(people._extent(back)[1][2]))
        out[r["id"]] = {"height_above_R": float(rhi[2]) - r_point_z, "width": float(rhi[1] - rlo[1]),
                        "backset": head_back - float(rhi[0]), "seatback_gap": gap}
    return out
