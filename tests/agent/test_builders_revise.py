"""Load cases from hiraku.catalog.builders, the scripted run reading their steps, and revise_design.

hiraku.catalog.builders and hiraku.designgen are stand-ins here
(tests.tools.fakes): an H1 assembly test whose stand-in tool finds the
steered wheel hitting the rail when the track is over 1200 mm.
"""

import contextlib
import copy
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

from hiraku import contract
from hiraku.agent import scripted
from hiraku.agent.scripted import compose_plan, load_plan, run_scripted
from hiraku.agent.session import start_run
from hiraku.catalog import CatalogError, build_load_cases, load_catalog, read_data
from hiraku.contract import layout
from hiraku.tools import ToolError
from hiraku.tools.functions import get_context, revise_design
from hiraku.tools.judge import update_judgement
from tests.tools import fakes
from tests.tools.helpers import REQUIREMENTS, requirements_with_tests

TRACK_FIX = {"track": {"value": 1150.0, "unit": "mm"}}
REVISE = [{"changes": TRACK_FIX, "reason": "H1: the steered front wheel hits the rail; a narrower track keeps it clear"}]


@contextlib.contextmanager
def builders(module):
    """hiraku.catalog.builders as `module`. Only this key of sys.modules is set and removed:
    patching sys.modules as a whole would also drop modules imported meanwhile (numpy
    cannot be imported twice)."""
    name = "hiraku.catalog.builders"
    old = sys.modules.get(name)
    sys.modules[name] = module
    try:
        yield
    finally:
        if old is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = old


class BuildersCase:
    """A run of P1, R1 and the stand-in H1, with the stand-in builders module and designgen."""

    tests = ["P1", "R1", "H1"]
    plans = True

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.enterContext(builders(fakes.builders_module(self.plans)))
        design = self.root / "design.json"
        design.write_text(json.dumps(fakes.loaded_design()), encoding="utf-8")
        self.designgen = fakes.FakeDesigngen()
        self.ctx = start_run(requirements_with_tests(self.root, self.tests), design_path=design, root=self.root / "runs",
                             formulas=fakes.FakeFormulas(), tools=fakes.shelf(assembly=True), designgen=self.designgen)

    def tearDown(self):
        self._tmp.cleanup()

    def events(self, kind):
        return [e for e in self.ctx.store.events() if e["kind"] == kind]


class CatalogBuildersTest(unittest.TestCase):
    def test_builders_module_supplies_tests_without_build(self):
        catalog = load_catalog()
        req = read_data(REQUIREMENTS)
        with builders(fakes.builders_module()):
            (h1,) = build_load_cases(catalog, req, fakes.loaded_design(), ["H1"])
            self.assertEqual((h1["load_case_id"], h1["domain"]), ("H1-steer30", "assembly"))
            with self.assertRaisesRegex(CatalogError, "S1 has no load-case builder"):
                build_load_cases(catalog, req, fakes.loaded_design(), ["H1", "S1"])
            skipped = []
            out = build_load_cases(catalog, req, fakes.loaded_design(), ["P1", "S1", "R1"], skipped)
            self.assertEqual([lc["test_id"] for lc in out], ["P1", "R1"])
            self.assertEqual(out[0]["feeds"], ["R1-step100"])
            self.assertEqual([t for t, _ in skipped], ["S1"])
        # a builders module with none: only the catalog's own builders
        with builders(types.SimpleNamespace()), self.assertRaisesRegex(CatalogError, "H1 has no load-case builder"):
            build_load_cases(catalog, req, fakes.loaded_design(), ["H1"])


