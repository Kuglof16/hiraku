"""Pre-processing checks: mesh quality (11) and the checks around the solve (12)."""

import contextlib
import json
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from hiraku import contract
from hiraku.solvers.fe import FEModel, ModelError, StaticSolver, analysis, checks, mesh as fm, model as fe_model, quality
from hiraku.solvers.fe.solid import Hex8, Hex20, surface_load

ROOT = Path(__file__).resolve().parents[2]
HAS_QUALITY = "quality" in contract.schema()["$defs"]["mesh"]["properties"]


def square(n=3, size=10.0):
    xs = np.linspace(0, size * n, n + 1)
    X, Y = np.meshgrid(xs, xs, indexing="ij")
    nodes = np.c_[X.ravel(), Y.ravel(), np.zeros(X.size)]
    idx = np.arange(X.size).reshape(n + 1, n + 1)
    q = np.stack([idx[:-1, :-1], idx[1:, :-1], idx[1:, 1:], idx[:-1, 1:]], axis=-1).reshape(-1, 4)
    return nodes, q


def tube(n_around=8, n_len=6, r=20.0, L=120.0):
    """A closed-section tube (polygon) with open ends: quads counter-clockwise seen from outside."""
    t = np.linspace(0, 2 * np.pi, n_around, endpoint=False)
    x = np.linspace(0, L, n_len + 1)
    nodes = np.array([[xx, r * np.cos(a), r * np.sin(a)] for xx in x for a in t])
    q = []
    for i in range(n_len):
        for j in range(n_around):
            a, b = i * n_around + j, i * n_around + (j + 1) % n_around
            q.append([a, a + n_around, b + n_around, b])
    return nodes, np.array(q)


@contextlib.contextmanager
def not_factorised():
    """Fail the test if the model is factorised (by the eliminate path's superlu or the others' splu)."""
    no = AssertionError("factorised")
    with mock.patch.object(fe_model, "superlu", side_effect=no), mock.patch.object(fe_model.spla, "splu", side_effect=no):
        yield


class GoodMeshTest(unittest.TestCase):
    def test_regular_shell_mesh_is_ok(self):
        nodes, q = square()
        r = quality.check_mesh(nodes, q)
        self.assertEqual(r["quality"]["verdict"], "ok")
        m = r["quality"]["metrics"]
        self.assertAlmostEqual(m["aspect_ratio"]["worst"], 1.0)
        self.assertAlmostEqual(m["jacobian_ratio"]["worst"], 1.0)
        self.assertAlmostEqual(m["skew_deg"]["worst"], 0.0)

    def test_tube_free_edges_and_normals(self):
        nodes, q = tube()
        r = quality.check_mesh(nodes, q, expected_free_loops=2)
        self.assertEqual(r["quality"]["topology"]["free_edges"], 16)
        self.assertEqual(r["free_edge_loops"], 2)
        self.assertEqual(r["quality"]["topology"]["flipped_normals"], 0)
        self.assertEqual(r["quality"]["verdict"], "ok")

    def test_hex20_box_is_ok(self):
        m = fm.box_mesh([((0, 0, 0), (40, 20, 20))], 10.0, 2)
        r = quality.check_mesh(m.nodes, solids=m.conn)
        self.assertEqual(r["quality"]["verdict"], "ok")
        self.assertEqual(r["quality"]["topology"]["negative_volume"], 0)

    @unittest.skipUnless(HAS_QUALITY, "the contract's mesh has no quality yet")
    def test_record_fits_the_contract(self):
        from hiraku.solvers import frame_mesh
        design = json.loads((ROOT / "data/designs/offroad-4w-v1.json").read_text(encoding="utf-8"))
        rec = frame_mesh(design)
        rec["mesh_id"] = "mesh-0001"
        rec["quality"] = quality.record_quality(rec)
        self.assertEqual(contract.errors("mesh", rec), [])


