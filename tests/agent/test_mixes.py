"""Model mixes: the model and effort of each role in the SDK options, and what a run records of them."""

import asyncio
import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from hiraku.__main__ import main
from hiraku.agent import roles, runner
from hiraku.agent.hooks import Hooks
from hiraku.agent.roles import CAE_EFFORT, DESIGNER_EFFORT, subagents
from hiraku.agent.runner import DEFAULT_EFFORT, DEFAULT_MODEL, MessageLog, build_options, role_models, run_with_claude
from hiraku.agent.scripted import run_scripted
from hiraku.catalog.mixes import ROLES, UnknownMix, get_mix, load_mixes, mix_names, role_key
from hiraku.tools import Attribution
from hiraku.tools.mcp import build_server
from tests.agent.test_conversation import FakeClient
from tests.tools.helpers import EXAMPLE_DESIGN, REQUIREMENTS, RunCase

MIXES = ("default", "cae-sonnet", "cae-haiku", "lead-sonnet")


class MixTableTest(unittest.TestCase):
    def test_the_mixes(self):
        self.assertEqual(mix_names(), list(MIXES))
        for name, mix in load_mixes().items():
            self.assertEqual(set(mix), set(ROLES), name)

    def test_default_is_the_behaviour_before_mixes(self):
        mix = get_mix("default")
        self.assertEqual(mix["lead"], {"model": DEFAULT_MODEL, "effort": DEFAULT_EFFORT})
        self.assertEqual(mix["designer"], {"model": DEFAULT_MODEL, "effort": DESIGNER_EFFORT})
        self.assertEqual(mix["cae"], {"model": DEFAULT_MODEL, "effort": CAE_EFFORT})
        self.assertEqual(mix["packager"], {"model": DEFAULT_MODEL, "effort": CAE_EFFORT})

    def test_the_other_mixes_change_only_models(self):
        default = get_mix("default")
        want = {"cae-sonnet": {"cae": "claude-sonnet-5-5", "packager": "claude-sonnet-5-5"},
                "cae-haiku": {"cae": "claude-haiku-5-5", "packager": "claude-haiku-5-5"},
                "lead-sonnet": {r: "claude-sonnet-5-5" for r in ROLES}}
        for name, changed in want.items():
            mix = get_mix(name)
            for role in ROLES:
                self.assertEqual(mix[role]["effort"], default[role]["effort"], (name, role))
                self.assertEqual(mix[role]["model"], changed.get(role, DEFAULT_MODEL), (name, role))

    def test_unknown_mix(self):
        with self.assertRaises(UnknownMix) as cm:
            get_mix("cheap")
        for name in MIXES:
            self.assertIn(name, str(cm.exception))

    def test_role_key(self):
        self.assertEqual(role_key("cae.strength"), "cae")
        self.assertEqual(role_key("packager"), "packager")
        self.assertEqual(role_key("designer"), "designer")
        with self.assertRaises(ValueError):
            role_key("system")


ORIGINAL_ROLES = roles.possible_roles  # the stand-in run has no packager; a test adds one


class OptionsPerRoleTest(RunCase, unittest.TestCase):
    def options(self, **kw):
        att = Attribution()
        return build_options(self.ctx, build_server(self.ctx, att), Hooks(self.ctx, att), **kw)

    def test_without_a_mix_as_before(self):
        o = self.options()
        self.assertEqual((o.model, o.effort), (DEFAULT_MODEL, DEFAULT_EFFORT))
        self.assertEqual((o.agents["cae-strength"].model, o.agents["cae-strength"].effort), ("inherit", CAE_EFFORT))
        self.assertEqual((o.agents["designer"].model, o.agents["designer"].effort), ("inherit", DESIGNER_EFFORT))
        o = self.options(model="claude-sonnet-5-5", effort="low")
        self.assertEqual((o.model, o.effort), ("claude-sonnet-5-5", "low"))
        self.assertEqual(o.agents["cae-strength"].model, "inherit")
        self.assertEqual(role_models(o)["cae.strength"], {"model": "claude-sonnet-5-5", "effort": CAE_EFFORT})

    def test_each_mix(self):
        for name in MIXES:
            mix = get_mix(name)
            with self.subTest(mix=name):
                o = self.options(mix=mix)
                self.assertEqual((o.model, o.effort), (mix["lead"]["model"], mix["lead"]["effort"]))
                for sdk, role in (("cae-strength", "cae"), ("designer", "designer")):
                    self.assertEqual((o.agents[sdk].model, o.agents[sdk].effort),
                                     (mix[role]["model"], mix[role]["effort"]))
                self.assertEqual(role_models(o), {
                    "lead": mix["lead"], "cae.strength": mix["cae"], "designer": mix["designer"]})

    def test_model_and_effort_given_set_the_lead_of_a_mix(self):
        o = self.options(mix=get_mix("cae-haiku"), model="claude-sonnet-5-5", effort="max")
        self.assertEqual((o.model, o.effort), ("claude-sonnet-5-5", "max"))
        self.assertEqual(o.agents["cae-strength"].model, "claude-haiku-5-5")

    def test_subagents_with_the_packager(self):
        add = lambda ctx: [*ORIGINAL_ROLES(ctx), {"agent_id": "packager", "role": "packager"}]  # noqa: E731
        with mock.patch.object(roles, "possible_roles", add):
            for name in MIXES:
                mix = get_mix(name)
                agents = subagents(self.ctx, mix)
                self.assertEqual((agents["packager"].model, agents["packager"].effort),
                                 (mix["packager"]["model"], mix["packager"]["effort"]), name)
                self.assertIn("mcp__hiraku__rule_check", agents["packager"].tools)
            agents = subagents(self.ctx)
            self.assertEqual((agents["packager"].model, agents["packager"].effort), ("inherit", CAE_EFFORT))


