"""The agent-facing tools as plain Python functions.

Each takes the RunContext, the tool input (shape: hiraku.contract
"tool_input.<name>" where one exists) and the contract agent id of the
caller, and returns a JSON-able result (the record itself where there is one).
They never compute physics: values come from the K0 formulas
(ctx.formulas.evaluate) and the analysis tools (AnalysisTool.run).
A ToolError message goes back to Claude as is.
"""

from __future__ import annotations

import copy
import importlib
import threading
import time
from datetime import datetime
from typing import Any

from hiraku import contract
from hiraku.tools import rules
from hiraku.tools.context import RunContext, ToolError
from hiraku.tools.judge import (OP_JA, init_judgements, make_verdict, prediction_is_unfavourable, unfavourable_side,
                                update_judgement)
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


# get_context answers with the small parts unless asked for more: every call goes into the
# caller's context, so the design (large) and the designer's variables come only on request.
CONTEXT_SECTIONS = ("requirements", "load_cases", "judgements", "records", "design_summary", "design",
                    "design_variables", "archetypes")
DEFAULT_CONTEXT_SECTIONS = ("requirements", "load_cases", "judgements", "records")


def get_context(ctx: RunContext, args: dict[str, Any] | None = None, agent: str | None = None) -> Record:
    """The run so far: always the run, design version, picked domains and open mismatches;
    `sections` adds parts (default: requirements, load_cases with the role that owns each,
    judgements, records). design_summary (inputs for hand calculations), design (the whole
    record) and design_variables (what the designer may change) come only when asked."""
    p = ctx.project
    s = ctx.store
    wanted = (args or {}).get("sections") or DEFAULT_CONTEXT_SECTIONS
    out: Record = {
        "run_id": p["run_id"],
        "want": p["want"],
        "product": p["product"],
        "design_version": ctx.version,
        "round_limit": p["round_limit"],
        "domain_picks": p["domain_picks"],
        "open_mismatches": [c["comparison_id"] for c in ctx.open_mismatches()],
        "sections": list(wanted),
    }
    parts = {
        "requirements": lambda: p["requirements"],
        "load_cases": lambda: [dict(lc, owner=ctx.owner(lc["load_case_id"])) for lc in ctx.load_cases],
        "judgements": lambda: s.judgements() if ctx.version else [],
        "records": lambda: {
            "predictions": [_compact_prediction(x) for x in s.records("prediction")],
            "analyses": [_compact_analysis(x) for x in s.records("analysis")],
            "comparisons": s.records("comparison"),
            "causes": s.records("cause"),
            "verdicts": s.records("verdict"),
        },
        "design_summary": lambda: design_summary(s.design()) if ctx.version else None,
        "design": lambda: s.design() if ctx.version else None,
        "design_variables": lambda: design_variables(ctx),
        "archetypes": lambda: archetypes(ctx),
    }
    for name in wanted:
        out[name] = parts[name]()
    return out


def list_formulas(ctx: RunContext, args: dict[str, Any] | None = None, agent: str | None = None) -> list[Record]:
    """K0 formulas, optionally only those a tool lists for its predictions (else those whose
    id starts with the tool's name) or those of one domain."""
    args = args or {}
    out = ctx.formulas.list_formulas()
    tool = args.get("tool")
    if tool:
        named = (ctx.specs.get(tool) or {}).get("formulas") or []
        out = [f for f in out if f["formula_id"] in named or f["formula_id"].split(".", 1)[0] == tool]
    if args.get("domain"):
        out = [f for f in out if f["domain"] == args["domain"]]
    return out


def _tool_line(spec: Record) -> Record:
    checks = spec.get("checks") or []
    return {**{k: spec[k] for k in ("name", "title", "domain", "base", "tests", "usable") if k in spec},
            "checks_passed": f"{sum(1 for c in checks if c.get('pass'))}/{len(checks)}"}


