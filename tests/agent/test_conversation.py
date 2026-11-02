"""The Claude run's message loop with a stand-in for the SDK client (no API key, no CLI).

The first real run ended while its roles were still working: they ran in the
background, the lead ended its turn to wait, each of its stops was counted as
giving up, and when the count ran out the run finished and the roles were cut
off. These tests play that shape with a stand-in client that calls the run's
own hooks the way the CLI does.
"""

import asyncio
import os
import unittest
from unittest import mock

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

from hiraku.agent import runner
from hiraku.agent.hooks import Hooks
from hiraku.agent.runner import FOREGROUND_ROLES_ENV, WAKE_TEXT, build_options, run_with_claude
from hiraku.tools import Attribution
from hiraku.tools.functions import analyze, compare
from hiraku.tools.mcp import build_server
from tests.tools.helpers import RunCase


def result(cost):
    return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
                         session_id="s", total_cost_usd=cost)


class FakeClient:
    """Stands in for ClaudeSDKClient. `script` is an async generator function (client) -> messages;
    it may call the hooks the run registered, like the CLI does."""

    script = None

    def __init__(self, options):
        self.options = options
        self.queries: list[str] = []
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.stop_answers: list[dict] = []
        FakeClient.last = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def query(self, prompt):
        self.queries.append(prompt)
        await self.inbox.put(prompt)

    def hook(self, event):
        return self.options.hooks[event][0].hooks[0]

    async def stop(self):
        self.stop_answers.append(await self.hook("Stop")({"stop_hook_active": False}, None, None))

    def receive_messages(self):
        return type(self).script(self)


class ConversationCase(RunCase):
    def run_claude(self, script):
        client = type("Client", (FakeClient,), {"script": staticmethod(script)})
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "x"}), \
                mock.patch("claude_agent_sdk.ClaudeSDKClient", client), mock.patch.object(runner, "WAKE_GRACE_S", 0.01):
            status = asyncio.run(asyncio.wait_for(run_with_claude(self.ctx), timeout=10))
        return status, FakeClient.last

    def strength_work(self):
        """What cae.strength does with the stand-in tools: P1 matched, then R1 matched and judged."""
        ana, _, _ = self.run_p1()
        r1 = self.predict_r1({"value": ana["results"][1]["value"], "unit": "N"})
        ana2 = analyze(self.ctx, {"tool": "frame_static", "load_case_id": "R1-step100", "prediction_ids": [r1["prediction_id"]]})
        compare(self.ctx, {"prediction_id": r1["prediction_id"], "analysis_id": ana2["analysis_id"]})

    def kinds(self, *wanted):
        return [(e["kind"], e.get("agent")) for e in self.ctx.store.events() if e["kind"] in wanted]


class BackgroundRoleTest(ConversationCase, unittest.TestCase):
    def test_lead_ending_its_turn_does_not_cut_the_role_off(self):
        case = self

        async def script(client):
            await client.inbox.get()
            yield AssistantMessage(content=[TextBlock(text="強度役に渡します"),
                                            ToolUseBlock(id="tu1", name="Agent", input={"subagent_type": "cae-strength"})],
                                   model="m")
            await client.hook("SubagentStart")({"agent_id": "a1", "agent_type": "cae-strength"}, None, None)
            for _ in range(4):  # the lead ends its turn again and again while the role works (the first run: 4 times)
                yield AssistantMessage(content=[TextBlock(text="報告を待ちます")], model="m")
                await client.stop()
            yield result(0.10)
            case.strength_work()  # the role goes on in the background and finishes
            await client.hook("SubagentStop")({"agent_id": "a1", "agent_type": "cae-strength",
                                               "stop_hook_active": False}, None, None)
            await client.inbox.get()  # the next turn comes only when the run wakes the lead
            yield AssistantMessage(content=[TextBlock(text="判定がそろいました")], model="m")
            await client.stop()
            yield result(0.25)
            await asyncio.Event().wait()  # nothing more: reading on would hang (and time out)

        status, client = self.run_claude(script)
        self.assertEqual(status, "done")
        self.assertEqual(self.ctx.store.events()[-1]["data"]["stop_code"], "done")
        self.assertEqual(client.queries[1:], [WAKE_TEXT])
        self.assertEqual(client.stop_answers, [{}] * 5)  # never blocked, never counted
        self.assertEqual(self.kinds("hook.blocked_stop"), [])
        self.assertEqual(self.ctx.store.judgements()[0]["state"], "green")
        self.assertEqual(self.kinds("agent.started", "agent.stopped"),
                         [("agent.started", "lead"), ("agent.started", "cae.strength"),
                          ("agent.stopped", "cae.strength"), ("agent.stopped", "lead")])
        waited = [e["text"] for e in self.ctx.store.events() if e["kind"] == "agent.said" and e["agent"] == "system"]
        self.assertEqual(waited, ["まとめ役の番が終わりましたが、役がまだ動いているので待ちます: cae.strength"])
        self.assertEqual(self.ctx.project["cost_usd"], 0.25)
        self.assert_events_fit()

    def test_a_turn_the_cli_starts_itself_is_not_doubled(self):
        case = self

        async def script(client):
            await client.inbox.get()
            await client.hook("SubagentStart")({"agent_id": "a1", "agent_type": "cae-strength"}, None, None)
            yield AssistantMessage(content=[TextBlock(text="報告を待ちます")], model="m")
            yield result(0.10)
            case.strength_work()
            await client.hook("SubagentStop")({"agent_id": "a1", "agent_type": "cae-strength",
                                               "stop_hook_active": False}, None, None)
            # the CLI itself tells the lead the role finished, before the run's grace is over
            yield AssistantMessage(content=[TextBlock(text="報告が来ました")], model="m")
            yield result(0.20)
            await asyncio.Event().wait()

        with mock.patch.object(runner, "WAKE_GRACE_S", 0.2):
            status, client = self.run_claude(script)
        self.assertEqual(status, "done")
        self.assertEqual(len(client.queries), 1)


