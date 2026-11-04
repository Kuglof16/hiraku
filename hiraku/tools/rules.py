"""The rules the hooks enforce (hiraku.contract.protocols.HOOK_RULES).

Pure checks on the run's records. The SDK hooks (hiraku.agent.hooks) and the
scripted driver call check_call() before a tool runs; the tool functions call
it again, so a direct call cannot skip a rule either. Every reason says what
is missing and what to call next, because Claude reads it and acts on it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from hiraku import contract
from hiraku.contract.protocols import HOOK_RULES
from hiraku.tools.context import RunContext
from hiraku.tools.judge import held_cases, latest_verdicts
from hiraku.catalog import is_beyond
from hiraku.tools.shelf import NOT_CHECKED, STAGE_NAMES_JA, load_case_stage, tool_stage

HK1 = "HK1_prediction_first"
HK2 = "HK2_tool_usable"
HK3 = "HK3_cause_before_continue"
HK4 = "HK4_no_stop_before_judged"
HK5 = "HK5_light_first"
HK6 = "HK6_module_scope"

assert set(HOOK_RULES) == {HK1, HK2, HK3, HK4, HK5, HK6}

Record = dict[str, Any]


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


def _stage_ja(n: int) -> str:
    return f"段 {n} ({STAGE_NAMES_JA.get(n, '?')})"


def _verdict_tool(ctx: RunContext, ver: Record) -> str | None:
    """The tool of the analysis a verdict came from (through its comparison, or analysis_id for unsupported_load)."""
    ana_id = ver.get("analysis_id")
    if ana_id is None and ver.get("comparison_id"):
        ana_id = (ctx.store.get("comparison", ver["comparison_id"]) or {}).get("analysis_id")
    return (ctx.store.get("analysis", ana_id) or {}).get("tool") if ana_id else None


def ended_cases(ctx: RunContext, version: int | None = None) -> set[str]:
    """Load cases of the version without a verdict whose mismatches all have a cause recorded with
    ends_case (contract 08cf4d6; a scripted plan's expected_mismatch end writes it too). They cannot be
    judged: no heavier stage waits for them (HK5) and HK4 does not hold a stop for them; their tiles
    keep them not judged."""
    v = version or ctx.version
    if not v:
        return set()
    ended: dict[str, set[str]] = {}
    for cause in ctx.store.records("cause", v):
        if not cause.get("ends_case"):
            continue
        cmp = ctx.store.get("comparison", cause["comparison_id"]) or {}
        pred = ctx.prediction(cmp["prediction_id"]) if cmp.get("prediction_id") else None
        if pred and pred.get("load_case_id"):
            ended.setdefault(pred["load_case_id"], set()).add(cause["cause_id"])
    if not ended:
        return set()
    verdicts = latest_verdicts(ctx, v)
    out = set()
    for lc_id, causes in ended.items():
        if lc_id in verdicts:
            continue
        lc = ctx.load_case(lc_id) if ctx.has_load_case(lc_id) else None
        open_causes = {cause["cause_id"] for _, cause in _mismatches(ctx, lc, v) if cause} if lc else set()
        if open_causes <= causes:
            out.add(lc_id)
    return out


def _mismatches(ctx: RunContext, lc: Record, v: int) -> list:
    from hiraku.tools.judge import unresolved_mismatches

    return unresolved_mismatches(ctx, lc, v)


def lower_stage_left(ctx: RunContext, stage: int, skip: str | None = None) -> list[tuple[int, Record, str]]:
    """HK5: the load cases of the current version, of a stage below `stage` (shelf.load_case_stage),
    in the picked domains and assembly, that are red (a failing verdict), not judged, or held back
    from passing (judge.held_cases): [(stage, load case, what)], lowest stage first. The load case
    `skip` (the one being analyzed) counts only when its latest verdict is red and came from a tool of
    a lower stage (red on the beam: revise before the heavy analysis); not judged or held, it waits as
    the others do not (its heavier tool may be what settles it). Not counted: cases past the limit
    (catalog.is_beyond: meant to fail), cases no usable tool checks and those no K0 formula can
    predict (unpredictable_cases)."""
    v = ctx.version
    if not v or stage <= 0:
        return []
    verdicts = latest_verdicts(ctx, v)
    domains = set(ctx.picked) | {"assembly"}
    held: dict[str, dict[str, list[str]]] = {}
    out = []
    for lc in ctx.load_cases:
        lc_id = lc["load_case_id"]
        if "criterion" not in lc or lc["domain"] not in domains or is_beyond(lc):
            continue
        n = load_case_stage(ctx, lc)
        if n is None or n >= stage:
            continue
        ver = verdicts.get(lc_id)
        if lc_id == skip:  # the same load case: only a red from a lighter tool stops the heavier one
            made_by = _verdict_tool(ctx, ver) if ver is not None and not ver["pass"] else None
            if made_by is not None and tool_stage(ctx, made_by) < stage:
                out.append((n, lc, f"赤、{ver['verdict_id']}"))
            continue
        if ver is not None and not ver["pass"]:
            out.append((n, lc, f"赤、{ver['verdict_id']}"))
            continue
        if lc["domain"] not in held:
            held[lc["domain"]] = held_cases(ctx, lc["domain"], v)
        if lc_id in held[lc["domain"]]:
            out.append((n, lc, "合格にできない: " + "、".join(held[lc["domain"]][lc_id])))
        elif ver is None:
            out.append((n, lc, "未判定"))
    if out:  # cases that cannot be judged hold no heavier stage (contract 110b991): no K0 formula, or ended by a cause
        cannot = set(unpredictable_cases(ctx)) | ended_cases(ctx, v)
        out = [x for x in out if x[1]["load_case_id"] not in cannot]
    return sorted(out, key=lambda x: x[0])


def _h5(ctx: RunContext, lc_id: str, tool: str, domain: str | None) -> Denial | None:
    n = tool_stage(ctx, tool)
    left = lower_stage_left(ctx, n, skip=lc_id)
    if not left:
        return None
    named = "、".join(f"{lc['load_case_id']} ({_stage_ja(s)}、{what})" for s, lc, what in left)
    reds = [x for x in left if x[2].startswith("赤")]
    red_hint = ""
    if reds:
        ids = ", ".join(what.split("、", 1)[1] for _, _, what in reds)
        red_hint = (f"赤のもの ({ids}) は、重い計算に進む前に、設計役 (designer) に通らなかった試験と合否の ID を渡して "
                    f"revise_design で次の版 (版 {ctx.version + 1}) を作らせてください (新しい版は段 0 からやり直す)。")
    return Denial(HK5, (
        f"{tool} は{_stage_ja(n)}のツールなので、{lc_id} の解析は止めました。版 {ctx.version} で、それより軽い段の荷重条件に"
        f"赤か未判定が残っています: {named}。軽い段から確かめてください: 未判定のものは予測 → analyze → compare で"
        f"判定を付けます。{red_hint}同じ段のツールは並べて回してかまいません"
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
            f"ツール {name} は、{NOT_CHECKED}ので使えません (答えの分かっている例をこの実行では回していない)。"
            f"list_tools で usable が true のツール ({usable}) を選んでください"
        ), domain)
    if (name in ctx.tools or spec is not None) and not (spec and spec["usable"]):
        checks = spec["checks"] if spec else []
        failed = [c["case"] for c in checks if not c["pass"]] or ["(答えの分かっている例が無い、またはまだ確かめていない)"]
        return Denial(HK2, (
            f"ツール {name} は答えの分かっている例に通っていないので使えません (通らなかった例: {'; '.join(failed)})。"
            f"list_tools で usable が true のツール ({usable}) を選んでください"
        ), domain)

    if lc_id is None or not ctx.has_load_case(lc_id):
        return None  # the tool function reports an unknown load case
    denial = _h5(ctx, lc_id, name, domain)
    if denial:
        return denial
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


# ---------------------------------------------------------------------------- HK6 (designs with modules)


def design_modules(ctx: RunContext) -> dict[str, Record] | None:
    """The current design's modules by module_id, or None for a design without modules (and before version 1)."""
    if not ctx.version:
        return None
    mods = ctx.store.design().get("modules")
    return {m["module_id"]: m for m in mods} if mods else None


def _module_ja(m: str) -> str:
    return f"{contract.MODULE_NAMES_JA.get(m, m)} ({m})"


def owners_of(mods: dict[str, Record], name: str, key: str = "variables") -> list[str]:
    """The modules whose `key` (variables or choices) list a name."""
    return [m for m, mod in mods.items() if name in (mod.get(key) or [])]


def outside_module(mods: dict[str, Record], module: str, args: dict[str, Any], what: str = "設計役") -> list[str]:
    """One sentence per variable (changes) or choice (choices) the module may not change, naming who may."""
    mod = mods.get(module) or {}
    out = []
    for key, word, names in (("variables", "変数", args.get("changes") or {}), ("choices", "近道", args.get("choices") or {})):
        for name in names:
            if name in (mod.get(key) or []):
                continue
            owners = owners_of(mods, name, key)
            who = "、".join(_module_ja(m) for m in owners) if owners else "どのモジュールにも入っていない"
            out.append(f"{word} {name} は{_module_ja(module)}の{what}が変えられるものではありません (持ち主: {who})")
    return out


def check_hk6(ctx: RunContext, tool: str, args: dict[str, Any], agent: str | None) -> Denial | None:
    """HK6 on a design with modules: revise_design only with `module`, by designer.<module>, inside the module's
    variables and choices, never by the packager; build_version only by the packager, with package variables only.
    agent None (a direct call that names no caller) skips the who-checks and keeps the scope checks."""
    mods = design_modules(ctx)
    if mods is None:
        return None
    if tool == "revise_design":
        if agent == "packager":
            return Denial(HK6, ("パッケージ役は revise_design を使いません。この設計はモジュールに分かれているので、"
                                "その回の提案とレイアウトの変更は build_version で 1 つの版にまとめてください"))
        module = args.get("module")
        if not module:
            return Denial(HK6, ("この設計はモジュールに分かれているので、revise_design には module (どのモジュールの提案か) が要ります。"
                                f"モジュールは {', '.join(mods)} です。自分のモジュールを付けて呼び直してください"))
        if module == "package":
            return Denial(HK6, ("レイアウト (package) の変数・空間・取付点を変えるのはパッケージ役だけで、build_version で変えます。"
                                "パッケージ役に、何をなぜ変えたいかを伝えてください"))
        if module not in mods:
            return Denial(HK6, f"モジュール {module} はこの設計にありません。あるのは {', '.join(mods)} です")
        if agent is not None and agent != f"designer.{module}":
            return Denial(HK6, (f"{_module_ja(module)}の提案を出せるのは、そのモジュールの設計役 (designer.{module}) だけです "
                                f"(呼んだのは {agent})。そのモジュールを変えたいときは、まとめ役から designer.{module} に頼んでください"))
        bad = outside_module(mods, module, args)
        if bad:
            return Denial(HK6, "自分のモジュールの外は変えられません: " + "; ".join(bad)
                          + "。自分のモジュールの変数と近道だけにして呼び直すか、持ち主の設計役に頼んでください")
        return None
    if tool == "build_version":
        if agent is not None and agent != "packager":
            return Denial(HK6, (f"次の版を作る build_version はパッケージ役 (packager) だけが使えます (呼んだのは {agent})。"
                                "モジュールの設計役は revise_design に module を付けて提案してください"))
        if "package" in mods:
            bad = outside_module(mods, "package", {"changes": args.get("changes") or {}}, "パッケージ役")
            if bad:
                return Denial(HK6, "パッケージ役の changes はレイアウトの変数だけです: " + "; ".join(bad)
                              + "。ほかのモジュールの変数は、そのモジュールの設計役が revise_design で提案します")
        return None
    return None


def check_call(ctx: RunContext, tool: str, args: dict[str, Any], agent: str | None = None) -> Denial | None:
    """HK1-HK3, HK5 and HK6 for one tool call. tool is the short name (analyze, compare, ...); agent is the
    caller's contract agent id (HK6 reads it; None skips HK6's who-checks)."""
    if tool in ("revise_design", "build_version"):
        return check_hk6(ctx, tool, args, agent)
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


def _shelf_quantities(ctx: RunContext) -> tuple[set[str], set[str]]:
    """(the registry's formula ids, the quantities predicted whatever the load case: the formulas'
    output names and the shelf tools' pairings of a result with a registry formula, describe()["predicts"])."""
    formulas = ctx.formulas.list_formulas()
    ids = {f["formula_id"] for f in formulas}
    out = {o for f in formulas for o in f.get("outputs", {})}
    for tool in ctx.tools.values():
        describe = getattr(tool, "describe", None)
        if not callable(describe):
            continue
        try:
            pairs = describe().get("predicts") or []
        except Exception:  # a tool made in the run may describe itself badly
            continue
        out |= {p["quantity"] for p in pairs if isinstance(p, dict) and p.get("formula_id") in ids}
    return ids, out


def predicted_quantities(ctx: RunContext, load_case: Record, shelf: tuple[set[str], set[str]] | None = None) -> set[str]:
    """The quantities a K0 formula of the registry (ctx.formulas) can predict for this load case: an
    output of that name, a shelf tool's pairing of a result with a formula (describe()["predicts"]),
    a prediction in the builders' steps for it (hiraku.catalog.build.plan_steps) and the quantities
    already predicted for it in this run. Only pairings with a formula that is in the registry count.
    shelf: _shelf_quantities(ctx), when already worked out."""
    from hiraku.catalog.build import plan_steps

    ids, base = shelf or _shelf_quantities(ctx)
    out = set(base)
    for step in plan_steps(ctx.project["product"], load_case):
        out |= {p["quantity"] for p in step.get("predictions") or [] if p.get("formula_id") in ids}
    out |= {p["quantity"] for p in ctx.store.records("prediction") if p["load_case_id"] == load_case["load_case_id"]}
    return out


def unpredictable_cases(ctx: RunContext) -> dict[str, str]:
    """Load cases of the picked domains, without a verdict of the current version, whose criterion's
    quantity no K0 formula of the registry predicts (predicted_quantities): load case id -> quantity.
    Without a registry (the plugin's hook command, ctx.formulas None) the run's server worked it out
    (ctx.unpredictable)."""
    if ctx.formulas is None:
        return dict(getattr(ctx, "unpredictable", None) or {})
    if not ctx.version:
        return {}
    verdicts = latest_verdicts(ctx, ctx.version)
    out: dict[str, str] = {}
    shelf = None
    for lc in ctx.load_cases:
        if lc["domain"] not in ctx.picked or "criterion" not in lc or lc["load_case_id"] in verdicts:
            continue
        shelf = shelf or _shelf_quantities(ctx)
        q = lc["criterion"]["quantity"]
        if q not in predicted_quantities(ctx, lc, shelf):
            out[lc["load_case_id"]] = q
    return out


def _usable_tools(ctx: RunContext, test_id: str) -> list[str]:
    return [n for n, s in ctx.specs.items() if s.get("usable") and test_id in (s.get("tests") or [])]


PICKS_ONLY_REASON = "分野選びまでの運転 (要件のファイルの stop_after: picks)。設計は作っていません"


def stop_after(ctx: RunContext) -> str | None:
    """The requirements file's stop_after (docs/contract.md 早めに終わる運転): from project.json when it
    carries it, else from the requirements file this process started the run with."""
    return ctx.project.get("stop_after") or (getattr(ctx, "requirements_file", None) or {}).get("stop_after")


def picks_done(ctx: RunContext) -> bool:
    """A picks-only run (stop_after picks) whose domains are picked: it may end there, HK4 waits for nothing."""
    return stop_after(ctx) == "picks" and any(e["kind"] == "domains.picked" for e in ctx.store.events())


def judged_end(ctx: RunContext) -> tuple[str, str | None, str]:
    """(status, stop_reason, stop_code) of a run that ended with every picked domain judged:
    stopped at round_limit when a tile is red and no design revision is left, else done.
    A picks-only run that has its picks ends done with PICKS_ONLY_REASON."""
    if picks_done(ctx):
        return "done", PICKS_ONLY_REASON, "done"
    reds = [j for j in ctx.store.judgements() if j["state"] == "red"]
    if reds and ctx.version - 1 >= ctx.project["round_limit"]:
        names = "、".join(_ja(j["domain"]) for j in reds)
        return ("stopped", f"{names}が赤のまま、設計の直しの回数 (round_limit {ctx.project['round_limit']}) を"
                           f"使い切りました (版 {ctx.version})", "round_limit")
    return "done", None, "done"


def revisions_used_up(ctx: RunContext) -> bool:
    """The design revisions are used up (version - 1 >= round_limit): HK4 lets the lead's stop through
    (contract HOOK_RULES HK4: unless the round limit is reached). runner.end_status then ends the run
    not_judged (a domain without a verdict) or round_limit (a red tile)."""
    return bool(ctx.version) and ctx.version - 1 >= ctx.project["round_limit"]


def red_left_reason(ctx: RunContext) -> str | None:
    """Why the lead may not stop yet although every picked domain is judged: a tile is red and design
    revisions are left (version - 1 < round_limit). Names the red domains, their failing verdicts
    (ver-...), the revisions left and what to do next. None when no tile is red, no revision is
    left, or a picked domain is still pending (stop_reason says that)."""
    if not ctx.version or holding(ctx):
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


def holding(ctx: RunContext, version: int | None = None) -> list[tuple[str, list[str]]]:
    """pending() without the load cases this run cannot judge (unpredictable_cases): those HK4 holds the stop for."""
    cannot = unpredictable_cases(ctx)
    ended = ended_cases(ctx, version)
    out = []
    for d, lcs in pending(ctx, version):
        held = held_cases(ctx, d, version or ctx.version) if (version or ctx.version) else {}
        # a case no K0 formula can predict is not held even with a result beyond the criterion (run N13: R8-stress
        # held the strength role's stop twice): no prediction, so no comparison can settle it; it ends not judged
        left = [lc for lc in lcs if lc not in cannot and lc not in ended]
        if left:
            out.append((d, left))
    return out


def stop_reason(ctx: RunContext, domain: str | None = None) -> str | None:
    """HK4: why the run may not stop yet (None when every picked domain is judged, or what is left
    is only load cases this run cannot judge: unjudged_reason names them). With domain, only that
    domain's pending load cases and open mismatches (a role's own stop). A load case whose test has
    no usable tool on the shelf names make_tool (and get_tool_template)."""
    if picks_done(ctx):
        return None
    if not ctx.version:
        return ("まだ設計がありません。設計役 (designer) に、get_context の archetypes から型を選んで "
                "propose_design で最初の版を作らせてください。版 1 ができると試験の荷重条件が決まります")
    left = [(d, lcs) for d, lcs in holding(ctx) if domain is None or d == domain]
    if not left:
        return None
    # HK5: lower stages first (a case no usable tool checks goes last)
    stage = {lc_id: load_case_stage(ctx, ctx.load_case(lc_id)) for _, lcs in left for lc_id in lcs}
    key = lambda lc_id: 9 if stage[lc_id] is None else stage[lc_id]  # noqa: E731
    left = sorted(((d, sorted(lcs, key=key)) for d, lcs in left), key=lambda x: key(x[1][0]))
    parts = []
    for d, lcs in left:
        held = held_cases(ctx, d, ctx.version)
        steps = []
        for lc_id in lcs:
            lc = ctx.load_case(lc_id)
            q = lc["criterion"]["quantity"]
            at = "" if stage[lc_id] is None else f"{_stage_ja(stage[lc_id])}: "
            if lc_id in held:
                steps.append(f"{at}{lc_id} ({'、'.join(held[lc_id])}。原因を書き、その結果と一致する予測を記録し直して "
                             f"compare で一致させると判定が付く。合う式が無いときは、そう報告して設計役に渡す)")
                continue
            ups = [u["load_case_id"] for u in ctx.upstream_of(lc_id)]
            first = f"{'・'.join(ups)} を照合で一致させ、" if ups else ""
            if not _usable_tools(ctx, lc["test_id"]):
                first = (f"棚に {lc['test_id']} を解析できる使えるツールがありません。get_tool_template でツールの形を見て書き、"
                         f"check_tool で試してから make_tool で棚に入れ、{first}")
            steps.append(f"{at}{lc_id} ({first}{q} を予測 → analyze → compare で一致させると判定が付く)")
        parts.append(f"{_ja(d)}: {'; '.join(steps)}")
    open_ = ctx.open_mismatches(domain)
    tail = ""
    if open_:
        tail = f" 原因の書かれていない食い違い {', '.join(c['comparison_id'] for c in open_)} も残っています (record_cause)。"
    order = " 軽い段から順に (段 0 レイアウトと規則 → 1 軽い計算 → 2 車の動き → 3 重い計算) 確かめ、赤が出たら次の段の前に設計役が直します。" \
        if len({s for s in stage.values() if s is not None}) > 1 else ""
    return (f"まだ判定の無い (合格にできない試験の残る) 分野があるので終われません。{' / '.join(parts)}。{order}{tail}").strip()


def cannot_judge_text(ctx: RunContext) -> str | None:
    """The load cases this run cannot judge (unpredictable_cases), in a sentence; None when there are none."""
    cannot = unpredictable_cases(ctx)
    ended = sorted(ended_cases(ctx) - set(cannot))
    parts = []
    if cannot:
        named = "、".join(f"{lc_id} ({q})" for lc_id, q in cannot.items())
        parts.append(f"{named} は、判定の量を予測する手計算 (K0) の式が式の台帳に無いので、この実行では判定できません "
                     f"(予測が無いと解析も照合もできない)")
    if ended:  # contract 110b991: the mismatch's cause was recorded as ending the step without a verdict
        parts.append(f"{'、'.join(ended)} は、食い違いの原因を記録して判定なしで終えたので、この実行では判定していません")
    return " ".join(parts) or None


def design_refusal(ctx: RunContext) -> str | None:
    """Before the first version: the reason the designer's last propose_design was refused (an
    agent.said of system with data.refused, hiraku.tools.mcp RECORDED_REFUSALS), or None. HK4 lets
    the lead's stop through on it: the roles could not fix what the tool refused."""
    if ctx.version:
        return None
    for e in reversed(ctx.store.events()):
        if e["kind"] == "design.created":
            return None
        if e["kind"] == "agent.said" and (e.get("data") or {}).get("refused") == "propose_design":
            return e["data"].get("reason") or e.get("text") or ""
    return None


def current_report(ctx: RunContext) -> Record | None:
    """The last report written (report.written), when it is of the current design version; else None."""
    for e in reversed(ctx.store.events()):
        if e["kind"] == "report.written":
            rep = e.get("data") or {}
            return rep if ctx.version and rep.get("design_version") == ctx.version else None
    return None


def _names(text: str, word: str) -> bool:
    """Whether `word` (a load case id, test id or domain) stands in `text` as a whole id (R1 not in R10)."""
    return bool(word) and re.search(rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])", text) is not None


