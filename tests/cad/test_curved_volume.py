"""Volumes and centres of curved solids written by hand (tests/cad/curved.py) against their formulas."""

import unittest

from hiraku.cad.step_read import read_step
from tests.cad import curved

TOL = 0.005  # 0.5 %


class CurvedVolumeTest(unittest.TestCase):
    def check(self, made, centre_tol=1e-3):
        text, V, C = made
        (s,) = read_step(text)
        err = abs(s.volume_mm3 - V) / V
        print(f"\n[cad] {s.name}: volume {s.volume_mm3:.4f} mm3, formula {V:.4f}, error {err:.2e}", end="")
        self.assertEqual(s.method, "approximate")
        self.assertEqual(s.notes, [])
        self.assertLess(err, TOL)
        for g, w in zip(s.center_mm, C):
            self.assertAlmostEqual(g, w, delta=centre_tol)
        (fine,) = read_step(text, refine=2)
        self.assertLessEqual(abs(fine.volume_mm3 - V), abs(s.volume_mm3 - V) + 1e-9 * V)
        return s

    def test_cylinder_with_a_coaxial_hole(self):
        s = self.check(curved.tube(R=10.0, r=4.0, h=20.0))
        self.assertEqual([round(x, 9) for x in s.size_mm], [20.0, 20.0, 20.0])

    def test_cone_to_its_apex(self):
        s = self.check(curved.cone(r=10.0, semi_angle_deg=30.0))
        self.assertAlmostEqual(s.bbox_max[2], 10.0 / (3 ** -0.5), delta=1e-9)

    def test_sphere_as_two_hemispheres(self):
        s = self.check(curved.sphere_halves(r=10.0))
        self.assertAlmostEqual(s.size_mm[2], 20.0, delta=1e-9)

    def test_sphere_as_one_face_with_a_seam(self):
        self.check(curved.sphere_seam(r=10.0, o=(3.0, -2.0, 5.0)))

    def test_torus_with_two_seams(self):
        s = self.check(curved.torus(R=20.0, r=5.0))
        self.assertAlmostEqual(s.size_mm[0], 50.0, delta=1e-6)
        self.assertAlmostEqual(s.size_mm[2], 10.0, delta=1e-3)

    def test_cylinder_cut_by_a_tilted_plane(self):
        s = self.check(curved.cut_cylinder(r=10.0, h=20.0, slope=0.5))
        self.assertAlmostEqual(s.bbox_max[2], 25.0, delta=1e-9)
        self.assertAlmostEqual(s.bbox_min[2], 0.0, delta=1e-9)


if __name__ == "__main__":
    unittest.main()
