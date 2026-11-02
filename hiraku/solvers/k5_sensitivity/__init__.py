"""K5: contribution and sensitivity.

K0 formulas already return their terms (sympy elasticities or additive
shares). For any CAE tool, sensitivity() moves one number of the design up and
down by a small relative step, runs the tool again, and returns the
elasticity d ln y / d ln x by central differences. When the two one-sided
slopes disagree (contact in K2 makes results jump), the term says the
sensitivity is lost in noise instead of giving a number to trust.

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
import math
from typing import Any

Record = dict[str, Any]

NOISE_RATIO = 0.3  # one-sided slopes further apart than this (relative) are called noisy
FLAT_REL = 1e-9  # a result that moves less than this (relative) does not depend on the number

_SUFFIX_UNITS = {"_mm": "mm", "_kg": "kg", "_MPa": "MPa", "_N_per_mm": "N/mm", "_Ns_per_mm": "1", "_kg_m3": "kg/m^3"}


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


def sensitivity(tool, design: Record, load_case: Record, quantity: str, paths: list[str],
                rel_step: float = 0.02, params: dict | None = None, predictions: list | None = None) -> list[Record]:
    """Elasticity of one result to each design number, as contract `term` records
    (value = the number, share = elasticity). Raises ValueError when the base run fails."""
    preds = predictions or [{"prediction_id": "pred-0000"}]
    base = dict(params or {})
    base.pop("sensitivity", None)
    y0 = _quantity(tool.run(design, load_case, preds, base), quantity)
    terms = []
    for path in paths:
        x0, unit = get_value(design, path)
        term: Record = {"name": f"{quantity} to {path}", "value": x0}
        if unit:
            term["unit"] = unit
        if x0 == 0 or y0 == 0:
            term["name"] += " (no elasticity at zero)"
            terms.append(term)
            continue
        h = rel_step * abs(x0)
        try:
            yp = _quantity(tool.run(with_value(design, path, x0 + h), load_case, preds, base), quantity)
            ym = _quantity(tool.run(with_value(design, path, x0 - h), load_case, preds, base), quantity)
        except ValueError as e:
            term["name"] += f" (not computed: {e})"
            terms.append(term)
            continue
        sp, sm = (yp - y0) / h, (y0 - ym) / h
        big = max(abs(sp), abs(sm))
        if big * h <= FLAT_REL * abs(y0):
            term["share"] = 0.0  # the result does not move (round-off only)
        elif sp * sm < 0 or abs(sp - sm) > NOISE_RATIO * big:
            term["name"] += " (lost in noise: the two one-sided slopes disagree)"
        else:
            term["share"] = (yp - ym) / (2.0 * h) * x0 / y0
        terms.append(term)
    return terms


def strongest(terms: list[Record]) -> Record | None:
    """The term with the largest elasticity, the first thing to change."""
    good = [t for t in terms if "share" in t and math.isfinite(t["share"])]
    return max(good, key=lambda t: abs(t["share"])) if good else None
