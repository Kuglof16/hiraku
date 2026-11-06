"""A run whose requirements file names no design: the designer makes the first version
(propose_design, hiraku.designgen.propose) and the load cases follow (project.updated).

The design is the real one hiraku.designgen builds; the solvers are the test stand-ins.
"""

import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from hiraku import contract
from hiraku.contract import layout
from hiraku.agent.hooks import Hooks
from hiraku.agent.runner import build_options
from hiraku.agent.scripted import load_plan, run_scripted
from hiraku.agent.session import start_run
from hiraku.catalog import read_data
from hiraku.tools import Attribution, ToolError, rules
from hiraku.tools.functions import get_context, propose_design
from hiraku.tools.mcp import build_server
from tests.tools import fakes
from tests.tools.helpers import REQUIREMENTS


def requirements_without_design(folder: Path, tests: list[str]) -> Path:
    req = read_data(REQUIREMENTS)
    req.pop("design_file")
    if tests is None:  # the tests are not fixed: the catalog picks them on version 1 (a products file's run)
        req.pop("tests", None)
    else:
        req["tests"] = tests
    path = folder / "requirements.json"
    path.write_text(json.dumps(req, ensure_ascii=False), encoding="utf-8")
    return path


class FirstDesignCase:
    tests = ["P1", "R1"]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.ctx = start_run(requirements_without_design(root, self.tests), root=root / "runs",
                             formulas=fakes.FakeFormulas(), tools=fakes.shelf())

    def kinds(self):
        return [e["kind"] for e in self.ctx.store.events()]


class PicksOnlyTest(unittest.TestCase):
    """docs/contract.md 早めに終わる運転: stop_after picks ends the run after the domain picks, HK4 waits for nothing."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        path = requirements_without_design(root, ["P1", "R1"])
        req = json.loads(path.read_text(encoding="utf-8"))
        req["stop_after"] = "picks"
        path.write_text(json.dumps(req, ensure_ascii=False), encoding="utf-8")
        self.ctx = start_run(path, root=root / "runs", formulas=fakes.FakeFormulas(), tools=fakes.shelf(), intake=True)

    def test_hk4_lets_the_stop_through_after_the_picks(self):
        from hiraku.agent.prompts import lead_task
        from hiraku.agent.runner import end_status

        self.assertEqual(self.ctx.project["stop_after"], "picks")
        self.assertEqual(contract.errors("project", self.ctx.project), [])
        self.assertIn("domains.picked", [e["kind"] for e in self.ctx.store.events()])
        self.assertIsNone(rules.stop_reason(self.ctx))
        self.assertIsNone(rules.unjudged_reason(self.ctx))
        self.assertEqual(end_status(self.ctx), ("done", rules.PICKS_ONLY_REASON, "done"))
        self.assertIn("分野選びまで", lead_task(self.ctx))

    def test_the_scripted_run_ends_after_the_picks(self):
        self.assertEqual(run_scripted(self.ctx), "done")
        kinds = [e["kind"] for e in self.ctx.store.events()]
        self.assertNotIn("design.created", kinds)
        fin = [e for e in self.ctx.store.events() if e["kind"] == "run.finished"][-1]
        self.assertEqual((fin["data"]["status"], fin["data"]["stop_code"]), ("done", "done"))
        self.assertIn("分野選びまで", fin["data"]["stop_reason"])

    def test_without_stop_after_hk4_still_waits(self):
        with mock.patch.object(rules, "stop_after", return_value=None):
            self.assertIn("propose_design", rules.stop_reason(self.ctx))

    def test_before_the_picks_hk4_still_waits(self):
        with mock.patch.object(self.ctx.store, "events", return_value=[]):
            self.assertFalse(rules.picks_done(self.ctx))


class HearingByClaudeTest(unittest.TestCase):
    """docs/contract.md hearing claude: start_run leaves the open items for the lead's ask_user (no hearing.* events,
    the file's requirements as they are); the project carries hearing, and the lead is told to ask."""

    def test_the_open_items_are_left_for_ask_user(self):
        from hiraku.agent.prompts import HEARING_CLAUDE_TEXT, lead_task

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = requirements_without_design(root, ["P1", "R1"])
            req = json.loads(path.read_text(encoding="utf-8"))
            req["hearing"] = "claude"
            req["requirements"] = req["requirements"][:5]  # most items left open
            path.write_text(json.dumps(req, ensure_ascii=False), encoding="utf-8")
            ctx = start_run(path, root=root / "runs", formulas=fakes.FakeFormulas(), tools=fakes.shelf(), intake=True)
            kinds = [e["kind"] for e in ctx.store.events()]
            self.assertEqual(ctx.project["hearing"], "claude")
            self.assertEqual(contract.errors("project", ctx.project), [])
            self.assertNotIn("hearing.answered", kinds)
            self.assertIn("domains.picked", kinds)
            self.assertEqual([r["key"] for r in ctx.project["requirements"]], [r["key"] for r in req["requirements"]])
            self.assertIn(HEARING_CLAUDE_TEXT, lead_task(ctx))


