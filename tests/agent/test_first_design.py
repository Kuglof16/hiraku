"""A run whose requirements file names no design: the designer makes the first version
(propose_design, hiraku.designgen.propose) and the load cases follow (project.updated).

The design is the real one hiraku.designgen builds; the solvers are the test stand-ins.
"""

import json
import tempfile
import unittest
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

    def test_the_hearings_answers_reach_the_first_version(self):
        """ask_user writes the answers into project.requirements; the design and its load cases are built
        from those values, not from the file as it was at the start."""
        reqs = [dict(r, value=120.0) if r["key"] == "Q15" else r for r in self.ctx.project["requirements"]]
        self.ctx.store.update_project({"requirements": reqs}, agent="lead", text="ヒアリングの答えを要件に入れました")
        self.assertEqual(next(r for r in self.ctx.requirements_file["requirements"] if r["key"] == "Q15")["value"], 100)
        out = propose_design(self.ctx, {"archetype": "ladder_4w", "reason": "答えの段差で"}, "designer")
        self.assertEqual([lc["load_case_id"] for lc in out["load_cases"]], ["P1-step120", "R1-step120"])


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
        self.assertEqual(said, ["要件から最初の版を作ります", "版 1 ができました。試験: P1-step100"])
        self.assertEqual(self.ctx.store.design()["change_reason"], "既定の変数で")

    def test_stop_button_before_the_first_version(self):
        (self.ctx.store.path / layout.STOP_REQUEST_FILE).write_text("", encoding="utf-8")
        self.assertEqual(run_scripted(self.ctx), "stopped")
        self.assertNotIn("design.created", self.kinds())  # the load cases were not built
        self.assertEqual(self.ctx.store.events()[-1]["data"]["stop_code"], "user_stop")
        self.assertFalse((self.ctx.store.path / layout.STOP_REQUEST_FILE).exists())


if __name__ == "__main__":
    unittest.main()
