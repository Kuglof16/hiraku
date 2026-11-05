"""Time histories and arrows in the contract's `series` / `vectors` shapes.

Channel names (the screen draws the driving animation from these):

  body_x, body_y, body_z        m    world position of the design origin carried by the body
  roll, pitch, yaw              deg  body attitude, R = Rz(yaw) Ry(pitch) Rx(roll)
  cg_x, cg_y, cg_z              m    whole-vehicle centre of mass (the CG marker)
  speed                         km/h forward speed of the body
  accel_x, accel_y              m/s^2 body-axis acceleration of the sprung CG
  per support <ID> (FL, FR, ...):
  wheel_load.<ID>               N    road normal force on the tyre (0 = lifted)
  wheel_fx.<ID>, wheel_fy.<ID>  N    tyre longitudinal / lateral force (tyre axes)
  wheel_stroke.<ID>             mm   suspension travel, bump positive
  wheel_steer.<ID>              deg  steer angle
  wheel_spin.<ID>               deg  wheel rotation angle (cumulative, for drawing)
  wheel_center_x/y/z.<ID>       m    wheel centre (world)
  contact_x/y/z.<ID>            m    contact point (world); arrows start here
  reaction_x/y/z.<ID>           N    road normal force vector (world)
  grip_x/y/z.<ID>               N    tyre friction force vector (world)
  wheel_camber.<ID>, wheel_toe.<ID>  deg  camber (top out +) and toe (toe-in +) to the body,
                                    only when a corner runs on a K&C table
  friction_limit.<ID> N, friction_use.<ID> 1, sliding.<ID> 1   the friction circle (mu N, |F| / mu N, 0 or 1)
  slip_angle.<ID> deg, slip_ratio.<ID> 1   the slips the tyre model takes (tan alpha = d_y / sigma,
                                    kappa = -d_x / sigma; wheels with a deflection-state tyre)
  plus scenario channels (table_angle deg, wind_speed m/s, steer deg, ...).
  Against slip angle, not time (series(): x = slip_angle deg): tyre_fy_curve.<ID>.<Fz N> N, the
  tyre model's side force at 0.5, 1, 1.5 and 2 times the wheel's static load (tyre_curves).

World axes are the road's (x along the road, y left, z up). On a tilt table
or a slope the road frame is the table; gravity is turned instead.
"""

from __future__ import annotations

import math

import numpy as np

from .sim import Sim, euler_from_r, quat_to_r

SIG = 6  # significant digits kept in series values


def _round(x: float) -> float:
    x = float(x)
    if x == 0.0 or not math.isfinite(x):
        return 0.0
    return float(f"{x:.{SIG}g}")


