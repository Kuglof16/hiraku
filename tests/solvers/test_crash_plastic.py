"""The crash tool's plastic crush zone: tube crushing forces, the crushing members, energy, no spring back,
the hand calculation and bottoming out."""

import json
import unittest
from pathlib import Path

import numpy as np

from hiraku import solvers
from hiraku.contract.loading import on_board
from hiraku.solvers.k0_handcalc.library_crash import crush_zone, crushing_members, tube_mean_crush_force
from hiraku.solvers.k0_handcalc.vehicle import mass_summary
from hiraku.solvers.shelf.crash import occupant_space, plastic_impact

ROOT = Path(__file__).resolve().parents[2]
DESIGN = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
PRED = [{"prediction_id": "pred-0001"}]
TOOL = {t.name: t for t in solvers.builtin_tools()}["crash"]
G = 9.80665
TOL = 0.15  # the load case's tolerance_rel below


def q(v, u):
    return {"value": v, "unit": u}


def lc(v):
    return {"load_case_id": "C1", "test_id": "C1", "domain": "crash", "title": "C1",
            "params": {"speed": q(v, "km/h")}, "tolerance_rel": TOL}


def run(v):
    rec = TOOL.run(DESIGN, lc(v), PRED, {})
    assert rec["status"] == "ok", rec.get("error")
    return rec, {r["quantity"]: r["value"] for r in rec["results"]}


def work(t_series, f_series):
    """Work of the wall force over the front travel, J, from the series (kN, mm)."""
    return float(np.trapezoid(np.asarray(f_series) * 1000.0, np.asarray(t_series) / 1000.0))


