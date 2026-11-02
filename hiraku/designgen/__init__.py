"""Designs built from their variables, for revise_design.

    from hiraku import designgen
    gen = designgen.generator("offroad-4w")
    gen.spec()                      # [{name, unit, default, min, max, revisable, meaning}]
    v1 = gen.build()                # the defaults: data/designs/offroad-4w-v1.json
    v2 = gen.build({**gen.values_of(v1), "rail_spacing": {"value": 580, "unit": "mm"}},
                   version=2, parent=1, reason="...")

build() raises DesignGenError (a ValueError) for a value out of its range or a
layout that does not close; interference between parts is left to the K4 tools.
"""

from __future__ import annotations

from types import ModuleType

from hiraku.designgen import ladder_4w
from hiraku.designgen.ladder_4w import DesignGenError

GENERATORS: dict[str, ModuleType] = {"offroad-4w": ladder_4w}


def generator(product: str) -> ModuleType:
    try:
        return GENERATORS[product]
    except KeyError:
        raise DesignGenError(f"no design generator for product {product!r}; known: {', '.join(GENERATORS)}") from None


__all__ = ["GENERATORS", "DesignGenError", "generator", "ladder_4w"]
