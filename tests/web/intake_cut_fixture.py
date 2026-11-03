"""Runs for the N09 and N14 cut tests (shape examples: the values are chosen, not computed).

n09_events: a run that stops once the domains are picked (run.started, domains.picked,
run.finished), as the intake department's pick runs do. Its picks are given by the test.
design_events: a run that holds only the demo design (data/designs/offroad-4w-v1.json).
n14_events: the rules there from the start, a dropped file read into rules (once, then again
with the lines not made into rules), rule checks on the design with one hit, the lead's
summary and question.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESIGN = ROOT / "data" / "designs" / "offroad-4w-v1.json"
TS = "2026-11-03T12:00:00.000+09:00"


def design():
    return json.loads(DESIGN.read_text(encoding="utf-8"))


def _project(run_id, want):
    return {"run_id": run_id, "created_at": TS, "want": want, "product": "offroad-4w", "flags": {}, "round_limit": 1,
            "cost_limit_usd": 1.0, "requirements": [], "domain_picks": [], "load_cases": [],
            "agents": [{"agent_id": "lead", "role": "lead", "prop": "binder"}], "status": "running", "cost_usd": 0.0, "design_versions": []}


def _numbered(run_id, events):
    out = []
    for i, (kind, agent, text, data, extra) in enumerate(events):
        e = {"seq": i, "ts": TS, "t": round(i * 0.01, 3), "run_id": run_id, "kind": kind, "agent": agent}
        if text is not None:
            e["text"] = text
        if data is not None:
            e["data"] = data
        e.update(extra)
        out.append(e)
    return out


def n09_events(run_id, want, picks, off, assembly_test_ids):
    picked = {"want": want, "flags": {}, "picks": picks, "off": off, "assembly_test_ids": assembly_test_ids}
    return _numbered(run_id, [
        ("run.started", "system", None, _project(run_id, want), {}),
        ("domains.picked", "lead", f"{len(picks)} 分野", picked, {}),
        ("run.finished", "system", "分野を選ぶところまで", {"status": "stopped", "stop_reason": "分野を選ぶところまで", "stop_code": "not_judged"}, {}),
    ])


def design_events(run_id):
    d = design()
    d["design_version"] = 1
    return _numbered(run_id, [
        ("run.started", "system", None, _project(run_id, "見本"), {}),
        ("design.created", "designer", "版 1", d, {"design_version": 1}),
        ("run.finished", "system", "見本", {"status": "stopped", "stop_reason": "見本", "stop_code": "not_judged"}, {}),
    ])


# N14's shape example: two documents there from the start, a dropped one (3 rules, then the
# same 3 with 2 lines not made into rules); the battery rule catches the battery.
BUILTIN = [
    ("BA", "板金の見本", [("BA-01", "曲げの半径は板厚以上", "bend_radius_mm >= thickness_mm", "body")]),
    ("BB", "人の見本", [("BB-01", "座面は 400 mm 以上", "size_y_mm >= 400", "seat"), ("BB-02", "1 人で持つ部品は 23 kg 以下", "mass_kg <= 23", "battery")]),
]
DROPPED = [("X1-01", "床の厚みは 3 mm 以上", "thickness_mm >= 3", "body"),
           ("X1-02", "電池の質量は 30 kg 以下", "mass_kg <= 30", "battery"),
           ("X1-03", "座面の幅は 400 mm 以上", "size_y_mm >= 400", "seat")]
SKIPPED = [{"text": "板金の部品は、傷が見えない向きに置く", "reason": "向きは式にできない"},
           {"text": "電池は乗員の足元から離す", "reason": "部品どうしの距離を測る値が設計に無い"}]
SAID = "板金と電池のメモです。規則にしてください"
ASK = "規則に当たる部品があります: X1-02「電池の質量は 30 kg 以下」(battery)。設計を直しますか、それとも、この規則は外しますか"
ASK_PLAIN = "規則に当たる部品があります: 「電池の質量は 30 kg 以下」(電池)。設計を直しますか、それとも、この規則は外しますか"
BATTERY_KG = 50.0  # the demo design's battery (data/designs/offroad-4w-v1.json)


def _rule(rid, doc_id, text, check, kind, origin):
    return {"rule_id": rid, "doc_id": doc_id, "category": "battery" if kind == "battery" else "sheet_metal", "text": text, "check": check,
            "origin": origin, "trust": 3, "basis": "shape example", "applies_to": {"kinds": [kind]}}


def n14_events(run_id):
    d = design()
    d["design_version"] = 1
    parts = {k: [p["id"] for p in d["parts"] if p["kind"] == k] for k in ("body", "seat", "battery")}
    evs = [("run.started", "system", None, _project(run_id, "見本"), {})]
    for doc_id, name, rules in BUILTIN:
        evs.append(("knowledge.imported", "lead", f"「{name}」から {len(rules)} 件", {"doc": {"doc_id": doc_id, "filename": name, "origin": "builtin"},
                    "rules": [_rule(r, doc_id, t, c, k, "builtin") for r, t, c, k in rules]}, {}))
    evs.append(("design.created", "designer", "版 1", d, {"design_version": 1}))
    evs.append(("user.said", "user", SAID, None, {}))
    dropped = [_rule(r, "X1", t, c, k, "dropped") for r, t, c, k in DROPPED]
    doc = {"doc_id": "X1", "filename": "notes.md", "origin": "dropped"}
    evs.append(("knowledge.imported", "lead", "「notes.md」から 3 件", {"doc": doc, "rules": copy.deepcopy(dropped)}, {}))
    evs.append(("agent.said", "lead", "式にできない 2 文は、理由を書いて規則にしません", None, {}))
    evs.append(("knowledge.imported", "lead", "「notes.md」から 3 件", {"doc": {**doc, "skipped": SKIPPED}, "rules": copy.deepcopy(dropped)}, {}))
    n = 0
    for _doc, _name, rules in BUILTIN:
        for rid, _t, _c, kind in rules:
            n += 1
            evs.append(("rule_check.recorded", "packager", None, {"rule_check_id": f"rchk-{n:04d}", "design_version": 1, "rule_id": rid,
                        "targets": parts[kind], "hits": [], "not_evaluable": [], "pass": True}, {"design_version": 1}))
    for rid, text, _c, kind in DROPPED:
        n += 1
        hits = [{"part_id": "battery", "values": {"mass_kg": BATTERY_KG}, "message": f"{text} に合わない"}] if kind == "battery" else []
        evs.append(("rule_check.recorded", "packager", None, {"rule_check_id": f"rchk-{n:04d}", "design_version": 1, "rule_id": rid,
                    "targets": parts[kind], "hits": hits, "not_evaluable": [], "pass": not hits}, {"design_version": 1}))
    evs.append(("agent.said", "lead", "6 規則: 当たり 1。当たった部品: X1-02 (電池の質量は 30 kg 以下): battery", None, {}))
    evs.append(("agent.said", "lead", ASK, None, {}))
    evs.append(("run.finished", "system", "見本", {"status": "stopped", "stop_reason": "見本", "stop_code": "not_judged"}, {}))
    return _numbered(run_id, evs)


def write_run(runs: Path, run_id: str, events) -> None:
    (runs / run_id).mkdir(parents=True, exist_ok=True)
    (runs / run_id / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
