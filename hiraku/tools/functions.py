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
import re
import threading
import time
from datetime import datetime
from typing import Any

from hiraku import contract
from hiraku.tools import rules
from hiraku.tools.context import RunContext, ToolError
from hiraku.tools.shelf import NOT_CHECKED, NOT_YET, check_shelf, load_case_stage, tool_stage, unchecked_tools
from hiraku.tools.judge import (OP_JA, failing_end, init_judgements, judged_quantity, make_verdict,
                                prediction_is_unfavourable, unfavourable_side, update_judgement)
from hiraku.tools.summary import design_summary

Record = dict[str, Any]


def _check_input(name: str, args: dict[str, Any]) -> None:
    errs = contract.errors(f"tool_input.{name}", args)
    if errs:
        raise ToolError(f"{name} の入力の形が合いません: " + "; ".join(errs))


def _gate(ctx: RunContext, name: str, args: dict[str, Any], agent: str | None = None) -> None:
    """HK1-HK3 again inside the tool. A call that gets here was not denied by a PreToolUse hook
    (that hook records its own denial and the call never runs), so a denial here is recorded
    once, as hook.denied, the same way: a direct call, a hook that did not run or answered
    allow on an older state (the plugin's hook command is a separate process)."""
    denial = rules.check_call(ctx, name, args)
    if denial:
        ctx.store.emit("hook.denied", agent=agent or "lead", domain=denial.domain, text=denial.reason,
                       data={"hook": "PreToolUse", "tool": contract.mcp_name(name), "rule": denial.rule,
                             "tool_input": args, "gate": "tool"})
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
                    "design_variables", "archetypes", "joint_wall_inputs", "formula_inputs")
DEFAULT_CONTEXT_SECTIONS = ()  # no sections asked: context_overview (counts and ids), every section by name
JOINT_WALL_FORMULA = "frame_static.joint_wall_bending"


def joint_wall_inputs(ctx: RunContext, load_case_id: str | None) -> Record:
    """The inputs of frame_static.joint_wall_bending for a step load case, worked out by code from the
    current version's design and the load case (the analysis builders' joint_wall_calc, the reader the
    scripted plans use too), and how to record the prediction with them."""
    from hiraku.catalog.builders.analysis import joint_wall_calc

    if not load_case_id:
        raise ToolError("joint_wall_inputs には load_case_id が要ります (段差の荷重条件の ID。get_context の load_cases)")
    if not ctx.version:
        raise ToolError("まだ設計の版がありません")
    lc = ctx.load_case(load_case_id)
    try:
        inputs = joint_wall_calc(ctx.store.design(), lc)  # the stored design: the loading goes on board inside
    except (ValueError, KeyError) as e:  # BuildError (a ValueError) says why: not a ladder frame, no step ...
        raise ToolError(f"{load_case_id} の {JOINT_WALL_FORMULA} の入力を設計から出せません: {e}") from None
    return {
        "load_case_id": load_case_id,
        "formula_id": JOINT_WALL_FORMULA,
        "inputs": inputs,
        "how": (f"つなぎ目の壁の曲げ (シェルのホットスポット) の予測: record_prediction(load_case_id=\"{load_case_id}\", "
                f"quantity=\"max_hot_spot_stress\", formula_id=\"{JOINT_WALL_FORMULA}\", output=\"sigma\", "
                f"inputs=この inputs をそのまま, assumptions=...)。値はコードが設計と荷重条件 (載せる組・段差・段に乗る輪) "
                f"から出したもので、横の部材の端のモーメントが最も大きいつなぎ目のもの"),
    }


def _design_section(ctx: RunContext, args: Record) -> Record | None:
    if not ctx.version:
        return None
    design = ctx.store.design()
    if args.get("design_part"):
        return design_page(design, args["design_part"], int(args.get("offset") or 0),
                           int(args["limit"]) if args.get("limit") is not None else None)
    return design_overview(design)


FORMULA_INPUTS_HOW = (
    "荷重条件ごとに、計画の式 (判定の量 criterion と途中の量 intermediate) の入力を、台本の運転と同じコードが"
    "この版の設計と荷重条件と記録から出した値・単位・出どころ (from) で並べたもの。record_prediction の inputs には"
    "この値をそのまま入れ、式の出力は output で選ぶ。refused はこの設計に当てはまらない式 (理由つき)、missing は"
    "上流の記録が揃ってから値が出る入力、no_reader は入力を読むコードの無い式。自分で別の値を使うときは、"
    "その値と出どころを assumptions に書く")


def formula_inputs_section(ctx: RunContext, load_case_id: str | None = None) -> Record:
    """get_context's formula_inputs: hiraku.tools.plan.formula_inputs for one load case or all."""
    from hiraku.tools.plan import formula_inputs

    if not ctx.version:
        raise ToolError("まだ設計の版がありません。式の入力は設計の版ができてから出せます")
    return {"design_version": ctx.version, "how": FORMULA_INPUTS_HOW,
            "load_cases": formula_inputs(ctx, load_case_id, pairs=lambda tool: tool_predicts(ctx, tool))}


DESIGN_PARTS = ("nodes", "members", "parts", "supports", "variables", "sections", "materials")
DESIGN_PAGE = 20  # items per page of design_part when no limit is given


def _value_of(v: Any) -> Any:
    """A design variable as {value, unit} (its other fields are design_variables')."""
    return {k: v[k] for k in ("value", "unit") if k in v} if isinstance(v, dict) else v


def design_overview(design: Record) -> Record:
    """get_context's design without design_part: the version, the counts and ids of each part of the
    record, the variables' values, the sections and materials (what the formulas take), and how to page
    the rest. The whole record can pass Claude Code's limit on a tool's output."""
    lists = {k: design[k] for k in ("nodes", "members", "parts", "supports") if isinstance(design.get(k), list)}
    out: Record = {k: design[k] for k in ("design_version", "parent_version", "name", "change_reason") if k in design}
    out["counts"] = {k: len(v) for k, v in lists.items()}
    out["ids"] = {k: [x.get("id") for x in v if isinstance(x, dict)] for k, v in lists.items()}
    out["variables"] = {k: _value_of(v) for k, v in (design.get("variables") or {}).items()}
    for k in ("sections", "materials"):
        if k in design:
            out[k] = design[k]
    out["pages"] = (f"部品・部材・節・支持の中身は get_context(sections=[\"design\"], design_part=<{'|'.join(DESIGN_PARTS)}>, "
                    f"offset=0, limit={DESIGN_PAGE}) で少しずつ読む。手計算の入力は design_summary と formula_inputs にある")
    return out


