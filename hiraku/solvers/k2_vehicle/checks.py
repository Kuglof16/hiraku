"""The checks K2 must pass before any scenario runs (as `tool_check` records).

1. static wheel loads: moment balance W (x_cg - x_rear) / L / 2, left = right bit for bit
2. mirror: straight driving keeps yaw at 0; a left manoeuvre and its mirror give opposite yaw (< 1e-9 rad)
3. free fall: the body follows the semi-implicit Euler sum z_N = z_0 - g h^2 N (N + 1) / 2
4. quarter car, closed form: 1.891 Hz = (1/2 pi) sqrt(k_ride / m_s), k_s 20 kN/m, k_t 150 kN/m, m_s 125 kg
   (check-vehicle values); and the exact two-mass frequency with a 25 kg wheel
5. rigid tilt table, closed form: atan((t/2) / h) = 50.19 deg for t = 1.2 m, h = 0.5 m (check-vehicle values),
   the same to the left and right
6. parked on a 20 % slope with the brakes on: moves < 0.1 mm in 10 s
7. the Magic Formula tyre at a wheel's static load: peak D = mu Fz in both directions, the slope at
   zero slip BCD = the slip and cornering stiffness K0 takes, the combined-slip forces inside the
   friction ellipse (to 5 %), and an aligning moment that turns the wheel toward its motion
"""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

from .model import box_design, build_vehicle
from .scenario import hold, new_sim, settle, slope_gravity, tilt_sweep
from .sim import Controls, Sim
from .road import flat
from .units import G0

# The tilt table and the side wind of the checks: fast to a tenth (a fifth) of the starting
# upper-wheel load, then slow to the tip. A full-speed sweep agrees with the slow one of the
# tools to 1e-4 of the tip angle on the check vehicles (K2 runs, 2026-11-03), and the checks'
# tolerances are 2e-3 and 5e-3.
CHECK_TILT = {"fast_rate": math.radians(10.0), "slow_rate": math.radians(0.5), "slow_below": 0.10,
              "settle_s": 1.0, "after_s": 0.0}
CHECK_WIND = {"fast": 25.0, "slow": 1.0, "slow_below": 0.2}


def _check(case: str, quantity: str, expected: float, got: float | None, unit: str, tol: float, source: str,
           passed: bool | None = None) -> dict:
    if passed is None:
        if got is None or not math.isfinite(got):
            passed = False
        elif expected == 0.0:
            passed = abs(got) <= tol
        else:
            passed = abs(got - expected) <= tol * abs(expected)
    return {"case": case, "quantity": quantity, "expected": float(expected),
            "got": None if got is None else float(got), "unit": unit, "tolerance_rel": float(tol),
            "pass": bool(passed), "source": source}


def check_static_loads() -> list[dict]:
    d = box_design(mass=500.0, cg_x=-0.7, cg_z=0.5, wheel_mass=20.0, name="static loads check")
    veh = build_vehicle(d)
    sim = new_sim(veh, settle_s=8.0)
    loads = dict(zip(veh.ids(), sim.out["ntot"]))
    # hand calculation from the part list and the design geometry
    m = sum(p["mass_kg"] for p in d["parts"])
    x_cg = sum(p["mass_kg"] * p["box"]["center_mm"][0] for p in d["parts"]) / m / 1000.0
    x_f, x_r = 0.0, -1.8
    front = m * G0 * (x_cg - x_r) / (x_f - x_r) / 2.0
    rear = m * G0 * (x_f - x_cg) / (x_f - x_r) / 2.0
    # the same balance on the settled geometry (the body pitches a little under the
    # unequal tyre deflections, which moves the contacts ~0.4 mm against the CG)
    cp = dict(zip(veh.ids(), sim.out["p"]))
    xg = float(sim.cg_world()[0])
    xf_s, xr_s = cp["FL"][0], cp["RL"][0]
    w = sum(loads.values())
    front_s = w * (xg - xr_s) / (xf_s - xr_s) / 2.0
    src = "moment balance W (x_cg - x_rear) / L, split equally left and right"
    return [
        _check("static load FL, 580 kg, CG 0.728 m behind the front axle (design geometry)", "wheel_load", front,
               loads["FL"], "N", 1e-3, src),
        _check("static load RL (design geometry)", "wheel_load", rear, loads["RL"], "N", 1e-3, src),
        _check("static load FL, moment balance on the settled contact points and CG", "wheel_load", front_s,
               loads["FL"], "N", 1e-6, src),
        _check("static loads sum = weight", "wheel_load_sum", m * G0, w, "N", 1e-9, "vertical force balance"),
        _check("static load FL - FR (bit for bit)", "wheel_load_difference", 0.0, loads["FL"] - loads["FR"], "N", 0.0,
               "symmetry", passed=loads["FL"] == loads["FR"]),
        _check("static load RL - RR (bit for bit)", "wheel_load_difference", 0.0, loads["RL"] - loads["RR"], "N", 0.0,
               "symmetry", passed=loads["RL"] == loads["RR"]),
    ]


