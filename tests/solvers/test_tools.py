import copy
import json
import unittest
from pathlib import Path

from hiraku import contract, solvers
from hiraku.contract.protocols import AnalysisTool
from hiraku.solvers.k0_handcalc import registry as formula_registry
from hiraku.solvers.k0_handcalc.units import STANDARD_GRAVITY

ROOT = Path(__file__).resolve().parents[2]
DESIGN = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
PRED = [{"prediction_id": "pred-0001"}]


def load_case(lc_id, test_id, domain, **params):
    return {"load_case_id": lc_id, "test_id": test_id, "domain": domain, "title": lc_id,
            "params": params, "tolerance_rel": 0.15}


def q(v, u):
    return {"value": v, "unit": u}


def results(rec):
    return {r["quantity"]: r for r in rec["results"]}


class ShelfTest(unittest.TestCase):
    def test_entry_point(self):
        shelf = solvers.registry()
        self.assertIn("frame_static", shelf.tools)
        self.assertIn("step_climb", shelf.tools)
        self.assertIs(shelf.formulas, formula_registry())
        with self.assertRaisesRegex(ValueError, "unknown tool"):
            shelf.tool("rollbar")

    def test_specs_are_usable_and_fit_the_contract(self):
        formula_ids = {f["formula_id"] for f in formula_registry().list_formulas()}
        for spec in solvers.registry().list_tools():
            contract.validate("tool_spec", spec)
            failed = [c for c in spec["checks"] if not c["pass"]]
            self.assertEqual(failed, [], spec["name"])
            self.assertTrue(spec["usable"])
            self.assertGreaterEqual(len(spec["checks"]), 5)
            self.assertTrue(set(spec["formulas"]) <= formula_ids)
        for t in solvers.registry().tools.values():
            self.assertIsInstance(t, AnalysisTool)


class StepClimbTest(unittest.TestCase):
    tool = solvers.registry().tool("step_climb")

    def run_case(self, design=DESIGN, **params):
        rec = self.tool.run(design, load_case("P1-step", "P1", "powertrain", **params), PRED, {})
        contract.validate("analysis", dict(rec, analysis_id="ana-0001"))
        return rec

    def test_demo_vehicle_100mm(self):
        rec = self.run_case(step_height=q(100, "mm"))
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        r = results(rec)
        self.assertAlmostEqual(r["force_ratio"]["value"], 1.118034, places=5)
        lead = r["wheel_force"]["where"]
        self.assertAlmostEqual(r["wheel_force"]["value"], r["force_ratio"]["value"] * r[f"wheel_load_{lead}"]["value"], places=6)
        total = sum(r[f"wheel_load_{s}"]["value"] for s in ("FL", "FR", "RL", "RR"))
        self.assertAlmostEqual(total, r["vehicle_mass"]["value"] * STANDARD_GRAVITY, places=6)
        for name in ("max_stress", "min_safety_factor", "max_displacement", "push_traction", "required_mu_push", "self_climb_torque"):
            self.assertIn(name, r)
        self.assertTrue(any(v["kind"] == "grip" for v in rec["vectors"]))
        self.assertEqual({f["kind"] for f in rec["fields"]}, {"stress", "displacement"})

    def test_pushing_unloads_the_front(self):
        rest = solvers.registry().tool("frame_static").run(DESIGN, load_case("R1-rest", "R1", "strength"), PRED, {})
        step = self.run_case(step_height=q(100, "mm"))
        front_rest = sum(results(rest)[f"wheel_load_{s}"]["value"] for s in ("FL", "FR"))
        front_step = sum(results(step)[f"wheel_load_{s}"]["value"] for s in ("FL", "FR"))
        self.assertLess(front_step, front_rest)

    def test_too_high_step_fails_with_reason(self):
        rec = self.run_case(step_height=q(300, "mm"))
        self.assertEqual(rec["status"], "failed")
        self.assertIn("wheel radius", rec["error"])
        self.assertEqual(rec["results"], [])

    def test_no_pusher_fails_with_reason(self):
        d = copy.deepcopy(DESIGN)
        for s in d["supports"]:
            s["actuation"]["drive"] = s["id"] in ("FL", "FR")
        rec = self.run_case(design=d, step_height=q(100, "mm"))
        self.assertEqual(rec["status"], "failed")
        self.assertIn("friction coefficient", rec["error"])

    def test_bad_load_case(self):
        rec = self.run_case()
        self.assertEqual(rec["status"], "failed")
        self.assertIn("step_height", rec["error"])
        rec = self.run_case(step_height=q(100, "kg"))
        self.assertEqual(rec["status"], "failed")
        rec = self.run_case(step_height=q(100, "mm"), step_supports="XX")
        self.assertEqual(rec["status"], "failed")

    def test_run_needs_a_prediction(self):
        with self.assertRaisesRegex(ValueError, "prediction"):
            self.tool.run(DESIGN, load_case("P1-step", "P1", "powertrain", step_height=q(100, "mm")), [], {})

    def test_bad_design_is_failed_not_raised(self):
        d = copy.deepcopy(DESIGN)
        d["sections"]["rt40x80x2.3"]["dims_mm"]["t"] = 30.0
        rec = self.run_case(design=d, step_height=q(100, "mm"))
        self.assertEqual(rec["status"], "failed")
        self.assertIn("rt40x80x2.3", rec["error"])


class FrameStaticTest(unittest.TestCase):
    tool = solvers.registry().tool("frame_static")

    def test_rest_and_g_factor_scale(self):
        a = self.tool.run(DESIGN, load_case("R1-1g", "R1", "strength"), PRED, {})
        b = self.tool.run(DESIGN, load_case("R1-2g", "R1", "strength", g_factor=q(2, "g")), PRED, {})
        c = self.tool.run(DESIGN, load_case("R1-2", "R1", "strength", g_factor=q(2, "1")), PRED, {})
        for rec in (a, b, c):
            contract.validate("analysis", dict(rec, analysis_id="ana-0002"))
            self.assertEqual(rec["status"], "ok", rec.get("error"))
        sa, sb, sc = (results(r)["max_stress"]["value"] for r in (a, b, c))
        self.assertAlmostEqual(sb, 2 * sa, delta=sa * 1e-9)
        self.assertAlmostEqual(sc, sb, delta=sa * 1e-9)

    def test_empty_vehicle(self):
        full = self.tool.run(DESIGN, load_case("R1", "R1", "strength"), PRED, {})
        empty = self.tool.run(DESIGN, load_case("R1-empty", "R1", "strength",
                                                exclude_parts="occupant_L,occupant_R,cargo"), PRED, {})
        m_full = results(full)["vehicle_mass"]["value"]
        m_empty = results(empty)["vehicle_mass"]["value"]
        self.assertAlmostEqual(m_full - m_empty, 2 * 76.6 + 150.0, places=9)
        bad = self.tool.run(DESIGN, load_case("R1", "R1", "strength", exclude_parts="ghost"), PRED, {})
        self.assertEqual(bad["status"], "failed")

    def test_step_through_frame_static(self):
        rec = self.tool.run(DESIGN, load_case("R1-step100", "R1", "strength", step_height=q(100, "mm")), PRED, {})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        step = solvers.registry().tool("step_climb").run(
            DESIGN, load_case("P1-step100", "P1", "powertrain", step_height=q(100, "mm")), PRED, {})
        self.assertAlmostEqual(results(rec)["max_stress"]["value"], results(step)["max_stress"]["value"], places=9)


if __name__ == "__main__":
    unittest.main()
