"""The tool shelf: load the solvers and check the tools a run can use before they can be used.

The run checks only the tools its tests can use (tools_for_tests). A check is reused from an
earlier run in the same runs folder while the hiraku package's source is unchanged
(docs/contract.md 照合の使い回し: source_hash over every .py, kept per tool in a TOOL_CHECK_FILE;
each check then says code_hash and cached_from). Tools made in a run, and tools whose code is not
in the hiraku package (the hash would not cover it), are always checked again. (The analysis
department's K2 checks also keep a disk cache for the tests only, HIRAKU_CHECK_CACHE=1 in
tests/__init__.py; checks_from_cache says when it was used.)"""

from __future__ import annotations

import functools
import hashlib
import importlib
import json
import os
import re
from pathlib import Path
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


HIRAKU_DIR = Path(__file__).resolve().parents[1]  # the hiraku package
# one file per tool in the runs folder (runs/ is local, not committed); a file, so the listings of run
# folders (the screen, qa) that keep only directories are not touched
TOOL_CHECK_FILE = ".tool-check-{hash}-{tool}.json"
TOOL_CHECK_GLOB = ".tool-check-*.json"
HASH_CHARS = 16  # of the sha256 hex digest, in the file name


@functools.lru_cache(maxsize=1)
def source_hash() -> str:
    """sha256 of the hiraku package's source as this process found it: every .py file under hiraku/
    (outside __pycache__), by its path relative to the package and its bytes."""
    h = hashlib.sha256()
    for p in sorted(HIRAKU_DIR.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        h.update(p.relative_to(HIRAKU_DIR).as_posix().encode("utf-8") + b"\0")
        h.update(p.read_bytes() + b"\0")
    return h.hexdigest()


def reusable(tool: AnalysisTool) -> bool:
    """A builtin tool whose code is in the hiraku package, so source_hash covers it."""
    return type(tool).__module__.startswith("hiraku.") and tool.spec().get("origin", "builtin") == "builtin"


def _check_file(folder: Path, code: str, name: str) -> Path:
    return folder / TOOL_CHECK_FILE.format(hash=code[:HASH_CHARS], tool=name)


def reused_check(folder: Path, code: str, name: str) -> tuple[Record, str] | None:
    """(spec, run_id) of an earlier run's check of the tool on the same source, or None."""
    try:
        kept = json.loads(_check_file(folder, code, name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    spec, run_id = kept.get("spec"), kept.get("run_id")
    if not isinstance(spec, dict) or not run_id or kept.get("code_hash") != code or contract.errors("tool_spec", spec):
        return None
    return spec, run_id


def keep_check(folder: Path, code: str, name: str, run_id: str, spec: Record) -> None:
    """Keep the tool's check for later runs on the same source; files of other sources are removed."""
    for old in folder.glob(TOOL_CHECK_GLOB):
        if not old.name.startswith(f".tool-check-{code[:HASH_CHARS]}-"):
            old.unlink(missing_ok=True)
    path = _check_file(folder, code, name)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"code_hash": code, "run_id": run_id, "spec": spec}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def check_shelf(ctx: RunContext, test_ids: list[str] | None = None) -> dict[str, Record]:
    """Check the tools the run's tests can use (tools_for_tests; all of them when test_ids is None)
    and emit tool.checked for each; the others are named in one agent.said and stay without a
    spec, so analyze refuses them (HK2) and list_tools says why (NOT_CHECKED). A check of the same
    source from an earlier run in the same runs folder is reused (reused_check). Once the stop
    button's stop.request is in the run folder the rest are not checked: the run is about to stop."""
    names = tools_for_tests(ctx.tools, test_ids)
    left = [n for n in ctx.tools if n not in names]
    if left:
        ctx.store.emit("agent.said", agent="system",
                       text=f"この実行の試験 ({'・'.join(test_ids or [])}) には使わないので確かめない道具: "
                            + "、".join(left))
    code = source_hash()
    folder = ctx.store.path.parent
    for name in names:
        tool = ctx.tools[name]
        if ctx.store.stop_requested():
            break
        got = reused_check(folder, code, name) if reusable(tool) else None
        if got is not None:
            spec, run_id = got
            spec = dict(spec, checks=[dict(c, code_hash=code, cached_from=run_id) for c in spec["checks"]])
        else:
            spec = check_tool(tool)
            spec["checks"] = [dict(c, code_hash=code) for c in spec["checks"]]
            if reusable(tool):
                keep_check(folder, code, name, ctx.project["run_id"], spec)
        ctx.specs[name] = spec
        ok = "使えます" if spec["usable"] else "使えません (答えの分かっている例に通らない)"
        how = f" (ソースが同じ前の実行 {got[1]} の照合を使いました)" if got is not None else ""
        ctx.store.emit("tool.checked", agent="system", domain=spec["domain"],
                       text=f"道具 {name} を確かめました: {ok}{how}", data=spec)
        if getattr(tool, "checks_from_cache", False):  # the tests' disk cache (HIRAKU_CHECK_CACHE=1), never a real run
            ctx.store.emit("agent.said", agent="system",
                           text=f"道具 {name} の照合は、試験のために覚えておいた前の結果を使いました")
    return ctx.specs
