"""A load case Claude writes for a test the catalog has no builder for (write_load_case, docs/contract.md
"Claude が書く設計"): the limit computed by code from limit_from (requirements and the design, units converted),
its refusals, who may call it, the load case added to the version and used by analyze, and kept on the next
version with its limit computed again on that version's design.

The design is test_write_design's serving robot (its steel's 245 MPa yield and the 20 x 20 x 1.6 mm tube are that
test's assumptions); the requirements are robot_requirements' (the fixed file's Q61 and the robot's Q15 20 mm).
R2 (impact) has no builder in the catalog: the written case is the robot's weights times 3 (g_factor 3, an
assumption of this test), judged on the frame stress against yield / Q61.
"""

import asyncio
import copy
import tempfile
import unittest
from pathlib import Path

from hiraku import contract
from hiraku.agent.session import start_run
from hiraku.tools import Attribution, ToolError
from hiraku.tools.functions import write_design
from hiraku.tools.load_case_writer import (WRITTEN_TOLERANCE_REL, LimitError, _limit_value, limit_value,
                                           write_load_case)
from tests.tools import fakes
from tests.tools.test_write_design import robot_requirements, serving_robot

YIELD = 245.0  # serving_robot's steel (test assumption)


def r2_args(**more):
    args = {"test_id": "R2", "domain": "strength", "title": "段差の衝撃を 3 g の静的な荷重とみる",
            "params": {"g_factor": {"value": 3.0, "unit": "1"}, "loading": "empty"},
            "criterion": {"quantity": "max_stress", "op": "<=",
                          "limit_from": {"expr": "yield / Q61", "unit": "MPa",
                                         "refs": {"yield": "materials.steel.yield_MPa", "Q61": "Q61"}}},
            "basis": "3 g はこの試験の前提 (出典なし)"}
    args.update(more)
    return args


def with_limit(args, **limit_from):
    out = copy.deepcopy(args)
    out["criterion"]["limit_from"] = limit_from
    return out


class Run:
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.ctx = self.start()
        self.design = serving_robot()

    def start(self):
        return start_run(robot_requirements(self.root), root=self.root / "runs", formulas=fakes.FakeFormulas(),
                         tools=fakes.shelf())

    def req(self, key):
        return next(r for r in self.ctx.project["requirements"] if r["key"] == key)

    def kinds(self):
        return [e["kind"] for e in self.ctx.store.events()]

    def assert_events_fit(self):
        for e in self.ctx.store.events():
            self.assertEqual(contract.errors("event", e), [], e["kind"])


