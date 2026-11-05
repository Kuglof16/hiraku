"""Launch and acceleration (P2, P3)."""

from __future__ import annotations

import dataclasses
import math

from ..checks import _check
from ..drive import Aero, Motor, controls
from ..model import DesignError, box_design, build_vehicle
from ..record import Recorder
from ..scenario import new_sim, slope_gravity
from .base import CD, FRONT_AREA, MOTOR, MU, RHO, K2Tool, P, Pair, _length, _ramp_gravity, _res, _slope
from .mounts import LoadLog, has_mounts, mount_peak, mount_series, mount_vectors, state_at

STEADY_GAIN = 0.02     # m/s in 1 s: below this gain the speed has stopped rising (the top speed)
AT_SPEED_LIMIT = 0.99  # share of motor_max_speed at which the motor counts as held by it (the cut-off
#                        keeps it just below the limit)
SPINNING = 0.1         # longitudinal slip of a driven wheel above which grip holds the speed


def driven_rolling_radius(veh) -> float:
    """The mean effective rolling radius (m) of the driven wheels (model.Corner.rolling_radius)."""
    radii = [c.rolling_radius for c in veh.corners if c.drive]
    if not radii:
        raise DesignError("no support has actuation.drive; a driven wheel is needed")
    return sum(radii) / len(radii)


def peak_tractive_force(motor: Motor, r: float) -> float:
    """F_max = T i eta / r (N) at the driven wheels together, the form of K0 launch.peak_tractive_force;
    K2 takes r = driven_rolling_radius."""
    return motor.torque * motor.gear * motor.efficiency / r


def _limit(sim, motor: Motor, driven: list[int]) -> dict:
    """What holds the speed now: grip (a driven wheel spins), motor_max_speed, motor_power
    (above the corner speed) or motor_torque; with the motor speed and the wheel slip."""
    v = sim.forward_speed()
    wbar = sum(sim.om[i] for i in driven) / len(driven)
    slip = max(abs(sim.om[i] * sim.veh.corners[i].rolling_radius - v) for i in driven) / max(abs(v), 0.5)
    wm = abs(wbar) * motor.gear
    if slip > SPINNING:
        by = "grip"
    elif wm >= AT_SPEED_LIMIT * motor.max_speed:
        by = "motor_max_speed"
    elif wm * motor.torque > motor.power:
        by = "motor_power"
    else:
        by = "motor_torque"
    return {"by": by, "motor_speed": wm, "slip": slip, "speed": v}


