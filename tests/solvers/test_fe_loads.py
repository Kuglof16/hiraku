"""Load types on the fe core: prescribed displacements with their reactions, pressure on shells and solids,
temperature (hiraku.solvers.fe.load_checks names the formulas)."""

import os
import unittest

import numpy as np

from hiraku.solvers.fe import FEModel, StaticSolver, loads
from hiraku.solvers.fe import load_checks as lc
from hiraku.solvers.fe.connect import rigid_link
from hiraku.solvers.fe.eigen_checks import plate_shell
from hiraku.solvers.fe.shell import MITC4
from hiraku.solvers.fe.solid import Hex8

SLOW = unittest.skipUnless(os.environ.get("HIRAKU_SLOW_TESTS") == "1", "a finer mesh: set HIRAKU_SLOW_TESTS=1")


class PrescribedDisplacementTest(unittest.TestCase):
    def test_beam_tip_force_is_3EId_over_L3(self):
        r = lc.prescribed_beam()
        self.assertAlmostEqual(r["tip_force"] / r["expected"], 1.0, delta=1e-9)
        self.assertAlmostEqual(r["root_force"], -r["tip_force"], delta=1e-9 * r["expected"])
        self.assertAlmostEqual(r["root_moment"], -r["tip_force"] * r["L"], delta=1e-9 * r["expected"] * r["L"])

    def test_shell_tube_through_a_rigid_link(self):
        """The prescribed node is the master of an RBE2 with no element of its own: its reaction is what the
        link carries (within 0.2 % of the shear-corrected cantilever, and equal to the base's)."""
        r = lc.prescribed_tube()
        self.assertLess(abs(r["tip_force"] / r["expected"] - 1.0), 0.002)
        self.assertAlmostEqual(r["base_force"], -r["tip_force"], delta=1e-6 * r["tip_force"])

    def test_reaction_of_a_link_master_equals_the_load_it_holds(self):
        """A plate strip clamped at one end, its free edge tied to a master node that is held: the master's
        reaction balances the load put on the strip."""
        nodes, q = plate_shell(100.0, 20.0, 10, 2)
        model = FEModel(nodes)
        model.add(MITC4(q, 2.0, 2e5, 0.3))
        model.fix(np.nonzero(nodes[:, 0] < 1e-9)[0], "all")
        m = model.add_node((100.0, 10.0, 0.0))
        model.add(rigid_link(m, np.nonzero(nodes[:, 0] > 100 - 1e-9)[0], model.nodes))
        model.fix(m, [2])
        F = np.zeros((len(model.nodes), 6))
        F[np.nonzero(np.isclose(nodes[:, 0], 50.0))[0], 2] = 10.0
        sol = StaticSolver(model).solve(F)
        R = sol.reactions()
        self.assertAlmostEqual(R[:, 2].sum(), -F[:, 2].sum(), delta=1e-9 * F[:, 2].sum())
        self.assertLess(R[m, 2], 0.0)


class PressureTest(unittest.TestCase):
    def test_thin_cylinder_hoop_stress(self):
        """48-facet cylinder, R 50, t 1, p 1: the hoop stress is p r_f / t exactly (r_f = R cos(pi/48), the
        facets' distance from the axis) and within 0.25 % of p R / t; no axial stress; the radial
        displacement within 0.5 % of p R^2 / (E t); the pressure has no resultant."""
        r = lc.cylinder_pressure()
        self.assertAlmostEqual(r["hoop_mean"] / r["expected"], 1.0, delta=1e-9)
        self.assertLess(r["hoop_spread"], 1e-9 * r["expected"])
        self.assertLess(abs(r["hoop_mean"] / r["expected_R"] - 1.0), 0.0025)
        self.assertLess(r["axial_max"], 1e-9 * r["expected"])
        self.assertLess(abs(r["radial"] / r["radial_expected"] - 1.0), 0.005)
        self.assertLess(r["total_force"], 1e-9)

    def test_simply_supported_plate_uniform_load(self):
        """200 x 200 x 2 plate, q 0.01 MPa, 16 x 16 MITC4: centre deflection and bending stress within 1 % of
        Navier's series (101 x 101 odd terms)."""
        r = lc.ss_plate_pressure(nx=16, ny=16)
        self.assertAlmostEqual(r["total_load"], r["q_area"], delta=1e-9 * r["q_area"])
        self.assertLess(abs(r["w_centre"] / r["w_navier"] - 1.0), 0.01)
        self.assertLess(abs(r["sigma_top"] / r["sigma_navier"] - 1.0), 0.01)
        self.assertGreater(r["sigma_top"], 0.0)

    @SLOW
    def test_fine_plate(self):
        r = lc.ss_plate_pressure(nx=64, ny=64)
        self.assertLess(abs(r["w_centre"] / r["w_navier"] - 1.0), 0.001)
        self.assertLess(abs(r["sigma_top"] / r["sigma_navier"] - 1.0), 0.001)

    def test_hex20_face_shares(self):
        """A flat square 8-node face: -1/12 of the load on each corner, 1/3 on each mid-edge node."""
        r = lc.hex20_face_pressure()
        np.testing.assert_allclose(r["corner_share"], 1.0 / 12.0, atol=1e-12)  # load is along -x: shares flip sign
        np.testing.assert_allclose(r["mid_share"], -1.0 / 3.0, atol=1e-12)
        self.assertAlmostEqual(r["sum"], -1.0, delta=1e-12)

    def test_hex20_block_pressed(self):
        r = lc.hex20_block_pressure()
        self.assertAlmostEqual(r["sxx_min"], r["expected"], delta=1e-9)
        self.assertAlmostEqual(r["sxx_max"], r["expected"], delta=1e-9)
        self.assertLess(r["others"], 1e-9)

    def test_shell_pressure_side(self):
        nodes, q = plate_shell(10.0, 10.0, 1, 1)
        model = FEModel(nodes)
        g = model.add(MITC4(q, 1.0, 2e5, 0.3))
        np.testing.assert_allclose(loads.shell_pressure(model, g, 2.0, "bottom")[:, 2], 50.0)
        np.testing.assert_allclose(loads.shell_pressure(model, g, 2.0, "top")[:, 2], -50.0)
        self.assertEqual(np.abs(loads.shell_pressure(model, g, 2.0, elements=[])).max(), 0.0)


