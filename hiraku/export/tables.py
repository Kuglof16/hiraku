"""CSV tables of a run's records: comparisons, analysis results, series. Values are the
records' own (nothing is computed again); a missing value is an empty cell. UTF-8 with a BOM,
so a spreadsheet reads the Japanese text."""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any, Iterable

from hiraku.store import RunStore

Record = dict[str, Any]

ENCODING = "utf-8-sig"

COMPARISON_COLUMNS = (
    "comparison_id", "design_version", "load_case_id", "test_id", "domain", "quantity",
    "predicted", "analyzed", "unit", "diff_abs", "diff_rel", "tolerance_rel", "status",
    "prediction_id", "formula_id", "analysis_id", "tool", "cause_id", "cause",
    "verdict_id", "verdict_value", "op", "limit", "limit_unit", "pass", "verdict_basis",
)
RESULT_COLUMNS = ("analysis_id", "design_version", "load_case_id", "tool", "base", "status", "quantity", "value",
                  "unit", "where")


def write_csv(path: Path, columns: Iterable[str], rows: Iterable[Record]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=ENCODING, newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(columns), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in r.items()})
    return path


def read_csv(path: Path) -> list[Record]:
    """The rows of a table written here (strings, as a spreadsheet reads them)."""
    with Path(path).open(encoding=ENCODING, newline="") as f:
        return list(csv.DictReader(f))


def _versions(store: RunStore, versions: Iterable[int] | None) -> list[int]:
    have = sorted(store.project.get("design_versions") or [])
    return [v for v in have if versions is None or v in set(versions)]


def comparison_rows(store: RunStore, versions: Iterable[int] | None = None) -> list[Record]:
    load_cases = {lc["load_case_id"]: lc for lc in store.project.get("load_cases", [])}
    rows = []
    for v in _versions(store, versions):
        verdicts = {x["comparison_id"]: x for x in store.records("verdict", v)}
        causes = {c["cause_id"]: c for c in store.records("cause", v)}
        for c in store.records("comparison", v):
            pred = store.get("prediction", c["prediction_id"]) or {}
            ana = store.get("analysis", c["analysis_id"]) or {}
            lc = load_cases.get(pred.get("load_case_id") or ana.get("load_case_id"), {})
            ver = verdicts.get(c["comparison_id"], {})
            cause = causes.get(c.get("cause_id") or "", {})
            rows.append({
                "comparison_id": c["comparison_id"], "design_version": v,
                "load_case_id": lc.get("load_case_id"), "test_id": lc.get("test_id"), "domain": lc.get("domain"),
                "quantity": c["quantity"], "predicted": c["predicted"]["value"], "analyzed": c["analyzed"]["value"],
                "unit": c["predicted"]["unit"], "diff_abs": c.get("diff_abs"), "diff_rel": c.get("diff_rel"),
                "tolerance_rel": c.get("tolerance_rel"), "status": c.get("status"),
                "prediction_id": c["prediction_id"], "formula_id": pred.get("formula_id"),
                "analysis_id": c["analysis_id"], "tool": ana.get("tool"),
                "cause_id": c.get("cause_id"), "cause": cause.get("cause"),
                "verdict_id": ver.get("verdict_id"), "verdict_value": (ver.get("value") or {}).get("value"),
                "op": ver.get("op"), "limit": (ver.get("limit") or {}).get("value"),
                "limit_unit": (ver.get("limit") or {}).get("unit"), "pass": ver.get("pass"),
                "verdict_basis": ver.get("basis"),
            })
    return rows


def result_rows(store: RunStore, versions: Iterable[int] | None = None) -> list[Record]:
    rows = []
    for v in _versions(store, versions):
        for a in store.records("analysis", v):
            for r in a.get("results", []):
                rows.append({"analysis_id": a["analysis_id"], "design_version": v, "load_case_id": a["load_case_id"],
                             "tool": a["tool"], "base": a.get("base"), "status": a["status"],
                             "quantity": r["quantity"], "value": r["value"], "unit": r["unit"], "where": r.get("where")})
    return rows


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "series"


def _axis(s: Record) -> tuple[str, list[Any]]:
    """The series' axis: x when it has one, else t_s, else the sample index."""
    if s.get("x"):
        return f"{s['x']['quantity']} [{s['x']['unit']}]", list(s["x"]["values"])
    if s.get("t_s") is not None:
        return "t [s]", list(s["t_s"])
    return "index", list(range(len(s["values"])))


def write_series(store: RunStore, folder: Path, versions: Iterable[int] | None = None) -> list[Path]:
    """Per analysis, one CSV for each axis its series share (<analysis_id>-series-<k>.csv): the
    axis in the first column, then one column per channel ("channel [unit]")."""
    out = []
    for v in _versions(store, versions):
        for a in store.records("analysis", v):
            groups: dict[tuple, list[Record]] = {}
            for s in a.get("series", []):
                head, axis = _axis(s)
                groups.setdefault((head, tuple(axis)), []).append(s)
            for k, ((head, axis), series) in enumerate(groups.items(), 1):
                path = folder / f"{a['analysis_id']}-series-{k}.csv"
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("w", encoding=ENCODING, newline="") as f:
                    w = csv.writer(f)
                    w.writerow([head] + [f"{s['channel']} [{s['unit']}]" for s in series])
                    w.writerows([x, *(s["values"][i] for s in series)] for i, x in enumerate(axis))
                out.append(path)
    return out
