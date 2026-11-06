import asyncio
import json
import unittest

from hiraku import contract
from hiraku.tools import Attribution
from hiraku.tools.mcp import HANDLERS, INPUT_SCHEMAS, build_server, make_handler
from hiraku.tools.functions import FUNCTIONS, RUN_TOOLS
from tests.tools.helpers import RunCase


def has_ref(node):
    if isinstance(node, dict):
        return "$ref" in node or any(has_ref(v) for v in node.values())
    if isinstance(node, list):
        return any(has_ref(v) for v in node)
    return False


class SchemaTest(unittest.TestCase):
    def test_every_run_tool_has_a_standalone_schema(self):
        # the CAD tools (hiraku/tools/cad_tools.py) are registered but not yet in a run (MCP_TOOLS_LATER)
        # and so is the flow tool (hiraku/tools/cfd_tools.py): not in the production run yet
        run = set(INPUT_SCHEMAS) - {"export_step", "import_step", "edit_step", "cfd_aero"}
        self.assertEqual(run, {*contract.MCP_TOOLS_MVP, "revise_design", "build_version", "propose_design",
                               "write_design", "edit_design", "check_design", "make_tool", "check_tool", "get_tool_template", "write_report",
                               "pick_domains", "ask_user", "import_knowledge", "rule_check", "find_tradeoffs",
                               "add_tests", "write_load_case"})
        self.assertEqual(run, set(RUN_TOOLS))
        for name, schema in INPUT_SCHEMAS.items():
            with self.subTest(name=name):
                self.assertFalse(has_ref(schema))
                self.assertEqual(schema["type"], "object")
        units = INPUT_SCHEMAS["record_prediction"]["properties"]["inputs"]["additionalProperties"]["properties"]["unit"]
        self.assertIn("MPa", units["enum"])

    def test_the_tools_register_find_tradeoffs_collector(self):
        """hiraku.intake cannot import hiraku.tools: importing the MCP tools registers the collector it calls."""
        from hiraku.intake import tradeoff
        from hiraku.tools import tradeoff_data

        self.assertIs(tradeoff.collector, tradeoff_data.collect)


