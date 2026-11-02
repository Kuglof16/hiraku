"""Build load cases from the requirements and the design.

Every number a load case carries (step height, masses, the stress limit) is
taken from a requirement or the design, or computed here. The model never
sets a limit or a tolerance.
"""

from __future__ import annotations

import math
from typing import Any, Callable

from hiraku.catalog.loader import Catalog
from hiraku.catalog.loading import CARGO, LOADING_JA, OCCUPANTS, parts_on_board
from hiraku.contract.protocols import DEFAULT_LOADING, LOADINGS

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


def loading_requirement(req: Record) -> str:
    """The loading to check (Q30): one of hiraku.contract.protocols.LOADINGS."""
    for r in req["requirements"]:
        if r["key"] == "Q30":
            if r.get("value") not in LOADINGS:
                raise CatalogError(f"Q30 must be one of {', '.join(LOADINGS)}, got {r.get('value')!r}")
            return r["value"]
    return DEFAULT_LOADING


def check_design_loading(req: Record, design: Record, loading: str) -> None:
    """The design's occupant and cargo parts must carry the requirement values (Q33, Q40).

    Occupants and cargo live in the design as parts, but their masses come
    from the requirements; a design that disagrees would be judged under a
    load nobody asked for.
    """
    on = parts_on_board(design, loading)
    people = [p for p in on if p["kind"] == "occupant"]
    cargo = [p for p in on if p["kind"] == "cargo"]
    problems = []
    if len(people) != OCCUPANTS[loading]:
        problems.append(f"loading {loading} needs {OCCUPANTS[loading]} occupant parts, the design has {len(people)}")
    if people:
        m = requirement(req, "Q33", "kg")["value"]
        off = [f"{p['id']} {p['mass_kg']:g} kg" for p in people if not math.isclose(p["mass_kg"], m, rel_tol=1e-6)]
        if off:
            problems.append(f"occupant parts must weigh Q33 = {m:g} kg: {', '.join(off)}")
    if CARGO[loading]:
        m = requirement(req, "Q40", "kg")["value"]
        total = sum(p["mass_kg"] for p in cargo)
        if not cargo or not math.isclose(total, m, rel_tol=1e-6):
            problems.append(f"cargo parts must weigh Q40 = {m:g} kg in total, the design has {total:g} kg")
    if problems:
        raise CatalogError("the design does not carry the requirements' load: " + "; ".join(problems))


def _loading(req: Record, design: Record) -> tuple[str, list[str]]:
    loading = loading_requirement(req)
    check_design_loading(req, design, loading)
    keys = ["Q30", "Q33"] if OCCUPANTS[loading] else ["Q30"]
    return loading, keys + (["Q40"] if CARGO[loading] else [])


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
    loading, keys = _loading(req, design)
    speed = spec["params"]["speed"]
    return {
        "load_case_id": f"P1-step{h:g}",
        "test_id": "P1",
        "domain": spec["domain"],
        "title": f"段差 {h:g} mm を{LOADING_JA[loading]}で越える",
        "params": {
            "step_height": _q(h, "mm"),
            "speed": _q(speed["value"], speed["unit"]),
            "loading": loading,
        },
        "requirement_keys": ["Q15", *keys],
        "tolerance_rel": spec["tolerance_rel"],
    }


def _r1_static(spec: Record, req: Record, design: Record) -> Record:
    h = requirement(req, "Q15", "mm")["value"]
    sf = requirement(req, "Q61", "1")["value"]
    if sf <= 0:
        raise CatalogError(f"Q61 safety factor must be positive, got {sf}")
    loading, keys = _loading(req, design)
    y, names, verified = min_frame_yield(design)
    note = "" if verified else ", not checked"
    return {
        "load_case_id": f"R1-step{h:g}",
        "test_id": "R1",
        "domain": spec["domain"],
        "title": f"段差 {h:g} mm を{LOADING_JA[loading]}で越えるときの前輪の力をフレームに",
        "params": {"step_height": _q(h, "mm"), "loading": loading},
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
