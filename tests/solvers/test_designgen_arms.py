"""kit/arms.py: the upper arms, dampers and ball joints of ladder_4w as parts, linked to their mounts."""

import unittest

import numpy as np

from hiraku.designgen import ladder_4w
from hiraku.designgen.kit import arms
from hiraku.designgen.kit.links import link_box
from hiraku.solvers.k2_vehicle.model import build_vehicle


def build_without_arms():
    orig = arms.place
    arms.place = lambda *a, **k: {}
    try:
        return ladder_4w.build()
    finally:
        arms.place = orig


class ArmsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design = ladder_4w.build()
        cls.parts = {p["id"]: p for p in cls.design["parts"]}
        cls.bare = build_without_arms()

    def links(self, pid):
        return {(c["to"], c["via"]) for c in self.parts[pid].get("connects_to") or []}

    def test_every_corner_has_its_parts_at_the_hard_points(self):
        for s in self.design["supports"]:
            hp = {k: np.asarray(v, float) for k, v in s["suspension"]["hardpoints_mm"].items()}
            ids = arms.part_ids(s["id"])
            for pid in ids.values():
                self.assertIn(pid, self.parts)
                self.assertEqual(self.parts[pid]["kind"], "suspension")
                self.assertEqual(self.parts[pid]["system"], "suspension")
            for role, a, b, r in (("uca_f", "uca_front", "uca_outer", arms.UPPER_ARM_D_MM / 2.0),
                                  ("uca_r", "uca_rear", "uca_outer", arms.UPPER_ARM_D_MM / 2.0),
                                  ("damper", "damper_lower", "damper_upper", arms.DAMPER_RADIUS_MM)):
                centre, size, _ = link_box(hp[a], hp[b], r)
                box = self.parts[ids[role]]["box"]
                np.testing.assert_allclose(box["center_mm"], centre, atol=1e-9)
                self.assertAlmostEqual(max(box["size_mm"]), float(np.linalg.norm(hp[b] - hp[a])) + 2 * r, places=9)
            np.testing.assert_allclose(self.parts[ids["bj_lower"]]["box"]["center_mm"], hp["lca_outer"], atol=1e-9)
            np.testing.assert_allclose(self.parts[ids["bj_upper"]]["box"]["center_mm"], hp["uca_outer"], atol=1e-9)

    def test_the_load_goes_through_the_arms(self):
        for s in self.design["supports"]:
            sid = s["id"]
            ids = arms.part_ids(sid)
            susp = f"susp_{sid}"
            self.assertEqual(self.links(f"knuckle_{sid}") & {(ids["bj_lower"], "ball_joint"), (ids["bj_upper"], "ball_joint")},
                             {(ids["bj_lower"], "ball_joint"), (ids["bj_upper"], "ball_joint")})
            self.assertNotIn((susp, "ball_joint"), self.links(f"knuckle_{sid}"))
            self.assertIn((susp, "bolt"), self.links(ids["bj_lower"]))
            self.assertEqual({t for t, v in self.links(ids["bj_upper"])}, {ids["uca_f"], ids["uca_r"]})
            self.assertIn((susp, "bushing"), self.links(ids["damper"]))
            # each upper leg and the damper hold on to one mount of the frame; the lower arm no longer does
            for role in ("uca_f", "uca_r", "damper"):
                self.assertEqual(len([t for t, v in self.links(ids[role]) if v == "bushing" and t != susp]), 1, role)
            upper_mounts = {t for role in ("uca_f", "uca_r", "damper") for t, v in self.links(ids[role]) if t != susp}
            self.assertFalse(upper_mounts & {t for t, v in self.links(susp)})

    def test_the_mass_and_the_unsprung_mass_are_kept(self):
        m = lambda d: sum(p["mass_kg"] for p in d["parts"])
        self.assertAlmostEqual(m(self.design), m(self.bare), places=9)
        for s in self.design["supports"]:
            group = [f"susp_{s['id']}", *arms.part_ids(s["id"]).values()]
            self.assertAlmostEqual(sum(self.parts[p]["mass_kg"] for p in group),
                                   next(p for p in self.bare["parts"] if p["id"] == f"susp_{s['id']}")["mass_kg"], places=9)
            self.assertGreater(self.parts[f"susp_{s['id']}"]["mass_kg"], 0.0)
        v0, v1 = build_vehicle(self.bare, {}), build_vehicle(self.design, {})
        self.assertAlmostEqual(v1.m_s, v0.m_s, places=9)
        for a, b in zip(v0.corners, v1.corners):
            self.assertAlmostEqual(a.m_u, b.m_u, places=9)
        # the sprung half of the arms moved up from the lower arm's box: the sprung CG 1.8 mm higher (v1)
        dz = (float(v1.cg_s[2]) - float(v0.cg_s[2])) * 1000.0
        self.assertAlmostEqual(dz, 1.77, delta=0.05)

    def test_a_suspension_part_too_light_is_an_error(self):
        from hiraku.designgen.ladder_4w import DesignGenError

        orig = arms.DAMPER_KG
        arms.DAMPER_KG = 50.0
        try:
            with self.assertRaisesRegex(DesignGenError, "lighter than the arms"):
                ladder_4w.build()
        finally:
            arms.DAMPER_KG = orig

    def test_tube_mass(self):
        # a 1 m tube 27.2 x 2.3 of steel 7850 kg/m^3: pi (d - t) t L rho
        self.assertAlmostEqual(arms.tube_mass(1000.0, 27.2, 2.3, 7850.0), np.pi * 24.9 * 2.3 * 7850e-6, places=12)


if __name__ == "__main__":
    unittest.main()
