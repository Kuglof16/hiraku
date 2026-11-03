"""Exports of a run's records (the A9 window calls these): CSV tables, VTK files for ParaView,
the report as Markdown and HTML, and a zip of them with the records. Everything is read from the
run folder; values are written as recorded, nothing is computed again.

    export_run(run_dir, out_dir=None, parts=PARTS, versions=None, domains=None, diff_from=None)
        -> {"out_dir", "files"}
    export_zip(run_dir, zip_path=None, parts=PARTS, versions=None, domains=None, diff_from=None,
               records=True) -> {"zip", "files"}

parts: "comparisons", "results", "series" (CSV), "vtk", "report". versions: the design versions
to include (all when None). domains: the domains to include (all when None). diff_from: an
earlier version to set the results beside. Files go to <run_dir>/export/ unless out_dir is given.
A choice the run cannot meet raises ExportError (a ValueError) with a sentence for the screen.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any, Iterable

from hiraku.export.report import write_report
from hiraku.export.tables import (COMPARISON_COLUMNS, DIFF_COLUMNS, RESULT_COLUMNS, comparison_rows, diff_rows,
                                  read_csv, result_rows, write_csv, write_series)
from hiraku.export.vtk import read_vtk, write_fields
from hiraku.store import RunStore

Record = dict[str, Any]

PARTS = ("comparisons", "results", "series", "vtk", "report")
EXPORT_DIR = "export"


class ExportError(ValueError):
    pass


def _check(store: RunStore, parts: Iterable[str], versions: Iterable[int] | None, domains: Iterable[str] | None,
           diff_from: int | None) -> tuple[list[str], list[int] | None, list[str] | None]:
    """The choices, or ExportError with a sentence the screen shows as is."""
    parts = list(parts)
    bad = [p for p in parts if p not in PARTS]
    if bad:
        raise ExportError(f"書き出せないもの: {', '.join(map(str, bad))}。選べるのは {', '.join(PARTS)} です")
    have = sorted(store.project.get("design_versions") or [])
    if versions is not None:
        versions = [int(v) for v in versions]
        missing = [v for v in versions if v not in have]
        if missing:
            raise ExportError(f"版 {', '.join(map(str, missing))} はこの実行にありません (ある版: "
                              f"{', '.join(map(str, have)) or 'なし'})")
    if domains is not None:
        domains = list(domains)
        known = sorted({lc["domain"] for lc in store.project.get("load_cases", [])})
        missing = [d for d in domains if d not in known]
        if missing:
            raise ExportError(f"分野 {', '.join(missing)} はこの実行にありません (ある分野: {', '.join(known) or 'なし'})")
    if diff_from is not None:
        if int(diff_from) not in have:
            raise ExportError(f"比べる前の版 {diff_from} はこの実行にありません (ある版: {', '.join(map(str, have)) or 'なし'})")
        if [int(diff_from)] == (versions or have[-1:]):
            raise ExportError(f"比べる前の版 {diff_from} と、書き出す版が同じです。別の版を選んでください")
    return parts, versions, domains


def export_run(run_dir: Path | str, out_dir: Path | str | None = None, *, parts: Iterable[str] = PARTS,
               versions: Iterable[int] | None = None, domains: Iterable[str] | None = None,
               diff_from: int | None = None) -> Record:
    """domains: only those domains' load cases (the report keeps the values not checked and the
    requirements with their sources). diff_from: results_diff.csv, the same results of that
    version and of `versions` (the last version when None) side by side."""
    store = RunStore.open(Path(run_dir))
    parts, versions, domains = _check(store, parts, versions, domains, diff_from)
    out = Path(out_dir) if out_dir is not None else store.path / EXPORT_DIR
    files: list[Path] = []
    if "comparisons" in parts:
        files.append(write_csv(out / "comparisons.csv", COMPARISON_COLUMNS, comparison_rows(store, versions, domains)))
    if "results" in parts:
        files.append(write_csv(out / "results.csv", RESULT_COLUMNS, result_rows(store, versions, domains)))
    if diff_from is not None:
        to = versions or sorted(store.project.get("design_versions") or [])[-1:]
        files.append(write_csv(out / "results_diff.csv", DIFF_COLUMNS, diff_rows(store, int(diff_from), to, domains)))
    if "series" in parts:
        files += write_series(store, out / "series", versions, domains)
    if "vtk" in parts:
        files += write_fields(store, out / "vtk", versions, domains)
    if "report" in parts:
        files += write_report(store, out, domains)
    return {"out_dir": str(out), "files": [p.relative_to(out).as_posix() for p in files]}


def export_zip(run_dir: Path | str, zip_path: Path | str | None = None, *, parts: Iterable[str] = PARTS,
               versions: Iterable[int] | None = None, domains: Iterable[str] | None = None,
               diff_from: int | None = None, records: bool = True) -> Record:
    """export_run, then one zip: the exported files under export/ and, with records, the run's own
    files (project.json, events.jsonl, report.json, v<NNN>/) under their names in the run folder."""
    run = Path(run_dir)
    got = export_run(run, parts=parts, versions=versions, domains=domains, diff_from=diff_from)
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
