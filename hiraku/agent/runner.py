"""Run the agents with Claude (Agent SDK): options, the message loop, the final status."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from hiraku import contract
from hiraku.agent import prompts
from hiraku.agent.hooks import Hooks
from hiraku.agent.roles import PLUGIN_DIR, agent_id_from_sdk, plugin_skills, subagents
from hiraku.agent.session import STOPPED_BY_USER
from hiraku.tools import Attribution, RunContext, rules
from hiraku.tools.functions import RUN_TOOLS

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "high"
DEFAULT_MAX_TURNS = 200
AGENT_TOOL = "Agent"
SKILL_TOOL = "Skill"


class MissingApiKey(RuntimeError):
    pass


def require_api_key() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise MissingApiKey(
            "ANTHROPIC_API_KEY is not set in this window, so HIRAKU will not call Claude. "
            "Set it only in the window that runs HIRAKU, or use --scripted to run without Claude."
        )


def build_options(ctx: RunContext, server: Any, hooks: Hooks, *, model: str = DEFAULT_MODEL,
                  effort: str = DEFAULT_EFFORT, max_budget_usd: float | None = None,
                  max_turns: int = DEFAULT_MAX_TURNS):
    from claude_agent_sdk import ClaudeAgentOptions

    budget = ctx.project["cost_limit_usd"] if max_budget_usd is None else max_budget_usd
    return ClaudeAgentOptions(
        # Only Agent (to start the role subagents) and Skill (the procedures in
        # plugins/hiraku) of the built-in set; Bash, Read, Write and the rest
        # stay out so the tools cannot be bypassed.
        tools=[AGENT_TOOL, SKILL_TOOL],
        setting_sources=[],  # never read this machine's CLAUDE.md, settings or hooks
        plugins=[{"type": "local", "path": str(PLUGIN_DIR)}],
        skills=plugin_skills(),  # only HIRAKU's skills, not the ones bundled with the CLI
        mcp_servers={contract.MCP_SERVER: server},
        strict_mcp_config=True,  # no MCP servers but hiraku (no connectors of a logged-in account)
        # Exact names, not a wildcard: under dontAsk anything not listed is denied.
        allowed_tools=[contract.mcp_name(t) for t in RUN_TOOLS] + [AGENT_TOOL, SKILL_TOOL],
        permission_mode="dontAsk",
        hooks=hooks.matchers(),
        agents=subagents(ctx),
        system_prompt=prompts.LEAD_PROMPT,
        model=model,
        effort=effort,
        max_budget_usd=budget,
        max_turns=max_turns,
        forward_subagent_text=True,
        cwd=str(ctx.store.path.resolve()),
    )


class MessageLog:
    """Turns the SDK message stream into agent.said / cost.updated events."""

    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx
        self.agent_by_tool_use: dict[str, str] = {}
        self.result: Any = None

    def agent_of(self, parent_tool_use_id: str | None) -> str:
        if parent_tool_use_id is None:
            return "lead"
        return self.agent_by_tool_use.get(parent_tool_use_id, "lead")

    def handle(self, msg: Any) -> None:
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

        if isinstance(msg, AssistantMessage):
            agent = self.agent_of(msg.parent_tool_use_id)
            domain = agent.split(".", 1)[1] if agent.startswith("cae.") else None
            for block in msg.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    self.ctx.store.emit("agent.said", agent=agent, domain=domain, text=block.text.strip())
                elif isinstance(block, ToolUseBlock) and block.name in (AGENT_TOOL, "Task"):
                    sub = agent_id_from_sdk((block.input or {}).get("subagent_type"))
                    if sub:
                        self.agent_by_tool_use[block.id] = sub
        elif isinstance(msg, ResultMessage):
            self.result = msg
            if msg.total_cost_usd is not None:
                self.ctx.store.set_cost(msg.total_cost_usd)


def error_text(ctx: RunContext, result: Any) -> str:
    """Why a Claude run failed, in the words it came with: the result message's text, else its
    errors, else the last thing an agent said, else the subtype. (An API failure can come as
    is_error with subtype "success" and the reason only in the text.)"""
    status = getattr(result, "api_error_status", None)
    tail = f" (API status {status})" if status else ""
    errors = "; ".join(str(e) for e in (getattr(result, "errors", None) or []))
    for text in (getattr(result, "result", None), errors):
        if text and str(text).strip():
            return str(text).strip() + tail
    said = [e["text"] for e in ctx.store.events()
            if e["kind"] == "agent.said" and e.get("agent") != "system" and e.get("text")]
    if said:
        return said[-1] + tail
    if result is None:
        return "no result message from Claude"
    return (getattr(result, "subtype", None) or "error") + tail


def final_status(ctx: RunContext, result: Any) -> tuple[str, str | None]:
    subtype = getattr(result, "subtype", None)
    if subtype == "error_max_budget_usd":
        return "stopped", f"cost limit {ctx.project['cost_limit_usd']} USD reached"
    if subtype == "error_max_turns":
        return "stopped", "turn limit reached"
    if result is None or getattr(result, "is_error", False):
        return "failed", error_text(ctx, result)
    left = rules.stop_reason(ctx)
    if left:
        return "stopped", left
    return "done", None


async def run_with_claude(ctx: RunContext, *, model: str = DEFAULT_MODEL, effort: str = DEFAULT_EFFORT,
                          max_budget_usd: float | None = None, max_turns: int = DEFAULT_MAX_TURNS) -> str:
    """Run the lead agent (and its role subagents) until the run is judged or a limit stops it."""
    require_api_key()
    from claude_agent_sdk import ClaudeSDKClient

    from hiraku.tools.mcp import build_server

    attribution = Attribution()
    hooks = Hooks(ctx, attribution)
    options = build_options(ctx, build_server(ctx, attribution), hooks, model=model, effort=effort,
                            max_budget_usd=max_budget_usd, max_turns=max_turns)
    log = MessageLog(ctx)
    ctx.store.emit("agent.started", agent="lead", data={"agent_id": "lead"})
    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompts.lead_task(ctx))
            async for msg in client.receive_response():
                log.handle(msg)
    except (KeyboardInterrupt, asyncio.CancelledError):  # Ctrl+C: asyncio.run cancels this task
        ctx.store.emit("agent.stopped", agent="lead", data={"agent_id": "lead"})
        ctx.store.finish("stopped", stop_reason=STOPPED_BY_USER)
        raise
    except Exception as e:
        ctx.store.emit("agent.stopped", agent="lead", data={"agent_id": "lead"})
        ctx.store.finish("failed", stop_reason=f"{type(e).__name__}: {e}")
        raise
    ctx.store.emit("agent.stopped", agent="lead", data={"agent_id": "lead"})
    status, reason = final_status(ctx, log.result)
    ctx.store.finish(status, stop_reason=reason)
    return status
