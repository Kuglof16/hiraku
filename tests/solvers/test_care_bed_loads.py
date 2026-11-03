"""The care bed's load cases (hiraku.catalog.builders.product_care_bed) and its K0 formulas (library_bed) against
closed-form answers."""

import math
import unittest

from hiraku import contract, designgen, solvers
from hiraku.catalog import load_catalog
from hiraku.catalog.build import NO_BUILDER, build_load_cases
from hiraku.catalog.builders import BUILDERS, PRODUCT_STEPS, BuildError
from hiraku.catalog.builders import analysis as general
from hiraku.catalog.builders import product_care_bed as bed

from tests.solvers.test_designgen_care_bed import G0, bed_requirements

REG = solvers.formula_registry()


def qq(v, u):
    return {"value": float(v), "unit": u}


def k0(formula, **inputs):
    return {k: o["value"] for k, o in REG.evaluate(formula, {k: qq(*v) for k, v in inputs.items()})["outputs"].items()}


class BedLoadCasesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.req = bed_requirements()
        cls.design = designgen.propose("care_bed", {}, {}, cls.req)
        cls.cases = {lc["load_case_id"]: lc for lc in build_load_cases(load_catalog(), cls.req, cls.design,
                                                                       ["S1", "R5", "P5", "T3", "R3"])}

    def test_cases_fit_the_contract(self):
        self.assertEqual(set(self.cases), {"S1-edge1350N", "S1-side-rail-push", "R5-deck", "R5-side-rail", "P5-back",
                                           "P5-knee", "T3-6uses", "R3-uses"})
        for lc in self.cases.values():
            contract.validate("load_case", lc)
            self.assertIn("criterion", lc, lc["load_case_id"])

    def test_limits_come_from_the_requirements_and_the_design(self):
        c = self.cases
        self.assertEqual(c["S1-edge1350N"]["params"]["load_z"], qq(-1350.0, "N"))  # Q60
        self.assertAlmostEqual(c["S1-edge1350N"]["params"]["load_point_y"]["value"], 910.0 / 2 - 125.0)
        self.assertEqual(c["R5-deck"]["criterion"]["limit"], qq(245.0 / 4.0, "MPa"))  # STKR400 / Q61
        self.assertEqual(c["R5-side-rail"]["criterion"]["limit"], qq(215.0 / 4.0, "MPa"))  # STKM13A / Q61
        self.assertEqual(c["R5-side-rail"]["params"]["load_y"], qq(0.5 * 1350.0, "N"))
        for k in ("P5-back", "P5-knee"):
            self.assertEqual(c[k]["criterion"]["limit"], qq(4000.0, "N"))
        self.assertEqual(c["T3-6uses"]["criterion"]["limit"], qq(10.0, "%"))
        self.assertEqual({k: v["value"] for k, v in c["R3-uses"]["params"].items() if k.startswith("n_")},
                         {"n_seat": 10000.0, "n_lift": 3000.0, "n_raise": 1000.0, "n_rail": 30000.0})  # Q92

    def test_other_products_keep_their_builders(self):
        self.assertIs(BUILDERS["R3"].general, general.r3_fatigue)
        with self.assertRaisesRegex(BuildError, NO_BUILDER):
            BUILDERS["P5"]({"id": "P5", "domain": "powertrain"}, {"product": "serving-robot", "requirements": []}, {})
        self.assertIs(PRODUCT_STEPS["care-bed"], bed.step_for)
        for lc_id in ("S1-edge1350N", "R5-deck", "R5-side-rail", "P5-back"):
            self.assertIsNotNone(bed.step_for(self.cases[lc_id]), lc_id)
        self.assertEqual(bed.step_for(self.cases["T3-6uses"])["tool"], "actuator_duty")
        self.assertEqual(bed.step_for(self.cases["R3-uses"])["tool"], "use_fatigue")

    def test_a_design_off_the_requirements_is_refused(self):
        light = designgen.care_bed.build({"occupant_load": {"value": 1000.0, "unit": "N"}})
        with self.assertRaisesRegex(BuildError, "Q33"):
            bed.r5_static({"id": "R5", "domain": "strength"}, self.req, light)
        with self.assertRaisesRegex(BuildError, "care bed"):
            bed.r5_static({"id": "R5", "domain": "strength"}, self.req, designgen.ladder_4w.build())

    def test_every_prediction_evaluates(self):
        for lc in self.cases.values():
            step = bed.step_for(lc)
            preds = step["predictions"] if step else {"T3": bed.T3_PREDICTIONS, "R3": bed.R3_PREDICTIONS}[lc["test_id"]]
            for p in preds:
                vals = p["inputs_from"](self.design, lc)
                got = REG.evaluate(p["formula_id"], {k: vals[r.split(".", 1)[1]] for k, r in p["inputs"].items()})
                self.assertTrue(math.isfinite(got["outputs"][p["output"]]["value"]), (lc["load_case_id"], p["quantity"]))

    def test_deck_load_per_rail_is_half_of_what_the_upper_frame_carries(self):
        from hiraku.contract.loading import on_board
        from hiraku.solvers.k0_handcalc.vehicle import mass_summary

        W = bed.deck_load_per_rail(self.design, "driver")
        total = mass_summary(on_board(self.design, "driver"))["mass_kg"] * G0
        base = sum(p["mass_kg"] for p in self.design["parts"] if p["kind"] == "caster") * G0
        self.assertLess(2 * W, total - base)  # the base frame and half the lift actuator are not on the rails
        self.assertGreater(2 * W, 1700.0)  # SWL is on them


