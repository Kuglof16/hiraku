"""Stand-ins for the K0 registry and the analysis tools of hiraku.solvers, used only in tests.

They follow hiraku.contract.protocols and do real closed-form arithmetic, so
the plumbing (store, tools, rules, hooks) can be tested apart from the
solvers. Their numbers are not HIRAKU's results.
"""

from __future__ import annotations

import json
import math
from typing import Any

from hiraku import contract
from hiraku.catalog import loading_of
from hiraku.tools.summary import design_summary

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


def pushed(m, a, L, n, r, h):
    """Front wheel load and push when the rear wheels push it onto a step (moment balance)."""
    k = math.sqrt(2 * r * h - h * h) / (r - h)
    w = m * G * (L - a) / (L * n) / (1 + k * r / L)
    return w, k * w


def _rect_tube_Z(b, h, t):
    i = (b * h ** 3 - (b - 2 * t) * (h - 2 * t) ** 3) / 12.0
    return i / (h / 2.0)


def rail_step_sigma(W, L, F, z, r, b, h, t, steps=2000):
    """A rail simply supported at the axles: half the weight as a uniform load plus
    end moments F (z - r) at the front and F z at the rear; largest |M| / Z + F / A."""
    w = W / (2 * L)
    ma, mb = F * (z - r), F * z
    m_max = max(abs(w * x * (L - x) / 2 - ma * (1 - x / L) - mb * x / L) for x in (L * i / steps for i in range(steps + 1)))
    area = b * h - (b - 2 * t) * (h - 2 * t)
    return m_max / _rect_tube_Z(b, h, t) + F / area


def rail_joint_sigma(M_oh, F, W, z, e, b, h, t):
    """The rail at the rear axle joint taking the wheel's offset moments alone (no share to the
    cross member): F/A + (M_oh + F z)(h/2)/I_v + F e (b/2)/I_l, twist W e as thin-wall shear, von Mises."""
    bi, hi = b - 2 * t, h - 2 * t
    area = b * h - bi * hi
    i_v = (b * h ** 3 - bi * hi ** 3) / 12.0
    i_l = (h * b ** 3 - hi * bi ** 3) / 12.0
    sigma = F / area + (M_oh + F * z) * (h / 2) / i_v + F * e * (b / 2) / i_l
    tau = W * e / (2 * (b - t) * (h - t) * t)
    return math.sqrt(sigma ** 2 + 3 * tau ** 2)


_JOINT_INPUTS = {"M_oh": "N*mm", "F": "N", "W": "N", "z": "mm", "e": "mm", "L1": "mm", "L_c": "mm", "E": "MPa",
                 "nu": "1", "b": "mm", "h": "mm", "t": "mm", "b_c": "mm", "h_c": "mm", "t_c": "mm"}


