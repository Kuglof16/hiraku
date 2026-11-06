"""R9 stage 2: the suspension mount brackets as plates of the frame shell (k1_shell.brackets), their pin load, the
base weld and plate root values against the stage 1 hand formulas, the hot_spots rows, and frame_shell's R9 role."""

from __future__ import annotations

import unittest

import numpy as np

from tests.solvers import bracket_case as bc


class BracketMeshTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from hiraku.solvers.k1_shell.mesh import build_mesh

        cls.design = bc.design()
        cls.ms = build_mesh(cls.design, 10.0)

    def test_cheeks_in_the_mesh_and_tied(self):
        ms = self.ms
        self.assertEqual([b.id for b in ms.brackets], ["bracket_uca_S1"])
        self.assertEqual(ms.bracket_skipped, {})
        br = ms.brackets[0]
        self.assertEqual(len(br.elements), 2)
        members = np.asarray(ms.element_member)
        tube = np.isin(members, ["m1", "m2"])
        tube_nodes = set(ms.elements[tube].ravel().tolist())
        for k in range(2):
            self.assertTrue(np.all(members[br.elements[k]] == "bracket_uca_S1"))
            base = br.base_nodes[k]
            self.assertGreaterEqual(len(base), 3)
            self.assertTrue(set(base.tolist()) <= tube_nodes, "the base nodes are the wall's nodes (welded)")
            # the base lies on the top wall's mid-surface (z = 50 - 3), across the tube along y, the whole b
            self.assertTrue(np.allclose(ms.nodes[base][:, 2], bc.TOP - 3.0))
            self.assertAlmostEqual(float(np.ptp(ms.nodes[base][:, 1])), bc.B_MM, places=6)
            # the hole ring: 4 m nodes at d0 / 2 = 6 mm from the hole's centre
            r = np.linalg.norm(ms.nodes[br.hole_nodes[k]] - br.hole_centres[k], axis=1)
            self.assertTrue(np.allclose(r, 6.0))
            self.assertEqual(len(br.hole_nodes[k]) % 4, 0)
        x = sorted(float(ms.nodes[br.base_nodes[k][0], 0]) for k in range(2))
        self.assertAlmostEqual(x[0], -25.0 + 1.6)
        self.assertAlmostEqual(x[1], 25.0 - 1.6)
        self.assertIn(ms.bracket_material["bracket_uca_S1"], ("m1", "m2"))
        self.assertTrue(np.all(ms.aspect_ratios()[np.concatenate(br.elements)] < 10.0))

    def test_pin_load_sums_to_the_force(self):
        from hiraku.solvers.k1_shell.brackets import pin_load

        br = self.ms.brackets[0]
        n_dof = 6 * self.ms.n_nodes
        for F in ((300.0, -700.0, 1100.0), (0.0, 1000.0, 0.0), (-2500.0, 0.0, 0.0), (0.0, -400.0, -900.0)):
            f = pin_load(self.ms.nodes, br, np.array(F), n_dof)
            for c in range(3):
                self.assertAlmostEqual(float(f[c::6].sum()), F[c], delta=1e-6)
            self.assertEqual(float(np.abs(f[3::6]).sum() + np.abs(f[4::6]).sum() + np.abs(f[5::6]).sum()), 0.0)
            on = np.nonzero(np.linalg.norm(f.reshape(-1, 6)[:, :3], axis=1))[0]
            self.assertTrue(set(on.tolist()) <= set(np.concatenate(br.hole_nodes).tolist()))

    def test_v1_meshes_the_brackets_on_the_rails(self):
        import json
        from pathlib import Path

        from hiraku.solvers.k1_shell.mesh import build_mesh

        d = json.loads((Path(__file__).resolve().parents[2] / "data" / "designs" / "offroad-4w-v1.json")
                       .read_text(encoding="utf-8"))
        ms = build_mesh(d, 10.0)
        self.assertEqual(len(ms.brackets), 16)
        self.assertEqual(sorted(ms.bracket_skipped), sorted(
            [f"bracket_damper_{s}" for s in ("FL", "FR", "RL", "RR")]
            + [f"bracket_lca_{s}{i}" for s in ("FL", "FR") for i in (1, 2)]))
        for b in ms.brackets:
            self.assertAlmostEqual(b.span_mm, 18.85, places=6)  # the rail's top or bottom wall, inner half
        # the convergence study's coarse level meshes the same brackets; there the upper arms' base line end (the
        # rail's centre line) would cut the tower posts' rings, so it is moved onto the nearest of the posts' own
        # lines inside the span (a shorter welded length at that level)
        coarse = build_mesh(d, 20.0)
        self.assertEqual(sorted(b.id for b in coarse.brackets), sorted(b.id for b in ms.brackets))
        uca = next(b for b in coarse.brackets if b.id == "bracket_uca_FL1")
        self.assertLess(uca.span_mm, 18.85)
        self.assertGreaterEqual(uca.span_mm, 2.0 * uca.t)


