"""K0 load_path.post_node and control_deflection's twist term against closed forms."""

import unittest

from hiraku.solvers import formula_registry

REG = formula_registry()
E, G, A, I, J = 205000.0, 205000.0 / 2.6, 254.84, 32814.0, 48884.0


def q(v, u):
    return {"value": v, "unit": u}


def post(F, a, b, N=0.0, T=0.0, P1=0.0, M1=0.0, P2=0.0, M2=0.0):
    ins = {"F": q(F, "N"), "N": q(N, "N"), "T": q(T, "N*mm"), "P_1": q(P1, "N"), "M_1": q(M1, "N*mm"),
           "P_2": q(P2, "N"), "M_2": q(M2, "N*mm"), "a": q(a, "mm"), "b": q(b, "mm"), "E": q(E, "MPa"),
           "G": q(G, "MPa"), "A": q(A, "mm^2"), "I_1": q(I, "mm^4"), "I_2": q(I, "mm^4"), "J": q(J, "mm^4")}
    return REG.evaluate("load_path.post_node", ins)["outputs"]["delta"]["value"]


class PostNodeTest(unittest.TestCase):
    def test_force_across_at_mid_span(self):
        """A beam clamped at both ends, load P at mid-span: P L^3 / (192 E I)."""
        P, L = 500.0, 400.0
        self.assertAlmostEqual(post(P, L / 2, L / 2, P1=P), P * L ** 3 / (192 * E * I), delta=1e-9)

    def test_force_across_off_centre(self):
        """Load P at a from one end of a clamped-clamped beam: P a^3 b^3 / (3 E I L^3)."""
        P, a, b = 500.0, 120.0, 90.0
        L = a + b
        self.assertAlmostEqual(post(P, a, b, P2=P), P * a ** 3 * b ** 3 / (3 * E * I * L ** 3), delta=1e-9)

    def test_moment_at_mid_span(self):
        """A moment M0 at mid-span of a clamped-clamped beam turns the node by M0 L / (16 E I); a load F at lever
        r = M0 / F across moves along itself by r theta."""
        F, r, L = 500.0, 100.0, 400.0
        M0 = F * r
        theta = M0 * L / (16 * E * I)
        self.assertAlmostEqual(post(F, L / 2, L / 2, M1=M0), r * theta, delta=1e-9)

    def test_twist_and_axial(self):
        """Torsion and axial springs of the two segments in parallel."""
        F, r, a, b = 500.0, 80.0, 120.0, 90.0
        self.assertAlmostEqual(post(F, a, b, T=F * r), F * r ** 2 / (G * J * (1 / a + 1 / b)), delta=1e-12)
        self.assertAlmostEqual(post(F, a, b, N=F), F / (E * A * (1 / a + 1 / b)), delta=1e-12)


class ControlTwistTest(unittest.TestCase):
    def test_twist_term(self):
        """A cantilever of length L twisted by F r_t: the load point moves along F by F r_t^2 L / (G J)."""
        F, L, r_t = 500.0, 121.875, 101.0
        ins = {"F": q(F, "N"), "k_t": q(1e15, "N/mm"), "k_r": q(1e15, "N/mm"), "L": q(L, "mm"), "E": q(E, "MPa"),
               "I": q(1e15, "mm^4"), "delta_plate": q(0.0, "mm"), "G": q(G, "MPa"), "J": q(J, "mm^4"),
               "r_t": q(r_t, "mm"), "delta_root": q(0.0, "mm")}
        got = REG.evaluate("load_path.control_deflection", ins)["outputs"]["delta"]["value"]
        self.assertAlmostEqual(got, F * r_t ** 2 * L / (G * J), delta=1e-9)


if __name__ == "__main__":
    unittest.main()
