"""R9 サスペンション取付, stage 1: the clevis formulas by hand numbers, the force split, the inputs on the demo design
with a synthetic upstream series, the builder's load cases and plan, the design variables."""

from __future__ import annotations

import math
import unittest

from hiraku.solvers.k0_handcalc import library_bracket as lb


class FormulaTest(unittest.TestCase):
    # F = (1000, 2000, 3000) N with the pin along x and the base line along y: F_n 1000, F_t 2000, F_v 3000
    F, U, BASE = (1000.0, 2000.0, 3000.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)
    D, N, T, S, B, E = 10.0, 1, 3.2, 3.0, 100.0, 50.0

    def test_split(self):
        f = lb.split_force(self.F, self.U, self.BASE)
        self.assertAlmostEqual(f["F_n"], 1000.0)
        self.assertAlmostEqual(f["F_t"], 2000.0)
        self.assertAlmostEqual(f["F_v"], 3000.0)
        self.assertAlmostEqual(f["F_p"], math.sqrt(2000.0**2 + 3000.0**2))
        along = lb.split_force((0.0, 0.0, 0.0), self.U, self.BASE)
        self.assertEqual(along["F_p"], 0.0)
        pin = lb.split_force((-750.0, 0.0, 0.0), self.U, self.BASE)  # a force along u: no in-plane part
        self.assertAlmostEqual(pin["F_n"], -750.0)
        self.assertAlmostEqual(pin["F_p"], 0.0)
        u = lb.clevis_utilisations((500.0, 0.0, 0.0), self.U, self.BASE, self.D, self.N, self.T, self.S, self.B, self.E)
        self.assertEqual(u["bolt_shear_utilisation"], 0.0)
        self.assertEqual(u["bolt_bearing_utilisation"], 0.0)
        with self.assertRaises(ValueError):
            lb.split_force(self.F, self.U, (1.0, 1.0, 0.0))  # base not normal to the pin

    def test_hand_numbers(self):
        Fp = math.sqrt(2000.0**2 + 3000.0**2)
        F = math.sqrt(1000.0**2 + 2000.0**2 + 3000.0**2)
        tau = Fp / (2 * 1 * math.pi * 10.0**2 / 4)
        sb = Fp / (2 * 1 * 10.0 * 3.2)
        sig = 6 * 500.0 * 50.0 / (100.0 * 3.2**2) + 6 * 1000.0 * 50.0 / (3.2 * 100.0**2) + 1500.0 / (100.0 * 3.2)
        tw = ((F / 2) / 200.0 + 500.0 * 50.0 / (100.0 * 3.2) + 3 * 1000.0 * 50.0 / 100.0**2) / (0.7 * 3.0)
        f_v, f_b, f_p, f_vw = 0.6 * 800 / 1.25, 1.66 * 0.4 * 400 / 1.25, 245.0, 400 / (math.sqrt(3) * 0.8 * 1.25)
        u = lb.clevis_utilisations(self.F, self.U, self.BASE, self.D, self.N, self.T, self.S, self.B, self.E)
        self.assertAlmostEqual(u["bolt_shear_utilisation"], tau / f_v, places=12)
        self.assertAlmostEqual(u["bolt_bearing_utilisation"], sb / f_b, places=12)
        self.assertAlmostEqual(u["plate_bending_utilisation"], sig / f_p, places=12)
        self.assertAlmostEqual(u["weld_utilisation"], tw / f_vw, places=12)
        no = lb.clevis_utilisations(self.F, self.U, self.BASE, self.D, self.N, self.T, None, None, None)
        self.assertEqual(set(no), {"bolt_shear_utilisation", "bolt_bearing_utilisation"})

    def test_registry(self):
        from hiraku.solvers.k0_handcalc.library import all_formulas
        from hiraku.solvers.k0_handcalc.registry import Registry

        reg = Registry(all_formulas())
        vals = {"F_n": 1000.0, "F_t": 2000.0, "F_v": 3000.0, "d": 10.0, "n": 1.0, "t": 3.2, "s": 3.0, "b": 100.0,
                "e": 50.0, "f_y": 245.0, "f_u": 400.0, "f_ub": 800.0}
        units = {"F_n": "N", "F_t": "N", "F_v": "N", "n": "1", "f_y": "MPa", "f_u": "MPa", "f_ub": "MPa"}
        want = lb.clevis_utilisations(self.F, self.U, self.BASE, self.D, self.N, self.T, self.S, self.B, self.E)
        for qty, fid in lb.FORMULA_OF.items():
            inputs = {k: {"value": vals[k], "unit": units.get(k, "mm")} for k in lb.INPUTS_OF[qty]}
            got = reg.evaluate(fid, inputs)
            self.assertAlmostEqual(got["outputs"]["u"]["value"], want[qty], places=9, msg=fid)


