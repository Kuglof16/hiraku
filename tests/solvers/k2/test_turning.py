"""The turning circle (S6): K0's turning.min_radius and turning.required_steer, K2's turning_circle tool
and the S6 load case. The steady circle (S2): K2's steady_circle tool, the S2 load case and its plan on the demo."""

import copy
import math
import unittest

from hiraku import contract, solvers
from hiraku.catalog.builders.common import BuildError
from hiraku.solvers.k2_vehicle.model import box_design, build_vehicle
from hiraku.solvers.k2_vehicle.shelf import TOOLS
from hiraku.solvers.k2_vehicle.shelf.turning import fit_circle, full_lock
from tests.solvers.k2.slow import SLOW


def q(v, u):
    return {"value": v, "unit": u}


class FormulasTest(unittest.TestCase):
    def test_min_radius_and_its_inverse(self):
        reg = solvers.formula_registry()
        out = reg.evaluate("turning.min_radius", {"L": q(1.8, "m"), "t": q(1.2, "m"), "delta_i": q(30.0, "deg")})["outputs"]
        d_o = math.atan(1.0 / (1.0 / math.tan(math.radians(30.0)) + 1.2 / 1.8))
        self.assertAlmostEqual(out["R"]["value"], 1.8 / math.sin(d_o), places=9)
        self.assertAlmostEqual(out["delta_o"]["value"], math.degrees(d_o), places=9)
        back = reg.evaluate("turning.required_steer", {"L": q(1.8, "m"), "t": q(1.2, "m"), "R_max": out["R"]})["outputs"]
        self.assertAlmostEqual(back["delta_i"]["value"], 30.0, places=9)


class ToolTest(unittest.TestCase):
    def test_full_lock_puts_the_inner_wheel_at_the_limit(self):
        veh = build_vehicle(box_design(variables={"max_steer_angle": q(30.0, "deg")}))
        for left in (True, False):
            angles = veh.wheel_steer(full_lock(veh, math.radians(30.0), left))
            self.assertAlmostEqual(max(abs(a) for a in angles), math.radians(30.0), places=12)

    def test_fit_circle(self):
        pts = [[3.0 + 2.0 * math.cos(a), -1.0 + 2.0 * math.sin(a)] for a in (0.1, 0.9, 1.7, 2.6)]
        import numpy as np
        c, r = fit_circle(np.array(pts))
        self.assertAlmostEqual(r, 2.0, places=9)
        np.testing.assert_allclose(c, [3.0, -1.0], atol=1e-9)

    def test_own_check_passes(self):
        for c in TOOLS["turning_circle"].own_checks():
            self.assertTrue(c["pass"], c)
            self.assertTrue(c["case_ja"] and c["source_ja"])

    def test_a_run_gives_the_radii_the_circles_and_the_rows(self):
        d = box_design(wheel_mass=15.0, variables={"max_steer_angle": q(30.0, "deg")})
        lc = {"load_case_id": "S6-turn", "params": {"direction": "left"},
              "criterion": {"quantity": "turning_radius_wheel", "op": "<=", "limit": q(12.0, "m"), "basis": "test"}}
        rec = TOOLS["turning_circle"].run(d, lc, [{"prediction_id": "pred-0001", "quantity": "turning_radius_wheel"}], {})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        self.assertEqual(contract.errors("analysis", dict(rec, analysis_id="ana-0001")), [])
        res = {r["quantity"]: r for r in rec["results"]}
        self.assertTrue(res["turning_radius_wheel"]["where"].endswith("FR"))  # turning left: the right front is outside
        self.assertGreater(res["turning_radius_body"]["value"], res["turning_radius_wheel"]["value"])
        self.assertAlmostEqual(res["inner_steer_angle"]["value"], 30.0, places=6)
        circles = [s for s in rec["scene"] if s["kind"] == "circle"]
        self.assertEqual(len(circles), 2)
        self.assertAlmostEqual(circles[0]["size_mm"][0] / 2000.0, res["turning_radius_wheel"]["value"], places=6)
        row = next(h for h in rec["hot_spots"] if h["quantity"] == "turning_radius_wheel")
        self.assertAlmostEqual(row["utilisation"], res["turning_radius_wheel"]["value"] / 12.0, places=9)


class LoadCaseTest(unittest.TestCase):
    def test_s6_from_q58(self):
        from hiraku.catalog.builders import k2_tests
        d = box_design(variables={"max_steer_angle": q(30.0, "deg")})
        req = {"requirements": [{"key": "Q30", "value": "empty"}, {"key": "Q58", "value": 12.0, "unit": "m"}]}
        lc = k2_tests.BUILDERS["S6"]({"id": "S6"}, req, d)
        self.assertEqual(lc["load_case_id"], "S6-turn")
        self.assertEqual(lc["criterion"]["quantity"], "turning_radius_wheel")
        self.assertEqual((lc["criterion"]["op"], lc["criterion"]["limit"]), ("<=", q(12.0, "m")))
        self.assertEqual(contract.errors("load_case", lc), [])
        ins = k2_tests.turning_inputs(d, lc)
        self.assertEqual((ins["L"], ins["t"]), (q(1.8, "m"), q(1.2, "m")))
        self.assertEqual(ins["delta_i"]["unit"], "deg")
        self.assertAlmostEqual(ins["delta_i"]["value"], 30.0, places=9)
        self.assertEqual(k2_tests.PLANS["S6"]["tool"], "turning_circle")


