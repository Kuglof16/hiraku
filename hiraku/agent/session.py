"""Start a run: requirements file -> load cases -> run folder -> design v1 -> checked tool shelf
(or, without a design file, a run at version 0 whose designer makes the first version)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from hiraku import contract
from hiraku.catalog import REPO_ROOT, build_tests, load_catalog, load_requirements, project_fields, read_data, skipped_text
from hiraku.contract import layout
from hiraku.contract.protocols import AnalysisTool, FormulaRegistry
from hiraku.store import RunStore
from hiraku.tools import RunContext, check_shelf, load_designgen, load_solvers
from hiraku.tools.shelf import defer_shelf
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


def intake_for(req: Record) -> Any:
    """The intake steps for a run from a requirements file (hiraku.intake.pipeline, from_file):
    the domains its flags pick; a hearing that asks nothing the file settles and fills the rest
    with the item defaults (a product HIRAKU has a file for lends its values); the built-in
    knowledge. Returns the IntakeResult: .events, and the merged requirements in
    .requirements_file["requirements"]."""
    from hiraku.intake.pipeline import intake
    from hiraku.intake.products import product_files

    product = req["product"] if req["product"] in product_files() else None
    kw = {}
    mode = req.get("hearing", "code")
    if mode != "code":  # docs/contract.md hearing claude: the items the file leaves open stay open for ask_user
        import inspect

        if "hearing_mode" not in inspect.signature(intake).parameters:  # an intake without the claude hearing yet
            got = intake(req["want"], req["flags"], req.get("speed_class"), product=product,
                         requirements=req["requirements"], from_file=True)
            got.events = [e for e in got.events if not e["kind"].startswith("hearing.")]
            got.requirements_file["requirements"] = list(req["requirements"])
            return got
        kw["hearing_mode"] = mode
    return intake(req["want"], req["flags"], req.get("speed_class"), product=product,
                  requirements=req["requirements"], from_file=True, **kw)


INTAKE_KINDS = ("domains.picked", "hearing.asked", "hearing.answered", "knowledge.imported")


def check_intake_events(events: Any) -> list[Record]:
    """Events of intake steps done before the run (read from a file): a list of
    {kind, agent?, text?, data} of the intake's kinds only. Raises ValueError saying what is wrong."""
    if not isinstance(events, list):
        raise ValueError("the intake events must be a JSON list of {kind, agent, text, data}")
    for i, e in enumerate(events):
        if not isinstance(e, dict) or e.get("kind") not in INTAKE_KINDS or not isinstance(e.get("data"), dict):
            raise ValueError(f"intake event {i} must be {{kind, agent, text, data}} with kind one of "
                             f"{', '.join(INTAKE_KINDS)}")
    return events


OWNERLESS_REASON = "Claude Code のセッションが終わり、実行が閉じられないまま残っていた"


def close_ownerless_plugin_runs(root: Path) -> list[str]:
    """docs/contract.md 持ち主のいない実行: a run of driver claude_code_plugin still running whose
    plugin.alive is missing or older than layout.PLUGIN_ALIVE_S (no server serves it) is closed by the next
    start_run in the same runs folder, whoever's: stopped / user_stop / OWNERLESS_REASON. Returns their run ids."""
    from hiraku.agent.plugin import locked, server_alive

    closed = []
    if not root.is_dir():
        return closed
    for path in sorted(root.iterdir()):
        if not (path / layout.PROJECT_FILE).is_file():
            continue
        try:
            project = read_data(path / layout.PROJECT_FILE)
        except (OSError, ValueError):
            continue
        if project.get("driver") != "claude_code_plugin" or project.get("status") != "running" or server_alive(path):
            continue
        try:
            with locked(path):
                store = RunStore.open(path)
                if store.project["status"] != "running":
                    continue
                store.emit("agent.stopped", agent="lead", data={"agent_id": "lead", "stop_code": "user_stop"})
                store.finish("stopped", stop_reason=OWNERLESS_REASON, stop_code="user_stop")
        except (OSError, ValueError, TimeoutError):
            continue  # a folder that cannot be closed now is left for the next start
        closed.append(store.project["run_id"])
    return closed


