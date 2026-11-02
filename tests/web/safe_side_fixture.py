"""A run whose strength test is judged from an explained safe-side mismatch.

Shape example built on the contract sample: two predictions go into the first
analysis and only one is compared; it mismatches, a cause is written, the
prediction is made again and mismatches again (now on the safe side: the
prediction is the larger value against a `<=` limit), the cause says so, and
the verdict is made from the prediction. The values are made up; the
differences and the margin are computed here from them.
"""

from __future__ import annotations

import copy
import json

from hiraku.webserve.server import SAMPLE_EVENTS

PRED_1 = 90.0  # MPa, first prediction (below the analysis: not the safe side)
PRED_2 = 150.0  # MPa, second prediction (above the analysis: the safe side)
ANALYSED = 120.0  # MPa
DISPLACEMENT_PRED = 1.0  # mm, a second quantity of the first analysis, never compared
TOL = 0.15
SAFE_REASON = "the hand formula carries the whole load on one rail, so it stays above the 3D frame (shape example)"


def _cmp(cid, pid, aid, pred, ana, cause_id=None):
    return {
        "comparison_id": cid, "prediction_id": pid, "analysis_id": aid, "quantity": "max_stress",
        "predicted": {"value": pred, "unit": "MPa"}, "analyzed": {"value": ana, "unit": "MPa"},
        "diff_abs": ana - pred, "diff_rel": (ana - pred) / abs(pred), "tolerance_rel": TOL,
        "status": "match" if abs((ana - pred) / pred) <= TOL else "mismatch", "cause_id": cause_id,
    }


def safe_side_events():
    src = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    head = [copy.deepcopy(src[i]) for i in (0, 1, 2, 3)]
    pred_ev, ana_start, ana_end, verdict_ev = (copy.deepcopy(src[i]) for i in (6, 7, 8, 10))
    limit = verdict_ev["data"]["limit"]["value"]
    base = {"run_id": head[0]["run_id"], "ts": "2026-11-02T20:30:10.000+09:00"}
    strength = {"agent": "cae.strength", "domain": "strength", "design_version": 1}

    def pred(pid, quantity, value, unit):
        d = {**copy.deepcopy(pred_ev["data"]), "prediction_id": pid, "quantity": quantity, "value": value, "unit": unit}
        return {"kind": "prediction.recorded", **strength, "data": d}

    def analysis(aid, pids, results):
        start = {**ana_start["data"], "analysis_id": aid, "prediction_ids": pids}
        end = {**copy.deepcopy(ana_end["data"]), "analysis_id": aid, "prediction_ids": pids, "results": results}
        return [{"kind": "analysis.started", **strength, "data": start}, {"kind": "analysis.finished", **strength, "data": end}]

    stress = {"quantity": "max_stress", "value": ANALYSED, "unit": "MPa", "where": "rail_L0"}
    cmp1 = _cmp("cmp-0001", "pred-0001", "ana-0001", PRED_1, ANALYSED)
    cmp2 = _cmp("cmp-0002", "pred-0003", "ana-0002", PRED_2, ANALYSED)
    cause1 = {"cause_id": "cause-0001", "comparison_id": "cmp-0001", "cause": "one rail only (shape example)",
              "fix": "predict again with the load shared by both rails", "resolved": False}
    cause2 = {"cause_id": "cause-0002", "comparison_id": "cmp-0002", "cause": "the cross members share the load (shape example)",
              "fix": "none needed for the verdict", "resolved": False, "safe_side": True, "safe_side_reason": SAFE_REASON}
    verdict = {
        **verdict_ev["data"], "comparison_id": "cmp-0002", "value": {"value": PRED_2, "unit": "MPa"},
        "pass": PRED_2 <= limit, "margin_pct": (limit - PRED_2) / limit * 100.0,
        "basis": "explained_safe_side", "value_from": "prediction", "cause_id": "cause-0002",
    }
    more = [
        pred("pred-0001", "max_stress", PRED_1, "MPa"),
        pred("pred-0002", "max_displacement", DISPLACEMENT_PRED, "mm"),
        *analysis("ana-0001", ["pred-0001", "pred-0002"], [stress, {"quantity": "max_displacement", "value": 1.2, "unit": "mm", "where": "LE"}]),
        {"kind": "comparison.recorded", **strength, "data": cmp1},
        {"kind": "hook.denied", "agent": "cae.strength", "domain": "strength", "text": "cause first (shape example)",
         "data": {"hook": "PreToolUse", "tool": "mcp__hiraku__analyze", "rule": "HK3_cause_before_continue"}},
        {"kind": "cause.recorded", **strength, "data": cause1},
        {"kind": "comparison.recorded", **strength, "data": {**cmp1, "cause_id": "cause-0001"}},
        pred("pred-0003", "max_stress", PRED_2, "MPa"),
        *analysis("ana-0002", ["pred-0003"], [stress]),
        {"kind": "comparison.recorded", **strength, "data": cmp2},
        {"kind": "cause.recorded", **strength, "data": cause2},
        {"kind": "comparison.recorded", **strength, "data": {**cmp2, "cause_id": "cause-0002"}},
        {"kind": "verdict.recorded", **strength, "data": verdict},
        {"kind": "judgement.updated", "agent": "lead", "domain": "strength", "design_version": 1,
         "data": {"design_version": 1, "domain": "strength", "state": "green", "red_count": 0, "verdict_ids": ["ver-0001"],
                  "summary": "judged from the prediction on the safe side (shape example)"}},
        {"kind": "agent.stopped", "agent": "cae.strength", "data": {"agent_id": "cae.strength"}},
        {"kind": "run.finished", "agent": "system", "data": {"status": "done"}},
    ]
    out = head
    t = out[-1]["t"]
    for ev in more:
        t += 0.5
        out.append({"seq": 0, "t": t, **base, **ev})
    for i, ev in enumerate(out):
        ev["seq"] = i
    return out
