"""The limit of a load case Claude wrote, computed by code from its limit_from (docs/contract.md
"Claude が書く設計", hiraku.contract load_case.limit_from).

limit_from = {"expr": "yield / Q61", "unit": "MPa", "refs": {"yield": "materials.steel.yield_MPa", "Q61": "Q61"}}

Each name of expr is read through refs: a requirement key (Q01..Q99) is that requirement's value and unit;
anything else is a path in the design (materials.<key>.<field>, sections.<key>.dims_mm.<field>,
variables.<name>, parts.<id>.mass_kg ...). A design value's unit is its own when it is a {value, unit}
quantity, else the one its field name ends in (yield_MPa, dims_mm.t, density_kg_m3; nu is a pure number).
A name without a ref that is itself a requirement key reads that requirement. The expression is evaluated
by hiraku.catalog.expr.evaluate_quantity (numbers, names, + - * /, **, min, max, dimensions checked) and
converted to limit_from.unit. No number is taken from the model.
"""

from __future__ import annotations

import re
from typing import Any

from hiraku.catalog.builders.common import BuildError
from hiraku.catalog.expr import ExprError, arith_names, evaluate_quantity

Record = dict[str, Any]

REQUIREMENT_KEY = re.compile(r"^Q[0-9]{2}$")
DIMENSIONLESS_FIELDS = frozenset({"nu"})  # Poisson's ratio
# units no field name is read as: "1" and "%" are not endings, and _g (standard gravity, not gram) and _h
# (hour, not height) would misread a field; such a value is written as a {value, unit} quantity instead
AMBIGUOUS_SUFFIX_UNITS = frozenset({"1", "%", "g", "h"})


def _units() -> Any:
    from hiraku.solvers.k0_handcalc import units

    return units


def _field_suffixes() -> list[tuple[str, str]]:
    """(field-name ending, unit) for every unit of the contract's table, longest ending first: MPa -> _MPa,
    kg/m^3 -> _kg_m3, mm^2 -> _mm2, N*m -> _Nm, N/mm -> _N_mm, km/h -> _km_h."""
    out = []
    for unit in _units().UNIT_TABLE:
        if unit in AMBIGUOUS_SUFFIX_UNITS:
            continue
        out.append(("_" + unit.replace("/", "_").replace("^", "").replace("*", ""), unit))
    return sorted(out, key=lambda e: -len(e[0]))


def unit_of_field(keys: list[str]) -> str | None:
    """The unit a design path's field names say: the last key's ending, else an enclosing key's (dims_mm.t)."""
    suffixes = _field_suffixes()
    for k in reversed(keys):
        if k in DIMENSIONLESS_FIELDS:
            return "1"
        for end, unit in suffixes:
            if k.endswith(end) and len(k) > len(end):
                return unit
    return None


def _requirements(requirements: Any) -> list[Record]:
    if isinstance(requirements, dict):
        return list(requirements.get("requirements") or [])
    return list(requirements or [])


def _number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _read_requirement(reqs: list[Record], key: str, name: str) -> Record:
    r = next((r for r in reqs if r.get("key") == key), None)
    if r is None:
        raise BuildError(f"limit_from: {name} refers to requirement {key}, which this run does not have")
    if not _number(r.get("value")):
        raise BuildError(f"limit_from: requirement {key} ({name}) is {r.get('value')!r}, not a number")
    if not r.get("unit"):
        raise BuildError(f"limit_from: requirement {key} ({name}) has no unit")
    return {"value": float(r["value"]), "unit": str(r["unit"]), "ref": key}


def _child(node: Any, key: str) -> Any:
    if isinstance(node, dict):
        return node[key]
    if isinstance(node, list):
        for item in node:  # records with ids (parts, members, supports, nodes)
            if isinstance(item, dict) and item.get("id") == key:
                return item
        if key.isdigit() and int(key) < len(node):
            return node[int(key)]
    raise KeyError(key)


