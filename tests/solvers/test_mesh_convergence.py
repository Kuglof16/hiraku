"""Mesh convergence (Richardson, verdict) and the Zienkiewicz-Zhu error estimate (k1_shell.convergence),
their known answers (k1_shell.checks.convergence_checks) and frame_shell's mesh_convergence record."""

import json
import math
import os
import time
import unittest
from pathlib import Path

import numpy as np

from hiraku import contract, solvers
from hiraku.solvers.k1_shell import build_mesh, checks
from hiraku.solvers.k1_shell.convergence import (LIMIT_PCT, convergence_assumption, convergence_record, richardson,
                                                 verdict_of, zz_assumption, zz_error)
from hiraku.solvers.k1_shell.stress import element_stresses
from hiraku.solvers.shelf import fixtures
from hiraku.solvers.shelf.frame_shell import LABEL_ZZ, convergence_sizes, hot_spot_key, hot_spot_on

ROOT = Path(__file__).resolve().parents[2]
SLOW = os.environ.get("HIRAKU_SLOW_TESTS") == "1"


def tool(name):
    return {t.name: t for t in solvers.builtin_tools()}[name]


class RichardsonTest(unittest.TestCase):
    def test_exact_power_law(self):
        # v(h) = 10 - 3 h^2 at h = 4, 2, 1: order 2, extrapolated 10
        vals = [10 - 3 * h * h for h in (4.0, 2.0, 1.0)]
        r = richardson(vals)
        self.assertAlmostEqual(r["observed_order"], 2.0, places=12)
        self.assertAlmostEqual(r["extrapolated"], 10.0, places=12)
        self.assertAlmostEqual(r["error_pct"], 30.0, places=10)
        self.assertEqual(r["why"], "")

    def test_flat_swinging_and_diverging(self):
        same = richardson([5.0, 5.0, 5.0])
        self.assertEqual((same["observed_order"], same["extrapolated"], same["error_pct"]), (None, 5.0, 0.0))
        fine_same = richardson([4.0, 5.0, 5.0])
        self.assertEqual((fine_same["observed_order"], fine_same["extrapolated"]), (None, 5.0))
        swing = richardson([4.0, 5.0, 4.8])
        self.assertIsNone(swing["observed_order"])
        self.assertIn("swings", swing["why"])
        grow = richardson([4.0, 5.0, 6.5])
        self.assertIsNone(grow["extrapolated"])
        self.assertIn("does not shrink", grow["why"])
        coarse_same = richardson([5.0, 5.0, 6.0])
        self.assertIsNone(coarse_same["observed_order"])

    def test_verdicts(self):
        self.assertEqual(verdict_of([100.0, 103.0, 104.0])[0], "converged")  # change 0.96 %, q 1/3
        self.assertEqual(verdict_of([100.0, 120.0, 135.0])[0], "singular")  # growing, q 0.75 >= 2^-0.5
        # change first: growing with p 0.415 (q 0.75) but only 1.4 % between the two finest -> converged
        self.assertEqual(verdict_of([100.0, 102.0, 103.5])[0], "converged")
        self.assertEqual(verdict_of([100.0, 130.0, 145.0])[0], "not_converged")  # growing, q 0.5 (p 1), change 10 %
        self.assertEqual(verdict_of([150.0, 120.0, 100.0])[0], "not_converged")  # falling: not singular
        self.assertEqual(verdict_of([100.0, 102.0])[0], "converged")  # two levels: change only
        v, change, rich = verdict_of([100.0, 110.0])
        self.assertEqual((v, rich), ("not_converged", None))
        self.assertAlmostEqual(change, 10 / 110 * 100)
        self.assertEqual(verdict_of([-100.0, -120.0, -135.0])[0], "singular")  # by size

    def test_record_fits_the_contract(self):
        levels = [{"element_size_mm": 20.0, "value": 100.0, "elements": 10},
                  {"element_size_mm": 10.0, "value": 120.0, "mesh_id": "mesh-0001"},
                  {"element_size_mm": 5.0, "value": 135.0}]
        rec = convergence_record("max_stress", "MPa", levels, notes="read at the corner")
        self.assertEqual(rec["verdict"], "singular")
        self.assertIn("cannot be judged", rec["notes"])
        self.assertIn("read at the corner", rec["notes"])
        self.assertNotIn("extrapolated", rec)  # a singular value has no limit to extrapolate to
        self.assertAlmostEqual(rec["observed_order"], math.log(1 / 0.75) / math.log(2))
        a = {"analysis_id": "ana-0001", "tool": "frame_shell", "base": "K1", "design_version": 1,
             "load_case_id": "R1-x", "prediction_ids": ["pred-0001"], "status": "ok", "results": []}
        contract.validate("analysis", dict(a, mesh_convergence=rec))
        ok = convergence_record("q", "MPa", [{"element_size_mm": 4.0, "value": 10 - 3 * 16},
                                             {"element_size_mm": 2.0, "value": 10 - 3 * 4},
                                             {"element_size_mm": 1.0, "value": 7.0}])
        contract.validate("analysis", dict(a, mesh_convergence=ok))
        self.assertAlmostEqual(ok["extrapolated"], 10.0)
        self.assertEqual(ok["limit_pct"], LIMIT_PCT)

    def test_assumptions_say_no_source(self):
        self.assertIn("仮定、出典なし", convergence_assumption())
        self.assertIn("2 倍", convergence_assumption())
        self.assertIn("5 %", convergence_assumption())
        self.assertIn("20 度", zz_assumption())
        self.assertIn("下限", zz_assumption())


