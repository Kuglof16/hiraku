"""Build load cases from the requirements and the design.

Every number a load case carries (step height, masses, the stress limit) is
taken from a requirement or the design, or computed here. The model never
sets a limit or a tolerance.
"""

from __future__ import annotations

import importlib
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
    try:
        on = parts_on_board(design, loading)
    except ValueError as e:  # occupant parts, but fewer than the loading names
        raise CatalogError(f"the design does not carry the requirements' load: {e}") from None
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


def loading_for(req: Record, design: Record) -> tuple[str, list[str]]:
    """The loading to check (Q30), after checking the design carries it; and the requirement keys it uses."""
    loading = loading_requirement(req)
    check_design_loading(req, design, loading)
    keys = ["Q30", "Q33"] if OCCUPANTS[loading] else ["Q30"]
    return loading, keys + (["Q40"] if CARGO[loading] else [])


_loading = loading_for
quantity = _q


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


# Past the limit (requirements_file `beyond`): the catalog spec carries variant "beyond", the
# load case id ends in BEYOND_SUFFIX and its title starts with BEYOND_TITLE.
BEYOND = "beyond"
BEYOND_SUFFIX = "-beyond"
BEYOND_TITLE = "限界の外"
# P1 and R1 past the limit: a step of this height. HIRAKU's choice for the video (an assumption,
# no outside source); on the demo vehicle it puts the R1 frame stress past its limit.
BEYOND_STEP_MM = 250.0


def is_beyond(load_case: Record) -> bool:
    return load_case["load_case_id"].endswith(BEYOND_SUFFIX)


def _step(spec: Record, req: Record) -> tuple[float, str, str]:
    """(step height mm, id suffix, title prefix): Q15, or BEYOND_STEP_MM for the case past the limit."""
    h = requirement(req, "Q15", "mm")["value"]
    if spec.get("variant") != BEYOND:
        return h, "", ""
    if BEYOND_STEP_MM <= h:
        raise CatalogError(f"the step past the limit ({BEYOND_STEP_MM:g} mm) must be higher than Q15 ({h:g} mm)")
    return BEYOND_STEP_MM, BEYOND_SUFFIX, f"{BEYOND_TITLE}: "


def _p1_step(spec: Record, req: Record, design: Record) -> Record:
    h, suffix, prefix = _step(spec, req)
    loading, keys = _loading(req, design)
    speed = spec["params"]["speed"]
    return {
        "load_case_id": f"P1-step{h:g}{suffix}",
        "test_id": "P1",
        "domain": spec["domain"],
        "title": f"{prefix}段差 {h:g} mm を{LOADING_JA[loading]}で越える",
        "params": {
            "step_height": _q(h, "mm"),
            "speed": _q(speed["value"], speed["unit"]),
            "loading": loading,
        },
        "requirement_keys": ["Q15", *keys],
        "tolerance_rel": spec["tolerance_rel"],
    }