class BeyondBuildTest(unittest.TestCase):
    """Cases past the limit (requirements `beyond`): built with the catalog spec's variant "beyond"."""

    def setUp(self):
        self.catalog = load_catalog()
        self.req = read_data(REQUIREMENTS)
        self.enterContext(builders(fakes.builders_module()))

    def test_each_test_gets_its_case_past_the_limit_fed_by_its_own_kind(self):
        out = build_load_cases(self.catalog, self.req, fakes.loaded_design(), ["P1", "R1", "H1"], beyond=["P1", "R1", "H1"])
        ids = [lc["load_case_id"] for lc in out]
        self.assertEqual(ids, ["P1-step100", "R1-step100", "H1-steer30",
                               "P1-step250-beyond", "P1-step330-beyond", "R1-step250-beyond", "H1-steer40-beyond"])
        by_id = {lc["load_case_id"]: lc for lc in out}
        self.assertEqual(by_id["P1-step100"]["feeds"], ["R1-step100"])
        self.assertEqual(by_id["P1-step250-beyond"]["feeds"], ["R1-step250-beyond"])
        # the step past the wheel (1.1 x the 300 mm front wheel): judged on climbable, feeds nothing
        wall = by_id["P1-step330-beyond"]
        self.assertNotIn("feeds", wall)
        self.assertEqual((wall["criterion"]["quantity"], wall["criterion"]["op"], wall["criterion"]["limit"]),
                         ("climbable", ">=", {"value": 1.0, "unit": "1"}))
        self.assertEqual(wall["params"]["step_height"], {"value": 330.0, "unit": "mm"})
        for lc in out:
            self.assertEqual(contract.errors("load_case", lc), [], lc["load_case_id"])
            self.assertEqual(lc["title"].startswith("限界の外"), lc["load_case_id"].endswith("-beyond"))
        self.assertEqual(by_id["R1-step250-beyond"]["params"]["step_height"], {"value": 250.0, "unit": "mm"})
        self.assertEqual(by_id["R1-step250-beyond"]["criterion"], by_id["R1-step100"]["criterion"])

    def test_refusals(self):
        high = copy.deepcopy(self.req)
        next(r for r in high["requirements"] if r["key"] == "Q15")["value"] = 300
        with self.assertRaisesRegex(CatalogError, "higher than Q15"):
            build_load_cases(self.catalog, high, fakes.loaded_design(), ["P1"], beyond=["P1"])
        skipped = []
        out = build_load_cases(self.catalog, high, fakes.loaded_design(), ["P1"], skipped, beyond=["P1"])
        self.assertEqual(([lc["load_case_id"] for lc in out], [t for t, _ in skipped]), (["P1-step300"], ["P1"]))
        self.assertTrue(skipped[0][1].startswith("限界の外: "))
        named_badly = types.SimpleNamespace(BUILDERS={"H1": lambda spec, req, design: fakes._h1_builder({}, req, design)})
        with builders(named_badly), self.assertRaisesRegex(CatalogError, "id ending -beyond"):
            build_load_cases(self.catalog, self.req, fakes.loaded_design(), [], beyond=["H1"])

    def test_the_drive_record_feeds_nothing(self):
        """requirements `drive`: P1's record of the run at speed (record_run speed), no criterion, feeding nothing,
        so R1 keeps reading the push's forces."""
        out = build_load_cases(self.catalog, self.req, fakes.loaded_design(), ["P1", "R1"], drive=["P1"])
        by_id = {lc["load_case_id"]: lc for lc in out}
        self.assertEqual(list(by_id), ["P1-step100", "R1-step100", "P1-step100-drive"])
        drive = by_id["P1-step100-drive"]
        self.assertEqual(drive["params"]["record_run"], "speed")
        self.assertEqual(drive["params"]["step_height"], by_id["P1-step100"]["params"]["step_height"])
        self.assertNotIn("criterion", drive)
        self.assertNotIn("feeds", drive)
        self.assertEqual(by_id["P1-step100"]["feeds"], ["R1-step100"])
        self.assertEqual(contract.errors("load_case", drive), [])
        with self.assertRaisesRegex(CatalogError, "no drive record"):
            build_load_cases(self.catalog, self.req, fakes.loaded_design(), [], drive=["H1"])


