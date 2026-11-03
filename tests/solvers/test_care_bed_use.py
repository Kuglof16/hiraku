"""The care bed's checks over how it is used and the side rail's sleeve: the shelf tools use_fatigue (R3) and
actuator_duty (T3), bed_static's tipping under the side rail push (S1-side-rail-push, the floor taking the push at
the casters' contact points), the K0 formulas bed.use_damage (slope 2 m - 1 below the knee), bed.side_push_tipping
and bed.side_rail_push (sleeve, the bar's ends held by the posts' torsion) against closed forms, and the generator's
sleeve variables."""

import math
import unittest

from hiraku import contract, designgen, solvers
from hiraku.catalog import load_catalog
from hiraku.catalog.build import build_load_cases
from hiraku.catalog.builders import product_care_bed as bed
from hiraku.designgen import care_bed

from tests.solvers.test_designgen_care_bed import bed_requirements

REG = solvers.formula_registry()
TOOLS = {t.name: t for t in solvers.builtin_tools()}
PRED = [{"prediction_id": "pred-0001"}]


def qq(v, u):
    return {"value": float(v), "unit": u}


def k0(formula, **inputs):
    return {k: o["value"] for k, o in REG.evaluate(formula, {k: qq(*v) for k, v in inputs.items()})["outputs"].items()}


def results(rec):
    return {r["quantity"]: r for r in rec["results"]}


def predict(design, lc, p):
    vals = p["inputs_from"](design, lc)
    return REG.evaluate(p["formula_id"], {k: vals[r.split(".", 1)[1]] for k, r in p["inputs"].items()})["outputs"][
        p["output"]]["value"]


class UseToolsOnTheShelfTest(unittest.TestCase):
    def test_usable_stage_and_known_answers(self):
        for name in ("use_fatigue", "actuator_duty", "bed_static"):
            t = TOOLS[name]
            contract.validate("tool_spec", t.spec())
            self.assertTrue(t.spec()["usable"], name)
            self.assertEqual(t.spec()["stage"], 1, name)
            checks = t.known_answers()
            self.assertGreaterEqual(len(checks), 3)
            self.assertEqual([c for c in checks if not c["pass"]], [], name)
        self.assertEqual(TOOLS["use_fatigue"].tests, ["R3"])
        self.assertEqual(TOOLS["actuator_duty"].tests, ["T3"])

    def test_bed_static_checks_the_floor_push(self):
        names = {c["case"] for c in TOOLS["bed_static"].known_answers()}
        self.assertIn("1 kN level at Q toward C, 300 mm above the floor", names)

    def test_vehicle_is_refused_with_what_is_missing(self):
        lc = {"load_case_id": "R3-x", "test_id": "R3", "domain": "strength", "title": "x",
              "params": {"n_seat": qq(1, "1"), "n_raise": qq(1, "1")}, "tolerance_rel": 0.5}
        rec = TOOLS["use_fatigue"].run(designgen.ladder_4w.build(), lc, PRED, {})
        self.assertEqual(rec["status"], "failed")
        self.assertIn("care bed", rec["error"])
        lc = {"load_case_id": "T3-x", "test_id": "T3", "domain": "thermal", "title": "x",
              "params": {"actuator": "back", "uses_per_hour": qq(6, "1")}, "tolerance_rel": 0.1}
        rec = TOOLS["actuator_duty"].run(designgen.ladder_4w.build(), lc, PRED, {})
        self.assertEqual(rec["status"], "failed")
        self.assertIn("care bed", rec["error"])


