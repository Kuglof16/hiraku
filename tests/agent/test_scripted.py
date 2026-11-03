import copy
import json
import unittest
from unittest import mock

from hiraku.agent.scripted import ScriptError, _reader, has_plan, load_plan, resolve, run_scripted
from hiraku.tools.summary import design_summary
from tests.tools.helpers import RunCase


def denied_rules(ctx):
    return [e["data"]["rule"] for e in ctx.store.events() if e["kind"] == "hook.denied"]


def comparisons(ctx, load_case_id):
    return [c for c in ctx.store.records("comparison") if ctx.prediction(c["prediction_id"])["load_case_id"] == load_case_id]


def cause_of(ctx, comparison):
    return next((c for c in ctx.store.records("cause") if c["comparison_id"] == comparison["comparison_id"]), None)


def r1_second(plan):
    """The R1 step's retry (the rail-joint prediction)."""
    return plan["steps"][1]["expected_mismatch"]["retry"]


# a third R1 prediction: the beam formula, which the stand-in frame tool agrees with
BEAM_R1 = {"predictions": [{
    "quantity": "max_stress", "formula_id": "frame_static.rail_bending", "output": "sigma", "assumptions": [],
    "inputs": {"F": "ana.P1-step100.front_wheel_force", "L": "summary.wheelbase", "b": "summary.rail_section.b",
               "h": "summary.rail_section.h", "t": "summary.rail_section.t"},
}]}


def plan_without_safe_side(third_prediction=False):
    """The offroad plan with the second R1 cause not judged safe side; optionally a third R1 prediction."""
    plan = copy.deepcopy(load_plan("offroad-4w"))
    em = r1_second(plan)["expected_mismatch"]
    del em["safe_side"], em["safe_side_reason"]
    if third_prediction:
        em["retry"] = copy.deepcopy(BEAM_R1)
    return plan


