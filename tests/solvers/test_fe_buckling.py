"""Linear buckling on the fe core: beams, MITC4 shells and HEX20 bricks against Euler's column and the
simply supported plate (hiraku.solvers.fe.eigen_checks names the formulas)."""

import os
import unittest

import numpy as np

from hiraku.solvers.fe import FEModel, StaticSolver, eigen
from hiraku.solvers.fe import eigen_checks as ec
from hiraku.solvers.fe.beam import Beam, _integrate, bending_block, hermite, line_mesh

SLOW = unittest.skipUnless(os.environ.get("HIRAKU_SLOW_TESTS") == "1", "a finer mesh: set HIRAKU_SLOW_TESTS=1")


class BeamElementTest(unittest.TestCase):
    def test_euler_bernoulli_block_is_the_integral(self):
        L = np.array([123.0])
        k = bending_block(np.array([7.0e9]), L, np.zeros(1))
        ki = _integrate(L, lambda xi: np.einsum("ea,eb->eab", hermite(xi, L)[2], hermite(xi, L)[2])) * 7.0e9
        self.assertLess(np.abs(k - ki).max(), 1e-12 * np.abs(k).max())

    def test_rigid_motions_have_no_energy_and_matrices_are_symmetric(self):
        x = np.array([[0.0, 0.0, 0.0], [30.0, 40.0, 50.0]])
        model = FEModel(x)
        g = model.add(Beam([[0, 1]], 2e5, 8e4, 100.0, 800.0, 1200.0, 1500.0, 7.85e-9, ref=(1.0, 0.0, 0.0),
                           shear_area_y=60.0, shear_area_z=50.0))
        K = g.stiffness(x)[0]
        Phi = model.rigid_modes()
        self.assertLess(np.abs(K @ Phi).max(), 1e-9 * np.abs(K).max())
        ue = np.zeros((1, 12))
        ue[0, 6:9] = -0.01 * (x[1] - x[0]) / np.linalg.norm(x[1] - x[0])  # shortened: compression
        KG = g.geometric_stiffness(x, ue)[0]
        self.assertLess(np.abs(KG - KG.T).max(), 1e-12 * np.abs(KG).max())
        self.assertLess(np.abs(KG @ Phi[:, :3]).max(), 1e-9 * np.abs(KG).max())  # translations
        M = g.mass(x)[0]
        self.assertAlmostEqual(M[0::6, 0::6].sum(), g.mass_total(x), delta=1e-12)

    def test_tip_force_matches_cantilever_formula(self):
        L, E, I = 500.0, 2e5, 1.0e4
        x, c = line_mesh((0, 0, 0), (L, 0, 0), 4)
        model = FEModel(x)
        model.add(Beam(c, E, 8e4, 100.0, I, I, 2 * I))
        model.fix(0, "all")
        F = np.zeros((5, 6))
        F[4, 1] = 100.0
        sol = StaticSolver(model).solve(F)
        self.assertAlmostEqual(sol.displacements[4, 1], 100.0 * L ** 3 / (3 * E * I), delta=1e-9)


class BucklingSolverTest(unittest.TestCase):
    def setUp(self):
        x, c = line_mesh((0, 0, 0), (1000.0, 0, 0), 10)
        self.model = FEModel(x)
        self.model.add(Beam(c, 2e5, 8e4, 400.0, 13333.0, 13333.0, 22500.0))
        self.model.fix(0, "xyz")
        self.model.fix(0, [3])
        self.model.fix(10, [1, 2])
        self.F = np.zeros((11, 6))
        self.F[10, 0] = -1000.0

    def test_shifted_buckling_mode_gives_the_same_factors(self):
        a = eigen.buckling(self.model, self.F, 3)
        b = eigen.buckling(self.model, self.F, 3, sigma=0.5 * a.factors[0])
        self.assertLess(np.abs(a.factors - b.factors).max(), 1e-8 * a.factors[0])
        self.assertIn("inverted pencil", a.info["method"])

    def test_tension_has_no_positive_factor_of_the_first_kind(self):
        """Reversed load (tension): no buckling factor near the compression one."""
        a = eigen.buckling(self.model, self.F, 1)
        r = eigen.buckling(self.model, -self.F, 1)
        self.assertTrue(len(r.factors) == 0 or r.factors[0] > 10 * a.factors[0])

    def test_mode_shape_is_a_half_sine(self):
        a = eigen.buckling(self.model, self.F, 1)
        u = a.node_shape(0)
        lat = np.linalg.norm(u[:, 1:3], axis=1)
        s = np.sin(np.pi * np.linspace(0, 1, 11))
        self.assertLess(np.abs(lat / lat.max() - s).max(), 1e-3)


