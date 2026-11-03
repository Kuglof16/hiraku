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
from hiraku.tools.judge import held_cases, latest_verdicts
from hiraku.tools.shelf import NOT_CHECKED

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
    usable = ", ".join(n for n, s in ctx.specs.items() if s["usable"]) or "なし"
    if name in ctx.tools and spec is None:
        return Denial(HK2, (
            f"道具 {name} は、{NOT_CHECKED}ので使えません (答えの分かっている例をこの実行では回していない)。"
            f"list_tools で usable が true の道具 ({usable}) を選んでください"
        ), domain)
    if (name in ctx.tools or spec is not None) and not (spec and spec["usable"]):
        checks = spec["checks"] if spec else []
        failed = [c["case"] for c in checks if not c["pass"]] or ["(答えの分かっている例が無い、またはまだ確かめていない)"]
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
    """Picked domains whose judgement is still pending: [(domain, load case ids)], the load cases
    without a verdict and (HK4, docs/contract.md 分野が緑になる条件) those held back from passing by an
    unresolved mismatch or a result beyond the criterion (judge.held_cases)."""
    v = version or ctx.version
    verdicts = latest_verdicts(ctx, v)
    out = []
    for d in ctx.picked:
        held = held_cases(ctx, d, v) if v else {}
        left = [lc["load_case_id"] for lc in ctx.load_cases
                if lc["domain"] == d and "criterion" in lc
                and (lc["load_case_id"] not in verdicts or lc["load_case_id"] in held)]
        if left:
            out.append((d, left))
    return out


def judged_end(ctx: RunContext) -> tuple[str, str | None, str]:
    """(status, stop_reason, stop_code) of a run that ended with every picked domain judged:
    stopped at round_limit when a tile is red and no design revision is left, else done."""
    reds = [j for j in ctx.store.judgements() if j["state"] == "red"]
    if reds and ctx.version - 1 >= ctx.project["round_limit"]:
        names = "、".join(_ja(j["domain"]) for j in reds)
        return ("stopped", f"{names}が赤のまま、設計の直しの回数 (round_limit {ctx.project['round_limit']}) を"
                           f"使い切りました (版 {ctx.version})", "round_limit")
    return "done", None, "done"


def red_left_reason(ctx: RunContext) -> str | None:
    """Why the lead may not stop yet although every picked domain is judged: a tile is red and design
    revisions are left (version - 1 < round_limit). Names the red domains, their failing verdicts
    (ver-...), the revisions left and what to do next. None when no tile is red, no revision is
    left, or a picked domain is still pending (stop_reason says that)."""
    if not ctx.version or pending(ctx):
        return None
    limit = ctx.project["round_limit"]
    left = limit - (ctx.version - 1)
    reds = [j for j in ctx.store.judgements() if j["state"] == "red" and j["domain"] in ctx.picked]
    if not reds or left <= 0:
        return None
    verdicts = latest_verdicts(ctx, ctx.version)
    parts, ids = [], []
    for j in reds:
        failed = [v for v in verdicts.values() if v["domain"] == j["domain"] and not v["pass"]]
        ids += [v["verdict_id"] for v in failed]
        what = "、".join(f"{v['test_id']} ({v['load_case_id']}、{v['verdict_id']})" for v in failed) or j["summary"]
        parts.append(f"{_ja(j['domain'])}: {what}")
    hand = f"と合否の ID ({', '.join(ids)}) " if ids else " "
    return (f"赤の分野が残っていますが、設計の直しがあと {left} 回できます (round_limit {limit}、今の版 {ctx.version})。"
            f"{' / '.join(parts)}。設計役 (designer) に、通らなかった試験{hand}を渡して、revise_design で"
            f"次の版 (版 {ctx.version + 1}) を作らせてください。新しい版で全部の荷重条件をやり直します")


def stop_reason(ctx: RunContext, domain: str | None = None) -> str | None:
    """HK4: why the run may not stop yet (None when every picked domain is judged). With domain,
    only that domain's pending load cases and open mismatches (a role's own stop)."""
    if not ctx.version:
        return ("まだ設計がありません。設計役 (designer) に、get_context の archetypes から型を選んで "
                "propose_design で最初の版を作らせてください。版 1 ができると試験の荷重条件が決まります")
    left = [(d, lcs) for d, lcs in pending(ctx) if domain is None or d == domain]
    if not left:
        return None
    parts = []
    for d, lcs in left:
        held = held_cases(ctx, d, ctx.version)
        steps = []
        for lc_id in lcs:
            lc = ctx.load_case(lc_id)
            q = lc["criterion"]["quantity"]
            if lc_id in held:
                steps.append(f"{lc_id} ({'、'.join(held[lc_id])}。原因を書き、その結果と一致する予測を記録し直して "
                             f"compare で一致させると判定が付く。合う式が無いときは、そう報告して設計役に渡す)")
                continue
            ups = [u["load_case_id"] for u in ctx.upstream_of(lc_id)]
            first = f"{'・'.join(ups)} を照合で一致させ、" if ups else ""
            steps.append(f"{lc_id} ({first}{q} を予測 → analyze → compare で一致させると判定が付く)")
        parts.append(f"{_ja(d)}: {'; '.join(steps)}")
    open_ = ctx.open_mismatches(domain)
    tail = ""
    if open_:
        tail = f" 原因の書かれていない食い違い {', '.join(c['comparison_id'] for c in open_)} も残っています (record_cause)。"
    return f"まだ判定の無い (合格にできない試験の残る) 分野があるので終われません。{' / '.join(parts)}。{tail}".strip()