class ZienkiewiczZhuTest(unittest.TestCase):
    def test_constant_stress_has_no_error(self):
        # the membrane patch test: constant stress, recovered = element stress
        _, detail = checks.patch_tests()
        model = checks.flat_model(checks.PATCH_XY, checks.PATCH_QUADS, 0.001, 1.0e6, 0.25)
        bnd = np.arange(4)
        vals = np.concatenate([[1e-3 * (x + y / 2), 1e-3 * (y + x / 2), 0, 0, 0, 0] for x, y in checks.PATCH_XY[bnd]])
        U = checks._prescribed(model, checks.dofs_of(bnd), vals)
        z = zz_error(model, element_stresses(model, U))
        self.assertLess(z["global_pct"], 1e-6)
        self.assertLess(float(z["per_element"].max()), 1e-6)

    def test_falls_on_the_plate_and_stays_high_at_the_corner(self):
        p = [checks.plate_centre_stress(n) for n in (4, 8, 16)]
        g = [x["zz_global"] for x in p]
        self.assertTrue(g[0] > g[1] > g[2])
        self.assertTrue(p[0]["zz_max"] > p[1]["zz_max"] > p[2]["zz_max"])
        lp = [checks.l_plate_corner(n) for n in (4, 8, 16)]
        self.assertTrue(lp[0]["zz_global"] > lp[1]["zz_global"] > lp[2]["zz_global"])
        self.assertGreater(lp[2]["zz_max"], lp[0]["zz_max"])  # the element at the corner does not get better

    @staticmethod
    def pressurised_tube(size):
        """A 40 x 80 x 2.3 tube, 400 mm, open ends, 1 MPa inside: the walls bend across at the tube's corners."""
        ms, model = checks._tube(size, 400.0, 40.0, 80.0, 2.3, checks.element.DRILL_FACTOR)
        xyz = model.element_xyz()
        nA = 0.5 * np.cross(xyz[:, 2] - xyz[:, 0], xyz[:, 3] - xyz[:, 1])  # outward normal x area
        F = np.zeros(model.n_dof)
        for e, el in enumerate(model.elements):
            for n in el:
                F[6 * n:6 * n + 3] += nA[e] / 4.0
        mid = np.nonzero(np.abs(model.nodes[:, 0] - 200.0) < 1e-6)[0]
        z, y = model.nodes[mid, 2], model.nodes[mid, 1]
        a, b, c = mid[np.argmax(z)], mid[np.argmin(z)], mid[np.argmax(y)]
        fixed = np.concatenate([checks.dofs_of(a, "xyz"), checks.dofs_of(b, "xy"), checks.dofs_of(c, "x")])
        U = checks.StaticSolver(model, fixed).solve(F)
        return model, element_stresses(model, U)

    def test_walls_of_a_tube_are_not_mixed(self):
        """Pressurised tube: the walls bend across at the corners; averaging over both walls at a corner node
        (normals 90 degrees apart) would halve that bending in the recovered stress. With the walls kept apart
        the estimate halves as the mesh halves; mixed it does not, and its largest element stays high."""
        w, m = [], []
        for size in (10.0, 5.0):
            model, st = self.pressurised_tube(size)
            w.append(zz_error(model, st))
            m.append(zz_error(model, st, same_wall_cos=-2.0))  # every element at a node, whatever its direction
        self.assertTrue(all(a["global_pct"] < b["global_pct"] for a, b in zip(w, m)))
        self.assertLess(w[1]["global_pct"] / w[0]["global_pct"], 0.6)
        self.assertGreater(m[1]["global_pct"] / m[0]["global_pct"], 0.6)
        self.assertGreater(float(m[1]["per_element"].max()), 2.0 * float(w[1]["per_element"].max()))

    def test_floor_limits_the_unloaded_elements(self):
        res = checks.tube_cantilever(size=10.0, torsion=False)
        model = res["model"]
        st = element_stresses(model, res["U"][:, 0])
        no_floor = zz_error(model, st, floor_factor=0.0)
        floor = zz_error(model, st)
        self.assertTrue(np.all(floor["per_element"] <= no_floor["per_element"] + 1e-9))
        self.assertAlmostEqual(floor["global_pct"], no_floor["global_pct"], places=12)  # the global value has no floor


