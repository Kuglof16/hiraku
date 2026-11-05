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


if __name__ == "__main__":
    unittest.main()
