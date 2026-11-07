"""Start a run on the fixed requirements file with the test fakes."""

from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path

from hiraku import contract
from unittest import mock

from hiraku.agent.session import start_run
from hiraku.tools import plan as _plan_mod
from hiraku.catalog import REPO_ROOT, read_data
from tests.tools import fakes

REQUIREMENTS = REPO_ROOT / "data" / "requirements" / "offroad-4w.yaml"
EXAMPLE_DESIGN = contract.EXAMPLES_DIR / "design.json"


def requirements_with_tests(folder: Path, tests: list[str] | None, beyond: list[str] | None = None) -> Path:
    """The fixed requirements file with its `tests` list set (None: no list, the catalog picks)
    and, given, its `beyond` list (the tests that also get a case past the limit)."""
    req = read_data(REQUIREMENTS)
    req.pop("tests", None)
    if tests is not None:
        req["tests"] = tests
    if beyond is not None:
        req["beyond"] = beyond
    req["round_limit"] = 1  # the tests' fixture: one revision (the demo file allows more, data/catalog/scripted.yaml)
    path = folder / "requirements.json"
    path.write_text(json.dumps(req, ensure_ascii=False), encoding="utf-8")
    return path


_REAL_LOAD_PLAN = _plan_mod.load_plan

# The stand-in design and frame tool of these tests: the design has no end cross member, and the tool mismatches R1's second prediction on purpose (frame_factor 1.8), to drive the
# scripted run through a safe-side cause. The demo plan's real retry (rail_joint_stress_closed) matches the real tool,
# so its plan no longer explains a mismatch there; the tests add the explanation the plan used to carry.
R1_RETRY_SAFE_SIDE = {
    "quantity": "max_stress",
    "source": "the tests' stand-in frame tool against the rail-joint prediction (frame_factor 1.8)",
    "cause": "今度は手計算が大きい。鉛直の曲げは合い、横の曲げとねじりが倍に出る。式が隣の継ぎ手を固定とみなしているのに、フレームではそこも回るため",
    "fix": "合否は安全側の手計算の値で出す。もっと近い値が要るときは、隣の継ぎ手も回るとみた式で予測し直す",
    "safe_side": True,
    "safe_side_reason": ("鉛直の曲げは合っていて、外れているのは隣の継ぎ手を固定とみた分だけ。継ぎ手を固定とみるとレールは実際より硬く、"
                         "手計算の応力は大きく出る側にしかずれない。だから手計算の値は本当の応力を上から押さえている"),
}

def stand_in_plan(product, requirements=None):
    """The scripted plan as the stand-in design and tools of RunCase need it (see R1_RETRY_SAFE_SIDE)."""
    plan = copy.deepcopy(_REAL_LOAD_PLAN(product, requirements))
    plan["rule_check"] = False  # the stand-in design has no systems or real parts: the knowledge's rules would hit it
    for step in plan.get("steps") or []:
        retry = (step.get("expected_mismatch") or {}).get("retry")
        if step.get("load_case") in ("R1-step100", "R1-step250-beyond") and retry is not None:
            # the stand-in tools have no load_path formulas: the retry's path check by hand is left out
            retry["predictions"] = [p for p in retry.get("predictions") or []
                                    if p.get("formula_id") != "load_path.free_parts"]
            for p in retry["predictions"]:  # the stand-in design has no end cross member (cross_E)
                if p.get("formula_id") == "frame_static.rail_joint_stress_closed":
                    p["formula_id"] = "frame_static.rail_joint_stress"
                    for k in ("a", "L_e", "b_e", "h_e", "t_e"):
                        p["inputs"].pop(k, None)
            # the stand-in frame tool's mismatch, not the real plan's (version 5's cross_B, tool_decides)
            retry["expected_mismatch"] = copy.deepcopy(R1_RETRY_SAFE_SIDE)
    return plan


class RunCase:
    """Mixin for unittest.TestCase: self.ctx is a fresh run of P1 and R1 with fake solvers
    and a stand-in design generator (the real builders and designgen are not used)."""

    step_factor = 1.03
    frame_factor = 1.03
    frame_checks_pass = True

    def setUp(self):
        import hiraku.agent.scripted as scripted_mod

        for mod in (_plan_mod, scripted_mod):  # the stand-in plan (stand_in_plan) for every scripted run of these tests
            patcher = mock.patch.object(mod, "load_plan", stand_in_plan)
            patcher.start()
            self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.design_path = self.root / "design.json"
        self.design_path.write_text(json.dumps(fakes.loaded_design()), encoding="utf-8")
        self.formulas = fakes.FakeFormulas()
        self.tools = fakes.shelf(self.step_factor, self.frame_factor, self.frame_checks_pass)
        self.ctx = start_run(requirements_with_tests(self.root, ["P1", "R1"]), design_path=self.design_path,
                             root=self.root, slug="offroad-4w-test", formulas=self.formulas, tools=self.tools,
                             designgen=fakes.FakeDesigngen())

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
