"""The result viewer (viewer/, the analysis department's) for one analysis of a run.

    GET /viewer/<path>                          a file under viewer/ (404 without it)
    GET /api/viewer[?run=<run_id>]              {"provisional": bool, "runs": {run_id: {"provisional": bool}},
                                                 "fields": {tool: field}, "not_here": {tool: reason}, "missing": [field ...]}
    GET /api/runs/<run_id>/viewer/<analysis_id> what the viewer draws for that analysis:
        {"field", "data", "title", "source"}, or {"field": None, "reason": "<one line>"}

The page opens it as viewer/index.html?data=<that url>#<field>.

The analysis department's entry `viewer.src.build.for_analysis(run_dir, analysis_id)` makes the
answer from the analysis' record (the run's v### folders). It is used whenever it exists and the run
keeps its records. The answer is provisional when the entry does not exist (ImportError) or the run
keeps no records (a saved run of showcase/ keeps only events.jsonl): the field comes from the tool's
name (TOOL_FIELD) and the data is the viewer's own sample of that field (viewer/data/<field>.js, as
viewer/src/<field>.py wrote it), passed as it is, with SAMPLE_SOURCE saying that it is not a picture
of this analysis.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Callable

REPO_DIR = Path(__file__).resolve().parents[2]
VIEWER_DIR = REPO_DIR / "viewer"
VIEWER_DATA_DIR = VIEWER_DIR / "data"  # made by viewer/src/<field>.py, not committed

SAMPLE_SOURCE = "見本 (この解析から作った絵ではない)"

# The viewer's field (viewer/js/main.js HV.FIELD_ORDER, or a data variant such as nvh_shell) for
# each tool name. The screen has the same table (web/js/viewer_link.js VIEWER_FIELD; a test keeps
# them equal).
TOOL_FIELD: dict[str, str] = {
    "frame_static": "strength",
    "frame_shell": "strength",
    "modal": "nvh",
    "modal_shell": "nvh_shell",
    "crash": "crash",
    "crash_beam": "crash",
    "tilt": "handling",
    "braking": "handling",
    "lane_change": "handling",
    "offroad_roll": "handling",
    "launch": "powertrain",
    "step_climb_dynamic": "powertrain",
    "crosswind": "aero",
    "fatigue": "fatigue",
    "thermal": "thermal",
    "drive_energy": "thermal",
    "assembly": "assembly",
    "rops_pushover": "rops",
}
# a tool a run wrote for the roll bar push-over (rops_pushover_frame, rops_frame_pushover, ...)
ROPS_PREFIX = "rops_"

NO_FIELD = "この解析の種類はビューアにまだ無い"
NO_SAMPLE = "ビューアの見本のデータがまだ無い"
NO_ANALYSIS = "この解析は記録に無い"
NOT_OK = "この解析は通らなかったので絵が無い"


def field_of(tool: str | None, table: dict[str, str] | None = None) -> str | None:
    """The viewer's field for a tool name (table: the tool table in use, by default TOOL_FIELD), or None."""
    if not tool:
        return None
    table = table or TOOL_FIELD
    if tool in table:
        return table[tool]
    return "rops" if tool.startswith(ROPS_PREFIX) else None


def tool_table() -> dict[str, str]:
    """The tool table in use: viewer.src.build's TOOL_FIELD when it has one, else TOOL_FIELD."""
    return entry_tables()[0] or dict(TOOL_FIELD)


def _build_module():
    try:
        return importlib.import_module("viewer.src.build")
    except ImportError:
        return None


def entry() -> Callable[[Path, str], dict[str, Any]] | None:
    """viewer.src.build.for_analysis, or None while it does not exist."""
    return getattr(_build_module(), "for_analysis", None)


def entry_tables() -> tuple[dict[str, str], dict[str, str]]:
    """(tool -> field, tool -> why it cannot be shown) of viewer.src.build (its TOOL_FIELD and
    NOT_HERE), or ({}, {}) without it."""
    mod = _build_module()
    fields = getattr(mod, "TOOL_FIELD", None)
    not_here = getattr(mod, "NOT_HERE", None)
    return (dict(fields) if isinstance(fields, dict) else {}), (dict(not_here) if isinstance(not_here, dict) else {})


