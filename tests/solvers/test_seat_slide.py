"""The driver's seat's fore-and-aft travel (ladder_4w seat_slide; K4 k4_geometry.people.poses): each body size
sits where their own H3 margins are best within the travel; the seat's box stays at the middle."""

import json
import math
import unittest
from pathlib import Path

import numpy as np

from hiraku import designgen
from hiraku.designgen import ladder_4w
from hiraku.designgen.ladder_4w import DesignGenError
from hiraku.solvers.k4_geometry import interferences, knee_space, people
from hiraku.solvers.k4_geometry.people import AIST, BIG, SMALL

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
SLIDE = 200.0


def slid(length=SLIDE, **more):
    return designgen.regenerate(V1, {"seat_slide": {"value": length, "unit": "mm"}, **more})


_FIXED = []


def fixed():
    """v1 built by the generator (the fixed seat, with the seats' backs and head restraints)."""
    if not _FIXED:
        _FIXED.append(designgen.regenerate(V1, {}))
    return _FIXED[0]


class BestOffsetTest(unittest.TestCase):
    def test_no_travel(self):
        self.assertEqual(people.best_offset(lambda s: -abs(s - 30.0), 0.0), 0.0)

    def test_a_peak_inside(self):
        for peak in (-83.3, -12.0, 0.0, 41.7, 99.0):
            got = people.best_offset(lambda s: -abs(s - peak), SLIDE)
            self.assertLessEqual(abs(got - peak), people.SLIDE_RESOLUTION_MM, peak)

    def test_the_end_of_the_travel(self):
        self.assertEqual(people.best_offset(lambda s: s, SLIDE), SLIDE / 2.0)
        self.assertEqual(people.best_offset(lambda s: -s, SLIDE), -SLIDE / 2.0)

    def test_a_plateau_keeps_the_middle(self):
        self.assertEqual(people.best_offset(lambda s: 5.0, SLIDE), 0.0)

    def test_reads_the_variable(self):
        self.assertEqual(people.seat_slide(V1), 0.0)
        self.assertEqual(people.seat_slide({"variables": {"seat_slide": {"value": 120.0, "unit": "mm"}}}), 120.0)
        with self.assertRaises(ValueError):
            people.seat_slide({"variables": {"seat_slide": {"value": 0.12, "unit": "m"}}})


class V1Test(unittest.TestCase):
    def test_v1_has_no_travel(self):
        """The default (0) builds v1 as it was: the variable is not written, the people sit at the seat's box."""
        self.assertNotIn("seat_slide", V1["variables"])
        self.assertEqual(ladder_4w.VARIABLES["seat_slide"].default, 0.0)
        ps = people.poses(V1)["occupant_R"]
        self.assertEqual(ps["offset"], {"big": 0.0, "small": 0.0})

    def test_out_of_range(self):
        with self.assertRaises(DesignGenError):
            slid(ladder_4w.SEAT_SLIDE_MAX_MM + 1.0)
        with self.assertRaises(DesignGenError):
            slid(-1.0)


class SlideTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = slid()
        cls.ps = people.poses(cls.d)["occupant_R"]

    def test_written_and_in_the_travel(self):
        self.assertEqual(self.d["variables"]["seat_slide"], {"value": SLIDE, "unit": "mm"})
        off = self.ps["offset"]
        for k in ("big", "small"):
            self.assertLessEqual(abs(off[k]), SLIDE / 2.0, k)
        # the big person sits back (knee room, the knee less bent), the small one forward (the pedal nearer)
        self.assertLess(off["big"], 0.0)
        self.assertGreater(off["small"], 0.0)
        self.assertEqual(people.poses(self.d)["occupant_L"]["offset"]["big"], 0.0)  # the passenger's seat is fixed

    def test_seat_box_stays_at_the_middle(self):
        base = fixed()
        a = {p["id"]: p for p in base["parts"]}
        b = {p["id"]: p for p in self.d["parts"]}
        for pid in ("seat_R", "seat_L", "seat_belt_L", "seatback_R", "head_restraint_R", "seatback_L", "head_restraint_L"):
            self.assertEqual(a[pid]["box"], b[pid]["box"], pid)
        # the same parts, but the belt's own fitting to its seat (kit/joints.py), made where the moved belt and the
        # seat's box (left at the middle) no longer touch
        # and the driver's seatback's fitting (kit/joints.py: its strut also misses the seat, its back included, at the
        # travel's ends, so it goes to another member)
        belt_fit = ("fit_seat_belt_R", "fit_seatback_R")
        self.assertEqual([p["id"] for p in base["parts"] if not p["id"].startswith(belt_fit)],
                         [p["id"] for p in self.d["parts"] if not p["id"].startswith(belt_fit)])

    def test_belt_behind_the_rearmost_seat_back(self):
        b = {p["id"]: p for p in self.d["parts"]}
        seat_back = people._box(b["seat_R"])[0][0]
        belt_front = people._box(b["seat_belt_R"])[1][0]
        self.assertAlmostEqual(belt_front, seat_back - SLIDE / 2.0, places=6)

    def test_poses_at_the_offsets(self):
        seat = self.ps["seat"]
        lo, hi = people._box(seat)
        for k, size in (("big", BIG), ("small", SMALL)):
            hip = people.hip_point(lo[0] + self.ps["offset"][k], hi[2], size)
            np.testing.assert_allclose(self.ps[k].legs["R"].hip[[0, 2]], hip, atol=1e-9)
            # the pedal stays: the right ball of the foot on its face
            np.testing.assert_allclose(self.ps[k].legs["R"].ball[[0, 2]], people.pedal_face(self.ps["pedal"])[[0, 2]],
                                       atol=1e-9)

    def test_each_person_at_their_best(self):
        """Neither person's smallest margin is larger at another place in the travel (checked every 5 mm)."""
        from hiraku.solvers.k4_geometry.model import bodies

        bs = bodies(self.d)
        o = people.occupants(self.d)[0]
        seat, lo, hi = self.ps["seat"], *people._box(self.ps["seat"])
        face = people.pedal_face(self.ps["pedal"])
        fz = float(people._extent(self.ps["floor"])[1][2])
        y = float(seat["box"]["center_mm"][1])
        wheel = people.steering_wheel_of(self.d, seat)

        def big(s):
            p = people.seated(lo[0] + s, hi[2], y, fz, BIG, right_ball=(face[0], face[2]))
            v = [m for m, _ in people._leg_checks(p, o["id"], self.ps, bs).values()] + list(people.knee_angle_margins(p))
            return min(v + [people._knee_length(self.d, o, seat, lo[0] + s)[0] - AIST["buttock_knee_m95"]])

        def small(s):
            p = people.seated(lo[0] + s, hi[2], y, fz, SMALL, right_ball=(face[0], face[2]))
            return min([m for m, _ in people._small_checks(p, self.ps, wheel).values()] + list(people.knee_angle_margins(p)))

        for k, f in (("big", big), ("small", small)):
            best = f(self.ps["offset"][k])
            for s in np.arange(-SLIDE / 2.0, SLIDE / 2.0 + 1e-9, 5.0):
                self.assertLessEqual(f(float(s)), best + 1e-6, (k, s))

    def test_both_knees_in_the_range(self):
        """v1 with a 200 mm travel: both knees inside 99-138 deg (on v1's fixed seat the small one is at 169.8)."""
        ang = people.knee_angles(self.d)["occupant_R"]
        for k in ("big", "small"):
            self.assertTrue(people.KNEE_COMFORT_DEG[0] <= ang[k] <= people.KNEE_COMFORT_DEG[1], (k, ang[k]))
        self.assertGreater(people.fit(self.d)["knee_angle_room.occupant_R"][0], 0.0)

    def test_knee_room_where_the_big_person_sits(self):
        """knee_room (and the hand calculation's input, which reads knee_space) from the big person's seat back."""
        f = people.fit(self.d)
        o = people.occupants(self.d)[0]
        seat = self.ps["seat"]
        length, where = people._knee_length(self.d, o, seat, people._box(seat)[0][0] + self.ps["offset"]["big"])
        self.assertAlmostEqual(f["knee_room.occupant_R"][0], length - AIST["buttock_knee_m95"], places=9)
        self.assertEqual(knee_space(self.d)["occupant_R"], (length, where))
        self.assertEqual(where, "steering_column")

    def test_body_parts_on_the_seat_as_drawn(self):
        """The driver's body parts stay on the seat as drawn (the travel's middle), the trunk's back on the seatback's
        front face, as on the fixed seat; K4 seats each size at its own place with the seat moved under them. (They were
        once moved to the large driver's place, behind the seat box left at the middle: 59.9 mm on this design.)"""
        a = {p["id"]: p["box"] for p in fixed()["parts"] if p["id"].split(".")[0] == "occupant_R"}
        b = {p["id"]: p for p in self.d["parts"]}
        for pid, box in a.items():
            self.assertEqual(b[pid]["box"], box, pid)
        self.assertAlmostEqual(people._box(b["occupant_R"])[0][0], people._box(b["seatback_R"])[1][0], places=9)
        mass = lambda d: sum(p["mass_kg"] for p in d["parts"] if p["id"].split(".")[0] == "occupant_R")
        self.assertAlmostEqual(mass(self.d), mass(V1), places=9)
        self.assertEqual(interferences(self.d), [])

    def test_leg_clearance_as_measuring_every_body(self):
        """_leg_checks skips the bodies farther (by their axis-aligned boxes) than the nearest found: the same value
        and part as measuring them all, the seat anywhere in the travel; the feet do not move with the seat."""
        from hiraku.solvers.k4_geometry.model import bodies, shape_overlap

        bs = bodies(self.d)
        seat, (lo, hi) = self.ps["seat"], people._box(self.ps["seat"])
        face = people.pedal_face(self.ps["pedal"])
        fz = float(people._extent(self.ps["floor"])[1][2])
        feet = None
        for s in np.arange(-SLIDE / 2.0, SLIDE / 2.0 + 1e-9, 20.0):
            p = people.seated(lo[0] + s, hi[2], float(seat["box"]["center_mm"][1]), fz, BIG, right_ball=(face[0], face[2]))
            got = people._leg_checks(p, "occupant_R", self.ps, bs)
            best = (-math.inf, "")
            for k, lb in people.leg_bodies(p, "occupant_R").items():
                if ".foot_" in k:
                    continue
                for kb, b in bs.items():
                    if kb == "part:seat_R" or b.kind in people.LEG_SKIP_KINDS:
                        continue
                    o = shape_overlap(lb, b)
                    if o > best[0]:
                        best = (o, kb.split(":", 1)[1])
            self.assertEqual(got["leg_clearance"], (-best[0], best[1]), s)
            now = {k: got[k] for k in ("foot_room", "foot_support")}
            feet = feet or now
            self.assertEqual(now, feet, s)

    def test_slide_does_not_move_other_parts(self):
        a = {p["id"]: p["box"] for p in fixed()["parts"]}
        moved = sorted(p["id"] for p in self.d["parts"] if p["box"] != a.get(p["id"]))
        # the belt's own fitting (kit/joints.py) goes with the belt; the driver's body stays on the seat
        self.assertTrue(all(m.startswith(("seat_belt_R", "fit_seat_belt_R", "fit_seatback_R")) for m in moved), moved)
        self.assertIn("seat_belt_R", moved)


