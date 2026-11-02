"""A run that touches all seven domains and assembly over two design versions, for the A6 tiles.

Shape example only: every value here is made up to show each kind of record
(stress, fatigue damage, temperature, mode shape, series, interference) on the
screen. None of it is a calculation result.
"""

from __future__ import annotations

import copy
import json
import math

from hiraku.webserve.server import SAMPLE_EVENTS

DOMAIN_TESTS = {
    "crash": ("C1", "正面の壁にぶつかる (形の見本)"),
    "nvh": ("N1", "フレームの 1 次のねじれ (形の見本)"),
    "strength": ("R1", "段差の力 (形の見本)"),
    "thermal": ("T1", "長い上り坂で部品の温度 (形の見本)"),
    "handling": ("S1", "傾斜台 (形の見本)"),
    "powertrain": ("P2", "0 → 40 km/h (形の見本)"),
    "aero": ("A1", "横風 (形の見本)"),
    "assembly": ("M1", "組み立ての順番と干渉 (形の見本)"),
}
AGENT = {d: ("lead" if d == "assembly" else f"cae.{d}") for d in DOMAIN_TESTS}
V1_RED = {"crash", "nvh", "thermal", "assembly"}
V2_RED = {"crash", "assembly"}


def _series(name, unit, t, values):
    return {"channel": name, "unit": unit, "t_s": t, "values": [round(v, 4) for v in values]}


def _analysis(i, domain, version, design):
    lc = f"{DOMAIN_TESTS[domain][0]}-v"
    members = [m["id"] for m in design["members"]]
    nodes = {n["id"]: n["xyz_mm"] for n in design["nodes"]}
    a = {
        "analysis_id": f"ana-{i:04d}", "tool": f"{domain}_shape_example", "base": "K1", "design_version": version,
        "load_case_id": lc, "prediction_ids": [f"pred-{i:04d}"], "status": "ok", "results": [],
    }
    t = [k * 0.1 for k in range(51)]
    if domain == "strength":
        a["fields"] = [
            {"kind": "stress", "label": "von Mises", "unit": "MPa", "per_member": {m: 40.0 + 12.0 * k for k, m in enumerate(members)}},
            {"kind": "damage", "label": "疲労度", "unit": "1", "per_member": {m: 0.08 * (k + 1) for k, m in enumerate(members)}},
        ]
        a["results"] = [{"quantity": "max_stress", "value": 40.0 + 12.0 * (len(members) - 1), "unit": "MPa", "where": members[-1]}]
    elif domain == "nvh":
        a["fields"] = [{"kind": "mode_shape", "label": "1 次", "unit": "1", "frequency_Hz": 23.4,
                        "per_node": {nid: [0.0, 0.0, (1.0 if p[1] > 0 else -1.0) * (p[0] / -1800.0)] for nid, p in nodes.items()}}]
    elif domain == "thermal":
        a["fields"] = [{"kind": "temperature", "label": "部品の温度", "unit": "degC",
                        "per_part": {"battery": 43.0 if version > 1 else 63.0, "seat": 31.0}}]
    elif domain == "crash":
        a["series"] = [_series("decel", "g", t, [18.0 * math.sin(math.pi * min(x, 2.0) / 2.0) if x <= 2.0 else 0.0 for x in t])]
        a["base"] = "K2"
    elif domain == "handling":
        a["series"] = [_series(f"wheel_load.{w}", "N", t, [1200.0 + s * 25.0 * x for x in t]) for w, s in (("FL", -1), ("FR", 1), ("RL", -1), ("RR", 1))]
        a["base"] = "K2"
    elif domain == "powertrain":
        a["series"] = [_series("speed", "km/h", t, [40.0 * (1 - math.exp(-x / 1.5)) for x in t])]
        a["base"] = "K3"
    elif domain == "aero":
        a["series"] = [_series("yaw", "deg", t, [2.0 * (1 - math.exp(-x)) for x in t])]
        a["base"] = "K2"
    elif domain == "assembly":
        a["base"] = "K4"
        a["assembly"] = [
            {"order": 1, "part": "seat", "direction": "-z", "no_interference": True, "tool_clearance": True, "hand_reach": True},
            {"order": 2, "part": "battery", "direction": "-z", "depends_on": ["seat"], "no_interference": version > 1,
             "tool_clearance": True, "hand_reach": True, **({} if version > 1 else {"blocked_by": ["cross1"]})},
        ]
        a["interferences"] = [{"a": "battery", "b": "cross1", "depth_mm": 8.0, "state": "rest"}]
    return a


def domain_events():
    src = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    run, d1_ev = copy.deepcopy(src[0]), copy.deepcopy(src[1])
    run["data"]["load_cases"] = [
        {"load_case_id": f"{tid}-v", "test_id": tid, "domain": d, "title": title, "params": {}, "tolerance_rel": 0.15,
         **({"criterion": {"quantity": "temperature", "op": "<=", "limit": {"value": 60.0, "unit": "degC"}, "basis": "電池の上限 (形の見本)"}} if d == "thermal" else {}),
         **({"criterion": {"quantity": "max_stress", "op": "<=", "limit": {"value": 156.7, "unit": "MPa"}, "basis": "形の見本"}} if d == "strength" else {})}
        for d, (tid, title) in DOMAIN_TESTS.items()
    ]
    run["data"]["domain_picks"] = [{"domain": d, "shown_name": d, "reason": "形の見本"} for d in DOMAIN_TESTS]
    run["data"]["round_limit"] = 1
    d1 = d1_ev["data"]
    d2 = copy.deepcopy(d1)
    d2.update({"design_version": 2, "parent_version": 1, "change_reason": "電池を下げて横の部材を避けた (形の見本)"})
    d2["parts"][0]["box"]["center_mm"][2] = 250.0
    d2_ev = {**copy.deepcopy(d1_ev), "data": d2, "design_version": 2}

    events = [run, d1_ev]
    i = 0
    for version, design, reds in ((1, d1, V1_RED), (2, d2, V2_RED)):
        if version == 2:
            events.append({**d2_ev})
        for d in DOMAIN_TESTS:
            i += 1
            ana = _analysis(i, d, version, design)
            events.append({"kind": "analysis.finished", "agent": AGENT[d], "design_version": version, "data": ana,
                           **({"domain": d} if d != "assembly" else {})})
            events.append({"kind": "judgement.updated", "agent": "system", "design_version": version,
                           "data": {"design_version": version, "domain": d, "state": "red" if d in reds else "green", "red_count": int(d in reds),
                                    "verdict_ids": [f"ver-{i:04d}"], "summary": f"{'直す' if d in reds else '合格'} (形の見本)"}})
    events.append({"kind": "screen.focus", "agent": "system", "data": {"screen": "A6"}})
    events.append({"kind": "run.finished", "agent": "system", "data": {"status": "stopped", "stop_reason": "round_limit"}})
    base = {"run_id": run["run_id"], "ts": run["ts"]}
    out = []
    for k, ev in enumerate(events):
        if k >= 2:
            ev = {**base, **ev, "t": 1.0 + k * 0.5}
        ev["seq"] = k
        out.append(ev)
    return out
