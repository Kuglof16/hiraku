"""Emergency stop (S4) and parking on a slope (S4b)."""

from __future__ import annotations

import math

from ..checks import _check, parked_drift
from ..drive import Aero, SpeedController, controls, start_rolling
from ..model import DesignError, box_design, build_vehicle
from ..record import Recorder
from ..scenario import HOLD_TORQUE, new_sim, slope_gravity
from ..units import G0
from .base import FRONT_AREA, MU, RHO, K2Tool, P, Pair, _length, _min_load, _ramp_gravity, _res, _slope
from .mounts import LoadLog, has_mounts, mount_peak, mount_series, mount_vectors, state_at


def brake_stop(veh, v0: float, *, reaction: float = 0.0, brake_torque: float | None = None, grade: float = 0.0,
               dt: float = 1e-3, aero: Aero | None = None, rec_hz: float = 0.0, rise: float = 0.0) -> dict:
    """Run at v0 for 0.5 s, coast `reaction` s, then brake until the vehicle stops.

    rise: the brake torque grows linearly from 0 over this time (s). With rise 0 and no
    brake_torque the braked wheels are held at once (the locked-wheel closed form); with
    rise > 0 and no brake_torque the torque grows to mu W r per braked wheel, enough to
    lock any wheel, so that a wheel's spin is not stopped within one step.
    """
    sim = new_sim(veh, dt=dt, settle_s=1.0)
    if grade:
        _ramp_gravity(sim, slope_gravity, math.atan(grade))
    rec = Recorder(sim, rec_hz) if rec_hz > 0 else None
    log = LoadLog(sim) if rec is not None and any(has_mounts(c) for c in veh.corners) else None
    start_rolling(sim, v0)
    ctrl = SpeedController(sim)
    n = sim.n
    if brake_torque is not None:
        tq = brake_torque
    elif rise > 0.0:
        tq = max(c.mu for c in veh.corners) * veh.m_total * G0 * max(c.radius for c in veh.corners)
    else:
        tq = HOLD_TORQUE
    brake_full = [tq if c.brake else 0.0 for c in veh.corners]
    brake_on = brake_full
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
            u = 1.0 if rise <= 0.0 else min(1.0, (t - t_brake) / rise)
            drive, brake = [0.0] * n, [u * x for x in brake_full]
        sim.step(controls(sim, 0.0, drive, brake), aero.forces(sim) if aero is not None else None)
        if x_cmd is None and t >= t_cmd:
            x_cmd = float(sim.design_origin()[0])
        if x_brake is None and t >= t_brake:
            x_brake = float(sim.design_origin()[0])
        if rec is not None:
            rec.tick()
        if log is not None:
            log.tick()
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
    t_stop = sim.t
    if rec is not None:
        rec.keep_snapshot()
        for _ in range(int(0.8 / dt)):
            sim.step(controls(sim, 0.0, [0.0] * n, brake_on))
            rec.tick()
            if log is not None:
                log.tick()
    return {"stopping": x_stop - x_cmd, "braking": x_stop - x_brake, "peak_decel": peak_dec, "min_load": min_load,
            "min_rear_axle": min_rear if rear else None, "max_pitch": max_pitch, "recorder": rec, "sim": sim,
            "states": log.states if log is not None else [], "t_brake": t_brake, "t_stop": t_stop}


