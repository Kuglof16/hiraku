"""K&C bench: double-wishbone loop closure against closed-form geometry."""

import math
import unittest

import numpy as np

from hiraku.solvers.k2_vehicle import kinematics as kc


def parallel_equal(radius=0.3):
    wc = np.array([0.0, 0.65, 0.3])
    lbj = np.array([0.0, 0.59, 0.18])
    ubj = np.array([0.0, 0.59, 0.43])
    arm = 0.35
    return kc.Hardpoints(
        lca_front=np.array([0.12, lbj[1] - arm, lbj[2]]), lca_rear=np.array([-0.12, lbj[1] - arm, lbj[2]]), lca_outer=lbj,
        uca_front=np.array([0.10, ubj[1] - arm, ubj[2]]), uca_rear=np.array([-0.10, ubj[1] - arm, ubj[2]]), uca_outer=ubj,
        tie_inner=np.array([-0.12, lbj[1] - arm, 0.28]), tie_outer=np.array([-0.12, 0.59, 0.28]),
        wheel_center=wc, axle=np.array([0.0, 1.0, 0.0]), radius=radius)


class Bench(unittest.TestCase):
    def test_parallel_equal_arms_keep_camber_and_put_roll_centre_on_ground(self):
        table = kc.build_table(parallel_equal(), 0.06)
        cambers = [r["camber_deg"] for r in table["rows"]]
        self.assertLess(max(abs(c) for c in cambers), 1e-9)
        mid = table["rows"][len(table["rows"]) // 2]
        self.assertAlmostEqual(mid["travel_mm"], 0.0, places=9)
        self.assertLess(abs(mid["roll_center_height_mm"]), 0.05)
        # loop closure: arm lengths are kept at full bump
        bench = kc.DoubleWishboneBench(parallel_equal())
        sol = bench.solve(0.1)
        self.assertAlmostEqual(float(np.linalg.norm(sol["ubj"] - sol["lbj"])), bench.l_king, places=12)

    def test_sla_camber_gain_and_roll_centre_from_instant_centre(self):
        hp = kc.generic_sla(np.array([0.0, 0.65, 0.3]), 0.3)
        table = kc.build_table(hp, 0.06, n=121)
        rows = table["rows"]
        s = np.array([r["travel_mm"] for r in rows]) / 1000.0
        cam = np.radians([r["camber_deg"] for r in rows])
        gain = float(np.gradient(cam, s)[len(s) // 2])
        ic = kc.front_view_ic(hp)
        self.assertIsNotNone(ic)
        expected = -1.0 / (hp.wheel_center[1] - ic[0])
        self.assertAlmostEqual(gain, expected, delta=0.02 * abs(expected))
        # roll centre: line from the contact point through the IC, at the centreline
        mid = rows[len(rows) // 2]
        cp = np.array(mid["contact_mm"]) / 1000.0
        rc = cp[2] + (ic[1] - cp[2]) * (0.0 - cp[1]) / (ic[0] - cp[1])
        self.assertAlmostEqual(mid["roll_center_height_mm"] / 1000.0, rc, delta=0.002)

    def test_path_polynomial_reproduces_table(self):
        hp = kc.generic_sla(np.array([0.0, 0.65, 0.3]), 0.3)
        table = kc.build_table(hp, 0.06)
        path = kc.PathPoly.from_table(table)
        for r in table["rows"][::10]:
            s = r["travel_mm"] / 1000.0
            dy = np.polyval(path.coef[1], s)
            self.assertAlmostEqual(dy * 1000.0, r["wheel_center_mm"][1], delta=0.01)
            cam = math.degrees(np.polyval(path.coef[3], s) + path.camber0)
            self.assertAlmostEqual(cam, r["camber_deg"], delta=1e-3)


class UpperPivotSpread(unittest.TestCase):
    """hardpoints_for's upper_pivot_spread_mm (the upper arm's inner pivots apart along x)."""

    def test_the_default_keeps_the_points_and_a_wider_spread_moves_only_the_upper_pivots_apart(self):
        from hiraku.solvers.k2_vehicle.model import hardpoints_for
        sup = {"id": "FL", "position_mm": [0.0, 600.0, 300.0], "radius_mm": 300.0,
               "suspension": {"type": "kc_table", "travel_mm": 150.0}}
        plain = hardpoints_for(sup, upper_joint_z_mm=470.0)
        same = hardpoints_for(sup, upper_joint_z_mm=470.0, upper_pivot_spread_mm=200.0)
        self.assertEqual(plain, same)
        wide = hardpoints_for(sup, upper_joint_z_mm=470.0, upper_pivot_spread_mm=300.0)
        self.assertAlmostEqual(wide["uca_front"][0] - wide["uca_rear"][0], 300.0, places=6)
        for k in ("lca_front", "lca_rear", "lca_outer", "uca_outer", "damper_lower", "damper_upper"):
            np.testing.assert_allclose(wide[k], plain[k], atol=1e-9, err_msg=k)
        # the linkage still moves through the travel; the tie rod is placed again
        self.assertIn("tie_inner", wide)


if __name__ == "__main__":
    unittest.main()