def stage_blocked(ctx: RunContext) -> dict[str, str]:
    """HK4 / HK5's dead end (docs/contract.md): once the design revisions are used up, the held load cases
    (holding) that wait only behind a red of a lower stage (lower_stage_left) are not analysed: load case id ->
    the text naming those reds. Empty when revisions are left or nothing waits behind a red."""
    if not revisions_used_up(ctx):
        return {}
    out = {}
    for _, lcs in holding(ctx):
        for lc_id in lcs:
            n = load_case_stage(ctx, ctx.load_case(lc_id))
            if n is None:
                continue
            reds = [(m, lc) for m, lc, what in lower_stage_left(ctx, n, skip=lc_id) if what.startswith("赤")]
            if reds:
                out[lc_id] = ("低い段の赤が残るので判定していない ("
                              + "、".join(f"{_stage_ja(m)} の {lc['load_case_id']}" for m, lc in reds) + ")")
    return out


def stage_blocked_end(ctx: RunContext) -> str | None:
    """When every held load case waits behind a lower stage's red with no revision left: the round_limit
    stop_reason (the red domains and the not judged load cases); None otherwise."""
    left = [lc_id for _, lcs in holding(ctx) for lc_id in lcs]
    blocked = stage_blocked(ctx)
    if not left or any(lc_id not in blocked for lc_id in left):
        return None
    reds = [j for j in ctx.store.judgements() if j["state"] == "red"]
    names = "、".join(_ja(j["domain"]) for j in reds) or "低い段"
    return (f"{names}が赤のまま、設計の直しの回数 (round_limit {ctx.project['round_limit']}) を使い切りました "
            f"(版 {ctx.version})。" + " / ".join(f"{lc_id}: {blocked[lc_id]}" for lc_id in left))


