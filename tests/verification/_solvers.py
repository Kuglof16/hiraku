"""Find the solver modules and objects the contract promises, or skip saying what is missing.

A missing module is a skip with the module name in the reason, so the summary
of `python -m unittest -v` shows what has not landed yet. A module that exists
but does not offer what protocols.py describes is a failure, not a skip.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import unittest
from typing import Any

from hiraku.contract.protocols import AnalysisTool


def import_or_skip(name: str, what: str):
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as e:
        if e.name and name.startswith(e.name):
            raise unittest.SkipTest(f"{name} is not in this checkout yet (analysis dept). Needed: {what}")
        raise


def _instances(module) -> list[Any]:
    """Objects in a module, with zero-argument classes instantiated."""
    out = []
    for _, obj in vars(module).items():
        if inspect.isclass(obj):
            if getattr(obj, "__module__", "").startswith(module.__name__):
                try:
                    out.append(obj())
                except TypeError:
                    pass
        else:
            out.append(obj)
    return out


def formula_registry(module):
    """The FormulaRegistry in a module: the module itself or one object in it."""
    if callable(getattr(module, "list_formulas", None)) and callable(getattr(module, "evaluate", None)):
        return module
    for obj in _instances(module):
        if callable(getattr(obj, "list_formulas", None)) and callable(getattr(obj, "evaluate", None)):
            return obj
    raise AssertionError(f"{module.__name__} has no list_formulas()/evaluate() (FormulaRegistry in protocols.py)")


def analysis_tools() -> dict[str, Any]:
    """Every AnalysisTool found in hiraku.solvers and its submodules, by name."""
    pkg = import_or_skip("hiraku.solvers", "the solver package with AnalysisTool objects")
    modules = [pkg]
    for info in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
        try:
            modules.append(importlib.import_module(info.name))
        except ImportError:
            continue
    tools = {}
    for m in modules:
        for obj in _instances(m):
            if not inspect.isclass(obj) and not inspect.ismodule(obj) and isinstance(obj, AnalysisTool):
                name = getattr(obj, "name", None)
                if isinstance(name, str) and name not in tools:
                    tools[name] = obj
    return tools


def tool_or_skip(name: str):
    tools = analysis_tools()
    if name not in tools:
        raise unittest.SkipTest(f"no AnalysisTool named {name!r} in hiraku.solvers yet "
                                f"(found: {', '.join(sorted(tools)) or 'none'})")
    return tools[name]
