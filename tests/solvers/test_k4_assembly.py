import json
import math
import unittest
from pathlib import Path

import numpy as np

from hiraku.solvers.k4_geometry import bodies, corners, disassemble, from_box, interferences
from hiraku.solvers.k4_geometry.assembly import STAGES, blocker, hand_way, is_attach, stage, steps
from hiraku.solvers.k4_geometry.capsule import (_box_sdf, capsule_box_overlap, capsule_overlap, from_obb, from_points,
                                                seg_box, seg_seg, swept_distance)

ROOT = Path(__file__).resolve().parents[2]
DESIGN = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))


def part(pid, kind, center, size, carried_by=None):
    p = {"id": pid, "name": pid, "kind": kind, "box": {"center_mm": list(center), "size_mm": list(size)}, "mass_kg": 1.0}
    if carried_by:
        p["carried_by"] = carried_by
    return p


def plain(parts, nodes=()):
    return {"design_version": 1, "name": "check", "variables": {}, "supports": [], "parts": parts, "nodes": list(nodes),
            "members": [], "sections": {}, "materials": {}}


class CapsuleTest(unittest.TestCase):
    def test_segments(self):
        self.assertAlmostEqual(float(seg_seg([0, 0, 0], [100, 0, 0], [0, 50, 0], [100, 50, 0])), 50.0)  # parallel
        self.assertAlmostEqual(float(seg_seg([0, 0, 0], [100, 0, 0], [150, 0, 0], [250, 0, 0])), 50.0)  # in line
        self.assertAlmostEqual(float(seg_seg([-100, 0, 0], [100, 0, 0], [0, -100, 30], [0, 100, 30])), 30.0)  # skew
        # ends near: the closest points are the two ends
        self.assertAlmostEqual(float(seg_seg([0, 0, 0], [100, 0, 0], [110, 10, 0], [200, 50, 0])), math.sqrt(200.0))
        self.assertAlmostEqual(float(seg_seg([50, 30, 40], [50, 30, 40], [0, 0, 0], [100, 0, 0])), 50.0)  # a point
        a = from_points([0, 0, 0], [100, 0, 0], 10.0)
        self.assertAlmostEqual(capsule_overlap(a, from_points([0, 50, 0], [100, 50, 0], 10.0)), -30.0)  # gap: negative
        self.assertAlmostEqual(capsule_overlap(a, from_points([50, -50, 15], [50, 50, 15], 10.0)), 5.0)  # into each other

    def test_segment_and_box(self):
        box = from_box([0, 0, 0], [100, 100, 100])
        self.assertAlmostEqual(seg_box([-200, 0, 80], [200, 0, 80], box), 30.0)  # passes over a face
        self.assertAlmostEqual(seg_box([60, 60, -10], [60, 60, 10], box), 10.0 * math.sqrt(2))  # beside an edge
        corner = np.full(3, 50.0)
        self.assertAlmostEqual(seg_box(corner + 30.0 / math.sqrt(3), np.full(3, 400.0), box), 30.0)  # off the corner
        flat = from_box([0, 0, 0], [100, 80, 60])
        self.assertAlmostEqual(seg_box([-200, 0, 0], [200, 0, 0], flat), -30.0)  # through: the deepest point
        self.assertAlmostEqual(seg_box([0, 0, 0], [200, 0, 0], box), -50.0)  # half in
        self.assertAlmostEqual(capsule_box_overlap(from_points([-200, 0, 80], [200, 0, 80], 40.0), box), 10.0)
        turned = from_box([0, 0, 0], [100, 100, 100], [0, 0, 45])  # its corner reaches 50 sqrt(2) along x
        self.assertAlmostEqual(seg_box([100, -100, 0], [100, 100, 0], turned), 100.0 - 50.0 * math.sqrt(2))

    def test_against_sampling(self):
        rng = np.random.default_rng(3)
        t = np.linspace(0.0, 1.0, 4001)
        for _ in range(40):
            box = from_box(rng.uniform(-50, 50, 3), rng.uniform(20, 200, 3), rng.uniform(-90, 90, 3))
            p, q = rng.uniform(-200, 200, 3), rng.uniform(-200, 200, 3)
            pts = p + t[:, None] * (q - p)
            sampled = _box_sdf((pts - box.center) @ box.axes.T, box.half).min()
            self.assertAlmostEqual(seg_box(p, q, box), sampled, delta=0.5)
            self.assertLessEqual(seg_box(p, q, box), sampled + 1e-9)
            p2, q2 = rng.uniform(-200, 200, 3), rng.uniform(-200, 200, 3)
            other = p2 + t[::20, None] * (q2 - p2)
            brute = np.sqrt(((pts[::4, None, :] - other[None]) ** 2).sum(-1)).min()
            self.assertAlmostEqual(float(seg_seg(p, q, p2, q2)), brute, delta=1.0)
            self.assertLessEqual(float(seg_seg(p, q, p2, q2)), brute + 1e-9)

    def test_swept_against_sampling(self):
        rng = np.random.default_rng(5)
        for _ in range(15):
            c = from_points(rng.uniform(-100, 100, 3), rng.uniform(-100, 100, 3), 10.0)
            d = np.eye(3)[rng.integers(3)] * rng.choice([-1, 1])
            box = from_box(rng.uniform(-150, 150, 3), rng.uniform(20, 120, 3), rng.uniform(-90, 90, 3))
            slides = np.linspace(0.0, 200.0, 201)
            sampled = min(max(seg_box(c.a + d * s, c.b + d * s, box), 0.0) for s in slides)
            self.assertAlmostEqual(swept_distance(c, d, 200.0, box), sampled, delta=1.0)
            rod = from_points(rng.uniform(-150, 150, 3), rng.uniform(-150, 150, 3), 5.0)
            sampled = min(float(seg_seg(c.a + d * s, c.b + d * s, rod.a, rod.b)) for s in slides)
            self.assertAlmostEqual(swept_distance(c, d, 200.0, rod), sampled, delta=1.0)

    def test_capsule_of_a_box(self):
        c = from_obb(from_box([500, 0, 0], [1100, 100, 60]))
        self.assertTrue(np.allclose(c.a, [0, 0, 0]) and np.allclose(c.b, [1000, 0, 0]))
        self.assertEqual(c.r, 50.0)


