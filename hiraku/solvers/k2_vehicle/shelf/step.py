"""Step climb driven in K2 (step_climb_dynamic; P1, feeds R1)."""

from __future__ import annotations

import math

import numpy as np

from .. import kinematics
from ..checks import _check
from ..drive import SpeedController, controls, start_rolling
from ..model import DesignError, box_design, build_vehicle
from ..record import BodyLog, Recorder
from ..road import step as step_road
from ..scenario import new_sim
from ..units import G0
from .base import MOTOR, MU, K2Tool, P, Pair, _box, _half_width, _length, _res


def step_push(veh, h: float, stepping: list[int], *, dt: float = 1e-3, y_range=None, ramp_s: float = 4.0,
              run_up: float = 2.0, rec_hz: float = 0.0, after_s: float = 0.0, crawl: float | None = None,
              log: bool = False) -> dict:
    """Quasi-static push: the stepping wheels stand 0.5 mm before the step corner and the
    drive torque grows slowly from zero until they leave the low ground.

    The forces are taken when the stepping wheel's road force is steepest (its ground
    contact has just unloaded), which is the lift-off of the rigid-wheel balance.
    run_up: the step for the dynamic run is placed this far further (m).
    rec_hz > 0 records the push, and the body's loads (Sim.loads) every 10 ms in "states".
    After the lift-off: with crawl (m/s) the speed controller holds that speed until every
    wheel the step covers has its centre past the step's corner ("crossed": the time of each),
    then after_s more (at most CRAWL_MAX_S; "stalled" when the body stands still for 2 s);
    without crawl, after_s with the torque of the lift-off held. log: every step in a
    record.BodyLog ("body_log", for replaying the sprung body elsewhere).
    """
    xs = max(float(veh.corners[i].pos[0]) for i in stepping)
    r = max(veh.corners[i].radius for i in stepping)
    hh = min(h, r * 0.999)
    d = math.sqrt(2.0 * r * hh - hh * hh)
    x_step = xs + d + 0.0005
    road = step_road(x_step, h, y_range)
    sim = new_sim(veh, road=road, dt=dt, settle_s=1.0)
    static = list(sim.out["ntot"])
    driven = [i for i, c in enumerate(veh.corners) if c.drive]
    if not driven:
        raise DesignError("no support has actuation.drive; a driven wheel is needed to climb")
    w = veh.m_total * G0
    t_need = w * max(d / max(r - hh, 1e-3), 0.2) * r
    rate = t_need / ramp_s
    # lifted: the centre is 10 mm above its free radius over the low ground (the tyre
    # alone rises a few mm as its ground contact unloads, before the real lift-off)
    z_free = [veh.corners[i].radius + 0.01 for i in stepping]
    rec = Recorder(sim, rec_hz) if rec_hz > 0 else None
    body = BodyLog(sim) if log else None
    states: list[dict] = []
    every = max(1, int(round(LOAD_EVERY_S / dt)))
    best, snap, lifted, slip = -1.0, None, False, False

    def push(total: float, drive: list[float] | None = None, brake: list[float] | None = None) -> None:
        if drive is None:
            drive = [0.0] * sim.n
            for i in driven:
                drive[i] = total / len(driven)
        sim.step(controls(sim, 0.0, drive, brake or [0.0] * sim.n))
        if body is not None:
            body.tick()
        if rec is not None:
            rec.tick({"wheel_torque": ("N*m", total)})
            if rec.k % every == 1 or every == 1:
                states.append(load_state(sim))

    while sim.t < 6.0 * ramp_s:
        total = rate * sim.t
        push(total)
        o = sim.out
        for i in stepping:
            fx, _, fz = o["f"][i]
            if fz > 0.0 and -fx / fz > best:
                best = -fx / fz
                snap = {"f": [tuple(v) for v in o["f"]], "p": [tuple(v) for v in o["p"]], "n": list(o["ntot"]),
                        "fn": [(o["n"][k][0] * o["ntot"][k], o["n"][k][1] * o["ntot"][k], o["n"][k][2] * o["ntot"][k])
                               for k in range(sim.n)],
                        "acc": tuple(o["acc"]), "cg": sim.cg_world().tolist(), "t": sim.t, "torque": total,
                        "g": list(sim.g), "loads": load_state(sim)}
                if rec is not None:
                    rec.snap = []
                    rec.keep_snapshot()
        wc = sim.wheel_centres()
        if all(wc[i][2] > z_free[k] for k, i in enumerate(stepping)):
            lifted = True
            break
        if any(abs(sim.om[i]) * veh.corners[i].radius > 0.5 for i in driven) and sim.forward_speed() < 0.05:
            slip = True
            break
    crossed: dict[int, float] = {}
    stalled = False
    if lifted and crawl is not None:
        covered = [i for i, c in enumerate(veh.corners)
                   if y_range is None or y_range[0] <= float(c.pos[1]) <= y_range[1]]
        ctrl = SpeedController(sim)
        t_max, t_end, still = sim.t + CRAWL_MAX_S, math.inf, 0.0
        while sim.t < min(t_max, t_end):
            drive, brake = ctrl.torques(sim, crawl)
            push(sum(drive), drive, brake)
            wc = sim.wheel_centres()
            for i in covered:
                if i not in crossed and wc[i][0] >= x_step:
                    crossed[i] = sim.t
            if len(crossed) == len(covered) and t_end == math.inf:
                t_end = sim.t + after_s
            still = still + sim.dt if sim.forward_speed() < 0.05 else 0.0
            if still > 2.0:
                stalled = True
                break
    elif lifted and after_s > 0.0:
        held = rate * sim.t
        t_end = sim.t + after_s
        while sim.t < t_end:
            push(held)
    return {"ratio": best, "snap": snap, "static": static, "lifted": lifted, "slip": slip, "x_corner": x_step,
            "x_step": x_step + run_up, "road": road, "recorder": rec, "states": states, "crossed": crossed,
            "stalled": stalled, "body_log": None if body is None else body.record()}


