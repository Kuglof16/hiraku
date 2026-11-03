import importlib
import unittest
from unittest import mock

from hiraku.tools import ToolError, check_shelf, rules
from hiraku.tools.functions import analyze, compare, get_context, list_formulas, list_tools, record_cause, record_prediction
from hiraku.tools.judge import compute_judgement
from hiraku.tools.summary import design_summary
from tests.tools import fakes
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
        self.assertEqual([f["formula_id"] for f in list_formulas(self.ctx, {"tool": "step_climb"})["formulas"]],
                         ["step_climb.pushed_wheel_load", "step_climb.wheel_force_rigid"])
        self.assertEqual([t["name"] for t in list_tools(self.ctx, {"domain": "strength"})], ["frame_static"])
        self.assertTrue(all(t["usable"] for t in list_tools(self.ctx)))

    def test_list_tools_is_one_line_per_tool(self):
        lines = list_tools(self.ctx)
        self.assertEqual(lines[0], {"name": "step_climb", "title": "fake step_climb", "domain": "powertrain",
                                    "base": "K2", "tests": ["P1"], "usable": True, "checks_passed": "1/1",
                                    "stage": 1})  # HK5: STAGE_FALLBACK, the fake spec has no stage
        self.assertEqual([t["name"] for t in list_tools(self.ctx, {"test_id": "R1"})], ["frame_static"])
        (full,) = list_tools(self.ctx, {"tool": "frame_static"})
        self.assertEqual(full["checks"][0]["case"], "known answer")
        with self.assertRaises(ToolError):
            list_tools(self.ctx, {"tool": "nope"})

    def test_list_formulas_follows_the_tools_list(self):
        self.ctx.specs["frame_static"]["formulas"] = ["frame_static.rail_bending", "mass_props.front_wheel_load"]
        got = [f["formula_id"] for f in list_formulas(self.ctx, {"tool": "frame_static"})["formulas"]]
        self.assertIn("mass_props.front_wheel_load", got)  # named by the tool, though not its prefix
        self.assertIn("frame_static.rail_step_bending", got)  # its prefix

    def test_list_formulas_for_a_tool_gives_its_pairing_with_the_notes(self):
        pairs = [{"quantity": "max_stress", "formula_id": "frame_static.rail_bending", "output": "sigma"},
                 {"quantity": "max_stress", "formula_id": "frame_static.rail_step_bending", "output": "sigma",
                  "note": "rigid: a first estimate"}]
        self.tools["frame_static"].describe = lambda: {"name": "frame_static", "params": [], "predicts": pairs}
        got = list_formulas(self.ctx, {"tool": "frame_static"})
        self.assertEqual(got["tool"], "frame_static")
        self.assertEqual(got["predicts"], pairs)
        self.assertIn("note", got["how"])
        self.assertEqual(list_formulas(self.ctx, {"tool": "step_climb"})["predicts"], [])  # no describe()
        self.assertIsInstance(list_formulas(self.ctx), list)  # without tool: the formulas as before
        from hiraku import contract

        for f in got["formulas"]:
            self.assertEqual(contract.errors("formula", f), [])  # the formula objects are unchanged

    def test_the_tilt_tool_says_its_rigid_formula_is_a_first_estimate(self):
        from hiraku.solvers.k2_vehicle.shelf.tilt import TiltTool

        self.ctx.tools["tilt"] = TiltTool()  # describe() reads the tool's pairs; no checks run
        got = list_formulas(self.ctx, {"tool": "tilt"})["predicts"]
        rigid = next(p for p in got if p["quantity"] == "tilt_angle" and p["formula_id"] == "tilt.rigid_angle")
        self.assertIn("rigid", rigid["note"])
        self.assertTrue(any(p["formula_id"] == "tilt.spring_roll_angle" and p["quantity"] == "tilt_angle" for p in got))


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


class AppliesToTest(RunCase, unittest.TestCase):
    """Contract tool_spec.applies_to: a tool for some kinds of design is refused on another kind and on a
    design of unknown kind; list_tools shows the field."""

    def setUp(self):
        super().setUp()
        self.ctx.specs["step_climb"] = dict(self.ctx.specs["step_climb"], applies_to=["space_frame_utv"])
        load, force = self.predict_p1()
        self.args = {"tool": "step_climb", "load_case_id": "P1-step100", "prediction_ids": [force["prediction_id"]]}

    def test_a_design_of_unknown_kind_gets_no_such_tool(self):
        from hiraku.tools.functions import design_kind

        self.assertIsNone(design_kind(self.ctx))  # the stand-in designgen names no kinds
        with self.assertRaisesRegex(ToolError, "space_frame_utv.*型が分からない"):
            analyze(self.ctx, self.args)
        (line,) = [t for t in list_tools(self.ctx) if t["name"] == "step_climb"]
        self.assertEqual(line["applies_to"], ["space_frame_utv"])

    def test_the_kind_named_runs_and_another_is_refused(self):
        from unittest import mock

        from hiraku.tools import functions

        with mock.patch.object(functions, "design_kind", return_value="space_frame_utv"):
            self.assertEqual(analyze(self.ctx, self.args)["status"], "ok")
        with mock.patch.object(functions, "design_kind", return_value="ladder_4w"):
            with self.assertRaisesRegex(ToolError, "この版の設計の型は ladder_4w"):
                analyze(self.ctx, self.args)

    def test_the_kind_of_a_generated_design(self):
        import hiraku.designgen as designgen
        from hiraku.tools.functions import design_kind

        self.ctx.designgen = designgen
        with mock.patch.object(designgen, "generator_of", return_value=designgen.ARCHETYPES["ladder_4w"]):
            self.assertEqual(design_kind(self.ctx), "ladder_4w")


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
        self.assertEqual(ver["margin"]["unit"], "MPa")
        self.assertAlmostEqual(ver["margin"]["value"], 156.6666666 - ver["value"]["value"], places=4)
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


