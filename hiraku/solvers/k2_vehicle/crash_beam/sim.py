"""Running the beam frame in time: the state arrays, the crash into the wall, the record, and the
"node_motion" field of a run.

The run goes in chunks of REC_DT (the kernel's own steps inside, at the stable step); after each chunk
the node positions, the wall's impulse and the yields and breaks are written down, and the run stops
once the whole vehicle's velocity toward the wall (its momentum over its mass) is down to STOP_SHARE of
the start, or at the time limit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import kernel
from .mesh import Mesh, stable_dt

COURANT = 0.8         # the time step is this share of the elements' stable step 2 / omega_max
PENALTY = 1.0         # wall stiffness of a node: PENALTY m / dt^2 (with COURANT 0.8: (omega dt)^2 <= 2.56 + 1.0 < 4)
CONTACT_PENALTY = 0.05  # element contact: (omega dt)^2 of one pair's spring on its contact point's mass (kernel step 6);
                        # a point at a node can be in up to 4 pairs (0.2), a wheel link adds LINK_SHARE^2 x 2.56 (0.1):
                        # 2.56 + 1.0 + 0.2 + 0.1 < 4 (HIRAKU's choice)
CONTACT_SKIN = 0.01   # m, the contact pair list keeps the pairs within their contact distance + this
REC_DT = 1e-4         # s, the record's step
WINDOW = 1e-3         # s, the deceleration and the mount forces are averaged over this (from the momentum)


@dataclass
class State:
    """Everything the kernel reads and writes (SI)."""

    x: np.ndarray
    v: np.ndarray
    R: np.ndarray
    w: np.ndarray
    up: np.ndarray
    tp: np.ndarray
    thp: np.ndarray
    alive: np.ndarray
    yielded: np.ndarray
    fix: np.ndarray
    vpre: np.ndarray
    fext: np.ndarray
    fint: np.ndarray
    kpen: np.ndarray
    cpairs: np.ndarray       # (pairs that can touch, 2) the contact pair list (kernel step 6)
    cnp: np.ndarray          # (1,) its length; -1: make it at the next step
    xref: np.ndarray         # (n, 3) the positions when it was made
    fcon: np.ndarray         # (n, 3) the contact forces of the last step
    cstat: np.ndarray        # (3,) the lists made, the longest list, the pair-steps in contact
    t: float = 0.0

    def copy(self) -> "State":
        return State(**{k: (getattr(self, k).copy() if isinstance(getattr(self, k), np.ndarray) else getattr(self, k))
                        for k in self.__dataclass_fields__})


def new_state(mesh: Mesh, velocity=(0.0, 0.0, 0.0), dt: float | None = None) -> State:
    n, ne = mesh.n, mesh.ne
    v = np.tile(np.asarray(velocity, float), (n, 1))
    kpen = np.zeros(n) if dt is None else PENALTY * mesh.mass / (dt * dt)
    can = max(1, int(np.count_nonzero(mesh.cdist > 0.0)) // 2)
    return State(x=mesh.x0.copy(), v=v, R=np.tile(np.eye(3), (n, 1, 1)), w=np.zeros((n, 3)), up=np.zeros(ne),
                 tp=np.zeros(ne), thp=np.zeros((ne, 2, 2)), alive=np.ones(ne, dtype=np.int8),
                 yielded=np.zeros(ne, dtype=np.int8), fix=np.zeros((n, 6), dtype=np.int8), vpre=np.zeros((n, 6)),
                 fext=np.zeros((n, 6)), fint=np.zeros((n, 6)), kpen=kpen, cpairs=np.zeros((can, 2), dtype=np.int64),
                 cnp=np.array([-1], dtype=np.int64), xref=mesh.x0.copy(), fcon=np.zeros((n, 3)),
                 cstat=np.zeros(3, dtype=np.int64))


def time_step(mesh: Mesh) -> float:
    return COURANT * stable_dt(mesh)[0]


def advance(mesh: Mesh, st: State, nsteps: int, dt: float, *, alpha: float = 0.0, wall: float | None = None,
            mu: float = 0.0, hinge_lim: float = math.inf, use_jit: bool = True, contact: bool = True) -> np.ndarray:
    """nsteps steps of dt; returns the wall's impulse on the frame over them (N s, 3). contact: the elements touch
    each other (kernel step 6)."""
    imp = np.zeros(3)
    fn = kernel.advance if use_jit else kernel.advance_py
    fn(int(nsteps), float(dt), st.x, st.v, st.R, st.w, mesh.mass, mesh.inertia, mesh.con, mesh.l0, mesh.e0,
       mesh.prop, st.up, st.tp, st.thp, st.alive, st.yielded, st.fix, st.vpre, st.fext, float(alpha),
       0 if wall is None else 1, 0.0 if wall is None else float(wall), st.kpen, float(mu),
       float(min(hinge_lim, 1e300)), st.fint, imp, mesh.nrad, mesh.link, mesh.cdist, st.cpairs, st.cnp, st.xref,
       float(CONTACT_SKIN), float(CONTACT_PENALTY if contact else 0.0), st.fcon, st.cstat)
    st.t += nsteps * dt
    return imp


def hinge_rotations(st: State) -> np.ndarray:
    """(ne,) the larger end's plastic hinge rotation sqrt(theta_p,y^2 + theta_p,z^2) of each element, rad."""
    return np.sqrt((st.thp ** 2).sum(axis=2)).max(axis=1)