class UseFormulasTest(unittest.TestCase):
    SN = dict(FAT=(71, "MPa"), m=(3, "1"), N_knee=(1e7, "1"))
    KNEE = 71.0 * (2e6 / 1e7) ** (1 / 3)

    def test_use_damage_below_the_knee_takes_2m_minus_1(self):
        dS = 11.2  # the frame's range of the run that summed it with 22 (D about 1e-16)
        got = k0("bed.use_damage", n1=(1e4, "1"), dS1=(dS, "MPa"), n2=(0, "1"), dS2=(0, "MPa"), n3=(0, "1"),
                 dS3=(0, "MPa"), n4=(0, "1"), dS4=(0, "MPa"), **self.SN)
        self.assertAlmostEqual(got["D1"] / (1e4 / (1e7 * (self.KNEE / dS) ** 5)), 1.0, places=9)
        self.assertGreater(got["D1"], 1e-7)
        self.assertEqual(got["m2"], 5.0)

    def test_use_damage_worst_weld(self):
        got = k0("bed.use_damage", n1=(1e4, "1"), dS1=(30, "MPa"), n2=(1e3, "1"), dS2=(65, "MPa"), n3=(1e3, "1"),
                 dS3=(45, "MPa"), n4=(3e3, "1"), dS4=(30, "MPa"), **self.SN)
        frame = 1.3e4 / (1e7 * (self.KNEE / 30.0) ** 5)
        back = 1e3 / (2e6 * (71.0 / 65.0) ** 3)
        knee = 1e3 / (2e6 * (71.0 / 45.0) ** 3)
        self.assertAlmostEqual(got["D_frame"] / frame, 1.0, places=9)
        self.assertAlmostEqual(got["D_worst"] / max(frame, back, knee), 1.0, places=9)
        self.assertAlmostEqual(got["D"] / (frame + back + knee), 1.0, places=9)

    def test_side_push_caster(self):
        # push and weight both at mid-base: each far caster W/4 - F h / (4 y_s); a push at the front pair: all of the
        # roll moment there, the rear far caster keeps W/4 (centre line weight)
        mid = k0("bed.side_push_caster", W=(1200, "N"), y_cg=(0, "mm"), F=(600, "N"), h_F=(880, "mm"), y_s=(440, "mm"),
                 x_cg=(-750, "mm"), x_F=(-750, "mm"), x_r=(-1500, "mm"), L_x=(1500, "mm"))
        self.assertAlmostEqual(mid["R_min"], 1200 / 4 - 600 * 880 / (4 * 440))
        self.assertAlmostEqual(mid["R_front"], mid["R_rear"])
        front = k0("bed.side_push_caster", W=(1200, "N"), y_cg=(0, "mm"), F=(600, "N"), h_F=(880, "mm"), y_s=(440, "mm"),
                   x_cg=(-750, "mm"), x_F=(0, "mm"), x_r=(-1500, "mm"), L_x=(1500, "mm"))
        self.assertAlmostEqual(front["R_rear"], 300.0)
        self.assertAlmostEqual(front["R_min"], 300.0 - 600 * 880 / (2 * 440))

    def test_side_push_tipping(self):
        got = k0("bed.side_push_tipping", W=(1200, "N"), y_cg=(5, "mm"), F=(675, "N"), h_F=(882, "mm"), y_s=(440, "mm"))
        self.assertAlmostEqual(got["R_far"], (1200 * 435 - 675 * 882) / 880)
        self.assertAlmostEqual(got["R_near"] + got["R_far"], 1200.0)

    def rail(self, s, ts, G):
        return k0("bed.side_rail_push", F=(600, "N"), H=(400, "mm"), S=(700, "mm"), n=(2, "1"), u2=(0, "mm"),
                  u3=(0, "mm"), d=(25.4, "mm"), t=(1.6, "mm"), s=(s, "mm"), t_s=(ts, "mm"), g=(0, "mm"),
                  t_g=(4.5, "mm"), b_k=(200, "mm"), t_k=(20, "mm"), E=(205000, "MPa"), G=(G, "MPa"),
                  k_f1=(0, "N*mm"), k_f2=(0, "N*mm"), k_f3=(0, "N*mm"), k_f4=(0, "N*mm"))

    def test_side_rail_sleeve(self):
        d, t, ts = 25.4, 1.6, 2.3
        Z = math.pi * (d**4 - (d - 2 * t) ** 4) / (32 * d)
        do = d + 2 * ts
        Z_in = math.pi * (do**4 - (d - 2 * t) ** 4) / (32 * do)
        G0 = 1e-3  # next to no torsion: the posts' torque and the bar's twist vanish, the posts are plain cantilevers
        got = self.rail(150, ts, G0)
        self.assertAlmostEqual(got["sigma_post"], max(300 * 400 / Z_in, 300 * 250 / Z), places=6)
        self.assertAlmostEqual(self.rail(0, ts, G0)["sigma_post"], 300 * 400 / Z, places=6)
        self.assertLess(got["sigma_post"], self.rail(0, ts, G0)["sigma_post"])

    def test_bar_between_fixed_and_simply_supported(self):
        d, t = 25.4, 1.6
        Z = math.pi * (d**4 - (d - 2 * t) ** 4) / (32 * d)
        self.assertAlmostEqual(self.rail(0, 2.3, 1e15)["sigma_bar"] / (600 * 700 / 8 / Z), 1.0, places=6)  # posts rigid
        self.assertAlmostEqual(self.rail(0, 2.3, 1e-3)["sigma_bar"], 600 * 700 / 4 / Z, places=4)  # posts free to twist
        I = math.pi * (d**4 - (d - 2 * t) ** 4) / 64
        k = 78846 * 2 * I / 400
        M_end = 600 * 700 / 8 / (1 + 2 * 205000 * I / (k * 700))
        self.assertAlmostEqual(self.rail(0, 2.3, 78846)["sigma_bar"], (600 * 700 / 4 - M_end) / Z, places=9)


