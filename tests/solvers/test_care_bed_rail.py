"""The care bed, fourth round: the side rail's post count, gusset and socket wall (designgen.care_bed) read by
bed.side_rail_push and bed_static; P5 feeding T3 (actuator_duty takes the upstream actuator_linkage stroke); R3
compared on stress_range when both damages are next to 0."""

import math
import unittest

from hiraku import contract, designgen, solvers
from hiraku.catalog import load_catalog
from hiraku.catalog.build import build_load_cases, feeds_map
from hiraku.catalog.builders import product_care_bed as bed
from hiraku.designgen import care_bed
from hiraku.solvers.k0_handcalc.library_bed import GUSSET_STEPS, rail_post_steps

from tests.solvers.test_designgen_care_bed import bed_requirements

REG = solvers.formula_registry()
TOOLS = {t.name: t for t in solvers.builtin_tools()}
PRED = [{"prediction_id": "pred-0001"}]
V2 = {"side_rail_height": 250.0, "side_rail_d": 34.0, "side_rail_t": 2.3}  # the eighth run's v2 (ranges' ends)
LIMIT = 215.0 / 4.0  # STKM13A / Q61


def results(rec):
    return {r["quantity"]: r for r in rec["results"]}


def k0_of(design, lc, p):
    vals = p["inputs_from"](design, lc)
    return REG.evaluate(p["formula_id"], {k: vals[r.split(".", 1)[1]] for k, r in p["inputs"].items()})["outputs"]


def rail_formula(**kw):
    base = dict(F=600, H=400, S=700, n=2, u2=0, u3=0, d=25.4, t=1.6, s=0, t_s=2.3, g=0, t_g=4.5, b_k=200, t_k=20,
                E=205000, G=1e-3, k_f1=0, k_f2=0, k_f3=0, k_f4=0)
    base.update(kw)
    units = {"n": "1", "E": "MPa", "G": "MPa", "F": "N", **{f"k_f{i}": "N*mm" for i in range(1, 5)}}
    got = REG.evaluate("bed.side_rail_push", {k: {"value": float(v), "unit": units.get(k, "mm")} for k, v in base.items()})
    return {k: o["value"] for k, o in got["outputs"].items()}


class RailVariablesTest(unittest.TestCase):
    def test_revisable_with_range_and_default(self):
        for name, lo, hi, default in (("side_rail_posts", 2.0, 4.0, 2.0), ("side_rail_gusset_leg", 0.0, 150.0, 0.0),
                                      ("side_rail_gusset_t", 3.2, 9.0, 4.5), ("side_rail_socket_t", 1.6, 4.5, 1.6)):
            v = care_bed.VARIABLES[name]
            self.assertTrue(v.revisable, name)
            self.assertEqual((v.lo, v.hi, v.default), (lo, hi, default), name)

    def test_default_rail_is_unchanged(self):
        d = care_bed.build()
        self.assertEqual(sorted(m["id"] for m in d["members"] if m.get("part") == "side_rail_L"),
                         ["side_rail_L_postK", "side_rail_L_postP1", "side_rail_L_topK", "side_rail_L_topP1"])
        self.assertEqual(d["sections"][next(m for m in d["members"] if m["id"] == "socket_LP1")["section"]]["dims_mm"],
                         {"b": 25.4, "h": 25.4, "t": 1.6})

    def test_posts(self):
        for n, stations in ((3, {"P1", "KA", "K"}), (4, {"P1", "KA", "B", "K"})):
            d = care_bed.build({"side_rail_posts": float(n)})
            contract.validate("design", d)
            socks = {m["id"][len("socket_L"):] for m in d["members"] if m["id"].startswith("socket_L")}
            self.assertEqual(socks, stations, n)
            rail = next(p for p in d["parts"] if p["id"] == "side_rail_L")
            inserts = {c["to"] for c in rail["connects_to"] if c["via"] == "insert"}
            self.assertEqual(inserts, {f"L{s}" for s in stations})
            from hiraku.solvers.k4_geometry import interferences

            self.assertEqual(interferences(d), [], n)
        with self.assertRaisesRegex(designgen.DesignGenError, "whole number"):
            care_bed.build({"side_rail_posts": 2.5})

    def test_gusset_steps_and_sections(self):
        d = care_bed.build({"side_rail_gusset_leg": 120.0, "side_rail_gusset_t": 6.0})
        contract.validate("design", d)
        gus = sorted(m["id"] for m in d["members"] if m["id"].startswith("side_rail_L_gussetP1"))
        self.assertEqual(len(gus), GUSSET_STEPS)
        sec = d["sections"][next(m for m in d["members"] if m["id"] == "side_rail_L_gussetP1_1")["section"]]
        self.assertEqual(sec["shape"], "custom")
        D, t, tg = 25.4, 1.6, 6.0
        h = D * (1 - 15.0 / 120.0)  # the first step's middle (15 mm up): 7/8 of the root depth
        I = math.pi / 64 * (D**4 - (D - 2 * t) ** 4) + 2 * tg * h**3 / 12
        self.assertAlmostEqual(sec["Iz_mm4"], I, places=6)
        self.assertAlmostEqual(sec["A_mm2"], math.pi / 4 * (D**2 - (D - 2 * t) ** 2) + 2 * tg * h, places=9)
        steps = rail_post_steps(400.0, D, t, 0.0, 2.3, 120.0, tg)
        self.assertEqual([round(s["z1"], 9) for s in steps], [30.0, 60.0, 90.0, 120.0, 400.0])