class ForegroundRoleTest(ConversationCase, unittest.TestCase):
    def test_turn_end_with_no_role_running_ends_the_run(self):
        case = self

        async def script(client):
            await client.inbox.get()
            # a foreground role: started and stopped inside the lead's turn
            await client.hook("SubagentStart")({"agent_id": "a1", "agent_type": "cae-strength"}, None, None)
            case.strength_work()
            await client.hook("SubagentStop")({"agent_id": "a1", "agent_type": "cae-strength",
                                               "stop_hook_active": False}, None, None)
            yield AssistantMessage(content=[TextBlock(text="判定がそろいました")], model="m")
            await client.stop()
            yield result(0.05)
            await asyncio.Event().wait()

        status, client = self.run_claude(script)
        self.assertEqual(status, "done")
        self.assertEqual(len(client.queries), 1)

    def test_a_lead_that_gives_up_is_still_counted(self):
        async def script(client):
            await client.inbox.get()
            for _ in range(4):  # no role at work: these stops are the lead giving up
                yield AssistantMessage(content=[TextBlock(text="終わります")], model="m")
                await client.stop()
            yield result(0.05)
            await asyncio.Event().wait()

        status, client = self.run_claude(script)
        self.assertEqual(status, "stopped")
        self.assertEqual([a.get("decision") for a in client.stop_answers], ["block", "block", "block", None])
        self.assertEqual(len(self.kinds("hook.blocked_stop")), 3)
        finished = self.ctx.store.events()[-1]["data"]
        self.assertEqual(finished["stop_code"], "not_judged")
        self.assertIn("止めるのを 3 回断ったあと、上限で終了", finished["stop_reason"])

    def test_roles_cut_off_with_the_run_are_stopped_too(self):
        async def script(client):
            await client.inbox.get()
            await client.hook("SubagentStart")({"agent_id": "a1", "agent_type": "cae-strength"}, None, None)
            yield AssistantMessage(content=[TextBlock(text="渡しました")], model="m")
            raise RuntimeError("the CLI went away")  # the stream ends with the role still marked running

        with self.assertRaises(RuntimeError):
            self.run_claude(script)
        self.assertEqual(self.kinds("agent.started", "agent.stopped")[-2:],
                         [("agent.stopped", "cae.strength"), ("agent.stopped", "lead")])
        finished = self.ctx.store.events()[-1]["data"]
        self.assertEqual((finished["status"], finished["stop_code"]), ("failed", "error"))
        self.assert_events_fit()


class OptionsTest(RunCase, unittest.TestCase):
    def test_roles_run_in_the_foreground_with_their_effort(self):
        att = Attribution()
        o = build_options(self.ctx, build_server(self.ctx, att), Hooks(self.ctx, att))
        self.assertEqual(o.env, FOREGROUND_ROLES_ENV)
        self.assertEqual(o.env, {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"})
        self.assertEqual(o.effort, "high")
        self.assertEqual({k: (a.background, a.effort) for k, a in o.agents.items()},
                         {"cae-strength": (False, "medium"), "designer": (False, "high")})


if __name__ == "__main__":
    unittest.main()
