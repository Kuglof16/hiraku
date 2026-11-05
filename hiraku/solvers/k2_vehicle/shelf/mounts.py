"""The loads the body receives at the suspension mounts over a run, for the tools that record them
(step_climb_dynamic, braking, tilt, lane_change).

A run keeps the body's loads (Sim.loads) every LOAD_EVERY_S (LoadLog); mount_series turns them into
time series at a subset of those instants: every SPARSE_EVERY_S, at most DENSE_MAX in each window the
tool names, and the instant of each mount's largest force (found by a screening with the bench at a
nearby travel, peak_states, then solved exactly like every other kept instant).
"""

from __future__ import annotations

import math

import numpy as np

from .. import kinematics
from .base import _res

LOAD_EVERY_S = 0.01  # s between the body's loads kept during a run
SPARSE_EVERY_S = 0.25
DENSE_MAX = 30


def load_state(sim) -> dict:
    """Sim.loads of the last step with its time, the world position of the design origin and the
    gravity (m/s^2, world: the sprung weight over the sprung mass)."""
    out = sim.loads()
    out["t"] = sim.t
    out["origin"] = out["cg"] - out["rot"] @ np.asarray(sim.veh.cg_s, dtype=float)
    out["gravity"] = out["weight"] / float(sim.veh.m_s)
    return out


class LoadLog:
    """load_state every LOAD_EVERY_S of a run: call tick() after each step. rewind(t) drops the
    states after t (a run that goes back to a saved state)."""

    def __init__(self, sim, every_s: float = LOAD_EVERY_S):
        self.sim = sim
        self.every = max(1, int(round(every_s / sim.dt)))
        self.k = 0
        self.states: list[dict] = []

    def tick(self) -> None:
        self.k += 1
        if self.k % self.every == 0:
            self.states.append(load_state(self.sim))

    def keep(self) -> None:
        """Keep the current step's state too (e.g. the tip of a tilt), once."""
        if not self.states or self.states[-1]["t"] != self.sim.t:
            self.states.append(load_state(self.sim))

    def rewind(self, t: float) -> None:
        while self.states and self.states[-1]["t"] > t + 1e-12:
            self.states.pop()


def has_mounts(c) -> bool:
    return c.table is not None and "damper_lower" in c.table["hardpoints_mm"]


def corner_mounts(veh, st: dict, i: int, sol: dict | None = None) -> dict | None:
    """The forces corner i's double wishbone puts on the body at its mounts in the load state st:
    kinematics.corner_mount_forces of the knuckle's load (force at the wheel centre and moment,
    Sim.loads) at its travel (or with the bench solution sol). {mount: (point, force)}, world axes,
    m and N; None for a corner off a K&C table or without damper points."""
    c = veh.corners[i]
    if not has_mounts(c):
        return None
    rot, origin, ld = st["rot"], st["origin"], st["corners"][i]
    got = kinematics.corner_mount_forces(c.table, c.side, ld["travel"], rot.T @ ld["force"],
                                         rot.T @ (ld["point"] - origin), rot.T @ ld["moment"], sol=sol)
    return {m: (origin + rot @ p, rot @ f) for m, (p, f) in got.items()}


def mount_vectors(veh, st: dict) -> list:
    """The forces each K&C corner puts on the body at its mounts in the load state st (corner_mounts),
    as joint vectors at '<support>.<hardpoint key>': the mount's point and the force the body
    receives, world axes. A corner whose hardpoints have no damper points gives none."""
    out = []
    for i, c in enumerate(veh.corners):
        got = corner_mounts(veh, st, i)
        for mount, (point, force) in (got or {}).items():
            out.append({"kind": "joint", "at": f"{c.id}.{mount}", "origin_mm": [float(x) * 1000.0 for x in point],
                        "vector_N": [float(x) for x in force]})
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


def _load_map(c, sol: dict) -> tuple[list[str], np.ndarray, np.ndarray]:
    """The mounts' forces as a linear map of the knuckle's load at one bench solution: (mounts, ref,
    A (3 n_mounts x 6)) with [F_mount...] = A @ [f, M_ref], f the force and M_ref the moment about ref
    (design frame, body axes). corner_mount_forces is linear in the force and the moment for a fixed
    solution: the columns are its answers to unit forces at ref and unit moments."""
    ref = np.asarray(c.pos, dtype=float)
    cols = []
    for k in range(6):
        e = np.zeros(6)
        e[k] = 1.0
        got = kinematics.corner_mount_forces(c.table, c.side, 0.0, e[:3], ref, e[3:], sol=sol)
        mounts = list(got)
        cols.append(np.concatenate([got[m][1] for m in mounts]))
    return mounts, ref, np.array(cols).T


