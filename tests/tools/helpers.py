"""Start a run on the fixed requirements file with the test fakes."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from hiraku import contract
from hiraku.agent.session import start_run
from hiraku.catalog import REPO_ROOT
from tests.tools import fakes

REQUIREMENTS = REPO_ROOT / "data" / "requirements" / "offroad-4w.yaml"
EXAMPLE_DESIGN = contract.EXAMPLES_DIR / "design.json"


class RunCase:
    """Mixin for unittest.TestCase: self.ctx is a fresh run with fake solvers."""

    step_factor = 1.03
    frame_factor = 1.03
    frame_checks_pass = True

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.design_path = self.root / "design.json"
        self.design_path.write_text(json.dumps(fakes.loaded_design()), encoding="utf-8")
        self.formulas = fakes.FakeFormulas()
        self.tools = fakes.shelf(self.step_factor, self.frame_factor, self.frame_checks_pass)
        self.ctx = start_run(REQUIREMENTS, design_path=self.design_path, root=self.root, slug="offroad-4w-test",
                             formulas=self.formulas, tools=self.tools)

    def tearDown(self):
        self._tmp.cleanup()

    def kinds(self):
        return [e["kind"] for e in self.ctx.store.events()]

    def assert_events_fit(self):
        ev = self.ctx.store.events()
        self.assertEqual([e["seq"] for e in ev], list(range(len(ev))))
        for e in ev:
            self.assertEqual(contract.errors("event", e), [], e["kind"])

    # the P1 -> R1 chain with the fake formulas -----------------------------

    def p1_inputs(self):
        from hiraku.tools.summary import design_summary

        s = design_summary(self.ctx.store.design())
        load = s["loadings"]["two_cargo"]
        return {"m": load["mass"], "a": load["cg_from_front_axle"], "L": s["wheelbase"], "n": s["front_wheels"],
                "r": s["front_wheel_radius"], "h": {"value": 100.0, "unit": "mm"}}

    def predict_p1(self, agent=None, formula_id="step_climb.pushed_wheel_load"):
        from hiraku.tools.functions import record_prediction

        ins = self.p1_inputs()
        if formula_id == "mass_props.front_wheel_load":  # the lever rule, at rest
            ins = {k: ins[k] for k in ("m", "a", "L", "n")}
        load = record_prediction(self.ctx, {
            "load_case_id": "P1-step100", "quantity": "front_wheel_load", "formula_id": formula_id, "output": "W",
            "inputs": ins, "assumptions": ["test"],
        }, agent)
        force = record_prediction(self.ctx, {
            "load_case_id": "P1-step100", "quantity": "front_wheel_force", "formula_id": "step_climb.pushed_wheel_load",
            "output": "F", "inputs": self.p1_inputs(), "assumptions": [],
        }, agent)
        return load, force

    def run_p1(self):
        from hiraku.tools.functions import analyze, compare

        load, force = self.predict_p1()
        ana = analyze(self.ctx, {"tool": "step_climb", "load_case_id": "P1-step100",
                                 "prediction_ids": [load["prediction_id"], force["prediction_id"]]})
        c1 = compare(self.ctx, {"prediction_id": load["prediction_id"], "analysis_id": ana["analysis_id"]})
        c2 = compare(self.ctx, {"prediction_id": force["prediction_id"], "analysis_id": ana["analysis_id"]})
        return ana, c1, c2

    def predict_r1(self, F):
        from hiraku.tools.functions import record_prediction

        return record_prediction(self.ctx, {
            "load_case_id": "R1-step100", "quantity": "max_stress", "formula_id": "frame_static.rail_bending",
            "output": "sigma",
            "inputs": {"F": F, "L": {"value": 1800.0, "unit": "mm"}, "b": {"value": 40.0, "unit": "mm"},
                       "h": {"value": 80.0, "unit": "mm"}, "t": {"value": 2.0, "unit": "mm"}},
            "assumptions": ["test"],
        })
