"""Assemble the project fields of a run: picked domains, load cases, agents."""

from __future__ import annotations

from typing import Any

from hiraku import contract
from hiraku.catalog.loader import Catalog

Record = dict[str, Any]


def judged_domains(load_cases: list[Record]) -> list[str]:
    """Domains that have at least one load case with a criterion, in contract order."""
    have = {lc["domain"] for lc in load_cases if "criterion" in lc}
    return [d for d in contract.DOMAINS if d in have]


def fixed_domain_picks(catalog: Catalog, load_cases: list[Record]) -> list[Record]:
    """Domain picks made by code from the tests in the requirements file (MVP, no pick_domains)."""
    picks = []
    for d in judged_domains(load_cases):
        tests = [lc for lc in load_cases if lc["domain"] == d and "criterion" in lc]
        names = "・".join(f"{lc['test_id']} {catalog.test(lc['test_id'])['title']}" for lc in tests)
        picks.append({
            "domain": d,
            "shown_name": contract.DOMAIN_NAMES_JA[d],
            "reason": f"要件のファイルが試験 {names} を選んだ",
        })
    return picks


def owner_agent(load_cases: list[Record], picked: list[str], load_case_id: str) -> str:
    """The CAE role that works on a load case.

    The role of its own domain when that domain is picked; otherwise the role
    of a load case it feeds (P1 feeds R1, so cae.strength does P1 too).
    """
    by_id = {lc["load_case_id"]: lc for lc in load_cases}
    lc = by_id[load_case_id]
    if lc["domain"] in picked and lc["domain"] != "assembly":
        return f"cae.{lc['domain']}"
    for target in lc.get("feeds", []):
        return owner_agent(load_cases, picked, target)
    return "lead" if lc["domain"] == "assembly" else f"cae.{lc['domain']}"


def agents_for(catalog: Catalog, load_cases: list[Record], picked: list[str]) -> list[Record]:
    out = [{"agent_id": "lead", "role": "lead", "prop": catalog.prop("lead")}]
    roles = []
    for lc in load_cases:
        a = owner_agent(load_cases, picked, lc["load_case_id"])
        if a.startswith("cae.") and a not in roles:
            roles.append(a)
    for a in roles:
        out.append({"agent_id": a, "role": "cae", "domain": a.split(".", 1)[1], "prop": catalog.prop(a)})
    return out


def project_fields(catalog: Catalog, req: Record, load_cases: list[Record]) -> Record:
    """Everything RunStore.create needs (it adds run_id, created_at, status, cost_usd)."""
    picks = fixed_domain_picks(catalog, load_cases)
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