def list_tools(ctx: RunContext, args: dict[str, Any] | None = None, agent: str | None = None) -> list[Record]:
    """The checked tool shelf, one line per tool (usable=false tools cannot be run); with
    `tool`, that tool's whole spec (its known-answer checks and formulas)."""
    args = args or {}
    if args.get("tool"):
        spec = ctx.specs.get(args["tool"])
        if spec is None:
            raise ToolError(f"道具 {args['tool']!r} は棚にありません ({', '.join(ctx.specs)})")
        return [spec]
    out = list(ctx.specs.values())
    if args.get("domain"):
        out = [s for s in out if s["domain"] == args["domain"]]
    if args.get("test_id"):
        out = [s for s in out if args["test_id"] in s.get("tests", [])]
    return [_tool_line(s) for s in out]


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


_MESH_LOCK = threading.Lock()  # roles analyse side by side (the MCP calls run in threads)


def version_mesh(ctx: RunContext, version: int, agent: str) -> Record:
    """The shell mesh of a design version: the one already recorded, else made once from the
    design by hiraku.solvers.frame_mesh and recorded (mesh-NNNN.json, mesh.created)."""
    with _MESH_LOCK:
        have = ctx.store.records("mesh", version)
        if have:
            return have[-1]
        solvers = importlib.import_module("hiraku.solvers")

        if not hasattr(solvers, "frame_mesh"):  # an optional entry point (protocols.OPTIONAL_SOLVER_ENTRY_POINTS)
            raise ToolError("hiraku.solvers has no frame_mesh yet, so the shell mesh cannot be made")
        mesh = {**solvers.frame_mesh(ctx.store.design(version)), "mesh_id": ctx.store.next_id("mesh"),
                "design_version": version}
        return ctx.store.add_record("mesh", mesh, design_version=version, agent=agent)


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
    # The mesh is an input of the analysis: mesh.created comes before analysis.started (contract).
    mesh, mesh_error = None, None
    if getattr(ctx.tools[name], "needs_mesh", False):  # once per version, before the tool's first run on it
        try:
            mesh = version_mesh(ctx, v, agent)
        except Exception as e:  # a design the mesher cannot take is a failed analysis, as a tool's own error is
            mesh_error = f"the version's shell mesh could not be made ({type(e).__name__}: {e})"
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
    if mesh is not None:
        params["mesh"] = mesh
    spec = ctx.specs.get(name, {})
    t0 = time.perf_counter()
    if mesh_error:
        result = {"status": "failed", "error": mesh_error, "results": []}
    else:
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


def _pick_result(analysis: Record, quantity: str, criterion: Record | None = None) -> Record:
    """The analysis result of a quantity. Given at several places, the unfavourable one: for
    the criterion's quantity the largest under <= and <, the smallest under >= and >;
    otherwise the largest in size."""
    hits = [r for r in analysis["results"] if r["quantity"] == quantity]
    if not hits:
        have = ", ".join(sorted({r["quantity"] for r in analysis["results"]})) or "なし"
        raise ToolError(
            f"解析 {analysis['analysis_id']} の結果に {quantity!r} がありません (結果: {have})。"
            f"名前が違うだけなら result_quantity で解析の側の名前を指定してください"
        )
    if criterion is not None and criterion["quantity"] == quantity:
        if unfavourable_side(criterion["op"]) == "larger":
            return max(hits, key=lambda r: r["value"])
        return min(hits, key=lambda r: r["value"])
    return max(hits, key=lambda r: abs(r["value"]))


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
    lc = ctx.load_case(pred["load_case_id"])
    crit = lc.get("criterion")
    res = _pick_result(ana, args.get("result_quantity") or pred["quantity"], crit)
    if res["unit"] != pred["unit"]:
        raise ToolError(
            f"単位が違うので照合できません (予測 {pred['unit']}、解析 {res['unit']})。"
            f"K0 の式で解析と同じ単位 {res['unit']} の予測を記録し直してください"
        )
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


# ---------------------------------------------------------------------------- design revision


def _variables(ctx: RunContext, design: Record) -> list[Record] | None:
    """hiraku.designgen.variables(design), or None when there is no designgen or it did not build this design."""
    if ctx.designgen is None:
        return None
    try:
        return ctx.designgen.variables(design)
    except ValueError:
        return None