def _drive_run(veh, steer_fn, seconds: float, torque: float, dt: float = 1e-3) -> tuple[list, list, Sim]:
    sim = new_sim(veh, settle_s=1.0, dt=dt)
    yaws, ys = [], []
    n = sim.n
    steps = int(round(seconds / dt))
    for k in range(steps):
        t = k * dt
        delta = steer_fn(t)
        ctl = Controls(veh.wheel_steer(delta).tolist(), [torque if c.drive else 0.0 for c in veh.corners], [0.0] * n)
        sim.step(ctl)
        yaws.append(sim.attitude()[2])
        ys.append(sim.p[1])
    return yaws, ys, sim


def check_mirror() -> list[dict]:
    d = box_design(mass=500.0, cg_x=-0.7, cg_z=0.5, wheel_mass=20.0, susp="kc_table", name="mirror check")
    veh = build_vehicle(d)
    src = "mirror symmetry of the equations (exact in IEEE arithmetic)"
    yaw0, y0, sim0 = _drive_run(veh, lambda t: 0.0, 4.0, 60.0)
    straight = max(abs(v) for v in yaw0)
    lat = max(abs(v) for v in y0)
    steer = lambda t: 0.08 * math.sin(2.0 * math.pi * 0.5 * (t - 1.0)) if 1.0 <= t <= 5.0 else 0.0
    yl, yl_y, _ = _drive_run(veh, steer, 6.0, 60.0)
    yr, yr_y, _ = _drive_run(veh, lambda t: -steer(t), 6.0, 60.0)
    mirror = max(abs(a + b) for a, b in zip(yl, yr))
    mirror_y = max(abs(a + b) for a, b in zip(yl_y, yr_y))
    yaw_amp = max(abs(v) for v in yl)
    return [
        _check("straight 4 s from rest: max |yaw| < 1e-9 rad", "yaw", 0.0, straight, "rad", 1e-9, src),
        _check(f"straight 4 s ({sim0.forward_speed():.1f} m/s at the end): max |lateral drift| < 1e-9 m", "lateral_drift", 0.0, lat, "m", 1e-9, src),
        _check(f"sine steer left vs right (yaw up to {math.degrees(yaw_amp):.1f} deg): max |yaw_L + yaw_R| < 1e-9 rad",
               "yaw_mirror_error", 0.0, mirror, "rad", 1e-9, src),
        _check("sine steer left vs right: max |y_L + y_R| < 1e-9 m", "lateral_mirror_error", 0.0, mirror_y, "m", 1e-9, src),
    ]


def check_free_fall() -> list[dict]:
    out = []
    for susp in ("rigid", "spring"):
        d = box_design(mass=500.0, cg_x=-0.9, cg_z=0.5, wheel_mass=20.0, susp=susp, name="free fall check")
        veh = build_vehicle(d)
        sim = Sim(veh, flat(), dt=1e-3)
        sim.p[2] += 1.0
        if susp == "spring":
            # springs at their free length so no internal force acts
            sim.s = [-pl / k if k > 0 else 0.0 for pl, k in zip(sim.preload, sim.k_w)]
        z0 = sim.p[2]
        n = 300
        ctl = sim.no_controls()
        for _ in range(n):
            sim.step(ctl)
        h = sim.dt
        expected = z0 - G0 * h * h * n * (n + 1) / 2.0
        err = sim.p[2] - expected
        out.append(_check(f"free fall 0.3 s, {susp} suspension: z_N - (z_0 - g h^2 N(N+1)/2) < 1e-12 m",
                          "fall_error", 0.0, err, "m", 1e-12, "semi-implicit Euler sum of a constant acceleration"))
    return out


def _quarter_car_freq(m_u: float) -> tuple[float, float]:
    d = box_design(mass=500.0, cg_x=-0.9, cg_z=0.5, k_w=20.0, c_w=0.0, wheel_mass=m_u, travel=400.0,
                   variables={"tyre_stiffness": {"value": 150.0, "unit": "N/mm"},
                              "tire_vertical_damping_ratio": {"value": 0.0, "unit": "1"}},
                   name="quarter car check")
    veh = build_vehicle(d)
    sim = Sim(veh, flat(), dt=1e-3, lock="heave")
    ks, kt = 20e3, 150e3
    z_eq = sim.p[2]
    amp = 0.005
    sim.p[2] += amp
    sim.s = [-amp * kt / (ks + kt)] * sim.n
    ctl = sim.no_controls()
    zs, ts = [], []
    for _ in range(6000):
        sim.step(ctl)
        zs.append(sim.p[2] - z_eq)
        ts.append(sim.t)
    cross = []
    for k in range(1, len(zs)):
        if zs[k - 1] > 0.0 >= zs[k]:
            cross.append(ts[k - 1] + (ts[k] - ts[k - 1]) * zs[k - 1] / (zs[k - 1] - zs[k]))
    f = (len(cross) - 1) / (cross[-1] - cross[0])
    return f, veh.m_s / 4.0