def step_body_log(design: dict, step_height_mm: float, step_supports: list[str] | None = None, *,
                  dt: float = 1e-3, after_s: float = 1.5) -> dict:
    """The quasi-static push of step_climb_dynamic (run 1, the design's defaults; the stepping
    supports are the front-most unless given) with every step logged: record.BodyLog.record()."""
    veh = build_vehicle(design)
    ids = veh.ids()
    if step_supports:
        stepping = [ids.index(x) for x in step_supports]
    else:
        xmax = max(float(c.pos[0]) for c in veh.corners)
        stepping = [i for i, c in enumerate(veh.corners) if abs(float(c.pos[0]) - xmax) < 1e-9]
    return step_push(veh, step_height_mm / 1000.0, stepping, dt=dt, after_s=after_s, log=True)["body_log"]


LOAD_EVERY_S = 0.01  # s between the body's loads kept during the push
DENSE_BEFORE_S, DENSE_AFTER_S, DENSE_MAX = 0.2, 0.2, 30  # around lift-off to the wheels on the step
CLIMB_BEFORE_S = 0.6  # a following wheel's climb: from this long before its centre passes the corner
SPARSE_EVERY_S = 0.25
CRAWL_SPEED = 0.5  # m/s after the lift-off (K2's choice: slow, so the record stays nearly quasi-static)
CRAWL_MAX_S = 20.0


def load_state(sim) -> dict:
    """Sim.loads of the last step with its time and the world position of the design origin."""
    out = sim.loads()
    out["t"] = sim.t
    out["origin"] = out["cg"] - out["rot"] @ np.asarray(sim.veh.cg_s, dtype=float)
    return out


def corner_mounts(veh, st: dict, i: int) -> dict | None:
    """The forces corner i's double wishbone puts on the body at its mounts in the load state st:
    kinematics.corner_mount_forces of the knuckle's load (force at the wheel centre and moment,
    Sim.loads) at its travel. {mount: (point, force)}, world axes, m and N; None for a corner
    off a K&C table or without damper points."""
    c = veh.corners[i]
    if c.table is None or "damper_lower" not in c.table["hardpoints_mm"]:
        return None
    rot, origin, ld = st["rot"], st["origin"], st["corners"][i]
    got = kinematics.corner_mount_forces(c.table, c.side, ld["travel"], rot.T @ ld["force"],
                                         rot.T @ (ld["point"] - origin), rot.T @ ld["moment"])
    return {m: (origin + rot @ p, rot @ f) for m, (p, f) in got.items()}


