import copy
import json
import math
import unittest
from pathlib import Path

import numpy as np

from hiraku import contract, solvers
from hiraku.solvers.k4_geometry import bodies, disassemble, fit, from_box, from_member, interferences, overlap, sweep

ROOT = Path(__file__).resolve().parents[2]
DESIGN = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
PRED = [{"prediction_id": "pred-0001"}]
TOOL = {t.name: t for t in solvers.builtin_tools()}["assembly"]


def results(rec):
    return {r["quantity"]: r for r in rec["results"]}


class ObbTest(unittest.TestCase):
    def test_separating_axes(self):
        a = from_box([0, 0, 0], [1, 1, 1])
        self.assertLess(overlap(a, from_box([1.01, 0, 0], [1, 1, 1])), 0)
        self.assertGreater(overlap(a, from_box([0.99, 0, 0], [1, 1, 1])), 0)
        # edge-edge: two cubes turned 45 deg about different axes, apart along the diagonal
        b = from_box([1.2, 1.2, 0], [1, 1, 1], [0, 0, 45])
        self.assertLess(overlap(a, b), 0)  # the face axes alone would not separate them along x and y... the cross axes do
        self.assertAlmostEqual(overlap(a, from_box([0, 0, 0], [1, 1, 1])), 1.0)

    def test_member_box(self):
        m = from_member([0, 0, 0], [1000, 0, 0], 40, 80)
        self.assertTrue(np.allclose(m.half, [500, 40, 20]))
        self.assertTrue(np.allclose(m.axes[1], [0, 0, 1]))  # height is vertical for a horizontal member


class DemoGeometryTest(unittest.TestCase):
    def test_no_static_interference(self):
        self.assertEqual(interferences(DESIGN), [])

    def test_mounts_are_not_interference(self):
        d = copy.deepcopy(DESIGN)
        for p in d["parts"]:
            if p["id"] == "battery":
                p["box"]["size_mm"][1] = 760.0  # wider than the gap between the rails
        hits = interferences(d)
        self.assertTrue(any("battery" in a + b for a, b, _ in hits))

    def test_steering_reaches_the_rails(self):
        sw = sweep(DESIGN)
        self.assertTrue(all("rail" in b for _, b, _ in sw["hits"]))
        # rails 80 mm inside the 600 x 200 wheel: free until w cos a + l sin a - w = 81 mm
        self.assertAlmostEqual(sw["free_steer"]["FL"], 16.48, delta=0.01)
        straight = copy.deepcopy(DESIGN)
        straight["variables"]["max_steer_angle"] = {"value": 10.0, "unit": "deg"}
        self.assertEqual(sweep(straight)["hits"], [])

    def test_assembly_order(self):
        asm = disassemble(DESIGN)
        self.assertEqual(asm["blocked"], {})
        order = [k for k, _ in asm["order"]]
        self.assertNotIn("part:occupant_R", order)
        self.assertNotIn("part:cargo", order)
        self.assertLess(order.index("part:susp_FL"), order.index("part:wheel_FL"))  # arms before the wheel

    def test_people(self):
        f = fit(DESIGN)
        self.assertAlmostEqual(f["shoulder_room"][0], 1300 - 2 * 495, places=6)
        self.assertAlmostEqual(f["hip_room.seat_R"][0], 500 - 391, places=6)
        self.assertEqual(f["knee_room.occupant_R"][1], "steering")


class AssemblyToolTest(unittest.TestCase):
    def test_checks_and_run(self):
        self.assertEqual([c for c in TOOL.check() if not c["pass"]], [])
        contract.validate("tool_spec", TOOL.spec())
        rec = TOOL.run(DESIGN, {"load_case_id": "H1", "test_id": "H1", "domain": "assembly", "title": "H1",
                                "params": {}, "tolerance_rel": 0.15}, PRED, {})
        contract.validate("analysis", dict(rec, analysis_id="ana-0001"))
        r = results(rec)
        self.assertEqual(r["interference_count"]["value"], 0.0)
        self.assertGreater(r["motion_interference_count"]["value"], 0.0)
        self.assertLess(r["free_steer_angle"]["value"], r["max_steer_angle"]["value"])
        self.assertEqual(r["blocked_parts"]["value"], 0.0)
        self.assertTrue(any(e["name"].startswith("assembly step 1:") for e in rec["effects"]))


class NoInfinityTest(unittest.TestCase):
    def test_infinite_results_are_left_out(self):
        tool = {t.name: t for t in solvers.builtin_tools()}["fatigue"]
        d = copy.deepcopy(DESIGN)
        d["variables"]["fatigue_fat_class"] = {"value": 1e6, "unit": "MPa"}  # no damage: life would be infinite
        rec = tool.run(d, {"load_case_id": "R3", "test_id": "R3", "domain": "strength", "title": "R3", "tolerance_rel": 0.15,
                           "params": {"distance": {"value": 1, "unit": "km"}, "road_class": "A", "speed": {"value": 10, "unit": "km/h"},
                                      "sample_length": {"value": 50, "unit": "m"}}}, PRED, {})
        self.assertTrue(all(math.isfinite(r["value"]) for r in rec["results"]))
        json.dumps(rec, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