def _series(design, peak_sid, peak_mount, F, t=(0.0, 0.5, 1.0)):
    """A fake K2 analysis with the mount series: 100 N up everywhere, F (world) at one instant at one mount."""
    from hiraku.solvers.k0_handcalc.bracket_inputs import MOUNTS

    ch = [{"channel": f"sprung_{a}", "unit": "deg", "t_s": list(t), "values": [0.0] * len(t)}
          for a in ("roll", "pitch", "yaw")]
    for s in design["supports"]:
        if not (s.get("suspension") or {}).get("hardpoints_mm"):
            continue
        for m in MOUNTS:
            for i, a in enumerate("xyz"):
                vals = [100.0 if a == "z" else 0.0] * len(t)
                if s["id"] == peak_sid and m == peak_mount:
                    vals[1] = F[i]
                ch.append({"channel": f"mount_{a}.{s['id']}.{m}", "unit": "N", "t_s": list(t), "values": vals})
    return {"status": "ok", "analysis_id": "ana-fake", "series": ch}


class DemoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from hiraku.designgen import ladder_4w

        cls.design = ladder_4w.build()
        cls.thick = ladder_4w.build({"mount_plate_t": 6.0, "mount_bolt_d": 12.0, "mount_weld_leg": 4.0})

    def test_every_mount_has_a_bracket(self):
        from hiraku.solvers.k0_handcalc.bracket_inputs import MOUNTS, mount_points

        for s in ("FL", "FR", "RL", "RR"):
            pts = mount_points(self.design, s)
            self.assertEqual(set(pts), set(MOUNTS))
            for m, g in pts.items():
                self.assertIsNotNone(g["bracket"], f"{s}.{m}")
                self.assertGreater(g["b"], 0.0)
                self.assertGreater(g["e"], 0.0)
                self.assertEqual(g["joint"]["bolts"], 1)

    def test_inputs_from_series(self):
        from hiraku.solvers.k0_handcalc.bracket_inputs import NoForces, bracket_inputs

        with self.assertRaises(NoForces):
            bracket_inputs(self.design, "FL", {})
        up = {"S4-brake": _series(self.design, "FL", "uca_front", (3000.0, 500.0, -2000.0)),
              "S1-tilt": _series(self.design, "FL", "uca_front", (0.0, 4000.0, 0.0))}
        got = bracket_inputs(self.design, "FL", up)
        self.assertEqual(got["used"], ["S4-brake", "S1-tilt"])
        w = got["points"]["uca_front"]["worst"]
        # shear goes with |F_p|: the tilt's 4000 N in the plates' plane beats braking's sqrt(500^2 + 2000^2)
        self.assertEqual(w["bolt_shear_utilisation"]["load_case_id"], "S1-tilt")
        self.assertAlmostEqual(w["bolt_shear_utilisation"]["inputs"]["F_t"] ** 2
                               + w["bolt_shear_utilisation"]["inputs"]["F_v"] ** 2, 4000.0**2, places=3)
        # the plate's out-of-plane bending goes with F_n: braking's 3000 N along the pin
        self.assertEqual(w["plate_bending_utilisation"]["load_case_id"], "S4-brake")
        self.assertAlmostEqual(w["plate_bending_utilisation"]["inputs"]["F_n"], 3000.0, places=6)
        self.assertEqual(got["worst"]["bolt_shear_utilisation"][0], "uca_front")
        self.assertTrue(any("half and half" in a for a in got["assumptions"]))

    def test_reader(self):
        from hiraku.catalog.builders.mounts_r9 import r9_inputs
        from hiraku.solvers.k0_handcalc.bracket_inputs import NoForces

        lc = {"load_case_id": "R9-FL", "params": {"support": "FL"}}
        with self.assertRaises(NoForces):
            r9_inputs(self.design, lc)
        up = {"S4-brake": _series(self.design, "FL", "lca_rear", (0.0, 0.0, 6000.0))}
        got = r9_inputs(self.design, lc, up)
        self.assertEqual(got["worst.bolt_shear_utilisation"], "lca_rear")
        self.assertEqual(got["weld_utilisation.uca_front.s"], {"value": 3.0, "unit": "mm"})
        self.assertEqual(got["bolt_shear_utilisation.d"], {"value": 10.0, "unit": "mm"})

    def test_variables(self):
        from hiraku.designgen import ladder_4w

        for k in ("mount_plate_t", "mount_bolt_d", "mount_weld_leg"):
            self.assertTrue(ladder_4w.VARIABLES[k].revisable)
            self.assertNotIn(k, self.design["variables"])  # the default design as before
            self.assertIn(k, self.thick["variables"])
        self.assertEqual(ladder_4w.VARIABLES["mount_plate_t"].default, 3.2)
        a = {p["id"]: p for p in self.design["parts"]}
        b = {p["id"]: p for p in self.thick["parts"]}
        for pid in ("bracket_uca_FL1", "bracket_lca_FL1", "bracket_damper_RR", "bracket_tie_RL", "bracket_lca_RL2"):
            self.assertEqual(a[pid]["joint"], {"bolt_d_mm": 10.0, "bolts": 1, "plate_t_mm": 3.2, "weld_leg_mm": 3.0})
            self.assertEqual(b[pid]["joint"], {"bolt_d_mm": 12.0, "bolts": 1, "plate_t_mm": 6.0, "weld_leg_mm": 4.0})
            size = a[pid]["box"]["size_mm"]
            rho = 7850.0
            self.assertAlmostEqual(a[pid]["mass_kg"], rho * 1e-9 * 2 * 3.2 * size[1] * size[2], places=12)
            self.assertAlmostEqual(b[pid]["mass_kg"] / a[pid]["mass_kg"], 6.0 / 3.2, places=9)
            self.assertEqual(b[pid]["declared"]["hole_diameter_mm"]["value"], 14.0)
            self.assertAlmostEqual(b[pid]["declared"]["hole_edge_mm"]["value"], 16.8)
            self.assertNotIn("bolt_diameter_mm", b[pid]["declared"])
        self.assertNotIn("joint", a["bracket_column"])
        welded = [p for p in self.design["parts"] if p["id"].startswith("fit_") and p.get("declared")]
        self.assertTrue(welded)
        self.assertAlmostEqual(welded[0]["declared"]["weld_length_mm"]["value"], round(math.pi * 20.0, 3))

    def test_contract(self):
        from hiraku import contract

        self.assertEqual(contract.errors("design", self.thick), [])

    def test_builder(self):
        from hiraku.catalog.builders import BUILDERS, FEEDS, PLANS

        from pathlib import Path

        from hiraku.catalog import load_requirements

        req = load_requirements(Path(__file__).resolve().parents[2] / "data" / "requirements" / "offroad-4w.yaml")
        spec = {"id": "R9", "domain": "strength"}
        cases = BUILDERS["R9"](spec, req, self.design)
        self.assertEqual([c["load_case_id"] for c in cases], ["R9-FL", "R9-FR", "R9-RL", "R9-RR"])
        for c in cases:
            self.assertNotIn("criterion", c)  # shown, not judged
            self.assertEqual(c["test_id"], "R9")
        plan = PLANS["R9"]
        qs = {p["quantity"] for p in plan["predictions"]}
        self.assertIn("weld_utilisation", qs)
        self.assertIn("bolt_shear_utilisation.uca_front", qs)
        self.assertEqual(len(qs), 4 * 7)
        for t in ("S1", "S3", "S4", "S5", "P1", "P3"):
            self.assertIn("R9", FEEDS[t])


if __name__ == "__main__":
    unittest.main()