class BrakingTool(K2Tool):
    """Emergency stop (S4) and parking on a slope (S4b).

    The brake torque grows over brake_rise_time: a brake that stops a wheel's spin within
    one step tips the body by the spin's momentum (the empty demo vehicle's rear unloaded
    to 0 within 18 ms at mu 0.6; with 0.2 s it keeps 325 N).
    Results: stopping_distance (m, from the brake command), braking_distance (m, from the
    brakes coming on, the rise included), mean_decel (m/s^2 = v^2 / 2 s_braking), mean_decel_g (g, the same),
    peak_decel (m/s^2), min_wheel_load (N, where = support), min_rear_wheel_load (N, supports
    behind the CG: 0 = the rear lifts), min_rear_axle_load (N, those supports together),
    max_pitch (deg, nose down +); with parking_slope: parking_drift (mm in 60 s, facing uphill,
    brakes on) and parking_friction_demand (1, the largest |F_road| / F_z of a braked wheel).
    With K&C corners the stop also gives the force the body receives at each suspension mount:
    the mount_* series of mounts.mount_series (closer from the brakes coming on to the stop, and
    at each mount's largest force), mount_force_peak (N, the largest of all the mounts, where
    '<support>.<mount> at t <s> s') and the joint vectors at that instant.
    """

    name = "braking"
    title = "急制動 (K2 で急ブレーキ)"
    domain = "handling"
    tests = ["S4", "S4b"]
    # speed 35 km/h: the default when a load case has none (S4's 35 km/h is cited in catalog/builders/k2_tests.py)
    params = (P("speed", "km/h", 35.0, "speed when the brake command comes"),
              MU,
              P("reaction_time", "s", 0.0, "coasting from the command until the brakes come on"),
              P("brake_torque", "N*m", None, "per braked wheel (default: enough to lock every braked wheel, no ABS)"),
              P("brake_rise_time", "s", 0.2, "the brake torque grows from 0 to full over this time (0.2 s "
                                             "assumed; no source read in this repository)", assumed=True),
              P("grade", "%", 0.0, "road grade, + uphill"),
              P("parking_slope", "%", None, "optional: also park on this slope, brakes on, 60 s"),
              P("cd", "1", 0.0, "drag coefficient (default 0: no air drag)"),
              RHO, FRONT_AREA)
    pairs = (Pair("stopping_distance", "braking.stopping_distance", "s",
                  (("v", "speed"), ("mu", "mu"), ("t_r", "reaction_time")),
                  "mu is the tyre's peak; K2 locks the wheels (no ABS) and they slide at slip -1, below the peak "
                  "(Magic Formula): the K2 builders' lock_inputs gives the locked-wheel mu of the same tyre"),
             Pair("braking_distance", "braking.stopping_distance", "s_braking", (("v", "speed"), ("mu", "mu")),
                  "as stopping_distance: the locked wheels slide below the peak mu"),
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
        # a locked wheel slides at kappa = -1, past the peak of the Magic Formula
        mu_lock = sum(abs(c.mf.forces(c.static_load, -1.0, 0.0)[0]) for c in veh.corners) / sum(
            c.static_load for c in veh.corners)
        expected = v0 * v0 / (2.0 * mu_lock * G0)
        return [_check(f"locked wheels from 40 km/h, peak mu 0.7, locked-wheel mu {mu_lock:.3f} (the tyre at kappa -1), "
                       "no air drag or rolling resistance", "braking_distance", expected, r["braking"], "m", 2e-2,
                       "s = v^2 / (2 mu_lock g), mu_lock = sum |Fx(kappa = -1)| / W at the static loads (the load moving "
                       "to the front while braking is not in it)")]

    def _run(self, design: dict, lp: dict) -> dict:
        veh = self.vehicle(design, lp)
        notes = list(veh.assumptions)
        v0 = self.q(lp, "speed", "m/s")
        aero = self.aero(veh, lp) if self.q(lp, "cd", "1") > 0 else None
        tq = self.q(lp, "brake_torque", "N*m")
        if tq is None:
            notes.append("brakes strong enough to lock every braked wheel (no ABS)")
        r = brake_stop(veh, v0, reaction=self.q(lp, "reaction_time", "s"), brake_torque=tq,
                       grade=self.q(lp, "grade", "1"), dt=self.dt, aero=aero, rec_hz=self.record_hz,
                       rise=self.q(lp, "brake_rise_time", "s"))
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
            park = parked_drift(veh, slope, 60.0, self.dt)
            res.append(_res("parking_drift", park["drift"] * 1000.0, "mm"))
            res.append(_res("parking_friction_demand", park["friction_demand"], "1"))
        rec = r["recorder"]
        grade = self.q(lp, "grade", "1")
        # the recorded run is the stop (on the grade); the parking run is not recorded
        scene = [_slope(f"縦の坂 {grade * 100:g} % (前が上)", veh, -_length(veh) - 1.0,
                        r["stopping"] + v0 * 0.5 + _length(veh) + 2.0, front_up=math.atan(grade))] if grade else []
        series, vectors = rec.series(), rec.vectors(-1)
        if r["states"]:
            # the body's loads at the mounts: closer from the brakes coming on to just after the stop
            series += mount_series(veh, r["states"], [(r["t_brake"] - 0.1, r["t_stop"] + 0.2)])
            top = mount_peak(series)
            if top is not None:
                res.append(top[0])
                # joint vectors: every mount at the instant of mount_force_peak (each mount's own
                # largest is in the series)
                vectors += mount_vectors(veh, state_at(r["states"], top[1]))
        return {"results": res, "series": series, "vectors": vectors, "scene": scene, "assumptions": notes}
