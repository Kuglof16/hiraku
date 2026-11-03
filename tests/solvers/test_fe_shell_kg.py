"""Initial-stress stiffness of MITC4 from the membrane forces at the Gauss points (with the incompatible
modes) and the t^2 / 12 rotation terms: element properties, a plate under a linearly varying edge load
against a Ritz solution, and a deep strip's lateral buckling with one element over the depth
(hiraku.solvers.fe.shell_checks names the formulas)."""

import unittest

import numpy as np

from hiraku.solvers.fe import shell_checks as sc
from hiraku.solvers.fe.eigen_checks import plate_k
from hiraku.solvers.fe.shell import MITC4
from hiraku.solvers.k1_shell import element as mitc4

RNG = np.random.default_rng(1)
Q = np.linalg.qr(RNG.normal(size=(3, 3)))[0]
X = np.array([[0.0, 0.0, 0.0], [40.0, 3.0, 0.0], [45.0, 30.0, 0.0], [-4.0, 25.0, 0.0]]) @ Q.T


class ElementTest(unittest.TestCase):
    def test_gauss_strains_give_the_condensed_membrane_forces(self):
        """sum over the Gauss points of Bm^T D t eps_g detJ equals the condensed membrane stiffness times u
        (k1_shell's, drilling left out): the recovered incompatible modes are the condensed ones."""
        g = MITC4([[0, 1, 2, 3]], 2.0, 2e5, 0.3)
        ue = RNG.normal(size=(1, 24))
        eps = g.gauss_membrane_strains(X, ue)
        R, xl, _, _ = mitc4.local_frames(X[None])
        Kl = mitc4.local_stiffness(xl, g.t, g.E, g.nu, 0.0)
        ul = np.einsum("eij,enj->eni", R, ue.reshape(1, 8, 3)).reshape(1, 24)
        f_ref = Kl[0][np.ix_(mitc4._MEM, mitc4._MEM)] @ ul[0, mitc4._MEM]
        D = mitc4.plane_stress_matrix(g.E, g.nu)[0] * 2.0
        f = np.zeros(8)
        for i, (xi, eta) in enumerate(mitc4.GAUSS_2x2):
            _, dN = mitc4.shape(xi, eta)
            _, det, inv = mitc4._jacobian(dN, xl)
            f += mitc4.membrane_B(np.einsum("eab,bk->eak", inv, dN))[0].T @ D @ eps[0, i] * det[0]
        self.assertLess(np.abs(f - f_ref).max(), 1e-12 * np.abs(f_ref).max())

    def test_uniform_stress_gives_the_centre_form_and_symmetry(self):
        g = MITC4([[0, 1, 2, 3]], 2.0, 2e5, 0.3)
        u = np.zeros((4, 6))
        u[:, :3] = X @ np.array([[1e-3, 2e-4, 0.0], [-3e-4, 5e-4, 0.0], [0.0, 0.0, 0.0]]).T
        ue = u.reshape(1, 24)
        self.assertLess(np.abs(g.gauss_membrane_forces(X, ue) - g.membrane_forces(X, ue)[:, None]).max(), 1e-9)
        Kg = g.geometric_stiffness(X, ue, thickness_terms=False)
        Kc = g.geometric_stiffness(X, ue, at="centre", thickness_terms=False)
        self.assertLess(np.abs(Kg - Kc).max(), 1e-12 * np.abs(Kc).max())
        Kt = g.geometric_stiffness(X, RNG.normal(size=(1, 24)))
        self.assertLess(np.abs(Kt - Kt.transpose(0, 2, 1)).max(), 1e-12 * np.abs(Kt).max())
        # the thickness terms sit on the two in-plane rotations only (none on the drilling rotation)
        n = mitc4.local_frames(X[None])[0][0, 2]
        Kd = g.geometric_stiffness(X, ue) - Kg
        self.assertLess(np.abs(Kd[0, 3:6, 3:6] @ n).max(), 1e-12 * np.abs(Kd).max())
        self.assertGreater(np.abs(Kd).max(), 0.0)


