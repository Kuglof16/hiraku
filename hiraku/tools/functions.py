"""The agent-facing tools as plain Python functions.

Each takes the RunContext, the tool input (shape: hiraku.contract
"tool_input.<name>" where one exists) and the contract agent id of the
caller, and returns a JSON-able result (the record itself where there is one).
They never compute physics: values come from the K0 formulas
(ctx.formulas.evaluate) and the analysis tools (AnalysisTool.run).
A ToolError message goes back to Claude as is.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from hiraku import contract
from hiraku.tools import rules
from hiraku.tools.context import RunContext, ToolError
from hiraku.tools.judge import OP_JA, make_verdict, prediction_is_unfavourable, unfavourable_side, update_judgement
from hiraku.tools.summary import design_summary

Record = dict[str, Any]


def _check_input(name: str, args: dict[str, Any]) -> None:
    errs = contract.errors(f"tool_input.{name}", args)
    if errs:
        raise ToolError(f"{name} の入力の形が合いません: " + "; ".join(errs))


def _gate(ctx: RunContext, name: str, args: dict[str, Any]) -> None:
    denial = rules.check_call(ctx, name, args)
    if denial:
        raise ToolError(denial.reason)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


# ---------------------------------------------------------------------------- reading


def _compact_prediction(p: Record) -> Record:
    keys = ("prediction_id", "design_version", "load_case_id", "quantity", "value", "unit", "formula_id", "agent")
    return {k: p[k] for k in keys}


def _compact_analysis(a: Record) -> Record:
    keys = ("analysis_id", "tool", "design_version", "load_case_id", "prediction_ids", "status", "error", "results")
    return {k: a[k] for k in keys if k in a}


def get_context(ctx: RunContext, args: dict[str, Any] | None = None, agent: str | None = None) -> Record:
    """Everything an agent needs to choose formulas and inputs: requirements,
    load cases (with the role that owns each), the current design, records so far."""
    p = ctx.project
    s = ctx.store
    load_cases = [dict(lc, owner=ctx.owner(lc["load_case_id"])) for lc in ctx.load_cases]
    return {
        "run_id": p["run_id"],
        "want": p["want"],
        "product": p["product"],
        "design_version": ctx.version,
        "round_limit": p["round_limit"],
        "requirements": p["requirements"],
        "domain_picks": p["domain_picks"],
        "load_cases": load_cases,
        "design": s.design() if ctx.version else None,
        "design_summary": design_summary(s.design()) if ctx.version else None,
        "judgements": s.judgements() if ctx.version else [],
        "records": {
            "predictions": [_compact_prediction(x) for x in s.records("prediction")],
            "analyses": [_compact_analysis(x) for x in s.records("analysis")],
            "comparisons": s.records("comparison"),
            "causes": s.records("cause"),
            "verdicts": s.records("verdict"),
        },
        "open_mismatches": [c["comparison_id"] for c in ctx.open_mismatches()],
    }


def list_formulas(ctx: RunContext, args: dict[str, Any] | None = None, agent: str | None = None) -> list[Record]:
    """K0 formulas, optionally only those of one tool (formula_id prefix) or one domain."""
    args = args or {}
    out = ctx.formulas.list_formulas()
    if args.get("tool"):
        out = [f for f in out if f["formula_id"].split(".", 1)[0] == args["tool"]]
    if args.get("domain"):
        out = [f for f in out if f["domain"] == args["domain"]]
    return out


def list_tools(ctx: RunContext, args: dict[str, Any] | None = None, agent: str | None = None) -> list[Record]:
    """The checked tool shelf (tool_spec records; usable=false tools cannot be run)."""
    args = args or {}
    out = list(ctx.specs.values())
    if args.get("domain"):
        out = [s for s in out if s["domain"] == args["domain"]]
    return out


# ---------------------------------------------------------------------------- prediction


def _formula(ctx: RunContext, formula_id: str) -> Record:
    formulas = ctx.formulas.list_formulas()
    for f in formulas:
        if f["formula_id"] == formula_id:
            return f
    tool = formula_id.split(".", 1)[0]
    near = [f["formula_id"] for f in formulas if f["formula_id"].startswith(tool + ".")]
    hint = f"{tool} の式は {', '.join(near)} です" if near else "list_formulas で式を探してください"
    raise ToolError(f"式 {formula_id!r} はありません。{hint}")


def record_prediction(ctx: RunContext, args: dict[str, Any], agent: str | None = None) -> Record:
    """Evaluate a K0 formula with the given inputs and record the result as a prediction."""
    _check_input("record_prediction", args)
    if not ctx.version:
        raise ToolError("まだ設計の版がありません")
    lc = ctx.load_case(args["load_case_id"])
    formula = _formula(ctx, args["formula_id"])
    try:
        out = ctx.formulas.evaluate(formula["formula_id"], args["inputs"])
    except (ValueError, KeyError, TypeError) as e:
        raise ToolError(
            f"式 {formula['formula_id']} を計算できませんでした: {e}。"
            f"入力は {', '.join(f'{k} [{v['unit']}]' for k, v in formula['inputs'].items())} です"
        ) from None

    outputs = out["outputs"]
    name = args.get("output")
    if name is None:
        if len(outputs) == 1:
            name = next(iter(outputs))
        elif args["quantity"] in outputs:
            name = args["quantity"]
        else:
            raise ToolError(f"式 {formula['formula_id']} の出力は {', '.join(outputs)} です。output でどれを予測にするか選んでください")
    if name not in outputs:
        raise ToolError(f"式 {formula['formula_id']} に出力 {name!r} はありません。出力は {', '.join(outputs)} です")

    assumptions = list(args["assumptions"])
    assumptions += [a for a in formula.get("assumptions", []) if a not in assumptions]
    pred = {
        "prediction_id": ctx.store.next_id("prediction"),
        "design_version": ctx.version,
        "load_case_id": lc["load_case_id"],
        "test_id": lc["test_id"],
        "domain": lc["domain"],
        "agent": agent or ctx.owner(lc["load_case_id"]),
        "quantity": args["quantity"],
        "value": float(outputs[name]["value"]),
        "unit": outputs[name]["unit"],
        "formula_id": formula["formula_id"],
        "expression": out.get("expression") or formula["expression"],
        "inputs": args["inputs"],
        "terms": out.get("terms", []),
        "assumptions": assumptions,
        "created_at": _now_iso(),
    }
    return ctx.store.add_record("prediction", pred, design_version=ctx.version, agent=pred["agent"],
                                domain=lc["domain"])


# ---------------------------------------------------------------------------- analysis


def trusted_analysis(ctx: RunContext, load_case_id: str) -> Record | None:
    """The newest ok analysis of a load case (current version) that has a matching comparison."""
    v = ctx.version
    matched = {c["analysis_id"] for c in ctx.store.records("comparison", v) if c["status"] == "match"}
    found = [a for a in ctx.store.records("analysis", v)
             if a["load_case_id"] == load_case_id and a["status"] == "ok" and a["analysis_id"] in matched]
    return found[-1] if found else None


def analyze(ctx: RunContext, args: dict[str, Any], agent: str | None = None) -> Record:
    """Run one analysis tool from the shelf on the current design and record it."""
    _check_input("analyze", args)
    name = args["tool"]
    if name not in ctx.tools:
        raise ToolError(f"道具 {name!r} は棚にありません。list_tools で道具を選んでください ({', '.join(ctx.tools)})")
    lc = ctx.load_case(args["load_case_id"])
    _gate(ctx, "analyze", args)

    upstream = {}
    for up in ctx.upstream_of(lc["load_case_id"]):
        a = trusted_analysis(ctx, up["load_case_id"])
        if a is None:
            raise ToolError(
                f"{lc['load_case_id']} は {up['load_case_id']} の解析の結果を入力に使います。"
                f"先に {up['load_case_id']} を予測 → analyze → compare で一致させてください"
            )
        upstream[up["load_case_id"]] = a

    v = ctx.version
    agent = agent or ctx.owner(lc["load_case_id"])
    analysis_id = ctx.store.next_id("analysis")
    ctx.store.emit("analysis.started", agent=agent, domain=lc["domain"], design_version=v, data={
        "analysis_id": analysis_id,
        "tool": name,
        "load_case_id": lc["load_case_id"],
        "prediction_ids": list(args["prediction_ids"]),
    })

    predictions = [ctx.prediction(pid) for pid in args["prediction_ids"]]
    params = dict(args.get("params") or {})
    if upstream:
        params["upstream_analyses"] = upstream
    spec = ctx.specs.get(name, {})
    t0 = time.perf_counter()
    try:
        result = dict(ctx.tools[name].run(ctx.store.design(v), lc, predictions, params))
    except Exception as e:  # a tool bug is recorded as a failed analysis, not lost
        result = {"status": "failed", "error": f"the tool raised {type(e).__name__}: {e}", "results": []}
    wall = time.perf_counter() - t0

    rec = {
        **result,
        "analysis_id": analysis_id,
        "tool": name,
        "base": result.get("base") or spec.get("base", "K0"),
        "design_version": v,
        "load_case_id": lc["load_case_id"],
        "prediction_ids": list(args["prediction_ids"]),
        "status": result.get("status", "failed"),
        "results": result.get("results", []),
    }
    rec.setdefault("wall_time_s", round(wall, 4))
    if upstream:
        ids = rec.get("inputs_from", [])
        rec["inputs_from"] = ids + [a["analysis_id"] for a in upstream.values() if a["analysis_id"] not in ids]
    errs = contract.errors("analysis", rec)
    if errs:  # the tool returned a bad shape; keep the run going with a failed record
        rec = {k: rec[k] for k in ("analysis_id", "tool", "base", "design_version", "load_case_id", "prediction_ids")}
        rec.update(status="failed", results=[], error="the tool returned a record that does not fit the contract: "
                   + "; ".join(errs)[:2000])
    return ctx.store.add_record("analysis", rec, design_version=v, agent=agent, domain=lc["domain"])


# ---------------------------------------------------------------------------- comparison


def _pick_result(analysis: Record, quantity: str) -> Record:
    hits = [r for r in analysis["results"] if r["quantity"] == quantity]
    if not hits:
        have = ", ".join(sorted({r["quantity"] for r in analysis["results"]})) or "なし"
        raise ToolError(
            f"解析 {analysis['analysis_id']} の結果に {quantity!r} がありません (結果: {have})。"
            f"名前が違うだけなら result_quantity で解析の側の名前を指定してください"
        )
    return max(hits, key=lambda r: abs(r["value"]))  # several places: the largest, the safe side


def _resolve_causes(ctx: RunContext, cmp: Record, pred: Record, agent: str) -> None:
    """A later match for the same load case and quantity resolves earlier causes."""
    for cause in ctx.store.records("cause"):
        if cause["resolved"]:
            continue
        old = ctx.store.get("comparison", cause["comparison_id"])
        old_pred = ctx.prediction(old["prediction_id"]) if old else None
        if old_pred and old_pred["load_case_id"] == pred["load_case_id"] and old["quantity"] == cmp["quantity"]:
            v, _ = ctx.store.find("cause", cause["cause_id"])
            ctx.store.add_record("cause", dict(cause, resolved=True, resolved_by=cmp["comparison_id"]),
                                 design_version=v, agent=agent, domain=pred["domain"])


def compare(ctx: RunContext, args: dict[str, Any], agent: str | None = None) -> Record:
    """Compare a prediction with an analysis result. A match against a load case
    with a criterion also makes the verdict and updates the domain's judgement."""
    _check_input("compare", args)
    pred = ctx.prediction(args["prediction_id"])
    if pred is None:
        raise ToolError(f"予測 {args['prediction_id']!r} はありません (get_context の records.predictions)")
    hit = ctx.store.find("analysis", args["analysis_id"])
    if hit is None:
        raise ToolError(f"解析 {args['analysis_id']!r} はありません (get_context の records.analyses)")
    v, ana = hit
    _gate(ctx, "compare", args)
    if ana["status"] != "ok":
        raise ToolError(f"解析 {ana['analysis_id']} は失敗しています: {ana.get('error', '')}。照合できません")
    if pred["prediction_id"] not in ana["prediction_ids"]:
        raise ToolError(
            f"解析 {ana['analysis_id']} は予測 {', '.join(ana['prediction_ids'])} に対して走らせたもので、"
            f"{pred['prediction_id']} は入っていません"
        )
    res = _pick_result(ana, args.get("result_quantity") or pred["quantity"])
    if res["unit"] != pred["unit"]:
        raise ToolError(
            f"単位が違うので照合できません (予測 {pred['unit']}、解析 {res['unit']})。"
            f"K0 の式で解析と同じ単位 {res['unit']} の予測を記録し直してください"
        )
    lc = ctx.load_case(pred["load_case_id"])
    crit = lc.get("criterion")
    agent = agent or ctx.owner(lc["load_case_id"])

    predicted, analyzed = pred["value"], res["value"]
    diff_abs = analyzed - predicted
    if predicted != 0:
        diff_rel = diff_abs / abs(predicted)
    else:
        diff_rel = 0.0 if analyzed == 0 else diff_abs / abs(analyzed)
    tol = lc["tolerance_rel"]
    cmp = {
        "comparison_id": ctx.store.next_id("comparison"),
        "prediction_id": pred["prediction_id"],
        "analysis_id": ana["analysis_id"],
        "quantity": pred["quantity"],
        "predicted": {"value": predicted, "unit": pred["unit"]},
        "analyzed": {"value": analyzed, "unit": res["unit"]},
        "diff_abs": diff_abs,
        "diff_rel": diff_rel,
        "tolerance_rel": tol,
        "status": "match" if abs(diff_rel) <= tol else "mismatch",
        "cause_id": None,
    }
    ctx.store.add_record("comparison", cmp, design_version=v, agent=agent, domain=lc["domain"])
    out: Record = {"comparison": cmp, "verdict": None}
    if cmp["status"] == "match":
        _resolve_causes(ctx, cmp, pred, agent)
        if crit and crit["quantity"] in (pred["quantity"], res["quantity"]) and crit["limit"]["unit"] == res["unit"]:
            ver = make_verdict(ctx, ctx.store.next_id("verdict"), cmp, lc)
            ctx.store.add_record("verdict", ver, design_version=v, agent=agent, domain=lc["domain"])
            out["verdict"] = ver
            if lc["domain"] in ctx.picked:
                update_judgement(ctx, lc["domain"], v)
    return out