def design_page(design: Record, part: str, offset: int = 0, limit: int | None = None) -> Record:
    """One page of a part of the design record (a list, or a dict's items in order)."""
    if part not in DESIGN_PARTS:
        raise ToolError(f"design_part は {', '.join(DESIGN_PARTS)} のどれかです: {part!r}")
    whole = design.get(part)
    if whole is None:
        raise ToolError(f"この設計には {part} がありません")
    items = list(whole.items()) if isinstance(whole, dict) else list(whole)
    limit = DESIGN_PAGE if limit is None else limit
    if offset < 0 or limit < 1:
        raise ToolError(f"offset は 0 以上、limit は 1 以上です (offset {offset}, limit {limit})")
    page = items[offset:offset + limit]
    out: Record = {"design_version": design.get("design_version"), "design_part": part, "total": len(items),
                   "offset": offset, "items": dict(page) if isinstance(whole, dict) else page}
    if offset + limit < len(items):
        out["next_offset"] = offset + limit
    return out


def design_changes(ctx: RunContext) -> Record | None:
    """What changed from the previous version (the version this one was revised from): the variables
    whose value or unit changed, old and new, and the designer's reason. None on version 0 and 1."""
    if ctx.version < 2:
        return None
    new = ctx.store.design()
    parent = new.get("parent_version") or ctx.version - 1
    old = ctx.store.design(parent)
    was, now = old.get("variables") or {}, new.get("variables") or {}
    changed = [{"variable": k, "old": _value_of(was.get(k)), "new": _value_of(now.get(k))}
               for k in [*now, *[k for k in was if k not in now]] if _value_of(was.get(k)) != _value_of(now.get(k))]
    return {"from_version": parent, "to_version": ctx.version, "changed": changed,
            "reason": new.get("change_reason"),
            "note": (f"版 {ctx.version} の値は前の版 {parent} から変わっている。前の版で読んだ値・予測を使い回さず、"
                     f"design_summary・formula_inputs をこの版でもう一度読む")}


RECORD_KINDS = ("prediction", "analysis", "comparison", "cause", "verdict")
# what each section holds, for the small default answer (no sections asked)
SECTION_HINTS = {
    "requirements": "要件の全部 (requirement_keys で絞れる)",
    "load_cases": "荷重条件の中身: params・criterion・tolerance_rel・feeds・担当の役 (domain か load_case_id で絞れる)",
    "judgements": "分野ごとの判定の札",
    "records": "予測・解析・照合・原因・合否の記録 (record_kinds と load_case_id で絞れる)",
    "design_summary": "手計算の入力になる設計の値",
    "design": "設計の要約 (design_part・offset・limit で中身を少しずつ)",
    "design_variables": "設計役が変えられる変数 (範囲と今の値)",
    "archetypes": "設計の型 (archetype で 1 つに絞れる)",
    "joint_wall_inputs": "つなぎ目の壁の曲げの式の入力 (load_case_id が要る)",
    "formula_inputs": "荷重条件ごとの式の入力 (load_case_id か domain で絞れる)",
}


def _load_cases_wanted(ctx: RunContext, args: Record) -> list[Record]:
    """The run's load cases, only the domain's or the one load case when args name them."""
    out = list(ctx.load_cases)
    if args.get("load_case_id"):
        out = [ctx.load_case(args["load_case_id"])]
    if args.get("domain"):
        out = [lc for lc in out if lc["domain"] == args["domain"]]
    return out


def _records_section(ctx: RunContext, args: Record) -> Record:
    """The run's records, only the kinds in record_kinds and those of load_case_id when given (a
    comparison's and a cause's load case is their prediction's)."""
    s = ctx.store
    kinds = list(args.get("record_kinds") or RECORD_KINDS)
    bad = [k for k in kinds if k not in RECORD_KINDS]
    if bad:
        raise ToolError(f"record_kinds は {', '.join(RECORD_KINDS)} のどれかです: {', '.join(bad)}")
    lc_id = args.get("load_case_id")
    if lc_id:
        ctx.load_case(lc_id)  # an unknown id is refused with its sentence

    def of_case(rec: Record) -> bool:
        if not lc_id:
            return True
        if "load_case_id" in rec:
            return rec["load_case_id"] == lc_id
        cmp = rec if "prediction_id" in rec else s.get("comparison", rec.get("comparison_id", ""))
        pred = ctx.prediction((cmp or {}).get("prediction_id", ""))
        return bool(pred) and pred["load_case_id"] == lc_id

    shape = {"prediction": ("predictions", _compact_prediction), "analysis": ("analyses", _compact_analysis),
             "comparison": ("comparisons", None), "cause": ("causes", None), "verdict": ("verdicts", None)}
    out: Record = {}
    for k in kinds:
        key, fn = shape[k]
        out[key] = [fn(x) if fn else x for x in s.records(k) if of_case(x)]
    return out


def context_overview(ctx: RunContext) -> Record:
    """get_context's default answer: counts and ids of what each section holds, with the sections named.
    Every call goes into the caller's context; the sections are asked for by name."""
    s = ctx.store
    return {
        "requirements": {"count": len(ctx.project["requirements"])},
        "load_cases": [{"load_case_id": lc["load_case_id"], "domain": lc["domain"], "owner": ctx.owner(lc["load_case_id"]),
                        "stage": load_case_stage(ctx, lc), **({"feeds": lc["feeds"]} if lc.get("feeds") else {})}
                       for lc in ctx.load_cases],
        "judgements": {j["domain"]: j["state"] for j in s.judgements()} if ctx.version else {},
        "records": {k: len(s.records(k)) for k in RECORD_KINDS},
        "sections_available": dict(SECTION_HINTS),
    }


