import json
import unittest
from pathlib import Path

from hiraku.designgen.kit import Item, Layout, PackageError, bottom_at, check, rear_at, top_at
from hiraku.solvers.k4_geometry.obb import from_box, overlap

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))


STATIONS = {"F": 400.0, "A": 0.0, "S1": -770.0, "S2": -1320.0, "B": -2020.0, "E": -2420.0}  # the demo's stations


def _ladder():
    """The demo vehicle's plain ladder frame (rails and cross members at its stations, without the
    suspension's mounts, the dash and the roll bar the generator adds)."""
    from hiraku.designgen.kit.frame import Frame
    from hiraku.designgen.kit.sections import rect_tube
    f = Frame("stkr400")
    rail, cross = f.section(*rect_tube(40, 80, 2.3)), f.section(*rect_tube(40, 40, 2.3))
    f.ladder(STATIONS, 350.0, 380.0, rail, cross)
    return {**f.part(), "supports": V1["supports"], "parts": []}


LADDER = _ladder()


def v1_part(pid):
    return next(p for p in V1["parts"] if p["id"] == pid)


def item_of(pid):
    p = v1_part(pid)
    return Item(pid, p["name"], p["kind"], tuple(p["box"]["size_mm"]), p["mass_kg"])


class PackageTest(unittest.TestCase):
    def setUp(self):
        self.lay = Layout(dict(LADDER, parts=[]))

    def test_frame_features(self):
        self.assertEqual(self.lay.stations(), STATIONS)
        self.assertEqual((self.lay.rail_top(), self.lay.rail_bottom()), (420.0, 340.0))
        self.assertEqual(self.lay.rail_inner_gap(), 660.0)

    def test_references_place_v1_parts(self):
        lay = self.lay
        lay.put(item_of("floor"), x=lay.between("S1", "S2"), y=0.0, z=bottom_at(lay.rail_top() + 10.0))
        lay.put(item_of("battery"), x=lay.between("S1", "S2"), y=0.0, z=top_at(lay.bottom_of("floor")))
        lay.put(item_of("seat_R"), x=lay.between("S1", "S2"), y=-300.0, z=bottom_at(lay.top_of("floor")))
        for pid in ("floor", "battery", "seat_R"):  # the motor: kit/drivetrain.py (test_parts.DrivetrainTest)
            got, want = lay.part(pid), v1_part(pid)
            self.assertEqual(got["box"], want["box"], pid)
            self.assertEqual(got.get("carried_by"), want.get("carried_by"), pid)

    def test_mounts(self):
        self.assertEqual(self.lay.mounts([-300.0, 0.0, 300.0], (300, 300, 100)), ["LA", "RA", "LS1", "RS1"])
        self.assertEqual(self.lay.mounts([-250.0, -200.0, 750.0], (300, 200, 500)), ["RA", "RS1"])  # right side only
        self.assertEqual(self.lay.mounts([-770.0, 0.0, 300.0], (100, 100, 100)), ["LS1", "RS1"])  # on a station
        with self.assertRaisesRegex(PackageError, "LX"):
            self.lay.at("X")

    def test_search_finds_free_room(self):
        lay = Layout(V1)
        box = Item("toolbox", "工具箱", "body", (300.0, 200.0, 150.0), 5.0)
        p = lay.search(box, region=((-2000, -500, 100), (400, 500, 900)), target=(-1300, 0, 300), clearance=10)
        placed = from_box(p["box"]["center_mm"], p["box"]["size_mm"])
        from hiraku.solvers.k4_geometry import bodies
        for b in bodies(dict(V1, parts=V1["parts"])).values():
            self.assertLessEqual(overlap(placed, b.obb), -10.0 + 1e-9, b.id)
        with self.assertRaisesRegex(PackageError, "does not fit"):
            lay.search(Item("big", "大きな箱", "body", (3000, 2000, 2000), 1.0), region=((-1000, 0, 500), (-1000, 0, 500)),
                       target=(-1000, 0, 500))


