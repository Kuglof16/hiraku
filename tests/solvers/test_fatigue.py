import copy
import json
import unittest
from pathlib import Path

import numpy as np

from hiraku import contract, solvers
from hiraku.solvers.k0_handcalc.vehicle import support_loads
from hiraku.solvers.k1_frame.fatigue import equivalent_range, influence, miner, rainflow, reversals, sn_cycles

ROOT = Path(__file__).resolve().parents[2]
DESIGN = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
PRED = [{"prediction_id": "pred-0001"}]
TOOL = {t.name: t for t in solvers.builtin_tools()}["fatigue"]


def q(v, u):
    return {"value": v, "unit": u}


def lc(**params):
    return {"load_case_id": "R3", "test_id": "R3", "domain": "strength", "title": "R3", "params": params, "tolerance_rel": 0.15}


ROAD = dict(distance=q(5000, "km"), road_class="C", speed=q(20, "km/h"), sample_length=q(200, "m"))


def results(rec):
    return {r["quantity"]: r for r in rec["results"]}


class RainflowTest(unittest.TestCase):
    def test_astm_example(self):
        counts = {}
        for r, _, c in rainflow([-2, 1, -3, 5, -1, 3, -4, 4, -2]):
            counts[r] = counts.get(r, 0.0) + c
        self.assertEqual(counts, {3.0: 0.5, 4.0: 1.5, 6.0: 0.5, 8.0: 1.0, 9.0: 0.5})

    def test_reversals_and_constant_amplitude(self):
        self.assertEqual(reversals([0, 1, 2, 2, 1, 0, 3]), [0, 2, 0, 3])
        cyc = rainflow([0, 10, 0, 10, 0, 10, 0])
        full = sum(c for r, _, c in cyc if r == 10)
        self.assertAlmostEqual(full, 3.0)

    def test_sn_and_miner(self):
        self.assertAlmostEqual(sn_cycles(71.0, 71.0, 3.0, 1e7, 22.0), 2e6)
        knee = 71.0 * (2e6 / 1e7) ** (1 / 3)
        self.assertAlmostEqual(sn_cycles(knee, 71.0, 3.0, 1e7, 22.0), 1e7, delta=1e-3)
        self.assertGreater(sn_cycles(knee * 0.9, 71.0, 3.0, 1e7, 22.0), 1e7 * (1 / 0.9) ** 21)
        self.assertAlmostEqual(miner([(71.0, 0.0, 2e6)], 71.0, 3.0, 1e7, 22.0), 1.0)
        self.assertAlmostEqual(equivalent_range([(50.0, 0.0, 2e6)], 3.0), 50.0)
        o = solvers.formula_registry().evaluate("fatigue.constant_amplitude_damage", {
            "dS": q(100, "MPa"), "n": q(1000, "1"), "FAT": q(71, "MPa"), "m": q(3, "1"), "N_knee": q(1e7, "1"), "m2": q(22, "1")})
        self.assertAlmostEqual(o["outputs"]["D"]["value"], 1000 / (2e6 * 0.71**3), places=12)


class RoadDamageFormulaTest(unittest.TestCase):
    BASE = dict(sigma_w=q(22.0, "MPa"), W=q(1700.0, "N"), m_u=q(22.0, "kg"), k_s=q(20.0, "N/mm"), zeta=q(0.3, "1"),
                k_t=q(150.0, "N/mm"), G_d=q(256e-6 * 1e9, "mm^3"), v=q(40.0, "km/h"), d=q(5000.0, "km"),
                FAT=q(71.0, "MPa"), m=q(3.0, "1"), N_knee=q(1e7, "1"), m2=q(22.0, "1"))

    def ev(self, **over):
        return {k: v["value"] for k, v in solvers.formula_registry().evaluate(
            "fatigue.road_damage_quarter_car", {**self.BASE, **over})["outputs"].items()}

    def test_linear_system_and_counting(self):
        a = self.ev()
        rougher = self.ev(G_d=q(4 * 256e-6 * 1e9, "mm^3"))  # one ISO class rougher: 4 times the PSD
        self.assertAlmostEqual(rougher["sigma_F"] / a["sigma_F"], 2.0, places=9)
        self.assertAlmostEqual(a["sigma_s"], 22.0 / 1700.0 * a["sigma_F"], places=9)
        self.assertAlmostEqual(self.ev(d=q(10000.0, "km"))["D"], 2 * a["D"], delta=a["D"] * 1e-9)
        self.assertAlmostEqual(a["n"], a["nu0"] * 5000e3 / (40 / 3.6), delta=1e-6 * a["n"])
        self.assertGreater(rougher["D"], a["D"])


