"""A design with the parts a vehicle people ride in is expected to have: body panels, split
seats on brackets, a steering column bracket and a roll cage (frame members of part
"rollbar"). It is data/designs/offroad-4w-rollbar.json with boxes added in the shapes the
analysis department gave for these parts (kind body / seat / bracket; a panel is a box whose
thin side is its thickness, turned with rpy_deg). The places are picked by hand to sit around
the vehicle for the screen's tests and pictures; they are not computed and not a design.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from hiraku.webserve.server import SAMPLE_EVENTS

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "data" / "designs" / "offroad-4w-rollbar.json"
RUN_ID = "20261103-120000-body-look"


def _box(pid, name, kind, center, size, rpy=None, mass=1.0):
    box = {"center_mm": list(center), "size_mm": list(size)}
    if rpy is not None:
        box["rpy_deg"] = list(rpy)
    return {"id": pid, "name": name, "kind": kind, "box": box, "mass_kg": mass}


# id -> (kind, centre, size, rpy). Panels are 1.2 mm sheet.
ADDED = [
    _box("hood", "ボンネット", "body", (220, 0, 716), (380, 640, 1.2), (0, 8, 0), 3.0),
    _box("fender_FL", "左前の泥よけ", "body", (0, 560, 640), (700, 240, 1.2), None, 1.5),
    _box("fender_FR", "右前の泥よけ", "body", (0, -560, 640), (700, 240, 1.2), None, 1.5),
    _box("side_panel_L", "左の横の板", "body", (-725, 615, 590), (900, 1.2, 300), None, 2.5),
    _box("side_panel_R", "右の横の板", "body", (-725, -615, 590), (900, 1.2, 300), None, 2.5),
    _box("bulkhead", "座席の後ろの仕切り", "body", (-1050, 0, 640), (2.0, 1100, 400), None, 7.0),
]
for side, y in (("R", -300), ("L", 300)):
    ADDED += [
        _box(f"seat_{side}_cushion", "座面", "seat", (-700, y, 520), (460, 460, 100), None, 6.0),
        _box(f"seat_{side}_back", "背もたれ", "seat", (-945, y, 760), (90, 460, 520), (0, -12, 0), 5.0),
        _box(f"bracket_seat_{side}_F", "座席の脚 (前)", "bracket", (-560, y, 460), (40, 420, 20), None, 0.6),
        _box(f"bracket_seat_{side}_B", "座席の脚 (後ろ)", "bracket", (-840, y, 460), (40, 420, 20), None, 0.6),
    ]
ADDED += [
    _box("bracket_column", "ステアリングコラムの取付", "bracket", (-240, -300, 1062), (80, 140, 5), (0, 35, 0), 0.4),
    _box("bracket_column_stay", "コラムの取付の柱", "bracket", (-215, -300, 870), (30, 30, 360), None, 0.5),
]


def body_design() -> dict:
    design = json.loads(BASE.read_text(encoding="utf-8"))
    design = copy.deepcopy(design)
    # the one-box seats give way to the backrest and cushion boxes
    design["parts"] = [p for p in design["parts"] if p["id"] not in ("seat_R", "seat_L")] + copy.deepcopy(ADDED)
    return design


def body_events(design: dict | None = None) -> list[dict]:
    """run.started and design.created of the sample run, with this design."""
    src = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    return [src[0], {**src[1], "data": design or body_design()}]