class SystemCheckTest(BuildersCase, unittest.TestCase):
    """A system rule (category system) that hits a part turns the assembly tile red."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.enterContext(builders(fakes.builders_module()))
        design = self.root / "design.json"
        design.write_text(json.dumps(fakes.loaded_design()), encoding="utf-8")
        self.ctx = start_run(requirements_with_tests(self.root, ["P1", "R1", "H1"]), design_path=design,
                             root=self.root / "runs", formulas=fakes.FakeFormulas(), tools=fakes.shelf(assembly=True),
                             designgen=fakes.FakeDesigngen())
        rule = {"rule_id": "SY-01", "category": "system", "text": "モーターから駆動輪まで torque の鎖がつながる",
                "check": "True", "origin": "builtin", "trust": 2, "basis": "test"}
        self.ctx.store.emit("knowledge.imported", agent="lead", data={
            "doc": {"doc_id": "SY", "filename": "system.yaml", "origin": "builtin"}, "rules": [rule]})

    def check(self, rule_id, hits):
        s = self.ctx.store
        return s.add_record("rule_check", {"rule_check_id": s.next_id("rule_check"), "design_version": 1,
                                           "rule_id": rule_id, "targets": ["motor"], "hits": hits, "pass": not hits},
                            design_version=1, agent="packager")

    def tile(self):
        return next(j for j in self.ctx.store.judgements() if j["domain"] == "assembly")

    def test_a_system_miss_turns_assembly_red_and_says_where(self):
        self.check("SY-01", [{"part_id": "motor", "values": {}, "message": "後輪までの torque の鎖が切れている"}])
        tile = update_judgement(self.ctx, "assembly")
        self.assertEqual((tile["state"], tile["red_count"]), ("red", 1))
        self.assertIn("系の欠け 1 件: SY-01", tile["summary"])
        self.assertIn("後輪までの torque の鎖が切れている", tile["summary"])
        self.check("SY-01", [])  # checked again after the fix: the latest check counts
        self.assertNotEqual(update_judgement(self.ctx, "assembly")["state"], "red")

    def test_an_advice_rule_never_turns_assembly_red(self):
        """A check of a rule of level advice (docs/contract.md 規則の強さ) is shown but fails no design."""
        s = self.ctx.store
        s.add_record("rule_check", {"rule_check_id": s.next_id("rule_check"), "design_version": 1, "rule_id": "SY-01",
                                    "targets": ["motor"], "level": "advice", "pass": False,
                                    "hits": [{"part_id": "motor", "values": {}, "message": "m"}]},
                     design_version=1, agent="packager")
        self.assertNotEqual(update_judgement(self.ctx, "assembly")["state"], "red")

    def test_other_categories_and_other_domains_are_not_affected(self):
        self.check("XX-01", [{"part_id": "seat", "values": {}, "message": "m"}])  # not a system rule
        self.assertNotEqual(update_judgement(self.ctx, "assembly")["state"], "red")
        self.check("SY-01", [{"part_id": "motor", "values": {}, "message": "m"}])
        self.assertNotIn("系の欠け", update_judgement(self.ctx, "strength")["summary"])


class BeyondRunTest(BuildersCase, unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.enterContext(builders(fakes.builders_module()))
        design = self.root / "design.json"
        design.write_text(json.dumps(fakes.loaded_design()), encoding="utf-8")
        self.ctx = start_run(requirements_with_tests(self.root, ["P1", "R1", "H1"], beyond=["P1", "R1", "H1"]),
                             design_path=design, root=self.root / "runs", formulas=fakes.FakeFormulas(),
                             tools=fakes.shelf(assembly=True), designgen=fakes.FakeDesigngen())

    def test_the_plan_takes_the_steps_past_the_limit(self):
        self.assertEqual(self.ctx.project["beyond"], ["P1", "R1", "H1"])
        self.assertEqual(contract.errors("project", self.ctx.project), [])
        plan = compose_plan(self.ctx)
        steps = {s["load_case"]: s for s in plan["steps"]}
        self.assertEqual(steps["H1-steer40-beyond"]["say"], fakes.H1_BEYOND_PLAN["say"])  # PLANS_BEYOND first
        self.assertEqual(steps["H1-steer30"]["say"], fakes.H1_PLAN["say"])
        self.assertEqual(steps["P1-step250-beyond"]["tool"], "step_climb")  # data/catalog/scripted.yaml
        # the stand-in shelf has no step_climb_dynamic: those steps are left out and named
        self.assertEqual(plan["unshelved"], [("P1-step100", "step_climb_dynamic"),
                                             ("P1-step250-beyond", "step_climb_dynamic"),
                                             ("P1-step330-beyond", "step_climb_dynamic")])
        order = [s["load_case"] for s in plan["steps"]]
        if "R1-step250-beyond" in order:
            self.assertLess(order.index("P1-step250-beyond"), order.index("R1-step250-beyond"))
        # ana.P1.x read by a case past the limit is the P1 case past the limit
        r1 = self.ctx.load_case("R1-step250-beyond")
        self.assertEqual(scripted._load_case_of(self.ctx, "P1", r1), "P1-step250-beyond")
        self.assertEqual(scripted._load_case_of(self.ctx, "P1", self.ctx.load_case("R1-step100")), "P1-step100")


class AutoTestsTest(BuildersCase, unittest.TestCase):
    tests = None  # the requirements file lists no tests: the catalog picks them from the flags

    def test_every_buildable_test_is_run_and_the_rest_named(self):
        self.assertEqual([lc["test_id"] for lc in self.ctx.load_cases], ["P1", "R1", "H1"])  # R1s only when listed
        (said,) = [e for e in self.events("agent.said") if e["agent"] == "system"]
        self.assertIn("荷重条件の作り方がまだ無い: S1 静的な転倒", said["text"])
        self.assertNotIn("H1", said["text"])
        self.assertIn("製品の性質", self.ctx.project["domain_picks"][0]["reason"])
        self.assertEqual([p["domain"] for p in self.ctx.project["domain_picks"]], ["strength", "assembly"])
        agents = {a["agent_id"]: a["role"] for a in self.ctx.project["agents"]}
        self.assertEqual(agents, {"lead": "lead", "designer": "designer", "packager": "packager",
                                  "cae.strength": "cae"})
        self.assertEqual(self.ctx.owner("H1-steer30"), "packager")


class ReviseDesignTest(BuildersCase, unittest.TestCase):
    def test_designer_sees_the_revisable_variables(self):
        (track,) = get_context(self.ctx, {"sections": ["design_variables"]})["design_variables"]
        self.assertEqual(track, {"name": "track", "unit": "mm", "min": 1000.0, "max": 1600.0,
                                 "meaning": "front and rear track", "value": {"value": 1300.0, "unit": "mm"}})

    def test_designer_makes_the_next_version(self):
        out = revise_design(self.ctx, {"changes": TRACK_FIX, "reason": "narrower track"}, "designer")
        self.assertEqual(self.designgen.calls, [TRACK_FIX])
        self.assertEqual((out["design_version"], out["parent_version"], out["revisions_left"]), (2, 1, 0))
        self.assertEqual(out["changes"], [{"variable": "track", "from": {"value": 1300.0, "unit": "mm"},
                                           "to": TRACK_FIX["track"]}])
        self.assertEqual(out["redo"], ["P1-step100", "R1-step100", "H1-steer30"])
        d2 = self.ctx.store.design()
        self.assertEqual((d2["design_version"], d2["parent_version"], d2["change_reason"]), (2, 1, "narrower track"))
        self.assertEqual(d2["variables"]["track"], TRACK_FIX["track"])
        created = self.events("design.created")[-1]
        self.assertEqual((created["agent"], created["design_version"], created["text"]), ("designer", 2, "narrower track"))
        self.assertEqual({j["state"] for j in self.ctx.store.judgements(2)}, {"pending"})
        self.assertEqual(self.ctx.version, 2)

    def test_refusals(self):
        cases = [
            ({"changes": TRACK_FIX, "reason": "x"}, "lead", "設計役"),
            ({"changes": {"girth": {"value": 1.0, "unit": "mm"}}, "reason": "x"}, "designer", "'girth' はありません"),
            ({"changes": {"wheelbase": {"value": 1.0, "unit": "mm"}}, "reason": "x"}, "designer", "直せない変数"),
            ({"changes": {"track": {"value": 1.15, "unit": "m"}}, "reason": "x"}, "designer", "単位は mm"),
            ({"changes": {"track": {"value": 900.0, "unit": "mm"}}, "reason": "x"}, "designer", "1000〜1600 mm の範囲"),
            ({"changes": TRACK_FIX, "reason": "  "}, "designer", "reason"),
            ({"changes": {}, "reason": "x"}, "designer", "入力の形"),
            ({"changes": {"track": {"value": 1550.0, "unit": "mm"}}, "reason": "x"}, "designer", "rails cannot reach"),
        ]
        for args, agent, needle in cases:
            with self.subTest(needle=needle):
                with self.assertRaises(ToolError) as cm:
                    revise_design(self.ctx, args, agent)
                self.assertIn(needle, str(cm.exception))
        self.assertEqual(self.ctx.version, 1)

    def test_round_limit_and_missing_designgen(self):
        revise_design(self.ctx, {"changes": TRACK_FIX, "reason": "first"}, "designer")
        with self.assertRaisesRegex(ToolError, "round_limit"):  # the requirements allow one revision
            revise_design(self.ctx, {"changes": TRACK_FIX, "reason": "second"}, "designer")
        self.ctx.project["round_limit"] = 5
        self.ctx.designgen = None
        with self.assertRaisesRegex(ToolError, "hiraku.designgen"):
            revise_design(self.ctx, {"changes": TRACK_FIX, "reason": "third"}, "designer")
        self.assertIsNone(get_context(self.ctx, {"sections": ["design_variables"]})["design_variables"])

    def test_the_cost_refusal_says_whose_cost_it_reads(self):
        self.ctx.project["cost_usd"] = self.ctx.project["cost_limit_usd"]  # as written at the end of the lead's turn
        with self.assertRaises(ToolError) as cm:
            revise_design(self.ctx, {"changes": TRACK_FIX, "reason": "x"}, "designer")
        self.assertIn("まとめ役の最後に終わった番の時点で", str(cm.exception))

    def test_a_null_cost_does_not_stop_a_revision(self):
        self.ctx.project["cost_usd"] = None  # the plugin path: HIRAKU cannot know the cost
        out = revise_design(self.ctx, {"changes": TRACK_FIX, "reason": "x"}, "designer")
        self.assertEqual(out["design_version"], 2)


class ScriptedReviseTest(BuildersCase, unittest.TestCase):
    def plan(self, revise=True):
        """The offroad steps, with this test's revision (the stand-in designgen has only track) or none."""
        plan = dict(load_plan("offroad-4w"))
        plan["revise"] = REVISE if revise else []
        plan["rule_check"] = False  # the stand-in design has no systems: the knowledge's system rules would hit
        return plan

    def test_compose_adds_builder_steps_upstream_first(self):
        plan = compose_plan(self.ctx, {"steps": [load_plan("offroad-4w")["steps"][1]]})  # R1 alone
        self.assertEqual([s["load_case"] for s in plan["steps"]], ["R1-step100", "H1-steer30"])
        self.assertEqual(plan["missing"], ["P1-step100"])
        plan = compose_plan(self.ctx, {"steps": list(reversed(load_plan("offroad-4w")["steps"]))})
        self.assertEqual([s["load_case"] for s in plan["steps"]], ["P1-step100", "R1-step100", "H1-steer30"])

    def test_a_step_for_a_load_case_the_run_did_not_build_is_left_out(self):
        steps = [dict(s, load_case=s["load_case"].replace("step100", "step250")) for s in load_plan("offroad-4w")["steps"]]
        plan = compose_plan(self.ctx, {"steps": steps})
        self.assertEqual([s["load_case"] for s in plan["steps"]], ["H1-steer30"])
        self.assertEqual(plan["missing"], ["P1-step100", "R1-step100"])

    def test_red_assembly_is_fixed_by_the_designer_and_judged_again(self):
        self.assertEqual(run_scripted(self.ctx, self.plan()), "done")
        self.assertEqual(self.events("run.finished")[-1]["data"]["stop_code"], "done")
        v1 = {j["domain"]: j["state"] for j in self.ctx.store.judgements(1)}
        v2 = {j["domain"]: j["state"] for j in self.ctx.store.judgements(2)}
        self.assertEqual(v1, {"strength": "pending", "assembly": "red"})  # HK5: stage 0 red, revised before R1
        self.assertEqual(v2, {"strength": "green", "assembly": "green"})
        self.assertEqual(self.designgen.calls, [TRACK_FIX])
        # a zero limit (interference <= 0 mm): margin in mm, no percentage
        h1_verdicts = [v for v in self.ctx.store.records("verdict") if v["load_case_id"] == "H1-steer30"]
        self.assertEqual([(v["margin"], v["margin_pct"]) for v in h1_verdicts],
                         [({"value": -100.0, "unit": "mm"}, None), ({"value": 0.0, "unit": "mm"}, None)])
        h1 = [p for p in self.ctx.store.records("prediction") if p["load_case_id"] == "H1-steer30"]
        self.assertEqual([(p["design_version"], p["value"], p["agent"]) for p in h1],
                         [(1, 100.0, "packager"), (2, 0.0, "packager")])
        self.assertEqual({p["design_version"] for p in self.ctx.store.records("prediction")
                          if p["load_case_id"] == "R1-step100"}, {2})  # the heavier stage waits for the layout
        created = self.events("design.created")
        self.assertEqual([(e["agent"], e["design_version"]) for e in created], [("designer", 1), ("designer", 2)])
        started = [e["agent"] for e in self.events("agent.started")]
        self.assertEqual(started, ["lead", "packager", "designer", "cae.strength"])
        said = [e["text"] for e in self.events("agent.said")]
        self.assertIn("設計の版 2 で、段 0 から全部の荷重条件をやり直します", said)
        self.assertIn("版 1、段 0 (レイアウトと規則)の荷重条件を確かめます: H1-steer30", said)
        self.assertIn("版 2、段 1 (軽い計算)の荷重条件を確かめます: P1-step100、R1-step100", said)
        self.assertNotIn("hook.denied", [e["kind"] for e in self.ctx.store.events()
                                         if (e.get("data") or {}).get("rule") == "HK5_light_first"])
        events = self.ctx.store.events()
        self.assertEqual([e["seq"] for e in events], list(range(len(events))))

    def test_without_a_revision_the_red_layout_holds_the_heavier_stages(self):
        # HK5: a red stage 0 and no revision in the plan: the lighter stage's red is the answer,
        # the heavier stages are not checked (a revision was left in round_limit, so not_judged)
        self.assertEqual(run_scripted(self.ctx, self.plan(revise=False)), "stopped")
        self.assertEqual(self.ctx.version, 1)
        self.assertEqual({j["domain"]: j["state"] for j in self.ctx.store.judgements()},
                         {"strength": "pending", "assembly": "red"})
        finished = self.events("run.finished")[-1]["data"]
        self.assertEqual(finished["stop_code"], "not_judged")
        self.assertIn("台本に設計の直しが残っていません", finished["stop_reason"])
        self.assertIn("HK5", finished["stop_reason"])

    def test_red_after_the_last_revision_ends_at_the_round_limit(self):
        plan = self.plan()
        plan["revise"] = [{"changes": {"track": {"value": 1250.0, "unit": "mm"}}, "reason": "not enough"}]
        self.assertEqual(run_scripted(self.ctx, plan), "stopped")
        self.assertEqual((self.ctx.version, self.ctx.store.judgements()[-1]["state"]), (2, "red"))
        finished = self.events("run.finished")[-1]["data"]
        self.assertEqual((finished["status"], finished["stop_code"]), ("stopped", "round_limit"))
        self.assertIn("組み立てが赤のまま", finished["stop_reason"])
        events = self.ctx.store.events()
        self.assertEqual(contract.errors("event", events[-1]), [])

    def test_refused_revision_stops_the_run(self):
        plan = self.plan()
        plan["revise"] = [{"changes": {"track": {"value": 900.0, "unit": "mm"}}, "reason": "too narrow"}]
        self.assertEqual(run_scripted(self.ctx, plan), "stopped")
        self.assertIn("範囲", self.ctx.project["stop_reason"])


class MissingStepTest(BuildersCase, unittest.TestCase):
    plans = False  # the builders module has H1's builder but no step for the scripted run

    def test_load_case_without_a_step_is_named_and_left_pending(self):
        self.assertEqual(run_scripted(self.ctx), "stopped")
        said = [e["text"] for e in self.ctx.store.events() if e["kind"] == "agent.said"]
        self.assertIn("台本の予測のしかたが無いので回せない荷重条件: H1-steer30", said)
        self.assertIn("H1-steer30", self.ctx.project["stop_reason"])
        self.assertEqual(self.events("run.finished")[-1]["data"]["stop_code"], "not_judged")
        project = json.loads((self.ctx.store.path / layout.PROJECT_FILE).read_text(encoding="utf-8"))
        self.assertEqual(project["status"], "stopped")


if __name__ == "__main__":
    unittest.main()
