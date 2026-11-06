import math
import unittest

from hiraku.cad import CadError
from hiraku.cad.step_read import read_step
from tests.cad import handmade as hm


class ReadStepTest(unittest.TestCase):
    def assertVec(self, got, want, tol=1e-6):
        for g, w in zip(got, want):
            self.assertAlmostEqual(g, w, delta=tol)

    def test_cube_mm(self):
        (s,) = read_step(hm.cube())
        self.assertEqual(s.name, "cube")
        self.assertAlmostEqual(s.volume_mm3, 1000.0, places=6)
        self.assertEqual(s.method, "exact")
        self.assertVec(s.center_mm, [5, 5, 5])
        self.assertVec(s.bbox_min, [0, 0, 0])
        self.assertVec(s.bbox_max, [10, 10, 10])

    def test_cube_in_inch_and_metre(self):
        (s,) = read_step(hm.cube(hm.INCH_CONTEXT, 1.0))
        self.assertAlmostEqual(s.volume_mm3, 25.4 ** 3, delta=1e-6)
        self.assertVec(s.bbox_max, [25.4] * 3)
        (s,) = read_step(hm.cube(hm.METRE_CONTEXT, 0.01))
        self.assertAlmostEqual(s.volume_mm3, 1000.0, delta=1e-6)
        self.assertVec(s.center_mm, [5, 5, 5])

    def test_cylinder_is_approximate_within_a_tenth_percent(self):
        (s,) = read_step(hm.CYLINDER)
        want = math.pi * 10 ** 2 * 20
        self.assertEqual(s.method, "approximate")
        self.assertLess(abs(s.volume_mm3 - want) / want, 1e-3)
        self.assertVec(s.center_mm, [0, 0, 10], tol=1e-3)
        self.assertVec(s.bbox_min, [-10, -10, 0], tol=1e-3)
        self.assertVec(s.bbox_max, [10, 10, 20], tol=1e-3)

    def test_assembly_moved_and_turned(self):
        solids = read_step(hm.assembly())
        self.assertEqual(len(solids), 2)
        self.assertEqual({s.name for s in solids}, {"block"})
        moved = min(solids, key=lambda s: -s.bbox_min[0])
        turned = min(solids, key=lambda s: s.bbox_min[0])
        for s in solids:
            self.assertAlmostEqual(s.volume_mm3, 6000.0, places=6)
        self.assertVec(moved.bbox_min, [100, 0, 0])
        self.assertVec(moved.bbox_max, [110, 20, 30])
        self.assertVec(moved.center_mm, [105, 10, 15])
        # (x, y, z) -> (-y, x, z) + (0, 50, 0)
        self.assertVec(turned.bbox_min, [-20, 50, 0])
        self.assertVec(turned.bbox_max, [0, 60, 30])
        self.assertVec(turned.center_mm, [-10, 55, 15])

    def test_errors_name_the_entity(self):
        with self.assertRaises(CadError) as c:
            read_step("hello")
        self.assertIn("not a STEP file", str(c.exception))
        bad = hm.cube().replace("#5=CARTESIAN_POINT('',(0.0,0.0,10.0));", "#5=CARTESIAN_POINT('',(0.0,0.0,10.0);")
        with self.assertRaises(CadError) as c:
            read_step(bad)
        self.assertIn("#5", str(c.exception))
        missing = hm.cube().replace("#5=CARTESIAN_POINT('',(0.0,0.0,10.0));", "")
        with self.assertRaises(CadError) as c:
            read_step(missing)
        self.assertIn("#5", str(c.exception))


if __name__ == "__main__":
    unittest.main()
