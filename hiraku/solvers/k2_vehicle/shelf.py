"""K2 scenarios as contract AnalysisTools (hiraku.contract.protocols.AnalysisTool).

Each tool: spec() -> tool_spec, check() -> tool_check list (the six pre-run
checks plus the tool's own known answers), run(design, load_case,
predictions, params) -> analysis record without analysis_id.

Load-case params: each tool declares the ones it reads in `params` (name,
contract unit, default in that unit, design variables that stand in when the
load case does not give it) and reads no others; describe()["params"] gives
them as records. params.loading (empty, driver, two, two_cargo; default
two_cargo) chooses the occupant and cargo parts on board for every tool. A
load-case param the tool does not read is named in the record's effects.
Tool options in `params` (not the load case): dt (s), record_hz.

Predictions: `pairs` names, for a result quantity, a K0 formula output that
predicts it in the same unit, and which formula inputs come straight from the
load-case params; spec()["formulas"] lists those formula ids.
"""

from __future__ import annotations

import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from scipy.optimize import brentq

from hiraku.contract.protocols import DEFAULT_LOADING, LOADINGS

from .checks import _check, base_checks
from .drive import RHO_AIR, Aero, Motor, PathDriver, SpeedController, controls, on_board, start_rolling
from .model import DesignError, Vehicle, box_design, build_vehicle
from .record import Recorder
from .scenario import HOLD_TORQUE, hold, new_sim, settle, side_gravity, slope_gravity, tilt_sweep
from .units import G0, si

try:  # the contract may grow an `assumptions` list on analysis records
    from hiraku import contract as _contract
    _ANALYSIS_PROPS = set(_contract.schema()["$defs"]["analysis"]["properties"])
except Exception:  # pragma: no cover
    _ANALYSIS_PROPS = set()


def _res(quantity: str, value: float, unit: str, where: str | None = None) -> dict:
    out = {"quantity": quantity, "value": float(value), "unit": unit}
    if where:
        out["where"] = where
    return out


@dataclass(frozen=True)
class P:
    """A load-case parameter a tool reads.

    unit: the contract unit of a quantity (None for a string or a boolean); default:
    in that unit, None when the parameter is optional or its default comes from the
    design (text says which). var: design variables read, in order, when the load case
    does not give the parameter (before the default). assumed: the default stands in for
    a property of the design, so using it is written down as an assumption.
    """

    name: str
    unit: str | None
    default: float | str | bool | None
    text: str
    required: bool = False
    choices: tuple = ()
    var: tuple = ()
    assumed: bool = False

    def describe(self) -> dict:
        """The shape of hiraku.solvers.shelf.base.Param.describe(), plus kind "boolean" and
        design_variables."""
        kind = "quantity" if self.unit else ("boolean" if isinstance(self.default, bool) else "string")
        out = {"name": self.name, "kind": kind, "required": self.required, "meaning": self.text}
        if self.unit:
            out["unit"] = self.unit
        if self.default is not None:
            out["default"] = {"value": float(self.default), "unit": self.unit} if self.unit else self.default
        if self.choices:
            out["choices"] = list(self.choices)
        if self.var:
            out["design_variables"] = list(self.var)
        return out


@dataclass(frozen=True)
class Pair:
    """A K0 formula output that predicts the tool's result `quantity` in the same unit.

    inputs: (formula input, load-case param) for the inputs taken straight from the load
    case; the others come from the design or another prediction (note says how when it is
    not plain).
    """

    quantity: str
    formula_id: str
    output: str
    inputs: tuple = ()
    note: str = ""

    def describe(self) -> dict:
        """The shape of hiraku.solvers.shelf.base.Predicts.describe(), plus inputs_from_load_case."""
        out = {"quantity": self.quantity, "formula_id": self.formula_id, "output": self.output}
        if self.note:
            out["note"] = self.note
        if self.inputs:
            out["inputs_from_load_case"] = dict(self.inputs)
        return out


DRIVELINE_EFFICIENCY = 0.9  # assumed when neither the load case nor the design gives one
LOADING = P("loading", None, DEFAULT_LOADING, "who and what is on board (catalog Q30)", choices=LOADINGS)
MU = P("mu", "1", None, "tyre-road friction (default: design variable tire_mu, else 0.8 assumed)")
RHO = P("rho", "kg/m^3", RHO_AIR, "air density (default: ISO 2533 sea level; not checked against the text)",
        var=("air_density",), assumed=True)
FRONT_AREA = P("front_area", "m^2", None, "frontal area (default: front projection of the part boxes)",
               var=("frontal_area", "front_area"))
SIDE_AREA = P("side_area", "m^2", None, "side area (default: side projection of the part boxes)", var=("side_area",))
SIDE_CP_HEIGHT = P("side_cp_height", "mm", None, "height of the side centre of pressure above the ground "
                   "(default: from the side projection of the part boxes)",
                   var=("centre_of_pressure_height", "center_of_pressure_height"))
CD = P("cd", "1", 0.8, "drag coefficient (0.8 assumed when neither the load case nor the design gives it)",
       var=("drag_coefficient",), assumed=True)
MOTOR = (P("motor_torque", "N*m", None, "peak torque at the motor", var=("motor_peak_torque", "motor_torque")),
         P("motor_power", "kW", None, "motor power, the limit above the corner speed (default: none)",
           var=("motor_peak_power", "motor_power")),
         P("gear_ratio", "1", None, "motor to wheel", var=("gear_ratio",)),
         P("efficiency", "1", DRIVELINE_EFFICIENCY, "motor shaft to wheels (0.9 assumed when not given)",
           var=("drivetrain_efficiency", "efficiency", "driveline_efficiency"), assumed=True),
         P("motor_max_speed", "rpm", None, "motor speed limit (default: none)", var=("motor_max_speed",)))


class K2Tool(ABC):
    """Base of the K2 tools: each scenario gives _run() and its own known answers."""

    name = "k2"
    title = "K2"
    domain = "handling"
    tests: list[str] = []
    params: tuple[P, ...] = ()
    pairs: tuple[Pair, ...] = ()

    @lru_cache(maxsize=1)
    def _own_checks(self) -> tuple:
        return tuple(self.own_checks())

    def own_checks(self) -> list[dict]:
        return []

    def check(self) -> list[dict]:
        return base_checks() + [dict(c) for c in self._own_checks()]

    def spec(self) -> dict:
        checks = self.check()
        return {"name": self.name, "title": self.title, "domain": self.domain, "base": "K2", "tests": list(self.tests),
                "origin": "builtin", "usable": all(c["pass"] for c in checks), "checks": checks,
                "formulas": self.formula_ids()}

    def formula_ids(self) -> list[str]:
        return list(dict.fromkeys(p.formula_id for p in self.pairs))

    def describe(self) -> dict:
        """What a load case for this tool needs (params) and which K0 formula outputs predict
        which results in the same unit (predicts); the shape of the analysis department's
        BuiltinTool.describe()."""
        return {"name": self.name, "title": self.title, "domain": self.domain, "tests": list(self.tests),
                "params": [p.describe() for p in (LOADING,) + self.params],
                "predicts": [p.describe() for p in self.pairs], "formulas": self.formula_ids()}

    def run(self, design: dict, load_case: dict, predictions: list[dict], params: dict | None = None) -> dict:
        t0 = time.perf_counter()
        rec = {"tool": self.name, "base": "K2", "design_version": int(design.get("design_version", 1)),
               "load_case_id": load_case.get("load_case_id", ""),
               "prediction_ids": [p["prediction_id"] for p in predictions if "prediction_id" in p],
               "status": "ok", "results": []}
        lp = dict(load_case.get("params") or {})
        self.opts = {k: v for k, v in (params or {}).items() if k != "upstream_analyses"}
        self._vars = design.get("variables") or {}
        self._assumed = []
        try:
            design = on_board(design, self.text(lp, "loading"))
            out = self._run(design, lp)
        except (DesignError, ValueError) as e:
            rec["status"] = "failed"
            rec["error"] = str(e)
            out = {}
        rec["results"] = out.get("results", [])
        for key in ("series", "vectors", "scene"):
            if out.get(key):
                rec[key] = out[key]
        # the values the design did not give and K2 took on its own, in the record itself
        assumptions = list(dict.fromkeys(list(out.get("assumptions", [])) + self._assumed))
        if "assumptions" in _ANALYSIS_PROPS and assumptions:
            rec["assumptions"] = assumptions
        effects = [{"name": "assumption: " + a} for a in assumptions]
        unread = sorted(set(lp) - {p.name for p in (LOADING,) + self.params})
        if unread:
            effects.insert(0, {"name": "load-case params this tool does not read: " + ", ".join(unread)})
        if effects:
            rec["effects"] = effects
        if out.get("error"):
            rec["status"] = "failed"
            rec["error"] = out["error"]
        rec["wall_time_s"] = time.perf_counter() - t0
        self.last_assumptions = assumptions
        return rec

    @abstractmethod
    def _run(self, design: dict, lp: dict) -> dict:
        """The scenario on the design with the occupants and cargo on board: results, series,
        vectors, assumptions (or error)."""

    # -- the declared load-case params
    def _declared(self, name: str) -> P:
        for p in (LOADING,) + self.params:
            if p.name == name:
                return p
        raise KeyError(f"{self.name} reads the load-case param {name!r} without declaring it")

    _vars: dict = {}

    def _note(self, text: str) -> None:
        """Write down an assumption of this run (it goes into the record's effects)."""
        self.__dict__.setdefault("_assumed", []).append(text)

    def q(self, lp: dict, name: str, want: str) -> float | None:
        """Quantity param `name` in the SI unit `want`: from the load case, else the declared
        design variables, else the declared default (written down when it is an assumption)."""
        p = self._declared(name)
        if name in lp:
            return si(lp[name], want)
        for v in p.var:
            if v in self._vars:
                return si(self._vars[v], want)
        if p.default is None:
            return None
        if p.assumed:
            names = " or ".join(p.var) if p.var else name
            self._note(f"{name} = {p.default:g} {p.unit} (assumed; neither the load case nor the design gives {names})")
        return si({"value": p.default, "unit": p.unit}, want)

    def given(self, lp: dict, name: str) -> bool:
        """The load case or a design variable gives `name` (the default is not used)."""
        return name in lp or any(v in self._vars for v in self._declared(name).var)

    def text(self, lp: dict, name: str) -> str | None:
        p = self._declared(name)
        v = lp.get(name, p.default)
        if v is not None and p.choices and v not in p.choices:
            raise ValueError(f"load-case param {name} must be one of {', '.join(p.choices)}, not {v!r}")
        return v

    def flag(self, lp: dict, name: str) -> bool:
        v = lp.get(name, self._declared(name).default)
        if not isinstance(v, bool):
            raise ValueError(f"load-case param {name} must be true or false, not {v!r}")
        return v

    def aero(self, veh, lp: dict) -> Aero:
        """Air forces from the declared rho / cd / cy / front_area / side_area / side_cp_height
        (no drag or side force when the tool does not declare cd or cy)."""
        names = {p.name for p in self.params}

        def get(name, want, fallback=None):
            return self.q(lp, name, want) if name in names else fallback

        aero = Aero.of(veh, get("rho", "kg/m^3", RHO_AIR), get("cd", "1", 0.0), get("cy", "1", 0.0),
                       get("front_area", "m^2"), get("side_area", "m^2"), get("side_cp_height", "m"))
        ground = min(float(c.pos[2]) - c.radius for c in veh.corners)
        for name, used, value in (("front_area", aero.cd > 0.0, f"{aero.front_area:.3g} m^2"),
                                  ("side_area", aero.cy > 0.0, f"{aero.side_area:.3g} m^2"),
                                  ("side_cp_height", aero.cy > 0.0,
                                   f"{(aero.side_cp[2] + float(veh.cg_s[2]) - ground) * 1000.0:.0f} mm")):
            if used and name in names and not self.given(lp, name):
                self._note(f"{name} = {value} from the projection of the part boxes (neither the load case nor "
                           f"the design gives {' or '.join(self._declared(name).var)})")
        return aero

    def motor(self, lp: dict) -> Motor:
        """The motor from the MOTOR params (load case, else design variables)."""
        torque, gear = self.q(lp, "motor_torque", "N*m"), self.q(lp, "gear_ratio", "1")
        if torque is None or gear is None:
            raise DesignError("give motor_torque (N*m at the motor), gear_ratio (1) and motor_power (kW) "
                              "in the load case params or design.variables")
        power, vmax = self.q(lp, "motor_power", "W"), self.q(lp, "motor_max_speed", "rad/s")
        if power is None:
            self._note("no motor power given: the drive is limited by the torque only")
        if vmax is None:
            self._note("no motor speed limit given")
        return Motor(torque, math.inf if power is None else power, gear, self.q(lp, "efficiency", "1"),
                     math.inf if vmax is None else vmax)

    def vehicle(self, design: dict, lp: dict, **overrides) -> Vehicle:
        mu = self.q(lp, "mu", "1")
        if mu is not None and "mu" not in overrides:
            overrides["mu"] = mu
        return build_vehicle(design, overrides)

    @property
    def dt(self) -> float:
        return float(self.opts.get("dt", 1e-3)) if hasattr(self, "opts") else 1e-3

    @property
    def record_hz(self) -> float:
        return float(self.opts.get("record_hz", 25.0)) if hasattr(self, "opts") else 25.0