class CompareWhereTest(RunCase, unittest.TestCase):
    """compare(where=...) picks the result at one member; a verdict only at the failing end of all places."""

    def _r1_with_cross(self, cross_factor):
        tool = self.tools["frame_static"]
        run = tool.run

        def with_cross(design, load_case, predictions, params):
            out = run(design, load_case, predictions, params)
            rail = out["results"][0]
            out["results"] = [rail, dict(rail, value=rail["value"] * cross_factor, where="cross_B")]
            return out

        tool.run = with_cross
        ana, _, _ = self.run_p1()
        r1 = self.predict_r1({"value": ana["results"][1]["value"], "unit": "N"})
        ana2 = analyze(self.ctx, {"tool": "frame_static", "load_case_id": "R1-step100",
                                  "prediction_ids": [r1["prediction_id"]]})
        return r1, ana2

    def test_a_place_that_is_not_the_failing_end_is_compared_but_not_judged(self):
        r1, ana = self._r1_with_cross(2.0)
        out = compare(self.ctx, {"prediction_id": r1["prediction_id"], "analysis_id": ana["analysis_id"],
                                 "where": "rail_L0"})
        self.assertEqual(out["comparison"]["status"], "match")
        self.assertEqual(out["where"], "rail_L0")
        stored = self.ctx.store.get("comparison", out["comparison"]["comparison_id"])
        self.assertEqual(stored["where"], "rail_L0")  # contract comparison.where
        self.assertIsNone(out["verdict"])
        self.assertIn("合否", out["note"])
        self.assertEqual(self.ctx.store.records("verdict"), [])
        self.assert_events_fit()  # the record keeps the contract's shape

    def test_the_failing_end_chosen_by_place_is_judged(self):
        r1, ana = self._r1_with_cross(0.5)
        out = compare(self.ctx, {"prediction_id": r1["prediction_id"], "analysis_id": ana["analysis_id"],
                                 "where": "rail_L0"})
        self.assertTrue(out["verdict"]["pass"])
        self.assertNotIn("note", out)

    def test_an_unknown_place_says_which_places_there_are(self):
        r1, ana = self._r1_with_cross(2.0)
        with self.assertRaises(ToolError) as cm:
            compare(self.ctx, {"prediction_id": r1["prediction_id"], "analysis_id": ana["analysis_id"], "where": "rail_R9"})
        self.assertIn("cross_B, rail_L0", str(cm.exception))

    def test_the_mcp_schema_takes_where(self):
        from hiraku.tools.mcp import INPUT_SCHEMAS

        self.assertIn("where", INPUT_SCHEMAS["compare"]["properties"])
        self.assertFalse(INPUT_SCHEMAS["compare"]["additionalProperties"])


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

    def test_a_result_named_by_result_quantity_gives_the_failing_end(self):
        from hiraku.tools.functions import _pick_result

        # the prediction is of the criterion's quantity; the analysis names it otherwise (result_quantity)
        ana = {"analysis_id": "ana-0001", "results": [
            {"quantity": "stress_at_joint", "value": -150.0, "unit": "MPa", "where": "a"},
            {"quantity": "stress_at_joint", "value": 120.0, "unit": "MPa", "where": "b"},
        ]}
        at_most = {"quantity": "max_stress", "op": "<=", "limit": {"value": 160.0, "unit": "MPa"}}
        self.assertEqual(_pick_result(ana, "stress_at_joint", at_most, "max_stress")["where"], "b")  # <=: the largest
        at_least = dict(at_most, op=">=")
        self.assertEqual(_pick_result(ana, "stress_at_joint", at_least, "max_stress")["where"], "a")  # >=: the smallest
        self.assertEqual(_pick_result(ana, "stress_at_joint", at_most, "other")["where"], "a")  # not the criterion's


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

    def test_a_prediction_recorded_after_the_cause_compares_with_the_same_analysis(self):
        load, force = self.predict_p1()
        ana = analyze(self.ctx, {"tool": "step_climb", "load_case_id": "P1-step100",
                                 "prediction_ids": [load["prediction_id"], force["prediction_id"]]})
        c1 = compare(self.ctx, {"prediction_id": load["prediction_id"], "analysis_id": ana["analysis_id"]})
        early, _ = self.predict_p1()  # recorded before the cause: not part of the analysis
        with self.assertRaises(ToolError) as cm:
            record_cause(self.ctx, {"comparison_id": c1["comparison"]["comparison_id"], "cause": "x", "fix": "y"})
            compare(self.ctx, {"prediction_id": early["prediction_id"], "analysis_id": ana["analysis_id"]})
        for needle in ("record_prediction", "analyze(tool=\"step_climb\"", early["prediction_id"]):
            self.assertIn(needle, str(cm.exception))
        later, _ = self.predict_p1()
        out = compare(self.ctx, {"prediction_id": later["prediction_id"], "analysis_id": ana["analysis_id"]})
        self.assertEqual(out["comparison"]["prediction_id"], later["prediction_id"])
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


