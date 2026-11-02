import copy
import importlib
import json
import sys
import unittest
from pathlib import Path

from hiraku import contract, solvers
from hiraku.contract.protocols import LOADINGS, SOLVER_ENTRY_POINTS, AnalysisTool
from hiraku.solvers.k0_handcalc.units import STANDARD_GRAVITY

ROOT = Path(__file__).resolve().parents[2]
DESIGN = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
PRED = [{"prediction_id": "pred-0001"}]
TOOLS = {t.name: t for t in solvers.builtin_tools()}


def load_case(lc_id, test_id, domain, **params):
    return {"load_case_id": lc_id, "test_id": test_id, "domain": domain, "title": lc_id,
            "params": params, "tolerance_rel": 0.15}


def q(v, u):
    return {"value": v, "unit": u}


def results(rec):
    return {r["quantity"]: r for r in rec["results"]}


class EntryPointTest(unittest.TestCase):
    def test_entry_points(self):
        for name in SOLVER_ENTRY_POINTS:
            self.assertTrue(callable(getattr(solvers, name)), name)
        self.assertIn("frame_static", TOOLS)
        self.assertIn("step_climb", TOOLS)
        self.assertIs(solvers.formula_registry(), solvers.formula_registry())
        self.assertEqual([t.name for t in solvers.builtin_tools()], list(TOOLS))

    def test_import_has_no_side_effects(self):
        saved = {k: v for k, v in sys.modules.items() if k == "hiraku.solvers" or k.startswith("hiraku.solvers.")}
        for k in saved:
            del sys.modules[k]
        try:
            importlib.import_module("hiraku.solvers")
            loaded = [k for k in sys.modules if k.startswith("hiraku.solvers.")]
            self.assertEqual(loaded, [])
        finally:
            for k in [k for k in sys.modules if k == "hiraku.solvers" or k.startswith("hiraku.solvers.")]:
                del sys.modules[k]
            sys.modules.update(saved)

    def test_specs_are_usable_and_fit_the_contract(self):
        formula_ids = {f["formula_id"] for f in solvers.formula_registry().list_formulas()}
        for t in TOOLS.values():
            self.assertIsInstance(t, AnalysisTool)
            checks = t.check()
            spec = t.spec()
            contract.validate("tool_spec", spec)
            self.assertEqual([c for c in checks if not c["pass"]], [], t.name)
            self.assertTrue(spec["usable"])
            self.assertGreaterEqual(len(checks), 5)
            self.assertTrue(set(spec["formulas"]) <= formula_ids)


