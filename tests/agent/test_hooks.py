import asyncio
import unittest

from hiraku.agent.hooks import Hooks
from hiraku.agent.roles import agent_id_from_sdk, sdk_name
from hiraku.tools import Attribution, rules
from hiraku.tools.functions import analyze, compare
from tests.tools.helpers import RunCase


def run(coro):
    return asyncio.run(coro)


class RolesTest(unittest.TestCase):
    def test_names(self):
        self.assertEqual(sdk_name("cae.strength"), "cae-strength")
        self.assertEqual(agent_id_from_sdk("cae-strength"), "cae.strength")
        self.assertEqual(agent_id_from_sdk("designer"), "designer")
        # the plugin's agents in Claude Code: "<plugin>:<name>"
        self.assertEqual(agent_id_from_sdk("hiraku:cae-strength"), "cae.strength")
        self.assertEqual(agent_id_from_sdk("hiraku:packager"), "packager")
        self.assertIsNone(agent_id_from_sdk("other:designer"))
        for other in (None, "", "general-purpose", "cae-assembly", "cae-made_up"):
            self.assertIsNone(agent_id_from_sdk(other))


class HooksTest(RunCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.attribution = Attribution()
        self.hooks = Hooks(self.ctx, self.attribution, max_stop_blocks=2)

    def pre(self, tool, args, agent_type=None):
        data = {"hook_event_name": "PreToolUse", "tool_name": f"mcp__hiraku__{tool}", "tool_input": args}
        if agent_type:
            data.update(agent_type=agent_type, agent_id="a1")
        return run(self.hooks.pre_tool_use(data, "tu1", None))

    def test_hk1_denial_is_recorded_and_explained(self):
        args = {"tool": "step_climb", "load_case_id": "P1-step100", "prediction_ids": []}
        out = self.pre("analyze", args, agent_type="cae-strength")
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["permissionDecision"], "deny")
        self.assertIn("record_prediction", hso["permissionDecisionReason"])
        ev = self.ctx.store.events()[-1]
        self.assertEqual(ev["kind"], "hook.denied")
        self.assertEqual(ev["agent"], "cae.strength")
        self.assertEqual(ev["data"]["rule"], rules.HK1)
        self.assertEqual(ev["data"]["tool"], "mcp__hiraku__analyze")
        self.assertEqual(ev["text"], hso["permissionDecisionReason"])
        self.assertIsNone(self.attribution.take("mcp__hiraku__analyze", args))
        self.assert_events_fit()

    def test_allowed_call_leaves_the_caller(self):
        load, force = self.predict_p1()
        args = {"tool": "step_climb", "load_case_id": "P1-step100", "prediction_ids": [force["prediction_id"]]}
        self.assertEqual(self.pre("analyze", args, agent_type="cae-strength"), {})
        self.assertEqual(self.attribution.take("mcp__hiraku__analyze", args), "cae.strength")
        self.assertEqual(self.pre("get_context", {}), {})
        self.assertEqual(self.attribution.take("mcp__hiraku__get_context", {}), "lead")

    def test_the_plugin_names_of_the_tools_are_hirakus(self):
        args = {"tool": "step_climb", "load_case_id": "P1-step100", "prediction_ids": []}
        data = {"tool_name": "mcp__plugin_hiraku_hiraku__analyze", "tool_input": args}
        out = run(self.hooks.pre_tool_use(data, "tu1", None))
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(self.ctx.store.events()[-1]["data"]["tool"], "mcp__hiraku__analyze")

    def test_other_tools_pass(self):
        out = run(self.hooks.pre_tool_use({"tool_name": "Agent", "tool_input": {}}, "tu", None))
        self.assertEqual(out, {})

    def test_post_tool_use_hints(self):
        load, force = self.predict_p1()
        args = {"tool": "step_climb", "load_case_id": "P1-step100", "prediction_ids": [force["prediction_id"]]}
        ana = analyze(self.ctx, args)
        out = run(self.hooks.post_tool_use({"tool_name": "mcp__hiraku__analyze", "tool_input": args}, "tu", None))
        self.assertIn(ana["analysis_id"], out["hookSpecificOutput"]["additionalContext"])
        self.tools["step_climb"].factor = 2.0
        ana2 = analyze(self.ctx, args)
        cargs = {"prediction_id": force["prediction_id"], "analysis_id": ana2["analysis_id"]}
        compare(self.ctx, cargs)
        out = run(self.hooks.post_tool_use({"tool_name": "mcp__hiraku__compare", "tool_input": cargs}, "tu", None))
        self.assertIn("record_cause", out["hookSpecificOutput"]["additionalContext"])

    def test_stop_is_blocked_while_pending_and_capped(self):
        out = run(self.hooks.stop({"stop_hook_active": False}, None, None))
        self.assertEqual(out["decision"], "block")
        self.assertIn("R1-step100", out["reason"])
        self.assertEqual(run(self.hooks.stop({"stop_hook_active": True}, None, None))["decision"], "block")
        self.assertEqual(run(self.hooks.stop({"stop_hook_active": True}, None, None)), {})  # cap reached
        blocked = [e for e in self.ctx.store.events() if e["kind"] == "hook.blocked_stop"]
        self.assertEqual([e["data"]["count"] for e in blocked], [1, 2])
        self.assertEqual(blocked[0]["data"]["rule"], rules.HK4)
        self.assert_events_fit()

    def test_stop_passes_when_judged(self):
        ana, _, _ = self.run_p1()
        r1 = self.predict_r1({"value": ana["results"][1]["value"], "unit": "N"})
        a2 = analyze(self.ctx, {"tool": "frame_static", "load_case_id": "R1-step100", "prediction_ids": [r1["prediction_id"]]})
        compare(self.ctx, {"prediction_id": r1["prediction_id"], "analysis_id": a2["analysis_id"]})
        self.assertEqual(run(self.hooks.stop({"stop_hook_active": False}, None, None)), {})

    def test_subagent_events(self):
        run(self.hooks.subagent_start({"agent_id": "a1", "agent_type": "cae-strength"}, None, None))
        run(self.hooks.subagent_start({"agent_id": "a2", "agent_type": "general-purpose"}, None, None))
        self.assertEqual(self.hooks.caller({"agent_id": "a1"}), "cae.strength")
        # strength is still pending: the first stops are blocked, then it may stop
        out = run(self.hooks.subagent_stop({"agent_id": "a1", "agent_type": "cae-strength", "stop_hook_active": False}, None, None))
        self.assertEqual(out["decision"], "block")
        run(self.hooks.subagent_stop({"agent_id": "a1", "agent_type": "cae-strength", "stop_hook_active": True}, None, None))
        out = run(self.hooks.subagent_stop({"agent_id": "a1", "agent_type": "cae-strength", "stop_hook_active": True}, None, None))
        self.assertEqual(out, {})
        kinds = [(e["kind"], e.get("agent")) for e in self.ctx.store.events()
                 if e["kind"] in ("agent.started", "agent.stopped")]
        self.assertEqual(kinds, [("agent.started", "cae.strength"), ("agent.stopped", "cae.strength")])
        self.assert_events_fit()

    def test_matchers(self):
        m = self.hooks.matchers()
        self.assertEqual(set(m), {"PreToolUse", "PostToolUse", "Stop", "SubagentStart", "SubagentStop"})


if __name__ == "__main__":
    unittest.main()
