"""Turning circle at full lock (S6) and the steady circle driven faster and faster until a wheel lifts (S2)."""

from __future__ import annotations

import math
from collections import deque

import numpy as np
from scipy.optimize import brentq

from ..checks import _check
from ..drive import SpeedController, controls, start_rolling
from ..model import DesignError, box_design, build_vehicle
from ..record import Recorder
from ..scenario import new_sim
from ..units import G0
from .base import MOTOR, MU, TIPPED_ROLL, K2Tool, P, Pair, _min_load, _res, _side_ids


def full_lock(veh, max_wheel: float, left: bool = True) -> float:
    """The bicycle-model angle delta (rad, + left) whose Ackermann wheel angles (veh.wheel_steer) put the
    inner steered wheel, the one turned most, at max_wheel (rad)."""
    if not any(c.steer and c.kind == "wheel" for c in veh.corners):
        raise DesignError("no support has actuation.steer; a turning circle needs a steered wheel")
    f = lambda d: float(np.max(np.abs(veh.wheel_steer(d)))) - max_wheel
    d = brentq(f, 1e-9, max_wheel, xtol=1e-14, rtol=1e-14)
    return d if left else -d


def fit_circle(xy: np.ndarray) -> tuple[np.ndarray, float]:
    """Least-squares circle through points (n, 2): (centre, radius) (algebraic fit: x^2 + y^2 + D x + E y + F = 0)."""
    a = np.column_stack([xy[:, 0], xy[:, 1], np.ones(len(xy))])
    b = -(xy[:, 0] ** 2 + xy[:, 1] ** 2)
    (d, e, f), *_ = np.linalg.lstsq(a, b, rcond=None)
    c = np.array([-d / 2.0, -e / 2.0])
    return c, float(math.sqrt(max(c @ c - f, 0.0)))


def body_points(design: dict) -> list[tuple[str, np.ndarray]]:
    """The plan corners (m, design frame) of every part box: (part id, point)."""
    out = []
    for p in design.get("parts", []):
        box = p.get("box")
        if not box:
            continue
        c, s = np.asarray(box["center_mm"], float) / 1000.0, np.asarray(box["size_mm"], float) / 2000.0
        for sx in (-1, 1):
            for sy in (-1, 1):
                out.append((p["id"], np.array([c[0] + sx * s[0], c[1] + sy * s[1], c[2]])))
    return out


TURN_RAD = math.pi  # the heading turns this much while the circles are measured (steady: half a turn is enough)


def circle_run(veh, design: dict, delta: float, speed: float, *, dt: float = 1e-3, rec_hz: float = 0.0,
               settle_s: float = 2.0) -> dict:
    """Drive at `speed` (m/s) with the bicycle angle `delta` held, from settle_s on while the heading turns
    TURN_RAD: the circle fitted to each wheel's contact centre (world x, y) and the largest distance of
    any part box's plan corner from the outer front wheel's circle's centre."""
    sim = new_sim(veh, dt=dt, settle_s=1.0)
    rec = Recorder(sim, rec_hz) if rec_hz > 0 else None
    start_rolling(sim, speed)
    ctrl = SpeedController(sim)
    pts = body_points(design)
    contacts, bodies, yaw0, turned, prev = [], [], None, 0.0, None
    t_max = settle_s + 4.0 * math.pi * 20.0 / max(speed, 0.1) + 10.0
    while sim.t < t_max:
        drive, brake = ctrl.torques(sim, speed)
        sim.step(controls(sim, delta, drive, brake))
        if rec is not None:
            rec.tick({"steer": ("deg", math.degrees(delta))})
        if sim.t < settle_s:
            continue
        yaw = sim.attitude()[2]
        if prev is not None:
            turned += (yaw - prev + math.pi) % (2.0 * math.pi) - math.pi
        prev = yaw
        contacts.append([sim.out["p"][i][:2] for i in range(sim.n)])
        r, o = sim.rotation(), sim.design_origin()
        bodies.append(np.array([(o + r @ p)[:2] for _, p in pts]))
        if abs(turned) >= TURN_RAD:
            break
    if abs(turned) < TURN_RAD:
        raise DesignError(f"the vehicle did not turn half a circle at full lock in {t_max:.0f} s")
    contacts = np.array(contacts)  # (n_steps, n_wheels, 2)
    circles = [fit_circle(contacts[:, i, :]) for i in range(sim.n)]
    steered = [i for i, c in enumerate(veh.corners) if c.steer and c.kind == "wheel"]
    outer = max(steered, key=lambda i: circles[i][1])
    centre = circles[outer][0]
    bodies = np.array(bodies)  # (n_steps, n_points, 2)
    dist = np.sqrt(((bodies - centre) ** 2).sum(axis=2))
    k = int(np.argmax(dist.max(axis=0)))
    return {"circles": circles, "outer": outer, "centre": centre, "body_radius": float(dist[:, k].max()),
            "body_part": pts[k][0] if pts else None, "recorder": rec, "sim": sim}


