"""Start a run: requirements file -> load cases -> run folder -> design v1 -> checked tool shelf."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hiraku.catalog import REPO_ROOT, CatalogError, build_load_cases, load_catalog, load_requirements, project_fields, read_data
from hiraku.contract import layout
from hiraku.contract.protocols import AnalysisTool, FormulaRegistry
from hiraku.store import RunStore
from hiraku.tools import RunContext, check_shelf, load_solvers
from hiraku.tools.judge import init_judgements

Record = dict[str, Any]


def resolve_design_path(req: Record, design_path: Path | str | None) -> Path:
    if design_path is not None:
        return Path(design_path)
    if "design_file" not in req:
        raise CatalogError("the requirements file names no design_file; pass a design")
    return REPO_ROOT / req["design_file"]


def start_run(requirements_path: Path | str, *, design_path: Path | str | None = None,
              root: Path | str = layout.RUNS_DIR, slug: str | None = None,
              formulas: FormulaRegistry | None = None, tools: dict[str, AnalysisTool] | None = None,
              **store_kw: Any) -> RunContext:
    """Make the run folder and everything the agents start from.

    Events: run.started, design.created, tool.checked (one per tool), judgement.updated (pending tiles).
    """
    req = load_requirements(requirements_path)
    catalog = load_catalog()
    path = resolve_design_path(req, design_path)
    if not path.exists():
        raise FileNotFoundError(
            f"design file {path} does not exist. The analysis department writes data/designs/ "
            f"(occupants and cargo as parts); pass --design to use another file"
        )
    design = dict(read_data(path))
    design["design_version"] = 1
    design["parent_version"] = None

    if formulas is None or tools is None:
        f, t = load_solvers()  # before the run folder exists, so a missing solver leaves nothing behind
        formulas = formulas if formulas is not None else f
        tools = tools if tools is not None else t

    test_ids = req.get("tests") or catalog.select_tests(req["flags"], req.get("speed_class"))
    load_cases = build_load_cases(catalog, req, design, test_ids)
    store = RunStore.create(project_fields(catalog, req, load_cases), root=Path(root), slug=slug, **store_kw)
    ctx = RunContext(store=store, formulas=formulas, tools=dict(tools))
    store.write_design(design)
    check_shelf(ctx)
    init_judgements(ctx)
    return ctx
