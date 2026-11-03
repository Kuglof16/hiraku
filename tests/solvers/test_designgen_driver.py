"""The driver's place as variables of ladder_4w (seat_height, steering_column_angle, steering_wheel_height,
pedal_x): their defaults build v1 unchanged, the seat, pedals, steering wheel, column and the driver's body are
built from them, the readers (K4 knee_space, H1 steer_room, R4 rops_inputs, the H3 builder) take the designs,
and the knee room moves as the closed-form geometry of the column says."""

import json
import math
import unittest
from pathlib import Path

import numpy as np

from hiraku import contract, designgen, solvers
from hiraku.catalog import load_catalog, load_requirements
from hiraku.catalog.builders import BUILDERS
from hiraku.catalog.builders.analysis import h3_calc
from hiraku.designgen import ladder_4w
from hiraku.designgen.kit import steering
from hiraku.solvers.k4_geometry import AIST, knee_space, people
from hiraku.solvers.k4_geometry.motion import steer_room

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
NEW = ("seat_height", "steering_column_angle", "steering_wheel_height", "pedal_x")
VAR = ladder_4w.VARIABLES
J5 = AIST["buttock_knee_m95"]
RC = steering.COLUMN_RADIUS_MM
R_WHEEL = steering.STEERING_WHEEL_DIAMETER_MM / 2.0
T_WHEEL = steering.STEERING_WHEEL_THICKNESS_MM
# The experiment's run 10 (its version 2): v1 with these, built when the pedals moved with the seat (30 mm forward)
RUN10 = {"track": 1250.0, "seat_x": -1015.0, "steering_x": -510.0, "battery_x": -1015.0,
         "pedal_x": VAR["pedal_x"].default + 30.0}


def build(**vals):
    return ladder_4w.build({**RUN10, **vals})


def part(design, pid):
    return next(p for p in design["parts"] if p["id"] == pid)


def floor_top(design):
    return people._extent(part(design, "floor_front"))[1][2]


def seat_top(design):
    return people._extent(part(design, "seat_R"))[1][2]


def knee_closed_form(design):
    """The driver's knee room (mm) from the variables alone: the seat back is seat_x - 250 (the seat 500 long);
    knee_space takes the turned box of the nearest part ahead at knee height (seat top to KNEE_BAND_MM above)
    along x. The wheel's box reaches down to its centre - R cos a - T/2 sin a, back to its centre x - R sin a -
    T/2 cos a; the column's box (the column from the wheel's centre to its lower end above the pinion, a radius
    RC beyond each end) down to the lower end - RC (sin a + cos a), back to the wheel's centre x - RC (cos a +
    sin a). Returns (room, part) or (None, None) when neither is at knee height."""
    v = {k: q["value"] for k, q in design["variables"].items()}
    a = math.radians(v["steering_column_angle"])
    back = v["seat_x"] - ladder_4w.SEAT_LENGTH_MM / 2.0
    wx = v["steering_x"] - ladder_4w.STEERING_WHEEL_BEHIND_MM
    wz = seat_top(design) + v["steering_wheel_height"]
    band = seat_top(design) + people.KNEE_BAND_MM
    x_low = steering.rack_ends(design)[0][0]  # the column ends above the pinion, on the rack's line along y
    wheel_bottom = wz - R_WHEEL * math.cos(a) - T_WHEEL / 2.0 * math.sin(a)
    column_bottom = wz - (x_low - wx) * math.tan(a) - RC * (math.sin(a) + math.cos(a))
    if wheel_bottom < band:
        return wx - R_WHEEL * math.sin(a) - T_WHEEL / 2.0 * math.cos(a) - back - J5, "steering_wheel"
    if column_bottom < band:
        return wx - RC * (math.cos(a) + math.sin(a)) - back - J5, "steering_column"
    return None, None


def column_height_to_clear_knees(design, angle):
    """The steering_wheel_height (mm) above which the column's box is above the knee band at this angle."""
    v = {k: q["value"] for k, q in design["variables"].items()}
    a = math.radians(angle)
    wx = v["steering_x"] - ladder_4w.STEERING_WHEEL_BEHIND_MM
    x_low = steering.rack_ends(design)[0][0]
    return people.KNEE_BAND_MM + (x_low - wx) * math.tan(a) + RC * (math.sin(a) + math.cos(a))