# --------------------------------------------------------------------------- tilt
def tilt_closed_form(m_s: float, h_s: float, m_u: float, h_u: float, track: float, k_roll: float,
                     k_heave: float = math.inf) -> float:
    """Tip angle (rad) of a vehicle on vertical-slider suspension (roll axis on the ground).

    The normal load falls with cos(theta), so the body rises on its springs:
    h_s(theta) = h_s + m_s g (1 - cos theta) / k_heave. Body and wheel carriers roll
    together by phi about the roll axis: k_roll phi = g S sin(theta + phi), with
    S = m_s h_s(theta) + m_u h_u. Tip when the moment about the lower contact line
    vanishes: cos(theta) (t/2 - S sin(phi) / M) = sin(theta) S cos(phi) / M. Tyres rigid.
    """
    m = m_s + m_u

    def big_s(theta: float) -> float:
        hs = h_s + (m_s * G0 * (1.0 - math.cos(theta)) / k_heave if math.isfinite(k_heave) else 0.0)
        return m_s * hs + m_u * h_u

    def f(theta: float) -> float:
        sm = big_s(theta)
        p = brentq(lambda p: k_roll * p - G0 * sm * math.sin(theta + p), 0.0, 1.0)
        return math.cos(theta) * (track / 2.0 - sm * math.sin(p) / m) - math.sin(theta) * sm * math.cos(p) / m

    return brentq(f, 0.01, 1.5)


class TiltTool(K2Tool):
    """Tilt table (S1): turn the table slowly until the upper-side wheels carry no load.

    Results: tilt_angle (deg, the smaller side), tilt_angle_right, tilt_angle_left (deg),
    static_stability_factor (1, T/2h on the settled vehicle), body_roll_at_tip (deg),
    cg_shift_at_tip (mm, lateral CG shift against the contact line from suspension roll),
    friction_demand_at_tip (1, tan of the tip angle), slides_first (1: 1 if mu < the demand).
    """

    name = "tilt"
    title = "傾斜台 (K2 で台をゆっくり傾ける)"
    domain = "handling"
    tests = ["S1", "S1'"]
    params = (P("direction", None, "both", "which way the table tips; right = the table's right side goes down",
                choices=("both", "right", "left")),
              P("table_mu", "1", 2.0, "table surface friction, high so that tipping, not sliding, is found"),
              P("mu", "1", None, "road friction (optional): reports slides_first"))
    pairs = (Pair("tilt_angle", "tilt.spring_roll_angle", "theta",
                  note="the body rolls on its springs, as in K2; the roll axis of vertical-slider supports is "
                       "on the ground"),
             Pair("tilt_angle", "tilt.rigid_angle", "theta",
                  note="rigid: above K2 by the body roll on the springs (a first estimate)"),
             Pair("static_stability_factor", "tilt.rigid_angle", "ssf", note="K2 takes the CG on the centre line"),
             Pair("body_roll_at_tip", "tilt.spring_roll_angle", "roll"),
             Pair("friction_demand_at_tip", "tilt.spring_roll_angle", "ssf", note="both are tan(theta) at the tip"))

    def own_checks(self) -> list[dict]:
        d = box_design(mass=500.0, cg_x=-0.9, cg_z=0.5, track=1.2, k_w=20.0, wheel_mass=20.0, travel=200.0,
                       variables={"tyre_stiffness": {"value": 10000.0, "unit": "N/mm"},
                                  "tire_mu": {"value": 2.0, "unit": "1"}}, name="tilt check, springs")
        veh = build_vehicle(d)
        r = tilt_sweep(veh, left_up=True)
        got = None if r["tip_angle"] is None else math.degrees(r["tip_angle"])
        k_roll = 4.0 * 20e3 * 0.6 ** 2
        expected = math.degrees(tilt_closed_form(500.0, 0.5, 80.0, 0.3, 1.2, k_roll, 4.0 * 20e3))
        return [_check("tilt table, springs 20 N/mm (roll stiffness 28.8 kN m/rad), roll axis on the ground", "tilt_angle",
                       expected, got, "deg", 2e-3,
                       "body rises m_s g (1 - cos theta) / k_heave; body and carriers roll k phi = g S sin(theta + phi), "
                       "S = m_s h_s + m_u h_u; tip when cos(theta)(t/2 - S sin(phi)/M) = sin(theta) S cos(phi)/M")]

    def _run(self, design: dict, lp: dict) -> dict:
        table_mu = self.q(lp, "table_mu", "1")
        veh = build_vehicle(design, {"mu": table_mu})
        sides = {"both": ["right", "left"], "right": ["right"], "left": ["left"]}[self.text(lp, "direction")]
        angles, runs = {}, {}
        for side in sides:
            # tipping to the right = left side up
            r = tilt_sweep(veh, left_up=(side == "right"), record_hz=10.0)
            if r["tip_angle"] is None:
                return {"error": f"the vehicle did not tip up to 80 deg toward the {side}; check the mass and supports",
                        "assumptions": veh.assumptions}
            angles[side] = r["tip_angle"]
            runs[side] = r
        worst = min(angles, key=lambda k: angles[k])
        run = runs[worst]
        sim = run["sim"]
        rec = run["recorder"]
        snap = rec.snap[run["snapshot"]]
        cps = [w[1] for w in snap["wheels"]]
        cg = snap["cg"]
        res = [_res("tilt_angle", math.degrees(angles[worst]), "deg", f"tipping to the {worst}")]
        for side in sides:
            res.append(_res(f"tilt_angle_{side}", math.degrees(angles[side]), "deg"))
        ys = sorted(set(round(float(c[1]), 9) for c in cps))
        track = max(ys) - min(ys)
        ssf_sim = _ssf(veh)
        res.append(_res("static_stability_factor", ssf_sim, "1"))
        roll_series = rec.ch["roll"]
        tip_i = len(rec.t) - 1
        for k, tt in enumerate(rec.t):
            if tt >= snap["t"] - 1e-9:
                tip_i = k
                break
        body_roll = roll_series[tip_i]
        res.append(_res("body_roll_at_tip", body_roll, "deg"))
        lower = [c for c in cps if (c[1] < 0) == (worst == "right")]
        y_line = sum(c[1] for c in lower) / len(lower)
        shift = (track / 2.0 - abs(float(cg[1]) - y_line)) * 1000.0
        res.append(_res("cg_shift_at_tip", shift, "mm"))
        demand = math.tan(angles[worst])
        res.append(_res("friction_demand_at_tip", demand, "1"))
        mu_road = self.q(lp, "mu", "1")
        if mu_road is not None:
            res.append(_res("slides_first", 1.0 if mu_road < demand else 0.0, "1"))
        xs = [float(c.pos[0]) for c in veh.corners]
        r_max = max(c.radius for c in veh.corners)
        hw = _half_width(veh) + 0.5
        scene = [_box("tilt_table", "傾斜台 (table_angle が + のとき左が上、x のまわりに回る)",
                      (min(xs) - r_max - 0.5, -hw, -0.02), (max(xs) + r_max + 0.5, hw, 0.0), angle_channel="table_angle")]
        return {"results": res, "series": rec.series(), "vectors": rec.vectors(run["snapshot"]), "scene": scene,
                "assumptions": veh.assumptions + [f"table surface friction {table_mu:g} so the tyres do not slide"]}


