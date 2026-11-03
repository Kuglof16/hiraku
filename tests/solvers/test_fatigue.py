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
                FAT=q(71.0, "MPa"), m=q(3.0, "1"), N_knee=q(1e7, "1"))

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
                    m=b["m"], N_knee=b["N_knee"])
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
        # the formula closes the chain's misfit as the chain does; the front pair bends cross_E the other way
        # (on the demo vehicle the masses' inertia behind the rear axle twists the overhang against the front pair)
        from hiraku.contract.loading import on_board
        from hiraku.solvers.k0_handcalc.frame_inputs import antisymmetric_twist

        tw = antisymmetric_twist(on_board(DESIGN, "two_cargo"), "cross_E", {"rear": {"RL": 1000.0, "RR": -1000.0}})
        self.assertAlmostEqual(abs(tw["moment"]["rear"]) / 2.0, out["M_anti_rear"], delta=1e-9 * out["M_anti_rear"])
        self.assertLess(out["M_anti_front"], 0.0)

    def _anti_pair_k1(self, design):
        """cross_E's vertical bending moment at its end b in K1 for 1 kN up at RL and down at RR, inertia relief."""
        from hiraku.solvers.k0_handcalc.vehicle import inertia_tensor, mass_summary, support_contact
        from hiraku.solvers.k1_frame.model import COMBO, build

        pair = {"RL": 1000.0, "RR": -1000.0}
        summ = mass_summary(design)
        items, cg = summ["items"], summ["cg_mm"]
        fm = build(design)
        M = np.zeros(3)
        for s in design["supports"]:
            if s["id"] in pair:
                p, f = support_contact(s), np.array([0.0, 0.0, pair[s["id"]]])
                fm.add_force_at(fm.wheel_nodes[s["id"]], p, f)
                M += np.cross(p - cg, f)
        fm.add_body_loads(items, np.zeros(3), -(np.linalg.inv(inertia_tensor(items, cg)) @ M) * 1e3, cg)
        fm.restrain_free_body()
        fm.solve()
        pm = fm.model.members["cross_E"]
        return pm.moment("Mz", pm.L(), COMBO)

    def test_chain_is_the_beam_models_antisymmetric_half(self):
        """The chain keeps the beam model's antisymmetric vertical bending and torsion and shares the masses' loads
        over the nodes the same way, so cross_E's end moment agrees with K1's; with the rails' torsion doubled the
        moment hardly moves in either (the rails twist less, but carry more of the twist in their own torsion,
        which the end cross member has to take off at the rail ends)."""
        from hiraku.contract.loading import on_board
        from hiraku.solvers.k0_handcalc import sections
        from hiraku.solvers.k0_handcalc.frame_inputs import antisymmetric_twist

        from hiraku import designgen

        # the strict check is on the ladder alone: the chain carries a roll bar's loads to the rails but not its
        # stiffness, which the beam model has (the default v1 has a roll bar; checked below within 5 %)
        bar = on_board(DESIGN, "two_cargo")
        d = on_board(designgen.regenerate(DESIGN, {"rollbar": {"value": 0, "unit": "1"}}), "two_cargo")
        stiff = copy.deepcopy(d)
        rail_sec = next(m["section"] for m in stiff["members"] if m["id"] == "rail_L_BE")
        p = sections.properties(stiff["sections"][rail_sec])
        stiff["sections"]["rail_2J"] = {"shape": "custom", "A_mm2": p["A"], "Iy_mm4": p["Iy"], "Iz_mm4": p["Iz"],
                                        "J_mm4": 2.0 * p["J"], "dims_mm": {"cy": p["cy"], "cz": p["cz"]}}
        for m in stiff["members"]:
            if m["section"] == rail_sec:
                m["section"] = "rail_2J"
        pair = {"rear": {"RL": 1000.0, "RR": -1000.0}}
        for des in (d, stiff):
            chain = antisymmetric_twist(des, "cross_E", pair)["moment"]["rear"]
            k1 = self._anti_pair_k1(des)
            self.assertAlmostEqual(abs(chain), abs(k1), delta=0.01 * abs(k1))
        base, twice = (abs(antisymmetric_twist(x, "cross_E", pair)["moment"]["rear"]) for x in (d, stiff))
        self.assertAlmostEqual(twice, base, delta=0.03 * base)
        with_bar = abs(antisymmetric_twist(bar, "cross_E", pair)["moment"]["rear"])
        self.assertAlmostEqual(with_bar, abs(self._anti_pair_k1(bar)), delta=0.05 * with_bar)

    def test_twist_rate_without_warping_is_st_venant(self):
        from hiraku.solvers.k0_handcalc.frame_inputs import ladder_twist_rate

        GJ, M = 6.0e10, 1.0e6
        pieces = [(1000.0, 5.0e6)]  # all the roll inertia at the front end: the whole frame behind carries M
        rate = ladder_twist_rate(pieces, -1000.0, 1000.0, GJ, 1.0, -900.0, M, 0.0)
        self.assertAlmostEqual(rate, M / GJ, delta=1e-6 * M / GJ)
        warped = ladder_twist_rate(pieces, -1000.0, 1000.0, GJ, 2.0e16, -900.0, M, 0.0)
        self.assertLess(warped, M / GJ)  # the rails' bending stiffens the ladder against twist


class SnSeriesTest(unittest.TestCase):
    def test_curve_and_spectrum(self):
        from hiraku.solvers.shelf.fatigue import sn_series

        out = sn_series(71.0, 3.0, 1e7, 22.0, [(100.0, 0.0, 1.0), (50.0, 0.0, 10.0), (0.0, 0.0, 5.0)], "m")
        curve, spec = out
        N, S = curve["x"]["values"], curve["values"]
        self.assertTrue(all(a > b for a, b in zip(S, S[1:])))  # fewer cycles to failure at a larger range
        self.assertAlmostEqual(float(np.interp(np.log(2e6), np.log(N), S)), 71.0, delta=0.5)  # FAT at 2e6 cycles
        self.assertEqual((spec["channel"], spec["values"], spec["x"]["values"]), ("stress_ranges.m", [100.0, 50.0], [1.0, 11.0]))


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
