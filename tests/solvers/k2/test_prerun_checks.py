"""The seven checks K2 must pass before any scenario runs."""

import unittest

from hiraku.solvers.k2_vehicle import checks
from tests.solvers.k2.slow import SLOW


@SLOW
class PreRunChecks(unittest.TestCase):
    def assert_all_pass(self, results):
        self.assertTrue(results)
        for c in results:
            with self.subTest(case=c["case"]):
                self.assertTrue(c["pass"], f"{c['case']}: expected {c['expected']} got {c['got']} {c['unit']}")

    def test_static_wheel_loads(self):
        res = checks.check_static_loads()
        self.assert_all_pass(res)
        diffs = [c for c in res if c["quantity"] == "wheel_load_difference"]
        self.assertEqual([c["got"] for c in diffs], [0.0, 0.0])

    def test_mirror(self):
        self.assert_all_pass(checks.check_mirror())

    def test_free_fall_matches_integrator_sum(self):
        self.assert_all_pass(checks.check_free_fall())

    def test_quarter_car_1891_hz(self):
        res = checks.check_quarter_car()
        self.assert_all_pass(res)
        self.assertAlmostEqual(res[0]["expected"], 1.891, places=3)

    def test_rigid_tilt_table_5019_deg(self):
        res = checks.check_rigid_tilt()
        self.assert_all_pass(res)
        self.assertAlmostEqual(res[0]["expected"], 50.19, places=2)
        self.assertEqual(res[0]["got"], res[1]["got"])

    def test_parked_on_slope_does_not_creep(self):
        res = checks.check_parked_on_slope()
        self.assert_all_pass(res)
        self.assertLess(res[0]["got"], 1.0)

    def test_magic_formula_tyre(self):
        res = checks.check_tyre()
        self.assert_all_pass(res)
        by = {c["quantity"]: c for c in res}
        self.assertEqual(set(by), {"peak_longitudinal_force", "peak_lateral_force", "slip_stiffness",
                                   "cornering_stiffness", "friction_ellipse", "aligning_moment_sign"})
        # the scaled tyre is the K0 tyre at small slip: the check vehicle's defaults
        self.assertAlmostEqual(by["cornering_stiffness"]["expected"] / by["peak_lateral_force"]["expected"] * 0.8, 8.0)


if __name__ == "__main__":
    unittest.main()