def design_variables(ctx: RunContext) -> list[Record] | None:
    """The variables the designer may change: name, unit, min, max, meaning and the current value."""
    if not ctx.version:
        return None
    found = _variables(ctx, ctx.store.design())
    if found is None:
        return None
    return [{**{k: s[k] for k in ("name", "unit", "min", "max", "meaning") if k in s},
             "value": {"value": s["value"], "unit": s["unit"]}} for s in found if s.get("revisable")]


def archetypes(ctx: RunContext) -> list[Record] | None:
    """The kinds of design hiraku.designgen can make for this product's flags (propose_design)."""
    if ctx.designgen is None or not hasattr(ctx.designgen, "archetypes"):
        return None
    return ctx.designgen.archetypes(ctx.project["flags"])


def propose_design(ctx: RunContext, args: dict[str, Any], agent: str | None = None) -> Record:
    """The designer's first version: an archetype, its choices and variables (the kit computes
    every coordinate; the requirements give the occupants, cargo and top speed). Then the load
    cases are built on it and the project gets them, with the picks and the roles
    (project.updated), and the new version gets pending tiles."""
    from hiraku.catalog import build_tests, load_catalog, project_fields, skipped_text

    _check_input("propose_design", args)
    agent = agent or "designer"
    if agent != "designer":
        raise ToolError("最初の版を作るのは設計役 (designer) です。まとめ役は設計役に、要件と型を伝えて頼んでください")
    if ctx.version:
        raise ToolError(f"設計の版 {ctx.version} がもうあります。変えるなら revise_design で次の版を作ってください")
    reason = args["reason"].strip()
    if not reason:
        raise ToolError("reason に、なぜこの型・選択肢・変数にしたかを書いてください")
    kinds = archetypes(ctx)
    if kinds is None:
        raise ToolError("設計を型から組む関数 (hiraku.designgen.propose) が無いので、最初の版は作れません")
    names = [k["name"] for k in kinds]
    if args["archetype"] not in names:
        raise ToolError(f"型 {args['archetype']!r} はありません。この製品に使えるのは {', '.join(names) or 'なし'} です "
                        f"(get_context の archetypes)")
    req = ctx.requirements_file
    try:
        design = dict(ctx.designgen.propose(args["archetype"], dict(args.get("choices") or {}),
                                            dict(args.get("variables") or {}), req))
    except ValueError as e:
        raise ToolError(f"この型・選択肢・変数では設計を組めません: {e}。直して propose_design を呼び直してください") from None
    design.update(design_version=1, parent_version=None, change_reason=reason)
    errs = contract.errors("design", design)
    if errs:
        raise ToolError("組んだ設計が契約の形に合いません (hiraku.designgen の不具合): " + "; ".join(errs)[:2000])
    catalog = load_catalog()
    try:
        load_cases, skipped = build_tests(catalog, req, design)
    except ValueError as e:
        raise ToolError(f"この設計では試験の荷重条件を組めません: {e}") from None
    ctx.store.write_design(design, agent=agent)
    fields = project_fields(catalog, req, load_cases)
    ctx.store.update_project({k: fields[k] for k in ("domain_picks", "load_cases", "agents")}, agent=agent,
                             text=f"版 1 の試験: {'、'.join(lc['load_case_id'] for lc in load_cases)}")
    if skipped:
        ctx.store.emit("agent.said", agent="system", text=skipped_text(catalog, skipped))
    init_judgements(ctx)
    return {
        "design_version": 1,
        "archetype": args["archetype"],
        "change_reason": reason,
        "load_cases": [{"load_case_id": lc["load_case_id"], "owner": ctx.owner(lc["load_case_id"])} for lc in load_cases],
        "domain_picks": [p["domain"] for p in ctx.project["domain_picks"]],
    }


