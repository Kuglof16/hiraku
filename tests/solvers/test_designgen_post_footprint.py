"""Posts standing on a rail: their footprint on the rail's top face is centred across the rail and leaves
ladder_4w.POST_FOOTPRINT_SPARE_MM of the face on each side, else the generator refuses with the rail_b it needs."""
import math
import unittest

from hiraku import designgen
from hiraku.designgen import ladder_4w as g
from hiraku.designgen.kit.sections import rect_tube, round_tube


def _design(post_top, post_section):
    """A rail piece either side of node N (rail 40 x 80) and one post from N to post_top."""
    rk, rs = rect_tube(40.0, 80.0, 2.3)
    pk, ps = post_section
    return {"nodes": [{"id": "N", "xyz_mm": [0.0, 350.0, 380.0]}, {"id": "F", "xyz_mm": [500.0, 350.0, 380.0]},
                      {"id": "E", "xyz_mm": [-500.0, 350.0, 380.0]}, {"id": "T", "xyz_mm": list(post_top)}],
            "members": [{"id": "rail_L_FN", "node_a": "F", "node_b": "N", "section": rk},
                        {"id": "rail_L_NE", "node_a": "N", "node_b": "E", "section": rk},
                        {"id": "tower_post_X", "node_a": "N", "node_b": "T", "section": pk}],
            "sections": {rk: rs, pk: ps}}


class FootprintGeometryTest(unittest.TestCase):
    def test_upright_post(self):
        fs = g.rail_post_footprints(_design([0.0, 350.0, 600.0], rect_tube(30.0, 30.0, 2.3)))
        self.assertEqual({f["rail"] for f in fs}, {"rail_L_FN", "rail_L_NE"})
        for f in fs:
            self.assertAlmostEqual(f["y_min"], 335.0)
            self.assertAlmostEqual(f["y_max"], 365.0)
            self.assertAlmostEqual(f["spare_mm"], 5.0)
            self.assertAlmostEqual(f["need_rail_b"], 32.0)

    def test_post_leaning_across_the_rail(self):
        # 20 mm out over 212 mm: at the top face (40 mm above the node) the axis is 40 * 20 / 212 outboard and the
        # 30 mm wall pair is cut 30 / cos(lean) wide
        fs = g.rail_post_footprints(_design([0.0, 370.0, 592.0], rect_tube(30.0, 30.0, 2.3)))
        c, half = 350.0 + 40.0 * 20.0 / 212.0, 15.0 * math.hypot(20.0, 212.0) / 212.0
        self.assertAlmostEqual(fs[0]["y_min"], c - half)
        self.assertAlmostEqual(fs[0]["y_max"], c + half)
        self.assertAlmostEqual(fs[0]["spare_mm"], 370.0 - (c + half))
        self.assertAlmostEqual(fs[0]["need_rail_b"], 2.0 * (c + half - 350.0 + 1.0))

    def test_round_post(self):
        fs = g.rail_post_footprints(_design([0.0, 350.0, 600.0], round_tube(42.7, 2.3)))
        self.assertAlmostEqual(fs[0]["spare_mm"], (40.0 - 42.7) / 2.0)

    def test_a_member_hanging_below_is_not_a_post(self):
        self.assertEqual(g.rail_post_footprints(_design([0.0, 350.0, 200.0], rect_tube(30.0, 30.0, 2.3))), [])


class GeneratorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = g.build()

    def test_default_posts_centred_with_spare(self):
        fs = g.rail_post_footprints(self.d)
        posts = {f["member"] for f in fs}
        self.assertEqual(posts, {f"tower_post_{s}{t}" for s in "LR" for t in ("A1", "A2", "B")})
        tb, rb = (self.d["variables"][k]["value"] for k in ("tower_b", "rail_b"))
        for f in fs:
            self.assertAlmostEqual((f["y_min"] + f["y_max"]) / 2.0, f["rail_y"], msg=f["member"])
            self.assertAlmostEqual(f["spare_mm"], (rb - tb) / 2.0, msg=f["member"])
            self.assertGreaterEqual(f["spare_mm"], g.POST_FOOTPRINT_SPARE_MM)

    def test_rear_post_leans_back_only(self):
        xyz = {n["id"]: n["xyz_mm"] for n in self.d["nodes"]}
        for side in "LR":
            m = next(m for m in self.d["members"] if m["id"] == f"tower_post_{side}B")
            a, b = xyz[m["node_a"]], xyz[m["node_b"]]
            self.assertEqual(a[1], b[1])  # over the rail's centre line
            self.assertNotEqual(a[0], b[0])  # not square to the rail

    def test_a_post_without_the_spare_is_refused(self):
        # tower_b 39 < rail_b 40 passes the older check; centred it leaves 0.5 mm each side
        with self.assertRaises(designgen.DesignGenError) as cm:
            g.build({"tower_b": 39.0, "tower_t": 2.3})
        msg = str(cm.exception)
        self.assertRegex(msg, r"post tower_post_[LR](A1|A2|B) on rail")
        self.assertIn("leaves only 0.5 mm", msg)
        self.assertIn("rail_b of at least 41.0 mm", msg)

    def test_roll_bar_post_on_the_rail_is_refused(self):
        # rollbar_width = rail_spacing: the posts stand on the rails; the round tube 42.7 is wider than the rail 40
        with self.assertRaises(designgen.DesignGenError) as cm:
            g.build({"rollbar_width": 700.0})
        msg = str(cm.exception)
        self.assertRegex(msg, r"post rollbar_post_[LR] on rail")
        self.assertIn("reaches 1.4 mm past the rail's side", msg)
        self.assertIn("rail_b of at least 44.7 mm", msg)


if __name__ == "__main__":
    unittest.main()
