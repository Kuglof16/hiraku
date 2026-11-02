"""Verdicts and judgements, made by code from matching comparisons."""

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


def margin_pct(value: float, op: str, limit: float) -> float:
    """How far the value is from the limit, as % of the limit; positive on the passing side."""
    if limit == 0:
        return 0.0
    side = (limit - value) if op in ("<=", "<") else (value - limit)
    return side / abs(limit) * 100.0


def make_verdict(ctx: RunContext, verdict_id: str, comparison: Record, load_case: Record) -> Record:
    crit = load_case["criterion"]
    value = comparison["analyzed"]
    limit = crit["limit"]
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
        "margin_pct": margin_pct(value["value"], crit["op"], limit["value"]),
        "requirement_keys": list(crit.get("requirement_keys", [])),
    }


def latest_verdicts(ctx: RunContext, version: int) -> dict[str, Record]:
    """The newest verdict per load case for one design version."""
    out: dict[str, Record] = {}
    for v in ctx.store.records("verdict", version):
        out[v["load_case_id"]] = v
    return out


def _line(v: Record) -> str:
    return (f"{v['test_id']} {v['quantity']} {v['value']['value']:.4g} {v['value']['unit']} "
            f"{OP_JA[v['op']]} {v['limit']['value']:.4g} {v['limit']['unit']} (余裕 {v['margin_pct']:.0f} %)")


def compute_judgement(ctx: RunContext, domain: str, version: int | None = None) -> Record:
    v = version or ctx.version
    verdicts = latest_verdicts(ctx, v)
    cases = [lc for lc in ctx.load_cases if lc["domain"] == domain and "criterion" in lc]
    got = [verdicts[lc["load_case_id"]] for lc in cases if lc["load_case_id"] in verdicts]
    missing = [lc["load_case_id"] for lc in cases if lc["load_case_id"] not in verdicts]
    reds = [x for x in got if not x["pass"]]
    if reds:
        state, summary = "red", f"不合格 {len(reds)} 件: " + "、".join(_line(x) for x in reds)
    elif got and not missing:
        state, summary = "green", "合格: " + "、".join(_line(x) for x in got)
    else:
        state, summary = "pending", "まだ判定していない試験: " + ("、".join(missing) or "なし")
    return {
        "design_version": v,
        "domain": domain,
        "state": state,
        "red_count": len(reds),
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