class TurningCircleTool(K2Tool):
    """Turning circle at full lock (S6): driven slowly with the steering at full lock, the inner front
    wheel at max_steer_angle and the outer one at its Ackermann angle (Vehicle.wheel_steer).

    Results (the larger of the two directions when both are run; where = which way and which wheel):
    turning_radius_wheel (m, the circle of the outer front wheel's contact centre), turning_radius_body
    (m, the circle of the farthest corner of any part box, plan view), turning_center_x / _y (m, the
    circle's centre in the road frame, the vehicle starting at its design origin), inner_steer_angle and
    outer_steer_angle (deg). The scene has both circles (kind circle). hot_spots: the radius each way.
    """

    name = "turning_circle"
    title = "小回り (K2 で全切りにして回る)"
    domain = "handling"
    tests = ["S6"]
    params = (P("speed", "km/h", 5.0, "held speed while turning (slow, so the tyres hardly slip)"),
              P("direction", None, "both", "which way to turn", choices=("both", "left", "right")),
              P("max_steer_angle", "deg", None, "the inner front wheel's largest steer angle (default: the design "
                "variable max_steer_angle, else K2's 35 deg)", var=("max_steer_angle",)),
              MU)
    pairs = (Pair("turning_radius_wheel", "turning.min_radius", "R", (("delta_i", "max_steer_angle"),),
                  "L and t from the K2 model (the steered axle to the unsteered one, the steered axle's track); "
                  "K2 drives slowly, so its tyres slip a little and the circle comes out a little larger"),)

    def own_checks(self) -> list[dict]:
        d = box_design(mass=500.0, cg_x=-0.9, cg_z=0.5, wheelbase=1.8, track=1.2, susp="rigid", wheel_mass=20.0,
                       variables={"tyre_stiffness": {"value": 10000.0, "unit": "N/mm"},
                                  "max_steer_angle": {"value": 30.0, "unit": "deg"}}, name="turning check")
        veh = build_vehicle(d)
        di = math.radians(30.0)
        speed = 2.0
        r = circle_run(veh, d, full_lock(veh, di), speed)
        L, t = abs(veh.l_steer), 1.2
        d_o = math.atan(1.0 / (1.0 / math.tan(di) + t / L))
        expected = L / math.sin(d_o)
        return [_check(f"rigid vehicle, wheelbase {L:g} m, track {t:g} m, inner wheel {math.degrees(di):g} deg, "
                       f"{speed:g} m/s: outer front wheel's turning radius", "turning_radius_wheel", expected,
                       r["circles"][r["outer"]][1], "m", 1e-2,
                       "Ackermann: cot(delta_o) = cot(delta_i) + t / L, R = L / sin(delta_o) (turning.min_radius)",
                       case_ja=f"サスペンションの無い車 (軸距 {L:g} m、輪距 {t:g} m) を、内側の前輪 {math.degrees(di):g}° の"
                               f"全切りで {speed:g} m/s で回す: 外側の前輪の回る円の半径",
                       source_ja="アッカーマンの舵: cot δ外 = cot δ内 + 輪距 / 軸距、半径 = 軸距 / sin δ外 "
                                 "(turning.min_radius)")]

    def _run(self, design: dict, lp: dict) -> dict:
        veh = self.vehicle(design, lp)
        notes = list(veh.assumptions)
        di = self.q(lp, "max_steer_angle", "rad")
        if di is None:
            di = veh.max_steer
            notes.append(f"max_steer_angle {math.degrees(di):g} deg read as the inner front wheel's largest angle "
                         "(K2's driver model reads it as the bicycle model's angle)")
        speed = self.q(lp, "speed", "m/s")
        sides = {"both": ["left", "right"], "left": ["left"], "right": ["right"]}[self.text(lp, "direction")]
        runs = {}
        for side in sides:
            delta = full_lock(veh, di, side == "left")
            runs[side] = (delta, circle_run(veh, design, delta, speed, dt=self.dt, rec_hz=self.record_hz))
        worst = max(sides, key=lambda s: runs[s][1]["circles"][runs[s][1]["outer"]][1])
        delta, r = runs[worst]
        cid = veh.corners[r["outer"]].id
        wheel_r = r["circles"][r["outer"]][1]
        angles = [abs(a) for a in veh.wheel_steer(delta)]
        steered = [a for a, c in zip(angles, veh.corners) if c.steer and c.kind == "wheel"]
        res = [_res("turning_radius_wheel", wheel_r, "m", f"turning {worst}, {cid}"),
               _res("turning_radius_body", r["body_radius"], "m", f"turning {worst}, {r['body_part']}"),
               _res("turning_center_x", float(r["centre"][0]), "m", f"turning {worst}"),
               _res("turning_center_y", float(r["centre"][1]), "m", f"turning {worst}"),
               _res("inner_steer_angle", math.degrees(max(steered)), "deg"),
               _res("outer_steer_angle", math.degrees(min(steered)), "deg")]
        cx, cy = (float(v) * 1000.0 for v in r["centre"])
        scene = [{"kind": "circle", "position_mm": [cx, cy, 0.0], "size_mm": [2000.0 * wheel_r, 2000.0 * wheel_r, 0.0],
                  "label": f"外側の前輪 ({cid}) の回る円 半径 {wheel_r:.2f} m"},
                 {"kind": "circle", "position_mm": [cx, cy, 0.0],
                  "size_mm": [2000.0 * r["body_radius"], 2000.0 * r["body_radius"], 0.0],
                  "label": f"車体のいちばん外の点 ({r['body_part']}) の回る円 半径 {r['body_radius']:.2f} m"}]
        # the record, the circles and the results are all of the same turn (the larger radius)
        rec = r["recorder"]
        places = [{"where": f"turning {s}", "quantity": "turning_radius_wheel",
                   "value": runs[s][1]["circles"][runs[s][1]["outer"]][1], "unit": "m", "judged": True} for s in sides]
        out = {"results": res, "scene": scene, "assumptions": notes, "places": places}
        if rec is not None:
            out["series"] = rec.series()
            out["vectors"] = rec.vectors(-1)
        return out


