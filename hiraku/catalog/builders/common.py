"""Shared parts of the load-case builders: reading requirements, the loading, quantities."""

from __future__ import annotations

from typing import Any

from hiraku.catalog.build import CatalogError

Record = dict[str, Any]


class BuildError(CatalogError):
    """A load case cannot be built; the message names the missing or wrong requirement."""


def q(value: float, unit: str) -> Record:
    return {"value": float(value), "unit": unit}


def entry(req: Record, key: str) -> Record | None:
    return next((r for r in req.get("requirements", []) if r["key"] == key), None)


def number(req: Record, key: str, unit: str) -> float:
    """A numeric requirement converted to `unit` (BuildError when missing or of another kind)."""
    from hiraku.solvers.k0_handcalc import units

    r = entry(req, key)
    if r is None:
        raise BuildError(f"requirement {key} is missing from the requirements file")
    v = r.get("value")
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        raise BuildError(f"requirement {key} needs a numeric value, got {v!r}")
    try:
        return units.convert(float(v), r.get("unit", "1"), unit, key)
    except ValueError as e:
        raise BuildError(f"requirement {key}: {e}") from None


def text(req: Record, key: str, choices: tuple[str, ...] = ()) -> str:
    r = entry(req, key)
    if r is None:
        raise BuildError(f"requirement {key} is missing from the requirements file")
    v = r.get("value")
    if not isinstance(v, str) or (choices and v not in choices):
        raise BuildError(f"requirement {key} must be one of {', '.join(choices)}, got {v!r}" if choices
                         else f"requirement {key} needs a text value, got {v!r}")
    return v


def body_mass_in_kg(req: Record) -> Record:
    """req with Q33 (the person) as a mass in kg when the requirements give it as a weight in N (a standard's user
    load, e.g. JIS T 9254's 1350 N): m = F / g with g the standard gravity (units.STANDARD_GRAVITY), said in the
    entry's basis. Other units stay as they are (check_design_loading names them)."""
    from hiraku.solvers.k0_handcalc import units

    out = []
    for r in req.get("requirements", []):
        if r["key"] == "Q33" and r.get("unit") == "N" and isinstance(r.get("value"), (int, float)):
            m = units.convert(float(r["value"]), "N", "N") / units.STANDARD_GRAVITY
            r = dict(r, value=m, unit="kg", basis=f"{r.get('basis', '')} (as a mass: {r['value']:g} N / "
                                                  f"{units.STANDARD_GRAVITY:g} m/s^2 = {m:.1f} kg)".strip())
        out.append(r)
    return dict(req, requirements=out)


def loading(req: Record, design: Record) -> tuple[str, list[str]]:
    """The loading to check (Q30) after checking the design carries the requirements' load
    (hiraku.catalog.build; Q33 given in N is taken as a mass, body_mass_in_kg), and the requirement keys that
    set it."""
    from hiraku.catalog.build import check_design_loading, loading_requirement
    from hiraku.catalog.loading import CARGO, OCCUPANTS

    try:
        name = loading_requirement(req)
        check_design_loading(body_mass_in_kg(req), design, name)
    except CatalogError as e:
        raise BuildError(str(e)) from None
    keys = ["Q30"] if entry(req, "Q30") else []
    keys += ["Q33"] if OCCUPANTS[name] and entry(req, "Q33") else []
    keys += ["Q40"] if CARGO[name] and entry(req, "Q40") else []
    return name, keys


def tolerance(spec: Record, default: float) -> float:
    """The catalog's tolerance_rel for the test when it gives one, else the builder's."""
    return float(spec.get("tolerance_rel", default))


def design_var(design: Record, name: str, unit: str) -> float | None:
    from hiraku.solvers.k0_handcalc import units

    v = design.get("variables", {}).get(name)
    return None if v is None else units.to_unit(v, unit, name)