def get_context(ctx: RunContext, args: dict[str, Any] | None = None, agent: str | None = None) -> Record:
    """The run so far: always the run, design version, picked domains and open mismatches. Without
    `sections`, an overview (context_overview: counts and ids, the sections named); `sections` adds
    parts: requirements (requirement_keys filter), load_cases with the role that owns each (domain or
    load_case_id filter), judgements, records (record_kinds and load_case_id filters), design_summary
    (inputs for hand calculations), design (an overview of the record; with design_part, offset and
    limit one page of a part of it), design_variables (what the designer may change), archetypes
    (archetype filter), joint_wall_inputs (the joint wall formula's inputs for `load_case_id`) and
    formula_inputs (every planned formula's inputs worked out by code, for `load_case_id`, a domain's
    or all load cases)."""
    p = ctx.project
    s = ctx.store
    args = args or {}
    wanted = args.get("sections") or DEFAULT_CONTEXT_SECTIONS
    out: Record = {
        "run_id": p["run_id"],
        "want": p["want"],
        "product": p["product"],
        "design_version": ctx.version,
    }
    changes = design_changes(ctx)
    if changes is not None:  # first, so a role of a revised version sees what changed before anything else
        out["design_changes"] = changes
    out |= {
        "round_limit": p["round_limit"],
        "domain_picks": p["domain_picks"],
        "open_mismatches": [c["comparison_id"] for c in ctx.open_mismatches()],
        "sections": list(wanted),
    }
    if not wanted:
        out["overview"] = context_overview(ctx)
        return out
    if args.get("domain") and args["domain"] not in contract.DOMAINS:
        raise ToolError(f"domain は {', '.join(contract.DOMAINS)} のどれかです: {args['domain']!r}")

    def requirements() -> list[Record]:
        keys = args.get("requirement_keys")
        return [r for r in p["requirements"] if not keys or r["key"] in keys]

    def kinds() -> Any:
        got = archetypes(ctx)
        if got is not None and args.get("archetype"):
            got = [k for k in got if k.get("name") == args["archetype"]]
            if not got:
                raise ToolError(f"型 {args['archetype']!r} はありません (get_context の archetypes)")
        return got

    def formula_inputs() -> Record:
        if args.get("load_case_id") or not args.get("domain"):
            return formula_inputs_section(ctx, args.get("load_case_id"))
        whole = {"design_version": ctx.version, "how": FORMULA_INPUTS_HOW, "load_cases": []}
        for lc in _load_cases_wanted(ctx, args):
            whole["load_cases"] += formula_inputs_section(ctx, lc["load_case_id"])["load_cases"]
        return whole

    parts = {
        "requirements": requirements,
        "load_cases": lambda: [dict(lc, owner=ctx.owner(lc["load_case_id"]), stage=load_case_stage(ctx, lc))
                               for lc in _load_cases_wanted(ctx, args)],  # stage: HK5, lower stages first
        "judgements": lambda: s.judgements() if ctx.version else [],
        "records": lambda: _records_section(ctx, args),
        "design_summary": lambda: design_summary(s.design()) if ctx.version else None,
        "design": lambda: _design_section(ctx, args),
        "design_variables": lambda: design_variables(ctx),
        "archetypes": kinds,
        "joint_wall_inputs": lambda: joint_wall_inputs(ctx, args.get("load_case_id")),
        "formula_inputs": formula_inputs,
    }
    for name in wanted:
        out[name] = parts[name]()
    return out


PAIRS_HOW = ("predicts: 道具のどの結果 (quantity) を、どの式 (formula_id) のどの出力 (output) で予測するかの対。"
             "判定の量を予測するときは、その量と対になった式を選び、note を読む (ばねのある道具の横に並ぶ剛体の式は、"
             "初めの見積もりで、解析とは食い違う)")


def list_formulas(ctx: RunContext, args: dict[str, Any] | None = None, agent: str | None = None) -> list[Record] | Record:
    """K0 formulas, optionally only those of one domain. With `tool`: {"tool", "formulas" (those the
    tool lists for its predictions, else those whose id starts with its name), "predicts" (the
    tool's own pairing of its result quantities with formula outputs and their notes, from its
    describe(); [] when it has none), "how"}."""
    args = args or {}
    out = ctx.formulas.list_formulas()
    tool = args.get("tool")
    if tool:
        named = (ctx.specs.get(tool) or {}).get("formulas") or []
        out = [f for f in out if f["formula_id"] in named or f["formula_id"].split(".", 1)[0] == tool]
    if args.get("domain"):
        out = [f for f in out if f["domain"] == args["domain"]]
    if not tool:
        return out
    return {"tool": tool, "formulas": out, "predicts": tool_predicts(ctx, tool), "how": PAIRS_HOW}


# A tool's notes may name the Python function that reads a formula's inputs off the design; a role
# cannot call it, so what the roles read points at get_context's formula_inputs instead.
_CODE_CALL = re.compile(r"hiraku\.[a-z0-9_.]+\((?:[^()]|\([^()]*\))*\)")
CODE_CALL_FOR_ROLE = "get_context(sections=[\"formula_inputs\"], load_case_id=...) (the values code worked out from the design)"


def for_role(obj: Any) -> Any:
    """`obj` with every call of a hiraku Python function in its strings replaced by the
    get_context section that gives the same values (CODE_CALL_FOR_ROLE)."""
    if isinstance(obj, str):
        return _CODE_CALL.sub(CODE_CALL_FOR_ROLE, obj)
    if isinstance(obj, list):
        return [for_role(x) for x in obj]
    if isinstance(obj, dict):
        return {k: for_role(v) for k, v in obj.items()}
    return obj


def tool_predicts(ctx: RunContext, name: str) -> list[Record]:
    """The tool's describe()["predicts"]: {quantity, formula_id, output, note?} per pairing."""
    describe = getattr(ctx.tools.get(name), "describe", None)
    if not callable(describe):
        return []
    try:
        got = describe().get("predicts") or []
    except Exception:  # a tool made in the run may describe itself badly; the formulas still list
        return []
    keys = ("quantity", "formula_id", "output", "note")
    return [for_role({k: p[k] for k in keys if k in p}) for p in got if isinstance(p, dict)]


