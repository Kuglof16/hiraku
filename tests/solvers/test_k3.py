import copy
import json
import math
import unittest
from pathlib import Path

import numpy as np

from hiraku import contract, solvers
from hiraku.solvers.k0_handcalc.vehicle import DesignError
from hiraku.solvers.k3_power_thermal import (Segment, ThermalNetwork, drive, forest_loop, from_design,
                                            network_from_design, repeat)

ROOT = Path(__file__).resolve().parents[2]
DESIGN = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
PRED = [{"prediction_id": "pred-0001"}]
TOOLS = {t.name: t for t in solvers.builtin_tools()}


def q(v, u):
    return {"value": v, "unit": u}


LOOP = dict(distance=q(20, "km"), grade_low=q(7, "%"), grade_high=q(12, "%"), speed_low=q(20, "km/h"), speed_high=q(40, "km/h"))


def lc(lc_id, test_id, domain, **params):
    return {"load_case_id": lc_id, "test_id": test_id, "domain": domain, "title": lc_id, "params": params, "tolerance_rel": 0.15}


def results(rec):
    return {r["quantity"]: r for r in rec["results"]}


class NetworkTest(unittest.TestCase):
    def test_step_is_exact_for_any_dt(self):
        net = ThermalNetwork(["a"], np.array([2000.0]), np.array([2.0]))
        for dt in (0.5, 10.0, 250.0):
            n = int(1000 / dt)
            T = net.simulate(np.full((n, 1), 100.0), dt, 0.0)
            self.assertAlmostEqual(T[-1, 0], 50 * (1 - math.exp(-1)), places=9)

    def test_rejects_bad_values(self):
        with self.assertRaises(ValueError):
            ThermalNetwork(["a"], np.array([0.0]), np.array([1.0]))


class DriveTest(unittest.TestCase):
    def setUp(self):
        self.pt = from_design(DESIGN)

    def test_energy_closes(self):
        r = drive(repeat(forest_loop(0.07, 0.12, 20 / 3.6, 40 / 3.6), 4000.0), self.pt)
        e = r.energy
        # battery energy = wheel work + chain and battery losses + aux - what regeneration did not have to burn
        self.assertGreater(e["battery"], e["rolling"] + e["aero"])
        self.assertAlmostEqual(e["grade_up"], -e["grade_down"], delta=1e-6 * e["grade_up"])  # a loop comes back down
        I_int = float(np.sum(r.I_batt * np.diff(r.t)))
        self.assertAlmostEqual(r.soc[0] - r.soc[-1], I_int / (self.pt.Q_batt * 3600), places=12)

    def test_loop_repeats(self):
        segs = repeat(forest_loop(0.07, 0.12, 5.0, 10.0), 4500.0)
        self.assertAlmostEqual(sum(s.length for s in segs), 4500.0, places=9)

    def test_stall_on_a_steep_grade(self):
        weak = copy.deepcopy(DESIGN)
        weak["variables"]["motor_peak_torque"] = q(5.0, "N*m")
        with self.assertRaisesRegex(DesignError, "stalls"):
            drive([Segment(100.0, 0.3, 5.0)], from_design(weak))

    def test_missing_data_is_listed(self):
        d = copy.deepcopy(DESIGN)
        del d["variables"]["battery_resistance"]
        with self.assertRaisesRegex(DesignError, "battery_resistance"):
            from_design(d)
        del d["variables"]["motor_heat_capacity"]
        with self.assertRaisesRegex(DesignError, "motor_heat_capacity"):
            network_from_design(d)


class ToolsTest(unittest.TestCase):
    def test_checks(self):
        for name in ("drive_energy", "thermal"):
            t = TOOLS[name]
            checks = t.check()
            self.assertEqual([c for c in checks if not c["pass"]], [], name)
            contract.validate("tool_spec", t.spec())

    def test_drive_energy_on_the_demo(self):
        rec = TOOLS["drive_energy"].run(DESIGN, lc("P4", "P4", "powertrain", **LOOP), PRED, {})
        contract.validate("analysis", dict(rec, analysis_id="ana-0001"))
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        r = results(rec)
        self.assertAlmostEqual(r["soc_used"]["value"] + r["soc_end"]["value"], 100.0, places=9)
        self.assertGreater(r["range"]["value"], 20.0)
        self.assertAlmostEqual(sum(e["share"] for e in rec["effects"]), 1.0, places=9)
        self.assertEqual({s["channel"] for s in rec["series"]}, {"speed", "grade", "power.battery", "current.battery", "soc"})
        empty = results(TOOLS["drive_energy"].run(DESIGN, lc("P4", "P4", "powertrain", **LOOP, loading="empty"), PRED, {}))
        self.assertLess(empty["energy_used"]["value"], r["energy_used"]["value"])

    def test_battery_runs_out(self):
        rec = TOOLS["drive_energy"].run(DESIGN, lc("P4", "P4", "powertrain", **dict(LOOP, distance=q(80, "km"))), PRED, {})
        r = results(rec)
        self.assertEqual(r["soc_end"]["value"], 0.0)
        self.assertLess(r["distance_on_battery"]["value"], 80.0)

    def test_thermal_on_the_demo(self):
        params = dict(LOOP, ambient_temperature=q(40, "degC"), charge_power=q(1, "kW"), charge_time=q(2, "h"))
        rec = TOOLS["thermal"].run(DESIGN, lc("T1", "T1", "thermal", **params), PRED, {})
        contract.validate("analysis", dict(rec, analysis_id="ana-0002"))
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        r = results(rec)
        for p in ("battery", "motor", "controller"):
            self.assertGreater(r[f"temp_{p}_max"]["value"], 40.0)
            self.assertEqual(r[f"temp_{p}_max"]["where"], p)
            self.assertLessEqual(len(next(s for s in rec["series"] if s["channel"] == f"temp.{p}")["t_s"]), 601)
        self.assertEqual(rec["fields"][0]["kind"], "temperature")

    def test_thermal_rc_prediction_matches(self):
        """thermal.rc_step with the mean drive loss and the drive time predicts the battery peak."""
        rec = TOOLS["thermal"].run(DESIGN, lc("T1", "T1", "thermal", **LOOP, ambient_temperature=q(40, "degC")), PRED, {})
        r = results(rec)
        R = DESIGN["variables"]["battery_thermal_resistance"]["value"]
        C = DESIGN["variables"]["battery_heat_capacity"]["value"]
        pred = solvers.formula_registry().evaluate("thermal.rc_step", {
            "P": q(r["loss_battery_mean"]["value"], "W"), "R": q(R, "K/W"), "tau": q(R * C, "s"),
            "t": q(r["drive_time"]["value"], "s"), "T_amb": q(40, "degC")})["outputs"]["T"]["value"]
        self.assertLess(abs(r["temp_battery_max"]["value"] - pred) / (pred - 40.0), 0.15)

    def test_thermal_needs_ambient(self):
        rec = TOOLS["thermal"].run(DESIGN, lc("T1", "T1", "thermal", **LOOP), PRED, {})
        self.assertEqual(rec["status"], "failed")
        self.assertIn("ambient_temperature", rec["error"])

    def test_loop_params_required(self):
        rec = TOOLS["drive_energy"].run(DESIGN, lc("P4", "P4", "powertrain", distance=q(20, "km")), PRED, {})
        self.assertEqual(rec["status"], "failed")
        self.assertIn("grade_low", rec["error"])


if __name__ == "__main__":
    unittest.main()
