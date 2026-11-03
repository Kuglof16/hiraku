"""The tilt tool as a contract AnalysisTool."""

import json
import unittest

from hiraku import contract
from hiraku.contract.protocols import AnalysisTool
from hiraku.solvers.k2_vehicle.model import box_design
from hiraku.solvers.k2_vehicle.shelf import TiltTool
from tests.solvers.k2.slow import SLOW


def load_case(**params):
    return {"load_case_id": "S1-test", "test_id": "S1", "domain": "handling", "title": "tilt",
            "params": params, "tolerance_rel": 0.1}


@SLOW
class TiltToolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = TiltTool()
        cls.design = box_design(mass=600.0, cg_z=0.55, wheel_mass=20.0, track=1.25, k_w=25.0)
        cls.rec = cls.tool.run(cls.design, load_case(direction="both", mu={"value": 0.6, "unit": "1"}),
                               [{"prediction_id": "pred-0001"}], {})

    def test_is_an_analysis_tool(self):
        self.assertIsInstance(self.tool, AnalysisTool)

    def test_own_check_passes(self):
        for c in self.tool.own_checks():
            self.assertTrue(c["pass"], c)

    def test_record_fits_the_contract(self):
        rec = dict(self.rec, analysis_id="ana-0001")
        self.assertEqual(contract.errors("analysis", rec), [])
        self.assertEqual(self.rec["status"], "ok")
        self.assertEqual(self.rec["base"], "K2")
        json.dumps(self.rec)

    def test_left_and_right_identical_and_below_rigid(self):
        res = {r["quantity"]: r["value"] for r in self.rec["results"]}
        self.assertEqual(res["tilt_angle_left"], res["tilt_angle_right"])
        rigid = 57.0  # atan(0.625 / h) with h = total CG height, about 53 deg; springs only lower it
        self.assertLess(res["tilt_angle"], rigid)
        self.assertGreater(res["tilt_angle"], 40.0)
        self.assertEqual(res["slides_first"], 1.0)  # mu 0.6 < tan(about 48 deg)

    def test_series_and_vectors_for_the_screen(self):
        names = {s["channel"] for s in self.rec["series"]}
        for ch in ("body_x", "roll", "cg_y", "table_angle", "wheel_load.FL", "reaction_z.RR", "grip_y.RR"):
            self.assertIn(ch, names)
        lifted = [v for v in self.rec["vectors"] if v["kind"] == "reaction" and v["vector_N"][2] == 0.0]
        self.assertEqual(len(lifted), 2)  # the two upper wheels carry nothing at the tip

    def test_bad_design_fails_with_advice(self):
        d = box_design(mass=600.0, cg_x=1.0)  # CG 1 m ahead of the front axle
        rec = self.tool.run(d, load_case(), [{"prediction_id": "pred-0001"}], {})
        self.assertEqual(rec["status"], "failed")
        self.assertIn("support area", rec["error"])


if __name__ == "__main__":
    unittest.main()
