"""Building blocks shared by the K2 scenarios (tools)."""

from __future__ import annotations

import math

import numpy as np

from .model import Vehicle
from .record import Recorder
from .road import Road, flat
from .sim import Controls, Sim
from .units import G0

HOLD_TORQUE = 1e6  # N m, a brake that cannot slip


def tilted_gravity(roll_left_up: float = 0.0, pitch_front_up: float = 0.0) -> list[float]:
    """Gravity in the frame of a table tilted left-side-up by roll and front-up by pitch (rad)."""
    rx = np.array([[1.0, 0.0, 0.0], [0.0, math.cos(roll_left_up), -math.sin(roll_left_up)],
                   [0.0, math.sin(roll_left_up), math.cos(roll_left_up)]])
    b = -pitch_front_up
    ry = np.array([[math.cos(b), 0.0, math.sin(b)], [0.0, 1.0, 0.0], [-math.sin(b), 0.0, math.cos(b)]])
    rt = rx @ ry
    g = rt.T @ np.array([0.0, 0.0, -G0])
    if roll_left_up == 0.0:
        g[1] = 0.0
    if pitch_front_up == 0.0:
        g[0] = 0.0
    return g.tolist()


def side_gravity(theta: float) -> list[float]:
    """Gravity for a table tilted about x only (left side up for theta > 0). Exactly mirrored in theta."""
    return [0.0, -G0 * math.sin(theta), -G0 * math.cos(theta)]


def slope_gravity(alpha: float) -> list[float]:
    """Gravity on a slope rising ahead by alpha (rad)."""
    return [-G0 * math.sin(alpha), 0.0, -G0 * math.cos(alpha)]


def hold(sim: Sim) -> Controls:
    """Every braked wheel held; nothing driven."""
    n = sim.n
    brake = [HOLD_TORQUE if c.brake else 0.0 for c in sim.veh.corners]
    return Controls([0.0] * n, [0.0] * n, brake)


def settle(sim: Sim, seconds: float, ctl: Controls | None = None, rec: Recorder | None = None):
    ctl = ctl or hold(sim)
    steps = int(round(seconds / sim.dt))
    for _ in range(steps):
        sim.step(ctl)
        if rec is not None:
            rec.tick()


def new_sim(veh: Vehicle, road: Road | None = None, dt: float = 1e-3, settle_s: float = 2.0, **kw) -> Sim:
    sim = Sim(veh, road or flat(), dt=dt, **kw)
    if settle_s > 0.0:
        settle(sim, settle_s)
        sim.t = 0.0
    return sim


def upper_side(sim: Sim, left_up: bool) -> list[int]:
    return [i for i, c in enumerate(sim.veh.corners) if (c.side > 0) == left_up and c.side != 0]


def tilt_sweep(veh: Vehicle, left_up: bool = True, dt: float = 1e-3, fast_rate: float = math.radians(4.0),
               slow_rate: float = math.radians(0.25), slow_below: float = 0.35, max_angle: float = math.radians(80.0),
               settle_s: float = 2.0, record_hz: float = 0.0, after_s: float = 1.5, slow_hz: float = 2.0,
               after_hz: float = 25.0) -> dict:
    """Turn gravity slowly (a tilt table) until the upper-side wheels carry no load.

    The table turns at fast_rate until the upper wheels carry less than
    `slow_below` of their starting load, then at slow_rate. The tip angle is
    where the upper-side load sum crosses zero (linear between two steps).
    """
    sim = new_sim(veh, dt=dt, settle_s=settle_s)
    rec = Recorder(sim, record_hz) if record_hz > 0 else None
    slow_hz = min(record_hz, slow_hz)
    slow_started = False
    ctl = hold(sim)
    up = upper_side(sim, left_up)
    sign = 1.0 if left_up else -1.0
    n0 = sum(sim.out["ntot"][i] for i in up) if sim.out else None
    if not n0:
        sim.step(ctl)
        n0 = sum(sim.out["ntot"][i] for i in up)
    theta = 0.0
    prev = (0.0, n0)
    tip = None
    slide = None
    while theta < max_angle:
        rate = fast_rate if prev[1] > slow_below * n0 else slow_rate
        if rec is not None and rate == slow_rate and not slow_started:
            rec.set_hz(slow_hz)
            slow_started = True
        theta += rate * dt
        sim.set_gravity(side_gravity(sign * theta))
        sim.step(ctl)
        n_up = 0.0
        for i in up:
            n_up += sim.out["ntot"][i]
        if slide is None and any(sim.out["sliding"][i] for i in range(sim.n) if i not in up):
            slide = theta
        if rec is not None:
            rec.tick({"table_angle": ("deg", math.degrees(sign * theta))})
        if n_up <= 0.0:
            t0, n_prev = prev
            tip = t0 + (theta - t0) * n_prev / (n_prev - n_up) if n_prev > n_up else theta
            break
        prev = (theta, n_up)
    snap = None
    if rec is not None:
        rec.sample({"table_angle": ("deg", math.degrees(sign * theta))})
        snap = rec.keep_snapshot()
        rec.set_hz(after_hz)
        # keep turning a little so the animation shows the tip
        steps = int(after_s / dt)
        for _ in range(steps):
            theta += slow_rate * dt * 4
            sim.set_gravity(side_gravity(sign * theta))
            sim.step(ctl)
            rec.tick({"table_angle": ("deg", math.degrees(sign * theta))})
    return {"tip_angle": tip, "slide_angle": slide, "recorder": rec, "snapshot": snap, "sim": sim}
