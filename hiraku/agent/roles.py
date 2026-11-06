"""Roles as SDK subagents, and the mapping between SDK agent names and contract agent ids.

Contract ids use a dot (cae.strength, designer.frame); SDK agent names use a hyphen
(cae-strength, designer-frame).

Designs with modules (docs/contract.md モジュールに分けた設計): one designer per module of the
design (designer.<module>) instead of the single designer, and the packager also builds the
round's proposals into a version (build_version). route_reds says which role answers which red.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hiraku import contract
from hiraku.agent import prompts
from hiraku.catalog import REPO_ROOT
from hiraku.catalog.mixes import Mix, role_key
from hiraku.tools.context import RunContext

Record = dict[str, Any]

CAE_TOOLS = ("get_context", "list_formulas", "list_tools", "record_prediction", "analyze", "compare", "record_cause",
             "get_tool_template", "check_tool", "make_tool")
PACKAGER_TOOLS = (*CAE_TOOLS, "rule_check")  # the knowledge rules are checked on the parts by the packager
# designs with modules: the packager also builds each round's version
MODULE_PACKAGER_TOOLS = (*PACKAGER_TOOLS, *contract.MCP_TOOLS_PACKAGER)
# write_design and check_design: a design Claude writes (docs/contract.md Claude が書く設計)
DESIGNER_TOOLS = ("get_context", "list_tools", "propose_design", "revise_design", "write_design", "check_design")
MODULE_DESIGNER_TOOLS = ("get_context", "list_tools", *contract.MCP_TOOLS_DESIGNER)  # revise_design with module
# Effort per role: the CAE roles and the packager follow a fixed procedure (predict, analyze,
# compare); the designer chooses changes, like the lead (whose effort is the run's).
CAE_EFFORT = "medium"
DESIGNER_EFFORT = "high"

PLUGIN_NAME = "hiraku"
PLUGIN_DIR = REPO_ROOT / "plugins" / PLUGIN_NAME
# Skills only the module designers load (not listed for the CAE roles and the packager)
MODULE_DESIGNER_SKILLS = ("revise-a-module",)
# Skills only the single designer loads (writing a design: write_design, check_design)
DESIGNER_SKILLS = ("write-a-design",)

# H1 (interference): the test whose reds are split by the modules of the overlapping parts
INTERFERENCE_TEST = "H1"


def all_plugin_skills(plugin_dir: Path = PLUGIN_DIR) -> list[str]:
    """Every skill name ("<plugin>:<skill>") of the local plugin, one per skills/<name>/SKILL.md."""
    return sorted(f"{PLUGIN_NAME}:{p.parent.name}" for p in (plugin_dir / "skills").glob("*/SKILL.md"))


def plugin_skills(plugin_dir: Path = PLUGIN_DIR) -> list[str]:
    """The skills of the CAE roles and the packager: every skill but MODULE_DESIGNER_SKILLS and DESIGNER_SKILLS."""
    only = {f"{PLUGIN_NAME}:{s}" for s in (*MODULE_DESIGNER_SKILLS, *DESIGNER_SKILLS)}
    return [s for s in all_plugin_skills(plugin_dir) if s not in only]


def _existing(names: tuple[str, ...], plugin_dir: Path) -> list[str]:
    have = set(all_plugin_skills(plugin_dir))
    return [f"{PLUGIN_NAME}:{s}" for s in names if f"{PLUGIN_NAME}:{s}" in have]


def module_designer_skills(plugin_dir: Path = PLUGIN_DIR) -> list[str]:
    """The skills of the module designers (MODULE_DESIGNER_SKILLS that exist)."""
    return _existing(MODULE_DESIGNER_SKILLS, plugin_dir)


def designer_skills(plugin_dir: Path = PLUGIN_DIR) -> list[str]:
    """The skills of the single designer (DESIGNER_SKILLS that exist)."""
    return _existing(DESIGNER_SKILLS, plugin_dir)


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
    if agent_type.startswith("designer-") and "designer." + agent_type[9:] in contract.MODULE_DESIGNERS:
        return "designer." + agent_type[9:]
    if agent_type in ("designer", "packager"):
        return agent_type
    return None


# ---------------------------------------------------------------- designs with modules

def design_modules(design: Record | None) -> list[Record]:
    """design["modules"] (docs/contract.md モジュールに分けた設計), or [] for a design without modules."""
    return list((design or {}).get("modules") or [])


def current_design(ctx: RunContext) -> Record | None:
    """The latest version's design, or None before the first version."""
    if not ctx.version:
        return None
    return ctx.store.design()


def has_modules(ctx: RunContext) -> bool:
    return bool(design_modules(current_design(ctx)))


