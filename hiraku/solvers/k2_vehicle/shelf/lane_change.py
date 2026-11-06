"""Double lane change (S3) and the path-following run it shares with the off-road roll."""

from __future__ import annotations

import math

from ..checks import _check
from ..drive import PathDriver, SpeedController, controls, start_rolling
from ..model import DesignError, box_design, build_vehicle
from ..record import Recorder
from ..scenario import new_sim
from ..units import G0
from .base import MOTOR, MU, TIPPED_ROLL, K2Tool, P, Pair, _box, _half_width, _min_load, _ramp_gravity, _res, _side_ids
from .mounts import LoadLog, has_mounts, mount_peak, mount_series, mount_vectors, state_at

# Section lengths (m) and lane offset of a double lane change: 15 / 30 / 25 / 25 / 15 / 15 m, offset
# 3.5 m. Source: ISO 3888-1:2018 Table 1 (the public sample,
# https://cdn.standards.iteh.ai/samples/67973/daa839ed4aca420788d9b4a51cd6cc95/ISO-3888-1-2018.pdf),
# checked against it on 2026-11-03 (README, the table of values checked). The course here is a smooth
# centre line, not the cone gates.
DLC_SECTIONS = (15.0, 30.0, 25.0, 25.0, 15.0, 15.0)
DLC_OFFSET = 3.5


# Lane widths of sections 1, 3 and 5 for a vehicle of width W (m, without mirrors): 1.1 W + 0.25,
# 1.2 W + 0.25, 1.3 W + 0.25. The same source and check.
DLC_LANE_WIDTH = ((1.1, 0.25), (1.2, 0.25), (1.3, 0.25))