class BeltStrutClearOfFloorTest(unittest.TestCase):
    """A short travel (the scripted run's version 2: seat_slide 56 mm, the floor out to the side posts): the belt's
    anchor strut ends behind the floor's rear edge, so the floor still goes in (assembly blocked_parts 0)."""

    def test_short_travel_with_side_posts(self):
        from hiraku.designgen.kit import joints
        from hiraku.solvers.k4_geometry import assembly

        d = slid(56.0, side_guard="bars_net", track={"value": 1220, "unit": "mm"},
                 rollbar_width={"value": 1265, "unit": "mm"})
        parts = {p["id"]: p for p in d["parts"]}
        strut = parts["fit_seat_belt_R_side_post_RS2_1"]
        floor = parts["floor"]["box"]
        floor_rear = floor["center_mm"][0] - floor["size_mm"][0] / 2.0
        anchor = next(c["at_mm"] for c in parts["seat_belt_R"]["connects_to"] if c["to"] == strut["id"])
        self.assertLessEqual(anchor[0] + joints.STRUT_D_MM / 2.0,
                             floor_rear - ladder_4w.BELT_FLOOR_GAP_MM + 1e-9)
        self.assertEqual(assembly.disassemble(d)["blocked"], {})


if __name__ == "__main__":
    unittest.main()