def revise_design(ctx: RunContext, args: dict[str, Any], agent: str | None = None) -> Record:
    """The designer changes design variables; hiraku.designgen builds the next version from them.

    Checks: only the designer; every variable is a revisable one of the
    design (hiraku.designgen.variables), keeps its unit and stays in its
    range; a reason; the number of revisions stays within round_limit and the
    cost within cost_limit_usd. hiraku.designgen.regenerate rebuilds the
    design; then design.created (parent_version, change_reason) and pending
    tiles for the new version, whose load cases are all redone.
    """
    _check_input("revise_design", args)
    agent = agent or "designer"
    if agent != "designer":
        raise ToolError("設計を直せるのは設計役 (designer) だけです。まとめ役から設計役に、どの試験をなぜ直すかを伝えてください")
    reason = args["reason"].strip()
    if not reason:
        raise ToolError("reason に、どの試験が通らず、変更がそれにどう効くかを書いてください")
    p, v = ctx.project, ctx.version
    if v - 1 >= p["round_limit"]:
        raise ToolError(f"設計の直しは round_limit ({p['round_limit']} 回) まで使い切りました (今の版は {v})。"
                        f"直さずに、今の版の判定で終えてください")
    if p["cost_usd"] >= p["cost_limit_usd"]:
        raise ToolError(f"費用の上限 {p['cost_limit_usd']} USD に達しているので、設計は直せません")
    design = ctx.store.design()
    found = _variables(ctx, design)
    if found is None:
        raise ToolError("この設計を変数から組み直す関数 (hiraku.designgen) が無いので、設計は直せません")
    spec = {s["name"]: s for s in found}
    revisable = [n for n, s in spec.items() if s.get("revisable")]
    problems = []
    for name, q in args["changes"].items():
        s = spec.get(name)
        if s is None or not s.get("revisable"):
            what = "ありません" if s is None else "直せない変数です"
            problems.append(f"変数 {name!r} は{what} (直せるのは {', '.join(revisable)})")
        elif q["unit"] != s["unit"]:
            problems.append(f"{name} の単位は {s['unit']} です ({q['unit']} ではなく)")
        elif "min" in s and not s["min"] <= q["value"] <= s["max"]:
            problems.append(f"{name} は {s['min']:g}〜{s['max']:g} {s['unit']} の範囲で選んでください ({q['value']:g} は外)")
    if problems:
        raise ToolError("設計の変数の指定が合いません: " + "; ".join(problems))
    changes = {k: {"value": float(q["value"]), "unit": q["unit"]} for k, q in args["changes"].items()}
    before = {k: {"value": spec[k]["value"], "unit": spec[k]["unit"]} for k in changes}
    try:
        new = dict(ctx.designgen.regenerate(copy.deepcopy(design), changes))
    except ValueError as e:
        raise ToolError(f"この変数では設計を組めません: {e}。値を変えて revise_design を呼び直してください") from None
    new.update(design_version=v + 1, parent_version=v, change_reason=reason)
    errs = contract.errors("design", new)
    if errs:
        raise ToolError("組み直した設計が契約の形に合いません (hiraku.designgen の不具合): " + "; ".join(errs)[:2000])
    ctx.store.write_design(new, agent=agent)
    init_judgements(ctx, v + 1)
    return {
        "design_version": v + 1,
        "parent_version": v,
        "change_reason": reason,
        "changes": [{"variable": k, "from": before.get(k), "to": q} for k, q in changes.items()],
        "redo": [lc["load_case_id"] for lc in ctx.load_cases],
        "revisions_left": p["round_limit"] - v,
    }


# ---------------------------------------------------------------------------- the report (N17)


def write_report(ctx: RunContext, args: dict[str, Any], agent: str | None = None) -> Record:
    """The lead's report: the summary and the next changes are its words; everything else is put
    together by code from the records (hiraku.tools.report). A number with a unit in the words
    that matches no recorded value of that unit is refused. Writes report.json, report.written."""
    from hiraku.tools.report import build_report

    _check_input("write_report", args)
    agent = agent or "lead"
    if agent != "lead":
        raise ToolError("報告を書くのはまとめ役 (lead) です。役の結果はまとめ役に返してください")
    if not args["summary"].strip():
        raise ToolError("summary に、分野ごとの結果を記録の ID と一緒に短く書いてください")
    return ctx.store.write_report(build_report(ctx, args, agent), agent=agent)