class BadMeshTest(unittest.TestCase):
    """Each bad element or topology gives the verdict it should."""

    def test_squashed_quad_fails(self):
        nodes, q = square()
        c = q[4]  # the middle element: move its 3rd corner past the diagonal (arrowhead, re-entrant corner)
        nodes[c[2]] = nodes[c[0]] + 0.3 * (nodes[c[2]] - nodes[c[0]])
        r = quality.check_mesh(nodes, q)
        self.assertEqual(r["quality"]["verdict"], "fail")
        self.assertLessEqual(r["quality"]["metrics"]["jacobian_ratio"]["worst"], 0.0)
        self.assertIn(r["quality"]["metrics"]["jacobian_ratio"]["worst_element"], (0, 1, 3, 4, 5, 7, 8))

    def test_collapsed_quad_fails(self):
        nodes, q = square()
        nodes[q[4][2]] = nodes[q[4][1]].copy()  # two corners of one quad on top of each other
        r = quality.check_mesh(nodes, q)
        self.assertEqual(r["quality"]["verdict"], "fail")

    def test_inverted_hex_fails(self):
        m = fm.box_mesh([((0, 0, 0), (20, 10, 10))], 10.0, 2)
        conn = m.conn.copy()
        conn[1] = conn[1][[4, 5, 6, 7, 0, 1, 2, 3, 12, 13, 14, 15, 8, 9, 10, 11, 16, 17, 18, 19]]  # top <-> bottom
        r = quality.check_mesh(m.nodes, solids=conn)
        self.assertEqual(r["quality"]["verdict"], "fail")
        self.assertEqual(r["quality"]["topology"]["negative_volume"], 1)
        self.assertEqual(r["quality"]["metrics"]["jacobian_ratio"]["worst_element"], 1)

    def test_coincident_nodes_warn(self):
        nodes, q = square(2)
        nodes = np.vstack([nodes, nodes[4]])  # a copy of the centre node, used by one element: a crack
        q = q.copy()
        q[0][q[0] == 4] = len(nodes) - 1
        r = quality.check_mesh(nodes, q)
        self.assertEqual(r["quality"]["topology"]["coincident_nodes"], 1)
        self.assertEqual(r["quality"]["verdict"], "warn")

    def test_skewed_and_warped_elements_warn(self):
        nodes, q = square(1)
        nodes[[1, 3], 0] += 20.0  # parallelogram: the top edge (y = 10) slides 20 along x
        r = quality.check_mesh(nodes, q)
        self.assertGreater(r["quality"]["metrics"]["skew_deg"]["worst"], 45.0)
        self.assertEqual(r["quality"]["verdict"], "warn")
        nodes, q = square(1)
        nodes[3, 2] = 5.0  # one corner lifted out of the plane
        r = quality.check_mesh(nodes, q)
        self.assertGreater(r["quality"]["metrics"]["warp_deg"]["worst"], 10.0)
        self.assertEqual(r["quality"]["verdict"], "warn")

    def test_hole_in_tube_wall_warns(self):
        nodes, q = tube()
        q = np.delete(q, 20, axis=0)
        r = quality.check_mesh(nodes, q, expected_free_loops=2)
        self.assertEqual(r["free_edge_loops"], 3)
        self.assertEqual(r["quality"]["topology"]["free_edges"], 20)
        self.assertEqual(r["quality"]["verdict"], "warn")

    def test_flipped_shell_warns(self):
        nodes, q = square()
        q = q.copy()
        q[4] = q[4][::-1]
        r = quality.check_mesh(nodes, q)
        self.assertEqual(r["quality"]["topology"]["flipped_normals"], 1)
        self.assertEqual(r["quality"]["verdict"], "warn")

    def test_lone_group_without_load_fails(self):
        nodes, q = square(2)
        nodes2, q2 = square(1)
        nodes2[:, 0] += 100.0
        allnodes = np.vstack([nodes, nodes2])
        allq = np.vstack([q, q2 + len(nodes)])
        r = quality.check_mesh(allnodes, allq)
        self.assertEqual(r["quality"]["topology"]["unconnected_groups"], 1)
        self.assertEqual(r["quality"]["verdict"], "warn")  # loads unknown
        r = quality.check_mesh(allnodes, allq, loaded_nodes=[0, 8])
        self.assertEqual(r["quality"]["verdict"], "fail")
        r = quality.check_mesh(allnodes, allq, loaded_nodes=[0, len(nodes)])
        self.assertEqual(r["quality"]["verdict"], "ok")


