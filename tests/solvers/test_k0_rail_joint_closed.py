"""K0 frame_static.rail_joint_stress_closed: the rear axle joint with the ladder closed behind it."""

import unittest

from hiraku.solvers import formula_registry

REG = formula_registry()


def q(v, u):
    return {"value": v, "unit": u}


BASE = {"M_oh": q(1.2e5, "N*mm"), "F": q(1300.0, "N"), "W": q(2300.0, "N"), "z": q(380.0, "mm"), "e": q(260.0, "mm"),
        "L1": q(620.0, "mm"), "L_c": q(700.0, "mm"), "E": q(205000.0, "MPa"), "nu": q(0.3, "1"), "b": q(40.0, "mm"),
        "h": q(80.0, "mm"), "t": q(4.5, "mm"), "b_c": q(40.0, "mm"), "h_c": q(50.0, "mm"), "t_c": q(2.3, "mm")}


def closed(**kw):
    ins = {**BASE, "a": q(400.0, "mm"), "L_e": q(700.0, "mm"), "b_e": q(40.0, "mm"), "h_e": q(50.0, "mm"),
           "t_e": q(2.3, "mm")}
    ins.update({k: q(v, ins[k]["unit"]) for k, v in kw.items()})
    return {k: v["value"] for k, v in REG.evaluate("frame_static.rail_joint_stress_closed", ins)["outputs"].items()}


def tube(b, h, t):
    bi, hi = b - 2 * t, h - 2 * t
    return {"A": b * h - bi * hi, "Iv": (b * h ** 3 - bi * hi ** 3) / 12, "Il": (h * b ** 3 - hi * bi ** 3) / 12,
            "Am": (b - t) * (h - t), "J": 4 * ((b - t) * (h - t)) ** 2 * t / (2 * ((b - t) + (h - t)))}


class ClosedJointTest(unittest.TestCase):
    def test_a_long_free_overhang_is_the_open_formula(self):
        """With the rail behind very long, it takes almost none: the open formula's value."""
        open_ = REG.evaluate("frame_static.rail_joint_stress", BASE)["outputs"]["sigma"]["value"]
        got = closed(a=1e9, L_e=1e9)
        self.assertAlmostEqual(got["sigma"], open_, delta=1e-6 * open_)

    def test_one_step_of_moment_distribution_by_hand(self):
        r, c, e_ = tube(40, 80, 4.5), tube(40, 50, 2.3), tube(40, 50, 2.3)
        E = 205000.0
        G = E / 2.6
        a, Le, L1, Lc = 400.0, 700.0, 620.0, 700.0
        k_t, k_cv = G * r["J"] / L1, 2 * E * c["Iv"] / Lc
        k_bt = 1 / (a / (G * r["J"]) + Le / (2 * E * e_["Iv"]))
        cc = E * r["Il"] / a
        k_e = 2 * E * e_["Il"] / Le
        k_bl = 4 * cc * (3 * cc + k_e) / (4 * cc + k_e)
        k_l, k_cl = 4 * E * r["Il"] / L1, 2 * E * c["Il"] / Lc
        T_b = 2300 * 260 * k_bt / (k_t + k_cv + k_bt)
        M_b = 1300 * 260 * k_bl / (k_l + k_cl + k_bl)
        s = 1300 / r["A"] + (1.2e5 + 1300 * 380) * 40 / r["Iv"] + M_b * 20 / r["Il"]
        tau = T_b / (2 * r["Am"] * 4.5)
        got = closed()
        self.assertAlmostEqual(got["sigma_behind"], (s ** 2 + 3 * tau ** 2) ** 0.5, delta=1e-9)
        self.assertEqual(got["sigma"], max(got["sigma_ahead"], got["sigma_behind"]))

    def test_closing_takes_load_off(self):
        """The rail behind shares the twist and the sideways moment: less than the open formula."""
        open_ = REG.evaluate("frame_static.rail_joint_stress", BASE)["outputs"]["sigma"]["value"]
        self.assertLess(closed()["sigma"], open_)


if __name__ == "__main__":
    unittest.main()
