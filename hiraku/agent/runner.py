"""Run the agents with Claude (Agent SDK): options, the message loop, the final status."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from hiraku import contract
from hiraku.agent import inbox, prompts
from hiraku.agent.hooks import Hooks
from hiraku.agent.roles import PLUGIN_DIR, agent_id_from_sdk, all_plugin_skills, has_modules, routing_text, subagents
from hiraku.catalog.mixes import Mix, get_mix
from hiraku.agent.session import STOPPED_BY_BUTTON, STOPPED_BY_USER
from hiraku.tools import Attribution, RunContext, rules
from hiraku.tools.functions import RUN_TOOLS

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "high"
DEFAULT_MAX_TURNS = 200
AGENT_TOOL = "Agent"
SKILL_TOOL = "Skill"

# The bundled CLI launches subagents in the background by default; with this set, the Agent
# tool has no run_in_background and every subagent is synchronous (checked in CLI 2.1.309).
FOREGROUND_ROLES_ENV = {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"}
# How long to wait after the last role stops, while the lead's turn has ended, before the lead
# is woken with WAKE_TEXT (the CLI may start a turn by itself first). Only for roles that ran in
# the background anyway.
WAKE_GRACE_S = 5.0
MAX_WAKES = 5
# The stop button: how often stop.request is looked for, and how long the interrupted turn may
# take to close before the reading is cut.
STOP_POLL_S = 1.0
STOP_GRACE_S = 10.0
WAKE_TEXT = "担当の役の仕事が終わりました。get_context で judgements を確かめて、続きを進めてください。"


class MissingApiKey(RuntimeError):
    pass


def require_api_key() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise MissingApiKey(
            "ANTHROPIC_API_KEY is not set in this window, so HIRAKU will not call Claude. "
            "Set it only in the window that runs HIRAKU, or use --scripted to run without Claude."
        )


def lead_model(mix: Mix | None, model: str | None = None, effort: str | None = None) -> tuple[str, str]:
    """(model, effort) of the lead: model and effort when given, else the mix's lead, else
    DEFAULT_MODEL / DEFAULT_EFFORT."""
    spec = mix["lead"] if mix is not None else {"model": DEFAULT_MODEL, "effort": DEFAULT_EFFORT}
    return model or spec["model"], effort or spec["effort"]


def role_models(options: Any) -> dict[str, dict[str, Any]]:
    """{contract agent id: {"model", "effort"}} of the lead and each role in the options, for the
    agent.started events. A role whose model is "inherit" runs on the lead's model."""
    out = {"lead": {"model": options.model, "effort": options.effort}}
    for name, d in (options.agents or {}).items():
        agent = agent_id_from_sdk(name)
        if agent:
            out[agent] = {"model": options.model if d.model in (None, "inherit") else d.model, "effort": d.effort}
    return out