# ------------------------------------------------------------------ the steady circle (S2)
# The tyres no longer hold the circle when the non-steered axle's centre has drifted this share of the radius
# outward (HIRAKU's choice, an assumption, no source): the run stops there as "slid out".
SLIDE_SHARE = 0.10
# The lateral acceleration reached is the mean over the run's last MEAN_S (s): the held value, or the value at the lift
# (the speed only rises, so the end is the most). Not the largest mean over the run: the speed overshoots a little
# where the ramp ends (8.09 m/s for 8 on the check vehicle, 0.338 g against the held 0.326 g; K2 runs, 2026-11-07).
MEAN_S = 0.2


def circle_pursuit(sim, centre: np.ndarray, radius: float, sign: float, delta0: float,
                   preview_time: float = 0.6, min_preview: float = 3.0, steer_rate: float = math.radians(400.0)):
    """Pure pursuit on a circle (road frame, centre (x, y), radius, sign + counter-clockwise = turning left): the
    point the driver aims at is on the circle, the preview length ahead along it of the non-steered axle's centre.
    The same driver as drive.PathDriver (preview 0.6 s, at least 3 m, 400 deg/s: HIRAKU's choice there), on a
    circle instead of a line y(x). Returns steer() -> the bicycle angle (rad), starting from delta0."""
    veh = sim.veh
    state = {"delta": delta0}

    def steer() -> float:
        r = sim.rotation()
        ref = sim.design_origin() + r @ [veh.x_ref, 0.0, 0.0]
        yaw = math.atan2(r[1, 0], r[0, 0])
        v = max(abs(sim.forward_speed()), 0.5)
        ld = max(min_preview, preview_time * v)
        phi = math.atan2(ref[1] - centre[1], ref[0] - centre[0]) + sign * ld / radius
        dx, dy = centre[0] + radius * math.cos(phi) - ref[0], centre[1] + radius * math.sin(phi) - ref[1]
        c, s = math.cos(yaw), math.sin(yaw)
        bx, by = c * dx + s * dy, -s * dx + c * dy
        target = math.atan2(2.0 * veh.l_steer * math.sin(math.atan2(by, bx)), math.hypot(bx, by))
        target = max(-veh.max_steer, min(veh.max_steer, target))
        step = steer_rate * sim.dt
        state["delta"] += max(-step, min(step, target - state["delta"]))
        return state["delta"]

    return steer


