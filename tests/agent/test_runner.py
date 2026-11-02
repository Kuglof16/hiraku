import asyncio
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

from hiraku.agent import prompts
from hiraku.agent.hooks import Hooks
from hiraku.agent.runner import MessageLog, MissingApiKey, build_options, final_status, require_api_key, run_with_claude
from hiraku.agent.session import STOPPED_BY_USER
from hiraku.tools import Attribution
from hiraku.tools.mcp import build_server
from tests.tools.helpers import RunCase


class ApiKeyTest(unittest.TestCase):
    def test_refuses_without_a_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(MissingApiKey) as cm:
                require_api_key()
        self.assertIn("--scripted", str(cm.exception))
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "x"}):
            require_api_key()


class OptionsTest(RunCase, unittest.TestCase):
    def test_options(self):
        att = Attribution()
        hooks = Hooks(self.ctx, att)
        o = build_options(self.ctx, build_server(self.ctx, att), hooks)
        self.assertEqual(o.tools, ["Agent", "Skill"])
        self.assertEqual(o.setting_sources, [])
        self.assertIn("mcp__hiraku__analyze", o.allowed_tools)
        self.assertIn("Agent", o.allowed_tools)
        self.assertIn("Skill", o.allowed_tools)
        self.assertIn("mcp__hiraku__revise_design", o.allowed_tools)
        self.assertIn("mcp__hiraku__make_tool", o.allowed_tools)
        self.assertIn("mcp__hiraku__write_report", o.allowed_tools)
        self.assertIn("mcp__hiraku__propose_design", o.allowed_tools)
        self.assertEqual(len(o.allowed_tools), 14)
        self.assertEqual(o.permission_mode, "dontAsk")
        self.assertTrue(o.strict_mcp_config)
        self.assertEqual(o.skills, ["hiraku:find-the-cause", "hiraku:predict-then-analyze"])
        self.assertEqual(o.agents["cae-strength"].skills, o.skills)
        self.assertEqual(len(o.plugins), 1)
        self.assertTrue(Path(o.plugins[0]["path"]).is_dir())
        self.assertEqual(o.model, "claude-opus-5-5")
        self.assertEqual(o.effort, "high")
        self.assertEqual(o.max_budget_usd, 2.0)  # cost_limit_usd of the requirements file
        self.assertEqual(set(o.agents), {"cae-strength", "designer"})
        self.assertIn("mcp__hiraku__record_prediction", o.agents["cae-strength"].tools)
        self.assertNotIn("mcp__hiraku__revise_design", o.agents["cae-strength"].tools)
        self.assertIn("mcp__hiraku__make_tool", o.agents["cae-strength"].tools)
        self.assertIn("mcp__hiraku__revise_design", o.agents["designer"].tools)
        self.assertNotIn("mcp__hiraku__make_tool", o.agents["designer"].tools)
        self.assertIn("PreToolUse", o.hooks)
        self.assertIn("hiraku", o.mcp_servers)
        self.assertTrue(o.forward_subagent_text)

    def test_lead_task_names_the_run(self):
        text = prompts.lead_task(self.ctx)
        self.assertIn("R1-step100", text)
        self.assertIn("強度信頼性", text)


