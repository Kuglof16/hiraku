"""The scripted run: no Claude, the same tool functions and hook rules in a fixed order.

The plan of a run: the product's steps in data/catalog/scripted.yaml, and for
every other load case the step its test's builder gives
(hiraku.catalog.builders.PLANS), upstream load cases first. Every value
still comes from K0 (record_prediction) and the analysis tools; the plan
only says which formula and which inputs to use. A script cannot
investigate a cause: it writes one only for a mismatch its plan names
(expected_mismatch, with the cause written in the plan and its source); any
other mismatch stops the run. Likewise the safe-side judgement of a
mismatch is the plan's; record_cause still checks the direction of the
numbers. The steps of a version run stage by stage (HK5, light first: the
stage of each step's tool, shelf.tool_stage, layout and rules first); when a
load case of a stage comes out red, the plan's next `revise` entry is the
designer's revise_design before the next stage, and the new version starts
again from stage 0.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hiraku import contract
from hiraku.agent import inbox
from hiraku.contract import layout
from hiraku.agent.session import STOPPED_BY_BUTTON, STOPPED_BY_USER
from hiraku.catalog import REPO_ROOT, is_beyond
from hiraku.tools import RunContext, ToolError, rules
from hiraku.tools.functions import (analyze, archetypes, compare, propose_design, record_cause, record_prediction,
                                    revise_design, rule_check, write_report)
from hiraku.tools.judge import latest_verdicts, verdict_line
from hiraku.tools.plan import (READERS, ReaderError, ScriptError, _load_case_of, _reader, compose_plan, given_inputs, has_plan,  # noqa: F401
                               load_plan, plan_of, resolve)
from hiraku.tools.report import scripted_next
from hiraku.tools.shelf import STAGE_NAMES_JA, tool_stage

Record = dict[str, Any]

SCRIPTED_SLUG_SUFFIX = "-scripted"
NO_MODEL = {"model": None, "effort": None}  # agent.started of a scripted run: no model is called
FIT_TIME_S = 900.0  # the driver fit's time limit when the plan gives none (a guard; fit_evals ends it first)
FIT_EVALS = 150  # designs the driver fit measures when the plan gives no fit_evals


def range_ends(design: Record, changes: dict[str, Record]) -> list[str]:
    """The changes that sit on the lower or upper bound of their variable's range (designgen.variables),
    as 'name value unit (下限|上限)'."""
    from hiraku import designgen

    spec = {s["name"]: s for s in designgen.variables(design)}
    out = []
    for k, q in changes.items():
        s = spec.get(k) or {}
        for bound, word in (("min", "下限"), ("max", "上限")):
            if bound in s and abs(q["value"] - s[bound]) < 1e-9:
                out.append(f"{k} {q['value']:g} {q['unit']} ({word})")
    return out


def fit_driver(ctx: RunContext, changes: dict[str, Record], reason: str, time_s: Any = None,
               evals: Any = None) -> tuple[dict, str]:
    """A revision with `fit: driver`: the plan's own changes, then the driver's place found by code on the design
    with them (hiraku.designgen.driver_fit.best_driver_place: the smallest of the H3 margins made the largest).
    Returns the changes with the fit's added (the fit's win for its variables) and the reason with the margins."""
    from hiraku import designgen
    from hiraku.designgen import driver_fit

    # measured on the design with the entry's variable changes (its choices, the mounts, do not move the driver;
    # with them every design takes longer to build). A fixed number of evaluations (`fit_evals`) makes the answer
    # the same on every machine; the time limit only guards against a fit that does not end.
    base = designgen.regenerate(ctx.store.design(), changes) if changes else ctx.store.design()
    got = driver_fit.best_driver_place(base, time_limit_s=float(time_s or FIT_TIME_S),
                                       max_evals=int(evals or FIT_EVALS))
    small = got["smallest"]
    found = "、".join(f"{k} {v['value']:g} {v['unit']}" for k, v in got["changes"].items()) or "変えるものなし"
    text = (f"{reason}。運転席の位置はコードで探した (driver_fit: いちばん小さい余裕を最大に): {found}。"
            f"いちばん小さい余裕は {small['name']} {small['value']:.1f} {small['unit']} ({small.get('where') or '-'})")
    ends = range_ends(base, got["changes"])
    if ends:  # the answer sits on a bound of the variable's range, and those bounds are not checked
        text += f"。範囲の端 (確かめていない値) で決まった: {'、'.join(ends)}"
    return {**changes, **got["changes"]}, text


REPORT_SUMMARY ="台本の運転の報告です。分野ごとの結果・効いている項・前提・食い違いと原因・確かめていない値は、記録からツールが入れました"


def scripted_summary(ctx: RunContext) -> str:
    """The report's one line for a scripted run, for the person who reads it: from the last version's
    tiles, which picked domains passed, failed or have no verdict (contract.DOMAIN_NAMES_JA). It says
    the run is scripted (no Claude); the parts behind it are in the report's domains."""
    tiles = {j["domain"]: j["state"] for j in ctx.store.judgements()}

    def names(ds: list[str]) -> str:
        return "・".join(contract.DOMAIN_NAMES_JA[d] for d in ds)

    green = [d for d in ctx.picked if tiles.get(d) == "green"]
    red = [d for d in ctx.picked if tiles.get(d) == "red"]
    rest = [d for d in ctx.picked if d not in green and d not in red]
    head = f"台本の運転で、設計の版 {ctx.version} は"
    if green and not red and not rest:
        return f"{head}選んだ {len(green)} 分野 ({names(green)}) がすべて合格"
    parts = ([f"{names(green)} が合格"] if green else []) + ([f"{names(red)} が不合格"] if red else []) \
        + ([f"{names(rest)} は判定が付いていない"] if rest else [])
    return head + "、".join(parts)



