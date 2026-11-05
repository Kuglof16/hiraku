"""Verdicts and judgements, made by code: from a matching comparison (value =
analysis), or from a mismatch whose cause is judged safe side and whose numbers
confirm it (value = the prediction, the unfavourable one)."""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any

from hiraku import contract
from hiraku.tools.context import RunContext

Record = dict[str, Any]


def reading(ctx: RunContext) -> Any:
    """The store's reading() (each record file read once, the records not to be changed), if it has one."""
    got = getattr(ctx.store, "reading", None)
    return got() if got is not None else nullcontext()

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


PATH_FORMULA = "load_path.free_parts"  # docs/contract.md 手計算の役目: the hand calculation of the load path
PATH_CHECKED = "path_checked"


def path_check(ctx: RunContext, load_case: Record, version: int, analysis: Record | None = None) -> str | None:
    """docs/contract.md 手計算の役目, the load path checked by hand, in this version: (1) a load_path.free_parts
    prediction of 0, of the load case's loaded parts (one of its own predictions) or of every part of the version
    (contract 947969a: the quantity free_parts, of any load case, as qa.check_run reads it); (2) the newest ok
    analysis of the load case that follows the path gives unsupported_load 0, and so does the analysis that
    decides (given: the compared one); a free_parts result of either, where given, is 0. None when it holds, else
    why not (for people)."""
    lc_id = load_case["load_case_id"]
    hand = [p for p in ctx.store.records("prediction", version) if p.get("formula_id") == PATH_FORMULA
            and (p["load_case_id"] == lc_id or p["quantity"] == "free_parts")]
    if not hand:
        return (f"版 {version} に道の確かめの手計算 ({PATH_FORMULA}: 部品から部材まで触れている所の道) がまだ"
                "記録されていない")
    if not any(p["value"] == 0 for p in hand):
        p = hand[-1]
        return f"手計算 {p['prediction_id']} ({PATH_FORMULA}) で、つながらない部品が {p['value']:g} ある"
    seen = [a for a in ctx.store.records("analysis", version) if a["load_case_id"] == lc_id and a["status"] == "ok"
            and any(r.get("quantity") == UNSUPPORTED_LOAD for r in a.get("results") or [])]
    if not seen:
        return (f"版 {version} で {lc_id} の力の道をたどるツールの解析 (unsupported_load を出すもの、load_path など) が"
                "まだ回っていない")
    for a in [seen[-1]] + ([analysis] if analysis is not None and analysis is not seen[-1] else []):
        res = a.get("results") or []
        lost = [r["value"] for r in res if r.get("quantity") == UNSUPPORTED_LOAD]
        if not lost:  # the deciding tool must say it found every load a path (contract 手計算の役目)
            return f"解析 {a['analysis_id']} ({a['tool']}) が、支えのない荷重 ({UNSUPPORTED_LOAD}) を出していない"
        if max(lost) > 0:
            return f"解析 {a['analysis_id']} ({a['tool']}) で、支えのない荷重が {max(lost):g} ある"
        free = [r["value"] for r in res if r.get("quantity") == "free_parts"]
        if free and max(free) > 0:
            return f"解析 {a['analysis_id']} ({a['tool']}) で、つながらない部品が {max(free):g} ある"
    return None