def _ssf(veh) -> float:
    """T / 2h of the vehicle as designed (track between the outermost supports, total CG height
    above the ground under the wheels)."""
    ys = [float(c.pos[1]) for c in veh.corners]
    t = max(ys) - min(ys)
    ground = min(float(c.pos[2]) - c.radius for c in veh.corners)
    return t / (2.0 * (float(veh.cg_total[2]) - ground))


# ------------------------------------------------------------------ helpers
def _half_width(veh) -> float:
    """Half the width over the wheels (m)."""
    return max(abs(float(c.pos[1])) + c.width / 2.0 for c in veh.corners)


def _length(veh) -> float:
    """Length over the wheels (m)."""
    return max(float(c.pos[0]) + c.radius for c in veh.corners) - min(float(c.pos[0]) - c.radius for c in veh.corners)


def _box(kind: str, label: str, lo: tuple, hi: tuple, **extra) -> dict:
    """A scene object from its corners (m, road frame): position_mm is the box centre and
    size_mm its edges, as a design part's box."""
    out = {"kind": kind, "label": label,
           "position_mm": [round((a + b) / 2.0 * 1000.0, 1) for a, b in zip(lo, hi)],
           "size_mm": [round((b - a) * 1000.0, 1) for a, b in zip(lo, hi)]}
    out.update(extra)
    return out


def _slope(label: str, veh, x0: float, x1: float, roll: float = 0.0, front_up: float = 0.0) -> dict:
    """The road plane of a test that turns gravity instead of the road: rpy_deg is the
    plane's rotation from level, right-handed about x then y (roll + = left side up,
    pitch - = front up), so the record's positions are in that plane."""
    hw = _half_width(veh) + 1.0
    return _box("slope", label, (x0, -hw, 0.0), (x1, hw, 0.0),
                rpy_deg=[round(math.degrees(roll), 6), round(-math.degrees(front_up), 6), 0.0])


def _side_ids(sim, left: bool) -> list[int]:
    return [i for i, c in enumerate(sim.veh.corners) if c.side != 0 and (c.side > 0) == left]


def _wind_world(speed: float, from_left: bool) -> tuple:
    return (0.0, -speed if from_left else speed, 0.0)


def _min_load(series_min: dict) -> tuple[str, float]:
    cid = min(series_min, key=lambda k: series_min[k])
    return cid, series_min[cid]


def _ramp_gravity(sim, grav_of, angle: float, seconds: float = 1.0):
    """Turn gravity to the slope or table angle with the brakes held, then settle."""
    ctl = hold(sim)
    steps = int(round(seconds / sim.dt))
    for k in range(steps):
        sim.set_gravity(grav_of(angle * (k + 1) / steps))
        sim.step(ctl)
    settle(sim, 1.0, ctl)
    sim.t = 0.0


# ------------------------------------------------------------------ crosswind
def wind_ramp(veh, aero: Aero, from_left: bool = True, dt: float = 1e-3, fast: float = 5.0, slow: float = 1.0,
              slow_below: float = 0.4, v_max: float = 200.0, stop_on_slide: bool = False) -> dict:
    """Vehicle held at rest; the side wind grows until the upwind wheels carry no load."""
    sim = new_sim(veh, dt=dt, settle_s=1.5)
    ctl = hold(sim)
    up = _side_ids(sim, from_left)
    n0 = sum(sim.out["ntot"][i] for i in up)
    v, prev, crit, slide = 0.0, (0.0, n0), None, None
    while v < v_max:
        v += (fast if prev[1] > slow_below * n0 else slow) * dt
        sim.step(ctl, aero.forces(sim, _wind_world(v, from_left)))
        nu = 0.0
        for i in up:
            nu += sim.out["ntot"][i]
        if slide is None and any(sim.out["sliding"][i] for i in range(sim.n) if i not in up):
            slide = v
            if stop_on_slide:
                break
        if nu <= 0.0:
            v0, n_prev = prev
            crit = v0 + (v - v0) * n_prev / (n_prev - nu) if n_prev > nu else v
            break
        prev = (v, nu)
    return {"v_crit": crit, "slide_speed": slide}


class CrosswindTool(K2Tool):
    """Crosswind (F1): side force 1/2 rho C_y A v^2 at the side centre of pressure.

    Results: critical_wind_speed (m/s: at rest, the tyres held from sliding (mu 2), the
    upwind wheels lift), slide_wind_speed (m/s: at rest with the road mu, the vehicle starts
    to slide sideways), slides_first (1), side_force (N at wind_speed),
    min_upwind_wheel_load (N, the upwind wheels together, a gust of wind_speed reaching the
    vehicle at `speed` with the steering held straight), lateral_drift (m, drift_time after
    the gust).
    """

    name = "crosswind"
    title = "横風 (K2 に横風の力を入れる)"
    domain = "aero"
    tests = ["F1"]
    params = (P("wind_speed", "m/s", 30.0, "the gust (relative to the ground, across the vehicle)"),
              P("speed", "km/h", 0.0, "vehicle speed when the gust arrives"),
              P("cy", "1", 1.0, "side force coefficient (1.0 assumed when neither the load case nor the "
                                "design gives it)", var=("side_force_coefficient",), assumed=True),
              CD, RHO, SIDE_AREA, SIDE_CP_HEIGHT, FRONT_AREA,
              P("wind_from", None, "left", "side the wind comes from", choices=("left", "right")),
              P("drift_time", "s", 2.0, "time after the gust for lateral_drift"),
              MU)
    pairs = (Pair("critical_wind_speed", "crosswind.critical_wind_speed", "v_crit",
                  (("C_y", "cy"), ("A", "side_area"), ("rho", "rho"), ("h_cp", "side_cp_height")),
                  "K2 at rest with the tyres held (mu 2); the body also rolls on its springs. C_y, A, h_cp, rho: "
                  "the load case, else the design variables named in describe(), else K2's defaults (A and "
                  "h_cp from the side projection of the part boxes)"),
             Pair("side_force", "crosswind.side_force", "F",
                  (("C_y", "cy"), ("A", "side_area"), ("v", "wind_speed"), ("rho", "rho"))),
             Pair("min_upwind_wheel_load", "crosswind.windward_wheel_load", "W_windward", (("h_cp", "side_cp_height"),),
                  "F from crosswind.side_force; K2 is the minimum after a gust rising in 0.2 s, so the "
                  "roll overshoot can take it below the steady value"))

    def own_checks(self) -> list[dict]:
        d = box_design(mass=500.0, cg_x=-0.9, cg_z=0.5, track=1.2, susp="rigid",
                       variables={"tyre_stiffness": {"value": 10000.0, "unit": "N/mm"},
                                  "tire_mu": {"value": 2.0, "unit": "1"}}, name="crosswind check, rigid")
        veh = build_vehicle(d)
        aero = Aero.of(veh, RHO_AIR, 0.0, 1.0)
        got = wind_ramp(veh, aero, True)["v_crit"]
        got_r = wind_ramp(veh, aero, False)["v_crit"]
        w = veh.m_total * G0
        expected = math.sqrt(w * 1.2 / (RHO_AIR * 1.0 * veh.side_area * veh.side_cp[2]))
        src = "moment about the downwind contact line: 1/2 rho C_y A v^2 h_cp = W t / 2"
        return [_check(f"rigid vehicle at rest, side area {veh.side_area:.3f} m^2 at {veh.side_cp[2]:.3f} m",
                       "critical_wind_speed", expected, got, "m/s", 5e-3, src),
                _check("wind from the left - wind from the right (bit for bit)", "critical_wind_speed_difference", 0.0,
                       None if got is None or got_r is None else got - got_r, "m/s", 0.0, "symmetry",
                       passed=got is not None and got == got_r)]

    def _run(self, design: dict, lp: dict) -> dict:
        veh = self.vehicle(design, lp)
        aero = self.aero(veh, lp)
        notes = list(veh.assumptions)
        from_left = self.text(lp, "wind_from") == "left"
        gust = self.q(lp, "wind_speed", "m/s")
        speed = self.q(lp, "speed", "m/s")
        drift_t = self.q(lp, "drift_time", "s")
        held = build_vehicle(design, {"mu": 2.0})
        ramp = wind_ramp(held, self.aero(held, lp), from_left, dt=self.dt)
        side_name = "wind from the " + ("left" if from_left else "right")
        res = []
        if ramp["v_crit"] is not None:
            res.append(_res("critical_wind_speed", ramp["v_crit"], "m/s", side_name))
        slide = wind_ramp(veh, aero, from_left, dt=self.dt, stop_on_slide=True)
        slide_v = slide["slide_speed"]
        if slide_v is not None:
            res.append(_res("slide_wind_speed", slide_v, "m/s", side_name))
        res.append(_res("slides_first", 1.0 if slide_v is not None and (ramp["v_crit"] is None or slide_v < ramp["v_crit"]) else 0.0, "1"))
        res.append(_res("side_force", 0.5 * aero.rho * aero.cy * aero.side_area * gust * gust, "N"))

        sim = new_sim(veh, dt=self.dt, settle_s=1.5)
        rec = Recorder(sim, self.record_hz)
        up = _side_ids(sim, from_left)
        ctrl = SpeedController(sim) if speed > 0.0 else None
        if speed > 0.0:
            start_rolling(sim, speed)
        t_g, t_end = 1.0, 1.0 + drift_t + 0.5
        y_g = y_d = None
        min_up = math.inf
        for k in range(int(round(t_end / sim.dt))):
            t = k * sim.dt
            vw = gust * min(1.0, max(0.0, (t - t_g) / 0.2))
            if ctrl is not None:
                drive, brake = ctrl.torques(sim, speed)
                ctl = controls(sim, 0.0, drive, brake)
            else:
                ctl = hold(sim)
            sim.step(ctl, aero.forces(sim, _wind_world(vw, from_left)))
            rec.tick({"wind_speed": ("m/s", vw)})
            if y_g is None and t >= t_g:
                y_g = float(sim.cg_world()[1])
            if y_d is None and t >= t_g + drift_t:
                y_d = float(sim.cg_world()[1])
            if t >= t_g:
                nu = sum(sim.out["ntot"][i] for i in up)
                if nu < min_up:
                    min_up = nu
                    rec.snap = []
                    rec.keep_snapshot()
        if y_d is None:
            y_d = float(sim.cg_world()[1])
        res.append(_res("min_upwind_wheel_load", min_up, "N", ",".join(veh.corners[i].id for i in up)))
        res.append(_res("lateral_drift", abs(y_d - y_g), "m"))
        r_ = sim.rotation()
        cp_world = [sim.p[k] + float(r_[k] @ aero.side_cp) for k in range(3)]
        side = 0.5 * aero.rho * aero.cy * aero.side_area * gust * gust * (-1.0 if from_left else 1.0)
        wind_vec = [{"kind": "wind", "at": "side_cp", "origin_mm": [x * 1000.0 for x in cp_world],
                     "vector_N": [0.0, side, 0.0]}]
        return {"results": res, "series": rec.series(), "vectors": rec.vectors(-1, wind_vec), "assumptions": notes}