def _deny(ctx: RunContext, agent: str, tool: str, args: Record) -> bool:
    """Run the same PreToolUse rules the SDK hook runs; record a denial like the hook does."""
    denial = rules.check_call(ctx, tool, args)
    if denial is None:
        return False
    ctx.store.emit("hook.denied", agent=agent, domain=denial.domain, text=denial.reason, data={
        "hook": "PreToolUse", "tool": contract.mcp_name(tool), "rule": denial.rule, "scripted": True,
    })
    return True


class Stop(Exception):
    """The script cannot go on; the message is the run's stop_reason."""


class StopRequested(Stop):
    """The stop button: stop.request is in the run folder."""


def _check_stop(ctx: RunContext) -> None:
    """Where the run looks at what the screen put: the inbox (no Claude here, so nothing is
    passed on to a lead), then stop.request."""
    if inbox.take(ctx):
        ctx.store.emit("agent.said", agent="system", text="台本の運転なので、Claude には渡しません")
    if ctx.store.stop_requested():
        raise StopRequested(STOPPED_BY_BUTTON)


def _predict(ctx: RunContext, lc: Record, agent: str, specs: list[Record]) -> list[str]:
    ids = []
    for p in specs:
        try:
            given = given_inputs(ctx, p, lc)  # the builder's reader of this formula's inputs off the design
        except ReaderError as e:  # the formula does not apply to this design (e.g. nothing outside point R for R8-push)
            ctx.store.emit("agent.said", agent="system", domain=lc["domain"],
                           text=f"{lc['load_case_id']} の {p['quantity']} は、この設計では式 {p['formula_id']} の入力が作れないので"
                                f"予測しません ({e})",
                           data={"skipped_prediction": p["formula_id"], "load_case_id": lc["load_case_id"],
                                 "quantity": p["quantity"], "reason": str(e)})
            continue
        args = {
            "load_case_id": lc["load_case_id"],
            "quantity": p["quantity"],
            "formula_id": p["formula_id"],
            "inputs": {k: resolve(ctx, ref, lc, given) for k, ref in p["inputs"].items()},
            "assumptions": list(p.get("assumptions", [])),
        }
        if p.get("output"):
            args["output"] = p["output"]
        ids.append(record_prediction(ctx, args, agent)["prediction_id"])
    return ids


def _say(ctx: RunContext, agent: str, lc: Record, text: str) -> None:
    ctx.store.emit("agent.said", agent=agent, domain=lc["domain"], text=text)


