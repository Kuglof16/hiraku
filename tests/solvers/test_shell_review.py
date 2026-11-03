"""Fixes from a read-only review of the shell solver and frame_shell:

- StaticSolver.solve refuses a solution that does not satisfy the equations (relative residual).
- The 3-2-1 hold node of a support is never the slave of a rigid link (tied joints).
- check_balance on the ground-force path reads the gravity passed in.
- change_pct stays finite when the finest value is 0.
- shell_frames leaves instants without a hot spot out of the hot-spot series (not 0 MPa).
- The element-peak fallback of the convergence study is not "converged" when the peak element is not refined.
- The shell_weight check reads the shell's masses.
"""

import json
import math
import types
import unittest
from unittest import mock

import numpy as np

from hiraku.solvers.k0_handcalc.vehicle import DesignError, mass_summary
from hiraku.solvers.k1_shell import checks, convergence, frame, solve
from hiraku.solvers.k1_shell.mesh import build_mesh
from hiraku.solvers.shelf import fixtures, frame_shell


class Residual(unittest.TestCase):
    def test_relative_residual(self):
        A = np.diag([2.0, 4.0])
        self.assertEqual(solve.relative_residual(A, np.array([1.0, 1.0]), np.array([2.0, 4.0])), 0.0)
        self.assertAlmostEqual(solve.relative_residual(A, np.array([1.0, 0.0]), np.array([2.0, 4.0])),
                               4.0 / math.hypot(2.0, 4.0))
        self.assertEqual(solve.relative_residual(A, np.zeros(2), np.zeros(2)), 0.0)

    def test_wrong_solution_is_refused(self):
        res = checks.tube_cantilever(size=20.0, torsion=False)
        s = solve.StaticSolver(res["model"], solve.dofs_of(res["mesh"].end_rings[("m", "a")]), solver="superlu")
        F = np.zeros(res["model"].n_dof)
        F[-6] = 100.0
        s.solve(F)  # a well-held model passes
        self.assertLess(res["model"].timings["residual"], 1e-9)
        good = s.lu
        s.lu = types.SimpleNamespace(solve=lambda b: good.solve(b) * 1.01)  # as a near mechanism would leave it
        with self.assertRaises(ValueError) as cm:
            s.solve(F)
        self.assertIn("relative residual", str(cm.exception))


class HoldNode(unittest.TestCase):
    def test_slaves_are_not_held(self):
        lad = fixtures.ladder_design()
        ms = build_mesh(lad, 20.0)
        node = lad["supports"][0]["frame_node"]
        x0 = frame.node_table(lad)[node]
        first = frame.hold_node(ms, node, x0)
        self.assertNotIn(first, {s for s, _ in ms.links})
        ms.links = list(ms.links) + [(first, int(ms.joints[node][0]) if ms.joints[node][0] != first
                                      else int(ms.joints[node][1]))]
        second = frame.hold_node(ms, node, x0)
        self.assertNotEqual(second, first)
        self.assertIn(second, set(int(n) for n in ms.joints[node]))
        ms.links = [(int(n), -1) for n in ms.joints[node]]
        with self.assertRaises(DesignError):
            frame.hold_node(ms, node, x0)


class BalanceGravity(unittest.TestCase):
    def test_tilted_gravity_is_read(self):
        lad = fixtures.ladder_design()
        summ = mass_summary(lad)
        a = math.radians(20.0)
        gvec = 9.80665 * np.array([0.0, math.sin(a), -math.cos(a)])
        cg = np.asarray(summ["cg_mm"], float)
        ground = {"all": {"vectors": [("reaction", cg, -summ["mass_kg"] * gvec)]}}
        self.assertLess(frame.check_balance(lad, summ, ground, 1.0, None, gvec), 1e-9)
        with self.assertRaises(DesignError):  # straight down: the sideways part of the ground force is left over
            frame.check_balance(lad, summ, ground, 1.0, None)


class ChangePct(unittest.TestCase):
    def test_finest_zero(self):
        v, change, _ = convergence.verdict_of([3.0, 2.0, 0.0])
        self.assertEqual((v, change), ("not_converged", 100.0))
        rec = convergence.convergence_record("x", "MPa", [{"element_size_mm": s, "value": x}
                                                         for s, x in ((40, 3.0), (20, 2.0), (10, 0.0))])
        json.dumps(rec, allow_nan=False)  # valid JSON: no Infinity
        self.assertIn("finest value is 0", rec["notes"])
        self.assertEqual(convergence.verdict_of([0.0, 0.0])[1], 0.0)


