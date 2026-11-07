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
from hiraku.catalog.builders.k2_tests import (BEYOND, BEYOND_FACTOR, BUILDERS, PLANS, PLANS_BEYOND, PLANS_DRIVE,
                                              PREDICTION_ONLY_PARAMS)
from hiraku.solvers.k2_vehicle.shelf import TOOLS
from hiraku.solvers.k2_vehicle.units import SI_OF
from hiraku.catalog.summary import design_summary

import json

# The requirement values the builders read, as the requirements file would carry them
# (test values here; the requirements file gives the real ones with their sources).
EXTRA = {"Q11": ("1", 0.6), "Q12": (None, "C"), "Q13": ("%", 14.0), "Q14": ("deg", 15.0), "Q18": ("m/s", 30.0),
         "Q51": ("s", 6.0), "Q52": ("m", 14.0), "Q53": ("%", 20.0), "Q60": ("deg", 24.0), "Q58": ("m", 12.0)}
TOOL_OF = {"S1": "tilt", "S1'": "tilt", "F1": "crosswind", "S3": "lane_change", "S4": "braking", "S4b": "braking",
           "S5": "offroad_roll", "P2": "launch", "P3": "launch", "S6": "turning_circle", "S2": "steady_circle"}


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
        self.assertEqual(set(PLANS), set(BUILDERS) | set(PLANS_DRIVE))  # the drive records' steps by load case id
        for tid, fn in BUILDERS.items():  # the package collects this module (a product's builder may wrap it)
            got = ALL_BUILDERS[tid]
            self.assertIs(getattr(got, "general", got), fn)
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
        # 702.3 mm before each seat's back and head restraint were parts of their own (ladder_4w._seat_back_parts:
        # 3.67 kg per seat taken out of the seat box, centred 319 mm and 879 mm above it, the parts' centre of gravity
        # 5.0 mm higher); 703.0 mm before every part was joined to the frame (kit/joints: fittings and struts as parts with their
        # mass, 47db54d and the commits before it on the joints branch; the arms' 703.0 came in after them unchanged);
        # 699.4 mm before the suspension box was the arms, damper and ball joints (kit/arms.py: the same mass, the
        # damper's and the upper arm's share higher up) and the clevis brackets; 710.4 mm before the occupants were in segments (2a4e726: head, neck, trunk, upper arm, forearm, hand; the
        # same mass spread over those boxes), 712.7 mm before the roll bar
        # was on by default (its tubes raise the centre of gravity), 718.8 mm before the suspension towers, the dash
        # and the occupants' bodies as parts, 726.5 mm before the floors and the deck were steel sheets 3.2 mm thick
        # (were 20 mm boxes): the deck's top, and the cargo on it, came 16.8 mm down; 706.9 mm before the rear lower
        # arms' brackets reached the rail's outer face (+0.70 kg, 10 mm further out each) and the seat mount brackets
        # (+0.35 kg)
        self.assertAlmostEqual(h[620.0], 706.3, delta=0.5)

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
        out = [(tid, {"id": tid}, step) for tid, step in PLANS.items() if tid not in PLANS_DRIVE]
        return out + [(tid, {"id": tid, "variant": "beyond"}, PLANS_BEYOND.get(tid, PLANS[tid])) for tid in BEYOND]

    def drive_case(self):
        """S1's drive record built from the demo requirements (hiraku.catalog.build DRIVE_BUILDERS)."""
        from hiraku.catalog import load_catalog
        from hiraku.catalog.build import DRIVE_BUILDERS
        (lc,) = DRIVE_BUILDERS["S1"](load_catalog(), self.req, self.design)
        return lc

    def test_the_drive_record_has_its_own_step_with_the_tools_pair(self):
        lc = self.drive_case()
        self.assertEqual(lc["load_case_id"], f"S1-tilt-{lc['params']['loading']}-rollover-drive")
        self.assertIn(lc["load_case_id"], PLANS_DRIVE)
        from hiraku.catalog.build import recipe_for
        step = recipe_for(PLANS, lc)
        self.assertIs(step, PLANS_DRIVE[lc["load_case_id"]])  # its own step, not S1's
        self.assertEqual(step["tool"], "rollover_tilt")
        tool = TOOLS["rollover_tilt"]
        pairs = {(p["quantity"], p["formula_id"], p["output"]) for p in tool.describe()["predicts"]}
        self.assertTrue(step["say"])
        self.assertTrue(step["predictions"])
        for p in step["predictions"]:
            self.assertIn((p["quantity"], p["formula_id"], p["output"]), pairs)
            self.assertEqual(set(p["inputs"]), set(self.formulas[p["formula_id"]]["inputs"]))
            self.assertTrue(p["assumptions"])
            # the refs resolve on the drive case and the formula evaluates, as S1's own prediction does
            frm = p["inputs_from"](self.design, lc)
            inputs = {k: self.resolve(ref, lc, {}, frm) for k, ref in p["inputs"].items()}
            out = self.registry.evaluate(p["formula_id"], inputs)["outputs"][p["output"]]
            s1 = BUILDERS["S1"]({"id": "S1"}, self.req, self.design)
            s1_inputs = {k: self.resolve(ref, s1, {}, p["inputs_from"](self.design, s1)) for k, ref in p["inputs"].items()}
            self.assertEqual(out, self.registry.evaluate(p["formula_id"], s1_inputs)["outputs"][p["output"]])
        # the drive case reads only what the tool declares (and the prediction's cargo height)
        declared = {q["name"] for q in tool.describe()["params"]}
        self.assertEqual(set(lc["params"]) - declared - set(PREDICTION_ONLY_PARAMS), set())
        self.assertEqual(lc["params"]["hold_past"], {"value": 1.0, "unit": "deg"})
        self.assertEqual(lc["tolerance_rel"], BUILDERS["S1"]({"id": "S1"}, self.req, self.design)["tolerance_rel"])

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
            later = {ref for p in predictions(step) for ref in p["inputs"].values()}
            for p in predictions(step):
                with self.subTest(test=tid, quantity=p["quantity"]):
                    if f"pred.{p['quantity']}" in later and p["quantity"] not in {q for q, _, _ in pairs}:
                        pass  # an intermediate hand value a later prediction takes (pred.<quantity>), not compared
                    else:
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
