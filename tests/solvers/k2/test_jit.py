"""K2's compiled kernels (numba) against the plain Python they replace."""

import types
import unittest

import numpy as np

from hiraku.solvers.k2_vehicle import jit
from hiraku.solvers.k2_vehicle.sim import Sim, _solve_pairs


class SolvePairs(unittest.TestCase):
    def test_the_same_solution_as_numpy_from_python(self):
        rng = np.random.default_rng(3)
        pairs = [(6, 7), (8, 9)]
        for _ in range(200):
            a = rng.normal(size=(10, 10))
            mm = a @ a.T + 10.0 * np.eye(10)  # a mass matrix: symmetric, positive definite
            rhs = rng.normal(size=10)
            ref = Sim._solve_sym_py(types.SimpleNamespace(pairs=pairs), mm.copy(), rhs.copy())
            got = _solve_pairs(mm.copy(), rhs.copy(), np.array(pairs, dtype=np.int64))
            np.testing.assert_allclose(got, ref, rtol=1e-12, atol=1e-12 * float(np.max(np.abs(ref))))

    def test_a_mirrored_state_stays_mirrored(self):
        # equal pair columns and right-hand sides: the pair's difference is exactly zero
        mm = np.diag([5.0, 5.0, 5.0, 2.0, 3.0, 4.0, 1.0, 1.0])
        mm[2, 6] = mm[6, 2] = mm[2, 7] = mm[7, 2] = 0.25
        rhs = np.array([0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.5, 0.5])
        x = _solve_pairs(mm, rhs, np.array([(6, 7)], dtype=np.int64))
        self.assertEqual(x[6], x[7])


class KcPath(unittest.TestCase):
    def test_the_same_path_as_python_inside_and_past_the_table(self):
        from hiraku.solvers.k2_vehicle.model import box_design, build_vehicle
        from hiraku.solvers.k2_vehicle.scenario import new_sim

        sim = new_sim(build_vehicle(box_design(susp="kc_table", wheel_mass=15.0)), settle_s=0.0)
        kc = [i for i in range(sim.n) if sim.dof[i] and sim.kc[i]]
        self.assertTrue(kc)
        for i in kc:
            sim.cc[i], sim.ct[i] = 1e-3, -2e-3  # the compliance's camber and toe of the step before
            lo, hi = sim.s_lo[i], sim.s_hi[i]
            for s in np.linspace(lo - 0.05, hi + 0.05, 101):
                got, ref = sim._path(i, float(s)), sim._path_py(i, float(s))
                self.assertEqual(len(got), len(ref))
                for a, b in zip(got, ref):
                    self.assertLessEqual(abs(a - b), 1e-12 * max(1.0, abs(b)), (i, s, got, ref))


class Switch(unittest.TestCase):
    def test_jit_is_on_with_numba(self):
        import importlib.util
        import os

        expected = importlib.util.find_spec("numba") is not None and os.environ.get("HIRAKU_NO_JIT") != "1"
        self.assertEqual(jit.ENABLED, expected)


if __name__ == "__main__":
    unittest.main()
