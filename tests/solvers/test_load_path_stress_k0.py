"""K0 load_path.rail_utilisation against closed forms, and the builder's stress inputs on the demo."""

import math
import unittest

from hiraku.solvers import formula_registry

REG = formula_registry()
B, H, T_W = 40.0, 80.0, 2.3
A = B * H - (B - 2 * T_W) * (H - 2 * T_W)
IV = (B * H ** 3 - (B - 2 * T_W) * (H - 2 * T_W) ** 3) / 12
IH = (H * B ** 3 - (H - 2 * T_W) * (B - 2 * T_W) ** 3) / 12


def q(v, u):
    return {"value": v, "unit": u}


def rail(Mv=0.0, Mh=0.0, T=0.0, N=0.0, s=700.0, fy=245.0, SF=1.5):
    ins = {"M_v": q(Mv, "N*mm"), "M_h": q(Mh, "N*mm"), "T": q(T, "N*mm"), "N": q(N, "N"), "b": q(B, "mm"),
           "h": q(H, "mm"), "t": q(T_W, "mm"), "A": q(A, "mm^2"), "I_v": q(IV, "mm^4"), "I_h": q(IH, "mm^4"),
           "s": q(s, "mm"), "f_y": q(fy, "MPa"), "SF": q(SF, "1")}
    return {k: v["value"] for k, v in REG.evaluate("load_path.rail_utilisation", ins)["outputs"].items()}


class RailUtilisationTest(unittest.TestCase):
    def test_bending_alone(self):
        M = 1.0e6
        got = rail(Mv=M)
        self.assertAlmostEqual(got["sigma"], M * (H / 2) / IV, delta=1e-9)
        self.assertAlmostEqual(got["u"], M * (H / 2) / IV * 1.5 / 245.0, delta=1e-12)

    def test_two_rails_sideways(self):
        """The two rails as one section about the frame's centre line: I = 2 (A (s/2)^2 + I_h), fibre s/2 + b/2."""
        M, s = 1.0e6, 700.0
        self.assertAlmostEqual(rail(Mh=M, s=s)["sigma"], M * (s / 2 + B / 2) / (2 * (A * (s / 2) ** 2 + IH)), delta=1e-9)

    def test_twist_bredt_and_von_mises(self):
        Tq, M = 2.0e5, 5.0e5
        tau = Tq / (2 * (B - T_W) * (H - T_W) * T_W)
        sig = M * (H / 2) / IV
        got = rail(Mv=M, T=Tq)
        self.assertAlmostEqual(got["tau"], tau, delta=1e-9)
        self.assertAlmostEqual(got["von_mises"], math.sqrt(sig ** 2 + 3 * tau ** 2), delta=1e-9)

    def test_axial(self):
        self.assertAlmostEqual(rail(N=5000.0)["sigma"], 5000.0 / A, delta=1e-12)


class StressInputsTest(unittest.TestCase):
    def test_demo_and_the_safety_factor(self):
        from hiraku.catalog.builders.load_path import stress_inputs
        from hiraku.designgen import ladder_4w as G

        d = G.build()
        lc = {"params": {"accel_vertical": q(1.0, "g"), "accel_lateral": q(0.7, "g"), "accel_longitudinal": q(0.9, "g"),
                         "safety_factor": q(1.5, "1"), "loading": "two_cargo"}}
        ins = stress_inputs(d, lc)
        self.assertGreater(ins["M_v"]["value"], 0.0)
        self.assertAlmostEqual(ins["s"]["value"], 700.0, delta=1e-6)  # the demo's rail spacing
        u15 = REG.evaluate("load_path.rail_utilisation", ins)["outputs"]["u"]["value"]
        lc["params"]["safety_factor"] = q(3.0, "1")
        u30 = REG.evaluate("load_path.rail_utilisation", stress_inputs(d, lc))["outputs"]["u"]["value"]
        self.assertAlmostEqual(u30, 2.0 * u15, delta=1e-9)


if __name__ == "__main__":
    unittest.main()
