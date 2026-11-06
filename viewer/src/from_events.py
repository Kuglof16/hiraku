"""A run that holds only events.jsonl (the showcase runs: events.jsonl, project.json, report.json, tools/) laid out as
the run store writes its folder, so the viewer's builders can read it:

    python -m viewer.src.from_events <run dir>      (prints the folder)

materialize(run_dir) -> <temp>/hiraku-viewer-events/<key>/<run name>/ with
    project.json                      the data of the last run.started / project.updated event
    v<NNN>/design.json                design.created (RunStore.write_design)
    v<NNN>/load_cases.json            load_cases.created (RunStore.write_version_load_cases)
    v<NNN>/<kind dir>/<id>.json       the record events (RunStore.add_record: analysis.finished -> analyses/,
                                      mesh.created -> meshes/, prediction.recorded -> predictions/, ...)
Each file is the event's data exactly as the event carries it, at the path the store wrote it (the event's
design_version; a record written again with the same id: the last event's). The folder's leaf is the run's own name,
so common.run_name (source.run) names the original run. <key> is a hash of the events file's resolved path, size
and modification time: the folder is reused while the file is unchanged, and nothing is written next to the run.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

Record = dict[str, Any]

EVENTS_FILE = "events.jsonl"
CACHE_DIR = "hiraku-viewer-events"
DONE = ".materialized"   # written last: a folder without it is not used

# hiraku/contract/layout.py RECORD_EVENT and RECORD_DIRS (kept here so the viewer does not import the contract;
# tests/viewer/test_build.py checks they are the same)
RECORD_EVENT = {"prediction": "prediction.recorded", "analysis": "analysis.finished",
                "comparison": "comparison.recorded", "cause": "cause.recorded", "verdict": "verdict.recorded",
                "rule_check": "rule_check.recorded", "mesh": "mesh.created"}
RECORD_DIRS = {"prediction": "predictions", "analysis": "analyses", "comparison": "comparisons", "cause": "causes",
               "verdict": "verdicts", "rule_check": "rule_checks", "mesh": "meshes"}
RECORD_OF_EVENT = {ev: kind for kind, ev in RECORD_EVENT.items()}
PROJECT_EVENTS = ("run.started", "project.updated")


def has_versions(run: Path) -> bool:
    run = Path(run)
    return (run / "analyses").is_dir() or (run / "analysis.json").is_file() or any(
        p.is_dir() and p.name[:1] == "v" and p.name[1:].isdigit() for p in run.iterdir())


def needs_materialize(run: Path) -> bool:
    """A run folder with events.jsonl and no version folders (nor a version or one-record folder)."""
    run = Path(run)
    return run.is_dir() and (run / EVENTS_FILE).is_file() and not has_versions(run)


def cache_key(events: Path) -> str:
    st = Path(events).stat()
    text = f"{Path(events).resolve()}|{st.st_size}|{st.st_mtime_ns}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


def cache_root() -> Path:
    return Path(tempfile.gettempdir()) / CACHE_DIR


def _vdir(root: Path, v: int) -> Path:
    return root / f"v{int(v):03d}"


def _write_json(path: Path, obj: Any) -> None:
    """As RunStore._write_json writes a file (UTF-8, indent 2, a newline at the end)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def files_of(events_path: Path) -> dict[str, Any]:
    """{relative path: the object written there}, from the events in order (a later write of a path replaces it)."""
    files: dict[str, Any] = {}
    with Path(events_path).open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            e = json.loads(line)
            kind, data = e.get("kind"), e.get("data")
            if not isinstance(data, dict):
                continue
            if kind in PROJECT_EVENTS:
                files["project.json"] = data
                continue
            v = e.get("design_version", data.get("design_version"))
            if v is None:
                continue
            vd = f"v{int(v):03d}"
            if kind == "design.created":
                files[f"{vd}/design.json"] = data
            elif kind == "load_cases.created":
                files[f"{vd}/load_cases.json"] = data
            elif kind in RECORD_OF_EVENT:
                rk = RECORD_OF_EVENT[kind]
                rid = data.get(f"{rk}_id")
                if rid:
                    files[f"{vd}/{RECORD_DIRS[rk]}/{rid}.json"] = data
    return files


def materialize(run_dir, root: Path | None = None) -> Path:
    """The run's folder laid out as the store writes it, from its events.jsonl only (made once per events file)."""
    run = Path(run_dir).resolve()
    events = run / EVENTS_FILE
    if not events.is_file():
        raise FileNotFoundError(f"{run.name}: no {EVENTS_FILE}")
    base = Path(root) if root else cache_root()
    out = base / cache_key(events) / run.name
    if (out / DONE).is_file():
        return out
    base.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="stage-", dir=str(base)))
    try:
        built = stage / run.name
        for rel, obj in files_of(events).items():
            _write_json(built / rel, obj)
        built.mkdir(parents=True, exist_ok=True)
        (built / DONE).write_text(f"{events.stat().st_size}\n", encoding="utf-8")
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():   # a folder left without its marker (an earlier attempt stopped part way)
            shutil.rmtree(out, ignore_errors=True)
        try:
            os.replace(built, out)
        except OSError:
            if not (out / DONE).is_file():   # another process made it meanwhile: use theirs, else fail
                raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return out


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print(__doc__.split("\n\n")[1].strip())
        return 2
    print(materialize(argv[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