class FourWheelFormulaTest(unittest.TestCase):
    QC = RoadDamageFormulaTest.BASE

    def four(self, **over):
        b = self.QC
        base = dict(sigma_ra=q(b["sigma_w"]["value"] / b["W"]["value"] * 1000.0, "MPa"), sigma_rb=q(0.0, "MPa"),
                    sigma_fa=q(0.0, "MPa"), sigma_fb=q(0.0, "MPa"),
                    W_f=b["W"], m_uf=b["m_u"], k_sf=b["k_s"], zeta_f=b["zeta"], W_r=b["W"], m_ur=b["m_u"], k_sr=b["k_s"],
                    zeta_r=b["zeta"], k_t=b["k_t"], L=q(1700.0, "mm"), G_d=b["G_d"], v=b["v"], d=b["d"], FAT=b["FAT"],
                    m=b["m"], N_knee=b["N_knee"], m2=b["m2"])
        return {k: v["value"] for k, v in solvers.formula_registry().evaluate(
            "fatigue.road_damage_four_wheels", {**base, **over})["outputs"].items()}

    def test_one_wheel_is_the_quarter_car(self):
        """With only one wheel stressing the weld, the four-wheel damage is the one-wheel formula's."""
        one = RoadDamageFormulaTest.ev(RoadDamageFormulaTest())
        four = self.four()
        for k in ("D", "sigma_s", "nu0", "n"):
            self.assertAlmostEqual(four[k], one[k], delta=1e-9 * abs(one[k]), msg=k)

    def test_tracks_add_and_axles_interfere(self):
        a = self.four()["sigma_s"]
        s = self.QC["sigma_w"]["value"] / self.QC["W"]["value"] * 1000.0
        two = self.four(sigma_rb=q(s, "MPa"))["sigma_s"]  # the other track: independent, variances add
        self.assertAlmostEqual(two, a * 2**0.5, delta=1e-9 * a)
        same = self.four(sigma_fa=q(s, "MPa"))["sigma_s"]  # the same track a wheelbase earlier: neither sum nor root sum
        self.assertTrue(0.0 < same < 2 * a and abs(same - a * 2**0.5) > 1e-3 * a)


class EndCrossMemberTest(unittest.TestCase):
    def test_demo_overhang_and_the_split(self):
        from hiraku.solvers.k0_handcalc.frame_inputs import end_cross_member_inputs

        ec = end_cross_member_inputs(DESIGN, loading="two_cargo")
        self.assertEqual((ec["end_cross_member"], ec["joint_cross_member"], ec["supports"]),
                         ("cross_E", "cross_B", ["RL", "RR", "FL", "FR"]))
        reg = solvers.formula_registry()
        out = {k: v["value"] for k, v in reg.evaluate("frame_static.end_cross_member_stress", ec["inputs"])["outputs"].items()}
        z = 20.0 / ((40.0**4 - 35.4**4) / 12.0)  # h_c / 2 over I of the 40 x 40 x 2.3 tube
        self.assertAlmostEqual(out["sigma_ra"] + out["sigma_rb"], 2 * out["M_sym"] * z, places=9)
        self.assertAlmostEqual(out["sigma_ra"] - out["sigma_rb"], 2 * out["M_anti_rear"] * z, places=9)
        self.assertAlmostEqual(out["sigma_fa"], -out["sigma_fb"], places=12)
        flat = dict(ec["inputs"], misfit_rear=q(0.0, "rad"), misfit_front=q(0.0, "rad"))
        out0 = {k: v["value"] for k, v in reg.evaluate("frame_static.end_cross_member_stress", flat)["outputs"].items()}
        self.assertAlmostEqual(out0["sigma_ra"], out0["sigma_rb"], places=12)  # no twist: both rear wheels alike

    def test_twist_rate_without_warping_is_st_venant(self):
        from hiraku.solvers.k0_handcalc.frame_inputs import ladder_twist_rate

        GJ, M = 6.0e10, 1.0e6
        pieces = [(1000.0, 5.0e6)]  # all the roll inertia at the front end: the whole frame behind carries M
        rate = ladder_twist_rate(pieces, -1000.0, 1000.0, GJ, 1.0, -900.0, M, 0.0)
        self.assertAlmostEqual(rate, M / GJ, delta=1e-6 * M / GJ)
        warped = ladder_twist_rate(pieces, -1000.0, 1000.0, GJ, 2.0e16, -900.0, M, 0.0)
        self.assertLess(warped, M / GJ)  # the rails' bending stiffens the ladder against twist