# ------------------------------------------------------------------ braking
def brake_stop(veh, v0: float, *, reaction: float = 0.0, brake_torque: float | None = None, grade: float = 0.0,
               dt: float = 1e-3, aero: Aero | None = None, rec_hz: float = 0.0) -> dict:
    """Run at v0 for 0.5 s, coast `reaction` s, then brake until the vehicle stops."""
    sim = new_sim(veh, dt=dt, settle_s=1.0)
    if grade:
        _ramp_gravity(sim, slope_gravity, math.atan(grade))
    rec = Recorder(sim, rec_hz) if rec_hz > 0 else None
    start_rolling(sim, v0)
    ctrl = SpeedController(sim)
    n = sim.n
    tq = HOLD_TORQUE if brake_torque is None else brake_torque
    brake_on = [tq if c.brake else 0.0 for c in veh.corners]
    if not any(brake_on):
        raise DesignError("no support has actuation.brake; braking needs a braked wheel")
    t_cmd, t_brake = 0.5, 0.5 + reaction
    x_cmd = x_brake = None
    peak_dec, max_pitch = 0.0, 0.0
    min_load = {c.id: math.inf for c in veh.corners}
    rear = [i for i, c in enumerate(veh.corners) if float(c.pos[0]) < float(veh.cg_total[0])]
    min_rear = math.inf
    k = 0
    while True:
        t = k * dt
        if t < t_cmd:
            drive, brake = ctrl.torques(sim, v0)
        elif t < t_brake:
            drive, brake = [0.0] * n, [0.0] * n
        else:
            drive, brake = [0.0] * n, brake_on
        sim.step(controls(sim, 0.0, drive, brake), aero.forces(sim) if aero is not None else None)
        if x_cmd is None and t >= t_cmd:
            x_cmd = float(sim.design_origin()[0])
        if x_brake is None and t >= t_brake:
            x_brake = float(sim.design_origin()[0])
        if rec is not None:
            rec.tick()
        if t >= t_brake:
            peak_dec = max(peak_dec, -float(sim.out["acc"][0]))
            for i, c in enumerate(veh.corners):
                min_load[c.id] = min(min_load[c.id], sim.out["ntot"][i])
            if rear:
                min_rear = min(min_rear, sum(sim.out["ntot"][i] for i in rear))
            max_pitch = max(max_pitch, sim.attitude()[1])
            if sim.forward_speed() <= 0.0:
                break
        k += 1
        if t > t_brake + 60.0:
            raise DesignError("the vehicle did not stop within 60 s of braking; check the brakes and mu")
    x_stop = float(sim.design_origin()[0])
    if rec is not None:
        rec.keep_snapshot()
        for _ in range(int(0.8 / dt)):
            sim.step(controls(sim, 0.0, [0.0] * n, brake_on))
            rec.tick()
    return {"stopping": x_stop - x_cmd, "braking": x_stop - x_brake, "peak_decel": peak_dec, "min_load": min_load,
            "min_rear_axle": min_rear if rear else None, "max_pitch": max_pitch, "recorder": rec, "sim": sim}


class BrakingTool(K2Tool):
    """Emergency stop (S4) and parking on a slope (S4b).

    Results: stopping_distance (m, from the brake command), braking_distance (m, from the
    brakes coming on), mean_decel (m/s^2 = v^2 / 2 s_braking), mean_decel_g (g, the same),
    peak_decel (m/s^2), min_wheel_load (N, where = support), min_rear_wheel_load (N, supports
    behind the CG: 0 = the rear lifts), min_rear_axle_load (N, those supports together),
    max_pitch (deg, nose down +); with parking_slope: parking_drift (mm in 60 s, facing uphill,
    brakes on) and parking_friction_demand (1, the largest |F_road| / F_z of a braked wheel).
    """

    name = "braking"
    title = "急制動 (K2 で急ブレーキ)"
    domain = "handling"
    tests = ["S4", "S4b"]
    params = (P("speed", "km/h", 35.0, "speed when the brake command comes"),
              MU,
              P("reaction_time", "s", 0.0, "coasting from the command until the brakes come on"),
              P("brake_torque", "N*m", None, "per braked wheel (default: enough to lock every braked wheel, no ABS)"),
              P("grade", "%", 0.0, "road grade, + uphill"),
              P("parking_slope", "%", None, "optional: also park on this slope, brakes on, 60 s"),
              P("cd", "1", 0.0, "drag coefficient (default 0: no air drag)"),
              RHO, FRONT_AREA)
    pairs = (Pair("stopping_distance", "braking.stopping_distance", "s",
                  (("v", "speed"), ("mu", "mu"), ("t_r", "reaction_time"))),
             Pair("braking_distance", "braking.stopping_distance", "s_braking", (("v", "speed"), ("mu", "mu"))),
             Pair("mean_decel_g", "braking.stopping_distance", "a_x", (("v", "speed"), ("mu", "mu"))),
             Pair("min_rear_axle_load", "braking.load_transfer", "W_rear",
                  note="a_x from braking.stopping_distance; K2 is the minimum while braking, so the pitch "
                       "overshoot on the springs can take it below the steady value"),
             Pair("parking_friction_demand", "braking.parking_friction", "mu_all", (("grade", "parking_slope"),),
                  "every wheel braked; K2 shares the hold by the tyres' stiffness and reports the largest "
                  "wheel's demand"),
             Pair("parking_friction_demand", "braking.parking_friction", "mu_rear", (("grade", "parking_slope"),),
                  "only the rear wheels braked"),
             Pair("parking_friction_demand", "braking.parking_friction", "mu_front", (("grade", "parking_slope"),),
                  "only the front wheels braked"))

    def own_checks(self) -> list[dict]:
        d = box_design(mass=500.0, cg_x=-0.9, cg_z=0.5, wheel_mass=20.0, name="braking check",
                       variables={"tire_mu": {"value": 0.7, "unit": "1"},
                                  "rolling_resistance": {"value": 0.0, "unit": "1"}})
        veh = build_vehicle(d)
        v0 = 40.0 / 3.6
        r = brake_stop(veh, v0)
        expected = v0 * v0 / (2.0 * 0.7 * G0)
        return [_check("locked wheels from 40 km/h, mu 0.7, no air drag or rolling resistance", "braking_distance",
                       expected, r["braking"], "m", 1e-2, "s = v^2 / (2 mu g)")]

    def _run(self, design: dict, lp: dict) -> dict:
        veh = self.vehicle(design, lp)
        notes = list(veh.assumptions)
        v0 = self.q(lp, "speed", "m/s")
        aero = self.aero(veh, lp) if self.q(lp, "cd", "1") > 0 else None
        tq = self.q(lp, "brake_torque", "N*m")
        if tq is None:
            notes.append("brakes strong enough to lock every braked wheel (no ABS)")
        r = brake_stop(veh, v0, reaction=self.q(lp, "reaction_time", "s"), brake_torque=tq,
                       grade=self.q(lp, "grade", "1"), dt=self.dt, aero=aero, rec_hz=self.record_hz)
        cid, mn = _min_load(r["min_load"])
        x_cg = float(veh.cg_total[0])
        rear = [c.id for c in veh.corners if float(c.pos[0]) < x_cg]
        decel = v0 * v0 / (2.0 * r["braking"])
        res = [_res("stopping_distance", r["stopping"], "m"), _res("braking_distance", r["braking"], "m"),
               _res("mean_decel", decel, "m/s^2"), _res("mean_decel_g", decel / G0, "g"),
               _res("peak_decel", r["peak_decel"], "m/s^2"), _res("min_wheel_load", mn, "N", cid)]
        if rear:
            rid = min(rear, key=lambda k: r["min_load"][k])
            res.append(_res("min_rear_wheel_load", r["min_load"][rid], "N", rid))
            res.append(_res("min_rear_axle_load", r["min_rear_axle"], "N", ",".join(rear)))
        res.append(_res("max_pitch", math.degrees(r["max_pitch"]), "deg"))
        slope = self.q(lp, "parking_slope", "1")
        if slope is not None:
            from .checks import parked_drift
            park = parked_drift(veh, slope, 60.0, self.dt)
            res.append(_res("parking_drift", park["drift"] * 1000.0, "mm"))
            res.append(_res("parking_friction_demand", park["friction_demand"], "1"))
        rec = r["recorder"]
        grade = self.q(lp, "grade", "1")
        # the recorded run is the stop (on the grade); the parking run is not recorded
        scene = [_slope(f"縦の坂 {grade * 100:g} % (前が上)", veh, -_length(veh) - 1.0,
                        r["stopping"] + v0 * 0.5 + _length(veh) + 2.0, front_up=math.atan(grade))] if grade else []
        return {"results": res, "series": rec.series(), "vectors": rec.vectors(-1), "scene": scene,
                "assumptions": notes}