def module_agent(module_id: str, design: Record | None = None) -> str:
    """The role that answers for a module: the module's owner in the design, else designer.<module>
    (package: the packager)."""
    for m in design_modules(design):
        if m["module_id"] == module_id and m.get("owner"):
            return m["owner"]
    return "packager" if module_id == "package" else f"designer.{module_id}"


def module_designer_agents(design: Record | None, prop: str) -> list[Record]:
    """A project agent per module designer of the design (contract.MODULE_DESIGNERS order), each
    with its own pet (prop: the designer's)."""
    present = {m["module_id"] for m in design_modules(design)}
    return [{"agent_id": a, "role": "designer", "module": a.split(".", 1)[1], "prop": prop}
            for a in contract.MODULE_DESIGNERS if a.split(".", 1)[1] in present]


def agents_with_modules(agents: list[Record], design: Record | None, prop_of: Any) -> list[Record]:
    """project.agents for a design with modules: the single designer replaced by the design's module
    designers, and the packager present (it builds every version: build_version). Unchanged for a
    design without modules. prop_of(agent_id) gives a pet's prop (catalog.prop)."""
    if not design_modules(design):
        return agents
    out, placed = [], False
    for a in agents:
        if a["agent_id"] == "designer" or a["agent_id"].startswith("designer."):
            if not placed:
                out += module_designer_agents(design, a.get("prop") or prop_of("designer"))
                placed = True
            continue
        out.append(a)
    if not placed:
        out += module_designer_agents(design, prop_of("designer"))
    if not any(a["agent_id"] == "packager" for a in out):
        out.append({"agent_id": "packager", "role": "packager", "prop": prop_of("packager")})
    return out


def _body_modules(design: Record) -> dict[str, str]:
    """Body id as the interference results name it -> module: 'part:<id>' and 'member:<id>'
    (hiraku.solvers.k4_geometry bodies, in a result's where), and the names of
    analysis.interferences ('<part id>', 'member <member id>'). A member without its own module
    takes its part's."""
    out: dict[str, str] = {}
    part_module = {p["id"]: p.get("module") for p in design.get("parts", [])}
    for pid, mod in part_module.items():
        if mod:
            out[f"part:{pid}"] = out[pid] = mod
    for m in design.get("members", []):
        mod = m.get("module") or part_module.get(m.get("part"))
        if mod:
            out[f"member:{m['id']}"] = out[f"member {m['id']}"] = mod
    return out


def overlap_pairs(analysis: Record | None) -> list[tuple[str, str]]:
    """The overlapping pairs an interference analysis names: the `where` of its results ('a x b',
    for a non-zero result) and its interferences (a, b)."""
    if not analysis:
        return []
    pairs = []
    for r in analysis.get("results", []):
        where = r.get("where") or ""
        if " x " in where and r.get("value"):
            a, b = where.split(" x ", 1)
            pairs.append((a.strip(), b.strip()))
    for i in analysis.get("interferences", []):
        pairs.append((i["a"], i["b"]))
    return pairs


def route_red(design: Record, load_case: Record, analysis: Record | None = None) -> list[str]:
    """The roles that answer one red load case (docs/contract.md モジュールに分けた設計, round (1)):
    the owners of its `modules` (package: the packager). For H1 (interference), by the modules of
    the overlapping parts in the analysis instead: parts of two modules -> the packager, both in one
    module -> that module's designer; when no pair's modules are known, the load case's modules.
    A load case without modules, or a module the design does not have, goes to the packager
    (it lays out the modules)."""
    present = {m["module_id"] for m in design_modules(design)}
    out: list[str] = []
    if load_case.get("test_id") == INTERFERENCE_TEST:
        mods = _body_modules(design)
        for a, b in overlap_pairs(analysis):
            ma, mb = mods.get(a), mods.get(b)
            if ma is None or mb is None:
                continue
            who = module_agent(ma, design) if ma == mb else "packager"
            if who not in out:
                out.append(who)
        if out:
            return out
    for m in load_case.get("modules") or ["package"]:
        who = module_agent(m, design) if m in present or m == "package" else "packager"
        if who not in out:
            out.append(who)
    return out


def red_load_cases(ctx: RunContext, version: int) -> list[tuple[Record, Record]]:
    """(load case, failing verdict) per red load case of a version (the newest verdict per load case)."""
    from hiraku.tools.judge import latest_verdicts

    verdicts = latest_verdicts(ctx, version)
    return [(lc, verdicts[lc["load_case_id"]]) for lc in ctx.load_cases
            if lc["load_case_id"] in verdicts and not verdicts[lc["load_case_id"]]["pass"]]