def check_quarter_car() -> list[dict]:
    ks, kt = 20e3, 150e3
    f_small, ms = _quarter_car_freq(0.5)
    k_ride = ks * kt / (ks + kt)
    expected = math.sqrt(k_ride / ms) / (2.0 * math.pi)
    f25, ms25 = _quarter_car_freq(25.0)
    mu = 25.0
    a = ms25 * mu
    b = -(ms25 * (ks + kt) + mu * ks)
    c = ks * kt
    w2 = (-b - math.sqrt(b * b - 4 * a * c)) / (2 * a)
    exact = math.sqrt(w2) / (2.0 * math.pi)
    return [
        _check("quarter car k_s 20 kN/m, k_t 150 kN/m, m_s 125 kg (wheel 0.5 kg)", "ride_frequency", expected, f_small, "Hz", 3e-3,
               "(1/2 pi) sqrt(k_ride / m_s), k_ride = k_s k_t / (k_s + k_t)"),
        _check("quarter car, same springs, m_s 125 kg, wheel 25 kg", "ride_frequency", exact, f25, "Hz", 1e-3,
               "two-mass system, lower root of m_s m_u w^4 - (m_s (k_s + k_t) + m_u k_s) w^2 + k_s k_t = 0"),
    ]


def check_rigid_tilt() -> list[dict]:
    d = box_design(mass=500.0, cg_x=-0.9, cg_z=0.5, track=1.2, susp="rigid",
                   variables={"tyre_stiffness": {"value": 10000.0, "unit": "N/mm"},
                              "tire_mu": {"value": 2.0, "unit": "1"}}, name="rigid tilt check")
    veh = build_vehicle(d)
    left = tilt_sweep(veh, left_up=True, **CHECK_TILT)["tip_angle"]
    right = tilt_sweep(veh, left_up=False, **CHECK_TILT)["tip_angle"]
    expected = math.degrees(math.atan(0.6 / 0.5))
    src = "tan(theta) = (t/2) / h, t = 1.2 m, h = 0.5 m"
    return [
        _check("rigid tilt table, left side up", "tilt_angle", expected, None if left is None else math.degrees(left), "deg", 1e-3, src),
        _check("rigid tilt table, right side up", "tilt_angle", expected, None if right is None else math.degrees(right), "deg", 1e-3, src),
        _check("rigid tilt table, left - right (bit for bit)", "tilt_angle_difference", 0.0,
               None if left is None or right is None else math.degrees(left - right), "deg", 0.0, "symmetry",
               passed=left is not None and left == right),
    ]


def parked_drift(veh, grade: float = 0.20, seconds: float = 60.0, dt: float = 1e-3) -> dict:
    """Park on a slope (facing uphill) with the brakes on; return the drift over `seconds`
    after settling, and the friction demand: the largest |tyre force along the road| / normal
    force among the braked wheels at the end."""
    sim = new_sim(veh, settle_s=1.0, dt=dt)
    ctl = hold(sim)
    alpha = math.atan(grade)
    ramp = int(2.0 / dt)
    for k in range(ramp):
        sim.set_gravity(slope_gravity(alpha * (k + 1) / ramp))
        sim.step(ctl)
    settle(sim, 5.0, ctl)
    p0 = sim.design_origin()
    settle(sim, seconds, ctl)
    p1 = sim.design_origin()
    f = sim.out["f"]
    demand = max((math.hypot(f[i][0], f[i][1]) / f[i][2] for i, c in enumerate(veh.corners) if c.brake and f[i][2] > 0.0),
                 default=math.inf)
    return {"drift": float(np.linalg.norm(p1 - p0)), "friction_demand": demand, "sim": sim}


def check_parked_on_slope() -> list[dict]:
    d = box_design(mass=500.0, cg_x=-0.9, cg_z=0.5, wheel_mass=20.0, name="parked on a slope check")
    veh = build_vehicle(d)
    r = parked_drift(veh, 0.20, 10.0)
    return [_check("parked on a 20 % slope, brakes on: drift in 10 s < 0.1 mm", "parked_drift", 0.0, r["drift"] * 1000.0,
                   "mm", 0.1, "a held vehicle must not creep (tyre deflection model has no slip at rest)")]


