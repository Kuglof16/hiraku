"""The friction-circle channels of K2's records: friction_limit, friction_use and sliding per wheel."""

import math
import unittest

from hiraku.solvers.k2_vehicle.model import box_design
from hiraku.solvers.k2_vehicle.shelf import TOOLS


class FrictionChannelsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design = box_design(wheel_mass=15.0)
        lc = {"load_case_id": "S4", "params": {"speed": {"value": 35.0, "unit": "km/h"}}}
        cls.rec = TOOLS["braking"].run(cls.design, lc, [{"prediction_id": "pred-0001"}], {})
        cls.ser = {s["channel"]: s for s in cls.rec["series"]}
        cls.ids = [s["id"] for s in cls.design["supports"]]

    def test_every_wheel_has_the_channels_on_the_record_times(self):
        self.assertEqual(self.rec["status"], "ok", self.rec.get("error"))
        t = self.ser["speed"]["t_s"]
        for cid in self.ids:
            for name, unit in (("friction_limit", "N"), ("friction_use", "1"), ("sliding", "1")):
                s = self.ser[f"{name}.{cid}"]
                self.assertEqual((s["unit"], s["t_s"]), (unit, t), f"{name}.{cid}")

    def test_the_values_are_the_tyre_model_limit_and_use(self):
        # the series are rounded when kept, so the checks allow 1e-4
        for cid in self.ids:
            load, lim = self.ser[f"wheel_load.{cid}"]["values"], self.ser[f"friction_limit.{cid}"]["values"]
            fx, fy = self.ser[f"wheel_fx.{cid}"]["values"], self.ser[f"wheel_fy.{cid}"]["values"]
            use, slide = self.ser[f"friction_use.{cid}"]["values"], self.ser[f"sliding.{cid}"]["values"]
            ratio = [b / a for a, b in zip(load, lim) if a > 10.0]
            self.assertLess(max(ratio) - min(ratio), 1e-4 * max(ratio))  # one mu per tyre: limit = mu N
            for k in range(len(load)):
                want = math.hypot(fx[k], fy[k]) / lim[k] if lim[k] > 10.0 else None
                if want is not None:
                    self.assertAlmostEqual(use[k], want, delta=1e-4 * max(1.0, want))
                self.assertIn(slide[k], (0.0, 1.0))
                if use[k] >= 1.0:
                    self.assertEqual(slide[k], 1.0)

    def test_the_locked_wheels_slide_and_the_rolling_ones_do_not(self):
        # braking locks every braked wheel (no ABS): sliding while the vehicle still moves, not before
        t, speed = self.ser["speed"]["t_s"], self.ser["speed"]["values"]
        for cid in self.ids:
            slide = self.ser[f"sliding.{cid}"]["values"]
            before = [s for tk, s in zip(t, slide) if tk < 0.45]
            self.assertEqual(max(before), 0.0, cid)
            moving = [s for tk, s, v in zip(t, slide, speed) if tk > 0.8 and v > 5.0]
            self.assertTrue(moving and min(moving) == 1.0, cid)


if __name__ == "__main__":
    unittest.main()