# ------------------------------------------------------------------ launch
def launch_run(veh, motor: Motor, target: float, *, grade: float = 0.0, max_time: float = 30.0, dt: float = 1e-3,
               aero: Aero | None = None, rec_hz: float = 0.0, until_top: bool = False) -> dict:
    """Full throttle from rest until `target` speed (then 1 s more), or until it stops accelerating.
    until_top: go on past the target until it stops accelerating (the top speed)."""
    sim = new_sim(veh, dt=dt, settle_s=1.0)
    if grade:
        _ramp_gravity(sim, slope_gravity, math.atan(grade))
    rec = Recorder(sim, rec_hz) if rec_hz > 0 else None
    driven = [i for i, c in enumerate(veh.corners) if c.drive]
    if not driven:
        raise DesignError("no support has actuation.drive; a driven wheel is needed")
    x0 = float(sim.design_origin()[0])
    t_hit = x_hit = None
    vmax, slip_max = 0.0, 0.0
    hist = []
    k = 0
    while True:
        wbar = sum(sim.om[i] for i in driven) / len(driven)
        total = motor.wheel_torque_total(wbar)
        drive = [0.0] * sim.n
        for i in driven:
            drive[i] = total / len(driven)
        sim.step(controls(sim, 0.0, drive, [0.0] * sim.n), aero.forces(sim) if aero is not None else None)
        if rec is not None:
            rec.tick({"wheel_torque": ("N*m", total)})
        v = sim.forward_speed()
        vmax = max(vmax, v)
        for i in driven:
            c = veh.corners[i]
            re = c.radius - c.static_load / c.k_z
            slip_max = max(slip_max, abs(sim.om[i] * re - v) / max(abs(v), 0.5))
        if t_hit is None and v >= target:
            t_hit, x_hit = sim.t, float(sim.design_origin()[0]) - x0
            if rec is not None:
                rec.keep_snapshot()
        hist.append(v)
        k += 1
        if sim.t >= max_time or (not until_top and t_hit is not None and sim.t > t_hit + 1.0):
            break
        if (until_top or t_hit is None) and k % 1000 == 0 and len(hist) > 2000 and hist[-1] - hist[-1001] < 0.02:
            break  # no longer accelerating: the top speed (below the target unless until_top)
    return {"time": t_hit, "distance": x_hit, "top_speed": vmax, "slip_max": slip_max, "recorder": rec,
            "moved": float(sim.design_origin()[0]) - x0, "sim": sim}


class LaunchTool(K2Tool):
    """Launch and acceleration (P2, P3) with the motor's torque curve.

    Results: time_to_speed (s), distance_to_speed (m), mean_accel (m/s^2), top_speed (km/h,
    highest speed reached), reached_target (1), max_wheel_slip (1), moved_uphill (m, on a grade).
    The run ends 1 s after target_speed (until "target"), or when the speed gains less
    than 0.02 m/s in 1 s, or at max_time.
    """

    name = "launch"
    title = "発進・加速 (K2 にモーターのトルクの線)"
    domain = "powertrain"
    tests = ["P2", "P3"]
    params = (P("target_speed", "km/h", 40.0, "full throttle from rest up to this speed"),
              P("until", None, "target", "target: stop 1 s after target_speed; top_speed: go on until the "
                                         "speed stops rising", choices=("target", "top_speed")),
              P("grade", "%", 0.0, "road grade, + uphill"),
              *MOTOR,
              P("max_time", "s", 30.0, "longest run"),
              CD, RHO, FRONT_AREA, MU)
    _launch_inputs = (("v", "target_speed"), ("grade", "grade"), ("P", "motor_power"), ("eta", "efficiency"),
                      ("C_d", "cd"), ("A", "front_area"), ("rho", "rho"))
    _launch_note = ("these inputs: the load case, else the design variables named in describe(); F_max = "
                    "motor_torque gear_ratio efficiency / loaded wheel radius; C_rr = design variable "
                    "rolling_resistance (0.015 assumed in K2 when absent); lambda = "
                    "1 + sum(J / r^2) / m, K2 takes J = m_u r^2 / 2 per wheel (a solid disc of the unsprung mass), "
                    "not a mass_factor variable; A: K2 uses the front projection of the part boxes when neither "
                    "gives front_area")
    pairs = (Pair("time_to_speed", "launch.time_to_speed", "t", _launch_inputs, _launch_note),
             Pair("distance_to_speed", "launch.time_to_speed", "s", _launch_inputs, _launch_note),
             Pair("mean_accel", "launch.time_to_speed", "a_mean", _launch_inputs, _launch_note),
             Pair("top_speed", "launch.top_speed", "v_top", _launch_inputs[1:],
                  "give until = top_speed, so that K2 runs until the speed stops rising (it stops when the "
                  "gain is below 0.02 m/s in 1 s, a little under the limit); motor_max_speed is not in the "
                  "formula"))

    def own_checks(self) -> list[dict]:
        d = box_design(mass=500.0, cg_x=-0.9, cg_z=0.5, wheel_mass=20.0, name="launch check",
                       variables={"tire_mu": {"value": 1.0, "unit": "1"},
                                  "rolling_resistance": {"value": 0.0, "unit": "1"}})
        veh = build_vehicle(d)
        motor = Motor(torque=50.0, power=math.inf, gear=8.0, efficiency=1.0)
        r = launch_run(veh, motor, 5.0, max_time=3.0)
        driven = [c for c in veh.corners if c.drive]
        re = driven[0].radius - driven[0].static_load / driven[0].k_z
        m_eff = veh.m_total + sum(c.j_w / (c.radius - c.static_load / c.k_z) ** 2 for c in veh.corners if c.kind == "wheel")
        expected = 5.0 * m_eff / (50.0 * 8.0 / re)
        return [_check("constant wheel torque 400 N m, no rolling resistance or drag: time to 5 m/s", "time_to_speed",
                       expected, r["time"], "s", 1e-2, "v = F t / (m + sum J / r^2), F = T / r_loaded")]

    def _run(self, design: dict, lp: dict) -> dict:
        veh = self.vehicle(design, lp)
        notes = list(veh.assumptions)
        motor = self.motor(lp)
        target = self.q(lp, "target_speed", "m/s")
        grade = self.q(lp, "grade", "1")
        aero = self.aero(veh, lp)
        r = launch_run(veh, motor, target, grade=grade, max_time=self.q(lp, "max_time", "s"), dt=self.dt,
                       aero=aero, rec_hz=self.record_hz, until_top=self.text(lp, "until") == "top_speed")
        res = []
        if r["time"] is not None:
            res += [_res("time_to_speed", r["time"], "s"), _res("distance_to_speed", r["distance"], "m"),
                    _res("mean_accel", target / r["time"], "m/s^2")]
        res += [_res("top_speed", r["top_speed"] * 3.6, "km/h"),
                _res("reached_target", 1.0 if r["time"] is not None else 0.0, "1"),
                _res("max_wheel_slip", r["slip_max"], "1")]
        if grade:
            res.append(_res("moved_uphill", r["moved"], "m"))
        rec = r["recorder"]
        if not rec.snap:
            rec.keep_snapshot()
        scene = [_slope(f"縦の坂 {grade * 100:g} % (前が上)", veh, -_length(veh) - 1.0,
                        max(r["moved"], 0.0) + _length(veh) + 2.0, front_up=math.atan(grade))] if grade else []
        return {"results": res, "series": rec.series(), "vectors": rec.vectors(-1), "scene": scene,
                "assumptions": notes}


# ------------------------------------------------------------------ lane change
# Section lengths (m) and lane offset of a double lane change in the layout of
# ISO 3888-1: 15 / 30 / 25 / 25 / 15 / 15 m, offset 3.5 m. NOT checked against the
# ISO 3888-1 text; the course here is a smooth centre line, not the cone gates.
DLC_SECTIONS = (15.0, 30.0, 25.0, 25.0, 15.0, 15.0)
DLC_OFFSET = 3.5


# Lane widths of sections 1, 3 and 5 for a vehicle of width W (m): 1.1 W + 0.25,
# 1.2 W + 0.25, 1.3 W + 0.25. Source: ISO 3888-1:2018 (the sample pages), as the plan
# quotes them; not checked in this repository.
DLC_LANE_WIDTH = ((1.1, 0.25), (1.2, 0.25), (1.3, 0.25))


def dlc_scene(width: float, sign: float, x0: float = 10.0, offset: float = DLC_OFFSET, sections=DLC_SECTIONS) -> list:
    """Cones and gates of the course around the K2 centre line (the driver follows the
    line, not the gates): a gate at the start and the end of sections 1, 3 and 5, a cone
    at each end of a gate."""
    starts = [x0 + sum(sections[:k]) for k in range(len(sections) + 1)]
    out = []
    for lane, (a, b) in zip((0, 2, 4), DLC_LANE_WIDTH):
        w = a * width + b
        yc = sign * offset if lane == 2 else 0.0
        for x in (starts[lane], starts[lane + 1]):
            label = f"区間 {lane + 1} の門 (幅 {w:.2f} m = {a:g} W + {b:g}、ISO 3888-1 の配置、確かめていない)"
            out.append(_box("gate", label, (x, yc - w / 2.0, 0.0), (x, yc + w / 2.0, 0.0)))
            out += [{"kind": "cone", "position_mm": [round(x * 1000.0, 1), round((yc + s * w / 2.0) * 1000.0, 1), 0.0]}
                    for s in (-1.0, 1.0)]
    return out


def dlc_path(x0: float = 10.0, offset: float = DLC_OFFSET, sections=DLC_SECTIONS):
    a = x0 + sections[0]
    b = a + sections[1]
    c = b + sections[2]
    d = c + sections[3]
    end = d + sections[4] + sections[5]

    def y(x: float) -> float:
        if x <= a or x >= d:
            return 0.0
        if x < b:
            return offset * 0.5 * (1.0 - math.cos(math.pi * (x - a) / (b - a)))
        if x <= c:
            return offset
        return offset * 0.5 * (1.0 + math.cos(math.pi * (x - c) / (d - c)))

    return y, end


