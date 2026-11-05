"""The occupant's body in segments (designgen kit/body.occupant_parts): head, neck, trunk, upper arm, forearm,
hand, thigh, shank and foot, their sizes from the AIST items in k4_geometry.people, the elbow folded between the
shoulder and the grip, the masses by SEGMENT_SHARE, and the body as one connected piece."""

import json
import math
import unittest
from pathlib import Path

import numpy as np

from hiraku.designgen.kit import body
from hiraku.solvers.k4_geometry import bodies, people
from hiraku.solvers.k4_geometry.model import allowance, shape_overlap
from hiraku.solvers.k4_geometry.obb import from_box
from hiraku.solvers.shelf import load_path

ROOT = Path(__file__).resolve().parents[2]
DESIGNS = {n: json.loads((ROOT / "data" / "designs" / f"{n}.json").read_text(encoding="utf-8"))
           for n in ("offroad-4w-v1", "offroad-4w-nodriveshaft", "offroad-4w-tallcargo")}
V1 = DESIGNS["offroad-4w-v1"]
WHO = ("occupant_R", "occupant_L")
NEW_ITEMS = {"head_length": "A1", "head_breadth": "A2", "head_height": "A36", "cervicale_height": "I4",
             "neck_circumference": "F1", "forearm_length": "C8", "upper_arm_circumference": "F25",
             "forearm_circumference": "F26", "hand_length": "L1", "hand_breadth": "L10", "hand_thickness": "L16"}


def person(design, who):
    return [p for p in design["parts"] if p["kind"] == "occupant" and body.person_of(p["id"]) == who]


def seat_top(design, who):
    seats = [p for p in design["parts"] if p["kind"] == "seat"]
    trunk = next(p for p in design["parts"] if p["id"] == who)
    seat = min(seats, key=lambda s: abs(s["box"]["center_mm"][1] - trunk["box"]["center_mm"][1]))
    return seat["box"]["center_mm"][2] + seat["box"]["size_mm"][2] / 2.0


def bottom(p):
    return p["box"]["center_mm"][2] - p["box"]["size_mm"][2] / 2.0


def top(p):
    return p["box"]["center_mm"][2] + p["box"]["size_mm"][2] / 2.0


class SizesTest(unittest.TestCase):
    def test_items_for_both_sizes_with_their_source(self):
        for size in (people.BIG, people.SMALL):
            for k in NEW_ITEMS:
                self.assertGreater(people.SIZES[size][k], 0.0, (size, k))
        for item in NEW_ITEMS.values():
            self.assertIn(f" {item},", people.BODY_SOURCE.replace(" read", ","))
        self.assertIn("2026-11-04", people.BODY_SOURCE)

    def test_shares_sum_to_one(self):
        two = sum(body.SEGMENT_SHARE[s] for s in body.SEGMENTS)
        self.assertAlmostEqual(body.SEGMENT_SHARE["trunk"] + body.SEGMENT_SHARE["head_neck"] + 2 * two, 1.0, places=12)


class ElbowTest(unittest.TestCase):
    S = np.array([0.0, 0.0, 0.0])

    def test_folds_with_the_two_lengths(self):
        g = np.array([500.0, -100.0, -150.0])
        for out in (1.0, -1.0, 0.0):
            e, reached = body.elbow_point(self.S, g, 336.4, 358.0, out)
            self.assertTrue(reached)
            self.assertAlmostEqual(float(np.linalg.norm(e - self.S)), 336.4, places=6)
            self.assertAlmostEqual(float(np.linalg.norm(g - e)), 358.0, places=6)
            mid = (self.S + g) / 2.0
            self.assertLess(e[2], mid[2])  # bent down
        e_out, _ = body.elbow_point(self.S, g, 336.4, 358.0, 1.0)
        e_in, _ = body.elbow_point(self.S, g, 336.4, 358.0, -1.0)
        self.assertGreater(e_out[1], e_in[1])  # outward is +y for +1

    def test_straight_beyond_reach(self):
        g = np.array([1000.0, 0.0, 0.0])
        e, reached = body.elbow_point(self.S, g, 336.4, 358.0, 1.0)
        self.assertFalse(reached)
        np.testing.assert_allclose(e, [336.4, 0.0, 0.0], atol=1e-9)


