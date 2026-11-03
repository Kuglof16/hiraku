"""The optional PARDISO path of k1_shell.solve.StaticSolver.

- The choice (HIRAKU_SOLVER, using(), whether pypardiso imports) and the wiring, with a stand-in
  module that factorises with SuperLU (runs everywhere).
- PARDISO itself against SuperLU on small models, within solve.PARDISO_REL_TOL (skipped when
  pypardiso is not installed).
"""

import os
import sys
import types
import unittest
from unittest import mock

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla

from hiraku.solvers.k1_shell import checks, solve


def _stand_in() -> types.ModuleType:
    """A module with pypardiso's PyPardisoSolver interface (factorize(A), solve(A, b)), on SuperLU."""
    mod = types.ModuleType("pypardiso")

    class PyPardisoSolver:
        calls = []

        def factorize(self, A):
            PyPardisoSolver.calls.append(A.shape)
            self.lu = spla.splu(sp.csc_matrix(A))

        def solve(self, A, b):
            return self.lu.solve(np.asarray(b, float))

    mod.PyPardisoSolver = PyPardisoSolver
    return mod


def _real_pardiso():
    try:
        import pypardiso  # noqa: F401
    except Exception:
        return None
    return sys.modules["pypardiso"]


class Choice(unittest.TestCase):
    def test_default_follows_the_import(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HIRAKU_SOLVER", None)
            with mock.patch.object(solve, "pardiso_module", return_value=None):
                self.assertEqual(solve.solver_choice(), "superlu")
            with mock.patch.object(solve, "pardiso_module", return_value=_stand_in()):
                self.assertEqual(solve.solver_choice(), "pardiso")

    def test_environment_and_using(self):
        with mock.patch.object(solve, "pardiso_module", return_value=_stand_in()):
            with mock.patch.dict(os.environ, {"HIRAKU_SOLVER": "superlu"}):
                self.assertEqual(solve.solver_choice(), "superlu")
                with solve.using("pardiso"):
                    self.assertEqual(solve.solver_choice(), "pardiso")
                self.assertEqual(solve.solver_choice(), "superlu")
            with mock.patch.dict(os.environ, {"HIRAKU_SOLVER": "pardiso"}), solve.using("superlu"):
                self.assertEqual(solve.solver_choice(), "superlu")
        with mock.patch.object(solve, "pardiso_module", return_value=None), \
                mock.patch.dict(os.environ, {"HIRAKU_SOLVER": "pardiso"}):
            self.assertEqual(solve.solver_choice(), "superlu")  # asked for but not installed
        with self.assertRaises(ValueError):
            with solve.using("umfpack"):
                pass

    def test_pardiso_asked_but_missing(self):
        res = checks.tube_cantilever(size=20.0, torsion=False)
        with mock.patch.object(solve, "pardiso_module", return_value=None):
            with self.assertRaises(ValueError):
                solve.StaticSolver(res["model"], solve.dofs_of(res["mesh"].end_rings[("m", "a")]), solver="pardiso")

    def test_assumption_names_the_solver(self):
        self.assertIn("SuperLU", solve.solver_assumption("superlu"))
        text = solve.solver_assumption("pardiso")
        self.assertIn("PARDISO", text)
        self.assertIn(f"{solve.PARDISO_REL_TOL:g}", text)


class Wiring(unittest.TestCase):
    """The pardiso path through StaticSolver with the stand-in module: the same displacements as SuperLU
    (the stand-in is SuperLU without the symmetric options, so only round-off apart)."""

    def test_tube_cantilever_both_ways(self):
        mod = _stand_in()
        with solve.using("superlu"):
            a = checks.tube_cantilever(size=20.0)
        with mock.patch.object(solve, "pardiso_module", return_value=mod), solve.using("pardiso"):
            b = checks.tube_cantilever(size=20.0)
        self.assertTrue(mod.PyPardisoSolver.calls, "the stand-in was not used")
        scale = np.abs(a["U"]).max()
        self.assertLessEqual(np.abs(a["U"] - b["U"]).max(), solve.PARDISO_REL_TOL * scale)

    def test_known_answers_use_superlu(self):
        mod = _stand_in()
        with mock.patch.object(solve, "pardiso_module", return_value=mod), \
                mock.patch.dict(os.environ, {"HIRAKU_SOLVER": "pardiso"}), \
                mock.patch.object(checks, "_known_answers", side_effect=lambda: [solve.solver_choice()]):
            self.assertEqual(checks.shell_known_answers(), ["superlu"])


@unittest.skipIf(_real_pardiso() is None, "pypardiso is not installed (optional; pip install pypardiso)")
class PardisoAgainstSuperLU(unittest.TestCase):
    """Intel MKL PARDISO against SuperLU on the known-answer models, within PARDISO_REL_TOL of the largest value."""

    def _both(self, make):
        with solve.using("superlu"):
            a = make()
        with solve.using("pardiso"):
            b = make()
        return a, b

    def test_tube_cantilever(self):
        a, b = self._both(lambda: checks.tube_cantilever(size=10.0))
        for key in ("U", "U_torsion"):
            with self.subTest(key=key):
                scale = np.abs(a[key]).max()
                self.assertLessEqual(np.abs(a[key] - b[key]).max(), solve.PARDISO_REL_TOL * scale)

    def test_square_plate(self):
        (wa, exp), (wb, _) = self._both(lambda: checks.square_plate(16))
        self.assertLessEqual(abs(wa - wb), solve.PARDISO_REL_TOL * abs(exp))

    def test_solver_name(self):
        res = checks.tube_cantilever(size=20.0, torsion=False)
        s = solve.StaticSolver(res["model"], solve.dofs_of(res["mesh"].end_rings[("m", "a")]), solver="pardiso")
        self.assertEqual(s.solver_name, "pardiso")


if __name__ == "__main__":
    unittest.main()
