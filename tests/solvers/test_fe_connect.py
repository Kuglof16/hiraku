"""Connections on the fe core: rigid and distributing links (7), shell to solid (8), springs (10)."""

import unittest

import numpy as np

from hiraku.solvers.fe import FEModel, StaticSolver, checks, precheck, resultant
from hiraku.solvers.fe.beam import Beam, line_mesh
from hiraku.solvers.fe.connect import (Spring, default_rotation_weights, distributing_link, rbe3_forces,
                                       rbe3_full_matrices, rbe3_loads, rbe3_matrices, rigid_link)
from hiraku.solvers.fe.shell import MITC4
from hiraku.solvers.k1_shell.checks import grid
from hiraku.solvers.k1_shell.solve import spread_load
from tests.solvers.k2.slow import SLOW

E, NU = 205000.0, 0.3


def plate(nx=10, ny=4, L=200.0, W=40.0, t=2.0):
    xy, q, idx = grid(nx, ny, L, W, y0=-W / 2)
    nodes = np.c_[xy, np.zeros(len(xy))]
    m = FEModel(nodes)
    m.add(MITC4(q, t, E, NU))
    m.fix(idx[0], "all")
    return m, idx


FORCE = np.array([10.0, 20.0, -100.0])
MOMENT = np.array([1000.0, 2000.0, 500.0])
REF = np.array([220.0, 3.0, 10.0])


def loaded(kind):
    m, idx = plate()
    ref = m.add_node(REF)
    edge = idx[-2:].ravel()  # the last two rows of nodes (a footprint, not one line)
    if kind == "rbe2":
        m.add(rigid_link(ref, edge, m.nodes))
    else:
        m.add(distributing_link(ref, edge, m.nodes))
    F = np.zeros((len(m.nodes), 6))
    F[ref, :3] = FORCE
    F[ref, 3:] = MOMENT
    return m, idx, ref, F


def rigid_fit_residual(x, u):
    """RMS of the nodal displacements u (n, 3) left after removing their best rigid motion."""
    c = x.mean(axis=0)
    r = x - c
    A = np.zeros((3 * len(x), 6))
    for i, ri in enumerate(r):
        A[3 * i:3 * i + 3, :3] = np.eye(3)
        A[3 * i:3 * i + 3, 3:] = -np.array([[0, -ri[2], ri[1]], [ri[2], 0, -ri[0]], [-ri[1], ri[0], 0]])
    coef = np.linalg.lstsq(A, u.ravel(), rcond=None)[0]
    return float(np.sqrt(np.mean((u.ravel() - A @ coef) ** 2)))