def _analyze_and_compare(ctx: RunContext, lc: Record, agent: str, tool: str, pred_ids: list[str],
                         expected: Record | None = None) -> Record | None:
    """Analyze, then compare every prediction the analysis has a result for.

    Returns the comparison of the expected mismatch when it happened (later
    compares in the domain are then denied by HK3, and recorded as such), else None.
    Raises Stop on anything the plan did not expect.
    """
    args = {"tool": tool, "load_case_id": lc["load_case_id"], "prediction_ids": pred_ids}
    _check_stop(ctx)
    denial = rules.check_call(ctx, "analyze", args)
    if _deny(ctx, agent, "analyze", args):
        raise Stop(f"a hook rule ({denial.rule}) stopped analyze on {lc['load_case_id']}: {denial.reason}")
    ana = analyze(ctx, args, agent)
    if ana["status"] != "ok":
        _say(ctx, agent, lc, f"解析が失敗しました: {ana.get('error', '')}")
        raise Stop(f"analysis {ana['analysis_id']} failed")
    decided = [v for v in ctx.store.records("verdict", ctx.version) if v.get("analysis_id") == ana["analysis_id"]]
    if decided:  # the analysis judged the load case itself (unsupported_load: a load with no path): nothing to compare
        _say(ctx, agent, lc, f"{lc['load_case_id']} は、荷重の通り道が無いので不合格です ({decided[-1]['verdict_id']})。"
                             f"予測との照合はしません")
        return None
    names = {r["quantity"] for r in ana["results"]}
    hit = None
    for pid in pred_ids:
        pred = ctx.prediction(pid)
        if pred["quantity"] not in names:
            continue
        cargs = {"prediction_id": pid, "analysis_id": ana["analysis_id"]}
        if _deny(ctx, agent, "compare", cargs):
            if hit is not None:
                continue  # HK3 after the expected mismatch: recorded, the cause comes next
            raise Stop(f"a hook rule stopped compare on {pid}")
        cmp = compare(ctx, cargs, agent)["comparison"]
        if cmp["status"] == "match":
            continue
        if hit is None and expected_for(expected, cmp) is not None:
            hit = cmp
            continue
        _say(ctx, agent, lc, f"{cmp['quantity']} が食い違いました (差 {cmp['diff_rel'] * 100:.1f} %)。"
                             f"台本にこの食い違いの原因は書かれていないので、ここで止めます")
        raise Stop(f"mismatch {cmp['comparison_id']} needs a cause (the plan has none for it)")
    return hit


def expected_for(expected: Any, cmp: Record) -> Record | None:
    """The plan's expected_mismatch entry for a comparison: one entry, or a list of them, each for a quantity
    and optionally a side (`when`: "high" when the prediction is above the analysis, "low" when below), so
    one step can say why the hand calculation is off either way (e.g. R7-hand: low on a version whose
    parts are not mounted, end; high on a stiffer frame, safe side)."""
    entries = expected if isinstance(expected, list) else [expected] if expected else []
    side = "high" if cmp["predicted"]["value"] > cmp["analyzed"]["value"] else "low"
    for e in entries:
        if e["quantity"] == cmp["quantity"] and e.get("when", side) == side:
            return e
    return None


def _run_step(ctx: RunContext, lc: Record, agent: str, tool: str, step: Record) -> None:
    """Predict, analyze, compare. On the expected mismatch, record its cause; then the
    verdict comes from the prediction when the plan judges it safe side (the tool checks
    the numbers), else the step goes on with its retry (itself a step, which may expect
    a mismatch of its own), else with `end` the step ends there (when the criterion judges that
    quantity, the load case stays without a verdict and its tile shows it), else the run stops."""
    if step.get("say"):
        _say(ctx, agent, lc, step["say"])
    expected = step.get("expected_mismatch")
    pred_ids = _predict(ctx, lc, agent, step["predictions"])
    if not pred_ids:  # every formula of the step was skipped: no prediction, so no analysis (HK1)
        _say(ctx, agent, lc, f"{lc['load_case_id']} は、この設計で使える予測の式が無いので、解析しません")
        return
    hit = _analyze_and_compare(ctx, lc, agent, tool, pred_ids, expected)
    if hit is None:
        return  # matched; the plan's retry is not needed
    expected = expected_for(expected, hit)  # the plan's entry for this mismatch (its quantity and its side)
    _say(ctx, agent, lc, f"{hit['quantity']} が食い違いました (差 {hit['diff_rel'] * 100:+.1f} %、"
                         f"許す幅 {hit['tolerance_rel'] * 100:.0f} %)。台本に書いてある原因を記録します")
    args = {"comparison_id": hit["comparison_id"], "cause": expected["cause"], "fix": expected["fix"]}
    if expected.get("safe_side"):
        args.update(safe_side=True, safe_side_reason=expected["safe_side_reason"])
    if expected.get("end"):
        args["ends_case"] = True  # rules.ended_cases reads it: the load case holds no heavier stage nor the stop
    recorded = record_cause(ctx, args, agent)
    ver = recorded["verdict"]
    retry = expected.get("retry")
    if ver is not None:
        if retry is not None:
            raise ScriptError(f"the plan gives {hit['comparison_id']} both safe_side and a retry")
        _say(ctx, agent, lc, f"安全側の食い違いとして、不利な方の値 (予測) で合否を出しました: "
                             f"{'合格' if ver['pass'] else '不合格'} — {verdict_line(ver)}")
        return
    if expected.get("end"):
        if retry is not None:
            raise ScriptError(f"the plan gives {hit['comparison_id']} both end and a retry")
        judged = (lc.get("criterion") or {}).get("quantity") == hit["quantity"]
        default = ("原因を記録しました。手計算では合否を出せないので、この試験は判定の無いまま次へ進みます" if judged
                   else "原因を記録して、この手順はここで終えます")
        _say(ctx, agent, lc, expected.get("end_say") or default)
        return
    if retry is None:
        _say(ctx, agent, lc, expected.get("stop") or "この先の予測は台本にありません。ここで止めます")
        raise Stop(f"the plan has no next prediction after {hit['comparison_id']}")
    _run_step(ctx, lc, agent, tool, retry)