class ScriptedRunTest(RunCase, unittest.TestCase):
    # R1 analyses come out as the stand-in's beam formula times 1.5 (+47 % against rail_step_bending,
    # like the real tool's +52 %), then 1.8 (-19 % against the stand-in rail-joint formula, like the
    # real -21 %), then 1.03 (a third prediction with the beam formula matches).
    frame_factor = (1.5, 1.8, 1.03)

    def test_offroad_plan(self):
        self.assertEqual(run_scripted(self.ctx), "done")
        kinds = self.kinds()
        self.assertEqual(kinds[0], "run.started")
        self.assertEqual(kinds[-1], "run.finished")
        self.assertEqual(denied_rules(self.ctx), ["HK1_prediction_first", "HK3_cause_before_continue"])
        self.assertLess(kinds.index("hook.denied"), kinds.index("prediction.recorded"))
        denied = next(e for e in self.ctx.store.events() if e["kind"] == "hook.denied")
        self.assertTrue(denied["data"]["scripted"])

        # P1: the lever rule mismatches, HK3 stops the next compare, the plan's cause is written,
        # the pushed-wheel formula matches and resolves the cause
        p1 = comparisons(self.ctx, "P1-step100")
        self.assertEqual([(c["quantity"], c["status"]) for c in p1],
                         [("front_wheel_load", "mismatch"), ("front_wheel_load", "match"), ("front_wheel_force", "match")])
        self.assertEqual(self.ctx.prediction(p1[1]["prediction_id"])["formula_id"], "step_climb.pushed_wheel_load")
        cause = cause_of(self.ctx, p1[0])
        self.assertTrue(cause["resolved"])
        self.assertEqual(cause["resolved_by"], p1[1]["comparison_id"])
        self.assertIn("偶力", cause["cause"])
        self.assertEqual(self.ctx.store.get("comparison", p1[0]["comparison_id"])["cause_id"], cause["cause_id"])

        # R1: rail_step_bending mismatches (analysis larger), the plan's cause is written; the
        # rail-joint formula mismatches the other way (hand calculation larger), its cause is
        # written as safe side, and the verdict comes from that prediction
        r1 = comparisons(self.ctx, "R1-step100")
        self.assertEqual([self.ctx.prediction(c["prediction_id"])["formula_id"] for c in r1],
                         ["frame_static.rail_step_bending", "frame_static.rail_joint_stress"])
        self.assertEqual([c["status"] for c in r1], ["mismatch", "mismatch"])
        self.assertGreater(r1[0]["diff_rel"], 0.15)
        self.assertLess(r1[1]["diff_rel"], -0.15)
        causes = [cause_of(self.ctx, c) for c in r1]
        # resolved_by names only a later match; the safe-side verdict settles the earlier cause for the tile
        self.assertEqual([c["resolved"] for c in causes], [False, False])
        from hiraku.tools.judge import held_cases

        self.assertNotIn("R1-step100", held_cases(self.ctx, "strength", self.ctx.version))
        self.assertIn("張り出し", causes[0]["cause"])
        self.assertIn("rail_joint_stress", causes[0]["fix"])
        self.assertNotIn("safe_side", causes[0])
        self.assertIn("隣の継ぎ手", causes[1]["cause"])
        self.assertTrue(causes[1]["safe_side"])
        self.assertIn("上から押さえている", causes[1]["safe_side_reason"])
        (ver,) = self.ctx.store.records("verdict")
        self.assertEqual((ver["basis"], ver["value_from"], ver["cause_id"], ver["comparison_id"]),
                         ("explained_safe_side", "prediction", causes[1]["cause_id"], r1[1]["comparison_id"]))
        self.assertEqual(ver["value"], r1[1]["predicted"])
        self.assertTrue(ver["pass"])
        self.assertEqual(self.ctx.store.judgements()[0]["state"], "green")
        self.assertIsNone(self.ctx.project.get("stop_reason"))
        said = [e["text"] for e in self.ctx.store.events() if e["kind"] == "agent.said"]
        self.assertTrue(any(t.startswith("安全側の食い違いとして") and "合格" in t for t in said))
        self.assert_events_fit()

    def test_a_fit_driver_revision_takes_the_values_found_by_code(self):
        from unittest import mock

        from hiraku import designgen
        from hiraku.agent import scripted
        from hiraku.designgen import driver_fit

        found = {"changes": {"steering_x": {"value": -509.5, "unit": "mm"}, "seat_height": {"value": 317.0, "unit": "mm"}},
                 "smallest": {"name": "knee_room", "value": 12.0, "unit": "mm", "where": "steering_column"}}
        with mock.patch.object(designgen, "regenerate", return_value={"design": "with track"}) as regen,                 mock.patch.object(driver_fit, "best_driver_place", return_value=found) as fit:
            changes, reason = scripted.fit_driver(self.ctx, {"track": {"value": 1220, "unit": "mm"},
                                                             "steering_x": {"value": -200, "unit": "mm"}}, "H3: 膝", 5)
        self.assertEqual(fit.call_args.args[0], {"design": "with track"})  # measured on the design with the plan's changes
        self.assertEqual(fit.call_args.kwargs["time_limit_s"], 5.0)
        self.assertEqual(fit.call_args.kwargs["max_evals"], scripted.FIT_EVALS)  # a fixed count by default
        self.assertEqual(regen.call_args.args[1]["track"]["value"], 1220)
        self.assertEqual(changes["steering_x"]["value"], -509.5)  # the fit wins for its variables
        self.assertEqual(changes["track"]["value"], 1220)
        self.assertIn("driver_fit", reason)
        self.assertIn("knee_room 12.0 mm", reason)

    def test_plan_notes_are_said_and_reported(self):
        plan = copy.deepcopy(load_plan("offroad-4w"))
        plan["notes"] = ["R4 は回していません"]
        self.assertEqual(run_scripted(self.ctx, plan), "done")
        said = [json.dumps(e, ensure_ascii=False) for e in self.ctx.store.events() if e["kind"] == "agent.said"]
        self.assertTrue(any("R4 は回していません" in t for t in said))
        report = next(e for e in self.ctx.store.events() if e["kind"] == "report.written")
        self.assertIn("R4 は回していません", report["data"]["summary"])

    def test_rail_joint_inputs_come_from_the_design(self):
        run_scripted(self.ctx)
        pred = next(p for p in self.ctx.store.records("prediction") if p["formula_id"] == "frame_static.rail_joint_stress")
        joint = design_summary(self.ctx.store.design())["loadings"]["two_cargo"]["rail_joint"]
        p1 = next(a for a in self.ctx.store.records("analysis") if a["load_case_id"] == "P1-step100")
        self.assertEqual(joint["support"], "RL")
        rear_load = next(r for r in p1["results"] if r["quantity"] == "wheel_load_RL")
        self.assertEqual(pred["inputs"]["W"], {"value": rear_load["value"], "unit": "N"})
        for name in ("M_oh", "z", "e", "L1", "L_c", "E", "nu", "b", "h", "t", "b_c", "h_c", "t_c"):
            self.assertEqual(pred["inputs"][name], joint[name], name)

    def test_without_safe_side_or_retry_the_run_stops(self):
        self.assertEqual(run_scripted(self.ctx, plan_without_safe_side()), "stopped")
        r1 = comparisons(self.ctx, "R1-step100")
        self.assertEqual([c["status"] for c in r1], ["mismatch", "mismatch"])
        self.assertNotIn("safe_side", cause_of(self.ctx, r1[1]))
        self.assertIn(f"no next prediction after {r1[1]['comparison_id']}", self.ctx.project["stop_reason"])
        self.assertEqual(self.ctx.store.records("verdict"), [])
        self.assertEqual(self.ctx.store.judgements()[0]["state"], "pending")
        self.assert_events_fit()

    def test_third_r1_prediction_reaches_a_verdict(self):
        self.assertEqual(run_scripted(self.ctx, plan_without_safe_side(third_prediction=True)), "done")
        r1 = comparisons(self.ctx, "R1-step100")
        self.assertEqual([c["status"] for c in r1], ["mismatch", "mismatch", "match"])
        self.assertEqual([cause_of(self.ctx, c)["resolved_by"] for c in r1[:2]], [r1[2]["comparison_id"]] * 2)
        (ver,) = self.ctx.store.records("verdict")
        self.assertEqual((ver["basis"], ver["comparison_id"]), ("match", r1[2]["comparison_id"]))
        self.assertEqual(self.ctx.store.judgements()[0]["state"], "green")
        self.assertEqual(self.ctx.project["status"], "done")
        self.assert_events_fit()

    def test_safe_side_and_retry_together_are_a_plan_error(self):
        plan = copy.deepcopy(load_plan("offroad-4w"))
        r1_second(plan)["expected_mismatch"]["retry"] = copy.deepcopy(BEAM_R1)
        self.assertEqual(run_scripted(self.ctx, plan), "stopped")
        self.assertIn("both safe_side and a retry", self.ctx.project["stop_reason"])

    def test_end_records_the_cause_and_ends_the_step(self):
        plan = copy.deepcopy(load_plan("offroad-4w"))
        em = plan["steps"][0]["expected_mismatch"]  # P1 has no criterion
        del em["retry"]
        em.update(end=True, end_say="原因を記録して、この手順は終えます (試験)")
        plan["steps"] = plan["steps"][:1]
        self.assertEqual(run_scripted(self.ctx, plan), "stopped")
        (p1,) = comparisons(self.ctx, "P1-step100")
        self.assertEqual((p1["quantity"], p1["status"]), ("front_wheel_load", "mismatch"))
        self.assertFalse(cause_of(self.ctx, p1)["resolved"])
        said = [e["text"] for e in self.ctx.store.events() if e["kind"] == "agent.said"]
        self.assertIn("原因を記録して、この手順は終えます (試験)", said)
        # the run went on: R1 had no step (P1 has no matched analysis for it), so strength is left pending
        self.assertNotIn("no next prediction", self.ctx.project["stop_reason"])
        self.assertIn("R1-step100", self.ctx.project["stop_reason"])
        self.assert_events_fit()

    def test_end_on_a_judged_quantity_leaves_the_test_without_a_verdict(self):
        plan = plan_without_safe_side()
        r1_second(plan)["expected_mismatch"]["end"] = True  # max_stress is R1's criterion
        self.assertEqual(run_scripted(self.ctx, plan), "stopped")
        self.assertEqual(self.ctx.store.records("verdict"), [])
        self.assertEqual(self.ctx.store.judgements()[0]["state"], "pending")
        self.assertEqual(self.ctx.store.events()[-1]["data"]["stop_code"], "not_judged")  # HK4 at the end
        self.assertNotIn("no next prediction", self.ctx.project["stop_reason"])
        said = [e["text"] for e in self.ctx.store.events() if e["kind"] == "agent.said"]
        self.assertTrue(any("判定の無いまま次へ進みます" in t for t in said))

    def test_end_and_retry_together_are_a_plan_error(self):
        plan = copy.deepcopy(load_plan("offroad-4w"))
        plan["steps"][0]["expected_mismatch"]["end"] = True
        self.assertEqual(run_scripted(self.ctx, plan), "stopped")
        self.assertIn("both end and a retry", self.ctx.project["stop_reason"])