def _read_design(design: Record, path: str, name: str) -> Record:
    keys = path.split(".")
    if any(not k for k in keys):
        raise BuildError(f"limit_from: {name} refers to {path!r}, which is not a path (e.g. materials.steel.yield_MPa)")
    node: Any = design
    for i, k in enumerate(keys):
        try:
            node = _child(node, k)
        except (KeyError, TypeError):
            at = ".".join(keys[:i]) or "the design"
            raise BuildError(f"limit_from: {name} refers to {path!r}, but {at} has no {k!r}") from None
    if isinstance(node, dict) and "value" in node and "unit" in node:
        value, unit = node["value"], node["unit"]
    else:
        value, unit = node, unit_of_field(keys)
        if unit is None:
            raise BuildError(f"limit_from: the unit of {path!r} ({name}) is unknown (a field ending in _MPa, _mm, "
                             "_kg ... or a {value, unit} quantity)")
    if not _number(value):
        raise BuildError(f"limit_from: {path!r} ({name}) is {value!r}, not a number")
    return {"value": float(value), "unit": str(unit), "ref": path}


def _si(q: Record, name: str) -> tuple[float, tuple[int, ...]]:
    row = _units().UNIT_TABLE.get(q["unit"])
    if row is None:
        raise BuildError(f"limit_from: {name} ({q['ref']}) is in {q['unit']!r}, a unit the contract does not know")
    factor, offset, dim = row
    return q["value"] * factor + offset, dim


def limit_value(limit_from: Record, requirements: Any, design: Record) -> Record:
    """{"value", "unit", "from": {name: {"value", "unit", "ref"}}} of a written load case's limit_from;
    requirements is the requirements file (or its list), design the version's design. BuildError says why
    the limit cannot be computed (no expression, a name without a ref, an unknown requirement or path, a
    value that is not a number, a unit the contract does not know, dimensions that do not match)."""
    if not isinstance(limit_from, dict):
        raise BuildError("limit_from must be {expr, unit, refs}")
    expr = limit_from.get("expr")
    refs = limit_from.get("refs") or {}
    if not isinstance(refs, dict):
        raise BuildError("limit_from.refs must map each name of expr to a requirement key or a design path")
    try:
        names = arith_names(expr)
    except ExprError as e:
        raise BuildError(f"limit_from.expr: {e}") from None
    reqs = _requirements(requirements)
    read: dict[str, Record] = {}
    env: dict[str, tuple[float, tuple[int, ...]]] = {}
    for name in names:
        ref = refs.get(name, name if REQUIREMENT_KEY.match(name) else None)
        if not isinstance(ref, str) or not ref.strip():
            raise BuildError(f"limit_from: the name {name!r} of expr has no ref (a requirement key such as Q61 or a "
                             "design path such as materials.steel.yield_MPa)")
        ref = ref.strip()
        q = _read_requirement(reqs, ref, name) if REQUIREMENT_KEY.match(ref) else _read_design(design, ref, name)
        read[name] = q
        env[name] = _si(q, name)
    try:
        si, dim = evaluate_quantity(expr, env)
    except ExprError as e:
        raise BuildError(f"limit_from.expr: {e}") from None
    unit = limit_from.get("unit")
    if not unit:  # the first ref of the answer's dimension, or a pure number
        unit = next((q["unit"] for n, q in read.items() if env[n][1] == dim), None)
        if unit is None:
            if any(dim):
                raise BuildError(f"limit_from: the answer is {_units().describe_dimension(dim)}; give limit_from.unit")
            unit = "1"
    row = _units().UNIT_TABLE.get(unit)
    if row is None:
        raise BuildError(f"limit_from.unit {unit!r} is not a unit the contract knows")
    factor, offset, udim = row
    if udim != dim:
        u = _units()
        raise BuildError(f"limit_from: the answer is {u.describe_dimension(dim)}, which cannot be given in {unit!r} "
                         f"({u.describe_dimension(udim)})")
    return {"value": (si - offset) / factor, "unit": unit, "from": read}
