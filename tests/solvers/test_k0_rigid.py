import unittest

import numpy as np

from hiraku.solvers.k0_handcalc import sections
from hiraku.solvers.k0_handcalc.rigid import box_inertia, rpy_matrix


class RigidTest(unittest.TestCase):
    def test_rpy_is_yaw_after_pitch_after_roll(self):
        R = rpy_matrix([30.0, -20.0, 75.0])
        np.testing.assert_allclose(R @ R.T, np.eye(3), atol=1e-12)
        self.assertAlmostEqual(float(np.linalg.det(R)), 1.0, places=12)
        np.testing.assert_allclose(rpy_matrix([0.0, 0.0, 90.0]) @ [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], atol=1e-12)
        np.testing.assert_allclose(rpy_matrix([90.0, 0.0, 90.0]) @ [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], atol=1e-12)

    def test_box_inertia_turns_with_the_box(self):
        plain = box_inertia(12.0, (300.0, 100.0, 50.0))
        self.assertAlmostEqual(plain[0, 0], 12.0 / 12.0 * (100.0**2 + 50.0**2))
        turned = box_inertia(12.0, (300.0, 100.0, 50.0), [0.0, 0.0, 90.0])  # long side along y
        np.testing.assert_allclose(np.diag(turned), [plain[1, 1], plain[0, 0], plain[2, 2]], rtol=1e-12)
        np.testing.assert_array_equal(box_inertia(12.0, (300.0, 100.0, 50.0), [0.0, 0.0, 0.0]), plain)


class AreaTest(unittest.TestCase):
    def test_area_from_dims_or_given(self):
        rect = {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 80.0, "t": 2.3}}
        self.assertEqual(sections.area(rect), sections.properties(rect)["A"])
        self.assertAlmostEqual(sections.area(rect), 40.0 * 80.0 - 35.4 * 75.4)
        self.assertEqual(sections.area({"shape": "angle", "dims_mm": {"b": 40.0}, "A_mm2": 300.0}), 300.0)
        with self.assertRaisesRegex(ValueError, "angle"):
            sections.area({"shape": "angle", "dims_mm": {"b": 40.0}})


if __name__ == "__main__":
    unittest.main()