class ScriptedUnsafeSideTest(RunCase, unittest.TestCase):
    # The second R1 analysis comes out 2.7 x the beam formula: above the rail-joint prediction
    # (+21 %), so the plan's safe-side judgement does not fit the numbers and the tool refuses it.
    frame_factor = (1.5, 2.7)

    def test_tool_refuses_and_the_run_stops(self):
        self.assertEqual(run_scripted(self.ctx), "stopped")
        r1 = comparisons(self.ctx, "R1-step100")
        self.assertEqual([c["status"] for c in r1], ["mismatch", "mismatch"])
        self.assertGreater(r1[1]["diff_rel"], 0.15)
        self.assertIsNone(cause_of(self.ctx, r1[1]))  # refused, so nothing written
        self.assertIn("安全側とはみなせません", self.ctx.project["stop_reason"])
        self.assertEqual(self.ctx.store.records("verdict"), [])
        self.assertEqual(self.ctx.store.judgements()[0]["state"], "pending")
        self.assert_events_fit()

    def test_resolve_refs(self):
        lc = self.ctx.load_case("P1-step100")
        frame = (4 * 464 * 900 + 3 * 304 * 800) * 7850e-9  # the example's frame members, steel
        mass = 60.0 + frame + 2 * 76.6 + 150.0
        self.assertEqual(resolve(self.ctx, "lc.step_height", lc), {"value": 100.0, "unit": "mm"})
        self.assertEqual(resolve(self.ctx, "req.Q33", lc), {"value": 76.6, "unit": "kg"})
        self.assertAlmostEqual(resolve(self.ctx, "load.mass", lc)["value"], mass)
        self.assertAlmostEqual(resolve(self.ctx, "load.weight", lc)["value"], mass * 9.80665)
        self.assertGreater(resolve(self.ctx, "load.cg_from_front_axle", lc)["value"], 0.0)
        self.assertEqual(resolve(self.ctx, "summary.front_wheel_radius", lc), {"value": 300.0, "unit": "mm"})
        self.assertEqual(resolve(self.ctx, "summary.rail_section.h", lc), {"value": 80.0, "unit": "mm"})
        self.assertEqual(resolve(self.ctx, "summary.rail_section.z", lc), {"value": 350.0, "unit": "mm"})
        self.assertEqual(resolve(self.ctx, "design.support.FL.radius_mm", lc), {"value": 300.0, "unit": "mm"})
        self.assertEqual(resolve(self.ctx, "design.section.rt40x80x2.h", lc), {"value": 80.0, "unit": "mm"})
        self.assertEqual(resolve(self.ctx, "design.var.track", lc), {"value": 1300.0, "unit": "mm"})
        self.assertEqual(resolve(self.ctx, "summary.member_length.rail_L0", lc), {"value": 900.0, "unit": "mm"})
        self.assertEqual(resolve(self.ctx, {"value": 1, "unit": "N"}, lc), {"value": 1.0, "unit": "N"})
        # the rail joint behind the example's rear axle: rail ahead 900 mm, cross member 800 mm,
        # wheel 250 mm outside the rail, rail centre 350 mm above the ground, nothing behind the axle
        r1 = self.ctx.load_case("R1-step100")
        self.assertEqual(resolve(self.ctx, "load.rail_joint.L1", r1), {"value": 900.0, "unit": "mm"})
        self.assertEqual(resolve(self.ctx, "load.rail_joint.L_c", r1), {"value": 800.0, "unit": "mm"})
        self.assertEqual(resolve(self.ctx, "load.rail_joint.e", r1), {"value": 250.0, "unit": "mm"})
        self.assertEqual(resolve(self.ctx, "load.rail_joint.z", r1), {"value": 350.0, "unit": "mm"})
        self.assertEqual(resolve(self.ctx, "load.rail_joint.M_oh", r1), {"value": 0.0, "unit": "N*mm"})
        self.assertEqual(resolve(self.ctx, "design.support.{load.rail_joint.support}.radius_mm", r1),
                         {"value": 300.0, "unit": "mm"})
        for bad in ("pred.nothing", "ana.P1-step100.front_wheel_force", "nope.x", "design.support.ZZ.radius_mm",
                    "load.rail_joint.support", "design.support.{lc.step_height}.radius_mm", "lc.{nope.x}"):
            with self.subTest(bad=bad):
                with self.assertRaises(ScriptError):
                    resolve(self.ctx, bad, lc)

    def test_pred_ref_reads_the_load_case_being_run(self):
        p1 = self.ctx.load_case("P1-step100")
        load, _ = self.predict_p1()
        self.assertEqual(resolve(self.ctx, "pred.front_wheel_load", p1)["value"], load["value"])
        # a newer prediction of the same quantity for another load case does not count for P1
        self.ctx.load_cases.append(dict(p1, load_case_id="P1-step250-beyond"))
        other = self.ctx.store.add_record("prediction", dict(load, prediction_id=self.ctx.store.next_id("prediction"),
                                                             load_case_id="P1-step250-beyond", value=load["value"] * 2),
                                          design_version=self.ctx.version, agent="cae.strength", domain="strength")
        self.assertEqual(resolve(self.ctx, "pred.front_wheel_load", p1)["value"], load["value"])
        self.assertEqual(resolve(self.ctx, "pred.front_wheel_load", self.ctx.load_case("P1-step250-beyond"))["value"],
                         other["value"])

    def test_plan_names_no_member_or_section(self):
        plan = load_plan("offroad-4w")
        self.assertEqual([(s["load_case"], s["tool"]) for s in plan["steps"]],
                         [("P1-step100", "step_climb"), ("R1-step100", "frame_static"),
                          ("R1s-step100", "frame_shell"),  # only where R1s is built
                          ("P1-step100", "step_climb_dynamic"),  # after R1: R1 reads step_climb's wheel loads
                          ("P1-step250-beyond", "step_climb"), ("R1-step250-beyond", "frame_static"),
                          ("P1-step250-beyond", "step_climb_dynamic"),
                          ("P1-step330-beyond", "step_climb_dynamic")])  # higher than the wheel: moving only
        refs, chain = [], []
        for step in plan["steps"]:
            node = step
            while node is not None:
                refs += [r for p in node["predictions"] for r in p["inputs"].values() if isinstance(r, str)]
                em = node.get("expected_mismatch")
                if em:
                    chain.append(em)
                node = em.get("retry") if em else None
        self.assertIn("ana.P1.wheel_load_{load.rail_joint.support}", refs)
        for r in refs:
            self.assertFalse(r.startswith(("design.section.", "summary.member_length.", "design.support.")), r)
        self.assertEqual(len(chain), 7)  # at each step height: P1 once, R1 twice; P1 driven at 250 mm once
        for em in chain:
            self.assertTrue(em["source"] and em["cause"] and em["fix"])