def stage_groups(ctx: RunContext, steps: list[Record]) -> list[tuple[int, list[Record]]]:
    """The steps grouped by stage, lowest first (HK5). A step's stage is its tool's (shelf.tool_stage), or
    the plan's `stage` on the step when it puts a check later (never earlier: HK5 still checks the call),
    and never lower than that of an earlier step of a load case feeding it, so upstream goes first;
    within a stage the steps keep their order (compose_plan: upstream first)."""
    keyed: list[tuple[int, Record]] = []
    for step in steps:
        ups = {u["load_case_id"] for u in ctx.upstream_of(step["load_case"])}
        own = tool_stage(ctx, step["tool"])
        k = max([own, int(step.get("stage", own))] + [kk for kk, st in keyed if st["load_case"] in ups])
        keyed.append((k, step))
    return [(k, [st for kk, st in keyed if kk == k]) for k in sorted({kk for kk, _ in keyed})]


def stage_reds(ctx: RunContext, group: list[Record]) -> list[Record]:
    """The failing verdicts of this version for the load cases of one stage's steps; cases past the
    limit (catalog.is_beyond) are meant to fail and do not count."""
    ids = {st["load_case"] for st in group}
    return [v for lc_id, v in latest_verdicts(ctx, ctx.version).items()
            if lc_id in ids and not v["pass"] and not is_beyond(ctx.load_case(lc_id))]


def _stage_ja(n: int) -> str:
    return f"段 {n} ({STAGE_NAMES_JA.get(n, '?')})"


def _red_tiles(ctx: RunContext) -> list[Record]:
    return [j for j in ctx.store.judgements() if j["design_version"] == ctx.version and j["state"] == "red"]


def _drop_knowledge(ctx: RunContext, drop: Record) -> None:
    """What the screen's server does when a person drops a file on A5: the file in knowledge/,
    then a request inbox/<n>-knowledge.json (hiraku.agent.inbox reads it at the next look)."""
    src = REPO_ROOT / drop["file"]
    name = drop.get("name") or src.name
    folder = ctx.store.path / layout.KNOWLEDGE_DIR
    folder.mkdir(exist_ok=True)
    (folder / name).write_bytes(src.read_bytes())
    box = ctx.store.path / layout.INBOX_DIR
    box.mkdir(exist_ok=True)
    n = max((int(m.group(1)) for p in box.iterdir() if (m := inbox.NAME_RE.match(p.name))), default=0) + 1
    body = {"kind": "knowledge", "file": name, **({"text": drop["text"]} if drop.get("text") else {})}
    tmp = box / f"{n}-knowledge.json.tmp"
    tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    tmp.replace(box / f"{n}-knowledge.json")


