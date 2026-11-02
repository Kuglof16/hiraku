"""K0: hand calculation. Units, the formula ledger, section properties and
rigid-body statics of a design."""

from __future__ import annotations

from functools import lru_cache

from .registry import Formula, Registry


@lru_cache(maxsize=1)
def registry() -> Registry:
    """The shared formula registry (hiraku.contract.protocols.FormulaRegistry)."""
    from .library import all_formulas

    return Registry(all_formulas())


__all__ = ["Formula", "Registry", "registry"]
