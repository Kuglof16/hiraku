"""Apply rules to a design: which parts a rule is about, and which of them it hits.

A rule (hiraku.intake.knowledge) has a `check`, a yes/no question about the facts of one part
(hiraku.intake.facts). A part is *hit* when the answer is no: the rule says "mass at most 23 kg" and the
battery weighs 50. When a name in the question has no value for a part, the part is reported as not
evaluable with the missing names instead of being passed or failed.
"""

from __future__ import annotations

from typing import Any, Callable

from hiraku.intake import expression, facts, systems

Record = dict[str, Any]


def applicable_targets(rule: Record, targets: list[Record]) -> list[Record]:
    """The targets a rule is about: those whose kind (or type, for frame and support) it lists, else every part."""
    kinds = (rule.get("applies_to") or {}).get("kinds")
    if kinds:
        return [t for t in targets if t["kind"] in kinds or t["type"] in kinds]
    return [t for t in targets if t["type"] == "part"]


def _message(rule: Record, values: dict[str, float]) -> str:
    detail = "、".join(f"{n} = {v:g}" for n, v in values.items())
    return f"{rule['text']} に合わない ({detail})" if detail else f"{rule['text']} に合わない"


def check_rule(rule: Record, targets: list[Record]) -> Record:
    """One rule against a design's targets. The rule_check record without its id and design version."""
    names = expression.names(rule["check"])
    hit_list: list[Record] = []
    not_evaluable: list[Record] = []
    checked: list[Record] = []
    chosen = applicable_targets(rule, targets)
    for t in chosen:
        answer, missing = expression.evaluate(rule["check"], t["facts"])
        if answer is None:
            not_evaluable.append({"part_id": t["id"], "missing": missing})
            continue
        values = {n: float(t["facts"][n]) for n in names if isinstance(t["facts"].get(n), (int, float)) and not isinstance(t["facts"].get(n), bool)}
        if values:  # every part the check was evaluated on, passing or not, with the values it used
            entry: Record = {"part_id": t["id"], "values": values}
            unchecked = [n for n in values if n in t.get("unchecked", ())]
            if unchecked:
                entry["unchecked"] = unchecked
            checked.append(entry)
        if not answer:
            hit_list.append({"part_id": t["id"], "values": values, "message": _message(rule, values)})
    rec = {"rule_id": rule["rule_id"], "targets": [t["id"] for t in chosen], "hits": hit_list,
           "not_evaluable": not_evaluable, "pass": not hit_list}
    if checked:
        rec["checked"] = checked
    return rec


def check_all(
    rules: list[Record],
    design: Record,
    requirements: list[Record] | None = None,
    *,
    disabled: set[str] | None = None,
    id_factory: Callable[[], str] | None = None,
    flags: dict[str, Any] | None = None,
) -> list[Record]:
    """Every enabled rule against one design: a list of rule_check records.

    id_factory gives the next rule_check_id (the run store's next_id); without it the ids are rchk-0001, ...
    A rule with no target in this design still gets a record (targets empty, pass true): that is an answer too.
    flags: the product's flags, which say what systems it needs (hiraku.intake.systems); without them the
    rules of the system category are reported as not evaluable (missing flags).
    """
    disabled = disabled or set()
    ts = facts.targets(design, requirements)
    out: list[Record] = []
    for rule in rules:
        if rule["rule_id"] in disabled:
            continue
        rec = systems.check_rule(rule, design, flags) if systems.is_system_check(rule["check"]) else check_rule(rule, ts)
        rid = id_factory() if id_factory else f"rchk-{len(out) + 1:04d}"
        out.append({"rule_check_id": rid, "design_version": design["design_version"], **rec,
                    **({"level": "advice"} if rule.get("level") == "advice" else {})})
    return out


def is_advice(check: Record) -> bool:
    return check.get("level") == "advice"


def summarize(checks: list[Record]) -> Record:
    """Counts for the screen and the chat reply."""
    firm = [c for c in checks if not is_advice(c)]  # an advice rule never fails a design and its gaps are not shown
    advice = [c for c in checks if is_advice(c)]
    return {
        "rules": len(checks),
        "hit_rules": sum(1 for c in firm if c["hits"]),
        "hits": sum(len(c["hits"]) for c in firm),
        "not_evaluable_rules": sum(1 for c in firm if c["not_evaluable"]),
        "passed_rules": sum(1 for c in firm if c["targets"] and not c["hits"] and not c["not_evaluable"]),
        "advice_rules": sum(1 for c in advice if c["hits"]),
        "advice_hits": sum(len(c["hits"]) for c in advice),
    }


def summary_line(checks: list[Record], rules: list[Record]) -> str:
    s = summarize(checks)
    text = {r["rule_id"]: r["text"] for r in rules}
    hits = "、".join(f"{c['rule_id']} ({text.get(c['rule_id'], '')}): " + "・".join(h["part_id"] for h in c["hits"])
                    for c in checks if c["hits"] and not is_advice(c))
    lines = [f"{s['rules']} 規則: 当たり {s['hit_rules']}、合格 {s['passed_rules']}、評価できない {s['not_evaluable_rules']}"]
    if hits:
        lines.append(f"当たった部品: {hits}")
    if s["advice_hits"]:
        lines.append(f"助言 {s['advice_hits']} 件 (合否には数えません): " + "、".join(
            f"{c['rule_id']} ({text.get(c['rule_id'], '')}): " + "・".join(h["part_id"] for h in c["hits"]) for c in checks if is_advice(c) and c["hits"]))
    return "。".join(lines)