class ScriptedNoExpectedMismatchTest(RunCase, unittest.TestCase):
    # The stand-in tool returns 1.186 x the pushed-wheel load: about what the lever rule gives,
    # so P1's expected mismatch does not happen and its retry is not used.
    step_factor = 1.186

    def test_retry_only_when_the_mismatch_happens(self):
        run_scripted(self.ctx)
        self.assertEqual([c["status"] for c in comparisons(self.ctx, "P1-step100")], ["match", "match"])
        self.assertEqual(denied_rules(self.ctx), ["HK1_prediction_first"])
        formulas = {p["formula_id"] for p in self.ctx.store.records("prediction")}
        self.assertNotIn("step_climb.pushed_wheel_load", formulas)
        self.assertEqual([cause_of(self.ctx, c) for c in comparisons(self.ctx, "P1-step100")], [None, None])
        self.assert_events_fit()


class ScriptedUnexpectedMismatchTest(RunCase, unittest.TestCase):
    # 1.5 x the pushed-wheel load: the lever rule mismatches as the plan expects, and then the
    # plan's second prediction mismatches too, which the plan has no cause for.
    step_factor = 1.5

    def test_stops_without_inventing_a_cause(self):
        self.assertEqual(run_scripted(self.ctx), "stopped")
        self.assertIn("the plan has none for it", self.ctx.project["stop_reason"])
        p1 = comparisons(self.ctx, "P1-step100")
        self.assertEqual([c["status"] for c in p1], ["mismatch", "mismatch"])
        self.assertIsNotNone(cause_of(self.ctx, p1[0]))  # the plan's
        self.assertIsNone(cause_of(self.ctx, p1[1]))  # nobody worked this one out
        self.assertEqual(len(self.ctx.store.records("cause")), 1)
        self.assertEqual(comparisons(self.ctx, "R1-step100"), [])
        self.assert_events_fit()