class ReviseWithChoicesTest(FirstDesignCase, unittest.TestCase):
    """Contract cb66036: revise_design takes the generator's choices (get_context design_choices) besides changes."""

    def setUp(self):
        super().setUp()
        propose_design(self.ctx, {"archetype": "ladder_4w", "reason": "要件から"}, "designer")

    def owner(self, module):
        """The designer that owns a module of the design (design.modules owner: a design role)."""
        from hiraku.tools import rules

        return rules.module_owner(rules.design_modules(self.ctx), module)

    def test_a_choice_sets_its_variables(self):
        from hiraku.tools.functions import build_version, revise_design

        got = get_context(self.ctx, {"sections": ["design_choices"]})["design_choices"]
        self.assertIn("bulkhead", got["choices"]["pedal_mount"])
        # ladder_4w is split into modules (contract d9631a5): pedal_mount is the steering module's choice
        prop = revise_design(self.ctx, {"module": "steering", "choices": {"pedal_mount": "bulkhead"},
                                        "reason": "R7: ペダルの力を隔壁へ"}, self.owner("steering"))
        self.assertEqual(self.ctx.version, 1)  # a proposal, not a version
        with self.assertRaisesRegex(ToolError, "stand_pedal_accel"):  # the post leaves the steering module's space
            build_version(self.ctx, {"reason": "R7 の提案をまとめる"}, "packager")
        out = build_version(self.ctx, {"reason": "R7 の提案をまとめ、操作系の空間を柱の形から作り直す", "regrow": ["steering"]},
                            "packager")
        self.assertIn("空間を部品の形から作り直した: steering", self.ctx.store.design()["change_reason"])
        moved = {c["variable"]: c for c in out["changes"]}
        self.assertEqual(moved["pedal_mount"]["to"]["value"], 2)  # the variable the choice stands for
        self.assertEqual(out["choices"], {"pedal_mount": "bulkhead"})
        self.assertEqual(self.ctx.version, 2)
        self.assertEqual(out["module_changes"], [prop["change_id"]])

    def test_an_unknown_choice_or_value_is_refused(self):
        from hiraku.tools.functions import revise_design

        with self.assertRaisesRegex(ToolError, "no_such"):
            revise_design(self.ctx, {"module": "steering", "choices": {"no_such": "x"}, "reason": "r"},
                          self.owner("steering"))
        with self.assertRaisesRegex(ToolError, "bulkhead"):
            revise_design(self.ctx, {"module": "steering", "choices": {"pedal_mount": "roof"}, "reason": "r"},
                          self.owner("steering"))
        with self.assertRaisesRegex(ToolError, "module"):  # the single designer's revise_design: HK6
            revise_design(self.ctx, {"choices": {"pedal_mount": "bulkhead"}, "reason": "r"}, "designer")
        self.assertEqual(self.ctx.version, 1)