class InfluenceTest(unittest.TestCase):
    def test_superposition_reproduces_the_static_stress(self):
        S, cols, pts = influence(DESIGN, "z")
        F = np.array([support_loads(DESIGN)["supports"][sid]["force_N"][2] for sid, _ in cols])
        sig = S @ F
        rest = TOOL_FS.run(DESIGN, {"load_case_id": "R1", "test_id": "R1", "domain": "strength", "title": "R1",
                                    "params": {}, "tolerance_rel": 0.15}, PRED, {})
        worst = {r["quantity"]: r for r in rest["results"]}["max_stress"]
        # normal stress at the welds vs the von Mises maximum (which also counts torsion): close, not above
        self.assertLessEqual(float(np.abs(sig).max()), worst["value"] * 1.0001)
        self.assertGreater(float(np.abs(sig).max()), 0.9 * worst["value"])


TOOL_FS = {t.name: t for t in solvers.builtin_tools()}["frame_static"]


class FatigueToolTest(unittest.TestCase):
    def test_checks(self):
        self.assertEqual([c for c in TOOL.check() if not c["pass"]], [])
        contract.validate("tool_spec", TOOL.spec())

    def test_demo_road(self):
        rec = TOOL.run(DESIGN, lc(**ROAD), PRED, {})
        contract.validate("analysis", dict(rec, analysis_id="ana-0001"))
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        r = results(rec)
        self.assertGreater(r["fatigue_damage"]["value"], 0.0)
        self.assertAlmostEqual(r["fatigue_life"]["value"] * r["fatigue_damage"]["value"], 5000.0, places=6)
        self.assertEqual(rec["fields"][0]["kind"], "damage")
        self.assertAlmostEqual(sum(e.get("share", 0) for e in rec["effects"]), 1.0, places=9)

    def test_rougher_road_does_more_damage(self):
        c = results(TOOL.run(DESIGN, lc(**ROAD), PRED, {}))["fatigue_damage"]["value"]
        d = results(TOOL.run(DESIGN, lc(**dict(ROAD, road_class="D")), PRED, {}))["fatigue_damage"]["value"]
        self.assertGreater(d, c)

    def test_damage_scales_with_distance(self):
        a = results(TOOL.run(DESIGN, lc(**ROAD), PRED, {}))["fatigue_damage"]["value"]
        b = results(TOOL.run(DESIGN, lc(**dict(ROAD, distance=q(10000, "km"))), PRED, {}))["fatigue_damage"]["value"]
        self.assertAlmostEqual(b, 2 * a, delta=a * 1e-9)

    def test_upstream_history_is_used(self):
        """A K2-style history (reaction_z.<ID>, speed) replaces the road model."""
        t = np.linspace(0.0, 10.0, 2001)
        static = support_loads(DESIGN)["supports"]
        series = [{"channel": "speed", "unit": "km/h", "t_s": list(t), "values": [36.0] * len(t)}]
        for i, sid in enumerate(("FL", "FR", "RL", "RR")):
            W = float(static[sid]["force_N"][2])
            series.append({"channel": f"reaction_z.{sid}", "unit": "N", "t_s": list(t),
                           "values": list(W * (1 + 0.3 * np.sin(2 * np.pi * 2.0 * t + i)))})
        up = {"status": "ok", "series": series, "vectors": []}
        rec = TOOL.run(DESIGN, lc(distance=q(5000, "km")), PRED, {"upstream_analyses": {"S5": up}})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        r = results(rec)
        self.assertAlmostEqual(r["sample_length"]["value"], 100.0, places=6)  # 10 s at 10 m/s
        self.assertIn("S5", rec["effects"][-1]["name"])

    def test_missing_inputs(self):
        self.assertEqual(TOOL.run(DESIGN, lc(road_class="C", speed=q(20, "km/h")), PRED, {})["status"], "failed")
        self.assertEqual(TOOL.run(DESIGN, lc(distance=q(5000, "km"), speed=q(20, "km/h")), PRED, {})["status"], "failed")
        d = copy.deepcopy(DESIGN)
        del d["variables"]["fatigue_fat_class"]
        rec = TOOL.run(d, lc(**ROAD), PRED, {})
        self.assertEqual(rec["status"], "failed")
        self.assertIn("fatigue_fat_class", rec["error"])


if __name__ == "__main__":
    unittest.main()
