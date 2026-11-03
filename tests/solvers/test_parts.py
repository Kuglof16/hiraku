"""The parts the designgen kits place (drive, steering, pedals, brakes, belts, switch, lamps, roll bar part),
their systems and links, the variable driveshafts, the K4 checks of the seated body, and the driveshafts'
modes against the K0 formulas."""

import json
import math
import unittest
from pathlib import Path

import numpy as np

from hiraku import contract, designgen, solvers
from hiraku.designgen import ladder_4w
from hiraku.designgen.kit import body, drivetrain, safety, steering, systems
from hiraku.solvers.k2_vehicle.model import hardpoints_of
from hiraku.solvers.k4_geometry import bodies, interferences
from hiraku.solvers.k4_geometry import people
from hiraku.solvers.k4_geometry.model import shape_overlap

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
NODS = json.loads((ROOT / "data" / "designs" / "offroad-4w-nodriveshaft.json").read_text(encoding="utf-8"))
PARTS = {p["id"]: p for p in V1["parts"]}
# the masses of the parts before they were split (the first v1 file): kg
BEFORE = {"battery": 50.0, "motor": 25.0, "controller": 4.0, "aux": 6.0, "floor_front": 6.0, "floor": 8.0, "deck": 18.0,
          "front_body": 15.0, "steering": 12.0, "seat": 12.0, "occupant": 76.6, "cargo": 150.0, "wheel": 14.0,
          "suspension": 8.0}


def q(v, u):
    return {"value": float(v), "unit": u}


def mass(design, *ids):
    ps = {p["id"]: p for p in design["parts"]}
    return sum(ps[i]["mass_kg"] for i in ids if i in ps)


def box_lo_hi(p):
    c, s = np.asarray(p["box"]["center_mm"], float), np.asarray(p["box"]["size_mm"], float)
    return c - s / 2, c + s / 2


