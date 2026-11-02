"""Vehicle, powertrain and battery parameters read from a design, and the
road load and drive force they give.

Design variables (all required; DesignError lists the missing ones):
    rolling_resistance (1), drag_coefficient (1), frontal_area (m^2),
    air_density (kg/m^3), mass_factor (1, rotating parts),
    motor_peak_power (kW), motor_peak_torque (N*m), motor_max_speed (rpm),
    gear_ratio (1), drivetrain_efficiency (1), motor_efficiency (1),
    controller_efficiency (1), regen_efficiency (1), aux_power (W),
    battery_voltage (V), battery_capacity (Ah), battery_resistance (ohm)
Wheel radius: the driven supports' radius_mm.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..k0_handcalc import units
from ..k0_handcalc.units import STANDARD_GRAVITY
from ..k0_handcalc.vehicle import DesignError, mass_summary

VARIABLES = {
    "rolling_resistance": "1",
    "drag_coefficient": "1",
    "frontal_area": "m^2",
    "air_density": "kg/m^3",
    "mass_factor": "1",
    "motor_peak_power": "kW",
    "motor_peak_torque": "N*m",
    "motor_max_speed": "rpm",
    "gear_ratio": "1",
    "drivetrain_efficiency": "1",
    "motor_efficiency": "1",
    "controller_efficiency": "1",
    "regen_efficiency": "1",
    "aux_power": "W",
    "battery_voltage": "V",
    "battery_capacity": "Ah",
    "battery_resistance": "ohm",
}


@dataclass
class Powertrain:
    mass: float  # kg
    Crr: float
    CdA: float  # m^2
    rho: float  # kg/m^3
    mass_factor: float
    P_max: float  # W at the motor shaft
    T_max: float  # N*m at the motor shaft
    n_max: float  # rpm of the motor
    gear: float
    r_wheel: float  # m
    eta_drive: float  # gear and driveline
    eta_motor: float
    eta_ctrl: float
    eta_regen: float  # share of the braking energy at the wheels returned to the battery
    P_aux: float  # W
    V_batt: float  # V (open-circuit, flat plateau)
    Q_batt: float  # Ah
    R_batt: float  # ohm

    @property
    def F_max(self) -> float:
        """Largest tractive force at the wheels (torque limit), N."""
        return self.T_max * self.gear * self.eta_drive / self.r_wheel

    @property
    def v_motor_limit(self) -> float:
        """Road speed at the motor's speed limit, m/s."""
        return self.n_max / 60.0 * 2.0 * math.pi * self.r_wheel / self.gear

    @property
    def E_batt_Wh(self) -> float:
        return self.V_batt * self.Q_batt

    def drive_force(self, v: float) -> float:
        """Tractive force available at speed v (m/s): torque, then power, then speed limit."""
        if v >= self.v_motor_limit:
            return 0.0
        if v <= 0:
            return self.F_max
        return min(self.F_max, self.eta_drive * self.P_max / v)

    def road_load(self, v: float, grade: float) -> tuple[float, float, float]:
        """(rolling, grade, aero) resistance in N at speed v (m/s) on a grade (rise/run)."""
        th = math.atan(grade)
        W = self.mass * STANDARD_GRAVITY
        return W * self.Crr * math.cos(th), W * math.sin(th), 0.5 * self.rho * self.CdA * v * v

    def battery_power(self, P_wheel: float) -> tuple[float, float, float]:
        """(battery power, motor loss, controller loss) in W for a power at the wheels."""
        if P_wheel >= 0:
            P_shaft = P_wheel / self.eta_drive
            P_elec = P_shaft / self.eta_motor
            P_in = P_elec / self.eta_ctrl
            return P_in + self.P_aux, P_elec - P_shaft, P_in - P_elec
        P_back = -P_wheel * self.eta_regen  # energy reaching the battery terminals
        loss_m = -P_wheel * self.eta_drive * (1 - self.eta_motor)  # heat of regeneration shared as in driving
        loss_c = -P_wheel * self.eta_drive * self.eta_motor * (1 - self.eta_ctrl)
        return -P_back + self.P_aux, max(loss_m, 0.0), max(loss_c, 0.0)

    def battery_current(self, P_batt: float) -> float:
        """Current (A, positive discharging) for a terminal power, OCV - R I = V_term."""
        V, R = self.V_batt, self.R_batt
        if R <= 0:
            return P_batt / V
        disc = V * V - 4.0 * R * P_batt
        if disc < 0:
            raise DesignError(
                f"the battery cannot deliver {P_batt / 1000:.1f} kW (its limit is {V * V / (4 * R) / 1000:.1f} kW at "
                f"{V:g} V and {R:g} ohm); use a battery with less resistance or a higher voltage")
        return (V - math.sqrt(disc)) / (2.0 * R)


def _var(design: dict, name: str, unit: str) -> float | None:
    q = design.get("variables", {}).get(name)
    if q is None:
        return None
    try:
        return units.to_unit(q, unit, name)
    except ValueError as e:
        raise DesignError(f"design variable {e}") from None


def from_design(design: dict) -> Powertrain:
    vals = {k: _var(design, k, u) for k, u in VARIABLES.items()}
    missing = [k for k, v in vals.items() if v is None]
    if missing:
        raise DesignError("the design has no powertrain data for: " + ", ".join(f"{k} [{VARIABLES[k]}]" for k in missing)
                          + "; add them to design.variables")
    driven = [s for s in design.get("supports", []) if s["actuation"]["drive"]]
    if not driven:
        raise DesignError("no support is driven, so the vehicle cannot move; set actuation.drive on a wheel")
    for k in ("drivetrain_efficiency", "motor_efficiency", "controller_efficiency"):
        if not 0 < vals[k] <= 1:
            raise DesignError(f"design variable {k} must be in (0, 1], got {vals[k]}")
    if not 0 <= vals["regen_efficiency"] <= 1:
        raise DesignError(f"regen_efficiency must be in [0, 1], got {vals['regen_efficiency']}")
    return Powertrain(
        mass=mass_summary(design)["mass_kg"],
        Crr=vals["rolling_resistance"], CdA=vals["drag_coefficient"] * vals["frontal_area"], rho=vals["air_density"],
        mass_factor=vals["mass_factor"], P_max=vals["motor_peak_power"] * 1000.0, T_max=vals["motor_peak_torque"],
        n_max=vals["motor_max_speed"], gear=vals["gear_ratio"],
        r_wheel=sum(float(s["radius_mm"]) for s in driven) / len(driven) / 1000.0,
        eta_drive=vals["drivetrain_efficiency"], eta_motor=vals["motor_efficiency"],
        eta_ctrl=vals["controller_efficiency"], eta_regen=vals["regen_efficiency"], P_aux=vals["aux_power"],
        V_batt=vals["battery_voltage"], Q_batt=vals["battery_capacity"], R_batt=vals["battery_resistance"],
    )