class HandCaseTest(unittest.TestCase):
    """One bracket on a thick straight tube, pure shear along the plate (F along the base line): the nominal base
    values from the shell's base resultant against stage 1's mount_bracket.weld and .plate_bending."""

    @classmethod
    def setUpClass(cls):
        cls.F_t = 1000.0
        cls.levels = {s: bc.solve(bc.design(), (0.0, cls.F_t, 0.0), s)[3]["bracket_uca_S1"] for s in (10.0, 5.0)}

    def test_weld_and_plate_against_stage_1(self):
        from hiraku.solvers.k0_handcalc import library_bracket as lb
        from hiraku.solvers.k1_shell.convergence import LIMIT_PCT

        st = lb.stresses(0.0, self.F_t, 0.0, 10.0, 1, 3.2, 3.0, bc.B_MM, bc.E_MM)
        w_hand = st["tau_w"] * lb.THROAT * 3.0
        # the shell's base is at the wall's mid-surface, t_wall / 2 = 3 mm under the base face: the moment arm is
        # e + 3 where stage 1 takes e (the whole difference here; the tube is thick, the cheeks share the force evenly)
        arm = (bc.E_MM + 3.0) / bc.E_MM
        w_expect = (self.F_t / 2.0) / (2.0 * bc.B_MM) + 3.0 * (self.F_t / 2.0) * bc.E_MM * arm / bc.B_MM ** 2
        for size, v in self.levels.items():
            self.assertLess(abs(v["weld_line_N_per_mm"] / w_hand - 1.0) * 100.0, LIMIT_PCT, size)
            self.assertAlmostEqual(v["weld_line_N_per_mm"], w_expect, delta=0.005 * w_expect)
            # plate root: 6 (F_t / 2) e / (t b^2), the same arm; tolerance 6 % (the arm's 5 % and 1 % for the rest)
            self.assertLess(abs(v["plate_sigma"] / st["sigma"] - 1.0), 0.06, size)
            self.assertAlmostEqual(v["plate_sigma"], st["sigma"] * arm, delta=0.005 * st["sigma"])
            self.assertAlmostEqual(v["weld_utilisation"], v["tau_w"] / lb.resistances(245.0, 400.0)["f_vw"])
        # the nominal values do not move with the mesh; the local peaks at the base's ends do (singular)
        a, b = self.levels[10.0], self.levels[5.0]
        self.assertLess(abs(a["weld_line_N_per_mm"] / b["weld_line_N_per_mm"] - 1.0) * 100.0, LIMIT_PCT)
        self.assertGreater(b["weld_line_peak_N_per_mm"], 1.2 * a["weld_line_peak_N_per_mm"])
        self.assertGreater(b["plate_vm_peak"], 1.2 * a["plate_vm_peak"])

    def test_hot_spot_rows(self):
        from hiraku.catalog.builders.mounts_r9 import R9_CRITERIA
        from hiraku.solvers.shelf import hot_spots
        from hiraku.solvers.shelf.frame_shell import r9_hot_spots

        v = dict(self.levels[10.0])
        got = {"worst": {(q, "uca_front"): {"value": v[q], "bracket": "bracket_uca_S1", "load_case_id": "S4",
                                            "analysis_id": "a", "t_s": 0.5, "vals": v}
                         for q in ("plate_bending_utilisation", "weld_utilisation")}}
        plate = r9_hot_spots({"criterion": R9_CRITERIA["plate_bending_utilisation"]}, got)
        weld = r9_hot_spots({"criterion": R9_CRITERIA["weld_utilisation"]}, got)
        self.assertEqual([(r["where"], r["quantity"]) for r in plate if r["quantity"] != "max_stress"],
                         [("bracket_uca_S1", "plate_bending_utilisation")])
        self.assertEqual([(r["where"], r["quantity"]) for r in weld if r["quantity"] != "max_stress"],
                         [("bracket_uca_S1@" + v["member"], "weld_utilisation")])
        for rows, q, at in ((plate, "plate_bending_utilisation", v["plate_at_mm"]),
                            (weld, "weld_utilisation", v["weld_at_mm"])):
            r = next(r for r in rows if r["quantity"] == q)
            self.assertEqual(r["value"]["unit"], "1")
            self.assertAlmostEqual(r["utilisation"], v[q])
            self.assertEqual(r["at_mm"], [round(float(x), 3) for x in at])
            sharp = next(r for r in rows if r["quantity"] == "max_stress")
            self.assertEqual(sharp["note"], hot_spots.SHARP_NOTE)
            self.assertEqual(sharp["at_mm"], [round(float(x), 3) for x in v["hole_at_mm"]])
        # a bolt criterion or none: no rows from the shell
        self.assertIsNone(r9_hot_spots({"criterion": {"quantity": "bolt_shear_utilisation", "op": "<=",
                                                      "limit": {"value": 1.0, "unit": "1"}}}, got))
        self.assertIsNone(r9_hot_spots({}, got))


