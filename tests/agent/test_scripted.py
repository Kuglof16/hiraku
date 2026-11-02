import unittest

from hiraku.agent.scripted import ScriptError, load_plan, resolve, run_scripted
from tests.tools.helpers import RunCase


def denied_rules(ctx):
    return [e["data"]["rule"] for e in ctx.store.events() if e["kind"] == "hook.denied"]


class ScriptedRunTest(RunCase, unittest.TestCase):
    def test_runs_to_done(self):
        self.assertEqual(run_scripted(self.ctx), "done")
        kinds = self.kinds()
        self.assertEqual(kinds[0], "run.started")
        self.assertEqual(kinds[-1], "run.finished")
        self.assertEqual(denied_rules(self.ctx), ["H1_prediction_first", "H3_cause_before_continue"])
        self.assertLess(kinds.index("hook.denied"), kinds.index("prediction.recorded"))
        denied = next(e for e in self.ctx.store.events() if e["kind"] == "hook.denied")
        self.assertTrue(denied["data"]["scripted"])

        # P1: the lever rule mismatches, H3 stops the next compare, the plan's cause is written,
        # the pushed-wheel formula matches and resolves the cause
        cmps = self.ctx.store.records("comparison")
        p1 = [c for c in cmps if self.ctx.prediction(c["prediction_id"])["load_case_id"] == "P1-step100"]
        self.assertEqual([(c["quantity"], c["status"]) for c in p1],
                         [("front_wheel_load", "mismatch"), ("front_wheel_load", "match"), ("front_wheel_force", "match")])
        self.assertEqual(self.ctx.prediction(p1[1]["prediction_id"])["formula_id"], "step_climb.pushed_wheel_load")
        (cause,) = self.ctx.store.records("cause")
        self.assertEqual(cause["comparison_id"], p1[0]["comparison_id"])
        self.assertTrue(cause["resolved"])
        self.assertEqual(cause["resolved_by"], p1[1]["comparison_id"])
        self.assertIn("偶力", cause["cause"])
        self.assertEqual(p1[0]["cause_id"], cause["cause_id"])
        self.assertLess(kinds.index("cause.recorded"), kinds.index("verdict.recorded"))

        self.assertEqual(kinds.count("verdict.recorded"), 1)
        self.assertEqual(self.ctx.project["status"], "done")
        self.assertEqual(self.ctx.store.judgements()[0]["state"], "green")
        self.assert_events_fit()

    def test_resolve_refs(self):
        lc = self.ctx.load_case("P1-step100")
        frame = (4 * 464 * 900 + 3 * 304 * 800) * 7850e-9  # the example's frame members, steel
        self.assertEqual(resolve(self.ctx, "lc.step_height", lc), {"value": 100.0, "unit": "mm"})
        self.assertEqual(resolve(self.ctx, "req.Q33", lc), {"value": 76.6, "unit": "kg"})
        self.assertAlmostEqual(resolve(self.ctx, "load.mass", lc)["value"], 60.0 + frame + 2 * 76.6 + 150.0)
        self.assertGreater(resolve(self.ctx, "load.cg_from_front_axle", lc)["value"], 0.0)
        self.assertEqual(resolve(self.ctx, "summary.front_wheel_radius", lc), {"value": 300.0, "unit": "mm"})
        self.assertEqual(resolve(self.ctx, "summary.rail_section.h", lc), {"value": 80.0, "unit": "mm"})
        self.assertEqual(resolve(self.ctx, "design.support.FL.radius_mm", lc), {"value": 300.0, "unit": "mm"})
        self.assertEqual(resolve(self.ctx, "design.section.rt40x80x2.h", lc), {"value": 80.0, "unit": "mm"})
        self.assertEqual(resolve(self.ctx, "summary.member_length.rail_L0", lc), {"value": 900.0, "unit": "mm"})
        self.assertEqual(resolve(self.ctx, {"value": 1, "unit": "N"}, lc), {"value": 1.0, "unit": "N"})
        for bad in ("pred.nothing", "ana.P1-step100.front_wheel_force", "nope.x", "design.support.ZZ.radius_mm"):
            with self.subTest(bad=bad):
                with self.assertRaises(ScriptError):
                    resolve(self.ctx, bad, lc)

    def test_plan_names_no_member_or_section(self):
        plan = load_plan("offroad-4w")
        self.assertEqual([s["load_case"] for s in plan["steps"]], ["P1-step100", "R1-step100"])
        refs = []
        for step in plan["steps"]:
            preds = step["predictions"] + step.get("expected_mismatch", {}).get("retry", {}).get("predictions", [])
            refs += [r for p in preds for r in p["inputs"].values() if isinstance(r, str)]
        for r in refs:
            self.assertFalse(r.startswith(("design.section.", "summary.member_length.", "design.support.")), r)


class ScriptedNoExpectedMismatchTest(RunCase, unittest.TestCase):
    # The stand-in tool returns 1.186 x the pushed-wheel load: about what the lever rule gives,
    # so the plan's expected mismatch does not happen and its retry is not used.
    step_factor = 1.186

    def test_retry_only_when_the_mismatch_happens(self):
        self.assertEqual(run_scripted(self.ctx), "done")
        self.assertEqual(self.ctx.store.records("cause"), [])
        self.assertEqual(denied_rules(self.ctx), ["H1_prediction_first"])
        formulas = {p["formula_id"] for p in self.ctx.store.records("prediction")}
        self.assertNotIn("step_climb.pushed_wheel_load", formulas)
        self.assert_events_fit()


class ScriptedMismatchTest(RunCase, unittest.TestCase):
    frame_factor = 1.4

    def test_stops_at_a_mismatch_the_plan_does_not_name(self):
        self.assertEqual(run_scripted(self.ctx), "stopped")
        self.assertIn("needs a cause", self.ctx.project["stop_reason"])
        r1 = [c for c in self.ctx.store.records("comparison")
              if self.ctx.prediction(c["prediction_id"])["load_case_id"] == "R1-step100"]
        self.assertEqual([c["status"] for c in r1], ["mismatch"])
        causes = self.ctx.store.records("cause")
        self.assertEqual([c["comparison_id"] for c in causes if c["comparison_id"] == r1[0]["comparison_id"]], [])
        self.assertEqual(len(causes), 1)  # only P1's, from the plan
        self.assertEqual(self.ctx.store.judgements()[0]["state"], "pending")
        self.assert_events_fit()


class ScriptedUnusableToolTest(RunCase, unittest.TestCase):
    frame_checks_pass = False

    def test_h2_stops_the_script(self):
        self.assertEqual(run_scripted(self.ctx), "stopped")
        self.assertEqual(denied_rules(self.ctx), ["H1_prediction_first", "H3_cause_before_continue", "H2_tool_usable"])
        self.assert_events_fit()


if __name__ == "__main__":
    unittest.main()
