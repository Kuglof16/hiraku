"""One-dimensional impact of the vehicle into a rigid wall: m x'' = -F(x).

The front is a bumper (rubber, linear up to its stroke) in front of a crush
zone; once the bumper bottoms out the crush zone's stiffness adds. Solved
with scipy's solve_ivp from first contact until the vehicle stops, which is
the largest crush, force and deceleration. What happens after (rebound) is
not modelled: crush zones keep most of the energy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.integrate import solve_ivp


@dataclass
class Front:
    k_bumper: float  # N/m
    stroke: float  # m, bumper travel before the crush zone takes load
    k_crush: float  # N/m, crush zone added after the bumper bottoms out

    def force(self, x: float) -> float:
        if x <= 0:
            return 0.0
        f = self.k_bumper * x
        if x > self.stroke:
            f += self.k_crush * (x - self.stroke)
        return f

    def energy(self, x: float) -> float:
        """Work done by the wall force up to crush x, J."""
        if x <= 0:
            return 0.0
        e = 0.5 * self.k_bumper * x * x
        if x > self.stroke:
            e += 0.5 * self.k_crush * (x - self.stroke) ** 2
        return e


def impact(mass: float, speed: float, front: Front, n_out: int = 300) -> dict:
    """Impact at speed (m/s) up to the stop. Returns time series and the peak values (SI)."""
    if mass <= 0 or speed <= 0:
        raise ValueError("mass and speed must be positive")
    if front.k_bumper <= 0 and front.k_crush <= 0:
        raise ValueError("the front has no stiffness")

    def rhs(t, y):
        return [y[1], -front.force(y[0]) / mass]

    def stopped(t, y):
        return y[1]
    stopped.terminal = True
    stopped.direction = -1

    k_min = front.k_bumper if front.k_bumper > 0 else front.k_crush
    k_max = front.k_bumper + front.k_crush
    t_scale = math.pi / 2.0 * math.sqrt(mass / k_min) + (front.stroke / speed if front.k_bumper <= 0 else 0.0)
    sol = solve_ivp(rhs, (0.0, 20.0 * t_scale), [0.0, speed], events=stopped, rtol=1e-10, atol=1e-13,
                    max_step=0.05 * math.sqrt(mass / k_max), dense_output=True)
    if not sol.t_events[0].size:
        raise ValueError("the vehicle did not stop against the wall in the time searched")
    t_stop = float(sol.t_events[0][0])
    x_max = float(sol.y_events[0][0][0])
    t = np.linspace(0.0, t_stop, n_out)
    x = sol.sol(t)[0]
    F = np.array([front.force(v) for v in x])
    return {"t": t, "x": x, "F": F, "a": F / mass, "t_stop": t_stop, "x_max": x_max,
            "F_max": front.force(x_max), "a_max": front.force(x_max) / mass,
            "E_absorbed": front.energy(x_max), "E_kinetic": 0.5 * mass * speed * speed}