class BedFormulasTest(unittest.TestCase):
    """K0 formulas of the care bed against closed-form answers."""

    TUBES = dict(b_r=(30, "mm"), h_r=(60, "mm"), t_r=(2.3, "mm"), b_g=(40, "mm"), h_g=(40, "mm"), t_g=(2.3, "mm"))

    def frame(self, b_p, t_p, a=0.0):
        return k0("bed.deck_frame_udl", W=(1000, "N"), L=(1500, "mm"), a1=(a, "mm"), a2=(a, "mm"), h=(250, "mm"),
                  E=(205000, "MPa"), b_p=(b_p, "mm"), t_p=(t_p, "mm"), **self.TUBES)

    def test_deck_frame_limits(self):
        w = 1000.0 / 1500.0
        stiff = self.frame(600.0, 100.0)  # posts far stiffer than the rail: the rail is fixed at both posts
        self.assertAlmostEqual(stiff["M_rail"] / (w * 1500.0**2 / 12.0), 1.0, delta=0.02)
        soft = self.frame(5.0, 0.5)  # posts that hardly bend: the rail is simply supported
        self.assertAlmostEqual(soft["M_rail"] / (w * 1500.0**2 / 8.0), 1.0, delta=0.01)
        self.assertAlmostEqual(soft["R_head"] + soft["R_foot"], 1000.0, places=9)
        I_r = (30 * 60**3 - 25.4 * 55.4**3) / 12.0
        self.assertAlmostEqual(soft["sigma_rail"], soft["M_rail"] * 30.0 / I_r, places=9)

    def test_deck_frame_overhang_only_hogs(self):
        w = 1000.0 / (1500.0 + 2 * 300.0)
        soft = self.frame(5.0, 0.5, a=300.0)
        span = w * 1500**2 / 8 - w * 300**2 / 2  # simply supported span with the overhangs' moments at its ends
        self.assertAlmostEqual(soft["M_rail"] / max(span, w * 300**2 / 2), 1.0, delta=0.01)

    def test_edge_load_tipping(self):
        got = k0("bed.edge_load_tipping", W=(1000, "N"), y_cg=(0, "mm"), F=(500, "N"), y_F=(600, "mm"), y_s=(400, "mm"))
        self.assertAlmostEqual(got["R_far"], (1000 * 400 - 500 * 200) / 800)
        self.assertAlmostEqual(got["R_near"], 1500 - got["R_far"])

    def test_lever_actuator_force(self):
        M0, r, a = 300_000.0, 120.0, 400.0
        got = k0("bed.lever_actuator_force", M0=(M0, "N*mm"), r=(r, "mm"), a_x=(a, "mm"), a_z=(r, "mm"),
                 theta_max=(70, "deg"))
        self.assertAlmostEqual(got["F_flat"], M0 / r, places=6)

        def closed(t):  # anchor level with the tip at the flat position (a_z = r)
            e = math.hypot(r * math.sin(t) + a, r - r * math.cos(t))
            return M0 * math.cos(t) * e / (r * (r * math.sin(t) + a * math.cos(t)))
        best = max(closed(math.radians(70.0 * i / 7000)) for i in range(7001))
        self.assertAlmostEqual(got["F_max"] / best, 1.0, delta=1e-4)
        t = math.radians(70.0)
        self.assertAlmostEqual(got["stroke"], math.hypot(r * math.sin(t) + a, r - r * math.cos(t)) - a, places=6)

    def test_side_rail_push_and_lug(self):
        d, t = 25.4, 1.6
        Z = math.pi * (d**4 - (d - 2 * t) ** 4) / (32 * d)
        got = k0("bed.side_rail_push", F=(600, "N"), H=(400, "mm"), S=(700, "mm"), n=(2, "1"), u2=(0, "mm"),
                 u3=(0, "mm"), d=(d, "mm"), t=(t, "mm"), s=(0, "mm"), t_s=(2.3, "mm"), g=(0, "mm"), t_g=(4.5, "mm"),
                 b_k=(200, "mm"), t_k=(20, "mm"), E=(205000, "MPa"), G=(1e-3, "MPa"), k_f1=(0, "N*mm"),
                 k_f2=(0, "N*mm"), k_f3=(0, "N*mm"), k_f4=(0, "N*mm"))  # next to no torsion: plain cantilevers
        self.assertAlmostEqual(got["sigma_post"], 300 * 400 / Z, places=6)
        self.assertAlmostEqual(got["sigma"], max(300 * 400, 600 * 700 / 4) / Z, places=6)  # the bar simply supported
        lug = k0("bed.lug_bending", F=(2000, "N"), h_p=(100, "mm"), w=(40, "mm"), t=(6, "mm"), d=(10, "mm"))
        self.assertAlmostEqual(lug["sigma_root"], 1000 * 100 / (6 * 40**2 / 6), places=9)
        self.assertAlmostEqual(lug["sigma_bearing"], 1000 / (10 * 6), places=9)

    def test_duty_and_damage(self):
        duty = k0("bed.actuator_duty", n=(6, "1"), k=(2, "1"), s=(120, "mm"), v=(0.006, "m/s"))
        self.assertAlmostEqual(duty["run_time"], 20.0)
        self.assertAlmostEqual(duty["duty"], 100 * 6 * 2 * 20 / 3600)
        sn = dict(FAT=(71, "MPa"), m=(3, "1"), N_knee=(1e7, "1"))
        for dS in (60.0, 20.0):  # above and below the knee (41.5 MPa); below it the spectrum's slope 2 m - 1 = 5
            one = k0("fatigue.constant_amplitude_damage", dS=(dS, "MPa"), n=(1e5, "1"), m2=(5, "1"), **sn)["D"]
            got = k0("bed.use_damage", n1=(1e5, "1"), dS1=(dS, "MPa"), n2=(0, "1"), dS2=(0, "MPa"), n3=(1e5, "1"),
                     dS3=(dS, "MPa"), n4=(5, "1"), dS4=(0, "MPa"), **sn)
            self.assertAlmostEqual(got["D"] / (2 * one), 1.0, places=9)
            self.assertAlmostEqual(got["D_worst"] / one, 1.0, places=9)  # frame (use 1) and knee lug (use 3) apart
            self.assertEqual(got["D4"], 0.0)
            self.assertEqual(got["m2"], 5.0)


if __name__ == "__main__":
    unittest.main()
