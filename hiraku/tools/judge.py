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


# The result a tool's analysis is judged on where it is not the criterion's own quantity (docs/contract.md,
# 判定に使う量): frame_shell judges max_stress criteria on the weld toe's structural hot-spot stress, since its
# element max_stress keeps rising at sharp corners as the mesh is refined. The tool's spec says it
# (judged_as); this table is for a shelf whose spec does not carry judged_as yet.
JUDGED_QUANTITY = {("frame_shell", "max_stress"): "max_hot_spot_stress"}


def judged_quantity(ctx: RunContext, tool: str | None, criterion_quantity: str) -> str:
    """The name of the tool's result that a criterion on criterion_quantity is judged on: the tool
    spec's judged_as, else JUDGED_QUANTITY, else the criterion's own quantity."""
    spec = ctx.specs.get(tool or "") or {}
    return ((spec.get("judged_as") or {}).get(criterion_quantity)
            or JUDGED_QUANTITY.get((tool, criterion_quantity), criterion_quantity))


def failing_end(results: list[Record], op: str) -> Record:
    """Of several results of one quantity, the one nearest failing: the largest under <= and <, the
    smallest under >= and >."""
    pick = max if unfavourable_side(op) == "larger" else min
    return pick(results, key=lambda r: r["value"])


def beyond_results(ctx: RunContext, load_case: Record, version: int) -> list[tuple[Record, Record]]:
    """(analysis, result) where a tool's newest ok analysis of the load case in the version has its
    judged quantity (judged_quantity) beyond the criterion at its failing end. A later run of the
    same tool replaces its earlier one; different tools (beam and shell) are each kept."""
    crit = load_case.get("criterion")
    if crit is None:
        return []
    newest: dict[str, Record] = {}
    for a in ctx.store.records("analysis", version):
        if a["load_case_id"] == load_case["load_case_id"] and a["status"] == "ok":
            newest[a["tool"]] = a
    out = []
    for a in newest.values():
        q = judged_quantity(ctx, a["tool"], crit["quantity"])
        hits = [r for r in a.get("results", []) if r["quantity"] == q and r.get("unit") == crit["limit"]["unit"]]
        if hits:
            worst = failing_end(hits, crit["op"])
            if not OPS[crit["op"]](worst["value"], crit["limit"]["value"]):
                out.append((a, worst))
    return out


STAND_IN_BELOW = 1e-6  # contract 9017d93: below this fraction of the limit, a judged value is too small to compare


def unresolved_mismatches(ctx: RunContext, load_case: Record, version: int) -> list[tuple[Record, Record | None]]:
    """(comparison, cause or None) holding the load case back (docs/contract.md 分野が緑になる条件 (b)):
    per judged quantity of the load case (the criterion's, the one a tool is judged on, judged_quantity,
    or a result named by result_quantity for a prediction of the criterion's quantity), the version's
    latest comparison, when it is a mismatch whose cause is missing or neither resolved nor safe side.
    Earlier comparisons of that quantity are superseded by the later one and hold nothing (their causes
    stay as recorded); comparisons of intermediate quantities do not decide the tile. With the criterion's
    stand_in_quantity (contract 9017d93), a judged comparison whose prediction and result are both smaller
    than STAND_IN_BELOW x the limit is replaced by the latest comparison of the stand-in quantity."""
    crit = load_case.get("criterion")
    if crit is None:
        return []
    latest: dict[tuple[str, str | None], Record] = {}
    stand_in_name, stand_in = crit.get("stand_in_quantity"), None
    for c in ctx.store.records("comparison", version):
        pred = ctx.prediction(c["prediction_id"])
        if pred is None or pred["load_case_id"] != load_case["load_case_id"]:
            continue
        tool = (ctx.store.get("analysis", c["analysis_id"]) or {}).get("tool")
        q = c["quantity"].partition(".")[0]  # a per-place result <q>.<place> is q at that place
        if stand_in_name and c["quantity"] == stand_in_name:
            stand_in = c  # the result compared is the stand-in quantity (comparisons come in order)
        elif q in (crit["quantity"], judged_quantity(ctx, tool, crit["quantity"])) \
                or pred["quantity"].partition(".")[0] == crit["quantity"]:
            latest[(q, c.get("where"))] = c  # comparisons come in the order they were made
    if any(place for _, place in latest):
        # docs/contract.md 場所ごとの予測: with per-place comparisons, the hand calculation's own maximum compared
        # with the tool's maximum (no place) is kept in the record but does not decide (b)
        latest = {k: c for k, c in latest.items() if k[1]}
    if stand_in is not None:
        tiny = STAND_IN_BELOW * abs(crit["limit"]["value"])
        for q, c in list(latest.items()):
            if abs(c["predicted"]["value"]) < tiny and abs(c["analyzed"]["value"]) < tiny:
                latest[q] = stand_in  # both too small to compare: the stand-in's comparison decides (b)
    causes = ctx.causes_by_comparison()
    out = []
    for c in latest.values():
        if c["status"] != "mismatch":
            continue
        cause = causes.get(c["comparison_id"])
        if cause is None or not (cause.get("resolved") or cause.get("safe_side")):
            out.append((c, cause))
    return out