def _answer_drop(ctx: RunContext, drop: Record) -> None:
    """The run's next look at the inbox (the dropped file becomes rules), then, for the
    sentences that need a formula, the lead's answer written in the plan (`answer`: formulas
    and skip reasons, as Claude would give them): import_knowledge again on the same path."""
    from hiraku.intake.tools import TOOLS

    told = inbox.take(ctx)
    answer = drop.get("answer")
    if not answer:
        if told:
            ctx.store.emit("agent.said", agent="system", text="台本の運転なので、Claude には渡しません")
        return
    name = drop.get("name") or Path(drop["file"]).name
    args = {"path": f"{layout.KNOWLEDGE_DIR}/{name}", **{k: answer[k] for k in ("formulas", "skip") if k in answer}}
    ctx.store.emit("agent.said", agent="lead", text=answer.get("say") or "式にできない文は、理由を書いて規則にしません")
    try:
        TOOLS["import_knowledge"]["function"](ctx, args, "lead")
    except ToolError as e:
        ctx.store.emit("agent.said", agent="lead", text=f"規則にできませんでした: {e}")
        raise Stop(f"import_knowledge refused the plan's answer: {e}") from None


def _check_rules(ctx: RunContext, agent: str) -> Record | None:
    """The intake's rule_check on this version's parts (rule_check.recorded), said in one line; the
    assembly tile follows the system rules (functions.rule_check)."""
    try:
        out = rule_check(ctx, {}, agent)
    except ToolError as e:
        ctx.store.emit("agent.said", agent=agent, text=f"規則を当てられませんでした: {e}")
        return None
    ctx.store.emit("agent.said", agent=agent, text=out["text"])
    return out


def _ask_on_hits(ctx: RunContext, checked: Record) -> bool:
    """The lead asks the person what to do about the parts a rule hits (the rule's sentence and
    the parts come from the rule_check record): the rules of the files the person dropped when
    they hit anything, else every hit. False when nothing was hit."""
    hits = checked.get("hits") or []
    if not hits:
        return False
    dropped = {e["data"]["doc"]["doc_id"] for e in ctx.store.events()
               if e["kind"] == "knowledge.imported" and e["data"]["doc"].get("origin") == "dropped"}
    hits = [h for h in hits if h["rule_id"].split("-", 1)[0] in dropped] or hits
    named = "、".join(f"{h['rule_id']}「{h['text']}」({'・'.join(h['parts'])})" for h in hits[:3])
    ctx.store.emit("agent.said", agent="lead",
                   text=f"規則に当たる部品があります: {named}。設計を直しますか、それとも、この規則は外しますか")
    return True