def launch_run(veh, motor: Motor, target: float, *, grade: float = 0.0, max_time: float = 30.0, dt: float = 1e-3,
               aero: Aero | None = None, rec_hz: float = 0.0, until_top: bool = False,
               power_top: bool = False) -> dict:
    """Full throttle from rest.

    The run ends ("stop"): "target" 1 s after `target` speed (not with until_top);
    "steady" when the speed gained less than STEADY_GAIN in 1 s (with until_top, or before
    the target is reached): the top speed; "max_time" at max_time (s of driving).
    vmax: the highest speed (m/s); end_speed: the speed at the end; limit: _limit() at the end.
    power_top: when the run ended steady at the motor speed limit, drive on from there with
    that limit lifted until steady again (max_time more at most, not recorded): "power_top"
    (dict: vmax, stop, limit) is then the speed where the power (or torque) meets the road
    load, the speed K0's launch.top_speed predicts. Without the limit reached, power_top is
    the run itself.
    """
    sim = new_sim(veh, dt=dt, settle_s=1.0)
    if grade:
        _ramp_gravity(sim, slope_gravity, math.atan(grade))
    rec = Recorder(sim, rec_hz) if rec_hz > 0 else None
    log = LoadLog(sim) if rec is not None and any(has_mounts(c) for c in veh.corners) else None
    driven = [i for i, c in enumerate(veh.corners) if c.drive]
    if not driven:
        raise DesignError("no support has actuation.drive; a driven wheel is needed")
    x0 = float(sim.design_origin()[0])
    per_s = max(1, int(round(1.0 / dt)))
    hit = {"t": None, "x": None, "slip_max": 0.0}

    def drive(mot: Motor, t_end: float, stop_at_target: bool, main: bool) -> tuple[float, str]:
        vmax, hist, k = 0.0, [], 0
        while True:
            wbar = sum(sim.om[i] for i in driven) / len(driven)
            total = mot.wheel_torque_total(wbar)
            torques = [0.0] * sim.n
            for i in driven:
                torques[i] = total / len(driven)
            sim.step(controls(sim, 0.0, torques, [0.0] * sim.n), aero.forces(sim) if aero is not None else None)
            v = sim.forward_speed()
            vmax = max(vmax, v)
            if main:
                if rec is not None:
                    rec.tick({"wheel_torque": ("N*m", total)})
                if log is not None:
                    log.tick()
                for i in driven:
                    re = veh.corners[i].rolling_radius
                    hit["slip_max"] = max(hit["slip_max"], abs(sim.om[i] * re - v) / max(abs(v), 0.5))
                if hit["t"] is None and v >= target:
                    hit["t"], hit["x"] = sim.t, float(sim.design_origin()[0]) - x0
                    if rec is not None:
                        rec.keep_snapshot()
            hist.append(v)
            k += 1
            if sim.t >= t_end:
                return vmax, "max_time"
            if stop_at_target and hit["t"] is not None and sim.t > hit["t"] + 1.0:
                return vmax, "target"
            if ((not stop_at_target or hit["t"] is None) and k % per_s == 0 and len(hist) > 2 * per_s
                    and hist[-1] - hist[-1 - per_s] < STEADY_GAIN):
                return vmax, "steady"

    t_start = sim.t
    vmax, stop = drive(motor, t_start + max_time, not until_top, True)
    limit = _limit(sim, motor, driven)
    moved = float(sim.design_origin()[0]) - x0
    power = None
    if power_top:
        power = {"vmax": vmax, "stop": stop, "limit": limit, "carried_on": False}
        if stop == "steady" and limit["by"] == "motor_max_speed":
            free = dataclasses.replace(motor, max_speed=math.inf)
            v2, stop2 = drive(free, sim.t + max_time, False, False)
            power = {"vmax": v2, "stop": stop2, "limit": _limit(sim, free, driven), "carried_on": True}
    return {"time": hit["t"], "distance": hit["x"], "vmax": vmax, "end_speed": limit["speed"], "stop": stop,
            "limit": limit, "power_top": power, "slip_max": hit["slip_max"], "recorder": rec, "moved": moved,
            "sim": sim, "states": log.states if log is not None else [], "t_start": t_start}


_UNTIL = ("how far the run goes, and so which results come out. target (default): full throttle from rest, "
          "stop 1 s after target_speed; results time_to_speed, distance_to_speed, mean_accel, reached_target, "
          "max_wheel_slip and speed_at_stop (the speed when the run stopped, a little over target_speed: not the "
          "top speed); top_speed only when the speed stops rising below target_speed. top_speed: go on past "
          "target_speed until the speed gains less than 0.02 m/s in 1 s (or max_time); the same results plus "
          "top_speed (the highest speed; where = what held it: motor_max_speed, motor_power, motor_torque, grip, "
          "or max_time when still rising) and top_speed_power (the run carried on with motor_max_speed lifted, "
          "the speed K0 launch.top_speed predicts). The analyze call's params may give it too")


