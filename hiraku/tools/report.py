"""The run's report (N17), put together by code from the records.

Per picked domain: the tile, each verdict in one line, the terms that weigh most
in the analysis behind it (its effects), the assumptions of the prediction it
was compared with, the mismatches on the way with their causes, and, where
asked, the sensitivity of the verdict's quantity to design numbers (K5, which
runs the tool again). Then the values the run used without having checked them
(requirements and materials whose basis says so). The author (Claude) writes
only the summary and what to change next, and chooses which sensitivities to
compute; every number with a unit in those sentences must be one of the numbers
recorded_numbers allows (the requirements, the load cases, the current design
version's scalar numbers, the predictions, analyses, comparisons, verdicts,
judgements and the sensitivities computed), at the precision written, or the
report is refused.
"""

from __future__ import annotations

import re
from typing import Any

from hiraku import contract
from hiraku.tools.context import RunContext, ToolError
from hiraku.tools.judge import verdict_line

Record = dict[str, Any]

TOP_EFFECTS = 3
# a number followed by a unit (or %): what a sentence claims about the vehicle
_UNITS = sorted((u for u in contract.UNITS if u not in ("1",)), key=len, reverse=True)
_NUMBER_WITH_UNIT = re.compile(r"(?<![\w.-])(-?\d+(?:\.\d+)?)\s*(" + "|".join(re.escape(u) for u in _UNITS) + r"|%)(?![\w/^])")


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


# keys whose number is a fraction, cited as %
_PERCENT_OF = {"diff_rel": 100.0, "tolerance_rel": 100.0, "share": 100.0, "margin_pct": 1.0}
# design keys that carry their unit in the name (density_kg_m3, radius_mm, E_MPa ...)
_KEY_UNITS = (("_kg_m3", "kg/m^3"), ("_N_per_mm", "N/mm"), ("_MPa", "MPa"), ("_mm", "mm"), ("_kg", "kg"))


def _quantities(node: Any, lists: bool = True) -> list[tuple[float, str]]:
    """Every number of a record with its unit: quantities {value, unit}, fractions as %, numbers
    whose key names their unit (position_mm, mass_kg ...). With lists false, a list of numbers
    under such a key (a coordinate such as position_mm [x, y, z]) is left out. Numbers whose unit
    is not known are left out."""
    out: list[tuple[float, str]] = []
    if isinstance(node, dict):
        if _is_num(node.get("value")) and isinstance(node.get("unit"), str):
            out.append((float(node["value"]), node["unit"]))
        for k, v in node.items():
            if k in _PERCENT_OF and _is_num(v):
                out.append((float(v) * _PERCENT_OF[k], "%"))
            elif _is_num(v):
                unit = next((u for suffix, u in _KEY_UNITS if k.endswith(suffix)), None)
                if unit:
                    out.append((float(v), unit))
            elif isinstance(v, list) and v and all(_is_num(x) for x in v):
                unit = next((u for suffix, u in _KEY_UNITS if k.endswith(suffix)), None)
                out += [(float(x), unit) for x in v] if unit and lists else []
            else:
                out += _quantities(v, lists)
    elif isinstance(node, list):
        for v in node:
            out += _quantities(v, lists)
    return out


def recorded_numbers(ctx: RunContext, extra: list[Record] = ()) -> list[tuple[float, str]]:
    """Numbers a report's sentences may cite: the requirements, the load cases, the design of the
    current version (scalars only: a list such as position_mm [x, y, z] gives no number), every
    prediction, analysis, comparison and verdict so far, the judgements, and `extra` (the
    sensitivities computed for the report). Designs of earlier versions give none."""
    s = ctx.store
    found = _quantities(ctx.project["requirements"]) + _quantities(ctx.load_cases)
    if ctx.version:
        found += _quantities(s.design(ctx.version), lists=False)
    for kind in ("prediction", "analysis", "comparison", "verdict"):
        found += _quantities(s.records(kind))
    for j in s.judgements():
        found += _quantities(j)
    return found + _quantities(list(extra))


