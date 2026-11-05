"""The Magic Formula tyre: its coefficient file, the scaling to the design and the deflection states."""

import unittest

from hiraku.solvers.k2_vehicle.magic_formula import MF61, PRESSURE_TERMS_READ, load_coefficients
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

    def test_the_coefficient_file_says_it_is_not_a_measured_utv_tyre(self):
        u = load_coefficients()["usage"]
        self.assertIn("not a measured UTV tyre", u["measured_tyre"])
        self.assertIn("shapes", u["what_k2_takes_from_it"])
        for name in ("FNOMIN", "UNLOADED_RADIUS", "lambda_mux", "lambda_Ky"):
            self.assertIn(name, u["what_k2_sets_from_the_design"])
        self.assertIn("dpi = 0", u["inflation_pressure"])

    def test_pressure_terms_are_not_read(self):
        c = load_coefficients()
        self.assertFalse(PRESSURE_TERMS_READ)
        self.assertFalse(any(k.startswith(("NOMPRES", "INFLPRES", "PP")) for g in c.values() if isinstance(g, dict)
                             for k in g))
        # a file with pressure terms gives the same tyre: they are not read
        with_p = dict(c, longitudinal=dict(c["longitudinal"]), pressure={"NOMPRES": 220000.0, "PPX1": -0.3})
        a, b = MF61(c, fz0=2000.0, r0=0.3), MF61(with_p, fz0=2000.0, r0=0.3)
        self.assertFalse(hasattr(b, "PPX1"))
        self.assertEqual(a.forces(2500.0, 0.05, 0.03, 0.01), b.forces(2500.0, 0.05, 0.03, 0.01))

    def test_the_assumption_says_the_tyre_is_not_a_measured_utv_tyre(self):
        veh = build_vehicle(box_design(wheel_mass=20.0))
        text = next(a for a in veh.assumptions if "Magic Formula 6.1" in a)
        self.assertIn("205/60R15", text)
        self.assertIn("Not a measured UTV tyre", text)
        self.assertIn("nominal inflation pressure", text)

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


class CompiledKernels(unittest.TestCase):
    """The compiled tyre kernels (numba) give the plain Python functions' values within 1e-12 of their size."""

    def assertClose(self, got, ref):
        for a, b in zip(got, ref):
            if isinstance(b, bool):
                self.assertEqual(bool(a), b)
            else:
                self.assertLessEqual(abs(a - b), 1e-12 * max(1.0, abs(b)), (got, ref))

    def test_forces_and_peaks(self):
        import random

        mf = MF61(load_coefficients(), fz0=2000.0, r0=0.3, lmux=0.9, lmuy=0.8, lkx=0.7, lky=0.6)
        rng = random.Random(7)
        for _ in range(5000):
            a = (rng.uniform(-500.0, 6000.0), rng.uniform(-1.0, 1.0), rng.uniform(-0.6, 0.6),
                 rng.choice((0.0, rng.uniform(-0.1, 0.1))))
            self.assertClose(mf.forces_and_peaks(*a), mf.forces_and_peaks_py(*a))
        self.assertEqual(tuple(mf.forces_and_peaks(0.0, 0.1, 0.1)), (0.0,) * 6)

    def test_mf_tyre_force_both_sides(self):
        import random

        mf = MF61(load_coefficients(), fz0=2000.0, r0=0.3)
        rng = random.Random(8)
        for side in (1.0, -1.0):
            tyre = MFTyre(mf, 0.3, 2000.0, side)
            for _ in range(5000):
                a = (rng.uniform(tyre.dx_lo * 1.1, tyre.dx_hi * 1.1), rng.uniform(tyre.dy_lo * 1.1, tyre.dy_hi * 1.1),
                     rng.uniform(-3.0, 3.0), rng.uniform(-3.0, 3.0), rng.uniform(0.0, 15.0),
                     rng.choice((0.0, rng.uniform(-15.0, 15.0), rng.uniform(-0.5, 0.5))), rng.uniform(-0.1, 0.1),
                     rng.choice((0.0, rng.uniform(0.0, 6000.0))))
                self.assertClose(tyre.force(*a), tyre.force_py(*a))


if __name__ == "__main__":
    unittest.main()