class DefaultsTest(unittest.TestCase):
    def test_v1_keeps_its_parts(self):
        """The four variables at their defaults build v1; a v1 saved without them rebuilds to the same parts."""
        self.assertEqual(ladder_4w.build(), V1)
        old = json.loads(json.dumps(V1))
        for k in NEW:
            del old["variables"][k]
        again = designgen.regenerate(old, {})
        self.assertEqual(again, V1)
        self.assertEqual({k: V1["variables"][k]["value"] for k in NEW}, {k: VAR[k].default for k in NEW})

    def test_defaults_are_the_earlier_placement(self):
        """steering_wheel_height: the rim's lowest point KNEE_GAP_MM over the large driver's knee top at 35 deg;
        pedal_x: the large driver's ball of the foot with the knee at KNEE_ANGLE_DEG."""
        pose = people.poses(V1)["occupant_R"]["big"]
        face = people.pedal_face(part(V1, "pedal_accel"))
        self.assertAlmostEqual(face[0], VAR["pedal_x"].default, places=9)
        self.assertAlmostEqual(pose.legs["R"].knee_angle, people.KNEE_ANGLE_DEG, places=6)
        knee_top = float(pose.legs["R"].knee[2]) + pose.radii["thigh"]
        rim_low = seat_top(V1) + VAR["steering_wheel_height"].default - R_WHEEL * math.cos(math.radians(35.0))
        self.assertAlmostEqual(rim_low - knee_top, steering.KNEE_GAP_MM, places=6)
        self.assertEqual(VAR["seat_height"].default, seat_top(V1) - floor_top(V1))
        self.assertEqual(VAR["steering_column_angle"].default, steering.COLUMN_ANGLE_DEG)

    def test_older_design_with_the_seat_moved_keeps_its_pedals(self):
        """A design saved before pedal_x had the pedals under the driver's foot: they moved with seat_x."""
        old = build()
        for k in NEW:
            del old["variables"][k]
        got = ladder_4w.values_of(old)
        self.assertEqual(got["pedal_x"]["value"], VAR["pedal_x"].default + 30.0)
        self.assertNotIn("pedal_x", ladder_4w.values_of({"variables": {k: v for k, v in V1["variables"].items()
                                                                       if k not in NEW}}))


class RangesTest(unittest.TestCase):
    def test_ranges_from_the_body_sizes(self):
        s = people.SIZES
        self.assertEqual((VAR["seat_height"].lo, VAR["seat_height"].hi),
                         (ladder_4w.SEAT_HEIGHT_MIN_MM,
                          math.floor(s["f5"]["knee_joint_height"] - s["f5"]["trochanter_height"])))
        self.assertEqual(VAR["seat_height"].hi, 341.0)
        knee_top = ladder_4w._DRIVER_V1["knee_top_above_seat"]
        self.assertEqual(VAR["steering_wheel_height"].lo, math.ceil(knee_top))
        self.assertEqual(VAR["steering_wheel_height"].hi, s["m95"]["shoulder_height"])
        self.assertEqual(VAR["pedal_x"].hi, 0.0)
        # the small person's ball of the foot (knee at KNEE_ANGLE_DEG) in the rearmost seat
        small = people.seated(VAR["seat_x"].lo - 250.0, 450.0 + 260.0, 0.0, 450.0, people.SMALL)
        self.assertEqual(VAR["pedal_x"].lo, math.floor(float(small.legs["R"].ball[0])))
        for k in NEW:
            self.assertTrue(VAR[k].revisable, k)
            self.assertLessEqual(VAR[k].lo, VAR[k].default)
            self.assertLessEqual(VAR[k].default, VAR[k].hi)
            self.assertIn(VAR[k].unit, contract.UNITS)

    def test_steepest_column_closes_at_the_default_steering(self):
        for a in VAR["steering_column_angle"].lo, VAR["steering_column_angle"].hi:
            contract.validate("design", ladder_4w.build({"steering_column_angle": a}))
        with self.assertRaisesRegex(designgen.DesignGenError, "outside 20 to 50 deg"):
            ladder_4w.build({"steering_column_angle": 55.0})