def steady_circle(veh, radius: float, speed: float, *, left: bool = True, ramp_s: float = 20.0, hold_s: float = 3.0,
                  dt: float = 1e-3, rec_hz: float = 0.0, motor=None) -> dict:
    """The circle of `radius` (m, the non-steered axle's centre on it) driven from half of `speed` (m/s) up to it
    over ramp_s, then held hold_s: a slowly growing lateral acceleration. Stops early when both wheels of the inner
    side are off the ground together (lifted), the body has rolled past TIPPED_ROLL (tipped) or the non-steered
    axle has drifted SLIDE_SHARE of the radius outward (slid: the tyres are at their friction).

    Returns the inner side's least road load (sum of its wheels, N) and each wheel's, the mean of the body's
    lateral acceleration over the last MEAN_S (m/s^2, "ay_end"), any_lift / two_lift, tipped, slid, the largest
    roll, steer and radius error, the speed at the end, and the recorder."""
    sign = 1.0 if left else -1.0
    sim = new_sim(veh, dt=dt, settle_s=1.0)
    rec = Recorder(sim, rec_hz) if rec_hz > 0 else None
    r0 = sim.rotation()
    ref0 = sim.design_origin() + r0 @ [veh.x_ref, 0.0, 0.0]
    yaw0 = math.atan2(r0[1, 0], r0[0, 0])
    # the centre is to the side the vehicle turns to, square to its heading at the start
    centre = np.array([ref0[0] - sign * radius * math.sin(yaw0), ref0[1] + sign * radius * math.cos(yaw0)])
    v0 = 0.5 * speed
    start_rolling(sim, v0)
    steer = circle_pursuit(sim, centre, radius, sign, sign * math.atan(veh.l_steer / radius))
    ctrl = SpeedController(sim, motor=motor)
    inner = _side_ids(sim, left)
    outer = _side_ids(sim, not left)
    st = {"min_load": {c.id: math.inf for c in veh.corners}, "min_inner": math.inf, "min_outer": math.inf,
          "any_lift": False, "two_lift": False, "tipped": False, "slid": False,
          "max_roll": 0.0, "max_steer": 0.0, "max_err": 0.0}
    window = deque(maxlen=max(1, int(round(MEAN_S / dt))))
    t0 = sim.t
    while sim.t - t0 < ramp_s + hold_s:
        target = v0 + (speed - v0) * min(1.0, (sim.t - t0) / ramp_s)
        delta = steer()
        drive, brake = ctrl.torques(sim, target)
        sim.step(controls(sim, delta, drive, brake))
        nt = sim.out["ntot"]
        for i, c in enumerate(veh.corners):
            st["min_load"][c.id] = min(st["min_load"][c.id], nt[i])
        st["any_lift"] = st["any_lift"] or any(x <= 0.0 for x in nt)
        n_in = sum(nt[i] for i in inner)
        st["min_inner"] = min(st["min_inner"], n_in)
        st["min_outer"] = min(st["min_outer"], sum(nt[i] for i in outer))
        r = sim.rotation()
        acc = sim.out["acc"]
        window.append(abs(r[0, 1] * acc[0] + r[1, 1] * acc[1] + r[2, 1] * acc[2]))
        st["max_roll"] = max(st["max_roll"], abs(sim.attitude()[0]))
        st["max_steer"] = max(st["max_steer"], abs(delta))
        ref = sim.design_origin() + r @ [veh.x_ref, 0.0, 0.0]
        err = math.hypot(ref[0] - centre[0], ref[1] - centre[1]) - radius
        st["max_err"] = max(st["max_err"], abs(err))
        if rec is not None:
            rec.tick({"steer": ("deg", math.degrees(delta)), "target_speed": ("km/h", target * 3.6),
                      "radius_error": ("m", err)})
            if n_in <= st["min_inner"]:
                rec.snap = []
                rec.keep_snapshot()
        if inner and all(nt[i] <= 0.0 for i in inner):
            st["two_lift"] = True
            break
        if abs(sim.attitude()[0]) > TIPPED_ROLL:
            st["tipped"] = True
            break
        if err > SLIDE_SHARE * radius:
            st["slid"] = True
            break
    st.update(ay_end=sum(window) / max(len(window), 1), centre=centre, speed_end=sim.forward_speed(),
              t_run=sim.t - t0, recorder=rec, sim=sim, inner_side="left" if left else "right")
    return st


