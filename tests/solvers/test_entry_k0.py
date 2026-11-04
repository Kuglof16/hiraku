"""H7 (proposed) getting in and out: the K0 formulas entry.opening_width, entry.opening_height, entry.step_height,
entry.blocked and entry.handholds, and the entry_opening tool's formulas registered."""

from __future__ import annotations

import unittest

from hiraku import solvers
from hiraku.solvers.shelf import EntryOpening

FORMULAS = solvers.formula_registry()


class RegisteredTest(unittest.TestCase):
    def test_tool_formulas(self):
        t = EntryOpening()
        for f in t.formulas:
            self.assertIn(f, FORMULAS)
        self.assertLessEqual({p.formula_id for p in t.predicts}, set(t.formulas))


class FormulaTest(unittest.TestCase):
    def q(self, v, u="mm"):
        return {"value": v, "unit": u}

    def test_width(self):
        got = FORMULAS.evaluate("entry.opening_width", {"x_f": self.q(-325.0), "x_b": self.q(-1295.0),
                                                        "x_p": self.q(-770.0), "d": self.q(42.7)})
        self.assertAlmostEqual(got["outputs"]["W"]["value"], -325.0 + 770.0 - 21.35)
        got = FORMULAS.evaluate("entry.opening_width", {"x_f": self.q(-325.0), "x_b": self.q(-1295.0),
                                                        "x_p": self.q(-1500.0), "d": self.q(42.7)})
        self.assertAlmostEqual(got["outputs"]["W"]["value"], 970.0)  # the post behind the seat: the whole span

    def test_height(self):
        base = {"x_f": self.q(-325.0), "x_b": self.q(-1295.0), "x_p": self.q(-770.0), "d": self.q(42.7),
                "h_low": self.q(82.0), "H_cap": self.q(3000.0)}
        self.assertAlmostEqual(FORMULAS.evaluate("entry.opening_height", {**base, "W_req": self.q(450.0)})["outputs"]["H"]["value"],
                               82.0 - 21.35)
        self.assertEqual(FORMULAS.evaluate("entry.opening_height", {**base, "W_req": self.q(400.0)})["outputs"]["H"]["value"],
                         3000.0)

    def test_step_and_counts(self):
        got = FORMULAS.evaluate("entry.step_height", {"z_rail": self.q(380.0), "h_rail": self.q(80.0), "gap": self.q(10.0),
                                                      "t": self.q(3.2), "z_g": self.q(0.0)})
        self.assertAlmostEqual(got["outputs"]["h"]["value"], 433.2)
        n = FORMULAS.evaluate("entry.blocked", {"n_frame": self.q(2.0, "1"), "n_fixed": self.q(1.0, "1")})
        self.assertEqual(n["outputs"]["n"]["value"], 3.0)
        self.assertEqual(FORMULAS.evaluate("entry.handholds", {"n_grip": self.q(3.0, "1")})["outputs"]["n"]["value"], 3.0)


if __name__ == "__main__":
    unittest.main()
