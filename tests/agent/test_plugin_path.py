"""The Claude Code plugin path (hiraku.agent.plugin): the MCP server over stdio is the run process,
the hook commands answer as hiraku.agent.hooks does and leave their events to the server."""

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import yaml

from hiraku import contract
from hiraku.contract import layout
from hiraku.agent import plugin, plugin_files
from hiraku.agent.hooks import Hooks
from hiraku.agent.roles import PLUGIN_DIR
from hiraku.catalog import REPO_ROOT
from hiraku.tools import Attribution, RunContext, rules
from hiraku.tools.context import ToolError
from hiraku.tools.functions import RUN_TOOLS, analyze, compare, record_prediction
from tests.tools import fakes
from tests.tools.helpers import requirements_with_tests

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
TOOL = plugin_files.plugin_tool


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


class PluginFilesTest(unittest.TestCase):
    def test_agents_and_command_are_what_the_script_writes_now(self):
        for rel, want in plugin_files.files().items():
            with self.subTest(file=rel):
                self.assertEqual(text(PLUGIN_DIR / rel), want, "run python -m hiraku.agent.plugin_files")
        on_disk = {f"agents/{p.name}" for p in (PLUGIN_DIR / "agents").glob("*.md")}
        self.assertEqual(on_disk, {r for r in plugin_files.files() if r.startswith("agents/")})

    def test_agents_load_and_name_only_tools_and_skills_that_exist(self):
        skills = {f"hiraku:{p.parent.name}" for p in (PLUGIN_DIR / "skills").glob("*/SKILL.md")}
        names = set()
        for path in (PLUGIN_DIR / "agents").glob("*.md"):
            with self.subTest(agent=path.name):
                meta = yaml.safe_load(FRONTMATTER.match(text(path)).group(1))
                self.assertEqual(meta["name"], path.stem)
                self.assertTrue(meta["description"])
                for t in meta["tools"].split(", "):
                    self.assertIn(t.removeprefix(TOOL("")), RUN_TOOLS)
                self.assertTrue(set(meta.get("skills", [])) <= skills)
                names.add(meta["name"])
        self.assertIn("designer", names)
        self.assertIn("packager", names)
        self.assertIn("cae-strength", names)
        self.assertNotIn("cae-assembly", names)

    def test_the_run_command_starts_a_run_and_carries_the_lead_prompt(self):
        body = text(PLUGIN_DIR / "commands" / "run.md")
        meta = yaml.safe_load(FRONTMATTER.match(body).group(1))
        self.assertTrue(meta["description"])
        self.assertIn(TOOL("start_run"), body)
        self.assertIn("$ARGUMENTS", body)
        self.assertIn(plugin_files.prompts.LEAD_PROMPT.strip(), body)
        for t in meta["allowed-tools"].split(", "):
            self.assertIn(t.removeprefix(TOOL("")), set(RUN_TOOLS) | set(plugin.OWN_TOOLS))

    def test_manifest_and_hooks_refer_to_files_that_exist(self):
        manifest = json.loads(text(PLUGIN_DIR / ".claude-plugin" / "plugin.json"))
        server = manifest["mcpServers"][contract.MCP_SERVER]
        hooks = json.loads(text(PLUGIN_DIR / "hooks" / "hooks.json"))["hooks"]
        self.assertEqual(set(hooks), set(plugin.HOOK_EVENTS))
        commands = [server] + [h for event in hooks.values() for m in event for h in m["hooks"]]
        for c in commands:
            with self.subTest(command=c["args"]):
                self.assertEqual(c["command"], "python")
                script = c["args"][0].replace("${CLAUDE_PLUGIN_ROOT}", str(PLUGIN_DIR))
                self.assertTrue(Path(script).is_file(), script)
        self.assertEqual(server["args"][1:], ["mcp"])
        for event, matchers in hooks.items():
            for m in matchers:
                self.assertEqual(m["hooks"][0]["args"][1:], ["hook", event])
                if event in ("PreToolUse", "PostToolUse"):  # the plugin's own tools only
                    self.assertTrue(re.search(m["matcher"], TOOL("analyze")))
                    self.assertFalse(re.search(m["matcher"], "Bash"))


