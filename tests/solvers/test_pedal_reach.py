"""K4's pedal_reach: whether the small person's right foot reaches the pedal at all (against the straight leg),
not how comfortable the knee is (that is knee_angle_room)."""

import unittest

from hiraku.solvers.k4_geometry import people
from hiraku.solvers.k4_geometry.people import BIG, SMALL


def pose_to(size, dx):
    """A seated pose whose right ankle is dx mm beyond (along the hip-ankle line) what the straight leg reaches."""
    l1, l2 = people._links(people.SIZES[size])
    hip = people.hip_point(-1000.0, 700.0, size)
    ankle_off = people._ankle_from_heel(size, people.FOOT_ANGLE_DEG)
    ball_off = people._rot(people.FOOT_ANGLE_DEG, (people.SIZES[size]["instep_length"], 0.0))
    d = l1 + l2 + dx
    dz = hip[1] - 400.0
    ankle = (hip[0] + (d * d - dz * dz) ** 0.5, 400.0)
    ball = (ankle[0] - ankle_off[0] + ball_off[0], ankle[1] - ankle_off[1] + ball_off[1])
    return people.seated(-1000.0, 700.0, 0.0, 300.0, size, right_ball=ball)


class PedalReachTest(unittest.TestCase):
    def test_zero_at_the_straight_leg(self):
        for size in (BIG, SMALL):
            self.assertAlmostEqual(people.pedal_margin(pose_to(size, 0.0)), 0.0, places=6)

    def test_negative_only_beyond_reach(self):
        for size in (BIG, SMALL):
            for dx in (-200.0, -50.0, -1.0, 1.0, 30.0):
                p = pose_to(size, dx)
                m = people.pedal_margin(p)
                self.assertAlmostEqual(m, -dx, places=6, msg=(size, dx))
                self.assertEqual(m >= 0.0, p.legs["R"].reached, (size, dx))

    def test_a_straight_knee_is_not_negative(self):
        """A knee at 170 deg is far outside the comfortable range but reaches: pedal_reach is positive and
        knee_angle_room is negative."""
        hip = people.hip_point(-1000.0, 700.0, SMALL)
        ball = people.leg_at_knee_angle(hip, 300.0, SMALL, 170.0)[3]
        p = people.seated(-1000.0, 700.0, 0.0, 300.0, SMALL, right_ball=(float(ball[0]), float(ball[1])))
        self.assertGreater(people.pedal_margin(p), 0.0)
        self.assertLess(people.knee_angle_margins(p)[1], 0.0)


if __name__ == "__main__":
    unittest.main()