class LimitValueTest(Run, unittest.TestCase):
    def limit(self, **limit_from):
        return _limit_value(limit_from, self.ctx.project["requirements"], self.design)

    def test_the_shim_is_used_until_the_catalog_has_one(self):
        try:
            import hiraku.catalog.limits  # noqa: F401
        except ImportError:
            self.assertIs(limit_value, _limit_value)

    def test_yield_over_the_safety_factor(self):
        got = self.limit(expr="yield / Q61", unit="MPa", refs={"yield": "materials.steel.yield_MPa", "Q61": "Q61"})
        q61 = self.req("Q61")["value"]
        self.assertAlmostEqual(got["value"], YIELD / q61)
        self.assertEqual(got["unit"], "MPa")
        self.assertEqual(got["from"]["yield"], {"value": YIELD, "unit": "MPa", "ref": "materials.steel.yield_MPa"})
        self.assertEqual(got["from"]["Q61"], {"value": q61, "unit": "1", "ref": "Q61"})

    def test_units_are_converted(self):
        got = self.limit(expr="yield", unit="Pa", refs={"yield": "materials.steel.yield_MPa"})
        self.assertAlmostEqual(got["value"], YIELD * 1e6)
        t = self.limit(expr="t", unit="m", refs={"t": "sections.sq20.dims_mm.t"})  # dims_mm: the unit of its fields
        self.assertAlmostEqual(t["value"], 1.6e-3)
        step = self.req("Q15")
        both = self.limit(expr="min(Q15, t)", unit="mm", refs={"t": "sections.sq20.dims_mm.t"})  # Q15 without a ref
        self.assertAlmostEqual(both["value"], min(step["value"], 1.6))
        self.assertEqual(both["from"]["Q15"]["ref"], "Q15")

    def test_other_paths_in_the_design(self):
        self.design["variables"] = {"shelf_gap": {"value": 0.45, "unit": "m"}}
        gap = self.limit(expr="gap / 2", unit="mm", refs={"gap": "variables.shelf_gap"})
        self.assertAlmostEqual(gap["value"], 225.0)
        dishes = self.limit(expr="m * 2", refs={"m": "parts.dishes.mass_kg"})  # a list item by its id; unit from it
        self.assertEqual((dishes["value"], dishes["unit"]), (20.0, "kg"))
        ratio = self.limit(expr="nu", refs={"nu": "materials.steel.nu"})
        self.assertEqual((ratio["value"], ratio["unit"]), (0.3, "1"))

    def test_refusals_say_why(self):
        mat = {"yield": "materials.steel.yield_MPa"}
        cases = [
            ({"expr": "yield / Q61", "unit": "MPa", "refs": {"yield": "materials.s355.yield_MPa"}}, "s355"),
            ({"expr": "y", "unit": "MPa", "refs": {"y": "materials.steel.source"}}, "単位が読めません"),
            ({"expr": "y", "unit": "1", "refs": {"y": "materials.steel.name"}}, "単位が読めません"),
            ({"expr": "yield / Q61", "unit": "mm", "refs": mat}, "換算できません"),
            ({"expr": "yield + Q15", "unit": "MPa", "refs": mat}, "次元が合いません"),
            ({"expr": "yield / Q99", "unit": "MPa", "refs": mat}, "Q99"),
            ({"expr": "yield / Q30", "unit": "MPa", "refs": mat}, "数ではない"),
            ({"expr": "yield / k", "unit": "MPa", "refs": mat}, "refs がありません"),
            ({"expr": "__import__('os')", "unit": "1"}, "使えるのは"),
            ({"expr": "yield / 0", "unit": "MPa", "refs": mat}, "0 で割って"),
            ({"expr": "yield", "unit": "furlong", "refs": mat}, "furlong"),
        ]
        for limit_from, word in cases:
            with self.subTest(expr=limit_from["expr"], word=word):
                with self.assertRaises(LimitError) as cm:
                    self.limit(**limit_from)
                self.assertIn(word, str(cm.exception))


