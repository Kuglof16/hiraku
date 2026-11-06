"""Rollover: a test driven past its limit with the sprung body touching the ground (contact.py).

K2's other tools stop a run when the body has rolled past TIPPED_ROLL: the wheels are their only
contact with the road. Here the body has contact points too (the roll bar's members, the corners of the
body's hull, rings on the wheels' outer faces; each a penalty spring and damper with Coulomb friction),
so the run goes on through the fall until the body lies at rest or after_tip_s past the tip. Nothing is
posed by hand: the vehicle starts upright on its wheels and only the test's own input moves it.

Two tools, the same watch of the run (rollover_tilt is in TOOLS, planned_only: only the plan of S1's drive
record names it; rollover_lane_change is not in TOOLS):
- rollover_tilt (S1): the tilt table turned as the tilt tool turns it (scenario.tilt_sweep, the tyres on
  the table's high friction) until the upper wheels have lost their load, then turned on to
  hold_past degrees past that angle and held there while the vehicle falls onto the table.
- rollover_lane_change (S3): the double lane change at a held speed, with the speed control's drive
  capped at the design's motor; with search_tip the speed is searched upward until the body goes over
  its side. Once the body has turned past TIPPED_ROLL the driver lets go (steer held, no drive, no
  brake: HIRAKU's choice).

A tip is the body's up axis turned past TIPPED_ROLL from the road normal; over its side (a rollover) when
the body's y axis carries more of that turn than its x axis, else over its end (pitch_over).
"""

from __future__ import annotations

import math

import numpy as np

from ..checks import _check
from ..contact import (C_CONTACT, K_CONTACT, POINT_SPACING, V_EPS, BodyContact, ContactPoint, design_points,
                       wheel_rim_points)
from ..drive import PathDriver, SpeedController, controls, start_rolling
from ..model import DesignError, box_design, build_vehicle
from ..record import Recorder, _round
from ..road import flat, table_with_floor
from ..scenario import hold, new_sim, side_gravity, tilt_sweep
from ..sim import Sim, quat_from_euler
from ..units import G0
from .base import MOTOR, MU, TIPPED_ROLL, K2Tool, P, Pair, _box, _half_width, _res, _side_ids
from .lane_change import DLC_OFFSET, dlc_path, dlc_scene

REST_SPEED = 0.05     # m/s: the body is at rest below this speed ...
REST_RATE = 0.05      # rad/s ... and this turn rate ...
REST_HOLD = 0.5       # s ... held this long (HIRAKU's choices)
RIM_EVERY = 10        # steps between the wheel-rim checks (after the tip)
# the tilt table's low edge (its hinge on the level floor) this far outside the wheels' half width: the
# table the tilt tool draws (shelf/tilt.py, half width + 0.5 m). HIRAKU's choice, no source
TABLE_MARGIN = 0.5


def _occupant_parts(design: dict) -> list[dict]:
    return [p for p in design.get("parts") or [] if p.get("kind") in ("occupant", "cargo") and "box" in p]


class ContactRecorder(Recorder):
    """A Recorder whose samples also carry the body contact's channels (contact_channels)."""

    def __init__(self, sim, hz, con: BodyContact, design: dict):
        super().__init__(sim, hz)
        self.con = con
        self.occ = _occupant_parts(design)
        cg_s = np.array(sim.veh.cg_s, dtype=float)
        self.occ_b = [np.asarray(p["box"]["center_mm"], dtype=float) / 1000.0 - cg_s for p in self.occ]
        self.touch_unforced = 0  # samples at which a point touched outside a contact run (the tilt's turn)

    def sample(self, extra: dict | None = None):
        if not self.sim.out:
            return
        super().sample(dict(extra or {}, **self.contact_channels()))

    def contact_channels(self) -> dict:
        """Per contact point its force (N, world), touch (0 / 1) and position (m, world) at the present state
        (con.ext() is evaluated here), per occupant or cargo box its centre (m, world)."""
        sim, con = self.sim, self.con
        if con.ext():
            self.touch_unforced += 0 if getattr(con, "live", False) else 1
        out = {}
        for i, p in enumerate(con.points):
            f, q = con.force[i], con.pos[i]
            for k, ax in enumerate("xyz"):
                out[f"ground_force_{ax}.{p.name}"] = ("N", f[k])
            out[f"ground_touch.{p.name}"] = ("1", 1.0 if con.touch[i] else 0.0)
            for k, ax in enumerate("xyz"):
                out[f"ground_point_{ax}.{p.name}"] = ("m", q[k])
        r = sim.rotation()
        pc = np.array(sim.p, dtype=float)
        for part, b in zip(self.occ, self.occ_b):
            w = pc + r @ b
            for k, ax in enumerate("xyz"):
                out[f"occupant_{ax}.{part['id']}"] = ("m", w[k])
        return out

    def all_series(self) -> list[dict]:
        """series() with the occupant channels' part_id, without the channels of the contact points that
        touched at no sample (their force is 0 throughout)."""
        out = self.series()
        never = {s["channel"].split(".", 1)[1] for s in out
                 if s["channel"].startswith("ground_touch.") and not any(s["values"])}
        out = [s for s in out if not (s["channel"].startswith("ground_") and s["channel"].split(".", 1)[1] in never)]
        ids = {p["id"] for p in self.occ}
        for s in out:
            kind, _, pid = s["channel"].partition(".")
            if kind.startswith("occupant_") and pid in ids:
                s["part_id"] = pid
        return out