def dlc_scene(width: float, sign: float, x0: float = 10.0, offset: float = DLC_OFFSET, sections=DLC_SECTIONS) -> list:
    """Cones and gates of the course around the K2 centre line (the driver follows the
    line, not the gates): a gate at the start and the end of sections 1, 3 and 5, a cone
    at each end of a gate."""
    starts = [x0 + sum(sections[:k]) for k in range(len(sections) + 1)]
    out = []
    for lane, (a, b) in zip((0, 2, 4), DLC_LANE_WIDTH):
        w = a * width + b
        yc = sign * offset if lane == 2 else 0.0
        for x in (starts[lane], starts[lane + 1]):
            label = f"区間 {lane + 1} の門 (幅 {w:.2f} m = {a:g} W + {b:g}、ISO 3888-1 の配置、確かめていない)"
            out.append(_box("gate", label, (x, yc - w / 2.0, 0.0), (x, yc + w / 2.0, 0.0)))
            out += [{"kind": "cone", "position_mm": [round(x * 1000.0, 1), round((yc + s * w / 2.0) * 1000.0, 1), 0.0]}
                    for s in (-1.0, 1.0)]
    return out


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
             gravity=None, steer_fn=None, ramp_s: float = 1.0, motor=None) -> dict:
    """Drive along y_ref(x) at a held speed until the design origin passes x_end.

    motor: the speed controller's drive is held to the motor's torque at the wheels (drive.SpeedController); without
    it the drive has no limit.

    gravity = (gravity_of_angle, angle): the slope is made by turning gravity over ramp_s
    with the brakes held first; that turn is in the record (slope_angle), and the angle at
    which the high-side wheels all left the ground is st["tip_angle"]. A body rolled past
    TIPPED_ROLL has tipped over: the run stops there (st["tipped"]).
    """
    sim = new_sim(veh, road=road, dt=dt, settle_s=1.0)
    rec = Recorder(sim, rec_hz) if rec_hz > 0 else None
    left = _side_ids(sim, True)
    right = _side_ids(sim, False)

    def extra(delta=0.0):
        cg = sim.cg_world()
        out = {"steer": ("deg", math.degrees(delta)), "path_y": ("m", y_ref(float(cg[0])))}
        if gravity is not None:
            out["slope_angle"] = ("deg", math.degrees(gravity[1]))
        return out

    tip = None
    if gravity is not None:
        high = left if gravity[1] > 0 else right
        tip = _ramp_gravity(sim, gravity[0], gravity[1], ramp_s, rec=rec, up=high, extra=extra)
    t0 = sim.t
    tipped = abs(sim.attitude()[0]) > TIPPED_ROLL
    if not tipped:
        start_rolling(sim, speed)
    drv = PathDriver(sim, y_ref)
    ctrl = SpeedController(sim, motor=motor)
    log = LoadLog(sim) if rec is not None and any(has_mounts(c) for c in veh.corners) else None
    st = {"min_load": {c.id: math.inf for c in veh.corners}, "two_lift_time": 0.0, "any_lift": False,
          "max_ay": 0.0, "max_roll": 0.0, "max_err": 0.0, "max_steer": 0.0, "min_side": {"left": math.inf, "right": math.inf},
          "mean_side": {"left": 0.0, "right": 0.0}, "az": [], "tip_angle": tip}
    steps = 0
    t_limit = 3.0 * abs(x_end) / max(speed, 0.5) + 5.0
    worst = math.inf
    while not tipped and sim.design_origin()[0] < x_end and sim.t - t0 < t_limit:
        delta = steer_fn(sim) if steer_fn is not None else drv.steer()
        drive, brake = ctrl.torques(sim, speed)
        sim.step(controls(sim, delta, drive, brake))
        if log is not None:
            log.tick()
        o = sim.out
        nt = o["ntot"]
        for i, c in enumerate(veh.corners):
            if nt[i] < st["min_load"][c.id]:
                st["min_load"][c.id] = nt[i]
        if any(x <= 0.0 for x in nt):
            st["any_lift"] = True
        if (left and all(nt[i] <= 0.0 for i in left)) or (right and all(nt[i] <= 0.0 for i in right)):
            st["two_lift_time"] += sim.dt
        n_left, n_right = sum(nt[i] for i in left), sum(nt[i] for i in right)
        st["min_side"]["left"] = min(st["min_side"]["left"], n_left)
        st["min_side"]["right"] = min(st["min_side"]["right"], n_right)
        st["mean_side"]["left"] += n_left
        st["mean_side"]["right"] += n_right
        steps += 1
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
            rec.tick(extra(delta))
            if m < worst:
                worst = m
                rec.snap = []
                rec.keep_snapshot()
        tipped = abs(sim.attitude()[0]) > TIPPED_ROLL
    if steps == 0:  # tipped while the slope was made: the loads of that moment
        nt = sim.out["ntot"]
        for i, c in enumerate(veh.corners):
            st["min_load"][c.id] = nt[i]
        st["any_lift"] = any(x <= 0.0 for x in nt)
        for side, ids in (("left", left), ("right", right)):
            st["min_side"][side] = st["mean_side"][side] = sum(nt[i] for i in ids)
        st["max_roll"] = abs(sim.attitude()[0])
        if rec is not None:
            rec.keep_snapshot()
    for side in ("left", "right"):
        st["mean_side"][side] /= max(steps, 1)
    st["tipped"] = tipped
    st["finished"] = not tipped and sim.design_origin()[0] >= x_end
    st["recorder"] = rec
    st["sim"] = sim
    st["states"] = log.states if log is not None else []
    return st


def add_mounts(veh, st: dict, out: dict) -> None:
    """With path_run's load states (K&C corners, recorded), the mount forces into a tool's output:
    the mount_* series (every SPARSE_EVERY_S and at each mount's largest force), the result
    mount_force_peak and the joint vectors at its instant."""
    if not st["states"]:
        return
    out["series"] += mount_series(veh, st["states"], [])
    top = mount_peak(out["series"])
    if top is not None:
        out["results"].append(top[0])
        out["vectors"] += mount_vectors(veh, state_at(st["states"], top[1]))


