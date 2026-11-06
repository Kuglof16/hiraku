"""K3: drive a cycle, and follow the battery and the part temperatures in time.

Along the speed profile (cycle.speed_profile) each 1 m step gives the wheel
force (inertia + road load), the battery power through the drive chain, the
battery current from its equivalent circuit (open-circuit voltage and series
resistance) and the heat in the battery, motor and controller. The heat is
then put on the thermal network on an even time grid (energy kept exactly)
and solved with the matrix exponential.

The power at each stage of the chain and the reducer loss are kept per step
(DriveResult.flow, gear_loss). Driving, battery terminal power = aux + losses of
the controller, motor and reducer + wheel power, exactly. Regenerating, the
battery gets regen_efficiency of the wheel power while the part losses follow
the chain efficiencies, so (eta_drive eta_motor eta_ctrl - eta_regen) |wheel
power| is lost without being given to a part.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..k0_handcalc import units
from ..k0_handcalc.vehicle import DesignError
from .cycle import Segment, speed_profile
from .powertrain import Powertrain
from .thermal import ThermalNetwork

THERMAL_PARTS = ("battery", "motor", "controller")


@dataclass
class DriveResult:
    t: np.ndarray  # s, at the ends of the road steps
    x: np.ndarray  # m
    v: np.ndarray  # m/s
    grade: np.ndarray
    P_batt: np.ndarray  # W per step (held over the step)
    I_batt: np.ndarray  # A per step
    soc: np.ndarray  # 0..1 at the step ends
    heat: dict[str, np.ndarray]  # W per step for battery, motor, controller
    energy: dict[str, float]  # Wh: rolling, grade_up, grade_down, aero, inertia, braking, losses, aux, battery
    # W per step along the chain (CHAIN_STAGES), positive from the battery to the wheels. "wheel": the wheel power the
    # chain carries (friction braking beyond the regeneration limit left out); "reducer": at the motor shaft
    # (wheel / eta_drive driving, wheel * eta_drive regenerating); "motor": the motor's electric side; "controller":
    # its battery side, the battery's terminal power less the auxiliary load (in regeneration the model's
    # regen_efficiency share, not the product of the chain efficiencies).
    flow: dict[str, np.ndarray] = None
    # W per step lost in the reducer and driveline: wheel (1 - eta_drive) / eta_drive driving, |wheel| (1 - eta_drive)
    # regenerating (the same share the motor's regeneration heat in Powertrain.battery_power assumes)
    gear_loss: np.ndarray = None


CHAIN_STAGES = ("controller", "motor", "reducer", "wheel")


def chain_flow(pt: Powertrain, P_wheel: float, P_batt: float) -> tuple[dict[str, float], float]:
    """Power at each stage of the chain (CHAIN_STAGES) and the reducer loss for a wheel power the chain carries
    and the battery terminal power the model gave for it (Powertrain.battery_power)."""
    if P_wheel >= 0:
        P_shaft = P_wheel / pt.eta_drive
        P_elec = P_shaft / pt.eta_motor
        loss_r = P_shaft - P_wheel
    else:
        P_shaft = P_wheel * pt.eta_drive
        P_elec = P_shaft * pt.eta_motor
        loss_r = -P_wheel * (1.0 - pt.eta_drive)
    return {"controller": P_batt - pt.P_aux, "motor": P_elec, "reducer": P_shaft, "wheel": P_wheel}, loss_r


def drive(segments: list[Segment], pt: Powertrain, soc0: float = 1.0) -> DriveResult:
    prof = speed_profile(segments, pt)
    x, v, grade = prof["x"], prof["v"], prof["grade"]
    h = np.diff(x)
    vm = (v[1:] + v[:-1]) / 2.0
    dt = h / np.maximum(vm, 1e-9)
    acc = (v[1:] ** 2 - v[:-1] ** 2) / (2.0 * h)
    g = grade[:-1]
    m_eff = pt.mass * pt.mass_factor
    n = len(h)
    P_batt = np.empty(n)
    I = np.empty(n)
    heat = {k: np.empty(n) for k in THERMAL_PARTS}
    flow = {k: np.empty(n) for k in CHAIN_STAGES}
    gear_loss = np.empty(n)
    e = dict.fromkeys(("rolling", "grade_up", "grade_down", "aero", "inertia", "braking", "losses", "aux", "battery"), 0.0)
    regen_cap = -pt.P_max * pt.eta_drive  # the motor cannot take back more than its power
    for k in range(n):
        roll, gr, aero = pt.road_load(vm[k], g[k])
        F = m_eff * acc[k] + roll + gr + aero
        P_wheel = F * vm[k]
        P_motor_side = max(P_wheel, regen_cap)
        Pb, loss_m, loss_c = pt.battery_power(P_motor_side)
        Ik = pt.battery_current(Pb)
        loss_b = Ik * Ik * pt.R_batt
        P_batt[k], I[k] = Pb, Ik
        heat["battery"][k], heat["motor"][k], heat["controller"][k] = loss_b, loss_m, loss_c
        fk, gear_loss[k] = chain_flow(pt, P_motor_side, Pb)
        for s in CHAIN_STAGES:
            flow[s][k] = fk[s]
        w = dt[k] / 3600.0
        e["rolling"] += roll * vm[k] * w
        e["grade_up" if gr > 0 else "grade_down"] += gr * vm[k] * w
        e["aero"] += aero * vm[k] * w
        e["inertia"] += m_eff * acc[k] * vm[k] * w
        e["braking"] += min(P_wheel - P_motor_side, 0.0) * w
        e["losses"] += (loss_m + loss_c + loss_b) * w
        e["aux"] += pt.P_aux * w
        e["battery"] += (Pb + loss_b) * w  # chemical energy drawn, including the internal loss
    t = np.concatenate([[0.0], np.cumsum(dt)])
    Q = pt.Q_batt * 3600.0
    soc = soc0 - np.concatenate([[0.0], np.cumsum(I * dt)]) / Q
    return DriveResult(t, x, v, grade, P_batt, I, soc, heat, e, flow, gear_loss)


def even_grid(t_steps: np.ndarray, power_steps: np.ndarray, dt: float) -> tuple[np.ndarray, np.ndarray]:
    """Mean power over an even time grid from piecewise-constant powers (energy kept exactly)."""
    E = np.concatenate([[0.0], np.cumsum(power_steps * np.diff(t_steps))])
    n = int(np.ceil(t_steps[-1] / dt - 1e-9))
    grid = dt * np.arange(n + 1)  # the last cell may run past the end; its power is spread over the cell
    Eg = np.interp(grid, t_steps, E)
    return grid, np.diff(Eg) / dt


def network_from_design(design: dict) -> ThermalNetwork:
    C, Gamb = [], []
    missing = []
    for p in THERMAL_PARTS:
        c = design.get("variables", {}).get(f"{p}_heat_capacity")
        r = design.get("variables", {}).get(f"{p}_thermal_resistance")
        if c is None:
            missing.append(f"{p}_heat_capacity [J/K]")
        if r is None:
            missing.append(f"{p}_thermal_resistance [K/W]")
        if c is not None and r is not None:
            try:
                C.append(units.to_unit(c, "J/K", f"{p}_heat_capacity"))
                R = units.to_unit(r, "K/W", f"{p}_thermal_resistance")
            except ValueError as e:
                raise DesignError(f"design variable {e}") from None
            if R <= 0:
                raise DesignError(f"{p}_thermal_resistance must be positive")
            Gamb.append(1.0 / R)
    if missing:
        raise DesignError("the design has no thermal data for: " + ", ".join(missing) + "; add them to design.variables")
    return ThermalNetwork(list(THERMAL_PARTS), np.array(C), np.array(Gamb))


def temperatures(res: DriveResult, net: ThermalNetwork, T_amb: float, dt: float = 1.0,
                 charge: tuple[float, float] | None = None, pt: Powertrain | None = None) -> dict:
    """Part temperatures on an even grid; optional charge phase (power W, duration s) after the drive."""
    cols = []
    grid = None
    for p in net.names:
        grid, P = even_grid(res.t, res.heat[p], dt)
        cols.append(P)
    losses = np.column_stack(cols)
    if charge is not None and pt is not None:
        P_ch, t_ch = charge
        I_ch = P_ch / pt.V_batt  # charging current at the terminals (approximation: OCV)
        n_ch = int(np.ceil(t_ch / dt))
        extra = np.zeros((n_ch, len(net.names)))
        extra[:, net.names.index("battery")] = I_ch * I_ch * pt.R_batt
        losses = np.vstack([losses, extra])
        grid = np.concatenate([grid, grid[-1] + dt * np.arange(1, n_ch + 1)])
    T = net.simulate(losses, dt, T_amb)
    return {"t": grid, "T": T, "losses": losses}
