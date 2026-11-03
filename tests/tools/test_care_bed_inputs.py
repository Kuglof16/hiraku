"""get_context's formula_inputs and compose_plan on a care-bed run with the real builders: the product's own
steps (PRODUCT_STEPS) and predictions (product_care_bed.predictions_for / inputs_for) give every load case its
hand calculations with the inputs worked out by code."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from hiraku import solvers
from hiraku.agent.session import start_run
from hiraku.catalog import REPO_ROOT
from hiraku.catalog.builders import product_care_bed as bed
from hiraku.designgen import care_bed
from hiraku.tools import plan
from hiraku.tools.functions import get_context

PRODUCTS = REPO_ROOT / "data" / "requirements" / "products"
BED_TOOLS = ("bed_static", "actuator_linkage", "use_fatigue", "actuator_duty")


def bed_requirements() -> dict:
    """data/requirements/products/care-bed.yaml with the sample answers (care-bed.answers.yaml) as requirements."""
    req = yaml.safe_load((PRODUCTS / "care-bed.yaml").read_text(encoding="utf-8"))
    ans = yaml.safe_load((PRODUCTS / "care-bed.answers.yaml").read_text(encoding="utf-8"))
    for key, a in ans["answers"].items():
        req["requirements"].append({"key": key, "name": key, "value": a["value"], **({"unit": a["unit"]} if "unit" in a else {}),
                                    "source": "hearing", "trust": a.get("trust", 1), "basis": a.get("basis", "")})
    return req


def bed_run(root: Path, tools=BED_TOOLS):
    (root / "req.json").write_text(json.dumps(bed_requirements(), ensure_ascii=False), encoding="utf-8")
    (root / "design.json").write_text(json.dumps(care_bed.build(), ensure_ascii=False), encoding="utf-8")
    shelf = {t.name: t for t in solvers.builtin_tools() if t.name in tools}
    return start_run(root / "req.json", design_path=root / "design.json", root=root, slug="care-bed-test",
                     formulas=solvers.formula_registry(), tools=shelf)


class CareBedFormulaInputsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.ctx = bed_run(Path(cls._tmp.name))
        cls.design = cls.ctx.store.design()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def cases(self, ctx=None):
        ctx = ctx or self.ctx
        return {c["load_case_id"]: c for c in get_context(ctx, {"sections": ["formula_inputs"]})["formula_inputs"]["load_cases"]}

    def assert_from_code(self, ctx, case, design):
        """Every listed prediction of the load case has all its inputs, and the from. ones are inputs_for's values."""
        lc = ctx.load_case(case["load_case_id"])
        want = bed.inputs_for(design, lc)
        preds = bed.predictions_for(lc)
        # the tool's own pairings (tool_predicts) may add more formulas; the product's are all there
        self.assertLessEqual({(p["formula_id"], p["output"]) for p in preds},
                             {(e["formula_id"], e.get("output")) for e in case["formulas"]}, case["load_case_id"])
        for e in case["formulas"]:
            spec = next((p for p in preds if (p["formula_id"], p["output"]) == (e["formula_id"], e.get("output"))), None)
            if spec is None:
                continue
            self.assertNotIn("refused", e, e)
            for name, ref in spec["inputs"].items():
                if str(ref).startswith("pred."):
                    continue  # another prediction of this load case, recorded first
                self.assertIn(name, e["inputs"], (e["formula_id"], e.get("missing")))
                if str(ref).startswith("from."):
                    got = e["inputs"][name]
                    self.assertEqual({"value": got["value"], "unit": got["unit"]},
                                     {"value": float(want[ref[5:]]["value"]), "unit": want[ref[5:]]["unit"]}, name)

    def test_every_load_case_has_the_products_predictions_with_values(self):
        cases = self.cases()
        self.assertEqual(set(cases), {lc["load_case_id"] for lc in self.ctx.load_cases})
        for lc in self.ctx.load_cases:
            if bed.predictions_for(lc):
                self.assert_from_code(self.ctx, cases[lc["load_case_id"]], self.design)

    def test_named_inputs_come_out(self):
        cases = self.cases()

        def entry(test_id, formula_id, prefix=""):
            for c in cases.values():
                if c["test_id"] == test_id and c["load_case_id"].startswith(prefix):
                    for e in c["formulas"]:
                        if e["formula_id"] == formula_id:
                            return e
            self.fail(f"{test_id} {formula_id}")

        self.assertGreater(entry("P5", "bed.lever_actuator_force", "P5-back")["inputs"]["M0"]["value"], 0)
        self.assertGreater(entry("P5", "bed.lever_actuator_force", "P5-knee")["inputs"]["M0"]["value"], 0)
        deck = entry("R5", "bed.deck_frame_udl", "R5-deck")["inputs"]
        self.assertGreater(deck["W"]["value"], 0)  # the deck load per rail (deck_load_per_rail)
        self.assertGreater(entry("T3", "bed.actuator_duty")["inputs"]["s"]["value"], 0)  # the lever stroke
        dmg = entry("R3", "bed.use_damage")["inputs"]
        self.assertGreater(dmg["dS2"]["value"], 0)  # the back bracket lug's range
        self.assertGreater(dmg["dS3"]["value"], 0)  # the knee's
        # item 6 of the eighth run: these three were empty
        for test_id, fid in (("S1", "bed.edge_load_tipping"), ("R5", "bed.deck_frame_udl"), ("T3", "bed.actuator_duty")):
            got = entry(test_id, fid)
            self.assertTrue(got["inputs"], fid)
            self.assertNotIn("missing", got, fid)

    def test_compose_plan_takes_the_products_steps(self):
        p = plan.compose_plan(self.ctx)
        self.assertEqual(p["missing"], [])
        for s in p["steps"]:
            self.assertIs(s["predictions"], bed.step_for(self.ctx.load_case(s["load_case"]))["predictions"], s["load_case"])

    def test_a_step_with_fewer_predictions_gets_the_rest(self):
        lc = next(x for x in self.ctx.load_cases if x["test_id"] == "T3")
        full = bed.step_for(lc)
        short = {**full, "predictions": full["predictions"][:1]}
        with mock.patch.object(plan, "product_step", side_effect=lambda product, x: short if x is lc or
                               x["load_case_id"] == lc["load_case_id"] else bed.step_for(x)):
            case = get_context(self.ctx, {"sections": ["formula_inputs"], "load_case_id": lc["load_case_id"]})[
                "formula_inputs"]["load_cases"][0]
        self.assertEqual([e.get("output") for e in case["formulas"]], ["duty", "run_time"])
        self.assertEqual({e["tool"] for e in case["formulas"]}, {"actuator_duty"})
        self.assert_from_code(self.ctx, case, self.design)


class CareBedWithoutAToolTest(unittest.TestCase):
    def test_a_load_case_whose_tool_is_not_on_the_shelf_lists_the_products_predictions(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = bed_run(Path(d), tools=("bed_static", "actuator_linkage"))
            p = plan.compose_plan(ctx)
            self.assertIn("actuator_duty", {t for _, t in p["unshelved"]})
            t3 = next(lc for lc in ctx.load_cases if lc["test_id"] == "T3")
            case = get_context(ctx, {"sections": ["formula_inputs"], "load_case_id": t3["load_case_id"]})[
                "formula_inputs"]["load_cases"][0]
            self.assertEqual(case["note"], plan.TOOLLESS_NOTE)
            self.assertEqual({e["tool"] for e in case["formulas"]}, {None})
            CareBedFormulaInputsTest.assert_from_code(self, ctx, case, ctx.store.design())


if __name__ == "__main__":
    unittest.main()