class LaneChangeTool(K2Tool):
    """Double lane change (S3; also S2 / S2b courses later) with a path-following driver.

    The driver is pure pursuit on the course centre line (preview 0.6 s, at least 3 m,
    steering rate 400 deg/s) and holds the speed. A wheel has lifted when its road normal
    force is zero.
    Results at `speed`: min_wheel_load (N, where), min_side_load (N, the lighter side's
    wheels together), wheel_lift (1), two_wheel_lift (1, both wheels of one side off at
    once), max_lateral_accel (m/s^2, body axis), max_lateral_accel_g (g, the same),
    max_roll (deg), max_path_error (m, CG from the centre line), max_steer (deg); and
    max_speed_no_lift (km/h, the highest speed with every wheel on the ground, to about
    0.6 km/h). With K&C corners the run at `speed` also gives the force the body receives at
    each suspension mount (add_mounts): the mount_* series, mount_force_peak (N, where
    '<support>.<mount> at t <s> s') and the joint vectors at that instant.
    """

    name = "lane_change"
    title = "ダブルレーンチェンジ (K2 + 運転のモデル)"
    domain = "handling"
    tests = ["S3"]
    # speed 40 km/h: the default when a load case has none (the catalog's load cases give it); a test value
    params = (P("speed", "km/h", 40.0, "held speed"),
              MU,
              P("offset", "m", DLC_OFFSET, "sideways shift of the lane (ISO 3888-1:2018 Table 1, checked 2026-11-03)"),
              P("direction", None, "left", "the first lane change goes toward", choices=("left", "right")),
              P("find_limit", None, True, "also search max_speed_no_lift"),
              P("v_min", "km/h", 10.0, "lowest speed of the search"),
              P("v_max", "km/h", 80.0, "highest speed of the search"),
              *MOTOR)
    pairs = (Pair("max_lateral_accel_g", "lane_change.peak_lateral_acceleration", "a_y_peak",
                  (("v", "speed"), ("Y", "offset")),
                  f"X = {DLC_SECTIONS[3] * 1000:g} mm, the shorter lane change of the course (back to the first "
                  "lane); K2 is the body's lateral acceleration with the driver model, which lags the path"),
             Pair("min_side_load", "lane_change.inner_wheel_load", "W_inner",
                  note="a_y from lane_change.peak_lateral_acceleration; K2 is the minimum over the run, with "
                       "the roll overshoot of the springs"))

    def own_checks(self) -> list[dict]:
        # rigid supports and stiff tyres: no body roll, so the wheels do not lean and the Magic Formula's
        # camber force and moment stay out; the pneumatic trail is in the closed form
        d = box_design(mass=500.0, cg_x=-0.9, cg_z=0.5, wheel_mass=20.0, susp="rigid",
                       variables={"tyre_stiffness": {"value": 10000.0, "unit": "N/mm"}}, name="steady cornering check")
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
        front = [c for c in veh.corners if c.steer]
        rear = [c for c in veh.corners if not c.steer]
        cf, cr = sum(c.c_lat for c in front), sum(c.c_lat for c in rear)
        x_cg = float(veh.cg_total[0])
        a, b = float(front[0].pos[0]) - x_cg, x_cg - float(rear[0].pos[0])
        tf, tr = front[0].mf.trail(front[0].static_load), rear[0].mf.trail(rear[0].static_load)
        big_l = a + b
        expected = delta / (big_l / v + veh.m_total * v / (big_l + tr - tf) * ((b + tr) / cf - (a - tf) / cr))
        deg = math.degrees(delta)
        out.append(_check(f"steady cornering {v:g} m/s, steer {deg:g} deg, rigid, cornering stiffness proportional to "
                          f"axle load, pneumatic trail {tf * 1000:.1f} mm front / {tr * 1000:.1f} mm rear",
                          "yaw_rate", expected, rates[0], "rad/s", 2e-2,
                          "bicycle model with the side forces acting the trail behind the axles: r = delta / (L / v "
                          "+ m v ((b + t_r) / C_f - (a - t_f) / C_r) / (L + t_r - t_f))",
                          case_ja=f"サスペンションの無い車が {v:g} m/s、ハンドル {deg:g}° で回り続けるときの向きの変わる速さ "
                                  f"(タイヤの曲がる硬さは車軸の荷重に比例。横の力が掛かる点は接地点の前輪 "
                                  f"{tf * 1000:.1f} mm・後輪 {tr * 1000:.1f} mm 後ろ)",
                          source_ja="前後 2 輪の自転車モデル (横の力は車軸の少し後ろに掛かる): "
                                    "r = δ / (L / v + m v ((b + t_r) / C_f - (a - t_f) / C_r) / (L + t_r - t_f))"))
        out.append(_check("steady cornering left + right (bit for bit)", "yaw_rate_mirror_error", 0.0, rates[0] + rates[1],
                          "rad/s", 0.0, "symmetry", passed=rates[0] == -rates[1],
                          case_ja="左に回るときと右に回るときの、向きの変わる速さの和",
                          source_ja="左右で 1 ビットも違わない (形が左右対称なので)"))
        return out

    def _lift(self, veh, speed, y_ref, x_end) -> bool:
        st = path_run(veh, speed, y_ref, x_end, dt=self.dt, motor=self._motor)
        return st["any_lift"] or not st["finished"]

    def _run(self, design: dict, lp: dict) -> dict:
        veh = self.vehicle(design, lp)
        notes = list(veh.assumptions) + ["double lane change course of ISO 3888-1:2018 Table 1 (15/30/25/25/15/15 m, "
                                         "offset 3.5 m; checked against the public sample on 2026-11-03); a centre "
                                         "line, not cone gates"]
        if not any(c.steer for c in veh.corners):
            return {"error": "no support has actuation.steer; a lane change needs a steered wheel"}
        speed = self.q(lp, "speed", "m/s")
        sign = -1.0 if self.text(lp, "direction") == "right" else 1.0
        y0, x_end = dlc_path(offset=self.q(lp, "offset", "m"))
        y_ref = (lambda x: sign * y0(x))
        # the held speed's drive is the motor's at most (a speed the motor cannot hold is not held)
        try:
            self._motor = self.motor(lp)
        except DesignError:
            self._motor = None
            notes.append("no motor given (motor_torque, gear_ratio): the speed controller's drive has no limit")
        st = path_run(veh, speed, y_ref, x_end, dt=self.dt, rec_hz=self.record_hz, motor=self._motor)
        cid, mn = _min_load(st["min_load"])
        light = min(st["min_side"], key=lambda k: st["min_side"][k])
        res = [_res("min_wheel_load", mn, "N", cid), _res("min_side_load", st["min_side"][light], "N", light),
               _res("wheel_lift", 1.0 if st["any_lift"] else 0.0, "1"),
               _res("two_wheel_lift", 1.0 if st["two_lift_time"] > 0 else 0.0, "1"),
               _res("max_lateral_accel", st["max_ay"], "m/s^2"), _res("max_lateral_accel_g", st["max_ay"] / G0, "g"),
               _res("max_roll", math.degrees(st["max_roll"]), "deg"),
               _res("max_path_error", st["max_err"], "m"), _res("max_steer", math.degrees(st["max_steer"]), "deg"),
               _res("tipped", 1.0 if st["tipped"] else 0.0, "1")]
        if st["tipped"]:
            notes.append("the vehicle tipped over; the run stops there (K2 has no body-ground contact)")
        if self.flag(lp, "find_limit"):
            lo, hi = self.q(lp, "v_min", "m/s"), self.q(lp, "v_max", "m/s")
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
        scene = dlc_scene(2.0 * _half_width(veh), sign, offset=self.q(lp, "offset", "m"))
        out = {"results": res, "series": rec.series(), "vectors": rec.vectors(-1), "scene": scene,
               "assumptions": notes + ["the cones and gates are drawn around the centre line; the driver model "
                                       "follows the line and the gates are not judged"]}
        add_mounts(veh, st, out)
        # each side's least load (S3 judges the lighter of the two: both sides are judged places)
        out["places"] = [{"where": side, "quantity": "min_side_load", "value": st["min_side"][side], "unit": "N",
                          "judged": True} for side in ("left", "right") if math.isfinite(st["min_side"][side])]
        return out