def mount_vectors(veh, snap: dict) -> list:
    """The forces each K&C corner puts on the body at its mounts at the snapshot (corner_mounts),
    as joint vectors at '<support>.<hardpoint key>': the mount's point and the force the body
    receives, world axes. A corner whose hardpoints have no damper points gives none."""
    out = []
    for i, c in enumerate(veh.corners):
        got = corner_mounts(veh, snap["loads"], i)
        for mount, (point, force) in (got or {}).items():
            out.append({"kind": "joint", "at": f"{c.id}.{mount}", "origin_mm": [float(x) * 1000.0 for x in point],
                        "vector_N": [float(x) for x in force]})
    return out


def climb_windows(t_lift: float, crossed: dict[int, float], stepping: list[int]) -> list[tuple[float, float]]:
    """The spans to sample closely: from DENSE_BEFORE_S before the lift-off to DENSE_AFTER_S after
    the stepping wheels' centres pass the step's corner (0.6 s after the lift-off when they did
    not), and each following wheel's climb, CLIMB_BEFORE_S before to DENSE_AFTER_S after its
    centre passes the corner."""
    on = [crossed[i] for i in stepping if i in crossed]
    t_on = max(on) if len(on) == len(stepping) else t_lift + 0.6
    out = [(t_lift - DENSE_BEFORE_S, t_on + DENSE_AFTER_S)]
    out += [(t - CLIMB_BEFORE_S, t + DENSE_AFTER_S) for i, t in sorted(crossed.items()) if i not in stepping]
    return out


def mount_times(ts: list[float], windows: list[tuple[float, float]]) -> list[int]:
    """Indices into the load states' times ts: every SPARSE_EVERY_S, and at most DENSE_MAX evenly
    in each window (lo, hi)."""
    keep: set[int] = set()
    for lo, hi in windows:
        dense = [k for k, t in enumerate(ts) if lo <= t <= hi]
        stride = max(1, math.ceil(len(dense) / DENSE_MAX))
        keep.update(dense[::stride])
        if dense:
            keep.add(dense[-1])
    sparse = max(1, int(round(SPARSE_EVERY_S / LOAD_EVERY_S)))
    keep.update(k for k in range(0, len(ts), sparse))
    keep.add(len(ts) - 1)
    return sorted(keep)


def mount_series(veh, states: list[dict], windows: list[tuple[float, float]]) -> list:
    """Time series of the body's loads at mount_times (windows: climb_windows): per K&C corner and mount
    mount_x/y/z.<support>.<mount> (N, the force the body receives, world axes); per corner whose
    drive or brake reacts on the body body_torque_x/y/z.<support> (N*m, world); and the sprung
    body's sprung_cg_x/y/z (m), sprung_accel_x/y/z (m/s^2), sprung_ang_vel_x/y/z (rad/s),
    sprung_roll/pitch/yaw (deg, as roll, pitch, yaw), inertia_force_x/y/z (N, -m_s a, at the
    sprung CG) and inertia_moment_x/y/z (N*m, about the sprung CG). With the sprung weight they
    balance: sum of the mount forces + weight + inertia force = 0, and the same for the moments
    about the sprung CG with the body torques and the inertia moment."""
    from ..record import _round
    from ..sim import euler_from_r
    if not states:
        return []
    idx = mount_times([st["t"] for st in states], windows)
    ts = [_round(states[k]["t"]) for k in idx]
    ch: dict[str, tuple[str, list]] = {}

    def put(name: str, unit: str, value: float) -> None:
        ch.setdefault(name, (unit, []))[1].append(_round(float(value)))

    for k in idx:
        st = states[k]
        for i, c in enumerate(veh.corners):
            got = corner_mounts(veh, st, i)
            for mount, (_, f) in (got or {}).items():
                for a, v in zip("xyz", f):
                    put(f"mount_{a}.{c.id}.{mount}", "N", v)
            for a, v in zip("xyz", st["corners"][i]["body_torque"]):
                put(f"body_torque_{a}.{c.id}", "N*m", v)
        for name, unit, vec in (("sprung_cg", "m", st["cg"]), ("sprung_accel", "m/s^2", st["acc"]),
                                ("sprung_ang_vel", "rad/s", st["omega"]),
                                ("inertia_force", "N", st["inertia_force"]),
                                ("inertia_moment", "N*m", st["inertia_moment"])):
            for a, v in zip("xyz", vec):
                put(f"{name}_{a}", unit, v)
        for name, v in zip(("roll", "pitch", "yaw"), euler_from_r(st["rot"])):
            put(f"sprung_{name}", "deg", math.degrees(v))
    return [{"channel": name, "unit": unit, "t_s": ts, "values": vals} for name, (unit, vals) in ch.items()
            if not (name.startswith("body_torque_") and not any(vals))]


