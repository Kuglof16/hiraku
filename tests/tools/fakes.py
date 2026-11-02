"""Stand-ins for the analysis department's K0 registry and tools, used only in tests.

They follow hiraku.contract.protocols and do real closed-form arithmetic, so
the plumbing (store, tools, rules, hooks) can be tested before hiraku.solvers
is on main. Their numbers are not HIRAKU's results.
"""

from __future__ import annotations

import json
import math
from typing import Any

from hiraku import contract

G = 9.80665  # standard gravity, m/s^2 (exact by definition)

Record = dict[str, Any]


def _f(formula_id, title, domain, expression, inputs, outputs, assumptions):
    return {
        "formula_id": formula_id,
        "title": title,
        "domain": domain,
        "expression": expression,
        "inputs": {k: {"unit": u, "description": k} for k, u in inputs.items()},
        "outputs": {k: {"unit": u} for k, u in outputs.items()},
        "assumptions": assumptions,
        "source": "test fake",
    }


def _rect_tube_Z(b, h, t):
    i = (b * h ** 3 - (b - 2 * t) * (h - 2 * t) ** 3) / 12.0
    return i / (h / 2.0)


class FakeFormulas:
    def __init__(self):
        self.calls = []

    def list_formulas(self):
        return [
            _f("mass_props.front_wheel_load", "前輪 1 輪の静的な荷重", "handling", "W = m g (L - a) / (L n)",
               {"m": "kg", "a": "mm", "L": "mm", "n": "1"}, {"W": "N"}, ["static"]),
            _f("step_climb.wheel_force_rigid", "剛い輪が段の角に当たったときの力", "powertrain",
               "d = sqrt(2 r h - h^2); F = W d / (r - h)", {"r": "mm", "h": "mm", "W": "N"},
               {"F": "N", "d": "mm"}, ["rigid wheel", "h < r"]),
            _f("frame_static.rail_bending", "レールの曲げ応力", "strength", "sigma = (F L / 4) / Z",
               {"F": "N", "L": "mm", "b": "mm", "h": "mm", "t": "mm"}, {"sigma": "MPa"}, ["simply supported"]),
        ]

    def evaluate(self, formula_id, inputs):
        self.calls.append((formula_id, inputs))
        spec = {f["formula_id"]: f for f in self.list_formulas()}[formula_id]
        x = {}
        for name, s in spec["inputs"].items():
            if name not in inputs:
                raise ValueError(f"input {name} is missing")
            if inputs[name]["unit"] != s["unit"]:
                raise ValueError(f"input {name} must be in {s['unit']}, got {inputs[name]['unit']}")
            x[name] = inputs[name]["value"]
        if formula_id == "mass_props.front_wheel_load":
            w = x["m"] * G * (x["L"] - x["a"]) / x["L"] / x["n"]
            return {"outputs": {"W": {"value": w, "unit": "N"}}, "terms": [], "expression": spec["expression"]}
        if formula_id == "step_climb.wheel_force_rigid":
            r, h, w = x["r"], x["h"], x["W"]
            if h >= r:
                raise ValueError("h must be smaller than r")
            d = math.sqrt(2 * r * h - h * h)
            return {"outputs": {"F": {"value": w * d / (r - h), "unit": "N"}, "d": {"value": d, "unit": "mm"}},
                    "terms": [{"name": "d/(r-h)", "value": d / (r - h), "unit": "1"}], "expression": spec["expression"]}
        if formula_id == "frame_static.rail_bending":
            z = _rect_tube_Z(x["b"], x["h"], x["t"])
            m = x["F"] * x["L"] / 4.0
            return {"outputs": {"sigma": {"value": m / z, "unit": "MPa"}},
                    "terms": [{"name": "M", "value": m, "unit": "N*mm", "share": 1.0}], "expression": spec["expression"]}
        raise KeyError(formula_id)


