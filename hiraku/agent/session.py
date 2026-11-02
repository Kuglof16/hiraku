"""Start a run: requirements file -> load cases -> run folder -> design v1 -> checked tool shelf."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hiraku.catalog import (REPO_ROOT, CatalogError, build_load_cases, load_catalog, load_requirements, project_fields,
                            read_data)
from hiraku.catalog.build import NO_BUILDER
from hiraku.contract import layout
from hiraku.contract.protocols import AnalysisTool, FormulaRegistry
from hiraku.store import RunStore
from hiraku.tools import RunContext, check_shelf, load_designgen, load_solvers
from hiraku.tools.judge import init_judgements

Record = dict[str, Any]

STOPPED_BY_USER = "stopped by the user (Ctrl+C)"


def resolve_design_path(req: Record, design_path: Path | str | None) -> Path:
    if design_path is not None:
        return Path(design_path)
    if "design_file" not in req:
        raise CatalogError("the requirements file names no design_file; pass a design")
    return REPO_ROOT / req["design_file"]


def _skipped_text(catalog, skipped: list[tuple[str, str]]) -> str:
    """One line for the screen: the tests this run leaves out, those without a builder grouped."""
    name = lambda t: f"{t} {catalog.test(t)['title']}"  # noqa: E731
    none = [t for t, why in skipped if why.endswith(NO_BUILDER)]
    other = [f"{name(t)} ({why})" for t, why in skipped if not why.endswith(NO_BUILDER)]
    parts = []
    if none:
        parts.append("荷重条件の作り方がまだ無い: " + "、".join(name(t) for t in none))
    if other:
        parts.append("要件か設計が足りない: " + "、".join(other))
    return "この実行で見ない試験 — " + " / ".join(parts)


def start_run(requirements_path: Path | str, *, design_path: Path | str | None = None,
              root: Path | str = layout.RUNS_DIR, slug: str | None = None,
              formulas: FormulaRegistry | None = None, tools: dict[str, AnalysisTool] | None = None,
              designgen: Any = None, **store_kw: Any) -> RunContext:
    """Make the run folder and everything the agents start from.

    Tests: those the requirements file lists (each must build), else every
    catalog test that applies to the product's flags and can be built; the
    others are named in an agent.said of system.

    Events: run.started, agent.said (tests left out, if any), design.created,
    tool.checked (one per tool), judgement.updated (pending tiles).
    """
    req = load_requirements(requirements_path)
    catalog = load_catalog()
    path = resolve_design_path(req, design_path)
    if not path.exists():
        raise FileNotFoundError(
            f"design file {path} does not exist (designs are in data/designs/, with occupants and cargo "
            f"as parts); pass --design to use another file"
        )
    design = dict(read_data(path))
    design["design_version"] = 1
    design["parent_version"] = None

    if formulas is None or tools is None:
        f, t = load_solvers()  # before the run folder exists, so a missing solver leaves nothing behind
        formulas = formulas if formulas is not None else f
        tools = tools if tools is not None else t

    skipped: list[tuple[str, str]] | None = None
    if req.get("tests"):
        test_ids = req["tests"]
    else:
        test_ids, skipped = catalog.select_tests(req["flags"], req.get("speed_class")), []
    load_cases = build_load_cases(catalog, req, design, test_ids, skipped)
    if not load_cases:
        raise CatalogError("no test could be built for this product: "
                           + "; ".join(f"{t}: {why}" for t, why in skipped or []))
    store = RunStore.create(project_fields(catalog, req, load_cases), root=Path(root), slug=slug, **store_kw)
    ctx = RunContext(store=store, formulas=formulas, tools=dict(tools),
                     designgen=designgen if designgen is not None else load_designgen())
    if skipped:
        store.emit("agent.said", agent="system", text=_skipped_text(catalog, skipped))
    try:
        store.write_design(design)
        check_shelf(ctx)
        init_judgements(ctx)
    except KeyboardInterrupt:  # the tool checks take a while; do not leave the folder "running"
        store.finish("stopped", stop_reason=STOPPED_BY_USER)
        raise
    return ctx
