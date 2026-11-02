"""A small plane-frame stiffness solver used only as a second opinion.

Euler-Bernoulli beam-column elements in the x-z plane, three degrees of
freedom per node (u along x, w along z, rotation r = dw/dx, counter-clockwise
in the x-z plane). It exists so QA can check the closed forms and, later, K1
on frames with no closed form. It shares no code with hiraku.solvers.

The element matrix is the standard 6x6 stiffness of a prismatic member
(axial EA/L, bending 12EI/L^3, 6EI/L^2, 4EI/L, 2EI/L), rotated to global axes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Frame2D:
    nodes: dict[str, tuple[float, float]] = field(default_factory=dict)
    members: list[tuple[str, str, float, float, float]] = field(default_factory=list)  # a, b, E, A, I
    fixed: dict[str, tuple[bool, bool, bool]] = field(default_factory=dict)
    loads: dict[str, tuple[float, float, float]] = field(default_factory=dict)  # Fx, Fz, M (sense of r)

    def node(self, name: str, x: float, z: float) -> None:
        self.nodes[name] = (x, z)

    def member(self, a: str, b: str, E: float, A: float, I: float, parts: int = 1) -> None:
        """Add a member, split into `parts` elements with new intermediate nodes."""
        (xa, za), (xb, zb) = self.nodes[a], self.nodes[b]
        names = [a] + [f"{a}_{b}_{k}" for k in range(1, parts)] + [b]
        for k in range(1, parts):
            s = k / parts
            self.node(names[k], xa + s * (xb - xa), za + s * (zb - za))
        for p, q in zip(names, names[1:]):
            self.members.append((p, q, E, A, I))

    def support(self, name: str, u: bool = True, w: bool = True, r: bool = True) -> None:
        self.fixed[name] = (u, w, r)

    def load(self, name: str, fx: float = 0.0, fz: float = 0.0, m: float = 0.0) -> None:
        self.loads[name] = (fx, fz, m)

    def _element(self, a: str, b: str, E: float, A: float, I: float) -> tuple[np.ndarray, list[int]]:
        order = list(self.nodes)
        (xa, za), (xb, zb) = self.nodes[a], self.nodes[b]
        L = float(np.hypot(xb - xa, zb - za))
        c, s = (xb - xa) / L, (zb - za) / L
        k_ax, k1, k2, k3, k4 = E * A / L, 12 * E * I / L**3, 6 * E * I / L**2, 4 * E * I / L, 2 * E * I / L
        k = np.array([
            [k_ax, 0, 0, -k_ax, 0, 0],
            [0, k1, k2, 0, -k1, k2],
            [0, k2, k3, 0, -k2, k4],
            [-k_ax, 0, 0, k_ax, 0, 0],
            [0, -k1, -k2, 0, k1, -k2],
            [0, k2, k4, 0, -k2, k3],
        ])
        t = np.array([[c, s, 0], [-s, c, 0], [0, 0, 1.0]])
        T = np.zeros((6, 6))
        T[:3, :3] = t
        T[3:, 3:] = t
        ia, ib = order.index(a), order.index(b)
        dofs = [3 * ia, 3 * ia + 1, 3 * ia + 2, 3 * ib, 3 * ib + 1, 3 * ib + 2]
        return T.T @ k @ T, dofs

    def solve(self) -> dict[str, dict[str, tuple[float, float, float]]]:
        """Return {"disp": {node: (u, w, r)}, "reaction": {node: (Rx, Rz, M)}}."""
        order = list(self.nodes)
        n = 3 * len(order)
        K = np.zeros((n, n))
        for a, b, E, A, I in self.members:
            ke, dofs = self._element(a, b, E, A, I)
            K[np.ix_(dofs, dofs)] += ke
        F = np.zeros(n)
        for name, f in self.loads.items():
            i = order.index(name)
            F[3 * i:3 * i + 3] += f
        fixed = [3 * order.index(nm) + j for nm, flags in self.fixed.items() for j in range(3) if flags[j]]
        free = [i for i in range(n) if i not in fixed]
        d = np.zeros(n)
        d[free] = np.linalg.solve(K[np.ix_(free, free)], F[free])
        R = K @ d - F
        disp = {nm: tuple(d[3 * i:3 * i + 3]) for i, nm in enumerate(order)}
        reaction = {nm: tuple(R[3 * order.index(nm):3 * order.index(nm) + 3]) for nm in self.fixed}
        return {"disp": disp, "reaction": reaction}