def shaft_design(kind):
    """A 1100 x 100 x 100 shaft along x and a 40 mm cube beside its box's long edge, 45 mm out in y and z."""
    return plain([part("shaft", kind, [500, 0, 0], [1100, 100, 100]), part("cube", "body", [500, 65, 65], [40, 40, 40])])


class SlenderPartsTest(unittest.TestCase):
    def test_kind_decides(self):
        self.assertIsNone(bodies(shaft_design("battery"))["part:shaft"].capsule)
        self.assertIsNotNone(bodies(shaft_design("driveshaft"))["part:shaft"].capsule)
        self.assertIsNotNone(bodies(shaft_design("tie_rod"))["part:shaft"].capsule)

    def test_interference_uses_the_capsule(self):
        self.assertEqual([h[:2] for h in interferences(shaft_design("battery"))], [("part:cube", "part:shaft")])
        self.assertEqual(interferences(shaft_design("driveshaft")), [])  # round: 45 sqrt(2) - 50 = 13.6 mm clear

    def test_two_known_points(self):
        bs = bodies(shaft_design("tie_rod"), ends=lambda d, p: ([0, 0, 0], [0, 0, 500]) if p["id"] == "shaft" else None)
        c = bs["part:shaft"].capsule
        self.assertTrue(np.allclose(c.a, [0, 0, 0]) and np.allclose(c.b, [0, 0, 500]))
        self.assertEqual(c.r, 50.0)

    def test_way_out_uses_the_round_shape(self):
        # lifting the shaft: a block off the end of its box, 35 mm out in x and in y, 10 mm above
        d = plain([part("shaft", "driveshaft", [500, 0, 0], [1100, 100, 100]),
                   part("block", "body", [1067.5, 67.5, 80], [65, 65, 40])])
        bs = bodies(d)
        self.assertIsNone(blocker(bs["part:shaft"], [bs["part:block"]], (0, 0, 1), 2000.0))  # 35 sqrt(2) > 50 - 1
        box_only = bodies(dict(d, parts=[dict(d["parts"][0], kind="battery"), d["parts"][1]]))
        self.assertEqual(blocker(box_only["part:shaft"], [box_only["part:block"]], (0, 0, 1), 2000.0), "part:block")


class CornersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.asm = disassemble(DESIGN)

    def test_groups_from_the_supports(self):
        g = corners(bodies(DESIGN))
        self.assertEqual(g, {s: [f"part:susp_{s}", f"part:wheel_{s}"] for s in ("FL", "FR", "RL", "RR")})
        lone = corners(bodies(dict(DESIGN, parts=[p for p in DESIGN["parts"] if p["id"] != "susp_FL"])))
        self.assertNotIn("FL", lone)  # one part is not a group

    def test_corners_first_then_drive_battery_electrics_body(self):
        units = [k for k, _ in self.asm["units"]]
        self.assertEqual(sorted(units[:4]), ["corner:FL", "corner:FR", "corner:RL", "corner:RR"])
        kinds = {p["id"]: p["kind"] for p in DESIGN["parts"]}
        stages = [stage(kinds[k.split(":", 1)[1]]) for k in units[4:]]
        self.assertEqual(stages, sorted(stages))
        self.assertEqual(units[4:6], ["part:motor", "part:battery"])
        self.assertLess(units.index("part:floor"), units.index("part:seat_L"))  # bottom up within a stage
        self.assertEqual(len(STAGES) + 1, stage("seat"))

    def test_inside_a_corner_and_onto_the_frame(self):
        for s, side in (("FL", 1), ("FR", -1), ("RL", 1), ("RR", -1)):
            g = self.asm["groups"][f"corner:{s}"]
            self.assertEqual([k for k, _ in g["order"]], [f"part:susp_{s}", f"part:wheel_{s}"])  # arm, then the wheel on it
            self.assertEqual(g["order"][1][1], "-y" if side > 0 else "+y")  # the wheel goes inward onto the hub
            self.assertEqual(dict(self.asm["units"])[f"corner:{s}"], "-y" if side > 0 else "+y")  # the corner inward
        order = [k for k, _ in self.asm["order"]]
        self.assertLess(order.index("part:susp_FL"), order.index("part:wheel_FL"))
        self.assertEqual(self.asm["blocked"], {})

    def test_steps_carry_both(self):
        seq = steps(DESIGN)
        self.assertEqual([s["order"] for s in seq], list(range(1, len(seq) + 1)))
        ids = [s["part"] for s in seq]
        self.assertEqual(len(ids), len(set(ids)))
        attach = [s for s in seq if is_attach(s)]
        self.assertEqual(len(attach), 4)
        self.assertEqual(len(seq) - len(attach), 19)  # the H5 count: 23 parts less 2 occupants, 2 others not assembled
        fl = ids.index("corner:FL")
        self.assertEqual(ids[fl - 2:fl], ["susp_FL", "wheel_FL"])
        self.assertIn("built off the vehicle", seq[fl - 1]["note"])
        self.assertEqual(seq[fl]["depends_on"], ["susp_FL", "wheel_FL"])
        self.assertIn("node LA", seq[fl]["note"])
        self.assertTrue(all(s["no_interference"] for s in seq))


def boxed_in(walls=True):
    """A 40 mm cube bolted at node N on a plate, walled in on four sides (10 mm clear) under two top
    plates with a 60 mm slot (in y) to lower it through. The walls go in first (kind motor, an
    earlier stage). walls=False: the plate alone."""
    parts = [part("unit", "electrical", [0, 0, 20], [40, 40, 40], ["N"]), part("bottom", "motor", [0, 0, -85], [160, 160, 50])]
    if walls:
        parts += [part("wall_px", "motor", [55, 0, 0], [50, 160, 120]), part("wall_mx", "motor", [-55, 0, 0], [50, 160, 120]),
                  part("wall_py", "motor", [0, 55, 0], [60, 50, 120]), part("wall_my", "motor", [0, -55, 0], [60, 50, 120]),
                  part("top_py", "motor", [0, 130, 70], [160, 200, 20]), part("top_my", "motor", [0, -130, 70], [160, 200, 20])]
    return plain(parts, [{"id": "N", "xyz_mm": [0, 0, 0]}])


class HandsTest(unittest.TestCase):
    def test_the_way_in(self):
        bs = bodies(plain([part("lid", "body", [0, 0, 125], [100, 100, 50])]))
        deep, in_way = hand_way([0, 0, 0], np.array([0, 0, 1.0]), list(bs.values()), set())
        self.assertEqual((deep, in_way), (150.0, ["part:lid"]))  # from above: through the lid, its top 150 mm up
        self.assertEqual(hand_way([0, 0, 0], np.array([1.0, 0, 0]), list(bs.values()), set()), (0.0, []))

    def test_no_room_names_what_takes_it(self):
        seq = steps(boxed_in())
        unit = next(s for s in seq if s["part"] == "unit")
        self.assertEqual(unit["order"], len(seq))  # the last in, down through the slot
        self.assertEqual(unit["direction"], "-z")
        self.assertTrue(unit["no_interference"])
        self.assertFalse(unit["tool_clearance"])
        # what is in the way from above, the side it came in from: the 200 x 125 mm sweep meets all six
        self.assertEqual(unit["blocked_by"], ["top_my", "top_py", "wall_mx", "wall_my", "wall_px", "wall_py"])
        self.assertIn("node N is taken from every side", unit["note"])
        self.assertTrue(unit["hand_reach"])  # 80 mm down from the top of the plates
        bare = next(s for s in steps(boxed_in(walls=False)) if s["part"] == "unit")
        self.assertTrue(bare["tool_clearance"])
        self.assertNotIn("blocked_by", bare)


if __name__ == "__main__":
    unittest.main()