def build_options(ctx: RunContext, server: Any, hooks: Hooks, *, model: str | None = None,
                  effort: str | None = None, mix: Mix | None = None, max_budget_usd: float | None = None,
                  max_turns: int = DEFAULT_MAX_TURNS):
    """The SDK options of a run. With a mix (hiraku.catalog.mixes), the lead's model and effort and
    each role's come from it (model and effort, when given, still set the lead's); without one, the
    lead runs on model / effort (default DEFAULT_MODEL, DEFAULT_EFFORT) and the roles inherit its model."""
    from claude_agent_sdk import ClaudeAgentOptions

    budget = ctx.project["cost_limit_usd"] if max_budget_usd is None else max_budget_usd
    model, effort = lead_model(mix, model, effort)
    modules = has_modules(ctx)  # docs/contract.md モジュールに分けた設計: module designers, build_version
    run_tools = (*RUN_TOOLS, *(t for t in contract.MCP_TOOLS_PACKAGER if modules and t not in RUN_TOOLS))
    return ClaudeAgentOptions(
        # Only Agent (to start the role subagents) and Skill (the procedures in
        # plugins/hiraku) of the built-in set; Bash, Read, Write and the rest
        # stay out so the tools cannot be bypassed.
        tools=[AGENT_TOOL, SKILL_TOOL],
        setting_sources=[],  # never read this machine's CLAUDE.md, settings or hooks
        plugins=[{"type": "local", "path": str(PLUGIN_DIR)}],
        skills=all_plugin_skills(),  # only HIRAKU's skills, not the ones bundled with the CLI
        mcp_servers={contract.MCP_SERVER: server},
        strict_mcp_config=True,  # no MCP servers but hiraku (no connectors of a logged-in account)
        # Exact names, not a wildcard: under dontAsk anything not listed is denied.
        allowed_tools=[contract.mcp_name(t) for t in run_tools] + [AGENT_TOOL, SKILL_TOOL],
        permission_mode="dontAsk",
        hooks=hooks.matchers(),
        agents=subagents(ctx, mix),
        system_prompt=prompts.lead_prompt(modules),
        model=model,
        effort=effort,
        max_budget_usd=budget,
        max_turns=max_turns,
        forward_subagent_text=True,
        cwd=str(ctx.store.path.resolve()),
        # Roles run in the foreground: the lead's turn waits for their results (several Agent
        # calls in one message run side by side) instead of ending while they work.
        env=dict(FOREGROUND_ROLES_ENV),
    )


class MessageLog:
    """Turns the SDK message stream into agent.said / cost.updated events. cost.updated carries, next
    to cost_usd, what the ResultMessage reports for measuring later runs (usage_data)."""

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
                self.ctx.store.set_cost(msg.total_cost_usd, extra=usage_data(msg))


def usage_data(result: Any) -> dict[str, Any]:
    """The token counts and turns of a ResultMessage as it reports them (claude_agent_sdk 0.2.173):
    usage (the CLI's usage dict), model_usage (per model id: inputTokens, outputTokens,
    cacheReadInputTokens, cacheCreationInputTokens, costUSD, ...) and num_turns; None where absent."""
    return {"usage": getattr(result, "usage", None), "model_usage": getattr(result, "model_usage", None),
            "num_turns": getattr(result, "num_turns", None)}


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


def final_status(ctx: RunContext, result: Any, hooks: Hooks | None = None,
                 budget_usd: float | None = None) -> tuple[str, str | None, str]:
    """(status, stop_reason, stop_code) of a Claude run from its last result message. budget_usd:
    the max_budget_usd the run was given (build_options; None: the project's cost_limit_usd)."""
    subtype = getattr(result, "subtype", None)
    if subtype == "error_max_budget_usd":
        budget = ctx.project["cost_limit_usd"] if budget_usd is None else budget_usd
        return "stopped", f"cost limit {budget} USD reached", "cost_limit"
    if subtype == "error_max_turns":
        return "stopped", "turn limit reached", "turn_limit"
    if result is None or getattr(result, "is_error", False):
        return "failed", error_text(ctx, result), "error"
    return end_status(ctx, hooks.stop_blocks if hooks is not None else 0)


def end_status(ctx: RunContext, blocks: int = 0) -> tuple[str, str | None, str]:
    """(status, stop_reason, stop_code) of a run whose lead has stopped, Claude having reported
    no error or limit: not_judged while a picked domain is pending (blocks: how many stops HK4
    turned down first); red_left when a tile is red and design revisions are left (the Stop hook
    let the stop through at its cap); else rules.judged_end. Shared with the plugin
    (hiraku.agent.plugin); the scripted driver keeps rules.judged_end."""
    after = f" (止めるのを {blocks} 回断ったあと、上限で終了)" if blocks else ""
    dead_end = rules.stage_blocked_end(ctx)  # no revision left and the rest waits behind a lower stage's red
    if dead_end:
        return "stopped", dead_end, "round_limit"
    left = rules.unjudged_reason(ctx)
    if left:
        return "stopped", f"まとめ役が、判定の付いていない分野を残して止まりました{after}。" + left, "not_judged"
    red = rules.red_left_reason(ctx)
    if red:
        return "stopped", f"まとめ役が、設計の直しの回数を残したまま、赤の分野を残して止まりました{after}。" + red, "red_left"
    return rules.judged_end(ctx)