def run_scripted(ctx: RunContext, plan: Record | None = None) -> str:
    """Run the plan to the end. Returns the final status (done / stopped / failed).

    A mismatch stops the run unless the plan names it in expected_mismatch;
    then the plan's cause (written in the plan with its source) is recorded,
    and either the plan judges the mismatch safe side (record_cause checks the
    numbers and makes the verdict from the prediction, and the step ends), or
    the step predicts again with the plan's retry, or, with neither, the run
    stops there. After all steps, a red tile and a `revise` entry left in the
    plan make the designer call revise_design, and the steps run again on the
    new version. Ctrl+C finishes the run as stopped and is raised again.
    run.finished carries stop_code: done, round_limit (judged, but a tile is
    red and no revision is left: status stopped), not_judged or user_stop.
    A run that starts without a design (version 0) begins with the designer's
    propose_design: the plan's `propose` entry, else the first archetype with
    its defaults.
    """
    s = ctx.store
    s.emit("agent.started", agent="lead", data={"agent_id": "lead", **NO_MODEL})
    s.emit("agent.said", agent="lead", text="台本の運転です (Claude は呼びません)。決まった順にツールを呼びます")
    if rules.picks_done(ctx):  # docs/contract.md 早めに終わる運転: the picks are the whole run
        s.emit("agent.said", agent="lead", text="分野選びまでの運転なので、ここで終わります")
        s.emit("agent.stopped", agent="lead", data={"agent_id": "lead"})
        status, reason, code = rules.judged_end(ctx)
        s.finish(status, stop_reason=reason, stop_code=code)
        return status
    if not ctx.version and not s.stop_requested():  # building the first version's load cases takes a while
        first = (plan if plan is not None else plan_of(ctx)).get("propose") or {}
        s.emit("agent.started", agent="designer", data={"agent_id": "designer", **NO_MODEL})
        s.emit("agent.said", agent="designer", text=first.get("say") or "要件から、最初の版を作ります")
        kinds = archetypes(ctx) or [{"name": ""}]
        try:
            out = propose_design(ctx, {"archetype": first.get("archetype") or kinds[0]["name"],
                                       "choices": dict(first.get("choices") or {}),
                                       "variables": dict(first.get("variables") or {}),
                                       "reason": first.get("reason") or "要件に合う型を、既定の変数で組んだ"}, "designer")
        except ToolError as e:
            s.emit("agent.said", agent="designer", text=f"最初の版を作れませんでした: {e}")
            s.emit("agent.stopped", agent="designer", data={"agent_id": "designer"})
            s.emit("agent.stopped", agent="lead", data={"agent_id": "lead"})
            s.finish("stopped", stop_reason=f"propose_design refused: {e}", stop_code="not_judged")
            return "stopped"
        s.emit("agent.said", agent="designer", text=f"版 1 ができました。試験: "
                                                    + "、".join(lc["load_case_id"] for lc in out["load_cases"]))
        s.emit("agent.stopped", agent="designer", data={"agent_id": "designer"})
    plan = compose_plan(ctx, plan)
    revisions = list(plan.get("revise") or [])
    if plan["missing"]:
        s.emit("agent.said", agent="lead", text="台本の予測のしかたが無いので回せない荷重条件: " + "、".join(plan["missing"]))
    if plan["unshelved"]:
        s.emit("agent.said", agent="lead", text="ツールが棚に無いので回さない手順: "
               + "、".join(f"{lc} ({tool})" for lc, tool in plan["unshelved"]))
    for note in plan.get("notes") or []:  # what the plan leaves out on purpose, said in the record
        s.emit("agent.said", agent="lead", text=note)
    started: list[str] = []
    showed_hk1 = not plan.get("show_hk1", False)
    drop, dropped, asked = plan.get("knowledge_drop"), False, False
    status, reason, code, interrupted = "done", None, "done", False

    def start(agent: str, domain: str | None = None) -> None:
        if agent not in started:
            s.emit("agent.started", agent=agent, domain=domain, data={"agent_id": agent, **NO_MODEL})
            started.append(agent)

    def revise(text: str) -> None:
        _check_stop(ctx)
        rev = revisions.pop(0)
        start("designer")
        s.emit("agent.said", agent="designer", text=rev.get("say") or text)
        changes, reason = dict(rev.get("changes") or {}), rev["reason"]
        choices = dict(rev.get("choices") or {})  # the generator's short ways (revise_design choices)
        if rev.get("fit") == "driver":  # the seat, steering and pedals searched by code (designgen.driver_fit)
            try:
                changes, reason = fit_driver(ctx, changes, reason, rev.get("fit_time_s"), rev.get("fit_evals"))
            except ValueError as e:  # the generator refuses the entry's own changes (designgen.DesignGenError)
                s.emit("agent.said", agent="designer", text=f"設計を直せませんでした: {e}")
                raise Stop(f"revise_design refused: {e}") from None
        try:
            out = revise_design(ctx, {**({"changes": changes} if changes else {}),
                                      **({"choices": choices} if choices else {}), "reason": reason}, "designer")
        except ToolError as e:
            s.emit("agent.said", agent="designer", text=f"設計を直せませんでした: {e}")
            raise Stop(f"revise_design refused: {e}") from None
        s.emit("agent.said", agent="lead", text=f"設計の版 {out['design_version']} で、段 0 から全部の荷重条件をやり直します")

    def can_revise() -> bool:
        return bool(revisions) and ctx.version - 1 < ctx.project["round_limit"]

    held_back: tuple[int, list[Record]] | None = None
    try:
        _check_stop(ctx)  # pressed while the tools were checked or the first version was built
        while True:
            revised, held_back = False, None
            for stage, group in stage_groups(ctx, plan["steps"]):
                _check_stop(ctx)
                s.emit("agent.said", agent="lead", text=f"版 {ctx.version}、{_stage_ja(stage)}の荷重条件を確かめます: "
                       + "、".join(dict.fromkeys(st["load_case"] for st in group)))
                for step in group:
                    _check_stop(ctx)
                    lc = ctx.load_case(step["load_case"])
                    agent = ctx.owner(lc["load_case_id"])
                    start(agent, lc["domain"] if agent.startswith("cae.") else None)
                    if not showed_hk1:
                        _deny(ctx, agent, "analyze", {"tool": step["tool"], "load_case_id": lc["load_case_id"],
                                                      "prediction_ids": []})
                        showed_hk1 = True
                    try:
                        _run_step(ctx, lc, agent, step["tool"], step)
                    except (ToolError, ScriptError) as e:
                        _say(ctx, agent, lc, f"止まりました: {e}")
                        raise Stop(str(e)) from None
                    if drop and not dropped and lc["load_case_id"] == drop["after"]:  # the person drops a file
                        _drop_knowledge(ctx, drop)
                        _answer_drop(ctx, drop)
                        dropped = True
                reds = stage_reds(ctx, group)
                if not reds:
                    continue
                named = "、".join(f"{v['test_id']} ({v['load_case_id']}、{v['verdict_id']})" for v in reds)
                if can_revise():
                    s.emit("agent.said", agent="lead", text=f"{_stage_ja(stage)}で赤が出ました: {named}。"
                                                            f"重い段に進む前に、設計役が直します")
                    names = "、".join(dict.fromkeys(contract.DOMAIN_NAMES_JA[v["domain"]] for v in reds))
                    revise(f"{names}が赤なので、設計を直します")
                    revised = True
                    break
                held_back = (stage, reds)
                s.emit("agent.said", agent="lead", text=f"{_stage_ja(stage)}で赤が出ました: {named}。設計を直す手が"
                                                        f"残っていないので、重い段には進みません (HK5)")
                break
            if revised:
                continue
            _check_stop(ctx)  # what was put in the inbox during the last step
            if plan.get("rule_check"):  # the knowledge rules on this version's parts
                who = "packager" if any(a["agent_id"] == "packager" for a in ctx.project["agents"]) else "lead"
                if who != "lead":
                    start(who)
                checked = _check_rules(ctx, who)
                if checked and plan.get("ask_on_hits") and not asked:
                    asked = _ask_on_hits(ctx, checked)
            if held_back is not None:
                break
            reds = _red_tiles(ctx)
            if not reds or not can_revise():
                break
            revise(f"{'、'.join(contract.DOMAIN_NAMES_JA[j['domain']] for j in reds)}が赤なので、設計を直します")
    except StopRequested:
        s.emit("agent.said", agent="lead", text="止めるボタンが押されたので、ここで止めます")
        status, reason, code = "stopped", STOPPED_BY_BUTTON, "user_stop"
    except Stop as e:
        status, reason, code = "stopped", str(e), "not_judged"
    except KeyboardInterrupt:
        status, reason, code, interrupted = "stopped", STOPPED_BY_USER, "user_stop", True

    if status == "done" and held_back is not None:  # HK5: a red stage and no revision left
        stage, reds = held_back
        used_up = ctx.version - 1 >= ctx.project["round_limit"]
        names = "、".join(dict.fromkeys(contract.DOMAIN_NAMES_JA[v["domain"]] for v in reds))
        why = (f"設計の直しの回数 (round_limit {ctx.project['round_limit']}) を使い切りました" if used_up
               else "台本に設計の直しが残っていません")
        status, code = "stopped", "round_limit" if used_up else "not_judged"
        reason = (f"{names}が赤のまま ({_stage_ja(stage)})、{why} (版 {ctx.version})。軽い段の赤が残るので、"
                  f"それより重い段の荷重条件は確かめていません (HK5)")
    if status == "done" and not rules.unjudged_reason(ctx):  # every picked domain judged: the lead's report
        rep = plan.get("report") or {}
        nxt, computed = list(rep.get("next") or []), []
        if not nxt:  # the plan names none: the code's, from the records (each item marked as the script's)
            nxt, computed = scripted_next(ctx)
        summary = rep.get("summary") or scripted_summary(ctx)
        if plan.get("notes"):  # the report says it too
            summary = summary + " " + " ".join(plan["notes"])
        write_report(ctx, {"summary": summary, "next": nxt}, "lead",
                     computed=computed)
    for agent in started:
        s.emit("agent.stopped", agent=agent, data={"agent_id": agent})
    s.emit("agent.stopped", agent="lead", data={"agent_id": "lead"})
    if status == "done":
        left = rules.unjudged_reason(ctx)
        if left:
            status, reason, code = "stopped", left, "not_judged"
        else:
            status, reason, code = rules.judged_end(ctx)
    s.finish(status, stop_reason=reason, stop_code=code)
    if reason == STOPPED_BY_BUTTON:
        s.clear_stop_request()
    if interrupted:
        raise KeyboardInterrupt
    return status