class LaunchTool(K2Tool):
    """Launch and acceleration (P2, P3) with the motor's torque curve.

    Results: time_to_speed (s), distance_to_speed (m), mean_accel (m/s^2), reached_target (1),
    max_wheel_slip (1), speed_at_stop (km/h, the speed when the run stopped, where = why:
    target_speed, top_speed or max_time), moved_uphill (m, on a grade), rolling_radius (mm,
    the driven wheels' effective rolling radius in K2: model.Corner.rolling_radius) and
    peak_tractive_force (N, T i eta / rolling_radius, as K2 drives from standstill). With until
    "top_speed" (or when the speed stops rising below target_speed) also top_speed (km/h, the
    highest speed, where = what held it) and top_speed_power (km/h, motor_max_speed lifted).
    The run ends 1 s after target_speed (until "target"), or when the speed gains less than
    0.02 m/s in 1 s, or at max_time.
    With K&C corners the run also gives the force the body receives at each suspension mount
    (the drive torque's reaction on the body is in body_torque_*): the mount_* series of
    mounts.mount_series, mount_force_peak (N, where '<support>.<mount> at t <s> s') and the joint
    vectors at that instant.
    """

    name = "launch"
    title = "発進・加速 (K2 にモーターのトルクの線)"
    domain = "powertrain"
    tests = ["P2", "P3"]
    params = (P("target_speed", "km/h", 40.0, "full throttle from rest up to this speed (time_to_speed, "
                "distance_to_speed and mean_accel are taken there)"),
              P("until", None, "target", _UNTIL, choices=("target", "top_speed"), run_option=True),
              P("grade", "%", 0.0, "road grade, + uphill"),
              *MOTOR,
              P("max_time", "s", 30.0, "longest run (s of driving); top_speed_power's run on past the motor speed "
                "limit may take as long again. The analyze call's params may give it too", run_option=True),
              P("rolling_radius", "mm", None, "an effective rolling radius of the driven wheels to set beside K2's "
                "(the r of K0 launch.peak_tractive_force; default: design variable rolling_radius, else none: K0 "
                "then computes the loaded radius at the static load as K2 does). K2 does not roll on it: its tyre rolls on the result "
                "rolling_radius (the loaded radius at the static load); the record gives the F_max either radius "
                "gives and their gap", var=("rolling_radius",)),
              CD, RHO, FRONT_AREA, MU)
    _launch_inputs = (("v", "target_speed"), ("grade", "grade"), ("P", "motor_power"), ("eta", "efficiency"),
                      ("C_d", "cd"), ("A", "front_area"), ("rho", "rho"))
    _launch_note = ("these inputs: the load case, else the design variables named in describe(); F_max = "
                    "motor_torque gear_ratio efficiency / r (launch.peak_tractive_force), K2's r is the result "
                    "rolling_radius (the loaded radius at the static load; the record gives F_max as "
                    "peak_tractive_force and says how far a given rolling_radius is from "
                    "it); C_rr = design variable "
                    "rolling_resistance (0.015 assumed in K2 when absent); lambda = "
                    "1 + sum(J / r^2) / m, K2 takes J = m_u r^2 / 2 per wheel (a solid disc of the unsprung mass), "
                    "not a mass_factor variable; A: K2 uses the front projection of the part boxes when neither "
                    "gives front_area")
    _top_note = ("a result only with until = top_speed (the load case or the analyze call's params); K2 stops when "
                 "the speed gains less than 0.02 m/s in 1 s, a little under the balance the formula solves; m and "
                 "C_rr as the other launch results")
    pairs = (Pair("time_to_speed", "launch.time_to_speed", "t", _launch_inputs, _launch_note),
             Pair("distance_to_speed", "launch.time_to_speed", "s", _launch_inputs, _launch_note),
             Pair("mean_accel", "launch.time_to_speed", "a_mean", _launch_inputs, _launch_note),
             Pair("top_speed_power", "launch.top_speed", "v_top", _launch_inputs[1:],
                  "K2's top speed with motor_max_speed lifted, as the formula (power against the road load); "
                  + _top_note),
             Pair("top_speed", "launch.top_speed", "v_top", _launch_inputs[1:],
                  "the formula has no motor speed limit: it predicts top_speed only when top_speed's where is "
                  "motor_power; where = motor_max_speed means the motor speed limit held it (then compare "
                  "top_speed_power); " + _top_note),
             Pair("peak_tractive_force", "launch.peak_tractive_force", "F_max",
                  (("T", "motor_torque"), ("i", "gear_ratio"), ("eta", "efficiency"),
                   ("rolling_radius", "rolling_radius")),
                  "F_max = T i eta / rolling_radius; K2's rolling_radius is the loaded radius at its static wheel "
                  "load, K0's (when the load case gives none) the free radius less K0's static wheel load / "
                  "tyre_stiffness: the two static loads differ a little (v1: 288.2 mm in K2, 287.4 mm in K0)"))

    def own_checks(self) -> list[dict]:
        d = box_design(mass=500.0, cg_x=-0.9, cg_z=0.5, wheel_mass=20.0, name="launch check",
                       variables={"tire_mu": {"value": 1.0, "unit": "1"},
                                  "rolling_resistance": {"value": 0.0, "unit": "1"}})
        veh = build_vehicle(d)
        motor = Motor(torque=50.0, power=math.inf, gear=8.0, efficiency=1.0)
        r = launch_run(veh, motor, 5.0, max_time=3.0)
        re = driven_rolling_radius(veh)
        m_eff = veh.m_total + sum(c.j_w / c.rolling_radius ** 2 for c in veh.corners if c.kind == "wheel")
        expected = 5.0 * m_eff / peak_tractive_force(motor, re)
        return [_check("constant wheel torque 400 N m, no rolling resistance or drag: time to 5 m/s", "time_to_speed",
                       expected, r["time"], "s", 1e-2,
                       "v = F t / (m + sum J / r^2), F = T i eta / r (launch.peak_tractive_force) with r = K2's "
                       "effective rolling radius, the loaded radius at the static load")]

    def _run(self, design: dict, lp: dict) -> dict:
        veh = self.vehicle(design, lp)
        notes = list(veh.assumptions)
        motor = self.motor(lp)
        target = self.q(lp, "target_speed", "m/s")
        grade = self.q(lp, "grade", "1")
        aero = self.aero(veh, lp)
        until_top = self.text(lp, "until") == "top_speed"
        r = launch_run(veh, motor, target, grade=grade, max_time=self.q(lp, "max_time", "s"), dt=self.dt,
                       aero=aero, rec_hz=self.record_hz, until_top=until_top, power_top=until_top)
        res = []
        if r["time"] is not None:
            res += [_res("time_to_speed", r["time"], "s"), _res("distance_to_speed", r["distance"], "m"),
                    _res("mean_accel", target / r["time"], "m/s^2")]
        why = {"target": "target_speed", "steady": "top_speed", "max_time": "max_time"}[r["stop"]]
        res.append(_res("speed_at_stop", r["end_speed"] * 3.6, "km/h", why))
        if r["stop"] == "target":
            notes.append(f"the run stopped 1 s after target_speed {target * 3.6:g} km/h (until = target): speed_at_stop "
                         f"{r['end_speed'] * 3.6:.2f} km/h is not the top speed, and there is no top_speed result; "
                         "give until = top_speed for it")
        if until_top or r["stop"] == "steady":
            lim = r["limit"]
            by = "max_time" if r["stop"] == "max_time" else lim["by"]
            res.append(_res("top_speed", r["vmax"] * 3.6, "km/h", by))
            notes.append(self._limit_note("top_speed", r["vmax"], r["stop"], lim, motor, veh))
            if r["power_top"] is not None:
                pt = r["power_top"]
                res.append(_res("top_speed_power", pt["vmax"] * 3.6, "km/h",
                                "max_time" if pt["stop"] == "max_time" else pt["limit"]["by"]))
                if pt["carried_on"]:
                    notes.append(f"top_speed_power: the run carried on from {r['vmax'] * 3.6:.2f} km/h with "
                                 "motor_max_speed lifted (not recorded); "
                                 + self._limit_note("top_speed_power", pt["vmax"], pt["stop"], pt["limit"],
                                                    dataclasses.replace(motor, max_speed=math.inf), veh))
                else:
                    notes.append("top_speed_power = top_speed: the motor speed limit did not hold the speed")
        res += [_res("reached_target", 1.0 if r["time"] is not None else 0.0, "1"),
                _res("max_wheel_slip", r["slip_max"], "1")]
        res += self._tractive(veh, motor, lp, notes)
        if grade:
            res.append(_res("moved_uphill", r["moved"], "m"))
        rec = r["recorder"]
        if not rec.snap:
            rec.keep_snapshot()
        scene = [_slope(f"縦の坂 {grade * 100:g} % (前が上)", veh, -_length(veh) - 1.0,
                        max(r["moved"], 0.0) + _length(veh) + 2.0, front_up=math.atan(grade))] if grade else []
        series, vectors = rec.series(), rec.vectors(-1)
        if r["states"]:
            # the body's loads at the mounts from rest: closer over the first second (the torque's step)
            series += mount_series(veh, r["states"], [(r["t_start"], r["t_start"] + 1.0)])
            top = mount_peak(series)
            if top is not None:
                res.append(top[0])
                vectors += mount_vectors(veh, state_at(r["states"], top[1]))
        return {"results": res, "series": series, "vectors": vectors, "scene": scene, "assumptions": notes}

    def _tractive(self, veh, motor: Motor, lp: dict, notes: list) -> list[dict]:
        """rolling_radius (mm) and peak_tractive_force (N) as K2 drives, and the F_max the radius K0
        takes would give (a given rolling_radius, else the same loaded radius)."""
        r = driven_rolling_radius(veh)
        f = peak_tractive_force(motor, r)
        driven = [c for c in veh.corners if c.drive]
        given = self.q(lp, "rolling_radius", "m")
        if given is not None:
            other, what = given, "the given rolling_radius (load case or design variable)"
        else:
            other, what = r, "K0, with no rolling_radius given, computes the same loaded radius:"
        f_other = peak_tractive_force(motor, other)
        notes.append(f"peak_tractive_force {f:.1f} N = motor_torque {motor.torque:g} N m x gear_ratio {motor.gear:g} x "
                     f"efficiency {motor.efficiency:g} / rolling_radius {r * 1000.0:.1f} mm (K2's effective rolling "
                     f"radius: the loaded radius at the static load, the free radius "
                     f"{sum(c.radius for c in driven) / len(driven) * 1000.0:.1f} mm less the static load / tyre "
                     f"stiffness); {what} {other * 1000.0:.1f} mm gives {f_other:.1f} N ({f_other / f - 1.0:+.2%})")
        return [_res("rolling_radius", r * 1000.0, "mm"), _res("peak_tractive_force", f, "N")]

    @staticmethod
    def _limit_note(name: str, vmax: float, stop: str, lim: dict, motor: Motor, veh) -> str:
        """One line on what held the speed, with the numbers."""
        rpm = lim["motor_speed"] * 60.0 / (2.0 * math.pi)
        head = f"{name} {vmax * 3.6:.2f} km/h"
        if stop == "max_time":
            return f"{head}: still rising at max_time (the top speed is higher; give a longer max_time)"
        if lim["by"] == "grip":
            return f"{head} is held by grip: a driven wheel slips {lim['slip']:.1%} at the end"
        if lim["by"] == "motor_max_speed":
            r = driven_rolling_radius(veh)
            v_lim = motor.max_speed / motor.gear * r
            return (f"{head} is held by the motor speed limit: motor_max_speed "
                    f"{motor.max_speed * 60.0 / (2.0 * math.pi):.0f} rpm / gear_ratio {motor.gear:g} x rolling_radius "
                    f"{r * 1000.0:.1f} mm = {v_lim * 3.6:.2f} km/h at the wheel rim, less the wheel slip "
                    f"({lim['slip']:.2%} at the end); motor_power and motor_torque do not change it")
        if lim["by"] == "motor_power":
            return (f"{head} is held by motor_power ({motor.power / 1000.0:g} kW x efficiency {motor.efficiency:g} "
                    f"against the road load; motor at {rpm:.0f} rpm, wheel slip {lim['slip']:.2%})")
        return (f"{head} is held by motor_torque (below the corner speed, {motor.torque:g} N m at the motor against "
                f"the road load; motor at {rpm:.0f} rpm)")