class MessageLogTest(RunCase, unittest.TestCase):
    def test_said_events_are_attributed(self):
        log = MessageLog(self.ctx)
        log.handle(AssistantMessage(content=[TextBlock(text="分野を選びます"),
                                             ToolUseBlock(id="tu1", name="Agent", input={"subagent_type": "cae-strength"})],
                                    model="m"))
        log.handle(AssistantMessage(content=[TextBlock(text="  レールの応力を見積もります ")], model="m",
                                    parent_tool_use_id="tu1"))
        log.handle(AssistantMessage(content=[TextBlock(text="   ")], model="m"))
        said = [(e["agent"], e["text"]) for e in self.ctx.store.events()
                if e["kind"] == "agent.said" and e["agent"] != "system"]  # not the start's tests-left-out line
        self.assertEqual(said, [("lead", "分野を選びます"), ("cae.strength", "レールの応力を見積もります")])
        log.handle(ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=3,
                                 session_id="s", total_cost_usd=0.42))
        self.assertEqual(self.ctx.project["cost_usd"], 0.42)
        self.assertEqual(self.ctx.store.events()[-1]["kind"], "cost.updated")
        self.assert_events_fit()

    def test_final_status(self):
        ok = SimpleNamespace(subtype="success", is_error=False)
        status, reason, code = final_status(self.ctx, ok)  # R1 still pending
        self.assertEqual((status, code), ("stopped", "not_judged"))
        self.assertTrue(reason.startswith("まとめ役が、判定の付いていない分野を残して止まりました。"))
        self.assertIn("R1-step100", reason)
        self.assertEqual(final_status(self.ctx, SimpleNamespace(subtype="error_max_budget_usd", is_error=True))[::2],
                         ("stopped", "cost_limit"))
        self.assertEqual(final_status(self.ctx, SimpleNamespace(subtype="error_max_turns", is_error=True))[::2],
                         ("stopped", "turn_limit"))
        self.assertEqual(final_status(self.ctx, None), ("failed", "no result message from Claude", "error"))

    def test_failure_reason_is_the_error_text(self):
        # an invalid key: is_error with subtype "success"; the reason is only in the result text
        bad_key = ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=True, num_turns=1,
                                session_id="s", result="Invalid API key · Fix external API key")
        self.assertEqual(final_status(self.ctx, bad_key), ("failed", "Invalid API key · Fix external API key", "error"))
        with_status = ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=True, num_turns=1,
                                    session_id="s", result="Invalid API key", api_error_status=401)
        self.assertEqual(final_status(self.ctx, with_status)[1], "Invalid API key (API status 401)")
        errs = ResultMessage(subtype="error_during_execution", duration_ms=1, duration_api_ms=1, is_error=True,
                             num_turns=1, session_id="s", errors=["tool crashed", "gave up"])
        self.assertEqual(final_status(self.ctx, errs)[1], "tool crashed; gave up")
        # nothing in the message: the last thing an agent said
        bare = ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=True, num_turns=1,
                             session_id="s")
        self.ctx.store.emit("agent.said", agent="lead", text="Invalid API key · Fix external API key")
        self.assertEqual(final_status(self.ctx, bare)[1], "Invalid API key · Fix external API key")


def interrupted_client(exc):
    """A stand-in ClaudeSDKClient whose message stream is cut by exc (Ctrl+C)."""

    class Client:
        def __init__(self, options):
            self.options = options

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def query(self, prompt):
            pass

        async def receive_messages(self):
            yield AssistantMessage(content=[TextBlock(text="分野を選びます")], model="m")
            raise exc

    return Client


class InterruptTest(RunCase, unittest.TestCase):
    def assert_stopped_by_ctrl_c(self, exc):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "x"}), \
                mock.patch("claude_agent_sdk.ClaudeSDKClient", interrupted_client(exc())):
            with self.assertRaises(exc):
                asyncio.run(run_with_claude(self.ctx))
        self.assertEqual((self.ctx.project["status"], self.ctx.project["stop_reason"]), ("stopped", STOPPED_BY_USER))
        self.assertEqual(self.kinds()[-2:], ["agent.stopped", "run.finished"])
        self.assertEqual(self.ctx.store.events()[-1]["data"]["stop_code"], "user_stop")
        self.assert_events_fit()

    def test_cancelled(self):  # asyncio.run cancels the task on Ctrl+C
        self.assert_stopped_by_ctrl_c(asyncio.CancelledError)

    def test_keyboard_interrupt(self):
        self.assert_stopped_by_ctrl_c(KeyboardInterrupt)


if __name__ == "__main__":
    unittest.main()