def keeps_records(run_dir: Path) -> bool:
    """Whether the entry has something to read in the run folder: v### folders, an analyses/ folder,
    a one-record analysis.json, or the run's events.jsonl (the entry builds the records from the
    events, as for a saved run of showcase/ that keeps only events.jsonl)."""
    run_dir = Path(run_dir)
    if (run_dir / "analysis.json").is_file() or (run_dir / "analyses").is_dir() or (run_dir / "events.jsonl").is_file():
        return True
    try:
        return any(p.is_dir() and p.name[:1] == "v" and p.name[1:].isdigit() for p in run_dir.iterdir())
    except OSError:
        return False


def _resolve(build) -> Callable[[Path, str], dict[str, Any]] | None:
    """build as answer takes it -> the entry to call, or None for the provisional answer."""
    return None if build is False else build or entry()


def sample_file(field: str, data_dir: Path = VIEWER_DATA_DIR) -> Path:
    return Path(data_dir) / f"{field}.js"


def read_sample(field: str, data_dir: Path = VIEWER_DATA_DIR) -> dict[str, Any] | None:
    """The object of viewer/data/<field>.js (window.VIEW_DATA["<field>"] = {...};), or None."""
    path = sample_file(field, data_dir)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    head = f"window.VIEW_DATA[{json.dumps(field)}] = "
    at = text.find(head)
    if at < 0:
        return None
    body = text[at + len(head):].rstrip().rstrip(";")
    try:
        obj = json.loads(body)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def find_analysis(events_file: Path, analysis_id: str) -> dict[str, Any] | None:
    """The data of the analysis.finished event with this analysis_id (the last one), or None."""
    found = None
    needle = json.dumps(analysis_id)
    try:
        with open(events_file, encoding="utf-8") as f:
            for line in f:
                if '"analysis.finished"' not in line or needle not in line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                d = ev.get("data") or {}
                if ev.get("kind") == "analysis.finished" and d.get("analysis_id") == analysis_id:
                    found = d
    except OSError:
        return None
    return found


def provisional(events_file: Path, analysis_id: str, data_dir: Path = VIEWER_DATA_DIR,
                ana: dict[str, Any] | None = None) -> dict[str, Any]:
    """The answer without the analysis department's entry: the field's sample, as it is. ana: the
    analysis' event data when already read."""
    ana = ana if ana is not None else find_analysis(events_file, analysis_id)
    if ana is None:
        return {"field": None, "reason": NO_ANALYSIS}
    if ana.get("status") == "failed":
        return {"field": None, "reason": NOT_OK}
    field = field_of(ana.get("tool"), tool_table())
    if field is None:
        return {"field": None, "reason": NO_FIELD}
    data = read_sample(field, data_dir)
    if data is None:
        return {"field": None, "reason": NO_SAMPLE}
    return {
        "field": field,
        "data": data,
        "title": data.get("title") or field,
        "source": SAMPLE_SOURCE,
        "provisional": True,
        "tool": ana.get("tool"),
    }


def answer(events_file: Path, analysis_id: str, data_dir: Path = VIEWER_DATA_DIR,
           build: Callable[[Path, str], dict[str, Any]] | None = None, ana: dict[str, Any] | None = None) -> dict[str, Any]:
    """What GET /api/runs/<run_id>/viewer/<analysis_id> answers. build: the entry to use (by default
    viewer.src.build.for_analysis when it exists, else the provisional sample; False: the provisional
    sample even when the entry exists). A run with nothing for the entry to read gets the provisional sample too.
    ana: as in provisional."""
    build = _resolve(build)
    if build is None or not keeps_records(Path(events_file).parent):
        return provisional(events_file, analysis_id, data_dir, ana)
    try:
        out = build(Path(events_file).parent, analysis_id)
    except Exception as e:  # noqa: BLE001 (the page gets one line, not a broken answer)
        return {"field": None, "reason": f"ビューアの絵を作れなかった ({type(e).__name__})"}
    if not isinstance(out, dict) or not out.get("field"):
        reason = (out or {}).get("reason") if isinstance(out, dict) else None
        return {"field": None, "reason": reason or NO_FIELD}
    return out


