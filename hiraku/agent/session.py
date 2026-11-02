"""Start a run: requirements file -> load cases -> run folder -> design v1 -> checked tool shelf
(or, without a design file, a run at version 0 whose designer makes the first version)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hiraku.catalog import REPO_ROOT, build_tests, load_catalog, load_requirements, project_fields, read_data, skipped_text
from hiraku.contract import layout
from hiraku.contract.protocols import AnalysisTool, FormulaRegistry
from hiraku.store import RunStore
from hiraku.tools import RunContext, check_shelf, load_designgen, load_solvers
from hiraku.tools.judge import init_judgements

Record = dict[str, Any]

STOPPED_BY_USER = "stopped by the user (Ctrl+C)"
STOPPED_BY_BUTTON = "stopped by the user (stop button: stop.request)"


def resolve_design_path(req: Record, design_path: Path | str | None) -> Path | None:
    """The design file to start from, or None: the designer makes the first version (propose_design)."""
    if design_path is not None:
        return Path(design_path)
    if "design_file" not in req:
        return None
    return REPO_ROOT / req["design_file"]


def start_run(requirements_path: Path | str, *, design_path: Path | str | None = None,
              root: Path | str = layout.RUNS_DIR, slug: str | None = None,
              formulas: FormulaRegistry | None = None, tools: dict[str, AnalysisTool] | None = None,
              designgen: Any = None, **store_kw: Any) -> RunContext:
    """Make the run folder and everything the agents start from.

    Tests: see build_tests; the ones left out are named in an agent.said of system.
    Without a design file the run starts at version 0 with no load cases and the
    roles lead and designer: the designer makes the first version (propose_design),
    and the load cases, picks and roles follow in project.updated.

    Events: run.started, agent.said (tests left out, if any), design.created,
    tool.checked (one per tool), judgement.updated (pending tiles); without a
    design file only run.started and tool.checked.
    """
    req = load_requirements(requirements_path)
    catalog = load_catalog()
    path = resolve_design_path(req, design_path)
    design = None
    if path is not None:
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

    if design is not None:
        load_cases, skipped = build_tests(catalog, req, design)
        fields = project_fields(catalog, req, load_cases)
    else:
        load_cases, skipped = [], None
        fields = dict(project_fields(catalog, req, []), agents=first_design_agents(catalog))
    store = RunStore.create(fields, root=Path(root), slug=slug, **store_kw)
    ctx = RunContext(store=store, formulas=formulas, tools=dict(tools),
                     designgen=designgen if designgen is not None else load_designgen(), requirements_file=req)
    if skipped:
        store.emit("agent.said", agent="system", text=skipped_text(catalog, skipped))
    try:
        if design is not None:
            store.write_design(design)
        check_shelf(ctx)
        if design is not None:
            init_judgements(ctx)
    except KeyboardInterrupt:  # the tool checks take a while; do not leave the folder "running"
        store.finish("stopped", stop_reason=STOPPED_BY_USER, stop_code="user_stop")
        raise
    return ctx


def first_design_agents(catalog: Any) -> list[Record]:
    """The roles of a run before its first version: the lead and the designer."""
    return [{"agent_id": "lead", "role": "lead", "prop": catalog.prop("lead")},
            {"agent_id": "designer", "role": "designer", "prop": catalog.prop("designer")}]