class FirstDesignTest(FirstDesignCase, unittest.TestCase):
    def test_the_run_starts_without_a_design(self):
        self.assertEqual(self.ctx.version, 0)
        self.assertEqual(self.ctx.load_cases, [])
        self.assertEqual([a["agent_id"] for a in self.ctx.project["agents"]], ["lead", "designer"])
        self.assertNotIn("design.created", self.kinds())
        self.assertIn("propose_design", rules.stop_reason(self.ctx))  # HK4: nothing to judge yet is no end
        kinds = get_context(self.ctx, {"sections": ["archetypes"]})["archetypes"]
        self.assertEqual(kinds[0]["name"], "ladder_4w")  # first: the scripted run's default (K2's space frame is another)

    def test_roles_are_defined_before_the_load_cases(self):
        att = Attribution()
        o = build_options(self.ctx, build_server(self.ctx, att), Hooks(self.ctx, att))
        self.assertIn("designer", o.agents)
        self.assertIn("packager", o.agents)  # H1 and others apply to the flags
        self.assertIn("cae-strength", o.agents)
        self.assertIn("mcp__hiraku__rule_check", o.agents["packager"].tools)
        self.assertIn("mcp__hiraku__analyze", o.agents["packager"].tools)

    def test_refusals(self):
        args = {"archetype": "ladder_4w", "reason": "要件から"}
        with self.assertRaisesRegex(ToolError, "設計役"):
            propose_design(self.ctx, args, "lead")
        with self.assertRaisesRegex(ToolError, "ladder_4w"):
            propose_design(self.ctx, dict(args, archetype="tricycle"), "designer")
        with self.assertRaisesRegex(ToolError, "reason"):
            propose_design(self.ctx, dict(args, reason=" "), "designer")
        with self.assertRaisesRegex(ToolError, "組めません"):  # a requirement's value is not the designer's to set
            propose_design(self.ctx, dict(args, variables={"occupant_mass": {"value": 60.0, "unit": "kg"}}), "designer")
        self.assertEqual(self.ctx.version, 0)

    def test_the_first_version_brings_the_load_cases(self):
        out = propose_design(self.ctx, {"archetype": "ladder_4w", "reason": "2 人と荷を載せる林道の車"}, "designer")
        self.assertEqual([lc["load_case_id"] for lc in out["load_cases"]], ["P1-step100", "R1-step100"])
        self.assertEqual(out["domain_picks"], ["strength"])
        d = self.ctx.store.design()
        self.assertEqual((d["design_version"], d["parent_version"], d["change_reason"]),
                         (1, None, "2 人と荷を載せる林道の車"))
        events = self.ctx.store.events()
        created = next(e for e in events if e["kind"] == "design.created")
        updated = next(e for e in events if e["kind"] == "project.updated")
        self.assertEqual(created["agent"], "designer")
        self.assertLess(created["seq"], updated["seq"])
        self.assertEqual([lc["load_case_id"] for lc in updated["data"]["load_cases"]], ["P1-step100", "R1-step100"])
        self.assertEqual(updated["data"], self.ctx.project)
        self.assertEqual([j["state"] for j in self.ctx.store.judgements()], ["pending"])
        self.assertEqual([a["agent_id"] for a in self.ctx.project["agents"]], ["lead", "designer", "cae.strength"])
        with self.assertRaisesRegex(ToolError, "revise_design"):
            propose_design(self.ctx, {"archetype": "ladder_4w", "reason": "again"}, "designer")
        for e in events:
            self.assertEqual(contract.errors("event", e), [], e["kind"])

    def test_a_refused_first_version_lets_the_run_end_with_the_refusal(self):
        """A propose_design the tool refuses is kept in the record; HK4 then lets the lead's stop through
        and the run ends not_judged with the refusal as its reason (the roles cannot fix it)."""
        import asyncio

        from hiraku.agent.runner import end_status
        from hiraku.tools.mcp import HANDLERS, make_handler

        hooks = Hooks(self.ctx, Attribution())
        self.assertEqual(hooks.decide_stop({})["decision"], "block")  # nothing tried yet: HK4 asks for the design
        args = {"archetype": "tricycle", "reason": "要件から"}
        att = Attribution()
        att.push(contract.mcp_name("propose_design"), args, "designer")
        out = asyncio.run(make_handler(self.ctx, att, "propose_design", HANDLERS["propose_design"])(args))
        self.assertTrue(out["is_error"])
        (said,) = [e for e in self.ctx.store.events() if (e.get("data") or {}).get("refused") == "propose_design"]
        self.assertEqual((said["agent"], said["data"]["agent"]), ("system", "designer"))
        self.assertIn("ladder_4w", rules.design_refusal(self.ctx))
        self.assertEqual(Hooks(self.ctx, Attribution()).decide_stop({}), {})
        status, reason, code = end_status(self.ctx)
        self.assertEqual((status, code), ("stopped", "not_judged"))
        self.assertIn("propose_design が断られました", reason)
        for e in self.ctx.store.events():
            self.assertEqual(contract.errors("event", e), [], e["kind"])

    def test_a_report_before_the_first_version(self):
        from hiraku.tools.functions import write_report

        issue = {"kind": "tool_error", "what": "最初の版を組めない", "where": "propose_design"}
        rep = write_report(self.ctx, {"summary": "版 1 を作れずに止まった", "next": [], "issues": [issue]}, "lead")
        self.assertEqual((rep["design_version"], rep["domains"], rep["next"], rep["issues"]), (0, [], [], [issue]))
        self.assertEqual(contract.errors("report", rep), [])
        last = self.ctx.store.events()[-1]
        self.assertEqual(last["kind"], "report.written")
        self.assertNotIn("design_version", last)  # an event's design_version is 1 or more
        self.assertEqual(contract.errors("event", last), [])
        with self.assertRaisesRegex(ToolError, "next"):
            write_report(self.ctx, {"summary": "x", "next": [{"domain": "strength", "change": "x", "reason": "y"}]}, "lead")

    def test_the_hearings_answers_reach_the_first_version(self):
        """ask_user writes the answers into project.requirements; the design and its load cases are built
        from those values, not from the file as it was at the start."""
        reqs = [dict(r, value=120.0) if r["key"] == "Q15" else r for r in self.ctx.project["requirements"]]
        self.ctx.store.update_project({"requirements": reqs}, agent="lead", text="ヒアリングの答えを要件に入れました")
        self.assertEqual(next(r for r in self.ctx.requirements_file["requirements"] if r["key"] == "Q15")["value"], 100)
        out = propose_design(self.ctx, {"archetype": "ladder_4w", "reason": "答えの段差で"}, "designer")
        self.assertEqual([lc["load_case_id"] for lc in out["load_cases"]], ["P1-step120", "R1-step120"])


