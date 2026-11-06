"""Gussets the shell mesh cannot place are refused when the version is built (not when its shell is meshed), with
what places them: with the side frame's feet on the rails' outer walls, cross members through the rails tie the
joints next to them and the gussets there have no T joint."""
import unittest

from hiraku import designgen
from hiraku.designgen import ladder_4w as g
from hiraku.solvers.k1_shell import mesh as shell_mesh

BOTH = {"side_bars": 1.0, "rollbar_width": 1400.0, "gusset_leg": 60.0, "cross_members_through": 1.0}


class GussetMeshTest(unittest.TestCase):
    def test_refused_with_through_off(self):
        with self.assertRaises(designgen.DesignGenError) as cm:
            g.build(BOTH)
        msg = str(cm.exception)
        self.assertIn("has no T joint", msg)
        self.assertIn("is tied", msg)
        self.assertIn("make cross_members_through 0", msg)
        d = g.build(dict(BOTH, cross_members_through=0.0))  # builds, and the mesh places the gussets
        shell_mesh.joint_layout(d)

    def test_gussets_alone_still_build(self):
        d = g.build({"gusset_leg": 60.0, "cross_members_through": 1.0})
        self.assertTrue(any(p["kind"] == "gusset" for p in d["parts"]))


# the scripted run's version 5 (its variables away from the defaults) with the tower tubes 35 x 4.5
V005_LIKE = {"track": 1220.0, "motor_peak_power": 17.0, "rail_t": 4.5, "cross_h": 50.0, "seat_x": -1170.0,
             "battery_x": -1170.0, "seat_height": 232.5, "steering_column_angle": 20.0, "steering_wheel_height": 512.0,
             "pedal_x": -458.0, "seat_slide": 300.0, "gusset_leg": 80.0, "gusset_thickness": 3.2,
             "rollbar_width": 1265.0, "side_bars": 1.0, "side_net": 1.0, "rollbar_foot_h": 60.0, "fenders": 1.0,
             "side_opening": 1.0, "grab_bar": 1.0, "tower_b": 35.0, "tower_t": 4.5}
CORNERS = {"LF": "rear", "RF": "rear", "LE": "front", "RE": "front"}  # the side each corner's rail runs on


class CornerGussetTest(unittest.TestCase):
    """gusset_ends 1: gussets also at the frame's four end corners (L joints), on the inside corner only."""

    def corner_ids(self):
        return {f"gusset_{n}_{fb}_{ul}" for n, fb in CORNERS.items() for ul in ("upper", "lower")}

    def test_v005_like_corners_mesh_with_their_gussets(self):
        from hiraku.solvers.k1_shell.mesh import build_mesh
        for leg, t in ((80.0, 3.2), (120.0, 4.5)):
            with self.subTest(leg=leg, t=t):
                d = g.build(dict(V005_LIKE, gusset_leg=leg, gusset_thickness=t, gusset_ends=1.0))
                parts = {p["id"]: p for p in d["parts"] if p["kind"] == "gusset"}
                self.assertLessEqual(self.corner_ids(), set(parts))
                xyz = {n["id"]: n["xyz_mm"] for n in d["nodes"]}
                for gid in self.corner_ids():
                    p = parts[gid]
                    node = p["carried_by"][0]
                    self.assertEqual(p["box"]["size_mm"], [leg, leg, t])
                    # the inside corner: the plate lies on the side the rail runs on, and inside the frame in y
                    sx = 1.0 if CORNERS[node] == "front" else -1.0
                    self.assertGreater(sx * (p["box"]["center_mm"][0] - xyz[node][0]), 0.0)
                    self.assertLess(abs(p["box"]["center_mm"][1]), abs(xyz[node][1]))
                ms = build_mesh(d, 10.0)
                for node in CORNERS:
                    self.assertEqual(ms.joint_kinds[node], "L")
                    self.assertTrue(any(w.joint == node for w in ms.weld_lines))
                member = ms.element_member
                for gid in self.corner_ids():
                    self.assertEqual(ms.part_material[gid], "cross_F" if gid[8] == "F" or gid[9] == "F" else "cross_E")
                    self.assertGreater(sum(1 for m in member if m == gid), 0)

    def test_without_the_switch_no_corner_gussets(self):
        d0 = g.build(V005_LIKE)
        self.assertEqual(g.build(dict(V005_LIKE, gusset_ends=0.0))["parts"], d0["parts"])
        self.assertFalse(self.corner_ids() & {p["id"] for p in d0["parts"]})
        d1 = g.build(dict(V005_LIKE, gusset_ends=1.0))
        self.assertEqual(len(d1["parts"]) - len(d0["parts"]), 8)


if __name__ == "__main__":
    unittest.main()
