"""Verdicts and judgements, made by code: from a matching comparison (value =
analysis), or from a mismatch whose cause is judged safe side and whose numbers
confirm it (value = the prediction, the unfavourable one)."""

from __future__ import annotations

from typing import Any

from hiraku import contract
from hiraku.tools.context import RunContext

Record = dict[str, Any]

OPS = {
    "<=": lambda v, lim: v <= lim,
    "<": lambda v, lim: v < lim,
    ">=": lambda v, lim: v >= lim,
    ">": lambda v, lim: v > lim,
}
OP_JA = {"<=": "≤", "<": "<", ">=": "≥", ">": ">"}


def margin(value: float, op: str, limit: float) -> float:
    """How far the value is from the limit in its own unit; positive on the passing side."""
    return (limit - value) if op in ("<=", "<") else (value - limit)


def margin_pct(value: float, op: str, limit: float) -> float | None:
    """margin as % of the limit; None when the limit is 0 (a % of zero says nothing)."""
    if limit == 0:
        return None
    return margin(value, op, limit) / abs(limit) * 100.0


def unfavourable_side(op: str) -> str:
    """Which side of the limit fails: "larger" for <= and <, "smaller" for >= and >."""
    return "larger" if op in ("<=", "<") else "smaller"


def prediction_is_unfavourable(comparison: Record, op: str) -> bool:
    """True when the prediction is at least as unfavourable as the analysis under op."""
    p, a = comparison["predicted"]["value"], comparison["analyzed"]["value"]
    return p >= a if unfavourable_side(op) == "larger" else p <= a


def make_verdict(ctx: RunContext, verdict_id: str, comparison: Record, load_case: Record,
                 cause_id: str | None = None) -> Record:
    """From a match: the analysis value. With cause_id (an explained safe-side mismatch):
    the prediction, which the caller has checked is the unfavourable value."""
    crit = load_case["criterion"]
    value = comparison["predicted"] if cause_id else comparison["analyzed"]
    limit = crit["limit"]
    extra = ({"basis": "explained_safe_side", "value_from": "prediction", "cause_id": cause_id} if cause_id
             else {"basis": "match", "value_from": "analysis"})
    return {
        "verdict_id": verdict_id,
        "comparison_id": comparison["comparison_id"],
        "load_case_id": load_case["load_case_id"],
        "test_id": load_case["test_id"],
        "domain": load_case["domain"],
        "quantity": crit["quantity"],
        "value": dict(value),
        "op": crit["op"],
        "limit": dict(limit),
        "pass": OPS[crit["op"]](value["value"], limit["value"]),
        "margin": {"value": margin(value["value"], crit["op"], limit["value"]), "unit": limit["unit"]},
        "margin_pct": margin_pct(value["value"], crit["op"], limit["value"]),
        "requirement_keys": list(crit.get("requirement_keys", [])),
        **extra,
    }


def latest_verdicts(ctx: RunContext, version: int) -> dict[str, Record]:
    """The newest verdict per load case for one design version."""
    out: dict[str, Record] = {}
    for v in ctx.store.records("verdict", version):
        out[v["load_case_id"]] = v
    return out


def _amount(x: float, unit: str) -> str:
    """A number and its unit; a dimensionless "1" is not written."""
    return f"{x:.4g}" if unit == "1" else f"{x:.4g} {unit}"


def verdict_line(v: Record) -> str:
    safe = " 安全側の予測で" if v.get("basis") == "explained_safe_side" else ""
    value, limit = v["value"]["value"], v["limit"]["value"]
    if v.get("margin_pct") is None:  # a zero limit: the margin in the quantity's own unit
        room = _amount(margin(value, v["op"], limit), v["limit"]["unit"])
    else:
        room = f"{v['margin_pct']:.0f} %"
    return (f"{v['test_id']} {v['quantity']} {_amount(value, v['value']['unit'])} "
            f"{OP_JA[v['op']]} {_amount(limit, v['limit']['unit'])} (余裕 {room}{safe})")


SYSTEM_CATEGORY = "system"  # knowledge rules that a product's systems are there and connected end to end
SYSTEM_DOMAIN = "assembly"  # their misses turn this domain's tile red


def knowledge_rules(ctx: RunContext) -> dict[str, Record]:
    """rule_id -> rule of the run's knowledge (the last knowledge.imported per document)."""
    docs: dict[str, list[Record]] = {}
    for e in ctx.store.events():
        if e["kind"] == "knowledge.imported":
            docs[e["data"]["doc"]["doc_id"]] = e["data"].get("rules", [])
    return {r["rule_id"]: r for rules in docs.values() for r in rules}


def system_misses(ctx: RunContext, version: int) -> list[tuple[Record, Record]]:
    """(rule, its latest rule_check of the version) for each system rule that hits a part."""
    rules = {rid: r for rid, r in knowledge_rules(ctx).items() if r.get("category") == SYSTEM_CATEGORY}
    latest: dict[str, Record] = {}
    for c in ctx.store.records("rule_check", version):
        if c["rule_id"] in rules:
            latest[c["rule_id"]] = c
    return [(rules[rid], c) for rid, c in latest.items() if c["hits"]]


def miss_line(rule: Record, check: Record) -> str:
    where = "、".join(f"{h['part_id']}: {h['message']}" for h in check["hits"])
    return f"{rule['rule_id']}「{rule['text']}」({where})"


def compute_judgement(ctx: RunContext, domain: str, version: int | None = None) -> Record:
    """The tile of a domain: red when a verdict fails, or (assembly) when a system rule hits a part;
    green when every load case with a criterion passed; else pending."""
    v = version or ctx.version
    verdicts = latest_verdicts(ctx, v)
    cases = [lc for lc in ctx.load_cases if lc["domain"] == domain and "criterion" in lc]
    got = [verdicts[lc["load_case_id"]] for lc in cases if lc["load_case_id"] in verdicts]
    missing = [lc["load_case_id"] for lc in cases if lc["load_case_id"] not in verdicts]
    reds = [x for x in got if not x["pass"]]
    misses = system_misses(ctx, v) if domain == SYSTEM_DOMAIN and v else []
    if reds or misses:  # red even with tests left without a verdict; they are counted in the line
        lines = ([f"不合格 {len(reds)} 件: " + "、".join(verdict_line(x) for x in reds)] if reds else []) + \
                ([f"系の欠け {len(misses)} 件: " + "、".join(miss_line(r, c) for r, c in misses)] if misses else [])
        state, summary = "red", "。".join(lines)
        if missing:
            summary += f"。判定の無い試験 {len(missing)}: " + "、".join(missing)
    elif got and not missing:
        state, summary = "green", "合格: " + "、".join(verdict_line(x) for x in got)
    else:
        state, summary = "pending", "まだ判定していない試験: " + ("、".join(missing) or "なし")
    return {
        "design_version": v,
        "domain": domain,
        "state": state,
        "red_count": len(reds) + len(misses),
        "verdict_ids": [x["verdict_id"] for x in got],
        "summary": summary,
    }


def update_judgement(ctx: RunContext, domain: str, version: int | None = None) -> Record:
    j = compute_judgement(ctx, domain, version)
    contract.validate("judgement", j)
    return ctx.store.set_judgement(j)


def init_judgements(ctx: RunContext, version: int | None = None) -> list[Record]:
    """A pending tile for every picked domain of a new design version."""
    return [update_judgement(ctx, d, version) for d in ctx.picked]
