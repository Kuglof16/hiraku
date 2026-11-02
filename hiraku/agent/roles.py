"""Roles as SDK subagents, and the mapping between SDK agent names and contract agent ids.

Contract ids use a dot (cae.strength); SDK agent names use a hyphen (cae-strength).
"""

from __future__ import annotations

from pathlib import Path

from hiraku import contract
from hiraku.agent import prompts
from hiraku.catalog import REPO_ROOT
from hiraku.tools.context import RunContext

CAE_TOOLS = ("get_context", "list_formulas", "list_tools", "record_prediction", "analyze", "compare", "record_cause")

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
    if agent_type.startswith("cae-") and agent_type[4:] in contract.DOMAINS and agent_type[4:] != "assembly":
        return "cae." + agent_type[4:]
    if agent_type in ("designer", "packager"):
        return agent_type
    return None


def subagents(ctx: RunContext) -> dict:
    """AgentDefinition per CAE role of this run (project.agents)."""
    from claude_agent_sdk import AgentDefinition

    out = {}
    for a in ctx.project["agents"]:
        if a["role"] != "cae":
            continue
        domain = a["domain"]
        out[sdk_name(a["agent_id"])] = AgentDefinition(
            description=prompts.cae_description(domain),
            prompt=prompts.cae_prompt(domain),
            tools=[contract.mcp_name(t) for t in CAE_TOOLS],
            skills=plugin_skills(),
            model="inherit",
        )
    return out
