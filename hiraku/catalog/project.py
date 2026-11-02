"""Assemble the project fields of a run: picked domains, load cases, agents."""

from __future__ import annotations

from typing import Any

from hiraku import contract
from hiraku.catalog.build import NO_BUILDER, CatalogError, build_load_cases
from hiraku.catalog.loader import Catalog

Record = dict[str, Any]


def build_tests(catalog: Catalog, req: Record, design: Record) -> tuple[list[Record], list[tuple[str, str]] | None]:
    """(load cases, tests left out): the tests the requirements file lists (each must build; none
    left out), else every catalog test that applies to the product's flags and can be built."""
    skipped: list[tuple[str, str]] | None = None
    if req.get("tests"):
        test_ids = req["tests"]
    else:
        test_ids, skipped = catalog.select_tests(req["flags"], req.get("speed_class")), []
    load_cases = build_load_cases(catalog, req, design, test_ids, skipped)
    if not load_cases:
        raise CatalogError("no test could be built for this product: "
                           + "; ".join(f"{t}: {why}" for t, why in skipped or []))
    return load_cases, skipped


def skipped_text(catalog: Catalog, skipped: list[tuple[str, str]]) -> str:
    """One line for the screen: the tests this run leaves out, those without a builder grouped."""
    name = lambda t: f"{t} {catalog.test(t)['title']}"  # noqa: E731
    none = [t for t, why in skipped if why.endswith(NO_BUILDER)]
    other = [f"{name(t)} ({why})" for t, why in skipped if not why.endswith(NO_BUILDER)]
    parts = []
    if none:
        parts.append("荷重条件の作り方がまだ無い: " + "、".join(name(t) for t in none))
    if other:
        parts.append("要件か設計が足りない: " + "、".join(other))
    return "この実行で見ない試験 — " + " / ".join(parts)


def judged_domains(load_cases: list[Record]) -> list[str]:
    """Domains that have at least one load case with a criterion, in contract order."""
    have = {lc["domain"] for lc in load_cases if "criterion" in lc}
    return [d for d in contract.DOMAINS if d in have]


def fixed_domain_picks(catalog: Catalog, load_cases: list[Record], from_file: bool = True) -> list[Record]:
    """Domain picks made by code from the load cases' tests (no pick_domains yet).

    from_file: the requirements file listed the tests; otherwise the catalog
    chose them from the product's flags.
    """
    picks = []
    for d in judged_domains(load_cases):
        tests = [lc for lc in load_cases if lc["domain"] == d and "criterion" in lc]
        names = "・".join(f"{lc['test_id']} {catalog.test(lc['test_id'])['title']}" for lc in tests)
        why = "要件のファイルが試験" if from_file else "製品の性質 (flags) からカタログが試験"
        picks.append({
            "domain": d,
            "shown_name": contract.DOMAIN_NAMES_JA[d],
            "reason": f"{why} {names} を選んだ",
        })
    return picks


def owner_agent(load_cases: list[Record], picked: list[str], load_case_id: str) -> str:
    """The role that works on a load case.

    Assembly load cases belong to the packager. Otherwise the CAE role of its
    own domain when that domain is picked, else the role of a load case it
    feeds (P1 feeds R1, so cae.strength does P1 when powertrain is not picked).
    """
    by_id = {lc["load_case_id"]: lc for lc in load_cases}
    lc = by_id[load_case_id]
    if lc["domain"] == "assembly":
        return "packager"
    if lc["domain"] in picked:
        return f"cae.{lc['domain']}"
    for target in lc.get("feeds", []):
        return owner_agent(load_cases, picked, target)
    return f"cae.{lc['domain']}"


def agents_for(catalog: Catalog, load_cases: list[Record], picked: list[str]) -> list[Record]:
    """lead, the designer (revise_design), the packager when there are assembly load cases, and a CAE role per domain."""
    out = [{"agent_id": "lead", "role": "lead", "prop": catalog.prop("lead")},
           {"agent_id": "designer", "role": "designer", "prop": catalog.prop("designer")}]
    roles = []
    for lc in load_cases:
        a = owner_agent(load_cases, picked, lc["load_case_id"])
        if a not in roles:
            roles.append(a)
    if "packager" in roles:
        out.append({"agent_id": "packager", "role": "packager", "prop": catalog.prop("packager")})
    for a in roles:
        if a.startswith("cae."):
            out.append({"agent_id": a, "role": "cae", "domain": a.split(".", 1)[1], "prop": catalog.prop(a)})
    return out


def project_fields(catalog: Catalog, req: Record, load_cases: list[Record]) -> Record:
    """Everything RunStore.create needs (it adds run_id, created_at, status, cost_usd)."""
    picks = fixed_domain_picks(catalog, load_cases, from_file=bool(req.get("tests")))
    picked = [p["domain"] for p in picks]
    fields: Record = {
        "want": req["want"],
        "product": req["product"],
        "flags": req["flags"],
        "round_limit": req.get("round_limit", 1),
        "cost_limit_usd": req.get("cost_limit_usd", 2.0),
        "requirements": req["requirements"],
        "domain_picks": picks,
        "load_cases": load_cases,
        "agents": agents_for(catalog, load_cases, picked),
    }
    if "speed_class" in req:
        fields["speed_class"] = req["speed_class"]
    return fields