def path_run(veh, speed: float, y_ref, x_end: float, *, dt: float = 1e-3, rec_hz: float = 0.0, road=None,
             gravity=None, steer_fn=None) -> dict:
    """Drive along y_ref(x) at a held speed until the design origin passes x_end."""
    sim = new_sim(veh, road=road, dt=dt, settle_s=1.0)
    if gravity is not None:
        _ramp_gravity(sim, gravity[0], gravity[1])
    rec = Recorder(sim, rec_hz) if rec_hz > 0 else None
    start_rolling(sim, speed)
    drv = PathDriver(sim, y_ref)
    ctrl = SpeedController(sim)
    left = _side_ids(sim, True)
    right = _side_ids(sim, False)
    st = {"min_load": {c.id: math.inf for c in veh.corners}, "two_lift_time": 0.0, "any_lift": False,
          "max_ay": 0.0, "max_roll": 0.0, "max_err": 0.0, "max_steer": 0.0, "min_side": {"left": math.inf, "right": math.inf},
          "mean_side": {"left": 0.0, "right": 0.0}, "az": []}
    steps = 0
    t_limit = 3.0 * abs(x_end) / max(speed, 0.5) + 5.0
    worst = math.inf
    while sim.design_origin()[0] < x_end and sim.t < t_limit:
        delta = steer_fn(sim) if steer_fn is not None else drv.steer()
        drive, brake = ctrl.torques(sim, speed)
        sim.step(controls(sim, delta, drive, brake))
        o = sim.out
        nt = o["ntot"]
        for i, c in enumerate(veh.corners):
            if nt[i] < st["min_load"][c.id]:
                st["min_load"][c.id] = nt[i]
        if any(x <= 0.0 for x in nt):
            st["any_lift"] = True
        if (left and all(nt[i] <= 0.0 for i in left)) or (right and all(nt[i] <= 0.0 for i in right)):
            st["two_lift_time"] += sim.dt
        n_left, n_right = sum(nt[i] for i in left), sum(nt[i] for i in right)
        st["min_side"]["left"] = min(st["min_side"]["left"], n_left)
        st["min_side"]["right"] = min(st["min_side"]["right"], n_right)
        st["mean_side"]["left"] += n_left
        st["mean_side"]["right"] += n_right
        steps += 1
        r = sim.rotation()
        acc = o["acc"]
        ay = r[0, 1] * acc[0] + r[1, 1] * acc[1] + r[2, 1] * acc[2]
        st["az"].append(r[0, 2] * acc[0] + r[1, 2] * acc[1] + r[2, 2] * acc[2])
        st["max_ay"] = max(st["max_ay"], abs(ay))
        st["max_roll"] = max(st["max_roll"], abs(sim.attitude()[0]))
        cg = sim.cg_world()
        st["max_err"] = max(st["max_err"], abs(float(cg[1]) - y_ref(float(cg[0]))))
        st["max_steer"] = max(st["max_steer"], abs(delta))
        m = min(nt)
        if rec is not None:
            rec.tick({"steer": ("deg", math.degrees(delta)), "path_y": ("m", y_ref(float(cg[0])))})
            if m < worst:
                worst = m
                rec.snap = []
                rec.keep_snapshot()
    for side in ("left", "right"):
        st["mean_side"][side] /= max(steps, 1)
    st["finished"] = sim.design_origin()[0] >= x_end
    st["recorder"] = rec
    st["sim"] = sim
    return st


class LaneChangeTool(K2Tool):
    """Double lane change (S3; also S2 / S2b courses later) with a path-following driver.

    The driver is pure pursuit on the course centre line (preview 0.6 s, at least 3 m,
    steering rate 400 deg/s) and holds the speed. A wheel has lifted when its road normal
    force is zero.
    Results at `speed`: min_wheel_load (N, where), min_side_load (N, the lighter side's
    wheels together), wheel_lift (1), two_wheel_lift (1, both wheels of one side off at
    once), max_lateral_accel (m/s^2, body axis), max_lateral_accel_g (g, the same),
    max_roll (deg), max_path_error (m, CG from the centre line), max_steer (deg); and
    max_speed_no_lift (km/h, the highest speed with every wheel on the ground, to about
    0.6 km/h).
    """

    name = "lane_change"
    title = "ダブルレーンチェンジ (K2 + 運転のモデル)"
    domain = "handling"
    tests = ["S3"]
    params = (P("speed", "km/h", 40.0, "held speed"),
              MU,
              P("offset", "m", DLC_OFFSET, "sideways shift of the lane (ISO 3888-1 layout, not checked)"),
              P("direction", None, "left", "the first lane change goes toward", choices=("left", "right")),
              P("find_limit", None, True, "also search max_speed_no_lift"),
              P("v_min", "km/h", 10.0, "lowest speed of the search"),
              P("v_max", "km/h", 80.0, "highest speed of the search"))
    pairs = (Pair("max_lateral_accel_g", "lane_change.peak_lateral_acceleration", "a_y_peak",
                  (("v", "speed"), ("Y", "offset")),
                  f"X = {DLC_SECTIONS[3] * 1000:g} mm, the shorter lane change of the course (back to the first "
                  "lane); K2 is the body's lateral acceleration with the driver model, which lags the path"),
             Pair("min_side_load", "lane_change.inner_wheel_load", "W_inner",
                  note="a_y from lane_change.peak_lateral_acceleration; K2 is the minimum over the run, with "
                       "the roll overshoot of the springs"))

    def own_checks(self) -> list[dict]:
        d = box_design(mass=500.0, cg_x=-0.9, cg_z=0.5, wheel_mass=20.0, name="steady cornering check")
        veh = build_vehicle(d)
        v, delta = 10.0, math.radians(1.5)
        out = []
        rates = []
        for sign in (1.0, -1.0):
            sim = new_sim(veh, settle_s=1.0)
            start_rolling(sim, v)
            ctrl = SpeedController(sim)
            ws = []
            for k in range(8000):
                drive, brake = ctrl.torques(sim, v)
                sim.step(controls(sim, sign * delta, drive, brake))
                if k >= 6000:
                    ws.append(sim.w[2])
            rates.append(sum(ws) / len(ws))
        expected = v * math.tan(delta) / veh.l_steer
        out.append(_check("steady cornering 10 m/s, steer 1.5 deg, cornering stiffness proportional to axle load (neutral steer)",
                          "yaw_rate", expected, rates[0], "rad/s", 2e-2, "neutral steer: r = v tan(delta) / L"))
        out.append(_check("steady cornering left + right (bit for bit)", "yaw_rate_mirror_error", 0.0, rates[0] + rates[1],
                          "rad/s", 0.0, "symmetry", passed=rates[0] == -rates[1]))
        return out

    def _lift(self, veh, speed, y_ref, x_end) -> bool:
        st = path_run(veh, speed, y_ref, x_end, dt=self.dt)
        return st["any_lift"] or not st["finished"]

    def _run(self, design: dict, lp: dict) -> dict:
        veh = self.vehicle(design, lp)
        notes = list(veh.assumptions) + ["double lane change course in the ISO 3888-1 layout (15/30/25/25/15/15 m, "
                                         "offset 3.5 m); not checked against the ISO text; a centre line, not cone gates"]
        if not any(c.steer for c in veh.corners):
            return {"error": "no support has actuation.steer; a lane change needs a steered wheel"}
        speed = self.q(lp, "speed", "m/s")
        sign = -1.0 if self.text(lp, "direction") == "right" else 1.0
        y0, x_end = dlc_path(offset=self.q(lp, "offset", "m"))
        y_ref = (lambda x: sign * y0(x))
        st = path_run(veh, speed, y_ref, x_end, dt=self.dt, rec_hz=self.record_hz)
        cid, mn = _min_load(st["min_load"])
        light = min(st["min_side"], key=lambda k: st["min_side"][k])
        res = [_res("min_wheel_load", mn, "N", cid), _res("min_side_load", st["min_side"][light], "N", light),
               _res("wheel_lift", 1.0 if st["any_lift"] else 0.0, "1"),
               _res("two_wheel_lift", 1.0 if st["two_lift_time"] > 0 else 0.0, "1"),
               _res("max_lateral_accel", st["max_ay"], "m/s^2"), _res("max_lateral_accel_g", st["max_ay"] / G0, "g"),
               _res("max_roll", math.degrees(st["max_roll"]), "deg"),
               _res("max_path_error", st["max_err"], "m"), _res("max_steer", math.degrees(st["max_steer"]), "deg")]
        if self.flag(lp, "find_limit"):
            lo, hi = self.q(lp, "v_min", "m/s"), self.q(lp, "v_max", "m/s")
            if self._lift(veh, lo, y_ref, x_end):
                res.append(_res("max_speed_no_lift", lo * 3.6, "km/h", "lifts already at the lowest speed tried"))
            elif not self._lift(veh, hi, y_ref, x_end):
                res.append(_res("max_speed_no_lift", hi * 3.6, "km/h", "no lift up to the highest speed tried"))
            else:
                for _ in range(7):
                    mid = 0.5 * (lo + hi)
                    if self._lift(veh, mid, y_ref, x_end):
                        hi = mid
                    else:
                        lo = mid
                res.append(_res("max_speed_no_lift", lo * 3.6, "km/h"))
        rec = st["recorder"]
        scene = dlc_scene(2.0 * _half_width(veh), sign, offset=self.q(lp, "offset", "m"))
        return {"results": res, "series": rec.series(), "vectors": rec.vectors(-1), "scene": scene,
                "assumptions": notes + ["the cones and gates are drawn around the centre line; the driver model "
                                        "follows the line and the gates are not judged"]}


