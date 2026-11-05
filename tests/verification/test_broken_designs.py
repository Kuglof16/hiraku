"""One design that passes, broken one way at a time: each break fails the load case it is meant to fail,
on the part it is meant to fail on, and fails exactly the load cases listed for it.

The design (fixtures/n15-v005-design.json) is version 5 of the scripted run of
data/requirements/offroad-4w-n15.yaml, the last version of that plan (no red tile, run 20261105-051354
on trial tree 20acc82). It is judged here with the assembly tool (H1 and H5 load cases) and the load
path tool (R8), the light tools that look at whether the design holds together as a thing.

Each break touches one thing (the breaks agreed with the analysis department, 11/5): only the meant load
case fails, except a free part, which also joins nothing (H1-joints).
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from hiraku.catalog.builders import BUILDERS
from hiraku.solvers.shelf.assembly import Assembly
from hiraku.solvers.shelf.load_path import LoadPath
from tests.solvers.test_builders_load_path import REQ

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "n15-v005-design.json"
OPS = {"<=": lambda v, lim: v <= lim, ">=": lambda v, lim: v >= lim, "<": lambda v, lim: v < lim,
       ">": lambda v, lim: v > lim}


def _part(d, pid):
    return next(p for p in d["parts"] if p["id"] == pid)


def _shift(d, pid, dx, dy, dz, with_bolt=True):
    p = _part(d, pid)
    b = p["box"]["center_mm"]
    p["box"]["center_mm"] = [b[0] + dx, b[1] + dy, b[2] + dz]
    if with_bolt:
        for c in p.get("connects_to", []):
            if "at_mm" in c:
                a = c["at_mm"]
                c["at_mm"] = [a[0] + dx, a[1] + dy, a[2] + dz]


def weak_frame(d):
    """The cross members' tube wall from 2.3 mm to 0.6 mm."""
    d["sections"]["rt40x50x2.3"]["dims_mm"]["t"] = 0.6


def into_the_motor(d):
    """The controller's box (not its bolt) 15 mm back along x: still in its room, 5 mm into the motor."""
    _shift(d, "controller", -15.0, 0.0, 0.0, with_bolt=False)


def bolt_off(d):
    """The controller's bolt point 20 mm up, the box where it was."""
    bolt = next(c for c in _part(d, "controller")["connects_to"] if c.get("via") == "bolt" and "at_mm" in c)
    bolt["at_mm"] = [bolt["at_mm"][0], bolt["at_mm"][1], bolt["at_mm"][2] + 20.0]


def room_too_small(d):
    """The powertrain module's room for the controller (the envelope box centred at [-1555, 0, 300])
    300 mm -> 200 mm long, shorter than the controller's 250 mm; nothing moved."""
    pt = next(m for m in d["modules"] if m["module_id"] == "powertrain")
    box = next(b for b in pt["envelope"] if [round(x) for x in b["center_mm"]] == [-1555, 0, 300])
    box["size_mm"] = [200.0] + list(box["size_mm"][1:])


def mount_taken_away(d):
    """The controller's bracket on the cross member (fit_controller_cross_S2) removed, with the controller's
    bolt to it; the controller keeps its carried_by. (A part with an empty carried_by is not a free part:
    the load path takes it as unsprung, as a wheel.)"""
    d["parts"] = [p for p in d["parts"] if p["id"] != "fit_controller_cross_S2"]
    for p in d["parts"]:
        if "connects_to" in p:
            p["connects_to"] = [c for c in p["connects_to"] if c.get("to") != "fit_controller_cross_S2"]


# break -> (the load case meant to fail, its quantity, the part named or None, every load case that fails)
BREAKS = {
    "weak frame": (weak_frame, "R8-stress", "max_utilisation", "cross_B", {"R8-stress"}),
    "into the motor": (into_the_motor, "H1-interference", "interference_count", None, {"H1-interference"}),
    "bolt off": (bolt_off, "H1-joints", "joint_gap_count", None, {"H1-joints"}),
    "room too small": (room_too_small, "H1-envelope", "envelope_outside_count", "controller", {"H1-envelope"}),
    # a free part also joins nothing: H1-joints fails with it whatever the break
    "mount taken away": (mount_taken_away, "R8-free", "free_parts", None, {"R8-free", "H1-joints"}),
}


def judge(design) -> dict[str, dict]:
    """{load case id: {quantity, value, op, limit, passed, where}} for the H1, H5 and R8 load cases."""
    cases = {}
    for test, domain in (("H1", "assembly"), ("H5", "assembly"), ("R8", "strength")):
        for lc in BUILDERS[test]({"domain": domain, "tolerance_rel": 0.05}, REQ, design):
            cases[lc["load_case_id"]] = lc
    asm = Assembly().run(design, cases["H1-interference"], [{"prediction_id": "pred-0001"}], {})
    by_q = {r["quantity"]: r for r in asm["results"]}
    out = {}
    for lc_id, lc in sorted(cases.items()):
        c = lc.get("criterion")
        if not c:
            continue
        if lc_id.startswith("R8"):
            rec = LoadPath().run(design, lc, [{"prediction_id": "pred-0001"}], {})
            r = {x["quantity"]: x for x in rec.get("results", [])}.get(c["quantity"], {})
        else:
            r = by_q.get(c["quantity"], {})
        v = r.get("value")
        out[lc_id] = {"quantity": c["quantity"], "value": v, "op": c["op"], "limit": c["limit"]["value"],
                      "passed": v is not None and OPS[c["op"]](v, c["limit"]["value"]), "where": r.get("where")}
    return out


class BrokenDesignsTest(unittest.TestCase):
    def setUp(self):
        self.base = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_the_design_passes(self):
        got = judge(self.base)
        self.assertEqual({k: v for k, v in got.items() if not v["passed"]}, {})
        for lc_id in ("H1-envelope", "H1-interference", "H1-joints", "R8-free", "R8-stress"):
            self.assertIn(lc_id, got)


def _make(name):
    def test(self):
        brk, lc_id, quantity, where, failing = BREAKS[name]
        d = copy.deepcopy(self.base)
        brk(d)
        got = judge(d)
        bad = {k: v for k, v in got.items() if not v["passed"]}
        self.assertIn(lc_id, bad, f"{name}: {lc_id} passed ({got.get(lc_id)})")
        self.assertEqual(bad[lc_id]["quantity"], quantity)
        if where is not None:
            self.assertEqual(bad[lc_id]["where"], where, bad[lc_id])
        self.assertEqual(set(bad), failing, f"{name}: {json.dumps(bad, ensure_ascii=False)}")
    test.__doc__ = f"{name}: {BREAKS[name][0].__doc__}"
    return test


for _name in BREAKS:
    setattr(BrokenDesignsTest, "test_" + _name.replace(" ", "_"), _make(_name))


if __name__ == "__main__":
    unittest.main()