class LinkTest(unittest.TestCase):
    def test_links_deliver_the_load(self):
        applied = np.r_[FORCE, MOMENT + np.cross(REF, FORCE)]  # about the origin
        for kind in ("rbe2", "rbe3"):
            m, idx, ref, F = loaded(kind)
            sol = StaticSolver(m).solve(F)
            Ku = sol.layout.node_array(sol.K @ sol.U)
            e = idx[-2:].ravel()
            delivered = resultant(m.nodes[e], Ku[e])  # forces the link puts on the connected nodes
            self.assertLess(np.abs(delivered - applied).max(), 1e-9 * np.abs(applied).max(), kind)
            self.assertLess(precheck.equilibrium(sol)["residual"], 1e-8, kind)
            r = precheck.rigid_body_energy(m)
            self.assertLess(r["constraint_residual"], 1e-12, kind)

    def test_rbe3_adds_no_stiffness(self):
        m, idx, ref, F = loaded("rbe3")
        sol3 = StaticSolver(m).solve(F)
        # the same plate with the link's nodal forces put on the edge directly
        m0, idx0 = plate()
        F0 = np.zeros((len(m0.nodes), 6))
        e0 = idx0[-2:].ravel()
        F0[e0, :3] = rbe3_forces(REF, m0.nodes[e0], FORCE, MOMENT)
        sol0 = StaticSolver(m0).solve(F0)
        n = len(m0.nodes)
        d3, d0 = sol3.displacements[:n], sol0.displacements
        self.assertLess(np.abs(d3 - d0).max(), 1e-10 * np.abs(d0).max())
        # k1_shell's spread_load is the same distribution (equal weights)
        f_k1 = spread_load(m0.nodes, e0, FORCE, MOMENT, about=REF).reshape(-1, 6)[e0, :3]
        self.assertLess(np.abs(f_k1 - F0[e0, :3]).max(), 1e-9 * np.abs(F0).max())

    def test_rbe2_stiffens_and_rbe3_does_not(self):
        out = {}
        for kind in ("rbe2", "rbe3"):
            m, idx, ref, F = loaded(kind)
            sol = StaticSolver(m).solve(F)
            e = idx[-2:].ravel()
            out[kind] = (rigid_fit_residual(m.nodes[e], sol.displacements[e, :3]), sol.strain_energy(),
                         sol.displacements[ref].copy())
        self.assertLess(out["rbe2"][0], 1e-12)  # the footprint moves as a rigid body
        self.assertGreater(out["rbe3"][0], 1e-4 * np.abs(out["rbe3"][2][:3]).max())  # the footprint deforms
        self.assertGreater(out["rbe3"][1], out["rbe2"][1])  # more flexible: more energy under the same load
        self.stats = out


