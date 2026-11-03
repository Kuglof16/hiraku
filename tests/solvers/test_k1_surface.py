import json
import math
import unittest
from pathlib import Path

import numpy as np

from hiraku import contract, solvers
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
        self.assertAlmostEqual(f["per_element"][0], P * (L - 5.0) * (H / 2.0) / IZ, delta=5e-4)  # rounded to 0.001 MPa
        self.assertAlmostEqual(f["per_element"][0], f["per_element"][1], places=9)
        contract.validate("field", f)


class FrameStaticOnTheMeshTest(unittest.TestCase):
    """frame_static draws the beam stress on the version's shell mesh when it is passed (needs_mesh)."""

    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[2]
        cls.design = json.loads((root / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
        cls.mesh = dict(solvers.frame_mesh(cls.design), mesh_id="mesh-0001")
        cls.tool = {t.name: t for t in solvers.builtin_tools()}["frame_static"]
        cls.lc = {"load_case_id": "R1-step100", "test_id": "R1", "domain": "strength", "title": "R1",
                  "params": {"step_height": {"value": 100.0, "unit": "mm"}}, "tolerance_rel": 0.15}

    def test_field_on_the_mesh(self):
        self.assertTrue(self.tool.needs_mesh)
        rec = self.tool.run(self.design, self.lc, [{"prediction_id": "pred-0001"}], {"mesh": self.mesh})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        f = next(f for f in rec["fields"] if f.get("mesh_id") == "mesh-0001")
        self.assertEqual((f["label"], len(f["per_element"])), (LABEL, len(self.mesh["elements"])))
        per_member = next(f for f in rec["fields"] if "per_member" in f)["per_member"]
        painted = {}
        for m, v in zip(self.mesh["element_member"], f["per_element"]):
            painted[m] = max(painted.get(m, 0.0), v)
        for m, v in painted.items():  # element centres are not at the corners, where the beam stress is largest
            self.assertLessEqual(v, per_member[m] * (1 + 1e-6), m)
        worst = max(per_member, key=per_member.get)
        self.assertGreater(painted[worst], 0.5 * per_member[worst])
        contract.validate("analysis", dict(rec, analysis_id="ana-0001"))

    def test_mesh_of_another_version_is_not_drawn(self):
        rec = self.tool.run(self.design, self.lc, [{"prediction_id": "pred-0001"}],
                            {"mesh": dict(self.mesh, design_version=2)})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        self.assertFalse(any("mesh_id" in f for f in rec["fields"]))
        self.assertTrue(any("not drawn" in e["name"] for e in rec["effects"]))


if __name__ == "__main__":
    unittest.main()
