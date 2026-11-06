"""The sensitivities find_tradeoffs (hiraku.intake.tools) reads: for the current version's load cases that have a
criterion and a verdict, K5's sensitivities of the judged quantity to some design numbers, with the result now and
the analysis it comes from. hiraku.intake cannot import hiraku.tools (the layers), so this module registers its
collect with hiraku.intake.tradeoff.register_collector when it is imported (hiraku.tools.mcp imports it).
"""

from __future__ import annotations

from typing import Any

from hiraku.contract.protocols import ToolError
from hiraku.intake import tradeoff
from hiraku.tools import report
from hiraku.tools.judge import latest_verdicts

Record = dict[str, Any]


def collect(ctx: Any, paths: list[str], only: set[str]) -> Record:
    """{sens: {quantity: [term]}, meta: {quantity: {label, unit, lower_is_better}}, bases: {quantity: {value,
    record_id}}, skipped: [why]} for the current version's load cases with a criterion (only: those quantities,
    when given). One load case per quantity: the first in the run's order."""
    verdicts = latest_verdicts(ctx, ctx.version)
    sens: dict[str, list[Record]] = {}
    meta: dict[str, Record] = {}
    bases: dict[str, Record] = {}
    skipped: list[str] = []
    for lc in ctx.load_cases:
        crit = lc.get("criterion")
        if not crit or (only and crit["quantity"] not in only) or crit["quantity"] in sens:
            continue
        _, ana = report.verdict_sources(ctx, verdicts.get(lc["load_case_id"]))
        if ana is None:
            skipped.append(f"{lc['load_case_id']}: この版の合否がまだ無い")
            continue
        value = next((r for r in ana.get("results") or [] if r.get("quantity") == crit["quantity"]), None)
        if value is None or not isinstance(value.get("value"), (int, float)):
            skipped.append(f"{lc['load_case_id']}: 解析に {crit['quantity']} の値が無い")
            continue
        try:
            got = report.sensitivities(ctx, [{"load_case_id": lc["load_case_id"], "paths": paths}])[0]
        except ToolError as e:
            skipped.append(f"{lc['load_case_id']}: {e}")
            continue
        q = crit["quantity"]
        sens[q] = got["terms"]
        meta[q] = {"label": lc.get("title") or q, "unit": value.get("unit") or "1",
                   "lower_is_better": crit["op"] in ("<=", "<")}
        bases[q] = {"value": float(value["value"]), "record_id": ana["analysis_id"]}
    return {"sens": sens, "meta": meta, "bases": bases, "skipped": skipped}


tradeoff.register_collector(collect)