class HandlerTest(RunCase, unittest.TestCase):
    def call(self, name, args):
        h = make_handler(self.ctx, self.attribution, name, FUNCTIONS[name])
        return asyncio.run(h(args))

    def setUp(self):
        super().setUp()
        self.attribution = Attribution()

    def test_an_exception_in_a_tool_is_logged_and_answered(self):
        from hiraku.tools.mcp import TOOL_ERRORS_FILE

        def broken(ctx, args, agent):
            raise KeyError("verdict_id")

        out = asyncio.run(make_handler(self.ctx, self.attribution, "compare", broken)({"prediction_id": "pred-0001"}))
        self.assertTrue(out["is_error"])
        self.assertIn("KeyError", out["content"][0]["text"])
        self.assertIn("もう一度", out["content"][0]["text"])
        log = (self.ctx.store.path / TOOL_ERRORS_FILE).read_text(encoding="utf-8")
        self.assertIn("Traceback", log)
        self.assertIn("compare", log)
        said = [e for e in self.ctx.store.events() if e["kind"] == "agent.said" and (e.get("data") or {}).get("tool_error")]
        self.assertEqual(said[-1]["data"]["tool_error"], "compare")
        self.assert_events_fit()

    def test_server_builds(self):
        cfg = build_server(self.ctx, self.attribution)
        self.assertEqual(cfg["name"], "hiraku")

    def test_result_is_the_record_as_json(self):
        out = self.call("list_tools", {})
        self.assertNotIn("is_error", out)
        names = [t["name"] for t in json.loads(out["content"][0]["text"])]
        self.assertEqual(sorted(names), ["frame_static", "step_climb"])

    def test_tool_error_comes_back_as_is_error(self):
        out = self.call("analyze", {"tool": "step_climb", "load_case_id": "P1-step100", "prediction_ids": ["pred-0001"]})
        self.assertTrue(out["is_error"])
        self.assertIn("record_prediction", out["content"][0]["text"])

    def test_analyze_answers_the_agent_with_the_results_only(self):
        load, force = self.predict_p1()
        out = self.call("analyze", {"tool": "step_climb", "load_case_id": "P1-step100",
                                    "prediction_ids": [load["prediction_id"], force["prediction_id"]]})
        got = json.loads(out["content"][0]["text"])
        self.assertEqual({r["quantity"] for r in got["results"]} >= {"front_wheel_load", "front_wheel_force"}, True)
        self.assertNotIn("vectors", got)
        stored = self.ctx.store.get("analysis", got["analysis_id"])
        self.assertIn("vectors", stored)  # the run keeps the whole record for the screen

    def test_rule_check_writes_its_records_through_the_store(self):
        h = make_handler(self.ctx, self.attribution, "rule_check", HANDLERS["rule_check"])
        out = asyncio.run(h({}))
        self.assertNotIn("is_error", out)
        self.assertTrue(json.loads(out["content"][0]["text"])["recorded"])
        checks = self.ctx.store.records("rule_check", 1)
        self.assertEqual(checks[0]["rule_check_id"], "rchk-0001")
        self.assertEqual(self.kinds().count("rule_check.recorded"), len(checks))
        self.assert_events_fit()

    def test_an_intake_error_comes_back_as_is_error(self):
        h = make_handler(self.ctx, self.attribution, "rule_check", HANDLERS["rule_check"])
        out = asyncio.run(h({"disable": ["no-such-rule"]}))
        self.assertTrue(out["is_error"])
        self.assertIn("no-such-rule", out["content"][0]["text"])

    def test_revise_design_is_the_designers_only(self):
        args = {"changes": {"track": {"value": 1150.0, "unit": "mm"}}, "reason": "x"}
        self.attribution.push("mcp__hiraku__revise_design", json.loads(json.dumps(args)), "cae.strength")
        out = self.call("revise_design", args)
        self.assertTrue(out["is_error"])
        self.assertIn("設計役", out["content"][0]["text"])

    def test_a_role_limited_tool_without_a_hook_record_is_refused(self):
        for name, args in (("revise_design", {"changes": {"track": {"value": 1150.0, "unit": "mm"}}, "reason": "x"}),
                           ("propose_design", {"archetype": "ladder_4w", "reason": "x"}),
                           ("write_report", {"summary": "x", "next": []})):
            with self.subTest(name=name):
                out = self.call(name, args)  # nothing pushed: the PreToolUse hook did not see the call
                self.assertTrue(out["is_error"])
                self.assertIn("どの役が呼んだのかを確かめられなかった", out["content"][0]["text"])
                self.assertNotIn("hooks", out["content"][0]["text"])  # it is not the person's hooks setup
        self.assertEqual(self.ctx.version, 1)
        self.assertNotIn("report.written", self.kinds())

    def test_attribution_reaches_the_record(self):
        args = {"load_case_id": "P1-step100", "quantity": "front_wheel_force", "formula_id": "step_climb.wheel_force_rigid",
                "output": "F", "inputs": {"r": {"value": 300.0, "unit": "mm"}, "h": {"value": 100.0, "unit": "mm"},
                                          "W": {"value": 1000.0, "unit": "N"}}, "assumptions": []}
        self.attribution.push("mcp__hiraku__record_prediction", json.loads(json.dumps(args)), "cae.powertrain")
        out = self.call("record_prediction", args)
        self.assertEqual(json.loads(out["content"][0]["text"])["agent"], "cae.powertrain")
        out = self.call("record_prediction", args)  # nothing pushed: the load case owner
        self.assertEqual(json.loads(out["content"][0]["text"])["agent"], "cae.strength")


if __name__ == "__main__":
    unittest.main()
