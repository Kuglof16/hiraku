"""A 3D frame by the direct stiffness method: 6 DOF per node, numpy only.

A second opinion for K1 (hiraku.solvers.k1_frame, built on PyNite); no code is shared with
either. Members are straight, uniform and rigidly joined, Euler-Bernoulli (no shear
deformation, as in K1): axial E A, torsion G J, bending E Iy about local y and E Iz about
local z. A member is split wherever another node lies on it. Loads: forces and moments at
nodes, and a uniform load per length along a member in global axes. Springs act along the
line between two nodes. Supports hold any of a node's six DOFs. Units are the caller's
(N, mm, MPa in HIRAKU).

Local axes: x from node i to node j; y is the caller's reference vector made
perpendicular to x; z = x cross y.

Internal forces at a distance s from node i come from the end forces and the uniform load.
N is positive in tension; the moments use the local axes (only their sizes are compared).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Member:
    name: str
    i: str
    j: str
    E: float
    G: float
    A: float
    Iy: float
    Iz: float
    J: float
    y_ref: np.ndarray
    w: np.ndarray = field(default_factory=lambda: np.zeros(3))  # uniform load per length, global axes


@dataclass
class _Element:
    member: Member
    a: int
    b: int
    s0: float  # distance of node a from the member's node i
    L: float
    R: np.ndarray  # rows: local x, y, z in global axes


def _local_stiffness(E, G, A, Iy, Iz, J, L) -> np.ndarray:
    k = np.zeros((12, 12))
    ea, gj = E * A / L, G * J / L
    for (p, q), v in {(0, 0): ea, (0, 6): -ea, (6, 6): ea, (3, 3): gj, (3, 9): -gj, (9, 9): gj}.items():
        k[p, q] = k[q, p] = v
    # bending in the x-y plane (v, theta_z) and the x-z plane (w, theta_y: theta_y = -dw/dx)
    for (v, t, I, sgn) in ((1, 5, Iz, 1.0), (2, 4, Iy, -1.0)):
        c = E * I / L**3
        m = c * np.array([[12, 6 * L, -12, 6 * L], [6 * L, 4 * L * L, -6 * L, 2 * L * L],
                          [-12, -6 * L, 12, -6 * L], [6 * L, 2 * L * L, -6 * L, 4 * L * L]])
        d = np.array([1.0, sgn, 1.0, sgn])
        m = m * np.outer(d, d)
        idx = [v, t, v + 6, t + 6]
        for p in range(4):
            for q in range(4):
                k[idx[p], idx[q]] = m[p, q]
    return k


def _equivalent_loads(wl: np.ndarray, L: float) -> np.ndarray:
    """Nodal loads (local) equivalent to a uniform local load wl = (wx, wy, wz) per length."""
    wx, wy, wz = wl
    f = np.zeros(12)
    f[[0, 6]] = wx * L / 2
    f[[1, 7]] = wy * L / 2
    f[[2, 8]] = wz * L / 2
    f[5], f[11] = wy * L * L / 12, -wy * L * L / 12
    f[4], f[10] = -wz * L * L / 12, wz * L * L / 12
    return f


class Frame3D:
    def __init__(self):
        self.names: list[str] = []
        self.xyz: list[np.ndarray] = []
        self.members: list[Member] = []
        self.springs: list[tuple[str, str, float]] = []
        self.loads: dict[str, np.ndarray] = {}
        self.fixed: dict[str, list[bool]] = {}

    def node(self, name: str, xyz) -> None:
        self.names.append(name)
        self.xyz.append(np.asarray(xyz, float))

    def member(self, name, i, j, E, G, A, Iy, Iz, J, y_ref, w=(0.0, 0.0, 0.0)) -> None:
        self.members.append(Member(name, i, j, E, G, A, Iy, Iz, J, np.asarray(y_ref, float), np.asarray(w, float)))

    def spring(self, i: str, j: str, k: float) -> None:
        self.springs.append((i, j, k))

    def load(self, node: str, fm) -> None:
        self.loads[node] = self.loads.get(node, np.zeros(6)) + np.asarray(fm, float)

    def support(self, node: str, dofs) -> None:
        """dofs: six booleans (DX, DY, DZ, RX, RY, RZ), True = held."""
        old = self.fixed.get(node, [False] * 6)
        self.fixed[node] = [a or bool(b) for a, b in zip(old, dofs)]

    # --- solving -----------------------------------------------------------------------
    def _elements(self) -> list[_Element]:
        idx = {n: k for k, n in enumerate(self.names)}
        X = np.array(self.xyz)
        out = []
        for m in self.members:
            pa, pb = X[idx[m.i]], X[idx[m.j]]
            L = float(np.linalg.norm(pb - pa))
            x = (pb - pa) / L
            y = m.y_ref - (m.y_ref @ x) * x
            y /= np.linalg.norm(y)
            R = np.vstack([x, y, np.cross(x, y)])
            s = (X - pa) @ x
            off = np.linalg.norm((X - pa) - np.outer(s, x), axis=1)
            on = [k for k in range(len(X)) if off[k] < 1e-6 * L and 1e-9 * L < s[k] < L * (1 - 1e-9)]
            inner = sorted(on, key=lambda k: s[k])
            chain = [idx[m.i]] + inner + [idx[m.j]]
            at = [0.0] + [float(s[k]) for k in inner] + [L]
            for t in range(len(chain) - 1):
                out.append(_Element(m, chain[t], chain[t + 1], at[t], at[t + 1] - at[t], R))
        return out

    def solve(self) -> dict:
        n = len(self.names)
        idx = {nm: k for k, nm in enumerate(self.names)}
        K = np.zeros((6 * n, 6 * n))
        F = np.zeros(6 * n)
        els = self._elements()
        for e in els:
            m = e.member
            T = np.kron(np.eye(4), e.R)
            k = T.T @ _local_stiffness(m.E, m.G, m.A, m.Iy, m.Iz, m.J, e.L) @ T
            f = T.T @ _equivalent_loads(e.R @ m.w, e.L)
            dofs = np.r_[6 * e.a:6 * e.a + 6, 6 * e.b:6 * e.b + 6]
            K[np.ix_(dofs, dofs)] += k
            F[dofs] += f
        X = np.array(self.xyz)
        for i, j, ks in self.springs:
            a, b = idx[i], idx[j]
            d = X[b] - X[a]
            d /= np.linalg.norm(d)
            kk = ks * np.outer(d, d)
            for p, q, sgn in ((a, a, 1), (b, b, 1), (a, b, -1), (b, a, -1)):
                K[6 * p:6 * p + 3, 6 * q:6 * q + 3] += sgn * kk
        for nm, fm in self.loads.items():
            F[6 * idx[nm]:6 * idx[nm] + 6] += fm
        held = np.zeros(6 * n, bool)
        for nm, flags in self.fixed.items():
            held[6 * idx[nm]:6 * idx[nm] + 6] = flags
        free = ~held
        U = np.zeros(6 * n)
        U[free] = np.linalg.solve(K[np.ix_(free, free)], F[free])
        reactions = K @ U - F
        return {"U": {nm: U[6 * k:6 * k + 6] for k, nm in enumerate(self.names)},
                "reactions": {nm: reactions[6 * idx[nm]:6 * idx[nm] + 6] for nm in self.fixed},
                "elements": [(e, self._end_forces(e, U)) for e in els]}

    @staticmethod
    def _end_forces(e: _Element, U: np.ndarray) -> np.ndarray:
        m = e.member
        T = np.kron(np.eye(4), e.R)
        u = T @ np.r_[U[6 * e.a:6 * e.a + 6], U[6 * e.b:6 * e.b + 6]]
        return _local_stiffness(m.E, m.G, m.A, m.Iy, m.Iz, m.J, e.L) @ u - _equivalent_loads(e.R @ m.w, e.L)

    @staticmethod
    def internal(result: dict, member: str, s: float) -> np.ndarray:
        """(N, Vy, Vz, T, My, Mz) at distance s from node i of a member (local axes)."""
        for e, f in result["elements"]:
            if e.member.name == member and e.s0 - 1e-9 <= s <= e.s0 + e.L + 1e-9:
                x = s - e.s0
                w = e.R @ e.member.w
                Fi, Mi = f[:3], f[3:6]
                force = Fi + w * x  # on the part [0, x] from its end and its load
                moment = Mi - x * np.cross([1.0, 0.0, 0.0], Fi) - 0.5 * x * x * np.cross([1.0, 0.0, 0.0], w)
                return -np.r_[force, moment]
        raise KeyError(f"no element of member {member} at s = {s}")