# ------------------------------------------------------------------ offroad roll
class OffroadRollTool(K2Tool):
    """Rough side slope (S5): an ISO 8608 road on a side slope, driven straight at a held speed.

    Results: min_wheel_load (N, where), wheel_lift (1), two_wheel_lift_time (s, both
    uphill wheels off at once), min_uphill_load (N, the uphill wheels' sum),
    mean_uphill_load (N, its mean over the run), max_roll (deg, against the slope),
    rms_vertical_accel (m/s^2, body axis).
    """

    name = "offroad_roll"
    title = "凸凹の横斜面 (K2 で ISO 8608 の路面)"
    domain = "handling"
    tests = ["S5"]
    params = (P("road_class", None, "C", "ISO 8608 road class (values not checked against the ISO text)",
                choices=tuple("ABCDEFGH")),
              P("side_slope", "deg", 15.0, "side slope"),
              P("slope_up", None, "left", "the side whose wheels are uphill", choices=("left", "right")),
              P("speed", "km/h", 20.0, "held speed"),
              P("length", "m", 100.0, "distance driven"),
              P("seed", "1", 1.0, "random phases of the road (an integer)"),
              MU)
    pairs = (Pair("mean_uphill_load", "tilt.side_slope_uphill_load", "W_up", (("beta", "side_slope"),),
                  "rigid balance on the slope; K2's body also rolls downhill on its springs, and min_uphill_load "
                  "has no hand calculation (the road roughness)"),
             Pair("mean_uphill_load", "lane_change.inner_wheel_load", "W_inner",
                  note="a_y = tan(side_slope) in g, the slope taken as a lateral acceleration: that is high by "
                       "1 / cos(side_slope) (3.5 % at 15 deg)"))

    def own_checks(self) -> list[dict]:
        from scipy.signal import welch

        from .road import ISO8608_GD_N0, RandomRoad, iso8608_psd
        road = RandomRoad("C", 0.6, seed=7, fade_start=-1.0, fade_end=-0.5)
        dx = 0.1
        x = np.arange(0.0, 3000.0, dx)
        z, _ = road.track(x, 0)
        f, pxx = welch(z, fs=1.0 / dx, nperseg=4096)
        band = (f >= 0.05) & (f <= 2.0)
        ratio = float(np.mean(pxx[band] / iso8608_psd(f[band], "C")))
        out = [_check("generated ISO 8608 class C track, Welch PSD / G_d(n0)(n/n0)^-2 over 0.05-2 cycle/m", "psd_ratio",
                      1.0, ratio, "1", 0.10, "G_d(n) = G_d(n0) (n / n0)^-w, n0 = 0.1 cycle/m, w = 2, "
                      f"G_d(n0) = {ISO8608_GD_N0['C']:g} m^3 (class C, not checked against the ISO text)")]
        d = box_design(mass=500.0, cg_x=-0.9, cg_z=0.5, track=1.2, susp="rigid",
                       variables={"tyre_stiffness": {"value": 10000.0, "unit": "N/mm"},
                                  "tire_mu": {"value": 2.0, "unit": "1"}}, name="side slope check, rigid")
        veh = build_vehicle(d)
        sim = new_sim(veh, settle_s=1.0)
        beta = math.radians(15.0)
        _ramp_gravity(sim, side_gravity, beta)
        up = sum(sim.out["ntot"][i] for i in _side_ids(sim, True))
        w = veh.m_total * G0
        h = float(veh.cg_total[2])
        expected = w * (math.cos(beta) * 0.6 - math.sin(beta) * h) / 1.2
        out.append(_check("rigid vehicle at rest on a 15 deg side slope: uphill wheel loads", "uphill_load", expected, up,
                          "N", 5e-3, "moment about the downhill contact line: W (cos b t/2 - sin b h) / t"))
        return out

    def _run(self, design: dict, lp: dict) -> dict:
        from .road import ISO8608_GD_N0, RandomRoad
        veh = self.vehicle(design, lp)
        if "road_class" in lp:
            lp = dict(lp, road_class=str(lp["road_class"]).upper())
        cls = self.text(lp, "road_class")
        notes = list(veh.assumptions) + [f"ISO 8608 class {cls}: G_d(n0) = {ISO8608_GD_N0[cls]:g} m^3, w = 2 "
                                         "(not checked against the ISO text); left and right tracks uncorrelated"]
        beta = self.q(lp, "side_slope", "rad")
        left_up = self.text(lp, "slope_up") == "left"
        speed = self.q(lp, "speed", "m/s")
        length = self.q(lp, "length", "m")
        seed = int(lp["seed"]) if isinstance(lp.get("seed"), str) else int(self.q(lp, "seed", "1"))
        ys = [abs(float(c.pos[1])) for c in veh.corners]
        road = RandomRoad(cls, max(ys) if ys else 0.6, seed=seed)
        g = (side_gravity, beta if left_up else -beta)
        st = path_run(veh, speed, lambda x: 0.0, length, dt=self.dt, rec_hz=self.record_hz, road=road, gravity=g)
        cid, mn = _min_load(st["min_load"])
        az = np.array(st["az"])
        res = [_res("min_wheel_load", mn, "N", cid), _res("wheel_lift", 1.0 if st["any_lift"] else 0.0, "1"),
               _res("two_wheel_lift_time", st["two_lift_time"], "s"),
               _res("min_uphill_load", st["min_side"]["left" if left_up else "right"], "N"),
               _res("mean_uphill_load", st["mean_side"]["left" if left_up else "right"], "N"),
               _res("max_roll", math.degrees(st["max_roll"]), "deg"),
               _res("rms_vertical_accel", float(np.sqrt(np.mean((az - az.mean()) ** 2))) if az.size else 0.0, "m/s^2")]
        rec = st["recorder"]
        # the road under the two wheel tracks, every 0.1 m from behind the start to past the end
        x = np.arange(-_length(veh) - 1.0, length + _length(veh) + 1.0, 0.1)
        zl, _ = road.track(x, 0)
        zr, _ = road.track(x, 1)
        ht = road.half_track * 1000.0
        profile = {"x_mm": [round(float(v) * 1000.0, 1) for v in x],
                   "z_mm": {"left": [round(float(v) * 1000.0, 2) for v in zl],
                            "right": [round(float(v) * 1000.0, 2) for v in zr]}}
        side = "左" if left_up else "右"
        scene = [{"kind": "road_profile", "profile": profile,
                  "label": f"ISO 8608 クラス {cls} (seed {seed})。left は y = +{ht:.0f} mm、right は y = -{ht:.0f} mm の"
                           "通り道の高さ。その間は直線、外は近い方の通り道と同じ"},
                 _slope(f"横 {math.degrees(beta):g}° の斜面 ({side}が上)", veh, float(x[0]), float(x[-1]),
                        roll=beta if left_up else -beta)]
        return {"results": res, "series": rec.series(), "vectors": rec.vectors(-1), "scene": scene,
                "assumptions": notes}


# ------------------------------------------------------------------ step climb
def step_push(veh, h: float, stepping: list[int], *, dt: float = 1e-3, y_range=None, ramp_s: float = 4.0,
              run_up: float = 2.0, rec_hz: float = 0.0, after_s: float = 0.0) -> dict:
    """Quasi-static push: the stepping wheels stand 0.5 mm before the step corner and the
    drive torque grows slowly from zero until they leave the low ground.

    The forces are taken when the stepping wheel's road force is steepest (its ground
    contact has just unloaded), which is the lift-off of the rigid-wheel balance.
    run_up: the step for the dynamic run is placed this far further (m).
    rec_hz > 0 records the push; after_s goes on that long after the lift-off with the
    torque of the lift-off held, so the record shows the wheels rolling onto the step.
    """
    from .road import step as step_road
    xs = max(float(veh.corners[i].pos[0]) for i in stepping)
    r = max(veh.corners[i].radius for i in stepping)
    hh = min(h, r * 0.999)
    d = math.sqrt(2.0 * r * hh - hh * hh)
    x_step = xs + d + 0.0005
    road = step_road(x_step, h, y_range)
    sim = new_sim(veh, road=road, dt=dt, settle_s=1.0)
    static = list(sim.out["ntot"])
    driven = [i for i, c in enumerate(veh.corners) if c.drive]
    if not driven:
        raise DesignError("no support has actuation.drive; a driven wheel is needed to climb")
    w = veh.m_total * G0
    t_need = w * max(d / max(r - hh, 1e-3), 0.2) * r
    rate = t_need / ramp_s
    # lifted: the centre is 10 mm above its free radius over the low ground (the tyre
    # alone rises a few mm as its ground contact unloads, before the real lift-off)
    z_free = [veh.corners[i].radius + 0.01 for i in stepping]
    rec = Recorder(sim, rec_hz) if rec_hz > 0 else None
    best, snap, lifted, slip = -1.0, None, False, False

    def push(total: float) -> None:
        drive = [0.0] * sim.n
        for i in driven:
            drive[i] = total / len(driven)
        sim.step(controls(sim, 0.0, drive, [0.0] * sim.n))
        if rec is not None:
            rec.tick({"wheel_torque": ("N*m", total)})

    while sim.t < 6.0 * ramp_s:
        total = rate * sim.t
        push(total)
        o = sim.out
        for i in stepping:
            fx, _, fz = o["f"][i]
            if fz > 0.0 and -fx / fz > best:
                best = -fx / fz
                snap = {"f": [tuple(v) for v in o["f"]], "p": [tuple(v) for v in o["p"]], "n": list(o["ntot"]),
                        "fn": [(o["n"][k][0] * o["ntot"][k], o["n"][k][1] * o["ntot"][k], o["n"][k][2] * o["ntot"][k])
                               for k in range(sim.n)],
                        "acc": tuple(o["acc"]), "cg": sim.cg_world().tolist(), "t": sim.t, "torque": total,
                        "g": list(sim.g)}
                if rec is not None:
                    rec.snap = []
                    rec.keep_snapshot()
        wc = sim.wheel_centres()
        if all(wc[i][2] > z_free[k] for k, i in enumerate(stepping)):
            lifted = True
            break
        if any(abs(sim.om[i]) * veh.corners[i].radius > 0.5 for i in driven) and sim.forward_speed() < 0.05:
            slip = True
            break
    if lifted and after_s > 0.0:
        held = rate * sim.t
        t_end = sim.t + after_s
        while sim.t < t_end:
            push(held)
    return {"ratio": best, "snap": snap, "static": static, "lifted": lifted, "slip": slip, "x_corner": x_step,
            "x_step": x_step + run_up, "road": road, "recorder": rec}


