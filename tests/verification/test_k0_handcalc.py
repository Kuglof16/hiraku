"""K0 hand calculation (hiraku.solvers.k0_handcalc) against closed forms.

The registry shape is FormulaRegistry in hiraku/contract/protocols.py:
list_formulas() -> [formula], evaluate(formula_id, {name: quantity}) ->
{"outputs": {name: quantity}, "terms": [term], "expression": str}.
Expected values come from qa.known_answers; unit factors from scipy.constants.
"""

import math
import unittest

from hiraku import contract
from qa import known_answers as ka

from ._solvers import formula_registry, import_or_skip

STEP_ID = "step_climb.wheel_force_rigid"  # the id used in hiraku/contract/examples/formula.json


def q(value, unit):
    return {"value": value, "unit": unit}


class K0Case(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        mod = import_or_skip("hiraku.solvers.k0_handcalc", "FormulaRegistry (list_formulas, evaluate)")
        cls.reg = formula_registry(mod)
        cls.formulas = {f["formula_id"]: f for f in cls.reg.list_formulas()}

    def formula_or_skip(self, formula_id):
        if formula_id not in self.formulas:
            self.skipTest(f"formula {formula_id!r} is not registered in k0_handcalc yet")
        return self.formulas[formula_id]

    def output_in(self, out, name, unit):
        """Output `name` converted to `unit` with the QA unit table."""
        got = out["outputs"][name]
        return ka.convert(got["value"], got["unit"], unit)


class Registry(K0Case):
    def test_every_formula_fits_the_contract(self):
        self.assertTrue(self.formulas, "list_formulas() returned nothing")
        for fid, f in self.formulas.items():
            with self.subTest(formula=fid):
                self.assertEqual(contract.errors("formula", f), [])

    def test_every_formula_says_where_it_comes_from_and_what_it_assumes(self):
        for fid, f in self.formulas.items():
            with self.subTest(formula=fid):
                self.assertTrue(f["source"].strip(), "empty source")
                self.assertTrue(f["assumptions"], "no assumptions listed")

    def test_ids_are_unique(self):
        ids = [f["formula_id"] for f in self.reg.list_formulas()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_mvp_formula_is_registered(self):
        self.assertIn(STEP_ID, self.formulas, "the step-climb formula of the MVP is missing")


class StepClimb(K0Case):
    def evaluate(self, r, h, W):
        return self.reg.evaluate(STEP_ID, {"r": r, "h": h, "W": W})

    def test_result_shape(self):
        f = self.formula_or_skip(STEP_ID)
        out = self.evaluate(q(300.0, "mm"), q(100.0, "mm"), q(1000.0, "N"))
        self.assertEqual(set(out), {"outputs", "terms", "expression"})
        self.assertEqual(set(out["outputs"]), set(f["outputs"]))
        for name, qty in out["outputs"].items():
            self.assertEqual(contract.errors("quantity", qty), [])
            self.assertEqual(qty["unit"], f["outputs"][name]["unit"], f"{name} not in the formula's unit")
        for t in out["terms"]:
            self.assertEqual(contract.errors("term", t), [])

    def test_known_answers(self):
        self.formula_or_skip(STEP_ID)
        W = 1000.0
        for c in ka.STEP_CASES:
            with self.subTest(case=c["case"]):
                out = self.evaluate(q(c["r"], "mm"), q(c["h"], "mm"), q(W, "N"))
                self.assertAlmostEqual(self.output_in(out, "F", "N") / (W * ka.step_force_ratio(c["r"], c["h"])),
                                       1.0, places=9)
                self.assertAlmostEqual(self.output_in(out, "d", "mm") / ka.step_corner_distance(c["r"], c["h"]),
                                       1.0, places=9)

    def test_inputs_in_other_units_give_the_same_answer(self):
        self.formula_or_skip(STEP_ID)
        base = self.evaluate(q(300.0, "mm"), q(100.0, "mm"), q(1200.0, "N"))
        other = self.evaluate(q(0.3, "m"), q(0.1, "m"), q(1.2, "kN"))
        for name in base["outputs"]:
            with self.subTest(output=name):
                self.assertAlmostEqual(other["outputs"][name]["value"] / base["outputs"][name]["value"], 1.0,
                                       places=12)

    def test_wrong_dimension_is_refused_by_name(self):
        self.formula_or_skip(STEP_ID)
        with self.assertRaises(ValueError) as cm:
            self.evaluate(q(300.0, "N"), q(100.0, "mm"), q(1000.0, "N"))
        self.assertIn("r", str(cm.exception))

    def test_input_below_its_declared_minimum_is_refused(self):
        f = self.formula_or_skip(STEP_ID)
        if "min" not in f["inputs"]["h"]:
            self.skipTest("h has no declared min")
        with self.assertRaises(ValueError) as cm:
            self.evaluate(q(300.0, "mm"), q(f["inputs"]["h"]["min"] - 1.0, "mm"), q(1000.0, "N"))
        self.assertIn("h", str(cm.exception))

    def test_step_as_high_as_the_wheel_is_not_a_number(self):
        # h >= r breaks the formula's own assumption; K0 must refuse, not return inf or nan
        self.formula_or_skip(STEP_ID)
        try:
            out = self.evaluate(q(300.0, "mm"), q(300.0, "mm"), q(1000.0, "N"))
        except ValueError:
            return
        for name, qty in out["outputs"].items():
            self.assertTrue(math.isfinite(qty["value"]), f"{name} = {qty['value']} for h = r")
