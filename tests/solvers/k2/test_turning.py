"""The turning circle (S6): K0's turning.min_radius and turning.required_steer, K2's turning_circle tool
and the S6 load case."""

import math
import unittest

from hiraku import contract, solvers
from hiraku.solvers.k2_vehicle.model import box_design, build_vehicle
from hiraku.solvers.k2_vehicle.shelf import TOOLS
from hiraku.solvers.k2_vehicle.shelf.turning import fit_circle, full_lock


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


if __name__ == "__main__":
    unittest.main()