class StdioTest(unittest.TestCase):
    def test_initialize_and_tools_list_over_stdio(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = subprocess.Popen([sys.executable, "-m", "hiraku", "mcp", "--runs", tmp], cwd=REPO_ROOT,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                def ask(msg):
                    p.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
                    p.stdin.flush()
                    if "id" in msg:
                        return json.loads(p.stdout.readline())

                init = ask({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}})
                self.assertEqual(init["result"]["serverInfo"]["name"], contract.MCP_SERVER)
                self.assertIn("tools", init["result"]["capabilities"])
                ask({"jsonrpc": "2.0", "method": "notifications/initialized"})
                tools = ask({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
                self.assertEqual({t["name"] for t in tools}, {"start_run", "open_run", *RUN_TOOLS})
                from hiraku.tools.mcp import DESCRIPTIONS, INPUT_SCHEMAS

                for t in tools:
                    if t["name"] in RUN_TOOLS:  # the SDK run's tables, as they are
                        self.assertEqual(t["description"], DESCRIPTIONS[t["name"]])
                        self.assertEqual(t["inputSchema"], INPUT_SCHEMAS[t["name"]])
                call = ask({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                            "params": {"name": "get_context", "arguments": {}}})
                self.assertTrue(call["result"]["isError"])  # no run yet
                self.assertIn("start_run", call["result"]["content"][0]["text"])
            finally:
                p.stdin.close()
                p.wait(60)
                p.stdout.close()
                p.stderr.close()
            self.assertEqual(p.returncode, 0)


class PluginRunCase:
    """A run started through the plugin's server (its tool start_run), with the test fakes."""

    step_factor = 1.03

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.runs = self.root / "runs"
        design = self.root / "design.json"
        design.write_text(json.dumps(fakes.loaded_design()), encoding="utf-8")
        self.tools = fakes.shelf(self.step_factor)
        self.server = plugin.PluginServer(self.runs, load=lambda: (fakes.FakeFormulas(), self.tools))
        self.started = self.call("start_run", {"requirements_file": str(requirements_with_tests(self.root, ["P1", "R1"])),
                                              "design_file": str(design)})
        self.ctx = self.server.ctx

    def tearDown(self):
        self._tmp.cleanup()

    def call(self, name, args):
        out = asyncio.run(self.server.call_tool(name, args))
        body = out["content"][0]["text"]
        if out.get("is_error"):
            raise ToolError(body)
        return json.loads(body)

    def hook(self, event, data):
        """The hook command as Claude Code runs it: a process, input on stdin, the answer on stdout."""
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        p = subprocess.run([sys.executable, "-m", "hiraku", "hook", event, "--runs", str(self.runs)], cwd=REPO_ROOT,
                           input=json.dumps({"hook_event_name": event, **data}).encode("utf-8"),
                           capture_output=True, timeout=120, env=env)
        self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace"))
        self.assertEqual(p.stderr, b"", p.stderr.decode("utf-8", "replace"))
        return json.loads(p.stdout)

    def sdk_hooks(self):
        """hiraku.agent.hooks on the same run, as the SDK run has it (its events kept apart)."""
        kept = []
        h = Hooks(RunContext(store=self.ctx.store, formulas=None, tools={}, specs=dict(self.ctx.specs)), Attribution(),
                  emit=lambda kind, **kw: kept.append(kind))
        h.load_state(plugin.read_state(self.ctx.store.path))
        return h, kept

    def events(self, kind):
        return [e for e in self.ctx.store.events() if e["kind"] == kind]

    def assert_events_fit(self):
        ev = self.ctx.store.events()
        self.assertEqual([e["seq"] for e in ev], list(range(len(ev))))
        for e in ev:
            self.assertEqual(contract.errors("event", e), [], e["kind"])

    def predict(self, lc="P1-step100"):
        from hiraku.tools.summary import design_summary

        s = design_summary(self.ctx.store.design())
        load = s["loadings"]["two_cargo"]
        return record_prediction(self.ctx, {
            "load_case_id": lc, "quantity": "front_wheel_force", "formula_id": "step_climb.pushed_wheel_load",
            "output": "F", "assumptions": ["test"],
            "inputs": {"m": load["mass"], "a": load["cg_from_front_axle"], "L": s["wheelbase"], "n": s["front_wheels"],
                       "r": s["front_wheel_radius"], "h": {"value": 100.0, "unit": "mm"}}}, "cae.strength")


class ServerTest(PluginRunCase, unittest.TestCase):
    def test_start_run_makes_a_plugin_run_and_names_it_current(self):
        self.assertEqual(self.ctx.project["driver"], plugin.DRIVER)
        self.assertEqual(self.started["driver"], "claude_code_plugin")
        self.assertIn("get_context", self.started["task"])
        self.assertEqual(plugin.current_run(self.runs), self.ctx.store.path.resolve())
        kinds = [e["kind"] for e in self.ctx.store.events()]
        self.assertEqual(kinds[0], "run.started")
        self.assertIn("domains.picked", kinds)  # the intake, as the CLI does it
        self.assertIn("tool.checked", kinds)
        self.assertEqual(self.events("agent.started")[-1]["agent"], "lead")
        self.assertEqual(self.call("get_context", {})["run_id"], self.ctx.project["run_id"])
        self.assert_events_fit()

    def test_the_screen_lists_the_run(self):
        from hiraku.webserve import list_runs

        runs = {r["run_id"]: r for r in list_runs(self.runs)}
        self.assertIn(self.ctx.project["run_id"], runs)

    def test_the_tools_gate_records_a_denial_once(self):
        args = {"tool": "step_climb", "load_case_id": "P1-step100", "prediction_ids": ["pred-0099"]}
        with self.assertRaises(ToolError):
            self.call("analyze", args)
        (denied,) = self.events("hook.denied")
        self.assertEqual(denied["data"]["rule"], rules.HK1)
        self.assertEqual(denied["data"]["tool"], contract.mcp_name("analyze"))
        self.assertEqual(denied["agent"], "lead")
        self.assert_events_fit()

    def test_open_run_takes_up_a_running_run(self):
        run_id = self.ctx.project["run_id"]
        other = plugin.PluginServer(self.runs, load=lambda: (fakes.FakeFormulas(), self.tools))
        got = asyncio.run(other.call_tool("open_run", {"run_id": run_id}))
        self.assertFalse(got.get("is_error"), got)
        self.assertEqual(other.ctx.specs.keys(), self.ctx.specs.keys())
        self.server.close("stopped", "test", "user_stop")
        got = asyncio.run(other.call_tool("open_run", {"run_id": run_id}))
        self.assertTrue(got["is_error"])

    def test_the_alive_file_is_touched_while_the_server_serves_the_run(self):
        """layout.PLUGIN_ALIVE_FILE: touched at start and on every poll; gone once the server lets go."""
        import os

        alive = self.ctx.store.path / layout.PLUGIN_ALIVE_FILE
        self.assertTrue(alive.is_file())
        os.utime(alive, (0, 0))  # as if the last touch were long ago
        self.server.poll_once()
        self.assertLess(time.time() - alive.stat().st_mtime, layout.PLUGIN_ALIVE_S)
        self.server.shutdown()
        self.assertFalse(alive.exists())

    def test_the_stop_button_ends_the_run(self):
        (self.ctx.store.path / "stop.request").write_text("", encoding="utf-8")
        self.server.poll_once()
        self.assertFalse((self.ctx.store.path / layout.PLUGIN_ALIVE_FILE).exists())  # the server let go
        self.assertEqual(self.ctx.project["status"], "stopped")
        self.assertEqual(self.events("run.finished")[-1]["data"]["stop_code"], "user_stop")
        self.assertTrue(self.call_error("get_context"))
        self.assertEqual(self.hook("Stop", {"stop_hook_active": False}), {})  # the hooks let everything through

    def call_error(self, name):
        return asyncio.run(self.server.call_tool(name, {})).get("is_error")

    def test_a_message_from_the_screen_reaches_the_lead_through_the_hooks(self):
        folder = self.ctx.store.path / "inbox"
        folder.mkdir()
        (folder / "1-message.json").write_text(json.dumps({"kind": "message", "text": "急いで"}), encoding="utf-8")
        self.server.poll_once()
        self.assertEqual([e["text"] for e in self.events("user.said")], ["急いで"])
        out = self.hook("Stop", {"stop_hook_active": False})
        self.assertEqual(out["decision"], "block")
        self.assertIn("急いで", out["reason"])

    def test_a_message_tells_a_role_at_work_to_return_and_stays_for_the_lead(self):
        from hiraku.agent.hooks import ROLE_RETURN_FOR_MESSAGE

        self.hook("SubagentStart", {"agent_id": "a1", "agent_type": "hiraku:cae-strength"})
        folder = self.ctx.store.path / "inbox"
        folder.mkdir()
        (folder / "1-message.json").write_text(json.dumps({"kind": "message", "text": "R1 を先に"}), encoding="utf-8")
        self.server.poll_once()
        role = {"tool_name": TOOL("get_context"), "tool_input": {}, "agent_type": "hiraku:cae-strength", "agent_id": "a1"}
        out = self.hook("PostToolUse", role)
        self.assertEqual(out["hookSpecificOutput"]["additionalContext"], ROLE_RETURN_FOR_MESSAGE)
        self.assertEqual(self.hook("PostToolUse", role), {})  # told once
        out = self.hook("SubagentStop", {"agent_id": "a1", "agent_type": "hiraku:cae-strength", "stop_hook_active": False})
        self.assertEqual(out, {})  # R1 is pending, but the role returns for the message (not held)
        lead = self.hook("PostToolUse", {"tool_name": "Agent", "tool_input": {}})  # the lead's Agent call has ended
        self.assertIn("R1 を先に", lead["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(plugin.read_state(self.ctx.store.path)["for_lead"], [])
        self.server.drain()
        self.assertEqual(self.events("hook.blocked_stop"), [])

    def test_the_server_leaves_the_unpredictable_cases_for_the_hook_command(self):
        lc = dict(self.ctx.load_case("R1-step100"), load_case_id="R4-rops", test_id="R4",
                  criterion=dict(self.ctx.load_case("R1-step100")["criterion"], quantity="rops_occupant_clearance"))
        self.ctx.load_cases.append(lc)
        self.call("get_context", {"sections": ["load_cases"]})  # after a tool call the server works it out
        state = plugin.read_state(self.ctx.store.path)
        self.assertEqual(state[plugin.UNPREDICTABLE_KEY], {"R4-rops": "rops_occupant_clearance"})
        self.hook("SubagentStart", {"agent_id": "a1", "agent_type": "hiraku:cae-strength"})
        self.assertEqual(plugin.read_state(self.ctx.store.path)[plugin.UNPREDICTABLE_KEY],
                         {"R4-rops": "rops_occupant_clearance"})  # the hook command keeps it


class HookCommandTest(PluginRunCase, unittest.TestCase):
    step_factor = 2.0  # the fake step_climb is off by 100 %: compare mismatches

    def test_hk1_deny_as_the_sdk_hook_and_recorded_by_the_server(self):
        data = {"tool_name": TOOL("analyze"), "agent_type": "hiraku:cae-strength", "agent_id": "a1",
                "tool_input": {"tool": "step_climb", "load_case_id": "P1-step100", "prediction_ids": []}}
        want, kept = self.sdk_hooks()
        want = want.decide("PreToolUse", data)
        out = self.hook("PreToolUse", data)
        self.assertEqual(out, want)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(self.events("hook.denied"), [])  # the hook process does not write events
        self.server.drain()
        (denied,) = self.events("hook.denied")
        self.assertEqual(denied["data"]["rule"], rules.HK1)
        self.assertEqual(denied["agent"], "cae.strength")
        self.assertEqual(denied["data"]["tool"], contract.mcp_name("analyze"))
        self.assertEqual(denied["text"], out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assert_events_fit()

    def test_japanese_arguments_reach_the_server_as_sent(self):
        """Claude Code sends the hook input as UTF-8 bytes (not \\u escapes). Read in the process's code
        page (cp932 on a Japanese Windows) they would give another Attribution key than the server's, and
        the server would refuse write_report for want of a caller."""
        args = {"summary": "強度は合格 (判定は記録の ID で)", "next": []}
        body = json.dumps({"hook_event_name": "PreToolUse", "tool_name": TOOL("write_report"), "tool_input": args},
                          ensure_ascii=False).encode("utf-8")
        env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "PYTHONUTF8")}
        env["PYTHONIOENCODING"] = "cp932"  # the console code page a Japanese Windows gives a piped stdin
        p = subprocess.run([sys.executable, "-m", "hiraku", "hook", "PreToolUse", "--runs", str(self.runs)],
                           cwd=REPO_ROOT, input=body, capture_output=True, timeout=120, env=env)
        self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace"))
        self.assertEqual(json.loads(p.stdout.decode("utf-8")), {})
        self.server.drain()
        self.assertEqual(self.server.attribution.take(contract.mcp_name("write_report"), args), "lead")

    def test_hk3_deny(self):
        pred = self.predict()
        ana = analyze(self.ctx, {"tool": "step_climb", "load_case_id": "P1-step100",
                                 "prediction_ids": [pred["prediction_id"]]}, "cae.strength")
        cmp_ = compare(self.ctx, {"prediction_id": pred["prediction_id"], "analysis_id": ana["analysis_id"]},
                       "cae.strength")
        self.assertEqual(cmp_["comparison"]["status"], "mismatch")
        data = {"tool_name": TOOL("compare"), "agent_type": "hiraku:cae-strength", "agent_id": "a1",
                "tool_input": {"prediction_id": pred["prediction_id"], "analysis_id": ana["analysis_id"]}}
        want = self.sdk_hooks()[0].decide("PreToolUse", data)
        out = self.hook("PreToolUse", data)
        self.assertEqual(out, want)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("record_cause", out["hookSpecificOutput"]["permissionDecisionReason"])
        self.server.drain()
        self.assertEqual([e["data"]["rule"] for e in self.events("hook.denied")], [rules.HK3])

    def test_allow_leaves_the_caller_for_the_server(self):
        data = {"tool_name": TOOL("get_context"), "agent_type": "hiraku:cae-strength", "agent_id": "a1",
                "tool_input": {"sections": ["load_cases"]}}
        self.assertEqual(self.hook("PreToolUse", data), self.sdk_hooks()[0].decide("PreToolUse", data))
        self.assertEqual(self.hook("PreToolUse", data), {})
        self.server.drain()
        self.assertEqual(self.server.attribution.take(contract.mcp_name("get_context"), {"sections": ["load_cases"]}),
                         "cae.strength")

    def test_hk4_blocks_the_stop_then_the_run_ends(self):
        want = self.sdk_hooks()[0].decide("Stop", {"stop_hook_active": False})
        out = self.hook("Stop", {"stop_hook_active": False})
        self.assertEqual(out, want)
        self.assertEqual(out["decision"], "block")
        self.assertIn("R1-step100", out["reason"])
        for _ in range(2):
            self.assertEqual(self.hook("Stop", {"stop_hook_active": True})["decision"], "block")
        self.assertEqual(plugin.read_state(self.ctx.store.path)["stop_blocks"], 3)
        self.assertEqual(self.hook("Stop", {"stop_hook_active": True}), {})  # the cap: the lead may stop
        self.server.drain()
        self.assertEqual([e["data"]["count"] for e in self.events("hook.blocked_stop")], [1, 2, 3])
        (fin,) = self.events("run.finished")
        self.assertEqual(fin["data"]["stop_code"], "not_judged")
        self.assertEqual(self.ctx.project["status"], "stopped")
        self.assertEqual(self.events("agent.stopped")[-1]["agent"], "lead")
        self.assert_events_fit()

    def _stop_to_the_cap(self):
        self.hook("Stop", {"stop_hook_active": False})
        for _ in range(2):
            self.hook("Stop", {"stop_hook_active": True})
        self.server.drain()  # the server reads the blocks while it is alive

    def test_the_next_start_closes_a_plugin_run_no_server_serves(self):
        """docs/contract.md 持ち主のいない実行: a running plugin run whose plugin.alive is stale is closed by the
        next start_run in the same runs folder; one with a fresh plugin.alive is left alone."""
        from hiraku.agent.session import OWNERLESS_REASON, close_ownerless_plugin_runs
        from hiraku.store import RunStore

        self.assertEqual(close_ownerless_plugin_runs(self.runs), [])  # the server touched it just now
        os.utime(self.ctx.store.path / layout.PLUGIN_ALIVE_FILE, (0, 0))
        self.assertEqual(close_ownerless_plugin_runs(self.runs), [self.ctx.project["run_id"]])
        store = RunStore.open(self.ctx.store.path)
        (fin,) = [e for e in store.events() if e["kind"] == "run.finished"]
        self.assertEqual((fin["data"]["status"], fin["data"]["stop_code"], fin["data"].get("stop_reason")),
                         ("stopped", "user_stop", OWNERLESS_REASON))

    def test_the_hook_ends_the_run_itself_when_no_server_serves_it(self):
        """Claude Code may stop the MCP server before it reads the hook's "end" note: with plugin.alive
        stale, the Stop hook that lets the stop through writes run.finished itself."""
        self._stop_to_the_cap()
        alive = self.ctx.store.path / layout.PLUGIN_ALIVE_FILE
        os.utime(alive, (0, 0))  # the server is gone: no touch for a long time
        self.assertEqual(self.hook("Stop", {"stop_hook_active": True}), {})
        from hiraku.store import RunStore

        store = RunStore.open(self.ctx.store.path)  # as the screen reads it, without the server
        self.assertEqual(store.project["status"], "stopped")
        (fin,) = [e for e in store.events() if e["kind"] == "run.finished"]
        self.assertEqual(fin["data"]["stop_code"], "not_judged")
        self.assertEqual([e["data"]["count"] for e in store.events() if e["kind"] == "hook.blocked_stop"], [1, 2, 3])
        self.assertEqual([e for e in store.events() if e["kind"] == "agent.stopped"][-1]["agent"], "lead")
        self.assertFalse(alive.exists())
        self.assertEqual([e["seq"] for e in store.events()], list(range(len(store.events()))))

    def test_a_server_shutting_down_ends_a_run_whose_stop_was_let_through(self):
        self._stop_to_the_cap()
        path = self.ctx.store.path / plugin.OUTBOX_FILE
        self.assertEqual(self.hook("Stop", {"stop_hook_active": True}), {})  # alive: a note for the server
        path.unlink(missing_ok=True)  # the note is lost (the server is stopped before it reads it)
        self.assertTrue(plugin.read_state(self.ctx.store.path)[plugin.END_KEY])
        self.server.shutdown()
        (fin,) = self.events("run.finished")
        self.assertEqual(fin["data"]["stop_code"], "not_judged")  # as end_status decides, not user_stop

    def test_a_case_the_server_found_unpredictable_does_not_hold_the_stop(self):
        state = plugin.read_state(self.ctx.store.path)
        plugin.write_state(self.ctx.store.path, {**state, plugin.UNPREDICTABLE_KEY: {"R1-step100": "max_stress"}})
        self.assertEqual(self.hook("Stop", {"stop_hook_active": False}), {})  # R1 alone is left, and cannot be judged
        self.server.drain()
        self.assertEqual(self.events("hook.blocked_stop"), [])
        (fin,) = self.events("run.finished")
        self.assertEqual(fin["data"]["stop_code"], "not_judged")  # the server's own reckoning: R1 is not judged

    def test_subagent_start_and_stop(self):
        self.hook("SubagentStart", {"agent_id": "a1", "agent_type": "hiraku:cae-strength"})
        self.assertEqual(plugin.read_state(self.ctx.store.path)["running"], {"a1": "cae.strength"})
        out = self.hook("SubagentStop", {"agent_id": "a1", "agent_type": "hiraku:cae-strength", "stop_hook_active": False})
        self.assertEqual(out["decision"], "block")  # strength is still pending
        self.server.drain()
        self.assertEqual([e["agent"] for e in self.events("agent.started")][-1], "cae.strength")
        self.assertEqual(len(self.events("hook.blocked_stop")), 1)

    def test_no_current_run_lets_everything_through(self):
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(plugin.run_hook("Stop", {}, Path(empty)), {})


if __name__ == "__main__":
    unittest.main()
