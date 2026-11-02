"""K2 scenarios as contract AnalysisTools (hiraku.contract.protocols.AnalysisTool).

Each tool: spec() -> tool_spec, check() -> tool_check list (the six pre-run
checks plus the tool's own known answers), run(design, load_case,
predictions, params) -> analysis record without analysis_id.

Load-case params each tool reads are listed in the tool's docstring; all are
contract quantities (or strings / booleans). params.loading (empty, driver,
two, two_cargo; default two_cargo) chooses the occupant and cargo parts on
board for every tool. Tool options in `params` (not the load case): dt (s),
record_hz.
"""

from __future__ import annotations

import math
import time
from functools import lru_cache

import numpy as np
from scipy.optimize import brentq

from .checks import _check, base_checks
from .drive import RHO_AIR, Aero, Motor, PathDriver, SpeedController, controls, on_board, start_rolling
from .model import DesignError, Vehicle, box_design, build_vehicle
from .record import Recorder
from .scenario import HOLD_TORQUE, hold, new_sim, settle, side_gravity, slope_gravity, tilt_sweep
from .units import G0, param, si

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


class K2Tool:
    name = "k2"
    title = "K2"
    domain = "handling"
    tests: list[str] = []
    formulas: list[str] = []

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
                "formulas": list(self.formulas)}

    def run(self, design: dict, load_case: dict, predictions: list[dict], params: dict | None = None) -> dict:
        t0 = time.perf_counter()
        rec = {"tool": self.name, "base": "K2", "design_version": int(design.get("design_version", 1)),
               "load_case_id": load_case.get("load_case_id", ""),
               "prediction_ids": [p["prediction_id"] for p in predictions if "prediction_id" in p],
               "status": "ok", "results": []}
        lp = dict(load_case.get("params") or {})
        self.opts = {k: v for k, v in (params or {}).items() if k != "upstream_analyses"}
        try:
            design = on_board(design, lp.get("loading"))
            out = self._run(design, lp)
        except (DesignError, ValueError) as e:
            rec["status"] = "failed"
            rec["error"] = str(e)
            out = {}
        rec["results"] = out.get("results", [])
        if out.get("series"):
            rec["series"] = out["series"]
        if out.get("vectors"):
            rec["vectors"] = out["vectors"]
        if "assumptions" in _ANALYSIS_PROPS and out.get("assumptions"):
            rec["assumptions"] = out["assumptions"]
        if out.get("error"):
            rec["status"] = "failed"
            rec["error"] = out["error"]
        rec["wall_time_s"] = time.perf_counter() - t0
        self.last_assumptions = out.get("assumptions", [])
        return rec

    def _run(self, design: dict, lp: dict) -> dict:  # pragma: no cover
        raise NotImplementedError

    def vehicle(self, design: dict, lp: dict, **overrides) -> Vehicle:
        mu = param(lp, "mu", "1", None)
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

    Load-case params: direction "left" | "right" | "both" (default both; "right" = the
    table's right side goes down), table_mu (1, default 2.0: the table surface holds the
    tyres so tipping, not sliding, is found), mu (1, optional: the road friction; reports
    whether the vehicle would slide before it tips).
    Results: tilt_angle (deg, the smaller side), tilt_angle_right, tilt_angle_left (deg),
    static_stability_factor (1, T/2h on the settled vehicle), body_roll_at_tip (deg),
    cg_shift_at_tip (mm, lateral CG shift against the contact line from suspension roll),
    friction_demand_at_tip (1), slides_first (1: 1 if mu < the demand).
    """

    name = "tilt"
    title = "傾斜台 (K2 で台をゆっくり傾ける)"
    domain = "handling"
    tests = ["S1", "S1'"]

    def own_checks(self) -> list[dict]:
        d = box_design(mass=500.0, cg_x=-0.9, cg_z=0.5, track=1.2, k_w=20.0, wheel_mass=20.0, travel=200.0,
                       variables={"tire_vertical_stiffness": {"value": 10000.0, "unit": "N/mm"},
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
        table_mu = param(lp, "table_mu", "1", 2.0)
        veh = build_vehicle(design, {"mu": table_mu})
        direction = str(lp.get("direction", "both"))
        sides = {"both": ["right", "left"], "right": ["right"], "left": ["left"]}.get(direction)
        if sides is None:
            return {"error": f"direction must be left, right or both, not {direction!r}"}
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
        mu_road = param(lp, "mu", "1", None)
        if mu_road is not None:
            res.append(_res("slides_first", 1.0 if mu_road < demand else 0.0, "1"))
        return {"results": res, "series": rec.series(), "vectors": rec.vectors(run["snapshot"]),
                "assumptions": veh.assumptions + [f"table surface friction {table_mu:g} so the tyres do not slide"]}


def _ssf(veh) -> float:
    """T / 2h of the vehicle as designed (track between the outermost supports, total CG height)."""
    ys = [float(c.pos[1]) for c in veh.corners]
    t = max(ys) - min(ys)
    return t / (2.0 * float(veh.cg_total[2]))


# ------------------------------------------------------------------ helpers
def _side_ids(sim, left: bool) -> list[int]:
    return [i for i, c in enumerate(sim.veh.corners) if c.side != 0 and (c.side > 0) == left]


def _wind_world(speed: float, from_left: bool) -> tuple:
    return (0.0, -speed if from_left else speed, 0.0)


def _aero(veh, lp: dict, cd_default: float = 0.0, cy_default: float = 0.0) -> Aero:
    return Aero.of(veh, param(lp, "rho", "kg/m^3", RHO_AIR), param(lp, "cd", "1", cd_default),
                   param(lp, "cy", "1", cy_default), param(lp, "front_area", "m^2", None),
                   param(lp, "side_area", "m^2", None))


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

    Load-case params: wind_speed (m/s, default 30: the gust), speed (km/h, default 0),
    cy (1, default 1.0), cd (1, default 0.8), rho (kg/m^3, default 1.225), side_area /
    front_area (m^2, default from the part boxes), wind_from "left" | "right" (default left),
    drift_time (s, default 2.0), mu (1).
    Results: critical_wind_speed (m/s: at rest, the tyres held from sliding (mu 2), the
    upwind wheels lift), slide_wind_speed (m/s: at rest with the road mu, the vehicle starts
    to slide sideways), slides_first (1), side_force (N at wind_speed),
    min_upwind_wheel_load (N, a gust of wind_speed reaching the vehicle at `speed` with the
    steering held straight), lateral_drift (m, drift_time after the gust).
    """

    name = "crosswind"
    title = "横風 (K2 に横風の力を入れる)"
    domain = "aero"
    tests = ["F1"]

    def own_checks(self) -> list[dict]:
        d = box_design(mass=500.0, cg_x=-0.9, cg_z=0.5, track=1.2, susp="rigid",
                       variables={"tire_vertical_stiffness": {"value": 10000.0, "unit": "N/mm"},
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
        aero = _aero(veh, lp, 0.8, 1.0)
        notes = list(veh.assumptions)
        for k, v in (("cy", 1.0), ("cd", 0.8)):
            if k not in lp:
                notes.append(f"{k} = {v} (assumed)")
        if "rho" not in lp:
            notes.append("air density 1.225 kg/m^3 (ISO 2533 sea level; not checked against the text)")
        from_left = str(lp.get("wind_from", "left")) != "right"
        gust = param(lp, "wind_speed", "m/s", 30.0)
        speed = param(lp, "speed", "m/s", 0.0)
        drift_t = param(lp, "drift_time", "s", 2.0)
        held = build_vehicle(design, {"mu": 2.0})
        ramp = wind_ramp(held, _aero(held, lp, 0.8, 1.0), from_left, dt=self.dt)
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
            "max_pitch": max_pitch, "recorder": rec, "sim": sim}


class BrakingTool(K2Tool):
    """Emergency stop (S4) and parking on a slope (S4b).

    Load-case params: speed (km/h, default 35), mu (1), reaction_time (s, default 0),
    brake_torque (N*m per braked wheel, default: enough to lock, no ABS), grade (%,
    default 0, + uphill), parking_slope (%, optional: also park there, brakes on, 60 s),
    cd (1, default 0: no air drag).
    Results: stopping_distance (m, from the brake command), braking_distance (m, from the
    brakes coming on), mean_decel (m/s^2 = v^2 / 2 s_braking), peak_decel (m/s^2),
    min_wheel_load (N, where = support), min_rear_wheel_load (N, supports behind the CG:
    0 = the rear lifts), max_pitch (deg, nose down +), parking_drift (mm).
    """

    name = "braking"
    title = "急制動 (K2 で急ブレーキ)"
    domain = "handling"
    tests = ["S4", "S4b"]

    def own_checks(self) -> list[dict]:
        d = box_design(mass=500.0, cg_x=-0.9, cg_z=0.5, wheel_mass=20.0, name="braking check",
                       variables={"tire_mu": {"value": 0.7, "unit": "1"},
                                  "tire_rolling_resistance": {"value": 0.0, "unit": "1"}})
        veh = build_vehicle(d)
        v0 = 40.0 / 3.6
        r = brake_stop(veh, v0)
        expected = v0 * v0 / (2.0 * 0.7 * G0)
        return [_check("locked wheels from 40 km/h, mu 0.7, no air drag or rolling resistance", "braking_distance",
                       expected, r["braking"], "m", 1e-2, "s = v^2 / (2 mu g)")]

    def _run(self, design: dict, lp: dict) -> dict:
        veh = self.vehicle(design, lp)
        notes = list(veh.assumptions)
        v0 = param(lp, "speed", "m/s", 35.0 / 3.6)
        cd = param(lp, "cd", "1", 0.0)
        aero = _aero(veh, lp, 0.0, 0.0) if cd > 0 else None
        tq = param(lp, "brake_torque", "N*m", None)
        if tq is None:
            notes.append("brakes strong enough to lock every braked wheel (no ABS)")
        r = brake_stop(veh, v0, reaction=param(lp, "reaction_time", "s", 0.0), brake_torque=tq,
                       grade=param(lp, "grade", "1", 0.0), dt=self.dt, aero=aero, rec_hz=self.record_hz)
        cid, mn = _min_load(r["min_load"])
        x_cg = float(veh.cg_total[0])
        rear = [c.id for c in veh.corners if float(c.pos[0]) < x_cg]
        res = [_res("stopping_distance", r["stopping"], "m"), _res("braking_distance", r["braking"], "m"),
               _res("mean_decel", v0 * v0 / (2.0 * r["braking"]), "m/s^2"), _res("peak_decel", r["peak_decel"], "m/s^2"),
               _res("min_wheel_load", mn, "N", cid)]
        if rear:
            rid = min(rear, key=lambda k: r["min_load"][k])
            res.append(_res("min_rear_wheel_load", r["min_load"][rid], "N", rid))
        res.append(_res("max_pitch", math.degrees(r["max_pitch"]), "deg"))
        slope = param(lp, "parking_slope", "1", None)
        if slope is not None:
            from .checks import parked_drift
            res.append(_res("parking_drift", parked_drift(veh, slope, 60.0, self.dt)["drift"] * 1000.0, "mm"))
        rec = r["recorder"]
        return {"results": res, "series": rec.series(), "vectors": rec.vectors(-1), "assumptions": notes}


# ------------------------------------------------------------------ launch
def _motor(design: dict, lp: dict) -> Motor:
    var = design.get("variables") or {}

    def get(names, want):
        for src in (lp, var):
            for n in names:
                if n in src:
                    return si(src[n], want)
        return None

    torque = get(("motor_torque", "motor_peak_torque"), "N*m")
    power = get(("motor_power", "motor_peak_power"), "W")
    gear = get(("gear_ratio",), "1")
    if torque is None or gear is None:
        raise DesignError("give motor_torque (N*m at the motor), gear_ratio (1) and motor_power (kW) "
                          "in the load case params or design.variables")
    eff = get(("driveline_efficiency", "efficiency"), "1")
    vmax = get(("motor_max_speed",), "rad/s")
    return Motor(torque, power if power is not None else math.inf, gear, 0.9 if eff is None else eff,
                 math.inf if vmax is None else vmax)


def launch_run(veh, motor: Motor, target: float, *, grade: float = 0.0, max_time: float = 30.0, dt: float = 1e-3,
               aero: Aero | None = None, rec_hz: float = 0.0) -> dict:
    """Full throttle from rest until `target` speed (then 1 s more), or until it stops accelerating."""
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
        if sim.t >= max_time or (t_hit is not None and sim.t > t_hit + 1.0):
            break
        if t_hit is None and k % 1000 == 0 and len(hist) > 2000 and hist[-1] - hist[-1001] < 0.02:
            break  # no longer accelerating: the top speed is below the target
    return {"time": t_hit, "distance": x_hit, "top_speed": vmax, "slip_max": slip_max, "recorder": rec,
            "moved": float(sim.design_origin()[0]) - x0, "sim": sim}


class LaunchTool(K2Tool):
    """Launch and acceleration (P2, P3) with the motor's torque curve.

    Load-case params: target_speed (km/h, default 40), grade (%, default 0), motor_torque
    (N*m at the motor), motor_power (kW), gear_ratio (1), efficiency (1, default 0.9),
    motor_max_speed (rpm, optional) - these also from design.variables - max_time (s,
    default 30), cd (1, default 0.8), mu (1).
    Results: time_to_speed (s), distance_to_speed (m), mean_accel (m/s^2), top_speed (km/h,
    highest speed reached), reached_target (1), max_wheel_slip (1), moved_uphill (m, on a grade).
    """

    name = "launch"
    title = "発進・加速 (K2 にモーターのトルクの線)"
    domain = "powertrain"
    tests = ["P2", "P3"]

    def own_checks(self) -> list[dict]:
        d = box_design(mass=500.0, cg_x=-0.9, cg_z=0.5, wheel_mass=20.0, name="launch check",
                       variables={"tire_mu": {"value": 1.0, "unit": "1"},
                                  "tire_rolling_resistance": {"value": 0.0, "unit": "1"}})
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
        motor = _motor(design, lp)
        target = param(lp, "target_speed", "m/s", 40.0 / 3.6)
        grade = param(lp, "grade", "1", 0.0)
        aero = _aero(veh, lp, 0.8, 0.0)
        if "cd" not in lp:
            notes.append("cd = 0.8 (assumed)")
        r = launch_run(veh, motor, target, grade=grade, max_time=param(lp, "max_time", "s", 30.0), dt=self.dt,
                       aero=aero, rec_hz=self.record_hz)
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
        return {"results": res, "series": rec.series(), "vectors": rec.vectors(-1), "assumptions": notes}


# ------------------------------------------------------------------ lane change
# Section lengths (m) and lane offset of a double lane change in the layout of
# ISO 3888-1: 15 / 30 / 25 / 25 / 15 / 15 m, offset 3.5 m. NOT checked against the
# ISO 3888-1 text; the course here is a smooth centre line, not the cone gates.
DLC_SECTIONS = (15.0, 30.0, 25.0, 25.0, 15.0, 15.0)
DLC_OFFSET = 3.5


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
          "az": []}
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
        st["min_side"]["left"] = min(st["min_side"]["left"], sum(nt[i] for i in left))
        st["min_side"]["right"] = min(st["min_side"]["right"], sum(nt[i] for i in right))
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
    st["finished"] = sim.design_origin()[0] >= x_end
    st["recorder"] = rec
    st["sim"] = sim
    return st