class RailFormulaTest(unittest.TestCase):
    def test_two_posts_share_half(self):
        got = rail_formula()
        self.assertAlmostEqual(got["R_max"], 300.0, places=6)
        bk, tk = 200.0, 20.0
        Zk = (bk**4 - (bk - 2 * tk) ** 4) / 12 / (bk / 2)
        self.assertAlmostEqual(got["sigma_socket"], 300 / (bk**2 - (bk - 2 * tk) ** 2) + 300 * 400 / Zk, places=4)

    def test_foot_spring_leaves_two_posts_statically_determinate(self):
        self.assertAlmostEqual(rail_formula(k_f1=5e7, k_f2=5e7)["sigma_post"], rail_formula()["sigma_post"], places=4)

    def test_more_posts_take_less_each(self):
        two = rail_formula(G=78846)
        three = rail_formula(G=78846, n=3, u2=295)
        four = rail_formula(G=78846, n=4, u2=295, u3=495)
        self.assertLess(three["R_max"], two["R_max"])
        self.assertLess(four["sigma_post"], two["sigma_post"])
        self.assertLessEqual(three["R_max"], 600.0)

    def test_gusset_lowers_the_post(self):
        self.assertLess(rail_formula(g=120, t_g=6)["sigma_post"], rail_formula()["sigma_post"])

    def test_refusals(self):
        for bad in ({"n": 2.5}, {"n": 3, "u2": 0}, {"n": 4, "u2": 400, "u3": 300}, {"g": 400}):
            with self.assertRaises(ValueError, msg=bad):
                rail_formula(**bad)


class RailV2Test(unittest.TestCase):
    """The eighth run's v2 (height 250, d 34, t 2.3: the ranges' ends) with the new variables, K0 against bed_static."""

    CASES = {"v2": {}, "socket 3.2": {"side_rail_socket_t": 3.2}, "3 posts": {"side_rail_posts": 3.0},
             "gusset 150 x 6": {"side_rail_gusset_leg": 150.0, "side_rail_gusset_t": 6.0},
             "3 posts, socket 3.2, gusset 75": {"side_rail_posts": 3.0, "side_rail_socket_t": 3.2,
                                                "side_rail_gusset_leg": 75.0}}

    @classmethod
    def setUpClass(cls):
        cls.req = bed_requirements()
        cls.got = {}
        for name, extra in cls.CASES.items():
            d = designgen.propose("care_bed", {}, {**V2, **extra}, cls.req)
            lc = next(c for c in build_load_cases(load_catalog(), cls.req, d, ["R5"]) if c["load_case_id"] == "R5-side-rail")
            k0 = k0_of(d, lc, bed.predictions_for(lc)[0])["sigma"]["value"]
            rec = TOOLS["bed_static"].run(d, lc, PRED, {})
            cls.got[name] = (k0, results(rec)["max_stress"], lc["tolerance_rel"])
            print(f"\nR5-side-rail {name}: K0 {k0:.2f} MPa, bed_static {cls.got[name][1]['value']:.2f} MPa "
                  f"({cls.got[name][1]['where']})")

    def test_k0_and_tool_agree(self):
        for name, (k0, tool, tol) in self.got.items():
            self.assertLessEqual(abs(tool["value"] - k0) / max(tool["value"], k0), tol, name)

    def test_v2_peak_is_the_socket_and_the_variables_bring_it_under_the_limit(self):
        self.assertTrue(self.got["v2"][1]["where"].startswith("socket_"))
        self.assertGreater(self.got["v2"][1]["value"], LIMIT)
        self.assertLess(self.got["3 posts, socket 3.2, gusset 75"][1]["value"], LIMIT)


class FeedsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.req = bed_requirements()
        cls.design = designgen.propose("care_bed", {}, {}, cls.req)
        cls.cases = {lc["load_case_id"]: lc for lc in build_load_cases(load_catalog(), cls.req, cls.design, ["P5", "T3"])}
        cls.p5 = TOOLS["actuator_linkage"].run(cls.design, cls.cases["P5-back"], PRED, {})

    def test_p5_feeds_t3(self):
        self.assertIn("T3", feeds_map()["P5"])
        self.assertEqual(self.cases["P5-back"]["feeds"], ["T3-6uses"])
        for lc in self.cases.values():
            contract.validate("load_case", lc)

    def test_duty_takes_the_upstream_stroke(self):
        own = results(TOOLS["actuator_duty"].run(self.design, self.cases["T3-6uses"], PRED, {}))
        rec = TOOLS["actuator_duty"].run(self.design, self.cases["T3-6uses"], PRED,
                                         {"upstream_analyses": {"P5-back": self.p5}})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        r = results(rec)
        self.assertAlmostEqual(r["actuator_stroke"]["value"], results(self.p5)["actuator_stroke"]["value"], places=9)
        self.assertAlmostEqual(r["actuator_duty"]["value"], own["actuator_duty"]["value"], places=9)
        self.assertTrue(any("P5-back" in e["name"] for e in rec["effects"]))
        # a different upstream stroke is the one used; the knee's stroke is not the back's
        fake = dict(self.p5, results=[dict(x, value=100.0) if x["quantity"] == "actuator_stroke" else x
                                      for x in self.p5["results"]])
        r = results(TOOLS["actuator_duty"].run(self.design, self.cases["T3-6uses"], PRED,
                                               {"upstream_analyses": {"P5-back": fake}}))
        v = self.design["variables"]["actuator_speed"]["value"]
        self.assertAlmostEqual(r["actuator_duty"]["value"], 100 * 6 * 2 * 0.1 / v / 3600, places=9)
        knee = TOOLS["actuator_linkage"].run(self.design, self.cases["P5-knee"], PRED, {})
        r = results(TOOLS["actuator_duty"].run(self.design, self.cases["T3-6uses"], PRED,
                                               {"upstream_analyses": {"P5-knee": knee}}))
        self.assertAlmostEqual(r["actuator_stroke"]["value"], own["actuator_stroke"]["value"], places=9)


class R3StressRangeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.req = bed_requirements()
        cls.design = designgen.propose("care_bed", {}, {}, cls.req)
        cls.lc = next(c for c in build_load_cases(load_catalog(), cls.req, cls.design, ["R3"]))

    def check(self, lc):
        rec = TOOLS["use_fatigue"].run(self.design, lc, PRED, {})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        r = results(rec)
        preds = {p["quantity"]: p for p in bed.predictions_for(lc)}
        self.assertEqual(set(preds), {"fatigue_damage", "stress_range"})
        k0 = {q: k0_of(self.design, lc, p)[p["output"]]["value"] for q, p in preds.items()}
        q = bed.r3_compared_quantity(k0["fatigue_damage"], r["fatigue_damage"]["value"])
        err = abs(r[q]["value"] - k0[q]) / max(abs(r[q]["value"]), abs(k0[q]))
        self.assertLessEqual(err, lc["tolerance_rel"], q)
        self.assertEqual(r["stress_range"]["value"], r[f"stress_range_{'lug_back'}"]["value"])
        return q, k0, r

    def test_damage_compared_when_it_is_not_tiny(self):
        q, k0, r = self.check(self.lc)
        self.assertEqual(q, "fatigue_damage")
        self.assertAlmostEqual(k0["stress_range"], r["stress_range"]["value"], places=6)

    def test_stress_range_compared_when_both_damages_are_next_to_0(self):
        tiny = dict(self.lc, params={**self.lc["params"], "n_seat": {"value": 1e-9, "unit": "1"},
                                     "n_raise": {"value": 1e-9, "unit": "1"}, "n_lift": {"value": 0.0, "unit": "1"}})
        q, k0, r = self.check(tiny)
        self.assertEqual(q, "stress_range")
        self.assertLess(r["fatigue_damage"]["value"], bed.R3_TINY_DAMAGE)
        self.assertEqual(bed.r3_compared_quantity(1e-13, 2e-14), "stress_range")
        self.assertEqual(bed.r3_compared_quantity(1e-13, 2e-9), "fatigue_damage")


if __name__ == "__main__":
    unittest.main()
