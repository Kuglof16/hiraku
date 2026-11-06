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
from hiraku.tools.judge import (HOT_SPOT, OP_JA, PATH_CHECKED, PATH_FORMULA, STAND_IN_BELOW, failing_end,
                                hot_spot_verdict, init_judgements, unpredicted_over,
                                judged_quantity, make_verdict, path_check, prediction_is_unfavourable, unfavourable_side, unsupported_load_verdict,
                                update_judgement)
from hiraku.tools.summary import design_summary

Record = dict[str, Any]


def _check_input(name: str, args: dict[str, Any]) -> None:
    errs = contract.errors(f"tool_input.{name}", args)
    if errs:
        raise ToolError(f"{name} の入力の形が合いません: " + "; ".join(errs))


def _gate(ctx: RunContext, name: str, args: dict[str, Any], agent: str | None = None) -> None:
    """HK1-HK3, HK5 and HK6 again inside the tool. A call that gets here was not denied by a PreToolUse hook
    (that hook records its own denial and the call never runs), so a denial here is recorded
    once, as hook.denied, the same way: a direct call, a hook that did not run or answered
    allow on an older state (the plugin's hook command is a separate process)."""
    denial = rules.check_call(ctx, name, args, agent)
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
    out = {k: a[k] for k in keys if k in a}
    results = out.get("results") or []
    if len(results) > RESULTS_SHOWN_MAX:
        out["results"] = _extreme_results(results)
        out["results_note"] = (f"結果 {len(results)} 件のうち、量ごとに大きい方と小さい方の {RESULTS_EACH_END} 件ずつ "
                               f"({len(out['results'])} 件)。照合 (compare) は全部の結果を読む")
    return out


# get_context's records list an analysis's results whole up to this many; beyond it (a shell's per-place results,
# run 094333: a designer's answer of 80 000 characters went over Claude Code's limit) each quantity's extremes
RESULTS_SHOWN_MAX = 24
RESULTS_EACH_END = 3


def _extreme_results(results: list[Record]) -> list[Record]:
    """Of each quantity's results, the RESULTS_EACH_END largest and smallest values, in their first order. A
    per-place result <q>.<place> counts as q (its places are what is trimmed)."""
    by: dict[str, list[int]] = {}
    for i, r in enumerate(results):
        by.setdefault(r["quantity"].partition(".")[0], []).append(i)
    keep: set[int] = set()
    for idx in by.values():
        ranked = sorted(idx, key=lambda i: results[i]["value"])
        keep.update(ranked[:RESULTS_EACH_END] + ranked[-RESULTS_EACH_END:])
    return [results[i] for i in sorted(keep)]


# get_context answers with the small parts unless asked for more: every call goes into the
# caller's context, so the design (large) and the designer's variables come only on request.
CONTEXT_SECTIONS = ("requirements", "load_cases", "judgements", "records", "design_summary", "design",
                    "design_variables", "design_choices", "archetypes", "joint_wall_inputs", "formula_inputs", "modules")
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
    "design_choices": "いくつかの変数をまとめて決める近道と選べる値 (revise_design の choices)",
    "archetypes": "設計の型 (archetype で 1 つに絞れる)",
    "joint_wall_inputs": "つなぎ目の壁の曲げの式の入力 (load_case_id が要る)",
    "formula_inputs": "荷重条件ごとの式の入力 (load_case_id か domain で絞れる)",
    "modules": "モジュールに分けた設計だけ: 自分のモジュールの変数・近道・空間・取付点と、その回の提案",
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
    # in reading(): a record file is parsed once and kept while its stat stays the same (a long run's 1468 records
    # took 1.3 s on every call without it); the compact analyses use none of the fields reading() leaves out, and
    # the other kinds are only copied out (json.dumps by the tool's wrapper)
    with s.reading():
        for k in kinds:
            key, fn = shape[k]
            out[key] = [fn(x) if fn else dict(x) for x in s.records(k) if of_case(x)]
    return out


def _record_counts(s: Any) -> Record:
    """How many records of each kind the run has (read in reading(): files kept while unchanged)."""
    with s.reading():
        return {k: len(s.records(k)) for k in RECORD_KINDS}


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
        "records": _record_counts(s),
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
        "design_choices": lambda: design_choices(ctx),
        "archetypes": kinds,
        "joint_wall_inputs": lambda: joint_wall_inputs(ctx, args.get("load_case_id")),
        "formula_inputs": formula_inputs,
        "modules": lambda: modules_section(ctx, agent),
    }
    for name in wanted:
        out[name] = parts[name]()
    return out


MODULES_HOW = ("モジュールの設計役は revise_design に module を付けて、自分のモジュールの variables と choices だけを変える提案を出す "
               "(版は作らない)。空間 (envelope) と取付点 (interfaces) はパッケージ役が決め、build_version がその回の提案をまとめて次の版にする")


def modules_section(ctx: RunContext, agent: str | None = None) -> Record | None:
    """For a design with modules: the caller's module (designer.<module>) or every module (others), each with
    its variables (current values), choices (the values the generator offers), envelope and interfaces, and the
    round's pending proposals. None for a design without modules."""
    mods = rules.design_modules(ctx)
    if mods is None:
        return None
    mine = agent.split(".", 1)[1] if agent and agent.startswith("designer.") else None
    values = {x["name"]: x["value"] for x in design_variables(ctx) or []}
    offered = (design_choices(ctx) or {}).get("choices") or {}

    def one(m: Record, whole: bool) -> Record:
        out: Record = {"module_id": m["module_id"], "owner": m["owner"],
                       "variables": {k: values.get(k) for k in m.get("variables") or []}}
        if m.get("choices"):
            out["choices"] = {k: offered.get(k) for k in m["choices"]}
        out["envelope"] = (m.get("envelope") or []) if whole else _envelope_outline(m.get("envelope") or [])
        if m.get("interfaces"):
            out["interfaces"] = _interface_groups(m["interfaces"])
        return out

    whole = mine in mods
    shown = [mods[mine]] if whole else list(mods.values())
    out = {"design_version": ctx.version, "how": MODULES_HOW, "modules": [one(m, whole) for m in shown],
           "pending": [_proposal_line(c) for c in ctx.store.pending_proposals()]}
    if not whole:
        out["shown"] = MODULES_OUTLINE_SAID
    return out


# Every module at once (the packager, the lead) is shown in outline: run 094333's packager asked for it six times
# and each answer (62 000 characters, the boxes and the 115 interfaces) was over Claude Code's limit, saved to a file
# the role could not read
MODULES_OUTLINE_SAID = ("全部のモジュールを見るときは、空間 (envelope) を箱の数と全体の範囲 (bounds) で、取付点 (interfaces) を "
                        "モジュールの組と付け方ごとの節の並びで示す。build_version の regrow は箱を書かずに空間を作り直す")


def _envelope_outline(boxes: list[Record]) -> Record:
    """A module's envelope as the number of boxes and the axis-aligned bounds of all of them (mm)."""
    if not boxes:
        return {"boxes": 0}
    lo = [min(b["center_mm"][i] - b["size_mm"][i] / 2.0 for b in boxes) for i in range(3)]
    hi = [max(b["center_mm"][i] + b["size_mm"][i] / 2.0 for b in boxes) for i in range(3)]
    return {"boxes": len(boxes), "bounds_mm": {"min": [round(x, 1) for x in lo], "max": [round(x, 1) for x in hi]}}


def _interface_groups(interfaces: list[Record]) -> list[Record]:
    """Interfaces grouped by the pair of modules and how they join: each group's ids with their node or point
    ("if-001@RS1"), in the order given."""
    groups: dict[tuple, Record] = {}
    for i in interfaces:
        key = (tuple(i["between"]), i.get("via"))
        g = groups.setdefault(key, {"between": list(i["between"]), **({"via": i["via"]} if i.get("via") else {}),
                                    "at": []})
        where = i.get("node") or ("(" + ", ".join(f"{round(x, 1):g}" for x in i["point_mm"]) + ")"
                                  if i.get("point_mm") else "")
        g["at"].append(f"{i['id']}@{where}" if where else i["id"])
    return list(groups.values())


PAIRS_HOW = ("predicts: ツールのどの結果 (quantity) を、どの式 (formula_id) のどの出力 (output) で予測するかの対。"
             "判定の量を予測するときは、その量と対になった式を選び、note を読む (ばねのあるツールの横に並ぶ剛体の式は、"
             "初めの見積もりで、解析とは食い違う)")


def list_formulas(ctx: RunContext, args: dict[str, Any] | None = None, agent: str | None = None) -> list[Record] | Record:
    """K0 formulas, optionally only those of one domain. With `tool`: {"tool", "formulas" (those the
    tool lists for its predictions, else those whose id starts with its name), "predicts" (the
    tool's own pairing of its result quantities with formula outputs and their notes, from its
    describe(); [] when it has none), "how"}."""
    args = args or {}
    out = ctx.formulas.list_formulas()
    tool, ids = args.get("tool"), args.get("formula_ids")
    if ids:  # the whole entries of the formulas named
        missing = [i for i in ids if i not in {f["formula_id"] for f in out}]
        if missing:
            raise ToolError(f"式 {', '.join(missing)} はありません (list_formulas の formula_id)")
        return [f for f in out if f["formula_id"] in ids]
    if tool:
        named = (ctx.specs.get(tool) or {}).get("formulas") or []
        out = [f for f in out if f["formula_id"] in named or f["formula_id"].split(".", 1)[0] == tool]
    if args.get("domain"):
        out = [f for f in out if f["domain"] == args["domain"]]
    if not tool:  # the short list (run N13: the whole strength list did not fit an answer); formula_ids gives the rest
        return [formula_line(f) for f in out]
    return {"tool": tool, "formulas": out, "predicts": tool_predicts(ctx, tool), "how": PAIRS_HOW}


def formula_line(f: Record) -> Record:
    """One formula in short: id, title, domain, and the names and units of its inputs and outputs."""
    names = lambda d: {k: v.get("unit") for k, v in (d or {}).items()} if isinstance(d, dict) else d  # noqa: E731
    return {"formula_id": f["formula_id"], "title": f.get("title"), "domain": f.get("domain"),
            "inputs": names(f.get("inputs")), "outputs": names(f.get("outputs"))}


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