class Recorder:
    def __init__(self, sim: Sim, hz: float = 25.0):
        self.sim = sim
        self.every = max(1, int(round(1.0 / (hz * sim.dt))))
        self.k = 0
        self.t: list[float] = []
        self.ch: dict[str, list[float]] = {}
        self.units: dict[str, str] = {}
        self.spin_angle = [0.0] * sim.n
        self.snap: list[dict] = []
        self.kc = any(sim.kc)

    def set_hz(self, hz: float):
        self.every = max(1, int(round(1.0 / (hz * self.sim.dt))))
        self.k = 0

    def _put(self, name: str, unit: str, value: float):
        if name not in self.ch:
            self.ch[name] = []
            self.units[name] = unit
        self.ch[name].append(float(value))

    def tick(self, extra: dict | None = None, force: bool = False):
        """Call after each sim.step(); samples every `every` steps."""
        sim = self.sim
        for i in range(sim.n):
            self.spin_angle[i] += sim.om[i] * sim.dt
        self.k += 1
        if not force and (self.k - 1) % self.every:
            return
        self.sample(extra)

    def sample(self, extra: dict | None = None):
        sim = self.sim
        o = sim.out
        if not o:
            return
        r = quat_to_r(sim.q)
        roll, pitch, yaw = euler_from_r(r)
        org = sim.design_origin()
        cg = sim.cg_world()
        self.t.append(sim.t)
        self._put("body_x", "m", org[0])
        self._put("body_y", "m", org[1])
        self._put("body_z", "m", org[2])
        self._put("roll", "deg", math.degrees(roll))
        self._put("pitch", "deg", math.degrees(pitch))
        self._put("yaw", "deg", math.degrees(yaw))
        self._put("cg_x", "m", cg[0])
        self._put("cg_y", "m", cg[1])
        self._put("cg_z", "m", cg[2])
        self._put("speed", "km/h", sim.forward_speed() * 3.6)
        ab = r.T @ np.array(o["acc"])
        self._put("accel_x", "m/s^2", ab[0])
        self._put("accel_y", "m/s^2", ab[1])
        centres = sim.wheel_centres()
        for i, c in enumerate(sim.veh.corners):
            cid = c.id
            n_ = o["ntot"][i]
            nv = np.array(o["n"][i]) * n_
            fv = np.array(o["f"][i])
            gv = fv - nv
            self._put(f"wheel_load.{cid}", "N", n_)
            self._put(f"wheel_fx.{cid}", "N", o["fx"][i])
            self._put(f"wheel_fy.{cid}", "N", o["fy"][i])
            # the friction circle: the tyre model's limit mu N, how much of it the force uses, and
            # sliding (the use at or past 1, or the tyre model's own judgement: past the peak slip)
            lim = sim.tyres[i].mu * n_
            use = math.hypot(o["fx"][i], o["fy"][i]) / lim if lim > 0.0 else 0.0
            self._put(f"friction_limit.{cid}", "N", lim)
            self._put(f"friction_use.{cid}", "1", use)
            self._put(f"sliding.{cid}", "1", 1.0 if use >= 1.0 or o["sliding"][i] else 0.0)
            # the slips the tyre model takes from its deflection states: tan(alpha) = d_y / sigma, kappa = -d_x / sigma
            sig = getattr(sim.tyres[i], "sigma", None)
            if sig:
                self._put(f"slip_angle.{cid}", "deg", math.degrees(math.atan(sim.dy[i] / sig)))
                self._put(f"slip_ratio.{cid}", "1", -sim.dx[i] / sig)
            self._put(f"wheel_stroke.{cid}", "mm", sim.s[i] * 1000.0)
            self._put(f"wheel_steer.{cid}", "deg", math.degrees(o["steer"][i]))
            self._put(f"wheel_spin.{cid}", "deg", math.degrees(self.spin_angle[i]))
            if self.kc:
                self._put(f"wheel_camber.{cid}", "deg", math.degrees(o["camber"][i]))
                self._put(f"wheel_toe.{cid}", "deg", math.degrees(o["toe"][i]))
            for k, ax in enumerate("xyz"):
                self._put(f"wheel_center_{ax}.{cid}", "m", centres[i][k])
            for k, ax in enumerate("xyz"):
                self._put(f"contact_{ax}.{cid}", "m", o["p"][i][k])
            for k, ax in enumerate("xyz"):
                self._put(f"reaction_{ax}.{cid}", "N", nv[k])
            for k, ax in enumerate("xyz"):
                self._put(f"grip_{ax}.{cid}", "N", gv[k])
        if extra:
            for name, (unit, value) in extra.items():
                self._put(name, unit, value)

    def keep_snapshot(self) -> int:
        """Remember the current arrows (for `vectors`); returns its index."""
        sim = self.sim
        o = sim.out
        snap = {"t": sim.t, "wheels": []}
        for i, c in enumerate(sim.veh.corners):
            n_ = o["ntot"][i]
            nv = np.array(o["n"][i]) * n_
            fv = np.array(o["f"][i])
            snap["wheels"].append((c.id, np.array(o["p"][i]), nv, fv - nv))
        snap["cg"] = sim.cg_world()
        self.snap.append(snap)
        return len(self.snap) - 1

    def series(self, names: list[str] | None = None) -> list[dict]:
        t = [_round(x) for x in self.t]
        out = []
        for name, vals in self.ch.items():
            if names is not None and name not in names:
                continue
            out.append({"channel": name, "unit": self.units[name], "t_s": t, "values": [_round(v) for v in vals]})
        if names is None:
            out += tyre_curves(self.sim)
        return out

    def vectors(self, index: int = -1, extra: list[dict] | None = None) -> list[dict]:
        if not self.snap:
            return []
        snap = self.snap[index]
        out = []
        for cid, p, nv, gv in snap["wheels"]:
            origin = [_round(x * 1000.0) for x in p]
            out.append({"kind": "reaction", "at": cid, "origin_mm": origin, "vector_N": [_round(x) for x in nv]})
            out.append({"kind": "grip", "at": cid, "origin_mm": origin, "vector_N": [_round(x) for x in gv]})
        return out + (extra or [])


