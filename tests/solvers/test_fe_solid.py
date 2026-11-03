"""HEX20 and HEX8 solids on the fe core: known answers 1-6 (patch, cantilever, Lame, holed plate, convergence,
shear locking) and the choice of the HEX20 integration."""

import unittest

import numpy as np

from hiraku.solvers.fe import checks, mesh as fm, precheck
from hiraku.solvers.fe.solid import HEX20_NAT, Hex20, shape_hex20, shape_quad8
from tests.solvers.k2.slow import SLOW


class ShapeFunctionTest(unittest.TestCase):
    def test_hex20_shape_functions(self):
        for i, p in enumerate(HEX20_NAT):
            N, _ = shape_hex20(*p)
            self.assertAlmostEqual(N[i], 1.0, places=14)
            self.assertAlmostEqual(float(np.abs(np.delete(N, i)).max()), 0.0, places=14)
        p = np.array([0.31, -0.27, 0.66])
        N, dN = shape_hex20(*p)
        self.assertAlmostEqual(N.sum(), 1.0, places=14)
        h = 1e-6
        for a in range(3):
            q1, q2 = p.copy(), p.copy()
            q1[a] += h
            q2[a] -= h
            num = (shape_hex20(*q1)[0] - shape_hex20(*q2)[0]) / (2 * h)
            self.assertLess(np.abs(num - dN[a]).max(), 1e-8)

    def test_quad8_shape_functions(self):
        nat = [(-1, -1), (1, -1), (1, 1), (-1, 1), (0, -1), (1, 0), (0, 1), (-1, 0)]
        for i, p in enumerate(nat):
            N, _ = shape_quad8(*p)
            self.assertAlmostEqual(N[i], 1.0, places=14)
            self.assertAlmostEqual(N.sum(), 1.0, places=14)


class IntegrationChoiceTest(unittest.TestCase):
    """Zero-energy modes of one free brick: 6 are the rigid motions; more are spurious."""

    def zero_modes(self, integration, box=((0, 0, 0), (10, 10, 10)), size=10.0):
        m = fm.box_mesh([box], size, 2)
        from hiraku.solvers.fe import FEModel
        model = FEModel(m.nodes)
        model.add(Hex20(m.conn, 1000.0, 0.3, integration=integration))
        w = np.linalg.eigvalsh(model.stiffness().toarray())
        return int(np.sum(w < 1e-8 * w.max()))

    def test_full_integration_has_only_rigid_modes(self):
        self.assertEqual(self.zero_modes(3), 6)

    def test_reduced_integration_has_spurious_modes(self):
        self.assertEqual(self.zero_modes(2), 12)  # 6 spurious modes in a single reduced HEX20
        # joined bricks (one layer of 2 x 2) do not share them: only the 6 rigid motions remain
        self.assertEqual(self.zero_modes(2, ((0, 0, 0), (20, 20, 10)), 10.0), 6)


class PatchTest(unittest.TestCase):
    def test_distorted_patch_hex20(self):
        r = checks.patch_test("hex20")
        self.assertLess(r["stress_error_rel"], 1e-9)
        self.assertLess(r["inner_displacement_error"], 1e-12)
        self.assertLess(r["rigid"]["energy_ratio"], 1e-12)

    def test_distorted_patch_hex8(self):
        r = checks.patch_test("hex8")
        self.assertLess(r["stress_error_rel"], 1e-9)
        self.assertLess(r["inner_displacement_error"], 1e-12)


class CantileverTest(unittest.TestCase):
    def test_tip_deflection_and_bending_stress(self):
        r = checks.cantilever("hex20", 2)
        self.assertAlmostEqual(r["tip"], r["expected"], delta=0.02 * r["expected"])
        self.assertAlmostEqual(r["stress_mid"], r["stress_expected"], delta=0.005 * r["stress_expected"])
        self.assertLess(r["equilibrium"], 1e-9)
        self.assertLess(r["load_balance"], 1e-9)
        self.assertLess(precheck.rigid_body_energy(r["model"])["energy_ratio"], 1e-12)

    def test_convergence(self):
        c = checks.convergence("hex20", (1, 2, 4))
        self.assertGreater(c["diffs"][0], c["diffs"][1])
        self.assertGreater(c["order"], 1.5)
        exp = checks.cantilever("hex20", 1)["expected"]
        self.assertAlmostEqual(c["extrapolated"], exp, delta=0.01 * exp)

    def test_shear_locking_of_hex8(self):
        r = checks.locking(10)
        self.assertLess(r["hex8"], 0.7)
        self.assertGreater(r["hex20"], 0.97)


class LameTest(unittest.TestCase):
    def test_thick_cylinder(self):
        r = checks.lame_cylinder(8, 8)
        self.assertLess(r["sr_err"], 0.03)
        self.assertLess(r["st_err"], 0.03)
        self.assertAlmostEqual(r["ur_inner"], r["ur_inner_expected"], delta=1e-3 * r["ur_inner_expected"])
        self.assertLess(r["equilibrium"], 1e-9)


class HoledPlateTest(unittest.TestCase):
    def test_stress_concentration(self):
        r = checks.holed_plate(n_quarter=8, n_radial=8, n_thick=3)
        prof = np.array(r["kt_profile"])
        avg = np.trapezoid(prof[:, 1], prof[:, 0]) / (prof[-1, 0] - prof[0, 0])
        self.assertAlmostEqual(avg, r["kt_pilkey"], delta=0.03 * r["kt_pilkey"])
        self.assertGreater(r["kt_mid"], r["kt_surface"])  # the peak is higher at the mid-plane
        self.assertLess(r["equilibrium"], 1e-9)


@SLOW
class SlowSolidTest(unittest.TestCase):
    def test_hex8_converges_slower(self):
        c8 = checks.convergence("hex8", (2, 4, 8))
        self.assertGreater(c8["order"], 1.5)
        exp = checks.cantilever("hex20", 1)["expected"]
        self.assertAlmostEqual(c8["extrapolated"], exp, delta=0.015 * exp)

    def test_fine_holed_plate(self):
        r = checks.holed_plate(n_quarter=12, n_radial=12, n_thick=5)
        prof = np.array(r["kt_profile"])
        avg = np.trapezoid(prof[:, 1], prof[:, 0]) / (prof[-1, 0] - prof[0, 0])
        self.assertAlmostEqual(avg, r["kt_pilkey"], delta=0.02 * r["kt_pilkey"])


if __name__ == "__main__":
    unittest.main()
