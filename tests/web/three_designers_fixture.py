"""A sample record with the three design roles (contract dabec03f: designer.structure, designer.mechanism,
designer.electric; DESIGNER_ROLES_JA), for the screen's tests and pictures until a real run is recorded.

It is tests.web.modules_fixture's record with, by hand, the shape the design department's runs write
(claude/design-three-designers 2d746757, hiraku/agent/roles.py module_designer_agents and module_agent):
- each module's `owner` in design.modules is one of the three roles (as docs/contract.md says for the car:
  frame, body and occupant structure; suspension and steering mechanism; powertrain electric); package
  stays the packager's;
- project.agents has one entry per role that owns a module, without `module` (a role owns several);
- every proposal's agent_id and every event's agent is the owner of its module.

Shape example only: nothing in it is a design or a calculation.
"""

from __future__ import annotations

from tests.web.modules_fixture import module_events

RUN_ID = "20261106-120000-three"
ROLE_OF_MODULE = {
    "frame": "designer.structure", "body": "designer.structure", "occupant": "designer.structure",
    "suspension": "designer.mechanism", "steering": "designer.mechanism",
    "powertrain": "designer.electric",
}
ROLES = ("designer.structure", "designer.mechanism", "designer.electric")  # the contract's order


def owner(module_id: str) -> str:
    return "packager" if module_id == "package" else ROLE_OF_MODULE[module_id]


def three_events() -> list[dict]:
    """The module record with the three roles in place of the six designer.<module>."""
    events = module_events()
    legacy = {f"designer.{m}": r for m, r in ROLE_OF_MODULE.items()}
    for e in events:
        e["run_id"] = RUN_ID
        d = e.get("data")
        if e["kind"] == "run.started":
            d["run_id"] = RUN_ID
            agents = [a for a in d["agents"] if not a["agent_id"].startswith("designer.")]
            at = next(i for i, a in enumerate(agents) if a["agent_id"] == "packager") + 1
            agents[at:at] = [{"agent_id": r, "role": "designer", "prop": "pencil"} for r in ROLES]
            d["agents"] = agents
        if e.get("agent") in legacy:
            e["agent"] = legacy[e["agent"]]
        if e["kind"] == "agent.started" and d.get("agent_id") in legacy:
            d["agent_id"] = legacy[d["agent_id"]]
        if e["kind"] == "design.change_proposed":
            d["agent_id"] = owner(d["module_id"])
        if e["kind"] == "design.created":
            for m in d.get("modules", []):
                m["owner"] = owner(m["module_id"])
            for c in d.get("module_changes", []):
                c["agent_id"] = owner(c["module_id"])
    return events