def _r1_static(spec: Record, req: Record, design: Record) -> Record:
    h, suffix, prefix = _step(spec, req)
    sf = requirement(req, "Q61", "1")["value"]
    if sf <= 0:
        raise CatalogError(f"Q61 safety factor must be positive, got {sf}")
    loading, keys = _loading(req, design)
    y, names, verified = min_frame_yield(design)
    note = "" if verified else ", not checked"
    return {
        "load_case_id": f"R1-step{h:g}{suffix}",
        "test_id": "R1",
        "domain": spec["domain"],
        "title": f"{prefix}段差 {h:g} mm を{LOADING_JA[loading]}で越えるときの前輪の力をフレームに",
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


Builder = Callable[[Record, Record, Record], Record]

# Builders named by `build:` in data/catalog/tests.yaml (this module's own).
BUILDERS: dict[str, Builder] = {
    "p1_step": _p1_step,
    "r1_static": _r1_static,
}

# Which load case feeds which (P1's wheel force goes into the R1 frame load).
FEEDS = {"P1": ["R1"]}

BUILDERS_MODULE = "hiraku.catalog.builders"
NO_BUILDER = "no load-case builder yet"


def _builders_module() -> Any:
    try:
        return importlib.import_module(BUILDERS_MODULE)
    except ModuleNotFoundError as e:
        if e.name == BUILDERS_MODULE:
            return None
        raise


def external_builders() -> tuple[dict[str, Builder], dict[str, list[str]], dict[str, Record]]:
    """(BUILDERS, FEEDS, PLANS) of hiraku.catalog.builders, keyed by test id; empty while it is absent.

    BUILDERS: test id -> fn(spec, req, design) -> load case. FEEDS: test id ->
    downstream test ids. PLANS: test id -> the scripted run's step for it
    (as a step of data/catalog/scripted.yaml, without load_case).
    """
    mod = _builders_module()
    if mod is None:
        return {}, {}, {}
    return dict(getattr(mod, "BUILDERS", {})), dict(getattr(mod, "FEEDS", {})), dict(getattr(mod, "PLANS", {}))


def beyond_plans() -> dict[str, Record]:
    """PLANS_BEYOND of hiraku.catalog.builders: the scripted steps of the cases past the limit that
    differ from PLANS (a test without one predicts as in PLANS)."""
    mod = _builders_module()
    return dict(getattr(mod, "PLANS_BEYOND", {})) if mod is not None else {}


def builder_for(catalog: Catalog, test_id: str) -> Builder | None:
    """The catalog's own builder (`build:` in tests.yaml) first, then hiraku.catalog.builders."""
    spec = catalog.test(test_id)
    if "build" in spec:
        return BUILDERS[spec["build"]]
    return external_builders()[0].get(test_id)


def feeds_map() -> dict[str, list[str]]:
    out = {k: list(v) for k, v in FEEDS.items()}
    for k, v in external_builders()[1].items():
        out.setdefault(k, [])
        out[k] += [t for t in v if t not in out[k]]
    return out


def _build_one(catalog: Catalog, req: Record, design: Record, tid: str, beyond: bool) -> Record:
    build = builder_for(catalog, tid)
    if build is None:
        raise CatalogError(f"test {tid} has {NO_BUILDER}")
    spec = dict(catalog.test(tid), variant=BEYOND) if beyond else catalog.test(tid)
    lc = build(spec, req, design)
    if beyond and not (is_beyond(lc) and lc["title"].startswith(BEYOND_TITLE)):
        raise CatalogError(f"the case past the limit of {tid} ({lc['load_case_id']}) must have an id ending "
                           f"{BEYOND_SUFFIX} and a title starting {BEYOND_TITLE}")
    return lc


def build_load_cases(catalog: Catalog, req: Record, design: Record, test_ids: list[str],
                     skipped: list[tuple[str, str]] | None = None, beyond: list[str] = ()) -> list[Record]:
    """One load case per test, then one past the limit for each test in `beyond` (the
    catalog spec with variant "beyond"). A test without a builder, or whose builder cannot
    build from these requirements and this design (CatalogError, or the
    builders' BuildError: both ValueError), raises; with `skipped` given it is
    left out and (test id, reason) is appended there. Feeds join load cases of
    the same kind: a case past the limit feeds the downstream case past the limit."""
    out = []
    for tid, past in [(t, False) for t in test_ids] + [(t, True) for t in beyond]:
        try:
            out.append(_build_one(catalog, req, design, tid, past))
        except ValueError as e:
            if skipped is None:
                raise
            skipped.append((tid, f"{BEYOND_TITLE}: {e}" if past else str(e)))
    by_test = {(lc["test_id"], is_beyond(lc)): lc["load_case_id"] for lc in out}
    feeds_of = feeds_map()
    for lc in out:
        feeds = [by_test[t, is_beyond(lc)] for t in feeds_of.get(lc["test_id"], []) if (t, is_beyond(lc)) in by_test]
        if feeds:
            lc["feeds"] = feeds
    return out