# ---------------------------------------------------------------------------- tools made in the run (N13)

def _check_code_input(name: str, args: dict[str, Any]) -> str:
    _check_input(name, args)
    if not args["code"].strip():
        raise ToolError(f"{name} の code が空です。道具の Python の文を書いてください")
    return args["code"]


def _made_answer(made: Any) -> Record:
    return {"name": made.name, "usable": made.usable, "checks": made.checks, "problems": made.problems,
            "printed": made.printed[-2000:]}


def check_tool(ctx: RunContext, args: dict[str, Any], agent: str | None = None) -> Record:
    """Check a tool's code on its own known answers in a separate process, without shelving it."""
    from hiraku.solvers import toolmaker

    code = _check_code_input("check_tool", args)
    return _made_answer(toolmaker.make_tool(code, taken=tuple(ctx.tools)))


def make_tool(ctx: RunContext, args: dict[str, Any], agent: str | None = None) -> Record:
    """Check a tool's code like check_tool; every checked attempt is recorded (tool.checked, and
    the code under <run>/tools/), and a tool that passes all its known answers goes on the shelf
    (origin claude), where analyze can run it like a built-in tool."""
    from hiraku.solvers import toolmaker

    code = _check_code_input("make_tool", args)
    agent = agent or "lead"
    if agent == "designer":
        raise ToolError("道具を作るのは CAE の役 (とまとめ役) です。設計役は設計の変数を変えます")
    if ctx.made_tools is None:
        ctx.made_tools = toolmaker.RunShelf(ctx.store.path)
    made = ctx.made_tools.make(code, taken=tuple(ctx.tools), reason=args.get("reason"))
    spec = made.spec
    if spec is not None and not contract.errors("tool_spec", spec):
        ok = "使えます" if made.usable else "使えません (" + "; ".join(made.problems)[:300] + ")"
        ctx.store.emit("tool.checked", agent=agent, domain=spec["domain"], text=f"道具 {spec['name']} を作って確かめました: {ok}",
                       data=spec)
    if made.usable:
        ctx.tools[made.name] = made.tool
        ctx.specs[made.name] = spec
    return _made_answer(made)


FUNCTIONS = {
    "get_context": get_context,
    "list_formulas": list_formulas,
    "list_tools": list_tools,
    "record_prediction": record_prediction,
    "analyze": analyze,
    "compare": compare,
    "record_cause": record_cause,
    "revise_design": revise_design,
    "propose_design": propose_design,
    "make_tool": make_tool,
    "check_tool": check_tool,
    "write_report": write_report,
}

def rule_check(ctx: RunContext, args: dict[str, Any] | None = None, agent: str | None = None) -> Record:
    """The intake's rule_check (hiraku.intake.tools.TOOLS), then the assembly tile again: a system
    rule that hits a part turns it red (judge.system_misses)."""
    from hiraku.intake.tools import TOOLS

    out = TOOLS["rule_check"]["function"](ctx, args or {}, agent)
    from hiraku.tools.judge import SYSTEM_DOMAIN

    if SYSTEM_DOMAIN in ctx.picked:
        update_judgement(ctx, SYSTEM_DOMAIN, out.get("design_version"))
    return out


# The intake department's MCP tools; their functions are in hiraku.intake.tools.TOOLS (mcp.py registers them).
INTAKE_TOOLS = ("pick_domains", "ask_user", "import_knowledge", "rule_check")
# The MCP tools of a run: the contract's MVP set, the designer's (revise_design, propose_design), tool
# making, the report and the intake tools.
RUN_TOOLS = (*contract.MCP_TOOLS_MVP, *contract.MCP_TOOLS_DESIGNER, "propose_design", "make_tool", "check_tool",
             "write_report", *INTAKE_TOOLS)
