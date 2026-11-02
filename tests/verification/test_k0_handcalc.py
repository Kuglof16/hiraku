"""K0 hand calculation (hiraku.solvers.formula_registry()) against closed forms.

The registry shape is FormulaRegistry in hiraku/contract/protocols.py:
list_formulas() -> [formula], evaluate(formula_id, {name: quantity}) ->
{"outputs": {name: quantity}, "terms": [term], "expression": str}.
Expected values come from qa.known_answers and qa.frame2d; unit factors from
scipy.constants. A formula that is not registered yet is skipped by name.
"""

import math
import unittest

from hiraku import contract
from qa import known_answers as ka
from qa.frame2d import Frame2D

from ._solvers import formula_registry

STEP_ID = "step_climb.wheel_force_rigid"  # the id used in hiraku/contract/examples/formula.json
TUBE = {"b": 40.0, "h": 80.0, "t": 2.0}
E_STEEL = 205000.0  # check input only


def q(value, unit):
    return {"value": value, "unit": unit}


def tube_inputs():
    return {k: q(v, "mm") for k, v in TUBE.items()}


class K0Case(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reg = formula_registry()
        cls.formulas = {f["formula_id"]: f for f in cls.reg.list_formulas()}

    def formula_or_skip(self, formula_id):
        if formula_id not in self.formulas:
            self.skipTest(f"formula {formula_id!r} is not registered in k0_handcalc yet")
        return self.formulas[formula_id]

    def value(self, out, name, unit):
        """Output `name` converted to `unit` with the QA unit table."""
        self.assertIn(name, out["outputs"], f"no output {name!r}; outputs: {sorted(out['outputs'])}")
        got = out["outputs"][name]
        return ka.convert(got["value"], got["unit"], unit)

    def by_dimension(self, out, dimension):
        """The only output whose unit has this dimension (force, length, ...)."""
        names = [n for n, v in out["outputs"].items() if ka.UNIT_TO_SI[v["unit"]][0] == dimension]
        self.assertEqual(len(names), 1, f"expected one {dimension} output, got {names}")
        return names[0]

    def assertRel(self, got, expected, rel=1e-9, msg=None):
        self.assertTrue(math.isclose(got, expected, rel_tol=rel), msg or f"{got} vs expected {expected}")


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
                force, dist = self.by_dimension(out, "force"), self.by_dimension(out, "length")
                self.assertRel(self.value(out, force, "N"), W * ka.step_force_ratio(c["r"], c["h"]))
                self.assertRel(self.value(out, dist, "mm"), ka.step_corner_distance(c["r"], c["h"]))

    def test_inputs_in_other_units_give_the_same_answer(self):
        self.formula_or_skip(STEP_ID)
        base = self.evaluate(q(300.0, "mm"), q(100.0, "mm"), q(1200.0, "N"))
        other = self.evaluate(q(0.3, "m"), q(0.1, "m"), q(1.2, "kN"))
        for name in base["outputs"]:
            with self.subTest(output=name):
                self.assertRel(other["outputs"][name]["value"], base["outputs"][name]["value"], rel=1e-12)

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


class WheelLoads(K0Case):
    FID = "mass_props.static_wheel_loads"

    def test_centred_vehicle(self):
        self.formula_or_skip(self.FID)
        m, a, L, t = 500.0, 800.0, 1800.0, 1300.0
        out = self.reg.evaluate(self.FID, {"m": q(m, "kg"), "a": q(a, "mm"), "L": q(L, "mm"), "t": q(t, "mm"),
                                           "e": q(0.0, "mm")})
        expected = ka.wheel_loads_equal_rates(m * ka.G_N, L, t, a)
        for wheel, value in expected.items():
            with self.subTest(wheel=wheel):
                self.assertRel(self.value(out, f"wheel_load_{wheel}", "N"), value)
        self.assertEqual(out["outputs"]["wheel_load_FL"]["value"], out["outputs"]["wheel_load_FR"]["value"])


class Beams(K0Case):
    P, L = 1000.0, 900.0

    def beam(self, formula_id, load):
        self.formula_or_skip(formula_id)
        inputs = {**load, "L": q(self.L, "mm"), "E": q(E_STEEL, "MPa"), **tube_inputs()}
        return self.reg.evaluate(formula_id, inputs)

    def check(self, out, cf):
        sec = ka.rect_tube(**TUBE)
        self.assertRel(self.value(out, "max_moment", "N*mm"), cf["moment"])
        self.assertRel(self.value(out, "max_stress", "MPa"), ka.bending_stress(cf["moment"], sec["Iy"], sec["c_z"]))
        self.assertRel(self.value(out, "max_displacement", "mm"), cf["deflection"])

    def test_simple_beam_centre_load(self):
        out = self.beam("frame_static.simple_beam_center_load", {"P": q(self.P, "N")})
        self.check(out, ka.simply_supported_centre_load(self.P, self.L, E_STEEL, ka.rect_tube(**TUBE)["Iy"]))

    def test_simple_beam_uniform_load(self):
        w = 2.5
        out = self.beam("frame_static.simple_beam_udl", {"w": q(w, "N/mm")})
        self.check(out, ka.simply_supported_uniform_load(w, self.L, E_STEEL, ka.rect_tube(**TUBE)["Iy"]))

    def test_cantilever_tip_load(self):
        out = self.beam("frame_static.cantilever_tip_load", {"P": q(self.P, "N")})
        self.check(out, ka.cantilever_tip_load(self.P, self.L, E_STEEL, ka.rect_tube(**TUBE)["Iy"]))

    def test_simple_beam_point_load_by_the_stiffness_method(self):
        a = self.L / 3
        out = self.beam("frame_static.simple_beam_point_load", {"P": q(self.P, "N"), "a": q(a, "mm")})
        sec = ka.rect_tube(**TUBE)
        fr = Frame2D()
        for n, x in (("A", 0.0), ("C", a), ("B", self.L)):
            fr.node(n, x, 0.0)
        fr.member("A", "C", E_STEEL, sec["A"], sec["Iy"], parts=3)
        fr.member("C", "B", E_STEEL, sec["A"], sec["Iy"], parts=3)
        fr.support("A", u=True, w=True, r=False)
        fr.support("B", u=False, w=True, r=False)
        fr.load("C", fz=-self.P)
        res = fr.solve()
        moment = res["reaction"]["A"][1] * a  # left reaction times the arm to the load
        self.assertRel(self.value(out, "max_displacement", "mm"), -res["disp"]["C"][1], rel=1e-7)
        self.assertRel(self.value(out, "max_moment", "N*mm"), moment, rel=1e-7)

    def test_bending_stress(self):
        self.formula_or_skip("frame_static.bending_stress")
        M = 2.0e5
        out = self.reg.evaluate("frame_static.bending_stress", {"M": q(M, "N*mm"), **tube_inputs()})
        sec = ka.rect_tube(**TUBE)
        self.assertRel(self.value(out, "max_stress", "MPa"), ka.bending_stress(M, sec["Iy"], sec["c_z"]))