def make_verdict(ctx: RunContext, verdict_id: str, comparison: Record, load_case: Record,
                 cause_id: str | None = None, basis: str | None = None) -> Record:
    """From a match: the analysis value. With cause_id (an explained safe-side mismatch):
    the prediction, which the caller has checked is the unfavourable value. With cause_id and basis
    path_checked (a cause with tool_decides, the load path checked by hand: path_check): the analysis value."""
    crit = load_case["criterion"]
    checked = cause_id is not None and basis == PATH_CHECKED
    value = comparison["predicted"] if cause_id and not checked else comparison["analyzed"]
    limit = crit["limit"]
    extra = ({"basis": PATH_CHECKED, "value_from": "analysis", "cause_id": cause_id} if checked
             else {"basis": "explained_safe_side", "value_from": "prediction", "cause_id": cause_id} if cause_id
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


UNSUPPORTED_LOAD = "unsupported_load"  # docs/contract.md 荷重の通り道が無いとき


def unsupported_load_verdict(verdict_id: str, analysis: Record, load_case: Record) -> Record | None:
    """The failing verdict of a load case whose analysis found load points with no path to the frame
    (result unsupported_load > 0, docs/contract.md 荷重の通り道が無いとき); None when there are none.
    A gap in the design, so the load case counts as judged (HK4 does not wait on it)."""
    hits = [r for r in analysis.get("results") or [] if r.get("quantity") == UNSUPPORTED_LOAD and r.get("value", 0) > 0]
    if analysis.get("status") != "ok" or not hits:
        return None
    count = float(max(r["value"] for r in hits))
    crit = load_case.get("criterion") or {}
    return {
        "verdict_id": verdict_id,
        "analysis_id": analysis["analysis_id"],
        "load_case_id": load_case["load_case_id"],
        "test_id": load_case["test_id"],
        "domain": load_case["domain"],
        "quantity": UNSUPPORTED_LOAD,
        "value": {"value": count, "unit": "1"},
        "op": "<=",
        "limit": {"value": 0.0, "unit": "1"},
        "pass": False,
        "margin": {"value": -count, "unit": "1"},
        "margin_pct": None,
        "requirement_keys": list(crit.get("requirement_keys", [])),
        "basis": UNSUPPORTED_LOAD,
        "value_from": "analysis",
    }


HOT_SPOT = "hot_spot"  # docs/contract.md CAE の役目: a place the hand calculation did not predict, over the limit


def unpredicted_over(ctx: RunContext, analysis: Record, load_case: Record) -> list[Record]:
    """The rows of an ok analysis' hot_spots that make the load case red (docs/contract.md CAE の役目): not
    predicted, over the limit (utilisation > 1), on the quantity the tool is judged on for the criterion
    (judged_quantity: frame_shell's weld lines are max_hot_spot_stress, its mesh spikes max_stress do not count)."""
    crit = load_case.get("criterion")
    if crit is None or analysis.get("status") != "ok":
        return []
    q = judged_quantity(ctx, analysis.get("tool"), crit["quantity"])
    return [r for r in analysis.get("hot_spots") or []
            if r.get("quantity") == q and not r.get("predicted") and r.get("utilisation", 0) > 1
            and SPIKE_NOTE not in (r.get("note") or "")]


# docs/contract.md 判定の量 (cc6f396): a singular point (a re-entrant corner whose stress keeps rising as the mesh is
# refined) is listed with this word in its note and never makes a red; the analysis department writes the note
SPIKE_NOTE = "尖り"


def hot_spot_verdict(verdict_id: str, analysis: Record, load_case: Record, row: Record) -> Record:
    """The failing verdict of one unpredicted place over the limit (basis hot_spot): the row's value and limit,
    the criterion's op, analysis_id and where; no comparison (made by analyze)."""
    crit = load_case["criterion"]
    value, limit = dict(row["value"]), dict(row["limit"])
    return {
        "verdict_id": verdict_id,
        "analysis_id": analysis["analysis_id"],
        "load_case_id": load_case["load_case_id"],
        "test_id": load_case["test_id"],
        "domain": load_case["domain"],
        "quantity": crit["quantity"],
        "value": value,
        "op": crit["op"],
        "limit": limit,
        "pass": False,
        "margin": {"value": margin(value["value"], crit["op"], limit["value"]), "unit": limit["unit"]},
        "margin_pct": margin_pct(value["value"], crit["op"], limit["value"]),
        "requirement_keys": list(crit.get("requirement_keys", [])),
        "basis": HOT_SPOT,
        "value_from": "analysis",
        "where": row["where"],
    }


def latest_verdicts(ctx: RunContext, version: int) -> dict[str, Record]:
    """The verdict that stands for each load case in one design version (docs/contract.md 分野が緑になる条件 (a)):
    per load case, verdict quantity and place (the compared result's where, from a per-place prediction
    `<quantity>.<place>`), the newest verdict replaces the earlier ones; of what is left, the newest failing one
    when any fails (a failing place is not hidden by a newer pass at another place or of another quantity), else
    the newest."""
    newest: dict[str, dict[tuple, Record]] = {}
    for v in ctx.store.records("verdict", version):
        cmp = ctx.store.get("comparison", v["comparison_id"]) if v.get("comparison_id") else None
        key = (v.get("quantity"), (cmp or {}).get("where") or v.get("where"))  # a hot_spot verdict has its own where
        per = newest.setdefault(v["load_case_id"], {})
        per.pop(key, None)  # re-insert so the dict's order stays the order the verdicts were made
        per[key] = v
    out: dict[str, Record] = {}
    for lc_id, per in newest.items():
        kept = list(per.values())
        failing = [v for v in kept if not v.get("pass")]
        out[lc_id] = (failing or kept)[-1]
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
    made = {(v.get("cause_id"), v.get("basis")) for v in ctx.store.records("verdict", version) if v.get("cause_id")}
    out = []
    for c in latest.values():
        if c["status"] != "mismatch":
            continue
        cause = causes.get(c["comparison_id"])
        # (b): settled by a resolved cause, or by a safe-side one or one where the tool decides after the path
        # check, each with the verdict it made (explained_safe_side, path_checked)
        cid = (cause or {}).get("cause_id")
        settled = cause is not None and (
            cause.get("resolved")
            or (cause.get("safe_side") and (cid, "explained_safe_side") in made)
            or (cause.get("tool_decides") and (cid, PATH_CHECKED) in made))
        if not settled:
            out.append((c, cause))
    return out


def blockers(ctx: RunContext, load_case: Record, version: int) -> list[str]:
    """Why a load case without a failing verdict may not count as passed (docs/contract.md, 分野が緑に
    なる条件): an unresolved mismatch, or an analysis result beyond the criterion (which also voids an
    earlier safe-side verdict). Empty when nothing holds it back."""
    out = []
    for c, cause in unresolved_mismatches(ctx, load_case, version):
        if cause is None:
            why = "原因がまだ書かれていない"
        elif cause.get("ends_case"):  # contract 08cf4d6: the load case ends not judged
            why = f"原因 {cause['cause_id']} が、この実行の手計算では届かないとして、判定なしで終えた"
        elif cause.get("safe_side") or cause.get("tool_decides"):
            why = f"原因 {cause['cause_id']} に合う合否がまだ無い"
        else:
            why = f"原因 {cause['cause_id']} がまだ解けていない"
        out.append(f"照合 {c['comparison_id']} の食い違い ({c['diff_rel'] * 100:+.0f} %) の{why}")
    crit = load_case.get("criterion")
    for a, r in beyond_results(ctx, load_case, version):
        out.append(f"解析 {a['analysis_id']} ({a['tool']}) の {r['quantity']} が {_amount(r['value'], r['unit'])} で、"
                   f"基準 {OP_JA[crit['op']]} {_amount(crit['limit']['value'], crit['limit']['unit'])} を超えている")
    gap = unchecked_since_beyond(ctx, load_case, version)
    if gap:
        out.append(gap)
    return out


def unchecked_since_beyond(ctx: RunContext, load_case: Record, version: int) -> str | None:
    """docs/contract.md 分野が緑になる条件 (d): when an earlier version of the run had an analysis of the load case
    beyond its criterion, this version needs an ok analysis that gives the judged quantity from a tool of the same
    or a heavier stage than the heaviest tool that was beyond; a lighter tool's pass does not clear it. The reason,
    or None when nothing earlier was beyond or such an analysis is there."""
    from hiraku.tools.shelf import tool_stage  # shelf reads the tools; judge is read by rules

    crit = load_case.get("criterion")
    if crit is None:
        return None
    earlier = [(w, a) for w in range(1, version) for a, _ in beyond_results(ctx, load_case, w)]
    if not earlier:
        return None
    need = max(tool_stage(ctx, a["tool"]) for _, a in earlier)
    w, worst = max(earlier, key=lambda wa: (tool_stage(ctx, wa[1]["tool"]), wa[0]))
    mine = [a for a in ctx.store.records("analysis", version) if a["load_case_id"] == load_case["load_case_id"]
            and tool_stage(ctx, a["tool"]) >= need]
    gives = [a for a in mine if a["status"] == "ok" and any(
        r["quantity"] == judged_quantity(ctx, a["tool"], crit["quantity"]) for r in a.get("results", []))]
    if gives:
        return None
    failed = [a["analysis_id"] for a in mine if a["status"] != "ok"]
    now = f"この版の解析 {'、'.join(failed)} は失敗した" if failed else "この版ではまだ回していない"
    return (f"版 {w} で解析 {worst['analysis_id']} ({worst['tool']}) が基準を超えた。同じか重い段のツールで "
            f"{crit['quantity']} を出す解析が要るが、{now} (軽い段のツールの合格では消えない)")


def beyond_text(ctx: RunContext, load_case: Record, version: int) -> str | None:
    """A load case that ends not judged, said with the tool's numbers when a tool's result is beyond the
    criterion (beyond_results): "ツールでは <where> の <quantity> が <value> で、基準 <op> <limit> を超える
    (the other results at the same place)". No verdict comes of it (HK4: no verdict without a prediction);
    None when no tool result is beyond."""
    crit = load_case.get("criterion")
    lines = []
    for a, r in beyond_results(ctx, load_case, version):
        at = f"{r['where']} の " if r.get("where") else ""
        same = [x for x in a.get("results", []) if r.get("where") and x.get("where") == r["where"] and x is not r]
        more = f" ({'、'.join(x['quantity'] + ' ' + _amount(x['value'], x['unit']) for x in same)})" if same else ""
        lines.append(f"ツール {a['tool']} では {at}{r['quantity']} が {_amount(r['value'], r['unit'])} で、基準 "
                     f"{OP_JA[crit['op']]} {_amount(crit['limit']['value'], crit['limit']['unit'])} を超える{more}")
    return "; ".join(lines) or None


NO_FORMULA_NOTE = "手計算 (K0) の式が無いので判定していない"


def no_formula_cases(ctx: RunContext) -> dict[str, str]:
    """rules.unpredictable_cases: the load cases no K0 formula can predict (no verdict can come)."""
    from hiraku.tools.rules import unpredictable_cases  # rules reads this module

    try:
        return unpredictable_cases(ctx)
    except Exception:  # a context without a registry or shelf: nothing said
        return {}


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
    safe = (" 安全側の予測で" if v.get("basis") == "explained_safe_side"
            else " 道を手計算で確かめ、ツールの値で" if v.get("basis") == PATH_CHECKED
            else f" 手計算が予測していない {v.get('where')} で" if v.get("basis") == HOT_SPOT else "")
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


def hot_spot_asks(ctx: RunContext, reds: list[Record], version: int) -> list[str]:
    """For each hot_spot red (docs/contract.md CAE の役目): the cause it still needs (record_cause with its
    verdict_id) and, when the version's load path is not checked by hand, that check too."""
    out = []
    answered = {c.get("verdict_id") for c in ctx.store.records("cause", version) if c.get("verdict_id")}
    for x in reds:
        if x.get("basis") != HOT_SPOT:
            continue
        asks = [] if x["verdict_id"] in answered else [f"原因を record_cause (verdict_id {x['verdict_id']}) で書く"]
        gap = (path_check(ctx, ctx.load_case(x["load_case_id"]), version)
               if ctx.has_load_case(x["load_case_id"]) else None)
        if gap:
            asks.append(f"道の確かめ ({PATH_FORMULA}) も要る: {gap}")
        if asks:
            out.append(f"{x['test_id']} の {x['where']} は手計算が予測していない場所: " + "。".join(asks))
    return out


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
    if held:  # a result past the limit on a case no formula predicts: said, not hidden (it gets no verdict)
        cannot = no_formula_cases(ctx)
        held = {k: why + [NO_FORMULA_NOTE] if k in cannot else why for k, why in held.items()}
    held_line = "まだ合格にできない試験: " + " / ".join(f"{lc_id} ({'、'.join(why)})" for lc_id, why in held.items())
    if reds or misses:  # red even with tests left without a verdict; they are counted in the line
        lines = ([f"不合格 {len(reds)} 件: " + "、".join(verdict_line(x) for x in reds)] if reds else []) + \
                ([f"系の欠け {len(misses)} 件: " + "、".join(miss_line(r, c) for r, c in misses)] if misses else [])
        state, summary = "red", "。".join(lines + hot_spot_asks(ctx, reds, v))
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
    """Recompute the tile and store it; an unchanged tile is not written again (no judgement.updated). The tile
    is worked out inside the store's reading(): green rule (d) reads every earlier version's analyses for each
    load case, so outside it each update read them all again (a demo run's version 4, all four tiles: 3260 files,
    299 MB; inside: 170 files the first time, none after)."""
    with reading(ctx):
        j = compute_judgement(ctx, domain, version)
    contract.validate("judgement", j)
    if any(t == j for t in ctx.store.judgements(j["design_version"])):
        return j
    return ctx.store.set_judgement(j)


def init_judgements(ctx: RunContext, version: int | None = None) -> list[Record]:
    """A pending tile for every picked domain of a new design version."""
    return [update_judgement(ctx, d, version) for d in ctx.picked]
