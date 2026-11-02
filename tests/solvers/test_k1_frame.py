import copy
import unittest

import numpy as np

from hiraku.solvers.k0_handcalc import sections
from hiraku.solvers.k0_handcalc.vehicle import DesignError
from hiraku.solvers.k1_frame import analyze, build, to_design, to_pynite
from hiraku.solvers.k1_frame.model import N_SUB
from hiraku.solvers.shelf import fixtures

RECT = {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 80.0, "t": 2.0}}


class AxesTest(unittest.TestCase):
    """PyNite orients local axes with global Y up; the design is z up (ISO 8855)."""

    def test_rotation_round_trip_and_up(self):
        v = np.array([1.0, 2.0, 3.0])
        self.assertTrue(np.allclose(to_design(to_pynite(v)), v))
        self.assertTrue(np.allclose(to_pynite([0, 0, 1]), [0, 1, 0]))   # design up is PyNite Y
        self.assertTrue(np.allclose(to_pynite([0, 1, 0]), [0, 0, -1]))  # design left is PyNite -Z
        self.assertAlmostEqual(np.linalg.det(np.array([to_pynite(e) for e in np.eye(3)])), 1.0)

    def test_horizontal_member_local_y_points_up(self):
        fm = build(fixtures.beam_design(1000.0, RECT))
        fm.model.members["m"].descritize()
        sub = next(iter(fm.model.members["m"].sub_members.values()))
        T = sub.T()[:3, :3]  # rows: local x, y, z in PyNite axes
        self.assertTrue(np.allclose(to_design(T[1]), [0, 0, 1]))  # local y = design up
        self.assertTrue(np.allclose(np.abs(to_design(T[2])), [0, 1, 0]))  # local z = sideways

    def test_vertical_load_bends_the_h_side(self):
        p = sections.properties(RECT)
        for force, I, comp in (((0, 0, -1000.0), p["Iz"], 2), ((0, 1000.0, 0), p["Iy"], 1)):
            fm = build(fixtures.beam_design(1000.0, RECT))
            fm.model.def_support("A", True, True, True, True, True, True)
            fm.add_force("B", force)
            fm.solve()
            want = 1000.0 * 1000.0**3 / (3 * 205000.0 * I)
            self.assertAlmostEqual(abs(fm.displacement("B")[comp]), want, delta=want * 1e-9)

    def test_members_have_at_least_four_elements(self):
        fm = build(fixtures.ladder_design())
        self.assertGreaterEqual(N_SUB, 4)
        for mid in fm.props:
            fm.model.members[mid].descritize()
            self.assertGreaterEqual(len(fm.model.members[mid].sub_members), 4, mid)


class FrameErrorsTest(unittest.TestCase):
    def test_missing_node_is_explained(self):
        d = fixtures.ladder_design()
        d["members"][0]["node_b"] = "nowhere"
        with self.assertRaisesRegex(DesignError, "nowhere"):
            build(d)

    def test_support_without_frame_node(self):
        d = fixtures.ladder_design()
        del d["supports"][0]["frame_node"]
        with self.assertRaisesRegex(DesignError, "frame_node"):
            build(d)

    def test_disconnected_frame_is_reported_not_nan(self):
        d = fixtures.ladder_design()
        d["nodes"].append({"id": "lost", "xyz_mm": [500.0, 0.0, 400.0]})
        d["nodes"].append({"id": "lost2", "xyz_mm": [600.0, 0.0, 400.0]})
        d["members"].append({"id": "floating", "node_a": "lost", "node_b": "lost2", "section": "rt", "material": "st"})
        with self.assertRaises(DesignError):
            analyze(d)


class FrameEquilibriumTest(unittest.TestCase):
    def test_rest_reactions_sum_to_weight(self):
        res = analyze(fixtures.ladder_design(payload_x=-700.0))
        total = sum(g["force_N"] for g in res["ground"].values())
        self.assertAlmostEqual(total[2], res["weight_N"], delta=res["weight_N"] * 1e-9)
        self.assertAlmostEqual(abs(total[0]) + abs(total[1]), 0.0, delta=res["weight_N"] * 1e-9)

    def test_step_loads_are_self_balanced(self):
        for wheels in (["FL", "FR"], ["FL"], ["FR"]):
            res = analyze(fixtures.ladder_design(), step_mm=100.0, step_supports=wheels)
            self.assertLess(res["residual_ratio"], 1e-6, wheels)

    def test_mirror_symmetry(self):
        a = analyze(fixtures.ladder_design(), step_mm=100.0, step_supports=["FL"])
        b = analyze(fixtures.ladder_design(), step_mm=100.0, step_supports=["FR"])
        for left, right in (("FL", "FR"), ("RL", "RR")):
            fa, fb = a["ground"][left]["force_N"], b["ground"][right]["force_N"]
            self.assertTrue(np.allclose(fa * [1, -1, 1], fb, rtol=1e-9, atol=1e-6), (left, fa, fb))
        sa = a["stresses"]["railLa"]["max"]
        sb = b["stresses"]["railRa"]["max"]
        self.assertAlmostEqual(sa, sb, delta=sa * 1e-9)

    def test_spring_supports_give_the_same_weight(self):
        # soft springs with stiff wheel links once tripped PyNite's singular-matrix residual check
        d = copy.deepcopy(fixtures.ladder_design())
        for s in d["supports"]:
            s["suspension"] = {"type": "spring", "stiffness_N_per_mm": 5.0}
        res = analyze(d)
        total = sum(g["force_N"][2] for g in res["ground"].values())
        self.assertAlmostEqual(total, res["weight_N"], delta=res["weight_N"] * 1e-7)  # soft springs: ~3e-9 round-off
        # spring sag is removed from the reported deflection
        self.assertLess(max(np.linalg.norm(u) for u in res["displacements"].values()), 5.0)


if __name__ == "__main__":
    unittest.main()
