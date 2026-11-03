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
numbers. When a judgement comes out red, the plan's next `revise` entry is
the designer's revise_design, and every step runs again on the new version.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from hiraku import contract
from hiraku.agent import inbox
from hiraku.agent.session import STOPPED_BY_BUTTON, STOPPED_BY_USER
from hiraku.catalog import CATALOG_DIR, beyond_plans, external_builders, is_beyond, loading_of, read_data
from hiraku.tools import RunContext, ToolError, rules
from hiraku.tools.functions import (analyze, archetypes, compare, propose_design, record_cause, record_prediction,
                                    revise_design, trusted_analysis, write_report)
from hiraku.tools.judge import verdict_line
from hiraku.tools.summary import design_summary

Record = dict[str, Any]

SCRIPTED_SLUG_SUFFIX = "-scripted"
REPORT_SUMMARY = "台本の運転の報告です。分野ごとの結果・効いている項・前提・食い違いと原因・確かめていない値は、記録から道具が入れました"


class ScriptError(RuntimeError):
    pass


def load_plan(product: str) -> Record:
    """The product's plan in data/catalog/scripted.yaml (no steps when it has none)."""
    return read_data(CATALOG_DIR / "scripted.yaml").get(product) or {"steps": []}


def compose_plan(ctx: RunContext, plan: Record | None = None) -> Record:
    """The product's plan plus a step from the builders' PLANS for each load case it lacks
    (PLANS_BEYOND first for a case past the limit), ordered upstream first. `missing` lists
    the load cases nobody gave a step for. A product step whose load case this run did not
    build (another step height, say) is left out: its causes were written for that load case.
    So is a step whose tool is not on this run's shelf; `unshelved` names those (load case, tool)."""
    out = copy.deepcopy(plan if plan is not None else load_plan(ctx.project["product"]))
    # each step its own copy: steps may share parts through YAML aliases
    built = [copy.deepcopy(s) for s in out["steps"] if ctx.has_load_case(s["load_case"])]
    out["steps"] = [s for s in built if s["tool"] in ctx.tools]
    out["unshelved"] = [(s["load_case"], s["tool"]) for s in built if s["tool"] not in ctx.tools]
    have = {s["load_case"] for s in out["steps"]}
    recipes, past = external_builders()[2], beyond_plans()
    out["missing"] = []
    for lc in ctx.load_cases:
        if lc["load_case_id"] in have:
            continue
        recipe = (is_beyond(lc) and past.get(lc["test_id"])) or recipes.get(lc["test_id"])
        if recipe is None:
            out["missing"].append(lc["load_case_id"])
        else:
            out["steps"].append({**recipe, "load_case": lc["load_case_id"]})
    ordered: list[Record] = []
    left = list(out["steps"])
    in_plan = {s["load_case"] for s in left}
    while left:  # a step goes once the load cases feeding it (and in the plan) have gone
        done = {s["load_case"] for s in ordered}
        ready = next((s for s in left if all(u["load_case_id"] in done or u["load_case_id"] not in in_plan
                                             for u in ctx.upstream_of(s["load_case"]))), left[0])
        ordered.append(ready)
        left.remove(ready)
    out["steps"] = ordered
    return out


_SUFFIX_UNITS = {"mm": "mm", "kg": "kg", "MPa": "MPa", "N": "N", "deg": "deg"}

_NAME_REF = re.compile(r"\{([^{}]+)\}")


def _load_case_of(ctx: RunContext, ref: str, load_case: Record) -> str:
    """A load case id, or for a test id (ana.P1.x) the run's load case of that test that feeds
    this one (a case past the limit is fed by the upstream case past the limit), else the first."""
    if ctx.has_load_case(ref):
        return ref
    of_test = [lc for lc in ctx.load_cases if lc["test_id"] == ref]
    feeding = [lc for lc in of_test if load_case["load_case_id"] in lc.get("feeds", [])]
    return (feeding or of_test)[0]["load_case_id"]