def start_run(requirements_path: Path | str, *, design_path: Path | str | None = None,
              root: Path | str = layout.RUNS_DIR, slug: str | None = None,
              formulas: FormulaRegistry | None = None, tools: dict[str, AnalysisTool] | None = None,
              designgen: Any = None, intake: bool = False, intake_events: list[Record] | None = None,
              driver: str | None = None, on_folder: Callable[[Path], None] | None = None,
              **store_kw: Any) -> RunContext:
    """Make the run folder and everything the agents start from.

    on_folder: called with the run folder as soon as it exists (with events.jsonl), before the tool shelf is checked.

    Tests: see build_tests; the ones left out are named in an agent.said of system.
    Without a design file the run starts at version 0 with no load cases and the
    roles lead and designer: the designer makes the first version (propose_design),
    and the load cases, picks and roles follow in project.updated.

    intake: run the intake steps on the file first (intake_for): the run's requirements are
    the merged ones (the file's, and the defaults the hearing added), and its events follow
    run.started. intake_events: the events of intake steps already done (a requirements
    file the intake pipeline wrote, A1 to A2 on the screen), emitted as they are instead.

    Events: run.started, with intake domains.picked, hearing.* and knowledge.imported,
    agent.said (tests left out, if any), design.created, tool.checked (one per tool),
    judgement.updated (pending tiles); without a design file no design.created or
    judgement.updated.
    """
    req = load_requirements(requirements_path)
    if intake_events is not None:
        intake_events = check_intake_events(intake_events)
    elif intake:
        got = intake_for(req)
        req = {**req, "requirements": got.requirements_file["requirements"]}
        intake_events = got.events
    else:
        intake_events = []
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
        if design.get("modules"):  # docs/contract.md モジュールに分けた設計: a designer per module, and the packager
            from hiraku.agent.roles import agents_with_modules

            fields = dict(fields, agents=agents_with_modules(fields["agents"], design, catalog.prop))
    else:
        load_cases, skipped = [], None
        fields = dict(project_fields(catalog, req, []), agents=first_design_agents(catalog))
    if driver is not None:  # project.driver: scripted, agent_sdk or claude_code_plugin
        fields = dict(fields, driver=driver)
    if intake and req.get("tests"):  # the intake picks from the flags; the file's tests decide the domains
        intake_events = [dict(e, data=picks_for_file_tests(e["data"], req["tests"], load_cases, fields, catalog))
                         if e["kind"] == "domains.picked" else e for e in intake_events]
    close_ownerless_plugin_runs(Path(root))
    store = RunStore.create(fields, root=Path(root), slug=slug, **store_kw)
    if on_folder is not None:  # the folder and events.jsonl exist from here: the screen can open the run while the
        on_folder(store.path)  # tool shelf is checked below (about 30 s idle, 250 s on a busy machine)
    ctx = RunContext(store=store, formulas=formulas, tools=dict(tools),
                     designgen=designgen if designgen is not None else load_designgen(), requirements_file=req,
                     requirements_path=Path(requirements_path))
    for e in intake_events:
        store.emit(e["kind"], agent=e.get("agent"), text=e.get("text"), data=e["data"])
    if skipped:
        store.emit("agent.said", agent="system", text=skipped_text(catalog, skipped))
    try:
        if design is not None:
            store.write_design(design)
        test_ids = run_test_ids(req, load_cases)
        if test_ids is None:  # Claude picks the tests: the tools are checked once version 1 has them
            defer_shelf(ctx)
        else:
            check_shelf(ctx, test_ids)
        if design is not None:
            init_judgements(ctx)
    except KeyboardInterrupt:  # the tool checks take a while; do not leave the folder "running"
        store.finish("stopped", stop_reason=STOPPED_BY_USER, stop_code="user_stop")
        raise
    return ctx


def picks_for_file_tests(picked: Record, tests: list[str], load_cases: list[Record], fields: Record,
                         catalog: Any) -> Record:
    """The intake's domains.picked data (hiraku.intake picks every domain the flags allow) narrowed
    to the domains of the requirements file's tests: the same domains as project.domain_picks (the
    load cases' judged domains; before version 1, the tests' own domains), each with the file's
    tests of that domain; the intake's other picks go to `off` with the reason."""
    if load_cases:
        domains = [p["domain"] for p in fields["domain_picks"]]
        run_tests = list(dict.fromkeys(lc["test_id"] for lc in load_cases))
    else:
        have = {catalog.test(t)["domain"] for t in tests}
        domains = [d for d in contract.DOMAINS if d in have and d != "assembly"]
        run_tests = list(tests)
    by_domain = {d: [t for t in run_tests if catalog.test(t)["domain"] == d] for d in contract.DOMAINS}
    theirs = {p["domain"]: p for p in picked["picks"]}
    reasons = {p["domain"]: p["reason"] for p in fields["domain_picks"]}
    listed = "・".join(tests)
    picks = []
    for d in domains:
        names = "・".join(f"{t} {catalog.test(t)['title']}" for t in by_domain[d])
        picks.append({"domain": d, "shown_name": (theirs.get(d) or {}).get("shown_name") or contract.DOMAIN_NAMES_JA[d],
                      "reason": reasons.get(d) or f"要件のファイルが試験 {names} を選んだ", "test_ids": by_domain[d]})
    off = [o for o in picked["off"] if o["domain"] not in domains] + [
        {"domain": d, "reason": f"要件のファイルの試験 ({'・'.join(by_domain[d])}) に、この分野の合否の基準が無い"
         if by_domain[d] else f"要件のファイルの試験 ({listed}) に、この分野の試験が無い"}
        for d in theirs if d not in domains]
    off.sort(key=lambda o: contract.DOMAINS.index(o["domain"]))
    return {**picked, "picks": picks, "off": off, "assembly_test_ids": by_domain["assembly"]}


def run_test_ids(req: Record, load_cases: list[Record]) -> list[str] | None:
    """The tests a run can use, for the tool checks (check_shelf): its load cases' tests; before the
    first version, the requirements file's tests and the tests that feed them (P1 for R1); None
    when they are not fixed yet (no load cases and no `tests` in the file: the checks wait for version 1,
    shelf.defer_shelf)."""
    if load_cases:
        return list(dict.fromkeys(lc["test_id"] for lc in load_cases))
    if not req.get("tests"):
        return None
    from hiraku.catalog.build import feeds_map

    tests = list(req["tests"])
    ups = [up for up, downs in feeds_map().items() if up not in tests and any(d in tests for d in downs)]
    return ups + tests


def first_design_agents(catalog: Any) -> list[Record]:
    """The roles of a run before its first version: the lead and the designer."""
    return [{"agent_id": "lead", "role": "lead", "prop": catalog.prop("lead")},
            {"agent_id": "designer", "role": "designer", "prop": catalog.prop("designer")}]
