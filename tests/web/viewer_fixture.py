"""A viewer sample for the viewer link's tests, made in a temporary folder (viewer/data is not committed).

The saved scripted run keeps its records only in its events (showcase/README.md); this writes them back
as a run folder (project.json, v00N/design.json, v00N/analyses/<id>.json) and has the viewer's own
script (viewer.src.assembly, read only) write data/assembly.js from it. Nothing is written under runs/
or viewer/.
"""

from __future__ import annotations

import json
from pathlib import Path

from hiraku.webserve.server import SAVED_DIR

SCRIPTED_RUN = "20261105-150416-offroad-4w-scripted"


def run_folder(events_file: Path, out: Path, tools: tuple[str, ...] | None = ("assembly",)) -> Path:
    """The records of events_file as a run folder under out (only the analyses of these tools; None: all)."""
    out.mkdir(parents=True, exist_ok=True)
    project = None
    with open(events_file, encoding="utf-8") as f:
        lines = f.readlines()
    for line in lines:
        ev = json.loads(line)
        d = ev.get("data") or {}
        if ev.get("kind") == "run.started" and isinstance(d.get("project"), dict):
            project = d["project"]
        elif ev.get("kind") == "load_cases.created":
            project = project or {}
            project.setdefault("load_cases", [])
            project["load_cases"] += d.get("load_cases") or []
        elif ev.get("kind") == "design.created":
            v = out / f"v{d['design_version']:03d}"
            (v / "analyses").mkdir(parents=True, exist_ok=True)
            (v / "design.json").write_text(json.dumps(d), encoding="utf-8")
        elif ev.get("kind") == "analysis.finished" and (tools is None or d.get("tool") in tools):
            v = out / f"v{d['design_version']:03d}" / "analyses"
            v.mkdir(parents=True, exist_ok=True)
            (v / f"{d['analysis_id']}.json").write_text(json.dumps(d), encoding="utf-8")
    saved_project = Path(events_file).parent / "project.json"
    if saved_project.is_file():
        project = {**json.loads(saved_project.read_text(encoding="utf-8")), **({"load_cases": project["load_cases"]} if project and project.get("load_cases") else {})}
    (out / "project.json").write_text(json.dumps(project or {}), encoding="utf-8")
    return out


def records_run(runs_dir: Path, run_id: str) -> Path:
    """runs_dir/run_id: the scripted run's events.jsonl with every analysis' record written back (a run
    that keeps its records, as a run of runs/ does), for viewer.src.build.for_analysis."""
    import shutil

    out = run_folder(SAVED_DIR / SCRIPTED_RUN / "events.jsonl", Path(runs_dir) / run_id, tools=None)
    shutil.copy(SAVED_DIR / SCRIPTED_RUN / "events.jsonl", out / "events.jsonl")
    return out


def assembly_sample(tmp: Path) -> Path:
    """tmp/data/assembly.js made by viewer.src.assembly from the scripted run; returns tmp/data."""
    from viewer.src import assembly, common

    run = run_folder(SAVED_DIR / SCRIPTED_RUN / "events.jsonl", tmp / "run")
    data = tmp / "data"
    common.main("assembly", assembly.build, assembly.extra, argv=[str(run), "--out", str(data)])
    return data
