"""K0's torsion chain with the suspension's mount members: the 3D beam stiffness, the rail wall under a member's
foot, and the mounts' spring against the rails' mirror roll (frame_inputs.mount_roll_stiffness)."""

import json
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from hiraku import designgen
from hiraku.solvers.k0_handcalc import frame_inputs
from hiraku.solvers.k0_handcalc.library_joint import joint_wall

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
E, G = 210000.0, 210000.0 / 2.6
SEC = {"A": 400.0, "Iy": 3.0e4, "Iz": 8.0e4, "J": 6.0e4}


class BeamStiffnessTest(unittest.TestCase):
    """beam_stiffness against cantilever results (Euler-Bernoulli, St Venant)."""

    def cantilever(self, p0, p1, load):
        k = frame_inputs.beam_stiffness(p0, p1, SEC, E, G)
        return np.linalg.solve(k[6:, 6:], np.asarray(load, float))  # end 0 held

    def test_symmetric(self):
        k = frame_inputs.beam_stiffness([0, 0, 0], [300, -120, 200], SEC, E, G)
        self.assertTrue(np.allclose(k, k.T, rtol=0, atol=1e-6 * np.abs(k).max()))

    def test_along_x(self):
        L, P = 500.0, 100.0
        u = self.cantilever([0, 0, 0], [L, 0, 0], [0, 0, P, 0, 0, 0])  # vertical: local y up, bends on Iz
        self.assertAlmostEqual(u[2], P * L**3 / (3 * E * SEC["Iz"]), delta=1e-9 * u[2])
        u = self.cantilever([0, 0, 0], [L, 0, 0], [0, P, 0, 0, 0, 0])  # sideways: on Iy
        self.assertAlmostEqual(u[1], P * L**3 / (3 * E * SEC["Iy"]), delta=1e-9 * u[1])
        u = self.cantilever([0, 0, 0], [L, 0, 0], [0, 0, 0, 1e5, 0, 0])
        self.assertAlmostEqual(u[3], 1e5 * L / (G * SEC["J"]), delta=1e-9 * u[3])

    def test_oblique_end_load(self):
        """A load along the member's local y at a skew member's tip deflects it P L^3 / (3 E I_z) that way."""
        p1 = np.array([200.0, -150.0, -250.0])
        L = float(np.linalg.norm(p1))
        y, z = frame_inputs.member_axes(p1 / L)
        u = self.cantilever([0, 0, 0], p1, [*(100.0 * y), 0, 0, 0])
        self.assertAlmostEqual(float(u[:3] @ y), 100.0 * L**3 / (3 * E * SEC["Iz"]), delta=1e-8 * L**3 / SEC["Iz"])
        self.assertAlmostEqual(float(u[:3] @ z), 0.0, delta=1e-9)


class FootWallTest(unittest.TestCase):
    """foot_wall_theta picks the rail wall a member stands on and gives joint_wall's rotation there."""

    @classmethod
    def setUpClass(cls):
        cls.props = frame_inputs.member_props(V1)
        _, _, _, cls.y_r, cls.z_r = frame_inputs._left_rail(V1)
        rail = V1["sections"]["rt40x80x2.3"]["dims_mm"]
        cls.common = {"t": rail["t"], "E": 205000.0, "nu": 0.3, "leg": 0.0, "through": 0.0, "open_end": 0.0, "M": 1e6}
        mat = cls.props["tower_post_LA1"]["material"]
        cls.common.update(E=float(mat["E_MPa"]), nu=float(mat["nu"]))

    def theta(self, member, node):
        return frame_inputs.foot_wall_theta(V1, self.props[member], node, self.y_r, self.z_r)

    def test_tower_post_on_the_upper_wall(self):
        """The post stands straight up: the upper wall, 40 mm wide (the rail's b), the post's 30 mm across it."""
        want = joint_wall(dict(self.common, b=80.0, h=40.0, b_c=30.0, h_c=30.0, t_c=2.3))["theta_per_kNm"]
        self.assertAlmostEqual(self.theta("tower_post_LA1", "LAp1"), want, places=12)

    def test_lower_leg_on_the_inner_side_wall(self):
        """The leg leaves inboard and down, through the 80 mm side wall before the 40 mm lower wall."""
        want = joint_wall(dict(self.common, b=40.0, h=80.0, b_c=40.0, h_c=40.0, t_c=2.3))["theta_per_kNm"]
        self.assertAlmostEqual(self.theta("lower_leg_LA2", "LAp2"), want, places=12)
        self.assertGreater(self.theta("lower_leg_LA2", "LAp2"), 3.0 * self.theta("tower_post_LA1", "LAp1"))


class MountRollTest(unittest.TestCase):
    """mount_roll_stiffness on the demo and its place in joint_wall_inputs."""

    @classmethod
    def setUpClass(cls):
        cls.mr = frame_inputs.mount_roll_stiffness(V1)
        with mock.patch.object(frame_inputs, "foot_wall_theta", return_value=0.0):
            cls.rigid = frame_inputs.mount_roll_stiffness(V1)
        cls.no_cross = frame_inputs.mount_roll_stiffness(designgen.regenerate(V1, {"lower_cross": {"value": 0, "unit": "1"}}))

    def test_nodes_and_members(self):
        self.assertEqual(self.mr["nodes"], ["LAp1", "LAp2", "LB"])
        self.assertIn("lower_cross_A1", self.mr["members"])
        self.assertFalse(any(m.startswith("rollbar_") for m in self.mr["members"]))
        self.assertTrue(all(f["theta_per_kNm"] > 0.0 for f in self.mr["feet"]))

    def test_symmetric_positive(self):
        K = self.mr["K"]
        self.assertTrue(np.allclose(K, K.T))
        self.assertTrue(np.all(np.linalg.eigvalsh(K) > 0.0))

    def test_rail_wall_softens_the_mounts(self):
        """The feet's wall bending in series: several times softer than with rigid feet."""
        for i in range(2):
            self.assertLess(self.mr["K"][i, i], 0.5 * self.rigid["K"][i, i])

    def test_lower_cross_member_closes_the_u(self):
        """Without the lower cross members the legs only bend about their foot: the front posts' springs drop."""
        for i in range(2):
            self.assertLess(self.no_cross["K"][i, i], self.mr["K"][i, i])

    def test_chain_shares_moment_with_the_mounts(self):
        args = dict(loading="two_cargo", step_mm=100.0, step_supports=["FL", "FR"], wheel_mounts=False)
        with_m = frame_inputs.joint_wall_inputs(V1, **args)
        without = frame_inputs.joint_wall_inputs(V1, mounts=False, **args)
        self.assertEqual(without["mount_moments_Nmm"], {})
        self.assertEqual(set(with_m["mount_moments_Nmm"]), {"LAp1", "LAp2", "LB"})
        self.assertLess(with_m["inputs"]["M"]["value"], without["inputs"]["M"]["value"])
        self.assertEqual(with_m["joint"], "LE")


if __name__ == "__main__":
    unittest.main()