class SleeveVariablesTest(unittest.TestCase):
    def test_revisable_with_range_and_default(self):
        for name, lo, hi, default in (("side_rail_sleeve_height", 0.0, 200.0, 0.0), ("side_rail_sleeve_t", 1.6, 4.5, 2.3)):
            v = care_bed.VARIABLES[name]
            self.assertTrue(v.revisable)
            self.assertEqual((v.lo, v.hi, v.default), (lo, hi, default))

    def test_default_has_no_sleeve(self):
        d = care_bed.build()
        self.assertFalse([m for m in d["members"] if "sleeve" in m["id"]])

    def test_sleeve_members_and_section(self):
        d = care_bed.build({"side_rail_sleeve_height": 150.0, "side_rail_sleeve_t": 3.2})
        contract.validate("design", d)
        sleeves = [m for m in d["members"] if "_sleeve" in m["id"]]
        self.assertEqual(len(sleeves), 4)
        self.assertTrue(all(m.get("part", "").startswith("side_rail_") for m in sleeves))
        nodes = {n["id"]: n["xyz_mm"] for n in d["nodes"]}
        self.assertAlmostEqual(nodes["LSSP1"][2] - nodes["LSP1"][2], 150.0)
        from hiraku.solvers.k4_geometry import interferences

        self.assertEqual(interferences(d), [])

    def test_the_longest_sleeve_leaves_post_above_it(self):
        d = care_bed.build({"side_rail_sleeve_height": 200.0, "side_rail_height": 250.0, "side_rail_d": 34.0,
                            "deck_t": 0.8, "rail_h": 40.0})
        nodes = {n["id"]: n["xyz_mm"] for n in d["nodes"]}
        self.assertGreater(nodes["LSTP1"][2] - nodes["LSSP1"][2], 34.0)


