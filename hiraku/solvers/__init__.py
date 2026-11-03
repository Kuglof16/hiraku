"""Solvers (解析部): K0 hand calculation and the CAE tools K1..K5.

The two entry points (hiraku.contract.protocols.SOLVER_ENTRY_POINTS):

    from hiraku import solvers
    formulas = solvers.formula_registry()   # FormulaRegistry
    tools = solvers.builtin_tools()         # list of AnalysisTool

The optional one (OPTIONAL_SOLVER_ENTRY_POINTS):

    mesh = solvers.frame_mesh(design)       # shell mesh record without mesh_id

Importing this package does nothing else; the solvers load on the first call.
Tools from hiraku.solvers.k2_vehicle are added when that package exposes
`tools()` returning AnalysisTool objects. Names on the shelf are unique: no
tool replaces another, and tests/solvers fails when two tools share a name.
"""

from __future__ import annotations

import importlib
from functools import lru_cache


def formula_registry():
    """The K0 formula registry (hiraku.contract.protocols.FormulaRegistry)."""
    from .k0_handcalc import registry

    return registry()


@lru_cache(maxsize=1)
def _tools() -> tuple:
    from .shelf import BUILTIN_TOOLS

    return tuple([cls() for cls in BUILTIN_TOOLS] + _k2_tools())


def builtin_tools() -> list:
    """Every analysis tool on the shelf (hiraku.contract.protocols.AnalysisTool)."""
    return list(_tools())


def frame_mesh(design: dict) -> dict:
    """The shell mesh of the design's frame as a contract `mesh` record without mesh_id
    (the optional entry point, hiraku.contract.protocols.OPTIONAL_SOLVER_ENTRY_POINTS)."""
    from .k1_shell import build_mesh

    rec = build_mesh(design).record("mesh-0000", int(design.get("design_version", 1)))
    del rec["mesh_id"]
    return rec


def _k2_tools() -> list:
    try:
        k2 = importlib.import_module(".k2_vehicle", __name__)
    except ModuleNotFoundError as e:
        if e.name and e.name.endswith("k2_vehicle"):
            return []
        raise
    make = getattr(k2, "tools", None)
    return list(make()) if callable(make) else []


__all__ = ["builtin_tools", "formula_registry", "frame_mesh"]
