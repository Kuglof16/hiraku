"""Three-node shell triangles (DKT bending + CST membrane, or MITC3 bending) on the fe core: rigid motions,
patch tests, the Navier plate, a cantilever strip and meshes that mix triangles with MITC4
(hiraku.solvers.fe.shell_checks names the formulas)."""

import os
import unittest

import numpy as np

from hiraku.solvers.fe import FEModel, StaticSolver, eigen, loads, quality
from hiraku.solvers.fe import shell_checks as sc
from hiraku.solvers.fe.eigen_checks import E_ST, NU_ST, RHO_ST, plate_k, plate_shell
from hiraku.solvers.fe.tri import Tri3, split_quads

SLOW = unittest.skipUnless(os.environ.get("HIRAKU_SLOW_TESTS") == "1", "a finer mesh: set HIRAKU_SLOW_TESTS=1")
X = np.array([[0.0, 0.0, 0.0], [30.0, 5.0, 8.0], [7.0, 25.0, -3.0]])


class ElementTest(unittest.TestCase):
    def test_rigid_motions_symmetry_and_six_zero_modes(self):
        for bending in ("dkt", "mitc3"):
            model = FEModel(X)
            g = model.add(Tri3([[0, 1, 2]], 2.0, E_ST, NU_ST, RHO_ST, bending=bending))
            K = g.stiffness(X)[0]
            self.assertLess(np.abs(K @ model.rigid_modes()).max(), 1e-12 * np.abs(K).max(), bending)
            self.assertLess(np.abs(K - K.T).max(), 1e-12 * np.abs(K).max(), bending)
            ev = np.linalg.eigvalsh(K)
            self.assertEqual(int(np.sum(ev < 1e-9 * ev.max())), 6, bending)

    def test_mass_and_pressure_totals(self):
        model = FEModel(X)
        g = model.add(Tri3([[0, 1, 2]], 2.0, E_ST, NU_ST, RHO_ST))
        A = 0.5 * np.linalg.norm(np.cross(X[1] - X[0], X[2] - X[0]))
        for lumped in (False, True):
            M = g.mass(X, lumped)[0]
            self.assertAlmostEqual(M[0::6, 0::6].sum(), RHO_ST * 2.0 * A, delta=1e-15)
        self.assertAlmostEqual(g.mass_total(X), RHO_ST * 2.0 * A, delta=1e-18)
        f = loads.shell_pressure(model, g, 0.5)
        n = np.cross(X[1] - X[0], X[2] - X[0])
        self.assertLess(np.abs(f[:, :3].sum(axis=0) - 0.5 * n / 2).max(), 1e-12 * A)

    def test_thermal_load_of_a_free_triangle_is_self_balanced_and_strain_free(self):
        """A free plate of triangles heated with a through-thickness difference: curvature alpha dT / t,
        no stress once the thermal strain is taken off."""
        nodes, q = plate_shell(100.0, 60.0, 6, 4)
        model = FEModel(nodes)
        g = model.add(Tri3(split_quads(q), 2.0, E_ST, NU_ST))
        model.fix(0, "xyz")
        model.fix(6, [1, 2])
        model.fix(len(nodes) - 1, [2])
        alpha, dT = 1.2e-5, 20.0
        F = loads.shell_thermal(model, g, alpha, 5.0, dT)
        sol = StaticSolver(model).solve(F)
        ue = sol.element_displacements(g)
        eps, kap, _ = g.strains(model.nodes, ue)
        self.assertLess(np.abs(kap[:, :2] - alpha * dT / 2.0).max(), 1e-9 * alpha * dT / 2.0)
        self.assertLess(np.abs(eps[:, :2] - alpha * 5.0).max(), 1e-9 * alpha * 5.0)
        st = g.stresses(model.nodes, ue, thermal=(alpha, 5.0, dT))
        self.assertLess(np.abs(st.sigma).max(), 1e-6)