class KnownAnswersTest(unittest.TestCase):
    def test_convergence_checks(self):
        t0 = time.perf_counter()
        rows = checks.convergence_checks()
        dt = time.perf_counter() - t0
        print(f"  convergence known answers: {len(rows)} checks in {dt:.2f} s")
        self.assertEqual([(r["case"], r["quantity"]) for r in rows if not r["pass"]], [])
        for r in rows:
            contract.validate("tool_check", r)
        q = {r["quantity"]: r for r in rows}
        self.assertIn("verdict singular (1 = yes)", q)
        self.assertIn("Richardson extrapolated centre stress (bottom surface)", q)
        self.assertLess(dt, 1.5)

    def test_in_frame_shell_known_answers(self):
        names = {r["quantity"] for r in checks.shell_known_answers()}
        self.assertIn("verdict singular (1 = yes)", names)

    def test_navier_moment_and_williams(self):
        # beta = M / (q a^2) at the centre for nu = 0.3: 0.0479 in Timoshenko's table (not checked here); the series
        self.assertAlmostEqual(checks.navier_centre_moment(100.0, 0.01, 0.3) / (0.01 * 100.0**2), 0.0479, places=4)
        lam = checks.williams_lambda()
        self.assertAlmostEqual(lam * math.sin(1.5 * math.pi) + math.sin(1.5 * math.pi * lam), 0.0, places=12)
        self.assertTrue(0.5 < lam < 0.6)