class BodyInTheDesignsTest(unittest.TestCase):
    def test_ids(self):
        for who in WHO:
            ids = {p["id"] for p in person(V1, who)}
            want = {who, f"{who}.head", f"{who}.neck"} | {f"{who}.{s}_{side}" for s in body.SEGMENTS for side in "LR"}
            self.assertEqual(ids, want)
            self.assertTrue(all(p["kind"] == "occupant" for p in person(V1, who)))

    def test_mass_to_the_last_digit_and_shares(self):
        for d in DESIGNS.values():
            m = d["variables"]["occupant_mass"]["value"] if "occupant_mass" in d.get("variables", {}) else 76.6
            for who in WHO:
                parts = {p["id"]: p for p in person(d, who)}
                self.assertEqual(sum(p["mass_kg"] for p in parts.values()), m)
                head_neck = parts[f"{who}.head"]["mass_kg"] + parts[f"{who}.neck"]["mass_kg"]
                self.assertAlmostEqual(head_neck, m * body.SEGMENT_SHARE["head_neck"], places=9)
                for s in body.SEGMENTS:
                    self.assertAlmostEqual(parts[f"{who}.{s}_L"]["mass_kg"], m * body.SEGMENT_SHARE[s], places=9)
                self.assertAlmostEqual(parts[who]["mass_kg"], m * body.SEGMENT_SHARE["trunk"], places=9)

    def test_trunk_neck_head_stacked_on_the_seat(self):
        s = people.SIZES[people.BIG]
        for who in WHO:
            parts = {p["id"]: p for p in person(V1, who)}
            z0 = seat_top(V1, who)
            trunk, neck, head = parts[who], parts[f"{who}.neck"], parts[f"{who}.head"]
            self.assertAlmostEqual(bottom(trunk), z0, places=6)
            self.assertAlmostEqual(top(trunk), z0 + s["cervicale_height"], places=6)
            self.assertAlmostEqual(bottom(neck), top(trunk), places=6)
            self.assertAlmostEqual(top(neck), bottom(head), places=6)
            self.assertAlmostEqual(top(head), z0 + s["sitting_height"], places=6)
            self.assertAlmostEqual(head["box"]["size_mm"][2], s["head_height"], places=9)
            self.assertAlmostEqual(neck["box"]["size_mm"][0], s["neck_circumference"] / math.pi, places=9)
            # the trunk keeps its plan (point R and the knee room read it): 2 x J9 deep, D2 wide
            self.assertAlmostEqual(trunk["box"]["size_mm"][0], 2.0 * s["buttock_trochanter"], places=6)
            self.assertAlmostEqual(trunk["box"]["size_mm"][1], people.AIST["shoulder_breadth_m95"], places=9)

    def test_point_r_on_the_trunk(self):
        s = people.SIZES[people.BIG]
        for t in load_path.occupant_trunks(V1):
            z0 = seat_top(V1, t["id"])
            pts = dict((n, x) for n, x, _ in load_path.point_r(t))
            self.assertAlmostEqual(float(pts["hip"][2]), z0 + s["trochanter_height"], places=6)
            self.assertAlmostEqual(float(pts["shoulder"][2]), z0 + s["shoulder_height"], places=6)

    def test_one_connected_body_and_apart_from_the_other(self):
        """load_path joins parts that touch within its TOUCH_MM: every person's parts form one piece; and no part
        of one person is inside another person's (K4 counts that as interference)."""
        for d in DESIGNS.values():
            bs = bodies(d)
            for who in WHO:
                ids = [p["id"] for p in person(d, who)]
                seen, todo = {ids[0]}, [ids[0]]
                while todo:
                    x = todo.pop()
                    for y in ids:
                        if y not in seen and shape_overlap(bs[f"part:{x}"], bs[f"part:{y}"]) >= -load_path.TOUCH_MM:
                            seen.add(y)
                            todo.append(y)
                self.assertEqual(seen, set(ids), who)
            for a in person(d, WHO[0]):
                for b in person(d, WHO[1]):
                    A, B = bs[f"part:{a['id']}"], bs[f"part:{b['id']}"]
                    self.assertLessEqual(shape_overlap(A, B), allowance(A, B, d), (a["id"], b["id"]))

    def test_driver_holds_the_wheel(self):
        """The grips on the steering wheel's rim are inside the driver's hands, C12 - C8 past the wrist."""
        wheel = next(p for p in V1["parts"] if p["id"] == "steering_wheel")
        parts = {p["id"]: p for p in V1["parts"]}
        for g in people.grip_points(wheel):
            hands = [parts[f"occupant_R.hand_{s}"] for s in "LR"]
            inside = []
            for h in hands:
                o = from_box(h["box"]["center_mm"], h["box"]["size_mm"], h["box"].get("rpy_deg"))
                local = np.abs(o.axes @ (np.asarray(g) - o.center))
                inside.append(bool(np.all(local <= o.half + 1e-6)))
            self.assertEqual(sum(inside), 1, g)


if __name__ == "__main__":
    unittest.main()
