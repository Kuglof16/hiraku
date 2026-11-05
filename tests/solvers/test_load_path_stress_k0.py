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


D_P, T_P = 42.7, 2.3  # the roll bar's round tube
A_P = math.pi / 4 * (D_P ** 2 - (D_P - 2 * T_P) ** 2)
I_P = math.pi / 64 * (D_P ** 4 - (D_P - 2 * T_P) ** 4)


def frame(Mp=0.0, Tp=0.0, Np=0.0, **rail_kw):
    base = {"M_v": 0.0, "M_h": 0.0, "T": 0.0, "N": 0.0}
    base.update(rail_kw)
    ins = {"M_v": q(base["M_v"], "N*mm"), "M_h": q(base["M_h"], "N*mm"), "T": q(base["T"], "N*mm"),
           "N": q(base["N"], "N"), "b": q(B, "mm"), "h": q(H, "mm"), "t": q(T_W, "mm"), "A": q(A, "mm^2"),
           "I_v": q(IV, "mm^4"), "I_h": q(IH, "mm^4"), "s": q(700.0, "mm"), "f_y": q(245.0, "MPa"), "SF": q(1.5, "1"),
           "M_p": q(Mp, "N*mm"), "T_p": q(Tp, "N*mm"), "N_p": q(Np, "N"), "A_p": q(A_P, "mm^2"),
           "I_p": q(I_P, "mm^4"), "J_p": q(2 * I_P, "mm^4"), "c_p": q(D_P / 2, "mm"), "f_yp": q(215.0, "MPa")}
    return {k: v["value"] for k, v in REG.evaluate("load_path.frame_utilisation", ins)["outputs"].items()}


class FrameUtilisationTest(unittest.TestCase):
    def test_the_post_as_a_cantilever(self):
        """A mass m hung at height h and offset e ahead of the post's foot, pushed forward by a and down by g: the
        foot's moment m g (a h + e) about one axis, sigma = M c / I + N / A, u = sigma SF / f_y."""
        m, g, a, h, e = 87.6, 9.80665, 0.9, 600.0, 250.0
        M, N = m * g * (a * h + e), m * g
        got = frame(Mp=M, Np=N)
        sig = M * (D_P / 2) / I_P + N / A_P
        self.assertAlmostEqual(got["von_mises_post"], sig, delta=1e-9)
        self.assertAlmostEqual(got["u_post"], sig * 1.5 / 215.0, delta=1e-12)
        self.assertEqual(got["u"], got["u_post"])  # the post governs: nothing on the rail

    def test_twist_on_a_round_tube(self):
        Tq = 2.0e5
        self.assertAlmostEqual(frame(Tp=Tq)["von_mises_post"], math.sqrt(3) * Tq * (D_P / 2) / (2 * I_P), delta=1e-9)

    def test_the_rail_governs_without_a_post(self):
        got = frame(M_v=1.0e6)
        self.assertEqual(got["u_post"], 0.0)
        self.assertAlmostEqual(got["u"], rail(Mv=1.0e6)["u"], delta=1e-12)


class SeatsOnTheRollBarTest(unittest.TestCase):
    """Version 1 with the seats hung on the roll bar's posts (mount_seats): the post is the most used member in
    the tool, and K0's post (a cantilever from its foot that carries all of the seat and its occupant) is above it
    within R8's tolerance."""

    def test_k0_post_against_the_tool(self):
        from hiraku.catalog.builders import BUILDERS
        from hiraku.catalog.builders.load_path import frame_inputs
        from hiraku.designgen import ladder_4w as G
        from hiraku.solvers.shelf import LoadPath
        from tests.solvers.test_builders_load_path import REQ, SPECS, results

        d = G.build({"mount_seats": q(1.0, "1")})
        lc = next(c for c in BUILDERS["R8"](SPECS["R8"], REQ, d) if c["load_case_id"] == "R8-stress")
        k0 = {k: v["value"] for k, v in REG.evaluate("load_path.frame_utilisation", frame_inputs(d, lc))["outputs"].items()}
        r = results(LoadPath().run(d, lc, [{"prediction_id": "p"}], {}))
        self.assertTrue(r["max_utilisation"]["where"].startswith("rollbar_post"), r["max_utilisation"])
        self.assertEqual(k0["u"], k0["u_post"])
        tool = r["max_utilisation"]["value"]
        self.assertGreater(k0["u"], tool)  # the post carries everything: K0 the larger
        self.assertLessEqual(abs(tool - k0["u"]) / k0["u"], lc["tolerance_rel"], (k0["u"], tool))


if __name__ == "__main__":
    unittest.main()
