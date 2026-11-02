import unittest

from hiraku.agent.scripted import ScriptError, load_plan, resolve, run_scripted
from tests.tools.helpers import RunCase


class ScriptedRunTest(RunCase, unittest.TestCase):
    def test_runs_to_done(self):
        self.assertEqual(run_scripted(self.ctx), "done")
        kinds = self.kinds()
        self.assertEqual(kinds[0], "run.started")
        self.assertEqual(kinds[-1], "run.finished")
        self.assertIn("hook.denied", kinds)  # show_h1
        self.assertLess(kinds.index("hook.denied"), kinds.index("prediction.recorded"))
        self.assertEqual(kinds.count("verdict.recorded"), 1)
        self.assertEqual(self.ctx.project["status"], "done")
        self.assertEqual(self.ctx.store.judgements()[0]["state"], "green")
        denied = next(e for e in self.ctx.store.events() if e["kind"] == "hook.denied")
        self.assertTrue(denied["data"]["scripted"])
        self.assert_events_fit()
        self.assertEqual(self.ctx.store.records("cause"), [])

    def test_resolve_refs(self):
        lc = self.ctx.load_case("P1-step100")
        self.assertEqual(resolve(self.ctx, "lc.step_height", lc), {"value": 100.0, "unit": "mm"})
        self.assertEqual(resolve(self.ctx, "req.Q33", lc), {"value": 76.6, "unit": "kg"})
        self.assertAlmostEqual(resolve(self.ctx, "load.mass", lc)["value"], 60.0 + 2 * 76.6 + 150.0)
        self.assertEqual(resolve(self.ctx, "design.support.FL.radius_mm", lc), {"value": 300.0, "unit": "mm"})
        self.assertEqual(resolve(self.ctx, "design.section.rt40x80x2.h", lc), {"value": 80.0, "unit": "mm"})
        self.assertEqual(resolve(self.ctx, "summary.member_length.rail_L0", lc), {"value": 900.0, "unit": "mm"})
        self.assertEqual(resolve(self.ctx, {"value": 1, "unit": "N"}, lc), {"value": 1.0, "unit": "N"})
        for bad in ("pred.nothing", "ana.P1-step100.front_wheel_force", "nope.x", "design.support.ZZ.radius_mm"):
            with self.subTest(bad=bad):
                with self.assertRaises(ScriptError):
                    resolve(self.ctx, bad, lc)

    def test_plan_exists(self):
        plan = load_plan("offroad-4w")
        self.assertEqual([s["load_case"] for s in plan["steps"]], ["P1-step100", "R1-step100"])


class ScriptedMismatchTest(RunCase, unittest.TestCase):
    frame_factor = 1.4

    def test_stops_without_inventing_a_cause(self):
        self.assertEqual(run_scripted(self.ctx), "stopped")
        self.assertIn("needs a cause", self.ctx.project["stop_reason"])
        self.assertEqual(self.ctx.store.records("cause"), [])
        self.assertEqual(self.ctx.store.judgements()[0]["state"], "pending")
        self.assert_events_fit()


class ScriptedUnusableToolTest(RunCase, unittest.TestCase):
    frame_checks_pass = False

    def test_h2_stops_the_script(self):
        self.assertEqual(run_scripted(self.ctx), "stopped")
        rules_hit = [e["data"]["rule"] for e in self.ctx.store.events() if e["kind"] == "hook.denied"]
        self.assertEqual(rules_hit, ["H1_prediction_first", "H2_tool_usable"])
        self.assert_events_fit()


if __name__ == "__main__":
    unittest.main()
