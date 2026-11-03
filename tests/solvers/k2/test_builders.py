"""The K2 tests' load-case builders and scripted steps (hiraku.catalog.builders.k2_tests).

Fast checks only (no tool is run): the load cases fit the contract and use only
params the tool declares, and every planned prediction names a K0 formula, its
output and all its inputs, with refs that resolve on the demo design.
"""

import copy
import math
import unittest

from hiraku import contract, solvers
from hiraku.catalog import REPO_ROOT, load_requirements
from hiraku.catalog.builders import BUILDERS as ALL_BUILDERS
from hiraku.catalog.builders.common import BuildError
from hiraku.catalog.builders.k2_tests import (BEYOND, BEYOND_FACTOR, BUILDERS, PLANS, PLANS_BEYOND,
                                              PREDICTION_ONLY_PARAMS)
from hiraku.solvers.k2_vehicle.shelf import TOOLS
from hiraku.solvers.k2_vehicle.units import SI_OF
from hiraku.catalog.summary import design_summary

import json

# The requirement values the builders read, as the requirements file would carry them
# (test values here; the requirements file gives the real ones with their sources).
EXTRA = {"Q11": ("1", 0.6), "Q12": (None, "C"), "Q13": ("%", 14.0), "Q14": ("deg", 15.0), "Q18": ("m/s", 30.0),
         "Q51": ("s", 6.0), "Q52": ("m", 14.0), "Q53": ("%", 20.0), "Q60": ("deg", 24.0)}
TOOL_OF = {"S1": "tilt", "S1'": "tilt", "F1": "crosswind", "S3": "lane_change", "S4": "braking", "S4b": "braking",
           "S5": "offroad_roll", "P2": "launch", "P3": "launch"}


def predictions(step):
    """The step's predictions, then those of its retries."""
    while step:
        yield from step["predictions"]
        step = (step.get("expected_mismatch") or {}).get("retry")


def demo():
    with (REPO_ROOT / "data" / "designs" / "offroad-4w-v1.json").open(encoding="utf-8") as f:
        design = json.load(f)
    req = copy.deepcopy(load_requirements(REPO_ROOT / "data" / "requirements" / "offroad-4w.yaml"))
    have = {r["key"] for r in req["requirements"]}
    for key, (unit, value) in EXTRA.items():
        if key not in have:
            r = {"key": key, "name": key, "value": value, "source": "default", "trust": 3, "basis": "test value"}
            if unit:
                r["unit"] = unit
            req["requirements"].append(r)
    return design, req


