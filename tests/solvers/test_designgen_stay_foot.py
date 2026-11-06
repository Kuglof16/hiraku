"""ladder_4w's rollbar_stay_at: where the roll bar's rear stays land on the rail (1, the default: the rail's end node
E as before; below 1: a rail node of their own between the roll bar's foot station H and E, so that the end joints
LE / RE keep their corner gussets and stay welded L joints in the shell mesh)."""

from __future__ import annotations

import unittest

import numpy as np

from hiraku import designgen
from hiraku.designgen import ladder_4w as GEN
from hiraku.solvers.k1_shell import mesh as shell_mesh

STAYS = {"rollbar_stays": 1, "gusset_ends": 1, "gusset_leg": 120}  # with the corner gussets at E (the lere study's)


def _frame(d):
    return ({m["id"]: (m["node_a"], m["node_b"], m["section"]) for m in d["members"]},
            {n["id"]: tuple(n["xyz_mm"]) for n in d["nodes"]})


class StayFootTest(unittest.TestCase):
    def test_default_at_1_is_todays_design(self):
        self.assertEqual(GEN.VARIABLES["rollbar_stay_at"].default, 1.0)
        today = GEN.build({"rollbar_stays": 1})
        at_1 = GEN.build({"rollbar_stays": 1, "rollbar_stay_at": 1.0})
        self.assertEqual(_frame(at_1), _frame(today))
        self.assertEqual({m["id"]: m["node_b"] for m in today["members"] if m["id"].startswith("rops_stay_")},
                         {"rops_stay_L": "LE", "rops_stay_R": "RE"})
        plain = GEN.build()
        self.assertNotIn("rollbar_stay_at", plain["variables"])  # an option left as it is is not written
        self.assertEqual(_frame(GEN.build({"rollbar_stay_at": 1.0})), _frame(plain))
        with self.assertRaises(designgen.DesignGenError):  # as before: the stays on E make LE / RE tied, no gussets
            GEN.build({**STAYS, "rollbar_stay_at": 1.0})

    def test_foot_inside_the_rail_keeps_le_re_welded(self):
        at = 0.35  # ahead of the rear suspension's upper arms on version 1 (0.5 runs into bracket_uca_RL1)
        d = GEN.build({**STAYS, "rollbar_stay_at": at})
        xyz = {n["id"]: np.asarray(n["xyz_mm"], float) for n in d["nodes"]}
        for side in "LR":
            stay = next(m for m in d["members"] if m["id"] == f"rops_stay_{side}")
            foot = stay["node_b"]
            self.assertEqual(foot, f"{side}HS")
            h, e = xyz[f"{side}H"], xyz[f"{side}E"]
            self.assertAlmostEqual(xyz[foot][0], h[0] + at * (e[0] - h[0]), places=6)
            self.assertAlmostEqual(xyz[foot][1], h[1], places=6)  # on the rail's centre line
            self.assertAlmostEqual(xyz[foot][2], h[2], places=6)
            rails = [m for m in d["members"] if m["id"].startswith(f"rail_{side}_") and foot in (m["node_a"], m["node_b"])]
            self.assertEqual(len(rails), 2)  # the rail split at the foot
            self.assertEqual(len([p for p in d["parts"] if p["kind"] == "gusset" and p["carried_by"] == [f"{side}E"]]), 2)
        kinds = shell_mesh.build_mesh(d).joint_kinds
        self.assertEqual((kinds["LE"], kinds["RE"]), ("L", "L"))

    def test_foot_into_the_rear_arms_is_refused(self):
        with self.assertRaises(designgen.DesignGenError):
            GEN.build({**STAYS, "rollbar_stay_at": 0.5})


if __name__ == "__main__":
    unittest.main()