def unknown_numbers(text: str, allowed: list[tuple[float, str]]) -> list[str]:
    """Numbers with a unit in `text` that match no allowed value (recorded_numbers) of that unit
    at the precision written: within half a unit of the last digit written or 0.5 % of the
    number, whichever is larger (a sign may be left out: 20.7 % for a diff_rel of -0.207)."""
    bad = []
    for m in _NUMBER_WITH_UNIT.finditer(text):
        written, unit = m.group(1), m.group(2)
        x = float(written)
        decimals = len(written.split(".")[1]) if "." in written else 0
        tol = max(0.5 * 10 ** -decimals, 0.005 * abs(x))
        if not any(u == unit and abs(abs(a) - abs(x)) <= tol for a, u in allowed):
            bad.append(m.group(0))
    return bad


def _effects(analysis: Record | None) -> list[Record]:
    terms = [t for t in (analysis or {}).get("effects", []) if "share" in t]
    return sorted(terms, key=lambda t: -abs(t["share"]))[:TOP_EFFECTS]


def unchecked(ctx: RunContext) -> list[Record]:
    """Values the run used without having checked them: requirements whose basis says
    "not checked" or "assumption", and the design's materials not verified."""
    out = []
    for r in ctx.project["requirements"]:
        basis = str(r.get("basis", ""))
        if "not checked" in basis or "assumption" in basis or "確かめていない" in basis:
            out.append({"what": f"{r['key']} {r.get('name', '')}".strip(), "value": r.get("value"),
                        "unit": r.get("unit"), "basis": basis})
    design = ctx.store.design() if ctx.version else {}
    for key, m in (design.get("materials") or {}).items():
        if not m.get("verified", False):
            out.append({"what": f"material {key}", "basis": str(m.get("source", ""))})
    return out


def domain_parts(ctx: RunContext) -> list[Record]:
    """Per picked domain of the current version: the tile and, per verdict, what lies behind it."""
    s, v = ctx.store, ctx.version
    tiles = {j["domain"]: j for j in s.judgements()}
    verdicts = s.records("verdict", v)
    comparisons = s.records("comparison", v)
    causes = {c["comparison_id"]: c for c in s.records("cause")}
    out = []
    for domain in ctx.picked:
        tile = tiles.get(domain, {})
        cases = []
        for lc in ctx.load_cases:
            if lc["domain"] != domain or "criterion" not in lc:
                continue
            ver = next((x for x in reversed(verdicts) if x["load_case_id"] == lc["load_case_id"]), None)
            cmp = s.get("comparison", ver["comparison_id"]) if ver else None
            pred = ctx.prediction(cmp["prediction_id"]) if cmp else None
            ana = s.get("analysis", cmp["analysis_id"]) if cmp else None
            mismatches = [c for c in comparisons if c["status"] == "mismatch"
                          and (ctx.prediction(c["prediction_id"]) or {}).get("load_case_id") == lc["load_case_id"]]
            cases.append({
                "load_case_id": lc["load_case_id"],
                "verdict_id": ver["verdict_id"] if ver else None,
                "line": verdict_line(ver) if ver else "まだ判定していない",
                "effects": _effects(ana),
                "assumptions": list((pred or {}).get("assumptions", [])),
                "differences": [{"comparison_id": c["comparison_id"], "quantity": c["quantity"], "diff_rel": c["diff_rel"],
                                 "cause_id": c.get("cause_id"),
                                 "cause": (causes.get(c["comparison_id"]) or {}).get("cause")} for c in mismatches],
            })
        out.append({"domain": domain, "state": tile.get("state", "pending"), "summary": tile.get("summary", ""),
                    "cases": cases})
    return out