def excused_pending(ctx: RunContext) -> str | None:
    """HK4's way out when what keeps a domain pending is written down: every load case HK4 holds the stop
    for (holding) is either named by an issue of the current version's report (write_report: its load case
    id, test id or domain in the issue's where, what or record_ids), or, once the design revisions are used
    up (revisions_used_up), held only by mismatches whose cause is written (no formula fits; the cause is
    right). Returns those reasons in one text (the lead's stop goes through, and the run ends not_judged
    with them in stop_reason); None when something held is not explained, or nothing is held."""
    if not ctx.version:
        return None
    left = holding(ctx)
    if not left:
        return None
    report = current_report(ctx)
    issues = [(i, " ".join([str(i.get("where") or ""), str(i.get("what") or ""), *map(str, i.get("record_ids") or [])]))
              for i in (report or {}).get("issues") or []]
    used_up = revisions_used_up(ctx)
    blocked = stage_blocked(ctx)
    reasons = []
    for d, lcs in left:
        for lc_id in lcs:
            lc = ctx.load_case(lc_id)
            if lc_id in blocked:  # HK5's dead end: no revision left and a lower stage is red
                reasons.append(f"{lc_id}: {blocked[lc_id]}")
                continue
            named = [i for i, text in issues if any(_names(text, w) for w in (lc_id, lc["test_id"], d))
                     or _ja(d) in text]
            if named:
                said = "; ".join(f"{i['kind']}: {i['what']}" for i in named)
                reasons.append(f"{lc_id}: 報告の issue ({said})")
                continue
            if used_up:
                from hiraku.tools.judge import beyond_results, unresolved_mismatches

                held = unresolved_mismatches(ctx, lc, ctx.version)
                if held and all(cause is not None for _, cause in held) and not beyond_results(ctx, lc, ctx.version):
                    written = "; ".join(f"照合 {c['comparison_id']} の食い違いの原因 {cause['cause_id']}: "
                                        f"{cause.get('cause', '')}" for c, cause in held)
                    reasons.append(f"{lc_id}: {written}")
                    continue
            return None
    head = (f"設計の直しの回数 (round_limit {ctx.project['round_limit']}) を使い切り、" if used_up else "")
    return f"{head}判定の付かない荷重条件の理由が書かれています: " + " / ".join(reasons)


def unjudged_reason(ctx: RunContext) -> str | None:
    """Why a run that ends now ends not judged: stop_reason (or, when every held load case's reason is
    written, excused_pending), and the load cases it cannot judge (cannot_judge_text); before the first
    version, the refusal of propose_design if that is why. None when every picked domain is judged."""
    if picks_done(ctx):
        return None
    refused = design_refusal(ctx)
    if refused is not None:
        return f"最初の版を作れませんでした。設計役の propose_design が断られました: {refused}"
    parts = [x for x in (excused_pending(ctx) or stop_reason(ctx), cannot_judge_text(ctx)) if x]
    return " ".join(parts) or None
