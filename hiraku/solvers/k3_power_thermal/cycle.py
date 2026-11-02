"""Drive cycles as segments of road (length, grade, target speed), and the
speed profile a driver can follow on them with the vehicle's drive force.

The forest loop used for the endurance tests: one lap climbs and descends the
same height, with flat parts at the high speed, the gentle grade at the
middle speed and the steep grade at the low speed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..k0_handcalc.units import STANDARD_GRAVITY
from .powertrain import Powertrain

BRAKE_DECEL = 1.5  # m/s^2, the driver's planned deceleration (assumption)
COMFORT_ACCEL = 2.0  # m/s^2, the driver never asks for more (assumption)


@dataclass
class Segment:
    length: float  # m
    grade: float  # rise / run
    speed: float  # m/s, target


def forest_loop(grade_low: float, grade_high: float, v_low: float, v_high: float) -> list[Segment]:
    """One 2 km lap (grades as rise/run, speeds in m/s)."""
    v_mid = (v_low + v_high) / 2.0
    return [
        Segment(400.0, 0.0, v_high),
        Segment(300.0, grade_low, v_mid),
        Segment(200.0, grade_high, v_low),
        Segment(300.0, 0.0, v_high),
        Segment(200.0, -grade_high, v_low),
        Segment(300.0, -grade_low, v_mid),
        Segment(300.0, 0.0, v_high),
    ]


def repeat(lap: list[Segment], distance: float) -> list[Segment]:
    """Laps (the last one cut) until the distance (m) is covered."""
    out, done = [], 0.0
    while done < distance - 1e-9:
        for seg in lap:
            take = min(seg.length, distance - done)
            if take <= 1e-9:
                break
            out.append(Segment(take, seg.grade, seg.speed))
            done += take
    return out


def speed_profile(segments: list[Segment], pt: Powertrain, dx: float = 1.0) -> dict:
    """Speed along the road that the vehicle can follow.

    Forward pass: accelerate toward the target with the drive force left over
    after the road load (and never more than COMFORT_ACCEL). Backward pass:
    start braking at BRAKE_DECEL in time for every lower target. Starts and
    ends at rest. Returns arrays x (m), v (m/s), grade, target.
    """
    x_end = np.cumsum([s.length for s in segments])
    n = int(np.ceil(x_end[-1] / dx)) + 1
    x = np.linspace(0.0, x_end[-1], n)
    idx = np.minimum(np.searchsorted(x_end, x, side="right"), len(segments) - 1)
    target = np.array([segments[i].speed for i in idx])
    grade = np.array([segments[i].grade for i in idx])
    target[-1] = 0.0
    v = np.zeros(n)
    m_eff = pt.mass * pt.mass_factor
    for k in range(1, n):
        h = x[k] - x[k - 1]
        u = v[k - 1]
        roll, gr, aero = pt.road_load(u, grade[k - 1])
        a = min((pt.drive_force(u) - roll - gr - aero) / m_eff, COMFORT_ACCEL)
        vk = np.sqrt(max(u * u + 2.0 * a * h, 0.0))
        v[k] = min(vk, target[k])
    for k in range(n - 2, -1, -1):
        h = x[k + 1] - x[k]
        v[k] = min(v[k], np.sqrt(v[k + 1] ** 2 + 2.0 * BRAKE_DECEL * h))
    if np.any(v[1:-1] <= 0):
        stuck = float(x[1:-1][v[1:-1] <= 0][0])
        raise_stall(stuck, grade[np.searchsorted(x, stuck)], pt)
    return {"x": x, "v": v, "grade": grade, "target": target}


def raise_stall(x: float, grade: float, pt: Powertrain) -> None:
    from ..k0_handcalc.vehicle import DesignError

    need = pt.mass * STANDARD_GRAVITY * (pt.Crr + grade)
    raise DesignError(
        f"the vehicle stalls at {x:.0f} m on a {grade * 100:.0f} % grade: it needs about {need:.0f} N at the wheels "
        f"and has {pt.F_max:.0f} N; raise the motor torque or the gear ratio")
