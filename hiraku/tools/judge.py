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


def verdict_line(v: Record) -> str:
    safe = " 安全側の予測で" if v.get("basis") == "explained_safe_side" else ""
    value, limit = v["value"]["value"], v["limit"]["value"]
    if v.get("margin_pct") is None:  # a zero limit: the margin in the quantity's own unit
        side = margin(value, v["op"], limit)
        room = f"{side:.4g} {v['limit']['unit']}"
    else:
        room = f"{v['margin_pct']:.0f} %"
    return (f"{v['test_id']} {v['quantity']} {value:.4g} {v['value']['unit']} "
            f"{OP_JA[v['op']]} {limit:.4g} {v['limit']['unit']} (余裕 {room}{safe})")


def compute_judgement(ctx: RunContext, domain: str, version: int | None = None) -> Record:
    v = version or ctx.version
    verdicts = latest_verdicts(ctx, v)
    cases = [lc for lc in ctx.load_cases if lc["domain"] == domain and "criterion" in lc]
    got = [verdicts[lc["load_case_id"]] for lc in cases if lc["load_case_id"] in verdicts]
    missing = [lc["load_case_id"] for lc in cases if lc["load_case_id"] not in verdicts]
    reds = [x for x in got if not x["pass"]]
    if reds:
        state, summary = "red", f"不合格 {len(reds)} 件: " + "、".join(verdict_line(x) for x in reds)
    elif got and not missing:
        state, summary = "green", "合格: " + "、".join(verdict_line(x) for x in got)
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