def route_reds(ctx: RunContext, version: int | None = None) -> dict[str, list[str]]:
    """agent_id -> the red load case ids it answers, for a version (default: the latest).
    A design without modules: every red goes to the designer. Keys in the order of
    contract.MODULE_DESIGNERS, the packager last (it builds the version after the designers)."""
    v = version or ctx.version
    if not v:
        return {}
    reds = red_load_cases(ctx, v)
    if not reds:
        return {}
    design = ctx.store.design(v)
    if not design_modules(design):
        return {"designer": [lc["load_case_id"] for lc, _ in reds]}
    got: dict[str, list[str]] = {}
    for lc, verdict in reds:
        analysis = ctx.store.get("analysis", verdict["analysis_id"]) if verdict.get("analysis_id") else None
        for who in route_red(design, lc, analysis):
            got.setdefault(who, []).append(lc["load_case_id"])
    order = {a: i for i, a in enumerate((*contract.MODULE_DESIGNERS, "packager"))}
    return {a: got[a] for a in sorted(got, key=lambda a: order.get(a, len(order)))}


def routing_text(ctx: RunContext, version: int | None = None) -> str | None:
    """The routing of a version's reds for the lead (prompts.routing_text), or None when there is
    no red or the design has no modules."""
    v = version or ctx.version
    if not v or not design_modules(ctx.store.design(v)):
        return None
    routes = route_reds(ctx, v)
    return prompts.routing_text(v, routes) if routes else None


# ---------------------------------------------------------------- the run's roles

def possible_roles(ctx: RunContext) -> list[Record]:
    """The roles of project.agents; before the first version (no load cases yet) also every
    role the product's catalog tests could need, since the SDK takes its subagents at the start.
    For a design with modules: the module designers of its modules and the packager
    (agents_with_modules)."""
    from hiraku.catalog import load_catalog

    agents = list(ctx.project["agents"])
    design = current_design(ctx)
    if design_modules(design):
        agents = agents_with_modules(agents, design, load_catalog().prop)
    if ctx.load_cases:
        return agents
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


def role_model(agent_id: str, mix: Mix | None) -> tuple[str, str]:
    """(model, effort) of a role other than the lead: from the mix, else "inherit" (the lead's model)
    with the role's fixed effort (DESIGNER_EFFORT, CAE_EFFORT), as before mixes existed.
    A module designer (designer.<module>) takes the designer's."""
    if agent_id.startswith("designer."):
        agent_id = "designer"
    if mix is not None:
        spec = mix[role_key(agent_id)]
        return spec["model"], spec["effort"]
    return "inherit", DESIGNER_EFFORT if agent_id == "designer" else CAE_EFFORT


def subagents(ctx: RunContext, mix: Mix | None = None) -> dict:
    """AgentDefinition per role of this run (possible_roles) but the lead: CAE roles,
    the packager (the CAE steps on the assembly load cases; with modules also build_version)
    and the designer, or for a design with modules one designer per module (designer-<module>).
    All run in the foreground (background=False), so the lead waits for their results. Each role's
    model and effort come from the mix (hiraku.catalog.mixes) when one is given."""
    from claude_agent_sdk import AgentDefinition

    modules = has_modules(ctx)
    out = {}
    for a in possible_roles(ctx):
        model, effort = role_model(a["agent_id"], mix) if a["role"] in ("cae", "packager", "designer") else (None, None)
        if a["role"] in ("cae", "packager"):
            domain = a.get("domain", "assembly")
            builds = a["role"] == "packager" and modules
            tools = CAE_TOOLS if a["role"] == "cae" else MODULE_PACKAGER_TOOLS if builds else PACKAGER_TOOLS
            out[sdk_name(a["agent_id"])] = AgentDefinition(
                description=prompts.cae_description(domain, modules=builds),
                prompt=prompts.cae_prompt(domain, modules=builds),
                tools=[contract.mcp_name(t) for t in tools],
                skills=plugin_skills(),
                model=model,
                effort=effort,
                background=False,
            )
        elif a["role"] == "designer" and a["agent_id"].startswith("designer."):
            module = a["agent_id"].split(".", 1)[1]
            out[sdk_name(a["agent_id"])] = AgentDefinition(
                description=prompts.module_designer_description(module),
                prompt=prompts.module_designer_prompt(module),
                tools=[contract.mcp_name(t) for t in MODULE_DESIGNER_TOOLS],
                skills=module_designer_skills(),
                model=model,
                effort=effort,
                background=False,
            )
        elif a["role"] == "designer":
            out["designer"] = AgentDefinition(
                description=prompts.DESIGNER_DESCRIPTION,
                prompt=prompts.DESIGNER_PROMPT,
                tools=[contract.mcp_name(t) for t in DESIGNER_TOOLS],
                skills=designer_skills(),
                model=model,
                effort=effort,
                background=False,
            )
    return out
