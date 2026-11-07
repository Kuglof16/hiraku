"""The record of a flooded-road pass (the tool cfd_water): what the run's folder keeps and what the tool answers.

The record says in its first field what a judgement on it covers (water.STATEMENT): only whether water reaches the battery, the motor
and the controller, by a calculation not compared with an experiment. physically_validated stays false. The verdict itself is the
run's own record: the tool, called with judge, goes through the prediction, analyze and compare (test F5).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hiraku.cfd.water import STATEMENT
from hiraku.cfd.water_coupled import vehicle_summary

Record = dict[str, Any]

KIND = "cfd_water_record"
VERSION = 1
TOOL = "cfd_water"
RECORD_FILE = "water_record.json"


def make_record(case: Record, *, design_version: int, requested: Record, kept_particles: bool, wall_time_s: float) -> Record:
    """The record of one pass, from the analysed case.json. The arrays stay in the case folder: case.json, replay.npz (and the
    particles, when asked to keep them)."""
    uf = case.get("underfloor", {})
    budget = case.get("momentum_budget", {})
    return {"statement": STATEMENT, "kind": KIND, "version": VERSION, "tool": TOOL, "design_version": int(design_version), "physically_validated": False,
            "requested": requested, "vehicle_moves": "coupling" in case, "faulted": case.get("faulted"),
            "road": {"water_level_mm": case["road"]["level_m"] * 1000.0, "water_length_m": case["road"]["water_length_m"], "ramp_slope": case["road"]["slope"]},
            "grid": {"dx_mm": case["inputs"]["dx_m"] * 1000.0, "particles": case["particles"], "cells": case["cells"], "frame_s": case["frame_s"], "frames": case["frames"], "steps": case["steps"], "device": case["device"]},
            "water": {"top_m": case.get("top_of_water_m"), "underfloor_depth_mean_largest_mm": uf.get("max_depth_mean_mm"), "underfloor_depth_largest_mm": uf.get("max_depth_mm"), "underfloor_reached_share_largest": uf.get("max_reached_share"),
                      "momentum_residual_over_gross": budget.get("residual_over_gross")},
            "protected_parts": case.get("protected_parts", {}), "protected_definition": case.get("protected_definition"),
            "vehicle": vehicle_summary(case["records"]) if "coupling" in case else None,
            "coupling": case.get("coupling"),
            "files": {"case": "case.json", "replay": "replay.npz", "particles": "frames/" if kept_particles else None},
            "assumptions": case.get("assumptions", []), "wall_time_s": wall_time_s}


def save_record(record: Record, folder: Path) -> Path:
    path = Path(folder) / RECORD_FILE
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return path


def load_record(path: Path) -> Record:
    """A saved record; ValueError when the file is not one."""
    try:
        record = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"{path}: {e}") from None
    if not isinstance(record, dict) or record.get("kind") != KIND:
        raise ValueError(f"{path}: not a {KIND}")
    return record


def answer(record: Record, path: str, replayed: bool) -> Record:
    """What the tool says about a record: the same for the run and for its replay, apart from `replayed`."""
    return {"statement": record["statement"], "path": path, "replayed": replayed, **{k: record[k] for k in ("design_version", "physically_validated", "vehicle_moves", "faulted", "road", "grid", "water", "protected_parts", "vehicle", "assumptions", "wall_time_s")}}
