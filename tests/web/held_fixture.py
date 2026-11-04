"""A run whose assembly tile is green while a load case without a criterion counts something.

tests.web.domains_fixture's run with, in version 2, assembly made green and a load case H1-joints
(assembly, no criterion: shown, not judged, as the analysis department made it in 7fc0ffc) with
its prediction and analysis. The count (joint_gap_count = joint_gaps + unjoined_parts, as the
assembly tool writes them at 7fc0ffc) is given here: shape example only, not a calculation.
"""

from __future__ import annotations

import copy

from tests.web.domains_fixture import domain_events

HELD_LC = "H1-joints"


def held_events(gaps: float = 200.0, unjoined: float = 6.0) -> list[dict]:
    events = copy.deepcopy(domain_events())
    run = events[0]["data"]
    run["load_cases"].append({"load_case_id": HELD_LC, "test_id": "H1", "domain": "assembly", "params": {}, "tolerance_rel": 0.05,
                              "title": "つながりが触れているか (判定なし: 形の見本)"})
    # version 2's assembly judged green
    for ev in events:
        d = ev.get("data") or {}
        if ev["kind"] == "judgement.updated" and d.get("domain") == "assembly" and d.get("design_version") == 2:
            d.update({"state": "green", "red_count": 0, "summary": "合格 (形の見本)"})
    end = next(i for i, ev in enumerate(events) if ev["kind"] == "screen.focus")
    base = {"run_id": events[0]["run_id"], "ts": events[0]["ts"]}
    t = events[end]["t"] - 0.3
    total = gaps + unjoined
    pred = {"prediction_id": "pred-0901", "load_case_id": HELD_LC, "design_version": 2, "test_id": "H1", "quantity": "joint_gap_count",
            "formula_id": "assembly.joint_count", "expression": "n = n_gap + n_none", "value": total, "unit": "1",
            "inputs": {"n_gap": {"value": gaps, "unit": "1"}, "n_none": {"value": unjoined, "unit": "1"}}, "assumptions": []}
    ana = {"analysis_id": "ana-0901", "tool": "assembly", "base": "K4", "design_version": 2, "load_case_id": HELD_LC,
           "prediction_ids": ["pred-0901"], "status": "ok",
           "results": [{"quantity": "joint_gap_count", "value": total, "unit": "1"},
                       {"quantity": "joint_gaps", "value": gaps, "unit": "1"},
                       {"quantity": "unjoined_parts", "value": unjoined, "unit": "1"}]}
    added = [
        {**base, "t": t - 0.1, "kind": "prediction.recorded", "agent": "lead", "design_version": 2, "data": pred},
        {**base, "t": t, "kind": "analysis.finished", "agent": "lead", "design_version": 2, "data": ana},
    ]
    events[end:end] = added
    for i, ev in enumerate(events):
        ev["seq"] = i
    return events
