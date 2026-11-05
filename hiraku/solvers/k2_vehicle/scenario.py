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


def _rec_mark(rec: Recorder | None):
    if rec is None:
        return None
    return len(rec.t), {k: len(v) for k, v in rec.ch.items()}, list(rec.spin_angle), rec.k, rec.every


def _rec_rewind(rec: Recorder | None, mark) -> None:
    if rec is None:
        return
    n_t, n_ch, spin, k, every = mark
    del rec.t[n_t:]
    for name in list(rec.ch):
        if name in n_ch:
            del rec.ch[name][n_ch[name]:]
        else:
            del rec.ch[name], rec.units[name]
    rec.spin_angle, rec.k, rec.every = list(spin), k, every


def tilt_sweep(veh: Vehicle, left_up: bool = True, dt: float = 1e-3, fast_rate: float = math.radians(4.0),
               slow_rate: float = math.radians(0.25), slow_span: float = math.radians(2.0),
               max_angle: float = math.radians(80.0), settle_s: float = 2.0, record_hz: float = 0.0,
               after_s: float = 1.5, slow_hz: float = 2.0, after_hz: float = 25.0,
               checkpoint_every: float = math.radians(0.25), load_log=None) -> dict:
    """Turn gravity slowly (a tilt table) until the upper-side wheels carry no load.

    Coarse to fine: the table first turns at fast_rate until the upper wheels lift,
    keeping the whole state (Sim.state, and the recorder's length) every
    `checkpoint_every`. It then goes back to the last checkpoint at least `slow_span`
    before that coarse tip and turns from there at slow_rate. If the slow turn tips
    within half a span of where it started (the fast turn's roll had not settled at the
    checkpoint), it goes back one more span and turns again. The tip angle is the first
    slow step at which the upper-side wheels have lost all load (a wheel's load is never
    negative, so there is no crossing to interpolate); it lies at most one step
    (slow_rate * dt) past the true crossing.

    Before 2026-11-03 the table turned at fast_rate down to 35 % (the checks: 10 %) of the
    starting upper-wheel load and slowly from there, about 12 deg of slow turning; the tip
    angles of the check vehicles and offroad-4w-v1 / -tallcargo agree with that sweep to
    5e-4 deg (K2 runs, 2026-11-03), at about a third of the steps.

    load_log: a class made with the sim (shelf.mounts.LoadLog) whose tick() is called after each
    step of the turn up to the tip (not after it), keep() at the tip, and rewind(t) when the sweep goes back to a
    checkpoint; returned as "log", with "t_slow" (s, where the last slow turn started) and
    "t_tip" (s).
    """
    sim = new_sim(veh, dt=dt, settle_s=settle_s)
    rec = Recorder(sim, record_hz) if record_hz > 0 else None
    log = load_log(sim) if load_log is not None else None
    t_slow = None
    slow_hz = min(record_hz, slow_hz)
    ctl = hold(sim)
    up = upper_side(sim, left_up)
    sign = 1.0 if left_up else -1.0
    if not sim.out:
        sim.step(ctl)

    def turn(theta: float, rate: float, checkpoints: list | None) -> tuple[float | None, float]:
        """Turn from theta at rate until the upper wheels lift (or max_angle); returns (tip, theta)."""
        nonlocal slide
        next_cp = (math.floor(theta / checkpoint_every + 1e-9) + 1) * checkpoint_every
        while theta < max_angle:
            theta += rate * dt
            sim.set_gravity(side_gravity(sign * theta))
            sim.step(ctl)
            if log is not None:
                log.tick()
            n_up = 0.0
            for i in up:
                n_up += sim.out["ntot"][i]
            if slide is None and any(sim.out["sliding"][i] for i in range(sim.n) if i not in up):
                slide = theta
            if rec is not None:
                rec.tick({"table_angle": ("deg", math.degrees(sign * theta))})
            if n_up <= 0.0:
                if log is not None:
                    log.keep()
                return theta, theta
            if checkpoints is not None and theta >= next_cp:
                checkpoints.append((theta, sim.state(), _rec_mark(rec)))
                next_cp += checkpoint_every
        return None, theta

    slide = None
    cps = [(0.0, sim.state(), _rec_mark(rec))]
    coarse, theta = turn(0.0, fast_rate, cps)
    tip = None
    if coarse is not None:
        back = coarse - slow_span
        while True:
            start, st, mark = max((c for c in cps if c[0] <= back), key=lambda c: c[0], default=cps[0])
            sim.restore(st)
            _rec_rewind(rec, mark)
            if log is not None:
                log.rewind(sim.t)
            t_slow = sim.t
            if slide is not None and slide > start:
                slide = None
            if rec is not None:
                rec.set_hz(slow_hz)
            tip, theta = turn(start, slow_rate, None)
            if tip is None or start == 0.0 or tip - start >= 0.5 * slow_span:
                break
            back = start - slow_span
    snap = None
    t_tip = sim.t
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
    return {"tip_angle": tip, "slide_angle": slide, "recorder": rec, "snapshot": snap, "sim": sim, "log": log,
            "t_slow": t_slow, "t_tip": t_tip}