def _check_safe_side(cmp: Record, lc: Record | None) -> None:
    """Refuse safe_side unless the load case has a criterion on this quantity and the
    prediction is the unfavourable value (larger for <=, smaller for >=)."""
    crit = (lc or {}).get("criterion")
    if not crit or crit["quantity"] != cmp["quantity"] or crit["limit"]["unit"] != cmp["predicted"]["unit"]:
        what = f"{crit['quantity']} ({crit['limit']['unit']})" if crit else "なし"
        raise ToolError(
            f"safe_side は合否の基準のある量にだけ付けられます。照合 {cmp['comparison_id']} は {cmp['quantity']} "
            f"({cmp['predicted']['unit']}) で、荷重条件の基準は {what} です。safe_side を外して原因と直し方だけ書いてください"
        )
    if prediction_is_unfavourable(cmp, crit["op"]):
        return
    side = "大きい" if unfavourable_side(crit["op"]) == "larger" else "小さい"
    p, a = cmp["predicted"], cmp["analyzed"]
    raise ToolError(
        f"安全側とはみなせません。基準は {crit['quantity']} {OP_JA[crit['op']]} {crit['limit']['value']:.4g} "
        f"{crit['limit']['unit']} なので、{side}方が不利です。いまは解析 ({a['value']:.4g} {a['unit']}) の方が予測 "
        f"({p['value']:.4g} {p['unit']}) より{side}く、予測は不利な側にありません。"
        f"safe_side を外して原因と直し方を書き、直した式で予測し直してください (解析の側を不利とみるなら、解析と一致する予測が要ります)"
    )


