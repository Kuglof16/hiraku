"""Natural modes on the fe core: mass matrices of shells and bricks, cantilever plate and tubes against the
Euler-Bernoulli beam (lambda_1 = 1.875), and the frame of offroad-4w-v1 in shells next to the beam model."""

import json
import os
import unittest
from pathlib import Path

import numpy as np

from hiraku.solvers.fe import FEModel, eigen
from hiraku.solvers.fe import eigen_checks as ec
from hiraku.solvers.fe import frame as ff
from hiraku.solvers.fe.shell import MITC4
from hiraku.solvers.fe.solid import Hex20
from hiraku.solvers.k1_shell import element as mitc4

SLOW = unittest.skipUnless(os.environ.get("HIRAKU_SLOW_TESTS") == "1", "a finer mesh: set HIRAKU_SLOW_TESTS=1")
ROOT = Path(__file__).resolve().parents[2]
DESIGN = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))


class MassMatrixTest(unittest.TestCase):
    def setUp(self):
        self.nodes = np.array([[0, 0, 0], [30, 0, 0], [30, 20, 5], [0, 20, 5]], float)
        self.g = MITC4([[0, 1, 2, 3]], 2.0, 2e5, 0.3, 7.85e-9)

    def test_lumped_shell_mass_is_k1_shells(self):
        M = self.g.mass(self.nodes, lumped=True)[0]
        d = mitc4.lumped_mass(self.nodes[None], np.array([2.0]), np.array([7.85e-9]))[0]
        self.assertLess(np.abs(M - np.diag(d)).max(), 1e-20)

    def test_consistent_shell_rows_sum_to_the_lumped_masses_on_a_rectangle(self):
        M = self.g.mass(self.nodes)[0]
        L = self.g.mass(self.nodes, lumped=True)[0]
        for k in range(6):
            idx = 6 * np.arange(4) + k
            np.testing.assert_allclose(M[np.ix_(idx, idx)].sum(axis=1), np.diag(L)[idx], rtol=1e-12)
        self.assertAlmostEqual(M[0::6, 0::6].sum(), self.g.mass_total(self.nodes), delta=1e-15)
        self.assertGreater(np.linalg.eigvalsh(M).min(), 0.0)

    def test_hex20_masses(self):
        m = ec.bar_mesh(40.0, 10.0, 2, 1)
        g = Hex20(m.conn, 2e5, 0.3, 7.85e-9)
        total = g.mass_total(m.nodes)
        for lumped in (False, True):
            M = g.mass(m.nodes, lumped)
            self.assertAlmostEqual(M[:, 0::3, 0::3].sum(), total, delta=1e-12 * total)
            self.assertLess(np.abs(M - np.transpose(M, (0, 2, 1))).max(), 1e-20)
        d = np.einsum("eii->ei", g.mass(m.nodes, True))
        self.assertGreater(d.min(), 0.0)  # HRZ: no negative corner masses

    def test_free_plate_has_six_rigid_modes(self):
        nodes, q = ec.plate_shell(100.0, 60.0, 5, 3)
        model = FEModel(nodes)
        model.add(MITC4(q, 2.0, 2e5, 0.3, 7.85e-9))
        r = eigen.modes(model, 8)
        self.assertLess(r.info["sigma"], 0.0)
        self.assertLess(r.frequencies[5], 1e-3 * r.frequencies[6])
        np.testing.assert_allclose(np.einsum("ik,ik->k", r.shapes, r.M @ r.shapes), 1.0, rtol=1e-9)