class LinearEdgeLoadTest(unittest.TestCase):
    """200 x 300 x 2 simply supported plate, edge compression sigma0 (1 - psi y / b)."""

    def test_ritz_reproduces_the_uniform_case(self):
        r = sc.ritz_linear_edge(200.0, 300.0, 2.0, 0.0)
        self.assertAlmostEqual(r["k"], plate_k(200.0, 300.0)[0], places=9)
        self.assertGreater(sc.ritz_linear_edge(200.0, 300.0, 2.0, 2.0)["k"], 23.8)  # pure bending (23.88 here)

    def test_converges_to_the_ritz_value(self):
        """Pure in-plane bending (psi = 2) and the triangle load (psi = 1): the error falls with the mesh,
        within 1 % at 16 x 24 (measured +0.74 % and +0.39 %). At these meshes the Gauss-point and centre forces
        differ by less than 1e-5 (a linear stress, whose mean the centre value is)."""
        for psi, tol in ((2.0, 0.01), (1.0, 0.006)):
            errs = [sc.linear_edge_buckling(psi, nx=nx, ny=ny) for nx, ny in ((4, 6), (8, 12), (16, 24))]
            e = [r["sigma_cr"] / r["sigma_ritz"] - 1.0 for r in errs]
            self.assertTrue(e[0] > e[1] > e[2] > 0.0, e)
            self.assertLess(e[2], tol)
            c = sc.linear_edge_buckling(psi, nx=16, ny=24, at="centre")
            self.assertLess(abs(c["sigma_cr"] / errs[2]["sigma_cr"] - 1.0), 1e-4)

    def test_thickness_terms_are_small_for_a_thin_plate(self):
        a = sc.linear_edge_buckling(2.0, nx=8, ny=12)
        b = sc.linear_edge_buckling(2.0, nx=8, ny=12, thickness_terms=False)
        self.assertLess(abs(a["sigma_cr"] / b["sigma_cr"] - 1.0), 1e-3)


class StripLateralBucklingTest(unittest.TestCase):
    """1000 x 50 x 2 strip bent in its plane, fork ends: M_cr = (pi / L) sqrt(E I_z G J)."""

    def test_one_element_over_the_depth(self):
        """Gauss-point forces: within 2 % of the formula with one element over the depth (measured +1.3 %;
        the strip's torsion in MITC4 measured 1.7 % stiffer than h t^3 / 3 (1 - 0.63 t / h) with the ends
        held), and within 0.5 % of the 8-element answer. Centre forces: the moment's stress is zero at the
        centre of a one-element web, so no buckling near it; with two elements +34 %."""
        g1 = sc.strip_lateral_buckling(nz=1)
        g8 = sc.strip_lateral_buckling(nz=8)
        self.assertLess(abs(g1["M_cr"] / g1["M_beam"] - 1.0), 0.02)
        self.assertLess(abs(g1["M_cr"] / g8["M_cr"] - 1.0), 0.005)
        c1 = sc.strip_lateral_buckling(nz=1, at="centre")
        self.assertGreater(c1["M_cr"], 100 * g1["M_cr"])
        c2 = sc.strip_lateral_buckling(nz=2, at="centre")
        self.assertGreater(c2["M_cr"] / g8["M_cr"], 1.3)

    def test_centre_forces_approach_from_above(self):
        """Centre forces converge to the 8-element Gauss-point answer from above as the depth is divided
        (measured +33.8 %, +6.9 %, +1.6 % at 2, 4, 8 elements); the Gauss-point answers at 1, 2, 4 elements
        are within 0.35 % of it."""
        g8 = sc.strip_lateral_buckling(nz=8)["M_cr"]
        c = [sc.strip_lateral_buckling(nz=n, at="centre")["M_cr"] / g8 - 1.0 for n in (2, 4, 8)]
        self.assertTrue(c[0] > c[1] > c[2] > 0.0, c)
        g = [sc.strip_lateral_buckling(nz=n)["M_cr"] / g8 - 1.0 for n in (1, 2, 4)]
        self.assertLess(max(abs(v) for v in g), 0.004)

    def test_long_strip(self):
        """L / h = 80: still within 2 % (measured +1.2 %), so the drilling springs take no moment."""
        r = sc.strip_lateral_buckling(L=4000.0, nx=160, nz=2)
        self.assertLess(abs(r["M_cr"] / r["M_beam"] - 1.0), 0.02)


if __name__ == "__main__":
    unittest.main()