def no_steer(d):
    d = copy.deepcopy(d)
    for s in d["supports"]:
        s["actuation"]["steer"] = False
    return d


class SteadyCircleToolTest(unittest.TestCase):
    def test_own_checks_pass(self):
        """Hand values: v^2 / (R g) and the rigid moment balance on a held circle, t / (2 h) at the lift."""
        checks = TOOLS["steady_circle"].own_checks()
        self.assertEqual([c["quantity"] for c in checks], ["max_lateral_accel_g", "min_side_load", "max_lateral_accel_g"])
        for c in checks:
            self.assertTrue(c["pass"], c)
            self.assertTrue(c["case_ja"] and c["source_ja"])
            self.assertEqual(contract.errors("tool_check", c), [])
        self.assertAlmostEqual(checks[0]["expected"], 8.0 ** 2 / (20.0 * 9.80665), places=12)
        self.assertAlmostEqual(checks[2]["expected"], 1.2 / (2.0 * 1.0), places=12)  # the tall box: CG 1 m up

    def test_a_run_gives_the_results_and_the_judged_row(self):
        d = box_design(wheel_mass=15.0)
        lc = {"load_case_id": "S2-t", "params": {"radius": q(15.0, "m"), "speed": q(30.0, "km/h"), "direction": "right",
                                                 "ramp_time": q(6.0, "s"), "hold_time": q(1.0, "s")},
              "criterion": {"quantity": "min_side_load", "op": ">", "limit": q(0.0, "N"), "basis": "test"}}
        rec = TOOLS["steady_circle"].run(d, lc, [{"prediction_id": "pred-0001", "quantity": "min_side_load"}], {})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        self.assertEqual(contract.errors("analysis", dict(rec, analysis_id="ana-0001")), [])
        self.assertNotIn("effects", rec)  # every param is read
        res = {r["quantity"]: r for r in rec["results"]}
        for name in ("min_side_load", "min_wheel_load", "max_lateral_accel_g", "max_lateral_accel", "wheel_lift",
                     "two_wheel_lift", "tipped", "max_roll", "max_steer", "max_path_error"):
            self.assertIn(name, res)
        self.assertEqual(res["min_side_load"]["where"], "right (inner)")
        self.assertEqual(res["two_wheel_lift"]["value"], 0.0)
        self.assertGreater(res["min_side_load"]["value"], 0.0)
        # 30 km/h on 15 m asks 0.47 g; the run holds it (the driver a little outside the circle)
        self.assertAlmostEqual(res["max_lateral_accel_g"]["value"], (30.0 / 3.6) ** 2 / (15.0 * 9.80665), delta=0.05)
        self.assertAlmostEqual(res["max_lateral_accel"]["value"], res["max_lateral_accel_g"]["value"] * 9.80665,
                               places=9)
        self.assertTrue(any(a.startswith("held the circle") for a in rec["assumptions"]), rec["assumptions"])
        row = next(h for h in rec["hot_spots"] if h["quantity"] == "min_side_load")
        self.assertTrue(row["predicted"])
        self.assertEqual(row["where"], "right (inner)")
        self.assertEqual(rec["scene"][0]["kind"], "circle")
        self.assertAlmostEqual(rec["scene"][0]["size_mm"][0], 30000.0)

    def test_no_steered_wheel_is_refused(self):
        lc = {"load_case_id": "S2-t", "params": {"radius": q(15.0, "m"), "speed": q(30.0, "km/h")}}
        rec = TOOLS["steady_circle"].run(no_steer(box_design()), lc, [{"prediction_id": "pred-0001"}], {})
        self.assertEqual(rec["status"], "failed")
        self.assertIn("steer", rec["error"])

    def test_the_pairs_name_the_formulas(self):
        desc = TOOLS["steady_circle"].describe()
        self.assertEqual(desc["tests"], ["S2"])
        self.assertEqual([(p["quantity"], p["formula_id"], p["output"]) for p in desc["predicts"]],
                         [("max_lateral_accel_g", "lane_change.steady_steer", "a_y"),
                          ("min_side_load", "lane_change.inner_wheel_load", "W_inner")])
        self.assertEqual(desc["stage"], 2)


