"""The fe core: DOF layout, assembly, elimination of constraints, rigid-body check, equilibrium."""

import unittest

import numpy as np

from hiraku.solvers.fe import Constraint, ElementGroup, Equation, FEModel, ModelError, StaticSolver, precheck


class Rod(ElementGroup):
    """Axial bar between two nodes (3 DOFs per node): k = EA/L along the bar. Only for testing the core."""

    kind = "rod"
    n_comp = 3

    def __init__(self, conn, EA):
        self.conn = np.asarray(conn, int).reshape(-1, 2)
        self.EA = np.broadcast_to(np.asarray(EA, float), (len(self.conn),))

    def stiffness(self, nodes):
        d = nodes[self.conn[:, 1]] - nodes[self.conn[:, 0]]
        L = np.linalg.norm(d, axis=1)
        n = d / L[:, None]
        k = (self.EA / L)[:, None, None] * np.einsum("ei,ej->eij", n, n)
        K = np.zeros((len(L), 6, 6))
        K[:, :3, :3] = k
        K[:, 3:, 3:] = k
        K[:, :3, 3:] = -k
        K[:, 3:, :3] = -k
        return K


def tetra():
    nodes = np.array([[0, 0, 0], [1000, 0, 0], [0, 1000, 0], [0, 0, 1000]], float)
    conn = [[0, 1], [0, 2], [0, 3], [1, 2], [1, 3], [2, 3]]
    m = FEModel(nodes)
    m.add(Rod(conn, 1.0e6))
    return m


def hold_321(m):
    m.fix(0, "xyz")
    m.fix(1, "yz")
    m.fix(2, "z")


class LayoutTest(unittest.TestCase):
    def test_dof_counts_follow_what_touches_a_node(self):
        m = tetra()
        m.add_node((5000.0, 0.0, 0.0))  # nothing touches it: no DOFs
        m.add(Constraint("tie", [Equation([(3, 3, 1.0)])]))  # a rotation on node 3 makes it a 6-DOF node
        lay = m.layout()
        self.assertEqual(lay.counts.tolist(), [3, 3, 3, 6, 0])
        self.assertEqual(lay.n_dof, 15)
        with self.assertRaises(ModelError):
            lay.dof(0, 3)


class SolveTest(unittest.TestCase):
    def test_two_bar_truss(self):
        # symmetric two-bar truss, bars at +-45 deg from a vertical load P: N = P / (2 cos 45), u = N L / (EA cos 45)
        a = 1000.0
        nodes = np.array([[-a, 0, a], [a, 0, a], [0, 0, 0]])
        m = FEModel(nodes)
        EA = 2.0e7
        m.add(Rod([[0, 2], [1, 2]], EA))
        m.fix([0, 1], "xyz")
        m.fix(2, "y")
        P = 1000.0
        F = np.zeros((3, 6))
        F[2, 2] = -P
        sol = StaticSolver(m).solve(F)
        L = a * np.sqrt(2.0)
        c = np.cos(np.pi / 4)
        expect = (P / (2 * c)) * L / EA / c
        self.assertAlmostEqual(-sol.displacements[2, 2], expect, delta=1e-12 * expect)
        eq = precheck.equilibrium(sol)
        self.assertLess(eq["residual"], 1e-12)
        # bar forces from the element nodal forces: |f| = N
        f = sol.element_forces(0)
        self.assertAlmostEqual(np.linalg.norm(f[0, 3:]), P / (2 * c), delta=1e-9 * P)

    def test_prescribed_value_and_equation(self):
        # rod 0-1 then node 2 tied to node 1 by u2 = u1 (x only) and a rod 2-3 to a fixed node 3
        nodes = np.array([[0, 0, 0], [100, 0, 0], [100, 0, 0], [300, 0, 0]], float)
        m = FEModel(nodes)
        m.add(Rod([[0, 1], [2, 3]], [1.0e5, 2.0e5]))
        m.fix(3, "xyz")
        m.fix([0, 1, 2], "yz")
        m.prescribe(0, 0, 0.5)
        m.add(Constraint("tie", [Equation([(2, 0, 1.0), (1, 0, -1.0)], prefer=(2, 0))]))
        sol = StaticSolver(m).solve(np.zeros((4, 6)))
        # springs in series k1 = 1e3, k2 = 1e3 N/mm, end displaced 0.5: middle at 0.25
        u = sol.displacements[:, 0]
        self.assertAlmostEqual(u[1], 0.25, places=12)
        self.assertAlmostEqual(u[2], 0.25, places=12)
        self.assertAlmostEqual(sol.reactions()[0, 0], 250.0, places=8)

    def test_contradicting_equations_are_refused(self):
        m = tetra()
        hold_321(m)
        m.add(Constraint("a", [Equation([(3, 0, 1.0)], rhs=1.0), Equation([(3, 0, 2.0)], rhs=1.0)]))
        with self.assertRaisesRegex(ModelError, "contradict"):
            StaticSolver(m)