class EulerColumnTest(unittest.TestCase):
    """P_cr of a 40 x 2 square tube (round: 40 mm mid diameter) 2000 mm long, and a 20 mm square HEX20 bar
    1000 mm long, against pi^2 E I / (K L)^2. Tolerances: beam 1e-4 (Euler-Bernoulli), Timoshenko beam
    within 1e-4 of Engesser; shells and bricks 1.5 % (they include shear deformation, about 0.3 % here, and
    the tube walls' discretisation)."""

    def check(self, r, tol):
        self.assertLess(abs(r["P_cr"] / r["P_euler"] - 1.0), tol, (r["P_cr"], r["P_euler"]))

    def test_beam(self):
        for end in ("pinned", "cantilever"):
            self.check(ec.beam_column(end), 1e-4)
            t = ec.beam_column(end, timoshenko=True)
            self.assertLess(abs(t["P_cr"] / t["P_engesser"] - 1.0), 1e-4)

    def test_shell_tubes(self):
        for shape in ("square", "round"):
            for end in ("pinned", "cantilever"):
                r = ec.shell_column(shape, end, n_c=16, n_l=40)
                self.check(r, 0.015)
                self.assertLess(r["P_cr"], 1.0001 * r["P_euler"])

    def test_solid_bar(self):
        for end in ("pinned", "cantilever"):
            self.check(ec.solid_column(end, n_len=20), 0.015)

    @SLOW
    def test_fine_meshes(self):
        for shape in ("square", "round"):
            self.check(ec.shell_column(shape, "pinned", n_c=32, n_l=160), 0.01)
        self.check(ec.solid_column("pinned", n_len=40, n_side=2), 0.01)


class LateralTorsionalTest(unittest.TestCase):
    """I-beam (h 200, b 100, tf 8.5, tw 5.6 plates) 4000 mm long, fork ends, uniform moment about the strong
    axis: M_cr = (pi / L) sqrt(E Iz G J) sqrt(1 + pi^2 E Cw / (G J L^2)) (Timoshenko & Gere)."""

    def test_beam_against_the_formula_without_warping(self):
        """Beam elements have no warping DOF, so against the formula with Cw = 0: +2.6 %, +0.64 %, +0.16 %
        at 4, 8, 16 elements (about h^2), within 0.2 % at 16."""
        e = [ec.beam_lateral_buckling(n_el=n) for n in (4, 8, 16)]
        err = [r["M_cr"] / r["M_formula"] - 1.0 for r in e]
        self.assertTrue(err[0] > err[1] > err[2] > 0.0, err)
        self.assertLess(err[2], 0.002)
        self.assertGreater(err[0] / err[1], 3.5)

    def test_moment_sign_and_gradient(self):
        """The reversed moment buckles at the same load (doubly symmetric section). A moment at one end only
        buckles at 1.77 times the uniform one (the moment-gradient factor C1; 1.75 by Salvadori's
        approximation 1.75 + 1.05 psi + 0.3 psi^2 at psi = 0, as commonly quoted - not checked here, so only
        a range 1.7 to 1.9 is held); without the moment terms in K_G there is no lateral buckling."""
        u = ec.beam_lateral_buckling(n_el=16)
        x, c = line_mesh((0, 0, 0), (4000.0, 0, 0), 16)
        s = u["section"]
        model = FEModel(x)
        model.add(Beam(c, ec.E_ST, ec.G_ST, s["A"], s["Iy"], s["Iz"], s["J"], ref=(0.0, 1.0, 0.0)))
        for n in (0, 16):
            model.fix(n, [1, 2, 3])
        model.fix(0, [0])
        F = np.zeros((17, 6))
        F[16, 4], F[0, 4] = -1e6, 1e6
        r = eigen.buckling(model, F, 1)
        self.assertLess(abs(r.factors[0] * 1e6 / u["M_cr"] - 1.0), 1e-9)
        g = ec.beam_lateral_buckling(n_el=32, ratio=0.0)
        c1 = g["M_cr"] / ec.beam_lateral_buckling(n_el=32)["M_cr"]
        self.assertTrue(1.7 < c1 < 1.9, c1)
        model.groups[0].kg_moments = False
        r = eigen.buckling(model, F, 1)
        self.assertEqual(len(r.factors), 0)

    def test_shell_ibeam_carries_warping(self):
        """The same beam in MITC4 shells (4 elements over the flange width and the web, 80 along): within 5 %
        of the formula with Cw (measured -3.5 %: the flanges' torsion in plates is below b tf^3 / 3 by about
        0.63 tf / b, and the web distorts a little) and 14 % above the one without (warping is in the shells)."""
        r = ec.shell_ibeam_lateral_buckling()
        self.assertAlmostEqual(r["moment_applied"] / 1e6, 1.0, delta=1e-3)  # the mid-line Iy vs the nodes'
        self.assertLess(abs(r["M_cr"] / r["M_warping"] - 1.0), 0.05)
        self.assertGreater(r["M_cr"] / r["M_formula"], 1.10)