class Watch:
    """The numbers of a contact run, from every step (not the recorded samples). Call step() after each
    sim.step(ctl, ext) whose ext came from con.ext() just before it."""

    def __init__(self, sim, con: BodyContact, up=(0.0, 0.0, 1.0)):
        self.sim, self.con = sim, con
        self.up_w = tuple(float(x) for x in up)  # the world's up in the road frame (on a table: not its normal)
        pts = con.points
        self.rb = [i for i, p in enumerate(pts) if p.group == "rollbar"]
        self.people = [i for i, p in enumerate(pts) if p.group in ("occupant", "cargo")]
        self.left, self.right = _side_ids(sim, True), _side_ids(sim, False)
        self.st = {"t_lift": None, "lift_side": None, "t_rollbar": None, "t_tip": None, "tip_kind": None,
                   "speed_at_tip": None, "rb_peak": 0.0, "rb_peak_at": "", "rb_total_peak": 0.0, "rb_total_t": None,
                   "pt_peak": 0.0, "pt_peak_at": "", "acc_peak": 0.0, "acc_peak_t": None, "people_touch": "",
                   "rim_min": math.inf, "rim_min_at": "", "rest": False, "depth": 0.0, "depth_at": "",
                   "roll_turned": 0.0, "max_roll": 0.0, "capped_s": 0.0, "snap": []}
        self.roll_prev = sim.attitude()[0]
        self.roll_unwrapped = self.roll_prev
        self.calm = 0.0
        self.steps = 0
        self.tipped = False
        self.new_peak = False

    def step(self):
        sim, con, st = self.sim, self.con, self.st
        pts = con.points
        dt = sim.dt
        t = sim.t
        t0 = t - dt  # the contact forces are the ones of the state the step started from
        self.steps += 1
        nt = sim.out["ntot"]
        if st["t_lift"] is None:
            for side, ids in (("left", self.left), ("right", self.right)):
                if ids and all(nt[i] <= 0.0 for i in ids):
                    st["t_lift"], st["lift_side"] = t, side
                    break
        tot = 0.0
        for i in self.rb:
            f = con.force[i]
            fm = math.sqrt(f[0] * f[0] + f[1] * f[1] + f[2] * f[2])
            tot += fm
            if fm > 0.0 and st["t_rollbar"] is None:
                st["t_rollbar"] = t0
            if fm > st["rb_peak"]:
                st["rb_peak"], st["rb_peak_at"] = fm, f"{pts[i].name} at t {t0:.3f} s"
        for i, f in enumerate(con.force):
            fm = math.sqrt(f[0] * f[0] + f[1] * f[1] + f[2] * f[2])
            if fm > st["pt_peak"]:
                st["pt_peak"], st["pt_peak_at"] = fm, f"{pts[i].name} ({pts[i].group}) at t {t0:.3f} s"
        for i in self.people:
            if con.force[i][2] > 0.0 and not st["people_touch"]:
                st["people_touch"] = f"{pts[i].part_id} at t {t0:.3f} s"
        for i, d in enumerate(con.depth):
            if d > st["depth"]:
                st["depth"], st["depth_at"] = d, f"{pts[i].name} ({pts[i].group}) at t {t0:.3f} s"
        a = sim.out["acc"]
        am = math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])
        if am > st["acc_peak"]:
            st["acc_peak"], st["acc_peak_t"] = am, t
        roll = sim.attitude()[0]
        st["max_roll"] = max(st["max_roll"], abs(roll))
        dr = roll - self.roll_prev
        dr -= 2.0 * math.pi * round(dr / (2.0 * math.pi))
        self.roll_unwrapped += dr
        self.roll_prev = roll
        st["roll_turned"] = max(st["roll_turned"], abs(self.roll_unwrapped))
        self.new_peak = tot > st["rb_total_peak"]
        if self.new_peak:
            st["rb_total_peak"], st["rb_total_t"] = tot, t0
            st["snap"] = [(pts[i].name, con.pos[i], con.force[i]) for i in range(len(pts)) if con.force[i][2] > 0.0]
        rm = sim.rotation()
        if self.tipped and self.steps % RIM_EVERY == 0:
            for cid, q in wheel_rim_points(sim):
                h = float(q[2]) - sim.road.height(float(q[0]), float(q[1]))
                if h < st["rim_min"]:
                    st["rim_min"], st["rim_min_at"] = h, f"{cid} at t {t:.3f} s"
        up = self._up(rm)
        if not self.tipped and math.acos(max(-1.0, min(1.0, up[2]))) > TIPPED_ROLL:
            self.tipped = True
            st["t_tip"] = t
            st["tip_kind"] = "roll" if abs(up[1]) >= abs(up[0]) else "pitch"
            st["speed_at_tip"] = sim.speed()
        if self.tipped:
            moving = sim.speed() > REST_SPEED or math.sqrt(sum(w * w for w in sim.w)) > REST_RATE
            self.calm = 0.0 if moving else self.calm + dt
            if self.calm >= REST_HOLD and not st["rest"]:
                st["rest"] = True

    def _up(self, rm) -> tuple:
        """The world's up in body axes."""
        ux, uy, uz = self.up_w
        return tuple(float(rm[0, a] * ux + rm[1, a] * uy + rm[2, a] * uz) for a in range(3))

    def finish(self) -> dict:
        sim, st = self.sim, self.st
        st["tipped"] = self.tipped
        st["rest_roll"] = sim.attitude()[0]
        st["rest_tilt"] = math.acos(max(-1.0, min(1.0, self._up(sim.rotation())[2])))
        st["t_end"] = sim.t
        return st