def _tool_line(spec: Record) -> Record:
    checks = spec.get("checks") or []
    return {**{k: spec[k] for k in ("name", "title", "domain", "base", "tests", "usable", "applies_to") if k in spec},
            "checks_passed": f"{sum(1 for c in checks if c.get('pass'))}/{len(checks)}"}


def design_kind(ctx: RunContext) -> str | None:
    """The kind of the version's design: the hiraku.designgen ARCHETYPES key of the generator that made it
    (designgen.generator_of); None for a design no generator made (one written by hand) or without designgen."""
    gen_of, kinds = getattr(ctx.designgen, "generator_of", None), getattr(ctx.designgen, "ARCHETYPES", None)
    if not ctx.version or gen_of is None or not kinds:
        return None
    try:
        gen = gen_of(ctx.store.design())
    except ValueError:  # DesignGenError: its variables match no generator
        return None
    return next((k for k, m in kinds.items() if m is gen), None)


def _check_applies_to(ctx: RunContext, name: str) -> None:
    """Contract tool_spec.applies_to: a tool that names kinds of design is used only on a design of one of them."""
    kinds = (ctx.specs.get(name) or {}).get("applies_to")
    if not kinds:
        return
    kind = design_kind(ctx)
    if kind in kinds:
        return
    what = f"この版の設計の型は {kind} です" if kind else "この版の設計は、型から作った設計ではありません (型が分からない)"
    raise ToolError(f"道具 {name} は、設計の型 {', '.join(kinds)} のための道具です。{what}。"
                    f"list_tools で、applies_to の無い道具か、この型を含む道具を選んでください")


def list_tools(ctx: RunContext, args: dict[str, Any] | None = None, agent: str | None = None) -> list[Record]:
    """The checked tool shelf, one line per tool (usable=false tools cannot be run); with
    `tool`, that tool's whole spec (its known-answer checks and formulas)."""
    args = args or {}
    lines = {n: _unchecked_line(n, ctx.tools[n], ctx.version) for n in unchecked_tools(ctx)}
    if args.get("tool"):
        spec = ctx.specs.get(args["tool"]) or lines.get(args["tool"])
        if spec is None:
            raise ToolError(f"道具 {args['tool']!r} は棚にありません ({', '.join([*ctx.specs, *lines])})")
        load, run = tool_params(ctx, args["tool"])
        whole = dict(spec)
        if load or run:
            whole.update(params=load, run_params=run, params_how=PARAMS_HOW)
        return [for_role(whole)]
    out = list(ctx.specs.values()) + list(lines.values())
    if args.get("domain"):
        out = [s for s in out if s.get("domain") == args["domain"]]
    if args.get("test_id"):
        out = [s for s in out if args["test_id"] in s.get("tests", [])]
    rows = []
    for s in out:
        row = s if "not_checked" in s else dict(_tool_line(s), stage=tool_stage(ctx, s["name"]))
        short = _params_short(*tool_params(ctx, s["name"]))
        rows.append(dict(row, params=short) if short else row)
    return rows


PARAMS_HOW = ("params は荷重条件の params で道具が読む名前 (無ければ default)。run_params は analyze の params に入れる設定。"
              "unit の量は {value, unit} で、choices があればその中から選ぶ")


def tool_params(ctx: RunContext, name: str) -> tuple[list[Record], list[Record]]:
    """The tool's describe() params and run_params ({name, kind, required, meaning, unit?, default?,
    choices?} each, from hiraku.solvers.shelf.base Param.describe); ([], []) when it does not describe itself."""
    describe = getattr(ctx.tools.get(name), "describe", None)
    if not callable(describe):
        return [], []
    try:
        d = describe()
    except Exception:  # a tool made in the run may describe itself badly
        return [], []
    pick = lambda xs: [dict(x) for x in xs or [] if isinstance(x, dict) and x.get("name")]  # noqa: E731
    return pick(d.get("params")), pick(d.get("run_params"))


def _params_short(load: list[Record], run: list[Record]) -> list[str]:
    """One string per param for list_tools' one-line entries: name, then [unit] or (choice|choice);
    analyze's run params marked run."""
    def one(p: Record, run_param: bool) -> str:
        tail = f" ({'|'.join(map(str, p['choices']))})" if p.get("choices") else (f" [{p['unit']}]" if p.get("unit") else "")
        return f"{p['name']}{tail}" + (" run" if run_param else "")
    return [one(p, False) for p in load] + [one(p, True) for p in run]


def _unchecked_line(name: str, tool: Any, version: int = 1) -> Record:
    """list_tools' line for a tool this run did not check (check_shelf): not usable, and why."""
    line: Record = {"name": name}
    for key in ("title", "domain", "base", "tests"):
        value = getattr(tool, key, None)
        if value is not None:
            line[key] = list(value) if key == "tests" else value
    return {**line, "usable": False, "checks_passed": "0/0", "not_checked": NOT_CHECKED if version else NOT_YET}


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


FRAME_MESHER = "frame_mesh"  # the version's shell mesh of hiraku.solvers.frame_mesh; a mesh without mesher is one


def version_mesh(ctx: RunContext, version: int, agent: str, tool: Any = None) -> Record:
    """The mesh of a design version for a tool (docs/contract.md: one per version and mesher): the one
    already recorded by that mesher, else made once and recorded (mesh-NNNN.json, mesh.created). The
    mesher is the tool's own make_mesh(design) when it has one (mesher = the tool's name), else
    hiraku.solvers.frame_mesh."""
    own = getattr(tool, "make_mesh", None)
    mesher = getattr(tool, "name", None) if own is not None else FRAME_MESHER
    with _MESH_LOCK:
        have = [m for m in ctx.store.records("mesh", version) if m.get("mesher", FRAME_MESHER) == mesher]
        if have:
            return have[-1]
        design = ctx.store.design(version)
        if own is not None:
            made = own(design)
        else:
            solvers = importlib.import_module("hiraku.solvers")
            if not hasattr(solvers, "frame_mesh"):  # an optional entry point (protocols.OPTIONAL_SOLVER_ENTRY_POINTS)
                raise ToolError("hiraku.solvers has no frame_mesh yet, so the shell mesh cannot be made")
            made = solvers.frame_mesh(design)
        mesh = {**made, "mesher": mesher, "mesh_id": ctx.store.next_id("mesh"), "design_version": version}
        return ctx.store.add_record("mesh", mesh, design_version=version, agent=agent)


