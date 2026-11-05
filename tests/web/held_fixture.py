"""A run whose assembly tile is green while a load case without a criterion counts something.

tests.web.domains_fixture's run with, in version 2, assembly made green and a load case H1-joints
(assembly, no criterion: shown, not judged, as the analysis department made it in 7fc0ffc) with
its prediction and analysis. The count (joint_gap_count = joint_gaps + unjoined_parts, as the
assembly tool writes them at 7fc0ffc) is given here: shape example only, not a calculation.
In version 2 also three load cases without a criterion whose quantities are not counts
(REFERENCE_CASES: compared only, handing on, a plain reference), shown as rows not coloured.
"""

from __future__ import annotations

import copy

from tests.web.domains_fixture import domain_events

HELD_LC = "H1-joints"

# (load_case_id, test_id, domain, quantity, value, unit, more of the load case) and the kind
# web/js/held.js gives each (NO_CRITERION_KINDS): shape examples, not calculations
REFERENCE_CASES = (
    ("S5-slope15", "S5", "handling", "mean_uphill_load", 2369.0, "N", {}),
    ("P1-step100", "P1", "powertrain", "front_wheel_load", 1167.0, "N", {"feeds": ["R1-v"]}),
    ("A9-gust", "A9", "aero", "side_force", 420.0, "N", {}),
)
REFERENCE_KINDS = {"S5-slope15": "compare", "P1-step100": "relay", "A9-gust": "reference"}
REFERENCE_TITLES = {"S5-slope15": "坂を斜めに走る", "P1-step100": "段差を越える力", "A9-gust": "突風の横力"}


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
    # quantities without a criterion that are not counts (held.js: not held, rows not coloured):
    # S5 compared only (k2_tests.py "S5 has no criterion"), P1 handing its force on (feeds), and
    # one plain reference quantity; right after version 2's design, so each domain's own analysis
    # stays its latest (the tile's picture)
    at = next(i for i, ev in enumerate(events) if ev["kind"] == "design.created" and ev["data"]["design_version"] == 2) + 1
    t = events[at - 1]["t"]
    added = []
    for k, (lc_id, test_id, domain, quantity, value, unit, extra) in enumerate(REFERENCE_CASES):
        run["load_cases"].append({"load_case_id": lc_id, "test_id": test_id, "domain": domain, "params": {}, "tolerance_rel": 0.1,
                                  "title": f"{REFERENCE_TITLES[lc_id]} (形の見本)", **extra})
        pid, aid = f"pred-091{k}", f"ana-091{k}"
        added += [
            {**base, "t": t, "kind": "prediction.recorded", "agent": f"cae.{domain}", "design_version": 2,
             "data": {"prediction_id": pid, "load_case_id": lc_id, "design_version": 2, "test_id": test_id, "quantity": quantity,
                      "formula_id": "shape.example", "expression": "x = a", "value": value, "unit": unit, "inputs": {}, "assumptions": []}},
            {**base, "t": t, "kind": "analysis.finished", "agent": f"cae.{domain}", "design_version": 2, "domain": domain,
             "data": {"analysis_id": aid, "tool": "shape_example", "base": "K2", "design_version": 2, "load_case_id": lc_id,
                      "prediction_ids": [pid], "status": "ok", "results": [{"quantity": quantity, "value": value, "unit": unit}]}},
        ]
    events[at:at] = added
    for i, ev in enumerate(events):
        ev["seq"] = i
    return events