def info(data_dir: Path = VIEWER_DATA_DIR, build: Callable[[Path, str], dict[str, Any]] | None = None,
         runs: dict[str, Path] | None = None) -> dict[str, Any]:
    """GET /api/viewer: whether the answers are provisional (on the whole, and for each of runs:
    {run_id: its folder}), the tool table in use, the tools whose records the entry cannot show
    (not_here: {tool: reason}), and the fields with no sample (the buttons of a provisional run's
    analyses of those fields cannot be pressed). build as in answer."""
    use = _resolve(build)
    prov = use is None
    table = tool_table()
    not_here = {} if prov else entry_tables()[1]
    per_run = {rid: {"provisional": prov or not keeps_records(d)} for rid, d in (runs or {}).items()}
    any_prov = prov or any(r["provisional"] for r in per_run.values()) or not per_run
    fields = sorted(set(table.values()))
    missing = [f for f in fields if not sample_file(f, data_dir).is_file()] if any_prov else []
    return {"provisional": prov, "runs": per_run, "fields": table, "not_here": not_here, "rops_prefix": ROPS_PREFIX,
            "missing": missing, "source": SAMPLE_SOURCE}


# ---------------------------------------------------------------- the static site

def git_files(root: Path, sub: str) -> list[Path]:
    """The files git knows under root/sub (tracked or new, not ignored), or every file without git."""
    import subprocess

    try:
        out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "--", sub], cwd=root,
                             capture_output=True, text=True, check=True).stdout
        return [root / p for p in out.splitlines() if (root / p).is_file()]
    except (OSError, subprocess.CalledProcessError):
        return [p for p in (root / sub).rglob("*") if p.is_file()]


def export(out_dir: Path, runs: dict[str, Path], data_dir: Path = VIEWER_DATA_DIR,
           viewer_dir: Path = VIEWER_DIR, build: Callable[[Path, str], dict[str, Any]] | None = None) -> dict[str, int]:
    """For the static site (export_static.py): write

        <out>/viewer/...                          viewer/ (the files git knows; and data/ when samples are used)
        <out>/data/viewer.json                    GET /api/viewer
        <out>/data/viewer/<run>/<analysis>.json   GET /api/runs/<run>/viewer/<analysis>, every analysis

    runs: {run_id: its events.jsonl}. Returns {"files": n, "bytes": total}."""
    out_dir = Path(out_dir)
    total = {"files": 0, "bytes": 0}

    def put(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        total["files"] += 1
        total["bytes"] += len(data)

    viewer_dir = Path(viewer_dir)
    if viewer_dir.is_dir():
        for f in git_files(viewer_dir.parent, viewer_dir.name):
            rel = f.relative_to(viewer_dir)
            if rel.parts[0] in ("src", "data") or f.suffix == ".pyc":
                continue  # (the Python is not served; the samples go into the answers)
            put(out_dir / "viewer" / rel, f.read_bytes())
    put(out_dir / "data" / "viewer.json", json.dumps(
        info(data_dir, build, {rid: Path(ev).parent for rid, ev in runs.items()}), ensure_ascii=False).encode("utf-8"))
    for run_id, events_file in runs.items():
        found: dict[str, dict[str, Any]] = {}  # (the last event of each id, as find_analysis)
        with open(events_file, encoding="utf-8") as f:
            for line in f:
                if '"analysis.finished"' not in line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                d = ev.get("data") or {}
                if ev.get("kind") == "analysis.finished" and d.get("analysis_id"):
                    found[d["analysis_id"]] = d
        for aid, ana in found.items():
            body = answer(Path(events_file), aid, data_dir, build, ana)
            put(out_dir / "data" / "viewer" / run_id / f"{aid}.json", json.dumps(body, ensure_ascii=False).encode("utf-8"))
    return total


def main(argv: list[str] | None = None) -> int:
    """python -m hiraku.webserve.viewer_link --out <folder> [--saved showcase]: the viewer part of the
    static site alone (export_static.py calls export for the whole site)."""
    import argparse

    from hiraku.contract.layout import EVENTS_FILE
    from hiraku.webserve.server import SAVED_DIR

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--saved", default=str(SAVED_DIR))
    a = ap.parse_args(argv)
    saved = Path(a.saved)
    runs = {d.name: d / EVENTS_FILE for d in sorted(saved.iterdir()) if (d / EVENTS_FILE).is_file()}
    t = export(Path(a.out), runs)
    print(f"{t['files']} files, {t['bytes']} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