def _peak(f, lo: float, hi: float) -> float:
    """Largest value of a single-peaked f on [lo, hi] (golden-section search)."""
    g = (math.sqrt(5.0) - 1.0) / 2.0
    a, b = lo, hi
    for _ in range(200):
        c, d = b - g * (b - a), a + g * (b - a)
        if f(c) > f(d):
            b = d
        else:
            a = c
    return f(0.5 * (a + b))


def check_tyre() -> list[dict]:
    d = box_design(mass=500.0, cg_x=-0.9, cg_z=0.5, wheel_mass=20.0, name="tyre check")
    veh = build_vehicle(d)
    c = veh.corners[0]
    mf, fz, mu = c.mf, c.static_load, c.mu
    case = f"Magic Formula tyre of the check vehicle's FL wheel at its static load {fz:.0f} N, mu {mu:g}"
    # peaks of the pure-slip curves without their vertical shifts (the D of the formula)
    dfz = (fz - mf.fz0) / mf.fz0
    svx = fz * (mf.PVX1 + mf.PVX2 * dfz) * mf.lmux
    peak_x = _peak(lambda k: mf.fx0(fz, k)[0] - svx, 0.0, 1.0)
    svy = mf._fy0(fz, 0.0, 0.0)[4]
    peak_y = _peak(lambda a: -(mf.fy0(fz, a) - svy), 0.0, math.tan(mf.alpha_max))
    # slopes at zero slip of the formula (kappa_x = 0, alpha_y = 0: the horizontal shifts taken out)
    h = 1e-7
    shx = mf.PHX1 + mf.PHX2 * dfz
    kx = (mf.fx0(fz, -shx + h)[0] - mf.fx0(fz, -shx - h)[0]) / (2.0 * h)
    shy = mf._fy0(fz, 0.0, 0.0)[3]
    ky = -(mf.fy0(fz, -shy + h) - mf.fy0(fz, -shy - h)) / (2.0 * h)
    # combined slip: the largest (Fx / Dx)^2 + (Fy / Dy)^2 over kappa -1..1, alpha -55..55 deg
    dx_, dy_ = mf.mu_x(fz) * fz, mf.mu_y(fz) * fz
    worst = 0.0
    for i in range(-50, 51):
        for deg in range(-55, 56):
            fx, fy, _ = mf.forces(fz, i / 50.0, math.tan(math.radians(deg)))
            worst = max(worst, math.hypot(fx / dx_, fy / dy_))
    mz_l = mf.forces(fz, 0.0, math.tan(math.radians(2.0)))[2]
    mz_r = mf.forces(fz, 0.0, math.tan(math.radians(-2.0)))[2]
    src = "Magic Formula: D sin(C atan(B x - E (B x - atan B x))) peaks at D; dF/dx at x = 0 is B C D"
    return [
        _check(case + ": peak Fx", "peak_longitudinal_force", mu * fz, peak_x, "N", 1e-6, src + "; D_x = mu_x F_z"),
        _check(case + ": peak Fy", "peak_lateral_force", mu * fz, peak_y, "N", 1e-6, src + "; D_y = mu_y F_z"),
        _check(case + ": slope dFx/dkappa at zero slip = the slip stiffness K0 takes", "slip_stiffness", c.c_long, kx,
               "N", 1e-6, src + " = K_xk (4.E15) scaled to the design"),
        _check(case + ": slope -dFy/dalpha at zero slip = the cornering stiffness K0 takes", "cornering_stiffness",
               c.c_lat, ky, "N/rad", 1e-6, src + " = K_ya (4.E25) scaled to the design"),
        _check(case + ": combined slip, kappa -1..1 and alpha -55..55 deg: largest hypot(Fx/Dx, Fy/Dy)",
               "friction_ellipse", 1.0, worst, "1", 0.05,
               "the friction ellipse of the pure-slip peaks; the combined-slip weighting is a fit, so 5 % over is allowed",
               passed=worst <= 1.05),
        _check(case + ": aligning moment at alpha +2 deg and -2 deg turns the wheel toward its motion (Mz(+2) > 0 > Mz(-2))",
               "aligning_moment_sign", 1.0, 1.0 if mz_l > 0.0 > mz_r else 0.0, "1", 0.0,
               "ISO signs: positive alpha slides the contact to the left, and the moment turns the heading that way"),
    ]


@lru_cache(maxsize=1)
def _base() -> tuple:
    out = []
    for fn in (check_static_loads, check_mirror, check_free_fall, check_quarter_car, check_rigid_tilt, check_parked_on_slope,
               check_tyre):
        out += fn()
    return tuple(out)


def base_checks() -> list[dict]:
    """All seven pre-run checks (computed once per process)."""
    return [dict(c) for c in _base()]
