"""scene_inputs.loaded_radius: launch.peak_tractive_force's r is the driven wheels' radius under load
(free radius less W / tyre_stiffness at rest), or the free radius, said so, when the design has no
tyre_stiffness."""

import json
import unittest
from pathlib import Path

from hiraku.catalog.builders import k2_tests
from hiraku.contract.loading import on_board
from hiraku.solvers.k0_handcalc import scene_inputs
from hiraku.solvers.k0_handcalc.vehicle import support_loads

ROOT = Path(__file__).resolve().parents[2]
DESIGN = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
LC = {"load_case_id": "P3-accel30", "test_id": "P3", "domain": "powertrain",
      "params": {"loading": "two_cargo", "target_speed": {"value": 30.0, "unit": "km/h"},
                 "grade": {"value": 0.0, "unit": "%"}}}


class LoadedRadiusTest(unittest.TestCase):
    def test_free_radius_less_the_static_deflection(self):
        got = scene_inputs.launch_inputs(DESIGN, "two_cargo", LC["params"])
        rad = got["radius"]
        self.assertEqual(rad["kind"], "loaded")
        self.assertEqual(rad["assumption"], scene_inputs.LOADED_RADIUS_ASSUMPTION)
        driven = [s for s in DESIGN["supports"] if s["actuation"]["drive"]]
        self.assertEqual(set(rad["deflection_mm"]), {s["id"] for s in driven})
        ground = support_loads(on_board(DESIGN, "two_cargo"))["supports"]
        kt = DESIGN["variables"]["tyre_stiffness"]["value"]
        free = sum(float(s["radius_mm"]) for s in driven) / len(driven)
        want = free - sum(ground[s["id"]]["force_N"][2] / kt for s in driven) / len(driven)
        self.assertAlmostEqual(rad["free_r_mm"], free, places=6)
        self.assertAlmostEqual(rad["r_mm"], want, places=6)
        self.assertAlmostEqual(got["inputs"]["r"]["value"], want, places=6)
        self.assertEqual(got["inputs"]["r"]["unit"], "mm")
        self.assertTrue(0.0 < free - want < 0.1 * free)  # a deflection, small against the radius

    def test_free_radius_without_tyre_stiffness(self):
        d = json.loads(json.dumps(DESIGN))
        del d["variables"]["tyre_stiffness"]
        rad = scene_inputs.loaded_radius(d, "two_cargo", {"a": 300.0, "b": 310.0})
        self.assertEqual((rad["kind"], rad["r_mm"], rad["free_r_mm"]), ("free", 305.0, 305.0))
        self.assertEqual(rad["assumption"], scene_inputs.FREE_RADIUS_ASSUMPTION)
        self.assertIn("自由半径", rad["assumption"])

    def test_the_plan_says_which_radius(self):
        first = k2_tests.PLANS["P3"]["predictions"][0]
        self.assertTrue(any("荷重でたわんだ半径" in a and "自由半径" in a for a in first["assumptions"]))
        self.assertEqual(k2_tests.launch_inputs(DESIGN, LC)["r"]["value"],
                         scene_inputs.launch_inputs(DESIGN, "two_cargo", LC["params"])["radius"]["r_mm"])


if __name__ == "__main__":
    unittest.main()