class FakeShell(fakes.FakeTool):
    """A stand-in frame_shell for R1: the hot spot is `factor` x the beam formula, the element
    max_stress (a sharp corner's, not judged) `corner` x it."""

    def __init__(self, factor, corner=4.0):
        super().__init__("frame_shell", "strength", ["R1"], factor=factor)
        self.corner = corner

    def run(self, design, load_case, predictions, params):
        self.runs.append((design, load_case, predictions, params))
        up = params["upstream_analyses"]["P1-step100"]
        f = next(r["value"] for r in up["results"] if r["quantity"] == "front_wheel_force")
        summ = design_summary(design)
        rail = summ["rail_section"]
        sigma = f * summ["wheelbase"]["value"] / 4.0 / fakes._rect_tube_Z(rail["b"]["value"], rail["h"]["value"],
                                                                           rail["t"]["value"])
        return {"status": "ok", "base": "K1", "results": [
            {"quantity": "max_hot_spot_stress", "value": sigma * self.current_factor(), "unit": "MPa", "where": "rail_L_BE"},
            {"quantity": "max_stress", "value": sigma * self.corner, "unit": "MPa", "where": "cross_E"}],
            "inputs_from": [up["analysis_id"]]}


class GreenConditionsTest(RunCase, unittest.TestCase):
    """docs/contract.md 分野が緑になる条件: no unresolved mismatch, no result beyond the criterion in the
    judged quantity (frame_shell: max_hot_spot_stress for max_stress); HK4 holds such a domain."""

    def add_shell(self, factor, corner=4.0):
        tool = FakeShell(factor, corner)
        self.ctx.tools["frame_shell"] = tool
        self.ctx.specs["frame_shell"] = {**tool.spec(), "usable": True, "checks": tool.check()}
        return tool

    def beam_green(self, frame_factor=1.03):
        """P1 matched, R1 by frame_static: a match (or, frame_factor 0.7, a safe-side mismatch) and green."""
        ana, _, _ = self.run_p1()
        self.F = {"value": ana["results"][1]["value"], "unit": "N"}
        self.tools["frame_static"].factor = frame_factor
        r1 = self.predict_r1(self.F)
        ana2 = analyze(self.ctx, {"tool": "frame_static", "load_case_id": "R1-step100", "prediction_ids": [r1["prediction_id"]]})
        out = compare(self.ctx, {"prediction_id": r1["prediction_id"], "analysis_id": ana2["analysis_id"]})
        if out["comparison"]["status"] == "mismatch":
            out = record_cause(self.ctx, {"comparison_id": out["comparison"]["comparison_id"], "cause": "fixed joints",
                                          "fix": "judge from the hand calculation", "safe_side": True,
                                          "safe_side_reason": "fixed joints overestimate the bending"})
        self.assertTrue(out["verdict"]["pass"])
        self.assertEqual(self.tile()["state"], "green")

    def tile(self):
        return self.ctx.store.judgements()[0]

    def shell(self, F):
        pred = self.predict_r1(F)
        ana = analyze(self.ctx, {"tool": "frame_shell", "load_case_id": "R1-step100", "prediction_ids": [pred["prediction_id"]]})
        return pred, ana

    def test_a_shell_result_beyond_the_limit_holds_green_back_until_judged(self):
        self.beam_green()
        self.add_shell(6.0)  # about 186 MPa, beyond 156.7
        pred, ana = self.shell(self.F)
        tile = self.tile()
        self.assertEqual(tile["state"], "pending")
        self.assertIn(f"解析 {ana['analysis_id']} (frame_shell) の max_hot_spot_stress", tile["summary"])
        self.assertEqual(rules.pending(self.ctx), [("strength", ["R1-step100"])])
        reason = rules.stop_reason(self.ctx)
        self.assertIn("R1-step100", reason)
        self.assertIn("基準 ≤ 156.7 MPa を超えている", reason)
        self.assertIsNone(rules.red_left_reason(self.ctx))
        # compared on the hot spot: the comparison names it, mismatches, and its cause is not resolved yet
        cmp = compare(self.ctx, {"prediction_id": pred["prediction_id"], "analysis_id": ana["analysis_id"],
                                 "result_quantity": "max_hot_spot_stress"})["comparison"]
        self.assertEqual((cmp["quantity"], cmp["status"]), ("max_hot_spot_stress", "mismatch"))
        cause = record_cause(self.ctx, {"comparison_id": cmp["comparison_id"], "cause": "the joint wall bends as a plate",
                                        "fix": "predict the joint wall"})["cause"]
        self.assertIn(f"原因 {cause['cause_id']} がまだ解けていない", self.tile()["summary"])
        self.assertEqual(self.tile()["state"], "pending")
        # a prediction that matches the shell: the cause is resolved and the verdict (a fail) turns the tile red
        again = self.predict_r1({"value": self.F["value"] * 6.0, "unit": "N"})
        ana2 = analyze(self.ctx, {"tool": "frame_shell", "load_case_id": "R1-step100", "prediction_ids": [again["prediction_id"]]})
        out = compare(self.ctx, {"prediction_id": again["prediction_id"], "analysis_id": ana2["analysis_id"],
                                 "result_quantity": "max_hot_spot_stress"})
        self.assertEqual(out["comparison"]["status"], "match")
        self.assertFalse(out["verdict"]["pass"])
        self.assertTrue(self.ctx.store.get("cause", cause["cause_id"])["resolved"])
        self.assertEqual(self.tile()["state"], "red")
        self.assertEqual(rules.pending(self.ctx), [])
        self.assert_events_fit()

    def test_a_later_result_beyond_the_limit_voids_a_safe_side_verdict(self):
        self.beam_green(frame_factor=0.7)
        self.assertEqual(self.ctx.store.records("verdict")[-1]["basis"], "explained_safe_side")
        self.add_shell(6.0)
        self.shell(self.F)
        self.assertEqual(self.tile()["state"], "pending")
        self.assertEqual(rules.pending(self.ctx), [("strength", ["R1-step100"])])

    def test_an_unresolved_mismatch_holds_green_back(self):
        self.beam_green()
        self.add_shell(1.3)  # within the limit, but 30 % off the prediction
        pred, ana = self.shell(self.F)
        self.assertEqual(self.tile()["state"], "green")  # nothing beyond the limit
        cmp = compare(self.ctx, {"prediction_id": pred["prediction_id"], "analysis_id": ana["analysis_id"],
                                 "result_quantity": "max_hot_spot_stress"})["comparison"]
        self.assertEqual(cmp["status"], "mismatch")
        self.assertEqual(self.tile()["state"], "pending")
        self.assertIn("原因がまだ書かれていない", self.tile()["summary"])
        record_cause(self.ctx, {"comparison_id": cmp["comparison_id"], "cause": "the joint", "fix": "predict the joint"})
        self.assertEqual(self.tile()["state"], "pending")
        again = self.predict_r1({"value": self.F["value"] * 1.3, "unit": "N"})
        ana2 = analyze(self.ctx, {"tool": "frame_shell", "load_case_id": "R1-step100", "prediction_ids": [again["prediction_id"]]})
        out = compare(self.ctx, {"prediction_id": again["prediction_id"], "analysis_id": ana2["analysis_id"],
                                 "result_quantity": "max_hot_spot_stress"})
        self.assertTrue(out["verdict"]["pass"])
        self.assertEqual(self.tile()["state"], "green")
        self.assertIsNone(rules.stop_reason(self.ctx))

    def test_the_judged_quantity_comes_from_the_tools_spec(self):
        from hiraku.tools.judge import judged_quantity

        self.ctx.specs["plate_tool"] = {"judged_as": {"max_stress": "toe_stress"}}
        self.assertEqual(judged_quantity(self.ctx, "plate_tool", "max_stress"), "toe_stress")
        self.assertEqual(judged_quantity(self.ctx, "frame_shell", "max_stress"), "max_hot_spot_stress")  # no spec: the table
        self.assertEqual(judged_quantity(self.ctx, "frame_static", "max_stress"), "max_stress")

    def test_the_shells_element_max_stress_gives_no_verdict(self):
        self.beam_green()
        self.add_shell(1.0, corner=1.0)  # the element maximum equals the prediction, and is still not judged
        pred, ana = self.shell(self.F)
        out = compare(self.ctx, {"prediction_id": pred["prediction_id"], "analysis_id": ana["analysis_id"]})
        self.assertEqual((out["comparison"]["quantity"], out["comparison"]["status"]), ("max_stress", "match"))
        self.assertIsNone(out["verdict"])
        out = compare(self.ctx, {"prediction_id": pred["prediction_id"], "analysis_id": ana["analysis_id"],
                                 "result_quantity": "max_hot_spot_stress"})
        self.assertEqual(out["verdict"]["quantity"], "max_stress")  # the criterion's, judged on the hot spot
        self.assertEqual(out["verdict"]["value"], out["comparison"]["analyzed"])


