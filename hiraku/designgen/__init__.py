"""Designs built from their variables, for revise_design.

    from hiraku import designgen
    designgen.variables(v1)         # [{name, unit, value, default, min, max, revisable, meaning}]
    v2 = designgen.regenerate(v1, {"track": {"value": 1220, "unit": "mm"}})
    # v2 is the whole design rebuilt; its version fields are v1's (the caller sets them)

regenerate() raises DesignGenError (a ValueError) saying why for a variable that
cannot be changed, a value out of its range, or a layout that does not close;
interference between parts is left to the K4 tools. generator(product).build()
with no values builds the product's first version (data/designs/offroad-4w-v1.json).
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

from hiraku.designgen import ladder_4w
from hiraku.designgen.ladder_4w import DesignGenError

Record = dict[str, Any]
GENERATORS: dict[str, ModuleType] = {"offroad-4w": ladder_4w}


def generator(product: str) -> ModuleType:
    try:
        return GENERATORS[product]
    except KeyError:
        raise DesignGenError(f"no design generator for product {product!r}; known: {', '.join(GENERATORS)}") from None


def generator_of(design: Record) -> ModuleType:
    """The generator that made a design: the one whose variables the design carries."""
    have = set(design.get("variables", {}))
    for gen in GENERATORS.values():
        if set(gen.VARIABLES) <= have:
            return gen
    raise DesignGenError("this design was not built from variables here: its variables match no design generator")


def variables(design: Record) -> list[Record]:
    """Every variable of the design's generator with its current value in the design: name,
    unit, value, default, min and max (revisable ones), revisable, meaning."""
    gen = generator_of(design)
    now = design["variables"]
    return [dict(v, value=now[v["name"]]["value"]) for v in gen.spec() if v["name"] in now]


def regenerate(design: Record, changes: dict[str, Record]) -> Record:
    """The design rebuilt with some of its variables changed ({name: {value, unit}}), the
    others as they are. design_version, parent_version, name and change_reason are copied
    from `design`; the caller sets them for the new version."""
    gen = generator_of(design)
    for name in changes:
        var = gen.VARIABLES.get(name)
        if var is None:
            raise DesignGenError(f"unknown variable {name!r}; the variables are listed by designgen.variables(design)")
        if not var.revisable:
            raise DesignGenError(f"{name} cannot be changed by revising the design: it is a requirement or a fixed "
                                 f"value ({var.meaning})")
    return gen.build({**gen.values_of(design), **changes}, version=design["design_version"],
                     parent=design.get("parent_version"), reason=design.get("change_reason"), name=design.get("name"))


__all__ = ["GENERATORS", "DesignGenError", "generator", "generator_of", "ladder_4w", "regenerate", "variables"]