def _lookup(ctx: RunContext, ref: str, load_case: Record, given: Record | None = None) -> Any:
    """What one plan ref points at: a quantity record, or a name (e.g. load.rail_joint.support).
    `given` is what the prediction's inputs_from read off the design (from.<name>)."""
    head, _, rest = ref.partition(".")
    design = ctx.store.design()
    if head == "from":
        if given is None:
            raise ScriptError(f"{ref!r}: the prediction has no inputs_from")
        return given[rest]
    if head == "lc":
        return load_case["params"][rest]
    if head == "req":
        return next(x for x in ctx.project["requirements"] if x["key"] == rest)
    if head in ("load", "summary"):
        node: Any = design_summary(design)
        if head == "load":
            node = node["loadings"][loading_of(load_case)]
        for part in rest.split("."):
            node = node[part]
        return node
    if head == "design":
        kind, _, rest = rest.partition(".")
        if kind == "var":
            return design["variables"][rest]
        key, field = rest.split(".", 1)
        if kind == "support":
            s = next(x for x in design["supports"] if x["id"] == key)
            return {"value": s[field], "unit": _SUFFIX_UNITS[field.rsplit("_", 1)[1]]}
        if kind == "section":
            return {"value": design["sections"][key]["dims_mm"][field], "unit": "mm"}
    if head == "pred":
        return [p for p in ctx.store.records("prediction", ctx.version) if p["quantity"] == rest][-1]
    if head == "ana":
        lc_id, quantity = rest.rsplit(".", 1)
        return next(r for r in trusted_analysis(ctx, _load_case_of(ctx, lc_id, load_case))["results"]
                    if r["quantity"] == quantity)
    raise ScriptError(f"unknown input ref {ref!r}")


def _name(ctx: RunContext, ref: str, load_case: Record, given: Record | None) -> str:
    got = _lookup(ctx, ref, load_case, given)
    if not isinstance(got, str):
        raise ScriptError(f"{{{ref}}} must point at a name, not {got!r}")
    return got


def resolve(ctx: RunContext, ref: Any, load_case: Record, given: Record | None = None) -> Record:
    """Turn one input ref of the plan into a quantity {value, unit}.

    `{ref}` inside a ref is replaced by the name it points at first, e.g.
    ana.P1-step100.wheel_load_{load.rail_joint.support}.
    """
    if isinstance(ref, dict):
        return {"value": float(ref["value"]), "unit": ref["unit"]}
    try:
        text = _NAME_REF.sub(lambda m: _name(ctx, m.group(1), load_case, given), str(ref))
        q = _lookup(ctx, text, load_case, given)
        return {"value": float(q["value"]), "unit": q["unit"]}
    except (KeyError, StopIteration, IndexError, ValueError, TypeError) as e:
        raise ScriptError(f"cannot resolve input ref {ref!r}: {type(e).__name__} {e}") from None


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
        given = None
        if p.get("inputs_from"):  # the builder's reader of this formula's inputs off the design
            try:
                given = p["inputs_from"](ctx.store.design(), lc)
            except (KeyError, ValueError, TypeError) as e:
                raise ScriptError(f"inputs_from of {p['formula_id']} cannot read this design: {e}") from None
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
    if _deny(ctx, agent, "analyze", args):
        raise Stop(f"a hook rule stopped analyze on {lc['load_case_id']}")
    ana = analyze(ctx, args, agent)
    if ana["status"] != "ok":
        _say(ctx, agent, lc, f"解析が失敗しました: {ana.get('error', '')}")
        raise Stop(f"analysis {ana['analysis_id']} failed")
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
        if expected and hit is None and cmp["quantity"] == expected["quantity"]:
            hit = cmp
            continue
        _say(ctx, agent, lc, f"{cmp['quantity']} が食い違いました (差 {cmp['diff_rel'] * 100:.1f} %)。"
                             f"台本にこの食い違いの原因は書かれていないので、ここで止めます")
        raise Stop(f"mismatch {cmp['comparison_id']} needs a cause (the plan has none for it)")
    return hit


