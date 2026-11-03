"""qa.mesh_quality on single elements whose measures follow from their shape, on bad ones, and on
small meshes whose topology can be counted by hand."""

import math
import unittest

import numpy as np
from scipy.spatial import ConvexHull

from qa import known_answers as ka
from qa import mesh_quality as mq


def one(points):
    m = mq.measure(points, [list(range(len(points)))])
    return {k: (v[0] if isinstance(v, np.ndarray) else v) for k, v in m.items()}


class Elements(unittest.TestCase):
    def test_rectangle(self):
        m = one(ka.rectangle_quad(4.0, 1.0))
        self.assertAlmostEqual(m["aspect_ratio"], 4.0, places=12)
        self.assertAlmostEqual(m["min_angle_deg"], 90.0, places=9)
        self.assertAlmostEqual(m["max_angle_deg"], 90.0, places=9)
        self.assertAlmostEqual(m["jacobian_ratio"], 1.0, places=12)
        self.assertAlmostEqual(m["warp_deg"], 0.0, places=9)
        self.assertAlmostEqual(m["area"], 4.0, places=12)

    def test_rhombus(self):
        for alpha in (30.0, 60.0, 89.0):
            with self.subTest(alpha=alpha):
                m = one(ka.rhombus_quad(2.0, alpha))
                self.assertAlmostEqual(m["min_angle_deg"], alpha, places=9)
                self.assertAlmostEqual(m["max_angle_deg"], 180.0 - alpha, places=9)
                self.assertAlmostEqual(m["aspect_ratio"], 1.0, places=12)
                self.assertAlmostEqual(m["jacobian_ratio"], 1.0, places=12)  # a parallelogram
                self.assertAlmostEqual(m["area"], 4.0 * math.sin(math.radians(alpha)), places=12)

    def test_warp_of_a_folded_square(self):
        self.assertAlmostEqual(one(ka.folded_quad(0.0))["warp_deg"], 0.0, places=9)
        for theta in (5.0, 30.0):
            with self.subTest(theta=theta):
                self.assertGreaterEqual(one(ka.folded_quad(theta))["warp_deg"], theta - 1e-9)
        # the fold itself: the triangles of diagonal 0-2 meet at theta
        p = np.array(ka.folded_quad(30.0))
        n1, n2 = np.cross(p[1] - p[0], p[2] - p[0]), np.cross(p[2] - p[0], p[3] - p[0])
        self.assertAlmostEqual(math.degrees(math.acos(abs(n1 @ n2) / np.linalg.norm(n1) / np.linalg.norm(n2))), 30.0, places=9)

    def test_bad_elements_are_caught(self):
        for name, points in ka.BAD_ELEMENTS.items():
            with self.subTest(element=name):
                m = one(points)
                self.assertTrue(m["degenerate"] or m["jacobian_ratio"] <= 0, m)
        self.assertTrue(one(ka.BAD_ELEMENTS["collapsed_quad"])["degenerate"])
        reentrant = one(ka.BAD_ELEMENTS["reentrant_quad"])
        self.assertFalse(reentrant["degenerate"])
        self.assertLess(reentrant["jacobian_ratio"], 0)

    def test_triangle(self):
        m = one([[0, 0, 0], [1, 0, 0], [0, 1, 0]])
        self.assertAlmostEqual(m["min_angle_deg"], 45.0, places=9)
        self.assertAlmostEqual(m["max_angle_deg"], 90.0, places=9)
        self.assertAlmostEqual(m["aspect_ratio"], math.sqrt(2), places=12)
        self.assertAlmostEqual(m["jacobian_ratio"], 1.0, places=12)


class Hexahedra(unittest.TestCase):
    def test_cube_and_its_inside_out(self):
        for size in (1.0, 2.0):
            cube = ka.unit_hex(size)
            np.testing.assert_allclose(mq.hex_corner_jacobians(cube), size**3)
            self.assertAlmostEqual(mq.hex_volume(cube), size**3, places=12)
            self.assertAlmostEqual(ConvexHull(cube).volume, size**3, places=9)  # a second route
            np.testing.assert_allclose(mq.hex_corner_jacobians(ka.inverted_hex(size)), -size**3)
            self.assertAlmostEqual(mq.hex_volume(ka.inverted_hex(size)), -size**3, places=12)

    def test_parallelepiped_volume_is_the_determinant(self):
        a, b, c = np.array([2.0, 0, 0]), np.array([0.5, 1.5, 0]), np.array([0.3, 0.2, 1.1])
        o = np.zeros(3)
        pts = [o, a, a + b, b, c, a + c, a + b + c, b + c]
        det = float(np.linalg.det(np.stack([a, b, c])))
        self.assertAlmostEqual(mq.hex_volume(pts), det, places=12)
        np.testing.assert_allclose(mq.hex_corner_jacobians(pts), det)


class Topology(unittest.TestCase):
    # two unit squares side by side: nodes 0-5, the shared edge 1-4
    NODES = [[0, 0, 0], [1, 0, 0], [2, 0, 0], [0, 1, 0], [1, 1, 0], [2, 1, 0]]
    ELEMENTS = [[0, 1, 4, 3], [1, 2, 5, 4]]

    def topo(self, nodes=None, elements=None):
        return mq.measure(nodes or self.NODES, elements or self.ELEMENTS)["topology"]

    def test_two_squares(self):
        t = self.topo()
        self.assertEqual((t["free_edges"], t["coincident_nodes"], t["unconnected_groups"],
                          t["orientation_conflicts"], t["degenerate"], t["inverted"]), (6, 0, 1, 0, 0, 0))

    def test_one_square_facing_the_other_way(self):
        self.assertEqual(self.topo(elements=[[0, 1, 4, 3], [1, 4, 5, 2]])["orientation_conflicts"], 1)

    def test_a_crack(self):
        # the second square on its own copies of nodes 1 and 4: two groups, two pairs of coincident nodes
        nodes = self.NODES + [[1, 0, 0], [1, 1, 0]]
        t = self.topo(nodes, [[0, 1, 4, 3], [6, 2, 5, 7]])
        self.assertEqual((t["unconnected_groups"], t["coincident_nodes"], t["free_edges"]), (2, 4, 8))


if __name__ == "__main__":
    unittest.main()
