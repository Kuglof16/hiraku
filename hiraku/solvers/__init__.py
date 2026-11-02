"""Solvers (解析部): K0 hand calculation and the CAE tools K1..K5.

Entry point for the agent side (設計部):

    from hiraku import solvers
    shelf = solvers.registry()
    shelf.formulas.list_formulas()            # FormulaRegistry
    shelf.formulas.evaluate(formula_id, inputs)
    shelf.tools["frame_static"].spec()        # AnalysisTool
    shelf.tools["step_climb"].run(design, load_case, predictions, params)

Tools from hiraku.solvers.k2_vehicle are picked up when that package exposes
`tools()` returning AnalysisTool objects; a K2 tool replaces a built-in tool
of the same name (step_climb moves to K2 that way).
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from functools import lru_cache

from .k0_handcalc import Registry
from .k0_handcalc import registry as formula_registry


@dataclass
class Shelf:
    formulas: Registry
    tools: dict = field(default_factory=dict)

    def tool(self, name: str):
        try:
            return self.tools[name]
        except KeyError:
            raise ValueError(f"unknown tool {name!r}; on the shelf: {', '.join(sorted(self.tools))}") from None

    def list_tools(self) -> list[dict]:
        """Every tool's `tool_spec` record (runs the known-answer checks once)."""
        return [t.spec() for t in self.tools.values()]


def _k2_tools() -> list:
    try:
        k2 = importlib.import_module(".k2_vehicle", __name__)
    except ModuleNotFoundError as e:
        if e.name and e.name.endswith("k2_vehicle"):
            return []
        raise
    make = getattr(k2, "tools", None)
    return list(make()) if callable(make) else []


@lru_cache(maxsize=1)
def registry() -> Shelf:
    from .shelf import BUILTIN_TOOLS

    tools = {}
    for cls in BUILTIN_TOOLS:
        t = cls()
        tools[t.name] = t
    for t in _k2_tools():
        tools[t.name] = t
    return Shelf(formula_registry(), tools)


__all__ = ["Shelf", "registry"]