class ScriptedUnusableToolTest(RunCase, unittest.TestCase):
    frame_checks_pass = False

    def test_hk2_stops_the_script(self):
        self.assertEqual(run_scripted(self.ctx), "stopped")
        self.assertEqual(denied_rules(self.ctx), ["HK1_prediction_first", "HK3_cause_before_continue", "HK2_tool_usable"])
        self.assert_events_fit()


class PlanOfARequirementsFileTest(unittest.TestCase):
    BASE = {"show_hk1": True, "revise": ["r"], "steps": [{"load_case": "P1-step100", "tool": "step_climb", "say": "a"},
                                                         {"load_case": "R1-step100", "tool": "frame_static", "say": "b"}]}

    def plans(self, data):
        return mock.patch("hiraku.tools.plan.read_data", return_value=data)

    def test_a_file_entry_extends_the_products_plan(self):
        own = {"extends": "offroad-4w", "rule_check": True,
               "steps": [{"load_case": "R1-step100", "tool": "frame_static", "say": "B"},
                         {"load_case": "R1-step100", "tool": "frame_shell", "say": "C"}]}
        with self.plans({"offroad-4w": self.BASE, "offroad-4w-n14": own}):
            plan = load_plan("offroad-4w", "offroad-4w-n14")
            self.assertEqual(load_plan("offroad-4w", "offroad-4w-video"), self.BASE)  # no entry: the product's
        self.assertEqual([s["say"] for s in plan["steps"]], ["a", "B", "C"])  # replaced in place, then the new one
        self.assertEqual((plan["show_hk1"], plan["revise"], plan["rule_check"]), (True, ["r"], True))
        self.assertNotIn("extends", plan)

    def test_has_plan(self):
        self.assertTrue(has_plan("offroad-4w"))
        self.assertTrue(has_plan("offroad-4w", "offroad-4w-n14"))
        self.assertFalse(has_plan("serving-robot"))
        self.assertFalse(has_plan("serving-robot", "requirements"))

    def test_inputs_from_by_name_reads_only_the_builders(self):
        from hiraku.catalog.builders import k2_tests

        self.assertIs(_reader("k2_tests.tilt_inputs"), k2_tests.tilt_inputs)
        for bad in ("os.system", "tilt_inputs", "k2_tests.no_such_reader", "../x.y"):
            with self.subTest(name=bad), self.assertRaises(ScriptError):
                _reader(bad)