class RigidBodyTest(unittest.TestCase):
    def test_free_motions_found_before_factorising(self):
        m = tetra()
        rb = precheck.rigid_body_check(m)
        self.assertEqual(rb["free_rigid_modes"], 6)
        hold_321(m)
        self.assertEqual(precheck.rigid_body_check(m)["free_rigid_modes"], 0)
        m2 = tetra()
        m2.fix(0, "xyz")
        m2.fix(1, "yz")  # z rotation about the x axis is free (no hold on node 2)
        rb = precheck.rigid_body_check(m2)
        self.assertEqual(rb["free_rigid_modes"], 1)
        self.assertEqual(rb["free_motions"], ["rotation x"])
        with self.assertRaisesRegex(ModelError, "rigid body"):
            StaticSolver(m2)

    def test_disconnected_group_is_found(self):
        m = tetra()
        hold_321(m)
        extra = m.add_nodes([[5000, 0, 0], [6000, 0, 0], [5000, 1000, 0], [5000, 0, 1000]])
        m.add(Rod([[extra[i], extra[j]] for i, j in [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]], 1.0e6))
        rb = precheck.rigid_body_check(m)
        self.assertEqual(rb["groups"], 2)
        self.assertEqual(rb["free_rigid_modes"], 6)
        self.assertTrue(all(s.startswith("group 1") for s in rb["free_motions"]))

    def test_rigid_motion_has_no_energy(self):
        r = precheck.rigid_body_energy(tetra())
        self.assertLess(r["energy_ratio"], 1e-12)


class ConstraintMethodTest(unittest.TestCase):
    """The same constrained truss by elimination, penalty and Lagrange multipliers."""

    def build(self):
        # two tetrahedra; the second is joined to the first by equations u(b) = u(a) at three node pairs
        m = tetra()
        hold_321(m)
        new = m.add_nodes(m.nodes[[3, 3, 3, 3]] + np.array([[0, 0, 0], [1000, 0, 0], [0, 1000, 0], [0, 0, 1000]]))
        m.add(Rod([[new[i], new[j]] for i, j in [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]], 1.0e6))
        eqs = [Equation([(int(new[0]), k, 1.0), (3, k, -1.0)], prefer=(int(new[0]), k)) for k in range(3)]
        # the new nodes 1 and 2 move with node 3 too (so the second tetra hangs on its top node)
        eqs += [Equation([(int(new[i]), k, 1.0), (3, k, -1.0)]) for i in (1, 2) for k in range(3)]
        m.add(Constraint("join", eqs))
        F = np.zeros((len(m.nodes), 6))
        F[new[3], 0] = 100.0
        F[new[3], 2] = -50.0
        F[3, 1] = 30.0
        return m, F

    def test_methods_agree_and_elimination_is_exact(self):
        m, F = self.build()
        out = {}
        for meth in ("eliminate", "penalty", "lagrange"):
            sol = StaticSolver(m, method=meth).solve(F)
            C, g = m.constraint_matrix(sol.layout)
            out[meth] = (sol.U, float(np.abs(C @ sol.U - g).max()))
        ref = out["eliminate"][0]
        self.assertLess(out["eliminate"][1], 1e-14)
        self.assertLess(out["lagrange"][1], 1e-12)
        self.assertLess(np.abs(out["lagrange"][0] - ref).max(), 1e-10 * np.abs(ref).max())
        self.assertLess(np.abs(out["penalty"][0] - ref).max(), 1e-5 * np.abs(ref).max())
        self.assertGreater(out["penalty"][1], out["eliminate"][1])


if __name__ == "__main__":
    unittest.main()
