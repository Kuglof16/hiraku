import math
import unittest

import numpy as np

from hiraku import contract
from hiraku.solvers.k1_frame import build
from hiraku.solvers.k1_frame.surface import LABEL, surface_field, wall_von_mises
from hiraku.solvers.shelf import fixtures

B, H, T = 40.0, 80.0, 2.0
RECT = {"shape": "rect_tube", "dims_mm": {"b": B, "h": H, "t": T}}
A = B * H - (B - 2 * T) * (H - 2 * T)
IZ = (B * H**3 - (B - 2 * T) * (H - 2 * T) ** 3) / 12.0  # h direction
IY = (H * B**3 - (H - 2 * T) * (B - 2 * T) ** 3) / 12.0  # b direction
L = 1000.0
P = 1000.0


def wall_point(s, along, h_dir, b_dir, wall):
    """Centre of the mid-surface of one wall ('+h', '-h', '+b', '-b') at s along the member."""
    hm, bm = (H - T) / 2.0, (B - T) / 2.0
    off = {"+h": hm * h_dir, "-h": -hm * h_dir, "+b": bm * b_dir, "-b": -bm * b_dir}[wall]
    return s * np.asarray(along, float) + off


def cantilever(force, node_b=(L, 0.0, 0.0), section=RECT, moment=(0.0, 0.0, 0.0)):
    d = fixtures.beam_design(L, section)
    d["nodes"][1]["xyz_mm"] = list(node_b)
    fm = build(d)
    fm.model.def_support("A", True, True, True, True, True, True)
    fm.add_force("B", force, moment)
    fm.solve()
    return fm


class WallStressTest(unittest.TestCase):
    def test_vertical_load_and_pull_on_a_horizontal_member(self):
        fm = cantilever((P, 0.0, -P))  # pull along the member and push down at the tip
        s = 250.0
        x, z = np.array([1.0, 0.0, 0.0]), np.array([0.0, 0.0, 1.0])
        top, bottom = (wall_point(s, x, z, np.array([0.0, 1.0, 0.0]), w) for w in ("+h", "-h"))
        got = wall_von_mises(fm, "m", [top, bottom])
        bend = P * (L - s) * (H / 2.0) / IZ  # hogging: top in tension
        self.assertAlmostEqual(got[0], P / A + bend, delta=1e-6 * bend)
        self.assertAlmostEqual(got[1], abs(P / A - bend), delta=1e-6 * bend)

    def test_side_load_on_a_horizontal_member(self):
        fm = cantilever((P, P, 0.0))  # pull and push toward +y at the tip
        s = 100.0
        x, y = np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])
        right, left = (wall_point(s, x, np.array([0.0, 0.0, 1.0]), y, w) for w in ("-b", "+b"))
        got = wall_von_mises(fm, "m", [right, left])
        bend = P * (L - s) * (B / 2.0) / IY  # the tip moves to +y: the -y side is stretched
        self.assertAlmostEqual(got[0], P / A + bend, delta=1e-6 * bend)
        self.assertAlmostEqual(got[1], abs(P / A - bend), delta=1e-6 * bend)

    def test_vertical_member(self):
        fm = cantilever((P, 0.0, P), node_b=(0.0, 0.0, L))  # a column pulled up and pushed toward +x
        s = 300.0
        up, x = np.array([0.0, 0.0, 1.0]), np.array([1.0, 0.0, 0.0])
        back, front = (wall_point(s, up, x, np.array([0.0, 1.0, 0.0]), w) for w in ("-h", "+h"))
        got = wall_von_mises(fm, "m", [back, front])
        bend = P * (L - s) * (H / 2.0) / IZ  # h lies along x on a vertical member; the -x side is stretched
        self.assertAlmostEqual(got[0], P / A + bend, delta=1e-6 * bend)
        self.assertAlmostEqual(got[1], abs(P / A - bend), delta=1e-6 * bend)

    def test_torsion_of_a_round_tube(self):
        d, t, Tq = 50.0, 3.0, 500_000.0
        fm = cantilever((0.0, 0.0, 0.0), section={"shape": "round_tube", "dims_mm": {"d": d, "t": t}}, moment=(Tq, 0.0, 0.0))
        rm = (d - t) / 2.0
        pts = [[500.0, rm * math.cos(a), rm * math.sin(a)] for a in (0.3, 2.0, 4.0)]
        J = math.pi / 32.0 * (d**4 - (d - 2 * t) ** 4)
        np.testing.assert_allclose(wall_von_mises(fm, "m", pts), math.sqrt(3.0) * Tq * (d / 2.0) / J, rtol=1e-9)


class SurfaceFieldTest(unittest.TestCase):
    def test_one_value_per_element(self):
        fm = cantilever((0.0, 0.0, -P))
        hm, bm = (H - T) / 2.0, (B - T) / 2.0
        nodes = [[0.0, -bm, hm], [10.0, -bm, hm], [10.0, bm, hm], [0.0, bm, hm],
                 [0.0, -bm, -hm], [10.0, -bm, -hm], [10.0, bm, -hm], [0.0, bm, -hm]]
        mesh = {"mesh_id": "mesh-0001", "design_version": 1, "element_type": "shell", "nodes_mm": nodes,
                "elements": [[0, 1, 2, 3], [4, 7, 6, 5]], "element_member": ["m", "m"], "member_thickness_mm": {"m": T}}
        f = surface_field(fm, mesh)
        self.assertEqual((f["mesh_id"], f["label"], len(f["per_element"])), ("mesh-0001", LABEL, 2))
        self.assertAlmostEqual(f["per_element"][0], P * (L - 5.0) * (H / 2.0) / IZ, places=6)
        self.assertAlmostEqual(f["per_element"][0], f["per_element"][1], places=9)
        if "mesh_id" in contract.schema()["$defs"]["field"]["properties"]:
            contract.validate("field", f)


if __name__ == "__main__":
    unittest.main()
