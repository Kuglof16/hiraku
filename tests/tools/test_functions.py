import unittest

from hiraku.tools import ToolError, rules
from hiraku.tools.functions import analyze, compare, get_context, list_formulas, list_tools, record_cause, record_prediction
from tests.tools.helpers import RunCase


class StartTest(RunCase, unittest.TestCase):
    def test_start_events(self):
        self.assertEqual(self.kinds(), ["run.started", "design.created", "tool.checked", "tool.checked", "judgement.updated"])
        self.assert_events_fit()
        self.assertEqual(self.ctx.store.judgements()[0]["state"], "pending")
        self.assertEqual([lc["test_id"] for lc in self.ctx.load_cases], ["P1", "R1"])

    def test_get_context(self):
        c = get_context(self.ctx, {})
        self.assertEqual(c["design_version"], 1)
        self.assertEqual([lc["load_case_id"] for lc in c["load_cases"]], ["P1-step100", "R1-step100"])
        self.assertEqual({lc["owner"] for lc in c["load_cases"]}, {"cae.strength"})
        # the large parts only on request
        self.assertEqual(c["sections"], ["requirements", "load_cases", "judgements", "records"])
        for big in ("design", "design_summary", "design_variables"):
            self.assertNotIn(big, c)
        c = get_context(self.ctx, {"sections": ["design_summary"]})
        self.assertEqual(c["design_summary"]["wheelbase"], {"value": 1800.0, "unit": "mm"})
        self.assertEqual(c["design_summary"]["member_length"]["rail_L0"]["value"], 900.0)
        self.assertNotIn("load_cases", c)
        self.assertEqual(get_context(self.ctx, {"sections": ["design"]})["design"]["design_version"], 1)

    def test_list_filters(self):
        self.assertEqual([f["formula_id"] for f in list_formulas(self.ctx, {"tool": "step_climb"})],
                         ["step_climb.pushed_wheel_load", "step_climb.wheel_force_rigid"])
        self.assertEqual([t["name"] for t in list_tools(self.ctx, {"domain": "strength"})], ["frame_static"])
        self.assertTrue(all(t["usable"] for t in list_tools(self.ctx)))

    def test_list_tools_is_one_line_per_tool(self):
        lines = list_tools(self.ctx)
        self.assertEqual(lines[0], {"name": "step_climb", "title": "fake step_climb", "domain": "powertrain",
                                    "base": "K2", "tests": ["P1"], "usable": True, "checks_passed": "1/1"})
        self.assertEqual([t["name"] for t in list_tools(self.ctx, {"test_id": "R1"})], ["frame_static"])
        (full,) = list_tools(self.ctx, {"tool": "frame_static"})
        self.assertEqual(full["checks"][0]["case"], "known answer")
        with self.assertRaises(ToolError):
            list_tools(self.ctx, {"tool": "nope"})

    def test_list_formulas_follows_the_tools_list(self):
        self.ctx.specs["frame_static"]["formulas"] = ["frame_static.rail_bending", "mass_props.front_wheel_load"]
        got = [f["formula_id"] for f in list_formulas(self.ctx, {"tool": "frame_static"})]
        self.assertIn("mass_props.front_wheel_load", got)  # named by the tool, though not its prefix
        self.assertIn("frame_static.rail_step_bending", got)  # its prefix


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
    def test_hk1_analyze_needs_a_prediction_of_this_load_case(self):
        with self.assertRaises(ToolError) as cm:
            analyze(self.ctx, {"tool": "step_climb", "load_case_id": "P1-step100", "prediction_ids": ["pred-0099"]})
        self.assertIn("record_prediction", str(cm.exception))
        r1 = self.predict_r1({"value": 100.0, "unit": "N"})
        d = rules.check_call(self.ctx, "analyze", {"tool": "step_climb", "load_case_id": "P1-step100",
                                                   "prediction_ids": [r1["prediction_id"]]})
        self.assertEqual(d.rule, rules.HK1)
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
        self.assertEqual((ver["basis"], ver["value_from"], ver["value"]), ("match", "analysis", out["comparison"]["analyzed"]))
        self.assertNotIn("cause_id", ver)
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


