"""Unit handling for K2: contract quantities in, SI floats inside.

K2 works in SI (m, kg, s, N, rad) internally. Contract quantities are
{"value": x, "unit": "..."} with units from hiraku.schema.json.
"""

from __future__ import annotations

import math
from typing import Any

# Standard gravity, a defined value (3rd CGPM, 1901; ISO 80000-3).
G0 = 9.80665

TO_SI = {
    "1": 1.0,
    "%": 0.01,
    "mm": 1e-3,
    "m": 1.0,
    "km": 1e3,
    "kg": 1.0,
    "N": 1.0,
    "kN": 1e3,
    "N*m": 1.0,
    "N*mm": 1e-3,
    "N/mm": 1e3,
    "MPa": 1e6,
    "Pa": 1.0,
    "deg": math.pi / 180.0,
    "rad": 1.0,
    "s": 1.0,
    "h": 3600.0,
    "m/s": 1.0,
    "km/h": 1.0 / 3.6,
    "m/s^2": 1.0,
    "g": G0,
    "Hz": 1.0,
    # revolutions per minute, a frequency like Hz (cycles per second), as K0 and the QA known
    # answers read it; one revolution is 2 pi rad, so angular rates (rad/s, deg/s) are kept apart
    "rpm": 1.0 / 60.0,
    "rad/s": 1.0,
    "deg/s": math.pi / 180.0,
    "W": 1.0,
    "kW": 1e3,
    "J": 1.0,
    "mm^2": 1e-6,
    "m^2": 1.0,
    "kg/m^3": 1.0,
    "kg*m^2": 1.0,
    "N/rad": 1.0,
}

# SI unit each contract unit converts to (for checking a quantity's dimension).
SI_OF = {
    "1": "1", "%": "1",
    "mm": "m", "m": "m", "km": "m",
    "kg": "kg",
    "N": "N", "kN": "N",
    "N*m": "N*m", "N*mm": "N*m",
    "N/mm": "N/m",
    "MPa": "Pa", "Pa": "Pa",
    "deg": "rad", "rad": "rad",
    "s": "s", "h": "s",
    "m/s": "m/s", "km/h": "m/s",
    "m/s^2": "m/s^2", "g": "m/s^2",
    "Hz": "Hz", "rpm": "Hz", "rad/s": "rad/s", "deg/s": "rad/s",
    "W": "W", "kW": "W",
    "J": "J",
    "mm^2": "m^2", "m^2": "m^2",
    "kg/m^3": "kg/m^3", "kg*m^2": "kg*m^2", "N/rad": "N/rad",
}


def si(q: Any, want: str | None = None) -> float:
    """Convert a contract quantity (or a bare number, taken as SI) to SI.

    want: the SI unit expected ("m", "m/s", "rad", ...); a quantity of another
    dimension raises ValueError.
    """
    if isinstance(q, (int, float)) and not isinstance(q, bool):
        return float(q)
    if not isinstance(q, dict) or "value" not in q or "unit" not in q:
        raise ValueError(f"not a quantity: {q!r}")
    unit = q["unit"]
    if unit not in TO_SI:
        raise ValueError(f"unit {unit!r} is not handled by K2")
    if want is not None and SI_OF[unit] != want:
        raise ValueError(f"quantity in {unit!r} cannot be read as {want!r}")
    return float(q["value"]) * TO_SI[unit]


def quantity(value: float, unit: str) -> dict:
    """SI value -> contract quantity in `unit`."""
    return {"value": float(value) / TO_SI[unit], "unit": unit}


def param(params: dict, name: str, want: str, default: float | None = None) -> float | None:
    """Read params[name] (quantity or number) as SI, or return default."""
    if params is None or name not in params:
        return default
    return si(params[name], want)