def lane_change_run(veh, design: dict, speed: float, y_ref, x_end: float, *, mu: float, k: float = K_CONTACT,
                    c: float = C_CONTACT, dt: float = 1e-3, rec_hz: float = 0.0, after_tip_s: float = 5.0,
                    points: list[ContactPoint] | None = None, on_step=None, motor=None) -> dict:
    """Drive along y_ref at a held speed with the body's ground contact on.

    Ends when the course is done without a tip, when the body has come to rest after the tip
    (REST_SPEED, REST_RATE for REST_HOLD), or after_tip_s after the tip. motor (drive.Motor): the speed
    control's drive torque is capped at what the motor gives at the driven wheels' mean spin (shared
    equally) and its integral stops growing while capped; without it the drive has no cap (as in the
    lane change tool). on_step(sim, con): called after every step."""
    sim = new_sim(veh, dt=dt, settle_s=1.0)
    pts = design_points(design) if points is None else points
    con = BodyContact(sim, pts, mu, k=k, c=c)
    con.live = True
    con.start()
    rec = ContactRecorder(sim, rec_hz, con, design) if rec_hz > 0 else None
    start_rolling(sim, speed)
    drv = PathDriver(sim, y_ref)
    ctrl = SpeedController(sim)
    w = Watch(sim, con)
    t_limit = 3.0 * abs(x_end) / max(speed, 0.5) + 5.0
    delta = 0.0
    while True:
        if not w.tipped:
            delta = drv.steer()
            drive, brake = ctrl.torques(sim, speed)
            if motor is not None and ctrl.driven:
                spin = sum(abs(sim.om[i]) for i in ctrl.driven) / len(ctrl.driven)
                cap = motor.wheel_torque_total(spin) / len(ctrl.driven)
                if any(x > cap for x in drive):
                    drive = [min(x, cap) for x in drive]
                    ctrl.integ -= (speed - sim.forward_speed()) * dt  # no wind-up while capped
                    w.st["capped_s"] += dt
        else:
            drive, brake = [0.0] * sim.n, [0.0] * sim.n
        ext = con.ext()
        sim.step(controls(sim, delta, drive, brake), ext, con.travel)
        w.step()
        if on_step is not None:
            on_step(sim, con)
        if rec is not None:
            cg = sim.cg_world()
            rec.tick({"steer": ("deg", math.degrees(delta)), "path_y": ("m", y_ref(float(cg[0])))})
            if w.new_peak:
                rec.snap = []
                rec.keep_snapshot()
        if w.tipped:
            if w.st["rest"] or sim.t - w.st["t_tip"] >= after_tip_s:
                break
        elif sim.design_origin()[0] >= x_end or sim.t >= t_limit:
            break
    st = w.finish()
    st.update(recorder=rec, sim=sim, points=pts)
    return st