def blockers(ctx: RunContext, load_case: Record, version: int) -> list[str]:
    """Why a load case without a failing verdict may not count as passed (docs/contract.md, 分野が緑に
    なる条件): an unresolved mismatch, or an analysis result beyond the criterion (which also voids an
    earlier safe-side verdict). Empty when nothing holds it back."""
    out = []
    for c, cause in unresolved_mismatches(ctx, load_case, version):
        why = "原因がまだ書かれていない" if cause is None else f"原因 {cause['cause_id']} がまだ解けていない"
        out.append(f"照合 {c['comparison_id']} の食い違い ({c['diff_rel'] * 100:+.0f} %) の{why}")
    crit = load_case.get("criterion")
    for a, r in beyond_results(ctx, load_case, version):
        out.append(f"解析 {a['analysis_id']} ({a['tool']}) の {r['quantity']} が {_amount(r['value'], r['unit'])} で、"
                   f"基準 {OP_JA[crit['op']]} {_amount(crit['limit']['value'], crit['limit']['unit'])} を超えている")
    return out


def held_cases(ctx: RunContext, domain: str, version: int) -> dict[str, list[str]]:
    """load case id -> blockers, for the domain's load cases with a criterion that have no failing
    verdict but are held back from passing."""
    verdicts = latest_verdicts(ctx, version)
    out = {}
    for lc in ctx.load_cases:
        if lc["domain"] != domain or "criterion" not in lc:
            continue
        ver = verdicts.get(lc["load_case_id"])
        if ver is not None and not ver["pass"]:
            continue
        why = blockers(ctx, lc, version)
        if why:
            out[lc["load_case_id"]] = why
    return out


def _amount(x: float, unit: str) -> str:
    """A number and its unit; a dimensionless "1" is not written."""
    return f"{x:.4g}" if unit == "1" else f"{x:.4g} {unit}"


NOT_OP = {"<=": ">", "<": ">=", ">=": "<", ">": "<="}  # the relation that holds when a criterion fails


def verdict_line(v: Record) -> str:
    """The verdict in one line. The sign between the numbers is the relation that holds: the
    criterion's when it passes; when it fails, the opposite, with the criterion beside it."""
    safe = " 安全側の予測で" if v.get("basis") == "explained_safe_side" else ""
    value, limit = v["value"]["value"], v["limit"]["value"]
    if v.get("margin_pct") is None:  # a zero limit: the margin in the quantity's own unit
        room = _amount(margin(value, v["op"], limit), v["limit"]["unit"])
    else:
        room = f"{v['margin_pct']:.0f} %"
    lim = _amount(limit, v["limit"]["unit"])
    if v["pass"]:
        return (f"{v['test_id']} {v['quantity']} {_amount(value, v['value']['unit'])} "
                f"{OP_JA[v['op']]} {lim} (余裕 {room}{safe})")
    return (f"{v['test_id']} {v['quantity']} {_amount(value, v['value']['unit'])} "
            f"{OP_JA[NOT_OP[v['op']]]} {lim} (基準 {OP_JA[v['op']]} {lim} に届かない、余裕 {room}{safe})")


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
    green when every load case with a criterion passed and none is held back (held_cases: an
    unresolved mismatch, or a result beyond the criterion); else pending."""
    v = version or ctx.version
    verdicts = latest_verdicts(ctx, v)
    cases = [lc for lc in ctx.load_cases if lc["domain"] == domain and "criterion" in lc]
    got = [verdicts[lc["load_case_id"]] for lc in cases if lc["load_case_id"] in verdicts]
    missing = [lc["load_case_id"] for lc in cases if lc["load_case_id"] not in verdicts]
    reds = [x for x in got if not x["pass"]]
    misses = system_misses(ctx, v) if domain == SYSTEM_DOMAIN and v else []
    held = held_cases(ctx, domain, v) if v else {}
    held_line = "まだ合格にできない試験: " + " / ".join(f"{lc_id} ({'、'.join(why)})" for lc_id, why in held.items())
    if reds or misses:  # red even with tests left without a verdict; they are counted in the line
        lines = ([f"不合格 {len(reds)} 件: " + "、".join(verdict_line(x) for x in reds)] if reds else []) + \
                ([f"系の欠け {len(misses)} 件: " + "、".join(miss_line(r, c) for r, c in misses)] if misses else [])
        state, summary = "red", "。".join(lines)
        if missing:
            summary += f"。判定の無い試験 {len(missing)}: " + "、".join(missing)
        if held:
            summary += "。" + held_line
    elif held:
        state, summary = "pending", held_line
        rest = [x for x in missing if x not in held]
        if rest:
            summary += "。まだ判定していない試験: " + "、".join(rest)
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
    """Recompute the tile and store it; an unchanged tile is not written again (no judgement.updated)."""
    j = compute_judgement(ctx, domain, version)
    contract.validate("judgement", j)
    if any(t == j for t in ctx.store.judgements(j["design_version"])):
        return j
    return ctx.store.set_judgement(j)


def init_judgements(ctx: RunContext, version: int | None = None) -> list[Record]:
    """A pending tile for every picked domain of a new design version."""
    return [update_judgement(ctx, d, version) for d in ctx.picked]