def new_routing(ctx: RunContext, hooks: Hooks, routed: dict[int, str]) -> str | None:
    """The routing of the latest version's reds (roles.routing_text, designs with modules) when no
    role is at work and it differs from what the lead was told for that version (kept in routed);
    else None. A failure here is said once as agent.said (system) and never ends the run."""
    if hooks.running or not ctx.version:
        return None
    try:
        if rules.revisions_used_up(ctx):  # no round left: nothing to route
            return None
        text = routing_text(ctx)
    except Exception as e:  # the routing helps the lead; a bug behind it must not end the run
        if routed.get(-1) is None:
            routed[-1] = repr(e)
            ctx.store.emit("agent.said", agent="system",
                           text=f"赤の振り分けを出せませんでした ({type(e).__name__}: {e})")
        return None
    if not text or routed.get(ctx.version) == text:
        return None
    routed[ctx.version] = text
    return text


async def converse(client: Any, ctx: RunContext, hooks: Hooks, log: MessageLog) -> None:
    """Give the lead its task and read messages until its turn ends with no role at work.

    Roles run in the foreground (FOREGROUND_ROLES_ENV), so the lead's turn normally outlasts
    them. Should a role still be at work when the lead's turn ends, the reading goes on (the
    roles are not cut off), and when the last role stops while the lead is idle, the lead is
    woken with WAKE_TEXT unless a turn of its own has begun within WAKE_GRACE_S; after MAX_WAKES
    wake-ups the reading ends there instead (no turn would come).
    The stop button (stop.request, checked every STOP_POLL_S) interrupts the CLI; the
    hooks deny every tool from then on, and the reading ends within STOP_GRACE_S.
    The inbox (hiraku.agent.inbox) is read at the same times: its events are emitted
    and what is for the lead goes to the hooks (told on its next tool call or stop),
    or straight to the lead as a new turn when its turn has ended while roles work.
    """
    from claude_agent_sdk import AssistantMessage, ResultMessage

    state = {"idle": False, "wakes": 0}
    loop = asyncio.get_running_loop()
    main = asyncio.current_task()
    cut = {"done": False}  # the reading was cut on purpose (stop button, no wake-up left): an ordinary end

    async def wake() -> None:
        await asyncio.sleep(WAKE_GRACE_S)
        if not state["idle"] or hooks.running:
            return
        if state["wakes"] < MAX_WAKES:
            state["wakes"] += 1
            state["idle"] = False
            await client.query(WAKE_TEXT)
            return
        # MAX_WAKES used up: nothing will start another turn of the lead's, so the reading ends here
        ctx.store.emit("agent.said", agent="system",
                       text=f"まとめ役を起こす回数の上限 ({MAX_WAKES} 回) に達したので、実行を終えます")
        cut["done"] = True
        main.cancel()

    def roles_done() -> None:
        if state["idle"]:
            loop.create_task(wake())

    async def watch_stop() -> None:
        while not hooks.stopping():
            await asyncio.sleep(STOP_POLL_S)
            told = await asyncio.to_thread(inbox.take, ctx)
            routing = await asyncio.to_thread(new_routing, ctx, hooks, routed)
            if routing:  # told on the lead's next tool call, like the inbox (not a turn of its own)
                hooks.for_lead.append(routing)
            if told:
                hooks.for_lead.extend(told)  # the hooks tell the lead on its next tool call or stop
                if state["idle"]:  # the lead's turn has ended while roles work: a turn of its own for them
                    state["idle"] = False
                    await client.query(hooks.take_for_lead())
        ctx.store.emit("agent.said", agent="system", text="止めるボタンが押されたので、実行を止めます")
        try:
            await client.interrupt()
        except Exception:  # the stop goes ahead whatever the CLI says
            pass
        await asyncio.sleep(STOP_GRACE_S)  # the interrupted turn may close by itself meanwhile
        cut["done"] = True
        main.cancel()

    routed: dict[int, str] = {}  # version -> the routing text told to the lead (new_routing)
    hooks.on_roles_done = roles_done
    watcher = loop.create_task(watch_stop())
    try:
        await client.query(prompts.lead_task(ctx))
        async for msg in client.receive_messages():
            log.handle(msg)
            if isinstance(msg, ResultMessage):
                state["idle"] = True
                if not hooks.running or hooks.stop_requested:
                    return
                ctx.store.emit("agent.said", agent="system", text="まとめ役の番が終わりましたが、役がまだ動いているので待ちます: "
                               + "、".join(sorted(set(hooks.running.values()))))
            elif isinstance(msg, AssistantMessage) and msg.parent_tool_use_id is None:
                state["idle"] = False
    except asyncio.CancelledError:
        if not cut["done"]:
            raise  # Ctrl+C
        main.uncancel()  # the stop button or the wake limit cut the reading: an ordinary end
    finally:
        watcher.cancel()


