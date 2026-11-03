"""K0 load_path.bar_on_posts against closed forms, and the builder's push inputs on the side bars."""

import unittest

from hiraku.solvers import formula_registry

REG = formula_registry()
E, I = 205000.0, 59750.0


def q(v, u):
    return {"value": v, "unit": u}


def push(F, a, b, hA=0.0, hB=0.0, IA=I, IB=I):
    ins = {"F": q(F, "N"), "a": q(a, "mm"), "b": q(b, "mm"), "E": q(E, "MPa"), "I": q(I, "mm^4"),
           "h_A": q(hA, "mm"), "E_A": q(E, "MPa"), "I_A": q(IA, "mm^4"), "h_B": q(hB, "mm"), "E_B": q(E, "MPa"),
           "I_B": q(IB, "mm^4")}
    return REG.evaluate("load_path.bar_on_posts", ins)["outputs"]["delta"]["value"]


class BarOnPostsTest(unittest.TestCase):
    def test_bar_alone_mid_span(self):
        """Held ends (no posts), load at mid-span: P L^3 / (192 E I)."""
        F, L = 725.0, 550.0
        self.assertAlmostEqual(push(F, L / 2, L / 2), F * L ** 3 / (192 * E * I), delta=1e-9)

    def test_post_at_the_pushed_end(self):
        """Pushed right at end A (b = L): the post A takes all of F as a cantilever, F h^3 / (3 E I)."""
        F, h = 725.0, 950.0
        got = push(F, 1e-6, 550.0, hA=h)
        self.assertAlmostEqual(got, F * h ** 3 / (3 * E * I), delta=1e-6 * got)

    def test_posts_share_by_the_lever(self):
        """Two equal posts, load at a third: post A takes 2/3 F and moves (2/3) F h^3/(3EI); the point moves by
        the movements interpolated, (2/3)^2 + (1/3)^2 of F h^3 / (3 E I), plus the bar's own."""
        F, L, h = 725.0, 600.0, 900.0
        a, b = L / 3, 2 * L / 3
        expect = ((b / L) ** 2 + (a / L) ** 2) * F * h ** 3 / (3 * E * I) + F * a ** 3 * b ** 3 / (3 * E * I * L ** 3)
        self.assertAlmostEqual(push(F, a, b, hA=h, hB=h), expect, delta=1e-9 * expect)


class PushInputsTest(unittest.TestCase):
    def test_side_bars_and_none(self):
        from hiraku.catalog.builders.load_path import push_inputs
        from hiraku.contract.loading import on_board
        from hiraku.designgen import ladder_4w as G

        d = on_board(G.build(G.apply_choices({"side_guard": "bars"}, {})), "two_cargo")
        ins = push_inputs(d, {"params": {"push_force": q(725.0, "N")}})
        self.assertEqual(ins["F"]["value"], 725.0)
        self.assertGreater(ins["h_A"]["value"], 500.0)  # the side posts stand from the outriggers
        from hiraku.solvers.k0_handcalc.vehicle import member_props

        bars = {round(p["length"], 6) for m, p in member_props(d).items() if m.startswith("side_bar_")}
        self.assertIn(round(ins["a"]["value"] + ins["b"]["value"], 6), bars)  # a + b is a side bar's length
        with self.assertRaises(ValueError):  # v1: nothing outboard of point R (the plan's reader refuses)
            push_inputs(on_board(G.build(), "two_cargo"), {"params": {}})


if __name__ == "__main__":
    unittest.main()