def _shared_design(ctx: RunContext) -> tuple[Any, Record]:
    """(stamp, design) of the current version, read once while the file holds (run 283a934: 8 980 reads);
    the stamp is None for a store without design_shared. Read the design, do not change it."""
    shared = getattr(ctx.store, "design_shared", None)
    return shared() if shared else (None, ctx.store.design())


def design_kind(ctx: RunContext) -> str | None:
    """The kind of the version's design: its design.archetype when it has one (docs/contract.md Claude が書く設計:
    read first), else the hiraku.designgen ARCHETYPES key of the generator that made it (designgen.generator_of);
    None for a design no generator made (one Claude wrote, write_design) or without designgen."""
    if not ctx.version:
        return None
    design = _shared_design(ctx)[1]
    if design.get("archetype"):
        return design["archetype"]
    if design.get("written_by"):  # Claude wrote it: never guessed from variables a draft may have kept
        return None
    gen_of, kinds = getattr(ctx.designgen, "generator_of", None), getattr(ctx.designgen, "ARCHETYPES", None)
    if gen_of is None or not kinds:
        return None
    try:
        gen = gen_of(design)
    except ValueError:  # DesignGenError: its variables match no generator
        return None
    return next((k for k, m in kinds.items() if m is gen), None)


_APPLIES_KEPT: dict[tuple, str | None] = {}  # (store, design stamp, tool, applies) -> its answer (HK5 asks often)


def not_applying(ctx: RunContext, name: str) -> str | None:
    """Why the tool does not apply to the version's design, as a sentence; None when it applies (or before
    version 1). A tool with an applies(design) method decides (a str is its reason, False a refusal without
    one; None or True: it applies). Without one, contract tool_spec.applies_to: a tool that names kinds of
    design applies only to a design of one of them (design_kind)."""
    if not ctx.version:
        return None
    applies = getattr(ctx.tools.get(name), "applies", None)
    if callable(applies):
        stamp, design = _shared_design(ctx)
        key = (id(ctx.store), stamp, name, id(getattr(applies, "__func__", applies)))
        if stamp is not None and key in _APPLIES_KEPT:
            return _APPLIES_KEPT[key]
        try:
            got = applies(design)
        except Exception as e:  # a tool made in the run may check itself badly; it is not used on a guess
            got = f"ツール {name} がこの設計に当てはまるかを確かめられませんでした ({type(e).__name__}: {e})"
        why = got if isinstance(got, str) and got else (
            f"ツール {name} は、この版の設計には当てはまりません (ツールの applies の答え)" if got is False else None)
        if stamp is not None:
            if len(_APPLIES_KEPT) > 512:
                _APPLIES_KEPT.clear()
            _APPLIES_KEPT[key] = why
        return why
    kinds = (ctx.specs.get(name) or {}).get("applies_to")
    if not kinds:
        return None
    kind = design_kind(ctx)
    if kind in kinds:
        return None
    what = f"この版の設計の型は {kind} です" if kind else "この版の設計は、型から作った設計ではありません (型が分からない)"
    return f"ツール {name} は、設計の型 {', '.join(kinds)} のためのツールです。{what}"


def _check_applies_to(ctx: RunContext, name: str) -> None:
    """The tool is used only on a design it applies to (not_applying), else a ToolError with the reason."""
    why = not_applying(ctx, name)
    if why is None:
        return
    tail = ("list_tools で、applies_to の無いツールか、この型を含むツールを選んでください"
            if not callable(getattr(ctx.tools.get(name), "applies", None))
            else "list_tools で、この設計に当てはまるほかのツールを選んでください")
    raise ToolError(f"{why}。{tail}")


def list_tools(ctx: RunContext, args: dict[str, Any] | None = None, agent: str | None = None) -> list[Record]:
    """The checked tool shelf, one line per tool (usable=false tools cannot be run); with
    `tool`, that tool's whole spec (its known-answer checks and formulas)."""
    args = args or {}
    lines = {n: _unchecked_line(n, ctx.tools[n], ctx.version) for n in unchecked_tools(ctx)}
    if args.get("tool"):
        spec = ctx.specs.get(args["tool"]) or lines.get(args["tool"])
        if spec is None:
            raise ToolError(f"ツール {args['tool']!r} は棚にありません ({', '.join([*ctx.specs, *lines])})")
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