@dataclass
class CrashRun:
    """The record of a crash run (SI): one entry per REC_DT chunk, t the end of the chunk."""

    mesh: Mesh
    dt: float
    speed: float
    wall_x: float
    t: np.ndarray
    impulse: np.ndarray      # (k, 3) the wall's impulse on the frame over each chunk
    positions: np.ndarray    # (k + 1, n, 3), row 0 at t = 0
    velocities: np.ndarray   # (k + 1, n, 3)
    momentum: np.ndarray     # (k + 1, 3)
    broken_at: dict          # element -> t
    first_yield: float | None
    final: State
    stopped: str
    steps: int
    per: int                 # kernel steps per record row
    extra: dict = field(default_factory=dict)

    @property
    def t_all(self) -> np.ndarray:
        return np.concatenate([[0.0], self.t])

    @property
    def total_mass(self) -> float:
        return float(self.mesh.mass.sum())

    def decel(self, window: float = WINDOW) -> np.ndarray:
        """(k + 1,) the whole mass's deceleration (m/s^2, + slowing down) over the window ending at each
        record time: the wall's x impulse over it / (mass x window) (before t = 0 there is no impulse)."""
        cum = np.concatenate([[0.0], np.cumsum(self.impulse[:, 0])])
        w = self.window_rows(window)
        k = np.arange(len(cum))
        return -(cum - cum[np.maximum(k - w, 0)]) / (self.total_mass * w * self.chunk)

    @property
    def chunk(self) -> float:
        """s, the time between two record rows."""
        return self.per * self.dt

    def window_rows(self, window: float = WINDOW) -> int:
        return max(1, int(round(window / self.chunk)))


SPLIT_STOP_SHARE = 0.03  # a piece broken off the seats' side with this share of the whole mass is the split (C1: NG)
STOP_SHARE = 0.05  # the run stops when the whole vehicle's speed toward the wall is down to this share of the start
                   # (its kinetic energy then 0.25 % of the start's at most if all of it were along x; a fully plastic
                   # crush comes to rest without turning back, and a frame that yaws off the wall drifts on at a few
                   # tenths of a m/s with no wall force: e.g. showcase 20261105-150416 v3 at 0.32 m/s from 95 ms on;
                   # HIRAKU's choice)