def record_cause(ctx: RunContext, args: dict[str, Any], agent: str | None = None) -> Record:
    """Write the cause of a mismatch and how to fix it (this unblocks the domain).

    With safe_side true (and safe_side_reason), the CAE role says the cause explains
    the gap and the prediction bounds the true value from the safe side. The numbers
    are checked first; then a verdict is made from the prediction (the unfavourable
    value), and the domain's judgement is updated.
    """
    _check_input("record_cause", args)
    hit = ctx.store.find("comparison", args["comparison_id"])
    if hit is None:
        raise ToolError(f"照合 {args['comparison_id']!r} はありません")
    v, cmp = hit
    if cmp["status"] != "mismatch":
        raise ToolError(f"照合 {cmp['comparison_id']} は一致しているので、原因を書く必要はありません")
    existing = ctx.causes_by_comparison().get(cmp["comparison_id"])
    if existing:
        raise ToolError(f"照合 {cmp['comparison_id']} の原因は {existing['cause_id']} にもう書かれています")
    if not args["cause"].strip() or not args["fix"].strip():
        raise ToolError("cause と fix の両方に中身を書いてください")
    pred = ctx.prediction(cmp["prediction_id"])
    lc = ctx.load_case(pred["load_case_id"]) if pred else None
    safe = bool(args.get("safe_side"))
    if safe:
        if not (args.get("safe_side_reason") or "").strip():
            raise ToolError("safe_side を付けるときは、予測が本当の値を安全側から押さえている理由を safe_side_reason に書いてください")
        _check_safe_side(cmp, lc)
    domain = pred["domain"] if pred else None
    agent = agent or (ctx.owner(pred["load_case_id"]) if pred else "lead")
    cause = {
        "cause_id": ctx.store.next_id("cause"),
        "comparison_id": cmp["comparison_id"],
        "cause": args["cause"].strip(),
        "fix": args["fix"].strip(),
        "resolved": False,
    }
    if safe:
        cause.update(safe_side=True, safe_side_reason=args["safe_side_reason"].strip())
    ctx.store.add_record("cause", cause, design_version=v, agent=agent, domain=domain)
    ctx.store.add_record("comparison", dict(cmp, cause_id=cause["cause_id"]), design_version=v, agent=agent,
                         domain=domain)
    out: Record = {"cause": cause, "verdict": None}
    if safe:
        ver = make_verdict(ctx, ctx.store.next_id("verdict"), cmp, lc, cause_id=cause["cause_id"])
        ctx.store.add_record("verdict", ver, design_version=v, agent=agent, domain=domain)
        out["verdict"] = ver
        if domain in ctx.picked:
            update_judgement(ctx, domain, v)
    return out


FUNCTIONS = {
    "get_context": get_context,
    "list_formulas": list_formulas,
    "list_tools": list_tools,
    "record_prediction": record_prediction,
    "analyze": analyze,
    "compare": compare,
    "record_cause": record_cause,
}