async def run_with_claude(ctx: RunContext, *, model: str | None = None, effort: str | None = None,
                          mix: str | None = None, max_budget_usd: float | None = None,
                          max_turns: int = DEFAULT_MAX_TURNS) -> str:
    """Run the lead agent (and its role subagents) until the run is judged or a limit stops it.
    mix: a name in data/catalog/model_mixes.yaml (None: every role on the lead's model, as before)."""
    require_api_key()
    chosen = get_mix(mix) if mix is not None else None
    from claude_agent_sdk import ClaudeSDKClient

    from hiraku.tools.mcp import build_server

    attribution = Attribution()
    hooks = Hooks(ctx, attribution)
    options = build_options(ctx, build_server(ctx, attribution), hooks, model=model, effort=effort, mix=chosen,
                            max_budget_usd=max_budget_usd, max_turns=max_turns)
    hooks.models = role_models(options)
    log = MessageLog(ctx)
    ctx.store.emit("agent.started", agent="lead", data={"agent_id": "lead", **hooks.models["lead"],
                                                        **({"mix": mix} if mix is not None else {})})

    def close(status: str, reason: str | None, code: str) -> None:
        # roles the CLI never reported stopped (cut off with the run) stop with it, then the lead
        for agent in sorted(set(hooks.running.values())):
            domain = agent.split(".", 1)[1] if agent.startswith("cae.") else None
            ctx.store.emit("agent.stopped", agent=agent, domain=domain, data={"agent_id": agent})
        hooks.running.clear()
        ctx.store.emit("agent.stopped", agent="lead", data={"agent_id": "lead"})
        ctx.store.finish(status, stop_reason=reason, stop_code=code)

    if ctx.store.stop_requested():  # pressed while the tools were checked: the CLI is not started
        close("stopped", STOPPED_BY_BUTTON, "user_stop")
        ctx.store.clear_stop_request()
        return "stopped"
    try:
        async with ClaudeSDKClient(options=options) as client:
            await converse(client, ctx, hooks, log)
    except (KeyboardInterrupt, asyncio.CancelledError):  # Ctrl+C: asyncio.run cancels this task
        close("stopped", STOPPED_BY_USER, "user_stop")
        raise
    except Exception as e:
        close("failed", f"{type(e).__name__}: {e}", "error")
        raise
    if hooks.stop_requested:
        close("stopped", STOPPED_BY_BUTTON, "user_stop")
        ctx.store.clear_stop_request()
        return "stopped"
    status, reason, code = final_status(ctx, log.result, hooks, options.max_budget_usd)
    close(status, reason, code)
    return status