class FakeTool:
    """A shelf tool whose result is the closed form times `factor` (1.03 = 3 % off)."""

    base = "K1"

    def __init__(self, name, domain, tests, factor=1.03, checks_pass=True):
        self.name = name
        self.domain = domain
        self.tests = tests
        self.factor = factor
        self.checks_pass = checks_pass
        self.runs = []

    def spec(self):
        return {"name": self.name, "title": f"fake {self.name}", "domain": self.domain, "base": self.base,
                "tests": self.tests, "origin": "builtin", "usable": False, "checks": [], "formulas": []}

    def check(self):
        return [{"case": "known answer", "quantity": "x", "expected": 1.0, "got": 1.0 if self.checks_pass else 2.0,
                 "unit": "1", "tolerance_rel": 0.01, "pass": self.checks_pass, "source": "test fake"}]


class FakeStepClimb(FakeTool):
    base = "K2"

    def __init__(self, **kw):
        super().__init__("step_climb", "powertrain", ["P1"], **kw)

    def run(self, design, load_case, predictions, params):
        self.runs.append((design, load_case, predictions, params))
        parts = design["parts"]
        m = sum(p["mass_kg"] for p in parts)
        a = sum(p["mass_kg"] * p["box"]["center_mm"][0] for p in parts) / m
        wheels = [s for s in design["supports"] if s["kind"] == "wheel"]
        xs = [s["position_mm"][0] for s in wheels]
        L = max(xs) - min(xs)
        front = [s for s in wheels if s["position_mm"][0] == min(xs)]
        w = m * G * (L - a) / L / len(front)
        r = front[0]["radius_mm"]
        h = load_case["params"]["step_height"]["value"]
        if h >= r:
            return {"status": "failed", "error": f"step {h} mm is not below the wheel radius {r} mm; use bigger wheels",
                    "results": []}
        d = math.sqrt(2 * r * h - h * h)
        f = w * d / (r - h) * self.factor
        return {"status": "ok", "base": self.base, "results": [
            {"quantity": "front_wheel_load", "value": w * self.factor, "unit": "N", "where": front[0]["id"]},
            {"quantity": "front_wheel_force", "value": f, "unit": "N", "where": front[0]["id"]},
        ], "vectors": [{"kind": "reaction", "at": front[0]["id"], "origin_mm": [0.0, 0.0, 0.0], "vector_N": [-f, 0.0, w]}]}


class FakeFrameStatic(FakeTool):
    def __init__(self, **kw):
        super().__init__("frame_static", "strength", ["R1"], **kw)

    def run(self, design, load_case, predictions, params):
        self.runs.append((design, load_case, predictions, params))
        up = params["upstream_analyses"]["P1-step100"]
        f = next(r["value"] for r in up["results"] if r["quantity"] == "front_wheel_force")
        nodes = {n["id"]: n["xyz_mm"] for n in design["nodes"]}
        mem = next(m for m in design["members"] if m["id"] == "rail_L0")
        L = math.dist(nodes[mem["node_a"]], nodes[mem["node_b"]])
        dims = design["sections"][mem["section"]]["dims_mm"]
        sigma = f * L / 4.0 / _rect_tube_Z(dims["b"], dims["h"], dims["t"]) * self.factor
        return {"status": "ok", "base": "K1",
                "results": [{"quantity": "max_stress", "value": sigma, "unit": "MPa", "where": "rail_L0"}],
                "fields": [{"kind": "stress", "label": "von Mises", "unit": "MPa", "per_member": {"rail_L0": sigma}}],
                "inputs_from": [up["analysis_id"]]}


def example_design() -> Record:
    with (contract.EXAMPLES_DIR / "design.json").open(encoding="utf-8") as f:
        return json.load(f)


def shelf(step_factor=1.03, frame_factor=1.03, frame_checks_pass=True):
    return {
        "step_climb": FakeStepClimb(factor=step_factor),
        "frame_static": FakeFrameStatic(factor=frame_factor, checks_pass=frame_checks_pass),
    }