def analyze(ctx: RunContext, args: dict[str, Any], agent: str | None = None) -> Record:
    """Run one analysis tool from the shelf on the current design and record it."""
    _check_input("analyze", args)
    name = args["tool"]
    if name not in ctx.tools:
        raise ToolError(f"道具 {name!r} は棚にありません。list_tools で道具を選んでください ({', '.join(ctx.tools)})")
    lc = ctx.load_case(args["load_case_id"])
    _check_applies_to(ctx, name)
    _gate(ctx, "analyze", args, agent)

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
            mesh = version_mesh(ctx, v, agent, ctx.tools[name])
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
    out = ctx.store.add_record("analysis", rec, design_version=v, agent=agent, domain=lc["domain"])
    if out["status"] == "ok" and lc["domain"] in ctx.picked:  # a result beyond the criterion holds the tile back
        update_judgement(ctx, lc["domain"], v)
    return out


# ---------------------------------------------------------------------------- comparison


def _pick_result(analysis: Record, quantity: str, criterion: Record | None = None,
                 predicted_quantity: str | None = None, where: str | None = None) -> Record:
    """The analysis result of a quantity. Given at several places, the unfavourable one: when the
    comparison is for the criterion (the criterion's quantity is this result's name or the
    prediction's quantity, predicted_quantity, as when result_quantity names the result) the
    largest under <= and <, the smallest under >= and >; otherwise the largest in size.
    With `where`, only the results at that place (a member, node, support or part id)."""
    hits = [r for r in analysis["results"] if r["quantity"] == quantity]
    if not hits:
        have = ", ".join(sorted({r["quantity"] for r in analysis["results"]})) or "なし"
        raise ToolError(
            f"解析 {analysis['analysis_id']} の結果に {quantity!r} がありません (結果: {have})。"
            f"名前が違うだけなら result_quantity で解析の側の名前を指定してください"
        )
    if where is not None:
        at = [r for r in hits if r.get("where") == where]
        if not at:
            places = ", ".join(sorted({r["where"] for r in hits if r.get("where")})) or "なし (場所の無い結果だけ)"
            raise ToolError(f"解析 {analysis['analysis_id']} の {quantity} に、場所 {where!r} の結果はありません "
                            f"(場所: {places})。where を外すと、全部の場所のうち不利な方と照合します")
        hits = at
    if criterion is not None and criterion["quantity"] in (quantity, predicted_quantity):
        if unfavourable_side(criterion["op"]) == "larger":
            return max(hits, key=lambda r: r["value"])
        return min(hits, key=lambda r: r["value"])
    return max(hits, key=lambda r: abs(r["value"]))


def _resolve_causes(ctx: RunContext, cmp: Record, pred: Record, agent: str) -> None:
    """A later match for the same load case and quantity resolves earlier causes (resolved_by is
    always a match; a safe-side verdict settles them for the tile instead, judge.unresolved_mismatches)."""
    for cause in ctx.store.records("cause"):
        if cause["resolved"]:
            continue
        old = ctx.store.get("comparison", cause["comparison_id"])
        old_pred = ctx.prediction(old["prediction_id"]) if old else None
        if old_pred and old_pred["load_case_id"] == pred["load_case_id"] and old["quantity"] == cmp["quantity"]:
            v, _ = ctx.store.find("cause", cause["cause_id"])
            ctx.store.add_record("cause", dict(cause, resolved=True, resolved_by=cmp["comparison_id"]),
                                 design_version=v, agent=agent, domain=pred["domain"])


def revised_after_cause(ctx: RunContext, pred: Record, ana: Record) -> bool:
    """A prediction of the analysis's version and load case, recorded after a cause was written for a
    mismatch on that analysis: the agreed way to fix a hand calculation (cause, new formula, compare
    again on the same analysis). qa/check_run.py tolerates the same."""
    if pred["design_version"] != ana["design_version"] or pred["load_case_id"] != ana["load_case_id"]:
        return False
    on_it = {c["comparison_id"] for c in ctx.store.records("comparison")
             if c["analysis_id"] == ana["analysis_id"] and c["status"] == "mismatch"}
    causes = {c["cause_id"] for c in ctx.store.records("cause") if c["comparison_id"] in on_it}
    if not causes:
        return False
    first_cause, pred_seq = None, None
    for e in ctx.store.events():
        data = e.get("data") or {}
        if e["kind"] == "cause.recorded" and data.get("cause_id") in causes and first_cause is None:
            first_cause = e["seq"]
        elif e["kind"] == "prediction.recorded" and data.get("prediction_id") == pred["prediction_id"]:
            pred_seq = e["seq"]
    return first_cause is not None and pred_seq is not None and first_cause < pred_seq


