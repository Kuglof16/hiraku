"""Where things live: the runs/<run_id>/ folder and who owns which folder.

runs/<run_id>/
    project.json                      PROJECT (status and requirements updated in place)
    events.jsonl                      every event, append-only; the screen draws from this
    v001/design.json                  DESIGN of version 1 (never rewritten)
    v001/predictions/pred-0001.json   PREDICTION
    v001/analyses/ana-0001.json       RUN (analysis)
    v001/comparisons/cmp-0001.json    COMPARISON
    v001/causes/cause-0001.json       CAUSE
    v001/verdicts/ver-0001.json       VERDICT
    v001/rule_checks/rchk-0001.json   RULE_CHECK (a knowledge rule checked on this version's parts)
    v001/meshes/mesh-0001.json        MESH (shell mesh of this version's frame; fields name it by mesh_id)
    v001/judgements.json              list of JUDGEMENT for this version (rewritten as tiles change)
    v001/load_cases.json              the load cases built for this version (load_cases.created; from version 2 on)
    report.json                       REPORT (later)
    stop.request                      present only while someone asks the run to stop
    tools/<name>-<n>.py               a tool Claude wrote during the run (attempt n)
    tools/<name>-<n>.check.json       its known-answer checks (tool_check records)
    knowledge/<filename>              a knowledge file the person dropped (its rules live in events only)
    hearing.request.json              (later) questions waiting for an answer during the run
    hearing.answer.json               (later) the answer put by the screen; the run reads and removes both
    inbox/<n>-<kind>.json             a request from the screen (a message for Claude, a dropped file); the run
                                      reads it, emits the event itself and removes the file
    plugin.hooks.json                 claude_code_plugin runs only: how the plugin's hooks are wired for this run
    plugin.outbox.jsonl               claude_code_plugin runs only: hook notes waiting for the MCP server to emit
    plugin.lock                       claude_code_plugin runs only: a short lock around reading or writing the plugin state and outbox (not a sign that a session holds the run)
    plugin.alive                      claude_code_plugin runs only: touched by the MCP server every poll while a Claude Code session serves the run
    tool.errors.log                   present only when a tool let an exception out: its traceback, appended per error
                                      (the run also says it in an agent.said of system with data.tool_error)

IDs are unique within a run (not per version): pred-0001, ana-0001, ...
Only the run process appends to events.jsonl (seq has no gaps and one writer);
anything else asks through stop.request, hearing.answer.json or inbox/.
Every record written to a file is also the `data` of one event, byte-for-byte
the same object, so replaying events.jsonl alone rebuilds the screen.

Updating a record (a cause becomes resolved, a comparison gets its cause_id):
emit the same kind of event again with the same id and the whole new record,
and rewrite the file to match. Per id, the last event wins and the file always
equals the last event's data. design.json is never updated; a change is a new
version.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

RUNS_DIR = Path("runs")
SHOWCASE_DIR = Path("showcase")  # committed example runs, same layout as runs/<run_id>/
PROJECT_FILE = "project.json"
EVENTS_FILE = "events.jsonl"
DESIGN_FILE = "design.json"
JUDGEMENTS_FILE = "judgements.json"
VERSION_LOAD_CASES_FILE = "load_cases.json"  # v00N/: the load cases built for that version (load_cases.created)
REPORT_FILE = "report.json"
TOOLS_DIR = "tools"  # tools written during the run and their checks
STOP_REQUEST_FILE = "stop.request"  # put by the screen to ask a running run to stop (stop_code user_stop)
KNOWLEDGE_DIR = "knowledge"  # knowledge files the person dropped, kept as given
HEARING_REQUEST_FILE = "hearing.request.json"  # later: hearing during the run
HEARING_ANSWER_FILE = "hearing.answer.json"
INBOX_DIR = "inbox"  # requests the screen puts for a running run (the run turns them into events)
PLUGIN_HOOKS_FILE = "plugin.hooks.json"  # claude_code_plugin runs: hook wiring for this run
PLUGIN_OUTBOX_FILE = "plugin.outbox.jsonl"  # claude_code_plugin runs: hook notes for the MCP server to emit
PLUGIN_LOCK_FILE = "plugin.lock"  # claude_code_plugin runs: held only while the plugin state or the outbox is read or written
PLUGIN_ALIVE_FILE = "plugin.alive"  # claude_code_plugin runs: the MCP server touches it every poll while it serves the run
PLUGIN_ALIVE_S = 10.0  # a plugin run is held by a session while plugin.alive is younger than this (seconds)
TOOL_ERRORS_FILE = "tool.errors.log"  # tracebacks of exceptions a tool let out (only when there was one)

RECORD_DIRS = {
    "prediction": "predictions",
    "analysis": "analyses",
    "comparison": "comparisons",
    "cause": "causes",
    "verdict": "verdicts",
    "rule_check": "rule_checks",
    "mesh": "meshes",
}

ID_PREFIX = {
    "prediction": "pred",
    "analysis": "ana",
    "comparison": "cmp",
    "cause": "cause",
    "verdict": "ver",
    "rule_check": "rchk",
    "mesh": "mesh",
}

# Event emitted when a record of this kind is written (its data is the record;
# the id field is f"{kind}_id"). RECORD_OF_EVENT is the same map read backwards.
RECORD_EVENT = {
    "prediction": "prediction.recorded",
    "analysis": "analysis.finished",
    "comparison": "comparison.recorded",
    "cause": "cause.recorded",
    "verdict": "verdict.recorded",
    "rule_check": "rule_check.recorded",
    "mesh": "mesh.created",
}
RECORD_OF_EVENT = {event: kind for kind, event in RECORD_EVENT.items()}

RUN_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9-]+$")


def new_run_id(now: datetime, slug: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", slug.lower()).strip("-") or "run"
    return f"{now:%Y%m%d-%H%M%S}-{slug}"


def format_id(kind: str, n: int) -> str:
    return f"{ID_PREFIX[kind]}-{n:04d}"


def run_dir(run_id: str, root: Path = RUNS_DIR) -> Path:
    if not RUN_ID_RE.match(run_id):
        raise ValueError(f"bad run id {run_id!r}")
    return Path(root) / run_id


def version_dir(run_path: Path, design_version: int) -> Path:
    return Path(run_path) / f"v{design_version:03d}"


def record_path(run_path: Path, design_version: int, kind: str, record_id: str) -> Path:
    return version_dir(run_path, design_version) / RECORD_DIRS[kind] / f"{record_id}.json"


# Who writes where. A department edits only its own paths; anything else
# goes through the lead. Paths are relative to the repository root.
OWNERS = {
    "lead": [
        "hiraku/contract/",
        "hiraku/__init__.py",
        "tests/__init__.py",
        "LICENSE",
        "showcase/",  # saved runs committed as examples (the screen opens the newest)
        "tests/contract/",
        "docs/",
        "README.md",
        "HIRAKU.cmd",  # double-click launcher of the screen
        "CLAUDE.md",
        "requirements.txt",
        "requirements-fast.txt",
        ".gitignore",
    ],
    "design": [  # 設計部: agent loop, hooks, MCP tools, run store, catalog
        "hiraku/agent/",
        "hiraku/tools/",
        "hiraku/store/",
        "hiraku/export/",  # results to CSV, VTK and report files (A9)
        "hiraku/catalog/",
        "hiraku/__main__.py",
        "data/catalog/",
        "data/requirements/",
        "plugins/",
        "tests/agent/",
        "tests/tools/",
        "tests/store/",
    ],
    "analysis": [  # 解析部: K0 hand calc and K1..K5 solvers (K2 team inside)
        "hiraku/solvers/",
        "hiraku/catalog/builders/",  # load-case builders per test (k2_*.py by the K2 team)
        "hiraku/designgen/",  # design from its variables (for revise_design)
        "data/designs/",
        "data/parts/",  # real components with their sources (sizes, masses, ratings)
        "data/materials/",
        "data/tyres/",  # tyre model coefficients (K2 team)
        "viewer/",  # the result viewer: one page per run, a data script per performance field
        "tests/solvers/",
        "tests/viewer/",
    ],
    "screen": [  # 画面部: three.js screens and replay
        "web/",
        "hiraku/webserve/",
        "tests/web/",
    ],
    "qa": [  # 品質保証部: verification tests, Rapier cross-check, reviews
        "tests/verification/",
        "qa/",
    ],
    "promo": [  # 広報部: title and explainer screens, video prep
        "promo/",
    ],
    "intake": [  # 受付部: picking domains, hearing, knowledge rules (N09, N14)
        "hiraku/intake/",
        "data/requirements/products/",
        "data/knowledge/",
        "web/js/intake/",
        "tests/intake/",
    ],
}


def owner_of(path: str) -> str | None:
    p = path.replace("\\", "/")
    best, best_len = None, -1
    for dept, prefixes in OWNERS.items():
        for prefix in prefixes:
            if (p == prefix or p.startswith(prefix)) and len(prefix) > best_len:
                best, best_len = dept, len(prefix)
    return best
