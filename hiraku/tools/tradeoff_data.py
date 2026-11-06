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
# K5 sets a design number on the record without building the design again (k5_sensitivity.with_value), so a tool
# that measures the parts' shapes (stage 0: the assembly, the rules) sees no change: left out unless named.
SHAPE_STAGE = 0
# At most this many load cases in a call with no quantities: the tool runs are then at most
# DEFAULT_CASES_MAX * (1 + 2 * variables) (K5: the value now, then each number up and down)
DEFAULT_CASES_MAX = 6


def _none_allowed(crit: Record) -> bool:
    """A criterion that allows none of something (a count, unit 1, at most 0: interferences, joint gaps, free parts,
    parts outside their space). Its quantity is a whole number held at 0, so a design number has no elasticity on it
    and it cannot be a side of a trade-off; left out of a call with no quantities (the scripted run 20261106-120935:
    7 of the 14 quantities of a 9.4-minute call with three variables were such counts or the assembly's)."""
    limit = crit.get("limit") or {}
    return crit.get("op") in ("<=", "<") and limit.get("unit") == "1" and limit.get("value") == 0


def collect(ctx: Any, paths: list[str], only: set[str]) -> Record:
    """{sens: {quantity: [term]}, meta: {quantity: {label, unit, lower_is_better}}, bases: {quantity: {value,
    record_id}}, skipped: [why]} for the current version's load cases with a criterion (only: those quantities,
    when given). One load case per quantity: the first in the run's order. Without `only`, so that a call with no
    quantities stays short: counts that must be 0 are left out (_none_allowed); a load case judged by a heavy tool
    (HEAVY_STAGE) or by one that measures the parts' shapes (SHAPE_STAGE) is skipped and named; at most
    DEFAULT_CASES_MAX load cases are looked at, the rest named."""
    verdicts = latest_verdicts(ctx, ctx.version)
    sens: dict[str, list[Record]] = {}
    meta: dict[str, Record] = {}
    bases: dict[str, Record] = {}
    skipped: list[str] = []
    for lc in ctx.load_cases:
        crit = lc.get("criterion")
        if not crit or (only and crit["quantity"] not in only) or crit["quantity"] in sens:
            continue
        if not only and _none_allowed(crit):
            continue  # a count that must be 0 (interferences, free parts): no elasticity to trade, and each costs a rerun
        _, ana = report.verdict_sources(ctx, verdicts.get(lc["load_case_id"]))
        if ana is None:
            skipped.append(f"{lc['load_case_id']}: この版の合否がまだ無い")
            continue
        stage = tool_stage(ctx, ana["tool"])
        if not only and stage >= HEAVY_STAGE:
            skipped.append(f"{lc['load_case_id']}: 重い計算のツール ({ana['tool']}) の合否なので、quantities に "
                           f"{crit['quantity']} を名指したときだけ感度を出す (変数ごとにツールを回し直すので時間がかかる)")
            continue
        if not only and stage <= SHAPE_STAGE:
            skipped.append(f"{lc['load_case_id']}: 部品の形から測るツール ({ana['tool']}) の合否で、感度は設計を組み直さずに"
                           "数だけを変えるので動かない。quantities に名指したときだけ見る")
            continue
        if not only and len(sens) >= DEFAULT_CASES_MAX:
            skipped.append(f"{lc['load_case_id']}: quantities を名指さない呼び出しで見る荷重条件は {DEFAULT_CASES_MAX} つまで")
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
    still = [p for p in paths if not _moved_anything(sens, p)]
    if sens and still:
        skipped.append(f"{'・'.join(p.removeprefix('variables.') for p in still)}: どの結果も動かなかった。形の変数 (部品や部材の"
                       "寸法・位置を決めるもの) なら、感度は設計を組み直さずに数だけを変えるので、まだ見られない "
                       "(default_variables の変数か、ツールが値をそのまま読む変数を使う)")
    return {"sens": sens, "meta": meta, "bases": bases, "skipped": skipped}


def _moved_anything(sens: dict[str, list[Record]], path: str) -> bool:
    """Whether K5 found a non-zero elasticity of any result on this design number."""
    for terms in sens.values():
        for t in terms:
            if str(t.get("name", "")).endswith(f" to {path}") and isinstance(t.get("share"), (int, float)) \
                    and abs(t["share"]) > ZERO_SHARE:
                return True
    return False


ZERO_SHARE = 1e-6  # an elasticity below this is no movement (a design number the tool does not read)

# The design numbers a tool reads as they are (K5 can see them), per design type: on offroad-4w-v1 (2026-11-06),
# rebuilding the design with each module variable 5 % up left the parts, members, nodes and sections the same for
# these; motor_peak_power changes the motor's part too but the launch tool reads the number itself. The others
# (wheelbase, track, rail_t, tower_b, ...) shape the parts and members, so a number changed without a rebuild moves
# no result.
DIRECT_VARIABLES = {
    "ladder_4w": ["motor_peak_power", "gear_ratio", "motor_max_speed", "front_spring_rate", "rear_spring_rate",
                  "bumper_stroke", "crush_stiffness", "max_steer_angle"],
}


def default_variables(ctx: Any) -> list[str]:
    """find_tradeoffs' variables when the caller names none: the design type's DIRECT_VARIABLES that the current
    design has, as paths (empty for a design type with no list: the caller then names them)."""
    design = ctx.store.design() if ctx.version else {}
    have = design.get("variables") or {}
    return [f"variables.{n}" for n in DIRECT_VARIABLES.get(design.get("archetype") or "", []) if n in have]


tradeoff.register_collector(collect)
if hasattr(tradeoff, "register_default_variables"):  # the intake's hook (asked for on 11/6); absent before it lands
    tradeoff.register_default_variables(default_variables)
