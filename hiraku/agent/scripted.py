"""The scripted run: no Claude, the same tool functions and hook rules in a fixed order.

The plan per product is data/catalog/scripted.yaml. Every value still comes
from K0 (record_prediction) and the analysis tools; the plan only says which
formula and which inputs to use. A mismatch stops the run: a script cannot
investigate a cause, so it never writes one.
"""

from __future__ import annotations

from typing import Any

from hiraku import contract
from hiraku.catalog import CATALOG_DIR, loading_of, read_data
from hiraku.tools import RunContext, ToolError, rules
from hiraku.tools.functions import analyze, compare, record_prediction, trusted_analysis
from hiraku.tools.summary import design_summary

Record = dict[str, Any]

SCRIPTED_SLUG_SUFFIX = "-scripted"


class ScriptError(RuntimeError):
    pass


def load_plan(product: str) -> Record:
    plans = read_data(CATALOG_DIR / "scripted.yaml")
    if product not in plans:
        raise ScriptError(f"no scripted plan for product {product!r} in data/catalog/scripted.yaml")
    return plans[product]


_SUFFIX_UNITS = {"mm": "mm", "kg": "kg", "MPa": "MPa", "N": "N", "deg": "deg"}


def resolve(ctx: RunContext, ref: Any, load_case: Record) -> Record:
    """Turn one input ref of the plan into a quantity {value, unit}."""
    if isinstance(ref, dict):
        return {"value": float(ref["value"]), "unit": ref["unit"]}
    head, _, rest = str(ref).partition(".")
    design = ctx.store.design()
    try:
        if head == "lc":
            return dict(load_case["params"][rest])
        if head == "req":
            r = next(x for x in ctx.project["requirements"] if x["key"] == rest)
            return {"value": float(r["value"]), "unit": r["unit"]}
        if head == "load":
            return dict(design_summary(design)["loadings"][loading_of(load_case)][rest])
        if head == "summary":
            node: Any = design_summary(design)
            for part in rest.split("."):
                node = node[part]
            return dict(node)
        if head == "design":
            kind, key, field = rest.split(".", 2)
            if kind == "support":
                s = next(x for x in design["supports"] if x["id"] == key)
                unit = field.rsplit("_", 1)[1]
                return {"value": float(s[field]), "unit": _SUFFIX_UNITS[unit]}
            if kind == "section":
                return {"value": float(design["sections"][key]["dims_mm"][field]), "unit": "mm"}
            if kind == "var":
                return dict(design["variables"][key])
        if head == "pred":
            preds = [p for p in ctx.store.records("prediction", ctx.version) if p["quantity"] == rest]
            return {"value": preds[-1]["value"], "unit": preds[-1]["unit"]}
        if head == "ana":
            lc_id, quantity = rest.rsplit(".", 1)
            ana = trusted_analysis(ctx, lc_id)
            hit = next(r for r in ana["results"] if r["quantity"] == quantity)
            return {"value": hit["value"], "unit": hit["unit"]}
    except (KeyError, StopIteration, IndexError, ValueError, TypeError) as e:
        raise ScriptError(f"cannot resolve input ref {ref!r}: {type(e).__name__} {e}") from None
    raise ScriptError(f"unknown input ref {ref!r}")


def _deny(ctx: RunContext, agent: str, tool: str, args: Record) -> bool:
    """Run the same PreToolUse rules the SDK hook runs; record a denial like the hook does."""
    denial = rules.check_call(ctx, tool, args)
    if denial is None:
        return False
    ctx.store.emit("hook.denied", agent=agent, domain=denial.domain, text=denial.reason, data={
        "hook": "PreToolUse", "tool": contract.mcp_name(tool), "rule": denial.rule, "scripted": True,
    })
    return True


def run_scripted(ctx: RunContext, plan: Record | None = None) -> str:
    """Run the plan to the end. Returns the final status (done / stopped / failed)."""
    s = ctx.store
    plan = plan or load_plan(ctx.project["product"])
    s.emit("agent.started", agent="lead", data={"agent_id": "lead"})
    s.emit("agent.said", agent="lead", text="台本の運転です (Claude は呼びません)。決まった順に道具を呼びます")
    started: list[str] = []
    showed_h1 = not plan.get("show_h1", False)
    status, reason = "done", None

    for step in plan["steps"]:
        lc = ctx.load_case(step["load_case"])
        agent = ctx.owner(lc["load_case_id"])
        if agent not in started:
            s.emit("agent.started", agent=agent, domain=lc["domain"] if agent.startswith("cae.") else None,
                   data={"agent_id": agent})
            started.append(agent)
        if not showed_h1:
            _deny(ctx, agent, "analyze", {"tool": step["tool"], "load_case_id": lc["load_case_id"], "prediction_ids": []})
            showed_h1 = True
        if step.get("say"):
            s.emit("agent.said", agent=agent, domain=lc["domain"], text=step["say"])

        try:
            pred_ids = []
            for p in step["predictions"]:
                args = {
                    "load_case_id": lc["load_case_id"],
                    "quantity": p["quantity"],
                    "formula_id": p["formula_id"],
                    "inputs": {k: resolve(ctx, ref, lc) for k, ref in p["inputs"].items()},
                    "assumptions": list(p.get("assumptions", [])),
                }
                if p.get("output"):
                    args["output"] = p["output"]
                pred_ids.append(record_prediction(ctx, args, agent)["prediction_id"])

            args = {"tool": step["tool"], "load_case_id": lc["load_case_id"], "prediction_ids": pred_ids}
            if _deny(ctx, agent, "analyze", args):
                status, reason = "stopped", f"a hook rule stopped analyze on {lc['load_case_id']}"
                break
            ana = analyze(ctx, args, agent)
            if ana["status"] != "ok":
                s.emit("agent.said", agent=agent, domain=lc["domain"], text=f"解析が失敗しました: {ana.get('error', '')}")
                status, reason = "stopped", f"analysis {ana['analysis_id']} failed"
                break

            names = {r["quantity"] for r in ana["results"]}
            for pid in pred_ids:
                pred = ctx.prediction(pid)
                if pred["quantity"] not in names:
                    continue
                cargs = {"prediction_id": pid, "analysis_id": ana["analysis_id"]}
                if _deny(ctx, agent, "compare", cargs):
                    status, reason = "stopped", f"a hook rule stopped compare on {pid}"
                    break
                cmp = compare(ctx, cargs, agent)["comparison"]
                if cmp["status"] == "mismatch":
                    s.emit("agent.said", agent=agent, domain=lc["domain"], text=(
                        f"{cmp['quantity']} が食い違いました (差 {cmp['diff_rel'] * 100:.1f} %)。"
                        f"台本の運転では原因を調べられないので、ここで止めます"))
                    status, reason = "stopped", f"mismatch {cmp['comparison_id']} needs a cause (scripted runs do not write causes)"
                    break
            if status != "done":
                break
        except (ToolError, ScriptError) as e:
            s.emit("agent.said", agent=agent, domain=lc["domain"], text=f"止まりました: {e}")
            status, reason = "stopped", str(e)
            break

    for agent in started:
        s.emit("agent.stopped", agent=agent, data={"agent_id": agent})
    s.emit("agent.stopped", agent="lead", data={"agent_id": "lead"})
    if status == "done":
        left = rules.stop_reason(ctx)
        if left:
            status, reason = "stopped", left
    s.finish(status, stop_reason=reason)
    return status
