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
    v001/judgements.json              list of JUDGEMENT for this version (rewritten as tiles change)
    report.json                       REPORT (later)

IDs are unique within a run (not per version): pred-0001, ana-0001, ...
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
PROJECT_FILE = "project.json"
EVENTS_FILE = "events.jsonl"
DESIGN_FILE = "design.json"
JUDGEMENTS_FILE = "judgements.json"
REPORT_FILE = "report.json"

RECORD_DIRS = {
    "prediction": "predictions",
    "analysis": "analyses",
    "comparison": "comparisons",
    "cause": "causes",
    "verdict": "verdicts",
}

ID_PREFIX = {
    "prediction": "pred",
    "analysis": "ana",
    "comparison": "cmp",
    "cause": "cause",
    "verdict": "ver",
}

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
        "tests/contract/",
        "docs/",
        "README.md",
        "CLAUDE.md",
        "requirements.txt",
        ".gitignore",
    ],
    "design": [  # 設計部: agent loop, hooks, MCP tools, run store, catalog
        "hiraku/agent/",
        "hiraku/tools/",
        "hiraku/store/",
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
        "data/designs/",
        "data/materials/",
        "tests/solvers/",
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
}


def owner_of(path: str) -> str | None:
    p = path.replace("\\", "/")
    best, best_len = None, -1
    for dept, prefixes in OWNERS.items():
        for prefix in prefixes:
            if (p == prefix or p.startswith(prefix)) and len(prefix) > best_len:
                best, best_len = dept, len(prefix)
    return best