def peak_states(veh, states: list[dict]) -> set[int]:
    """The index of the state with the largest |F| of each mount of each K&C corner, screened with the
    bench at the nearest step of its march from design (kinematics.TravelSolver.near, within about
    1 mm of the travel) through _load_map. The kept instants are then solved exactly (mount_series)."""
    out: set[int] = set()
    for i, c in enumerate(veh.corners):
        if not has_mounts(c):
            continue
        ts = kinematics.table_solver(c.table)
        lds = [st["corners"][i] for st in states]
        rot = np.array([st["rot"] for st in states])
        body = lambda key: np.einsum("nji,nj->ni", rot, np.array([ld[key] for ld in lds]))  # world -> body axes
        f = body("force")
        cp = np.einsum("nji,nj->ni", rot, np.array([ld["point"] - st["origin"] for ld, st in zip(lds, states)]))
        ref = np.asarray(c.pos, dtype=float)
        load = np.hstack([f, body("moment") + np.cross(cp - ref, f)])  # [f, M about ref], one row per state
        groups: dict[int, tuple[dict, list[int]]] = {}
        lo, hi = ts.limits
        for k, ld in enumerate(lds):
            sol = ts.near(min(max(ld["travel"], lo), hi))
            groups.setdefault(id(sol), (sol, []))[1].append(k)
        sq = np.zeros((len(states), len(kinematics.MOUNTS)))
        for sol, ks in groups.values():
            _, _, a = _load_map(c, sol)
            fm = (load[ks] @ a.T).reshape(len(ks), -1, 3)
            sq[ks] = np.einsum("nmj,nmj->nm", fm, fm)
        out.update(int(k) for k in np.argmax(sq, axis=0))
    return out


def mount_series(veh, states: list[dict], windows: list[tuple[float, float]], peaks: bool = True) -> list:
    """Time series of the body's loads at mount_times (and, with peaks, at peak_states): per K&C
    corner and mount mount_x/y/z.<support>.<mount> (N, the force the body receives, world axes); per
    corner whose drive or brake reacts on the body body_torque_x/y/z.<support> (N*m, world); and the
    sprung body's sprung_cg_x/y/z (m), sprung_accel_x/y/z (m/s^2), sprung_ang_vel_x/y/z (rad/s),
    sprung_roll/pitch/yaw (deg, as roll, pitch, yaw), inertia_force_x/y/z (N, -m_s a, at the sprung
    CG), inertia_moment_x/y/z (N*m, about the sprung CG) and gravity_x/y/z (m/s^2, world: turned on a
    slope or a tilt table). With the sprung weight m_s g they balance: sum of the mount forces +
    weight + inertia force = 0, and the same for the moments about the sprung CG with the body
    torques and the inertia moment."""
    from ..record import _round
    from ..sim import euler_from_r
    if not states:
        return []
    idx = set(mount_times([st["t"] for st in states], windows))
    if peaks:
        idx |= peak_states(veh, states)
    idx = sorted(idx)
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
                                ("inertia_moment", "N*m", st["inertia_moment"]),
                                ("gravity", "m/s^2", st["gravity"])):
            for a, v in zip("xyz", vec):
                put(f"{name}_{a}", unit, v)
        for name, v in zip(("roll", "pitch", "yaw"), euler_from_r(st["rot"])):
            put(f"sprung_{name}", "deg", math.degrees(v))
    return [{"channel": name, "unit": unit, "t_s": ts, "values": vals} for name, (unit, vals) in ch.items()
            if not (name.startswith("body_torque_") and not any(vals))]


def state_at(states: list[dict], t: float) -> dict:
    """The load state nearest to the time t."""
    return min(states, key=lambda st: abs(st["t"] - t))


def mount_peak(series: list) -> tuple[dict, float] | None:
    """(the result mount_force_peak (N): the largest |F| of all the mounts in mount_series' series, where
    '<support>.<mount> at t <s> s'; its time t, s), None when the series have no mount forces. Each
    mount's own largest is in the series (one result per analysis: the checks look results up by
    quantity)."""
    ch = {s["channel"]: s for s in series}
    best = None
    for name, s in ch.items():
        if not name.startswith("mount_x."):
            continue
        rest = name[len("mount_x."):]
        ys, zs = ch[f"mount_y.{rest}"]["values"], ch[f"mount_z.{rest}"]["values"]
        for t, x, y, z in zip(s["t_s"], s["values"], ys, zs):
            v = math.sqrt(x * x + y * y + z * z)
            if best is None or v > best[0]:
                best = (v, rest, t)
    if best is None:
        return None
    return _res("mount_force_peak", best[0], "N", f"{best[1]} at t {best[2]:.3f} s"), best[2]
