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
from hiraku.catalog.loading import CARGO, LOADING_JA, OCCUPANTS, parts_on_board, person_of
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
    """The loading to check (Q30): one of hiraku.contract.protocols.LOADINGS. Without Q30, the
    requirements file's default_loading (a one-person product: driver), else DEFAULT_LOADING."""
    for r in req["requirements"]:
        if r["key"] == "Q30":
            if r.get("value") not in LOADINGS:
                raise CatalogError(f"Q30 must be one of {', '.join(LOADINGS)}, got {r.get('value')!r}")
            return r["value"]
    return req.get("default_loading") or DEFAULT_LOADING


def check_design_loading(req: Record, design: Record, loading: str) -> None:
    """The design's occupant and cargo parts must carry the requirement values (Q33, Q40).

    Occupants and cargo live in the design as parts, but their masses come
    from the requirements; a design that disagrees would be judged under a
    load nobody asked for. A person may be split into body segments
    (hiraku.contract.loading.person_of): the people on board are counted, and
    each person's parts summed must weigh Q33.
    """
    try:
        on = parts_on_board(design, loading)
    except ValueError as e:  # occupant parts, but fewer than the loading names
        raise CatalogError(f"the design does not carry the requirements' load: {e}") from None
    people: dict[str, list[Record]] = {}
    for p in on:
        if p["kind"] == "occupant":
            people.setdefault(person_of(p), []).append(p)
    cargo = [p for p in on if p["kind"] == "cargo"]
    problems = []
    if len(people) != OCCUPANTS[loading]:
        problems.append(f"loading {loading} needs {OCCUPANTS[loading]} occupant parts (people; a person may be "
                        f"split into body segments), the design has {len(people)}")
    if people:
        m = requirement(req, "Q33", "kg")["value"]
        weights = {who: sum(p["mass_kg"] for p in parts) for who, parts in people.items()}
        off = [f"{who} {w:g} kg" for who, w in weights.items() if not math.isclose(w, m, rel_tol=1e-6)]
        if off:
            problems.append(f"each person's occupant parts must weigh Q33 = {m:g} kg in total: {', '.join(off)}")
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
    """Smallest yield stress among the materials the frame's members use (the safe side). A member
    that belongs to another part of the design (a roll bar: part = that part's id) is that part's,
    judged by its own test (R4), and left out; without such parts every member is the frame's.

    Returns (yield MPa, material names, all verified).
    """
    parts = {p["id"] for p in design.get("parts", [])}
    members = design.get("members", [])
    frame = [m for m in members if m.get("part") not in parts] or members
    used = {m["material"] for m in frame}
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
# P1 past the limit also at a step no rigid wheel climbs: higher than the front wheel radius
# (the corner's push is level from h = r up: step_climb.climb_limit). 1.1 r is HIRAKU's choice
# for the video (an assumption, no outside source).
WALL_STEP_RATIO = 1.1


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


