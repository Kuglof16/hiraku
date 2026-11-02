"""Lumped thermal network: C_i dT_i/dt = P_i - sum_j G_ij (T_i - T_j) - G_i0 (T_i - T_amb).

The network is linear, and the losses are held constant over each time step,
so each step is advanced exactly with the matrix exponential (no step-size
error, deterministic).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.linalg import expm


@dataclass
class ThermalNetwork:
    names: list[str]
    C: np.ndarray  # J/K per node
    G_amb: np.ndarray  # W/K from each node to the air
    G: np.ndarray = field(default=None)  # W/K between nodes (symmetric, zero diagonal)

    def __post_init__(self) -> None:
        n = len(self.names)
        self.C = np.asarray(self.C, float)
        self.G_amb = np.asarray(self.G_amb, float)
        self.G = np.zeros((n, n)) if self.G is None else np.asarray(self.G, float)
        if np.any(self.C <= 0):
            raise ValueError("every heat capacity must be positive")
        if np.any(self.G_amb < 0) or np.any(self.G < 0):
            raise ValueError("conductances cannot be negative")
        L = np.diag(self.G_amb + self.G.sum(axis=1)) - self.G
        self.A = -L / self.C[:, None]  # dT/dt = A (T - T_amb) + P / C

    def stepper(self, dt: float):
        """(Phi, Gamma) so that dT_next = Phi dT + Gamma P for a constant loss P over dt."""
        n = len(self.names)
        M = np.zeros((2 * n, 2 * n))
        M[:n, :n] = self.A * dt
        M[:n, n:] = np.diag(1.0 / self.C) * dt
        E = expm(M)
        return E[:n, :n], E[:n, n:]

    def steady(self, P: np.ndarray) -> np.ndarray:
        """Steady rise above the air for constant losses P (W)."""
        return np.linalg.solve(-self.A, np.asarray(P, float) / self.C)

    def simulate(self, P_series: np.ndarray, dt: float, T_amb: float, T0: np.ndarray | None = None) -> np.ndarray:
        """Temperatures (n_steps + 1, n) for losses P_series (n_steps, n) held over each step."""
        Phi, Gam = self.stepper(dt)
        n = len(self.names)
        dT = np.zeros(n) if T0 is None else np.asarray(T0, float) - T_amb
        out = np.empty((len(P_series) + 1, n))
        out[0] = dT
        for k, P in enumerate(P_series):
            dT = Phi @ dT + Gam @ P
            out[k + 1] = dT
        return out + T_amb