class MeshTest(RunCase, unittest.TestCase):
    """A tool with needs_mesh gets the version's shell mesh in params["mesh"], made once and recorded."""

    def setUp(self):
        super().setUp()
        # the module analyze imports (sys.modules), even after a test has re-imported the package
        self.solvers = importlib.import_module("hiraku.solvers")

    @staticmethod
    def mesh_of(design):
        return {"design_version": 99, "element_type": "shell", "nodes_mm": [[0, 0, 0], [10, 0, 0], [10, 10, 0]],
                "elements": [[0, 1, 2]], "element_member": ["m1"], "member_thickness_mm": {"m1": 2.0}}

    def analyze_p1(self):
        load, force = self.predict_p1()
        return analyze(self.ctx, {"tool": "step_climb", "load_case_id": "P1-step100",
                                  "prediction_ids": [force["prediction_id"]]})

    def test_made_once_per_version_and_passed_to_the_tool(self):
        self.tools["step_climb"].needs_mesh = True
        made = []

        def frame_mesh(design):
            made.append(design["design_version"])
            return self.mesh_of(design)

        with mock.patch.object(self.solvers,"frame_mesh", frame_mesh, create=True):
            self.analyze_p1()
            self.analyze_p1()
        self.assertEqual(made, [1])
        meshes = self.ctx.store.records("mesh", 1)
        self.assertEqual([(m["mesh_id"], m["design_version"]) for m in meshes], [("mesh-0001", 1)])
        self.assertEqual([r[3]["mesh"]["mesh_id"] for r in self.tools["step_climb"].runs], ["mesh-0001"] * 2)
        self.assertEqual(self.kinds().count("mesh.created"), 1)
        kinds = self.kinds()  # the mesh is an input: recorded before the analysis that uses it starts
        self.assertLess(kinds.index("mesh.created"), kinds.index("analysis.started"))
        self.assert_events_fit()

    def test_a_tool_with_make_mesh_gets_its_own_mesh_once_per_version(self):
        """Contract 3950ac7: make_mesh(design) instead of frame_mesh, mesher = the tool's name, made once per
        version and mesher, recorded before the analysis; frame_mesh's mesh of the version is another one."""
        tool = self.tools["step_climb"]
        tool.needs_mesh = True
        own = []
        tool.make_mesh = lambda design: own.append(1) or self.mesh_of(design)
        with mock.patch.object(self.solvers, "frame_mesh", mock.Mock(side_effect=AssertionError("not frame_mesh")),
                               create=True):
            self.analyze_p1()
            self.analyze_p1()
        self.assertEqual(own, [1])
        (mesh,) = self.ctx.store.records("mesh", 1)
        self.assertEqual((mesh["mesher"], mesh["mesh_id"]), ("step_climb", "mesh-0001"))
        self.assertEqual([r[3]["mesh"]["mesh_id"] for r in tool.runs], ["mesh-0001"] * 2)
        kinds = self.kinds()
        self.assertLess(kinds.index("mesh.created"), kinds.index("analysis.started"))
        del tool.make_mesh  # a frame_mesh tool on the same version gets its own (another mesher)
        with mock.patch.object(self.solvers, "frame_mesh", self.mesh_of, create=True):
            self.analyze_p1()
        self.assertEqual([m["mesher"] for m in self.ctx.store.records("mesh", 1)], ["step_climb", "frame_mesh"])
        self.assert_events_fit()

    def test_a_tool_without_needs_mesh_gets_none(self):
        with mock.patch.object(self.solvers,"frame_mesh", mock.Mock(side_effect=AssertionError), create=True):
            self.analyze_p1()
        self.assertNotIn("mesh", self.tools["step_climb"].runs[0][3])
        self.assertNotIn("mesh.created", self.kinds())

    def test_no_mesher_or_a_mesher_error_is_a_failed_analysis(self):
        self.tools["step_climb"].needs_mesh = True
        with mock.patch.dict(self.solvers.__dict__):
            self.solvers.__dict__.pop("frame_mesh", None)
            ana = self.analyze_p1()
        self.assertEqual(ana["status"], "failed")
        self.assertIn("frame_mesh", ana["error"])
        with mock.patch.object(self.solvers,"frame_mesh", mock.Mock(side_effect=ValueError("member m1 has no wall")),
                               create=True):
            ana = self.analyze_p1()
        self.assertEqual(ana["status"], "failed")
        self.assertIn("member m1 has no wall", ana["error"])
        self.assertEqual(self.tools["step_climb"].runs, [])
        self.assertNotIn("mesh.created", self.kinds())