class WriteLoadCaseTest(Run, unittest.TestCase):
    def setUp(self):
        super().setUp()
        write_design(self.ctx, {"design": self.design, "reason": "版 1"}, "designer")

    def test_the_domain_role_writes_it_and_the_code_makes_the_numbers(self):
        before = [lc["load_case_id"] for lc in self.ctx.load_cases]
        out = write_load_case(self.ctx, r2_args(), "cae.strength")
        q61 = self.req("Q61")["value"]
        self.assertEqual(out["load_case_id"], "R2-g3")
        self.assertAlmostEqual(out["criterion"]["limit"]["value"], YIELD / q61)
        self.assertEqual(out["criterion"]["limit"]["unit"], "MPa")
        self.assertEqual(out["criterion"]["basis"], f"yield {YIELD:g} MPa / Q61 {q61:g} (materials steel: not checked)")
        self.assertEqual(out["criterion"]["requirement_keys"], ["Q61"])
        self.assertEqual(out["tolerance_rel"], WRITTEN_TOLERANCE_REL)  # the catalog gives R2 none
        self.assertEqual(out["owner"], "cae.strength")
        self.assertIn("stage", out)
        lc = self.ctx.load_case("R2-g3")
        self.assertEqual([x["load_case_id"] for x in self.ctx.load_cases], before + ["R2-g3"])
        self.assertEqual(self.ctx.project["load_cases"][-1], lc)  # version 1: project.load_cases
        self.assertEqual(lc["params"], r2_args()["params"])
        self.assertEqual(contract.errors("load_case", lc), [])
        self.assertEqual((lc["written_by"], lc["basis"]), ("claude", r2_args()["basis"]))
        self.assertEqual(lc["limit_from"], r2_args()["criterion"]["limit_from"])
        self.assertFalse(any((e.get("data") or {}).get("written_load_case") for e in self.ctx.store.events()))
        self.assertEqual(self.kinds()[-3:-1], ["project.updated", "agent.said"])
        tile = next(j for j in self.ctx.store.judgements(1) if j["domain"] == "strength")
        self.assertEqual(tile["state"], "pending")
        self.assert_events_fit()

    def test_the_lead_may_write_it_and_another_role_may_not(self):
        for agent in ("designer", "cae.handling", "packager"):
            with self.subTest(agent=agent):
                with self.assertRaisesRegex(ToolError, "cae.strength"):
                    write_load_case(self.ctx, r2_args(), agent)
        self.assertFalse(self.ctx.has_load_case("R2-g3"))
        self.assertEqual(write_load_case(self.ctx, r2_args(), "lead")["owner"], "cae.strength")

    def test_a_test_with_a_builder_uses_the_catalogs(self):
        with self.assertRaisesRegex(ToolError, "カタログの荷重条件を使います"):
            write_load_case(self.ctx, r2_args(test_id="R1"), "cae.strength")

    def test_other_refusals(self):
        with self.assertRaisesRegex(ToolError, "分野は strength"):
            write_load_case(self.ctx, r2_args(domain="handling"), "lead")
        with self.assertRaisesRegex(ToolError, "選ばれていません"):  # S2 (handling) has no builder; handling not picked
            write_load_case(self.ctx, r2_args(test_id="S2", domain="handling"), "lead")
        with self.assertRaisesRegex(ToolError, "入力の形"):
            write_load_case(self.ctx, {**r2_args(), "criterion": {**r2_args()["criterion"],
                                                                  "limit": {"value": 1.0, "unit": "MPa"}}}, "lead")
        with self.assertRaisesRegex(ToolError, "load_case"):
            write_load_case(self.ctx, r2_args(params={"g_factor": [3]}), "lead")
        with self.assertRaisesRegex(ToolError, "Q98"):
            write_load_case(self.ctx, r2_args(requirement_keys=["Q98"]), "lead")
        self.assertFalse(any(lc["test_id"] == "R2" for lc in self.ctx.load_cases))

    def test_a_limit_the_code_cannot_compute_is_refused(self):
        base = r2_args()
        for limit_from, word in (
                ({"expr": "yield / Q61", "unit": "MPa", "refs": {"yield": "materials.steel.yield"}}, "yield"),
                ({"expr": "yield / Q61", "unit": "kg", "refs": {"yield": "materials.steel.yield_MPa"}}, "換算できません"),
                ({"expr": "yield / Q99", "unit": "MPa", "refs": {"yield": "materials.steel.yield_MPa"}}, "Q99")):
            with self.subTest(word=word):
                with self.assertRaises(ToolError) as cm:
                    write_load_case(self.ctx, with_limit(base, **limit_from), "cae.strength")
                self.assertIn("限度を計算できません", str(cm.exception))
                self.assertIn(word, str(cm.exception))
        self.assertFalse(any(lc["test_id"] == "R2" for lc in self.ctx.load_cases))

    def test_ids_do_not_collide(self):
        self.assertEqual(write_load_case(self.ctx, r2_args(), "lead")["load_case_id"], "R2-g3")
        self.assertEqual(write_load_case(self.ctx, r2_args(), "lead")["load_case_id"], "R2-g3-2")
        drop = r2_args(params={"drop_height": {"value": 1.0, "unit": "m"}})
        self.assertEqual(write_load_case(self.ctx, drop, "lead")["load_case_id"], "R2-drop1m")

    def test_the_next_version_keeps_it_with_its_limit_on_that_design(self):
        write_load_case(self.ctx, r2_args(), "cae.strength")
        stronger = copy.deepcopy(self.design)
        stronger["materials"]["steel"]["yield_MPa"] = 355.0  # test assumption
        write_design(self.ctx, {"design": stronger, "reason": "R2 に答えて材料を変えた"}, "designer")
        lc = self.ctx.load_case("R2-g3")
        self.assertAlmostEqual(lc["criterion"]["limit"]["value"], 355.0 / self.req("Q61")["value"])
        self.assertEqual(lc["tolerance_rel"], WRITTEN_TOLERANCE_REL)
        self.assertEqual(self.ctx.store.version_load_cases(2)[-1], lc)
        created = [e for e in self.ctx.store.events() if e["kind"] == "load_cases.created"][-1]
        self.assertIn("R2-g3", created["text"])  # changed: its limit
        self.assertNotIn("前の版のまま", created["text"])
        self.assertEqual([lc["load_case_id"] for lc in self.ctx.load_cases], ["R1-step20", "R2-g3"])
        # a third version without that material keeps version 2's case and says so
        renamed = copy.deepcopy(stronger)
        renamed["materials"] = {"s355": renamed["materials"]["steel"]}
        for m in renamed["members"]:
            m["material"] = "s355"
        self.ctx.project["round_limit"] = 3
        write_design(self.ctx, {"design": renamed, "reason": "材料の名前を変えた"}, "designer")
        self.assertEqual(self.ctx.load_case("R2-g3"), lc)
        created = [e for e in self.ctx.store.events() if e["kind"] == "load_cases.created"][-1]
        self.assertIn("前の版のままのもの: R2-g3", created["text"])
        self.assert_events_fit()

    def test_a_reopened_run_still_knows_it(self):
        from hiraku.store import RunStore
        from hiraku.tools.context import RunContext
        from hiraku.tools.load_case_writer import written_inputs

        write_load_case(self.ctx, r2_args(), "cae.strength")
        again = RunContext(store=RunStore.open(self.ctx.store.path), formulas=fakes.FakeFormulas(), tools=fakes.shelf())
        self.assertEqual(written_inputs(again.load_cases), {"R2-g3": r2_args(requirement_keys=["Q61"])})
        self.assertTrue(again.has_load_case("R2-g3"))

    def test_on_version_2_it_goes_to_the_versions_load_cases(self):
        thicker = copy.deepcopy(self.design)
        thicker["sections"]["sq20"]["dims_mm"]["t"] = 2.0
        write_design(self.ctx, {"design": thicker, "reason": "版 2"}, "designer")
        write_load_case(self.ctx, r2_args(), "cae.strength")
        self.assertEqual(self.ctx.store.version_load_cases(2)[-1]["load_case_id"], "R2-g3")
        self.assertNotIn("R2-g3", [lc["load_case_id"] for lc in self.ctx.project["load_cases"]])
        last = [e for e in self.ctx.store.events() if e["kind"] == "load_cases.created"][-1]
        self.assertEqual(last["agent"], "cae.strength")
        self.assert_events_fit()

    def test_through_mcp_a_call_without_its_caller_is_refused(self):
        from hiraku.tools.mcp import DESCRIPTIONS, HANDLERS, INPUT_SCHEMAS, make_handler

        self.assertIn("write_load_case", INPUT_SCHEMAS)
        self.assertIn("limit_from", DESCRIPTIONS["write_load_case"])
        out = asyncio.run(make_handler(self.ctx, Attribution(), "write_load_case", HANDLERS["write_load_case"])(r2_args()))
        self.assertTrue(out["is_error"])
        self.assertFalse(self.ctx.has_load_case("R2-g3"))