class PartsFromTheVariablesTest(unittest.TestCase):
    VALS = {"seat_height": 320.0, "steering_column_angle": 25.0, "steering_wheel_height": 560.0, "pedal_x": -430.0,
            "steering_x": -540.0}

    @classmethod
    def setUpClass(cls):
        cls.d = ladder_4w.build(cls.VALS)

    def test_seat_and_occupants(self):
        d = self.d
        self.assertAlmostEqual(seat_top(d) - floor_top(d), 320.0, places=9)
        for occ in ("occupant_R", "occupant_L"):
            self.assertAlmostEqual(people._extent(part(d, occ))[0][2], seat_top(d), places=9)  # the trunk on the seat
        top = max(n["xyz_mm"][2] for n in d["nodes"] if n["id"].endswith("HT"))  # the roll bar's top goes up with it
        self.assertAlmostEqual(top, seat_top(d) + VAR["rollbar_height"].default, places=9)

    def test_steering_wheel_and_column(self):
        d = self.d
        a = math.radians(25.0)
        wheel = part(d, "steering_wheel")
        np.testing.assert_allclose(wheel["box"]["center_mm"], [-540.0 - 150.0, -VAR["seat_y"].default, seat_top(d) + 560.0])
        self.assertAlmostEqual(wheel["box"]["rpy_deg"][1], 25.0, places=9)  # the disc square to the column
        top, low = steering.column_ends(d, part(d, "steering_column"))
        np.testing.assert_allclose(top, wheel["box"]["center_mm"], atol=1e-9)
        u = np.subtract(low, top)
        length = float(np.linalg.norm(u))
        np.testing.assert_allclose(u / length, [math.cos(a), 0.0, -math.sin(a)], atol=1e-12)
        # the column runs from the wheel's centre to above the pinion on the rack; the shaft from there to it
        pinion = steering.shaft_ends(d, part(d, "steering_shaft"))[1]
        self.assertAlmostEqual(length, (pinion[0] - top[0]) / math.cos(a), places=9)
        self.assertAlmostEqual(low[0], pinion[0], places=9)

    def test_pedals_and_the_drivers_leg(self):
        d = self.d
        face = people.pedal_face(part(d, "pedal_accel"))
        self.assertAlmostEqual(face[0], -430.0, places=9)
        s = people.SIZES[people.BIG]
        ball_z = floor_top(d) + s["instep_length"] * math.sin(math.radians(people.FOOT_ANGLE_DEG))
        self.assertAlmostEqual(face[2], ball_z, places=9)  # the heel on the footwell floor
        # the driver's body as parts is the pose K4 sits the driver in: the right foot's ball on the pedal
        pose = people.poses(d)["occupant_R"]["big"]
        g = pose.legs["R"]
        thigh = part(d, "occupant_R.thigh_R")
        np.testing.assert_allclose(thigh["box"]["center_mm"], (g.hip + g.knee) / 2.0, atol=1e-6)
        foot = part(d, "occupant_R.foot_R")
        u = (g.toe - g.heel) / np.linalg.norm(g.toe - g.heel)
        up = np.array([-u[2], 0.0, u[0]])
        np.testing.assert_allclose(foot["box"]["center_mm"], (g.heel + g.toe) / 2.0 + up * s["malleolus_height"] / 2.0,
                                   atol=1e-6)
        self.assertAlmostEqual(g.ball[0], -430.0, places=6)
        # the pedal 37 mm further back (the seat 60 mm higher): the leg reaches it with the knee bent a little more
        self.assertTrue(g.reached)
        self.assertLess(g.knee_angle, people.KNEE_ANGLE_DEG)

    def test_readers_take_the_design(self):
        d = self.d
        contract.validate("design", d)
        self.assertEqual(steer_room(d), steer_room(V1))  # the front wheels are where they were
        from hiraku.solvers.k0_handcalc.rops_inputs import rops_inputs
        spaces = {s["occupant"]: s for s in rops_inputs(d, "two")["gaps"]["spaces"]}
        self.assertAlmostEqual(spaces["occupant_R"]["lo_mm"][2], seat_top(d), places=9)
        self.assertAlmostEqual(spaces["occupant_R"]["hi_mm"][2], seat_top(d) + AIST["sitting_height_m95"], places=9)
        room, where = knee_closed_form(d)
        self.assertEqual(knee_space(d)["occupant_R"][1], where)
        self.assertAlmostEqual(knee_space(d)["occupant_R"][0] - J5, room, places=6)
        req = load_requirements(ROOT / "data" / "requirements" / "offroad-4w.yaml")
        lc = BUILDERS["H3"](load_catalog().test("H3"), req, d)
        got = h3_calc(d, lc)
        self.assertAlmostEqual(got["knee_length"]["value"] - got["buttock_knee"]["value"], room, places=6)