PARAMS_HOW = ("params は荷重条件の params でツールが読む名前 (無ければ default)。run_params は analyze の params に入れる設定。"
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


OTHER_OUTPUTS = "式のほかの出力 (予測にしていないもの): "
MAX_OTHER_OUTPUTS = 16


def _short_number(x: float) -> str:
    return f"{x:.4g}"


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
    rest = [f"{k} {_short_number(q.get('value'))} {q.get('unit', '')}".rstrip() for k, q in outputs.items()
            if k != name and isinstance(q, dict) and isinstance(q.get("value"), (int, float))]
    if rest:
        # run 160725: the R4 clearance formula's travel left (t_side -212, -94, -44 mm on versions 3-5) was in its
        # outputs only; the designers read the clearance, which stays at contact, and spent two rounds on it
        assumptions.append(f"{OTHER_OUTPUTS}{'、'.join(rest[:MAX_OTHER_OUTPUTS])}"
                           + (f" ほか {len(rest) - MAX_OTHER_OUTPUTS} 個" if len(rest) > MAX_OTHER_OUTPUTS else ""))
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
        raise ToolError(f"ツール {name!r} は棚にありません。list_tools でツールを選んでください ({', '.join(ctx.tools)})")
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
    # docs/contract.md 荷重の通り道が無いとき: load points with no path give the load case a failing verdict
    ver = unsupported_load_verdict("", out, lc) if lc.get("criterion") else None
    if ver is not None:
        ver["verdict_id"] = ctx.store.next_id("verdict")
        ctx.store.add_record("verdict", ver, design_version=v, agent=agent, domain=lc["domain"])
    # docs/contract.md CAE の役目: a place the hand calculation did not predict, over the limit, makes the load case
    # red (one verdict per place)
    for row in unpredicted_over(ctx, out, lc):
        ctx.store.add_record("verdict", hot_spot_verdict(ctx.store.next_id("verdict"), out, lc, row),
                             design_version=v, agent=agent, domain=lc["domain"])
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
        if cause["resolved"] or not cause.get("comparison_id"):  # a hot_spot cause is settled by a version, not a match
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
    causes = {c["cause_id"] for c in ctx.store.records("cause") if c.get("comparison_id") in on_it}
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


def _per_place(ctx: RunContext, ana: Record, pred: Record, crit: Record | None, judged: str | None,
               where: str | None, result_quantity: str | None) -> tuple[Record, str | None, str]:
    """docs/contract.md 場所ごとの予測 (contract 3b17139): (the prediction to compare, the place, the result
    name to pick). A prediction named <quantity>.<place> of the judged quantity is compared with the result at
    that place. A whole prediction of the judged quantity is compared with the prediction of the place where
    the analysis's failing end is (same version and load case, the newest), when there is one."""
    name = result_quantity or pred["quantity"]
    if crit is None or result_quantity:
        return pred, where, name
    quantities = {crit["quantity"], judged}
    base, _, place = pred["quantity"].partition(".")
    if place and base in quantities:  # the result named <q>.<place>, else <q> at that place
        named = any(r["quantity"] == pred["quantity"] for r in ana.get("results", []))
        return pred, place, pred["quantity"] if named else base
    if where is not None or pred["quantity"] not in quantities:
        return pred, where, name
    hits = [r for r in ana.get("results", []) if r["quantity"] == judged and r.get("unit") == crit["limit"]["unit"]]
    worst = failing_end(hits, crit["op"]) if hits else None
    if worst is None or not worst.get("where"):
        return pred, where, name
    want = f"{judged}.{worst['where']}"
    own = [p for p in ctx.store.records("prediction", pred["design_version"])
           if p["load_case_id"] == pred["load_case_id"] and p["quantity"] in (want, f"{crit['quantity']}.{worst['where']}")
           and (p["prediction_id"] in ana["prediction_ids"] or revised_after_cause(ctx, p, ana))]
    if not own:
        return pred, where, name
    return own[-1], worst["where"], judged


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
    # the result a verdict may come from (docs/contract.md 判定に使う量: frame_shell judges max_stress on
    # max_hot_spot_stress, never on its element max_stress)
    judged = judged_quantity(ctx, ana["tool"], crit["quantity"]) if crit else None
    pred, where, name = _per_place(ctx, ana, pred, crit, judged, where, args.get("result_quantity"))
    res = _pick_result(ana, name, crit, pred["quantity"].partition(".")[0], where)
    base = pred["quantity"].partition(".")[0]  # a per-place prediction's quantity without its place
    for_verdict = bool(crit) and (res["quantity"] == judged
                                  or (judged == crit["quantity"] and base == crit["quantity"]))
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
    # contract 9017d93: with the criterion's stand_in_quantity, values both below STAND_IN_BELOW x the limit
    # are too small to compare; the verdict still comes from the analysis, and the tile's (b) from the stand-in
    tiny = bool(crit and crit.get("stand_in_quantity")) and \
        max(abs(predicted), abs(analyzed)) < STAND_IN_BELOW * abs(crit["limit"]["value"])
    if cmp["status"] == "match" or (tiny and for_verdict):
        if cmp["status"] == "match":
            _resolve_causes(ctx, cmp, pred, agent)
        if for_verdict and crit["limit"]["unit"] == res["unit"]:
            ver = make_verdict(ctx, ctx.store.next_id("verdict"), cmp, lc)
            ctx.store.add_record("verdict", ver, design_version=v, agent=agent, domain=lc["domain"])
            out["verdict"] = ver
    if lc["domain"] in ctx.picked:  # a mismatch holds the domain back from green too
        update_judgement(ctx, lc["domain"], v)
    return out


def _check_on_criterion(ctx: RunContext, cmp: Record, lc: Record | None, tool: str | None, pred_quantity: str | None,
                        flag: str) -> Record:
    """The load case's criterion, refusing flag (safe_side, tool_decides: both make a verdict from this comparison)
    unless the comparison is of the quantity the criterion judges (see _check_safe_side)."""
    crit = (lc or {}).get("criterion")
    judged = judged_quantity(ctx, tool, crit["quantity"]) if crit else None
    on_criterion = bool(crit) and (cmp["quantity"] == judged
                                   or (judged == crit["quantity"] and pred_quantity == crit["quantity"]))
    if not crit or not on_criterion or crit["limit"]["unit"] != cmp["predicted"]["unit"]:
        what = f"{crit['quantity']} ({crit['limit']['unit']})" if crit else "なし"
        raise ToolError(
            f"{flag} は合否の基準のある量にだけ付けられます。照合 {cmp['comparison_id']} は {cmp['quantity']} "
            f"({cmp['predicted']['unit']}) で、荷重条件の基準は {what} です。{flag} を外して原因と直し方だけ書いてください"
        )
    return crit


def _on_criterion(ctx: RunContext, cmp: Record, lc: Record, pred: Record | None) -> bool:
    """True when _check_on_criterion passes for the comparison (it is of the quantity the load case is judged on)."""
    ana = ctx.store.get("analysis", cmp["analysis_id"]) or {}
    try:
        _check_on_criterion(ctx, cmp, lc, ana.get("tool"), pred["quantity"] if pred else None, "tool_decides")
    except ToolError:
        return False
    return True


def _check_safe_side(ctx: RunContext, cmp: Record, lc: Record | None, tool: str | None = None,
                     pred_quantity: str | None = None) -> None:
    """Refuse safe_side unless the load case has a criterion on this quantity and the
    prediction is the unfavourable value (larger for <=, smaller for >=). The quantity is the
    compared result's (cmp["quantity"]): the criterion's own, the one the tool is judged on
    (judge.judged_quantity), or a result named by result_quantity for a prediction of the
    criterion's quantity."""
    crit = _check_on_criterion(ctx, cmp, lc, tool, pred_quantity, "safe_side")
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


def _cause_of_hot_spot(ctx: RunContext, args: dict[str, Any], agent: str | None) -> Record:
    """record_cause for a hot_spot verdict (docs/contract.md CAE の役目): the cause of a red the tool found where
    the hand calculation predicted nothing. No comparison, so no safe_side, ends_case or tool_decides (the red
    stands until a revised version keeps that place within its limit); the cause carries the verdict and its place."""
    hit = ctx.store.find("verdict", args["verdict_id"])
    if hit is None:
        raise ToolError(f"合否 {args['verdict_id']!r} はありません")
    v, ver = hit
    if ver.get("basis") != HOT_SPOT:
        raise ToolError(f"合否 {ver['verdict_id']} は basis {ver.get('basis') or 'match'} です。verdict_id で指せるのは、"
                        "手計算が予測していない場所の赤 (basis hot_spot) だけです。食い違いの原因は comparison_id で書いてください")
    flags = [k for k in ("safe_side", "ends_case", "tool_decides") if args.get(k)]
    if flags:
        raise ToolError(f"{'・'.join(flags)} は、照合のある食い違いにだけ付けます。手計算が予測していない場所の赤は、"
                        "直した版でその場所が許しの内になるまで赤のままです。印を外して、原因と直し方だけを書いてください")
    if args.get("where") and args["where"] != ver["where"]:
        raise ToolError(f"合否 {ver['verdict_id']} の場所は {ver['where']} です (where {args['where']!r} と違います)")
    done = next((c for c in ctx.store.records("cause") if c.get("verdict_id") == ver["verdict_id"]), None)
    if done:
        raise ToolError(f"合否 {ver['verdict_id']} の原因は {done['cause_id']} にもう書かれています。書いた原因のまま進めてください")
    if not args["cause"].strip() or not args["fix"].strip():
        raise ToolError("cause と fix の両方に中身を書いてください")
    lc = ctx.load_case(ver["load_case_id"])
    cause = {"cause_id": ctx.store.next_id("cause"), "verdict_id": ver["verdict_id"], "where": ver["where"],
             "cause": args["cause"].strip(), "fix": args["fix"].strip(), "resolved": False}
    who = agent or ctx.owner(lc["load_case_id"])
    ctx.store.add_record("cause", cause, design_version=v, agent=who, domain=lc["domain"])
    if lc["domain"] in ctx.picked:
        update_judgement(ctx, lc["domain"], v)
    return {"cause": cause, "verdict": None}


def record_cause(ctx: RunContext, args: dict[str, Any], agent: str | None = None) -> Record:
    """Write the cause of a mismatch and how to fix it (this unblocks the domain).

    With safe_side true (and safe_side_reason), the CAE role says the cause explains
    the gap and the prediction bounds the true value from the safe side. The numbers
    are checked first; then a verdict is made from the prediction (the unfavourable
    value), and the domain's judgement is updated. With ends_case true, no prediction checkable
    in this run reaches the quantity: the load case ends not judged (not with safe_side).
    """
    _check_input("record_cause", args)
    if bool(args.get("comparison_id")) == bool(args.get("verdict_id")):
        raise ToolError("comparison_id (食い違った照合) か verdict_id (手計算が予測していない場所の赤、basis hot_spot) の、"
                        "どちらか一方を付けてください")
    if args.get("verdict_id"):
        return _cause_of_hot_spot(ctx, args, agent)
    hit = ctx.store.find("comparison", args["comparison_id"])
    if hit is None:
        raise ToolError(f"照合 {args['comparison_id']!r} はありません")
    v, cmp = hit
    if cmp["status"] != "mismatch":
        raise ToolError(f"照合 {cmp['comparison_id']} は一致しているので、原因を書く必要はありません")
    existing = ctx.causes_by_comparison().get(cmp["comparison_id"])
    if existing:
        raise ToolError(f"照合 {cmp['comparison_id']} の原因は {existing['cause_id']} にもう書かれています。原因の記録は"
                        "書き換えられないので、書いた原因のまま進めてください (直した式で予測し直して照合すれば、"
                        "あとの照合が前の食い違いを置き換えます)")
    if not args["cause"].strip() or not args["fix"].strip():
        raise ToolError("cause と fix の両方に中身を書いてください")
    pred = ctx.prediction(cmp["prediction_id"])
    lc = ctx.load_case(pred["load_case_id"]) if pred else None
    safe = bool(args.get("safe_side"))
    ends = bool(args.get("ends_case"))
    decides = bool(args.get("tool_decides"))
    if safe and ends:
        raise ToolError("safe_side と ends_case は一緒に付けられません (safe_side は予測で合否を出し、ends_case は合否を出さずに終えます)")
    if decides and (safe or ends):
        raise ToolError("tool_decides は safe_side とも ends_case とも一緒に付けられません (tool_decides はツールの値で合否を"
                        "出し、safe_side は予測の値で合否を出し、ends_case は合否を出さずに終えます)")
    if safe:
        if not (args.get("safe_side_reason") or "").strip():
            raise ToolError("safe_side を付けるときは、予測が本当の値を安全側から押さえている理由を safe_side_reason に書いてください")
        ana = ctx.store.get("analysis", cmp["analysis_id"]) or {}
        _check_safe_side(ctx, cmp, lc, ana.get("tool"), pred["quantity"] if pred else None)
    if decides:  # docs/contract.md 手計算の役目: the hand calculation does not reach the value, the path was checked
        if not (args.get("tool_decides_reason") or "").strip():
            raise ToolError("tool_decides を付けるときは、手計算が値に届かない理由を tool_decides_reason に書いてください")
        ana = ctx.store.get("analysis", cmp["analysis_id"]) or {}
        _check_on_criterion(ctx, cmp, lc, ana.get("tool"), pred["quantity"] if pred else None, "tool_decides")
        gap = path_check(ctx, lc, v, ana)
        if gap:
            raise ToolError(f"tool_decides は、力の道を手計算で確かめてあるときだけ付けられます。いまは{gap}。"
                            f"{PATH_FORMULA} で部品から部材までの道を予測して記録し、道をたどるツール (load_path) で解析して"
                            "つながりを確かめてから、もう一度書いてください")
    if ends and lc is not None and lc.get("criterion") and _on_criterion(ctx, cmp, lc, pred) \
            and path_check(ctx, lc, v) is None:
        # only where tool_decides could be put instead (a comparison of the judged quantity), so the two refusals
        # never point at each other
        raise ToolError(f"{lc['load_case_id']} は、この版で力の道を手計算で確かめてあります。手計算が値に届かないときは、"
                        "ends_case ではなく tool_decides と tool_decides_reason を付けてください (ツールの値で合否を出します)")
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
    if ends:
        cause["ends_case"] = True  # the load case ends not judged (rules.ended_cases: HK4 and HK5 do not wait for it)
    if decides:
        cause.update(tool_decides=True, tool_decides_reason=args["tool_decides_reason"].strip())
    ctx.store.add_record("cause", cause, design_version=v, agent=agent, domain=domain)
    ctx.store.add_record("comparison", dict(cmp, cause_id=cause["cause_id"]), design_version=v, agent=agent,
                         domain=domain)
    out: Record = {"cause": cause, "verdict": None}
    if safe or decides:
        ver = make_verdict(ctx, ctx.store.next_id("verdict"), cmp, lc, cause_id=cause["cause_id"],
                           basis=PATH_CHECKED if decides else None)
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


CHOICES_HOW = ("revise_design の choices に {名前: 選ぶ値} で書ける近道 (いくつかの変数をまとめて決める)。"
               "同じことは design_variables の変数でも書ける。どの変数が動いたかは revise_design の答えの changes に出る")


def design_choices(ctx: RunContext) -> Record | None:
    """The short ways to set several variables at once (the generator's CHOICES: {name: [values]}), which
    revise_design takes like a variable (designgen.regenerate); None without a generated design."""
    if not ctx.version or ctx.designgen is None:
        return None
    gen_of = getattr(ctx.designgen, "generator_of", None)
    try:
        gen = gen_of(ctx.store.design()) if gen_of else None
    except Exception:  # a design no generator made (written by hand)
        gen = None
    choices = getattr(gen, "CHOICES", None)
    if not choices:
        return None
    return {"choices": {k: list(v) for k, v in choices.items()}, "how": CHOICES_HOW}


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
    (project.updated), and the new version gets pending tiles (first_version)."""
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
    load_cases = first_version(ctx, design, req, agent)
    return {
        "design_version": 1,
        "archetype": args["archetype"],
        "change_reason": reason,
        "load_cases": [{"load_case_id": lc["load_case_id"], "owner": ctx.owner(lc["load_case_id"])} for lc in load_cases],
        "domain_picks": [p["domain"] for p in ctx.project["domain_picks"]],
    }


def first_version(ctx: RunContext, design: Record, req: Record, agent: str) -> list[Record]:
    """Make version 1 of a design that fits the contract (propose_design, write_design): build the load cases
    on it (Claude's picks when the requirements file lists no tests), write the design (design.created), give
    the project its load cases, picks and roles (project.updated), say the tests left out, check the tools of
    version 1's tests and start the judgements. A ToolError, before anything is written, when no load case
    can be built on it. Returns the load cases."""
    from hiraku.catalog import build_tests, load_catalog, project_fields, skipped_text

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
    return load_cases


def rebuild_load_cases(ctx: RunContext, design: Record) -> list[Record]:
    """Build the run's load cases again on a new version's design (a criterion may read the design,
    e.g. H5's number of parts) and write them for that version (load_cases.created). The same load
    case ids as the version before: a test that no longer builds keeps its earlier load case, and
    the run says so."""
    from hiraku.catalog import load_catalog
    from hiraku.catalog.build import build_load_cases

    v = design["design_version"]
    before = ctx.store.version_load_cases(v - 1)
    tests = list(dict.fromkeys(lc["test_id"] for lc in before if not lc["load_case_id"].endswith("-beyond")))
    req = requirements_now(ctx)
    skipped: list = []
    try:
        built = build_load_cases(load_catalog(), req, design, tests, skipped, beyond=req.get("beyond", []))
    except ValueError as e:
        built, skipped = [], [("*", str(e))]
    by_id = {lc["load_case_id"]: lc for lc in built}
    kept = [lc["load_case_id"] for lc in before if lc["load_case_id"] not in by_id]
    load_cases = [by_id.get(lc["load_case_id"], lc) for lc in before]
    changed = [new["load_case_id"] for old, new in zip(before, load_cases) if old != new]
    text = (f"版 {v} の荷重条件を、この版の設計で作り直しました"
            + (f" (変わったもの: {'、'.join(changed)})" if changed else " (前の版と同じ)"))
    if kept:
        text += f"。この版では作れなかったので前の版のままのもの: {'、'.join(kept)}"
    ctx.store.write_version_load_cases(v, load_cases, text=text)
    return load_cases


def _round_and_cost_left(ctx: RunContext) -> None:
    """revise_design's and build_version's limits: the revisions stay within round_limit (each new version is one
    round) and the project's cost_usd is below cost_limit_usd (see revise_design)."""
    p, v = ctx.project, ctx.version
    if v - 1 >= p["round_limit"]:
        raise ToolError(f"設計の直しは round_limit ({p['round_limit']} 回) まで使い切りました (今の版は {v})。"
                        f"直さずに、今の版の判定で終えてください")
    if p["cost_usd"] is not None and p["cost_usd"] >= p["cost_limit_usd"]:  # null: the plugin path, not known
        raise ToolError(f"まとめ役の最後に終わった番の時点で、費用 ({p['cost_usd']:.4g} USD) が上限 "
                        f"{p['cost_limit_usd']} USD に達しているので、設計は直せません")


def _checked_changes(ctx: RunContext, design: Record, given: Record, choices: Record) -> tuple[dict, list[str], dict]:
    """(variable spec by name, revisable names, changes as floats) after checking every variable is a revisable
    one of the design (hiraku.designgen.variables), keeps its unit and stays in its range, and every choice is
    one of the generator's with one of its values; a ToolError lists every problem."""
    found = _variables(ctx, design)
    if found is None:
        if ctx.designgen is None:
            raise ToolError("設計を変数から組み直す関数 (hiraku.designgen) がこの実行に無いので、revise_design では直せません。"
                            "設計役が write_design で次の版を書いてください")
        raise ToolError("この設計は型から作った設計ではない (どの型の変数とも合わない) ので、revise_design では直せません。"
                        "write_design で次の版を書いてください (変えた所と理由を reason に)")
    spec = {s["name"]: s for s in found}
    revisable = [n for n, s in spec.items() if s.get("revisable")]
    problems = []
    known = (design_choices(ctx) or {}).get("choices") or {}
    for name, opt in choices.items():  # contract tool_input.revise_design.choices: the generator's short ways
        if name not in known:
            problems.append(f"近道 {name!r} はありません (この設計の近道: {', '.join(known) or 'なし'}。get_context の design_choices)")
        elif opt not in known[name]:
            problems.append(f"{name} は {', '.join(map(str, known[name]))} のどれかです ({opt!r} ではなく)")
    for name, q in given.items():
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
    return spec, revisable, {k: {"value": float(q["value"]), "unit": q["unit"]} for k, q in given.items()}


def _regenerate(ctx: RunContext, design: Record, merged: Record, tool: str) -> Record:
    try:
        return dict(ctx.designgen.regenerate(copy.deepcopy(design), merged))
    except ValueError as e:
        raise ToolError(f"この変数では設計を組めません: {e}。{_who_changes(design, str(e), tool)}") from None


def _who_changes(design: Record, said: str, tool: str) -> str:
    """The next step after the generator refused: on a design with modules, who changes the variables its words
    name (run 094333: the packager was told to call build_version again over an occupant variable it cannot
    change, and spent its rounds on it)."""
    mods = {m["module_id"]: m for m in design.get("modules") or []}
    named = [(k, mid) for mid, m in mods.items() for k in list(m.get("variables") or []) + list(m.get("choices") or [])
             if re.search(rf"\b{re.escape(k)}\b", said)]
    if not named:
        return f"値を変えて {tool} を呼び直してください"
    by: dict[str, list[str]] = {}
    for k, mid in named:
        by.setdefault(mid, []).append(k)
    steps = []
    for mid, keys in by.items():
        if mid == "package":
            steps.append(f"{'・'.join(keys)} はレイアウトの変数: パッケージ役が build_version の changes で変える")
        else:
            steps.append(f"{'・'.join(keys)} は{contract.MODULE_NAMES_JA.get(mid, mid)}の変数: designer.{mid} が "
                         f"revise_design (module={mid}) で出し直す (前の提案は置き換わる。パッケージ役はまとめ役に返す)")
    return "次にすること: " + "。".join(steps) + f"。そのあと {tool} を呼び直す"


def _moved(ctx: RunContext, spec: dict, revisable: list[str], new: Record, changes: Record) -> tuple[list[str], dict]:
    """The revisable variables whose value or unit the new design has changed (read back from it), and the new
    design's variables by name; the given changes count as moved even when designgen does not list them."""
    after = {x["name"]: x for x in (_variables(ctx, new) or [])}
    moved = [k for k in revisable if k in after and (after[k]["value"], after[k]["unit"]) != (spec[k]["value"], spec[k]["unit"])]
    moved += [k for k in changes if k not in moved]
    return moved, after


def _new_version(ctx: RunContext, new: Record, agent: str) -> None:
    errs = contract.errors("design", new)
    if errs:
        raise ToolError("組み直した設計が契約の形に合いません (hiraku.designgen の不具合): " + "; ".join(errs)[:2000])
    ctx.store.write_design(new, agent=agent)
    rebuild_load_cases(ctx, new)
    init_judgements(ctx, new["design_version"])


def _version_summary(ctx: RunContext, v: int, reason: str, spec: dict, moved: list[str], after: dict,
                     changes: Record, choices: Record) -> Record:
    return {
        "design_version": v + 1,
        "parent_version": v,
        "change_reason": reason,
        "changes": [{"variable": k, "from": {"value": spec[k]["value"], "unit": spec[k]["unit"]},
                     "to": {"value": after[k]["value"], "unit": after[k]["unit"]} if k in after else changes[k]}
                    for k in moved],
        **({"choices": choices} if choices else {}),
        "redo": [lc["load_case_id"] for lc in ctx.load_cases],
        "revisions_left": ctx.project["round_limit"] - v,
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

    On a design with modules (design.modules) the call is a module designer's proposal instead
    (propose_module_change): no version is made; build_version builds the round's proposals.
    """
    _check_input("revise_design", args)
    if rules.design_modules(ctx) is not None:
        return propose_module_change(ctx, args, agent)
    agent = agent or "designer"
    if agent != "designer":
        raise ToolError("設計を直せるのは設計役 (designer) だけです。まとめ役から設計役に、どの試験をなぜ直すかを伝えてください")
    if args.get("module"):
        raise ToolError("この設計はモジュールに分かれていないので、module は付けません。module を外して呼び直してください")
    reason = args["reason"].strip()
    if not reason:
        raise ToolError("reason に、どの試験が通らず、変更がそれにどう効くかを書いてください")
    v = ctx.version
    if v and ctx.store.design().get("written_by"):
        raise ToolError("この設計は Claude が書いた設計 (written_by) なので、revise_design では直しません (変数から組み直すと、"
                        "書いた所が消えます)。次の版は write_design で書いてください")
    _round_and_cost_left(ctx)
    design = ctx.store.design()
    choices = dict(args.get("choices") or {})
    spec, revisable, changes = _checked_changes(ctx, design, args.get("changes") or {}, choices)
    new = _regenerate(ctx, design, {**changes, **choices}, "revise_design")
    new.update(design_version=v + 1, parent_version=v, change_reason=reason)
    _new_version(ctx, new, agent)
    # every variable that moved: those given, and those a choice set (read back from the new design)
    moved, after = _moved(ctx, spec, revisable, new, changes)
    return _version_summary(ctx, v, reason, spec, moved, after, changes, choices)


# ---------------------------------------------------------------------------- designs Claude writes
# docs/contract.md Claude が書く設計 (2026-11-06): write_design makes a version of a design Claude wrote itself,
# after check_design (the code's checks of references, connection, sizes, interference and masses) passes.

# Size checks of a written design. These limits are this tool's choice (no external source): lengths are mm
# and masses kg by the fields' names, and a value far outside them is most likely in another unit.
MEMBER_MIN_MM = 1.0        # shorter: the two ends are at one place (or the lengths are in metres); K1 cannot solve it
MEMBER_MAX_MM = 20000.0    # longer than any product HIRAKU designs
COORD_MAX_MM = 1.0e5       # a node farther than 100 m from the origin
PART_SIZE_MAX_MM = 20000.0
SUPPORT_RADIUS_MIN_MM = 5.0
INTERFERENCES_SHOWN = 5
LISTED_MAX = 12            # ids named in one sentence; the rest are counted
WRITE_FIELDS = ("design_version", "parent_version", "change_reason")  # the tool sets them


def _listed(names: Any, n: int = LISTED_MAX) -> str:
    names = list(names)
    return ", ".join(map(str, names[:n])) + (f" ほか {len(names) - n} 件" if len(names) > n else "")


def _vec3(v: Any) -> bool:
    return isinstance(v, list) and len(v) == 3 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)


def _items(design: Record, key: str) -> list[Record]:
    got = design.get(key)
    return [x for x in got if isinstance(x, dict)] if isinstance(got, list) else []


def _id_set(items: list[Record], what: str, problems: list[str]) -> set:
    seen: set = set()
    dup = []
    for it in items:
        i = it.get("id")
        if i in seen:
            dup.append(i)
        seen.add(i)
    if dup:
        problems.append(f"{what}の id が重なっています: {_listed(dict.fromkeys(dup))}。id は 1 つずつ別にしてください")
    return seen


def _references(design: Record, ids: Record, problems: list[str], notes: list[str]) -> None:
    """(b) Every name a member, part and support uses is in the design."""
    nodes, members, parts, supports = ids["nodes"], ids["members"], ids["parts"], ids["supports"]
    sections = design.get("sections") if isinstance(design.get("sections"), dict) else {}
    materials = design.get("materials") if isinstance(design.get("materials"), dict) else {}
    member_part = []
    for m in _items(design, "members"):
        mid = m.get("id")
        for end in ("node_a", "node_b"):
            if m.get(end) not in nodes:
                problems.append(f"部材 {mid} の {end} {m.get(end)!r} は節点 (nodes) にありません")
        if m.get("section") not in sections:
            problems.append(f"部材 {mid} の断面 {m.get('section')!r} は sections にありません ({_listed(sections) or 'なし'})")
        if m.get("material") not in materials:
            problems.append(f"部材 {mid} の材料 {m.get('material')!r} は materials にありません ({_listed(materials) or 'なし'})")
        if m.get("part") is not None and m.get("part") not in parts:
            member_part.append(mid)
    if member_part:
        notes.append(f"部材の part が部品 (parts) にありません: {_listed(member_part)}。part は部材がどの部品に入るかの印で、"
                     "無くても解析はできます (骨組みを囲む箱の部品は置かないので、part は書かなくてよい)")
    hardpoint_of = {s.get("id") for s in _items(design, "supports")}
    for p in _items(design, "parts"):
        pid = p.get("id")
        for nid in p.get("carried_by") or []:
            if nid not in nodes:
                problems.append(f"部品 {pid} の carried_by {nid!r} は節点 (nodes) にありません")
        if p.get("material") is not None and p.get("material") not in materials:
            problems.append(f"部品 {pid} の材料 {p.get('material')!r} は materials にありません")
        for c in p.get("connects_to") or []:
            to = c.get("to") if isinstance(c, dict) else None
            if to in parts or to in members or to in nodes or to in supports:
                continue
            if isinstance(to, str) and "." in to and to.split(".", 1)[0] in hardpoint_of:
                continue  # <support>.<hardpoint>
            problems.append(f"部品 {pid} の connects_to の to {to!r} は、部品・部材・節点・支えのどれにもありません")
    for s in _items(design, "supports"):
        if s.get("frame_node") is not None and s.get("frame_node") not in nodes:
            problems.append(f"支え {s.get('id')} の frame_node {s.get('frame_node')!r} は節点 (nodes) にありません")


def _connection(design: Record, ids: Record, problems: list[str], notes: list[str]) -> None:
    """(c) Every member and every node a part is carried by reaches a support's frame_node through the members."""
    nodes = ids["nodes"]
    members = _items(design, "members")
    adj: dict[str, set] = {}
    for m in members:
        a, b = m.get("node_a"), m.get("node_b")
        if a in nodes and b in nodes:
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
    supports = _items(design, "supports")
    roots = [s["frame_node"] for s in supports if s.get("frame_node") in nodes]
    without = [s.get("id") for s in supports if s.get("frame_node") is None]
    if members and not roots:
        problems.append("どの支え (supports) にも frame_node (その支えの荷重を受ける骨組みの節点) が無いので、骨組みが地面に"
                        "届きません。支えごとに frame_node を書いてください")
        return
    if without:
        notes.append(f"frame_node の無い支え: {_listed(without)}。その支えの荷重がどの節点から骨組みに入るかが分かりません")
    reached, todo = set(roots), list(roots)
    while todo:
        for n in adj.get(todo.pop(), ()):
            if n not in reached:
                reached.add(n)
                todo.append(n)
    loose = [m.get("id") for m in members if m.get("node_a") in nodes and m.get("node_a") not in reached]
    if loose:
        problems.append(f"支え (supports の frame_node) まで部材でつながっていない部材があります: {_listed(loose)}。"
                        "部材でつなぐか、外してください (ばらばらの骨組みは解けません)")
    carried = set()
    for p in _items(design, "parts"):
        cb = [n for n in p.get("carried_by") or [] if n in nodes]
        carried |= set(cb)
        off = [n for n in cb if n not in reached]
        if off:
            problems.append(f"部品 {p.get('id')} を載せる節点 {_listed(off)} は、支えまで部材でつながっていません")
    no_carry = [p.get("id") for p in _items(design, "parts") if not p.get("carried_by") and p.get("kind") != "wheel"]
    if no_carry:
        notes.append(f"carried_by の無い部品: {_listed(no_carry)}。重さを受ける節点を書かないと、ツールはその部品を"
                     "車輪の側 (ばね下) の部品として扱います")
    alone = [n for n in nodes if n not in adj and n not in carried and n not in roots]
    if alone:
        notes.append(f"どの部材にも部品にも使われていない節点: {_listed(alone)}")


def _sizes(design: Record, problems: list[str], notes: list[str]) -> None:
    """(d) Lengths in mm: members not shorter than MEMBER_MIN_MM nor longer than MEMBER_MAX_MM, nodes within
    COORD_MAX_MM of the origin; part boxes and wheel radii said in notes when far off."""
    xyz = {n.get("id"): n["xyz_mm"] for n in _items(design, "nodes") if _vec3(n.get("xyz_mm"))}
    short, long_ = [], []
    for m in _items(design, "members"):
        a, b = xyz.get(m.get("node_a")), xyz.get(m.get("node_b"))
        if a is None or b is None:
            continue
        length = sum((p - q) ** 2 for p, q in zip(a, b)) ** 0.5
        if length < MEMBER_MIN_MM:
            short.append(f"{m.get('id')} ({length:.4g} mm)")
        elif length > MEMBER_MAX_MM:
            long_.append(f"{m.get('id')} ({length:.4g} mm)")
    if short:
        problems.append(f"長さが {MEMBER_MIN_MM:g} mm より短い部材があります: {_listed(short)}。両端の節点が同じ所にあるか、"
                        "座標を m で書いています (座標と長さは mm で書く)")
    if long_:
        problems.append(f"長さが {MEMBER_MAX_MM:g} mm (20 m) を超える部材があります: {_listed(long_)}。座標は mm で書きます")
    far = [str(i) for i, p in xyz.items() if max(abs(x) for x in p) > COORD_MAX_MM]
    if far:
        problems.append(f"原点から {COORD_MAX_MM:g} mm を超える節点があります: {_listed(far)}。座標は mm で書きます")
    big = [str(p.get("id")) for p in _items(design, "parts")
           if _vec3((p.get("box") or {}).get("size_mm")) and max(p["box"]["size_mm"]) > PART_SIZE_MAX_MM]
    if big:
        notes.append(f"箱の辺が {PART_SIZE_MAX_MM:g} mm を超える部品: {_listed(big)}。箱の大きさは mm で書きます")
    small = [str(s.get("id")) for s in _items(design, "supports")
             if isinstance(s.get("radius_mm"), (int, float)) and 0 < s["radius_mm"] < SUPPORT_RADIUS_MIN_MM]
    if small:
        notes.append(f"半径が {SUPPORT_RADIUS_MIN_MM:g} mm より小さい支え: {_listed(small)}。半径は mm で書きます")


def _interference(design: Record, out: Record, notes: list[str]) -> None:
    """(e) hiraku.solvers.k4_geometry.interferences: the pairs that overlap at rest, counted, the first few named."""
    try:
        from hiraku.solvers.k4_geometry import interferences
    except ImportError:
        notes.append("干渉を調べる関数 (hiraku.solvers.k4_geometry) が無いので、干渉は確かめていません")
        return
    try:
        hits = list(interferences(design))
    except Exception as e:  # a shape K4 cannot read: said, the other checks stand
        notes.append(f"干渉を確かめられませんでした ({type(e).__name__}: {e})")
        return
    out["interferences"] = {"count": len(hits),
                            "first": [{"a": a, "b": b, "overlap_mm": mm} for a, b, mm in hits[:INTERFERENCES_SHOWN]]}
    if hits:
        notes.append(f"止まっているときに重なる組が {len(hits)} 件あります (interferences の first)。組み立ての試験 (H1) で"
                     "赤になります。骨組みを囲む箱の部品を置いていないか、部品の箱が部材に入っていないかを見てください")


def _requirement_value(req: Record, key: str) -> Any:
    return next((r.get("value") for r in req.get("requirements") or [] if r.get("key") == key), None)


def _masses(ctx: RunContext, design: Record, out: Record, notes: list[str], whole: bool) -> None:
    """(f) The parts' masses summed (and the members' when the design is whole), the cargo's against Q40 and the
    people against Q30's loading."""
    from hiraku.contract.loading import OCCUPANTS, person_of

    parts = _items(design, "parts")
    kg = [p for p in parts if isinstance(p.get("mass_kg"), (int, float))]
    out["mass_kg"] = sum(float(p["mass_kg"]) for p in kg)
    cargo = [p for p in kg if p.get("kind") == "cargo"]
    out["cargo_mass_kg"] = sum(float(p["mass_kg"]) for p in cargo)
    if whole:
        from hiraku.tools.summary import frame_masses

        skipped: list[Record] = []
        out["frame_mass_kg"] = sum(m for _, m, _ in frame_masses(design, skipped))
        if skipped:
            notes.append(f"質量を足せなかった部材: {_listed(s['member'] for s in skipped)} (断面の面積が出ない など)")
    req = requirements_now(ctx)
    q40 = _requirement_value(req, "Q40")
    if isinstance(q40, (int, float)) and not isinstance(q40, bool):
        out["Q40_kg"] = float(q40)
        if not cargo:
            notes.append("荷 (kind cargo) の部品がありません。要件 Q40 (積載) の荷を部品として置きます")
        elif abs(out["cargo_mass_kg"] - float(q40)) > 1e-6 * max(1.0, abs(float(q40))):
            notes.append("荷 (kind cargo) の部品の質量の和 (cargo_mass_kg) が要件 Q40 (Q40_kg) と違います。"
                         "荷の質量は要件の値にします")
    people = {person_of(p) for p in parts if p.get("kind") == "occupant"}
    out["occupants"] = len(people)
    loading = _requirement_value(req, "Q30") or req.get("default_loading")
    if loading in OCCUPANTS and people and len(people) != OCCUPANTS[loading]:
        notes.append(f"要件の載せ方 {loading} の人の数と、この設計の人 (kind occupant) の数 (occupants) が違います")


def design_check(ctx: RunContext, design: Record) -> Record:
    """check_design's answer for one design: {ok, design_version, problems, notes, counts, mass_kg, cargo_mass_kg,
    frame_mass_kg?, Q40_kg?, occupants, interferences?}. problems (a)-(d) keep it from being a version; the
    interference and the masses are worked out when the design is whole (no problem) and said in notes. Every
    number comes from the code."""
    problems: list[str] = []
    notes: list[str] = []
    errs = contract.errors("design", design)
    if errs:
        more = f" (ほか {len(errs) - LISTED_MAX} 件)" if len(errs) > LISTED_MAX else ""
        problems.append("契約の design の形に合いません: " + "; ".join(errs[:LISTED_MAX]) + more)
    ids = {k: _id_set(_items(design, k), what, problems)
           for k, what in (("nodes", "節点"), ("members", "部材"), ("parts", "部品"), ("supports", "支え"))}
    _references(design, ids, problems, notes)
    _connection(design, ids, problems, notes)
    _sizes(design, problems, notes)
    out: Record = {"ok": False, "design_version": design.get("design_version"), "problems": problems, "notes": notes,
                   "counts": {k: len(v) for k, v in ids.items()}}
    if not problems:
        _interference(design, out, notes)
    _masses(ctx, design, out, notes, whole=not problems)
    out["ok"] = not problems
    return out


def _as_version(ctx: RunContext, given: Record, reason: str) -> Record:
    """A copy of a written design with the fields the tool sets: the next version's number, its parent and reason;
    variables {} when it has none (the contract asks for the field; a written design may have no variables)."""
    design = copy.deepcopy(given)
    design.setdefault("variables", {})
    v = ctx.version
    design.update(design_version=v + 1, parent_version=v or None, change_reason=reason)
    return design


def check_design(ctx: RunContext, args: dict[str, Any] | None = None, agent: str | None = None) -> Record:
    """Check a written design without making a version (design_check); without `design`, the current version."""
    args = args or {}
    _check_input("check_design", args)
    if "design" not in args:
        if not ctx.version:
            raise ToolError("まだ設計の版がありません。確かめたい設計を design に付けて呼んでください")
        return design_check(ctx, ctx.store.design())
    given = dict(args["design"])
    keep = {k: given[k] for k in WRITE_FIELDS if k in given}
    design = _as_version(ctx, given, "(check_design)")
    design.update(keep)  # a design that says its version is checked as it is
    return design_check(ctx, design)


def write_design(ctx: RunContext, args: dict[str, Any], agent: str | None = None) -> Record:
    """The designer writes the design itself (docs/contract.md Claude が書く設計): the first call makes version 1,
    a later call the next version. The tool sets design_version, parent_version, change_reason (the reason),
    written_by ("claude") and based_on (the archetype of the draft, when given), and drops an archetype (that
    field marks a design a generator made). check_design must pass (every problem said at once).
    Version 1 then goes as propose_design's (first_version); a later version as revise_design's: within
    round_limit and the cost limit, design.created, the load cases built again, pending tiles. Only the designer,
    and not on a design with modules (the module designers propose, the packager builds)."""
    _check_input("write_design", args)
    agent = agent or "designer"
    if agent != "designer":
        raise ToolError("設計を書けるのは設計役 (designer) だけです。まとめ役は設計役に、何をなぜ書くかを伝えて頼んでください")
    if rules.design_modules(ctx) is not None:
        raise ToolError("この設計はモジュールに分かれているので、write_design は使いません。モジュールの設計役が revise_design "
                        "(module つき) で提案し、パッケージ役が build_version で次の版を作ります")
    reason = args["reason"].strip()
    if not reason:
        raise ToolError("reason に、なぜこの設計にしたか (2 回目からは、変えた所と理由、答える試験の記録の ID) を書いてください")
    given = args["design"]
    if given.get("modules") or given.get("module_changes"):
        raise ToolError("write_design の設計には modules と module_changes を書きません (モジュールに分けた設計は型が作ります)。"
                        "外して呼び直してください")
    based_on = args.get("based_on")
    kinds = getattr(ctx.designgen, "ARCHETYPES", None)
    if based_on is not None and kinds and based_on not in kinds:
        raise ToolError(f"based_on の型 {based_on!r} はありません (型: {', '.join(kinds)})。下書きに型を使っていなければ外してください")
    v = ctx.version
    if v:
        _round_and_cost_left(ctx)
    said = [k for k in (*WRITE_FIELDS, "archetype", "written_by", "based_on") if k in given]
    design = _as_version(ctx, {k: x for k, x in given.items() if k not in said}, reason)
    design["written_by"] = "claude"  # contract ac46bf22: revise_design refuses it, and its kind is never guessed
    if based_on:
        design["based_on"] = based_on
    check = design_check(ctx, design)
    if check["problems"]:
        listed = " / ".join(f"({i}) {p}" for i, p in enumerate(check["problems"], 1))
        raise ToolError(f"この設計は版 {v + 1} にできません。check_design の問題が {len(check['problems'])} 件あります: "
                        f"{listed}。全部を直して write_design を呼び直してください")
    notes = list(check["notes"])
    if said:
        notes.insert(0, f"{', '.join(said)} はツールが付ける (archetype は型から作った設計の印、written_by と based_on は "
                        "write_design が付ける) ので、書かれた値は外しました")
    if not v:
        load_cases = first_version(ctx, design, requirements_now(ctx), agent)
        out: Record = {"design_version": 1, "parent_version": None, "change_reason": design["change_reason"],
                       "load_cases": [{"load_case_id": lc["load_case_id"], "owner": ctx.owner(lc["load_case_id"])}
                                      for lc in load_cases],
                       "domain_picks": [p["domain"] for p in ctx.project["domain_picks"]]}
    else:
        ctx.store.write_design(design, agent=agent)
        rebuild_load_cases(ctx, design)
        init_judgements(ctx, v + 1)
        out = {"design_version": v + 1, "parent_version": v, "change_reason": design["change_reason"],
               "redo": [lc["load_case_id"] for lc in ctx.load_cases],
               "revisions_left": ctx.project["round_limit"] - v}
    if based_on:
        out["based_on"] = based_on
    out["check"] = {**{k: check[k] for k in ("counts", "mass_kg", "cargo_mass_kg", "frame_mass_kg", "Q40_kg",
                                              "occupants", "interferences") if k in check}, "notes": notes}
    return out


def _proposal_line(c: Record) -> Record:
    """A pending proposal, short: who, which module, what it changes."""
    out: Record = {"change_id": c["change_id"], "module_id": c["module_id"], "agent_id": c["agent_id"], "reason": c["reason"]}
    if c.get("changes"):
        out["changes"] = {k: f"{q['value']:g} {q['unit']}" for k, q in c["changes"].items()}
    if c.get("choices"):
        out["choices"] = dict(c["choices"])
    if c.get("load_case_ids"):
        out["load_case_ids"] = list(c["load_case_ids"])
    return out


def _proposal_builds(ctx: RunContext, design: Record, module: str, changes: Record, choices: Record) -> None:
    """Refuse a module proposal the generator cannot build, to the designer who made it (run 160725: an occupant
    proposal of rollbar_width 1200 mm was refused only at build_version, and went packager -> lead -> designer
    -> packager before version 3 was made). Tried on the version with this proposal alone, then with the round's
    other pending proposals too (another module's change may be what makes it fit); refused only when neither
    builds. A design the generator builds is not kept: build_version builds the round's proposals again."""
    if ctx.designgen is None or not (changes or choices):
        return
    others: Record = {}
    for c in ctx.store.pending_proposals(ctx.version):
        if c["module_id"] != module:
            others.update({**(c.get("changes") or {}), **(c.get("choices") or {})})
    said = None
    for merged in ([{**changes, **choices}] + ([{**others, **changes, **choices}] if others else [])):
        try:
            ctx.designgen.regenerate(copy.deepcopy(design), merged)
            return
        except ValueError as e:
            said = said or str(e)
    mine = set((rules.design_modules(ctx) or {}).get(module, {}).get("variables") or [])
    named_others = [k for m in design.get("modules") or [] if m["module_id"] != module
                    for k in m.get("variables") or [] if re.search(rf"\b{re.escape(k)}\b", said)]
    nxt = (_who_changes(design, said, "build_version") if named_others and not any(
        re.search(rf"\b{re.escape(k)}\b", said) for k in mine)
        else "文の示す値に直して、revise_design を呼び直してください")
    raise ToolError(f"この提案では設計を組めません (提案は記録していません): {said}。{nxt}")


def propose_module_change(ctx: RunContext, args: dict[str, Any], agent: str | None = None) -> Record:
    """revise_design on a design with modules (HK6): a proposal of one module's designer (designer.<module>)
    that changes only that module's variables and choices (design.modules[].variables and choices), checked
    like a revision (revisable, unit, range, the generator's choices). It is recorded as a module_change
    (design.change_proposed; the event's design_version and the record's are the version it was based on) and makes no version;
    build_version (the packager) builds the round's proposals into the next one. The round limit and the
    cost limit apply as to a revision, since a proposal is only of use if a version can still be built.

    The scripted run's `fit: driver` (scripted.fit_driver) runs before this call and puts the fitted values
    into `changes`, so a fit that moves a variable outside the module is refused here like any other."""
    module = args.get("module")
    agent = agent or (f"designer.{module}" if module else None)
    v = ctx.version
    _gate(ctx, "revise_design", args, agent)
    if not module or agent != f"designer.{module}":  # _gate's HK6 denies these; kept so a skipped check cannot pass
        raise ToolError("この設計はモジュールに分かれているので、revise_design は module を付けて、そのモジュールの設計役が呼びます")
    reason = args["reason"].strip()
    if not reason:
        raise ToolError("reason に、どの試験が通らず、変更がそれにどう効くかを書いてください")
    _round_and_cost_left(ctx)
    design = ctx.store.design()
    choices = dict(args.get("choices") or {})
    _, _, changes = _checked_changes(ctx, design, args.get("changes") or {}, choices)
    _proposal_builds(ctx, design, module, changes, choices)
    if args.get("load_case_ids"):
        # the contract's module_change.load_case_ids: "The red load cases this change answers" (red on version v)
        from hiraku.tools.judge import latest_verdicts

        verdicts = latest_verdicts(ctx, v)
        not_red = [i for i in args["load_case_ids"] if i not in verdicts or verdicts[i]["pass"]]
        if not_red:
            raise ToolError(
                f"load_case_ids には、版 {v} で赤 (合否の記録が不合格) の荷重条件だけを書きます。赤でないもの: "
                f"{', '.join(not_red)}。前に合格した試験を戻さないための変更や、照合が保留で合否の無いものは、"
                "load_case_ids から外して reason に書いてください")
    change: Record = {"change_id": ctx.store.next_id("module_change"), "module_id": module, "agent_id": agent,
                      "reason": reason, "design_version": v}  # the version it was made on (contract 18c2679)
    if args.get("load_case_ids"):
        change["load_case_ids"] = list(args["load_case_ids"])
    if changes:
        change["changes"] = changes
    if choices:
        change["choices"] = choices
    errs = contract.errors("module_change", change)
    if errs:
        raise ToolError("提案が契約の形に合いません: " + "; ".join(errs))
    before_ids = [c["change_id"] for c in ctx.store.pending_proposals(v) if c["module_id"] == module]
    ctx.store.propose_change(change, design_version=v, agent=agent,
                             text=f"{contract.MODULE_NAMES_JA.get(module, module)}の提案 {change['change_id']}: {reason}")
    pending = ctx.store.pending_proposals(v)
    replaced = [c for c in ctx.store.superseded_change_ids(v) if c in before_ids]
    return {
        "change_id": change["change_id"],
        "module": module,
        "based_on_version": v,
        **({"changes": changes} if changes else {}),
        **({"choices": choices} if choices else {}),
        "pending": [c["change_id"] for c in pending],
        **({"replaced": replaced} if replaced else {}),
        "note": (f"提案として記録しました (版はまだ作っていません)。"
                 + (f"同じモジュールの前の提案 {'、'.join(replaced)} は、この提案に置き換わりました (1 回の直しで、"
                    "モジュールの提案は 1 つ)。" if replaced else "")
                 + f"パッケージ役が build_version で、版 {v} への提案を全部まとめて版 {v + 1} を作ります"),
    }


# ---------------------------------------------------------------------------- build_version (designs with modules)

ENVELOPE_TOL_MM = 0.5  # a corner this close outside an envelope box counts as inside (rounding of the boxes)


def _box_frame(box: Record):
    """(centre, rotation, half sizes) of a box; rotation by hiraku.solvers.k4_geometry's rpy convention."""
    import numpy as np

    from hiraku.solvers.k0_handcalc.rigid import rpy_matrix

    rpy = box.get("rpy_deg")
    R = rpy_matrix(rpy) if rpy is not None and any(rpy) else np.eye(3)
    return np.asarray(box["center_mm"], float), R, np.asarray(box["size_mm"], float) / 2.0


def _box_corners(box: Record) -> list:
    import itertools

    import numpy as np

    c, R, h = _box_frame(box)
    return [c + R @ (np.asarray(s) * h) for s in itertools.product((-1.0, 1.0), repeat=3)]


def _outside_mm(point, envelope: list[Record]) -> float:
    """How far (mm) a point lies outside the nearest envelope box (0 inside one)."""
    import numpy as np

    best = None
    for box in envelope:
        c, R, h = _box_frame(box)
        local = R.T @ (np.asarray(point, float) - c)
        d = float(np.linalg.norm(np.maximum(np.abs(local) - h, 0.0)))
        best = d if best is None else min(best, d)
    return best or 0.0


def envelope_breaches(design: Record, modules: list[str]) -> list[tuple[str, str, float]]:
    """(module, part or member id, mm outside) for every part box corner and member end node of the modules
    given that lies outside all of its module's envelope boxes (modules with an empty envelope are not checked).
    hiraku.designgen has no envelope check yet, so this small one stands in (see .notes/contract-questions.md)."""
    mods = {m["module_id"]: m for m in design.get("modules") or []}
    nodes = {n["id"]: n["xyz_mm"] for n in design.get("nodes") or []}
    out = []
    for m in modules:
        env = (mods.get(m) or {}).get("envelope") or []
        if not env:
            continue
        for part in design.get("parts") or []:
            if part.get("module") == m and part.get("box"):
                far = max(_outside_mm(p, env) for p in _box_corners(part["box"]))
                if far > ENVELOPE_TOL_MM:
                    out.append((m, part["id"], far))
        for mem in design.get("members") or []:
            if mem.get("module") == m:
                ends = [nodes[k] for k in (mem.get("node_a"), mem.get("node_b")) if k in nodes]
                far = max((_outside_mm(p, env) for p in ends), default=0.0)
                if far > ENVELOPE_TOL_MM:
                    out.append((m, mem["id"], far))
    return out


def _modules_moved(old: Record, new: Record) -> list[str]:
    """The modules with a part box or a member end node that differs between two designs (added and removed
    parts and members count)."""
    def shapes(d: Record) -> dict[tuple[str, str], tuple[str | None, Any]]:
        nodes = {n["id"]: n["xyz_mm"] for n in d.get("nodes") or []}
        out = {("part", p["id"]): (p.get("module"), p.get("box")) for p in d.get("parts") or []}
        out.update({("member", m["id"]): (m.get("module"), [nodes.get(m.get("node_a")), nodes.get(m.get("node_b"))])
                    for m in d.get("members") or []})
        return out

    a, b = shapes(old), shapes(new)
    return list(dict.fromkeys(m for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)
                              for m, _ in (a.get(k) or (None, None), b.get(k) or (None, None)) if m))


def _outside_envelopes(ctx: RunContext, design: Record, modules: list[str]) -> list[tuple[str, str, float]]:
    """(module, part or member id, mm outside) of the modules given: hiraku.designgen.modules.outside (the check
    H1-envelope makes) when the run's designgen has it, else the stand-in envelope_breaches (test designgens)."""
    outside = None
    if getattr(ctx.designgen, "__name__", "") == "hiraku.designgen":
        from hiraku.designgen.modules import outside
    if outside is None:
        return envelope_breaches(design, modules)
    envs = {m["module_id"]: m.get("envelope") or [] for m in design.get("modules") or []}
    return [(m, body.split(":", 1)[-1], float(mm)) for body, m, mm in outside(design, envs) if m in modules]


def _apply_layout(modules: list[Record], envelopes: Record, interfaces: list[Record]) -> list[Record]:
    """The modules with the packager's new envelopes (module_id -> boxes) and interfaces (each put into both
    modules it is between, replacing one of the same id)."""
    out = copy.deepcopy(modules)
    by_id = {m["module_id"]: m for m in out}
    for m, boxes in envelopes.items():
        by_id[m]["envelope"] = copy.deepcopy(boxes)
    for itf in interfaces:
        for m in itf["between"]:
            if m in by_id:
                kept = [i for i in by_id[m].get("interfaces") or [] if i["id"] != itf["id"]]
                by_id[m]["interfaces"] = kept + [copy.deepcopy(itf)]
    return out


def build_version(ctx: RunContext, args: dict[str, Any], agent: str | None = None) -> Record:
    """The packager builds the next version of a design with modules (HK6) from every pending proposal based on
    the current version (store.pending_proposals) and its own `changes` (package variables only), `envelopes` and
    `interfaces`, through the same regenerate path as revise_design, as one round of round_limit. Refused while
    two proposals (or a proposal and the packager's changes) change the same variable or set the same choice, and
    when a proposing module's parts or members (every module's, when the packager changed package variables or
    interfaces; the module's, when its envelope changed) would leave its envelope. The new design's module_changes
    lists every proposal used and, when the build moved package variables, one module_change of the packager's
    (module package, agent packager) whose changes hold every package variable the build moved. Returns what
    revise_design returns for a new version, with module_changes (their ids)."""
    _check_input("build_version", args)
    agent = agent or "packager"
    mods = rules.design_modules(ctx)
    if mods is None:
        raise ToolError("build_version は、モジュールに分かれた設計だけで使います。この設計は設計役が revise_design で直します")
    _gate(ctx, "build_version", args, agent)
    if agent != "packager":  # _gate's HK6 denies this; kept so a skipped check cannot pass
        raise ToolError("次の版を作る build_version はパッケージ役 (packager) だけが使えます")
    reason = args["reason"].strip()
    if not reason:
        raise ToolError("reason に、その回の提案をどうまとめたか (レイアウトを変えたなら、その理由も) を書いてください")
    _round_and_cost_left(ctx)
    v = ctx.version
    design = ctx.store.design()
    pending = ctx.store.pending_proposals(v)
    own = args.get("changes") or {}
    envelopes = args.get("envelopes") or {}
    interfaces = list(args.get("interfaces") or [])
    regrow = list(args.get("regrow") or [])  # contract 6f56356: spaces the code makes again around the parts
    problems = [f"モジュール {m} はこの設計にありません ({', '.join(mods)})" for m in [*envelopes, *regrow] if m not in mods]
    problems += [f"{m} は envelopes と regrow の両方にあります (どちらか 1 つにしてください)" for m in regrow if m in envelopes]
    problems += ["package には空間がありません (regrow に入れられません)" for m in regrow if m == "package"]
    problems += [f"取付点 {i['id']} のモジュール {m} はこの設計にありません" for i in interfaces for m in i["between"] if m not in mods]
    if problems:
        raise ToolError("空間・取付点の指定が合いません: " + "; ".join(problems))
    # the same variable or choice from two sources
    who: dict[tuple[str, str], list[str]] = {}
    for c in pending:
        for k in c.get("changes") or {}:
            who.setdefault(("変数", k), []).append(c["change_id"])
        for k in c.get("choices") or {}:
            who.setdefault(("近道", k), []).append(c["change_id"])
    for k in own:
        who.setdefault(("変数", k), []).append("パッケージ役の changes")
    clash = [f"{word} {k}: {' と '.join(ids)}" for (word, k), ids in who.items() if len(ids) > 1]
    if clash:
        raise ToolError("同じものを 2 つの提案が変えているので、版を作れません: " + "; ".join(clash)
                        + "。どちらにするかを決め、要らない方の設計役に出し直してもらうか、まとめ役に伝えてください")
    merged_changes: Record = {}
    merged_choices: Record = {}
    for c in pending:
        merged_changes.update(c.get("changes") or {})
        merged_choices.update(c.get("choices") or {})
    merged_changes.update(own)
    spec, revisable, changes = _checked_changes(ctx, design, merged_changes, merged_choices)
    new = _regenerate(ctx, design, {**changes, **merged_choices}, "build_version")
    fresh = copy.deepcopy(new.get("modules") or design["modules"])
    grown = {m["module_id"]: copy.deepcopy(m.get("envelope") or []) for m in fresh}  # made around the new parts
    envelopes = {**{m: grown.get(m, []) for m in regrow}, **envelopes}
    if not own:  # no layout change: every module keeps the previous version's space and mounts (designgen.modules)
        before = {m["module_id"]: m for m in design["modules"]}
        for m in fresh:
            if m["module_id"] in before:
                m["envelope"] = copy.deepcopy(before[m["module_id"]].get("envelope") or [])
                m["interfaces"] = copy.deepcopy(before[m["module_id"]].get("interfaces") or [])
    new["modules"] = _apply_layout(fresh, envelopes, interfaces)
    moved, after = _moved(ctx, spec, revisable, new, changes)
    package_vars = set((mods.get("package") or {}).get("variables") or [])
    own_moved = {k: (dict(changes[k]) if k in own else {"value": after[k]["value"], "unit": after[k]["unit"]})
                 for k in moved if k in package_vars}
    stray = [k for k in own_moved if k not in own]
    if stray:  # a module's choice moved a layout variable: only the packager changes those
        raise ToolError(f"提案の近道がレイアウトの変数 ({', '.join(stray)}) を動かします。レイアウトの変数を変えるのはパッケージ役だけなので、"
                        f"changes にその値を書いて変えるか、近道を出した設計役に別の値を頼んでください")
    # a module's change can move another module's parts (a steering proposal moves the frame's dash posts and
    # column bracket): those modules are checked too
    touched = list(dict.fromkeys([c["module_id"] for c in pending] + list(envelopes)
                                 + _modules_moved(design, new)))
    if own or interfaces:  # the layout moved: every module's parts may have moved with it
        touched = [m for m in mods if m != "package"]
    breaches = _outside_envelopes(ctx, new, touched)
    if breaches:
        by_module: dict[str, list[str]] = {}
        for c in pending:
            by_module.setdefault(c["module_id"], []).append(c["change_id"])
        lines = [f"{contract.MODULE_NAMES_JA.get(m, m)} ({m}) の {pid} が空間から {far:.1f} mm はみ出す"
                 + (f" (提案 {', '.join(by_module[m])})" if m in by_module else "") for m, pid, far in breaches]
        # run 160725: an occupant proposal moved the frame's rail pieces; the packager found regrow frame on its
        # second call. A module with no proposal of its own moved because of another's: name it
        carried = [m for m in dict.fromkeys(m for m, _, _ in breaches) if m not in by_module and m not in regrow]
        hint = (f"。{'・'.join(carried)} には自分の提案が無く、ほかのモジュールの変更で部品が動いたので、"
                f"regrow に {'・'.join(carried)} を足して呼び直すのが近道です (空間を動いた部品に合わせる)" if carried else "")
        raise ToolError("部品がモジュールの空間 (envelope) からはみ出すので、版を作れません: " + "; ".join(lines) + hint
                        + "。ほかには、その設計役に収まる値で出し直してもらうか、envelopes で空間を配り直してください")
    used = [copy.deepcopy(c) for c in pending]
    if own_moved:
        used.append({"change_id": ctx.store.next_id("module_change"), "module_id": "package", "agent_id": "packager",
                     "reason": reason, "design_version": v, "changes": own_moved})
    ids = [c["change_id"] for c in pending]
    parts = [f"その回の提案をまとめた ({', '.join(ids) if ids else 'なし'})", reason]
    if regrow:
        parts.append("空間を部品の形から作り直した: " + "、".join(regrow))
    if [m for m in envelopes if m not in regrow]:
        parts.append("空間を配り直した: " + "、".join(m for m in envelopes if m not in regrow))
    if interfaces:
        parts.append("取付点を変えた: " + "、".join(i["id"] for i in interfaces))
    change_reason = "。".join(parts)
    new.update(design_version=v + 1, parent_version=v, change_reason=change_reason, module_changes=used)
    _new_version(ctx, new, agent)
    out = _version_summary(ctx, v, change_reason, spec, moved, after, changes, merged_choices)
    out["module_changes"] = [c["change_id"] for c in used]
    return out


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
        raise ToolError(f"{name} の code が空です。ツールの Python の文を書いてください")
    return args["code"]


def _made_answer(made: Any) -> Record:
    return {"name": made.name, "usable": made.usable, "checks": made.checks, "problems": made.problems,
            "printed": _printed(made.printed)}


PRINTED_HEAD, PRINTED_TAIL = 1500, 2500  # chars of a long print kept from its start and its end


def _printed(text: str) -> str:
    if len(text) <= PRINTED_HEAD + PRINTED_TAIL:
        return text
    cut = len(text) - PRINTED_HEAD - PRINTED_TAIL
    return text[:PRINTED_HEAD] + f"\n... ({cut} 文字を省いた) ...\n" + text[-PRINTED_TAIL:]


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
        out.append("spec の formulas が空です。このツールの結果を予測する K0 の式の formula_id (list_formulas) を "
                   "formulas = [...] に書いてください。判定には手計算の予測が要ります")
    else:
        known = {f["formula_id"] for f in ctx.formulas.list_formulas()}
        unknown = [f for f in formulas if f not in known]
        if unknown:
            out.append(f"formulas の {', '.join(unknown)} は式の一覧にありません。list_formulas にある formula_id を書いてください")
    good = [c for c in made.checks if isinstance(c.get("expected"), (int, float)) and c["expected"] != 0
            and _names_reference(str(c.get("source") or ""), spec)]
    if not good:
        out.append("答えの分かっている例に、expected が 0 でなく、source がツールの外の出典 (閉じた式・教科書・規格、"
                   "例: \"closed form sigma = F L / 4 (h / 2) / I, worked out by hand\") を名指しするものがありません。"
                   "そういう例を known_answers に少なくとも 1 つ足してください (ツール自身の式の値は出典になりません)")
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

    out = tool_template()
    if (args or {}).get("api_of") is not None:
        out["api_of"] = _api_of(args["api_of"])
    return out


def _api_of(names: Any) -> Record:
    from hiraku.tools.tool_template import api_doc

    try:
        return api_doc(names)
    except ValueError as e:
        raise ToolError(str(e)) from None


def check_tool(ctx: RunContext, args: dict[str, Any], agent: str | None = None) -> Record:
    """Check a tool's code on its own known answers in a separate process, without shelving it; the answer
    has the API a made tool may use (tool_template.allowed_api, read from the toolmaker's allow list)."""
    from hiraku.solvers import toolmaker
    from hiraku.tools.tool_template import RULE, allowed_api

    api_of = args.get("api_of")
    args = {k: v for k, v in args.items() if k != "api_of"}  # tool_input.check_tool has no api_of; the tool takes it
    code = _check_code_input("check_tool", args)
    out = _made_answer(_apply_refusals(ctx, toolmaker.make_tool(code, taken=tuple(ctx.tools))))
    out = {**out, "rule": RULE, "api": allowed_api()}
    if api_of is not None:
        out["api_of"] = _api_of(api_of)
    return out


UNNAMED_TOOL = "unnamed"
UNKNOWN_TOOL_TITLE = "形の分からないツール (spec() が契約の tool_spec に合わない)"


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
        raise ToolError("ツールを作るのは CAE の役 (とまとめ役) です。設計役は設計の変数を変えます")
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
    ctx.store.emit("tool.checked", agent=agent, domain=spec["domain"], text=f"ツール {spec['name']} を作って確かめました: {ok}",
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
    "build_version": build_version,
    "propose_design": propose_design,
    "write_design": write_design,
    "check_design": check_design,
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


# The intake department's MCP tools; their functions are in hiraku.intake.tools.TOOLS (mcp.py registers them;
# find_tradeoffs reads the sensitivities hiraku.tools.tradeoff_data collects).
INTAKE_TOOLS = ("pick_domains", "ask_user", "import_knowledge", "rule_check", "find_tradeoffs")
# The MCP tools of a run: the contract's MVP set, the designer's (revise_design, propose_design, and for a design
# Claude writes write_design and check_design), tool making (with its template), the report and the intake tools.
RUN_TOOLS = (*contract.MCP_TOOLS_MVP, *contract.MCP_TOOLS_DESIGNER, *contract.MCP_TOOLS_PACKAGER, "propose_design",
             "write_design", "check_design", "make_tool", "check_tool", "get_tool_template", "write_report", *INTAKE_TOOLS)