class BeforeVersionTest(Run, unittest.TestCase):
    def test_no_design_no_load_case(self):
        with self.assertRaisesRegex(ToolError, "v1"):
            write_load_case(self.ctx, r2_args(), "lead")


class RolesTest(unittest.TestCase):
    def test_the_cae_roles_and_the_packager_have_it_and_the_designer_not(self):
        from hiraku.agent import roles
        from hiraku.tools.functions import RUN_TOOLS

        self.assertIn("write_load_case", RUN_TOOLS)
        self.assertIn("write_load_case", roles.CAE_TOOLS)
        self.assertIn("write_load_case", roles.PACKAGER_TOOLS)
        self.assertNotIn("write_load_case", roles.DESIGNER_TOOLS)


class IntakeRegistrationTest(unittest.TestCase):
    def test_the_writer_registers_itself_when_the_intake_takes_one(self):
        """hiraku.intake's register_load_case_writer (the intake department's branch) is called on import when it is
        there; the module is loaded again here under another name with a stand-in for it."""
        import importlib.util
        import sys
        from unittest import mock

        from hiraku import intake
        from hiraku.tools import load_case_writer

        got = []
        name = "hiraku.tools._load_case_writer_again"
        spec = importlib.util.spec_from_file_location(name, load_case_writer.__file__)
        mod = importlib.util.module_from_spec(spec)
        with mock.patch.object(intake, "register_load_case_writer", got.append, create=True), \
                mock.patch.dict(sys.modules, {name: mod}):
            spec.loader.exec_module(mod)
        self.assertEqual(got, [mod.write_load_case])


