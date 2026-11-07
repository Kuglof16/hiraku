"""HK5 (light first): tool and load case stages, the analyze gate, and the order in HK4's text."""

import unittest

from hiraku.tools import rules
from hiraku.tools.shelf import DEFAULT_STAGE, STAGE_FALLBACK, load_case_stage, stages_of, tool_stage
from tests.tools.helpers import RunCase


class StageTest(RunCase, unittest.TestCase):
    def test_the_fallback_table_is_used_where_the_spec_has_no_stage(self):
        self.assertEqual(tool_stage(self.ctx, "step_climb"), STAGE_FALLBACK["step_climb"])
        self.assertEqual(tool_stage(self.ctx, "a_tool_nobody_knows"), DEFAULT_STAGE)
        self.ctx.specs["step_climb"] = dict(self.ctx.specs["step_climb"], stage=2)
        self.assertEqual(tool_stage(self.ctx, "step_climb"), 2)

    def test_the_table_agrees_with_what_the_departments_declared(self):
        # the analysis department (commit 51a639a) and the K2 team (commit 4c009c5)
        declared = {"assembly": 0, "frame_static": 1, "step_climb": 1, "modal": 1, "drive_energy": 1, "thermal": 1,
                    "crash": 1, "bed_static": 1, "actuator_linkage": 1, "frame_shell": 3, "frame_tubes": 3,
                    "fatigue": 3, "kc_bench": 1, "tilt": 2, "crosswind": 2, "braking": 2, "launch": 2,
                    "lane_change": 2, "offroad_roll": 2, "step_climb_dynamic": 2}
        self.assertEqual(STAGE_FALLBACK, declared)
        from hiraku import solvers

        for t in solvers.builtin_tools():  # a tool's own stage (class attribute, no checks run) agrees too
            own = getattr(t, "stage", None)
            if own is not None and t.name in STAGE_FALLBACK:
                self.assertEqual(own, STAGE_FALLBACK[t.name], t.name)

    def test_a_specs_stage_wins_over_the_table(self):
        name = "frame_static"
        self.assertEqual(tool_stage(self.ctx, name), STAGE_FALLBACK[name])
        self.ctx.specs[name] = dict(self.ctx.specs[name], stage=3)  # the checked spec
        self.assertEqual(tool_stage(self.ctx, name), 3)
        del self.ctx.specs[name]["stage"]
        own = self.tools[name].spec
        self.tools[name].spec = lambda: dict(own(), stage=0)  # the tool's own spec()
        self.assertEqual(tool_stage(self.ctx, name), 0)

    def test_a_load_case_takes_the_lowest_stage_of_the_tools_that_give_its_quantity(self):
        lc = self.ctx.load_case("R1-step100")
        self.assertEqual(load_case_stage(self.ctx, lc), 1)
        self.ctx.specs["frame_static"] = dict(self.ctx.specs["frame_static"], stage=3)
        self.assertEqual(load_case_stage(self.ctx, lc), 3)  # the fake step_climb lists P1 only
        self.ctx.specs["frame_static"] = dict(self.ctx.specs["frame_static"], usable=False)
        self.assertIsNone(load_case_stage(self.ctx, lc))  # no usable tool checks it

    def test_a_tool_for_another_kind_of_design_does_not_set_the_stage(self):
        lc = self.ctx.load_case("R1-step100")
        self.ctx.specs["frame_static"] = dict(self.ctx.specs["frame_static"], stage=3)
        self.ctx.specs["bed_only"] = {"name": "bed_only", "usable": True, "tests": ["R1"], "stage": 0,
                                      "applies_to": ["care_bed"]}
        self.assertEqual(load_case_stage(self.ctx, lc), 3)  # the care bed's stage-0 tool does not check this design

    def test_the_hook_command_reads_the_servers_stages(self):
        kept = stages_of(self.ctx)
        self.ctx.tools = {}
        self.ctx.stages = {"tools": kept["tools"], "cases": dict(kept["cases"], **{"R1-step100": 3})}
        self.assertEqual(load_case_stage(self.ctx, self.ctx.load_case("R1-step100")), 3)

    def test_a_load_case_is_at_least_as_heavy_as_the_cases_that_feed_it(self):
        """R9-bolt on frame_static (stage 1) reads K2's mount forces of stage-2 cases: it waits for them (feeds)."""
        r1 = self.ctx.load_case("R1-step100")
        self.assertIn("R1-step100", self.ctx.load_case("P1-step100").get("feeds") or [])
        self.assertEqual(load_case_stage(self.ctx, r1), 1)
        self.ctx.specs["step_climb"] = dict(self.ctx.specs["step_climb"], stage=2)  # P1, upstream, heavier
        self.assertEqual(load_case_stage(self.ctx, self.ctx.load_case("P1-step100")), 2)
        self.assertEqual(load_case_stage(self.ctx, r1), 2)
        self._unfeed()
        self.assertEqual(load_case_stage(self.ctx, r1), 1)

    def _unfeed(self):
        """P1 feeds R1 in this run; the gate tests below make P1's tool heavier with R1 not depending on it."""
        for lc in self.ctx.load_cases:
            lc.pop("feeds", None)

    # In this run P1 has no criterion (powertrain is not picked), R1 (strength) has one: R1 is the
    # lower-stage case and step_climb is made heavier for the gate (P1's feeds taken out: a case fed by a heavier
    # one is as heavy, test_a_load_case_is_at_least_as_heavy_as_the_cases_that_feed_it).

    def test_hk5_holds_a_heavier_tool_until_the_lighter_cases_pass(self):
        from unittest import mock

        self._unfeed()
        self.ctx.specs["step_climb"] = dict(self.ctx.specs["step_climb"], stage=2)
        args = {"tool": "step_climb", "load_case_id": "P1-step100", "prediction_ids": []}
        d = rules.check_analyze(self.ctx, args)
        self.assertEqual(d.rule, rules.HK5)
        self.assertIn("R1-step100 (段 1 (軽い計算)、未判定)", d.reason)
        self.assertIn("段 2 (車の動き)", d.reason)
        # a tool of the stage of what is left is not held (HK1 comes next)
        d = rules.check_analyze(self.ctx, {"tool": "frame_static", "load_case_id": "R1-step100", "prediction_ids": []})
        self.assertEqual(d.rule, rules.HK1)
        green = {"R1-step100": {"verdict_id": "ver-0001", "load_case_id": "R1-step100", "pass": True}}
        with mock.patch.object(rules, "latest_verdicts", return_value=green):
            self.assertEqual(rules.check_analyze(self.ctx, args).rule, rules.HK1)

    def test_hk5_names_a_red_case_and_revise_design(self):
        from unittest import mock

        self._unfeed()
        self.ctx.specs["step_climb"] = dict(self.ctx.specs["step_climb"], stage=3)
        red = {"R1-step100": {"verdict_id": "ver-0009", "load_case_id": "R1-step100", "pass": False}}
        with mock.patch.object(rules, "latest_verdicts", return_value=red):
            d = rules.check_analyze(self.ctx, {"tool": "step_climb", "load_case_id": "P1-step100",
                                               "prediction_ids": []})
        self.assertEqual(d.rule, rules.HK5)
        self.assertIn("NG、ver-0009", d.reason)
        self.assertIn("revise_design", d.reason)

    def test_hk5_a_lighter_tools_red_on_the_same_case_holds_the_heavier_tool(self):
        from unittest import mock

        self.ctx.specs["shell_x"] = {"name": "shell_x", "usable": True, "tests": ["R1"], "stage": 3, "checks": []}
        args = {"tool": "shell_x", "load_case_id": "R1-step100", "prediction_ids": []}
        # not judged yet on the beam: the heavy tool on the same case is not held by HK5 (HK1 comes next)
        self.assertEqual(rules.check_analyze(self.ctx, args).rule, rules.HK1)
        red = {"R1-step100": {"verdict_id": "ver-0009", "load_case_id": "R1-step100", "pass": False,
                              "comparison_id": "cmp-0001"}}
        with mock.patch.object(rules, "latest_verdicts", return_value=red):
            with mock.patch.object(rules, "_verdict_tool", return_value="frame_static"):  # red on the beam (stage 1)
                d = rules.check_analyze(self.ctx, args)
                self.assertEqual(d.rule, rules.HK5)
                self.assertIn("R1-step100 (段 1 (軽い計算)、NG、ver-0009)", d.reason)
                self.assertIn("revise_design", d.reason)
            with mock.patch.object(rules, "_verdict_tool", return_value="shell_x"):  # red from the heavy tool itself
                self.assertEqual(rules.check_analyze(self.ctx, args).rule, rules.HK1)

    def test_hk4_names_the_stage_of_each_pending_case(self):
        text = rules.stop_reason(self.ctx)
        self.assertIn("段 1 (軽い計算): R1-step100", text)
        self.assertNotIn("軽い段から順に", text)  # one stage only

    def test_the_scripted_steps_go_stage_by_stage_upstream_first(self):
        from hiraku.agent.scripted import stage_groups

        self.ctx.specs["step_climb"] = dict(self.ctx.specs["step_climb"], stage=2)
        steps = [{"load_case": "P1-step100", "tool": "step_climb"}, {"load_case": "R1-step100", "tool": "frame_static"}]
        # R1 is fed by P1: it never goes before its upstream step, though its own tool is lighter
        self.assertEqual([(k, [s["load_case"] for s in g]) for k, g in stage_groups(self.ctx, steps)],
                         [(2, ["P1-step100", "R1-step100"])])
        # with R1's step first in the plan as well (R9 is composed before S1 to P3, which feed it)
        self.assertEqual([(k, [s["load_case"] for s in g]) for k, g in stage_groups(self.ctx, list(reversed(steps)))],
                         [(2, ["P1-step100", "R1-step100"])])


if __name__ == "__main__":
    unittest.main()
