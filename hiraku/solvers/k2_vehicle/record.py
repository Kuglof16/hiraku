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
  plus scenario channels (table_angle deg, wind_speed m/s, steer deg, ...).

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
            self._put(f"wheel_stroke.{cid}", "mm", sim.s[i] * 1000.0)
            self._put(f"wheel_steer.{cid}", "deg", math.degrees(o["steer"][i]))
            self._put(f"wheel_spin.{cid}", "deg", math.degrees(self.spin_angle[i]))
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