class LaneChangeTool(K2Tool):
    """Double lane change (S3; also S2 / S2b courses later) with a path-following driver.

    The driver is pure pursuit on the course centre line (preview 0.6 s, at least 3 m,
    steering rate 400 deg/s) and holds the speed. A wheel has lifted when its road normal
    force is zero.
    Load-case params: speed (km/h, default 40), mu (1), offset (m, default 3.5), direction
    "left" | "right" (first lane change toward; default left), find_limit (bool, default
    true), v_min / v_max (km/h, default 10 / 80: the search range of the limit).
    Results at `speed`: min_wheel_load (N, where), wheel_lift (1), two_wheel_lift (1, both
    wheels of one side off at once), max_lateral_accel (m/s^2), max_roll (deg),
    max_path_error (m, CG from the centre line), max_steer (deg); and max_speed_no_lift
    (km/h, the highest speed with every wheel on the ground, to about 0.6 km/h).
    """

    name = "lane_change"
    title = "ダブルレーンチェンジ (K2 + 運転のモデル)"
    domain = "handling"
    tests = ["S3"]

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
        speed = param(lp, "speed", "m/s", 40.0 / 3.6)
        sign = -1.0 if str(lp.get("direction", "left")) == "right" else 1.0
        y0, x_end = dlc_path(offset=param(lp, "offset", "m", DLC_OFFSET))
        y_ref = (lambda x: sign * y0(x))
        st = path_run(veh, speed, y_ref, x_end, dt=self.dt, rec_hz=self.record_hz)
        cid, mn = _min_load(st["min_load"])
        res = [_res("min_wheel_load", mn, "N", cid), _res("wheel_lift", 1.0 if st["any_lift"] else 0.0, "1"),
               _res("two_wheel_lift", 1.0 if st["two_lift_time"] > 0 else 0.0, "1"),
               _res("max_lateral_accel", st["max_ay"], "m/s^2"), _res("max_roll", math.degrees(st["max_roll"]), "deg"),
               _res("max_path_error", st["max_err"], "m"), _res("max_steer", math.degrees(st["max_steer"]), "deg")]
        if lp.get("find_limit", True) is not False:
            lo, hi = param(lp, "v_min", "m/s", 10.0 / 3.6), param(lp, "v_max", "m/s", 80.0 / 3.6)
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
        return {"results": res, "series": rec.series(), "vectors": rec.vectors(-1), "assumptions": notes}


