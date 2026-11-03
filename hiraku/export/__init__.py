"""Exports of a run's records (the A9 window calls these): CSV tables, VTK files for ParaView,
the report as Markdown and HTML, and a zip of them with the records. Everything is read from the
run folder; values are written as recorded, nothing is computed again.

    export_run(run_dir, out_dir=None, parts=PARTS, versions=None) -> {"out_dir", "files"}
    export_zip(run_dir, zip_path=None, parts=PARTS, versions=None, records=True) -> {"zip", "files"}

parts: "comparisons", "results", "series" (CSV), "vtk", "report". versions: the design versions
to include (all when None). Files go to <run_dir>/export/ unless out_dir is given.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any, Iterable

from hiraku.export.report import write_report
from hiraku.export.tables import (COMPARISON_COLUMNS, RESULT_COLUMNS, comparison_rows, read_csv, result_rows,
                                  write_csv, write_series)
from hiraku.export.vtk import read_vtk, write_fields
from hiraku.store import RunStore

Record = dict[str, Any]

PARTS = ("comparisons", "results", "series", "vtk", "report")
EXPORT_DIR = "export"


class ExportError(ValueError):
    pass


def _check(parts: Iterable[str], versions: Iterable[int] | None) -> tuple[list[str], list[int] | None]:
    parts = list(parts)
    bad = [p for p in parts if p not in PARTS]
    if bad:
        raise ExportError(f"unknown parts {bad}; use some of {', '.join(PARTS)}")
    if versions is not None:
        versions = [int(v) for v in versions]
    return parts, versions


def export_run(run_dir: Path | str, out_dir: Path | str | None = None, *, parts: Iterable[str] = PARTS,
               versions: Iterable[int] | None = None) -> Record:
    parts, versions = _check(parts, versions)
    store = RunStore.open(Path(run_dir))
    out = Path(out_dir) if out_dir is not None else store.path / EXPORT_DIR
    files: list[Path] = []
    if "comparisons" in parts:
        files.append(write_csv(out / "comparisons.csv", COMPARISON_COLUMNS, comparison_rows(store, versions)))
    if "results" in parts:
        files.append(write_csv(out / "results.csv", RESULT_COLUMNS, result_rows(store, versions)))
    if "series" in parts:
        files += write_series(store, out / "series", versions)
    if "vtk" in parts:
        files += write_fields(store, out / "vtk", versions)
    if "report" in parts:
        files += write_report(store, out)
    return {"out_dir": str(out), "files": [p.relative_to(out).as_posix() for p in files]}


def export_zip(run_dir: Path | str, zip_path: Path | str | None = None, *, parts: Iterable[str] = PARTS,
               versions: Iterable[int] | None = None, records: bool = True) -> Record:
    """export_run, then one zip: the exported files under export/ and, with records, the run's own
    files (project.json, events.jsonl, report.json, v<NNN>/) under their names in the run folder."""
    run = Path(run_dir)
    got = export_run(run, parts=parts, versions=versions)
    out = Path(got["out_dir"])
    target = Path(zip_path) if zip_path is not None else out / f"{run.name}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in got["files"]:
            z.write(out / rel, f"{EXPORT_DIR}/{rel}")
        if records:
            for p in sorted(run.rglob("*")):
                rel = p.relative_to(run)
                if p.is_file() and rel.parts[0] != EXPORT_DIR and p.resolve() != target.resolve():
                    z.write(p, rel.as_posix())
    return {"zip": str(target), "files": got["files"]}


__all__ = ["PARTS", "ExportError", "export_run", "export_zip", "read_csv", "read_vtk"]