class FrameMeshTest(unittest.TestCase):
    def test_offroad_frame_mesh(self):
        from hiraku.solvers import frame_mesh
        design = json.loads((ROOT / "data/designs/offroad-4w-v1.json").read_text(encoding="utf-8"))
        q = quality.record_quality(frame_mesh(design))
        self.assertIn(q["verdict"], ("ok", "warn"))
        self.assertEqual(q["topology"]["negative_volume"], 0)
        self.assertEqual(q["topology"]["flipped_normals"], 0)
        self.assertGreater(q["metrics"]["jacobian_ratio"]["worst"], 0.6)


def bar_model(kind="hex20"):
    m = fm.box_mesh([((0, -10, -10), (200, 10, 10))], 20.0, 2 if kind == "hex20" else 1)
    model = FEModel(m.nodes)
    model.add((Hex20 if kind == "hex20" else Hex8)(m.conn, 205000.0, 0.3, rho=7.85e-9))
    root = m.nodes_where(lambda x, y, z: x < 1e-9)
    F = surface_load(m.nodes, m.faces_where(lambda x, y, z: np.abs(x - 200) < 1e-9), (0.0, 0.0, -1000.0 / 400))
    return m, model, root, F


class PrecheckTest(unittest.TestCase):
    INTENDED = np.array([0.0, 0.0, -1000.0, 0.0, 200.0 * 1000.0, 0.0])  # -1 kN at x = 200: My = +200 kN mm

    def test_balance_of_a_correct_model(self):
        m, model, root, F = bar_model()
        model.fix(root, "xyz")
        out = analysis.run_static(model, F, design_mass_kg=7.85e-9 * 200 * 400 * 1000, intended=self.INTENDED)
        self.assertEqual(out["status"], "solved")
        res = {r["quantity"]: r for r in out["results"]}
        self.assertLess(res["equilibrium_residual"]["value"], 1e-9)
        self.assertLess(res["load_balance_residual"]["value"], 1e-9)
        self.assertAlmostEqual(res["mass_difference"]["value"], 0.0, places=9)
        self.assertEqual(res["model_mass"]["unit"], "kg")
        self.assertEqual(res["free_rigid_body_modes"]["value"], 0.0)

    def test_lost_load_shows_in_the_balance(self):
        m, model, root, F = bar_model()
        model.fix(root, "xyz")
        F_lost = F.copy()
        loaded = np.nonzero(F[:, 2])[0]
        F_lost[loaded[: len(loaded) // 2], 2] = 0.0  # half the tip load never reached the model
        out = analysis.run_static(model, F_lost, intended=self.INTENDED)
        res = {r["quantity"]: r["value"] for r in out["results"]}
        self.assertLess(res["equilibrium_residual"], 1e-9)  # what was applied is still balanced
        self.assertGreater(res["load_balance_residual"], 0.1)  # but not what was meant

    def test_missing_support_is_stopped_before_factorising(self):
        m, model, root, F = bar_model()
        model.fix(root, "z")  # only uz held on the face x = 0: free to slide in x and y, turn about z, and
        # turn about the y axis through that face (uz = -theta_y x is 0 there)
        with not_factorised():
            out = analysis.run_static(model, F)
            with self.assertRaisesRegex(ModelError, "rigid body"):
                StaticSolver(model)
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["rigid"]["free_rigid_modes"], 4)
        self.assertEqual(sorted(out["rigid"]["free_motions"]),
                         ["rotation y", "rotation z", "translation x", "translation y"])
        self.assertIn("rigid body", out["reasons"][0])

    def test_failed_mesh_is_not_solved(self):
        m, model, root, F = bar_model("hex8")
        model.fix(root, "xyz")
        g = model.groups[0]
        g.conn[3] = g.conn[3][[4, 5, 6, 7, 0, 1, 2, 3]]  # an inverted brick
        with not_factorised():
            out = analysis.run_static(model, F)
        self.assertEqual(out["status"], "failed")
        self.assertIn("negative volume", out["reasons"][0])
        self.assertEqual(out["quality"]["verdict"], "fail")

    def test_rigid_motion_energy_of_the_check_models(self):
        from hiraku.solvers.fe.precheck import rigid_body_energy
        r = checks.cantilever("hex8", 1)
        self.assertLess(rigid_body_energy(r["model"])["energy_ratio"], 1e-12)


if __name__ == "__main__":
    unittest.main()
