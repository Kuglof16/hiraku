import asyncio
import unittest
from unittest import mock

from hiraku.agent.hooks import Hooks
from hiraku.agent.roles import agent_id_from_sdk, sdk_name
from hiraku.tools import Attribution, rules
from hiraku.tools.functions import analyze, build_version, compare, revise_design
from tests.agent.test_builders_revise import BuildersCase
from tests.tools.helpers import RunCase
from tests.tools.modules import ModuleCase


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

    def test_the_leads_stop_while_a_role_runs(self):
        """The SDK run lets it through (its runner waits for the role); the plugin blocks it, not counted against
        HK4, and tells the lead to wait with its wait tool, at most MAX_WAIT_BLOCKS times."""
        from hiraku.agent import hooks as H

        self.hooks.running["a1"] = "cae.strength"
        self.assertEqual(run(self.hooks.stop({"stop_hook_active": False}, None, None)), {})
        self.hooks.wait_tool = "wait_tool_name"
        out = run(self.hooks.stop({"stop_hook_active": False}, None, None))
        self.assertEqual(out["decision"], "block")
        self.assertIn("wait_tool_name", out["reason"])
        self.assertIn("cae.strength", out["reason"])
        self.assertEqual((self.hooks.stop_blocks, self.hooks.wait_blocks), (0, 1))
        self.assertEqual([e for e in self.ctx.store.events() if e["kind"] == "hook.blocked_stop"], [])
        self.assertEqual(self.hooks.state()["wait_blocks"], 1)
        self.hooks.wait_blocks = H.MAX_WAIT_BLOCKS
        self.assertEqual(run(self.hooks.stop({"stop_hook_active": True}, None, None)), {})

    def test_stop_passes_when_judged(self):
        ana, _, _ = self.run_p1()
        r1 = self.predict_r1({"value": ana["results"][1]["value"], "unit": "N"})
        a2 = analyze(self.ctx, {"tool": "frame_static", "load_case_id": "R1-step100", "prediction_ids": [r1["prediction_id"]]})
        compare(self.ctx, {"prediction_id": r1["prediction_id"], "analysis_id": a2["analysis_id"]})
        self.assertEqual(run(self.hooks.stop({"stop_hook_active": False}, None, None)), {})

    def make_red(self):
        """P1 matched, then R1 matched far past its limit: the strength tile is red, every case judged."""
        ana, _, _ = self.run_p1()
        orig = self.tools["frame_static"].run

        def scaled(design, lc, preds, params):
            out = orig(design, lc, preds, params)
            out["results"][0]["value"] *= 100
            return out

        self.tools["frame_static"].run = scaled
        r1 = self.predict_r1({"value": ana["results"][1]["value"] * 100, "unit": "N"})
        a2 = analyze(self.ctx, {"tool": "frame_static", "load_case_id": "R1-step100", "prediction_ids": [r1["prediction_id"]]})
        ver = compare(self.ctx, {"prediction_id": r1["prediction_id"], "analysis_id": a2["analysis_id"]})["verdict"]
        self.assertFalse(ver["pass"])
        return ver

    def test_red_with_revisions_left_blocks_the_stop_up_to_the_cap(self):
        from hiraku.agent.runner import end_status

        ver = self.make_red()
        self.assertIsNone(rules.stop_reason(self.ctx))  # every case judged
        self.assertEqual(self.ctx.project["round_limit"], 1)  # version 1: one revision left
        out = run(self.hooks.stop({"stop_hook_active": False}, None, None))
        self.assertEqual(out["decision"], "block")
        for needle in (ver["verdict_id"], "R1-step100", "あと 1 回", "revise_design", "強度信頼性"):
            self.assertIn(needle, out["reason"])
        self.assertEqual(run(self.hooks.stop({"stop_hook_active": True}, None, None))["decision"], "block")
        self.assertEqual(run(self.hooks.stop({"stop_hook_active": True}, None, None)), {})  # cap reached
        blocked = [e for e in self.ctx.store.events() if e["kind"] == "hook.blocked_stop"]
        self.assertEqual([(e["data"]["rule"], e["data"]["count"]) for e in blocked], [(rules.HK4, 1), (rules.HK4, 2)])
        status, reason, code = end_status(self.ctx, self.hooks.stop_blocks)
        self.assertEqual((status, code), ("stopped", "red_left"))
        self.assertIn("止めるのを 2 回断ったあと", reason)
        self.assertEqual(rules.judged_end(self.ctx)[2], "done")  # the scripted driver's end is unchanged
        self.assert_events_fit()

    def test_a_result_beyond_the_limit_after_a_pass_blocks_the_stop(self):
        """docs/contract.md 分野が OK になる条件: a later analysis beyond the criterion holds the passed
        load case back; HK4 treats it as pending, and a run stopped at the cap is not_judged."""
        from hiraku.agent.runner import end_status

        ana, _, _ = self.run_p1()
        r1 = self.predict_r1({"value": ana["results"][1]["value"], "unit": "N"})
        a2 = analyze(self.ctx, {"tool": "frame_static", "load_case_id": "R1-step100", "prediction_ids": [r1["prediction_id"]]})
        self.assertTrue(compare(self.ctx, {"prediction_id": r1["prediction_id"], "analysis_id": a2["analysis_id"]})["verdict"]["pass"])
        orig = self.tools["frame_static"].run

        def scaled(design, lc, preds, params):
            out = orig(design, lc, preds, params)
            out["results"][0]["value"] *= 100
            return out

        self.tools["frame_static"].run = scaled
        a3 = analyze(self.ctx, {"tool": "frame_static", "load_case_id": "R1-step100", "prediction_ids": [r1["prediction_id"]]})
        self.assertEqual(self.ctx.store.judgements()[0]["state"], "pending")
        out = run(self.hooks.stop({"stop_hook_active": False}, None, None))
        self.assertEqual(out["decision"], "block")
        for needle in ("R1-step100", a3["analysis_id"], "を超えている", "設計役に渡す"):
            self.assertIn(needle, out["reason"])
        run(self.hooks.stop({"stop_hook_active": True}, None, None))
        self.assertEqual(run(self.hooks.stop({"stop_hook_active": True}, None, None)), {})  # cap reached
        self.assertEqual(end_status(self.ctx, self.hooks.stop_blocks)[::2], ("stopped", "not_judged"))
        self.assert_events_fit()

    def test_a_case_behind_a_lower_stage_red_with_no_revision_left_ends_round_limit(self):
        """docs/contract.md HK4 / HK5: with the revisions used up, cases that wait only behind a red of a lower
        stage are not analysed; the stop goes through and the run ends round_limit naming them."""
        from unittest import mock

        from hiraku.agent.runner import end_status

        self.run_p1()
        self.ctx.project["round_limit"] = 0  # version 1 is the last
        red_lc = {"load_case_id": "H1-steer", "domain": "assembly"}
        with mock.patch.object(rules, "load_case_stage", return_value=1), \
                mock.patch.object(rules, "lower_stage_left", return_value=[(0, red_lc, "NG、ver-0009")]):
            blocked = rules.stage_blocked(self.ctx)
            self.assertEqual(list(blocked), ["R1-step100"])
            self.assertIn("H1-steer", blocked["R1-step100"])
            self.assertEqual(run(self.hooks.stop({"stop_hook_active": False}, None, None)), {})
            status, reason, code = end_status(self.ctx)
        self.assertEqual((status, code), ("stopped", "round_limit"))
        self.assertIn("低い段の赤が残るので判定していない", reason)
        self.assertIn("H1-steer", reason)

    def test_red_with_no_revision_left_may_stop(self):
        from hiraku.agent.runner import end_status

        self.make_red()
        self.ctx.project["round_limit"] = 0
        self.assertIsNone(rules.red_left_reason(self.ctx))
        self.assertEqual(run(self.hooks.stop({"stop_hook_active": False}, None, None)), {})
        self.assertEqual(end_status(self.ctx)[::2], ("stopped", "round_limit"))

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
        stopped = [e for e in self.ctx.store.events() if e["kind"] == "agent.stopped"][0]
        self.assertEqual(stopped["data"], {"agent_id": "cae.strength", "stop_code": "done"})  # ended its own work
        self.assert_events_fit()

    def test_stopped_data_says_why(self):
        from hiraku.agent.hooks import stopped_data

        for run_code, cut_off, want in ((None, False, "done"), ("not_judged", False, "done"),
                                        ("not_judged", True, "run_ended"), ("cost_limit", False, "run_ended"),
                                        ("turn_limit", True, "run_ended"), ("user_stop", True, "user_stop"),
                                        ("user_stop", False, "user_stop"), ("error", True, "error")):
            with self.subTest(run_code=run_code, cut_off=cut_off):
                self.assertEqual(stopped_data("cae.nvh", run_code, cut_off=cut_off),
                                 {"agent_id": "cae.nvh", "stop_code": want})

    def test_the_latest_version_is_judged_although_the_revisions_are_used_up(self):
        """Contract HOOK_RULES HK4: the round limit ends revisions only; the latest version's pending cases
        still hold the stop, up to the cap, and the run then ends not_judged."""
        from hiraku.agent.runner import end_status

        self.ctx.project["round_limit"] = 0  # no revisions: version 1 is the last, and it is still judged
        self.assertIsNotNone(rules.stop_reason(self.ctx))  # R1 and P1 are still pending
        self.assertEqual(run(self.hooks.stop({"stop_hook_active": False}, None, None))["decision"], "block")
        run(self.hooks.stop({"stop_hook_active": True}, None, None))
        self.assertEqual(run(self.hooks.stop({"stop_hook_active": True}, None, None)), {})  # the cap
        self.assertEqual(end_status(self.ctx, self.hooks.stop_blocks)[::2], ("stopped", "not_judged"))

    def report(self, issues):
        from hiraku.tools.functions import write_report

        return write_report(self.ctx, {"summary": "途中で止めます", "next": [], "issues": issues}, "lead")

    def stop(self):
        return run(self.hooks.stop({"stop_hook_active": False}, None, None))

    def test_after_the_report_names_every_pending_case_the_lead_may_stop(self):
        """HK4 after write_report: the current version's report issues name what keeps each pending domain
        pending (load case id, test id or domain); a pending case the issues do not name still blocks."""
        from hiraku.agent.runner import end_status

        self.report([{"kind": "missing_tool", "what": "段差のツールが止まる", "where": "P1-step100"}])
        self.assertEqual(self.stop()["decision"], "block")  # strength's R1 is pending and not named
        self.assertIn("R1-step100", self.hooks_reason())
        self.report([{"kind": "missing_tool", "what": "段差のツールが止まる", "where": "P1-step100"},
                     {"kind": "missing_formula", "what": "合う式が無い", "record_ids": ["cmp-0009"], "where": "R1"}])
        self.assertIsNotNone(rules.excused_pending(self.ctx))
        self.assertEqual(self.stop(), {})
        status, reason, code = end_status(self.ctx, self.hooks.stop_blocks)
        self.assertEqual((status, code), ("stopped", "not_judged"))
        for needle in ("R1-step100", "合う式が無い"):
            self.assertIn(needle, reason)
        self.assertNotIn("終われません", reason)
        # a report of an earlier version names nothing for this one; P10 is not P1
        self.assertFalse(rules._names("P10-step100", "P1"))
        with mock.patch.object(type(self.ctx), "version", new_callable=mock.PropertyMock, return_value=2):
            self.assertIsNone(rules.current_report(self.ctx))

    def hooks_reason(self):
        return [e for e in self.ctx.store.events() if e["kind"] == "hook.blocked_stop"][-1]["text"]

    def test_a_case_no_formula_predicts_does_not_hold_a_stop_even_past_its_limit(self):
        """Run N13: R8-stress (no K0 formula) had a result past the limit; HK4 held the strength role's stop on it twice,
        though no comparison could settle it. It is left to end not judged."""
        from unittest import mock

        held = {"R1-step100": ["解析 ana-0010 の max_stress が基準を超えている"]}
        with mock.patch.object(rules, "unpredictable_cases", return_value={"R1-step100": "max_stress"}),                 mock.patch.object(rules, "held_cases", return_value=held):
            self.assertNotIn("R1-step100", [lc for _, lcs in rules.holding(self.ctx) for lc in lcs])
        with mock.patch.object(rules, "held_cases", return_value=held):  # a case a formula predicts is still held
            self.assertIn("R1-step100", [lc for _, lcs in rules.holding(self.ctx) for lc in lcs])

    def test_with_the_revisions_used_up_a_mismatch_with_its_cause_lets_the_lead_stop(self):
        """HK4 once round_limit is used up: a pending case held only by mismatches whose cause is written (no
        formula fits) lets the lead's stop through, and stop_reason carries the cause; without the cause it blocks."""
        from hiraku.agent.runner import end_status
        from hiraku.tools.functions import record_cause

        self.ctx.project["round_limit"] = 0
        ana, _, _ = self.run_p1()
        self.tools["frame_static"].factor = 2.0
        r1 = self.predict_r1({"value": ana["results"][1]["value"], "unit": "N"})
        a2 = analyze(self.ctx, {"tool": "frame_static", "load_case_id": "R1-step100", "prediction_ids": [r1["prediction_id"]]})
        c = compare(self.ctx, {"prediction_id": r1["prediction_id"], "analysis_id": a2["analysis_id"]})["comparison"]
        self.assertEqual(c["status"], "mismatch")
        self.assertEqual(rules.holding(self.ctx), [("strength", ["R1-step100"])])
        self.assertIsNone(rules.excused_pending(self.ctx))  # no cause yet
        self.assertEqual(self.stop()["decision"], "block")
        record_cause(self.ctx, {"comparison_id": c["comparison_id"], "cause": "レールの式が横の部材のねじりを入れていない",
                                "fix": "合う式が無いので、そう報告する"}, "cae.strength")
        self.assertEqual(self.stop(), {})
        status, reason, code = end_status(self.ctx, self.hooks.stop_blocks)
        self.assertEqual((status, code), ("stopped", "not_judged"))
        for needle in ("round_limit 0", "R1-step100", c["comparison_id"], "レールの式が横の部材のねじりを入れていない"):
            self.assertIn(needle, reason)
        # with revisions left, a written cause alone does not let the stop through
        self.ctx.project["round_limit"] = 1
        self.assertIsNone(rules.excused_pending(self.ctx))

    def _stop_role(self, agent_type, agent_id="a1"):
        return run(self.hooks.subagent_stop({"agent_id": agent_id, "agent_type": agent_type,
                                             "stop_hook_active": False}, None, None))

    def test_role_stop_blocks_are_counted_per_design_version(self):
        from unittest import mock

        held = [("strength", ["R1-step100"])]
        with mock.patch.object(rules, "holding", return_value=held), \
                mock.patch.object(rules, "stop_reason", return_value="R1-step100 がまだ"):
            self.assertEqual(self._stop_role("cae-strength")["decision"], "block")
            self.assertEqual(self._stop_role("cae-strength")["decision"], "block")
            self.assertEqual(self._stop_role("cae-strength"), {})  # the cap of version 1
            with mock.patch.object(type(self.ctx), "version", new_callable=mock.PropertyMock, return_value=2):
                self.assertEqual(self._stop_role("cae-strength")["decision"], "block")  # version 2 has its own
        blocked = [e for e in self.ctx.store.events() if e["kind"] == "hook.blocked_stop"]
        self.assertEqual([(e["data"]["count"], e["data"]["design_version"]) for e in blocked], [(1, 1), (2, 1), (1, 2)])

    def test_the_packagers_stop_is_held_by_assembly(self):
        from unittest import mock

        with mock.patch.object(rules, "holding", return_value=[("assembly", ["H1-interference"])]), \
                mock.patch.object(rules, "stop_reason", return_value="H1-interference がまだ"):
            out = self._stop_role("packager")
        self.assertEqual(out["decision"], "block")
        ev = [e for e in self.ctx.store.events() if e["kind"] == "hook.blocked_stop"][-1]
        self.assertEqual((ev["agent"], ev["domain"]), ("packager", "assembly"))
        self.assert_events_fit()

    def test_matchers(self):
        m = self.hooks.matchers()
        self.assertEqual(set(m), {"PreToolUse", "PostToolUse", "Stop", "SubagentStart", "SubagentStop"})