def _run_step(ctx: RunContext, lc: Record, agent: str, tool: str, step: Record) -> None:
    """Predict, analyze, compare. On the expected mismatch, record its cause; then the
    verdict comes from the prediction when the plan judges it safe side (the tool checks
    the numbers), else the step goes on with its retry (itself a step, which may expect
    a mismatch of its own), else with `end` the step ends there (when the criterion judges that
    quantity, the load case stays without a verdict and its tile shows it), else the run stops."""
    if step.get("say"):
        _say(ctx, agent, lc, step["say"])
    expected = step.get("expected_mismatch")
    hit = _analyze_and_compare(ctx, lc, agent, tool, _predict(ctx, lc, agent, step["predictions"]), expected)
    if hit is None:
        return  # matched; the plan's retry is not needed
    _say(ctx, agent, lc, f"{hit['quantity']} が食い違いました (差 {hit['diff_rel'] * 100:+.1f} %、"
                         f"許す幅 {hit['tolerance_rel'] * 100:.0f} %)。台本に書いてある原因を記録します")
    args = {"comparison_id": hit["comparison_id"], "cause": expected["cause"], "fix": expected["fix"]}
    if expected.get("safe_side"):
        args.update(safe_side=True, safe_side_reason=expected["safe_side_reason"])
    ver = record_cause(ctx, args, agent)["verdict"]
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


def _red_tiles(ctx: RunContext) -> list[Record]:
    return [j for j in ctx.store.judgements() if j["design_version"] == ctx.version and j["state"] == "red"]


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
    s.emit("agent.started", agent="lead", data={"agent_id": "lead"})
    s.emit("agent.said", agent="lead", text="台本の運転です (Claude は呼びません)。決まった順に道具を呼びます")
    if not ctx.version and not s.stop_requested():  # building the first version's load cases takes a while
        first = (plan if plan is not None else load_plan(ctx.project["product"])).get("propose") or {}
        s.emit("agent.started", agent="designer", data={"agent_id": "designer"})
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
        s.emit("agent.said", agent="lead", text="道具が棚に無いので回さない手順: "
               + "、".join(f"{lc} ({tool})" for lc, tool in plan["unshelved"]))
    started: list[str] = []
    showed_hk1 = not plan.get("show_hk1", False)
    status, reason, code, interrupted = "done", None, "done", False

    def start(agent: str, domain: str | None = None) -> None:
        if agent not in started:
            s.emit("agent.started", agent=agent, domain=domain, data={"agent_id": agent})
            started.append(agent)

    try:
        _check_stop(ctx)  # pressed while the tools were checked or the first version was built
        while True:
            for step in plan["steps"]:
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
            reds = _red_tiles(ctx)
            if not reds or not revisions or ctx.version - 1 >= ctx.project["round_limit"]:
                break
            _check_stop(ctx)
            rev = revisions.pop(0)
            start("designer")
            names = "、".join(contract.DOMAIN_NAMES_JA[j["domain"]] for j in reds)
            s.emit("agent.said", agent="designer", text=rev.get("say") or f"{names}が赤なので、設計を直します")
            try:
                out = revise_design(ctx, {"changes": rev["changes"], "reason": rev["reason"]}, "designer")
            except ToolError as e:
                s.emit("agent.said", agent="designer", text=f"設計を直せませんでした: {e}")
                raise Stop(f"revise_design refused: {e}") from None
            s.emit("agent.said", agent="lead", text=f"設計の版 {out['design_version']} で、全部の荷重条件をやり直します")
    except StopRequested:
        s.emit("agent.said", agent="lead", text="止めるボタンが押されたので、ここで止めます")
        status, reason, code = "stopped", STOPPED_BY_BUTTON, "user_stop"
    except Stop as e:
        status, reason, code = "stopped", str(e), "not_judged"
    except KeyboardInterrupt:
        status, reason, code, interrupted = "stopped", STOPPED_BY_USER, "user_stop", True

    if status == "done" and not rules.stop_reason(ctx):  # every picked domain judged: the lead's report
        rep = plan.get("report") or {}
        write_report(ctx, {"summary": rep.get("summary") or REPORT_SUMMARY, "next": list(rep.get("next") or [])}, "lead")
    for agent in started:
        s.emit("agent.stopped", agent=agent, data={"agent_id": agent})
    s.emit("agent.stopped", agent="lead", data={"agent_id": "lead"})
    if status == "done":
        left = rules.stop_reason(ctx)
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
