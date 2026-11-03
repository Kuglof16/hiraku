"""The tool shelf: load the solvers and check the tools a run can use before they can be used.

A real run computes every check (the analysis department's K2 checks keep a disk cache only for
the tests, HIRAKU_CHECK_CACHE=1 in tests/__init__.py; checks_from_cache says when it was used), so
the run checks only the tools its tests can use (tools_for_tests)."""

from __future__ import annotations

import importlib
import re
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
        raise SolversMissing(f"{module} cannot be imported ({e})") from None
    missing = [n for n in SOLVER_ENTRY_POINTS if not hasattr(mod, n)]
    if missing:
        raise SolversMissing(f"{module} does not provide {', '.join(missing)}()")
    tools = mod.builtin_tools()
    if not isinstance(tools, dict):
        tools = {t.name: t for t in tools}
    return mod.formula_registry(), tools


DESIGNGEN_MODULE = "hiraku.designgen"


def load_designgen(module: str = DESIGNGEN_MODULE) -> Any:
    """hiraku.designgen (variables(design), regenerate(design, changes)), or None while it is absent."""
    try:
        mod = importlib.import_module(module)
    except ModuleNotFoundError as e:
        if e.name == module:
            return None
        raise
    return mod if hasattr(mod, "variables") and hasattr(mod, "regenerate") else None


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


NOT_CHECKED = "この実行の試験には使わないので確かめていない"


def test_family(test_id: str) -> str:
    """The test a variant belongs to: S1' and S1 -> S1, R1s -> R1, S4b -> S4."""
    m = re.match(r"[A-Z]+\d+", test_id)
    return m.group(0) if m else test_id


def tools_for_tests(tools: dict[str, AnalysisTool], test_ids: list[str] | None) -> list[str]:
    """The tools a run with these tests can use: those whose `tests` share a test (or a variant of
    one, test_family) with them. test_ids None (the run's tests are not fixed yet): every tool.
    A tool that does not say its tests is kept."""
    if test_ids is None:
        return list(tools)
    wanted = {test_family(t) for t in test_ids}
    out = []
    for name, tool in tools.items():
        tests = getattr(tool, "tests", None)
        if tests is None or any(test_family(t) in wanted for t in tests):
            out.append(name)
    return out


def unchecked_tools(ctx: RunContext) -> list[str]:
    """Tools on the run's shelf that were not checked for this run (no spec): not usable (HK2)."""
    return [n for n in ctx.tools if n not in ctx.specs]


def check_shelf(ctx: RunContext, test_ids: list[str] | None = None) -> dict[str, Record]:
    """Check the tools the run's tests can use (tools_for_tests; all of them when test_ids is None)
    and emit tool.checked for each; the others are named in one agent.said and stay without a
    spec, so analyze refuses them (HK2) and list_tools says why (NOT_CHECKED). Once the stop
    button's stop.request is in the run folder the rest are not checked: the run is about to stop."""
    names = tools_for_tests(ctx.tools, test_ids)
    left = [n for n in ctx.tools if n not in names]
    if left:
        ctx.store.emit("agent.said", agent="system",
                       text=f"この実行の試験 ({'・'.join(test_ids or [])}) には使わないので確かめない道具: "
                            + "、".join(left))
    for name in names:
        tool = ctx.tools[name]
        if ctx.store.stop_requested():
            break
        spec = check_tool(tool)
        ctx.specs[name] = spec
        ok = "使えます" if spec["usable"] else "使えません (答えの分かっている例に通らない)"
        ctx.store.emit("tool.checked", agent="system", domain=spec["domain"],
                       text=f"道具 {name} を確かめました: {ok}", data=spec)
        if getattr(tool, "checks_from_cache", False):  # the tests' disk cache (HIRAKU_CHECK_CACHE=1), never a real run
            ctx.store.emit("agent.said", agent="system",
                           text=f"道具 {name} の照合は、試験のために覚えておいた前の結果を使いました")
    return ctx.specs