class StepClimbTool(K2Tool):
    """Step climb driven in K2 (step_climb_dynamic; P1, feeds R1). The analysis
    department's step_climb is the hand-calculation balance -> K1; this one drives over
    the step and is the moving picture and the second opinion.

    Two runs. (1) Quasi-static: the stepping wheels stand against the step corner and the
    drive torque grows slowly until they leave the low ground; the forces at that moment
    compare with the K0 rigid-wheel balance. (2) The test speed with the motor's torque
    (unlimited when the motor is not given): does it climb, and the peak wheel forces.
    Results (named front_* when the stepping wheels are the front ones, step_* otherwise):
    *_wheel_force (N, the push |F_x| at lift-off), *_wheel_load (N, its vertical force),
    *_wheel_load_static (N), force_ratio (1, F_x / F_z of that wheel), push_traction (N, the
    driven wheels' forward force), required_mu_push (1), lifted_by_push (1: 0 when the
    push of run 1 did not lift the stepping wheels, e.g. the driven wheels spun first; the
    *_wheel_* values are then those of the steepest push reached, not of a lift-off),
    climbed (1, run 2; where says
    "grip" or "torque" when it did not), climbable (1: run 1 or run 2 got the stepping wheels
    onto the step), peak_wheel_force (N, run 2, where),
    peak_vertical_wheel_force (N, run 2), climb_time (s, run 2).
    series, vectors and scene all come from run 1: the series run from the standstill
    against the step through the lift-off, then at a crawl (CRAWL_SPEED, the speed
    controller) until every wheel the step covers has crossed it and 0.5 s more, the
    vectors are that run at lift-off (each wheel's reaction and grip, and the inertia
    force -(road forces + weight) at the CG, so the set balances the weight), and the
    scene's step stands where that run's step is. Run 2 gives numbers only.
    With K&C corners, run 1 also gives the force the body receives at each suspension mount
    (joint vectors at lift-off, and the mount_* series of mount_series: tens of instants from
    the standstill against the step until the end of the record, closer around each climb,
    climb_windows), with the sprung body's motion and d'Alembert force and moment that
    balance them.
    """

    name = "step_climb_dynamic"
    title = "段差越え (K2 で段差を走らせる)"
    domain = "powertrain"
    tests = ["P1", "R1"]
    params = (P("step_height", "mm", None, "height of the step", required=True),
              P("speed", "km/h", 10.0, "speed of run 2"),
              P("step_supports", None, None, "comma-separated support ids that meet the step "
                                             "(default: the front-most supports)"),
              MU, *MOTOR)
    pairs = (Pair("front_wheel_force", "step_climb.pushed_wheel_load", "F", (("h", "step_height"),)),
             Pair("front_wheel_force", "step_climb.wheel_force_rigid", "F", (("h", "step_height"),),
                  "W = a front wheel's load; with the static load it comes out high, since the push moves "
                  "load to the rear (step_climb.pushed_wheel_load)"),
             Pair("front_wheel_load", "step_climb.pushed_wheel_load", "W", (("h", "step_height"),)),
             Pair("front_wheel_load", "mass_props.front_wheel_load", "W",
                  note="static: high, the push moves load to the rear (step_climb.pushed_wheel_load)"),
             Pair("front_wheel_load_static", "mass_props.front_wheel_load", "W"),
             Pair("force_ratio", "step_climb.self_climb", "mu", (("h", "step_height"),),
                  "both are d / (r - h) for a rigid wheel: F_x / F_z at lift-off"))

    def own_checks(self) -> list[dict]:
        d = box_design(mass=500.0, cg_x=-1.0, cg_z=0.5, wheelbase=2.0, susp="rigid", radius=0.3,
                       variables={"tyre_stiffness": {"value": 10000.0, "unit": "N/mm"},
                                  "tire_mu": {"value": 1.5, "unit": "1"},
                                  "rolling_resistance": {"value": 0.0, "unit": "1"}}, name="step check, rigid")
        veh = build_vehicle(d)
        front = [i for i, c in enumerate(veh.corners) if c.steer]
        r = step_push(veh, 0.10, front)
        dd = math.sqrt(2 * 0.3 * 0.1 - 0.1 ** 2)
        k = dd / (0.3 - 0.1)
        out = [_check("stiff wheel r = 300 mm on a 100 mm step", "force_ratio", k, r["ratio"], "1", 2e-2,
                      "rigid wheel on a step corner: d = sqrt(2 r h - h^2), F/W = d / (r - h)")]
        snap = r["snap"]
        wf = sum(snap["f"][i][2] for i in front)
        w = veh.m_total * G0
        a = 1.0
        expected = w * (2.0 - a) / 2.0 / (1.0 + k * 0.3 / 2.0)
        out.append(_check("both front wheels on a 100 mm step, pushed by the rear: front axle load at lift-off",
                          "front_axle_load", expected, wf, "N", 3e-2,
                          "moment balance with the push at axle height: W_f = m g (L - a) / L / (1 + k r / L)"))
        return out

    def _run(self, design: dict, lp: dict) -> dict:
        veh = self.vehicle(design, lp)
        notes = list(veh.assumptions)
        h = self.q(lp, "step_height", "m")
        if h is None or h <= 0:
            return {"error": "step_climb_dynamic needs a positive step_height (a length) in the load case params"}
        ids = veh.ids()
        if self.text(lp, "step_supports") is not None:
            want = [x.strip() for x in str(lp["step_supports"]).split(",") if x.strip()]
            bad = [x for x in want if x not in ids]
            if bad:
                return {"error": f"step_supports {', '.join(bad)} are not supports of the design"}
            stepping = [ids.index(x) for x in want]
        else:
            xmax = max(float(c.pos[0]) for c in veh.corners)
            stepping = [i for i, c in enumerate(veh.corners) if abs(float(c.pos[0]) - xmax) < 1e-9]
        sides = {veh.corners[i].side for i in stepping}
        y_range = None
        if sides == {1.0}:
            y_range = (0.0, math.inf)
        elif sides == {-1.0}:
            y_range = (-math.inf, 0.0)
        xmax = max(float(c.pos[0]) for c in veh.corners)
        tag = "front" if all(abs(float(veh.corners[i].pos[0]) - xmax) < 1e-9 for i in stepping) else "step"

        crawl = step_push(veh, h, stepping, dt=self.dt, y_range=y_range, rec_hz=self.record_hz, after_s=0.5,
                          crawl=CRAWL_SPEED)
        res = []
        vectors = []
        snap = crawl["snap"]
        if snap is not None:
            lead = max(stepping, key=lambda i: abs(snap["f"][i][0]))
            lid = ids[lead]
            fx, fy, fz = snap["f"][lead]
            driven = [i for i, c in enumerate(veh.corners) if c.drive and i not in stepping]
            push = sum(snap["f"][i][0] for i in driven)
            nd = sum(snap["n"][i] for i in driven)
            res += [_res(f"{tag}_wheel_force", abs(fx), "N", lid), _res(f"{tag}_wheel_load", fz, "N", lid),
                    _res(f"{tag}_wheel_load_static", crawl["static"][lead], "N", lid),
                    _res("force_ratio", crawl["ratio"], "1", lid), _res("push_traction", push, "N")]
            if nd > 0:
                res.append(_res("required_mu_push", push / nd, "1"))
            res.append(_res("lifted_by_push", 1.0 if crawl["lifted"] else 0.0, "1",
                            None if crawl["lifted"] else ("grip" if crawl["slip"] else "torque")))
            for i, c in enumerate(veh.corners):
                p = [x * 1000.0 for x in snap["p"][i]]
                fn = snap["fn"][i]
                g = [snap["f"][i][k] - fn[k] for k in range(3)]
                vectors.append({"kind": "reaction", "at": c.id, "origin_mm": p, "vector_N": [float(x) for x in fn]})
                vectors.append({"kind": "grip", "at": c.id, "origin_mm": p, "vector_N": [float(x) for x in g]})
            # d'Alembert force of the whole vehicle: minus its rate of momentum, which in the
            # model equals the road forces plus the weight, so the set balances the weight exactly
            m = veh.m_total
            inertia = [-(sum(snap["f"][i][k] for i in range(len(snap["f"]))) + m * snap["g"][k]) for k in range(3)]
            vectors.append({"kind": "inertia", "at": "cg", "origin_mm": [x * 1000.0 for x in snap["cg"]],
                            "vector_N": [float(x) for x in inertia]})
            vectors += mount_vectors(veh, snap)
        if not crawl["lifted"]:
            notes.append("pushing with a growing torque did not lift the stepping wheels"
                         + (" (the driven wheels spun: grip)" if crawl["slip"] else ""))
        elif crawl["stalled"]:
            notes.append(f"after the lift-off the crawl at {CRAWL_SPEED} m/s stopped before every wheel the step covers "
                         "had crossed it (the record ends there)")

        # run 2: the test speed with the motor's torque
        speed = self.q(lp, "speed", "m/s")
        try:
            motor = self.motor(lp)
        except DesignError:
            motor = None
            notes.append("motor not given: the driven wheels get the torque the speed controller asks for (no limit)")
        road = step_road(crawl["x_step"], h, y_range)
        sim = new_sim(veh, road=road, dt=self.dt, settle_s=1.0)
        start_rolling(sim, speed)
        ctrl = SpeedController(sim)
        driven = [i for i, c in enumerate(veh.corners) if c.drive]
        peak, peak_id, peak_z, slow_t, t_climb = 0.0, None, 0.0, 0.0, None
        reason = None
        # climbed: the hindmost wheel centre (world x) is 0.6 m past the step's corner
        x_done = crawl["x_step"] + 0.6
        while sim.t < 30.0:
            drive, brake = ctrl.torques(sim, speed)
            if motor is not None:
                wbar = sum(sim.om[i] for i in driven) / len(driven)
                cap = motor.wheel_torque_total(wbar) / len(driven)
                drive = [min(x, cap) for x in drive]
            sim.step(controls(sim, 0.0, drive, brake))
            o = sim.out
            for i, c in enumerate(veh.corners):
                fmag = math.sqrt(sum(x * x for x in o["f"][i]))
                if fmag > peak:
                    peak, peak_id = fmag, c.id
                peak_z = max(peak_z, o["f"][i][2])
            if t_climb is None and float(sim.wheel_centres()[:, 0].min()) > x_done:
                t_climb = sim.t
            if t_climb is not None and sim.t > t_climb + 0.8:
                break
            slow_t = slow_t + sim.dt if sim.forward_speed() < 0.05 else 0.0
            if slow_t > 2.0:
                slipping = any(abs(sim.om[i] * veh.corners[i].radius - sim.forward_speed()) > 0.2 for i in driven)
                reason = "grip" if slipping or motor is None else "torque"
                break
        # climbable: either run gets the stepping wheels onto the step (K0's step_climb.climb_limit)
        res += [_res("climbed", 1.0 if t_climb is not None else 0.0, "1", reason),
                _res("climbable", 1.0 if t_climb is not None or crawl["lifted"] else 0.0, "1"),
                _res("peak_wheel_force", peak, "N", peak_id), _res("peak_vertical_wheel_force", peak_z, "N")]
        if t_climb is not None:
            res.append(_res("climb_time", t_climb, "s"))
        rec = crawl["recorder"]
        y0, y1 = (-_half_width(veh) - 0.5, _half_width(veh) + 0.5) if y_range is None else \
            ((0.0, _half_width(veh) + 0.5) if y_range[0] >= 0.0 else (-_half_width(veh) - 0.5, 0.0))
        x0 = crawl["x_corner"]
        scene = [_box("step", f"段差 {h * 1000:.0f} mm (1 回目の押し上げの位置)",
                      (x0, y0, 0.0), (x0 + _length(veh) + 3.0, y1, h))]
        series = rec.series()
        if crawl["states"] and snap is not None:
            series += mount_series(veh, crawl["states"], climb_windows(snap["t"], crawl["crossed"], stepping))
        return {"results": res, "series": series, "vectors": vectors or rec.vectors(-1), "scene": scene,
                "assumptions": notes}
