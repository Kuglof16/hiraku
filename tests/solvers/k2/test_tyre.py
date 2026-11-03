"""The Magic Formula tyre: its coefficient file, the scaling to the design and the deflection states."""

import unittest

from hiraku.solvers.k2_vehicle.magic_formula import MF61, load_coefficients
from hiraku.solvers.k2_vehicle.model import box_design, build_vehicle
from hiraku.solvers.k2_vehicle.tyre import MFTyre


class MagicFormula(unittest.TestCase):
    def test_the_coefficient_file_names_its_source_and_is_not_checked(self):
        c = load_coefficients()
        self.assertEqual(c["kind"], "mf_tyre")
        self.assertEqual(c["model"], "MF 6.1")
        self.assertFalse(c["source"]["checked"])
        self.assertIn("Pacejka", c["source"]["title"])
        self.assertTrue(c["source"]["read_from"].startswith("https://"))

    def test_scaled_tyre_matches_the_design_numbers_at_its_static_load(self):
        veh = build_vehicle(box_design(wheel_mass=20.0, variables={"tire_mu": {"value": 0.7, "unit": "1"}}))
        for c in veh.corners:
            with self.subTest(corner=c.id):
                self.assertAlmostEqual(c.mf.mu_x(c.static_load), 0.7, places=12)
                self.assertAlmostEqual(c.mf.mu_y(c.static_load), 0.7, places=12)
                self.assertAlmostEqual(c.mf.kxk(c.static_load) / c.c_long, 1.0, places=12)
                self.assertAlmostEqual(-c.mf.kya(c.static_load) / c.c_lat, 1.0, places=12)
        self.assertTrue(any("Magic Formula 6.1" in a for a in veh.assumptions))

    def test_a_right_tyre_is_the_left_one_mirrored(self):
        mf = MF61(load_coefficients(), fz0=2000.0, r0=0.3)
        left, right = MFTyre(mf, 0.3, 2000.0, 1.0), MFTyre(mf, 0.3, 2000.0, -1.0)
        for dx, dy, gamma in ((-0.01, 0.004, 0.01), (0.02, -0.03, -0.02), (0.0, 0.05, 0.0)):
            fl = left.force(dx, dy, 0.1, 0.05, 10.0, 10.0, gamma, 2100.0)
            fr = right.force(dx, -dy, 0.1, -0.05, 10.0, 10.0, -gamma, 2100.0)
            self.assertEqual(fl[0], fr[0])
            self.assertEqual(fl[1], -fr[1])
            self.assertEqual(fl[2], -fr[2])

    def test_locked_and_spinning_wheels_stay_in_the_slip_range(self):
        mf = MF61(load_coefficients(), fz0=2000.0, r0=0.3)
        tyre = MFTyre(mf, 0.3, 2000.0, 1.0)
        dx, dy = 0.0, 0.0
        for _ in range(5000):  # spinning at standstill: the contact slides backward at 2 m/s
            dx, dy = tyre.relax(dx, dy, -2.0, 0.0, 6.0, 0.0, 1e9, 1e-3)
        self.assertAlmostEqual(-dx / 0.3, mf.kappa_max)
        fl, _, _, sliding, lim = tyre.force(dx, dy, -2.0, 0.0, 6.0, 0.0, 0.0, 2000.0)
        self.assertTrue(sliding)
        self.assertLessEqual(fl, lim)
        self.assertAlmostEqual(fl, mf.forces(2000.0, mf.kappa_max, 0.0)[0])  # no damping on a held deflection


if __name__ == "__main__":
    unittest.main()