def tilt_run(veh, design: dict, left_up: bool, *, mu: float, k: float = K_CONTACT, c: float = C_CONTACT,
             dt: float = 1e-3, rec_hz: float = 0.0, after_tip_s: float = 5.0, hold_past: float = math.radians(1.0),
             turn_rate: float = math.radians(4.0), points: list[ContactPoint] | None = None, on_step=None) -> dict:
    """The tilt table turned to the tip (scenario.tilt_sweep: the upper-side wheels have lost their load),
    then on at turn_rate to hold_past past it and held, the brakes held, the body's contact on, until the
    body rests (REST_SPEED, REST_RATE for REST_HOLD) or after_tip_s after it has tipped (or after
    after_tip_s if it does not tip). Gravity is turned, the table is K2's road (z its normal). From the
    tip on, the ground is the table and the level floor it is hinged on at its low edge, TABLE_MARGIN
    outside the wheels (road.table_with_floor), so the vehicle falls off the table onto the floor."""
    pts = design_points(design) if points is None else points
    made = {}

    def make_rec(sim, hz):
        made["con"] = BodyContact(sim, pts, mu, k=k, c=c)
        made["rec"] = ContactRecorder(sim, hz, made["con"], design)
        return made["rec"]

    hz = rec_hz if rec_hz > 0 else 1e-6  # no recording: a sample at the start only
    sw = tilt_sweep(veh, left_up=left_up, dt=dt, record_hz=hz, after_s=0.0, make_recorder=make_rec)
    if sw["tip_angle"] is None:
        raise DesignError("the vehicle did not tip on the table up to 80 deg")
    sim, rec, con = sw["sim"], made["rec"], made["con"]
    touched_in_turn = rec.touch_unforced
    con.live = True
    held = con.start()  # the points already under the table (the turn ran without the body's contact)
    rec.set_hz(hz)
    sign = 1.0 if left_up else -1.0
    theta = sw["tip_angle"]
    target = theta + hold_past
    y_hinge = -sign * (_half_width(veh) + TABLE_MARGIN)
    sim.road = table_with_floor(sign * theta, y_hinge)
    ctl = hold(sim)
    w = Watch(sim, con, up=(0.0, math.sin(sign * target), math.cos(sign * target)))
    t_start = sim.t
    while True:
        if theta < target:
            theta = min(target, theta + turn_rate * dt)
            sim.set_gravity(side_gravity(sign * theta))
            sim.road = table_with_floor(sign * theta, y_hinge)
        ext = con.ext()
        sim.step(ctl, ext, con.travel)
        w.step()
        if on_step is not None:
            on_step(sim, con)
        rec.tick({"table_angle": ("deg", math.degrees(sign * theta))})
        if w.new_peak:
            rec.snap = []
            rec.keep_snapshot()
        if w.st["rest"] or sim.t - (w.st["t_tip"] if w.tipped else t_start) >= after_tip_s:
            break
    rec.tick({"table_angle": ("deg", math.degrees(sign * theta))}, force=True)
    st = w.finish()
    st.update(recorder=rec, sim=sim, points=pts, tip_angle=sw["tip_angle"], t_wheels_off=sw["t_tip"],
              hold_angle=target, touched_in_turn=touched_in_turn, t_fall=t_start, y_hinge=y_hinge, held=held)
    return st


def _vectors(st) -> list[dict]:
    rec = st["recorder"]
    if not rec.snap:
        rec.keep_snapshot()
    out = rec.vectors(-1)
    for name, q, f in st["snap"]:
        out.append({"kind": "reaction", "at": name, "origin_mm": [_round(x * 1000.0) for x in q],
                    "vector_N": [_round(x) for x in f]})
    return out


