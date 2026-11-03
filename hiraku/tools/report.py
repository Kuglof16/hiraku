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


def build_report(ctx: RunContext, args: Record, agent: str) -> Record:
    """The report record (contract `report`). args: summary (text), next [{domain, change, reason}],
    and optional sensitivity [{load_case_id, paths}] (design paths as hiraku.solvers.k5_sensitivity
    names them; not in the contract's tool input yet)."""
    if not ctx.version:
        raise ToolError("設計の版がまだ無いので、報告は書けません")
    sens = sensitivities(ctx, args.get("sensitivity") or [])
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
