"""MITC4 shells on the fe core: the same answers as k1_shell's own solver, and k1_shell's known answers
(shell_known_answers, 22 checks) run with the core in place of k1_shell's StaticSolver."""

import unittest
from unittest import mock

import numpy as np

from hiraku.solvers.fe import precheck
from hiraku.solvers.fe.shell import K1CompatSolver, from_shell_model, shell_stresses
from hiraku.solvers.k1_shell import checks as k1checks
from hiraku.solvers.k1_shell import frame as k1frame
from hiraku.solvers.k1_shell.solve import StaticSolver as K1StaticSolver
from hiraku.solvers.k1_shell.solve import dofs_of
from hiraku.solvers.k1_shell.stress import element_stresses


class SameAnswerTest(unittest.TestCase):
    def test_tube_cantilever_matches_k1_shell(self):
        res = k1checks.tube_cantilever(size=20.0, L=400.0, torsion=False)
        model, U_k1 = res["model"], res["U"]
        ms = res["mesh"]
        fixed = dofs_of(ms.end_rings[("m", "a")])
        F = np.zeros((model.n_dof, 2))
        F[6 * res["tip"] + 2, 0] = -res["P"]
        F[6 * res["tip"] + 1, 1] = res["P"]
        U_fe = K1CompatSolver(model, fixed).solve(F)
        self.assertLess(np.abs(U_fe - U_k1).max(), 1e-9 * np.abs(U_k1).max())
        # stresses through the fe group equal k1_shell's
        fe_model, g = from_shell_model(model)
        from hiraku.solvers.fe import StaticSolver
        for d in fixed:
            fe_model.prescribe(int(d) // 6, int(d) % 6, 0.0)
        sol = StaticSolver(fe_model).solve(F[:, 0])
        s_fe = shell_stresses(sol, g).von_mises("max")
        s_k1 = element_stresses(model, U_k1[:, 0]).von_mises("max")
        self.assertLess(np.abs(s_fe - s_k1).max(), 1e-7 * s_k1.max())
        self.assertAlmostEqual(fe_model.total_mass(), model.total_mass(), delta=1e-12 * model.total_mass())
        self.assertLess(precheck.equilibrium(sol)["residual"], 1e-9)
        self.assertLess(precheck.rigid_body_energy(fe_model)["energy_ratio"], 1e-10)


class K1KnownAnswersOnCoreTest(unittest.TestCase):
    def test_shell_known_answers_on_the_core(self):
        with mock.patch.object(k1checks, "StaticSolver", K1CompatSolver), \
                mock.patch.object(k1frame, "StaticSolver", K1CompatSolver):
            recs = k1checks.shell_known_answers()
        self.assertEqual(len(recs), 22)
        bad = [(r["case"], r["quantity"], r["expected"], r["got"]) for r in recs if not r["pass"]]
        self.assertEqual(bad, [])
        self.assertIsNot(k1checks.StaticSolver, K1CompatSolver)  # patched only inside the block
        self.assertIs(k1checks.StaticSolver, K1StaticSolver)


if __name__ == "__main__":
    unittest.main()