def sensitivities(ctx: RunContext, asked: list[Record]) -> list[Record]:
    """K5 for the (load case, design paths) pairs asked: the elasticity of the verdict's quantity.
    The tool is the one of the analysis behind the load case's verdict (current version); the
    upstream results are the trusted analyses analyze itself passes (functions.trusted_analysis)."""
    from hiraku.solvers.k5_sensitivity import sensitivity  # runs the tool again, so only on request
    from hiraku.tools.functions import trusted_analysis
    from hiraku.tools.judge import latest_verdicts

    out = []
    verdicts = latest_verdicts(ctx, ctx.version)
    for a in asked:
        lc = ctx.load_case(a["load_case_id"])
        crit = lc.get("criterion")
        if crit is None:
            raise ToolError(f"{lc['load_case_id']} には合否の基準が無いので、感度を出す量がありません")
        ver = verdicts.get(lc["load_case_id"])
        cmp = ctx.store.get("comparison", ver["comparison_id"]) if ver else None
        ana = ctx.store.get("analysis", cmp["analysis_id"]) if cmp else None
        if ana is None or ana["tool"] not in ctx.tools:
            raise ToolError(f"{lc['load_case_id']} には版 {ctx.version} の合否がまだ無いので、感度は出せません "
                            f"(感度は合否の元になった解析の道具で出します)")
        upstream = {}
        for u in ctx.upstream_of(lc["load_case_id"]):
            got = trusted_analysis(ctx, u["load_case_id"])
            if got is not None:
                upstream[u["load_case_id"]] = got
        try:
            terms = sensitivity(ctx.tools[ana["tool"]], ctx.store.design(), lc, crit["quantity"], list(a["paths"]),
                                params={"upstream_analyses": upstream})
        except (KeyError, ValueError) as e:
            raise ToolError(f"{lc['load_case_id']} の感度を出せません: {e}") from None
        out.append({"load_case_id": lc["load_case_id"], "quantity": crit["quantity"], "terms": terms})
    return out


def _domain_record(part: Record, sens: list[Record]) -> Record:
    """One entry of the report's domains, in the contract's shape."""
    cases = part["cases"]
    lines = [c["line"] for c in cases]
    out: Record = {
        "domain": part["domain"],
        "state": part["state"],
        "line": " / ".join(lines) if lines else (part["summary"] or "判定の基準のある試験がありません"),
        "verdict_ids": [c["verdict_id"] for c in cases if c["verdict_id"]],
        "effects": [t for c in cases for t in c["effects"]],
        "assumptions": list(dict.fromkeys(a for c in cases for a in c["assumptions"])),
        "differences": [{"comparison_id": d["comparison_id"], "diff_rel": d["diff_rel"], "cause_id": d["cause_id"]}
                        for c in cases for d in c["differences"]],
    }
    ids = {c["load_case_id"] for c in cases}
    terms = [t for s in sens if s["load_case_id"] in ids for t in s["terms"]]
    if terms:
        out["sensitivity"] = terms
    return out


def rule_hits(ctx: RunContext) -> list[str]:
    """Rule ids whose latest rule_check of the current version has hits (a next item answering one
    may name a domain the run did not pick)."""
    latest: dict[str, Record] = {}
    for rc in ctx.store.records("rule_check", ctx.version):
        latest[rc["rule_id"]] = rc
    return [r for r, rc in latest.items() if rc["hits"]]


def _report_before_a_design(ctx: RunContext, args: Record, agent: str) -> Record:
    """design_version 0: why the run stopped before its first version, in the summary and issues."""
    if args.get("next") or args.get("sensitivity"):
        raise ToolError("設計の版がまだ無いので、next と sensitivity は書けません。summary と issues だけで報告してください")
    bad = sorted(set(unknown_numbers(args.get("summary", ""), recorded_numbers(ctx))))
    if bad:
        raise ToolError("報告の文に、記録に無い数があります: " + "、".join(bad)
                        + "。数は記録 (要件) の値をそのまま引くか、数を書かずに記録の ID で指してください")
    return {
        "design_version": 0,
        "agent": agent,
        "domains": [],
        "unchecked": [{"item": u["what"], "source": u["basis"] or "not given"} for u in unchecked(ctx)],
        "next": [],
        "summary": args.get("summary", "").strip(),
        **({"issues": [dict(i) for i in args["issues"]]} if args.get("issues") else {}),
    }


