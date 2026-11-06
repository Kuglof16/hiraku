"""Links the screen animates from: a frequency row's field_index to its mode_shape field (modal, with the
driveshafts' shapes), and the K3 per-part power, loss and heat-to-air series with their part_id."""

from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

import numpy as np

from hiraku import contract, solvers
from hiraku.designgen import ladder_4w as GEN
from hiraku.solvers.k1_frame.shaft import driveshaft_shapes, shaft_inputs
from hiraku.solvers.k3_power_thermal import drive, forest_loop, from_design, network_from_design, repeat, temperatures
from hiraku.solvers.k3_power_thermal.simulate import CHAIN_STAGES

ROOT = Path(__file__).resolve().parents[2]
DESIGNS = ROOT / "data" / "designs"
V1 = json.loads((DESIGNS / "offroad-4w-v1.json").read_text(encoding="utf-8"))
NO_SHAFT = json.loads((DESIGNS / "offroad-4w-nodriveshaft.json").read_text(encoding="utf-8"))
PRED = [{"prediction_id": "pred-0001"}]
TOOLS = {t.name: t for t in solvers.builtin_tools()}


def q(v, u):
    return {"value": v, "unit": u}


def lc(test_id, **params):
    return {"load_case_id": test_id, "test_id": test_id, "domain": "x", "title": test_id, "params": params,
            "tolerance_rel": 0.15}


LOOP = dict(distance=q(20, "km"), grade_low=q(7, "%"), grade_high=q(12, "%"), speed_low=q(20, "km/h"),
            speed_high=q(40, "km/h"))


def rows(rec):
    return {r["quantity"]: r for r in rec["results"]}


def channel(rec, name):
    return [s for s in rec["series"] if s["channel"] == name]


class ModalLinksTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rec = TOOLS["modal"].run(V1, lc("N1"), PRED, {})
        cls.plain = TOOLS["modal"].run(NO_SHAFT, lc("N1"), PRED, {})
        cls.many = TOOLS["modal"].run(V1, lc("N1", n_modes=q(12, "1")), PRED, {})

    def test_valid(self):
        for rec in (self.rec, self.plain, self.many):
            self.assertEqual(rec["status"], "ok", rec.get("error"))
            contract.validate("analysis", dict(rec, analysis_id="ana-0001"))

    def test_field_index_points_at_the_mode_shape_of_that_frequency(self):
        for rec in (self.rec, self.plain, self.many):
            linked = [r for r in rec["results"] if "field_index" in r]
            self.assertGreater(len(linked), 10)
            for r in linked:
                fl = rec["fields"][r["field_index"]]
                self.assertEqual(fl["kind"], "mode_shape", r["quantity"])
                self.assertEqual(fl["frequency_Hz"], r["value"], r["quantity"])
            for r in rec["results"]:  # rows that are not one mode's frequency get no field_index
                if r["unit"] != "Hz" or r["quantity"].endswith("rotation_frequency"):
                    self.assertNotIn("field_index", r, r["quantity"])

    def test_named_modes_are_linked(self):
        r = rows(self.rec)
        for name in ("first_body_mode", "first_frame_mode", "first_rollbar_mode", "driveshaft_bending_mode",
                     "driveshaft_torsion_mode"):
            self.assertIn("field_index", r[name], name)

    def test_modes_7_to_10_have_shapes(self):
        r = rows(self.rec)
        for i in range(7, 11):
            fl = self.rec["fields"][r[f"mode_{i}"]["field_index"]]
            self.assertTrue(fl["label"].startswith(f"mode {i} "))
            self.assertTrue(fl["per_node"])

    def test_modes_past_the_fields_are_not_linked(self):
        r = rows(self.many)
        self.assertNotIn("field_index", r["mode_11"])
        self.assertNotIn("field_index", r["mode_12"])

    def test_no_driveshaft_no_shaft_rows(self):
        r = rows(self.plain)
        self.assertNotIn("driveshaft_bending_mode", r)
        self.assertEqual(len(self.plain["fields"]), 10)

    def test_driveshaft_shapes(self):
        r = rows(self.rec)
        pid = r["driveshaft_bending_mode"]["where"]
        bend = self.rec["fields"][r["driveshaft_bending_mode"]["field_index"]]
        tors = self.rec["fields"][r["driveshaft_torsion_mode"]["field_index"]]
        for fl in (bend, tors):
            self.assertIn(pid, fl["label"])
            self.assertTrue(all(k.startswith(f"{pid}.p") for k in fl["per_node"]))
        u = list(bend["per_node"].values())
        self.assertEqual(len(u), 21)
        self.assertEqual(u[0], [0.0, 0.0, 0.0])  # pinned at both joints
        self.assertEqual(u[-1], [0.0, 0.0, 0.0])
        # the beam model's first bending shape at its nodes is the simply supported tube's sin(pi x / L)
        size = [math.sqrt(sum(c * c for c in v)) for v in u]
        for k, s in enumerate(size):
            self.assertAlmostEqual(s, math.sin(math.pi * k / 20), delta=1e-3)
        # torsion: the twist as the displacement (vec3, as every mode_shape) of the tube's top line along its
        # tangent, across the axis and level
        vecs = list(tors["per_node"].values())
        self.assertTrue(all(len(v) == 3 for v in vecs))
        self.assertTrue(all(abs(v[2]) < 1e-12 for v in vecs))
        lead = max(vecs, key=lambda v: math.sqrt(sum(c * c for c in v)))
        twist = [sum(a * b for a, b in zip(v, lead)) for v in vecs]  # signed, along the largest one's direction
        self.assertAlmostEqual(max(abs(x) for x in twist), 1.0, places=12)
        self.assertLess(twist[0] * twist[-1], 0.0)  # the motor side and the wheel turn against each other

    def test_inner_joint_is_toward_the_middle(self):
        for pid, s in driveshaft_shapes(V1).items():
            self.assertLess(abs(s["inner_mm"][1]), abs(s["outer_mm"][1]), pid)
            self.assertAlmostEqual(float(np.linalg.norm(s["outer_mm"] - s["inner_mm"])), shaft_inputs(V1)[pid]["L"],
                                   places=9)


class ChainSeriesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.drive = TOOLS["drive_energy"].run(V1, lc("P4", **LOOP), PRED, {})
        cls.thermal = TOOLS["thermal"].run(V1, lc("T1", **LOOP, ambient_temperature=q(40, "degC")), PRED, {})
        cls.split = GEN.build({"battery_split": 2})
        cls.split_drive = TOOLS["drive_energy"].run(cls.split, lc("P4", **LOOP), PRED, {})
        cls.split_thermal = TOOLS["thermal"].run(cls.split, lc("T1", **LOOP, ambient_temperature=q(40, "degC")),
                                                 PRED, {})

    def test_valid(self):
        for rec in (self.drive, self.thermal, self.split_drive, self.split_thermal):
            self.assertEqual(rec["status"], "ok", rec.get("error"))
            contract.validate("analysis", dict(rec, analysis_id="ana-0001"))

    def test_part_ids_and_units(self):
        for rec, design in ((self.drive, V1), (self.thermal, V1), (self.split_drive, self.split),
                            (self.split_thermal, self.split)):
            kinds = {p["id"]: p["kind"] for p in design["parts"]}
            for s in rec["series"]:
                head, _, kind = s["channel"].partition(".")
                if head in ("power", "loss", "heat_to_air"):
                    self.assertEqual(kinds[s["part_id"]], kind, s["channel"])
                    self.assertEqual(s["unit"], "kW" if s["channel"] == "power.battery" else "W", s["channel"])
        for name in ("power.controller", "power.motor", "power.reducer", "loss.battery", "loss.controller",
                     "loss.motor", "loss.reducer"):
            self.assertEqual(len(channel(self.drive, name)), 1, name)
        self.assertEqual({s["part_id"] for s in channel(self.drive, "power.wheel")}, {"wheel_RL", "wheel_RR"})
        for name in ("heat_to_air.battery", "heat_to_air.motor", "heat_to_air.controller"):
            self.assertEqual(len(channel(self.thermal, name)), 1, name)

    def test_two_packs_one_series_each(self):
        for rec, names in ((self.split_drive, ("power.battery", "loss.battery")),
                           (self.split_thermal, ("loss.battery", "heat_to_air.battery"))):
            for name in names:
                a, b = channel(rec, name)
                self.assertEqual({a["part_id"], b["part_id"]}, {"battery", "battery2"})
                self.assertEqual(a["values"], b["values"])  # in series: the same current, half the voltage each
        r = drive(repeat(forest_loop(0.07, 0.12, 20 / 3.6, 40 / 3.6), 20000.0), from_design(self.split))
        total = sum(s["values"][3] for s in channel(self.split_drive, "power.battery"))
        self.assertAlmostEqual(total, float(np.interp(channel(self.split_drive, "power.battery")[0]["t_s"][3],
                                                      r.t, np.concatenate([[r.P_batt[0]], r.P_batt]))) / 1000.0,
                               places=9)

    def test_power_and_loss_balance(self):
        pt = from_design(V1)
        r = drive(repeat(forest_loop(0.07, 0.12, 20 / 3.6, 40 / 3.6), 20000.0), pt)
        downstream = r.heat["controller"] + r.heat["motor"] + r.gear_loss + r.flow["wheel"]
        driving = r.flow["wheel"] >= 0
        self.assertTrue(driving.any() and (~driving).any())
        tol = 1e-9 * float(np.max(np.abs(r.P_batt)))
        # driving: battery terminal power = aux + controller, motor and reducer losses + wheel power
        np.testing.assert_allclose(r.P_batt[driving], pt.P_aux + downstream[driving], rtol=0.0, atol=tol)
        # regenerating: the battery gets regen_efficiency of the wheel power; the part losses follow the chain
        # efficiencies, leaving (eta_drive eta_motor eta_ctrl - eta_regen) |wheel| not given to a part
        gap = (pt.eta_drive * pt.eta_motor * pt.eta_ctrl - pt.eta_regen) * np.abs(r.flow["wheel"][~driving])
        np.testing.assert_allclose(r.P_batt[~driving] - pt.P_aux, downstream[~driving] + gap, rtol=0.0, atol=tol)
        for a, b, loss in (("motor", "reducer", "motor"), ("reducer", "wheel", None)):
            lost = r.heat[loss] if loss else r.gear_loss
            np.testing.assert_allclose(r.flow[a] - r.flow[b], lost, rtol=0.0, atol=tol)
        self.assertEqual(set(r.flow), set(CHAIN_STAGES))
        # the same on the record's series (0.1 W rounding): stage by stage and battery to controller
        s = {x["channel"]: np.array(x["values"]) for x in self.drive["series"] if x["channel"] != "power.wheel"}
        wheel = sum(np.array(x["values"]) for x in channel(self.drive, "power.wheel"))
        np.testing.assert_allclose(s["power.motor"] - s["power.reducer"], s["loss.motor"], atol=0.25)
        np.testing.assert_allclose(s["power.reducer"] - wheel, s["loss.reducer"], atol=0.35)
        np.testing.assert_allclose(s["power.battery"] * 1000.0 - pt.P_aux, s["power.controller"], atol=0.1)

    def test_heat_to_air_and_stored_heat_close_the_ledger(self):
        pt, net = from_design(V1), network_from_design(V1)
        r = drive(repeat(forest_loop(0.07, 0.12, 20 / 3.6, 40 / 3.6), 20000.0), pt)
        th = temperatures(r, net, 40.0)
        T, t, P = th["T"], th["t"], th["losses"]
        dt = np.diff(t)
        for i, p in enumerate(net.names):
            q_air = net.G_amb[i] * (T[:, i] - 40.0)
            out = float(np.sum((q_air[1:] + q_air[:-1]) / 2.0 * dt))
            stored = float(net.C[i] * (T[-1, i] - 40.0))
            put_in = float(np.sum(P[:, i] * dt))
            self.assertAlmostEqual(out + stored, put_in, delta=1e-3 * put_in, msg=p)
        # the record: heat_to_air series (sampled every SERIES_STEP_S) + stored heat = mean loss x drive time
        res = rows(self.thermal)
        for i, p in enumerate(net.names):
            [s] = channel(self.thermal, f"heat_to_air.{p}")
            out = float(np.trapezoid(s["values"], s["t_s"])) if hasattr(np, "trapezoid") else float(
                np.trapz(s["values"], s["t_s"]))
            stored = float(net.C[i] * (res[f"temp_{p}_end"]["value"] - 40.0))
            put_in = res[f"loss_{p}_mean"]["value"] * res["drive_time"]["value"]
            self.assertAlmostEqual(out + stored, put_in, delta=0.01 * put_in, msg=p)


if __name__ == "__main__":
    unittest.main()
