"""The front bulkhead's place (ladder_4w bulkhead_dx) and the search for the driver's place with the most room
(hiraku.designgen.driver_fit), on the layouts where H3 was red (the experiment's run 10, and N15 of the script)."""

import json
import unittest
from pathlib import Path

from hiraku import designgen, solvers
from hiraku.designgen import driver_fit, ladder_4w
from hiraku.designgen.ladder_4w import DesignGenError
from hiraku.solvers.k4_geometry import people

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))


def mm(v):
    return {"value": v, "unit": "mm"}


RUN10 = {"track": mm(1250), "seat_x": mm(-1015), "steering_x": mm(-510), "battery_x": mm(-1015),
         "pedal_x": mm(-393.19 + 30)}
N15 = {"track": mm(1220), "steering_x": mm(-520)}


class BulkheadTest(unittest.TestCase):
    def test_default_keeps_v1(self):
        from tests.solvers.same_design import assert_same_design

        assert_same_design(self, ladder_4w.build(), V1)
        self.assertNotIn("bulkhead_dx", V1["variables"])  # written only when it is not the default
        var = ladder_4w.VARIABLES["bulkhead_dx"]
        self.assertTrue(var.revisable)
        self.assertEqual((var.lo, var.default, var.hi), (ladder_4w.BULKHEAD_DX_MIN_MM, ladder_4w.TOWER_MIN_DX_MM,
                                                        ladder_4w.TOWER_MIN_DX_MM))

    def test_the_lower_end_builds_and_below_it_is_refused(self):
        d = designgen.regenerate(V1, {"bulkhead_dx": mm(ladder_4w.BULKHEAD_DX_MIN_MM)})
        self.assertEqual(d["variables"]["bulkhead_dx"]["value"], ladder_4w.BULKHEAD_DX_MIN_MM)
        with self.assertRaises((DesignGenError, ValueError)):
            designgen.regenerate(V1, {"bulkhead_dx": mm(ladder_4w.BULKHEAD_DX_MIN_MM - 20.0)})


class DriverFitTest(unittest.TestCase):
    """The search finds a driver's place with every margin at least 0 where H3 was red, in under its time limit."""

    def check(self, changes, label):
        d = designgen.regenerate(V1, changes)
        before = driver_fit.margins(d)
        self.assertLess(min(v for v, _ in before.values()), 0.0, label)  # H3 red as the runs saw it
        got = driver_fit.best_driver_place(d, time_limit_s=280)
        print(f"\n{label}: smallest {got['smallest']}, {got['evaluations']} designs, {got['seconds']:.0f} s, "
              f"{got['stopped']}; changes {({k: v['value'] for k, v in got['changes'].items()})}")
        self.assertEqual(got["stopped"], "converged", label)
        self.assertGreaterEqual(got["smallest"]["value"], 0.0, label)
        fixed = designgen.regenerate(d, got["changes"])
        # the margins are the assembly tool's H3 results, by the same names
        tool = {t.name: t for t in solvers.builtin_tools()}["assembly"]
        lc = {"load_case_id": "H3-body-size", "test_id": "H3", "domain": "assembly", "title": "H3", "params": {},
              "tolerance_rel": 0.05}
        res = {r["quantity"]: r["value"] for r in tool.run(fixed, lc, [{"prediction_id": "p"}], {})["results"]}
        for name, m in got["margins"].items():
            self.assertAlmostEqual(res[name], m["value"], places=6, msg=(label, name))
        self.assertGreaterEqual(res["knee_room"], 0.0)
        # the default margins have knee_angle_room: both body sizes' knees inside the comfortable range
        self.assertEqual(driver_fit.MARGINS, driver_fit.COMFORT_MARGINS)
        self.assertGreaterEqual(res["knee_angle_room"], 0.0)
        for k, a in people.knee_angles(fixed)["occupant_R"].items():
            self.assertTrue(people.KNEE_COMFORT_DEG[0] <= a <= people.KNEE_COMFORT_DEG[1], (label, k, a))

    def test_run10(self):
        self.check(RUN10, "run 10")

    def test_n15(self):
        self.check(N15, "N15")


if __name__ == "__main__":
    unittest.main()