class StepClimbTool(K2Tool):
    """Step climb driven in K2 (step_climb_dynamic; P1, feeds R1). The analysis
    department's step_climb is the hand-calculation balance -> K1; this one drives over
    the step and is the moving picture and the second opinion.

    Two runs. (1) Quasi-static: the stepping wheels stand against the step corner and the
    drive torque grows slowly until they leave the low ground; the forces at that moment
    compare with the K0 rigid-wheel balance. (2) The test speed with the motor's torque
    (unlimited when the motor is not given): does it climb, and the peak wheel forces.
    Results (named front_* when the stepping wheels are the front ones, step_* otherwise):
    *_wheel_force (N, the push |F_x| at lift-off), *_wheel_load (N, its vertical force),
    *_wheel_load_static (N), force_ratio (1, F_x / F_z of that wheel), push_traction (N, the
    driven wheels' forward force), required_mu_push (1), climbed (1, run 2; where says
    "grip" or "torque" when it did not), peak_wheel_force (N, run 2, where),
    peak_vertical_wheel_force (N, run 2), climb_time (s, run 2).
    series, vectors and scene all come from run 1: the series run from the standstill
    against the step until 1.5 s after the lift-off (the torque of the lift-off held), the
    vectors are that run at lift-off (each wheel's reaction and grip, and the inertia
    force -(road forces + weight) at the CG, so the set balances the weight), and the
    scene's step stands where that run's step is. Run 2 gives numbers only.
    """

    name = "step_climb_dynamic"
    title = "段差越え (K2 で段差を走らせる)"
    domain = "powertrain"
    tests = ["P1", "R1"]
    params = (P("step_height", "mm", None, "height of the step", required=True),
              P("speed", "km/h", 10.0, "speed of run 2"),
              P("step_supports", None, None, "comma-separated support ids that meet the step "
                                             "(default: the front-most supports)"),
              MU, *MOTOR)
    pairs = (Pair("front_wheel_force", "step_climb.pushed_wheel_load", "F", (("h", "step_height"),)),
             Pair("front_wheel_force", "step_climb.wheel_force_rigid", "F", (("h", "step_height"),),
                  "W = a front wheel's load; with the static load it comes out high, since the push moves "
                  "load to the rear (step_climb.pushed_wheel_load)"),
             Pair("front_wheel_load", "step_climb.pushed_wheel_load", "W", (("h", "step_height"),)),
             Pair("front_wheel_load", "mass_props.front_wheel_load", "W",
                  note="static: high, the push moves load to the rear (step_climb.pushed_wheel_load)"),
             Pair("front_wheel_load_static", "mass_props.front_wheel_load", "W"),
             Pair("force_ratio", "step_climb.self_climb", "mu", (("h", "step_height"),),
                  "both are d / (r - h) for a rigid wheel: F_x / F_z at lift-off"))

    def own_checks(self) -> list[dict]:
        d = box_design(mass=500.0, cg_x=-1.0, cg_z=0.5, wheelbase=2.0, susp="rigid", radius=0.3,
                       variables={"tyre_stiffness": {"value": 10000.0, "unit": "N/mm"},
                                  "tire_mu": {"value": 1.5, "unit": "1"},
                                  "rolling_resistance": {"value": 0.0, "unit": "1"}}, name="step check, rigid")
        veh = build_vehicle(d)
        front = [i for i, c in enumerate(veh.corners) if c.steer]
        r = step_push(veh, 0.10, front)
        dd = math.sqrt(2 * 0.3 * 0.1 - 0.1 ** 2)
        k = dd / (0.3 - 0.1)
        out = [_check("stiff wheel r = 300 mm on a 100 mm step", "force_ratio", k, r["ratio"], "1", 2e-2,
                      "rigid wheel on a step corner: d = sqrt(2 r h - h^2), F/W = d / (r - h)")]
        snap = r["snap"]
        wf = sum(snap["f"][i][2] for i in front)
        w = veh.m_total * G0
        a = 1.0
        expected = w * (2.0 - a) / 2.0 / (1.0 + k * 0.3 / 2.0)
        out.append(_check("both front wheels on a 100 mm step, pushed by the rear: front axle load at lift-off",
                          "front_axle_load", expected, wf, "N", 3e-2,
                          "moment balance with the push at axle height: W_f = m g (L - a) / L / (1 + k r / L)"))
        return out

    def _run(self, design: dict, lp: dict) -> dict:
        veh = self.vehicle(design, lp)
        notes = list(veh.assumptions)
        h = self.q(lp, "step_height", "m")
        if h is None or h <= 0:
            return {"error": "step_climb_dynamic needs a positive step_height (a length) in the load case params"}
        ids = veh.ids()
        if self.text(lp, "step_supports") is not None:
            want = [x.strip() for x in str(lp["step_supports"]).split(",") if x.strip()]
            bad = [x for x in want if x not in ids]
            if bad:
                return {"error": f"step_supports {', '.join(bad)} are not supports of the design"}
            stepping = [ids.index(x) for x in want]
        else:
            xmax = max(float(c.pos[0]) for c in veh.corners)
            stepping = [i for i, c in enumerate(veh.corners) if abs(float(c.pos[0]) - xmax) < 1e-9]
        sides = {veh.corners[i].side for i in stepping}
        y_range = None
        if sides == {1.0}:
            y_range = (0.0, math.inf)
        elif sides == {-1.0}:
            y_range = (-math.inf, 0.0)
        xmax = max(float(c.pos[0]) for c in veh.corners)
        tag = "front" if all(abs(float(veh.corners[i].pos[0]) - xmax) < 1e-9 for i in stepping) else "step"

        crawl = step_push(veh, h, stepping, dt=self.dt, y_range=y_range, rec_hz=self.record_hz, after_s=1.5)
        res = []
        vectors = []
        snap = crawl["snap"]
        if snap is not None:
            lead = max(stepping, key=lambda i: abs(snap["f"][i][0]))
            lid = ids[lead]
            fx, fy, fz = snap["f"][lead]
            driven = [i for i, c in enumerate(veh.corners) if c.drive and i not in stepping]
            push = sum(snap["f"][i][0] for i in driven)
            nd = sum(snap["n"][i] for i in driven)
            res += [_res(f"{tag}_wheel_force", abs(fx), "N", lid), _res(f"{tag}_wheel_load", fz, "N", lid),
                    _res(f"{tag}_wheel_load_static", crawl["static"][lead], "N", lid),
                    _res("force_ratio", crawl["ratio"], "1", lid), _res("push_traction", push, "N")]
            if nd > 0:
                res.append(_res("required_mu_push", push / nd, "1"))
            for i, c in enumerate(veh.corners):
                p = [x * 1000.0 for x in snap["p"][i]]
                fn = snap["fn"][i]
                g = [snap["f"][i][k] - fn[k] for k in range(3)]
                vectors.append({"kind": "reaction", "at": c.id, "origin_mm": p, "vector_N": [float(x) for x in fn]})
                vectors.append({"kind": "grip", "at": c.id, "origin_mm": p, "vector_N": [float(x) for x in g]})
            # d'Alembert force of the whole vehicle: minus its rate of momentum, which in the
            # model equals the road forces plus the weight, so the set balances the weight exactly
            m = veh.m_total
            inertia = [-(sum(snap["f"][i][k] for i in range(len(snap["f"]))) + m * snap["g"][k]) for k in range(3)]
            vectors.append({"kind": "inertia", "at": "cg", "origin_mm": [x * 1000.0 for x in snap["cg"]],
                            "vector_N": [float(x) for x in inertia]})
        if not crawl["lifted"]:
            notes.append("pushing with a growing torque did not lift the stepping wheels"
                         + (" (the driven wheels spun: grip)" if crawl["slip"] else ""))

        # run 2: the test speed with the motor's torque
        from .road import step as step_road
        speed = self.q(lp, "speed", "m/s")
        try:
            motor = self.motor(lp)
        except DesignError:
            motor = None
            notes.append("motor not given: the driven wheels get the torque the speed controller asks for (no limit)")
        road = step_road(crawl["x_step"], h, y_range)
        sim = new_sim(veh, road=road, dt=self.dt, settle_s=1.0)
        start_rolling(sim, speed)
        ctrl = SpeedController(sim)
        driven = [i for i, c in enumerate(veh.corners) if c.drive]
        peak, peak_id, peak_z, slow_t, t_climb = 0.0, None, 0.0, 0.0, None
        rear_x = min(float(c.pos[0]) for c in veh.corners)
        reason = None
        x_done = crawl["x_step"] + (xmax - rear_x) + 0.6
        while sim.t < 30.0:
            drive, brake = ctrl.torques(sim, speed)
            if motor is not None:
                wbar = sum(sim.om[i] for i in driven) / len(driven)
                cap = motor.wheel_torque_total(wbar) / len(driven)
                drive = [min(x, cap) for x in drive]
            sim.step(controls(sim, 0.0, drive, brake))
            o = sim.out
            for i, c in enumerate(veh.corners):
                fmag = math.sqrt(sum(x * x for x in o["f"][i]))
                if fmag > peak:
                    peak, peak_id = fmag, c.id
                peak_z = max(peak_z, o["f"][i][2])
            if t_climb is None and sim.design_origin()[0] > x_done:
                t_climb = sim.t
            if t_climb is not None and sim.t > t_climb + 0.8:
                break
            slow_t = slow_t + sim.dt if sim.forward_speed() < 0.05 else 0.0
            if slow_t > 2.0:
                slipping = any(abs(sim.om[i] * veh.corners[i].radius - sim.forward_speed()) > 0.2 for i in driven)
                reason = "grip" if slipping or motor is None else "torque"
                break
        res += [_res("climbed", 1.0 if t_climb is not None else 0.0, "1", reason),
                _res("peak_wheel_force", peak, "N", peak_id), _res("peak_vertical_wheel_force", peak_z, "N")]
        if t_climb is not None:
            res.append(_res("climb_time", t_climb, "s"))
        rec = crawl["recorder"]
        y0, y1 = (-_half_width(veh) - 0.5, _half_width(veh) + 0.5) if y_range is None else \
            ((0.0, _half_width(veh) + 0.5) if y_range[0] >= 0.0 else (-_half_width(veh) - 0.5, 0.0))
        x0 = crawl["x_corner"]
        scene = [_box("step", f"段差 {h * 1000:.0f} mm (1 回目の押し上げの位置)",
                      (x0, y0, 0.0), (x0 + _length(veh) + 3.0, y1, h))]
        return {"results": res, "series": rec.series(), "vectors": vectors or rec.vectors(-1), "scene": scene,
                "assumptions": notes}


TOOLS = {t.name: t for t in (TiltTool(), CrosswindTool(), BrakingTool(), LaunchTool(), LaneChangeTool(),
                             OffroadRollTool(), StepClimbTool())}
