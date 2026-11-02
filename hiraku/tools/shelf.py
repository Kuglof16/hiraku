"""The tool shelf: load the solvers and check every tool before it can be used."""

from __future__ import annotations

import importlib
from typing import Any

from hiraku import contract
from hiraku.contract.protocols import SOLVER_ENTRY_POINTS, AnalysisTool, FormulaRegistry
from hiraku.tools.context import RunContext

Record = dict[str, Any]

SOLVERS_MODULE = "hiraku.solvers"


class SolversMissing(RuntimeError):
    pass


def load_solvers(module: str = SOLVERS_MODULE) -> tuple[FormulaRegistry, dict[str, AnalysisTool]]:
    """The K0 formula registry and the builtin analysis tools.

    Expects `hiraku.solvers` to provide formula_registry() -> FormulaRegistry
    and builtin_tools() -> list[AnalysisTool] (or a dict by name).
    """
    try:
        mod = importlib.import_module(module)
    except ModuleNotFoundError as e:
        raise SolversMissing(f"{module} is not here yet ({e}). The analysis department writes it.") from None
    missing = [n for n in SOLVER_ENTRY_POINTS if not hasattr(mod, n)]
    if missing:
        raise SolversMissing(f"{module} does not provide {', '.join(missing)}()")
    tools = mod.builtin_tools()
    if not isinstance(tools, dict):
        tools = {t.name: t for t in tools}
    return mod.formula_registry(), tools


def check_tool(tool: AnalysisTool) -> Record:
    """Run a tool's known-answer cases and return its spec. Code decides `usable`:
    at least one case, and every case passes."""
    try:
        checks = list(tool.check())
        error = None
    except Exception as e:
        checks, error = [], f"{type(e).__name__}: {e}"
    spec = dict(tool.spec())
    spec["name"] = tool.name
    spec.setdefault("origin", "builtin")
    spec["checks"] = checks
    spec["usable"] = bool(checks) and all(c["pass"] for c in checks) and error is None
    contract.validate("tool_spec", spec)
    return spec


def check_shelf(ctx: RunContext) -> dict[str, Record]:
    """Check every tool on the shelf and emit tool.checked for each."""
    for name, tool in ctx.tools.items():
        spec = check_tool(tool)
        ctx.specs[name] = spec
        ok = "使えます" if spec["usable"] else "使えません (答えの分かっている例に通らない)"
        ctx.store.emit("tool.checked", agent="system", domain=spec["domain"],
                       text=f"道具 {name} を確かめました: {ok}", data=spec)
    return ctx.specs