class LineRBE3Test(unittest.TestCase):
    """RBE3 on nodes that lie on one line (a shell edge, a beam's nodes): the connected nodes' rotations
    fix the twist about the line."""

    def edge_model(self):
        m, idx = plate()
        ref = m.add_node(REF)
        edge = idx[-1]  # the tip edge: one straight line of nodes
        return m, idx, ref, edge

    def test_translations_alone_are_refused_on_a_line(self):
        m, idx, ref, edge = self.edge_model()
        with self.assertRaises(ValueError):
            distributing_link(ref, edge, m.nodes, rotation_weights=0.0)

    def test_balance_and_no_added_stiffness(self):
        applied = np.r_[FORCE, MOMENT + np.cross(REF, FORCE)]
        m, idx, ref, edge = self.edge_model()
        link = m.add(distributing_link(ref, edge, m.nodes))
        self.assertIsNotNone(link.rotation_weights)  # chosen because the nodes are on one line
        F = np.zeros((len(m.nodes), 6))
        F[ref, :3], F[ref, 3:] = FORCE, MOMENT
        sol = StaticSolver(m).solve(F)
        Ku = sol.layout.node_array(sol.K @ sol.U)
        delivered = resultant(m.nodes[edge], Ku[edge])
        self.assertLess(np.abs(delivered - applied).max(), 1e-9 * np.abs(applied).max())
        self.assertLess(precheck.equilibrium(sol)["residual"], 1e-9)
        self.assertLess(precheck.rigid_body_energy(m)["constraint_residual"], 1e-12)
        # the same plate with the link's nodal loads put on the edge: the same displacements
        m0, idx0 = plate()
        F0 = np.zeros((len(m0.nodes), 6))
        F0[edge] = rbe3_loads(REF, m0.nodes[edge], FORCE, MOMENT)
        self.assertLess(np.abs(resultant(m0.nodes, F0) - applied).max(), 1e-9 * np.abs(applied).max())
        sol0 = StaticSolver(m0).solve(F0)
        n = len(m0.nodes)
        self.assertLess(np.abs(sol.displacements[:n] - sol0.displacements).max(),
                        1e-10 * np.abs(sol0.displacements).max())

    def test_twist_about_the_line_goes_to_the_rotations_by_weight(self):
        """A moment about the edge's own line (y) reaches the edge nodes as moments about y shared in proportion
        to the weights, whatever the rotation-weight factor; it puts no net force on them."""
        m, idx, ref, edge = self.edge_model()
        x = m.nodes[edge]
        w = np.linspace(1.0, 2.0, len(edge))
        c = (w[:, None] * x).sum(axis=0) / w.sum()
        for factor in (1e-3, 1.0):
            v = default_rotation_weights(x, w, factor)
            f = rbe3_loads(c, x, (0, 0, 0), (0.0, 1000.0, 0.0), w, v)
            self.assertLess(np.abs(f[:, 4] - 1000.0 * w / w.sum()).max(), 1e-9 * 1000.0)
            self.assertLess(np.abs(f[:, :3].sum(axis=0)).max(), 1e-9 * 1000.0)

    def test_on_beam_nodes(self):
        """A beam cantilever along x, RBE3 from an off-axis point to its last three nodes: the load arrives in
        full and the tip moves as the beam carries the same loads put on the nodes directly."""
        x, c = line_mesh((0, 0, 0), (600.0, 0, 0), 6)
        def model():
            mm = FEModel(x)
            mm.add(Beam(c, 2e5, 8e4, 400.0, 2e4, 3e4, 4e4))
            mm.fix(0, "all")
            return mm
        p = np.array([550.0, 40.0, -30.0])
        ids = [4, 5, 6]
        m = model()
        ref = m.add_node(p)
        m.add(distributing_link(ref, ids, m.nodes))
        F = np.zeros((len(m.nodes), 6))
        F[ref, :3], F[ref, 3:] = FORCE, MOMENT
        sol = StaticSolver(m).solve(F)
        applied = np.r_[FORCE, MOMENT + np.cross(p, FORCE)]
        R = sol.reactions()
        self.assertLess(np.abs(resultant(m.nodes, R) + applied).max(), 1e-9 * np.abs(applied).max())
        m0 = model()
        F0 = np.zeros((len(x), 6))
        F0[ids] = rbe3_loads(p, x[ids], FORCE, MOMENT)
        sol0 = StaticSolver(m0).solve(F0)
        self.assertLess(np.abs(sol.displacements[:len(x)] - sol0.displacements).max(),
                        1e-10 * np.abs(sol0.displacements).max())

    def test_solid_nodes_on_a_line_are_refused(self):
        from hiraku.solvers.fe.solid import Hex8
        nodes = np.array([[i, j, k] for k in (0, 10) for j in (0, 10) for i in (0, 10)], float)
        m = FEModel(nodes)
        m.add(Hex8([[0, 1, 3, 2, 4, 5, 7, 6]], 2e5, 0.3))
        ref = m.add_node((5.0, 5.0, 20.0))
        m.add(distributing_link(ref, [4, 5], m.nodes))
        with self.assertRaises(ValueError):
            m.equations()

    def test_plane_footprint_is_unchanged(self):
        """Nodes off one line: no rotation weights by default, the same matrices as before."""
        m, idx = plate()
        e = idx[-2:].ravel()
        H, G = rbe3_matrices(REF, m.nodes[e])
        H2, Hr, G2, Gr = rbe3_full_matrices(REF, m.nodes[e])
        self.assertEqual(np.abs(H - H2).max(), 0.0)
        self.assertEqual(np.abs(G - G2).max(), 0.0)
        self.assertEqual(np.abs(Hr).max() + np.abs(Gr).max(), 0.0)


