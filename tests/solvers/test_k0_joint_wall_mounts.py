"""frame_inputs.joint_wall_inputs compares the joints at the wheel mounts too (frame_shell reads the hot spots
at a mount's cross member weld): on v1 with rail_t 4.5 mm at R1s's 100 mm step the joint picked is LB
(cross_B), the place of the shell's largest hot spot (RB, its mirror)."""

import unittest

from hiraku.designgen.ladder_4w import build
from hiraku.solvers import formula_registry
from hiraku.solvers.k0_handcalc import frame_inputs
from hiraku.solvers.shelf.frame_static import front_supports


def r1s(design, **kw):
    return frame_inputs.joint_wall_inputs(design, loading="two_cargo", step_mm=100.0,
                                          step_supports=front_supports(design), **kw)


class WheelMountJointsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reg = formula_registry()
        cls.t45 = build({"rail_t": 4.5})
        cls.ji = r1s(cls.t45)

    def test_the_largest_of_every_joint(self):
        top = max(self.ji["joints"], key=lambda j: j["sigma_MPa"])
        self.assertEqual((self.ji["joint"], self.ji["cross_member"]), (top["node"], top["cross_member"]))
        out = self.reg.evaluate("frame_static.joint_wall_bending", self.ji["inputs"])["outputs"]
        self.assertAlmostEqual(out["sigma"]["value"], top["sigma_MPa"], places=6)

    def test_rail_t_45_picks_the_rear_wheel_mount(self):
        self.assertEqual((self.ji["joint"], self.ji["cross_member"], self.ji["at_wheel_mount"]), ("LB", "cross_B", True))
        off = r1s(self.t45, wheel_mounts=False)
        self.assertEqual((off["joint"], off["at_wheel_mount"]), ("LE", False))
        self.assertGreater(self.ji["inputs"]["M"]["value"], off["inputs"]["M"]["value"])

    def test_only_the_comparison_changes(self):
        off = r1s(self.t45, wheel_mounts=False)
        self.assertEqual(off["joints"], self.ji["joints"])
        self.assertEqual(off["twisting_moments_Nmm"], self.ji["twisting_moments_Nmm"])


if __name__ == "__main__":
    unittest.main()
