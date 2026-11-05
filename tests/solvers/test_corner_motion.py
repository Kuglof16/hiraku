"""H1's sweep with a double-wishbone corner moved as its suspension moves (k4_geometry/corner_motion.py)."""

import json
import math
import unittest
from pathlib import Path

import numpy as np

from hiraku.solvers.k4_geometry import bodies
from hiraku.solvers.k4_geometry.corner_motion import FLIP, moved, role, wheel_overlap, wheel_slabs
from hiraku.solvers.k4_geometry.model import shape_overlap
from hiraku.solvers.k4_geometry.motion import _rest_own, corner_kinematics

ROOT = Path(__file__).resolve().parents[2]
DESIGN = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))


class CornerMotionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bs = bodies(DESIGN)
        cls.kin = {s: corner_kinematics(DESIGN, s) for s in ("FL", "FR")}

    def group(self, sid):
        return {k: b for k, b in self.bs.items() if b.support == sid and b.kind != "suspension"}

    def test_the_design_pose_leaves_every_part_in_place(self):
        m = self.kin["FL"].maps(0.0, 0.0)
        for k, b in self.group("FL").items():
            o = moved(b, "FL", m)
            np.testing.assert_allclose(o.obb.center, b.obb.center, atol=1e-6, err_msg=k)
            if b.capsule is not None:
                np.testing.assert_allclose(sorted(map(tuple, (o.capsule.a, o.capsule.b))),
                                           sorted(map(tuple, (b.capsule.a, b.capsule.b))), atol=1e-6, err_msg=k)

    def test_the_roles(self):
        roles = {k.split(":", 1)[1]: role(b, "FL") for k, b in self.group("FL").items()}
        self.assertEqual(roles["lca_FL_f"], "lower")
        self.assertEqual(roles["uca_FL_r"], "upper")
        self.assertEqual(roles["bj_lower_FL"], "lbj")
        self.assertEqual(roles["damper_FL"], "damper")
        self.assertEqual(roles["tie_rod_FL"], "tie_rod")
        self.assertEqual(roles["wheel_FL"], "knuckle")

    def test_links_keep_their_length_and_the_damper_stretches(self):
        g = self.group("FL")
        tie, damper = g["part:tie_rod_FL"], g["part:damper_FL"]
        length = lambda c: float(np.linalg.norm(c.b - c.a))
        for dz, st in ((60.0, 0.0), (-60.0, 20.0), (30.0, -25.0)):
            m = self.kin["FL"].maps(dz, st)
            self.assertAlmostEqual(length(moved(tie, "FL", m).capsule), length(tie.capsule), places=6)
            d2 = length(moved(damper, "FL", m).capsule)
            if dz > 0:
                self.assertLess(d2, length(damper.capsule))  # bump: the damper is shorter
            else:
                self.assertGreater(d2, length(damper.capsule))
        # the wheel rises with the travel; steering turns its heading by about the steer angle
        w = g["part:wheel_FL"]
        up = moved(w, "FL", self.kin["FL"].maps(40.0, 0.0))
        self.assertAlmostEqual(up.obb.center[2] - w.obb.center[2], 40.0, delta=2.0)
        turned = moved(w, "FL", self.kin["FL"].maps(0.0, 20.0))
        fwd = turned.obb.axes[int(np.argmax(np.abs(turned.obb.axes[:, 0])))]
        heading = math.degrees(math.atan2(abs(fwd[1]), abs(fwd[0])))
        self.assertAlmostEqual(heading, 20.0, delta=2.0)  # about the kingpin axis, so a little off the vertical's

    def test_left_and_right_are_mirrors(self):
        for dz, st in ((50.0, 15.0), (-40.0, -10.0)):
            ml, mr = self.kin["FL"].maps(dz, st), self.kin["FR"].maps(dz, -st)
            for name in ("wheel", "knuckle", "tie_rod", "damper"):
                a = moved(self.bs[f"part:{name}_FL"], "FL", ml)
                b = moved(self.bs[f"part:{name}_FR"], "FR", mr)
                np.testing.assert_allclose(a.obb.center * FLIP, b.obb.center, atol=1e-6, err_msg=name)

    def test_the_tyre_slabs_cover_the_disc(self):
        w = self.bs["part:wheel_FL"]
        slabs = wheel_slabs(w)
        r = float(sorted(w.obb.half)[1])
        for a in np.linspace(0.0, 2.0 * math.pi, 73):
            p = w.obb.center + 0.999 * r * (math.cos(a) * np.array([1.0, 0.0, 0.0]) + math.sin(a) * np.array([0.0, 0.0, 1.0]))
            inside = any(np.all(np.abs((p - s.center) @ s.axes.T) <= s.half + 1e-9) for s in slabs)
            self.assertTrue(inside, a)
        # every slab reaches at most 2 % past the tyre's radius
        for s in slabs:
            self.assertLessEqual(float(np.linalg.norm(s.half[1:])), 1.02 * r)
        # a body clear of the box is clear of the tyre with the same gap
        far = self.bs[next(k for k, b in self.bs.items() if b.kind == "member")]
        box = shape_overlap(w, far)
        if box <= 0:
            self.assertEqual(wheel_overlap(w, far), box)

    def test_own_parts_at_rest_are_the_baseline(self):
        own = _rest_own(DESIGN, self.bs, "FL")
        self.assertTrue(own)
        self.assertNotIn(("part:knuckle_FL", "part:wheel_FL"), own)  # mounted to each other


if __name__ == "__main__":
    unittest.main()
