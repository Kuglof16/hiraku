"""Structured hexahedral meshes: design boxes (shared nodes where boxes touch), O-grid cylinders, rings and a
plate with a hole (nodes on the true circle)."""

import json
import unittest
from pathlib import Path

import numpy as np

from hiraku.solvers.fe import FEModel, mesh as fm, quality
from hiraku.solvers.fe.solid import Hex20, Hex8

ROOT = Path(__file__).resolve().parents[2]


def design():
    return json.loads((ROOT / "data/designs/offroad-4w-v1.json").read_text(encoding="utf-8"))


def volume(m):
    cls = Hex20 if m.order == 2 else Hex8
    return float(cls(m.conn, 1.0, 0.3).volumes(m.nodes).sum())


class BoxMeshTest(unittest.TestCase):
    def test_touching_parts_share_nodes(self):
        d = design()
        ids = ["floor_front", "floor"]  # touch at x = -450 (from their boxes)
        m = fm.parts_mesh(d, 50.0, ids)
        boxes = {p["id"]: p["box"] for p in d["parts"] if p["id"] in ids}
        vol = sum(np.prod(b["size_mm"]) for b in boxes.values())
        self.assertAlmostEqual(volume(m), vol, delta=1e-9 * vol)
        q = quality.check_mesh(m.nodes, solids=m.conn)
        self.assertEqual(q["quality"]["topology"]["unconnected_groups"], 0)
        self.assertEqual(q["quality"]["topology"]["coincident_nodes"], 0)
        self.assertEqual(q["quality"]["verdict"], "ok")
        self.assertEqual(set(m.part), set(ids))
        # every node on the common face belongs to elements of both parts
        lo = [np.asarray(boxes[i]["center_mm"]) - np.asarray(boxes[i]["size_mm"]) / 2 for i in ids]
        x_face = lo[0][0]
        face = m.nodes_where(lambda x, y, z: np.abs(x - x_face) < 1e-9)
        part = np.array(m.part)
        on = {p: set(m.conn[part == p].ravel().tolist()) for p in ids}
        shared = [n for n in face if n in on["floor_front"] and n in on["floor"]]
        self.assertGreater(len(shared), 0)
        ff = boxes["floor_front"]
        y_half = ff["size_mm"][1] / 2
        inside = [n for n in face if abs(m.nodes[n, 1]) <= y_half + 1e-9]
        self.assertEqual(sorted(shared), sorted(inside))

    def test_overlapping_and_stacked_parts(self):
        d = design()
        m = fm.parts_mesh(d, 60.0, ["seat_L", "occupant_L"], order=1)  # stacked: the occupant sits on the seat
        q = quality.check_mesh(m.nodes, solids=m.conn)
        self.assertEqual(q["quality"]["topology"]["unconnected_groups"], 0)
        self.assertEqual(q["quality"]["topology"]["coincident_nodes"], 0)

    def test_needs_boxes(self):
        with self.assertRaises(ValueError):
            fm.parts_mesh(design(), 50.0, ["no_such_part"])


class CurvedMeshTest(unittest.TestCase):
    def test_cylinder_nodes_on_the_circle(self):
        R, L = 25.0, 60.0
        m = fm.cylinder_mesh(R, L, 4, 3, 4, centre=(10.0, -5.0, 2.0), axis=(1.0, 1.0, 0.0))
        a = np.array([1.0, 1.0, 0.0]) / np.sqrt(2)
        rel = m.nodes - np.array([10.0, -5.0, 2.0])
        r = np.linalg.norm(rel - np.outer(rel @ a, a), axis=1)
        outer = r > R * (1 - 1e-9)
        self.assertGreater(outer.sum(), 0)
        self.assertLess(np.abs(r[outer] - R).max(), 1e-9)
        self.assertLessEqual(r.max(), R * (1 + 1e-12))
        # mid-edge nodes on the circle too: volume close to pi R^2 L (quadratic edges)
        self.assertAlmostEqual(volume(m), np.pi * R**2 * L, delta=2e-4 * np.pi * R**2 * L)
        q = quality.check_mesh(m.nodes, solids=m.conn)
        self.assertNotEqual(q["quality"]["verdict"], "fail")
        self.assertEqual(q["quality"]["topology"]["unconnected_groups"], 0)
        self.assertEqual(q["quality"]["topology"]["coincident_nodes"], 0)

    def test_cylinder_in_torsion(self):
        """Twist of a round bar under torque T: T L / (G J), J = pi R^4 / 2 (circular shaft, no warping); torque
        as the shear traction tau = T r / J on the end face, base face clamped."""
        from hiraku.solvers.fe import StaticSolver
        from hiraku.solvers.fe.solid import surface_load
        R, L, E, nu, T = 20.0, 100.0, 205000.0, 0.3, 1.0e6
        m = fm.cylinder_mesh(R, L, 4, 3, 6)
        model = FEModel(m.nodes)
        model.add(Hex20(m.conn, E, nu))
        model.fix(m.nodes_where(lambda x, y, z: np.abs(z) < 1e-9), "xyz")
        J = np.pi * R**4 / 2

        def tau(x, n):
            return T / J * np.c_[-x[:, 1], x[:, 0], np.zeros(len(x))]

        F = surface_load(m.nodes, m.faces_where(lambda x, y, z: np.abs(z - L) < 1e-9), tau)
        sol = StaticSolver(model).solve(F)
        top = m.nodes_where(lambda x, y, z: (np.abs(z - L) < 1e-9) & (np.hypot(x, y) > R - 1e-6))
        x, y = m.nodes[top, 0], m.nodes[top, 1]
        u = sol.displacements[top]
        theta = np.mean((x * u[:, 1] - y * u[:, 0]) / (x * x + y * y))
        G = E / (2 * (1 + nu))
        expect = T * L / (G * J)
        self.assertAlmostEqual(theta, expect, delta=0.01 * expect)

    def test_ring_and_holed_plate(self):
        m = fm.ring_mesh(50.0, 100.0, 10.0, 4, 6, 1)
        self.assertAlmostEqual(volume(m), np.pi / 4 * (100.0**2 - 50.0**2) * 10.0, delta=1e-4 * volume(m))
        W, Lp, T, d = 100.0, 200.0, 5.0, 20.0
        m = fm.holed_plate_mesh(W, Lp, T, d, 6, 6, 2, 1.2)
        hole = m.nodes_where(lambda x, y, z: np.hypot(x, y) < d / 2 + 1e-6)
        self.assertGreater(len(hole), 0)
        self.assertLess(np.abs(np.hypot(m.nodes[hole, 0], m.nodes[hole, 1]) - d / 2).max(), 1e-9)
        exact = (W / 2 * Lp / 2 - np.pi * (d / 2) ** 2 / 4) * T
        self.assertAlmostEqual(volume(m), exact, delta=1e-4 * exact)
        q = quality.check_mesh(m.nodes, solids=m.conn)
        self.assertNotEqual(q["quality"]["verdict"], "fail")
        self.assertEqual(q["quality"]["topology"]["unconnected_groups"], 0)


if __name__ == "__main__":
    unittest.main()