class PickResultTest(unittest.TestCase):
    def test_several_places_give_the_unfavourable_one(self):
        from hiraku.tools.functions import _pick_result

        ana = {"analysis_id": "ana-0001", "results": [
            {"quantity": "free_steer_angle", "value": 34.0, "unit": "deg", "where": "FR"},
            {"quantity": "free_steer_angle", "value": 28.0, "unit": "deg", "where": "FL"},
            {"quantity": "max_stress", "value": -90.0, "unit": "MPa", "where": "a"},
            {"quantity": "max_stress", "value": 60.0, "unit": "MPa", "where": "b"},
        ]}
        at_least = {"quantity": "free_steer_angle", "op": ">=", "limit": {"value": 30.0, "unit": "deg"}}
        at_most = {"quantity": "max_stress", "op": "<=", "limit": {"value": 160.0, "unit": "MPa"}}
        self.assertEqual(_pick_result(ana, "free_steer_angle", at_least)["where"], "FL")  # >=: the smallest
        self.assertEqual(_pick_result(ana, "max_stress", at_most)["where"], "b")  # <=: the largest
        self.assertEqual(_pick_result(ana, "max_stress")["where"], "a")  # no criterion: the largest in size
        self.assertEqual(_pick_result(ana, "free_steer_angle", at_most)["where"], "FR")  # criterion on another quantity


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

    def test_hk3_until_a_cause_then_resolved(self):
        load, force = self.predict_p1()
        ana = analyze(self.ctx, {"tool": "step_climb", "load_case_id": "P1-step100",
                                 "prediction_ids": [load["prediction_id"], force["prediction_id"]]})
        c1 = compare(self.ctx, {"prediction_id": load["prediction_id"], "analysis_id": ana["analysis_id"]})
        self.assertEqual(c1["comparison"]["status"], "mismatch")
        cmp_id = c1["comparison"]["comparison_id"]
        with self.assertRaises(ToolError) as cm:  # HK3 also stops compare in the same domain
            compare(self.ctx, {"prediction_id": force["prediction_id"], "analysis_id": ana["analysis_id"]})
        self.assertIn(cmp_id, str(cm.exception))
        args = {"tool": "step_climb", "load_case_id": "P1-step100", "prediction_ids": [force["prediction_id"]]}
        d = rules.check_call(self.ctx, "analyze", args)
        self.assertEqual(d.rule, rules.HK3)
        self.assertIn(f'record_cause(comparison_id="{cmp_id}"', d.reason)
        with self.assertRaises(ToolError):
            analyze(self.ctx, args)
        self.assertIn("record_cause", rules.stop_reason(self.ctx))

        with self.assertRaises(ToolError):
            record_cause(self.ctx, {"comparison_id": cmp_id, "cause": " ", "fix": "x"})
        out = record_cause(self.ctx, {"comparison_id": cmp_id, "cause": "load split differs", "fix": "use the tool's split"})
        cause = out["cause"]
        self.assertIsNone(out["verdict"])
        self.assertFalse(cause["resolved"])
        self.assertNotIn("safe_side", cause)
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


