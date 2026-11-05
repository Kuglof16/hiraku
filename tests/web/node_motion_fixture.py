"""A run with one crash analysis carrying a node_motion field, for the screen's tests.

The analysis is the record of the K2 tool crash_beam (branch claude/k2-crash-beam, commit f605e3b8)
run on data/designs/offroad-4w-v1.json at 40 km/h into the rigid wall with loading two_cargo
(node_motion_records/offroad-4w-v1-crash40.json). The tool's output, cut down: every node (156) and
member line (166), 14 of its 122 frames (t 0 .. 94.9 ms; frames 16 and 17 are the last before and
the first after the first member broke), positions rounded to 0.1 mm, the series rounded. Nothing
else is changed. The prediction is a shape example only (no calculation behind its value).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from hiraku.webserve.server import SAMPLE_EVENTS

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RECORD = HERE / "node_motion_records" / "offroad-4w-v1-crash40.json"
DESIGN = REPO / "data" / "designs" / "offroad-4w-v1.json"
RUN_ID = "20261106-200000-nodemotion"
T_ANALYSIS = 6.0  # s: when the analysis arrives in the replay


def record() -> dict:
    return json.loads(RECORD.read_text(encoding="utf-8"))


def node_motion_events(rec: dict | None = None) -> list[dict]:
    rec = copy.deepcopy(rec if rec is not None else record())
    src = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    run = copy.deepcopy(src[0])
    created = copy.deepcopy(next(e for e in src if e["kind"] == "design.created"))
    created["data"] = json.loads(DESIGN.read_text(encoding="utf-8"))
    lc = {"load_case_id": rec["load_case_id"], "test_id": "C1", "domain": "crash", "title": "壁に当たる",
          "params": {"speed": {"value": 40.0, "unit": "km/h"}, "loading": "two_cargo"}, "tolerance_rel": 0.15}
    run["data"]["load_cases"] = [lc]
    run["data"]["domain_picks"] = [{"domain": "crash", "shown_name": "衝突", "reason": "壁に当たったときの骨組み (形の見本)"}]
    run["data"]["agents"] = [{"agent_id": "lead", "role": "lead", "prop": "binder"}, {"agent_id": "cae.crash", "role": "cae", "domain": "crash", "prop": "steering_wheel"}]
    ana = {**rec, "analysis_id": "ana-0001"}
    pred = {"prediction_id": "pred-0001", "design_version": 1, "load_case_id": lc["load_case_id"], "test_id": "C1", "domain": "crash",
            "agent": "cae.crash", "quantity": "max_crush", "value": 500.0, "unit": "mm", "formula_id": "crash.shape_example",
            "expression": "shape example", "inputs": {}, "assumptions": ["shape example"], "created_at": run["ts"]}
    base = {"run_id": run["run_id"], "ts": run["ts"]}
    events = [
        run,
        created,
        {**base, "t": 2.0, "kind": "agent.started", "agent": "cae.crash", "domain": "crash", "data": {"agent_id": "cae.crash"}},
        {**base, "t": 3.0, "kind": "tool.checked", "agent": "system",
         "data": {"name": "crash_beam", "title": "梁の骨組みが壁に当たる (K2)", "domain": "crash", "base": "K2", "tests": ["C1"], "origin": "builtin", "usable": True, "checks": []}},
        {**base, "t": 4.0, "kind": "prediction.recorded", "agent": "cae.crash", "domain": "crash", "design_version": 1, "data": pred},
        {**base, "t": 5.0, "kind": "analysis.started", "agent": "cae.crash", "domain": "crash", "design_version": 1,
         "data": {"analysis_id": "ana-0001", "tool": "crash_beam", "load_case_id": lc["load_case_id"], "prediction_ids": ["pred-0001"]}},
        {**base, "t": T_ANALYSIS, "kind": "analysis.finished", "agent": "cae.crash", "domain": "crash", "design_version": 1, "data": ana},
        {**base, "t": T_ANALYSIS + 0.5, "kind": "run.finished", "agent": "system", "data": {"status": "done"}},
    ]
    for i, ev in enumerate(events):
        ev["seq"] = i
    return events