class CheckCacheTest(RunCase, unittest.TestCase):
    def test_checks_from_the_tests_cache_are_said(self):
        self.tools["step_climb"].checks_from_cache = True  # K2's tools: the tests' disk cache was used
        check_shelf(self.ctx)
        said = [e["text"] for e in self.ctx.store.events() if e["kind"] == "agent.said"]
        self.assertIn("道具 step_climb の照合は、試験のために覚えておいた前の結果を使いました", said)
        self.assertFalse(any("frame_static の照合" in t for t in said))


class RunToolsOnlyTest(RunCase, unittest.TestCase):
    """start_run checks only the tools the run's tests can use; the rest are not usable and say why."""

    def setUp(self):
        from tests.tools import fakes

        self.extra = {"tilt": fakes.FakeTool("tilt", "handling", ["S1", "S1'"]),
                      "frame_shell": fakes.FakeTool("frame_shell", "strength", ["R1"])}
        real_shelf = fakes.shelf

        def shelf(*a, **kw):
            return {**real_shelf(*a, **kw), **self.extra}

        with mock.patch.object(fakes, "shelf", shelf):
            super().setUp()

    def test_only_the_tools_of_the_runs_tests_are_checked(self):
        checked = [e["data"]["name"] for e in self.ctx.store.events() if e["kind"] == "tool.checked"]
        self.assertEqual(checked, ["step_climb", "frame_static", "frame_shell"])  # R1 (P1, R1); tilt is S1's
        said = [e["text"] for e in self.ctx.store.events() if e["kind"] == "agent.said"]
        self.assertIn("この実行の試験 (P1・R1) には使わないので確かめない道具: tilt", said)
        self.assertEqual(self.extra["tilt"].runs, [])
        line = next(t for t in list_tools(self.ctx) if t["name"] == "tilt")
        self.assertFalse(line["usable"])
        self.assertEqual(line["not_checked"], "この実行の試験には使わないので確かめていない")
        self.assertEqual(list_tools(self.ctx, {"tool": "tilt"}), [line])
        self.assertEqual([t["name"] for t in list_tools(self.ctx, {"test_id": "S1"})], ["tilt"])
        load, _ = self.predict_p1()
        args = {"tool": "tilt", "load_case_id": "P1-step100", "prediction_ids": [load["prediction_id"]]}
        d = rules.check_call(self.ctx, "analyze", args)
        self.assertEqual(d.rule, rules.HK2)
        self.assertIn("この実行の試験には使わないので確かめていない", d.reason)
        with self.assertRaises(ToolError):
            analyze(self.ctx, args)
        self.assertEqual(self.extra["tilt"].runs, [])

    def test_the_tests_of_a_run(self):
        from hiraku.agent.session import run_test_ids
        from hiraku.tools.shelf import test_family, tools_for_tests

        self.assertEqual([test_family(t) for t in ("S1'", "R1s", "S4b", "P10")], ["S1", "R1", "S4", "P10"])
        self.assertEqual(tools_for_tests(self.ctx.tools, ["R1s"]), ["frame_static", "frame_shell"])
        self.assertEqual(tools_for_tests(self.ctx.tools, None), list(self.ctx.tools))
        self.assertEqual(run_test_ids({"tests": ["S1"]}, self.ctx.load_cases), ["P1", "R1"])  # the load cases first
        self.assertEqual(run_test_ids({"tests": ["R1"]}, []), ["P1", "R1"])  # before version 1: and what feeds them
        self.assertIsNone(run_test_ids({}, []))  # not fixed yet: every tool


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
        # a second test of the domain without a verdict: the tile stays red and counts it,
        # but the run is not judged until that test has a verdict too (HK4 goes by load case)
        self.ctx.load_cases.append(dict(self.ctx.load_case("R1-step100"), load_case_id="R1-step250-beyond"))
        tile = compute_judgement(self.ctx, "strength")
        self.assertEqual(tile["state"], "red")
        self.assertTrue(tile["summary"].endswith("。判定の無い試験 1: R1-step250-beyond"), tile["summary"])
        self.assertIn("R1-step250-beyond", rules.stop_reason(self.ctx))


