"""kit/arms.py: ladder_4w's arms, dampers and ball joints as parts in place of the suspension boxes,
each joined to what it touches (brackets at the frame, ball joints at the knuckle)."""

import unittest

import numpy as np

from hiraku.designgen import ladder_4w
from hiraku.designgen.kit import arms
from hiraku.designgen.kit.links import link_box
from hiraku.solvers.k2_vehicle.model import build_vehicle
from hiraku.solvers.k4_geometry import interferences
from hiraku.solvers.k4_geometry.model import bodies, shape_overlap

JOINT_GAP_MM = 2.0  # a link's two parts touch within this (H1-joints)


class ArmsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design = ladder_4w.build()
        cls.parts = {p["id"]: p for p in cls.design["parts"]}
        cls.bodies = bodies(cls.design)

    def links(self, pid):
        return {(c["to"], c["via"]) for c in self.parts[pid].get("connects_to") or []}

    def test_the_box_is_gone_and_the_parts_sit_on_the_hard_points(self):
        for s in self.design["supports"]:
            sid = s["id"]
            self.assertNotIn(f"susp_{sid}", self.parts)
            hp = {k: np.asarray(v, float) for k, v in s["suspension"]["hardpoints_mm"].items()}
            ids = arms.part_ids(sid)
            for role, pid in ids.items():
                self.assertEqual(self.parts[pid]["module"], "suspension", pid)
                self.assertEqual(self.parts[pid]["system"], "suspension", pid)
            r = arms.ARM_D_MM / 2.0
            for role, a, b, rad in (("lca_f", "lca_front", "lca_outer", r), ("lca_r", "lca_rear", "lca_outer", r),
                                    ("uca_f", "uca_front", "uca_outer", r), ("uca_r", "uca_rear", "uca_outer", r),
                                    ("damper", "damper_lower", "damper_upper", arms.DAMPER_RADIUS_MM)):
                centre, size, _ = link_box(hp[a], hp[b], rad)
                box = self.parts[ids[role]]["box"]
                np.testing.assert_allclose(box["center_mm"], centre, atol=1e-9)
                self.assertAlmostEqual(max(box["size_mm"]), float(np.linalg.norm(hp[b] - hp[a])) + 2 * rad, places=9)
            np.testing.assert_allclose(self.parts[ids["bj_lower"]]["box"]["center_mm"], hp["lca_outer"], atol=1e-9)
            np.testing.assert_allclose(self.parts[ids["bj_upper"]]["box"]["center_mm"], hp["uca_outer"], atol=1e-9)

    def test_the_joints_at_each_end(self):
        for s in self.design["supports"]:
            sid = s["id"]
            ids = arms.part_ids(sid)
            self.assertEqual({c for c in self.links(f"knuckle_{sid}") if c[1] == "ball_joint"},
                             {(ids["bj_lower"], "ball_joint"), (ids["bj_upper"], "ball_joint")})
            self.assertEqual(self.links(ids["bj_lower"]), {(ids["lca_f"], "weld"), (ids["lca_r"], "weld")})
            self.assertEqual(self.links(ids["bj_upper"]), {(ids["uca_f"], "weld"), (ids["uca_r"], "weld")})
            self.assertIn((ids["lca_x"], "bushing"), self.links(ids["damper"]))
            # each pivot and the damper's top: a bushing in a bracket (a clevis welded to the frame)
            for role in ("lca_f", "lca_r", "uca_f", "uca_r", "damper"):
                held = [t for t, v in self.links(ids[role]) if v == "bushing" and t in self.parts
                        and self.parts[t]["kind"] == "bracket"]
                self.assertEqual(len(held), 1, f"{sid} {role}: {self.links(ids[role])}")

    def test_the_front_lower_pivots_and_damper_tops_have_clevis_brackets(self):
        for s in self.design["supports"]:
            for pid in (f"bracket_lca_{s['id']}1", f"bracket_lca_{s['id']}2", f"bracket_damper_{s['id']}"):
                if pid.startswith("bracket_damper") or s["position_mm"][0] >= 0:
                    p = self.parts[pid]
                    self.assertEqual(p["kind"], "bracket")
                    self.assertEqual(p["module"], "frame")
                    self.assertTrue(p["carried_by"])

    def test_every_suspension_link_touches(self):
        far = []
        for p in self.design["parts"]:
            if p["kind"] not in arms.KINDS + ("knuckle",):
                continue
            for c in p.get("connects_to") or []:
                other = self.bodies.get(f"part:{c['to']}")
                self.assertIsNotNone(other, f"{p['id']} -> {c['to']}")
                gap = -shape_overlap(self.bodies[f"part:{p['id']}"], other)
                if gap > JOINT_GAP_MM:
                    far.append((p["id"], c["to"], round(gap, 1)))
        self.assertEqual(far, [])

    def test_no_interference(self):
        self.assertEqual(interferences(self.design), [])

    def test_the_mass_and_the_unsprung_mass_are_kept(self):
        # the box's mass went to the arms, the ball joints and the damper (the rest)
        for s in self.design["supports"]:
            ids = arms.part_ids(s["id"])
            self.assertGreater(self.parts[ids["damper"]]["mass_kg"], 0.0)
            for role in ("lca_f", "lca_r", "uca_f", "uca_r"):
                self.assertGreater(self.parts[ids[role]]["mass_kg"], 0.0)
        v = build_vehicle(self.design, {})
        # v1's unsprung masses as before the arms (the knuckle, wheel, caliper and half the suspension)
        self.assertEqual([round(c.m_u, 4) for c in v.corners], [18.5, 18.5, 18.0537, 18.0537])

    def test_a_box_too_light_is_an_error(self):
        from hiraku.designgen.ladder_4w import DesignGenError

        orig = arms.BALL_JOINT_KG
        arms.BALL_JOINT_KG = 50.0
        try:
            with self.assertRaisesRegex(DesignGenError, "lighter than the arms"):
                ladder_4w.build()
        finally:
            arms.BALL_JOINT_KG = orig

    def test_the_damper_bracket_misses_the_tower_post_on_a_wide_track(self):
        # track 1200 mm with 140 mm wheels: the tower arm to the damper top is short and the bracket's full
        # inboard reach went 3.55 mm into the tower's post; it is cut back until K4 allows it
        d = ladder_4w.build({"track": {"value": 1200.0, "unit": "mm"}, "wheel_width": {"value": 140.0, "unit": "mm"}})
        hits = [h for h in interferences(d) if "bracket_damper" in h[0] + h[1]]
        self.assertEqual(hits, [])
        for sid in ("RL", "RR"):
            b = next(p for p in d["parts"] if p["id"] == f"bracket_damper_{sid}")
            self.assertGreater(b["box"]["size_mm"][1], arms.NODE_TOL_MM + 20.0)  # still a clevis round the eye

    def test_tube_mass(self):
        # a 1 m tube 25.4 x 2.0 of steel 7850 kg/m^3: pi (d - t) t L rho
        self.assertAlmostEqual(arms.tube_mass(1000.0, 25.4, 2.0, 7850.0), np.pi * 23.4 * 2.0 * 7850e-6, places=12)


if __name__ == "__main__":
    unittest.main()
