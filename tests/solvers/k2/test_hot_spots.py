"""K2's analysis.hot_spots (contract: CAE の役目): the criterion at places the load case does not judge,
and the wheels' friction use and unloading over the whole run."""

import unittest

from hiraku import contract
from hiraku.solvers.k2_vehicle.model import box_design
from hiraku.solvers.k2_vehicle.shelf import TOOLS
from hiraku.solvers.k2_vehicle.shelf.base import hot_spots, utilisation
from tests.solvers.k2.slow import SLOW


def q(v, u):
    return {"value": v, "unit": u}


class UtilisationTest(unittest.TestCase):
    def test_upper_and_lower_limits(self):
        self.assertEqual(utilisation(30.0, 40.0, "<="), 0.75)
        self.assertEqual(utilisation(20.0, 30.0, ">="), 1.5)  # 20 m/s against a 30 m/s floor: past it
        # no ratio, or outside 0..1000 (contract 46306b5): 1000 when failing, 0 when passing
        self.assertEqual(utilisation(100.0, 0.0, ">"), 0.0)  # a 0 N floor, held
        self.assertEqual(utilisation(0.0, 0.0, ">"), 1000.0)  # a 0 N floor, reached
        self.assertEqual(utilisation(0.0, 30.0, ">="), 1000.0)
        self.assertEqual(utilisation(-5.0, 30.0, "<="), 0.0)
        self.assertEqual(utilisation(1e6, 1.0, "<="), 1000.0)

    def test_rows_from_places_and_series(self):
        out = {"places": [{"where": "a", "quantity": "critical_wind_speed", "value": 20.0, "unit": "m/s", "judged": True},
                          {"where": "b", "quantity": "critical_wind_speed", "value": 25.0, "unit": "m/s", "judged": False}],
               "series": [{"channel": "friction_use.FL", "unit": "1", "t_s": [0, 1], "values": [0.2, 0.9]},
                          {"channel": "wheel_load.FL", "unit": "N", "t_s": [0, 1], "values": [2000.0, 500.0]}]}
        lc = {"criterion": {"quantity": "critical_wind_speed", "op": ">=", "limit": q(30.0, "m/s"), "basis": "x"}}
        rows = hot_spots(out, lc, [{"quantity": "critical_wind_speed"}])
        self.assertEqual([(r["where"], r["quantity"]) for r in rows],
                         [("a", "critical_wind_speed"), ("b", "critical_wind_speed"), ("FL", "friction_use"),
                          ("FL", "wheel_unloading")])
        by = {(r["where"], r["quantity"]): r for r in rows}
        self.assertEqual(by[("a", "critical_wind_speed")]["predicted"], True)
        self.assertEqual(by[("b", "critical_wind_speed")]["predicted"], False)
        self.assertAlmostEqual(by[("b", "critical_wind_speed")]["utilisation"], 1.2)
        self.assertAlmostEqual(by[("FL", "wheel_unloading")]["value"]["value"], 0.75)
        self.assertFalse(by[("FL", "friction_use")]["predicted"])


class CrosswindHotSpotsTest(unittest.TestCase):
    def test_both_wind_sides_are_rows(self):
        lc = {"load_case_id": "F1-wind30", "test_id": "F1", "params": {"wind_speed": q(30.0, "m/s"), "wind_from": "left"},
              "criterion": {"quantity": "critical_wind_speed", "op": ">=", "limit": q(30.0, "m/s"), "basis": "test"}}
        rec = TOOLS["crosswind"].run(box_design(wheel_mass=15.0), lc, [{"prediction_id": "pred-0001"}], {})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        self.assertEqual(contract.errors("analysis", dict(rec, analysis_id="ana-0001")), [])
        rows = {(r["where"], r["quantity"]): r for r in rec["hot_spots"]}
        left, right = rows[("wind from the left", "critical_wind_speed")], rows[("wind from the right", "critical_wind_speed")]
        self.assertTrue(left["predicted"])
        self.assertFalse(right["predicted"])
        res = {r["quantity"]: r["value"] for r in rec["results"]}
        self.assertAlmostEqual(left["value"]["value"], res["critical_wind_speed"])
        self.assertAlmostEqual(left["utilisation"], 30.0 / res["critical_wind_speed"])
        self.assertTrue(any(r["quantity"] == "wheel_unloading" for r in rec["hot_spots"]))
        u = [r["utilisation"] for r in rec["hot_spots"]]
        self.assertEqual(u, sorted(u, reverse=True))


@SLOW
class SlopeHotSpotsTest(unittest.TestCase):
    def test_both_slope_sides_are_rows(self):
        # a slope steep enough to tip the box either way while it is made (mu 2: it tips, about 50 deg,
        # before it slides)
        lc = {"load_case_id": "S5-beyond", "test_id": "S5",
              "params": {"side_slope": q(75.0, "deg"), "slope_up": "left", "length": q(5.0, "m"),
                         "slope_ramp_time": q(2.0, "s")},
              "criterion": {"quantity": "tip_slope_angle", "op": ">", "limit": q(75.0, "deg"), "basis": "test"}}
        d = box_design(wheel_mass=15.0, variables={"tire_mu": {"value": 2.0, "unit": "1"}})
        rec = TOOLS["offroad_roll"].run(d, lc, [{"prediction_id": "pred-0001"}], {})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        self.assertEqual(contract.errors("analysis", dict(rec, analysis_id="ana-0001")), [])
        rows = {(r["where"], r["quantity"]): r for r in rec["hot_spots"]}
        left, right = rows[("left side up", "tip_slope_angle")], rows[("right side up", "tip_slope_angle")]
        self.assertTrue(left["predicted"])
        self.assertFalse(right["predicted"])
        res = {r["quantity"]: r["value"] for r in rec["results"]}
        self.assertAlmostEqual(left["value"]["value"], res["tip_slope_angle"])
        # the box is symmetric: the other side tips at nearly the same angle
        self.assertAlmostEqual(right["value"]["value"], left["value"]["value"], delta=0.5)
        self.assertGreater(right["utilisation"], 1.0)


if __name__ == "__main__":
    unittest.main()
