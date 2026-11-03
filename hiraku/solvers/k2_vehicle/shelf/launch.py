"""Launch and acceleration (P2, P3)."""

from __future__ import annotations

import math

from ..checks import _check
from ..drive import Aero, Motor, controls
from ..model import DesignError, box_design, build_vehicle
from ..record import Recorder
from ..scenario import new_sim, slope_gravity
from .base import CD, FRONT_AREA, MOTOR, MU, RHO, K2Tool, P, Pair, _length, _ramp_gravity, _res, _slope


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