def _results(st) -> list[dict]:
    """The numbers of a contact run (Watch) as results."""
    res = [_res("rollover", 1.0 if st["tip_kind"] == "roll" else 0.0, "1"),
           _res("pitch_over", 1.0 if st["tip_kind"] == "pitch" else 0.0, "1")]
    if st["t_lift"] is not None:
        res.append(_res("two_wheel_lift_time", st["t_lift"], "s", f"{st['lift_side']} wheels"))
    if st["t_tip"] is not None:
        res.append(_res("tip_time", st["t_tip"], "s", f"the body's up axis past {math.degrees(TIPPED_ROLL):g} deg "
                        f"from the road normal, over its {'side' if st['tip_kind'] == 'roll' else 'end'}"))
        res.append(_res("speed_at_tip", st["speed_at_tip"] * 3.6, "km/h"))
    if st["t_rollbar"] is not None:
        res.append(_res("rollbar_first_contact_time", st["t_rollbar"], "s"))
    res += [_res("rollbar_contact_force_peak", st["rb_peak"], "N", st["rb_peak_at"] or None),
            _res("rollbar_contact_total_peak", st["rb_total_peak"], "N",
                 f"at t {st['rb_total_t']:.3f} s" if st["rb_total_t"] is not None else None),
            _res("body_contact_force_peak", st["pt_peak"], "N", st["pt_peak_at"] or None),
            _res("max_body_accel", st["acc_peak"], "m/s^2",
                 f"at t {st['acc_peak_t']:.3f} s" if st["acc_peak_t"] is not None else None),
            _res("max_body_accel_g", st["acc_peak"] / G0, "g"),
            _res("max_roll", math.degrees(st["max_roll"]), "deg"),
            _res("roll_turned", math.degrees(st["roll_turned"]), "deg"),
            _res("max_contact_depth", st["depth"], "m", st["depth_at"] or None),
            _res("rest_roll", math.degrees(st["rest_roll"]), "deg", f"at t {st['t_end']:.3f} s"),
            _res("rest_tilt", math.degrees(st["rest_tilt"]), "deg"),
            _res("came_to_rest", 1.0 if st["rest"] else 0.0, "1"),
            _res("occupant_box_ground_contact", 1.0 if st["people_touch"] else 0.0, "1", st["people_touch"] or None)]
    if math.isfinite(st["rim_min"]):
        res.append(_res("wheel_rim_min_height", st["rim_min"], "m", st["rim_min_at"]))
    return res


K2X = ("rollbar_contact_force_peak", "rollbar_contact_total_peak", "max_body_accel", "rest_roll", "rest_tilt",
       "rollbar_first_contact_time", "body_contact_force_peak", "max_contact_depth")


def _k2x(st2) -> list[dict]:
    out = []
    for r in _results(st2):
        if r["quantity"] in K2X:
            r = dict(r, quantity=r["quantity"] + "_k2x")
            r.pop("where", None)
            out.append(r)
    return out


def _contact_note(pts, k, mu) -> str:
    return (f"body-to-ground contact: {sum(p.group == 'rollbar' for p in pts)} points on the roll bar members "
            f"(every {POINT_SPACING * 1000:.0f} mm), {sum(p.group not in ('rollbar', 'wheel') for p in pts)} corners "
            f"of the body's hull (frame nodes and sprung part boxes) and {sum(p.group == 'wheel' for p in pts)} on "
            "rings on the wheels' outer faces (0.8 x the radius, following the wheel's travel, the force into the "
            f"body); each a penalty spring {k / 1000:g} N/mm and damper {C_CONTACT:g} N s/m along the road normal, "
            f"Coulomb friction mu {mu:g} regularised below {V_EPS:g} m/s (HIRAKU's choices, no source); the road is "
            "flat under each point; the peak forces and accelerations depend on the stiffness (max_contact_depth: "
            "the deepest sink; the _k2x results: the same run at twice the stiffness)")


class _RolloverBase(K2Tool):
    domain = "handling"

    def own_checks(self) -> list[dict]:
        out = []
        # a rigid 500 kg box (its wheels never touch: a negative radius) flat on its four bottom corners
        veh = build_vehicle(box_design(mass=500.0, cg_x=-0.9, cg_z=0.5, susp="rigid", box_size=(2.0, 1.0, 0.3)))
        mu, v0 = 0.8, 5.0
        sim = Sim(veh, flat(), dt=1e-3)
        sim.radius = [-100.0] * sim.n
        cg = [float(x) for x in veh.cg_s]
        pts = [ContactPoint(f"c{k}", "box", (cg[0] + sx, cg[1] + sy, cg[2] - 0.15))
               for k, (sx, sy) in enumerate(((-1.0, -0.5), (-1.0, 0.5), (1.0, -0.5), (1.0, 0.5)))]
        con = BodyContact(sim, pts, mu)
        sim.q = quat_from_euler(0.0, 0.0, 0.0)
        sim.p = [0.0, 0.0, 0.15]
        sim.v = [0.0, 0.0, 0.0]
        for _ in range(1000):
            sim.step(sim.no_controls(), con.ext())
        kn = con.k / 1000.0
        out.append(_check(f"rigid 500 kg box at rest on four corner points, {kn:g} N/mm each", "rest_height",
                          0.15 - 500.0 * G0 / (4.0 * con.k), sim.p[2], "m", 1e-4,
                          "statics: each point carries m g / 4, sinking m g / (4 k)",
                          case_ja=f"500 kg の箱が四隅の接触点 (1 点 {kn:g} N/mm) で地面に載って止まったときの重心の高さ",
                          source_ja="つり合い: 1 点が m g / 4 を受け、m g / (4 k) 沈む"))
        sim.v = [v0, 0.0, sim.v[2]]
        vs = []
        for _ in range(500):
            sim.step(sim.no_controls(), con.ext())
            vs.append(sim.v[0])
        a = (vs[99] - vs[499]) / 0.4
        out.append(_check(f"the same box sliding at {v0:g} m/s on its points, mu {mu:g}", "deceleration", mu * G0, a,
                          "m/s^2", 5e-3, "Coulomb friction: a = mu g",
                          case_ja=f"同じ箱が接触点で {v0:g} m/s で滑るときの減速度 (摩擦係数 {mu:g})",
                          source_ja="クーロン摩擦: a = μ g"))
        return out


