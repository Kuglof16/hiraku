"""K4's knee_angle_room: the pedal foot's knee angle against the comfortable range of the driving posture
(hiraku.solvers.k4_geometry.people.KNEE_COMFORT_DEG), for the big and the small person on the one seat."""

import json
import unittest
from pathlib import Path

import numpy as np

from hiraku import designgen, solvers
from hiraku.designgen import driver_fit
from hiraku.solvers.k4_geometry import people
from hiraku.solvers.k4_geometry.people import BIG, KNEE_COMFORT_DEG, SMALL

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
LC = {"load_case_id": "H3-body-size", "test_id": "H3", "domain": "assembly", "title": "H3", "params": {},
      "tolerance_rel": 0.05}


def mm(v):
    return {"value": v, "unit": "mm"}


RUN10 = {"track": mm(1250), "seat_x": mm(-1015), "steering_x": mm(-510), "battery_x": mm(-1015),
         "pedal_x": mm(-393.19 + 30)}
N15 = {"track": mm(1220), "steering_x": mm(-520)}


def pose_at(size, knee_angle):
    """A seated pose whose right ball of the foot is where the leg puts it with the heel on the floor at knee_angle."""
    hip = people.hip_point(-1000.0, 700.0, size)
    ball = people.leg_at_knee_angle(hip, 300.0, size, knee_angle)[3]
    return people.seated(-1000.0, 700.0, 0.0, 300.0, size, right_ball=(float(ball[0]), float(ball[1])))


class MarginTest(unittest.TestCase):
    def test_zero_at_the_ends_of_the_range(self):
        lo, hi = KNEE_COMFORT_DEG
        for size in (BIG, SMALL):
            p = pose_at(size, lo)
            self.assertAlmostEqual(p.legs["R"].knee_angle, lo, places=6)
            self.assertAlmostEqual(people.knee_angle_margins(p)[0], 0.0, places=6)
            self.assertAlmostEqual(people.knee_angle_margins(pose_at(size, hi))[1], 0.0, places=6)

    def test_sign_follows_the_angle(self):
        lo, hi = KNEE_COMFORT_DEG
        for size in (BIG, SMALL):
            for a in (lo - 5.0, (lo + hi) / 2.0, hi + 5.0):
                bent, straight = people.knee_angle_margins(pose_at(size, a))
                self.assertEqual(bent >= 0.0, a >= lo, (size, a))
                self.assertEqual(straight >= 0.0, a <= hi, (size, a))


class V1Test(unittest.TestCase):
    def test_v1(self):
        """v1 as it is: the big person's knee at 100 deg (the pedals are placed there), the small person's at
        169.8 deg, beyond 138: knee_angle_room is the small person's straight side."""
        ang = people.knee_angles(V1)["occupant_R"]
        self.assertAlmostEqual(ang["big"], 100.0, places=6)
        self.assertAlmostEqual(ang["small"], 169.81, delta=0.005)
        f = people.fit(V1)
        v, where = f["knee_angle_room.occupant_R"]
        self.assertAlmostEqual(v, -46.36, delta=0.005)
        self.assertEqual(where, "pedal_accel")
        self.assertNotIn("knee_angle_room.occupant_L", f)  # only the driver has a pedal
        small = people.knee_angle_margins(people.poses(V1)["occupant_R"]["small"])
        self.assertAlmostEqual(v, small[1], places=9)
        # the assembly tool reports it by the same name, unit mm; the other margins are as before
        tool = {t.name: t for t in solvers.builtin_tools()}["assembly"]
        rec = tool.run(V1, LC, [{"prediction_id": "p"}], {})
        res = {r["quantity"]: r for r in rec["results"]}
        self.assertAlmostEqual(res["knee_angle_room"]["value"], v, places=9)
        self.assertEqual(res["knee_angle_room"]["unit"], "mm")
        self.assertAlmostEqual(res["pedal_reach"]["value"], 2.93, delta=0.005)  # the straight leg reaches
        self.assertAlmostEqual(res["knee_room"]["value"], -69.3, delta=0.05)


class OneSeatTwoSizesTest(unittest.TestCase):
    def test_no_ball_place_puts_both_in_the_range(self):
        """On v1's seat and pedal height, along x: wherever the big person's knee is at least 99 deg, the small
        person's is beyond 138 (the two legs differ by more than the range is wide)."""
        ps = people.poses(V1)["occupant_R"]
        lo, hi = people._box(ps["seat"])
        face = people.pedal_face(ps["pedal"])
        both = []
        for dx in np.arange(-400.0, 400.0, 1.0):
            ball = np.array([face[0] + dx, face[2]])
            a = {s: people.leg_to_ball(people.hip_point(lo[0], hi[2], s), ball, s).knee_angle for s in (BIG, SMALL)}
            if KNEE_COMFORT_DEG[0] <= a[BIG] and a[SMALL] <= KNEE_COMFORT_DEG[1]:
                both.append(dx)
        self.assertEqual(both, [])

    def check_search(self, changes, label):
        d = designgen.regenerate(V1, changes)
        got = driver_fit.best_driver_place(d, names=driver_fit.COMFORT_MARGINS, time_limit_s=180)
        print(f"\n{label} with knee_angle_room: smallest {got['smallest']}, {got['evaluations']} designs, "
              f"{got['seconds']:.0f} s, {got['stopped']}; changes {({k: v['value'] for k, v in got['changes'].items()})}; "
              f"knee angles {people.knee_angles(designgen.regenerate(d, got['changes']))}")
        self.assertEqual(got["stopped"], "converged", label)
        self.assertIn("knee_angle_room", got["margins"])
        self.assertLess(got["smallest"]["value"], 0.0, label)  # no set has every margin at least 0

    def test_run10_search(self):
        self.check_search(RUN10, "run 10")

    def test_n15_search(self):
        self.check_search(N15, "N15")


if __name__ == "__main__":
    unittest.main()