class PatchTest(unittest.TestCase):
    """Linear membrane field and quadratic deflection on an irregular patch, boundary DOFs given."""

    def test_triangles_pass(self):
        for bending in ("dkt", "mitc3"):
            r = sc.patch_test("tri", bending=bending)
            self.assertLess(r["inner_error"], 1e-10, bending)
            self.assertLess(r["strain_error"], 1e-10, bending)
            self.assertLess(r["curvature_error"], 1e-10, bending)

    def test_mixed_with_mitc4(self):
        """MITC3 with MITC4 passes (both tie the edges' tangential shear). DKT with MITC4 passes the membrane
        part but not the bending part: the Kirchhoff triangle puts forces on w for a constant moment, the
        Mindlin quad does not, so they do not cancel at the shared edges. Measured: inner DOFs 5.7e-5,
        curvature 0.75 % off on this patch (held below 1e-4 and 1 % so a change shows)."""
        r = sc.patch_test("mixed", bending="mitc3")
        self.assertLess(r["inner_error"], 1e-10)
        self.assertLess(r["curvature_error"], 1e-10)
        r = sc.patch_test("mixed", bending="dkt")
        self.assertLess(r["strain_error"], 1e-10)
        self.assertLess(r["inner_error"], 1e-4)
        self.assertLess(r["curvature_error"], 0.01)
        self.assertGreater(r["curvature_error"], 1e-6)


class PlateTest(unittest.TestCase):
    def test_navier_plate(self):
        """200 x 200 x 2 plate, 16 x 16 squares cut in two: DKT within 0.5 % of Navier (measured -0.31 %),
        mixed (triangles on half, MITC4 on the other) within 0.5 % (-0.20 %); the load totals q a b."""
        for kind in ("tri", "mixed"):
            r = sc.ss_plate(kind)
            self.assertLess(abs(r["w_centre"] / r["w_navier"] - 1.0), 0.005, kind)
            self.assertAlmostEqual(r["total_load"] / r["q_area"], 1.0, delta=1e-12)

    def test_dkt_converges_and_mitc3_is_stiffer(self):
        """DKT error 1.3 % at 8 x 8, 0.31 % at 16 x 16 (about h^2). MITC3 is stiffer on the same mesh
        (-3.6 % at 16 x 16, t = 2) and locks for thin plates on coarse meshes (measured -63 % at t = 0.2)."""
        e8 = 1.0 - sc.ss_plate("tri", nx=8, ny=8)["w_centre"] / sc.ss_plate("tri", nx=8, ny=8)["w_navier"]
        r16 = sc.ss_plate("tri")
        e16 = 1.0 - r16["w_centre"] / r16["w_navier"]
        self.assertGreater(e8 / e16, 3.0)
        m3 = sc.ss_plate("tri", bending="mitc3")
        self.assertLess(m3["w_centre"], r16["w_centre"])
        self.assertLess(abs(m3["w_centre"] / m3["w_navier"] - 1.0), 0.05)

    def test_cantilever_strip(self):
        """nu = 0 strip 200 x 20 x 2, end line load: DKT within 0.1 % of P L^3 / (3 E I) (Kirchhoff: no shear
        term), mixed with MITC4 within 0.1 %; MITC3 within 0.1 %."""
        for kind, bending in (("tri", "dkt"), ("mixed", "dkt"), ("tri", "mitc3")):
            r = sc.cantilever_strip(kind, bending=bending)
            self.assertLess(abs(r["w_tip"] / r["w_beam"] - 1.0), 0.001, (kind, bending))

    def test_plate_buckling_with_triangles(self):
        """Square 200 x 200 x 2 simply supported plate in uniaxial compression, 16 x 16 squares cut in two:
        sigma_cr within 3 % of k pi^2 D / (b^2 t), k = 4."""
        r = sc.tri_plate_buckling()
        self.assertLess(abs(r["sigma_cr"] / r["sigma_formula"] - 1.0), 0.03, r)

    def test_quality_check_reads_triangles(self):
        nodes, q = plate_shell(100.0, 60.0, 6, 4)
        model = FEModel(nodes)
        model.add(Tri3(split_quads(q), 2.0, E_ST, NU_ST))
        r = quality.model_quality(model)
        self.assertIn("tri_min_angle_deg", r["quality"]["metrics"])
        self.assertEqual(r["quality"]["topology"]["free_edges"], 20)
        self.assertEqual(r["quality"]["topology"]["flipped_normals"], 0)

    @SLOW
    def test_fine_navier(self):
        r = sc.ss_plate("tri", nx=32, ny=32)
        self.assertLess(abs(r["w_centre"] / r["w_navier"] - 1.0), 0.001)


if __name__ == "__main__":
    unittest.main()
