import copy
import importlib
import json
import sys
import unittest
from pathlib import Path

import numpy as np

from hiraku import contract, solvers
from hiraku.contract.protocols import LOADINGS, SOLVER_ENTRY_POINTS, AnalysisTool
from hiraku.solvers.k0_handcalc.units import STANDARD_GRAVITY

ROOT = Path(__file__).resolve().parents[2]
DESIGN = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
PRED = [{"prediction_id": "pred-0001"}]
TOOLS = {t.name: t for t in solvers.builtin_tools() if t.__class__.__module__.startswith("hiraku.solvers.shelf")}


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
        self.assertTrue(set(TOOLS) <= {t.name for t in solvers.builtin_tools()})

    def test_tool_names_are_unique(self):
        """One name, one tool on the shelf (K2's tools included): nothing replaces anything."""
        names = [t.name for t in solvers.builtin_tools()]
        self.assertEqual(sorted(n for n in set(names) if names.count(n) > 1), [])

    def test_import_has_no_side_effects(self):
        import hiraku

        saved = {k: v for k, v in sys.modules.items() if k == "hiraku.solvers" or k.startswith("hiraku.solvers.")}
        saved_attr = hiraku.__dict__.get("solvers")  # importing the package again rebinds hiraku.solvers
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
            if saved_attr is None:
                hiraku.__dict__.pop("solvers", None)
            else:
                hiraku.solvers = saved_attr
        from hiraku import solvers as again

        self.assertIs(again, sys.modules["hiraku.solvers"])

    def test_specs_are_usable_and_fit_the_contract(self):
        formula_ids = {f["formula_id"] for f in solvers.formula_registry().list_formulas()}
        for t in TOOLS.values():
            self.assertIsInstance(t, AnalysisTool)
            checks = t.check()
            spec = t.spec()
            contract.validate("tool_spec", spec)
            self.assertEqual([c for c in checks if not c["pass"]], [], t.name)
            self.assertTrue(spec["usable"])
            self.assertGreaterEqual(len(checks), 3)
            self.assertTrue(set(spec["formulas"]) <= formula_ids)


def required_case(tool, test_id):
    """A load case with the required params of a tool, at values that run on the demo vehicle."""
    values = {"step_height": q(100, "mm"), "distance": q(2, "km"), "grade_low": q(5, "%"), "grade_high": q(15, "%"),
              "speed_low": q(10, "km/h"), "speed_high": q(25, "km/h"), "ambient_temperature": q(30, "degC"),
              "speed": q(20, "km/h"), "actuator": "back"}
    params = {p.name: values[p.name] for p in tool.load_case_params if p.required}
    if tool.name == "fatigue":
        params.update(road_class="C", speed=q(10, "km/h"), sample_length=q(50, "m"))
    if tool.name == "actuator_linkage":
        params.update(loading="driver")  # a care bed has one occupant; the default loading names two
    return load_case(f"{test_id}-x", test_id, tool.domain, **params)


def design_for(tool):
    """The demo vehicle, a default care bed for the tools that read a care bed's names, or the default space frame
    for frame_tubes (it needs the suspension's pickup points on frame nodes)."""
    if tool.name == "actuator_linkage":
        from hiraku.designgen import care_bed

        return care_bed.build()
    if tool.name == "frame_tubes":
        from hiraku.designgen import space_frame_utv

        return space_frame_utv.build()
    return DESIGN