def compare(ctx: RunContext, args: dict[str, Any], agent: str | None = None) -> Record:
    """Compare a prediction with an analysis result. A match against a load case
    with a criterion also makes the verdict and updates the domain's judgement.

    diff_rel = (analysed - predicted) / |predicted|. A prediction of 0 (zero predictions can be
    legitimate, e.g. an interference of 0 mm) divides by |analysed| instead (0 when both are 0),
    which the contract does not define."""
    where = args.get("where")
    if where is not None and not (isinstance(where, str) and where):
        raise ToolError(f"compare の where は部材か部品の ID (文字列) です: {where!r}")
    # tool_input.compare has no `where` (it goes in the comparison record, contract comparison.where)
    _check_input("compare", {k: v for k, v in args.items() if k != "where"})
    pred = ctx.prediction(args["prediction_id"])
    if pred is None:
        raise ToolError(f"予測 {args['prediction_id']!r} はありません (get_context の records.predictions)")
    hit = ctx.store.find("analysis", args["analysis_id"])
    if hit is None:
        raise ToolError(f"解析 {args['analysis_id']!r} はありません (get_context の records.analyses)")
    v, ana = hit
    _gate(ctx, "compare", args, agent)
    if ana["status"] != "ok":
        raise ToolError(f"解析 {ana['analysis_id']} は失敗しています: {ana.get('error', '')}。照合できません")
    if pred["prediction_id"] not in ana["prediction_ids"] and not revised_after_cause(ctx, pred, ana):
        same = pred["design_version"] == ana["design_version"] and pred["load_case_id"] == ana["load_case_id"]
        how = ("この解析の照合の食い違いに record_cause を書いたあとに記録した予測なら、この解析と照合できます。"
               if same else f"この予測は {pred['load_case_id']} (版 {pred['design_version']}) のものです。")
        raise ToolError(
            f"解析 {ana['analysis_id']} は予測 {', '.join(ana['prediction_ids']) or 'なし'} に対して走らせたもので、"
            f"{pred['prediction_id']} は入っていません。{how}そうでなければ、record_prediction で予測を記録してから、"
            f"analyze(tool=\"{ana['tool']}\", load_case_id=\"{pred['load_case_id']}\", "
            f"prediction_ids=[\"{pred['prediction_id']}\"]) で解析し直し、その analysis_id と照合してください"
        )
    lc = ctx.load_case(pred["load_case_id"])
    crit = lc.get("criterion")
    res = _pick_result(ana, args.get("result_quantity") or pred["quantity"], crit, pred["quantity"], where)
    # the result a verdict may come from (docs/contract.md 判定に使う量: frame_shell judges max_stress on
    # max_hot_spot_stress, never on its element max_stress)
    judged = judged_quantity(ctx, ana["tool"], crit["quantity"]) if crit else None
    for_verdict = bool(crit) and (res["quantity"] == judged
                                  or (judged == crit["quantity"] and pred["quantity"] == crit["quantity"]))
    if for_verdict and where is not None:  # a verdict only on the failing end over all places, never on one chosen
        worst = failing_end([r for r in ana["results"] if r["quantity"] == res["quantity"]
                             and r["unit"] == res["unit"]], crit["op"])
        for_verdict = worst["value"] == res["value"]
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
        "quantity": res["quantity"],  # the result actually compared (result_quantity when given)
        "predicted": {"value": predicted, "unit": pred["unit"]},
        "analyzed": {"value": analyzed, "unit": res["unit"]},
        "diff_abs": diff_abs,
        "diff_rel": diff_rel,
        "tolerance_rel": tol,
        "status": "match" if abs(diff_rel) <= tol else "mismatch",
        "cause_id": None,
        **({"where": where} if where else {}),  # contract comparison.where: the place the result was taken
    }
    ctx.store.add_record("comparison", cmp, design_version=v, agent=agent, domain=lc["domain"])
    out: Record = {"comparison": cmp, "verdict": None}
    if where is not None:
        out["where"] = where
        if not for_verdict and crit and res["quantity"] == judged:
            out["note"] = (f"{where} の値は、全部の場所のうち不合格に近い方の値ではないので、この照合からは合否を作りません。"
                           f"合否は where を外した照合 (全部の場所の不利な方) で作ります")
    if cmp["status"] == "match":
        _resolve_causes(ctx, cmp, pred, agent)
        if for_verdict and crit["limit"]["unit"] == res["unit"]:
            ver = make_verdict(ctx, ctx.store.next_id("verdict"), cmp, lc)
            ctx.store.add_record("verdict", ver, design_version=v, agent=agent, domain=lc["domain"])
            out["verdict"] = ver
    if lc["domain"] in ctx.picked:  # a mismatch holds the domain back from green too
        update_judgement(ctx, lc["domain"], v)
    return out


def _check_safe_side(ctx: RunContext, cmp: Record, lc: Record | None, tool: str | None = None,
                     pred_quantity: str | None = None) -> None:
    """Refuse safe_side unless the load case has a criterion on this quantity and the
    prediction is the unfavourable value (larger for <=, smaller for >=). The quantity is the
    compared result's (cmp["quantity"]): the criterion's own, the one the tool is judged on
    (judge.judged_quantity), or a result named by result_quantity for a prediction of the
    criterion's quantity."""
    crit = (lc or {}).get("criterion")
    judged = judged_quantity(ctx, tool, crit["quantity"]) if crit else None
    on_criterion = bool(crit) and (cmp["quantity"] == judged
                                   or (judged == crit["quantity"] and pred_quantity == crit["quantity"]))
    if not crit or not on_criterion or crit["limit"]["unit"] != cmp["predicted"]["unit"]:
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
        ana = ctx.store.get("analysis", cmp["analysis_id"]) or {}
        _check_safe_side(ctx, cmp, lc, ana.get("tool"), pred["quantity"] if pred else None)
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


def requirements_now(ctx: RunContext) -> Record:
    """The requirements file as the run has it now: its requirement values are the project's
    (project.requirements, which the hearing's answers update through ask_user), its other keys
    (tests, beyond, default_loading ...) the file's. Without the file (a run opened again), the
    project's want, product and flags."""
    base = ctx.requirements_file or {k: ctx.project[k] for k in ("want", "product", "flags") if k in ctx.project}
    if ctx.project.get("requirements"):
        return dict(base, requirements=list(ctx.project["requirements"]))
    return base


def claude_picks(ctx: RunContext) -> list[Record] | None:
    """The picks of the last domains.picked (pick_domains: Claude's domains, names, reasons and tests),
    or None when the run has none."""
    for e in reversed(ctx.store.events()):
        if e["kind"] == "domains.picked":
            picks = (e.get("data") or {}).get("picks")
            return [dict(p) for p in picks] if isinstance(picks, list) else None
    return None


def _has_builder(catalog: Any, test_id: str) -> bool:
    """Whether the test has a load case builder: the intake department's has_builder when there is one,
    else hiraku.catalog.build.builder_for. An unknown test id has none."""
    try:
        from hiraku.intake import has_builder  # type: ignore[attr-defined]
    except ImportError:
        has_builder = None
    try:
        if has_builder is not None:
            try:
                return bool(has_builder(test_id))
            except TypeError:  # another signature than this guess: the catalog's own lookup below
                pass
        from hiraku.catalog.build import builder_for

        return builder_for(catalog, test_id) is not None
    except (KeyError, ValueError):
        return False