class ThermalTest(unittest.TestCase):
    def test_restrained_bar(self):
        """alpha 1.2e-5, dT 50: sigma = -E alpha dT = -123 MPa in the beam, the HEX20 bar and the MITC4 strip;
        no other stress; the bar's free sideways growth (1 + nu) alpha dT b."""
        r = lc.restrained_bar()
        e = r["expected"]
        self.assertAlmostEqual(r["beam_sigma"], e, delta=1e-9 * abs(e))
        for lo_hi in (r["solid_sxx"], r["shell_sxx"]):
            self.assertAlmostEqual(lo_hi[0], e, delta=1e-9 * abs(e))
            self.assertAlmostEqual(lo_hi[1], e, delta=1e-9 * abs(e))
        self.assertLess(r["solid_others"], 1e-9 * abs(e))
        self.assertLess(r["shell_syy"], 1e-9 * abs(e))
        self.assertAlmostEqual(r["solid_lateral"], r["solid_lateral_expected"], delta=1e-9)

    def test_free_plate_bends_without_stress(self):
        """Top 20 K warmer than the bottom, t 2: curvature alpha dT / t = 1.2e-4 /mm in x and y, no twist, no
        stress; the thermal load has no resultant."""
        r = lc.free_plate_gradient()
        np.testing.assert_allclose(r["kappa"][:, :2], r["expected"], rtol=1e-9)
        self.assertLess(np.abs(r["kappa"][:, 2]).max(), 1e-9 * r["expected"])
        self.assertLess(r["stress_max"], 1e-6)
        self.assertLess(r["load_resultant"], 1e-6)

    def test_solid_nodal_temperature_when_nodes_and_elements_are_as_many(self):
        """A cube split into 8 coincident HEX8 of E/8 each: 8 nodes and 8 elements. A per-node temperature
        (nodal=True) gives the same loads and stresses as the one full cube; per-element values are uniform in
        each element; a shape that fits neither is refused."""
        nodes = np.array([[x, y, z] for z in (0.0, 10.0) for y in (0.0, 10.0) for x in (0.0, 10.0)])
        cube = [0, 1, 3, 2, 4, 5, 7, 6]
        E, nu, alpha = 2e5, 0.3, 1.2e-5
        T = 5.0 + 3.0 * nodes[:, 0] - 2.0 * nodes[:, 1] + nodes[:, 2]  # per node, not uniform
        one = FEModel(nodes)
        g1 = one.add(Hex8([cube], E, nu))
        eight = FEModel(nodes)
        g8 = eight.add(Hex8([cube] * 8, E / 8, nu))
        self.assertEqual(len(g8.conn), len(nodes))
        F1 = loads.solid_thermal(one, g1, alpha, T, nodal=True)
        F8 = loads.solid_thermal(eight, g8, alpha, T, nodal=True)
        np.testing.assert_allclose(F8, F1, rtol=1e-12, atol=1e-12 * np.abs(F1).max())
        # read as per element the same array is a different load (the mistake this guards against)
        Fe = loads.solid_thermal(eight, g8, alpha, T)
        self.assertGreater(np.abs(Fe - F1).max(), 1e-3 * np.abs(F1).max())
        np.testing.assert_allclose(Fe, sum(loads.solid_thermal(one, g1, alpha, t) for t in T) / 8, rtol=1e-12,
                                   atol=1e-12 * np.abs(Fe).max())
        # stresses of a given displacement with the nodal temperature taken off: same in both models
        ue = np.random.default_rng(0).normal(scale=1e-3, size=24)
        s1 = g1.gauss_stresses(nodes, ue[None, :], thermal=(alpha, T, True))
        s8 = g8.gauss_stresses(nodes, np.tile(ue, (8, 1)), thermal=(alpha, T, True))
        for e in range(8):
            np.testing.assert_allclose(s8[e] * 8, s1[0], rtol=1e-12, atol=1e-9)
        with self.assertRaises(ValueError):
            loads.solid_thermal(one, g1, alpha, T)  # 8 values for 1 element without nodal=True
        with self.assertRaises(ValueError):
            loads.solid_thermal(one, g1, alpha, T[:4], nodal=True)  # too few nodes
        with self.assertRaises(ValueError):
            g1.gauss_stresses(nodes, ue[None, :], thermal=(alpha, np.ones((1, 8))))


if __name__ == "__main__":
    unittest.main()
