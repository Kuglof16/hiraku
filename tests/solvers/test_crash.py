import copy
import json
import unittest
from pathlib import Path

from hiraku import contract, solvers
from hiraku.solvers.k1_frame.impact import Front, impact

ROOT = Path(__file__).resolve().parents[2]
DESIGN = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
PRED = [{"prediction_id": "pred-0001"}]
TOOL = {t.name: t for t in solvers.builtin_tools()}["crash"]
G = 9.80665


def q(v, u):
    return {"value": v, "unit": u}


def lc(**params):
    return {"load_case_id": "C1", "test_id": "C1", "domain": "crash", "title": "C1", "params": params, "tolerance_rel": 0.15}


def results(rec):
    return {r["quantity"]: r for r in rec["results"]}


class ImpactTest(unittest.TestCase):
    def test_linear_known_answer(self):
        # 500 kg, 200 kN/m, 40 km/h: a = 222.2 m/s^2 (22.7 g), x = 0.556 m; standstill after a quarter
        # period (pi/2) sqrt(m/k) = 0.0785 s (0.157 s is the whole contact if it bounced back elastically)
        r = impact(500.0, 40 / 3.6, Front(200e3, 10.0, 0.0))
        self.assertAlmostEqual(r["a_max"], 222.2, delta=0.05)
        self.assertAlmostEqual(r["x_max"], 0.556, delta=0.0005)
        self.assertAlmostEqual(r["t_stop"], 0.0785, delta=0.0001)
        self.assertAlmostEqual(2 * r["t_stop"], 0.157, delta=0.0005)
        f = solvers.formula_registry().evaluate("crash.linear_spring", {"m": q(500, "kg"), "v": q(40, "km/h"), "k": q(200, "N/mm")})
        self.assertAlmostEqual(f["outputs"]["a_max"]["value"] * G, r["a_max"], delta=r["a_max"] * 1e-6)

    def test_bottoming_out_raises_the_peak(self):
        soft = impact(600.0, 10.0, Front(100e3, 10.0, 0.0))
        stiff = impact(600.0, 10.0, Front(100e3, 0.08, 600e3))
        self.assertGreater(stiff["a_max"], soft["a_max"])
        self.assertAlmostEqual(stiff["E_absorbed"], stiff["E_kinetic"], delta=stiff["E_kinetic"] * 1e-6)


class CrashToolTest(unittest.TestCase):
    def test_checks(self):
        self.assertEqual([c for c in TOOL.check() if not c["pass"]], [])
        contract.validate("tool_spec", TOOL.spec())

    def test_demo(self):
        rec = TOOL.run(DESIGN, lc(speed=q(40, "km/h")), PRED, {})
        contract.validate("analysis", dict(rec, analysis_id="ana-0001"))
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        r = results(rec)
        m = r["vehicle_mass"]["value"]
        self.assertAlmostEqual(r["crash_energy"]["value"], 0.5 * m * (40 / 3.6) ** 2, places=6)
        self.assertAlmostEqual(r["inertia_acceleration"]["value"], r["peak_deceleration"]["value"] * G, delta=1e-6)
        self.assertAlmostEqual(r["battery_inertia_force"]["value"], 50.0 * r["peak_deceleration"]["value"] * G, places=6)
        self.assertIn("occupant_space_margin", r)
        self.assertEqual({s["channel"] for s in rec["series"]}, {"deceleration", "crush", "wall_force"})

    def test_lighter_load_decelerates_harder(self):
        full = results(TOOL.run(DESIGN, lc(speed=q(40, "km/h")), PRED, {}))
        empty = results(TOOL.run(DESIGN, lc(speed=q(40, "km/h"), loading="empty"), PRED, {}))
        self.assertGreater(empty["peak_deceleration"]["value"], full["peak_deceleration"]["value"])

    def test_missing_front_data(self):
        d = copy.deepcopy(DESIGN)
        del d["variables"]["crush_stiffness"]
        rec = TOOL.run(d, lc(speed=q(40, "km/h")), PRED, {})
        self.assertEqual(rec["status"], "failed")
        self.assertIn("crush_stiffness", rec["error"])
        self.assertEqual(TOOL.run(DESIGN, lc(), PRED, {})["status"], "failed")

    def test_mean_deceleration_formula(self):
        o = solvers.formula_registry().evaluate("crash.mean_deceleration", {"m": q(760, "kg"), "v": q(40, "km/h"), "s": q(300, "mm")})["outputs"]
        self.assertAlmostEqual(o["a_mean"]["value"], (40 / 3.6) ** 2 / 0.6 / G, places=9)
        self.assertAlmostEqual(o["E"]["value"], 0.5 * 760 * (40 / 3.6) ** 2, places=6)


if __name__ == "__main__":
    unittest.main()