def build_report(ctx: RunContext, args: Record, agent: str, computed: list[Record] | None = None) -> Record:
    """The report record (contract `report`). args: summary (text), next [{domain, change, reason}],
    and optional sensitivity [{load_case_id, paths}] (design paths as hiraku.solvers.k5_sensitivity
    names them; not in the contract's tool input yet). computed: sensitivities already computed
    by sensitivities() (the scripted driver's next items, scripted_next), added to those asked.
    Before the first version (a run that stopped there), the report has design_version 0 and only the
    summary and issues: there are no domains, sensitivities or next changes to give."""
    if not ctx.version:
        return _report_before_a_design(ctx, args, agent)
    sens = list(computed or []) + sensitivities(ctx, args.get("sensitivity") or [])
    texts = [args.get("summary", "")] + [f"{n.get('change', '')} {n.get('reason', '')}" for n in args.get("next") or []]
    allowed = recorded_numbers(ctx, sens)
    bad = sorted({b for t in texts for b in unknown_numbers(t, allowed)})
    if bad:
        raise ToolError("報告の文に、記録に無い数があります: " + "、".join(bad)
                        + "。数は記録 (予測・解析・照合・合否・要件・設計) か感度の値をそのまま引くか、数を書かずに記録の ID で指してください")
    parts = domain_parts(ctx)
    domains = {d["domain"] for d in parts}
    hit = rule_hits(ctx)
    not_hit = [n["rule_id"] for n in args.get("next") or [] if n.get("rule_id") and n["rule_id"] not in hit]
    if not_hit:
        raise ToolError(f"next の rule_id {not_hit} は、版 {ctx.version} の最後の rule_check で当たった規則ではありません"
                        f" (当たった規則: {', '.join(hit) or 'なし'})")
    wrong = [n.get("domain") for n in args.get("next") or [] if n.get("domain") not in domains and not n.get("rule_id")]
    if wrong:
        raise ToolError(f"next の domain {wrong} は、この実行で選んだ分野 ({', '.join(sorted(domains))}) ではありません。"
                        f"rule_check で当たった規則に答える変更なら、その規則の rule_id を付けてください")
    return {
        "design_version": ctx.version,
        "agent": agent,
        "domains": [_domain_record(p, sens) for p in parts],
        "unchecked": [{"item": u["what"], "source": u["basis"] or "not given"} for u in unchecked(ctx)],
        "next": [{"domain": n["domain"], "change": n["change"], "reason": n["reason"],
                  **({"rule_id": n["rule_id"]} if n.get("rule_id") else {})} for n in args.get("next") or []],
        "summary": args.get("summary", "").strip(),
        # where HIRAKU itself got in the way of the run, in the lead's words (contract report.issues)
        **({"issues": [dict(i) for i in args["issues"]]} if args.get("issues") else {}),
    }


# ---------------------------------------------------------------------------- the scripted driver's next items

SCRIPT_MARK = "台本: "  # every next item the code writes for a scripted run starts with this
K5_HEAVY_TOOLS = ("frame_shell",)  # re-running these for K5 takes minutes (the shell solve)
K5_MAX_WALL_S = 30.0  # no K5 when the governing analysis took longer than this (wall_time_s)
K5_MAX_PATHS = 3  # design paths per governing verdict
_IN_PARENS = re.compile(r"\(([A-Za-z0-9_.:-]+)\)")