class RolloverTiltTool(_RolloverBase):
    """The tilt table (S1) past its limit: turned until the upper wheels have lost their load (as the tilt
    tool), then hold_past further and held while the vehicle falls onto the table with its body's contact.

    Results: tip_angle (deg, where the upper wheels lost their load), hold_angle (deg), wheels_off_time
    (s, that moment), the Watch numbers (rollover, tip_time, rollbar_first_contact_time,
    rollbar_contact_force_peak / _total_peak, body_contact_force_peak, max_body_accel(_g), max_roll,
    roll_turned, max_contact_depth, rest_roll, rest_tilt (the body's up axis from the table normal),
    came_to_rest, occupant_box_ground_contact, wheel_rim_min_height after the tip) and with
    stiffness_study the _k2x ones. Times are from the start of the table's turn."""

    name = "rollover_tilt"
    title = "横転 (傾斜台を倒れる角度より先まで傾け、車体と台の接触つき、K2)"
    tests = ["S1"]
    # only the plan of S1's drive record names it (hiraku.catalog.builders.k2_tests PLANS_DRIVE): a run with S1
    # checks it, and it covers no test, so S1's stage and tool stay the tilt tool's
    planned_only = True
    # tip_angle is the tilt tool's tilt_angle (the same tilt_sweep, the side that tips first): the same pair
    pairs = (Pair("tip_angle", "tilt.spring_roll_angle", "theta",
                  note="the angle at which the upper wheels lose their load, as the tilt tool's tilt_angle; the body "
                       "rolls on its springs, the roll axis of vertical-slider supports on the ground"),)
    params = (P("direction", None, "both", "which way the table tips; right = the table's right side goes down; "
                "both: the side with the smaller tip angle (both turned without recording first)",
                choices=("both", "right", "left")),
              P("table_mu", "1", 2.0, "table surface friction (tyres and body), high so that it tips, not slides, "
                                      "as in the tilt tool"),
              P("hold_past", "deg", 1.0, "the table is held this far past the tip angle", run_option=True),
              P("after_tip_s", "s", 5.0, "how long the run goes on after the tip at most", run_option=True),
              P("contact_stiffness", "N/mm", K_CONTACT / 1000.0, "per body contact point (assumed, no source)",
                assumed=True),
              P("stiffness_study", None, True, "also run at twice the contact stiffness", run_option=True))

    def _run(self, design: dict, lp: dict) -> dict:
        mu = self.q(lp, "table_mu", "1")
        veh = build_vehicle(design, {"mu": mu})
        k = self.q(lp, "contact_stiffness", "N/m")
        side = self.text(lp, "direction")
        notes_side = []
        if side == "both":
            angles = {}
            for s in ("right", "left"):
                a = tilt_sweep(veh, left_up=(s == "right"), dt=self.dt)["tip_angle"]
                angles[s] = math.inf if a is None else a
            side = min(angles, key=lambda s: angles[s])
            notes_side.append("tip angles without the body's contact: " + ", ".join(
                f"{s} {math.degrees(a):.3f} deg" for s, a in angles.items()) + f"; the run tips to the {side}")
        left_up = side == "right"
        pts = design_points(design)

        def run(k_, hz):
            return tilt_run(veh, design, left_up, mu=mu, k=k_, dt=self.dt, rec_hz=hz,
                            after_tip_s=self.q(lp, "after_tip_s", "s"), hold_past=self.q(lp, "hold_past", "rad"),
                            points=pts)

        st = run(k, self.record_hz)
        res = [_res("tip_angle", math.degrees(st["tip_angle"]), "deg", f"tipping to the {side}"),
               _res("hold_angle", math.degrees(st["hold_angle"]), "deg"),
               _res("wheels_off_time", st["t_wheels_off"], "s")] + _results(st)
        notes = list(veh.assumptions) + notes_side + [
            _contact_note(pts, k, mu),
            f"table surface friction {mu:g} for the tyres and the body's contact points, so it tips, not slides",
            f"the table turned as the tilt tool turns it to the tip ({math.degrees(st['tip_angle']):.2f} deg), then "
            f"at 4 deg/s to {math.degrees(st['hold_angle']):.2f} deg and held; the brakes held"]
        if st["touched_in_turn"]:
            notes.append(f"a body contact point was below the table at {st['touched_in_turn']} samples of the turn "
                         "to the tip (the turn runs without the body's contact)")
        if st["held"]:
            notes.append("under the table when the body's contact started (at the tip): " + ", ".join(st["held"])
                         + "; each takes the table at that depth and is pushed only for what it goes deeper "
                         "(contact.BodyContact.start: no energy from starting the contact)")
        # the body's roll to the floor at the end (the record's roll is to the table)
        sgn = 1.0 if left_up else -1.0
        th = sgn * st["hold_angle"]
        rr = math.degrees(st["rest_roll"] + th)
        res.append(_res("rest_roll_world", rr - 360.0 * round(rr / 360.0), "deg"))
        notes.append(f"from the tip on the ground is the table and the level floor it is hinged on at its low edge "
                     f"(y = {st['y_hinge'] * 1000:.0f} mm in the table frame, {TABLE_MARGIN * 1000:.0f} mm outside the "
                     "wheels: the table the tilt tool draws; HIRAKU's choice); the tip (the body's up axis past "
                     f"{math.degrees(TIPPED_ROLL):g} deg), rest_tilt and rest_roll_world are to the floor (the world's "
                     "up), max_roll, roll_turned, rest_roll and the record's roll to the table")
        if self.flag(lp, "stiffness_study") and st["tipped"]:
            res += _k2x(run(2.0 * k, 0.0))
        rec = st["recorder"]
        xs = [float(c.pos[0]) for c in veh.corners]
        r_max = max(c.radius for c in veh.corners)
        x0, x1 = min(xs) - r_max - 1.0, max(xs) + r_max + 1.0
        yh = st["y_hinge"]
        scene = [_box("tilt_table", "傾斜台 (table_angle が + のとき左が上、x のまわりに回る。倒れた車体はこの面に当たる)",
                      (x0, min(yh, -yh), -0.02), (x1, max(yh, -yh), 0.0), angle_channel="table_angle")]
        # the floor beyond the hinge, drawn in the table frame at the held angle (a band 6 m wide)
        wf = 6.0
        cy = yh - sgn * wf / 2.0 * math.cos(th)
        cz = sgn * wf / 2.0 * math.sin(th)
        scene.append({"kind": "ground", "label": f"水平な床 (傾斜台の低い縁 y = {yh * 1000:.0f} mm の蝶番でつながる。"
                      f"台を {math.degrees(st['hold_angle']):.1f}° に止めたときの位置で、台の座標で描く)",
                      "position_mm": [round((x0 + x1) / 2.0 * 1000.0, 1), round(cy * 1000.0, 1), round(cz * 1000.0, 1)],
                      "size_mm": [round((x1 - x0) * 1000.0, 1), round(wf * 1000.0, 1), 0.0],
                      "rpy_deg": [round(-math.degrees(th), 6), 0.0, 0.0]})
        return {"results": res, "series": rec.all_series(), "vectors": _vectors(st), "scene": scene,
                "assumptions": notes}


