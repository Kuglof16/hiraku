"""The D3Q19 lattice (same order as kernels.cu) and a plain CPU step for the tests.

The CPU step is written separately from the kernels, from the equations, so a GPU step can be compared
with it population by population on small grids. It is not a way to run real cases.
"""

from __future__ import annotations

import numpy as np

C = np.array([
    (0, 0, 0),
    (1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1),
    (1, 1, 0), (-1, -1, 0), (1, -1, 0), (-1, 1, 0),
    (1, 0, 1), (-1, 0, -1), (1, 0, -1), (-1, 0, 1),
    (0, 1, 1), (0, -1, -1), (0, 1, -1), (0, -1, 1),
], dtype=np.int64)
W = np.array([1.0 / 3.0] + [1.0 / 18.0] * 6 + [1.0 / 36.0] * 12)
OPP = np.array([0, 2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 11, 14, 13, 16, 15, 18, 17], dtype=np.int64)
CS2 = 1.0 / 3.0


def equilibrium_h(dr: np.ndarray, u: np.ndarray) -> np.ndarray:
    """Shifted equilibrium h_eq (19, ...) for density perturbation dr (...) and velocity u (3, ...)."""
    rho = 1.0 + dr
    usq = (u * u).sum(axis=0)
    out = np.empty((19,) + np.shape(dr))
    for q in range(19):
        cu = sum(int(C[q, a]) * u[a] for a in range(3))
        out[q] = W[q] * (dr + rho * (3.0 * cu + 4.5 * cu * cu - 1.5 * usq))
    return out


def moments(h: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """dr (...) and velocity (3, ...) of h (19, ...)."""
    dr = h.sum(axis=0)
    rho = 1.0 + dr
    u = np.stack([sum(int(C[q, a]) * h[q] for q in range(19)) / rho for a in range(3)])
    return dr, u


def collide(h: np.ndarray, tau0: float, cs2: float = 0.0) -> np.ndarray:
    """Regularized collision with the Smagorinsky closure on h (19, ...)."""
    dr, u = moments(h)
    rho = 1.0 + dr
    heq = equilibrium_h(dr, u)
    neq = h - heq
    Pi = np.empty((3, 3) + np.shape(dr))
    for a in range(3):
        for b in range(3):
            Pi[a, b] = sum(int(C[q, a] * C[q, b]) * neq[q] for q in range(19))
    if cs2 > 0.0:
        pp = (Pi * Pi).sum(axis=(0, 1))
        tau = 0.5 * (tau0 + np.sqrt(tau0 * tau0 + 18.0 * cs2 * np.sqrt(2.0 * pp) / rho))
    else:
        tau = tau0 * np.ones(np.shape(dr))
    out = np.empty_like(h)
    tr = Pi[0, 0] + Pi[1, 1] + Pi[2, 2]
    for q in range(19):
        Qpi = sum(int(C[q, a] * C[q, b]) * Pi[a, b] for a in range(3) for b in range(3)) - tr / 3.0
        out[q] = heq[q] + (1.0 - 1.0 / tau) * 4.5 * W[q] * Qpi
    return out