def governing_verdicts(ctx: RunContext, domain: str) -> list[Record]:
    """The newest verdicts of a domain on the current version that govern it: every red one, else
    the one with the smallest margin_pct (verdicts with a null margin_pct, a limit of 0, only when
    none has one: then the smallest margin)."""
    from hiraku.tools.judge import latest_verdicts

    mine = [v for lc_id, v in latest_verdicts(ctx, ctx.version).items()
            if ctx.has_load_case(lc_id) and ctx.load_case(lc_id)["domain"] == domain]
    red = [v for v in mine if not v["pass"]]
    if red:
        return red
    pct = [v for v in mine if v.get("margin_pct") is not None]
    if pct:
        return [min(pct, key=lambda v: v["margin_pct"])]
    return [min(mine, key=lambda v: v["margin"]["value"])] if mine else []


def _verdict_result(analysis: Record, verdict: Record) -> Record | None:
    """The analysis result the verdict judged: its quantity, nearest the verdict's value."""
    hits = [r for r in analysis.get("results", []) if r["quantity"] == verdict["quantity"]]
    return min(hits, key=lambda r: abs(r["value"] - verdict["value"]["value"])) if hits else None


def _top_effect(analysis: Record) -> Record | None:
    terms = [t for t in analysis.get("effects", []) if isinstance(t.get("share"), (int, float))]
    return max(terms, key=lambda t: abs(t["share"])) if terms else None


def candidate_paths(design: Record, analysis: Record, verdict: Record, tool: Any) -> list[str]:
    """Design paths (hiraku.solvers.k5_sensitivity) that may move the verdict's quantity, at most
    K5_MAX_PATHS: for a member named in the judged result's `where` or in the largest effects term,
    its section's wall and height and its material's E; for a part, the design variables named
    after it and its mass; else the design variables the tool's load-case parameters read (K2's
    `var`); else, for a frame tool (K1), the most used section and its material."""
    members = {m["id"]: m for m in design.get("members", [])}
    parts = {p["id"]: p for p in design.get("parts", [])}
    variables = design.get("variables") or {}
    names = []
    res = _verdict_result(analysis, verdict)
    if res and isinstance(res.get("where"), str):
        names.append(res["where"])
    top = _top_effect(analysis)
    if top:
        names += _IN_PARENS.findall(top["name"])
    paths: list[str] = []

    def member_paths(m: Record) -> list[str]:
        dims = (design.get("sections", {}).get(m.get("section")) or {}).get("dims_mm") or {}
        out = [f"sections.{m['section']}.dims_mm.{k}" for k in ("t", "h", "d", "b") if k in dims][:2]
        if "E_MPa" in (design.get("materials", {}).get(m.get("material")) or {}):
            out.append(f"materials.{m['material']}.E_MPa")
        return out

    for name in dict.fromkeys(names):
        if name in members:
            paths += member_paths(members[name])
        elif name in parts:
            paths += [f"variables.{k}" for k in variables if k.startswith(name + "_")]
            if "mass_kg" in parts[name]:
                paths.append(f"parts.{name}.mass_kg")
    if not paths:
        for p in getattr(tool, "params", ()) or ():
            paths += [f"variables.{v}" for v in getattr(p, "var", ()) or () if v in variables]
    if not paths and members and analysis.get("base") == "K1":
        counts: dict[str, int] = {}
        for m in members.values():
            counts[m.get("section")] = counts.get(m.get("section"), 0) + 1
        common = max(counts, key=lambda sec: counts[sec])
        paths += member_paths(next(m for m in members.values() if m.get("section") == common))
    return list(dict.fromkeys(paths))[:K5_MAX_PATHS]


def _num(x: float, unit: str | None = None) -> str:
    text = f"{x:.4g}"
    return text if unit in (None, "1") else f"{text} {unit}"


def _margin_text(v: Record) -> str:
    out = _num(v["margin"]["value"], v["margin"]["unit"])
    return out + (f" ({_num(v['margin_pct'], '%')})" if v.get("margin_pct") is not None else "")


