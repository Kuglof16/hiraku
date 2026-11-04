"""The tilt tool as a contract AnalysisTool."""

import json
import math
import unittest

from hiraku import contract
from hiraku.contract.protocols import AnalysisTool
from hiraku.solvers.k2_vehicle.checks import CHECK_TILT
from hiraku.solvers.k2_vehicle.model import box_design, build_vehicle
from hiraku.solvers.k2_vehicle.scenario import tilt_sweep
from hiraku.solvers.k2_vehicle.shelf import TiltTool
from tests.solvers.k2.slow import SLOW


def load_case(**params):
    return {"load_case_id": "S1-test", "test_id": "S1", "domain": "handling", "title": "tilt",
            "params": params, "tolerance_rel": 0.1}


def _spring_check_vehicle():
    d = box_design(mass=500.0, cg_x=-0.9, cg_z=0.5, track=1.2, k_w=20.0, wheel_mass=20.0, travel=200.0,
                   variables={"tyre_stiffness": {"value": 10000.0, "unit": "N/mm"},
                              "tire_mu": {"value": 2.0, "unit": "1"}}, name="tilt check, springs")
    return build_vehicle(d)


class TiltSweepTest(unittest.TestCase):
    """The coarse-to-fine sweep: going back far enough, and the recorder rewound with the state."""

    @classmethod
    def setUpClass(cls):
        cls.veh = _spring_check_vehicle()
        cls.ref = tilt_sweep(cls.veh, left_up=True, **CHECK_TILT)["tip_angle"]
        # a 1 deg span after a 20 deg/s turn starts the slow turn with the body still rolling: the
        # wheels lift within a few hundredths of a degree (0.475 deg early without the step back)
        short = dict(CHECK_TILT, slow_span=math.radians(1.0))
        cls.short = tilt_sweep(cls.veh, left_up=True, **short)["tip_angle"]
        cls.recorded = tilt_sweep(cls.veh, left_up=False, record_hz=10.0, after_s=0.2,
                                  **{k: v for k, v in CHECK_TILT.items() if k != "after_s"})

    def test_steps_back_when_the_slow_turn_tips_at_once(self):
        self.assertAlmostEqual(math.degrees(self.short), math.degrees(self.ref), delta=1e-3)

    def test_recorder_rewound_with_the_state(self):
        r = self.recorded
        rec = r["recorder"]
        self.assertEqual(r["tip_angle"], self.ref)  # mirrored, and the recorder does not change the run
        self.assertTrue(all(b > a for a, b in zip(rec.t, rec.t[1:])), "time runs forward after the rewind")
        for name, vals in rec.ch.items():
            self.assertEqual(len(vals), len(rec.t), name)
        angles = rec.ch["table_angle"]
        self.assertTrue(all(b < a for a, b in zip(angles, angles[1:])), "the table turns one way (right side up)")
        # the sample at the tip (taken when the snapshot is kept) shows the tip angle
        k = max(i for i, t in enumerate(rec.t) if t <= rec.snap[r["snapshot"]]["t"])
        self.assertAlmostEqual(-angles[k], math.degrees(r["tip_angle"]), delta=1e-9)


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