class PlacementTest(unittest.TestCase):
    def test_driveshafts_from_the_hard_points(self):
        for sid in ("RL", "RR"):
            hp = hardpoints_of(V1, sid)
            inner, outer = drivetrain.driveshaft_joints(V1, sid)
            self.assertEqual(outer, [float(v) for v in hp["wheel_center"]])
            mid = (np.asarray(hp["lca_front"]) + np.asarray(hp["lca_rear"])) / 2
            self.assertEqual(inner, [float(mid[0]), float(mid[1]), float(hp["wheel_center"][2])])
            cap = bodies(V1)[f"part:driveshaft_{sid}"].capsule
            ends = sorted([cap.a.tolist(), cap.b.tolist()], key=lambda p: abs(p[1]))
            np.testing.assert_allclose(ends, [inner, outer], atol=1e-6)
        red = PARTS["reducer"]
        lo, hi = box_lo_hi(red)
        self.assertAlmostEqual(hi[1] - lo[1], 2 * abs(drivetrain.driveshaft_joints(V1, "RL")[0][1]), places=6)
        self.assertAlmostEqual(red["box"]["center_mm"][2], hardpoints_of(V1, "RL")["wheel_center"][2], places=6)
        self.assertAlmostEqual(box_lo_hi(PARTS["motor"])[0][0], hi[0], places=6)  # motor's back face on the reducer

    def test_steering_from_the_hard_points_and_the_driver(self):
        for sid in ("FL", "FR"):
            hp = hardpoints_of(V1, sid)
            cap = bodies(V1)[f"part:tie_rod_{sid}"].capsule
            np.testing.assert_allclose(sorted([cap.a.tolist(), cap.b.tolist()], key=lambda p: abs(p[1])),
                                       [hp["tie_inner"], hp["tie_outer"]], atol=1e-6)
        rack = bodies(V1)["part:steering_rack"].capsule
        ends = sorted([rack.a.tolist(), rack.b.tolist()], key=lambda p: p[1])
        np.testing.assert_allclose(ends, [hardpoints_of(V1, "FR")["tie_inner"], hardpoints_of(V1, "FL")["tie_inner"]],
                                   atol=1e-6)
        wheel = PARTS["steering_wheel"]
        self.assertEqual(wheel["kind"], "steering")  # the systems check starts the steering at kind steering
        # the rim's lowest point KNEE_GAP_MM above the top of the large driver's knee
        pose = body.driver_pose(_layout(), "seat_R", "floor_front")
        knee_top = float(pose.legs["R"].knee[2]) + pose.radii["thigh"]
        a = math.radians(steering.COLUMN_ANGLE_DEG)
        rim_low = wheel["box"]["center_mm"][2] - steering.STEERING_WHEEL_DIAMETER_MM / 2 * math.cos(a)
        self.assertAlmostEqual(rim_low, knee_top + steering.KNEE_GAP_MM, places=6)
        self.assertEqual(wheel["box"]["center_mm"][1], -V1["variables"]["seat_y"]["value"])

    def test_pedals_under_the_right_foot_inside_the_rails(self):
        pose = body.driver_pose(_layout(), "seat_R", "floor_front")
        face = people.pedal_face(PARTS["pedal_accel"])
        ball = pose.legs["R"].ball
        self.assertAlmostEqual(face[0], ball[0], places=6)
        self.assertAlmostEqual(face[2], ball[2], places=6)
        rail_out = V1["variables"]["rail_spacing"]["value"] / 2 + V1["variables"]["rail_b"]["value"] / 2
        lo, hi = box_lo_hi(PARTS["pedal_accel"])
        self.assertAlmostEqual(lo[1], -rail_out, places=6)  # the foot is further out: the pad stops at the rail's face
        self.assertGreater(abs(ball[1]) + body.PEDAL_PAD_MM[1] / 2, rail_out)  # (the foot is further out)
        self.assertAlmostEqual(PARTS["pedal_brake"]["box"]["center_mm"][1] - PARTS["pedal_accel"]["box"]["center_mm"][1],
                               body.BRAKE_OFFSET_MM, places=6)
        self.assertEqual((PARTS["pedal_accel"]["kind"], PARTS["pedal_brake"]["kind"]), ("accelerator_pedal", "brake_pedal"))

    def test_safety_parts(self):
        braked = [s["id"] for s in V1["supports"] if s["actuation"]["brake"]]
        self.assertEqual(sorted(p["id"] for p in V1["parts"] if p["kind"] == "brake_caliper"),
                         sorted(f"caliper_{s}" for s in braked))
        self.assertEqual(PARTS["brake_master"]["kind"], "brake_master_cylinder")
        self.assertAlmostEqual(box_lo_hi(PARTS["brake_master"])[1][0], box_lo_hi(PARTS["front_body"])[0][0], places=6)
        for seat in ("seat_R", "seat_L"):
            belt = PARTS[safety.belt_of(seat)]
            self.assertEqual(belt["kind"], "seat_belt")
            self.assertAlmostEqual(box_lo_hi(belt)[1][0], box_lo_hi(PARTS[seat])[0][0], places=6)  # behind the seat back
        self.assertEqual(PARTS["main_switch"]["kind"], "switch")
        rb = PARTS["rollbar"]
        self.assertEqual((rb["kind"], rb["mass_kg"]), ("rollbar", 0.0))
        self.assertFalse(any(p["kind"] in safety_lamps() for p in V1["parts"]))  # off public roads: no lamps
        self.assertEqual(interferences(V1), [])

    def test_lamps_on_public_roads(self):
        d = ladder_4w.build({"lamps": q(1, "1")})
        kinds = sorted({p["kind"] for p in d["parts"] if p["kind"] in safety_lamps()})
        self.assertEqual(kinds, sorted(safety_lamps()))
        self.assertEqual(interferences(d), [])
        self.assertAlmostEqual(sum(p["mass_kg"] for p in d["parts"]), sum(p["mass_kg"] for p in V1["parts"]), places=9)


class LampsFromTheRequirementsTest(unittest.TestCase):
    def test_public_road_puts_the_lamps_in(self):
        from hiraku.catalog import load_requirements
        req = load_requirements(ROOT / "data" / "requirements" / "offroad-4w.yaml")
        self.assertFalse(req["flags"].get("public_road"))
        self.assertEqual(designgen.propose("ladder_4w", {}, {}, req)["variables"]["lamps"]["value"], 0.0)
        road = dict(req, flags=dict(req["flags"], public_road=True))
        d = designgen.propose("ladder_4w", {}, {}, road)
        self.assertEqual(d["variables"]["lamps"]["value"], 1.0)
        self.assertTrue(any(p["kind"] == "headlamp" for p in d["parts"]))