class RolloverLaneChangeTool(_RolloverBase):
    """The double lane change of S3 driven with the body's ground contact, at the load case's speed or (with
    search_tip) the lowest of v_start, v_start + v_step, ... up to v_max at which the body goes over its side.
    The speed control's drive is capped at the design's motor (MOTOR params), else uncapped.

    Results: run_speed (km/h), drive_capped_time (s), the Watch numbers and with stiffness_study the _k2x
    ones (when it tipped). Times are from the start of the run (the vehicle at speed)."""

    name = "rollover_lane_change"
    title = "横転 (ダブルレーンチェンジを転ぶ速さで、車体と地面の接触つき、K2)"
    tests = ["S3"]
    params = (P("speed", "km/h", 40.0, "held speed (when search_tip is false)"),
              MU,
              P("offset", "m", DLC_OFFSET, "sideways shift of the lane (ISO 3888-1:2018 Table 1, checked 2026-11-03)"),
              P("direction", None, "left", "the first lane change goes toward", choices=("left", "right")),
              P("search_tip", None, True, "search upward from v_start for the speed at which it rolls over",
                run_option=True),
              P("v_start", "km/h", 60.0, "the first speed of the search", run_option=True),
              P("v_step", "km/h", 2.5, "the step of the search", run_option=True),
              P("v_max", "km/h", 90.0, "the highest speed of the search", run_option=True),
              P("after_tip_s", "s", 5.0, "how long the run goes on after the tip at most", run_option=True),
              P("contact_stiffness", "N/mm", K_CONTACT / 1000.0, "per body contact point (assumed, no source)",
                assumed=True),
              P("stiffness_study", None, True, "also run at twice the contact stiffness", run_option=True)) + MOTOR

    def _run(self, design: dict, lp: dict) -> dict:
        veh = self.vehicle(design, lp)
        if not any(c.steer for c in veh.corners):
            return {"error": "no support has actuation.steer; a lane change needs a steered wheel"}
        mu = self.q(lp, "mu", "1")
        if mu is None:
            mu = sum(c.mu for c in veh.corners) / veh.n
            self._note(f"body contact friction mu = {mu:g}, the tyres' (the load case gives no mu)")
        k = self.q(lp, "contact_stiffness", "N/m")
        sign = -1.0 if self.text(lp, "direction") == "right" else 1.0
        offset = self.q(lp, "offset", "m")
        y0, x_end = dlc_path(offset=offset)
        y_ref = (lambda x: sign * y0(x))
        after = self.q(lp, "after_tip_s", "s")
        pts = design_points(design)
        notes = list(veh.assumptions) + [
            "double lane change course of ISO 3888-1:2018 Table 1 (15/30/25/25/15/15 m, offset 3.5 m; checked "
            "against the public sample on 2026-11-03); a centre line, not cone gates",
            _contact_note(pts, k, mu),
            f"once the body has turned past {math.degrees(TIPPED_ROLL):g} deg the driver lets go: steer held, no "
            "drive, no brake (HIRAKU's choice)"]
        try:
            motor = self.motor(lp)
            driven = [c for c in veh.corners if c.drive]
            top = motor.max_speed / motor.gear * sum(c.rolling_radius for c in driven) / max(1, len(driven))
            notes.append(f"the speed control's drive torque is capped at the motor's ({motor.torque:g} N*m, "
                         f"{motor.power / 1000.0:g} kW, gear {motor.gear:g}, efficiency {motor.efficiency:g}); at the "
                         f"motor's speed limit the wheels turn at about {top * 3.6:.1f} km/h, above that it gives no "
                         "drive and the vehicle coasts from the speed it starts at")
        except DesignError:
            motor = None
            notes.append("the design gives no motor: the speed control's drive torque has no cap")

        def run(v, k_, hz):
            return lane_change_run(veh, design, v, y_ref, x_end, mu=mu, k=k_, dt=self.dt, rec_hz=hz,
                                   after_tip_s=after, points=pts, motor=motor)

        tried = []
        if self.flag(lp, "search_tip"):
            v = self.q(lp, "v_start", "m/s")
            step, vmax = self.q(lp, "v_step", "m/s"), self.q(lp, "v_max", "m/s")
            while True:
                st = run(v, k, self.record_hz)
                tried.append((v * 3.6, st["tip_kind"]))
                if st["tip_kind"] == "roll" or v + step > vmax + 1e-9:
                    break
                v += step
            speed = v
        else:
            speed = self.q(lp, "speed", "m/s")
            st = run(speed, k, self.record_hz)
            tried.append((speed * 3.6, st["tip_kind"]))
        notes.append("speeds tried (km/h: what happened): " + ", ".join(
            f"{a:g}: {'rolled over' if b == 'roll' else ('went over its end (pitch)' if b else 'stayed on its wheels')}"
            for a, b in tried))
        res = [_res("run_speed", speed * 3.6, "km/h"), _res("drive_capped_time", st["capped_s"], "s")] + _results(st)
        if self.flag(lp, "stiffness_study") and st["tipped"]:
            res += _k2x(run(speed, 2.0 * k, 0.0))
        rec = st["recorder"]
        scene = dlc_scene(2.0 * _half_width(veh), sign, offset=offset)
        scene.append(_box("ground", "地面 (平ら、z = 0。車体の接触点はこの面に当たる)", (-10.0, -30.0, 0.0),
                          (x_end + 80.0, 30.0, 0.0)))
        return {"results": res, "series": rec.all_series() if rec else [], "vectors": _vectors(st) if rec else [],
                "scene": scene,
                "assumptions": notes + ["the cones and gates are drawn around the centre line; the driver model "
                                        "follows the line and the gates are not judged"]}