class KneeRoomClosedFormTest(unittest.TestCase):
    def check(self, d):
        room, where = knee_closed_form(d)
        got = knee_space(d)["occupant_R"]
        self.assertEqual(got[1], where)
        self.assertAlmostEqual(got[0] - J5, room, places=6)
        return room

    def test_run10_is_the_columns_box(self):
        d = build()
        self.assertAlmostEqual(self.check(d), -39.25, delta=0.005)
        # what the experiment read off its run 10: knee room -39.25 mm at the column, pedal reach -22.36 mm (then
        # against a 150 deg knee); pedal reach is now against the straight leg: the small person reaches, 2.93 mm
        f = people.fit(d)
        self.assertAlmostEqual(f["pedal_reach.occupant_R"][0], 2.93, delta=0.005)

    def test_along_x_and_the_angle(self):
        """While the column is the part at knee height: +1 mm per mm of steering_x, -1 per mm of seat_x, and
        -RC (cos a + sin a) from the angle; the seat's height, the wheel's height and the pedals do not move it."""
        base = self.check(build())
        for sx in (-560.0, -470.0):
            self.assertAlmostEqual(self.check(build(steering_x=sx)) - base, sx - RUN10["steering_x"], places=6)
        self.assertAlmostEqual(self.check(build(seat_x=-1045.0, battery_x=-1045.0)) - base, 30.0, places=6)
        for a in (20.0, 50.0):
            t = math.radians(a)
            want = -RC * (math.cos(t) + math.sin(t)) + RC * (math.cos(math.radians(35)) + math.sin(math.radians(35)))
            self.assertAlmostEqual(self.check(build(steering_column_angle=a)) - base, want, places=6)
        for vals in ({"seat_height": 200.0}, {"seat_height": 341.0}, {"pedal_x": -450.0},
                     {"steering_wheel_height": 600.0}):
            self.assertAlmostEqual(self.check(build(**vals)), base, places=6, msg=str(vals))

    def test_wheel_low_into_the_knees(self):
        """A wheel whose rim comes down into the knee band: its own box is nearest, R sin a + T/2 cos a back."""
        room = self.check(build(steering_wheel_height=400.0))
        self.assertLess(room, -100.0)

    def test_column_above_the_knees(self):
        """Raised and flatter, the column's box leaves the knee band at the height the closed form gives."""
        d = build()
        for a in (20.0, 25.0):
            h = column_height_to_clear_knees(d, a)
            below = build(steering_column_angle=a, steering_wheel_height=h - 0.05)
            above = build(steering_column_angle=a, steering_wheel_height=h + 0.05)
            self.assertEqual(knee_space(below)["occupant_R"][1], "steering_column")
            self.assertNotIn(knee_space(above)["occupant_R"][1], ("steering_column", "steering_wheel"))
            self.assertIsNone(knee_closed_form(above)[0])
        self.assertAlmostEqual(column_height_to_clear_knees(d, 20.0), 522.18, delta=0.005)


class Run10H3Test(unittest.TestCase):
    """run 10's shape is red in H3 (the assembly tool and the hand prediction); raising the wheel and laying the
    column flatter makes it green without moving it along x (the arm reach of the small person is kept)."""

    def h3(self, d):
        tool = {t.name: t for t in solvers.builtin_tools()}["assembly"]
        req = load_requirements(ROOT / "data" / "requirements" / "offroad-4w.yaml")
        lc = BUILDERS["H3"](load_catalog().test("H3"), req, d)
        rec = tool.run(d, lc, [{"prediction_id": "pred-0001"}], {})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        got = {r["quantity"]: r for r in rec["results"]}
        hand = h3_calc(d, lc)
        room = solvers.formula_registry().evaluate("occupant.knee_room", {"L": hand["knee_length"],
                                                                          "L_bk": hand["buttock_knee"]})
        self.assertAlmostEqual(room["outputs"]["room"]["value"], got["knee_room"]["value"], places=6)
        return got

    def test_run10_red_and_a_fix(self):
        got = self.h3(build())
        self.assertAlmostEqual(got["knee_room"]["value"], -39.25, delta=0.005)
        self.assertEqual(got["knee_room"]["where"], "steering_column")
        fixed = self.h3(build(steering_column_angle=20.0, steering_wheel_height=523.0))
        self.assertGreaterEqual(fixed["knee_room"]["value"], 0.0)
        self.assertGreaterEqual(fixed["wheel_reach"]["value"], 0.0)
        self.assertGreaterEqual(fixed["leg_clearance"]["value"], 0.0)


if __name__ == "__main__":
    unittest.main()
