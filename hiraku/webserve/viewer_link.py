"""The result viewer (viewer/, the analysis department's) for one analysis of a run.

    GET /viewer/<path>                          a file under viewer/ (404 without it)
    GET /api/viewer                             {"provisional": bool, "fields": {tool: field}, "missing": [field ...]}
    GET /api/runs/<run_id>/viewer/<analysis_id> what the viewer draws for that analysis:
        {"field", "data", "title", "source"}, or {"field": None, "reason": "<one line>"}

The page opens it as viewer/index.html?data=<that url>#<field>.

The analysis department's entry `viewer.src.build.for_analysis(run_dir, analysis_id)` makes the
answer. Until it exists (ImportError), the answer is provisional: the field comes from the tool's
name (TOOL_FIELD) and the data is the viewer's own sample of that field (viewer/data/<field>.js, as
viewer/src/<field>.py wrote it), passed as it is, with SAMPLE_SOURCE saying that it is not a picture
of this analysis. When the entry arrives, only `answer` changes (it already prefers the entry).
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


def field_of(tool: str | None) -> str | None:
    """The viewer's field for a tool name, or None."""
    if not tool:
        return None
    if tool in TOOL_FIELD:
        return TOOL_FIELD[tool]
    return "rops" if tool.startswith(ROPS_PREFIX) else None


def entry() -> Callable[[Path, str], dict[str, Any]] | None:
    """viewer.src.build.for_analysis, or None while it does not exist."""
    try:
        mod = importlib.import_module("viewer.src.build")
    except ImportError:
        return None
    return getattr(mod, "for_analysis", None)


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
    field = field_of(ana.get("tool"))
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
    sample even when the entry exists). ana: as in provisional."""
    build = None if build is False else build or entry()
    if build is None:
        return provisional(events_file, analysis_id, data_dir, ana)
    try:
        out = build(Path(events_file).parent, analysis_id)
    except Exception as e:  # noqa: BLE001 (the page gets one line, not a broken answer)
        return {"field": None, "reason": f"ビューアの絵を作れなかった ({type(e).__name__})"}
    if not isinstance(out, dict) or not out.get("field"):
        reason = (out or {}).get("reason") if isinstance(out, dict) else None
        return {"field": None, "reason": reason or NO_FIELD}
    return out


def info(data_dir: Path = VIEWER_DATA_DIR, build: Callable[[Path, str], dict[str, Any]] | None = None) -> dict[str, Any]:
    """GET /api/viewer: whether the answers are provisional, the tool table, and (provisional only)
    the fields with no sample, whose buttons cannot be pressed. build as in answer."""
    prov = build is False or (build is None and entry() is None)
    fields = sorted(set(TOOL_FIELD.values()))
    missing = [f for f in fields if not sample_file(f, data_dir).is_file()] if prov else []
    return {"provisional": prov, "fields": dict(TOOL_FIELD), "rops_prefix": ROPS_PREFIX, "missing": missing,
            "source": SAMPLE_SOURCE if prov else None}


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
    put(out_dir / "data" / "viewer.json", json.dumps(info(data_dir, build), ensure_ascii=False).encode("utf-8"))
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
    static site alone (until export_static.py calls export)."""
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
