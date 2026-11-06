"""K5: contribution and sensitivity.

K0 formulas already return their terms (sympy elasticities or additive
shares). For any CAE tool, sensitivity() moves one number of the design up and
down by a small relative step, runs the tool again, and returns the
elasticity d ln y / d ln x by central differences. When the two one-sided
slopes disagree (contact in K2 makes results jump), the term says the
sensitivity is lost in noise instead of giving a number to trust.

A design variable (variables.<name>) is moved by its value alone: the design is
not rebuilt, so the parts, sections and supports the generator made from it stay
as they were. A result that does not move is then not a sensitivity of 0: the
term says it was not looked at (no share). A result that moves gives the
sensitivity to the value alone (what the tool reads of the variable itself).

Paths name numbers inside the design, e.g.
    variables.motor_peak_power            (a quantity: its value)
    sections.rt40x80x2.3.dims_mm.t        (dots inside keys are matched greedily)
    materials.stkr400.E_MPa
    parts.cargo.mass_kg                    (list items by id)
    parts.cargo.box.center_mm.2            (list items by index)
    supports.FL.suspension.stiffness_N_per_mm
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any

Record = dict[str, Any]

NOISE_RATIO = 0.3  # one-sided slopes further apart than this (relative) are called noisy
FLAT_REL = 1e-9  # a result that moves less than this (relative) does not depend on the number

REBUILDS_DESIGN = True  # sensitivity() builds the design again for a variable when the caller passes rebuild
VARIABLES = "variables."  # paths to design variables: the generator builds the design from them
_BUILT: dict[tuple, Record] = {}  # (rebuild, design fingerprint, variable, value) -> rebuilt design or refusal
_BUILT_MAX = 512
NOT_LOOKED_AT = ("not looked at: the result does not move when only the variable's value changes; the parts the "
                 "design is built from it need the design rebuilt")

_SUFFIX_UNITS ={"_mm": "mm", "_kg": "kg", "_MPa": "MPa", "_N_per_mm": "N/mm", "_Ns_per_mm": "1", "_kg_m3": "kg/m^3"}


def _step_into(node: Any, parts: list[str]) -> tuple[Any, Any]:
    """(container, key) for the last part of a path, following list ids and greedy dotted keys."""
    i = 0
    while i < len(parts) - 1:
        node, i = _child(node, parts, i)
    key = parts[-1]
    if isinstance(node, list):
        key = _list_index(node, key)
    elif key not in node:
        raise KeyError(".".join(parts))
    return node, key


def _child(node: Any, parts: list[str], i: int) -> tuple[Any, int]:
    if isinstance(node, list):
        return node[_list_index(node, parts[i])], i + 1
    for j in range(len(parts) - 1, i, -1):  # longest key first, so "rt40x80x2.3" works
        k = ".".join(parts[i:j])
        if k in node:
            return node[k], j
    raise KeyError(".".join(parts[: i + 1]))


def _list_index(node: list, key: str) -> int:
    for n, item in enumerate(node):
        if isinstance(item, dict) and item.get("id") == key:
            return n
    if key.isdigit() and int(key) < len(node):
        return int(key)
    raise KeyError(key)


def _resolve(design: Record, path: str) -> tuple[Any, Any, str | None]:
    parts = path.split(".")
    node, key = _step_into(design, parts)
    value = node[key]
    unit = None
    if isinstance(value, dict) and "value" in value:  # a quantity
        node, key, unit = value, "value", value.get("unit")
    else:
        for suffix, u in _SUFFIX_UNITS.items():
            if any(p.endswith(suffix) for p in parts):
                unit = u
    return node, key, unit


def get_value(design: Record, path: str) -> tuple[float, str | None]:
    node, key, unit = _resolve(design, path)
    return float(node[key]), unit


def with_value(design: Record, path: str, value: float) -> Record:
    out = copy.deepcopy(design)
    node, key, _ = _resolve(out, path)
    node[key] = float(value)
    return out


def _quantity(rec: Record, quantity: str) -> float:
    if rec.get("status") != "ok":
        raise ValueError(f"the tool run failed: {rec.get('error')}")
    for r in rec["results"]:
        if r["quantity"] == quantity:
            return float(r["value"])
    raise ValueError(f"the tool gives no result named {quantity!r}")


def _fingerprint(design: Record) -> str:
    return hashlib.sha1(json.dumps(design, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _built(rebuild, design: Record, fp: str, name: str, value: float | None, unit: str | None) -> Record:
    """rebuild(design, {name: value}) (or {} when name is ""), kept for the next call on the same design, so a
    caller asking load case after load case builds each (variable, value) once. A refusal (ValueError) is kept too."""
    key = (rebuild, fp, name, value if value is not None else -math.inf)  # the function too: callers differ
    got = _BUILT.get(key)
    if got is None:
        try:
            got = rebuild(design, {name: {"value": value, "unit": unit}} if name else {})
        except (KeyError, ValueError) as e:
            got = ValueError(str(e))
        if len(_BUILT) >= _BUILT_MAX:
            _BUILT.pop(next(iter(_BUILT)))
        _BUILT[key] = got
    if isinstance(got, ValueError):
        raise ValueError(str(got))
    return got


def _rebuildable(rebuild, design: Record, fp: str, name: str, x0: float, unit: str | None) -> bool:
    """Whether `rebuild` builds the design from this variable (it accepts the variable at its present value)."""
    try:
        _built(rebuild, design, fp, name, x0, unit)
    except ValueError:  # not a variable the generator builds from, or one it may not change
        return False
    return True


def sensitivity(tool, design: Record, load_case: Record, quantity: str, paths: list[str],
                rel_step: float = 0.02, params: dict | None = None, predictions: list | None = None,
                rebuild=None, heavy: bool = False) -> list[Record]:
    """Elasticity of one result to each design number, as contract `term` records
    (value = the number, share = elasticity). Raises ValueError when the base run fails.

    rebuild(design, {name: {value, unit}}) -> design: the caller's way to build the design again with one
    variable changed (K5 is below the generator's layer, so it does not import it; hiraku.designgen.regenerate
    fits). With it, a variable the generator builds from is moved by building the design at x0 +- h, and the
    elasticity is about the rebuilt design at x0 (its result taken once); a variable the rebuild refuses (an
    outside or fixed value) and every other number are moved by value, as without it. A tool of stage 3 or more
    (shells and other heavy ones) is not run on rebuilt designs unless `heavy` is true. When the rebuild refuses
    one side (the range ends there), the elasticity comes from the other side alone and its name says so."""
    preds = predictions or [{"prediction_id": "pred-0000"}]
    base = dict(params or {})
    base.pop("sensitivity", None)

    def y(d: Record) -> float:
        return _quantity(tool.run(d, load_case, preds, base), quantity)

    y0 = y(design)
    rebuilt_y0: list[float] = []  # the rebuilt design's result at x0, taken once
    is_heavy = int(getattr(tool, "stage", 1) or 1) >= 3
    fp = _fingerprint(design) if rebuild is not None else ""
    terms = []
    for path in paths:
        x0, unit = get_value(design, path)
        term: Record = {"name": f"{quantity} to {path}", "value": x0}
        if unit:
            term["unit"] = unit
        variable = path.startswith(VARIABLES)
        name = path[len(VARIABLES):]
        by_rebuild = variable and rebuild is not None and _rebuildable(rebuild, design, fp, name, x0, unit)
        if by_rebuild and is_heavy and not heavy:
            term["name"] += (" (not looked at: the design must be rebuilt for it, and the tool is a heavy one; "
                             "heavy = true runs it)")
            terms.append(term)
            continue
        if x0 == 0 or y0 == 0:
            term["name"] += " (no elasticity at zero)"
            terms.append(term)
            continue
        h = rel_step * abs(x0)
        one_sided = None
        try:
            if by_rebuild:
                if not rebuilt_y0:
                    rebuilt_y0.append(y(_built(rebuild, design, fp, "", None, None)))
                yc = rebuilt_y0[0]
                side = {}
                for sgn in (1.0, -1.0):
                    try:
                        side[sgn] = y(_built(rebuild, design, fp, name, x0 + sgn * h, unit))
                    except ValueError as e:  # the rebuild refuses this side (a range end) or the run fails there
                        side[sgn] = None
                        one_sided = str(e)
                if side[1.0] is None and side[-1.0] is None:
                    raise ValueError(f"the design cannot be rebuilt on either side: {one_sided}")
                yp = side[1.0] if side[1.0] is not None else yc + (yc - side[-1.0])
                ym = side[-1.0] if side[-1.0] is not None else yc - (side[1.0] - yc)
            else:
                yc = y0
                yp = y(with_value(design, path, x0 + h))
                ym = y(with_value(design, path, x0 - h))
        except ValueError as e:
            term["name"] += f" (not computed: {e})"
            terms.append(term)
            continue
        if yc == 0:
            term["name"] += " (no elasticity at zero)"
            terms.append(term)
            continue
        sp, sm = (yp - yc) / h, (yc - ym) / h
        big = max(abs(sp), abs(sm))
        if big * h <= FLAT_REL * abs(yc):
            if variable and not by_rebuild:
                # with_value changes the variable's own value only; the parts, sections and supports the
                # generator built from it stay as they were, so a flat result says nothing (not a zero)
                term["name"] += f" ({NOT_LOOKED_AT})"
            else:
                term["share"] = 0.0  # the result does not move (round-off only)
        elif sp * sm < 0 or abs(sp - sm) > NOISE_RATIO * big:
            term["name"] += " (lost in noise: the two one-sided slopes disagree)"
        else:
            # a term with a share keeps its name "<quantity> to <path>" as it is: the screen matches the path
            term["share"] = (yp - ym) / (2.0 * h) * x0 / yc
            if one_sided is not None:
                term["name"] += f" (one side only: the rebuild refused the other: {one_sided})"
        terms.append(term)
    return terms


def strongest(terms: list[Record]) -> Record | None:
    """The term with the largest elasticity, the first thing to change."""
    good = [t for t in terms if "share" in t and math.isfinite(t["share"])]
    return max(good, key=lambda t: abs(t["share"])) if good else None