class JointWallInputsTest(RunCase, unittest.TestCase):
    def test_the_section_gives_the_analysis_builders_inputs_for_the_load_case(self):
        from hiraku.catalog.builders.analysis import joint_wall_calc

        out = get_context(self.ctx, {"sections": ["joint_wall_inputs"], "load_case_id": "R1-step100"})
        got = out["joint_wall_inputs"]
        self.assertEqual(got["formula_id"], "frame_static.joint_wall_bending")
        self.assertEqual(got["inputs"], joint_wall_calc(self.ctx.store.design(), self.ctx.load_case("R1-step100")))
        units = {k: v["unit"] for k, v in got["inputs"].items()}
        self.assertEqual(units, {"M": "N*mm", "b": "mm", "h": "mm", "t": "mm", "b_c": "mm", "h_c": "mm", "t_c": "mm",
                                 "leg": "mm", "E": "MPa", "nu": "1", "through": "1", "open_end": "1"})
        self.assertNotIn("joint_wall_inputs", get_context(self.ctx))  # only on request

    def test_the_inputs_record_the_hot_spot_prediction_with_the_real_formula(self):
        from hiraku.solvers import formula_registry

        got = get_context(self.ctx, {"sections": ["joint_wall_inputs"], "load_case_id": "R1-step100"})["joint_wall_inputs"]
        self.ctx.formulas = formula_registry()
        pred = record_prediction(self.ctx, {"load_case_id": "R1-step100", "quantity": "max_hot_spot_stress",
                                            "formula_id": got["formula_id"], "output": "sigma", "inputs": got["inputs"],
                                            "assumptions": ["test"]}, "cae.strength")
        self.assertEqual(pred["unit"], "MPa")
        self.assertGreater(pred["value"], 0.0)

    def test_refusals_say_why(self):
        from hiraku.catalog.builders.common import BuildError

        with self.assertRaisesRegex(ToolError, "load_case_id"):
            get_context(self.ctx, {"sections": ["joint_wall_inputs"]})
        with self.assertRaisesRegex(ToolError, "荷重条件 'R9'"):
            get_context(self.ctx, {"sections": ["joint_wall_inputs"], "load_case_id": "R9"})
        why = "no ladder joint for the joint wall hand calculation: fewer than two cross members"
        with mock.patch("hiraku.catalog.builders.analysis.joint_wall_calc", side_effect=BuildError(why)):
            with self.assertRaises(ToolError) as cm:
                get_context(self.ctx, {"sections": ["joint_wall_inputs"], "load_case_id": "R1-step100"})
        self.assertIn("出せません", str(cm.exception))
        self.assertIn(why, str(cm.exception))


