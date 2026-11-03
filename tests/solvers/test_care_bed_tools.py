"""The care bed's tools on the shelf (bed_static, actuator_linkage): their known answers, and the care bed's load
cases (hiraku.catalog.builders.product_care_bed) run end to end: propose -> load cases -> K0 prediction -> tool,
the tool's value within the load case's tolerance of the K0 value."""

import time
import unittest

from hiraku import contract, designgen, solvers
from hiraku.catalog import load_catalog
from hiraku.catalog.build import build_load_cases
from hiraku.catalog.builders import product_care_bed as bed

from tests.solvers.test_designgen_care_bed import bed_requirements

REG = solvers.formula_registry()
TOOLS = {t.name: t for t in solvers.builtin_tools()}
PRED = [{"prediction_id": "pred-0001"}]


def results(rec):
    return {r["quantity"]: r for r in rec["results"]}


def predict(design, lc, p):
    vals = p["inputs_from"](design, lc)
    out = REG.evaluate(p["formula_id"], {k: vals[r.split(".", 1)[1]] for k, r in p["inputs"].items()})
    return out["outputs"][p["output"]]


class BedToolKnownAnswersTest(unittest.TestCase):
    def test_on_the_shelf_and_usable(self):
        for name in ("bed_static", "actuator_linkage"):
            t = TOOLS[name]
            contract.validate("tool_spec", t.spec())
            self.assertTrue(t.spec()["usable"], name)

    def test_known_answers_pass(self):
        for name in ("bed_static", "actuator_linkage"):
            checks = TOOLS[name].known_answers()
            self.assertGreaterEqual(len(checks), 3)
            self.assertEqual([c for c in checks if not c["pass"]], [], name)


class BedEndToEndTest(unittest.TestCase):
    """propose -> build_load_cases -> the scripted step's K0 prediction -> the step's tool."""

    @classmethod
    def setUpClass(cls):
        t0 = time.perf_counter()
        cls.req = bed_requirements()
        cls.design = designgen.propose("care_bed", {}, {}, cls.req)
        cls.cases = {lc["load_case_id"]: lc for lc in build_load_cases(load_catalog(), cls.req, cls.design,
                                                                       ["S1", "R5", "P5", "T3", "R3"])}
        cls.runs = {}
        for lc_id, lc in cls.cases.items():
            step = bed.step_for(lc)
            if step is None:
                continue
            rec = TOOLS[step["tool"]].run(cls.design, lc, PRED, {})
            cls.runs[lc_id] = (step, rec)
        print(f"\ncare bed end to end: {len(cls.runs)} load cases through the tools in {time.perf_counter() - t0:.1f} s")

    def test_every_step_runs_and_fits_the_contract(self):
        self.assertEqual(set(self.runs), {"S1-edge1350N", "R5-deck", "R5-side-rail", "P5-back", "P5-knee"})
        for lc_id, (step, rec) in self.runs.items():
            self.assertEqual(rec["status"], "ok", f"{lc_id}: {rec.get('error')}")
            contract.validate("analysis", dict(rec, analysis_id="ana-0001"))
            self.assertIn(self.cases[lc_id]["criterion"]["quantity"], results(rec), lc_id)

    def test_tool_agrees_with_k0_within_the_tolerance(self):
        for lc_id, (step, rec) in self.runs.items():
            lc = self.cases[lc_id]
            r = results(rec)
            for p in step["predictions"]:
                k0 = predict(self.design, lc, p)
                got = r[p["quantity"]]
                self.assertEqual(got["unit"], k0["unit"], (lc_id, p["quantity"]))
                tol = lc["tolerance_rel"]
                scale = max(abs(k0["value"]), abs(got["value"]))
                if lc["test_id"] == "S1":  # the far side's load: a difference of large moments, compared to W
                    scale = results(rec)["total_mass"]["value"] * 9.80665
                err = abs(got["value"] - k0["value"]) / scale
                print(f"{lc_id} {p['quantity']}: K0 {k0['value']:.4g} {k0['unit']}, tool {got['value']:.4g} "
                      f"{got['unit']}, diff {err:.1%} (tolerance {tol:.0%})")
                self.assertLessEqual(err, tol, (lc_id, p["quantity"], k0["value"], got["value"]))

    def test_edge_load_balances(self):
        _, rec = self.runs["S1-edge1350N"]
        r = results(rec)
        W = r["total_mass"]["value"] * 9.80665
        wheels = sum(v["value"] for k, v in r.items() if k.startswith("wheel_load_"))
        self.assertAlmostEqual(wheels, W + 1350.0, delta=1e-6 * W)
        self.assertGreater(r["lighter_side_load"]["value"], 0.0)  # the default bed stands

    def test_side_rail_push_takes_the_rail(self):
        _, rec = self.runs["R5-side-rail"]
        self.assertTrue(results(rec)["max_stress"]["where"].startswith(("side_rail_L", "socket_")),
                        results(rec)["max_stress"]["where"])

    def test_actuator_forces_over_the_raise(self):
        for lc_id in ("P5-back", "P5-knee"):
            rec = self.runs[lc_id][1]
            r = results(rec)
            print(f"{lc_id}: largest force {r['actuator_force']['value']:.4g} N at "
                  f"{r['angle_at_max_force']['value']:.1f} deg, flat {r['actuator_force_flat']['value']:.4g} N")
            self.assertGreater(r["actuator_force"]["value"], 0.0)
            self.assertGreaterEqual(r["actuator_force"]["value"], r["actuator_force_flat"]["value"])
            s = rec["series"][0]
            self.assertEqual(len(s["x"]["values"]), len(s["values"]))
            self.assertAlmostEqual(max(s["values"]), r["actuator_force"]["value"], delta=0.05 * r["actuator_force"]["value"])


class BedToolFailuresTest(unittest.TestCase):
    def test_actuator_linkage_on_a_vehicle_names_what_is_missing(self):
        lc = {"load_case_id": "P5-back", "test_id": "P5", "domain": "powertrain", "title": "x",
              "params": {"actuator": "back"}, "tolerance_rel": 0.2}
        rec = TOOLS["actuator_linkage"].run(designgen.ladder_4w.build(), lc, PRED, {})
        self.assertEqual(rec["status"], "failed")
        self.assertIn("care bed", rec["error"])

    def test_bed_static_bad_node(self):
        lc = {"load_case_id": "R5-x", "test_id": "R5", "domain": "strength", "title": "x",
              "params": {"loading": "empty", "load_node": "NOPE", "load_z": {"value": -100.0, "unit": "N"}}, "tolerance_rel": 0.2}
        rec = TOOLS["bed_static"].run(designgen.care_bed.build(), lc, PRED, {})
        self.assertEqual(rec["status"], "failed")
        self.assertIn("NOPE", rec["error"])


if __name__ == "__main__":
    unittest.main()