class DeferredChecksTest(FirstDesignCase, unittest.TestCase):
    """No tests in the file (Claude picks them): no tool is checked at the start; version 1's tests'
    tools are checked when propose_design builds the load cases."""
    tests = None

    def test_the_tools_are_checked_once_version_1_has_its_tests(self):
        from hiraku.tools.functions import list_tools
        from hiraku.tools.shelf import DEFERRED_TEXT, NOT_YET

        self.assertNotIn("tool.checked", self.kinds())
        self.assertIn(DEFERRED_TEXT, [e.get("text") for e in self.ctx.store.events()])
        self.assertTrue(all(line.get("not_checked") == NOT_YET for line in list_tools(self.ctx)))
        out = propose_design(self.ctx, {"archetype": "ladder_4w", "reason": "要件から"}, "designer")
        tests = {lc["test_id"] for lc in self.ctx.load_cases}
        checked = [e["data"]["name"] for e in self.ctx.store.events() if e["kind"] == "tool.checked"]
        self.assertTrue(checked)
        self.assertTrue(all(set(self.ctx.tools[n].tests) & tests for n in checked))
        self.assertEqual(out["design_version"], 1)
        for e in self.ctx.store.events():
            self.assertEqual(contract.errors("event", e), [], e["kind"])


class ClaudesPicksTest(FirstDesignCase, unittest.TestCase):
    """No tests in the file and Claude picked with pick_domains: version 1 keeps Claude's picks, names and
    reasons in the project and builds Claude's tests; only the tests without a builder (N3, R2 here) are
    dropped, said in one line."""
    tests = None

    def test_the_first_version_keeps_claudes_picks(self):
        picks = [{"domain": "strength", "shown_name": "フレームの強さ", "reason": "段差で人と荷を支える",
                  "test_ids": ["R1", "R2"]},
                 {"domain": "nvh", "shown_name": "振動", "reason": "林道の揺れ", "test_ids": ["N3"]}]
        data = {"want": self.ctx.project["want"], "flags": self.ctx.project["flags"], "picks": picks, "off": []}
        self.ctx.store.emit("domains.picked", agent="lead", text="分野を選びました", data=data)
        out = propose_design(self.ctx, {"archetype": "ladder_4w", "reason": "要件から"}, "designer")
        self.assertEqual(self.ctx.project["domain_picks"], picks)
        self.assertEqual(out["domain_picks"], ["strength", "nvh"])
        self.assertEqual({lc["test_id"] for lc in self.ctx.load_cases}, {"R1"})
        said = [e["text"] for e in self.ctx.store.events() if e["kind"] == "agent.said" and "builder" in (e.get("text") or "")]
        self.assertEqual(len(said), 1)
        for t in ("R2", "N3"):
            self.assertIn(t, said[0])
        self.assertNotIn("R1 ", said[0])
        for e in self.ctx.store.events():
            self.assertEqual(contract.errors("event", e), [], e["kind"])


class ScriptedFirstDesignTest(FirstDesignCase, unittest.TestCase):
    tests = ["P1"]  # no criterion: the scripted run ends done once P1 is matched

    def test_the_scripted_run_proposes_first(self):
        plan = {"propose": {"archetype": "ladder_4w", "say": "要件から最初の版を作ります", "reason": "既定の変数で"},
                "steps": [load_plan("offroad-4w")["steps"][0]]}
        self.assertEqual(run_scripted(self.ctx, plan), "done")
        kinds = self.kinds()
        self.assertLess(kinds.index("design.created"), kinds.index("project.updated"))
        self.assertLess(kinds.index("project.updated"), kinds.index("prediction.recorded"))
        said = [e["text"] for e in self.ctx.store.events() if e["kind"] == "agent.said" and e["agent"] == "designer"]
        self.assertEqual(said, ["要件から最初の版を作ります", "v1 ができました。試験: P1-step100"])
        self.assertEqual(self.ctx.store.design()["change_reason"], "既定の変数で")

    def test_stop_button_before_the_first_version(self):
        (self.ctx.store.path / layout.STOP_REQUEST_FILE).write_text("", encoding="utf-8")
        self.assertEqual(run_scripted(self.ctx), "stopped")
        self.assertNotIn("design.created", self.kinds())  # the load cases were not built
        self.assertEqual(self.ctx.store.events()[-1]["data"]["stop_code"], "user_stop")
        self.assertFalse((self.ctx.store.path / layout.STOP_REQUEST_FILE).exists())


if __name__ == "__main__":
    unittest.main()