class FormulaInputsTest(RunCase, unittest.TestCase):
    """get_context's formula_inputs: the plan's formulas per load case with the inputs the scripted
    run's own readers and refs work out (hiraku.tools.plan)."""

    def _entries(self, load_case_id):
        out = get_context(self.ctx, {"sections": ["formula_inputs"], "load_case_id": load_case_id})
        cases = out["formula_inputs"]["load_cases"]
        self.assertEqual([c["load_case_id"] for c in cases], [load_case_id])
        return cases[0]["formulas"]

    def test_upstream_inputs_are_missing_until_the_upstream_case_matches_then_come_from_its_analysis(self):
        from hiraku.tools import plan

        first = next(e for e in self._entries("R1-step100") if e["formula_id"] == "frame_static.rail_step_bending")
        self.assertEqual(first["role"], "criterion")
        self.assertIn("P1-step100", first["missing"]["F"])
        self.assertEqual(first["inputs"]["L"]["from"], "design_summary の wheelbase")
        ana, _, _ = self.run_p1()
        got = next(e for e in self._entries("R1-step100") if e["formula_id"] == "frame_static.rail_step_bending")
        self.assertNotIn("missing", got)
        force = next(r for r in ana["results"] if r["quantity"] == "front_wheel_force")
        self.assertEqual(got["inputs"]["F"]["value"], force["value"])
        self.assertIn(ana["analysis_id"], got["inputs"]["F"]["from"])
        # the same values the scripted run resolves
        step = next(s for s in plan.compose_plan(self.ctx)["steps"] if s["load_case"] == "R1-step100")
        spec = step["predictions"][0]
        lc = self.ctx.load_case("R1-step100")
        for name, ref in spec["inputs"].items():
            want = plan.resolve(self.ctx, ref, lc, plan.given_inputs(self.ctx, spec, lc))
            self.assertEqual({k: got["inputs"][name][k] for k in ("value", "unit")}, want, name)
        self.assertNotIn("formula_inputs", get_context(self.ctx))  # only on request

    def test_a_reader_that_does_not_apply_refuses_with_its_reason(self):
        from hiraku.catalog.builders.common import BuildError
        from hiraku.tools import plan

        why = "not a ladder frame: no cross members"

        def reader(design, load_case):
            raise BuildError(why)

        own = {"steps": [{"load_case": "R1-step100", "tool": "frame_static", "predictions": [
            {"quantity": "max_stress", "formula_id": "frame_static.rail_bending", "output": "sigma",
             "inputs": {"L": "from.L"}, "inputs_from": reader}]}]}
        with mock.patch.object(plan, "plan_of", return_value=own):
            got = self._entries("R1-step100")[0]
        self.assertIn(why, got["refused"])
        self.assertIn("当てはまらない", got["refused"])
        self.assertNotIn("inputs", got)

    def test_a_test_with_no_tool_gets_the_builders_hand_calculation(self):
        """A test no shelf tool analyses (R4: its tool is made in the run) has no plan step; its
        predictions kept by the builders (TOOLLESS_PREDICTIONS) are listed with their inputs read by code."""
        import types

        from hiraku.tools import plan

        def reader(design, load_case):
            return {"L": {"value": 1800.0, "unit": "mm"}}

        kept = types.SimpleNamespace(PREDICTIONS=[
            {"quantity": "max_stress", "formula_id": "frame_static.rail_bending", "output": "sigma",
             "inputs": {"L": "from.L", "F": "pred.front_wheel_force"}, "inputs_from": reader}])
        own = {"steps": [s for s in plan.plan_of(self.ctx)["steps"] if s["load_case"] != "R1-step100"]}
        with mock.patch.object(plan, "plan_of", return_value=own), \
                mock.patch.object(plan, "external_builders", return_value=({}, {}, {})), \
                mock.patch.dict(plan.TOOLLESS_PREDICTIONS, {"R1": ("kept_stand_in", "PREDICTIONS")}), \
                mock.patch.object(plan.importlib, "import_module", return_value=kept):
            out = get_context(self.ctx, {"sections": ["formula_inputs"], "load_case_id": "R1-step100"})
        (case,) = out["formula_inputs"]["load_cases"]
        self.assertEqual(case["note"], plan.TOOLLESS_NOTE)
        (entry,) = case["formulas"]
        self.assertIsNone(entry["tool"])
        self.assertEqual((entry["formula_id"], entry["role"]), ("frame_static.rail_bending", "criterion"))
        self.assertEqual(entry["inputs"]["L"]["value"], 1800.0)
        self.assertIn("F", entry["missing"])  # a prediction not recorded yet

    def test_role_facing_text_points_at_the_section_not_at_python(self):
        from hiraku.tools.functions import CODE_CALL_FOR_ROLE, for_role

        note = "inputs from hiraku.solvers.k0_handcalc.frame_inputs.joint_wall_inputs(design, loading=..., step_mm=<a (b)>): x"
        self.assertEqual(for_role({"note": [note]}), {"note": [f"inputs from {CODE_CALL_FOR_ROLE}: x"]})

    def test_refused_before_a_design(self):
        with mock.patch.object(type(self.ctx), "version", new_callable=mock.PropertyMock, return_value=0):
            with self.assertRaisesRegex(ToolError, "設計の版"):
                get_context(self.ctx, {"sections": ["formula_inputs"]})