def _test_title(catalog: Any, test_id: str) -> str:
    try:
        return catalog.test(test_id)["title"]
    except (KeyError, ValueError):
        return ""


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
    req = requirements_now(ctx)
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
    claude = None if req.get("tests") else claude_picks(ctx)  # a file that lists its tests decides them
    no_builder: list[str] = []
    try:
        if claude is not None and any(p.get("test_ids") for p in claude):
            from hiraku.catalog.build import build_load_cases

            wanted = list(dict.fromkeys(t for p in claude for t in p.get("test_ids") or []))
            no_builder = [t for t in wanted if not _has_builder(catalog, t)]
            skipped = []
            load_cases = build_load_cases(catalog, req, design, [t for t in wanted if t not in no_builder], skipped,
                                          beyond=req.get("beyond", []))
            if not load_cases:
                raise ValueError("no test Claude picked could be built: "
                                 + "; ".join(f"{t}: {why}" for t, why in skipped) if skipped else
                                 f"none of {', '.join(wanted)} has a builder")
        else:
            load_cases, skipped = build_tests(catalog, req, design)
    except ValueError as e:
        raise ToolError(f"この設計では試験の荷重条件を組めません: {e}") from None
    ctx.store.write_design(design, agent=agent)
    fields = project_fields(catalog, req, load_cases)
    if claude is not None:  # Claude's picks (pick_domains) as Claude wrote them: domains, names, reasons, tests
        from hiraku.catalog import agents_for

        fields["domain_picks"] = [{k: p[k] for k in ("domain", "shown_name", "reason", "test_ids") if k in p}
                                  for p in claude]
        fields["agents"] = agents_for(catalog, load_cases, [p["domain"] for p in claude])
    ctx.store.update_project({k: fields[k] for k in ("domain_picks", "load_cases", "agents")}, agent=agent,
                             text=f"版 1 の試験: {'、'.join(lc['load_case_id'] for lc in load_cases)}")
    if no_builder:
        named = "、".join(f"{t} {_test_title(catalog, t)}" for t in no_builder)
        ctx.store.emit("agent.said", agent="system",
                       text=f"選んだ試験のうち {named} は、荷重条件の作り方 (builder) がまだ無いので、この実行では見ません")
    if skipped:
        ctx.store.emit("agent.said", agent="system", text=skipped_text(catalog, skipped))
    # the tools of version 1's tests that the start did not check (a run whose tests were not fixed then)
    check_shelf(ctx, list(dict.fromkeys(lc["test_id"] for lc in load_cases)), skip_checked=True)
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
    range; a reason; the number of revisions stays within round_limit; and the
    project's cost_usd is below cost_limit_usd. cost_usd is the cost as of the lead's
    last finished turn (written when a ResultMessage ends a turn, runner.MessageLog),
    so inside a turn this check sees the previous turn's cost; what stops a run in the
    middle of a turn is the SDK's max_budget_usd. hiraku.designgen.regenerate rebuilds the
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
    if p["cost_usd"] is not None and p["cost_usd"] >= p["cost_limit_usd"]:  # null: the plugin path, not known
        raise ToolError(f"まとめ役の最後に終わった番の時点で、費用 ({p['cost_usd']:.4g} USD) が上限 "
                        f"{p['cost_limit_usd']} USD に達しているので、設計は直せません")
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


def write_report(ctx: RunContext, args: dict[str, Any], agent: str | None = None, *,
                 computed: list[Record] | None = None) -> Record:
    """The lead's report: the summary and the next changes are its words; everything else is put
    together by code from the records (hiraku.tools.report). A number with a unit in the words
    that matches no recorded value of that unit is refused. Writes report.json, report.written.
    computed: sensitivities already computed (the scripted driver's next items)."""
    from hiraku.tools.report import build_report

    _check_input("write_report", args)
    agent = agent or "lead"
    if agent != "lead":
        raise ToolError("報告を書くのはまとめ役 (lead) です。役の結果はまとめ役に返してください")
    if not args["summary"].strip():
        raise ToolError("summary に、分野ごとの結果を記録の ID と一緒に短く書いてください")
    return ctx.store.write_report(build_report(ctx, args, agent, computed), agent=agent)


# ---------------------------------------------------------------------------- tools made in the run (N13)

def _check_code_input(name: str, args: dict[str, Any]) -> str:
    _check_input(name, args)
    if not args["code"].strip():
        raise ToolError(f"{name} の code が空です。道具の Python の文を書いてください")
    return args["code"]


def _made_answer(made: Any) -> Record:
    return {"name": made.name, "usable": made.usable, "checks": made.checks, "problems": made.problems,
            "printed": made.printed[-2000:]}


# Words by which a known answer's source names a reference outside the tool (lower case; HIRAKU's choice).
REFERENCE_WORDS = ("closed form", "closed-form", "textbook", "handbook", "standard", "by hand", "statics",
                   "閉じた式", "教科書", "便覧", "規格", "手計算", "jis ", "iso ", "iso-", "iiw", "roark", "eurocode")


def _names_reference(source: str, spec: Record) -> bool:
    """Whether a known answer's source names a reference, once the tool's own name and formula ids are taken
    out of it (the tool's own formula is not a source for its own answer)."""
    text = f" {source.lower()} "
    for own in [spec.get("name") or "", *(spec.get("formulas") or [])]:
        if own:
            text = text.replace(own.lower(), " ")
    return any(w in text for w in REFERENCE_WORDS)


