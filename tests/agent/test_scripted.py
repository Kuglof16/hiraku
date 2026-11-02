import copy
import unittest

from hiraku.agent.scripted import ScriptError, load_plan, resolve, run_scripted
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


def plan_with_third_r1_prediction():
    """The offroad plan with a third R1 prediction the stand-in frame tool agrees with."""
    plan = copy.deepcopy(load_plan("offroad-4w"))
    r1_second(plan)["expected_mismatch"]["retry"] = {"predictions": [{
        "quantity": "max_stress", "formula_id": "frame_static.rail_bending", "output": "sigma", "assumptions": [],
        "inputs": {"F": "ana.P1-step100.front_wheel_force", "L": "summary.wheelbase", "b": "summary.rail_section.b",
                   "h": "summary.rail_section.h", "t": "summary.rail_section.t"},
    }]}
    return plan


class ScriptedRunTest(RunCase, unittest.TestCase):
    # R1 analyses come out as the stand-in's beam formula times 1.5 (+47 % against rail_step_bending,
    # like the real tool's +52 %), then 1.8 (-19 % against the stand-in rail-joint formula, like the
    # real -21 %), then 1.03 (a third prediction with the beam formula matches).
    frame_factor = (1.5, 1.8, 1.03)

    def test_offroad_plan(self):
        self.assertEqual(run_scripted(self.ctx), "stopped")
        kinds = self.kinds()
        self.assertEqual(kinds[0], "run.started")
        self.assertEqual(kinds[-1], "run.finished")
        self.assertEqual(denied_rules(self.ctx), ["H1_prediction_first", "H3_cause_before_continue"])
        self.assertLess(kinds.index("hook.denied"), kinds.index("prediction.recorded"))
        denied = next(e for e in self.ctx.store.events() if e["kind"] == "hook.denied")
        self.assertTrue(denied["data"]["scripted"])

        # P1: the lever rule mismatches, H3 stops the next compare, the plan's cause is written,
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
        # rail-joint formula mismatches the other way, its cause is written, and with nothing
        # decided after that the run stops with R1 unjudged
        r1 = comparisons(self.ctx, "R1-step100")
        self.assertEqual([self.ctx.prediction(c["prediction_id"])["formula_id"] for c in r1],
                         ["frame_static.rail_step_bending", "frame_static.rail_joint_stress"])
        self.assertEqual([c["status"] for c in r1], ["mismatch", "mismatch"])
        self.assertGreater(r1[0]["diff_rel"], 0.15)
        self.assertLess(r1[1]["diff_rel"], -0.15)
        causes = [cause_of(self.ctx, c) for c in r1]
        self.assertEqual([c["resolved"] for c in causes], [False, False])
        self.assertIn("張り出し", causes[0]["cause"])
        self.assertIn("rail_joint_stress", causes[0]["fix"])
        self.assertIn("隣の継ぎ手", causes[1]["cause"])
        self.assertIn(f"no next prediction after {r1[1]['comparison_id']}", self.ctx.project["stop_reason"])
        said = [e["text"] for e in self.ctx.store.events() if e["kind"] == "agent.said"]
        self.assertIn(r1_second(load_plan("offroad-4w"))["expected_mismatch"]["stop"], said)
        self.assertEqual(self.ctx.store.judgements()[0]["state"], "pending")
        self.assert_events_fit()

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

    def test_third_r1_prediction_reaches_a_verdict(self):
        self.assertEqual(run_scripted(self.ctx, plan_with_third_r1_prediction()), "done")
        r1 = comparisons(self.ctx, "R1-step100")
        self.assertEqual([c["status"] for c in r1], ["mismatch", "mismatch", "match"])
        self.assertEqual([cause_of(self.ctx, c)["resolved_by"] for c in r1[:2]], [r1[2]["comparison_id"]] * 2)
        self.assertEqual(self.kinds().count("verdict.recorded"), 1)
        self.assertEqual(self.ctx.store.judgements()[0]["state"], "green")
        self.assertEqual(self.ctx.project["status"], "done")
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

    def test_plan_names_no_member_or_section(self):
        plan = load_plan("offroad-4w")
        self.assertEqual([s["load_case"] for s in plan["steps"]], ["P1-step100", "R1-step100"])
        refs, chain = [], []
        for step in plan["steps"]:
            node = step
            while node is not None:
                refs += [r for p in node["predictions"] for r in p["inputs"].values() if isinstance(r, str)]
                em = node.get("expected_mismatch")
                if em:
                    chain.append(em)
                node = em.get("retry") if em else None
        self.assertIn("ana.P1-step100.wheel_load_{load.rail_joint.support}", refs)
        for r in refs:
            self.assertFalse(r.startswith(("design.section.", "summary.member_length.", "design.support.")), r)
        self.assertEqual(len(chain), 3)  # P1 once, R1 twice
        for em in chain:
            self.assertTrue(em["source"] and em["cause"] and em["fix"])


class ScriptedNoExpectedMismatchTest(RunCase, unittest.TestCase):
    # The stand-in tool returns 1.186 x the pushed-wheel load: about what the lever rule gives,
    # so P1's expected mismatch does not happen and its retry is not used.
    step_factor = 1.186

    def test_retry_only_when_the_mismatch_happens(self):
        run_scripted(self.ctx)
        self.assertEqual([c["status"] for c in comparisons(self.ctx, "P1-step100")], ["match", "match"])
        self.assertEqual(denied_rules(self.ctx), ["H1_prediction_first"])
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

    def test_h2_stops_the_script(self):
        self.assertEqual(run_scripted(self.ctx), "stopped")
        self.assertEqual(denied_rules(self.ctx), ["H1_prediction_first", "H3_cause_before_continue", "H2_tool_usable"])
        self.assert_events_fit()


if __name__ == "__main__":
    unittest.main()
