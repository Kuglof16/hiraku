import unittest

from hiraku.tools import ToolError, rules
from hiraku.tools.functions import analyze, compare, get_context, list_formulas, list_tools, record_cause, record_prediction
from tests.tools.helpers import RunCase


class StartTest(RunCase, unittest.TestCase):
    def test_start_events(self):
        self.assertEqual(self.kinds(), ["run.started", "design.created", "tool.checked", "tool.checked", "judgement.updated"])
        self.assert_events_fit()
        self.assertEqual(self.ctx.store.judgements()[0]["state"], "pending")

    def test_get_context(self):
        c = get_context(self.ctx, {})
        self.assertEqual(c["design_version"], 1)
        self.assertEqual([lc["load_case_id"] for lc in c["load_cases"]], ["P1-step100", "R1-step100"])
        self.assertEqual({lc["owner"] for lc in c["load_cases"]}, {"cae.strength"})
        self.assertEqual(c["design_summary"]["wheelbase"], {"value": 1800.0, "unit": "mm"})
        self.assertEqual(c["design_summary"]["member_length"]["rail_L0"]["value"], 900.0)

    def test_list_filters(self):
        self.assertEqual([f["formula_id"] for f in list_formulas(self.ctx, {"tool": "step_climb"})],
                         ["step_climb.pushed_wheel_load", "step_climb.wheel_force_rigid"])
        self.assertEqual([t["name"] for t in list_tools(self.ctx, {"domain": "strength"})], ["frame_static"])
        self.assertTrue(all(t["usable"] for t in list_tools(self.ctx)))


class PredictionTest(RunCase, unittest.TestCase):
    def test_value_comes_from_k0(self):
        load, force = self.predict_p1(agent="cae.strength")
        self.assertEqual(self.formulas.calls[-1][0], "step_climb.pushed_wheel_load")
        self.assertEqual(force["unit"], "N")
        self.assertEqual(force["agent"], "cae.strength")
        self.assertEqual(force["domain"], "powertrain")  # the load case's domain
        self.assertIn("rear wheels push at the ground, the step pushes back at axle height", force["assumptions"])
        ev = self.ctx.store.events()[-1]
        self.assertEqual((ev["kind"], ev["data"]), ("prediction.recorded", force))

    def test_default_agent_is_the_owner(self):
        load, _ = self.predict_p1(agent=None)
        self.assertEqual(load["agent"], "cae.strength")

    def test_output_must_be_chosen_when_ambiguous(self):
        with self.assertRaises(ToolError) as cm:
            record_prediction(self.ctx, {
                "load_case_id": "P1-step100", "quantity": "something", "formula_id": "step_climb.wheel_force_rigid",
                "inputs": {"r": {"value": 300.0, "unit": "mm"}, "h": {"value": 100.0, "unit": "mm"},
                           "W": {"value": 100.0, "unit": "N"}},
                "assumptions": [],
            })
        self.assertIn("output", str(cm.exception))

    def test_bad_inputs_are_explained(self):
        cases = [
            ({"load_case_id": "X1", "quantity": "q", "formula_id": "step_climb.wheel_force_rigid", "inputs": {},
              "assumptions": []}, "P1-step100"),
            ({"load_case_id": "P1-step100", "quantity": "q", "formula_id": "step_climb.nope", "inputs": {},
              "assumptions": []}, "step_climb.wheel_force_rigid"),
            ({"load_case_id": "P1-step100", "quantity": "F", "formula_id": "step_climb.wheel_force_rigid",
              "inputs": {"r": {"value": 0.3, "unit": "m"}}, "assumptions": []}, "r [mm]"),
            ({"load_case_id": "P1-step100", "quantity": "F", "formula_id": "step_climb.wheel_force_rigid",
              "inputs": {}, "assumptions": [], "value": 1.0}, "入力の形"),
        ]
        for args, needle in cases:
            with self.subTest(needle=needle):
                with self.assertRaises(ToolError) as cm:
                    record_prediction(self.ctx, args)
                self.assertIn(needle, str(cm.exception))
        self.assertEqual(self.ctx.store.records("prediction"), [])