class SafeSideTest(RunCase, unittest.TestCase):
    """R1 (max_stress <= limit): a prediction larger than the analysis may be judged safe side."""

    def r1_mismatch(self, factor):
        """P1 matched, then an R1 analysis `factor` x the stand-in's beam formula, which the prediction uses."""
        ana, _, _ = self.run_p1()
        self.tools["frame_static"].factor = factor
        r1 = self.predict_r1({"value": ana["results"][1]["value"], "unit": "N"})
        ana2 = analyze(self.ctx, {"tool": "frame_static", "load_case_id": "R1-step100", "prediction_ids": [r1["prediction_id"]]})
        cmp = compare(self.ctx, {"prediction_id": r1["prediction_id"], "analysis_id": ana2["analysis_id"]})["comparison"]
        self.assertEqual(cmp["status"], "mismatch")
        return cmp

    def args(self, cmp, **kw):
        return {"comparison_id": cmp["comparison_id"], "cause": "the joint is taken as fixed",
                "fix": "judge from the hand calculation", **kw}

    def test_prediction_above_the_analysis_gives_a_verdict_from_the_prediction(self):
        cmp = self.r1_mismatch(0.7)  # analysis 30 % below the prediction
        out = record_cause(self.ctx, self.args(cmp, safe_side=True, safe_side_reason="fixed joints overestimate the twist"))
        cause, ver = out["cause"], out["verdict"]
        self.assertEqual((cause["safe_side"], cause["safe_side_reason"]), (True, "fixed joints overestimate the twist"))
        self.assertEqual((ver["basis"], ver["value_from"], ver["cause_id"]), ("explained_safe_side", "prediction", cause["cause_id"]))
        self.assertEqual((ver["comparison_id"], ver["value"]), (cmp["comparison_id"], cmp["predicted"]))
        self.assertTrue(ver["pass"])
        self.assertAlmostEqual(ver["margin_pct"], (156.6666666 - cmp["predicted"]["value"]) / 156.6666666 * 100, places=4)
        tile = self.ctx.store.judgements()[0]
        self.assertEqual((tile["state"], tile["verdict_ids"]), ("green", [ver["verdict_id"]]))
        self.assertIn("安全側の予測で", tile["summary"])
        self.assertIsNone(rules.stop_reason(self.ctx))  # HK4: judged
        self.assertEqual(self.ctx.open_mismatches(), [])  # HK3: the cause is written
        self.assertFalse(self.ctx.store.get("cause", cause["cause_id"])["resolved"])  # no later match
        self.assert_events_fit()

    def test_prediction_below_the_analysis_is_refused(self):
        cmp = self.r1_mismatch(1.5)  # analysis 50 % above: the prediction is on the unsafe side
        with self.assertRaises(ToolError) as cm:
            record_cause(self.ctx, self.args(cmp, safe_side=True, safe_side_reason="it seems fine"))
        msg = str(cm.exception)
        self.assertIn("大きい方が不利", msg)
        self.assertIn("safe_side を外して", msg)
        self.assertEqual(self.ctx.store.records("cause"), [])
        self.assertEqual(self.ctx.store.records("verdict"), [])
        # the same cause without safe_side is accepted and gives no verdict
        self.assertIsNone(record_cause(self.ctx, self.args(cmp))["verdict"])
        self.assertEqual(self.ctx.store.judgements()[0]["state"], "pending")

    def test_safe_side_needs_a_reason(self):
        cmp = self.r1_mismatch(0.7)
        for kw in ({"safe_side": True}, {"safe_side": True, "safe_side_reason": "  "}):
            with self.subTest(kw=kw):
                with self.assertRaises(ToolError) as cm:
                    record_cause(self.ctx, self.args(cmp, **kw))
                self.assertIn("safe_side_reason", str(cm.exception))
        self.assertEqual(self.ctx.store.records("cause"), [])

    def test_no_criterion_no_safe_side(self):
        load, force = self.predict_p1(formula_id="mass_props.front_wheel_load")
        ana = analyze(self.ctx, {"tool": "step_climb", "load_case_id": "P1-step100",
                                 "prediction_ids": [load["prediction_id"], force["prediction_id"]]})
        cmp = compare(self.ctx, {"prediction_id": load["prediction_id"], "analysis_id": ana["analysis_id"]})["comparison"]
        self.assertEqual(cmp["status"], "mismatch")  # the lever rule, larger than the tool
        with self.assertRaises(ToolError) as cm:
            record_cause(self.ctx, self.args(cmp, safe_side=True, safe_side_reason="larger is safer"))
        self.assertIn("基準は なし", str(cm.exception))


class UnusableToolTest(RunCase, unittest.TestCase):
    frame_checks_pass = False

    def test_hk2(self):
        self.assertFalse(self.ctx.specs["frame_static"]["usable"])
        r1 = self.predict_r1({"value": 100.0, "unit": "N"})
        d = rules.check_call(self.ctx, "analyze", {"tool": "frame_static", "load_case_id": "R1-step100",
                                                   "prediction_ids": [r1["prediction_id"]]})
        self.assertEqual(d.rule, rules.HK2)
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
