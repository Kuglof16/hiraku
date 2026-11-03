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


def _box(pid, name, kind, center, size, rpy=None, mass=1.0, material=None, finish=None):
    box = {"center_mm": list(center), "size_mm": list(size)}
    if rpy is not None:
        box["rpy_deg"] = list(rpy)
    part = {"id": pid, "name": name, "kind": kind, "box": box, "mass_kg": mass}
    if material is not None:
        part["material"] = material  # a key into design.materials (docs/contract.md)
    if finish is not None:
        part["finish"] = finish  # painted / powder_coat / bare / anodized / galvanized / molded
    return part


# The materials the added parts name, in the contract's shape. The numbers are only there
# because the schema asks for them: rough handbook-like values for the screen's tests, not
# checked against any standard and not used for any analysis (the screen reads the names,
# and the numbers only to sort a material whose name says nothing).
_NOT_CHECKED = "fixture for the screen's tests (tests/web/body_fixture.py); values not checked against any source, not for analysis"


def _material(name, E, nu, rho, yld):
    return {"name": name, "E_MPa": E, "nu": nu, "density_kg_m3": rho, "yield_MPa": yld, "source": _NOT_CHECKED, "verified": False}


MATERIALS = {
    "spcc": _material("JIS G 3141 SPCC (cold rolled steel sheet)", 205000.0, 0.3, 7850.0, 180.0),
    "ss400": _material("JIS G 3101 SS400 (rolled steel for general structure)", 205000.0, 0.3, 7850.0, 245.0),
    "a5052": _material("JIS H 4000 A5052 (aluminium alloy sheet)", 70000.0, 0.33, 2680.0, 90.0),
    "pp": _material("PP (polypropylene, injection moulded)", 1500.0, 0.42, 905.0, 30.0),
    "epdm": _material("EPDM rubber sheet", 10.0, 0.49, 1200.0, 8.0),
    # a name that says nothing: the numbers (aluminium-like) sort it
    "stock_77": _material("sheet stock 77", 71000.0, 0.33, 2700.0, 100.0),
}

# id -> (kind, centre, size, rpy). Panels are 1.2 mm sheet.
ADDED = [
    _box("hood", "ボンネット", "body", (220, 0, 716), (380, 640, 1.2), (0, 8, 0), 3.0, "spcc", "painted"),
    _box("fender_FL", "左前の泥よけ", "body", (0, 560, 640), (700, 240, 1.2), None, 1.5, "pp", "molded"),
    _box("fender_FR", "右前の泥よけ", "body", (0, -560, 640), (700, 240, 1.2), None, 1.5, "pp", "molded"),
    _box("side_panel_L", "左の横の板", "body", (-725, 615, 590), (900, 1.2, 300), None, 2.5, "spcc", "painted"),
    _box("side_panel_R", "右の横の板", "body", (-725, -615, 590), (900, 1.2, 300), None, 2.5, "spcc", "painted"),
    _box("bulkhead", "座席の後ろの仕切り", "body", (-1050, 0, 640), (2.0, 1100, 400), None, 7.0, "spcc", "painted"),
]
for side, y in (("R", -300), ("L", 300)):
    ADDED += [
        # no material or finish: the kind (seat) decides
        _box(f"seat_{side}_cushion", "座面", "seat", (-700, y, 520), (460, 460, 100), None, 6.0),
        _box(f"seat_{side}_back", "背もたれ", "seat", (-945, y, 760), (90, 460, 520), (0, -12, 0), 5.0),
        _box(f"bracket_seat_{side}_F", "座席の脚 (前)", "bracket", (-560, y, 460), (40, 420, 20), None, 0.6, "ss400", "powder_coat"),
        _box(f"bracket_seat_{side}_B", "座席の脚 (後ろ)", "bracket", (-840, y, 460), (40, 420, 20), None, 0.6, "ss400", "powder_coat"),
    ]
ADDED += [
    _box("bracket_column", "ステアリングコラムの取付", "bracket", (-240, -300, 1062), (80, 140, 5), (0, 35, 0), 0.4, "ss400", "painted"),
    _box("bracket_column_stay", "コラムの取付の柱", "bracket", (-215, -300, 870), (30, 30, 360), None, 0.5, "ss400", "painted"),
]
# parts of the base design given a material and finish here
SET = {"floor": ("a5052", "bare")}


def body_design() -> dict:
    design = copy.deepcopy(json.loads(BASE.read_text(encoding="utf-8")))
    # the one-box seats give way to the backrest and cushion boxes
    design["parts"] = [p for p in design["parts"] if p["id"] not in ("seat_R", "seat_L")] + copy.deepcopy(ADDED)
    for p in design["parts"]:
        if p["id"] in SET:
            p["material"], p["finish"] = SET[p["id"]]
    design["materials"] = {**design["materials"], **copy.deepcopy(MATERIALS)}
    return design


def schema_has_part_material() -> bool:
    """Whether this branch's contract has part.material and part.finish (lead commit a4ad2f6)."""
    from hiraku import contract

    props = contract.schema()["$defs"]["part"]["properties"]
    return "material" in props and "finish" in props


def body_events(design: dict | None = None) -> list[dict]:
    """run.started and design.created of the sample run, with this design."""
    src = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    return [src[0], {**src[1], "data": design or body_design()}]