class LoopTest(RunCase, unittest.TestCase):
    def test_h1_analyze_needs_a_prediction_of_this_load_case(self):
        with self.assertRaises(ToolError) as cm:
            analyze(self.ctx, {"tool": "step_climb", "load_case_id": "P1-step100", "prediction_ids": ["pred-0099"]})
        self.assertIn("record_prediction", str(cm.exception))
        r1 = self.predict_r1({"value": 100.0, "unit": "N"})
        d = rules.check_call(self.ctx, "analyze", {"tool": "step_climb", "load_case_id": "P1-step100",
                                                   "prediction_ids": [r1["prediction_id"]]})
        self.assertEqual(d.rule, rules.H1)
        self.assertNotIn("analysis.started", self.kinds())

    def test_upstream_must_match_before_r1(self):
        r1 = self.predict_r1({"value": 100.0, "unit": "N"})
        with self.assertRaises(ToolError) as cm:
            analyze(self.ctx, {"tool": "frame_static", "load_case_id": "R1-step100", "prediction_ids": [r1["prediction_id"]]})
        self.assertIn("P1-step100", str(cm.exception))

    def test_full_chain_to_green(self):
        ana, c1, c2 = self.run_p1()
        self.assertEqual(ana["analysis_id"], "ana-0001")
        self.assertEqual(ana["base"], "K2")
        self.assertEqual((c1["comparison"]["status"], c2["comparison"]["status"]), ("match", "match"))
        self.assertIsNone(c2["verdict"])  # P1 has no criterion
        F = {"value": ana["results"][1]["value"], "unit": "N"}
        r1 = self.predict_r1(F)
        ana2 = analyze(self.ctx, {"tool": "frame_static", "load_case_id": "R1-step100", "prediction_ids": [r1["prediction_id"]]})
        self.assertIn("ana-0001", ana2["inputs_from"])
        self.assertIn("P1-step100", self.tools["frame_static"].runs[0][3]["upstream_analyses"])
        out = compare(self.ctx, {"prediction_id": r1["prediction_id"], "analysis_id": ana2["analysis_id"]})
        self.assertEqual(out["comparison"]["status"], "match")
        self.assertAlmostEqual(out["comparison"]["diff_rel"], 0.03)
        ver = out["verdict"]
        self.assertTrue(ver["pass"])
        self.assertAlmostEqual(ver["margin_pct"], (156.6666666 - ver["value"]["value"]) / 156.6666666 * 100, places=4)
        tile = self.ctx.store.judgements()[0]
        self.assertEqual((tile["state"], tile["verdict_ids"]), ("green", [ver["verdict_id"]]))
        self.assertIsNone(rules.stop_reason(self.ctx))
        self.assert_events_fit()

    def test_failed_analysis_is_recorded(self):
        load, force = self.predict_p1()
        self.tools["step_climb"].run = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
        ana = analyze(self.ctx, {"tool": "step_climb", "load_case_id": "P1-step100", "prediction_ids": [force["prediction_id"]]})
        self.assertEqual(ana["status"], "failed")
        self.assertIn("boom", ana["error"])
        with self.assertRaises(ToolError):
            compare(self.ctx, {"prediction_id": force["prediction_id"], "analysis_id": ana["analysis_id"]})


class LeverRuleTest(RunCase, unittest.TestCase):
    def test_lever_rule_at_rest_overestimates_the_climbing_wheel(self):
        load, force = self.predict_p1(formula_id="mass_props.front_wheel_load")
        ana = analyze(self.ctx, {"tool": "step_climb", "load_case_id": "P1-step100",
                                 "prediction_ids": [load["prediction_id"], force["prediction_id"]]})
        cmp = compare(self.ctx, {"prediction_id": load["prediction_id"], "analysis_id": ana["analysis_id"]})["comparison"]
        self.assertEqual(cmp["status"], "mismatch")
        self.assertLess(cmp["diff_rel"], -0.10)  # the push couple moves load rearward