class ScriptedKnowledgeDropTest(RunCase, unittest.TestCase):
    def test_a_dropped_file_becomes_rules_that_are_checked_on_the_parts(self):
        plan = copy.deepcopy(load_plan("offroad-4w"))
        plan.update(knowledge_drop={"after": "P1-step100", "file": "tests/intake/fixtures/bending-notes.md",
                                    "text": "板金のメモを渡します",
                                    "answer": {"say": "式にできない 2 文は、理由を書いて外します",
                                               "skip": {"c3": "向きは式にできない", "c5": "部品どうしの距離を測る値が設計に無い"}}},
                    rule_check=True, ask_on_hits=True)
        run_scripted(self.ctx, plan)
        kinds = self.kinds()
        dropped = [e for e in self.ctx.store.events()
                   if e["kind"] == "knowledge.imported" and e["data"]["doc"]["origin"] == "dropped"]
        # read from the inbox, then the plan's answer for the two sentences without a formula (as the lead would)
        self.assertEqual([e["data"]["doc"]["filename"] for e in dropped], ["bending-notes.md"] * 2)
        self.assertEqual(sorted(s["reason"] for s in dropped[-1]["data"]["doc"].get("skipped") or []),
                         ["向きは式にできない", "部品どうしの距離を測る値が設計に無い"])
        self.assertTrue((self.ctx.store.path / "knowledge" / "bending-notes.md").is_file())
        self.assertEqual(list((self.ctx.store.path / "inbox").iterdir()), [])
        self.assertIn("板金のメモを渡します", [e.get("text") for e in self.ctx.store.events() if e["kind"] == "user.said"])
        # the rules are checked after the file became rules, once per version
        first_check = kinds.index("rule_check.recorded")
        self.assertLess(kinds.index("knowledge.imported", kinds.index("user.said")), first_check)
        checked = self.ctx.store.records("rule_check")
        self.assertTrue(any(r["rule_id"].startswith(dropped[0]["data"]["doc"]["doc_id"]) for r in checked))
        # a part hit by a rule: the lead asks the person, once, naming the rule and the part from the record
        doc = dropped[0]["data"]["doc"]["doc_id"]
        hits = [r for r in checked if r["hits"]]
        mine = [r for r in hits if r["rule_id"].startswith(doc + "-")]
        asks = [e["text"] for e in self.ctx.store.events() if e["kind"] == "agent.said" and "規則に当たる部品" in e["text"]]
        self.assertEqual(len(asks), 1)
        # the dropped file's rules are the ones asked about when they hit; else every hit
        for r in mine or hits:
            self.assertIn(r["rule_id"], asks[0])
            self.assertIn(r["hits"][0]["part_id"], asks[0])
        if mine:
            self.assertFalse([r["rule_id"] for r in hits if r not in mine and r["rule_id"] in asks[0]])
        self.assert_events_fit()


if __name__ == "__main__":
    unittest.main()