class FrameKitTest(unittest.TestCase):
    def test_sections(self):
        from hiraku.designgen.kit.sections import rect_tube, round_tube, table
        key, sec = rect_tube(40, 80, 2.3)
        self.assertEqual(key, "rt40x80x2.3")
        self.assertEqual(sec, V1["sections"][key])
        t = table({key: sec, **dict([round_tube(50, 3)])})
        self.assertAlmostEqual(t[key]["A_mm2"], 40 * 80 - (40 - 4.6) * (80 - 4.6), places=9)
        self.assertAlmostEqual(t["ct50x3"]["J_mm4"], 2 * 3.141592653589793 / 64 * (50**4 - 44**4), places=6)

    def test_ladder_is_v1(self):
        from hiraku.designgen.kit.frame import Frame
        from hiraku.designgen.kit.sections import rect_tube
        f = Frame("stkr400")
        rail, cross = f.section(*rect_tube(40, 80, 2.3)), f.section(*rect_tube(40, 40, 2.3))
        f.ladder(STATIONS, 350.0, 380.0, rail, cross)
        part = f.part()
        # v1 has the ladder's nodes and cross members; its rails are split at the mounts' nodes along the same lines
        v1_nodes = {n["id"]: n["xyz_mm"] for n in V1["nodes"]}
        for n in part["nodes"]:
            self.assertEqual(v1_nodes[n["id"]], n["xyz_mm"])
        crosses = [m for m in part["members"] if m["id"].startswith("cross_")]
        self.assertEqual(crosses, [m for m in V1["members"] if m["id"].startswith("cross_")])
        rail_len = sum(abs(v1_nodes[m["node_a"]][0] - v1_nodes[m["node_b"]][0]) for m in V1["members"]
                       if m["id"].startswith("rail_L_"))
        self.assertAlmostEqual(rail_len, STATIONS["F"] - STATIONS["E"], places=9)

    def test_lines_curves_mirror_and_merge(self):
        import math
        from hiraku.designgen.kit.frame import Frame, FrameError
        from hiraku.designgen.kit.sections import round_tube
        f = Frame("m")
        tube = f.section(*round_tube(30, 2))
        pts = Frame.arc([0, 300, 1000], 200, 180, 0, 4)  # a hoop over the seat, 4 straight pieces
        self.assertTrue(all(abs(math.dist(p, [0, 300, 1000]) - 200) < 1e-9 for p in pts))
        ids = f.polyline("hoop", [(f"LH{i}", p) for i, p in enumerate(pts)], tube)
        self.assertEqual(len(ids), 4)
        copies = f.mirror_y(ids)
        self.assertEqual(copies[0], "hoop_RH0RH1")
        self.assertEqual(f.nodes["RH2"], [f.nodes["LH2"][0], -f.nodes["LH2"][1], f.nodes["LH2"][2]])
        self.assertEqual(f.node("near", [f.nodes["LH0"][0] + 0.5, 300, 1000]), "LH0")  # within 1 mm: the same joint
        with self.assertRaisesRegex(FrameError, "not defined"):
            f.member("x", "LH0", "LH1", "nope")
        with self.assertRaisesRegex(FrameError, "already exists"):
            f.member(ids[0], "LH0", "LH1", tube)


class RunningGearTest(unittest.TestCase):
    def test_four_wheels_is_v1(self):
        from hiraku.designgen.kit.running_gear import Suspension, corner_name, four_wheels
        s = Suspension("kc_table", 20.0, 1.2, 150.0, "builtin:double_wishbone")
        sup = four_wheels(2020.0, 1100.0, 300.0, 200.0, s, s)
        # ladder_4w.build adds the hard points K2 lays out (write_hardpoints) after the wheels are placed
        bare = [dict(x, suspension={k: v for k, v in x["suspension"].items() if k != "hardpoints_mm"})
                for x in V1["supports"]]
        self.assertEqual(sup, bare)
        self.assertEqual([corner_name(x, sup) for x in sup], ["前左", "前右", "後左", "後右"])
        awd = four_wheels(1700.0, 1100.0, 300.0, 200.0, s, s, drive="all")
        self.assertTrue(all(x["actuation"]["drive"] for x in awd))

    def test_suspension_types(self):
        from hiraku.designgen.kit.running_gear import Suspension
        self.assertEqual(Suspension("rigid").record(), {"type": "rigid"})
        self.assertEqual(Suspension("kc_table", 20.0, kc_table="builtin:double_wishbone").record(),
                         {"type": "kc_table", "stiffness_N_per_mm": 20.0, "kc_table": "builtin:double_wishbone"})
        with self.assertRaisesRegex(ValueError, "kc_table"):
            Suspension("kc_table", 20.0).record()
        with self.assertRaisesRegex(ValueError, "wheel rate"):
            Suspension("spring").record()


class CheckTest(unittest.TestCase):
    def test_v1(self):
        r = check.check(V1)
        q = r["quantities"]
        # 602.14 kg before the suspension's mounts, the dash and bulkhead and the 320 mm longer front
        self.assertAlmostEqual(q["mass"]["value"], 631.15, delta=0.01)
        self.assertEqual(q["ground_clearance"]["value"], 150.0)  # the arms' box, 30 mm below the lower arm
        self.assertEqual(q["ground_clearance"]["where"], "susp_FL")
        self.assertAlmostEqual(q["free_steer_angle"]["value"], 16.48, delta=0.01)
        self.assertAlmostEqual(q["knee_room"]["value"], -69.3, delta=0.1)  # the steering column's turned box
        self.assertEqual(q["knee_room"]["where"], "steering_column")
        self.assertEqual(q["reach_to_controls"]["where"], "steering_column")  # the driver's controls
        for k in ("foot_room", "leg_clearance", "foot_support", "pedal_reach", "wheel_reach"):  # the seated body (K4)
            self.assertIn(k, q)
        for k in ("foot_room", "leg_clearance", "foot_support"):  # the first v1's layout errors are gone
            self.assertGreater(q[k]["value"], 0.0, k)
        self.assertTrue(any("knee_room" in p for p in r["problems"]))
        self.assertTrue(any("rail_L_" in p for p in r["problems"]))
        # approach: now the front lower arm's leg (its lower end at the pivot, 180 mm up, 120 mm ahead of the axle)
        self.assertEqual(q["approach_angle"]["where"], "lower_leg_LA1")
        self.assertAlmostEqual(q["approach_angle"]["value"], 39.40, delta=0.01)


if __name__ == "__main__":
    unittest.main()
