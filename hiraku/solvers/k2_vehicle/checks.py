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

import json
import math
import os
from functools import lru_cache
from pathlib import Path

import numpy as np

from .model import box_design, build_vehicle
from .scenario import hold, new_sim, settle, slope_gravity, tilt_sweep
from .sim import Controls, Sim
from .road import flat
from .units import G0

# The tilt table and the side wind of the checks: the table fast to the tip, then slowly over
# the last 2 deg (the wind: fast to a fifth of the starting upper-wheel load, then slow). A
# full-speed sweep agrees with the slow one of the tools to 1e-4 of the tip angle on the check
# vehicles (K2 runs, 2026-11-03), and the checks' tolerances are 2e-3 and 5e-3.
CHECK_TILT = {"fast_rate": math.radians(20.0), "slow_rate": math.radians(0.5), "slow_span": math.radians(2.0),
              "settle_s": 1.0, "after_s": 0.0}
CHECK_WIND = {"fast": 25.0, "slow": 1.0, "slow_below": 0.2}


def _check(case: str, quantity: str, expected: float, got: float | None, unit: str, tol: float, source: str,
           passed: bool | None = None, case_ja: str | None = None, source_ja: str | None = None) -> dict:
    """A contract tool_check. case_ja, source_ja: the case and the source in plain Japanese for the
    screen (contract bf6712f), built beside the English from the same values."""
    if passed is None:
        if got is None or not math.isfinite(got):
            passed = False
        elif expected == 0.0:
            passed = abs(got) <= tol
        else:
            passed = abs(got - expected) <= tol * abs(expected)
    out = {"case": case, "quantity": quantity, "expected": float(expected),
           "got": None if got is None else float(got), "unit": unit, "tolerance_rel": float(tol),
           "pass": bool(passed), "source": source}
    if expected == 0.0:
        # an expected 0 is checked on an absolute bound: tolerance_abs (contract tool_check) says so;
        # tolerance_rel keeps the same number, which readers without tolerance_abs take as absolute
        # when the expected value is 0
        out["tolerance_abs"] = float(tol)
    if case_ja:
        out["case_ja"] = case_ja
    if source_ja:
        out["source_ja"] = source_ja
    return out


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
    src_ja = "前後の車軸まわりの力のつり合い W (x_cg - x_rear) / L で出し、左右に半分ずつ"
    behind = x_f - x_cg
    same = "左右で 1 ビットも違わない (形が左右対称なので)"
    return [
        _check(f"static load FL, {m:.0f} kg, CG {behind:.3f} m behind the front axle (design geometry)", "wheel_load",
               front, loads["FL"], "N", 1e-3, src,
               case_ja=f"止まっているときの左前輪の荷重 ({m:.0f} kg、重心は前の車軸の {behind:.3f} m 後ろ。設計の形で)",
               source_ja=src_ja),
        _check("static load RL (design geometry)", "wheel_load", rear, loads["RL"], "N", 1e-3, src,
               case_ja="止まっているときの左後輪の荷重 (設計の形で)", source_ja=src_ja),
        _check("static load FL, moment balance on the settled contact points and CG", "wheel_load", front_s,
               loads["FL"], "N", 1e-6, src,
               case_ja="止まっているときの左前輪の荷重 (ばねとタイヤが沈んだ後の接地点と重心で)", source_ja=src_ja),
        _check("static loads sum = weight", "wheel_load_sum", m * G0, w, "N", 1e-9, "vertical force balance",
               case_ja="4 輪の荷重の合計が車の重さになるか", source_ja="上下の力のつり合い"),
        _check("static load FL - FR (bit for bit)", "wheel_load_difference", 0.0, loads["FL"] - loads["FR"], "N", 0.0,
               "symmetry", passed=loads["FL"] == loads["FR"],
               case_ja="左前輪と右前輪の荷重の差", source_ja=same),
        _check("static load RL - RR (bit for bit)", "wheel_load_difference", 0.0, loads["RL"] - loads["RR"], "N", 0.0,
               "symmetry", passed=loads["RL"] == loads["RR"],
               case_ja="左後輪と右後輪の荷重の差", source_ja=same),
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
    v_end = sim0.forward_speed()
    src_ja = "左右を入れ替えても式が同じになること (計算機の数の上でも正確に同じ)"
    return [
        _check("straight 4 s from rest: max |yaw| < 1e-9 rad", "yaw", 0.0, straight, "rad", 1e-9, src,
               case_ja="止まった所からまっすぐ 4 秒走って、向きが 1e-9 rad より変わらないか", source_ja=src_ja),
        _check(f"straight 4 s ({v_end:.1f} m/s at the end): max |lateral drift| < 1e-9 m", "lateral_drift", 0.0, lat, "m",
               1e-9, src,
               case_ja=f"まっすぐ 4 秒走って (最後は {v_end:.1f} m/s)、横に 1e-9 m よりずれないか", source_ja=src_ja),
        _check(f"sine steer left vs right (yaw up to {math.degrees(yaw_amp):.1f} deg): max |yaw_L + yaw_R| < 1e-9 rad",
               "yaw_mirror_error", 0.0, mirror, "rad", 1e-9, src,
               case_ja=f"ハンドルを左右に揺らす走りを、左始まりと右始まりで比べる (向きは最大 {math.degrees(yaw_amp):.1f}°): "
                       "向きが左右で鏡写しか", source_ja=src_ja),
        _check("sine steer left vs right: max |y_L + y_R| < 1e-9 m", "lateral_mirror_error", 0.0, mirror_y, "m", 1e-9, src,
               case_ja="同じ走りで、横の位置が左右で鏡写しか", source_ja=src_ja),
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
        susp_ja = {"rigid": "サスペンションなし", "spring": "ばねのサスペンション"}[susp]
        out.append(_check(f"free fall 0.3 s, {susp} suspension: z_N - (z_0 - g h^2 N(N+1)/2) < 1e-12 m",
                          "fall_error", 0.0, err, "m", 1e-12, "semi-implicit Euler sum of a constant acceleration",
                          case_ja=f"車を {n * h:.1f} 秒落とす ({susp_ja}): 高さが手計算と 1e-12 m より違わないか",
                          source_ja="一定の重力で 1 歩ずつ進める計算 (半陰的オイラー法) を足し合わせた式 "
                                    "z_0 - g h² N(N+1)/2"))
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
        _check(f"quarter car k_s {ks / 1e3:g} kN/m, k_t {kt / 1e3:g} kN/m, m_s {ms:g} kg (wheel 0.5 kg)", "ride_frequency",
               expected, f_small, "Hz", 3e-3, "(1/2 pi) sqrt(k_ride / m_s), k_ride = k_s k_t / (k_s + k_t)",
               case_ja=f"車の 1/4 (ばね {ks / 1e3:g} kN/m、タイヤ {kt / 1e3:g} kN/m、車体 {ms:g} kg、輪 0.5 kg) の"
                       "上下に揺れる速さ",
               source_ja="ばねとタイヤを直列につないだ 1 つのばねと車体の揺れ: (1/2π) √(k_ride / m_s)、"
                         "k_ride = k_s k_t / (k_s + k_t)"),
        _check(f"quarter car, same springs, m_s {ms25:g} kg, wheel {mu:g} kg", "ride_frequency", exact, f25, "Hz", 1e-3,
               "two-mass system, lower root of m_s m_u w^4 - (m_s (k_s + k_t) + m_u k_s) w^2 + k_s k_t = 0",
               case_ja=f"同じばねで、車体 {ms25:g} kg、輪 {mu:g} kg のときの、上下に揺れる速さ",
               source_ja="車体と輪の 2 つの重さの揺れ。m_s m_u ω⁴ - (m_s (k_s + k_t) + m_u k_s) ω² + k_s k_t = 0 の"
                         "小さい方の解"),
    ]


def check_rigid_tilt() -> list[dict]:
    d = box_design(mass=500.0, cg_x=-0.9, cg_z=0.5, track=1.2, susp="rigid",
                   variables={"tyre_stiffness": {"value": 10000.0, "unit": "N/mm"},
                              "tire_mu": {"value": 2.0, "unit": "1"}}, name="rigid tilt check")
    veh = build_vehicle(d)
    left = tilt_sweep(veh, left_up=True, **CHECK_TILT)["tip_angle"]
    right = tilt_sweep(veh, left_up=False, **CHECK_TILT)["tip_angle"]
    t, h = 1.2, 0.5
    expected = math.degrees(math.atan(t / 2.0 / h))
    src = f"tan(theta) = (t/2) / h, t = {t:g} m, h = {h:g} m"
    src_ja = f"重心が下の輪の真上に来る角度: tan θ = (輪の幅 / 2) / 重心の高さ、輪の幅 {t:g} m、重心の高さ {h:g} m"
    return [
        _check("rigid tilt table, left side up", "tilt_angle", expected, None if left is None else math.degrees(left), "deg", 1e-3, src,
               case_ja="サスペンションの無い車を傾斜台に載せ、左を上にして倒れる角度", source_ja=src_ja),
        _check("rigid tilt table, right side up", "tilt_angle", expected, None if right is None else math.degrees(right), "deg", 1e-3, src,
               case_ja="同じ車で、右を上にして倒れる角度", source_ja=src_ja),
        _check("rigid tilt table, left - right (bit for bit)", "tilt_angle_difference", 0.0,
               None if left is None or right is None else math.degrees(left - right), "deg", 0.0, "symmetry",
               passed=left is not None and left == right,
               case_ja="左を上にした角度と、右を上にした角度の差", source_ja="左右で 1 ビットも違わない (形が左右対称なので)"),
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
    grade, secs = 0.20, 10.0
    r = parked_drift(veh, grade, secs)
    return [_check(f"parked on a {grade * 100:g} % slope, brakes on: drift in {secs:g} s < 0.1 mm", "parked_drift", 0.0,
                   r["drift"] * 1000.0, "mm", 0.1, "a held vehicle must not creep (tyre deflection model has no slip at rest)",
                   case_ja=f"{grade * 100:g} % の坂にブレーキをかけて止めておき、{secs:g} 秒で 0.1 mm より動かないか",
                   source_ja="ブレーキで止めた車は、じわじわ動いてはいけない (タイヤのたわみのモデルは、止まっているときに滑らない)")]


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
    case_ja = f"確かめ用の車の左前輪のタイヤ (Magic Formula、止まっているときの荷重 {fz:.0f} N、摩擦係数 {mu:g})"
    src_ja = "タイヤの式 D sin(C atan(B x - E (B x - atan B x))) は、いちばん大きい値が D、滑り 0 での傾きが B C D"
    return [
        _check(case + ": peak Fx", "peak_longitudinal_force", mu * fz, peak_x, "N", 1e-6, src + "; D_x = mu_x F_z",
               case_ja=case_ja + ": 前後の力のいちばん大きい値", source_ja=src_ja + "。D = 摩擦係数 × 荷重"),
        _check(case + ": peak Fy", "peak_lateral_force", mu * fz, peak_y, "N", 1e-6, src + "; D_y = mu_y F_z",
               case_ja=case_ja + ": 横の力のいちばん大きい値", source_ja=src_ja + "。D = 摩擦係数 × 荷重"),
        _check(case + ": slope dFx/dkappa at zero slip = the slip stiffness K0 takes", "slip_stiffness", c.c_long, kx,
               "N", 1e-6, src + " = K_xk (4.E15) scaled to the design",
               case_ja=case_ja + ": 滑り 0 のときの前後の力の立ち上がり (手計算が使う前後の滑りの硬さと同じか)",
               source_ja=src_ja + "。式の K_xk (4.E15) を設計に合わせたもの"),
        _check(case + ": slope -dFy/dalpha at zero slip = the cornering stiffness K0 takes", "cornering_stiffness",
               c.c_lat, ky, "N/rad", 1e-6, src + " = K_ya (4.E25) scaled to the design",
               case_ja=case_ja + ": 横滑りの角度 0 のときの横の力の立ち上がり (手計算が使う曲がる硬さと同じか)",
               source_ja=src_ja + "。式の K_ya (4.E25) を設計に合わせたもの"),
        _check(case + ": combined slip, kappa -1..1 and alpha -55..55 deg: largest hypot(Fx/Dx, Fy/Dy)",
               "friction_ellipse", 1.0, worst, "1", 0.05,
               "the friction ellipse of the pure-slip peaks; the combined-slip weighting is a fit, so 5 % over is allowed",
               passed=worst <= 1.05,
               case_ja=case_ja + ": 前後と横に同時に滑らせたとき (前後の滑り -1〜1、横滑りの角度 -55〜55°)、"
                                 "力が摩擦の楕円の外に出ないか",
               source_ja="前後だけ・横だけのときのいちばん大きい力で作る摩擦の楕円。同時に滑るときの式は測定に合わせた形なので、"
                         "5 % まで外に出てよい"),
        _check(case + ": aligning moment at alpha +2 deg and -2 deg turns the wheel toward its motion (Mz(+2) > 0 > Mz(-2))",
               "aligning_moment_sign", 1.0, 1.0 if mz_l > 0.0 > mz_r else 0.0, "1", 0.0,
               "ISO signs: positive alpha slides the contact to the left, and the moment turns the heading that way",
               case_ja=case_ja + ": 横滑りの角度 +2° と -2° で、タイヤを戻す回る力が、進む向きにタイヤを向けるか",
               source_ja="ISO の符号の決まり: 横滑りの角度が + なら接地点は左に滑り、回る力はタイヤをその向きに回す"),
    ]


CACHE_ENV = "HIRAKU_CHECK_CACHE"  # "1": the tests' runs keep check results on disk (never a real HIRAKU run)
FROM_CACHE: set[str] = set()       # the check sets this process took from the disk cache


@lru_cache(maxsize=1)
def code_key() -> str:
    """Hash of everything K2's checks depend on: the K2 package, the K0 helpers it calls, the
    loading rule, the tyre data, and the numpy and scipy versions."""
    import hashlib

    import scipy

    here = Path(__file__).resolve().parent
    repo = here.parents[2]
    files = sorted(here.rglob("*.py")) + [repo / "hiraku" / "solvers" / "k0_handcalc" / n for n in ("rigid.py", "sections.py")]
    files += [repo / "hiraku" / "contract" / "loading.py"] + sorted((repo / "data" / "tyres").glob("*.json"))
    h = hashlib.sha256()
    for p in files:
        h.update(p.relative_to(repo).as_posix().encode())
        h.update(p.read_bytes())
    h.update(f"numpy {np.__version__} scipy {scipy.__version__}".encode())
    return h.hexdigest()[:20]


def cached(name: str, compute) -> list[dict]:
    """compute(), or with HIRAKU_CHECK_CACHE=1 the records kept for the same code_key under
    runs/.check_cache (made there the first time). Only the tests set the variable: a real
    HIRAKU run computes its checks every time."""
    if os.environ.get(CACHE_ENV) != "1":
        return compute()
    path = Path(__file__).resolve().parents[3] / "runs" / ".check_cache" / f"k2-{code_key()}-{name}.json"
    try:
        out = json.loads(path.read_text(encoding="utf-8"))
        FROM_CACHE.add(name)
        return out
    except (OSError, ValueError):
        pass
    out = compute()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(out), encoding="utf-8")
    os.replace(tmp, path)
    return out


@lru_cache(maxsize=1)
def _base() -> tuple:
    def compute():
        out = []
        for fn in (check_static_loads, check_mirror, check_free_fall, check_quarter_car, check_rigid_tilt,
                   check_parked_on_slope, check_tyre):
            out += fn()
        return out

    return tuple(cached("base", compute))


def base_checks() -> list[dict]:
    """All seven pre-run checks (computed once per process)."""
    return [dict(c) for c in _base()]