class R9RoleTest(unittest.TestCase):
    def test_frame_shell_judges_plate_and_weld_bolts_stay_hand(self):
        from hiraku.catalog.builders.mounts_r9 import R9_CRITERIA, R9_PLAN
        from hiraku.solvers.k0_handcalc import library_bracket as lb
        from hiraku.solvers.shelf.frame_shell import R9_QUANTITIES, FrameShell

        self.assertEqual(set(R9_CRITERIA), {"plate_bending_utilisation", "weld_utilisation"})
        self.assertEqual(set(R9_QUANTITIES), set(R9_CRITERIA))
        self.assertEqual(R9_PLAN["tool"], "frame_shell")
        self.assertIn("R9", FrameShell.tests)
        self.assertIn(lb.FORMULA_OF["plate_bending_utilisation"], FrameShell.formulas)
        self.assertIn(lb.FORMULA_OF["weld_utilisation"], FrameShell.formulas)
        for q in ("bolt_shear_utilisation", "bolt_bearing_utilisation"):
            self.assertNotIn(q, R9_CRITERIA)
            self.assertNotIn(lb.FORMULA_OF[q], FrameShell.formulas)
            self.assertTrue(any(p["quantity"] == q for p in R9_PLAN["predictions"]), q)  # the hand calc's still

    def test_r9_needs_upstream_forces(self):
        from hiraku.solvers.shelf.base import LoadCaseError
        from hiraku.solvers.shelf.frame_shell import r9_outputs

        with self.assertRaises(LoadCaseError):
            r9_outputs(bc.design(), {"load_case_id": "R9-S", "test_id": "R9", "params": {}}, None, 10.0)


if __name__ == "__main__":
    unittest.main()
