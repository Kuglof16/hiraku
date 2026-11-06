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
from hiraku.tools.shelf import tool_stage

Record = dict[str, Any]

# K5 runs the verdict's tool again for every design number asked. A load case whose verdict came from a tool of
# this stage or above (HK5's 3, the heavy calculations: the shells, the crash beam) is left out unless the caller
# names its quantity (a call with no quantities on the scripted run 20261106-120935 had not ended after 14 minutes)
HEAVY_STAGE = 3


def collect(ctx: Any, paths: list[str], only: set[str]) -> Record:
    """{sens: {quantity: [term]}, meta: {quantity: {label, unit, lower_is_better}}, bases: {quantity: {value,
    record_id}}, skipped: [why]} for the current version's load cases with a criterion (only: those quantities,
    when given). One load case per quantity: the first in the run's order. Without `only`, a load case judged by
    a heavy tool (HEAVY_STAGE) is skipped and named, so that a call with no quantities stays short."""
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
        if not only and tool_stage(ctx, ana["tool"]) >= HEAVY_STAGE:
            skipped.append(f"{lc['load_case_id']}: 重い計算のツール ({ana['tool']}) の合否なので、quantities に "
                           f"{crit['quantity']} を名指したときだけ感度を出す (変数ごとにツールを回し直すので時間がかかる)")
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
