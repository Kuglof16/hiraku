"""Roles as SDK subagents, and the mapping between SDK agent names and contract agent ids.

Contract ids use a dot (cae.strength); SDK agent names use a hyphen (cae-strength).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hiraku import contract
from hiraku.agent import prompts
from hiraku.catalog import REPO_ROOT
from hiraku.tools.context import RunContext

Record = dict[str, Any]

CAE_TOOLS = ("get_context", "list_formulas", "list_tools", "record_prediction", "analyze", "compare", "record_cause",
             "check_tool", "make_tool")
PACKAGER_TOOLS = (*CAE_TOOLS, "rule_check")  # the knowledge rules are checked on the parts by the packager
DESIGNER_TOOLS = ("get_context", "list_tools", "propose_design", "revise_design")
# Effort per role: the CAE roles and the packager follow a fixed procedure (predict, analyze,
# compare); the designer chooses changes, like the lead (whose effort is the run's).
CAE_EFFORT = "medium"
DESIGNER_EFFORT = "high"

PLUGIN_NAME = "hiraku"
PLUGIN_DIR = REPO_ROOT / "plugins" / PLUGIN_NAME


def plugin_skills(plugin_dir: Path = PLUGIN_DIR) -> list[str]:
    """Skill names ("<plugin>:<skill>") of the local plugin, one per skills/<name>/SKILL.md."""
    return sorted(f"{PLUGIN_NAME}:{p.parent.name}" for p in (plugin_dir / "skills").glob("*/SKILL.md"))


def sdk_name(agent_id: str) -> str:
    return agent_id.replace(".", "-")


def agent_id_from_sdk(agent_type: str | None) -> str | None:
    """Contract agent id for an SDK agent name, or None for agents HIRAKU did not define."""
    if not agent_type:
        return None
    # Claude Code names a plugin's agents "<plugin>:<name>" (plugins/hiraku/agents/<name>.md)
    agent_type = agent_type.removeprefix(f"{PLUGIN_NAME}:")
    if agent_type.startswith("cae-") and agent_type[4:] in contract.DOMAINS and agent_type[4:] != "assembly":
        return "cae." + agent_type[4:]
    if agent_type in ("designer", "packager"):
        return agent_type
    return None


def possible_roles(ctx: RunContext) -> list[Record]:
    """The roles of project.agents; before the first version (no load cases yet) also every
    role the product's catalog tests could need, since the SDK takes its subagents at the start."""
    agents = list(ctx.project["agents"])
    if ctx.load_cases:
        return agents
    from hiraku.catalog import load_catalog

    catalog = load_catalog()
    have = {a["agent_id"] for a in agents}
    for tid in catalog.select_tests(ctx.project["flags"], ctx.project.get("speed_class")):
        domain = catalog.test(tid)["domain"]
        a = ({"agent_id": "packager", "role": "packager"} if domain == "assembly"
             else {"agent_id": f"cae.{domain}", "role": "cae", "domain": domain})
        if a["agent_id"] not in have:
            agents.append(a)
            have.add(a["agent_id"])
    return agents


def subagents(ctx: RunContext) -> dict:
    """AgentDefinition per role of this run (possible_roles) but the lead: CAE roles,
    the packager (the CAE steps on the assembly load cases) and the designer. All run in
    the foreground (background=False), so the lead waits for their results."""
    from claude_agent_sdk import AgentDefinition

    out = {}
    for a in possible_roles(ctx):
        if a["role"] in ("cae", "packager"):
            domain = a.get("domain", "assembly")
            out[sdk_name(a["agent_id"])] = AgentDefinition(
                description=prompts.cae_description(domain),
                prompt=prompts.cae_prompt(domain),
                tools=[contract.mcp_name(t) for t in (PACKAGER_TOOLS if a["role"] == "packager" else CAE_TOOLS)],
                skills=plugin_skills(),
                model="inherit",
                effort=CAE_EFFORT,
                background=False,
            )
        elif a["role"] == "designer":
            out["designer"] = AgentDefinition(
                description=prompts.DESIGNER_DESCRIPTION,
                prompt=prompts.DESIGNER_PROMPT,
                tools=[contract.mcp_name(t) for t in DESIGNER_TOOLS],
                model="inherit",
                effort=DESIGNER_EFFORT,
                background=False,
            )
    return out
