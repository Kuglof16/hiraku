"""The analysis dept's shell solver (hiraku.solvers.k1_shell) against qa.known_answers.

The solver's own checks compare with its own expected values; here the
expected values come from qa.known_answers only (Pilkey's fit and Kirsch for
the hole, the tube's mid-line section with the webs' shear for the cantilever,
Bredt for the torsion). Only the solver's results are read from it: the plate
and tube helpers of k1_shell.checks build and solve the models, this file
picks the values from the solved fields. E and nu are the solver's check
material (inputs, like ka.SHELL). Skipped until the shell solver is in.
"""

import importlib
import math
import unittest

import numpy as np

from qa import known_answers as ka


def shell_or_skip():
    try:
        checks = importlib.import_module("hiraku.solvers.k1_shell.checks")
        shell = importlib.import_module("hiraku.solvers.k1_shell")
    except ModuleNotFoundError as e:
        raise unittest.SkipTest(f"the shell solver is not in this checkout yet (analysis dept): {e}")
    need = [n for n in ("holed_plate_kt", "tube_cantilever", "E_ST", "NU_ST") if not hasattr(checks, n)]
    if need or not hasattr(shell, "element_stresses"):
        raise unittest.SkipTest(f"hiraku.solvers.k1_shell.checks has no {', '.join(need) or 'element_stresses'}")
    return checks, shell


def ring_twist(nodes, U, x):
    """Least-squares rotation about +x of the ring of nodes at axial position x (axis on y = z = 0)."""
    sel = np.nonzero(np.abs(nodes[:, 0] - x) < 1e-6)[0]
    r = nodes[sel].copy()
    r[:, 0] = 0.0
    u = np.stack([U[6 * sel + k] for k in range(3)], axis=1)
    return float(np.sum(np.cross(r, u)[:, 0]) / np.sum(r[:, 1] ** 2 + r[:, 2] ** 2))


class HoledPlate(unittest.TestCase):
    """Kt on the gross stress of a central hole, Pilkey's fit (3 at d/W = 0, Kirsch)."""

    MESH = {"small_hole": (24, 28), "wide_hole": (12, 14)}  # a small hole needs a finer ring

    @classmethod
    def setUpClass(cls):
        cls.checks, _ = shell_or_skip()

    def test_kt_of_the_hole_cases(self):
        for c in ka.HOLE_CASES:
            with self.subTest(case=c["case"]):
                nq, nr = self.MESH[c["case"]]
                got = self.checks.holed_plate_kt(W=c["W"], d=c["d"], length=c["length"], n_quarter=nq,
                                                 n_radial=nr, t=c["t"], sigma=c["sigma"])["kt"]
                expected = ka.hole_kt_gross(c["d"] / c["W"])
                self.assertAlmostEqual(got / expected, 1.0, delta=0.02, msg=f"Kt {got:.4f}, expected {expected:.4f}")

    def test_small_hole_is_kirsch(self):
        c = ka.HOLE_CASES[0]
        got = self.checks.holed_plate_kt(W=c["W"], d=c["d"], length=c["length"], n_quarter=24, n_radial=28,
                                         t=c["t"], sigma=c["sigma"])["kt"]
        self.assertAlmostEqual(got, ka.kirsch_kt(), delta=0.06)  # d/W 0.05: within 2 % of 3


class BoxTube(unittest.TestCase):
    """ka.BOX clamped at x = 0: tip load (bending + the webs' shear) and tip torque (Bredt)."""

    @classmethod
    def setUpClass(cls):
        cls.checks, cls.shell = shell_or_skip()
        b = ka.BOX
        cls.E, cls.nu = cls.checks.E_ST, cls.checks.NU_ST
        cls.res = cls.checks.tube_cantilever(size=10.0, L=b["L"], b=b["b"], h=b["h"], t=b["t"], P=b["P"], T=b["T"])

    def test_tip_deflection(self):
        b, r = ka.BOX, self.res
        got = -r["U"][6 * r["tip"] + 2, 0]
        expected = ka.cantilever_box_tip_load(b["P"], b["L"], self.E, self.nu, b["b"], b["h"], b["t"])["deflection"]
        self.assertAlmostEqual(got / expected, 1.0, delta=0.01, msg=f"{got:.5f} mm, expected {expected:.5f} mm")

    def test_top_wall_stress_along_the_tube(self):
        # mid-surface axial stress of the top wall (z = +h_m/2), away from both ends: M c / I on the mid-line
        b, r = ka.BOX, self.res
        hm = b["h"] - b["t"]
        st = self.shell.element_stresses(r["model"], r["U"][:, 0])
        c = st.centres
        sxx = st.in_plane((1.0, 0.0, 0.0), "mid")[:, 0]
        top = np.nonzero((np.abs(c[:, 2] - hm / 2) < 1e-3) & (c[:, 0] > b["h"]) & (c[:, 0] < b["L"] - b["h"]))[0]
        self.assertGreater(len(top), 10)
        worst = 0.0
        for e in top:
            expected = ka.cantilever_box_tip_load(b["P"], b["L"], self.E, self.nu, b["b"], b["h"], b["t"],
                                                  x=float(c[e, 0]))["flange_stress"]
            worst = max(worst, abs(sxx[e] / expected - 1))
        self.assertLess(worst, 0.02, f"largest relative difference {worst:.4f}")

    def test_bredt_twist_rate_and_shear(self):
        b, r = ka.BOX, self.res
        model, U = r["torsion_model"], r["U_torsion"]
        xs = np.unique(np.round(model.nodes[:, 0], 6))
        x0, x1 = (float(xs[np.argmin(np.abs(xs - f * b["L"]))]) for f in (0.3, 0.7))
        rate = (ring_twist(model.nodes, U, x1) - ring_twist(model.nodes, U, x0)) / (x1 - x0)
        expected = ka.bredt_torsion(b["T"], b["L"], ka.shear_modulus(self.E, self.nu), b["b"], b["h"], b["t"])
        # the shell walls also twist as strips: Bredt plus s t^3 / 3 (0.37 % stiffer than Bredt alone here)
        self.assertAlmostEqual(abs(rate) / expected["twist_rate_walls"], 1.0, delta=0.001)
        self.assertAlmostEqual(abs(rate) / expected["twist_rate"], 1.0, delta=0.01)
        st = self.shell.element_stresses(model, U)
        c = st.centres
        mid = np.nonzero((c[:, 0] > 0.3 * b["L"]) & (c[:, 0] < 0.7 * b["L"]))[0]
        tau = np.abs(st.in_plane((1.0, 0.0, 0.0), "mid")[mid, 2])
        self.assertLess(float(np.max(np.abs(tau / expected["shear_stress_walls"] - 1))), 0.001)


if __name__ == "__main__":
    unittest.main()