def _instant(hot):
    return {"von_mises": {"max": np.array([1.0, 2.0])}, "member_max": {"railLa": (5.0 + (hot or 0.0), 0)},
            "worst_member": "railLa", "hot_spot_max": None if hot is None else {"value": -hot, "joint": "n1",
                                                                               "member": "railLa"}}


class FramesWithoutHotSpot(unittest.TestCase):
    def _frames(self, hots):
        lad = fixtures.ladder_design()
        inst = [{"t_s": 0.1 * i, "mount_loads": {}, "gravity_mps2": None, "inertia_N": None} for i in range(len(hots))]
        out = iter([_instant(h) for h in hots])
        with mock.patch.object(frame_shell, "analyze_shell", side_effect=lambda *a, **k: next(out)):
            return frame_shell.shell_frames(lad, {"frame": None}, ("LC", "ana-1", inst), None)

    def test_some_instants_without(self):
        rec = self._frames([10.0, None, 30.0])
        hot = next(s for s in rec["series"] if s["channel"] == "max_hot_spot_stress")
        self.assertEqual((hot["t_s"], hot["values"]), ([0.0, 0.2], [10.0, 30.0]))
        self.assertIn("2 of the 3 instants", rec["effects"][0]["name"])
        r = next(x for x in rec["results"] if x["quantity"] == "max_hot_spot_stress_over_time")
        self.assertEqual(r["value"], 30.0)

    def test_none_read(self):
        rec = self._frames([None, None])
        self.assertEqual([s["channel"] for s in rec["series"]], ["max_stress"])
        self.assertEqual([r["quantity"] for r in rec["results"]], ["max_stress_over_time"])
        self.assertIn("no hot spot read", rec["effects"][0]["name"])


class _Mesh:
    def __init__(self, size, n, edge):
        self.element_size_mm, self.n_elements, self._edge = size, n, edge

    def edge_lengths(self):
        return np.full((self.n_elements, 4), self._edge)


def _level_res(size, n, peak, edge):
    return {"mesh": _Mesh(size, n, edge), "member_max": {"m": (peak, 0)}, "worst_member": "m", "worst_element": 0,
            "hot_spots": [], "hot_spot_max": None, "timings": {"total_s": 1.0},
            "frame": types.SimpleNamespace(model=types.SimpleNamespace(timings={}))}


class PeakFallback(unittest.TestCase):
    def _study(self, edges):
        peaks = (100.0, 101.0, 101.5)  # a change of 0.5 %: "converged" by the change alone
        sizes = (40.0, 20.0, 10.0)
        res = _level_res(20.0, 400, peaks[1], edges[1])

        class Lv:
            where = "here"

            def results(self):
                return [{"size": s, "res": _level_res(s, n, p, e), "zz": {"global_pct": 5.0}, "seconds": 1.0,
                         "n_dof_free": 6 * n, "elements": n, "factor_s": 0.1}
                        for s, n, p, e in ((sizes[0], 100, peaks[0], edges[0]), (sizes[2], 1600, peaks[2], edges[2]))]

        place = {"member": "m", "wall": "upper", "node": "n1", "node_distance_mm": 5.0}
        with mock.patch.object(frame_shell, "element_place", return_value=place):
            rec, _ = frame_shell.mesh_convergence(fixtures.ladder_design(), res, {}, None, {"global_pct": 5.0}, Lv())
        return rec

    def test_peak_element_not_refined(self):
        rec = self._study((3.0, 3.0, 3.0))
        self.assertEqual((rec["quantity"], rec["verdict"]), ("max_stress", "not_converged"))
        self.assertIn("not judged converged", rec["notes"])

    def test_peak_element_refined(self):
        rec = self._study((40.0, 20.0, 10.0))
        self.assertEqual(rec["verdict"], "converged")


class ShellWeightCheck(unittest.TestCase):
    def test_check_reads_the_shell_masses(self):
        recs = checks.ladder_frame_checks(40.0)
        rec = next(r for r in recs if r["quantity"] == "shell_weight")
        self.assertTrue(rec["pass"], rec)
        real = frame.analyze_shell

        def heavier(*a, **k):
            r = real(*a, **k)
            r["weight_N"] *= 1.05  # a shell with 5 % too much mass
            return r

        with mock.patch.object(frame, "analyze_shell", side_effect=heavier):
            rec = next(r for r in checks.ladder_frame_checks(40.0) if r["quantity"] == "shell_weight")
        self.assertFalse(rec["pass"], rec)


if __name__ == "__main__":
    unittest.main()