def made_refusals(ctx: RunContext, made: Any) -> list[str]:
    """This department's rule on a made tool, after the toolmaker's checks (tool_template.RULE): the spec
    names formulas the run knows, and one known answer has a nonzero expected value whose source names a
    reference. The sentences say what to add. [] when the code gave no spec (the toolmaker said why)."""
    spec = made.spec if isinstance(made.spec, dict) else None
    if spec is None:
        return []
    out = []
    formulas = [f for f in spec.get("formulas") or [] if isinstance(f, str)]
    if not formulas:
        out.append("spec の formulas が空です。この道具の結果を予測する K0 の式の formula_id (list_formulas) を "
                   "formulas = [...] に書いてください。判定には手計算の予測が要ります")
    else:
        known = {f["formula_id"] for f in ctx.formulas.list_formulas()}
        unknown = [f for f in formulas if f not in known]
        if unknown:
            out.append(f"formulas の {', '.join(unknown)} は式の一覧にありません。list_formulas にある formula_id を書いてください")
    good = [c for c in made.checks if isinstance(c.get("expected"), (int, float)) and c["expected"] != 0
            and _names_reference(str(c.get("source") or ""), spec)]
    if not good:
        out.append("答えの分かっている例に、expected が 0 でなく、source が道具の外の出典 (閉じた式・教科書・規格、"
                   "例: \"closed form sigma = F L / 4 (h / 2) / I, worked out by hand\") を名指しするものがありません。"
                   "そういう例を known_answers に少なくとも 1 つ足してください (道具自身の式の値は出典になりません)")
    return out


def _apply_refusals(ctx: RunContext, made: Any) -> Any:
    refused = made_refusals(ctx, made)
    if refused:
        made.problems = list(made.problems) + refused
        made.usable = False
        made.tool = None
        if isinstance(made.spec, dict):
            made.spec = dict(made.spec, usable=False)
    return made


def get_tool_template(ctx: RunContext, args: dict[str, Any] | None = None, agent: str | None = None) -> Record:
    """One whole tool in the shape make_tool takes, and what its parts must be (hiraku.tools.tool_template)."""
    from hiraku.tools.tool_template import tool_template

    return tool_template()


def check_tool(ctx: RunContext, args: dict[str, Any], agent: str | None = None) -> Record:
    """Check a tool's code on its own known answers in a separate process, without shelving it; the answer
    has the API a made tool may use (tool_template.allowed_api, read from the toolmaker's allow list)."""
    from hiraku.solvers import toolmaker
    from hiraku.tools.tool_template import RULE, allowed_api

    code = _check_code_input("check_tool", args)
    out = _made_answer(_apply_refusals(ctx, toolmaker.make_tool(code, taken=tuple(ctx.tools))))
    return {**out, "rule": RULE, "api": allowed_api()}


UNNAMED_TOOL = "unnamed"
UNKNOWN_TOOL_TITLE = "形の分からない道具 (spec() が契約の tool_spec に合わない)"


def attempt_spec(made: Any, agent: str, picked: list[str]) -> Record:
    """The tool_spec to record for an attempt: its own when it fits the contract, else a minimal one
    that fits, usable false: the attempt's fields where they fit the contract, otherwise name
    UNNAMED_TOOL, title UNKNOWN_TOOL_TITLE, the caller's domain (cae.<domain>, else the run's first
    picked domain), base K0 and no tests (placeholders: the attempt did not say)."""
    spec = made.spec if isinstance(made.spec, dict) else {}
    if spec and not contract.errors("tool_spec", spec):
        return spec
    bases = contract.schema()["$defs"]["base"]["enum"]
    caller = agent.split(".", 1)[1] if agent.startswith("cae.") else None
    domain = next((d for d in (spec.get("domain"), caller, *picked) if d in contract.DOMAINS), contract.DOMAINS[0])
    name = spec.get("name") if isinstance(spec.get("name"), str) and re.fullmatch(r"[a-z0-9_]+", spec["name"]) else None
    checks = [c for c in made.checks if not contract.errors("tool_check", c)]
    return {
        "name": name or UNNAMED_TOOL,
        "title": spec["title"] if isinstance(spec.get("title"), str) else UNKNOWN_TOOL_TITLE,
        "domain": domain,
        "base": spec["base"] if spec.get("base") in bases else "K0",
        "tests": [],
        "origin": "claude",
        "usable": False,
        "checks": checks,
    }


def make_tool(ctx: RunContext, args: dict[str, Any], agent: str | None = None) -> Record:
    """Check a tool's code like check_tool; every attempt is recorded (tool.checked, and the code
    under <run>/tools/): its spec when it fits the contract's tool_spec, else a minimal one
    (attempt_spec, usable false). A tool that passes all its known answers and whose spec fits the
    contract goes on the shelf (origin claude), where analyze can run it like a built-in tool."""
    from hiraku.solvers import toolmaker

    code = _check_code_input("make_tool", args)
    agent = agent or "lead"
    if agent == "designer":
        raise ToolError("道具を作るのは CAE の役 (とまとめ役) です。設計役は設計の変数を変えます")
    if ctx.made_tools is None:
        ctx.made_tools = toolmaker.RunShelf(ctx.store.path)
    shelf = ctx.made_tools
    if callable(getattr(shelf, "add", None)):  # checked, then this department's rule, then kept (RunShelf.make's order)
        made = _apply_refusals(ctx, toolmaker.make_tool(code, taken=tuple(ctx.tools)))
        made.reason = args.get("reason")
        shelf.add(made)
    else:
        made = _apply_refusals(ctx, shelf.make(code, taken=tuple(ctx.tools), reason=args.get("reason")))
    spec = attempt_spec(made, agent, list(ctx.picked))
    usable = bool(made.usable) and spec is made.spec and spec["usable"]
    ok = "使えます" if usable else "使えません (" + ("; ".join(made.problems) or "spec() の形が契約に合いません")[:300] + ")"
    ctx.store.emit("tool.checked", agent=agent, domain=spec["domain"], text=f"道具 {spec['name']} を作って確かめました: {ok}",
                   data=spec)
    if usable:
        ctx.tools[made.name] = made.tool
        ctx.specs[made.name] = spec
    out = _made_answer(made)
    out["usable"] = usable
    return out


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
    "get_tool_template": get_tool_template,
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
# making (with its template), the report and the intake tools.
RUN_TOOLS = (*contract.MCP_TOOLS_MVP, *contract.MCP_TOOLS_DESIGNER, "propose_design", "make_tool", "check_tool",
             "get_tool_template", "write_report", *INTAKE_TOOLS)
