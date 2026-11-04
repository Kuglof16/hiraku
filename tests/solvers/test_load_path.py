"""load_path: the parts as rigid bodies on their contacts (hiraku.solvers.shelf.load_path)."""

from __future__ import annotations

import copy
import json
import math
import unittest
from pathlib import Path

import numpy as np

from hiraku import contract
from hiraku.solvers.k4_geometry import model as k4
from hiraku.solvers.shelf import LoadPath
from hiraku.solvers.shelf.load_path import (TOUCH_MM, analyse_load_path, contact_groups, contact_stiffness,
                                            find_contacts, load_path_known_answers)

ROOT = Path(__file__).resolve().parents[2]
# version 1 as generated before designgen joined every part in shape (kit/joints.py): its pedals, floors, battery
# and seats float, the cases these tests are about (tests/solvers/data/offroad-4w-v1-unjoined.json)
V1 = json.loads((ROOT / "tests" / "solvers" / "data" / "offroad-4w-v1-unjoined.json").read_text(encoding="utf-8"))
JOINED = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
PRED = [{"prediction_id": "pred-0001"}]
VALUES = dict(hand_force=300.0, foot_force=500.0, accel_vertical=1.0, accel_longitudinal=0.9, accel_lateral=0.7,
              occupant_mass=76.6, occupant_force_lateral=526.0, occupant_force_longitudinal=676.0, push_force=725.0,
              hand_part=None, foot_parts=None)


def quantity(out, name):
    return next((r for r in out["results"] if r["quantity"] == name), None)


def case(kind: str, **params) -> dict:
    p = {"load_kind": kind, "loading": "two_cargo"}
    p.update(params)
    return {"load_case_id": f"R7-{kind}", "test_id": "R7", "domain": "strength", "title": kind, "params": p,
            "tolerance_rel": 0.3}


def touch_nearest(design: dict, pid: str, target: str) -> dict:
    """A copy of the design with part `pid`'s box moved straight toward `target` (a body id of k4_geometry) until
    the two touch (the gap by the same separating-axis test the tool uses)."""
    d = copy.deepcopy(design)
    part = next(p for p in d["parts"] if p["id"] == pid)
    bs = k4.bodies(d)
    t = bs[target].obb
    c = np.asarray(part["box"]["center_mm"], float)
    goal = t.center + np.clip(t.axes @ (c - t.center), -t.half, t.half) @ t.axes  # nearest point of the target
    u = (goal - c) / float(np.linalg.norm(goal - c))
    for _ in range(2000):
        if k4.shape_overlap(k4.bodies(d)[f"part:{pid}"], bs[target]) >= -0.5:
            return d
        part["box"]["center_mm"] = [float(v) for v in np.asarray(part["box"]["center_mm"]) + u * 0.5]
    raise AssertionError(f"{pid} never reached {target}")


class KnownAnswersTest(unittest.TestCase):
    def test_all_pass(self):
        checks = load_path_known_answers()
        names = {c["quantity"] for c in checks}
        self.assertTrue({"box_deflection", "top_deflection", "free_parts", "post1_load", "post2_load"} <= names)
        for c in checks:
            self.assertTrue(c["pass"], c)
        spec = LoadPath().spec()
        self.assertTrue(spec["usable"])
        self.assertEqual(contract.errors("tool_spec", spec), [])
        self.assertEqual(spec["stage"], 1)
        self.assertNotIn("applies_to", spec)

    def test_contact_stiffness_grows_with_the_area(self):
        k1, r1, n1 = contact_stiffness(100.0)
        k2, r2, n2 = contact_stiffness(10000.0)
        self.assertEqual((n1, n2), (1, 4))
        self.assertAlmostEqual(k2 / k1, 4.0)
        self.assertGreater(r2 / k2, r1 / k1)


