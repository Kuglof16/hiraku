"""The base of the K2 tools: declared load-case params, K0 formula pairs, K2Tool, shared helpers."""

from __future__ import annotations

import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache

from hiraku.contract.protocols import DEFAULT_LOADING, LOADINGS

from ..checks import FROM_CACHE, base_checks, cached
from ..drive import RHO_AIR, Aero, Motor, on_board
from ..model import DesignError, Vehicle, build_vehicle
from ..record import Recorder
from ..scenario import hold
from ..units import si


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
        return tuple(cached(self.name, self.own_checks))

    @property
    def checks_from_cache(self) -> bool:
        """The checks came from the tests' disk cache (HIRAKU_CHECK_CACHE=1), not computed in this
        process: the run says so in one line."""
        return "base" in FROM_CACHE or self.name in FROM_CACHE

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
        # the values neither the load case nor the design gave, which K2 took on its own
        assumptions = list(dict.fromkeys(list(out.get("assumptions", [])) + self._assumed))
        if assumptions:
            rec["assumptions"] = assumptions
        unread = sorted(set(lp) - {p.name for p in (LOADING,) + self.params})
        if unread:
            rec["effects"] = [{"name": "load-case params this tool does not read: " + ", ".join(unread)}]
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
        """Write down an assumption of this run (it goes into the record's assumptions)."""
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

    def turn_rate(self, lp: dict, name: str) -> float | None:
        """A rotational speed param (q) in rad/s: rpm and Hz count revolutions (2 pi rad each),
        rad/s and deg/s are angular rates."""
        try:
            rev = self.q(lp, name, "Hz")
        except ValueError:
            return self.q(lp, name, "rad/s")
        return None if rev is None else 2.0 * math.pi * rev

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
        power, vmax = self.q(lp, "motor_power", "W"), self.turn_rate(lp, "motor_max_speed")
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


def _ssf(veh) -> float:
    """T / 2h of the vehicle as designed (track between the outermost supports, total CG height
    above the ground under the wheels)."""
    ys = [float(c.pos[1]) for c in veh.corners]
    t = max(ys) - min(ys)
    ground = min(float(c.pos[2]) - c.radius for c in veh.corners)
    return t / (2.0 * (float(veh.cg_total[2]) - ground))


TIPPED_ROLL = math.radians(60.0)  # a body rolled this far has tipped over (K2 has no body-ground contact)


def _ramp_gravity(sim, grav_of, angle: float, seconds: float = 1.0, rec: Recorder | None = None,
                  up: list[int] | None = None, extra=None) -> float | None:
    """Turn gravity to the slope or table angle with the brakes held, then settle.

    Without rec the clock restarts at 0. With rec the turn is recorded (channel
    slope_angle, deg, plus extra() so the channels stay the same as the run's after it)
    and the clock runs on. up: the wheels on the high side; returns the angle (rad) at
    which they all left the ground, or None. Stops when the body has tipped.
    """
    ctl = hold(sim)
    steps = int(round(seconds / sim.dt))
    lift = None

    def tick(a):
        if rec is not None:
            rec.tick(dict(extra() if extra else {}, slope_angle=("deg", math.degrees(a))))

    for k in range(steps):
        a = angle * (k + 1) / steps
        sim.set_gravity(grav_of(a))
        sim.step(ctl)
        tick(a)
        if up and lift is None and all(sim.out["ntot"][i] <= 0.0 for i in up):
            lift = abs(a)
        if abs(sim.attitude()[0]) > TIPPED_ROLL:
            return lift
    for _ in range(int(round(1.0 / sim.dt))):
        sim.step(ctl)
        tick(angle)
        if abs(sim.attitude()[0]) > TIPPED_ROLL:
            break
    if rec is None:
        sim.t = 0.0
    return lift