class FrameShellConvergenceTest(unittest.TestCase):
    def test_level_sizes(self):
        self.assertEqual(convergence_sizes(10.0), [20.0, 10.0, 5.0])
        self.assertEqual(convergence_sizes(20.0), [40.0, 20.0, 10.0])
        self.assertEqual(convergence_sizes(8.0), [32.0, 16.0, 8.0])
        self.assertEqual(convergence_sizes(5.0), [20.0, 10.0, 5.0])

    def test_same_weld_line(self):
        hs = [{"joint": "A", "side": "host", "place": "p", "member": "m", "towards": "+x", "surface": "top", "value": v}
              for v in (3.0, -7.0)]
        hs.append(dict(hs[0], joint="B", value=99.0))
        key = hot_spot_key(hs[0])
        self.assertEqual(hot_spot_on({"hot_spots": hs}, key)["value"], -7.0)
        self.assertIsNone(hot_spot_on({"hot_spots": hs}, ("C",) + key[1:]))

    def test_ladder_record(self):
        """The ladder at 20 mm with the default convergence (40 / 20 / 10 mm) and its mesh record."""
        lad = fixtures.ladder_design()
        mesh = build_mesh(lad, 20.0).record("mesh-0003", int(lad.get("design_version", 1)))
        lc = {"load_case_id": "R1-x", "test_id": "R1", "domain": "strength", "title": "R1", "params": {},
              "tolerance_rel": 0.15}
        t0 = time.perf_counter()
        rec = tool("frame_shell").run(lad, lc, [{"prediction_id": "pred-0001"}],
                                      {"element_size": {"value": 20.0, "unit": "mm"}, "mesh": mesh})
        print(f"  frame_shell ladder with mesh convergence: {time.perf_counter() - t0:.1f} s")
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        contract.validate("analysis", dict(rec, analysis_id="ana-0001"))
        conv = rec["mesh_convergence"]
        self.assertEqual([lv.get("mesh_id") for lv in conv["levels"]], [None, "mesh-0003", None])
        self.assertTrue(all(lv["elements"] > 0 for lv in conv["levels"]))
        self.assertIn("read at joint", conv["notes"])
        est = [f for f in rec["fields"] if f["kind"] == "error_estimate"]
        self.assertEqual(len(est), 1)
        self.assertEqual((est[0]["unit"], est[0]["mesh_id"], est[0]["label"]), ("%", "mesh-0003", LABEL_ZZ))
        self.assertEqual(len(est[0]["per_element"]), len(mesh["elements"]))
        kinds = [f["kind"] for f in rec["fields"] if "mesh_id" in f]
        self.assertEqual(kinds, ["stress", "stress", "stress", "error_estimate", "displacement"])
        names = " | ".join(e["name"] for e in rec["effects"])
        for s in ("error estimate (Zienkiewicz-Zhu) largest", "error estimate (Zienkiewicz-Zhu) of the whole model",
                  "mesh convergence of max_hot_spot_stress", "mesh convergence of max_stress",
                  "mesh convergence solves: 40 mm"):
            self.assertIn(s, names)
        self.assertTrue(any("仮定、出典なし" in a for a in rec["assumptions"]))


@unittest.skipUnless(SLOW, "set HIRAKU_SLOW_TESTS=1 for the demo's mesh convergence (about 40 s)")
class DemoConvergenceTest(unittest.TestCase):
    """offroad-4w-v1 (two_cargo), R1 on a 100 mm step, the version's 10 mm mesh: 20 / 10 (/ 5) mm."""

    def test_demo(self):
        design = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
        mesh = dict(solvers.frame_mesh(design), mesh_id="mesh-0001")
        lc = {"load_case_id": "R1-step100", "test_id": "R1", "domain": "strength", "title": "R1",
              "params": {"step_height": {"value": 100.0, "unit": "mm"}}, "tolerance_rel": 0.15}
        rec = tool("frame_shell").run(design, lc, [{"prediction_id": "pred-0001"}], {"mesh": mesh})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        contract.validate("analysis", dict(rec, analysis_id="ana-0001"))
        conv = rec["mesh_convergence"]
        from hiraku.solvers.k1_shell import build_mesh
        from hiraku.solvers.shelf.frame_shell import MAX_LEVEL_ELEMENTS

        fine = build_mesh(design, 5.0).n_elements  # the 5 mm level is left out above the element budget
        want = [20.0, 10.0] + ([5.0] if fine <= MAX_LEVEL_ELEMENTS else [])
        self.assertEqual([lv["element_size_mm"] for lv in conv["levels"]], want)
        if fine > MAX_LEVEL_ELEMENTS:
            self.assertIn(f"the 5 mm level ({fine} elements) is left out", conv["notes"])
        self.assertEqual(conv["levels"][1].get("mesh_id"), "mesh-0001")
        hs = next(r["value"] for r in rec["results"] if r["quantity"] == "max_hot_spot_stress")
        self.assertEqual(conv["levels"][1]["value"], hs)
        self.assertLess(conv["change_pct"], 10.0)  # the hot spot holds as the elements halve
        print("  demo mesh convergence: " + json.dumps(conv, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