class ConstraintMethodOnLinksTest(unittest.TestCase):
    """Elimination, Lagrange multipliers and penalty on a shell plate with a rigid link over 22 nodes:
    accuracy, constraint violation and the condition number of the matrix that is factorised."""

    @staticmethod
    def cond(A):
        sv = np.linalg.svd(A.toarray(), compute_uv=False)
        return sv[0] / sv[-1]

    def test_elimination_keeps_the_condition(self):
        from hiraku.solvers.fe import ModelError
        m, idx, ref, F = loaded("rbe2")
        out = {}
        for meth, kw in (("eliminate", {}), ("lagrange", {}), ("penalty", {"penalty_scale": 1e2})):
            s = StaticSolver(m, method=meth, **kw)
            sol = s.solve(F)
            C, g = m.constraint_matrix(sol.layout)
            out[meth] = (sol.displacements[ref, 2], float(np.abs(C @ sol.U - g).max()), self.cond(s.A))
        m0, _ = plate()
        c0 = self.cond(StaticSolver(m0).A)
        u = out["eliminate"][0]
        self.assertLess(out["eliminate"][1], 1e-12)
        self.assertLess(out["eliminate"][2], 1.1 * c0)  # no worse than the plate without the link
        self.assertLess(abs(out["lagrange"][0] / u - 1), 1e-8)
        self.assertGreater(out["lagrange"][2], 1e3 * out["eliminate"][2])
        self.assertGreater(abs(out["penalty"][0] / u - 1), 1e-8)  # penalty leaves an error ...
        self.assertGreater(out["penalty"][2], 1e3 * out["eliminate"][2])  # ... and a worse matrix
        with self.assertRaises(ModelError):  # a larger penalty loses the solution; the residual check says so
            StaticSolver(m, method="penalty", penalty_scale=1e6).solve(F)


class ShellSolidTest(unittest.TestCase):
    def test_cantilever_half_solid_half_shell(self):
        r = checks.shell_solid_cantilever()
        self.assertAlmostEqual(r["mixed"], r["all_solid"], delta=0.01 * r["all_solid"])
        self.assertAlmostEqual(r["mixed"], r["all_shell"], delta=0.02 * r["all_shell"])
        self.assertAlmostEqual(r["mixed"], r["beam"], delta=0.03 * r["beam"])
        self.assertLess(r["mixed_equilibrium"], 1e-8)
        self.assertLess(r["mixed_rigid"]["energy_ratio"], 1e-12)
        self.assertLess(r["mixed_rigid"]["constraint_residual"], 1e-12)