class CantileverModesTest(unittest.TestCase):
    """First bending frequencies against f = 1.875^2 / (2 pi L^2) sqrt(E I / (rho A))."""

    def test_lambda1(self):
        self.assertAlmostEqual(ec.cantilever_lambda(1), 1.8751040687, places=9)
        self.assertAlmostEqual(ec.cantilever_lambda(2), 4.6940911330, places=9)

    def test_cantilever_plate(self):
        """500 x 50 x 2 strip: between the beam with E and the wide-plate bound E / (1 - nu^2) (4.8 % apart),
        within 1.5 % of the beam with E."""
        for lumped in (False, True):
            r = ec.cantilever_plate_modes(nx=40, ny=4, lumped=lumped)
            self.assertGreater(r["f"], r["f_beam"])
            self.assertLess(r["f"], r["f_plate_bound"])
            self.assertLess(r["f"] / r["f_beam"] - 1.0, 0.015)

    def test_tubes(self):
        """40 x 2 square and 40 mm round tubes 1000 mm long: shell bending within 1 % of the formula and of
        the beam model of the same section; y and z bending equal (symmetric sections)."""
        for shape in ("square", "round"):
            r = ec.tube_cantilever_modes(shape, n_c=16, n_l=25)
            self.assertLess(abs(r["f_y"] / r["f_formula"] - 1.0), 0.01, shape)
            self.assertLess(abs(r["f_beam_fe"] / r["f_formula"] - 1.0), 1e-4, shape)
            self.assertLess(abs(r["f_y"] / r["f_z"] - 1.0), 1e-4, shape)

    def test_solid_bar(self):
        r = ec.solid_cantilever_modes(n_len=10)
        self.assertLess(abs(r["f"] / r["f_formula"] - 1.0), 0.015)

    @SLOW
    def test_finer(self):
        for shape in ("square", "round"):
            r = ec.tube_cantilever_modes(shape, n_c=32, n_l=100)
            self.assertLess(abs(r["f_y"] / r["f_formula"] - 1.0), 0.01, shape)
        r = ec.solid_cantilever_modes(n_len=40)
        self.assertLess(abs(r["f"] / r["f_formula"] - 1.0), 0.005)


class FrameModesTest(unittest.TestCase):
    """offroad-4w-v1: shell frame modes (k1_shell mesh, 20 mm here) and the beam N1 model's. The three
    lowest modes (the body on the springs) agree within 2 % and pair one to one by MAC; every beam mode up to
    the eighth has a shell mode with MAC above 0.85. The flexible modes are lower in the shell (its joints
    are not rigid), which is the point of comparing."""

    @classmethod
    def setUpClass(cls):
        cls.r = ff.frame_modes(DESIGN, 8, element_size_mm=20.0)

    def test_rigid_body_modes_match(self):
        r = self.r
        for i in range(3):
            self.assertEqual(r["best"][i], i)
            self.assertLess(abs(r["shell_hz"][i] / r["beam_hz"][i] - 1.0), 0.02)

    def test_every_beam_mode_has_a_shell_mode(self):
        r = self.r
        self.assertGreater(min(r["mac"][i, r["best"][i]] for i in range(8)), 0.85)
        self.assertEqual(len(set(r["best"])), 8)

    def test_twist_mode_is_softer_in_the_shell(self):
        r = self.r
        i = int(np.argmax(r["beam_flexibility"]))  # the frame twist of the beam model
        j = r["best"][i]
        self.assertLess(r["shell_hz"][j], r["beam_hz"][i])
        self.assertGreater(r["shell_hz"][j], 0.8 * r["beam_hz"][i])

    def test_mass(self):
        self.assertAlmostEqual(self.r["model_mass_kg"], self.r["shell_mass_kg"] + self.r["part_mass_kg"], delta=1e-6)


class FrameTorsionTest(unittest.TestCase):
    def test_one_wheel_lift(self):
        """Lift FL by 1 mm with the other wheels held: the four reactions are equal and opposite on the
        diagonals; the stiffness is within 10 % of the beam model's."""
        r = ff.frame_torsion(DESIGN, element_size_mm=20.0)
        R = r["reactions_N"]
        self.assertGreater(R["FL"], 0.0)
        self.assertLess(abs(r["sum_reactions_N"]), 1e-6 * abs(R["FL"]))
        for k, s in (("FR", -1), ("RL", -1), ("RR", 1)):
            self.assertAlmostEqual(R[k], s * R["FL"], delta=1e-6 * abs(R["FL"]))
        b = ff.beam_torsion(DESIGN)
        self.assertLess(abs(r["stiffness_Nm_per_deg"] / b["stiffness_Nm_per_deg"] - 1.0), 0.10)

    @SLOW
    def test_fine_mesh(self):
        r = ff.frame_torsion(DESIGN)
        b = ff.beam_torsion(DESIGN)
        self.assertLess(abs(r["stiffness_Nm_per_deg"] / b["stiffness_Nm_per_deg"] - 1.0), 0.10)
        m = ff.frame_modes(DESIGN, 8)
        self.assertGreater(min(m["mac"][i, m["best"][i]] for i in range(8)), 0.85)


if __name__ == "__main__":
    unittest.main()