def _body_state(sim: Sim) -> dict:
    r = sim.rotation()
    return {"cg_m": [float(x) for x in sim.p], "quat": [float(x) for x in sim.q],
            "v_m_s": [float(x) for x in sim.v], "omega_rad_s": (r @ np.array(sim.w, dtype=float)).tolist()}


class BodyLog:
    """Every step of a run, for replaying the sprung body in another integrator (qa/rapier).

    start: the body's state before the first step. steps[k]: the loads of step k (Sim.loads,
    from the state the step started from) and the state after it. Loads: per corner the
    knuckle's force at the wheel centre ([force N, point m], world) and a free moment (N*m,
    world: the knuckle's moment and the drive and brake reaction). The body is rigid, mass
    m_s and inertia I_s about its centre of mass in body axes (rot = R maps body to world);
    gravity is the run's. External loads given to Sim.step (aerodynamic) are not logged.
    State: centre of mass (m), quaternion (w, x, y, z), velocity (m/s) and angular velocity
    (rad/s), world axes. K2 integrates semi-implicit Euler: v and omega (body axes) first,
    then the position with the new v and the attitude by q <- q (cos(|w| h/2), w/|w| sin(|w| h/2))."""

    def __init__(self, sim: Sim):
        self.sim = sim
        self.start = _body_state(sim)
        self.steps: list[dict] = []

    def tick(self) -> None:
        """Call after each sim.step()."""
        ld = self.sim.loads()
        self.steps.append(dict({"t": float(self.sim.t),
                                "forces": [[c["force"].tolist(), c["point"].tolist()] for c in ld["corners"]],
                                "moments": [(c["moment"] + c["body_torque"]).tolist() for c in ld["corners"]]},
                               **_body_state(self.sim)))

    def record(self) -> dict:
        sim = self.sim
        return {"dt": float(sim.dt), "n_steps": len(self.steps), "gravity_m_s2": [float(x) for x in sim.g],
                "mass_kg": float(sim.veh.m_s), "inertia_kg_m2": [list(map(float, row)) for row in sim.i_s],
                "corners": [c.id for c in sim.veh.corners], "start": self.start, "steps": self.steps}


TYRE_CURVE_LOADS = (0.5, 1.0, 1.5, 2.0)  # x each wheel's static load: the Fz of the tyre curves
TYRE_CURVE_DEG = [x * 0.5 for x in range(-30, 31)]  # slip angles of the curves, deg


def tyre_curves(sim: Sim) -> list[dict]:
    """The tyre model's side force against slip angle (pure slip, no camber), for every wheel with a Magic
    Formula tyre at TYRE_CURVE_LOADS times its static load: tyre_fy_curve.<support>.<Fz in N> (N) against
    slip_angle (deg), the sign as the wheel's wheel_fy and slip_angle (a right wheel's tyre is the left one
    mirrored), so a run's (slip_angle, wheel_fy) points move on them."""
    out = []
    for i, c in enumerate(sim.veh.corners):
        ty = sim.tyres[i]
        mf = getattr(ty, "mf", None)
        if mf is None:
            continue
        mirror = bool(getattr(ty, "mirror", False))
        for k in TYRE_CURVE_LOADS:
            fz = k * float(c.static_load)
            ys = []
            for a in TYRE_CURVE_DEG:
                ta = math.tan(math.radians(a))
                fy = -mf.forces(fz, 0.0, -ta)[1] if mirror else mf.forces(fz, 0.0, ta)[1]
                ys.append(_round(fy))
            out.append({"channel": f"tyre_fy_curve.{c.id}.{fz:.0f}", "unit": "N",
                        "x": {"quantity": "slip_angle", "unit": "deg", "values": list(TYRE_CURVE_DEG)}, "values": ys})
    return out
