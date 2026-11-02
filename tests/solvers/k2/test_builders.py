"""The K2 tests' load-case builders and scripted steps (hiraku.catalog.builders.k2_tests).

Fast checks only (no tool is run): the load cases fit the contract and use only
params the tool declares, and every planned prediction names a K0 formula, its
output and all its inputs, with refs that resolve on the demo design.
"""

import copy
import math
import unittest

from hiraku import contract, solvers
from hiraku.catalog import CatalogError, REPO_ROOT, load_requirements
from hiraku.catalog.builders.k2_tests import BUILDERS, PLANS
from hiraku.solvers.k2_vehicle.shelf import TOOLS
from hiraku.solvers.k2_vehicle.units import SI_OF
from hiraku.tools.summary import design_summary

import json

# The requirement values the builders read, as the requirements file would carry them
# (test values here; the requirements file gives the real ones with their sources).
EXTRA = {"Q11": ("1", 0.6), "Q12": (None, "C"), "Q13": ("%", 14.0), "Q14": ("deg", 15.0), "Q18": ("m/s", 30.0),
         "Q51": ("s", 6.0), "Q52": ("m", 14.0), "Q53": ("%", 20.0), "Q60": ("deg", 24.0)}
TOOL_OF = {"S1": "tilt", "S1'": "tilt", "F1": "crosswind", "S3": "lane_change", "S4": "braking", "S4b": "braking",
           "S5": "offroad_roll", "P2": "launch", "P3": "launch"}


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
        self.assertEqual(set(PLANS) - set(BUILDERS), set())

    def test_load_cases_fit_the_contract(self):
        for tid, lc in self.cases.items():
            with self.subTest(test=tid):
                self.assertEqual(contract.errors("load_case", lc), [])
                self.assertEqual(lc["test_id"], tid)

    def test_params_are_declared_by_the_tool(self):
        for tid, lc in self.cases.items():
            declared = {p["name"]: p for p in TOOLS[TOOL_OF[tid]].describe()["params"]}
            for name, value in lc["params"].items():
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
        with self.assertRaises(CatalogError) as e:
            BUILDERS["S4"]({"id": "S4"}, req, self.design)
        self.assertIn("Q52", str(e.exception))

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
        self.fail(f"unexpected ref {ref}")

    def test_steps_predict_with_paired_formulas(self):
        for tid, step in PLANS.items():
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
            for p in step["predictions"]:
                with self.subTest(test=tid, quantity=p["quantity"]):
                    self.assertIn((p["quantity"], p["formula_id"], p["output"]), pairs)
                    self.assertEqual(set(p["inputs"]), set(self.formulas[p["formula_id"]]["inputs"]))
                    self.assertTrue(p["assumptions"])

    def test_refs_resolve_and_the_formulas_evaluate(self):
        for tid, step in PLANS.items():
            lc = BUILDERS[tid]({"id": tid}, self.req, self.design)
            preds = {}
            for p in step["predictions"]:
                with self.subTest(test=tid, quantity=p["quantity"]):
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