class Builders(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design, cls.req = demo()
        cls.cases = {tid: fn({"id": tid}, cls.req, cls.design) for tid, fn in BUILDERS.items()}

    def test_the_nine_tests(self):
        self.assertEqual(sorted(BUILDERS), sorted(TOOL_OF))
        self.assertEqual(set(PLANS), set(BUILDERS))
        for tid, fn in BUILDERS.items():  # the package collects this module
            self.assertIs(ALL_BUILDERS[tid], fn)
        from hiraku.catalog.builders import PLANS_BEYOND as ALL_PLANS_BEYOND
        for tid, step in PLANS_BEYOND.items():
            self.assertIs(ALL_PLANS_BEYOND[tid], step)

    def test_load_cases_fit_the_contract(self):
        for tid, lc in self.cases.items():
            with self.subTest(test=tid):
                self.assertEqual(contract.errors("load_case", lc), [])
                self.assertEqual(lc["test_id"], tid)

    def test_params_are_declared_by_the_tool(self):
        for tid, lc in self.cases.items():
            declared = {p["name"]: p for p in TOOLS[TOOL_OF[tid]].describe()["params"]}
            for name, value in lc["params"].items():
                if name in PREDICTION_ONLY_PARAMS:  # read by the step's inputs_from, not the tool
                    continue
                with self.subTest(test=tid, param=name):
                    self.assertIn(name, declared)
                    p = declared[name]
                    if p["kind"] == "quantity":
                        self.assertEqual(SI_OF[value["unit"]], SI_OF[p["unit"]])
                    if "choices" in p:
                        self.assertIn(value, p["choices"])

    def test_limits_come_from_the_requirements(self):
        lc = self.cases["S4"]
        self.assertEqual(lc["criterion"]["limit"], {"value": 14.0, "unit": "m"})
        self.assertEqual(lc["params"]["mu"], {"value": 0.6, "unit": "1"})
        self.assertEqual(self.cases["S1"]["criterion"]["limit"], {"value": 24.0, "unit": "deg"})
        self.assertEqual(self.cases["S1'"]["params"]["loading"], "empty")
        self.assertNotIn("criterion", self.cases["S5"])

    def test_a_missing_requirement_is_named(self):
        req = copy.deepcopy(self.req)
        req["requirements"] = [r for r in req["requirements"] if r["key"] != "Q52"]
        with self.assertRaises(BuildError) as e:
            BUILDERS["S4"]({"id": "S4"}, req, self.design)
        self.assertIn("Q52", str(e.exception))

    def test_s1_predicts_with_the_cargo_height_of_q41(self):
        from hiraku.catalog.builders.k2_tests import tilt_inputs
        h = {}
        for above in (None, 150.0, 620.0):
            req = copy.deepcopy(self.req)
            req["requirements"] = [r for r in req["requirements"] if r["key"] != "Q41"]
            if above is not None:
                req["requirements"].append({"key": "Q41", "name": "荷の重心の高さ", "value": above, "unit": "mm",
                                            "source": "default", "trust": 3, "basis": "test value"})
            lc = BUILDERS["S1"]({"id": "S1"}, req, self.design)
            self.assertEqual("Q41" in lc["requirement_keys"], above is not None)
            self.assertEqual("cargo_cg_above_deck" in lc["params"], above is not None)
            h[above] = tilt_inputs(self.design, lc)["h"]["value"]
        # the demo design's cargo box stands on its floor with its centre 150 mm up
        self.assertAlmostEqual(h[150.0], h[None], places=6)
        self.assertAlmostEqual(h[620.0], 712.7, delta=0.5)

    def test_cases_past_the_limit(self):
        for tid in BEYOND:
            with self.subTest(test=tid):
                lc = BUILDERS[tid]({"id": tid, "variant": "beyond"}, self.req, self.design)
                self.assertEqual(contract.errors("load_case", lc), [])
                self.assertTrue(lc["load_case_id"].endswith("-beyond"), lc["load_case_id"])
                self.assertTrue(lc["title"].startswith("限界の外"))
                declared = {p["name"] for p in TOOLS[TOOL_OF[tid]].describe()["params"]}
                self.assertEqual(set(lc["params"]) - declared, set())
        f1 = BUILDERS["F1"]({"id": "F1", "variant": "beyond"}, self.req, self.design)
        self.assertEqual(f1["params"]["wind_speed"]["value"], f1["criterion"]["limit"]["value"])
        s5 = BUILDERS["S5"]({"id": "S5", "variant": "beyond"}, self.req, self.design)
        self.assertEqual(s5["criterion"]["quantity"], "tip_slope_angle")
        self.assertAlmostEqual(s5["params"]["side_slope"]["value"], s5["criterion"]["limit"]["value"])
        self.assertGreater(s5["params"]["side_slope"]["value"], 30.0 * BEYOND_FACTOR)
        with self.assertRaises(BuildError):
            BUILDERS["S3"]({"id": "S3", "variant": "beyond"}, self.req, self.design)

    def test_a_catalog_tolerance_replaces_the_default(self):
        lc = BUILDERS["S4"]({"id": "S4", "tolerance_rel": 0.2}, self.req, self.design)
        self.assertEqual(lc["tolerance_rel"], 0.2)


class Plans(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design, cls.req = demo()
        cls.summary = design_summary(cls.design)
        cls.formulas = {f["formula_id"]: f for f in solvers.formula_registry().list_formulas()}
        cls.registry = solvers.formula_registry()

    def resolve(self, ref, lc, preds, frm):
        head, _, rest = ref.partition(".")
        if head == "lc":
            return lc["params"][rest]
        if head == "load":
            return self.summary["loadings"][lc["params"]["loading"]][rest]
        if head == "summary":
            return self.summary[rest]
        if head == "from":
            return frm[rest]
        if head == "pred":
            return preds[rest]
        if head == "design" and rest.startswith("var."):
            return self.design["variables"][rest[4:]]
        self.fail(f"unexpected ref {ref}")

    def steps(self):
        """(test id, catalog spec, step) of every plan, the cases past the limit included."""
        out = [(tid, {"id": tid}, step) for tid, step in PLANS.items()]
        return out + [(tid, {"id": tid, "variant": "beyond"}, PLANS_BEYOND.get(tid, PLANS[tid])) for tid in BEYOND]

    def test_steps_predict_with_paired_formulas(self):
        for tid, _, step in self.steps():
            tool = TOOLS[step["tool"]]
            self.assertEqual(step["tool"], TOOL_OF[tid])
            pairs = {(p["quantity"], p["formula_id"], p["output"]) for p in tool.describe()["predicts"]}
            self.assertTrue(step["say"])
            expected = step.get("expected_mismatch")
            if expected:  # a cause written for a mismatch that happened, with where it was found
                self.assertIn(expected["quantity"], {p["quantity"] for p in step["predictions"]})
                for key in ("source", "cause", "fix"):
                    self.assertTrue(expected[key].strip(), key)
                self.assertTrue(expected.get("retry") or expected.get("stop"))
            for p in predictions(step):
                with self.subTest(test=tid, quantity=p["quantity"]):
                    self.assertIn((p["quantity"], p["formula_id"], p["output"]), pairs)
                    self.assertEqual(set(p["inputs"]), set(self.formulas[p["formula_id"]]["inputs"]))
                    self.assertTrue(p["assumptions"])

    def test_refs_resolve_and_the_formulas_evaluate(self):
        for tid, spec, step in self.steps():
            lc = BUILDERS[tid](spec, self.req, self.design)
            preds = {}
            for p in predictions(step):
                with self.subTest(test=tid, quantity=p["quantity"], formula=p["formula_id"]):
                    self.assertEqual(any(ref.startswith("from.") for ref in p["inputs"].values()), "inputs_from" in p)
                    frm = p["inputs_from"](self.design, lc) if "inputs_from" in p else {}
                    inputs = {k: self.resolve(ref, lc, preds, frm) for k, ref in p["inputs"].items()}
                    spec = self.formulas[p["formula_id"]]["inputs"]
                    for k, q in inputs.items():
                        self.assertEqual(SI_OF[q["unit"]], SI_OF[spec[k]["unit"]], k)
                    out = self.registry.evaluate(p["formula_id"], inputs)["outputs"][p["output"]]
                    self.assertTrue(math.isfinite(out["value"]))
                    preds[p["quantity"]] = {"value": out["value"], "unit": out["unit"]}


if __name__ == "__main__":
    unittest.main()