class DemoV1Test(unittest.TestCase):
    """The demo's version 1 at the default touch (2 mm)."""

    @classmethod
    def setUpClass(cls):
        cls.contacts = find_contacts(V1)
        cls.held, cls.free = contact_groups(V1, cls.contacts)
        cls.out = {k: analyse_load_path(V1, k, VALUES) for k in ("hand", "foot", "inertia", "push")}

    def test_free_parts_by_graph_and_by_rigid_body_check_agree(self):
        for kind, out in self.out.items():
            self.assertEqual(sorted(self.free), out["free"], kind)
            self.assertEqual(quantity(out, "free_parts")["value"], len(self.free))
            for pid in self.free:
                self.assertEqual(quantity(out, f"free_part.{pid}")["where"], pid)

    def test_which_parts_float(self):
        # the floor panels sit 10 mm over the rails, the battery 5 mm under the cross members, the pedals 30-39 mm
        # from anything: all free at 2 mm
        for pid in ("floor", "floor_front", "battery", "pedal_brake", "pedal_accel", "seat_L", "seat_R", "deck"):
            self.assertIn(pid, self.free)
        # the steering column reaches the dash through bracket_column (and the shaft and rack): held
        for pid in ("steering_column", "steering_wheel", "bracket_column", "steering_shaft", "steering_rack"):
            self.assertIn(pid, self.held)

    def test_hand_is_held_and_small(self):
        out = self.out["hand"]
        g = quantity(out, "grip_deflection")
        self.assertEqual(g["where"], "steering_wheel")
        self.assertTrue(0.0 < g["value"] <= 5.0, g)
        for d in ("fore_aft", "vertical", "turning"):
            self.assertLessEqual(quantity(out, f"grip_deflection.{d}")["value"], g["value"])

    def test_floating_pedals_give_no_deflection(self):
        out = self.out["foot"]
        self.assertIsNone(quantity(out, "pedal_deflection"))
        self.assertTrue(any("pedal_brake はどこにも繋がらない" in e["name"] for e in out["effects"]))

    def test_inertia_and_push(self):
        self.assertIsNotNone(quantity(self.out["inertia"], "max_part_deflection"))
        self.assertIsNotNone(quantity(self.out["inertia"], "max_stress"))
        # nothing stands beside the occupants' hips and shoulders on version 1
        self.assertEqual(quantity(self.out["push"], "occupant_push_open")["value"], 4.0)
        self.assertIsNone(quantity(self.out["push"], "occupant_push_deflection"))

    def test_pedal_moved_onto_the_bulkhead_is_held(self):
        d = touch_nearest(V1, "pedal_brake", "part:bulkhead")
        out = analyse_load_path(d, "foot", dict(VALUES, foot_parts=["pedal_brake"]))
        self.assertNotIn("pedal_brake", out["free"])
        p = quantity(out, "pedal_deflection")
        self.assertEqual(p["where"], "pedal_brake")
        self.assertTrue(math.isfinite(p["value"]) and p["value"] > 0.0)

    def test_a_larger_touch_holds_the_panels(self):
        _, free = contact_groups(V1, find_contacts(V1, 10.0))
        self.assertGreater(len(self.free), len(free))
        self.assertNotIn("floor", free)


class JoinedV1Test(unittest.TestCase):
    """Version 1 as designgen makes it now (every joint touches): nothing floats, every load kind solves."""

    def test_nothing_floats_and_every_kind_solves(self):
        _, free = contact_groups(JOINED, find_contacts(JOINED))
        self.assertEqual(sorted(free), [])
        for kind in ("hand", "foot", "inertia", "push"):
            out = analyse_load_path(JOINED, kind, VALUES)
            self.assertEqual(out["free"], [], kind)
        self.assertIsNotNone(quantity(analyse_load_path(JOINED, "foot", VALUES), "pedal_deflection"))


class ToolRecordTest(unittest.TestCase):
    def test_records_fit_the_contract(self):
        tool = LoadPath()
        for kind in ("hand", "foot", "inertia", "push"):
            rec = tool.run(V1, case(kind), PRED, {})
            self.assertEqual(rec["status"], "ok", rec.get("error"))
            rec = dict(rec, analysis_id="ana-0001")
            self.assertEqual(contract.errors("analysis", rec), [], kind)
            json.dumps(rec, allow_nan=False)
            self.assertTrue(rec["assumptions"])

    def test_touch_run_param(self):
        tool = LoadPath()
        rec = tool.run(V1, case("hand"), PRED, {"touch": {"value": 10.0, "unit": "mm"}})
        n10 = next(r["value"] for r in rec["results"] if r["quantity"] == "free_parts")
        rec = tool.run(V1, case("hand"), PRED, {})
        n2 = next(r["value"] for r in rec["results"] if r["quantity"] == "free_parts")
        self.assertLess(n10, n2)
        self.assertEqual(TOUCH_MM, 2.0)

    def test_no_hand_control_fails_with_a_reason(self):
        d = copy.deepcopy(V1)
        d["parts"] = [p for p in d["parts"] if p["kind"] != "steering"]
        rec = LoadPath().run(d, case("hand"), PRED, {})
        self.assertEqual(rec["status"], "failed")
        self.assertIn("hand control", rec["error"])


if __name__ == "__main__":
    unittest.main()
