"""Build load cases from the requirements and the design.

Every number a load case carries (step height, masses, the stress limit) is
taken from a requirement or the design, or computed here. The model never
sets a limit or a tolerance.
"""

from __future__ import annotations

from typing import Any, Callable

from hiraku.catalog.loader import Catalog

Record = dict[str, Any]


class CatalogError(ValueError):
    pass


def _q(value: float, unit: str) -> Record:
    return {"value": float(value), "unit": unit}


def requirement(req: Record, key: str, unit: str | None = None) -> Record:
    for r in req["requirements"]:
        if r["key"] == key:
            if unit is not None and r.get("unit") != unit:
                raise CatalogError(f"requirement {key} must be in {unit}, got {r.get('unit')!r}")
            if not isinstance(r.get("value"), (int, float)) or isinstance(r.get("value"), bool):
                raise CatalogError(f"requirement {key} needs a numeric value, got {r.get('value')!r}")
            return r
    raise CatalogError(f"requirement {key} is missing from the requirements file")


def _load_set(req: Record) -> tuple[Record, list[str]]:
    """Occupants and cargo of the heaviest load set (MVP: Q30 people + Q40 cargo)."""
    n = requirement(req, "Q30", "1")["value"]
    m_person = requirement(req, "Q33", "kg")["value"]
    m_cargo = requirement(req, "Q40", "kg")["value"]
    params = {
        "load_set": f"{n:g} 人 + 荷",
        "occupants": _q(n, "1"),
        "occupant_mass": _q(m_person, "kg"),
        "cargo_mass": _q(m_cargo, "kg"),
    }
    return params, ["Q30", "Q33", "Q40"]


def min_frame_yield(design: Record) -> tuple[float, list[str], bool]:
    """Smallest yield stress among the materials the members use (the safe side).

    Returns (yield MPa, material names, all verified).
    """
    used = {m["material"] for m in design.get("members", [])}
    if not used:
        raise CatalogError("the design has no members, so the frame yield stress is unknown")
    missing = used - set(design.get("materials", {}))
    if missing:
        raise CatalogError(f"members use materials not in design.materials: {sorted(missing)}")
    mats = [design["materials"][k] for k in sorted(used)]
    y = min(m["yield_MPa"] for m in mats)
    weakest = [m["name"] for m in mats if m["yield_MPa"] == y]
    return y, weakest, all(m["verified"] for m in mats)


def _p1_step(spec: Record, req: Record, design: Record) -> Record:
    h = requirement(req, "Q15", "mm")["value"]
    load, keys = _load_set(req)
    speed = spec["params"]["speed"]
    return {
        "load_case_id": f"P1-step{h:g}",
        "test_id": "P1",
        "domain": spec["domain"],
        "title": f"段差 {h:g} mm を {load['load_set']} で越える",
        "params": {
            "step_height": _q(h, "mm"),
            "speed": _q(speed["value"], speed["unit"]),
            **load,
        },
        "requirement_keys": ["Q15", *keys],
        "tolerance_rel": spec["tolerance_rel"],
    }


def _r1_static(spec: Record, req: Record, design: Record) -> Record:
    h = requirement(req, "Q15", "mm")["value"]
    sf = requirement(req, "Q61", "1")["value"]
    if sf <= 0:
        raise CatalogError(f"Q61 safety factor must be positive, got {sf}")
    load, keys = _load_set(req)
    y, names, verified = min_frame_yield(design)
    note = "" if verified else ", not checked"
    return {
        "load_case_id": f"R1-step{h:g}",
        "test_id": "R1",
        "domain": spec["domain"],
        "title": f"段差 {h:g} mm を越えるときの前輪の力をフレームに",
        "params": {"step_height": _q(h, "mm"), **load},
        "requirement_keys": ["Q15", *keys, "Q61"],
        "criterion": {
            "quantity": "max_stress",
            "op": "<=",
            "limit": _q(y / sf, "MPa"),
            "basis": f"yield {y:g} MPa ({', '.join(names)}{note}) / Q61 {sf:g}",
            "requirement_keys": ["Q61"],
        },
        "tolerance_rel": spec["tolerance_rel"],
    }


BUILDERS: dict[str, Callable[[Record, Record, Record], Record]] = {
    "p1_step": _p1_step,
    "r1_static": _r1_static,
}

# Which load case feeds which (P1's wheel force goes into the R1 frame load).
FEEDS = {"P1": ["R1"]}


def build_load_cases(catalog: Catalog, req: Record, design: Record, test_ids: list[str]) -> list[Record]:
    out = []
    for tid in test_ids:
        spec = catalog.test(tid)
        if "build" not in spec:
            raise CatalogError(f"test {tid} has no load-case builder yet")
        out.append(BUILDERS[spec["build"]](spec, req, design))
    by_test = {lc["test_id"]: lc["load_case_id"] for lc in out}
    for lc in out:
        feeds = [by_test[t] for t in FEEDS.get(lc["test_id"], []) if t in by_test]
        if feeds:
            lc["feeds"] = feeds
    return out