class MismatchTest(RunCase, unittest.TestCase):
    step_factor = 1.5  # 50 % off: beyond P1's 10 %

    def test_h3_until_a_cause_then_resolved(self):
        load, force = self.predict_p1()
        ana = analyze(self.ctx, {"tool": "step_climb", "load_case_id": "P1-step100",
                                 "prediction_ids": [load["prediction_id"], force["prediction_id"]]})
        c1 = compare(self.ctx, {"prediction_id": load["prediction_id"], "analysis_id": ana["analysis_id"]})
        self.assertEqual(c1["comparison"]["status"], "mismatch")
        cmp_id = c1["comparison"]["comparison_id"]
        with self.assertRaises(ToolError) as cm:  # H3 also stops compare in the same domain
            compare(self.ctx, {"prediction_id": force["prediction_id"], "analysis_id": ana["analysis_id"]})
        self.assertIn(cmp_id, str(cm.exception))
        args = {"tool": "step_climb", "load_case_id": "P1-step100", "prediction_ids": [force["prediction_id"]]}
        d = rules.check_call(self.ctx, "analyze", args)
        self.assertEqual(d.rule, rules.H3)
        self.assertIn(f'record_cause(comparison_id="{cmp_id}"', d.reason)
        with self.assertRaises(ToolError):
            analyze(self.ctx, args)
        self.assertIn("record_cause", rules.stop_reason(self.ctx))

        with self.assertRaises(ToolError):
            record_cause(self.ctx, {"comparison_id": cmp_id, "cause": " ", "fix": "x"})
        cause = record_cause(self.ctx, {"comparison_id": cmp_id, "cause": "load split differs", "fix": "use the tool's split"})
        self.assertFalse(cause["resolved"])
        self.assertEqual(self.ctx.store.get("comparison", cmp_id)["cause_id"], cause["cause_id"])
        self.assertIsNone(rules.check_call(self.ctx, "analyze", {**args, "prediction_ids": [force["prediction_id"]]}))
        with self.assertRaises(ToolError):
            record_cause(self.ctx, {"comparison_id": cmp_id, "cause": "again", "fix": "again"})

        # the tool is fixed; a later match of the same quantity resolves the cause
        self.tools["step_climb"].factor = 1.0
        ana2 = analyze(self.ctx, {"tool": "step_climb", "load_case_id": "P1-step100",
                                  "prediction_ids": [load["prediction_id"], force["prediction_id"]]})
        out = compare(self.ctx, {"prediction_id": load["prediction_id"], "analysis_id": ana2["analysis_id"]})
        self.assertEqual(out["comparison"]["status"], "match")
        resolved = self.ctx.store.get("cause", cause["cause_id"])
        self.assertTrue(resolved["resolved"])
        self.assertEqual(resolved["resolved_by"], out["comparison"]["comparison_id"])
        self.assert_events_fit()

    def test_match_needs_no_cause(self):
        self.tools["step_climb"].factor = 1.0
        _, c1, _ = self.run_p1()
        with self.assertRaises(ToolError):
            record_cause(self.ctx, {"comparison_id": c1["comparison"]["comparison_id"], "cause": "x", "fix": "y"})


class UnusableToolTest(RunCase, unittest.TestCase):
    frame_checks_pass = False

    def test_h2(self):
        self.assertFalse(self.ctx.specs["frame_static"]["usable"])
        r1 = self.predict_r1({"value": 100.0, "unit": "N"})
        d = rules.check_call(self.ctx, "analyze", {"tool": "frame_static", "load_case_id": "R1-step100",
                                                   "prediction_ids": [r1["prediction_id"]]})
        self.assertEqual(d.rule, rules.H2)
        self.assertIn("step_climb", d.reason)  # names the usable tools

    def test_unknown_tool(self):
        with self.assertRaises(ToolError) as cm:
            analyze(self.ctx, {"tool": "nope", "load_case_id": "P1-step100", "prediction_ids": ["pred-0001"]})
        self.assertIn("list_tools", str(cm.exception))


class RedVerdictTest(RunCase, unittest.TestCase):
    def test_red_tile(self):
        ana, _, _ = self.run_p1()
        F = {"value": ana["results"][1]["value"] * 100, "unit": "N"}  # far beyond the limit, but predicted the same way
        self.tools["frame_static"].run_scale = 100
        orig = self.tools["frame_static"].run

        def scaled(design, lc, preds, params):
            out = orig(design, lc, preds, params)
            out["results"][0]["value"] *= 100
            return out

        self.tools["frame_static"].run = scaled
        r1 = self.predict_r1(F)
        ana2 = analyze(self.ctx, {"tool": "frame_static", "load_case_id": "R1-step100", "prediction_ids": [r1["prediction_id"]]})
        out = compare(self.ctx, {"prediction_id": r1["prediction_id"], "analysis_id": ana2["analysis_id"]})
        self.assertFalse(out["verdict"]["pass"])
        self.assertLess(out["verdict"]["margin_pct"], 0)
        tile = self.ctx.store.judgements()[0]
        self.assertEqual((tile["state"], tile["red_count"]), ("red", 1))
        self.assertIsNone(rules.stop_reason(self.ctx))  # judged (red), so the run may stop


if __name__ == "__main__":
    unittest.main()
