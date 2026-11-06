"""A small run cut from a showcase record, for the energy's flow (web/js/energy_flow.js).

The record is showcase/20261105-150416-offroad-4w-scripted (the scripted offroad-4w run). Kept:
the run's start (its load cases cut to P4 and the thermal ones), design version 5, its load
cases, the two tools' checks (drive_energy, thermal), and of version 5 the predictions, the four
analyses (P4 drive_energy, T1 and the two T2 thermal ones), their comparisons, verdicts and
judgements. The events keep their data; only seq and t are new (0.25 s apart, then the run's end
20 s later, so a thermal record's run plays to its end on the bench).

Read from the record at test time (the 50 MB file is read line by line once per process).
Variants for the tests (shape examples, not records): over_limit_events puts the battery's
temperature 8 degC up and its T1 verdict to a fail; second_battery_events adds a second battery
part to the design (no series names it).
"""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECORD = ROOT / "showcase" / "20261105-150416-offroad-4w-scripted" / "events.jsonl"
VERSION = 5
LOAD_CASES = ("P4-20km", "T1-40C", "T2-40C-motor", "T2-40C-controller")
ANALYSES = ("ana-0114", "ana-0115", "ana-0116", "ana-0117")
STEP_S = 0.25
END_GAP_S = 20.0
RUN_ID = "20261106-120000-heat"


@lru_cache(maxsize=1)
def _cut() -> tuple[str, ...]:
    keep = []
    comparisons = set()
    with RECORD.open(encoding="utf-8") as f:
        for line in f:
            ev = json.loads(line)
            k = ev["kind"]
            d = ev.get("data") or {}
            v = d.get("design_version", ev.get("design_version"))
            if k == "run.started":
                d["load_cases"] = [c for c in d.get("load_cases", []) if c["load_case_id"] in LOAD_CASES]
            elif k == "design.created":
                if v != VERSION:
                    continue
            elif k == "load_cases.created":
                if v != VERSION:
                    continue
                d["load_cases"] = [c for c in d["load_cases"] if c["load_case_id"] in LOAD_CASES]
            elif k == "tool.checked":
                if d.get("name") not in ("drive_energy", "thermal"):
                    continue
            elif k == "agent.started":
                if ev.get("agent") not in ("cae.thermal", "cae.powertrain"):
                    continue
            elif k == "prediction.recorded":
                if v != VERSION or d.get("load_case_id") not in LOAD_CASES:
                    continue
            elif k in ("analysis.started", "analysis.finished"):
                if d.get("analysis_id") not in ANALYSES:
                    continue
            elif k == "comparison.recorded":
                if d.get("analysis_id") not in ANALYSES:
                    continue
                comparisons.add(d["comparison_id"])
            elif k == "verdict.recorded":
                if d.get("comparison_id") not in comparisons:
                    continue
            elif k == "judgement.updated":
                if v != VERSION or d.get("domain") not in ("thermal", "powertrain"):
                    continue
            elif k == "run.finished":
                pass
            else:
                continue
            keep.append(json.dumps(ev, ensure_ascii=False))
    return tuple(keep)


def heat_events() -> list[dict]:
    """The cut events, numbered again (seq from 0, t STEP_S apart, the end END_GAP_S later)."""
    out = []
    events = [json.loads(s) for s in _cut()]
    for i, ev in enumerate(events):
        ev["seq"] = i
        ev["run_id"] = RUN_ID
        ev["t"] = round(i * STEP_S, 3) if ev["kind"] != "run.finished" else round((i - 1) * STEP_S + END_GAP_S, 3)
        out.append(ev)
    if out and out[0]["kind"] == "run.started":
        out[0]["data"]["run_id"] = RUN_ID
    return out


def finished(events: list[dict], analysis_id: str) -> dict:
    return next(e for e in events if e["kind"] == "analysis.finished" and e["data"]["analysis_id"] == analysis_id)


def arrival(events: list[dict], analysis_id: str) -> float:
    return finished(events, analysis_id)["t"]


def over_limit_events(up: float = 8.0) -> list[dict]:
    """Shape example: the thermal records' temp.battery up by `up` degC, T1's verdict a fail."""
    events = copy.deepcopy(heat_events())
    for e in events:
        if e["kind"] == "analysis.finished" and e["data"]["tool"] == "thermal":
            for s in e["data"]["series"]:
                if s["channel"] == "temp.battery":
                    s["values"] = [v + up for v in s["values"]]
        if e["kind"] == "verdict.recorded" and e["data"]["quantity"] == "temp_battery_max":
            e["data"]["value"]["value"] += up
            e["data"]["pass"] = e["data"]["value"]["value"] <= e["data"]["limit"]["value"]
    return events


def second_battery_events() -> list[dict]:
    """Shape example: a second battery part (battery2) beside the first; no series names it."""
    events = copy.deepcopy(heat_events())
    for e in events:
        if e["kind"] == "design.created":
            parts = e["data"]["parts"]
            b = next(p for p in parts if p["id"] == "battery")
            b2 = copy.deepcopy(b)
            b2["id"] = "battery2"
            b2["name"] = "電池 2 (形の見本)"
            b2["connects_to"] = [{"to": "battery", "via": "wire", "carries": "power"}]
            c = b2["box"]["center_mm"]
            b2["box"]["center_mm"] = [c[0] - b["box"]["size_mm"][0] - 40, c[1], c[2]]
            parts.append(b2)
    return events


def write_run(root: Path, events: list[dict], run_id: str = RUN_ID) -> None:
    (root / run_id).mkdir(parents=True, exist_ok=True)
    (root / run_id / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
