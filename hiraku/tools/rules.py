"""The rules the hooks enforce (hiraku.contract.protocols.HOOK_RULES).

Pure checks on the run's records. The SDK hooks (hiraku.agent.hooks) and the
scripted driver call check_call() before a tool runs; the tool functions call
it again, so a direct call cannot skip a rule either. Every reason says what
is missing and what to call next, because Claude reads it and acts on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hiraku import contract
from hiraku.contract.protocols import HOOK_RULES
from hiraku.tools.context import RunContext
from hiraku.tools.judge import latest_verdicts

HK1 = "HK1_prediction_first"
HK2 = "HK2_tool_usable"
HK3 = "HK3_cause_before_continue"
HK4 = "HK4_no_stop_before_judged"

assert set(HOOK_RULES) == {HK1, HK2, HK3, HK4}


@dataclass
class Denial:
    rule: str
    reason: str
    domain: str | None = None


def _ja(domain: str | None) -> str:
    return contract.DOMAIN_NAMES_JA.get(domain or "", domain or "?")


def _h3(ctx: RunContext, domain: str | None, tool: str) -> Denial | None:
    if domain is None:
        return None
    open_ = ctx.open_mismatches(domain)
    if not open_:
        return None
    ids = ", ".join(c["comparison_id"] for c in open_)
    first = open_[0]
    return Denial(HK3, (
        f"{_ja(domain)}の分野に、原因の書かれていない食い違い ({ids}) があるので {tool} は止めました。"
        f"予測 {first['predicted']['value']:.4g} {first['predicted']['unit']} と解析 "
        f"{first['analyzed']['value']:.4g} {first['analyzed']['unit']} の差は {first['diff_rel'] * 100:.1f} % "
        f"(許す幅 {first['tolerance_rel'] * 100:.0f} %)。"
        f"record_cause(comparison_id=\"{first['comparison_id']}\", cause=..., fix=...) で原因と直し方を書いてから進めてください"
    ), domain)


def check_analyze(ctx: RunContext, args: dict[str, Any]) -> Denial | None:
    lc_id = args.get("load_case_id")
    domain = ctx.load_case(lc_id)["domain"] if lc_id and ctx.has_load_case(lc_id) else None

    denial = _h3(ctx, domain, "解析")
    if denial:
        return denial

    name = args.get("tool") or ""
    spec = ctx.specs.get(name)
    if (name in ctx.tools or spec is not None) and not (spec and spec["usable"]):
        checks = spec["checks"] if spec else []
        failed = [c["case"] for c in checks if not c["pass"]] or ["(答えの分かっている例が無い、またはまだ確かめていない)"]
        usable = ", ".join(n for n, s in ctx.specs.items() if s["usable"]) or "なし"
        return Denial(HK2, (
            f"道具 {name} は答えの分かっている例に通っていないので使えません (通らなかった例: {'; '.join(failed)})。"
            f"list_tools で usable が true の道具 ({usable}) を選んでください"
        ), domain)

    if lc_id is None or not ctx.has_load_case(lc_id):
        return None  # the tool function reports an unknown load case
    v = ctx.version
    ids = list(args.get("prediction_ids") or [])
    good = [p["prediction_id"] for p in ctx.store.records("prediction", v) if p["load_case_id"] == lc_id]
    bad = [i for i in ids if i not in good]
    if not ids or bad:
        what = f"予測 {', '.join(bad)} は版 {v} の {lc_id} の予測ではありません" if bad else "予測の ID がありません"
        hint = f"使える予測は {', '.join(good)} です。" if good else ""
        return Denial(HK1, (
            f"予測が無いので解析は止めました ({what})。{hint}"
            f"record_prediction(load_case_id=\"{lc_id}\", ...) で手計算の予測を記録し、"
            f"返った prediction_id を prediction_ids に入れて analyze を呼び直してください"
        ), domain)
    return None


def check_compare(ctx: RunContext, args: dict[str, Any]) -> Denial | None:
    pred = ctx.prediction(args.get("prediction_id") or "")
    return _h3(ctx, pred["domain"] if pred else None, "照合")


CHECKS = {"analyze": check_analyze, "compare": check_compare}


def check_call(ctx: RunContext, tool: str, args: dict[str, Any]) -> Denial | None:
    """HK1-HK3 for one tool call. tool is the short name (analyze, compare, ...)."""
    check = CHECKS.get(tool)
    return check(ctx, args) if check else None


def pending(ctx: RunContext, version: int | None = None) -> list[tuple[str, list[str]]]:
    """Picked domains whose judgement is still pending: [(domain, load case ids without a verdict)]."""
    v = version or ctx.version
    verdicts = latest_verdicts(ctx, v)
    out = []
    for d in ctx.picked:
        missing = [lc["load_case_id"] for lc in ctx.load_cases
                   if lc["domain"] == d and "criterion" in lc and lc["load_case_id"] not in verdicts]
        if missing:
            out.append((d, missing))
    return out


def judged_code(ctx: RunContext) -> str:
    """stop_code of a run that ended with every picked domain judged: round_limit when a tile
    is red and no design revision is left (round_limit), else done."""
    red = any(j["state"] == "red" for j in ctx.store.judgements())
    return "round_limit" if red and ctx.version - 1 >= ctx.project["round_limit"] else "done"


def stop_reason(ctx: RunContext) -> str | None:
    """HK4: why the run may not stop yet (None when every picked domain is judged)."""
    left = pending(ctx)
    if not left:
        return None
    parts = []
    for d, lcs in left:
        steps = []
        for lc_id in lcs:
            lc = ctx.load_case(lc_id)
            q = lc["criterion"]["quantity"]
            ups = [u["load_case_id"] for u in ctx.upstream_of(lc_id)]
            first = f"{'・'.join(ups)} を照合で一致させ、" if ups else ""
            steps.append(f"{lc_id} ({first}{q} を予測 → analyze → compare で一致させると判定が付く)")
        parts.append(f"{_ja(d)}: {'; '.join(steps)}")
    open_ = ctx.open_mismatches()
    tail = ""
    if open_:
        tail = f" 原因の書かれていない食い違い {', '.join(c['comparison_id'] for c in open_)} も残っています (record_cause)。"
    return f"まだ判定の無い分野があるので終われません。{' / '.join(parts)}。{tail}".strip()
