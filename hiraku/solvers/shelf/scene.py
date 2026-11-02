"""What the tools put in analysis.scene: things around the vehicle, placed from the
design and the load case (mm, road frame = the design's axes at the start).

Only the positions that matter are computed (the wall at the vehicle's front, a
step's face where the wheel meets its corner, its height); the sizes that only
make the object visible (wall thickness, how far a step runs on) are display sizes.
"""

from __future__ import annotations

import numpy as np

from ..k0_handcalc.vehicle import step_geometry
from .base import Record

WALL_THICKNESS_MM = 200.0  # display size
STEP_RUN_MM = 1000.0  # display size: how far the step's top runs on past its face


def extent(design: Record) -> tuple[np.ndarray, np.ndarray]:
    """Lower and upper corners (mm) of everything in the design: frame nodes, part boxes, wheels."""
    pts = [np.asarray(n["xyz_mm"], float) for n in design.get("nodes", [])]
    for p in design.get("parts", []):
        c = np.asarray(p["box"]["center_mm"], float)
        h = np.asarray(p["box"]["size_mm"], float) / 2.0
        pts += [c - h, c + h]
    for s in design.get("supports", []):
        c = np.asarray(s["position_mm"], float)
        r = float(s.get("radius_mm", 0.0))
        pts += [c - r, c + r]
    a = np.vstack(pts)
    return a.min(axis=0), a.max(axis=0)


def wall(design: Record) -> Record:
    """A rigid wall touching the front-most point of the vehicle."""
    lo, hi = extent(design)
    width, height = float(hi[1] - lo[1]), float(hi[2])
    return {"kind": "wall", "label": "剛体の壁", "position_mm": [float(hi[0]) + WALL_THICKNESS_MM / 2.0, 0.0, 0.75 * height],
            "size_mm": [WALL_THICKNESS_MM, 1.5 * width, 1.5 * height]}


def steps(design: Record, support_ids: list[str], height_mm: float) -> list[Record]:
    """A step of the given height under each stepping wheel, its face where the wheel meets
    the corner as it lifts off (d = sqrt(2 r h - h^2) ahead of the wheel centre)."""
    out = []
    for s in design.get("supports", []):
        if s["id"] not in support_ids:
            continue
        x, y, _ = (float(v) for v in s["position_mm"])
        d, _ = step_geometry(float(s["radius_mm"]), height_mm)
        width = 2.0 * float(s.get("width_mm") or s["radius_mm"])
        out.append({"kind": "step", "label": f"段差 {height_mm:g} mm ({s['id']})",
                    "position_mm": [x + d + STEP_RUN_MM / 2.0, y, height_mm / 2.0],
                    "size_mm": [STEP_RUN_MM, width, height_mm]})
    return out