class AnalyzedTest(unittest.TestCase):
    """With the real K0 formulas and tools: the written R2 case is predicted, analyzed with frame_static (its
    g_factor read by the tool) and compared within its tolerance."""

    def test_frame_static_runs_on_the_written_case(self):
        from hiraku.tools import load_solvers
        from hiraku.tools.functions import analyze, compare, record_prediction

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            formulas, tools = load_solvers()
            ctx = start_run(robot_requirements(root), root=root / "runs", formulas=formulas, tools=tools)
            write_design(ctx, {"design": serving_robot(), "reason": "配膳ロボの骨組み"}, "designer")
            lc_id = write_load_case(ctx, r2_args(), "cae.strength")["load_case_id"]
            pred = record_prediction(ctx, {
                "load_case_id": lc_id, "quantity": "max_stress", "formula_id": "frame_static.rail_bending",
                "output": "sigma",
                "inputs": {"F": {"value": 300.0, "unit": "N"}, "L": {"value": 500.0, "unit": "mm"},
                           "b": {"value": 20.0, "unit": "mm"}, "h": {"value": 20.0, "unit": "mm"},
                           "t": {"value": 1.6, "unit": "mm"}},
                "assumptions": ["棚の 1 本の管を単純支持の梁とみる (この試験の前提)"]}, "cae.strength")
            ana = analyze(ctx, {"tool": "frame_static", "load_case_id": lc_id,
                                "prediction_ids": [pred["prediction_id"]]}, "cae.strength")
            self.assertEqual(ana["status"], "ok", ana.get("error"))
            self.assertTrue(any(r["quantity"] == "max_stress" for r in ana["results"]))
            out = compare(ctx, {"prediction_id": pred["prediction_id"], "analysis_id": ana["analysis_id"]},
                          "cae.strength")
            cmp = out["comparison"]
            self.assertEqual((cmp["prediction_id"], cmp["analysis_id"]), (pred["prediction_id"], ana["analysis_id"]))
            self.assertEqual(cmp["tolerance_rel"], WRITTEN_TOLERANCE_REL)  # the written case's, set by the code
            for e in ctx.store.events():
                self.assertEqual(contract.errors("event", e), [], e["kind"])


if __name__ == "__main__":
    unittest.main()