def _p1_step(spec: Record, req: Record, design: Record) -> Record | list[Record]:
    """P1 at Q15; past the limit, at BEYOND_STEP_MM and at a wall (_p1_wall)."""
    h, suffix, prefix = _step(spec, req)
    loading, keys = _loading(req, design)
    speed = spec["params"]["speed"]
    lc = {
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
    return [lc, _p1_wall(spec, design, loading, keys, taken=(lc["load_case_id"],))] if suffix else lc


def _p1_wall(spec: Record, design: Record, loading: str, keys: list[str], taken: tuple[str, ...] = ()) -> Record:
    """P1 past the limit at a step higher than the front wheel radius (WALL_STEP_RATIO r): judged on
    climbable (1 = climbs, by the push or at speed: step_climb_dynamic's result, predicted by
    step_climb.climb_limit), which the case is meant to fail. Its id is P1-step{h}-beyond, or
    P1-wall{h}-beyond when that id is in `taken` (h rounds to BEYOND_STEP_MM, r about 227 mm)."""
    from hiraku.catalog.summary import design_summary

    r = design_summary(design).get("front_wheel_radius", {}).get("value")
    if not r:
        raise CatalogError("the design has no wheels, so a step higher than the wheel cannot be set")
    h = round(WALL_STEP_RATIO * r)
    speed = spec["params"]["speed"]
    lc_id = f"P1-step{h:g}{BEYOND_SUFFIX}"
    if lc_id in taken:
        lc_id = f"P1-wall{h:g}{BEYOND_SUFFIX}"
    return {
        "load_case_id": lc_id,
        "test_id": "P1",
        "domain": spec["domain"],
        "title": f"{BEYOND_TITLE}: 車輪の半径 {r:g} mm より高い段差 {h:g} mm を{LOADING_JA[loading]}で越える",
        "params": {"step_height": _q(h, "mm"), "speed": _q(speed["value"], speed["unit"]), "loading": loading},
        "requirement_keys": list(keys),
        "criterion": {
            "quantity": "climbable",
            "op": ">=",
            "limit": _q(1, "1"),
            "basis": f"the vehicle climbs the step (1 climbs, 0 does not); the step is {WALL_STEP_RATIO:g} times "
                     f"the front wheel radius (an assumption for the case past the limit)",
        },
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


def _r1_joint(spec: Record, req: Record, design: Record) -> Record:
    """R1s: R1's step, wheel force and limit on the frame as shells (frame_shell), judged on the IIW
    hot-spot stress of the tube walls where they meet (max_hot_spot_stress)."""
    lc = _r1_static(spec, req, design)
    h, suffix, prefix = _step(spec, req)
    lc.update(load_case_id=f"R1s-step{h:g}{suffix}", test_id="R1s",
              title=lc["title"].replace("前輪の力をフレームに", "前輪の力で、管のつなぎ目の壁"))
    lc["criterion"] = {**lc["criterion"], "quantity": "max_hot_spot_stress",
                       "basis": lc["criterion"]["basis"] + "; the hot-spot stress (IIW) at the tube joints"}
    return lc


Builder = Callable[[Record, Record, Record], Record]

# Builders named by `build:` in data/catalog/tests.yaml (this module's own).
BUILDERS: dict[str, Builder] = {
    "p1_step": _p1_step,
    "r1_static": _r1_static,
    "r1_joint": _r1_joint,
}

# Which load case feeds which (P1's wheel force goes into the R1 frame load, beams and shells).
FEEDS = {"P1": ["R1", "R1s"]}

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


def plan_steps(product: str, load_case: Record) -> list[Record]:
    """The steps hiraku.catalog.builders has for one load case: the product's own (PRODUCT_STEPS),
    the one past the limit (PLANS_BEYOND, for a load case id ending in -beyond) and the general one
    (PLANS) of its test, those that exist, in that order. A product step that cannot take the load
    case is left out."""
    mod = _builders_module()
    if mod is None:
        return []
    out = []
    own = getattr(mod, "PRODUCT_STEPS", {}).get(product)
    if own is not None:
        try:
            step = own(load_case)
        except Exception:  # a step function that does not know this load case gives no step
            step = None
        if step:
            out.append(step)
    tid = load_case.get("test_id")
    if str(load_case.get("load_case_id", "")).endswith("-beyond") and tid in getattr(mod, "PLANS_BEYOND", {}):
        out.append(mod.PLANS_BEYOND[tid])
    if tid in getattr(mod, "PLANS", {}):
        out.append(mod.PLANS[tid])
    return out


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


def _build_one(catalog: Catalog, req: Record, design: Record, tid: str, beyond: bool) -> list[Record]:
    """The test's load case, or its cases past the limit (a builder may give more than one)."""
    build = builder_for(catalog, tid)
    if build is None:
        raise CatalogError(f"test {tid} has {NO_BUILDER}")
    spec = dict(catalog.test(tid), variant=BEYOND) if beyond else catalog.test(tid)
    got = build(spec, req, design)
    cases = got if isinstance(got, list) else [got]
    for lc in cases:
        if beyond and not (is_beyond(lc) and lc["title"].startswith(BEYOND_TITLE)):
            raise CatalogError(f"the case past the limit of {tid} ({lc['load_case_id']}) must have an id ending "
                               f"{BEYOND_SUFFIX} and a title starting {BEYOND_TITLE}")
        # docs/contract.md: a case that names no loading gets the requirements file's default_loading
        if req.get("default_loading") and "loading" not in (lc.get("params") or {}):
            lc["params"] = dict(lc.get("params") or {}, loading=req["default_loading"])
    return cases


def _feeds(up: Record, down: Record) -> bool:
    """Feeds join load cases of the same kind: past the limit to past the limit, and of the same
    step height when both have one."""
    a, b = up.get("params", {}).get("step_height"), down.get("params", {}).get("step_height")
    return is_beyond(up) == is_beyond(down) and (a is None or b is None or a == b)


def build_load_cases(catalog: Catalog, req: Record, design: Record, test_ids: list[str],
                     skipped: list[tuple[str, str]] | None = None, beyond: list[str] = ()) -> list[Record]:
    """One load case per test, then the ones past the limit for each test in `beyond` (the
    catalog spec with variant "beyond"; P1 gives two). A test without a builder, or whose
    builder cannot build from these requirements and this design (CatalogError, or the
    builders' BuildError: both ValueError), raises; with `skipped` given it is
    left out and (test id, reason) is appended there. Two load cases with one id
    raise CatalogError (always). Feeds join load cases of
    the same kind (_feeds): a case past the limit feeds the downstream case past the limit
    of the same step."""
    out = []
    for tid, past in [(t, False) for t in test_ids] + [(t, True) for t in beyond]:
        try:
            out.extend(_build_one(catalog, req, design, tid, past))
        except ValueError as e:
            if skipped is None:
                raise
            skipped.append((tid, f"{BEYOND_TITLE}: {e}" if past else str(e)))
    seen: dict[str, str] = {}
    for lc in out:
        if lc["load_case_id"] in seen:
            raise CatalogError(f"two load cases have the id {lc['load_case_id']} (tests {seen[lc['load_case_id']]} "
                               f"and {lc['test_id']}); each load case needs its own id")
        seen[lc["load_case_id"]] = lc["test_id"]
    feeds_of = feeds_map()
    for lc in out:
        feeds = [d["load_case_id"] for t in feeds_of.get(lc["test_id"], []) for d in out
                 if d["test_id"] == t and _feeds(lc, d)]
        if feeds:
            lc["feeds"] = feeds
    return out