class ThermalBucklingTest(unittest.TestCase):
    """Buckling with the stresses of a temperature change (eigen.buckling(..., thermal=...))."""

    def test_heated_column_held_at_both_ends(self):
        """dT_cr = pi^2 I / (alpha A L^2) (54.9 K for the 40 x 2 square tube, 2000 mm): beam within 1e-4 and its
        axial force -E A alpha dT; shell tube (end rings on RBE3) within 1 % (measured -0.40 %); HEX20 bar
        20 x 20 x 1000 within 1.5 % (+0.92 %)."""
        r = ec.thermal_column("beam")
        self.assertLess(abs(r["dT_cr"] / r["dT_formula"] - 1.0), 1e-4)
        self.assertAlmostEqual(r["axial_force"] / r["axial_expected"], 1.0, delta=1e-9)
        r = ec.thermal_column("shell", n_el=40)
        self.assertLess(abs(r["dT_cr"] / r["dT_formula"] - 1.0), 0.01)
        r = ec.thermal_column("solid", n_el=20)
        self.assertLess(abs(r["dT_cr"] / r["dT_formula"] - 1.0), 0.015)

    def test_rigid_end_links_lower_the_heated_column(self):
        """With RBE2 at the ends the end sections cannot expand: the shell tube comes out 2.9 % low and the bar
        6.6 % low (measured), below the RBE3 answers."""
        for kind, n in (("shell", 40), ("solid", 20)):
            a = ec.thermal_column(kind, n_el=n)
            b = ec.thermal_column(kind, n_el=n, ends="rbe2")
            self.assertLess(b["dT_cr"], a["dT_cr"] * 0.98, kind)

    def test_heated_plate_held_in_plane(self):
        """Square 200 x 200 x 2 plate, simply supported and held in plane on its edges: the membrane force is
        -E alpha dT t / (1 - nu) in every element, and dT_cr = (1 - nu) pi^2 D (2 / b^2) / (E alpha t) = 10.5 K;
        MITC4 within 1 % (+0.47 %), DKT triangles (+0.02 %) and the mixed mesh (+0.25 %) within 1 %."""
        for kind in ("quad", "tri", "mixed"):
            r = ec.thermal_plate(kind)
            self.assertLess(abs(r["dT_cr"] / r["dT_formula"] - 1.0), 0.01, kind)
            self.assertLess(np.abs(r["n_membrane"][:, :2] / r["n_expected"] - 1.0).max(), 1e-9, kind)
            self.assertLess(np.abs(r["n_membrane"][:, 2]).max(), 1e-9 * abs(r["n_expected"]), kind)


class PrescribedDisplacementBucklingTest(unittest.TestCase):
    def test_column_pushed_by_a_given_displacement(self):
        """The top of the pinned column moved down by 1 mm (no force): the reference force is E A d / L, and
        factor x force = Euler's load (beam within 1e-4; shell tube through its RBE2 within 1.5 %, measured
        -0.79 %)."""
        r = ec.prescribed_column("beam")
        self.assertAlmostEqual(r["P_ref"] / r["P_ref_expected"], 1.0, delta=1e-9)
        self.assertLess(abs(r["P_cr"] / r["P_euler"] - 1.0), 1e-4)
        r = ec.prescribed_column("shell", n_el=40)
        self.assertLess(abs(r["P_cr"] / r["P_euler"] - 1.0), 0.015)


class PlateBucklingTest(unittest.TestCase):
    def test_simply_supported_plate(self):
        """300 x 200 x 2 plate: k = 4.34 at m = 2 half waves; MITC4 30 x 20 within 1 %, the same m."""
        r = ec.plate_buckling(nx=30, ny=20)
        self.assertEqual(r["m"], 2)
        self.assertEqual(r["m_fe"], 2)
        self.assertLess(abs(r["sigma_cr"] / r["sigma_formula"] - 1.0), 0.01)

    def test_square_plate_k_is_4(self):
        r = ec.plate_buckling(a=200.0, b=200.0, nx=16, ny=16)
        self.assertAlmostEqual(r["k"], 4.0, places=12)
        self.assertLess(abs(r["sigma_cr"] / r["sigma_formula"] - 1.0), 0.015)

    @SLOW
    def test_fine_plate(self):
        r = ec.plate_buckling(nx=60, ny=40)
        self.assertLess(abs(r["sigma_cr"] / r["sigma_formula"] - 1.0), 0.005)


if __name__ == "__main__":
    unittest.main()