class TubeForceTest(unittest.TestCase):
    STKR = {"yield_MPa": 245.0, "tensile_MPa": 400.0}

    def test_rect_tube_hand_number(self):
        # 40x80x2.3, sigma_0 = (245 + 400)/2 = 322.5 MPa, C = 60 mm: 2.3^(5/3) = 4.0067, 60^(1/3) = 3.9149,
        # 9.56 x 322.5 x 4.0067 x 3.9149 = 48 371 N
        f = tube_mean_crush_force({"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 80.0, "t": 2.3}}, self.STKR)
        self.assertAlmostEqual(f, 48371.0, delta=5.0)

    def test_round_tube_hand_number(self):
        # 42.7x2.3, yield only (245 MPa), D = 40.4 mm: 6 x 245 x 2.3^1.5 x sqrt(40.4) = 1470 x 3.4881 x 6.3561 = 32 591 N
        f = tube_mean_crush_force({"shape": "round_tube", "dims_mm": {"d": 42.7, "t": 2.3}}, {"yield_MPa": 245.0})
        self.assertAlmostEqual(f, 32591.0, delta=5.0)

    def test_formulas_match_the_function(self):
        reg = solvers.formula_registry()
        o = reg.evaluate("crash.rect_tube_crush", {"sigma_0": q(322.5, "MPa"), "b": q(40, "mm"), "h": q(80, "mm"),
                                                   "t": q(2.3, "mm"), "n": q(2, "1")})["outputs"]
        sec = {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 80.0, "t": 2.3}}
        self.assertAlmostEqual(o["F_m"]["value"], 2 * tube_mean_crush_force(sec, self.STKR), delta=1e-6)
        o = reg.evaluate("crash.round_tube_crush", {"sigma_0": q(245, "MPa"), "d": q(42.7, "mm"), "t": q(2.3, "mm"),
                                                    "n": q(1, "1")})["outputs"]
        sec = {"shape": "round_tube", "dims_mm": {"d": 42.7, "t": 2.3}}
        self.assertAlmostEqual(o["F_m"]["value"], tube_mean_crush_force(sec, {"yield_MPa": 245.0}), delta=1e-6)


class CrushZoneTest(unittest.TestCase):
    def test_demo_members(self):
        z = crush_zone(DESIGN)
        self.assertEqual(z["end_member"], "cross_S1")
        self.assertAlmostEqual(z["length_mm"], 1170.0)
        self.assertAlmostEqual(z["usable_mm"], 0.7 * 1170.0)
        self.assertEqual(len(z["rails"]), 2)
        want = {f"rail_{s}_{p}" for s, p in (("L", "FA"), ("L", "LAp1_LAu1"), ("L", "LAu1_LA"), ("L", "LA_LAu2"),
                                             ("L", "LAu2_LAp2"), ("L", "AS1"), ("R", "FA"), ("R", "RAp1_RAu1"),
                                             ("R", "RAu1_RA"), ("R", "RA_RAu2"), ("R", "RAu2_RAp2"), ("R", "AS1"))}
        self.assertEqual(set(crushing_members(DESIGN)), want)
        self.assertAlmostEqual(z["force_N"], 2 * 48371.0, delta=10.0)


class PlasticImpactTest(unittest.TestCase):
    def check_energy(self, r):
        self.assertAlmostEqual(r["E_bumper"] + r["E_plastic"] + r["E_hard"], r["E_kinetic"], delta=1e-6 * r["E_kinetic"])
        w = float(np.trapezoid(r["F"], r["x"]))
        self.assertAlmostEqual(w, r["E_kinetic"], delta=0.01 * r["E_kinetic"])

    def test_energy_conserved(self):
        self.check_energy(plastic_impact(646.0, 40 / 3.6, 100e3, 0.08, 96.7e3, 0.819, 6.2e8))
        self.check_energy(plastic_impact(500.0, 10.0, 1e6, 0.1, 50e3, 1.0, 1e8))  # folds before the bumper bottoms
        self.check_energy(plastic_impact(646.0, 60 / 3.6, 100e3, 0.08, 96.7e3, 0.819, 6.2e8))  # bottoms out

    def test_no_spring_back(self):
        r = plastic_impact(646.0, 40 / 3.6, 100e3, 0.08, 96.7e3, 0.819, 6.2e8)
        self.assertTrue(np.all(np.diff(r["c"]) >= -1e-12))
        self.assertEqual(r["c"][-1], r["c"].max())
        self.assertAlmostEqual(r["permanent_crush"], r["c"].max(), delta=1e-12)
        self.assertAlmostEqual(r["c"][-1] + 0.08, r["x_max"], delta=1e-9)

    def test_tool_series_energy_and_no_spring_back(self):
        rec, res = run(40)
        s = {x["channel"]: x for x in rec["series"]}
        self.assertEqual(s["crush"]["t_s"], s["wall_force"]["t_s"])
        w = work(s["crush"]["values"], s["wall_force"]["values"])
        self.assertAlmostEqual(w, res["crash_energy"], delta=0.01 * res["crash_energy"])
        self.assertAlmostEqual(s["crush"]["values"][-1], res["max_crush"], delta=1e-6)
        self.assertAlmostEqual(res["permanent_crush"], res["max_crush"] - 80.0, delta=1e-6)  # the bumper springs back


class HandCalcTest(unittest.TestCase):
    def test_demo_against_the_tool(self):
        rec, res = run(40)
        db = on_board(DESIGN, "two_cargo")
        m = mass_summary(db)["mass_kg"]
        ins = {"m": q(m, "kg"), "v": q(40, "km/h"), "F_m": q(res["mean_crush_force"], "N"),
               "k_b": q(100, "N/mm"), "stroke": q(80, "mm")}
        reg = solvers.formula_registry()
        o = {k: v["value"] for k, v in reg.evaluate("crash.plastic_crush", ins)["outputs"].items()}
        for quantity, out in (("max_crush", "x_max"), ("peak_deceleration", "a_max"), ("stop_time", "t_stop"),
                              ("crash_energy", "E")):
            self.assertLessEqual(abs(o[out] - res[quantity]), TOL * abs(res[quantity]), quantity)
            self.assertAlmostEqual(o[out], res[quantity], delta=1e-6 * abs(res[quantity]), msg=quantity)
        self.assertAlmostEqual(o["a_mean"] * G * m, res["mean_crush_force"], delta=1e-6 * res["mean_crush_force"])
        sp = reg.evaluate("crash.plastic_occupant_space_margin", dict(ins, s=q(occupant_space(db), "mm")))["outputs"]
        self.assertAlmostEqual(sp["margin"]["value"], res["occupant_space_margin"], delta=1e-6)
        self.assertEqual(res["crush_bottomed"], 0.0)


class BottomingTest(unittest.TestCase):
    def test_raising_the_speed_bottoms_out(self):
        _, slow = run(40)
        rec, fast = run(60)
        z = crush_zone(DESIGN)
        self.assertEqual(slow["crush_bottomed"], 0.0)
        self.assertEqual(fast["crush_bottomed"], 1.0)
        self.assertAlmostEqual(fast["permanent_crush"], z["usable_mm"], delta=1e-9)
        self.assertGreater(fast["peak_deceleration"], 2 * slow["peak_deceleration"])
        self.assertTrue(any(e["name"].startswith("energy taken by the hard stop") for e in rec["effects"]))
        s = {x["channel"]: x for x in rec["series"]}
        w = work(s["crush"]["values"], s["wall_force"]["values"])
        self.assertAlmostEqual(w, fast["crash_energy"], delta=0.01 * fast["crash_energy"])


if __name__ == "__main__":
    unittest.main()