class DesignContextTest(RunCase, unittest.TestCase):
    """get_context's design: an overview, pages of its parts, and what changed after revise_design."""

    def test_the_design_section_is_an_overview_and_the_parts_come_in_pages(self):
        design = self.ctx.store.design()
        got = get_context(self.ctx, {"sections": ["design"]})["design"]
        self.assertEqual(got["counts"]["members"], len(design["members"]))
        self.assertEqual(got["ids"]["parts"], [p["id"] for p in design["parts"]])
        self.assertEqual(got["variables"]["track"], {k: design["variables"]["track"][k] for k in ("value", "unit")})
        self.assertNotIn("nodes", got)
        page = get_context(self.ctx, {"sections": ["design"], "design_part": "members", "offset": 1, "limit": 2})["design"]
        self.assertEqual(page["items"], design["members"][1:3])
        self.assertEqual((page["total"], page.get("next_offset")), (len(design["members"]), 3 if len(design["members"]) > 3 else None))
        last = get_context(self.ctx, {"sections": ["design"], "design_part": "members",
                                      "offset": len(design["members"]) - 1})["design"]
        self.assertNotIn("next_offset", last)
        with self.assertRaisesRegex(ToolError, "design_part"):
            get_context(self.ctx, {"sections": ["design"], "design_part": "wheels"})

    def test_after_revise_design_the_changes_come_first(self):
        from hiraku.tools.functions import revise_design

        self.assertNotIn("design_changes", get_context(self.ctx))
        old = dict(self.ctx.store.design()["variables"]["track"])
        revise_design(self.ctx, {"changes": {"track": {"value": 1400.0, "unit": "mm"}}, "reason": "wider"}, "designer")
        out = get_context(self.ctx)
        self.assertEqual(list(out)[:5], ["run_id", "want", "product", "design_version", "design_changes"])
        ch = out["design_changes"]
        self.assertEqual((ch["from_version"], ch["to_version"]), (1, 2))
        self.assertEqual(ch["changed"], [{"variable": "track", "old": {"value": old["value"], "unit": old["unit"]},
                                          "new": {"value": 1400.0, "unit": "mm"}}])


class UnpredictableCaseTest(RunCase, unittest.TestCase):
    """HK4's stop text for a load case with no usable tool, and one no K0 formula can predict."""

    def add_case(self, lc_id, test_id, quantity):
        lc = dict(self.ctx.load_case("R1-step100"), load_case_id=lc_id, test_id=test_id,
                  criterion=dict(self.ctx.load_case("R1-step100")["criterion"], quantity=quantity))
        self.ctx.load_cases.append(lc)

    def test_no_usable_tool_names_make_tool(self):
        self.add_case("R4-x", "R4", "max_stress")  # predictable (frame_static pairs it), but no tool for R4
        self.assertEqual(rules.unpredictable_cases(self.ctx), {})
        reason = rules.stop_reason(self.ctx)
        part = reason[reason.index("R4-x"):]
        self.assertIn("棚に R4 を解析できる使える道具がありません", part)
        self.assertIn("get_tool_template", part)
        self.assertIn("make_tool", part)
        self.assertNotIn("棚に R1", reason)  # frame_static is on the shelf for R1

    def test_a_case_no_formula_predicts_does_not_hold_the_stop(self):
        from hiraku.agent.runner import end_status

        p1, _, _ = self.run_p1()
        r1 = self.predict_r1({"value": p1["results"][1]["value"], "unit": "N"})  # front_wheel_force
        ana = analyze(self.ctx, {"tool": "frame_static", "load_case_id": "R1-step100",
                                 "prediction_ids": [r1["prediction_id"]]})
        compare(self.ctx, {"prediction_id": r1["prediction_id"], "analysis_id": ana["analysis_id"]})
        self.assertIsNone(rules.stop_reason(self.ctx))  # judged
        self.add_case("R4-rops", "R4", "rops_occupant_clearance")
        self.assertEqual(rules.unpredictable_cases(self.ctx), {"R4-rops": "rops_occupant_clearance"})
        self.assertIsNone(rules.stop_reason(self.ctx))  # HK4 does not hold the stop for it
        self.assertEqual(rules.holding(self.ctx), [])
        reason = rules.unjudged_reason(self.ctx)
        self.assertIn("R4-rops (rops_occupant_clearance)", reason)
        self.assertIn("この実行では判定できません", reason)
        status, why, code = end_status(self.ctx)
        self.assertEqual((status, code), ("stopped", "not_judged"))
        self.assertIn("R4-rops", why)
        # a prediction of that quantity recorded in the run (a formula did give it): judged as usual again
        rec =dict(r1, prediction_id=self.ctx.store.next_id("prediction"), load_case_id="R4-rops",
                   quantity="rops_occupant_clearance", unit="mm")
        self.ctx.store.add_record("prediction", rec, design_version=self.ctx.version, agent="cae.strength",
                                  domain="strength")
        self.assertEqual(rules.unpredictable_cases(self.ctx), {})
        self.assertIn("R4-rops", rules.stop_reason(self.ctx))


if __name__ == "__main__":
    unittest.main()