class BedUseEndToEndTest(unittest.TestCase):
    """v1 (the proposed default) through the new steps: T3 on the lever's stroke, R3 at the worst weld with 2 m - 1,
    S1-side-rail-push red."""

    @classmethod
    def setUpClass(cls):
        cls.req = bed_requirements()
        cls.design = designgen.propose("care_bed", {}, {}, cls.req)
        cls.cases = {lc["load_case_id"]: lc for lc in build_load_cases(load_catalog(), cls.req, cls.design,
                                                                       ["S1", "T3", "R3"])}
        cls.runs = {}
        for lc_id in ("S1-side-rail-push", "T3-6uses", "R3-uses"):
            lc = cls.cases[lc_id]
            step = bed.step_for(lc)
            cls.runs[lc_id] = (step, TOOLS[step["tool"]].run(cls.design, lc, PRED, {}))

    def test_runs_fit_the_contract(self):
        for lc_id, (step, rec) in self.runs.items():
            self.assertEqual(rec["status"], "ok", f"{lc_id}: {rec.get('error')}")
            contract.validate("analysis", dict(rec, analysis_id="ana-0001"))
            self.assertIn(self.cases[lc_id]["criterion"]["quantity"], results(rec))

    def test_side_rail_push_tips_the_bed(self):
        lc = self.cases["S1-side-rail-push"]
        # judged on the lighter caster: one caster lifting starts the tipping, though the far side's sum may stay above 0
        self.assertEqual(lc["criterion"]["quantity"], "min_wheel_load")
        self.assertEqual(lc["criterion"]["op"], ">=")
        step, rec = self.runs["S1-side-rail-push"]
        W = results(rec)["total_mass"]["value"] * 9.80665
        for p in step["predictions"]:
            got = results(rec)[p["quantity"]]["value"]
            want = predict(self.design, lc, p)
            print(f"\nS1-side-rail-push v1 {p['quantity']}: tool {got:.2f} N, K0 {want:.2f} N")
            self.assertLess(got, 0.0)  # red
            self.assertLessEqual(abs(got - want) / W, lc["tolerance_rel"])
        self.assertTrue(any("contact points" in a for a in rec["assumptions"]))

    def test_duty_on_the_lever_stroke(self):
        step, rec = self.runs["T3-6uses"]
        r = results(rec)
        lever = REG.evaluate("bed.lever_actuator_force", bed.p5_inputs(self.design, {
            "load_case_id": "P5-back", "test_id": "P5", "params": {"loading": "driver", "actuator": "back"}}))["outputs"]
        self.assertAlmostEqual(r["actuator_stroke"]["value"], lever["stroke"]["value"], places=6)
        self.assertLess(r["actuator_stroke"]["value"], self.design["variables"]["actuator_stroke"]["value"])
        v = self.design["variables"]["actuator_speed"]["value"]
        self.assertAlmostEqual(r["actuator_duty"]["value"], 100 * 6 * 2 * lever["stroke"]["value"] / 1000 / v / 3600, places=9)
        for p in step["predictions"]:
            self.assertAlmostEqual(predict(self.design, self.cases["T3-6uses"], p), r[p["quantity"]]["value"], places=6)
        print(f"T3-6uses v1: duty {r['actuator_duty']['value']:.3f} % on {r['actuator_stroke']['value']:.1f} mm")

    def test_fatigue_at_the_worst_weld(self):
        step, rec = self.runs["R3-uses"]
        r = results(rec)
        D = r["fatigue_damage"]["value"]
        self.assertEqual(D, max(r[f"fatigue_damage_{w}"]["value"] for w in ("frame", "lug_back", "lug_knee")))
        self.assertEqual(r["fatigue_damage"]["where"], "bracket_back")
        self.assertGreater(r["fatigue_damage_frame"]["value"], 1e-8)  # 2 m - 1, not 22
        k0_D = predict(self.design, self.cases["R3-uses"], step["predictions"][0])
        print(f"R3-uses v1: tool D {D:.4g} (lug {r['stress_range_lug_back']['value']:.2f} MPa), K0 D_worst {k0_D:.4g}")
        self.assertLessEqual(abs(D - k0_D) / max(D, k0_D), self.cases["R3-uses"]["tolerance_rel"])
        self.assertTrue(any("IIW-1823-07" in a for a in rec["assumptions"]))

    def test_predictions_for_the_new_cases(self):
        for lc_id, formulas in (("S1-side-rail-push", {"bed.side_push_tipping", "bed.side_push_caster"}),
                                ("T3-6uses", {"bed.actuator_duty"}), ("R3-uses", {"bed.use_damage"})):
            preds = bed.predictions_for(self.cases[lc_id])
            self.assertEqual({p["formula_id"] for p in preds}, formulas)
            vals = bed.inputs_for(self.design, self.cases[lc_id])
            for p in preds:
                refs = [x.split(".", 1)[1] for x in p["inputs"].values() if x.startswith("from.")]
                self.assertEqual([x for x in refs if x not in vals], [], lc_id)
        self.assertNotIn("m2", bed.inputs_for(self.design, self.cases["R3-uses"]))


if __name__ == "__main__":
    unittest.main()