class S2LoadCaseTest(unittest.TestCase):
    REQ = {"requirements": [{"key": "Q30", "value": "empty"}, {"key": "Q50", "value": 40.0, "unit": "km/h"}]}

    def test_s2_from_q50(self):
        from hiraku.catalog.builders import k2_tests
        lc = k2_tests.BUILDERS["S2"]({"id": "S2"}, self.REQ, box_design())
        contract.validate("load_case", lc)
        self.assertEqual(lc["load_case_id"], "S2-circle25")
        self.assertEqual(lc["params"]["speed"], q(40.0, "km/h"))
        self.assertEqual(lc["params"]["radius"], q(k2_tests.S2_RADIUS_M, "m"))
        self.assertEqual(lc["params"]["direction"], "left")  # the CG on the centre line: left
        self.assertEqual((lc["criterion"]["quantity"], lc["criterion"]["op"], lc["criterion"]["limit"]),
                         ("min_side_load", ">", q(0.0, "N")))
        self.assertIn("HIRAKU の仮定", lc["criterion"]["basis"])
        self.assertIn("HIRAKU の仮定", lc["title"])
        self.assertEqual(lc["requirement_keys"], ["Q30", "Q50"])
        declared = {p["name"] for p in TOOLS["steady_circle"].describe()["params"]}
        self.assertEqual(set(lc["params"]) - declared, set())
        self.assertEqual(k2_tests.PLANS["S2"]["tool"], "steady_circle")

    def test_no_steered_wheel_is_not_buildable(self):
        from hiraku.catalog.builders import k2_tests
        with self.assertRaises(BuildError) as e:
            k2_tests.BUILDERS["S2"]({"id": "S2"}, self.REQ, no_steer(box_design()))
        self.assertIn("actuation.steer", str(e.exception))

    def test_the_run_lists_it_as_not_buildable_with_the_reason(self):
        from hiraku.catalog import load_catalog
        from hiraku.catalog.build import NO_BUILDER, build_load_cases
        cat = load_catalog()
        self.assertEqual(cat.test("S2")["tolerance_rel"], 0.10)
        skipped = []
        got = build_load_cases(cat, self.REQ, box_design(), ["S2"], skipped)
        self.assertEqual([lc["test_id"] for lc in got], ["S2"])
        self.assertEqual(got[0]["tolerance_rel"], 0.10)  # the catalog's
        got = build_load_cases(cat, self.REQ, no_steer(box_design()), ["S2"], skipped)
        self.assertEqual(got, [])
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0][0], "S2")
        self.assertIn("actuation.steer", skipped[0][1])
        self.assertFalse(skipped[0][1].endswith(NO_BUILDER))


@SLOW
class S2DemoPlanTest(unittest.TestCase):
    """S2 on the demo v1 with its plan: the lateral acceleration within the tolerance, the inner wheels' load
    missing it as the plan expects (the springs' roll), and its retry within the tolerance."""

    @classmethod
    def setUpClass(cls):
        from hiraku.catalog.builders.k2_tests import BUILDERS, PLANS
        from hiraku.catalog.summary import design_summary
        from tests.solvers.k2.test_builders import demo
        cls.design, req = demo()
        cls.lc = BUILDERS["S2"]({"id": "S2"}, req, cls.design)
        cls.plan = PLANS["S2"]
        cls.summary = design_summary(cls.design)
        rec = TOOLS["steady_circle"].run(cls.design, cls.lc, [{"prediction_id": "pred-0001"}], {"record_hz": 0})
        cls.rec = rec
        cls.got = {r["quantity"]: r["value"] for r in rec["results"]}

    def predict(self, step):
        reg = solvers.formula_registry()
        preds = {}
        for p in step["predictions"]:
            frm = p["inputs_from"](self.design, self.lc)
            ins = {}
            for k, ref in p["inputs"].items():
                head, _, rest = ref.partition(".")
                ins[k] = {"lc": lambda: self.lc["params"][rest], "from": lambda: frm[rest], "pred": lambda: preds[rest],
                          "load": lambda: self.summary["loadings"][self.lc["params"]["loading"]][rest],
                          "summary": lambda: self.summary[rest]}[head]()
            preds[p["quantity"]] = reg.evaluate(p["formula_id"], ins)["outputs"][p["output"]]
        return {k: v["value"] for k, v in preds.items()}

    def rel(self, pred, quantity):
        return (self.got[quantity] - pred[quantity]) / abs(pred[quantity])

    def test_the_plan_and_its_retry_on_the_demo(self):
        self.assertEqual(self.rec["status"], "ok", self.rec.get("error"))
        self.assertEqual(self.got["two_wheel_lift"], 0.0)  # the demo holds the circle at 40 km/h
        self.assertGreater(self.got["min_side_load"], 0.0)  # S2 passes on the demo v1
        tol = self.lc["tolerance_rel"]
        first = self.predict(self.plan)
        self.assertLessEqual(abs(self.rel(first, "max_lateral_accel_g")), tol)
        expected = self.plan["expected_mismatch"]
        self.assertEqual(expected["quantity"], "min_side_load")
        self.assertGreater(abs(self.rel(first, "min_side_load")), tol)  # the springs' roll: it really misses
        self.assertGreater(first["min_side_load"], self.got["min_side_load"])  # the hand value is the higher
        again = self.predict(expected["retry"])
        for name in ("max_lateral_accel_g", "min_side_load"):
            self.assertLessEqual(abs(self.rel(again, name)), tol, name)


if __name__ == "__main__":
    unittest.main()
