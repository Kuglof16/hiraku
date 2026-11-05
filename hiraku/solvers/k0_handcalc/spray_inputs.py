"""Lines of spray off the wheels' treads (test H6), shared by the spray_reach tool and the K0 readers of the
spray formulas (library_spray).

The tread points: on each wheel (supports of kind wheel, not steered, at rest), the arc from arc_front ahead of
the top (straight above the axle) to arc_rear behind it, cut into equal bins of at most arc_step (one point at the
middle of each bin), and lines_across points spread over the tyre's width (one at the middle of each of
lines_across equal strips). From each point a line leaves along the tangent in the direction the tread moves when
the vehicle goes forward, as seen from the vehicle: at the top it points forward (+x), behind the top it points
forward and up, ahead of the top forward and down. Angles are from the top at the wheel centre, positive toward
the rear (as the ladder_4w fender angles: hiraku.designgen.ladder_4w.fender_parts).

The defaults below are assumptions of this repository (no source): what is thrown flies straight (no gravity, no
air), forward only, ARC_STEP_DEG between points along the arc, LINES_ACROSS across the tyre, LINE_LENGTH_MM the
farthest a line is followed.

spray_arc_inputs and spray_reach_inputs read the K0 formulas' inputs off a design and a load case (the H6
builder's inputs_from).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from . import units
from .vehicle import DesignError

ARC_STEP_DEG = 5.0  # HIRAKU's choice, an assumption, no source: spacing of the tread points along the arc
LINES_ACROSS = 5  # HIRAKU's choice, an assumption, no source: tread points across the tyre's width
LINE_LENGTH_MM = 3000.0  # HIRAKU's choice, an assumption, no source: how far a line is followed
# The fender variables of the ladder_4w design (hiraku.designgen.ladder_4w VARIABLES)
FENDER_VARS = ("fenders", "fender_front_deg", "fender_rear_deg", "fender_gap", "fender_width")


@dataclass
class Line:
    support: str
    wheel_part: str | None  # the wheel's own part (a line starts inside its box)
    theta_deg: float  # where on the tread: from the top, + toward the rear
    bin: int  # which bin of the arc
    origin: np.ndarray  # mm
    direction: np.ndarray  # unit


def arc_bins(arc_front: float, arc_rear: float, step: float) -> tuple[list[float], float]:
    """The middles of the bins (deg from the top, + rear) and the bins' width: the arc from -arc_front to
    +arc_rear cut into the fewest equal bins no wider than step."""
    total = arc_front + arc_rear
    if total <= 0:
        return [], 0.0
    if step <= 0:
        raise DesignError(f"arc_step must be positive, got {step:g} deg")
    n = max(1, math.ceil(total / step - 1e-9))
    h = total / n
    return [-arc_front + (k + 0.5) * h for k in range(n)], h


def wheels(design: dict) -> list[dict]:
    """The wheels: {"support", "center" (mm), "radius" (mm), "width" (mm), "part" (wheel part id or None)}. The
    width is the support's width_mm, else the wheel part's box across y (the part of kind wheel nearest the
    support's centre)."""
    out = []
    parts = [p for p in design.get("parts", []) if p.get("kind") == "wheel"]
    for s in design.get("supports", []):
        if s.get("kind") != "wheel":
            continue
        c = np.asarray(s["position_mm"], float)
        part = min(parts, key=lambda p: float(np.linalg.norm(np.asarray(p["box"]["center_mm"], float) - c)),
                   default=None)
        width = s.get("width_mm")
        if width is None and part is not None:
            from ..k4_geometry.obb import from_box

            box = from_box(part["box"]["center_mm"], part["box"]["size_mm"], part["box"].get("rpy_deg"))
            width = 2.0 * float(np.abs(box.axes[:, 1]) @ box.half)  # the box's extent along y
        if not width:
            raise DesignError(f"wheel {s['id']}: no width_mm and no wheel part to take the tyre's width from")
        out.append({"support": s["id"], "center": c, "radius": float(s["radius_mm"]), "width": float(width),
                    "part": part["id"] if part is not None else None})
    if not out:
        raise DesignError("the design has no wheels (supports of kind wheel): nothing throws spray")
    return out


def tread_lines(design: dict, arc_front: float, arc_rear: float, step: float = ARC_STEP_DEG,
                across: int = LINES_ACROSS) -> list[Line]:
    """Every line off every wheel's tread (module docstring)."""
    if across < 1:
        raise DesignError(f"lines_across must be at least 1, got {across}")
    mids, _ = arc_bins(arc_front, arc_rear, step)
    out = []
    for w in wheels(design):
        x, y, z = w["center"]
        R, W = w["radius"], w["width"]
        for k, th in enumerate(mids):
            t = math.radians(th)
            d = np.array([math.cos(t), 0.0, math.sin(t)])
            for j in range(across):
                yy = y + W * ((j + 0.5) / across - 0.5)
                o = np.array([x - R * math.sin(t), yy, z + R * math.cos(t)])
                out.append(Line(w["support"], w["part"], th, k, o, d))
    return out


# ---------------------------------------------------------------- the K0 readers


def _param(load_case: dict, name: str, unit: str, default: float | None) -> float:
    p = load_case.get("params") or {}
    if name in p:
        return units.to_unit(p[name], unit, name)
    if default is None:
        raise DesignError(f"the load case has no {name}")
    return default


REACH_DEG = 90.0  # the whole tread above the axle: what reaches the occupants is counted from all of it


def sampling(load_case: dict) -> dict:
    """The load case's arcs and sampling: arc_front, arc_rear (the borrowed rule's arc, for the uncovered arc),
    reach_front, reach_rear (the tread counted for lines that reach an occupant, default REACH_DEG: all of it above
    the axle), arc_step (deg), lines_across, line_length (mm)."""
    return {"arc_front": _param(load_case, "arc_front", "deg", None),
            "arc_rear": _param(load_case, "arc_rear", "deg", None),
            "reach_front": _param(load_case, "reach_front", "deg", REACH_DEG),
            "reach_rear": _param(load_case, "reach_rear", "deg", REACH_DEG),
            "arc_step": _param(load_case, "arc_step", "deg", ARC_STEP_DEG),
            "lines_across": int(round(_param(load_case, "lines_across", "1", float(LINES_ACROSS)))),
            "line_length": _param(load_case, "line_length", "mm", LINE_LENGTH_MM)}


def fender(design: dict) -> dict | None:
    """The fender as the design's variables give it (ladder_4w: fenders, fender_front_deg, fender_rear_deg,
    fender_gap, fender_width, in deg and mm), None when fenders is 0 or absent. A design with parts of kind fender
    but without those variables cannot be read (DesignError)."""
    v = design.get("variables") or {}
    on = v.get("fenders")
    has_parts = any(p.get("kind") == "fender" for p in design.get("parts", []))
    if on is None or units.to_unit(on, "1", "fenders") == 0:
        if has_parts:
            raise DesignError("the design has parts of kind fender but no fenders variable: the fender's angles "
                              "cannot be read as numbers")
        return None
    missing = [k for k in FENDER_VARS if k not in v]
    if missing:
        raise DesignError(f"fenders is on but the design lacks {', '.join(missing)}")
    return {"front": units.to_unit(v["fender_front_deg"], "deg", "fender_front_deg"),
            "rear": units.to_unit(v["fender_rear_deg"], "deg", "fender_rear_deg"),
            "gap": units.to_unit(v["fender_gap"], "mm", "fender_gap"),
            "width": units.to_unit(v["fender_width"], "mm", "fender_width")}


def spray_arc_inputs(design: dict, load_case: dict) -> dict:
    """spray.uncovered_arc's inputs: a_f, a_r (the load case's arc), f, r (the fender's angles, 0 without one),
    R (the smallest wheel radius: the largest gap angle) and g (the fender's gap over the tyre, 0 without one)."""
    s = sampling(load_case)
    f = fender(design)
    R = min(w["radius"] for w in wheels(design))
    return {"a_f": units.quantity(s["arc_front"], "deg"), "a_r": units.quantity(s["arc_rear"], "deg"),
            "f": units.quantity(f["front"] if f else 0.0, "deg"), "r": units.quantity(f["rear"] if f else 0.0, "deg"),
            "R": units.quantity(R, "mm"), "g": units.quantity(f["gap"] if f else 0.0, "mm")}


def spray_reach_inputs(design: dict, load_case: dict) -> dict:
    """spray.reaching_lines' inputs, counted on a simpler scene than the tool's: the occupants' body-part boxes
    (the load case's loading) and the fender as the design's variables give it (a cylinder of radius R + gap over
    the angles from fender_front_deg ahead of the top to fender_rear_deg behind, fender_width wide); no other part,
    no member and no ground. n_aim: lines that meet an occupant's box within line_length; n_cover: those of them
    that meet the fender's cylinder first (a tangent line leaves the cylinder sqrt((R + g)^2 - R^2) from its start,
    at the angle theta - acos(R / (R + g)) from the top)."""
    from hiraku.contract.loading import loading_of, on_board

    from ..k4_geometry.obb import from_box, ray_box

    s = sampling(load_case)
    try:
        d = on_board(design, loading_of(load_case))
    except ValueError as e:
        raise DesignError(str(e)) from None
    occ = [from_box(p["box"]["center_mm"], p["box"]["size_mm"], p["box"].get("rpy_deg"), p["id"])
           for p in d.get("parts", []) if p.get("kind") == "occupant"]
    f = fender(design)
    centre = {w["support"]: w for w in wheels(design)}
    n_aim = n_cover = 0
    for line in tread_lines(design, s["reach_front"], s["reach_rear"], s["arc_step"], s["lines_across"]):
        hits = [h[0] for h in (ray_box(line.origin, line.direction, b) for b in occ)
                if h is not None and 0.0 <= h[0] <= s["line_length"]]
        if not hits:
            continue
        n_aim += 1
        if f is None:
            continue
        w = centre[line.support]
        R = w["radius"]
        t_c = math.sqrt((R + f["gap"]) ** 2 - R * R)
        at = line.theta_deg - math.degrees(math.acos(R / (R + f["gap"])))
        across = abs(line.origin[1] - w["center"][1]) <= f["width"] / 2.0
        if -f["front"] <= at <= f["rear"] and across and t_c < min(hits):
            n_cover += 1
    return {"n_aim": units.quantity(float(n_aim), "1"), "n_cover": units.quantity(float(n_cover), "1")}