class RecordedModelsTest(RunCase, unittest.TestCase):
    def test_cost_updated_carries_the_usage(self):
        usage = {"input_tokens": 10, "output_tokens": 20}
        per_model = {"claude-opus-5-5": {"inputTokens": 10, "outputTokens": 20, "cacheReadInputTokens": 5,
                                         "cacheCreationInputTokens": 1, "webSearchRequests": 0, "costUSD": 0.5,
                                         "contextWindow": 200000, "maxOutputTokens": 32000}}
        MessageLog(self.ctx).handle(ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                                                  num_turns=7, session_id="s", total_cost_usd=0.5, usage=usage,
                                                  model_usage=per_model))
        e = [e for e in self.ctx.store.events() if e["kind"] == "cost.updated"][-1]
        self.assertEqual(e["data"], {"cost_usd": 0.5, "usage": usage, "model_usage": per_model, "num_turns": 7})
        self.assertEqual(self.ctx.project["cost_usd"], 0.5)
        self.assert_events_fit()

    def test_a_claude_run_records_each_roles_model(self):
        async def script(client):
            await client.inbox.get()
            await client.hook("SubagentStart")({"agent_id": "a1", "agent_type": "cae-strength"}, None, None)
            for _ in range(3):  # HK4 blocks the role's stop twice (load cases left), then lets it stop
                await client.hook("SubagentStop")({"agent_id": "a1", "agent_type": "cae-strength",
                                                   "stop_hook_active": False}, None, None)
            yield AssistantMessage(content=[TextBlock(text="終わります")], model="m")
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=3,
                                session_id="s", total_cost_usd=0.01)
            await asyncio.Event().wait()

        client = type("Client", (FakeClient,), {"script": staticmethod(script)})
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "x"}), mock.patch("claude_agent_sdk.ClaudeSDKClient", client), \
                mock.patch.object(runner, "WAKE_GRACE_S", 0.01), mock.patch.object(runner, "STOP_POLL_S", 0.01):
            asyncio.run(asyncio.wait_for(run_with_claude(self.ctx, mix="cae-haiku"), timeout=10))
        started = {e["agent"]: e["data"] for e in self.ctx.store.events() if e["kind"] == "agent.started"}
        self.assertEqual(started["lead"], {"agent_id": "lead", "model": "claude-opus-5-5", "effort": "high", "mix": "cae-haiku"})
        self.assertEqual(started["cae.strength"], {"agent_id": "cae.strength", "model": "claude-haiku-5-5", "effort": "medium"})
        self.assertEqual(FakeClient.last.options.agents["cae-strength"].model, "claude-haiku-5-5")
        self.assert_events_fit()

    def test_a_scripted_run_records_no_model(self):
        run_scripted(self.ctx)
        started = [e for e in self.ctx.store.events() if e["kind"] == "agent.started"]
        self.assertTrue(started)
        for e in started:
            self.assertIsNone(e["data"]["model"], e["agent"])
            self.assertIn("model", e["data"])


class MixFlagTest(unittest.TestCase):
    def test_an_unknown_mix_is_refused_before_the_run_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            err = io.StringIO()
            with redirect_stderr(err):
                code = main(["run", str(REQUIREMENTS), "--scripted", "--design", str(EXAMPLE_DESIGN), "--runs", tmp,
                             "--mix", "cheap"])
            self.assertEqual(code, 2)
            for name in MIXES:
                self.assertIn(name, err.getvalue())
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_the_mix_goes_to_the_run(self):
        seen = {}

        async def fake_run(ctx, **kw):
            seen.update(kw)
            ctx.store.finish("done", stop_code="done")
            return "done"

        fake_ctx = mock.MagicMock()
        fake_ctx.store.project = {"status": "done", "run_id": "r", "cost_usd": 0.0}
        fake_ctx.store.events.return_value = []
        fake_ctx.store.judgements.return_value = []
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "x"}), \
                mock.patch("hiraku.agent.session.start_run", return_value=fake_ctx), \
                mock.patch("hiraku.agent.runner.run_with_claude", fake_run), redirect_stderr(io.StringIO()), \
                mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(main(["run", str(REQUIREMENTS), "--mix", "cae-sonnet"]), 0)
            self.assertEqual((seen["mix"], seen["model"], seen["effort"]), ("cae-sonnet", None, None))
            main(["run", str(REQUIREMENTS)])
            self.assertEqual(seen["mix"], "default")


if __name__ == "__main__":
    unittest.main()