class DeclarationsTest(unittest.TestCase):
    """load_case_params and predicts: what the design department wires load cases and predictions from."""

    def test_describe(self):
        registry = {f["formula_id"]: f for f in solvers.formula_registry().list_formulas()}
        for t in TOOLS.values():
            d = t.describe()
            json.dumps(d, allow_nan=False)
            names = [p["name"] for p in d["params"]]
            self.assertEqual(len(names), len(set(names)), t.name)
            self.assertTrue(set(LOADINGS) <= set(next(p for p in d["params"] if p["name"] == "loading")["choices"]))
            for p in t.predicts:
                self.assertIn(p.formula_id, t.formulas, f"{t.name}: {p.formula_id}")
                outs = registry[p.formula_id]["outputs"]
                if "<" in p.output:
                    self.assertTrue(any(o.startswith(p.output.split("<")[0]) for o in outs), p.output)
                else:
                    self.assertIn(p.output, outs, f"{t.name}: {p.formula_id}")

    def test_required_params_run_and_predicted_quantities_exist(self):
        for t in TOOLS.values():
            if not t.predicts:
                continue
            # frame_shell's mesh convergence solves more meshes; it is tested in test_mesh_convergence
            params = {"mesh_convergence": "off"} if t.name == "frame_shell" else {}
            rec = t.run(design_for(t), required_case(t, t.tests[0]), PRED, params)
            self.assertEqual(rec["status"], "ok", f"{t.name}: {rec.get('error')}")
            got = [r["quantity"] for r in rec["results"]]
            for p in t.predicts:
                head, _, tail = p.quantity.partition("<")
                tail = tail.partition(">")[2]
                self.assertTrue(any(g.startswith(head) and g.endswith(tail) for g in got), f"{t.name}: {p.quantity}")

    def test_missing_required_param_is_named(self):
        for t in TOOLS.values():
            for p in t.load_case_params:
                if p.required:
                    rec = t.run(DESIGN, load_case("x", t.tests[0], t.domain), PRED, {})
                    self.assertEqual(rec["status"], "failed", t.name)
                    self.assertIn(f"{p.name} [{p.unit or 'string'}]", rec["error"])

    def test_bad_values_fail(self):
        tool = TOOLS["frame_static"]
        for bad in ({"g_factor": q(2, "mm")}, {"loading": "three"}, {"step_supports": 3}, {"g_factor": 2.0}):
            rec = tool.run(DESIGN, load_case("R1", "R1", "strength", **bad), PRED, {})
            self.assertEqual(rec["status"], "failed", bad)
            self.assertIn(next(iter(bad)), rec["error"])

    def test_undeclared_read_is_a_tool_fault(self):
        from hiraku.solvers.shelf.base import G_FACTOR

        class Forgetful(type(TOOLS["frame_static"])):
            load_case_params = (G_FACTOR,)  # reads loading without declaring it

        rec = Forgetful().run(DESIGN, load_case("R1", "R1", "strength"), PRED, {})
        self.assertEqual(rec["status"], "failed")
        self.assertIn("does not declare it", rec["error"])

    def test_defaults_are_filled_and_extra_params_ignored(self):
        tool = TOOLS["frame_static"]
        a = results(tool.run(DESIGN, load_case("R1", "R1", "strength"), PRED, {}))
        b = results(tool.run(DESIGN, load_case("R1", "R1", "strength", g_factor=q(1, "1"), loading="two_cargo",
                                               speed=q(10, "km/h")), PRED, {}))
        self.assertEqual(a["max_stress"]["value"], b["max_stress"]["value"])


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

    def test_scene_steps_meet_the_wheels(self):
        rec = self.run_case(step_height=q(100, "mm"))
        r = results(rec)
        for st in rec["scene"]:
            self.assertEqual(st["kind"], "step")
            sid = st["label"].split("(")[1].rstrip(")")
            s = next(x for x in DESIGN["supports"] if x["id"] == sid)
            face = st["position_mm"][0] - st["size_mm"][0] / 2
            self.assertAlmostEqual(face - s["position_mm"][0], r["corner_distance"]["value"], places=9)
            self.assertEqual(st["size_mm"][2], 100.0)
        self.assertEqual(len(rec["scene"]), 2)

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

    def test_vectors_balance(self):
        """reaction + grip + weight (+ inertia) add up to zero, as K2's vectors do."""
        for rec in (TOOLS["step_climb"].run(DESIGN, load_case("P1", "P1", "powertrain", step_height=q(100, "mm")), PRED, {}),
                    self.run_case()):
            total = np.sum([v["vector_N"] for v in rec["vectors"]], axis=0)
            self.assertLess(float(np.linalg.norm(total)), 1e-6 * results(rec)["vehicle_mass"]["value"] * STANDARD_GRAVITY)
        kinds = {v["kind"] for v in TOOLS["step_climb"].run(
            DESIGN, load_case("P1", "P1", "powertrain", step_height=q(100, "mm")), PRED, {})["vectors"]}
        self.assertEqual(kinds, {"reaction", "grip", "load"})

    def test_accelerating_upstream_takes_inertia_relief(self):
        """A K2-style upstream whose ground forces leave a net force, with the matching inertia vector."""
        p1 = TOOLS["step_climb"].run(DESIGN, load_case("P1-step100", "P1", "powertrain", step_height=q(100, "mm")), PRED, {})
        up = copy.deepcopy(p1)
        extra = np.array([300.0, 0.0, 0.0])
        for v in up["vectors"]:
            if v["kind"] == "grip" and v["at"] == "RL":
                v["vector_N"] = [a + b for a, b in zip(v["vector_N"], extra)]
        refused = self.run_case({"upstream_analyses": {"P1-step100": up}})
        self.assertEqual(refused["status"], "failed")
        up["vectors"].append({"kind": "inertia", "at": "cg", "origin_mm": [0.0, 0.0, 0.0], "vector_N": list(-extra)})
        rec = self.run_case({"upstream_analyses": {"P1-step100": up}})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        r = results(rec)
        m = r["vehicle_mass"]["value"]
        self.assertAlmostEqual(r["inertia_acceleration"]["value"], 300.0 / m, places=9)
        total = np.sum([v["vector_N"] for v in rec["vectors"]], axis=0)
        self.assertLess(float(np.linalg.norm(total)), 1e-6 * m * STANDARD_GRAVITY)

    def test_inertia_relief_holds_an_unbalanced_moment(self):
        from hiraku.solvers.k1_frame import analyze
        from hiraku.solvers.shelf import fixtures
        lad = fixtures.ladder_design()
        rest = analyze(lad)
        forces = {sid: [("reaction", g["point_mm"], g["force_N"])] for sid, g in rest["ground"].items()}
        bump = np.array([0.0, 0.0, 500.0])  # an extra upward push under one wheel: force and moment
        forces["FL"] = forces["FL"] + [("reaction", rest["ground"]["FL"]["point_mm"], bump)]
        res = analyze(lad, ground_forces=forces, inertia_N=-bump)
        self.assertLess(res["residual_ratio"], 1e-6)
        self.assertGreater(float(np.linalg.norm(res["inertia_alpha"])), 0.0)

    def test_upstream_without_wheel_forces_is_ignored(self):
        rec = self.run_case({"upstream_analyses": {"X": {"status": "ok", "vectors": []}}})
        self.assertEqual(rec["status"], "ok")
        self.assertEqual(results(rec)["max_stress"]["value"], results(self.run_case())["max_stress"]["value"])


if __name__ == "__main__":
    unittest.main()
