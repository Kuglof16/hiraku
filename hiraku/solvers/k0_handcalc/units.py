"""Unit conversion for the contract's unit strings (no external library).

Every unit in hiraku.contract.UNITS has a row here: a factor to SI, an
offset (only for degC) and a dimension vector over
(length, mass, time, current, temperature, angle). Angle is kept as its own
dimension so a plain number is never silently read as radians.
"""

from __future__ import annotations

import math

# (L, M, T, I, Theta, angle)
_DIM_NAMES = ("length", "mass", "time", "current", "temperature", "angle")

_NONE = (0, 0, 0, 0, 0, 0)
_LEN = (1, 0, 0, 0, 0, 0)
_MASS = (0, 1, 0, 0, 0, 0)
_TIME = (0, 0, 1, 0, 0, 0)
_FORCE = (1, 1, -2, 0, 0, 0)
_MOMENT = (2, 1, -2, 0, 0, 0)
_STIFF = (0, 1, -2, 0, 0, 0)
_STRESS = (-1, 1, -2, 0, 0, 0)
_ANGLE = (0, 0, 0, 0, 0, 1)
_SPEED = (1, 0, -1, 0, 0, 0)
_ACCEL = (1, 0, -2, 0, 0, 0)
_FREQ = (0, 0, -1, 0, 0, 0)
_POWER = (2, 1, -3, 0, 0, 0)
_VOLT = (2, 1, -3, -1, 0, 0)
_CURRENT = (0, 0, 0, 1, 0, 0)
_TEMP = (0, 0, 0, 0, 1, 0)
_THERMAL_R = (-2, -1, 3, 0, 1, 0)

STANDARD_GRAVITY = 9.80665  # m/s^2, the defined value of 1 g (CGPM 1901)

# unit -> (factor to SI, offset to SI, dimension)
UNIT_TABLE: dict[str, tuple[float, float, tuple[int, ...]]] = {
    "1": (1.0, 0.0, _NONE),
    "%": (0.01, 0.0, _NONE),
    "mm": (1e-3, 0.0, _LEN),
    "m": (1.0, 0.0, _LEN),
    "km": (1e3, 0.0, _LEN),
    "mm^2": (1e-6, 0.0, (2, 0, 0, 0, 0, 0)),
    "mm^3": (1e-9, 0.0, (3, 0, 0, 0, 0, 0)),
    "mm^4": (1e-12, 0.0, (4, 0, 0, 0, 0, 0)),
    "kg": (1.0, 0.0, _MASS),
    "N": (1.0, 0.0, _FORCE),
    "kN": (1e3, 0.0, _FORCE),
    "N*m": (1.0, 0.0, _MOMENT),
    "N*mm": (1e-3, 0.0, _MOMENT),
    "N/mm": (1e3, 0.0, _STIFF),
    "MPa": (1e6, 0.0, _STRESS),
    "Pa": (1.0, 0.0, _STRESS),
    "deg": (math.pi / 180.0, 0.0, _ANGLE),
    "rad": (1.0, 0.0, _ANGLE),
    "s": (1.0, 0.0, _TIME),
    "h": (3600.0, 0.0, _TIME),
    "m/s": (1.0, 0.0, _SPEED),
    "km/h": (1.0 / 3.6, 0.0, _SPEED),
    "m/s^2": (1.0, 0.0, _ACCEL),
    "g": (STANDARD_GRAVITY, 0.0, _ACCEL),
    "Hz": (1.0, 0.0, _FREQ),
    "rpm": (1.0 / 60.0, 0.0, _FREQ),
    "W": (1.0, 0.0, _POWER),
    "kW": (1e3, 0.0, _POWER),
    "J": (1.0, 0.0, _MOMENT),
    "Wh": (3600.0, 0.0, _MOMENT),
    "kWh": (3.6e6, 0.0, _MOMENT),
    "V": (1.0, 0.0, _VOLT),
    "A": (1.0, 0.0, _CURRENT),
    "degC": (1.0, 273.15, _TEMP),
    "K": (1.0, 0.0, _TEMP),
    "K/W": (1.0, 0.0, _THERMAL_R),
}


def dimension(unit: str) -> tuple[int, ...]:
    try:
        return UNIT_TABLE[unit][2]
    except KeyError:
        raise ValueError(f"unknown unit {unit!r}; known: {', '.join(UNIT_TABLE)}") from None


def describe_dimension(dim: tuple[int, ...]) -> str:
    parts = [f"{n}^{p}" if p != 1 else n for n, p in zip(_DIM_NAMES, dim) if p]
    return " ".join(parts) or "dimensionless"


def convert(value: float, from_unit: str, to_unit: str, name: str | None = None) -> float:
    """Convert value from one contract unit to another.

    Raises ValueError (naming `name` when given) for unknown units or
    different dimensions.
    """
    label = f"input {name!r}: " if name else ""
    if from_unit not in UNIT_TABLE:
        raise ValueError(f"{label}unknown unit {from_unit!r}")
    if to_unit not in UNIT_TABLE:
        raise ValueError(f"{label}unknown unit {to_unit!r}")
    f1, o1, d1 = UNIT_TABLE[from_unit]
    f2, o2, d2 = UNIT_TABLE[to_unit]
    if d1 != d2:
        raise ValueError(
            f"{label}cannot convert {from_unit!r} ({describe_dimension(d1)}) "
            f"to {to_unit!r} ({describe_dimension(d2)})"
        )
    if from_unit == to_unit:
        return float(value)
    si = float(value) * f1 + o1
    return (si - o2) / f2


def quantity(value: float, unit: str) -> dict:
    """A contract `quantity` record."""
    if unit not in UNIT_TABLE:
        raise ValueError(f"unknown unit {unit!r}")
    return {"value": float(value), "unit": unit}


def to_unit(q: dict, unit: str, name: str | None = None) -> float:
    """Value of a quantity record expressed in `unit`."""
    if not isinstance(q, dict) or "value" not in q or "unit" not in q:
        label = f"input {name!r}: " if name else ""
        raise ValueError(f"{label}expected a quantity {{'value': number, 'unit': str}}, got {q!r}")
    return convert(q["value"], q["unit"], unit, name)
