import json
import unittest
from pathlib import Path

from hiraku.designgen.kit import Item, Layout, PackageError, bottom_at, check, rear_at, top_at
from hiraku.solvers.k4_geometry.obb import from_box, overlap

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))


def v1_part(pid):
    return next(p for p in V1["parts"] if p["id"] == pid)


def item_of(pid):
    p = v1_part(pid)
    return Item(pid, p["name"], p["kind"], tuple(p["box"]["size_mm"]), p["mass_kg"])


class PackageTest(unittest.TestCase):
    def setUp(self):
        self.lay = Layout(dict(V1, parts=[]))

    def test_frame_features(self):
        self.assertEqual(self.lay.stations(), {"F": 400.0, "A": 0.0, "S1": -450.0, "S2": -1000.0, "B": -1700.0, "E": -2100.0})
        self.assertEqual((self.lay.rail_top(), self.lay.rail_bottom()), (420.0, 340.0))
        self.assertEqual(self.lay.rail_inner_gap(), 660.0)

    def test_references_place_v1_parts(self):
        lay = self.lay
        lay.put(item_of("floor"), x=lay.between("S1", "S2"), y=0.0, z=bottom_at(lay.rail_top() + 10.0))
        lay.put(item_of("battery"), x=lay.between("S1", "S2"), y=0.0, z=top_at(lay.bottom_of("floor")))
        lay.put(item_of("motor"), x=rear_at(lay.station_x("B") + 25.0), y=0.0, z=top_at(lay.rail_top()), carried_by=lay.at("B"))
        lay.put(item_of("seat_R"), x=lay.between("S1", "S2"), y=-300.0, z=bottom_at(lay.top_of("floor")))
        for pid in ("floor", "battery", "motor", "seat_R"):
            got, want = lay.part(pid), v1_part(pid)
            self.assertEqual(got["box"], want["box"], pid)
            self.assertEqual(got.get("carried_by"), want.get("carried_by"), pid)

    def test_mounts(self):
        self.assertEqual(self.lay.mounts([-300.0, 0.0, 300.0], (300, 300, 100)), ["LA", "RA", "LS1", "RS1"])
        self.assertEqual(self.lay.mounts([-250.0, -200.0, 750.0], (300, 200, 500)), ["RA", "RS1"])  # right side only
        self.assertEqual(self.lay.mounts([-450.0, 0.0, 300.0], (100, 100, 100)), ["LS1", "RS1"])  # on a station
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


class CheckTest(unittest.TestCase):
    def test_v1(self):
        r = check.check(V1)
        q = r["quantities"]
        self.assertAlmostEqual(q["mass"]["value"], 591.47, delta=0.01)
        self.assertEqual(q["ground_clearance"]["value"], 200.0)  # the motor's underside
        self.assertEqual(q["ground_clearance"]["where"], "motor")
        self.assertAlmostEqual(q["free_steer_angle"]["value"], 16.48, delta=0.01)
        self.assertAlmostEqual(q["knee_room"]["value"], -41.4, places=6)
        self.assertAlmostEqual(q["reach_to_controls"]["value"], 606.0 - 575.0, places=6)
        self.assertTrue(any("knee_room" in p for p in r["problems"]))
        self.assertTrue(any("rail_L_AS1" in p for p in r["problems"]))
        # approach: the line tangent to the 300 mm tyre through cross_F's lower front edge (420, 360)
        import math
        a, b = 420.0, 300.0 - 360.0
        self.assertAlmostEqual(q["approach_angle"]["value"],
                               math.degrees(math.asin(300 / math.hypot(a, b)) - math.atan2(b, a)), places=9)


if __name__ == "__main__":
    unittest.main()