def _raises_margin(op: str, share: float) -> str:
    """大きく / 小さく: which way to move a number of elasticity `share` so that the margin grows."""
    up = (share > 0) == (op in (">=", ">"))
    return "大きく" if up else "小さく"


def scripted_next(ctx: RunContext) -> tuple[list[Record], list[Record]]:
    """(next items, the sensitivities computed for them) for a scripted run's report, by code from
    the records: per picked domain of the current version, each governing verdict (governing_verdicts)
    and the change that moves its quantity most with the direction that raises the margin, from K5
    over candidate_paths on the verdict's load case; where K5 is not run (a heavy tool, a long
    analysis, no candidate path, or no number that moves) the largest-share effects term and its
    member, with no direction. Every item starts with SCRIPT_MARK; its numbers are the records'."""
    from hiraku.solvers.k5_sensitivity import strongest

    s = ctx.store
    items: list[Record] = []
    computed: list[Record] = []
    for domain in ctx.picked:
        for ver in governing_verdicts(ctx, domain):
            cmp = s.get("comparison", ver["comparison_id"])
            ana = s.get("analysis", cmp["analysis_id"])
            tool = ctx.tools.get(ana["tool"])
            wall = float(ana.get("wall_time_s") or 0.0)
            head = (f"{ver['test_id']} {ver['quantity']} の余裕 {_margin_text(ver)} ({ver['verdict_id']}、"
                    f"{ana['analysis_id']} {ana['tool']})")
            why_not = None
            if tool is None:
                why_not = f"K5 は回していない: 道具 {ana['tool']} が棚に無い"
            elif ana["tool"] in K5_HEAVY_TOOLS or wall > K5_MAX_WALL_S:
                why_not = f"K5 は回していない: {ana['tool']} の解き直しが重い (解析に {wall:.0f} 秒)"
            paths = [] if why_not else candidate_paths(s.design(), ana, ver, tool)
            if not why_not and not paths:
                why_not = "K5 は回していない: 動かす設計の数の候補が無い"
            best = None
            if paths:
                try:
                    (got,) = sensitivities(ctx, [{"load_case_id": ver["load_case_id"], "paths": paths}])
                except ToolError as e:
                    why_not = f"K5 を出せない ({e})"
                else:
                    computed.append(got)
                    best = strongest(got["terms"])
                    if best is None or best["share"] == 0:
                        best, why_not = None, f"K5 では候補 ({'、'.join(paths)}) の中に、確かに動く数が無い"
            if best is not None:
                path = best["name"].split(" to ", 1)[1].split(" (", 1)[0]
                items.append({"domain": domain,
                              "change": f"{SCRIPT_MARK}{path} を{_raises_margin(ver['op'], best['share'])}する",
                              "reason": f"{head}。K5: {ver['quantity']} の {path} への弾性 {best['share']:.3g} "
                                        f"(候補 {len(paths)} つの中でいちばん大きい)"})
                continue
            top = _top_effect(ana)
            if top is not None:
                where = "・".join(_IN_PARENS.findall(top["name"])) or top["name"]
                items.append({"domain": domain, "change": f"{SCRIPT_MARK}{where} が効いている (向きは出していない)",
                              "reason": f"{head}。いちばん効いている項は {top['name']} で、割合 "
                                        f"{_num(top['share'] * 100, '%')} ({why_not})"})
            else:
                items.append({"domain": domain, "change": f"{SCRIPT_MARK}変える数を選べない",
                              "reason": f"{head}。{why_not}。解析の効いている項にも割合が無い"})
    allowed = recorded_numbers(ctx, computed)
    for item in items:  # an effects term's own name may carry numbers the checker reads otherwise
        if unknown_numbers(f"{item['change']} {item['reason']}", allowed):
            item["reason"] = re.sub(r"いちばん効いている項は .+? で、", "", item["reason"])
    return items, computed