class TieTest(unittest.TestCase):
    def test_projection_onto_quad8_face(self):
        from hiraku.solvers.fe.connect import project_to_faces
        from hiraku.solvers.fe.solid import shape_quad8
        nodes = np.array([[0, 0, 0], [10, 0, 0], [10, 10, 1], [0, 10, 0], [5, 0, 0.2], [10, 5, 0.4], [5, 10, 0.3],
                          [0, 5, -0.2]], float)
        N, _ = shape_quad8(0.3, -0.6)
        p = N @ nodes + np.array([0.0, 0.0, 0.0])
        f, xi, eta, dist, inside = project_to_faces(p[None, :], np.arange(8)[None, :], nodes)[0]
        self.assertTrue(inside)
        self.assertAlmostEqual(xi, 0.3, places=9)
        self.assertAlmostEqual(eta, -0.6, places=9)
        self.assertLess(dist, 1e-9)

    QUAD8 = np.array([[0, 0, 0], [10, 0, 0], [10, 10, 1], [0, 10, 0], [5, 0, 0.2], [10, 5, 0.4], [5, 10, 0.3],
                      [0, 5, -0.2]], float)

    def _check_weights(self, w, nodes):
        x_s = nodes[w["slave"]]
        N = np.asarray(w["weights"])
        self.assertEqual(len(N), len(w["masters"]))
        self.assertAlmostEqual(N.sum(), 1.0, places=12)
        back = N @ nodes[w["masters"]] + np.asarray(w["offset"])
        np.testing.assert_allclose(back, x_s, rtol=0, atol=1e-12)
        self.assertAlmostEqual(w["gap"], np.linalg.norm(w["offset"]), places=12)

    def test_tie_weights_flat_quad4(self):
        from hiraku.solvers.fe.connect import tie_weights
        face = np.array([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]], float)
        pts = np.array([[3.0, 4.0, 0.0], [3.0, 4.0, 0.5], [7.5, 2.0, -0.25], [10.4, 4.0, 0.3], [-0.2, -0.3, 0.0]])
        # distance to the square [0, 10]^2 in z = 0
        dist = [0.0, 0.5, 0.25, np.hypot(0.4, 0.3), np.hypot(0.2, 0.3)]
        nodes = np.vstack([face, pts])
        tw = tie_weights(np.arange(4, 9), np.arange(4)[None, :], nodes)
        self.assertEqual([w["slave"] for w in tw], [4, 5, 6, 7, 8])
        for w, d, inside in zip(tw, dist, (True, True, True, False, False)):
            self.assertEqual(w["face"], 0)
            self.assertEqual(w["masters"], [0, 1, 2, 3])
            self.assertEqual(w["inside"], inside)
            self._check_weights(w, nodes)
            self.assertAlmostEqual(w["gap"], d, places=9)
        # on the face at (3, 4): bilinear weights of (xi, eta) = (-0.4, -0.2)
        xi, eta = -0.4, -0.2
        expect = [(1 - xi) * (1 - eta) / 4, (1 + xi) * (1 - eta) / 4, (1 + xi) * (1 + eta) / 4,
                  (1 - xi) * (1 + eta) / 4]
        np.testing.assert_allclose(tw[0]["weights"], expect, rtol=0, atol=1e-12)
        self.assertAlmostEqual(tw[0]["xi"], xi, places=12)
        self.assertAlmostEqual(tw[0]["eta"], eta, places=12)

    def test_tie_weights_quad8(self):
        from hiraku.solvers.fe.connect import tie_weights
        from hiraku.solvers.fe.solid import shape_quad8
        X = self.QUAD8
        pts, gaps = [], []
        for xi, eta, h in ((0.3, -0.6, 0.0), (0.3, -0.6, 0.05), (-0.7, 0.4, -0.03), (0.9, 0.9, 0.02)):
            N, dN = shape_quad8(xi, eta)
            J = dN @ X
            n = np.cross(J[0], J[1])
            pts.append(N @ X + h * n / np.linalg.norm(n))  # along the normal: the face point is the closest
            gaps.append(abs(h))
        # slightly outside the edge xi = 1: closest point on that edge, found by sampling it finely
        N, _ = shape_quad8(1.0, 0.0)
        p_out = N @ X + np.array([0.3, 0.0, 0.0])
        edge = np.array([shape_quad8(1.0, e)[0] @ X for e in np.linspace(-1, 1, 200001)])
        pts.append(p_out)
        gaps.append(np.linalg.norm(edge - p_out, axis=1).min())
        nodes = np.vstack([X, pts])
        tw = tie_weights(np.arange(8, 8 + len(pts)), np.arange(8)[None, :], nodes)
        for w, g in zip(tw[:4], gaps[:4]):
            self.assertAlmostEqual(w["gap"], g, places=9)
        # outside a curved face the projection is the unconstrained solution clamped to the edge, not the exact
        # closest point on it: close to the distance, not equal (on a flat face it is exact, see the quad4 test)
        self.assertLessEqual(gaps[4], tw[4]["gap"])
        self.assertLess(tw[4]["gap"] - gaps[4], 1e-4 * gaps[4])
        for w in tw:
            self.assertEqual(w["masters"], list(range(8)))
            self._check_weights(w, nodes)
        self.assertTrue(all(w["inside"] for w in tw[:4]))
        self.assertFalse(tw[4]["inside"])
        self.assertAlmostEqual(tw[1]["xi"], 0.3, places=6)
        self.assertAlmostEqual(tw[1]["eta"], -0.6, places=6)

    def test_tie_coefficients_are_the_weights(self):
        from hiraku.solvers.fe.connect import skew, tie, tie_weights
        X = self.QUAD8
        pts = np.array([[4.0, 3.0, 0.6], [8.0, 7.0, 0.1], [10.3, 5.0, 0.5]])
        nodes = np.vstack([X, pts])
        slaves = [8, 9, 10]
        faces = np.arange(8)[None, :]
        tw = tie_weights(slaves, faces, nodes)
        c = tie(slaves, faces, nodes)
        self.assertEqual(c.projection, [{k: w[k] for k in ("slave", "face", "xi", "eta", "gap", "inside")}
                                        for w in tw])
        for cnt in (6, 3):
            eqs = c.make(np.full(len(nodes), cnt))
            self.assertEqual(len(eqs), len(slaves) * (6 if cnt == 6 else 3))
            for w in tw:
                s = w["slave"]
                Sd = skew(np.asarray(w["offset"]))
                for comp in range(6 if cnt == 6 else 3):
                    (eq,) = [e for e in eqs if e.prefer == (s, comp)]
                    coef = {}
                    for node, k, a in eq.terms:
                        coef[(node, k)] = coef.get((node, k), 0.0) + a
                    expect = {(s, comp): 1.0}
                    for i, n in zip(w["masters"], w["weights"]):
                        expect[(i, comp)] = expect.get((i, comp), 0.0) - n
                        if comp < 3 and cnt == 6:  # + theta_p x offset = -[offset]x theta_p
                            for l in range(3):
                                expect[(i, 3 + l)] = expect.get((i, 3 + l), 0.0) + n * Sd[comp, l]
                    keys = set(coef) | set(expect)
                    for key in keys:
                        self.assertAlmostEqual(coef.get(key, 0.0), expect.get(key, 0.0), places=14, msg=str(key))

    def test_tie_weights_max_gap(self):
        from hiraku.solvers.fe.connect import tie, tie_weights
        face = np.array([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]], float)
        nodes = np.vstack([face, [[5.0, 5.0, 0.1], [5.0, 5.0, 2.0]]])
        self.assertEqual(len(tie_weights([4], np.arange(4)[None, :], nodes, max_gap=0.5)), 1)
        for fn in (tie_weights, tie):
            with self.assertRaises(ValueError) as cm:
                fn([4, 5], np.arange(4)[None, :], nodes, max_gap=0.5)
            self.assertEqual(str(cm.exception), "tie: slave node 5 is 2 from the surface (more than 0.5)")

    def test_rect_t_tied_vs_shared_nodes(self):
        a = checks.rect_t_joint(5.0, tied=False)
        b = checks.rect_t_joint(5.0, tied=True)
        da, db = np.linalg.norm(a["tip"]), np.linalg.norm(b["tip"])
        self.assertAlmostEqual(db, da, delta=0.06 * da)
        # the peak is at the footprint's singular corner on both meshes (within one 5 mm element of it); its
        # value depends on the corner elements' size (the shared mesh has t x t weld strips there), so the
        # two are compared at least 10 mm (two elements, about 4 t) from the footprint
        for r in (a, b):
            self.assertLess(r["d_foot_of_max"], 5.0)
            self.assertGreater(r["vm_max_near_joint"], r["vm_max_off_corner"])
        self.assertAlmostEqual(b["vm_max_off_corner"], a["vm_max_off_corner"], delta=0.10 * a["vm_max_off_corner"])
        self.assertLess(b["equilibrium"], 1e-8)
        r = precheck.rigid_body_energy(b["model"])
        self.assertLess(r["energy_ratio"], 1e-12)
        self.assertLess(r["constraint_residual"], 1e-12)

    def test_oblique_round_t_solves(self):
        for ang in (60.0, 45.0):
            r16 = checks.oblique_round_t(ang, 16)
            r32 = checks.oblique_round_t(ang, 32)
            self.assertTrue(r16["all_inside"] and r32["all_inside"])
            self.assertLess(r32["equilibrium"], 1e-8)
            d16, d32 = -r16["tip"][2], -r32["tip"][2]
            self.assertGreater(d32, d16)  # finer is more flexible (the joint's local flexibility)
            self.assertLess((d32 - d16) / d32, 0.15)
            self.assertLess(precheck.rigid_body_energy(r32["model"])["constraint_residual"], 1e-12)