def pieces(mesh: Mesh, alive: np.ndarray, keep: list[int]) -> list[list[int]]:
    """The pieces (node lists) the elements alive leave apart from the one holding `keep`'s first node."""
    parent = list(range(mesh.n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for e in np.nonzero(alive)[0]:
        i, j = mesh.con[e]
        parent[find(int(i))] = find(int(j))
    comps: dict[int, list[int]] = {}
    for k in range(mesh.n):
        comps.setdefault(find(k), []).append(k)
    main = find(int(keep[0]))
    return [nodes for r, nodes in comps.items() if r != main]


def run_crash(mesh: Mesh, speed: float, time_limit: float = 0.2, mu: float = 0.0, hinge_lim: float = math.inf,
              use_jit: bool = True, dt: float | None = None, wall: float | None = None,
              keep: list[int] | None = None, stop_share: float = SPLIT_STOP_SHARE, contact: bool = True,
              stop_at_split: bool | None = None) -> CrashRun:
    """The frame moving at `speed` (m/s, +x) into a rigid wall at x = `wall` (default its front-most frame node).

    keep: nodes of the main piece (e.g. the occupants' seat mounts). After each break the pieces apart from the
    main one are found: each is written in run.extra["pieces"] ({"t", "nodes", "share"}: its share of the whole
    mass); the first one with at least `stop_share` is the split (run.extra["split"] = {"t", "elements" broken in
    that record step, "share"}). contact: the elements touch each other (a broken-off piece pushes on the rest);
    stop_at_split: the run stops at the split (default: only without contact, where the rest would pass through
    the piece). Every record row also keeps the elements' plastic axial elongation and plastic hinge rotations
    (run.extra["up"], ["thp"]), for the members' forces; run.extra["contact"] the contact pair list's counts."""
    if stop_at_split is None:
        stop_at_split = not contact
    dt = time_step(mesh) if dt is None else dt
    st = new_state(mesh, (speed, 0.0, 0.0), dt)
    wall = float(mesh.x0[:mesh.n_frame, 0].max()) if wall is None else float(wall)
    per = max(1, int(round(REC_DT / dt)))
    M = float(mesh.mass.sum())
    ts, imps, pos, vel, mom = [], [], [st.x.copy()], [st.v.copy()], [mesh.mass @ st.v]
    ups, thps = [st.up.copy()], [st.thp.copy()]
    broken_at, first_yield = {}, None
    stopped = f"time limit {time_limit:g} s"
    steps = 0
    extra: dict = {"pieces": []}
    seen: set[tuple[int, ...]] = set()
    while st.t < time_limit - 0.5 * dt:
        alive0 = st.alive.copy()
        imp = advance(mesh, st, per, dt, wall=wall, mu=mu, hinge_lim=hinge_lim, use_jit=use_jit, contact=contact)
        steps += per
        ts.append(st.t)
        imps.append(imp)
        pos.append(st.x.copy())
        vel.append(st.v.copy())
        mom.append(mesh.mass @ st.v)
        ups.append(st.up.copy())
        thps.append(st.thp.copy())
        new = [int(e) for e in np.nonzero(alive0 != st.alive)[0]]
        for e in new:
            broken_at[e] = st.t
        if first_yield is None and st.yielded.any():
            first_yield = st.t
        if not np.all(np.isfinite(st.x)):
            raise ValueError(f"the beam frame run went unstable at t = {st.t:.4g} s (dt {dt:.3g} s)")
        if new and keep:
            big = None
            for nodes in pieces(mesh, st.alive, keep):
                key = tuple(nodes)
                if key in seen:
                    continue
                seen.add(key)
                share = float(mesh.mass[nodes].sum()) / M
                extra["pieces"].append({"t": st.t, "nodes": nodes, "share": share})
                if share >= stop_share and (big is None or share > big):
                    big = share
            if big is not None and "split" not in extra:
                extra["split"] = {"t": st.t, "elements": new, "share": big}
                if stop_at_split:
                    stopped = (f"a piece with {big * 100:.1f} % of the mass broke off the seats' side (at least "
                               f"{stop_share * 100:g} %; the run stops at the split)")
                    break
        if mom[-1][0] / M <= STOP_SHARE * speed:
            stopped = (f"the vehicle stopped (its speed toward the wall, its momentum / its mass, fell to "
                       f"{STOP_SHARE * 100:g} % of the start)")
            break
    extra["up"], extra["thp"] = np.array(ups), np.array(thps)
    extra["contact"] = {"on": bool(contact), "lists": int(st.cstat[0]), "longest": int(st.cstat[1]),
                        "pair_steps": int(st.cstat[2]), "pairs_possible": int(np.count_nonzero(mesh.cdist > 0.0)) // 2}
    return CrashRun(mesh, dt, speed, wall, np.array(ts), np.array(imps).reshape(-1, 3), np.array(pos), np.array(vel),
                    np.array(mom), broken_at, first_yield, st, stopped, steps, per, extra)


def member_forces(run: CrashRun, k: int) -> dict[str, float]:
    """{member: N} at record row k: of the member's elements alive then, the axial force E A / L0 (l - L0 - u_p),
    at most the yield load A f_y in size, of the largest size (signed, + tension); a member with none alive is left
    out."""
    mesh = run.mesh
    p = run.positions[k]
    t = run.t_all[k]
    up = run.extra["up"][k]
    out: dict[str, float] = {}
    for e in mesh.frame_elements():
        if run.broken_at.get(e, math.inf) <= t + 1e-12:
            continue
        i, j = mesh.con[e]
        ln = float(np.linalg.norm(p[j] - p[i]))
        # the positions are a kernel step ahead of u_p (central differences): the force the kernel takes there is
        # returned to the yield load, as it does
        n = float(np.clip(mesh.prop[e, 0] / mesh.l0[e] * (ln - mesh.l0[e] - up[e]), -mesh.prop[e, 4], mesh.prop[e, 4]))
        m = mesh.member_of[e]
        if m not in out or abs(n) > abs(out[m]):
            out[m] = n
    return out


def member_end_hinges(run: CrashRun, k: int) -> dict[tuple[str, str], float]:
    """{(member, end node id): rad} at record row k: the plastic hinge rotation sqrt(theta_y^2 + theta_z^2) at each
    end of each member (the end of the member's element at that node)."""
    mesh = run.mesh
    thp = run.extra["thp"][k]
    by_member: dict[str, list[int]] = {}
    for e in mesh.frame_elements():
        by_member.setdefault(mesh.member_of[e], []).append(e)
    out = {}
    for m, els in by_member.items():
        count: dict[int, int] = {}
        for e in els:
            for n in mesh.con[e]:
                count[int(n)] = count.get(int(n), 0) + 1
        for e in els:
            for side in (0, 1):
                n = int(mesh.con[e][side])
                if count[n] == 1:  # an end of the member
                    out[(m, mesh.node_ids[n])] = float(math.sqrt(float(thp[e, side, 0]) ** 2 + float(thp[e, side, 1]) ** 2))
    return out


# ------------------------------------------------------------------ the field of a run
def frame_picks(n_samples: int, special: list[int], even: int = 120, most: int = 150) -> list[int]:
    """Indices into n_samples record rows: `even` evenly spaced, plus the first, the last and the
    `special` ones (peak deceleration, first yield, first break), sorted, at most `most`."""
    if n_samples <= 0:
        return []
    picks = set(int(round(k)) for k in np.linspace(0, n_samples - 1, min(even, n_samples)))
    picks |= {0, n_samples - 1}
    picks |= {int(s) for s in special if s is not None and 0 <= s < n_samples}
    out = sorted(picks)
    while len(out) > most:  # drop evenly spaced ones next to others, never a special or an end
        keep = {0, n_samples - 1} | {int(s) for s in special if s is not None}
        gaps = [(out[i + 1] - out[i - 1], i) for i in range(1, len(out) - 1) if out[i] not in keep]
        if not gaps:
            break
        out.pop(min(gaps)[1])
    return out


MAX_FIELD_NODES = 300  # the contract's limit on a node_motion field's nodes


def default_picks(run: CrashRun, decel_g: np.ndarray | None = None) -> list[int]:
    """frame_picks with the peak deceleration's, the first yield's and the first break's rows (node_motion's
    frames; the members' series are kept at the same rows)."""
    tt = run.t_all
    dec = run.decel() if decel_g is None else decel_g
    peak = int(np.argmax(dec)) if len(dec) else 0
    fy = None if run.first_yield is None else int(np.searchsorted(tt, run.first_yield - 1e-12))
    fb = None if not run.broken_at else int(np.searchsorted(tt, min(run.broken_at.values()) - 1e-12))
    return frame_picks(len(tt), [peak, fy, fb])


def node_motion(run: CrashRun, label: str, picks: list[int] | None = None, decel_g: np.ndarray | None = None) -> dict:
    """The field record of kind "node_motion": the mesh nodes' positions (mm, 0.1 mm) at the picked
    record rows, the members as the elements' node pairs with their member id (the wheel links are not
    members and are left out; the wheel nodes are among the nodes), and the members broken by each
    frame. The picks: frame_picks with the peak deceleration's, the first yield's and the first break's
    rows."""
    mesh = run.mesh
    tt = run.t_all
    if picks is None:
        picks = default_picks(run, decel_g)
    broken = []
    for k in picks:
        ids = sorted({mesh.member_of[e] for e, tb in run.broken_at.items() if tb <= tt[k] + 1e-12})
        broken.append(ids)
    return {"kind": "node_motion", "label": label, "unit": "mm", "nodes": list(mesh.node_ids),
            "members": [[mesh.node_ids[mesh.con[e, 0]], mesh.node_ids[mesh.con[e, 1]], mesh.member_of[e]]
                        for e in mesh.frame_elements()],
            "frames": {"t_s": [float(tt[k]) for k in picks],
                       "positions_mm": [[[round(float(c) * 1000.0, 1) for c in p] for p in run.positions[k]] for k in picks],
                       "broken": broken}}


def contract_has_node_motion() -> bool:
    """The contract's field kinds include "node_motion" (read from the schema at run time)."""
    from hiraku import contract

    try:
        return "node_motion" in contract.schema()["$defs"]["field"]["properties"]["kind"]["enum"]
    except (KeyError, TypeError):
        return False
