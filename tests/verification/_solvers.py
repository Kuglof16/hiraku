"""Reach the solvers only through the contract's entry points, or skip saying what is missing.

hiraku/contract/protocols.py names two entry points of hiraku.solvers:
formula_registry() -> FormulaRegistry and builtin_tools() -> list[AnalysisTool].
A missing package is a skip with the name in the reason, so the summary of
`python -m unittest -v` shows what has not landed yet. A package that exists
but does not offer the entry points is a failure, not a skip.
"""

from __future__ import annotations

import importlib
import unittest
from typing import Any

from hiraku.contract.protocols import SOLVER_ENTRY_POINTS, AnalysisTool

SOLVERS = "hiraku.solvers"


def solvers_or_skip():
    try:
        mod = importlib.import_module(SOLVERS)
    except ModuleNotFoundError as e:
        if e.name and SOLVERS.startswith(e.name):
            raise unittest.SkipTest(f"{SOLVERS} is not in this checkout yet (analysis dept). "
                                    f"Needed: {', '.join(n + '()' for n in SOLVER_ENTRY_POINTS)}")
        raise
    missing = [n for n in SOLVER_ENTRY_POINTS if not callable(getattr(mod, n, None))]
    if missing:
        raise AssertionError(f"{SOLVERS} does not provide {', '.join(n + '()' for n in missing)} (protocols.py)")
    return mod


def formula_registry():
    reg = solvers_or_skip().formula_registry()
    for name in ("list_formulas", "evaluate"):
        assert callable(getattr(reg, name, None)), f"formula_registry() result has no {name}()"
    return reg


def analysis_tools() -> dict[str, Any]:
    """builtin_tools() by name; every one must have name/spec/check/run."""
    tools = solvers_or_skip().builtin_tools()
    if isinstance(tools, dict):  # tolerated by the agent side; the contract says list
        tools = list(tools.values())
    out = {}
    for t in tools:
        assert isinstance(t, AnalysisTool), f"{t!r} does not have name/spec/check/run"
        assert t.name and t.name not in out, f"tool name {t.name!r} empty or repeated"
        out[t.name] = t
    return out


def tool_or_skip(name: str):
    tools = analysis_tools()
    if name not in tools:
        raise unittest.SkipTest(f"no tool named {name!r} in builtin_tools() yet "
                                f"(found: {', '.join(sorted(tools)) or 'none'})")
    return tools[name]
