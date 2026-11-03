"""Connections on the fe core: rigid and distributing links (7), springs (10)."""

import unittest

import numpy as np

from hiraku.solvers.fe import FEModel, StaticSolver, precheck, resultant
from hiraku.solvers.fe.connect import Spring, distributing_link, rbe3_forces, rigid_link
from hiraku.solvers.fe.shell import MITC4
from hiraku.solvers.k1_shell.checks import grid
from hiraku.solvers.k1_shell.solve import spread_load

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