class StepClimbTest(unittest.TestCase):
    tool = TOOLS["step_climb"]

    def run_case(self, design=DESIGN, **params):
        rec = self.tool.run(design, load_case("P1-step", "P1", "powertrain", **params), PRED, {})
        contract.validate("analysis", dict(rec, analysis_id="ana-0001"))
        return rec

    def test_demo_vehicle_100mm(self):
        rec = self.run_case(step_height=q(100, "mm"))
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        r = results(rec)
        self.assertAlmostEqual(r["force_ratio"]["value"], 1.118034, places=5)
        self.assertAlmostEqual(r["front_wheel_force"]["value"], r["force_ratio"]["value"] * r["front_wheel_load"]["value"], places=6)
        lead = r["front_wheel_force"]["where"]
        self.assertEqual(r["front_wheel_load"]["value"], r[f"wheel_load_{lead}"]["value"])
        total = sum(r[f"wheel_load_{s}"]["value"] for s in ("FL", "FR", "RL", "RR"))
        self.assertAlmostEqual(total, r["vehicle_mass"]["value"] * STANDARD_GRAVITY, places=6)
        for name in ("max_stress", "min_safety_factor", "max_displacement", "push_traction", "required_mu_push",
                     "self_climb_torque", "front_wheel_load_static"):
            self.assertIn(name, r)
        self.assertTrue(any(v["kind"] == "grip" for v in rec["vectors"]))
        self.assertEqual({f["kind"] for f in rec["fields"]}, {"stress", "displacement"})

    def test_pushing_unloads_the_front(self):
        r = results(self.run_case(step_height=q(100, "mm")))
        self.assertLess(r["front_wheel_load"]["value"], r["front_wheel_load_static"]["value"])

    def test_pushed_formula_predicts_the_climbing_load(self):
        """The static lever rule misses the push couple; step_climb.pushed_wheel_load includes it."""
        from hiraku.solvers.k0_handcalc.vehicle import mass_summary
        s = mass_summary(DESIGN)
        ins = {"m": q(s["mass_kg"], "kg"), "a": q(-s["cg_mm"][0], "mm"), "L": q(1700, "mm"), "n": q(2, "1")}
        F = solvers.formula_registry()
        static = F.evaluate("mass_props.front_wheel_load", ins)["outputs"]["W"]["value"]
        pushed = F.evaluate("step_climb.pushed_wheel_load", dict(ins, r=q(300, "mm"), h=q(100, "mm")))["outputs"]
        got = results(self.run_case(step_height=q(100, "mm")))
        for name, pred in (("front_wheel_load", pushed["W"]["value"]), ("front_wheel_force", pushed["F"]["value"])):
            self.assertLess(abs(got[name]["value"] - pred) / pred, 0.03, name)
        self.assertGreater(abs(got["front_wheel_load"]["value"] - static) / static, 0.15)

    def test_rear_wheels_on_the_step_are_named_step(self):
        d = copy.deepcopy(DESIGN)
        for s in d["supports"]:
            s["actuation"]["drive"] = s["id"] in ("FL", "FR")
        r = results(self.run_case(design=d, step_height=q(100, "mm"), step_supports="RL,RR"))
        self.assertIn("step_wheel_force", r)
        self.assertNotIn("front_wheel_force", r)

    def test_loadings(self):
        masses = {}
        for loading in LOADINGS:
            rec = self.run_case(step_height=q(100, "mm"), loading=loading)
            self.assertEqual(rec["status"], "ok", rec.get("error"))
            masses[loading] = results(rec)["vehicle_mass"]["value"]
        self.assertAlmostEqual(masses["two_cargo"] - masses["two"], 150.0, places=9)
        self.assertAlmostEqual(masses["two"] - masses["driver"], 76.6, places=9)
        self.assertAlmostEqual(masses["driver"] - masses["empty"], 76.6, places=9)
        default = results(self.run_case(step_height=q(100, "mm")))["vehicle_mass"]["value"]
        self.assertEqual(default, masses["two_cargo"])
        rec = self.run_case(step_height=q(100, "mm"), loading="three")
        self.assertEqual(rec["status"], "failed")
        self.assertIn("three", rec["error"])

    def test_driver_sits_on_the_right(self):
        r = results(self.run_case(step_height=q(100, "mm"), loading="driver"))
        self.assertGreater(r["wheel_load_FR"]["value"], r["wheel_load_FL"]["value"])

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
        self.assertEqual(self.run_case(step_height=q(100, "kg"))["status"], "failed")
        self.assertEqual(self.run_case(step_height=q(100, "mm"), step_supports="XX")["status"], "failed")

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
    tool = TOOLS["frame_static"]

    def run_case(self, params=None, **lc_params):
        rec = self.tool.run(DESIGN, load_case("R1", "R1", "strength", **lc_params), PRED, params or {})
        contract.validate("analysis", dict(rec, analysis_id="ana-0002"))
        return rec

    def test_rest_and_g_factor_scale(self):
        a, b, c = self.run_case(), self.run_case(g_factor=q(2, "g")), self.run_case(g_factor=q(2, "1"))
        for rec in (a, b, c):
            self.assertEqual(rec["status"], "ok", rec.get("error"))
        sa, sb, sc = (results(r)["max_stress"]["value"] for r in (a, b, c))
        self.assertAlmostEqual(sb, 2 * sa, delta=sa * 1e-9)
        self.assertAlmostEqual(sc, sb, delta=sa * 1e-9)

    def test_empty_vehicle(self):
        full = results(self.run_case())["vehicle_mass"]["value"]
        empty = results(self.run_case(loading="empty"))["vehicle_mass"]["value"]
        self.assertAlmostEqual(full - empty, 2 * 76.6 + 150.0, places=9)

    def test_step_through_frame_static(self):
        rec = self.run_case(step_height=q(100, "mm"))
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        step = TOOLS["step_climb"].run(DESIGN, load_case("P1-step100", "P1", "powertrain", step_height=q(100, "mm")), PRED, {})
        self.assertAlmostEqual(results(rec)["max_stress"]["value"], results(step)["max_stress"]["value"], places=9)

    def test_upstream_step_forces_are_used(self):
        p1 = TOOLS["step_climb"].run(DESIGN, load_case("P1-step100", "P1", "powertrain", step_height=q(100, "mm")), PRED, {})
        p1["analysis_id"] = "ana-0001"
        rec = self.run_case({"upstream_analyses": {"P1-step100": p1}})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        self.assertAlmostEqual(results(rec)["max_stress"]["value"], results(p1)["max_stress"]["value"], places=6)
        for s in ("FL", "FR", "RL", "RR"):
            self.assertAlmostEqual(results(rec)[f"wheel_load_{s}"]["value"], results(p1)[f"wheel_load_{s}"]["value"], places=6)

    def test_upstream_from_another_loading_is_refused(self):
        p1 = TOOLS["step_climb"].run(DESIGN, load_case("P1-step100", "P1", "powertrain", step_height=q(100, "mm")), PRED, {})
        rec = self.run_case({"upstream_analyses": {"P1-step100": p1}}, loading="empty")
        self.assertEqual(rec["status"], "failed")
        self.assertIn("do not balance", rec["error"])

    def test_upstream_without_wheel_forces_is_ignored(self):
        rec = self.run_case({"upstream_analyses": {"X": {"status": "ok", "vectors": []}}})
        self.assertEqual(rec["status"], "ok")
        self.assertEqual(results(rec)["max_stress"]["value"], results(self.run_case())["max_stress"]["value"])


if __name__ == "__main__":
    unittest.main()
