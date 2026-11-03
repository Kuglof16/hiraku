"""A design with the parts a vehicle people ride in is expected to have: body panels, split
seats on brackets, a steering column bracket and a roll cage (frame members of part
"rollbar"). It is data/designs/offroad-4w-v1.json (which since 92a97c6 has the roll bar,
the front bulkhead, the column bracket and one-box seats) with boxes added in the shapes the
analysis department gave for these parts (kind body / seat / bracket; a panel is a box whose
thin side is its thickness, turned with rpy_deg). Parts v1 already has (bulkhead,
bracket_column, floor, ...) are used as they are and only given a material and finish; the
one-box seats are split into a cushion and a backrest inside the seat's own box. The panels'
places are picked by hand to sit around v1 for the screen's tests and pictures, clear of its
parts (tests/web/test_body_look_browser.py checks this with the analysis department's
interference check); they are not a design.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from hiraku.webserve.server import SAMPLE_EVENTS

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "data" / "designs" / "offroad-4w-v1.json"
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

# Panels are 1.2 mm sheet. Places against v1's boxes: the hood lies on front_body (top at
# z 710) tipped 8 degrees; the fenders over the front wheels (top at z 600); the side panels
# between the front wheels (rear edge x -300) and the seat belts (x -1320).
ADDED = [
    _box("hood", "ボンネット", "body", (220, 0, 740), (380, 640, 1.2), (0, 8, 0), 3.0, "spcc", "painted"),
    _box("fender_FL", "左前の泥よけ", "body", (0, 560, 640), (700, 240, 1.2), None, 1.5, "pp", "molded"),
    _box("fender_FR", "右前の泥よけ", "body", (0, -560, 640), (700, 240, 1.2), None, 1.5, "pp", "molded"),
    _box("side_panel_L", "左の横の板", "body", (-800, 615, 590), (900, 1.2, 300), None, 2.5, "spcc", "painted"),
    _box("side_panel_R", "右の横の板", "body", (-800, -615, 590), (900, 1.2, 300), None, 2.5, "spcc", "painted"),
]
# parts of the base design given a material and finish here (v1 has them already)
SET = {
    "floor": ("a5052", "bare"),
    "bulkhead": ("spcc", "painted"),
    "bracket_column": ("ss400", "painted"),
}
# v1's one-box seats, split into these pieces inside the seat's box
SPLIT_SEATS = ("seat_R", "seat_L")
SEAT_BRACKET_MM = 20.0  # the seat legs' height, from the seat box's bottom
SEAT_CUSHION_MM = 100.0
SEAT_BACK_MM = 90.0  # backrest thickness along x, at the seat box's rear


def _split_seat(seat: dict) -> list[dict]:
    """A cushion, a backrest and two legs that fill a one-box seat's box (axis-aligned, centre
    and size in mm): legs on its bottom, the cushion on the legs in front of the backrest, the
    backrest at its rear from the legs to its top."""
    side = seat["id"].split("_", 1)[1]
    (cx, cy, cz), (sx, sy, sz) = seat["box"]["center_mm"], seat["box"]["size_mm"]
    x0, x1, z0, z1 = cx - sx / 2, cx + sx / 2, cz - sz / 2, cz + sz / 2
    zl = z0 + SEAT_BRACKET_MM  # top of the legs
    cush_x0 = x0 + SEAT_BACK_MM
    leg = (40.0, sy - 40.0, SEAT_BRACKET_MM)
    return [
        # no material or finish: the kind (seat) decides
        _box(f"seat_{side}_cushion", "座面", "seat", ((cush_x0 + x1) / 2, cy, zl + SEAT_CUSHION_MM / 2), (x1 - cush_x0, sy, SEAT_CUSHION_MM), None, 6.0),
        _box(f"seat_{side}_back", "背もたれ", "seat", (x0 + SEAT_BACK_MM / 2, cy, (zl + z1) / 2), (SEAT_BACK_MM, sy, z1 - zl), None, 5.0),
        _box(f"bracket_seat_{side}_F", "座席の脚 (前)", "bracket", (x1 - 60.0, cy, z0 + SEAT_BRACKET_MM / 2), leg, None, 0.6, "ss400", "powder_coat"),
        _box(f"bracket_seat_{side}_B", "座席の脚 (後ろ)", "bracket", (x0 + 60.0, cy, z0 + SEAT_BRACKET_MM / 2), leg, None, 0.6, "ss400", "powder_coat"),
    ]


def body_design() -> dict:
    design = copy.deepcopy(json.loads(BASE.read_text(encoding="utf-8")))
    ids = {p["id"] for p in design["parts"]}
    clash = ids & {p["id"] for p in ADDED}
    assert not clash, f"{BASE.name} already has {sorted(clash)}: use them through SET instead of adding"
    seats = [p for p in design["parts"] if p["id"] in SPLIT_SEATS]
    pieces = [q for s in seats for q in _split_seat(s)]
    design["parts"] = [p for p in design["parts"] if p["id"] not in SPLIT_SEATS] + copy.deepcopy(ADDED) + pieces
    for p in design["parts"]:
        if p["id"] in SET:
            p["material"], p["finish"] = SET[p["id"]]
    design["materials"] = {**design["materials"], **copy.deepcopy(MATERIALS)}
    return design


def added_ids() -> set[str]:
    """The ids body_design() adds to the base design."""
    base = {p["id"] for p in json.loads(BASE.read_text(encoding="utf-8"))["parts"]}
    return {p["id"] for p in body_design()["parts"]} - base


def schema_has_part_material() -> bool:
    """Whether this branch's contract has part.material and part.finish (lead commit a4ad2f6)."""
    from hiraku import contract

    props = contract.schema()["$defs"]["part"]["properties"]
    return "material" in props and "finish" in props


def body_events(design: dict | None = None) -> list[dict]:
    """run.started and design.created of the sample run, with this design."""
    src = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    return [src[0], {**src[1], "data": design or body_design()}]