def safety_lamps():
    return systems.LAMP_KINDS


def _layout():
    from hiraku.designgen.kit.package import Layout
    return Layout(V1)


class MassTest(unittest.TestCase):
    """The parts are split out of the earlier ones: the vehicle's parts weigh what they did."""

    def test_split_keeps_each_group(self):
        d = V1
        self.assertAlmostEqual(mass(d, "motor", "reducer", "driveshaft_RL", "driveshaft_RR"), BEFORE["motor"], places=9)
        self.assertAlmostEqual(mass(d, "steering_wheel", "steering_column", "steering_shaft", "steering_rack",
                                    "tie_rod_FL", "tie_rod_FR"), BEFORE["steering"], places=9)
        self.assertAlmostEqual(mass(d, "floor_front", "pedal_accel", "pedal_brake", "brake_master"), BEFORE["floor_front"],
                               places=9)
        for s in ("R", "L"):
            self.assertAlmostEqual(mass(d, f"seat_{s}", f"seat_belt_{s}"), BEFORE["seat"], places=9)
        for sid in ("FL", "FR", "RL", "RR"):
            self.assertAlmostEqual(mass(d, f"susp_{sid}", f"knuckle_{sid}", f"caliper_{sid}", f"toe_link_{sid}"),
                                   BEFORE["suspension"], places=9)
        self.assertAlmostEqual(mass(d, "aux", "main_switch"), BEFORE["aux"], places=9)

    def test_total_of_the_parts(self):
        total = (BEFORE["battery"] + BEFORE["motor"] + BEFORE["controller"] + BEFORE["aux"] + BEFORE["floor_front"]
                 + BEFORE["floor"] + BEFORE["deck"] + BEFORE["front_body"] + BEFORE["steering"] + 2 * BEFORE["seat"]
                 + 2 * BEFORE["occupant"] + BEFORE["cargo"] + 4 * BEFORE["wheel"] + 4 * BEFORE["suspension"])
        for d in (V1, NODS, ladder_4w.build({"lamps": q(1, "1")}), ladder_4w.build({"rollbar": q(0, "1")})):
            self.assertAlmostEqual(sum(p["mass_kg"] for p in d["parts"]), total, places=9)

    def test_wider_track_keeps_the_motor(self):
        d = designgen.regenerate(V1, {"track": q(1220, "mm")})
        self.assertAlmostEqual(mass(d, "motor", "reducer"), mass(V1, "motor", "reducer"), places=9)
        self.assertGreater(mass(d, "driveshaft_RL"), mass(V1, "driveshaft_RL"))  # longer shafts weigh more


class DriveshaftsVariableTest(unittest.TestCase):
    def test_without_driveshafts(self):
        self.assertFalse(any(p["kind"] == "driveshaft" for p in NODS["parts"]))
        self.assertEqual(NODS["variables"]["driveshafts"], q(0, "1"))
        var = ladder_4w.VARIABLES["driveshafts"]
        self.assertEqual((var.unit, var.default, var.lo, var.hi), ("1", 1.0, 0.0, 1.0))
        self.assertTrue(var.meaning)
        lk = systems.links(NODS)
        reached = systems.reach(lk, "motor", "torque")
        self.assertFalse({"RL", "RR"} & reached)
        self.assertFalse(any(c["to"].startswith("driveshaft") for r in lk.values() for c in r["connects_to"]))
        self.assertAlmostEqual(mass(NODS, "motor", "reducer"), mass(V1, "motor", "reducer", "driveshaft_RL", "driveshaft_RR"),
                               places=9)

    def test_revised_back_is_v1(self):
        d = designgen.regenerate(NODS, {"driveshafts": {"value": 1, "unit": "1"}})
        keep = ("design_version", "parent_version", "name", "change_reason")
        self.assertEqual({k: v for k, v in d.items() if k not in keep}, {k: v for k, v in V1.items() if k not in keep})

    def test_the_file_is_its_script(self):
        built = ladder_4w.build({"driveshafts": q(0, "1")}, name=NODS["name"], reason=NODS["change_reason"])
        text = (ROOT / "data" / "designs" / "offroad-4w-nodriveshaft.json").read_text(encoding="utf-8")
        self.assertEqual(json.dumps(built, ensure_ascii=False, indent=2) + "\n", text)
        contract.validate("design", NODS)


class SystemsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.links = systems.links(V1)

    def test_every_part_has_a_system(self):
        self.assertEqual(set(self.links), set(PARTS))
        self.assertFalse([pid for pid, r in self.links.items() if r["system"] == "other"])

    def test_chains_end_to_end(self):
        sups = {s["id"]: s["actuation"] for s in V1["supports"]}
        torque = systems.reach(self.links, "motor", "torque")
        self.assertTrue({s for s, a in sups.items() if a["drive"]} <= torque)
        steer = systems.reach(self.links, "steering_wheel", "steer")
        self.assertTrue({"steering_column", "steering_shaft", "steering_rack", "knuckle_FL", "knuckle_FR"} <= steer)
        self.assertTrue({s for s, a in sups.items() if a["steer"]} <= steer)
        brake = systems.reach(self.links, "pedal_brake", "brake")
        self.assertTrue({"brake_master"} | {s for s, a in sups.items() if a["brake"]} <= brake)
        power = systems.reach(self.links, "battery", "power")
        self.assertIn("motor", power)
        self.assertIn(PARTS["main_switch"]["kind"], systems.SWITCH_KINDS)
        # the switch is in the battery's chain: without it the battery does not reach the motor
        cut = {k: dict(v, connects_to=[c for c in v["connects_to"] if c["to"] != "main_switch"])
               for k, v in self.links.items() if k != "main_switch"}
        self.assertNotIn("motor", systems.reach(cut, "battery", "power"))

    def test_seats_restraints_and_rops_on_the_frame(self):
        frame = {n["id"] for n in V1["nodes"]} | {m["id"] for m in V1["members"]}
        for seat in ("seat_R", "seat_L"):
            self.assertTrue(any(c["to"] in frame for c in self.links[seat]["connects_to"]))
            belt = self.links[safety.belt_of(seat)]
            self.assertEqual(belt["system"], "restraint")
            self.assertIn(seat, [c["to"] for c in belt["connects_to"]])
            self.assertTrue(any(c["to"] in frame for c in belt["connects_to"]))
        rb = self.links["rollbar"]
        self.assertEqual(rb["system"], "rops")
        self.assertTrue(rb["connects_to"])
        self.assertTrue(all(c["to"] in frame and c["via"] == "weld" for c in rb["connects_to"]))

    def test_written_when_the_contract_has_the_fields(self):
        if systems.contract_has_fields():
            self.assertTrue(all("system" in p for p in V1["parts"]))
            contract.validate("design", V1)
        else:
            self.assertFalse(any("system" in p or "connects_to" in p for p in V1["parts"]))
            d = json.loads(json.dumps(V1))
            self.assertTrue(systems.write(d, force=True))
            self.assertTrue(all("system" in p for p in d["parts"]))


class SeatedBodyTest(unittest.TestCase):
    def test_body_checks_are_reported(self):
        f = people.fit(V1)
        for k in ("foot_room", "leg_clearance", "foot_support", "pedal_reach", "wheel_reach"):
            self.assertIn(f"{k}.occupant_R", f)
        self.assertIn("knee_room.occupant_R", f)  # the earlier check stays

    def test_legs_clear_of_the_rack_and_column(self):
        ps = people.poses(V1)["occupant_R"]
        legs = people.leg_bodies(ps["big"], "occupant_R")
        bs = bodies(V1)
        for k in ("part:steering_rack", "part:steering_column", "part:steering_shaft"):
            worst = max(shape_overlap(lb, bs[k]) for key, lb in legs.items() if ".foot_" not in key)
            self.assertLess(worst, 0.0, k)

    def test_occupant_as_body_parts(self):
        """kit/body.occupant_parts (not written by the generator yet: ladder_4w.BODY_PARTS)."""
        from hiraku.designgen.kit.package import Layout
        lay = Layout(V1)
        pose = body.driver_pose(lay, "seat_R", "floor_front")
        out = body.occupant_parts(lay, "occupant_R", pose, people.grip_points(lay.part("steering_wheel")))
        self.assertAlmostEqual(sum(p["mass_kg"] for p in out), PARTS["occupant_R"]["mass_kg"], places=9)
        self.assertEqual({p["kind"] for p in out}, {"occupant"})
        self.assertEqual({body.person_of(p["id"]) for p in out}, {"occupant_R"})
        self.assertEqual(len(out), 1 + 2 * 4)  # trunk, and thigh, shank, arm, foot on each side
        self.assertFalse(ladder_4w.BODY_PARTS)


class DriveshaftModesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from hiraku.solvers.k1_frame.shaft import driveshaft_modes
        cls.r = driveshaft_modes(V1)
        cls.R = solvers.formula_registry()

    def test_fe_against_k0(self):
        for pid, i in self.r["inputs"].items():
            b = self.R.evaluate("modal.tube_shaft_bending", {"E": q(i["E"], "MPa"), "rho": q(i["rho"], "kg/m^3"),
                                                              "L": q(i["L"], "mm"), "D": q(i["D"], "mm"),
                                                              "t": q(i["t"], "mm")})["outputs"]["f1"]["value"]
            t = self.R.evaluate("modal.two_inertia_torsion", {"E": q(i["E"], "MPa"), "nu": q(i["nu"], "1"),
                                                               "L": q(i["L"], "mm"), "D": q(i["D"], "mm"),
                                                               "t": q(i["t"], "mm"), "J_1": q(i["J_in"], "kg*m^2"),
                                                               "J_2": q(i["J_out"], "kg*m^2")})["outputs"]["f"]["value"]
            self.assertLessEqual(abs(b / self.r["bending"][pid] - 1), 0.15)  # modal's tolerance
            self.assertLessEqual(abs(t / self.r["torsion"][pid] - 1), 0.15)

    def test_known_answers(self):
        from hiraku.solvers.k1_frame.shaft import bending_frequencies, torsion_frequencies, tube
        # pinned-pinned tube: f_n = (n^2 pi / 2 L^2) sqrt(E I / rho A)
        sec = tube(30.0, 5.0)
        f1 = math.pi / (2 * 0.5 ** 2) * math.sqrt(205e9 * sec["I"] * 1e-12 / (7850 * sec["A"] * 1e-6))
        f = bending_frequencies(500.0, 30.0, 5.0, 205000.0, 7850.0)
        self.assertAlmostEqual(f[0] / f1, 1.0, delta=1e-4)
        self.assertAlmostEqual(f[1] / (4 * f1), 1.0, delta=1e-3)
        # two discs on a massless shaft (rho ~ 0): sqrt(k (J1 + J2) / (J1 J2)) / 2 pi
        G = 79000.0
        k = G * 1e6 * sec["J"] * 1e-12 / 0.5
        ft = torsion_frequencies(500.0, 30.0, 5.0, G, 1e-9, 0.3, 0.6)
        self.assertAlmostEqual(ft[0], 0.0, delta=1e-3)
        self.assertAlmostEqual(ft[1], math.sqrt(k * 0.9 / 0.18) / (2 * math.pi), delta=1e-6)

    def test_modal_tool_reports_them(self):
        tool = {t.name: t for t in solvers.builtin_tools()}["modal"]
        lc = {"load_case_id": "N1", "test_id": "N1", "domain": "nvh", "title": "N1", "params": {}, "tolerance_rel": 0.15}
        res = {r["quantity"]: r for r in tool.run(V1, lc, [{"prediction_id": "pred-0001"}], {})["results"]}
        self.assertAlmostEqual(res["driveshaft_bending_mode"]["value"], min(self.r["bending"].values()), places=6)
        self.assertAlmostEqual(res["driveshaft_torsion_mode"]["value"], min(self.r["torsion"].values()), places=6)
        self.assertIn("first_rollbar_mode", res)  # the roll bar's sway, apart from the ladder's twist
        self.assertLess(res["first_rollbar_mode"]["value"], res["first_frame_mode"]["value"])
        from hiraku.solvers.k1_frame.shaft import driveshaft_modes
        self.assertEqual(driveshaft_modes(NODS)["bending"], {})


if __name__ == "__main__":
    unittest.main()
