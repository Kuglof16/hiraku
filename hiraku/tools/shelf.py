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


# HK5 (docs/contract.md, HOOK_RULES HK5_light_first): every tool has a stage, 0 layout and rules,
# 1 light (hand-calc sized models, beams, lumped thermal), 2 vehicle dynamics (time simulations),
# 3 heavy (shell and solid FE, convergence, fatigue on spectra, time-varying fields). The tool's own
# spec should say it (tool_spec.stage); this table is used only where the spec does not. It agrees
# with what the departments declared: the analysis department's shelf tools (their tool_spec.stage,
# commit 51a639a) and the K2 team's (commit 4c009c5: kc_bench 1; the time simulations 2; frame_tubes 3).
STAGE_FALLBACK: dict[str, int] = {
    "assembly": 0,            # K4 geometry: steering clearance, knee room, assembly order and interference
    "frame_static": 1,        # K1 beams
    "step_climb": 1,          # K0 statics of the step push -> K1 beams
    "bed_static": 1,          # K1 beams of the bed frame
    "actuator_linkage": 1,    # K0 linkage of the bed's actuators
    "modal": 1,               # natural frequencies of the frame on beams
    "crash": 1,               # 1-D mass and spring, then K1 with inertia relief
    "drive_energy": 1,        # K3 lumped power circuit on a loop
    "thermal": 1,             # K3 lumped thermal circuit
    "tilt": 2,                # K2 vehicle model
    "crosswind": 2,
    "braking": 2,
    "launch": 2,
    "lane_change": 2,
    "offroad_roll": 2,
    "step_climb_dynamic": 2,
    "kc_bench": 1,            # K2: the suspension K&C bench, quasi-static
    "frame_shell": 3,         # shell FE (MITC4), hot spots
    "frame_tubes": 3,         # shell FE tube by tube with ties
    "fatigue": 3,             # rainflow on a road spectrum, Miner
}
DEFAULT_STAGE = 1  # tool_spec.stage: missing means 1


def tool_stage(ctx: RunContext, tool: str) -> int:
    """The tool's stage (HK5): its spec's `stage` (the checked spec, else the tool's own spec()),
    else STAGE_FALLBACK, else DEFAULT_STAGE. Without the tools (the plugin's hook command) the run's
    server worked it out (ctx.stages)."""
    spec = ctx.specs.get(tool) or {}
    if "stage" in spec:
        return int(spec["stage"])
    obj = ctx.tools.get(tool)
    if obj is not None:
        try:
            own = obj.spec().get("stage")
        except Exception:  # a tool made in the run may describe itself badly
            own = None
        if own is not None:
            return int(own)
    kept = (getattr(ctx, "stages", None) or {}).get("tools") or {}
    if tool in kept:
        return int(kept[tool])
    return STAGE_FALLBACK.get(tool, DEFAULT_STAGE)


def _gives(tool: AnalysisTool | None, quantity: str) -> bool | None:
    """Whether the tool gives a result to judge this quantity on (describe()["predicts"], a name with
    <...> standing for any part, or judged_as); None when the tool does not describe itself."""
    describe = getattr(tool, "describe", None)
    if not callable(describe):
        return None
    try:
        d = describe()
    except Exception:
        return None
    names = [p.get("quantity", "") for p in d.get("predicts") or [] if isinstance(p, dict)]
    names += list((d.get("judged_as") or {}).values())
    return any(re.fullmatch(".+".join(re.escape(part) for part in re.split(r"<[^>]*>", n)), quantity)
               for n in names if n)


STAGE_NAMES_JA = {0: "レイアウトと規則", 1: "軽い計算", 2: "車の動き", 3: "重い計算"}


def load_case_stage(ctx: RunContext, load_case: Record) -> int | None:
    """The lowest stage among the usable tools on the shelf whose tests cover the load case's test
    (test_family) and that give the quantity its criterion judges (_gives; all covering tools when
    none says so). None when no usable tool covers it (no tool checks it, so HK5 does not wait for
    it). Without the tools (the plugin's hook command) the run's server worked it out (ctx.stages)."""
    kept = ((getattr(ctx, "stages", None) or {}).get("cases") or {})
    if not ctx.tools and load_case["load_case_id"] in kept:
        got = kept[load_case["load_case_id"]]
        return None if got is None else int(got)
    fam = test_family(load_case["test_id"])
    covering = [n for n, s in ctx.specs.items()
                if s.get("usable") and any(test_family(t) == fam for t in s.get("tests") or [])]
    q = (load_case.get("criterion") or {}).get("quantity")
    if q:
        giving = [n for n in covering if _gives(ctx.tools.get(n), q) is not False]
        covering = giving or covering
    return min((tool_stage(ctx, n) for n in covering), default=None)


def stages_of(ctx: RunContext) -> Record:
    """{"tools": name -> stage, "cases": load case id -> stage}, for a context without the tools."""
    return {"tools": {n: tool_stage(ctx, n) for n in set(ctx.specs) | set(ctx.tools)},
            "cases": {lc["load_case_id"]: load_case_stage(ctx, lc) for lc in ctx.load_cases}}


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


REUSE_ENV = "HIRAKU_TOOL_CHECK_REUSE"  # "0": every check runs (a run compared byte for byte with another)


def reusable(tool: AnalysisTool) -> bool:
    """A builtin tool whose code is in the hiraku package, so source_hash covers it; never with
    HIRAKU_TOOL_CHECK_REUSE=0."""
    if os.environ.get(REUSE_ENV) == "0":
        return False
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


NOT_YET = "試験がまだ決まっていないので、まだ確かめていない"
DEFERRED_TEXT = "この実行の試験はまだ決まっていないので、道具は版 1 の試験が決まってから、その試験に使う分だけ確かめます"


def defer_shelf(ctx: RunContext) -> None:
    """A run whose tests are not fixed at the start (a products file: Claude picks the domains): no
    tool is checked yet; propose_design checks the tools of the first version's tests."""
    ctx.store.emit("agent.said", agent="system", text=DEFERRED_TEXT)


def check_shelf(ctx: RunContext, test_ids: list[str] | None = None, *, skip_checked: bool = False) -> dict[str, Record]:
    """Check the tools the run's tests can use (tools_for_tests; all of them when test_ids is None)
    and emit tool.checked for each; the others are named in one agent.said and stay without a
    spec, so analyze refuses them (HK2) and list_tools says why (NOT_CHECKED). A check of the same
    source from an earlier run in the same runs folder is reused (reused_check). skip_checked: tools
    the run checked before are left as they are (the first version's tests after defer_shelf). Once
    the stop button's stop.request is in the run folder the rest are not checked: the run is about to stop."""
    names = [n for n in tools_for_tests(ctx.tools, test_ids) if not (skip_checked and n in ctx.specs)]
    left = [n for n in ctx.tools if n not in names and n not in ctx.specs]
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