class SteadyCircleTool(K2Tool):
    """Rollover in a turn (S2): the steady circle of `radius` driven at a speed raised slowly from half of
    `speed` to it (a slowly growing lateral acceleration), the driver following the circle (pure pursuit, as the
    lane change's), until both inner wheels lift, the tyres slide out (the non-steered axle SLIDE_SHARE of the
    radius outside the circle) or the speed reaches `speed` and is held.

    Results: min_side_load (N, the inner side's wheels together, the least over the run: 0 or below when they
    lifted), min_wheel_load (N, where), max_lateral_accel_g / max_lateral_accel (g, m/s^2: the body's lateral
    acceleration reached, its mean over the run's last MEAN_S; where: the speed at the end), wheel_lift (1: a wheel
    at 0 load at some time), two_wheel_lift (1: both inner wheels off together; the run stops), tipped (1),
    max_roll (deg), max_steer (deg, the bicycle angle) and max_path_error (m, the non-steered axle's centre off
    the circle). The assumptions say how the run ended (held, lifted, slid out or tipped).
    """

    name = "steady_circle"
    title = "定常円旋回で速さを上げる (K2 + 運転のモデル)"
    domain = "handling"
    tests = ["S2"]
    params = (P("radius", "m", None, "radius of the circle the non-steered axle's centre follows", required=True),
              P("speed", "km/h", None, "the speed the run is raised to (from half of it) and held", required=True),
              P("direction", None, "left", "which way the circle turns", choices=("left", "right")),
              P("ramp_time", "s", 20.0, "time over which the speed is raised (HIRAKU's choice: slow enough to be "
                "near steady)"),
              P("hold_time", "s", 3.0, "time the speed is held at the end"),
              MU, *MOTOR)
    pairs = (Pair("max_lateral_accel_g", "lane_change.steady_steer", "a_y", (("R", "radius"), ("v", "speed")),
                  "a_y = v^2 / (R g) of the circle at the load case's speed; K2's is the body's, on the circle the "
                  "driver keeps (the tyres' slip angles put the CG a little off it), and lower when the run stops "
                  "before the speed (lift, slide)"),
             Pair("min_side_load", "lane_change.inner_wheel_load", "W_inner",
                  note="a_y from lane_change.steady_steer; K2 adds the body's roll on its springs, which moves the "
                       "CG outward, and the CG's offset from the centre line"))

    def own_checks(self) -> list[dict]:
        out = []
        # 1. rigid supports, stiff tyres (as the lane change's check): no body roll, so the moment balance about
        # the outer contact line is the whole story
        d = box_design(mass=500.0, cg_x=-0.9, cg_z=0.5, wheel_mass=20.0, susp="rigid",
                       variables={"tyre_stiffness": {"value": 10000.0, "unit": "N/mm"}}, name="steady circle check")
        veh = build_vehicle(d)
        radius, v = 20.0, 8.0
        st = steady_circle(veh, radius, v, ramp_s=6.0, hold_s=3.0)
        ay = v * v / (radius * G0)
        out.append(_check(f"rigid vehicle on a {radius:g} m circle at {v:g} m/s: steady lateral acceleration",
                          "max_lateral_accel_g", ay, st["ay_end"] / G0, "g", 2e-2,
                          "a_y = v^2 / (R g) (lane_change.steady_steer)",
                          case_ja=f"サスペンションの無い車が半径 {radius:g} m の円を {v:g} m/s で回るときの横の加速度",
                          source_ja="横の加速度 = 速さ² / (半径 × g)"))
        ground = min(float(c.pos[2]) - c.radius for c in veh.corners)
        h = float(veh.cg_total[2]) - ground
        t = max(float(c.pos[1]) for c in veh.corners) - min(float(c.pos[1]) for c in veh.corners)
        m = veh.m_total
        w_in = m * G0 / 2.0 - m * st["ay_end"] * h / t
        out.append(_check(f"the same run: inner wheels' load at that lateral acceleration (h {h * 1000:.0f} mm, "
                          f"track {t * 1000:.0f} mm)", "min_side_load", w_in, st["min_inner"], "N", 2e-2,
                          "W_inner = m g / 2 - m a_y h / t (lane_change.inner_wheel_load) at K2's a_y",
                          case_ja="同じ走りで、内側の 2 輪の荷重の和 (その横の加速度で)",
                          source_ja="外側の接地の線のまわりのモーメントの釣り合い: 内側 = m g / 2 − m a_y h / 輪距"))
        # 2. a tall rigid vehicle on high friction: the inner wheels lift at a_y = t / (2 h) before the tyres slide
        tall = box_design(mass=500.0, cg_x=-0.9, cg_z=1.0, wheel_mass=0.0, susp="rigid",
                          variables={"tyre_stiffness": {"value": 10000.0, "unit": "N/mm"},
                                     "tire_mu": {"value": 1.2, "unit": "1"}}, name="steady circle lift check")
        veh = build_vehicle(tall)
        h = float(veh.cg_total[2]) - min(float(c.pos[2]) - c.radius for c in veh.corners)
        lift = 1.2 / (2.0 * h)
        radius = 15.0
        v_top = math.sqrt(1.3 * lift * G0 * radius)  # asks 1.3 times the lift acceleration at the top speed
        st = steady_circle(veh, radius, v_top, ramp_s=12.0, hold_s=1.0)
        out.append(_check(f"tall rigid vehicle (h {h * 1000:.0f} mm, track 1200 mm, mu 1.2) on a {radius:g} m circle, "
                          "speed raised: lateral acceleration at which the inner wheels lift", "max_lateral_accel_g",
                          lift, st["ay_end"] / G0, "g", 5e-2,
                          f"a_y_lift = t / (2 h) (lane_change.inner_wheel_load); K2's is the mean over the last "
                          f"{MEAN_S:g} s before the lift, which lags the rising value a little",
                          passed=st["two_lift"] and abs(st["ay_end"] / G0 - lift) <= 5e-2 * lift,
                          case_ja=f"重心の高い車 (重心 {h * 1000:.0f} mm、輪距 1200 mm、μ 1.2) で半径 {radius:g} m の円の"
                                  "速さを上げていき、内側の 2 輪が浮くときの横の加速度",
                          source_ja="内側の輪が浮く横の加速度 = 輪距 / (2 × 重心の高さ)"))
        return out

    def _run(self, design: dict, lp: dict) -> dict:
        veh = self.vehicle(design, lp)
        if not any(c.steer and c.kind == "wheel" for c in veh.corners):
            return {"error": "no support has actuation.steer; a steady circle needs a steered wheel"}
        radius, speed = self.q(lp, "radius", "m"), self.q(lp, "speed", "m/s")
        if radius is None or speed is None:
            raise ValueError("the load case must give radius (m) and speed (km/h)")
        notes = list(veh.assumptions) + [
            f"the speed is raised from {speed * 1.8:g} to {speed * 3.6:g} km/h over {self.q(lp, 'ramp_time', 's'):g} s "
            f"on a {radius:g} m circle, then held {self.q(lp, 'hold_time', 's'):g} s; the driver follows the circle "
            "(pure pursuit, preview 0.6 s, at least 3 m)",
            f"the tyres count as slid out when the non-steered axle is {SLIDE_SHARE:.0%} of the radius outside the "
            "circle (HIRAKU's choice)"]
        try:
            motor = self.motor(lp)
        except DesignError:
            motor = None
            notes.append("no motor given (motor_torque, gear_ratio): the speed controller's drive has no limit")
        left = self.text(lp, "direction") == "left"
        st = steady_circle(veh, radius, speed, left=left, ramp_s=self.q(lp, "ramp_time", "s"),
                           hold_s=self.q(lp, "hold_time", "s"), dt=self.dt, rec_hz=self.record_hz, motor=motor)
        side = st["inner_side"]
        v_end = st["speed_end"] * 3.6
        if st["two_lift"]:
            notes.append(f"the inner ({side}) wheels lifted together at {v_end:.1f} km/h; the run stops there")
        elif st["tipped"]:
            notes.append(f"the vehicle tipped over at {v_end:.1f} km/h; the run stops there (K2 has no body-ground "
                         "contact)")
        elif st["slid"]:
            notes.append(f"the tyres slid out at {v_end:.1f} km/h (the circle could not be held): the lateral "
                         f"acceleration reached is the friction's, below what {speed * 3.6:g} km/h asks")
        else:
            notes.append(f"held the circle at {v_end:.1f} km/h with the inner wheels down")
        cid, mn = _min_load(st["min_load"])
        res = [_res("min_side_load", st["min_inner"], "N", f"{side} (inner)"), _res("min_wheel_load", mn, "N", cid),
               _res("max_lateral_accel_g", st["ay_end"] / G0, "g", f"the last {MEAN_S:g} s, at {v_end:.1f} km/h"),
               _res("max_lateral_accel", st["ay_end"], "m/s^2", f"the last {MEAN_S:g} s, at {v_end:.1f} km/h"),
               _res("wheel_lift", 1.0 if st["any_lift"] else 0.0, "1"),
               _res("two_wheel_lift", 1.0 if st["two_lift"] else 0.0, "1"),
               _res("tipped", 1.0 if st["tipped"] else 0.0, "1"),
               _res("max_roll", math.degrees(st["max_roll"]), "deg"),
               _res("max_steer", math.degrees(st["max_steer"]), "deg"),
               _res("max_path_error", st["max_err"], "m")]
        cx, cy = (float(v) * 1000.0 for v in st["centre"])
        scene = [{"kind": "circle", "position_mm": [cx, cy, 0.0], "size_mm": [2000.0 * radius, 2000.0 * radius, 0.0],
                  "label": f"かじの無い車軸の中心がたどる円 半径 {radius:g} m"}]
        out = {"results": res, "scene": scene, "assumptions": notes,
               "places": [{"where": f"{side} (inner)", "quantity": "min_side_load", "value": st["min_inner"],
                           "unit": "N", "judged": True}]}
        rec = st["recorder"]
        if rec is not None:
            out["series"] = rec.series()
            out["vectors"] = rec.vectors(-1)
        return out
