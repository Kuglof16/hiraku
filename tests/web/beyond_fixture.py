"""The A6 fixture (domains_fixture) with load cases past the limit (id ending -beyond, title
starting 限界の外), for the grid cuts.

Shape example only: the verdicts and values are made up to show each case the screen tells
apart. In both versions:
- strength: R1-v passes, R1-step250-beyond fails as meant; the record's judgement is red
  (judge.py counts every verdict), red only through the case past the limit.
- thermal: T1-v fails in version 1 and passes in version 2, T1-52C-beyond fails as meant; the
  record's judgement is red in both (version 1 also through T1-v).
- handling: S1-v passes and S1-tilt-beyond passes too (meant to fail, it did not); green.
"""

from __future__ import annotations

import copy

from tests.web.domains_fixture import domain_events

BEYOND_CASES = {
    "strength": ("R1-step250-beyond", "R1", "限界の外: 段差 250 mm (形の見本)", "max_stress", "MPa", 156.7),
    "thermal": ("T1-52C-beyond", "T1", "限界の外: 気温 52 ℃ (形の見本)", "temp_battery_max", "degC", 60.0),
    "handling": ("S1-tilt-beyond", "S1", "限界の外: 傾き 40 度 (形の見本)", "tilt_margin", "deg", 0.0),
}
# Whether each verdict passes: {domain: {version: (the domain's own case, the case past the limit)}}.
PASSES = {
    "strength": {1: (True, False), 2: (True, False)},
    "thermal": {1: (False, False), 2: (True, False)},
    "handling": {1: (True, True), 2: (True, True)},
}
# What the screen shows: the domain's colour without the cases past the limit, per version.
SHOWN = {
    "strength": {1: "green", 2: "green"},
    "thermal": {1: "red", 2: "green"},
    "handling": {1: "green", 2: "green"},
}


def _verdict(vid, lc_id, test_id, domain, quantity, unit, limit, passed):
    value = limit * (0.8 if passed else 1.2) if limit else (1.0 if passed else -1.0)
    return {"verdict_id": vid, "comparison_id": f"cmp-{vid}", "load_case_id": lc_id, "test_id": test_id, "domain": domain,
            "quantity": quantity, "value": {"value": round(value, 3), "unit": unit}, "op": "<=" if limit else ">=",
            "limit": {"value": limit, "unit": unit}, "pass": passed, "margin_pct": 20.0 if passed else -20.0}


def beyond_events():
    src = domain_events()
    out = copy.deepcopy(src)
    run = out[0]["data"]
    for domain, (lc_id, tid, title, q, unit, lim) in BEYOND_CASES.items():
        run["load_cases"].append({"load_case_id": lc_id, "test_id": tid, "domain": domain, "title": title, "params": {},
                                  "tolerance_rel": 0.15, "criterion": {"quantity": q, "op": "<=" if lim else ">=", "limit": {"value": lim, "unit": unit}, "basis": "形の見本"}})
    events = []
    for ev in out:
        d = ev.get("data") or {}
        if ev["kind"] == "judgement.updated" and d.get("domain") in BEYOND_CASES:
            domain, v = d["domain"], d["design_version"]
            lc_id, _tid, _title, q, unit, lim = BEYOND_CASES[domain]
            own_lc = next(c for c in run["load_cases"] if c["domain"] == domain and not c["load_case_id"].endswith("-beyond"))
            own_ok, past_ok = PASSES[domain][v]
            vs = [_verdict(f"ver-{domain}-{v}-own", own_lc["load_case_id"], own_lc["test_id"], domain, q, unit, lim, own_ok),
                  _verdict(f"ver-{domain}-{v}-beyond", lc_id, BEYOND_CASES[domain][1], domain, q, unit, lim, past_ok)]
            for x in vs:
                events.append({"run_id": ev["run_id"], "ts": ev["ts"], "kind": "verdict.recorded", "agent": f"cae.{domain}", "domain": domain,
                               "design_version": v, "data": x})
            reds = sum(not x["pass"] for x in vs)
            # as judge.py compute_judgement records it: red when any verdict fails
            d.update({"state": "red" if reds else "green", "red_count": reds, "verdict_ids": [x["verdict_id"] for x in vs],
                      "summary": f"{'不合格' if reds else '合格'} (形の見本)"})
        events.append(ev)
    for i, ev in enumerate(events):
        ev["seq"] = i
        if i >= 2:
            ev["t"] = 1.0 + i * 0.5
    return events