# ------------------------------------------------------------------ offroad roll
class OffroadRollTool(K2Tool):
    """Rough side slope (S5): an ISO 8608 road on a side slope, driven straight at a held speed.

    Load-case params: road_class (string, default "C"), side_slope (deg, default 15),
    slope_up "left" | "right" (default left: the left wheels are uphill), speed (km/h,
    default 20), length (m, default 100), seed (1, default 1), mu (1).
    Results: min_wheel_load (N, where), wheel_lift (1), two_wheel_lift_time (s, both
    uphill wheels off at once), min_uphill_load (N, the uphill wheels' sum), max_roll
    (deg, against the slope), rms_vertical_accel (m/s^2, body axis).
    """

    name = "offroad_roll"
    title = "凸凹の横斜面 (K2 で ISO 8608 の路面)"
    domain = "handling"
    tests = ["S5"]

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
                       variables={"tire_vertical_stiffness": {"value": 10000.0, "unit": "N/mm"},
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
        cls = str(lp.get("road_class", "C")).upper()
        if cls not in ISO8608_GD_N0:
            return {"error": f"road_class must be one of {', '.join(ISO8608_GD_N0)}"}
        notes = list(veh.assumptions) + [f"ISO 8608 class {cls}: G_d(n0) = {ISO8608_GD_N0[cls]:g} m^3, w = 2 "
                                         "(not checked against the ISO text); left and right tracks uncorrelated"]
        beta = param(lp, "side_slope", "rad", math.radians(15.0))
        left_up = str(lp.get("slope_up", "left")) != "right"
        speed = param(lp, "speed", "m/s", 20.0 / 3.6)
        length = param(lp, "length", "m", 100.0)
        seed = int(param(lp, "seed", "1", 1.0)) if not isinstance(lp.get("seed"), str) else int(lp["seed"])
        ys = [abs(float(c.pos[1])) for c in veh.corners]
        road = RandomRoad(cls, max(ys) if ys else 0.6, seed=seed)
        g = (side_gravity, beta if left_up else -beta)
        st = path_run(veh, speed, lambda x: 0.0, length, dt=self.dt, rec_hz=self.record_hz, road=road, gravity=g)
        cid, mn = _min_load(st["min_load"])
        az = np.array(st["az"])
        res = [_res("min_wheel_load", mn, "N", cid), _res("wheel_lift", 1.0 if st["any_lift"] else 0.0, "1"),
               _res("two_wheel_lift_time", st["two_lift_time"], "s"),
               _res("min_uphill_load", st["min_side"]["left" if left_up else "right"], "N"),
               _res("max_roll", math.degrees(st["max_roll"]), "deg"),
               _res("rms_vertical_accel", float(np.sqrt(np.mean((az - az.mean()) ** 2))) if az.size else 0.0, "m/s^2")]
        rec = st["recorder"]
        return {"results": res, "series": rec.series(), "vectors": rec.vectors(-1), "assumptions": notes,
                "road": road.describe()}


# ------------------------------------------------------------------ step climb
def step_push(veh, h: float, stepping: list[int], *, dt: float = 1e-3, y_range=None, ramp_s: float = 4.0,
              run_up: float = 2.0) -> dict:
    """Quasi-static push: the stepping wheels stand 0.5 mm before the step corner and the
    drive torque grows slowly from zero until they leave the low ground.

    The forces are taken when the stepping wheel's road force is steepest (its ground
    contact has just unloaded), which is the lift-off of the rigid-wheel balance.
    run_up: the step for the dynamic run is placed this far further (m).
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
    best, snap, lifted, slip = -1.0, None, False, False
    while sim.t < 6.0 * ramp_s:
        total = rate * sim.t
        drive = [0.0] * sim.n
        for i in driven:
            drive[i] = total / len(driven)
        sim.step(controls(sim, 0.0, drive, [0.0] * sim.n))
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
        wc = sim.wheel_centres()
        if all(wc[i][2] > z_free[k] for k, i in enumerate(stepping)):
            lifted = True
            break
        if any(abs(sim.om[i]) * veh.corners[i].radius > 0.5 for i in driven) and sim.forward_speed() < 0.05:
            slip = True
            break
    return {"ratio": best, "snap": snap, "static": static, "lifted": lifted, "slip": slip,
            "x_step": x_step + run_up, "road": road}


class StepClimbTool(K2Tool):
    """Step climb driven in K2 (step_climb_dynamic; P1, feeds R1). The analysis
    department's step_climb is the hand-calculation balance -> K1; this one drives over
    the step and is the moving picture and the second opinion.

    Two runs. (1) Quasi-static: the stepping wheels stand against the step corner and the
    drive torque grows slowly until they leave the low ground; the forces at that moment
    compare with the K0 rigid-wheel balance. (2) The test speed with the motor's torque
    (unlimited when the motor is not given): does it climb, and the peak wheel forces.
    Load-case params: step_height (mm, required), speed (km/h, default 10), step_supports
    (string, default: the front-most supports), mu (1), motor_torque / motor_power /
    gear_ratio (as launch, optional).
    Results (named front_* when the stepping wheels are the front ones, step_* otherwise):
    *_wheel_force (N, the push |F_x| at lift-off), *_wheel_load (N, its vertical force),
    *_wheel_load_static (N), force_ratio (1, F_x / F_z of that wheel), push_traction (N, the
    driven wheels' forward force), required_mu_push (1), climbed (1, run 2; where says
    "grip" or "torque" when it did not), peak_wheel_force (N, run 2, where),
    peak_vertical_wheel_force (N, run 2), climb_time (s, run 2).
    vectors: run 1 at lift-off: each wheel's reaction and grip, and the inertia force
    -(road forces + weight) at the CG, so the set is in balance with the weight.
    """

    name = "step_climb_dynamic"
    title = "段差越え (K2 で段差を走らせる)"
    domain = "powertrain"
    tests = ["P1", "R1"]
    formulas = ["step_climb.wheel_force_rigid", "step_climb.pushed_wheel_load", "step_climb.self_climb"]

    def own_checks(self) -> list[dict]:
        d = box_design(mass=500.0, cg_x=-1.0, cg_z=0.5, wheelbase=2.0, susp="rigid", radius=0.3,
                       variables={"tire_vertical_stiffness": {"value": 10000.0, "unit": "N/mm"},
                                  "tire_mu": {"value": 1.5, "unit": "1"},
                                  "tire_rolling_resistance": {"value": 0.0, "unit": "1"}}, name="step check, rigid")
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
        h = param(lp, "step_height", "m", None)
        if h is None or h <= 0:
            return {"error": "step_climb_dynamic needs a positive step_height (a length) in the load case params"}
        ids = veh.ids()
        if "step_supports" in lp:
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

        crawl = step_push(veh, h, stepping, dt=self.dt, y_range=y_range)
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
        speed = param(lp, "speed", "m/s", 10.0 / 3.6)
        try:
            motor = _motor(design, lp)
        except DesignError:
            motor = None
            notes.append("motor not given: the driven wheels get the torque the speed controller asks for (no limit)")
        road = step_road(crawl["x_step"], h, y_range)
        sim = new_sim(veh, road=road, dt=self.dt, settle_s=1.0)
        rec = Recorder(sim, self.record_hz)
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
            rec.tick()
            o = sim.out
            for i, c in enumerate(veh.corners):
                fmag = math.sqrt(sum(x * x for x in o["f"][i]))
                if fmag > peak:
                    peak, peak_id = fmag, c.id
                    rec.snap = []
                    rec.keep_snapshot()
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
        return {"results": res, "series": rec.series(), "vectors": vectors or rec.vectors(-1), "assumptions": notes}


TOOLS = {t.name: t for t in (TiltTool(), CrosswindTool(), BrakingTool(), LaunchTool(), LaneChangeTool(),
                             OffroadRollTool(), StepClimbTool())}