@SLOW
class SlowTieTest(unittest.TestCase):
    def test_tied_t_converges_to_shared(self):
        diffs = []
        for size in (10.0, 5.0, 2.5):
            a = checks.rect_t_joint(size, tied=False)
            b = checks.rect_t_joint(size, tied=True)
            diffs.append(abs(np.linalg.norm(b["tip"]) / np.linalg.norm(a["tip"]) - 1))
        self.assertGreater(diffs[0], diffs[1])
        self.assertGreater(diffs[1], diffs[2])

    def test_oblique_round_t_converges(self):
        for ang in (60.0, 45.0):
            tips = [-checks.oblique_round_t(ang, n)["tip"][2] for n in (16, 32, 64)]
            self.assertGreater(abs(tips[1] - tips[0]), abs(tips[2] - tips[1]))


class SpringTest(unittest.TestCase):
    def line_model(self, n_nodes):
        return FEModel(np.c_[np.arange(n_nodes) * 100.0, np.zeros(n_nodes), np.zeros(n_nodes)])

    def test_series_and_parallel(self):
        k1, k2, P = 300.0, 700.0, 50.0
        m = self.line_model(2)
        m.add(Spring([[0, -1], [0, 1]], [[k1, k1, k1, 0, 0, 0], [k2, k2, k2, 0, 0, 0]]))
        F = np.zeros((2, 6))
        F[1, 0] = P
        sol = StaticSolver(m).solve(F)
        self.assertAlmostEqual(sol.displacements[1, 0], P / k1 + P / k2, places=12)  # series
        m = self.line_model(1)
        m.add(Spring([[0, -1], [0, -1]], [[k1] * 3 + [0] * 3, [k2] * 3 + [0] * 3]))
        F = np.zeros((1, 6))
        F[0, 1] = P
        sol = StaticSolver(m).solve(F)
        self.assertAlmostEqual(sol.displacements[0, 1], P / (k1 + k2), places=12)  # parallel


    def test_rotational_spring(self):
        kr, M = 2.0e6, 3.0e4
        m = self.line_model(2)
        m.add(Spring([[0, 1]], [1e5, 1e5, 1e5, kr, kr, 3 * kr]))
        m.fix(0, "all")
        F = np.zeros((2, 6))
        F[1, 3] = M
        F[1, 5] = M
        sol = StaticSolver(m).solve(F)
        self.assertAlmostEqual(sol.displacements[1, 3], M / kr, delta=1e-12)
        self.assertAlmostEqual(sol.displacements[1, 5], M / (3 * kr), delta=1e-12)

    def test_rotated_spring(self):
        a = np.radians(30.0)
        R = np.array([[np.cos(a), np.sin(a), 0.0], [-np.sin(a), np.cos(a), 0.0], [0.0, 0.0, 1.0]])  # rows: axes
        k = np.array([100.0, 400.0, 900.0])
        m = self.line_model(1)
        m.add(Spring([[0, -1]], np.r_[k, 0, 0, 0], frames=R))
        F = np.zeros((1, 6))
        F[0, 0] = 10.0
        sol = StaticSolver(m).solve(F)
        expect = R.T @ np.diag(1.0 / k) @ R @ F[0, :3]
        self.assertLess(np.abs(sol.displacements[0, :3] - expect).max(), 1e-14)
        self.assertGreater(abs(sol.displacements[0, 1]), 1e-3)  # the spring's axes make y move too

    def test_offset_bushing_has_no_rigid_energy(self):
        m = FEModel(np.array([[0.0, 0.0, 0.0], [30.0, 40.0, -20.0]]))
        R = np.linalg.qr(np.array([[1.0, 2.0, 0.5], [0.3, -1.0, 2.0], [1.0, 0.0, 1.0]]))[0].T
        m.add(Spring([[0, 1]], [1e3, 2e3, 3e3, 1e5, 2e5, 3e5], frames=R))
        r = precheck.rigid_body_energy(m)
        self.assertLess(r["energy_ratio"], 1e-14)


if __name__ == "__main__":
    unittest.main()