class FakeFormulas:
    def __init__(self):
        self.calls = []

    def list_formulas(self):
        return [
            _f("mass_props.front_wheel_load", "前輪 1 輪の静的な荷重", "handling", "W = m g (L - a) / (L n)",
               {"m": "kg", "a": "mm", "L": "mm", "n": "1"}, {"W": "N"}, ["static"]),
            _f("step_climb.pushed_wheel_load", "押されて段を登る前輪の荷重と力", "powertrain",
               "k = sqrt(2 r h - h^2)/(r - h); W = m g (L - a)/(L n)/(1 + k r/L); F = k W",
               {"m": "kg", "a": "mm", "L": "mm", "n": "1", "r": "mm", "h": "mm"}, {"W": "N", "F": "N"},
               ["rear wheels push at the ground, the step pushes back at axle height"]),
            _f("step_climb.wheel_force_rigid", "剛い輪が段の角に当たったときの力", "powertrain",
               "d = sqrt(2 r h - h^2); F = W d / (r - h)", {"r": "mm", "h": "mm", "W": "N"},
               {"F": "N", "d": "mm"}, ["rigid wheel", "h < r"]),
            _f("frame_static.rail_bending", "レールの曲げ応力", "strength", "sigma = (F L / 4) / Z",
               {"F": "N", "L": "mm", "b": "mm", "h": "mm", "t": "mm"}, {"sigma": "MPa", "M": "N*mm", "Z": "mm^3"},
               ["simply supported"]),
            _f("frame_static.rail_step_bending", "段差を押して登るときのレールの曲げ応力", "strength",
               "w = W/(2L); M(x) = w x (L - x)/2 - F (z - r)(1 - x/L) - F z x/L; sigma = max|M|/Z + F/A",
               {"W": "N", "L": "mm", "F": "N", "z": "mm", "r": "mm", "b": "mm", "h": "mm", "t": "mm"},
               {"sigma": "MPa"}, ["no overhang, no sideways bending or torsion"]),
            _f("frame_static.rail_joint_stress", "後ろの車軸の継ぎ手のレールの応力", "strength",
               "sigma = F/A + (M_oh + F z)(h/2)/I_v + F e (b/2)/I_l; tau = W e/(2 A_m t); sqrt(sigma^2 + 3 tau^2)",
               _JOINT_INPUTS, {"sigma": "MPa"}, ["the rail takes the offset moments alone"]),
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
        if formula_id == "step_climb.pushed_wheel_load":
            w, f = pushed(x["m"], x["a"], x["L"], x["n"], x["r"], x["h"])
            return {"outputs": {"W": {"value": w, "unit": "N"}, "F": {"value": f, "unit": "N"}}, "terms": [],
                    "expression": spec["expression"]}
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
            return {"outputs": {"sigma": {"value": m / z, "unit": "MPa"}, "M": {"value": m, "unit": "N*mm"},
                                "Z": {"value": z, "unit": "mm^3"}},
                    "terms": [{"name": "M", "value": m, "unit": "N*mm", "share": 1.0}], "expression": spec["expression"]}
        if formula_id == "frame_static.rail_step_bending":
            sigma = rail_step_sigma(x["W"], x["L"], x["F"], x["z"], x["r"], x["b"], x["h"], x["t"])
            return {"outputs": {"sigma": {"value": sigma, "unit": "MPa"}}, "terms": [], "expression": spec["expression"]}
        if formula_id == "frame_static.rail_joint_stress":
            sigma = rail_joint_sigma(*(x[k] for k in ("M_oh", "F", "W", "z", "e", "b", "h", "t")))
            return {"outputs": {"sigma": {"value": sigma, "unit": "MPa"}}, "terms": [], "expression": spec["expression"]}
        raise KeyError(formula_id)


class FakeTool:
    """A shelf tool whose result is the closed form times `factor` (1.03 = 3 % off).

    `factor` may be a sequence: one value per run, the last one repeating.
    """

    base = "K1"

    def __init__(self, name, domain, tests, factor=1.03, checks_pass=True):
        self.name = name
        self.domain = domain
        self.tests = tests
        self.factor = factor
        self.checks_pass = checks_pass
        self.runs = []

    def current_factor(self):
        """The factor of the run in progress (call after appending to self.runs)."""
        if isinstance(self.factor, (list, tuple)):
            return self.factor[min(len(self.runs), len(self.factor)) - 1]
        return self.factor

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
        s = design_summary(design)
        load = s["loadings"][loading_of(load_case)]
        r = s["front_wheel_radius"]["value"]
        h = load_case["params"]["step_height"]["value"]
        if h >= r:
            return {"status": "failed", "error": f"step {h} mm is not below the wheel radius {r} mm; use bigger wheels",
                    "results": []}
        w, f = pushed(load["mass"]["value"], load["cg_from_front_axle"]["value"], s["wheelbase"]["value"],
                      s["front_wheels"]["value"], r, h)
        w, f = w * self.current_factor(), f * self.current_factor()
        # every wheel's load: the front ones w, the rear ones share the rest of the weight
        front_x = s["front_axle_x"]["value"]
        rear = (load["weight"]["value"] - s["front_wheels"]["value"] * w) / s["rear_wheels"]["value"]
        per_wheel = [{"quantity": f"wheel_load_{sp['id']}", "value": w if sp["position_mm"][0] == front_x else rear,
                      "unit": "N", "where": sp["id"]} for sp in design["supports"]]
        return {"status": "ok", "base": self.base, "results": [
            {"quantity": "front_wheel_load", "value": w, "unit": "N", "where": "FL"},
            {"quantity": "front_wheel_force", "value": f, "unit": "N", "where": "FL"},
        ] + per_wheel, "vectors": [{"kind": "reaction", "at": "FL", "origin_mm": [0.0, 0.0, 0.0], "vector_N": [-f, 0.0, w]}]}


class FakeFrameStatic(FakeTool):
    def __init__(self, **kw):
        super().__init__("frame_static", "strength", ["R1"], **kw)

    def run(self, design, load_case, predictions, params):
        self.runs.append((design, load_case, predictions, params))
        up = params["upstream_analyses"]["P1-step100"]
        f = next(r["value"] for r in up["results"] if r["quantity"] == "front_wheel_force")
        summ = design_summary(design)
        L = summ["wheelbase"]["value"]
        rail = summ["rail_section"]
        sigma = f * L / 4.0 / _rect_tube_Z(rail["b"]["value"], rail["h"]["value"], rail["t"]["value"])
        sigma *= self.current_factor()
        return {"status": "ok", "base": "K1",
                "results": [{"quantity": "max_stress", "value": sigma, "unit": "MPa", "where": "rail_L0"}],
                "fields": [{"kind": "stress", "label": "von Mises", "unit": "MPa", "per_member": {"rail_L0": sigma}}],
                "inputs_from": [up["analysis_id"]]}


def example_design() -> Record:
    with (contract.EXAMPLES_DIR / "design.json").open(encoding="utf-8") as f:
        return json.load(f)


def _part(pid, kind, center, size, mass):
    return {"id": pid, "name": pid, "kind": kind, "box": {"center_mm": center, "size_mm": size}, "mass_kg": mass,
            "carried_by": ["L1", "R1"]}


def loaded_design(person_kg=76.6, cargo_kg=150.0) -> Record:
    """The contract's shape example plus two occupants (driver first) and cargo, as the contract asks."""
    d = example_design()
    d["parts"] += [
        _part("driver", "occupant", [-900.0, 250.0, 800.0], [400.0, 450.0, 900.0], person_kg),
        _part("passenger", "occupant", [-900.0, -250.0, 800.0], [400.0, 450.0, 900.0], person_kg),
        _part("cargo", "cargo", [-1500.0, 0.0, 550.0], [500.0, 800.0, 300.0], cargo_kg),
    ]
    return d


def shelf(step_factor=1.03, frame_factor=1.03, frame_checks_pass=True):
    return {
        "step_climb": FakeStepClimb(factor=step_factor),
        "frame_static": FakeFrameStatic(factor=frame_factor, checks_pass=frame_checks_pass),
    }