class RoleStopReasonTest(BuildersCase, unittest.TestCase):
    def test_a_roles_stop_reason_names_its_own_domain_only(self):
        hooks = Hooks(self.ctx, Attribution())
        self.assertEqual({d for d, _ in rules.pending(self.ctx)}, {"strength", "assembly"})
        run(hooks.subagent_start({"agent_id": "a1", "agent_type": "cae-strength"}, None, None))
        out = run(hooks.subagent_stop({"agent_id": "a1", "agent_type": "cae-strength", "stop_hook_active": False},
                                      None, None))
        self.assertEqual(out["decision"], "block")
        self.assertIn("R1-step100", out["reason"])
        self.assertNotIn("H1-steer30", out["reason"])  # assembly's case is not the strength role's


class ModuleScopeHookTest(ModuleCase, unittest.TestCase):
    """HK6 in PreToolUse on a design with modules (the caller is the running role of agent_id a1)."""

    def setUp(self):
        super().setUp()
        self.attribution = Attribution()
        self.hooks = Hooks(self.ctx, self.attribution)

    def pre(self, tool, args, agent):
        self.hooks.running["a1"] = agent
        data = {"hook_event_name": "PreToolUse", "tool_name": f"mcp__hiraku__{tool}", "tool_input": args,
                "agent_type": agent.replace(".", "-"), "agent_id": "a1"}
        return run(self.hooks.pre_tool_use(data, "tu1", None))

    def assert_denied(self, out, word):
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["permissionDecision"], "deny")
        self.assertIn(word, hso["permissionDecisionReason"])
        ev = self.ctx.store.events()[-1]
        self.assertEqual((ev["kind"], ev["data"]["rule"]), ("hook.denied", rules.HK6))

    def test_denials(self):
        seat = {"seat_x": {"value": -1000.0, "unit": "mm"}}
        self.assert_denied(self.pre("revise_design", {"changes": seat, "reason": "x"}, "designer.occupant"), "module")
        self.assert_denied(self.pre("revise_design", {"module": "occupant", "changes": seat, "reason": "x"},
                                    "designer.frame"), "designer.occupant")
        self.assert_denied(self.pre("revise_design", {"module": "occupant", "reason": "x",
                                                      "changes": {"battery_x": {"value": -950.0, "unit": "mm"}}},
                                    "designer.occupant"), "powertrain")
        self.assert_denied(self.pre("revise_design", {"module": "occupant", "choices": {"battery_pack": "big"},
                                                      "reason": "x"}, "designer.occupant"), "battery_pack")
        self.assert_denied(self.pre("revise_design", {"module": "occupant", "changes": seat, "reason": "x"},
                                    "packager"), "build_version")
        self.assert_denied(self.pre("build_version", {"reason": "x"}, "designer.occupant"), "packager")
        self.assert_denied(self.pre("build_version", {"reason": "x", "changes": seat}, "packager"), "occupant")
        self.assertEqual(self.ctx.version, 1)
        self.assert_events_fit()

    def test_allowed_calls_and_the_hint_after_a_proposal(self):
        args = {"module": "occupant", "changes": {"seat_x": {"value": -1000.0, "unit": "mm"}}, "reason": "x"}
        self.assertEqual(self.pre("revise_design", args, "designer.occupant"), {})
        self.assertEqual(self.attribution.take("mcp__hiraku__revise_design", args), "designer.occupant")
        revise_design(self.ctx, args, "designer.occupant")
        hint = self.hooks.next_step("revise_design", args)
        self.assertIn("chg-0001", hint)
        self.assertIn("build_version", hint)
        self.assertEqual(self.pre("build_version", {"reason": "まとめ"}, "packager"), {})
        build_version(self.ctx, {"reason": "まとめ"}, "packager")
        self.assertIn("v2", self.hooks.next_step("build_version", {"reason": "まとめ"}))


if __name__ == "__main__":
    unittest.main()
