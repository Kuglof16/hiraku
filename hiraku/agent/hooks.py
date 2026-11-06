"""SDK hooks: the contract's HK1-HK6, agent start/stop events and next-step hints.

- PreToolUse: HK1-HK3, HK5 and HK6 (hiraku.tools.rules.check_call, with the caller's agent id: on a
  design with modules, revise_design only with module and by designer.<module>, inside its module;
  build_version only by the packager). A denial is recorded as a
  hook.denied event and its reason (what is missing, what to call next) goes to Claude.
  Allowed calls leave the caller's agent id for the tool handler (Attribution).
- PostToolUse: after analyze and compare, additionalContext says what to do next.
  After any tool call of the lead's, it also carries what the person sent through
  the screen (inbox, hiraku.agent.inbox) and the lead has not been told yet. While such a
  message waits, a role's next tool call tells the role (once per message) to finish the load
  case it is on and return, so the lead's Agent call ends and the lead hears it sooner; the
  message stays for the lead.
- Stop / SubagentStop: HK4. While a picked domain is pending the stop is blocked
  (hook.blocked_stop); the lead's stop also while a tile is red and design revisions
  are left (rules.red_left_reason); blocks are counted and capped so a run always
  ends. The round limit ends revisions only: the latest version is still judged, so its
  pending cases hold the stop as well (contract HOOK_RULES HK4), unless what holds each of them
  is written down (rules.excused_pending: the current version's report names it in an issue, or,
  with the revisions used up, its mismatch has a cause); a run whose first version
  was refused (rules.design_refusal) may stop. A role's stop (the packager's: assembly) is blocked only on its own domain's
  pending cases, counted per design version, and not while a
  message for the lead waits (the role returns so the lead hears it). Load cases no K0
  formula can predict (rules.unpredictable_cases) do not hold a stop. The
  lead's stop while a role is still running is not counted: the SDK run waits for the
  role (hiraku.agent.runner.converse); the plugin blocks it and the lead waits with the
  plugin's wait tool (wait_tool, plugin.WAIT_TOOL), since claude -p may cut background
  roles off some minutes after the lead's turn ends. A stop of the lead's
  with a message from the person not yet told is blocked once with that message
  (not counted either).
- The stop button (stop.request in the run folder): every tool call is denied
  and every stop is let through, so the run winds down (runner.converse).
- SubagentStart / SubagentStop: agent.started / agent.stopped. agent.started carries the
  role's model and effort (models, filled by hiraku.agent.runner from the options).

The decisions are plain methods (decide, one per event) shared by two drivers: the
Agent SDK run (the async methods below, registered by matchers()) and the Claude Code
plugin (hiraku.agent.plugin: one hook command per call, which keeps the state between
calls in a file, see state()/load_state(), and hands its events and the caller's agent
to the MCP server process through `emit` and the attribution, since only the run
process writes events.jsonl).
"""

from __future__ import annotations

from typing import Any, Callable

from hiraku import contract
from hiraku.agent.roles import agent_id_from_sdk, routing_text
from hiraku.tools import Attribution, RunContext, rules

PREFIX = f"mcp__{contract.MCP_SERVER}__"
# A plugin's MCP server's tools are named mcp__plugin_<plugin>_<server>__<tool> in Claude Code.
PLUGIN_PREFIX = f"mcp__plugin_{contract.MCP_SERVER}_{contract.MCP_SERVER}__"
MAX_STOP_BLOCKS = 3
MAX_SUBAGENT_STOP_BLOCKS = 2
# The plugin: the lead's stop while roles still run is blocked with WAIT_REASON (it waits with the wait tool), at
# most this many times; the bound only keeps a run whose role never stops from going on for ever
MAX_WAIT_BLOCKS = 60
WAIT_REASON = ("役 ({roles}) がまだ動いています。番を終えると Claude Code が役を打ち切ることがあるので、{tool} を呼んで"
               "役が終わるのを待ち、役の結果を受け取ってから続けてください")
STOP_REASON_FOR_AGENT = "止めるボタンが押されたので、実行を止めます。ツールを呼ばずに、ここまでのことを 1 文で言って番を終えてください"
# what a role is told while a message from the person waits for the lead (the message itself stays for the lead)

# run.finished's stop_code -> the stop_code of the roles (and the lead) it closes (contract a31e520): the stop
# button and an error are said as such; a run cut by its cost or turn limit cuts the lead's work too
_ROLE_STOP_OF_RUN = {"user_stop": "user_stop", "error": "error", "cost_limit": "run_ended", "turn_limit": "run_ended"}


def stopped_data(agent: str, run_code: str | None = None, *, cut_off: bool = False) -> dict[str, Any]:
    """agent.stopped's data: the agent id and why it stopped. run_code is the run's stop_code when the run's end
    closes the agent; cut_off marks a role still working then (run_ended). Without either: it ended its own work."""
    code = _ROLE_STOP_OF_RUN.get(run_code or "") or ("run_ended" if cut_off else "done")
    return {"agent_id": agent, "stop_code": code}


def role_domain(agent: str) -> str | None:
    """The domain whose load cases hold a role's stop (HK4): cae.<domain>'s, and the packager's assembly."""
    if agent.startswith("cae."):
        return agent.split(".", 1)[1]
    return "assembly" if agent == "packager" else None


class Hooks:
    def __init__(self, ctx: RunContext, attribution: Attribution, *, max_stop_blocks: int = MAX_STOP_BLOCKS,
                 max_subagent_stop_blocks: int = MAX_SUBAGENT_STOP_BLOCKS,
                 emit: Callable[..., Any] | None = None) -> None:
        self.ctx = ctx
        self.attribution = attribution
        self.emit = emit or ctx.store.emit  # where the hooks' events go (the plugin: to the MCP server)
        self.max_stop_blocks = max_stop_blocks
        self.max_subagent_stop_blocks = max_subagent_stop_blocks
        self.stop_blocks = 0
        self.subagent_stop_blocks: dict[str, int] = {}
        self.running: dict[str, str] = {}  # SDK agent_id -> contract agent id
        self.on_roles_done: Callable[[], None] | None = None  # called when the last running role stops
        self.stop_requested = False  # set once stop.request is seen; it stays set
        self.for_lead: list[str] = []  # what the screen sent for the lead (inbox), not told yet
        self.told_roles: list[str] = []  # roles told to return for the messages in for_lead (SDK agent ids)
        self.models: dict[str, dict[str, Any]] = {}  # contract agent id -> {"model", "effort"} for agent.started
        self.on_end: Callable[[], None] | None = None  # the lead's stop was let through (the plugin ends the run)
        # the plugin: the lead hears the routing of a version's reds (roles.routing_text, designs with modules) from
        # the hooks; the SDK run's runner sends it as a message of its own (runner.new_routing)
        self.tell_routing = False
        self.routed: dict[str, str] = {}  # design version (str) -> the routing told for it
        # the plugin: the tool the lead waits for running roles with (plugin.WAIT_TOOL). Claude Code (claude -p) may
        # end background roles some time after the lead's turn ends, so the lead's stop while roles run is blocked
        # and the lead waits with this tool. The SDK run leaves it None: its runner waits for the roles itself
        self.wait_tool: str | None = None
        self.wait_blocks = 0

    # ------------------------------------------------------------------ state kept between calls

    STATE_FIELDS = ("stop_blocks", "subagent_stop_blocks", "running", "stop_requested", "for_lead", "told_roles",
                    "routed", "wait_blocks")

    def state(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.STATE_FIELDS}

    def load_state(self, state: dict[str, Any]) -> None:
        for k in self.STATE_FIELDS:
            if k in state:
                setattr(self, k, state[k])

    # ------------------------------------------------------------------ helpers

    def caller(self, input_data: dict[str, Any]) -> str:
        """Contract agent id of whoever triggered the hook (main thread = lead)."""
        return agent_id_from_sdk(input_data.get("agent_type")) or self.running.get(input_data.get("agent_id", "")) or "lead"

    @staticmethod
    def _short(tool_name: str) -> str | None:
        for prefix in (PREFIX, PLUGIN_PREFIX):
            if tool_name.startswith(prefix):
                return tool_name[len(prefix):]
        return None

    # ------------------------------------------------------------------ PreToolUse

    def stopping(self) -> bool:
        """The stop button was pressed (stop.request in the run folder)."""
        if not self.stop_requested and self.ctx.store.stop_requested():
            self.stop_requested = True
        return self.stop_requested

    def decide(self, event: str, input_data: dict[str, Any]) -> dict:
        """The answer to one hook event (PreToolUse, PostToolUse, Stop, SubagentStart, SubagentStop)."""
        with rules.reading(self.ctx):  # the decisions only read the records: each file once
            return {
                "PreToolUse": self.decide_pre_tool_use,
                "PostToolUse": self.decide_post_tool_use,
                "Stop": self.decide_stop,
                "SubagentStart": self.decide_subagent_start,
                "SubagentStop": self.decide_subagent_stop,
            }[event](input_data)

    async def pre_tool_use(self, input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict:
        return self.decide("PreToolUse", input_data)

    def decide_pre_tool_use(self, input_data: dict[str, Any]) -> dict:
        name = input_data.get("tool_name", "")
        if self.stopping():  # no more work of any kind, HIRAKU's tools or the Agent tool
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": STOP_REASON_FOR_AGENT,
            }}
        short = self._short(name)
        if short is None:
            return {}
        args = input_data.get("tool_input") or {}
        agent = self.caller(input_data)
        try:
            denial = rules.check_call(self.ctx, short, args, agent)
        except Exception:
            denial = None  # the tool function checks again and reports the problem itself
        full = contract.mcp_name(short)  # one name on both drivers (the plugin's tools are mcp__plugin_...)
        if denial is not None:
            self.emit("hook.denied", agent=agent, domain=denial.domain, text=denial.reason,
                      data={"hook": "PreToolUse", "tool": full, "rule": denial.rule, "tool_input": args})
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": denial.reason,
            }}
        self.attribution.push(full, args, agent)
        return {}

    # ------------------------------------------------------------------ PostToolUse

    def next_step(self, short: str, args: dict[str, Any]) -> str | None:
        s, v = self.ctx.store, self.ctx.version
        if short == "analyze":
            hits = [a for a in s.records("analysis", v)
                    if a["load_case_id"] == args.get("load_case_id") and a["prediction_ids"] == args.get("prediction_ids")]
            if not hits:
                return None
            a = hits[-1]
            if a["status"] != "ok":
                return f"解析 {a['analysis_id']} は失敗しました。error の文を読み、設計か入力を見直してください"
            names = ", ".join(r["quantity"] for r in a["results"])
            return (f"解析 {a['analysis_id']} の結果 ({names}) が出ました。"
                    f"次は compare(prediction_id=..., analysis_id=\"{a['analysis_id']}\") で、同じ名前の予測と照合してください")
        if short == "compare":
            hits = [c for c in s.records("comparison") if c["prediction_id"] == args.get("prediction_id")
                    and c["analysis_id"] == args.get("analysis_id")]
            if not hits or hits[-1]["status"] == "match":
                return None
            c = hits[-1]
            return (f"食い違いです (差 {c['diff_rel'] * 100:.1f} %、許す幅 {c['tolerance_rel'] * 100:.0f} %)。"
                    f"record_cause(comparison_id=\"{c['comparison_id']}\", cause=..., fix=...) を書くまで、"
                    f"この分野の analyze と compare は止まります")
        if short == "revise_design" and rules.design_modules(self.ctx) is not None:
            mine = [c for c in s.pending_proposals(v) if c["module_id"] == args.get("module")]
            if not mine:
                return None
            return (f"提案 {mine[-1]['change_id']} を記録しました (版はまだ作っていません)。パッケージ役が build_version で、"
                    f"その回の提案をまとめて版 {v + 1} を作ります")
        if short in ("revise_design", "build_version", "write_design") and v > 1 and s.design(v).get("parent_version") == v - 1:
            return (f"設計の版 {v} ができました。判定は版ごとなので、まとめ役は全部の荷重条件を版 {v} で"
                    f"やり直すよう各役に頼んでください")
        return None

    def routing_for_lead(self) -> str | None:
        """The routing of the latest version's reds, once per version and text, when tell_routing is set, no role
        is at work and revisions are left; else None. A failure behind it gives None (it helps the lead only)."""
        if not self.tell_routing or self.running or not self.ctx.version:
            return None
        try:
            if rules.revisions_used_up(self.ctx):
                return None
            text = routing_text(self.ctx)
        except Exception:
            return None
        key = str(self.ctx.version)
        if not text or self.routed.get(key) == text:
            return None
        self.routed[key] = text
        return text

    def take_for_lead(self) -> str | None:
        """What the screen sent for the lead (inbox), as one text, once."""
        if not self.for_lead:
            return None
        texts, self.for_lead, self.told_roles = self.for_lead, [], []
        return "\n".join(texts)

    async def post_tool_use(self, input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict:
        return self.decide("PostToolUse", input_data)

    def decide_post_tool_use(self, input_data: dict[str, Any]) -> dict:
        short = self._short(input_data.get("tool_name", ""))
        hint = None
        if short is not None:
            try:
                hint = self.next_step(short, input_data.get("tool_input") or {})
            except Exception:
                hint = None
        # after any tool of the lead's, what the person sent; a role is not told (the person's words are for the
        # lead, who reads them at its next tool call or stop, and asks the roles for what follows from them)
        lead = self.caller(input_data) == "lead"
        told = self.take_for_lead() if lead else None
        routing = self.routing_for_lead() if lead else None
        text = "\n\n".join(x for x in (hint, told, routing) if x)
        if not text:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}

    # ------------------------------------------------------------------ Stop (HK4)

    async def stop(self, input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict:
        return self.decide("Stop", input_data)

    def _end(self) -> dict:
        if self.on_end is not None:
            self.on_end()
        return {}

    def decide_stop(self, input_data: dict[str, Any]) -> dict:
        if self.stopping():  # the stop button: let it end
            return self._end()
        told = self.take_for_lead()
        if told:  # the person said something the lead has not heard: go on with it (not HK4, not counted)
            return {"decision": "block", "reason": told}
        if self.running:  # the lead waits for roles still at work: not giving up, so not counted against HK4
            if self.wait_tool and self.wait_blocks < MAX_WAIT_BLOCKS:
                self.wait_blocks += 1
                roles = "、".join(sorted(set(self.running.values())))
                return {"decision": "block", "reason": WAIT_REASON.format(roles=roles, tool=self.wait_tool)}
            return {}
        # the round limit ends revisions only: the latest version is still judged (HOOK_RULES HK4), so a
        # pending case still holds the stop; red_left_reason already lets a red tile go once no revision is left
        if rules.design_refusal(self.ctx) is not None:  # no first version: the designer's last try was refused
            return self._end()
        # what keeps each pending domain pending is written down (the report's issues name it, or, with the
        # revisions used up, its mismatch has a cause): blocking again would only call the roles for nothing
        if rules.excused_pending(self.ctx) is not None:
            return self._end()
        routing = self.routing_for_lead()
        if routing:  # the lead has not heard who answers this version's reds: go on with it (not counted)
            return {"decision": "block", "reason": routing}
        # HK4: a pending domain, or (red_left) a red tile while design revisions are left
        reason = rules.stop_reason(self.ctx) or rules.red_left_reason(self.ctx)
        if reason is None or self.stop_blocks >= self.max_stop_blocks:
            return self._end()
        self.stop_blocks += 1
        self.emit("hook.blocked_stop", agent="lead", text=reason, data={
            "hook": "Stop", "rule": rules.HK4, "count": self.stop_blocks, "limit": self.max_stop_blocks,
        })
        return {"decision": "block", "reason": reason}

    # ------------------------------------------------------------------ subagents

    async def subagent_start(self, input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict:
        return self.decide_subagent_start(input_data)

    def decide_subagent_start(self, input_data: dict[str, Any]) -> dict:
        agent = agent_id_from_sdk(input_data.get("agent_type"))
        if agent is None:
            return {}
        self.running[input_data.get("agent_id", "")] = agent
        domain = agent.split(".", 1)[1] if agent.startswith("cae.") else None
        self.emit("agent.started", agent=agent, domain=domain, data={"agent_id": agent, **self.models.get(agent, {})})
        return {}

    async def subagent_stop(self, input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict:
        return self.decide("SubagentStop", input_data)

    def decide_subagent_stop(self, input_data: dict[str, Any]) -> dict:
        agent = agent_id_from_sdk(input_data.get("agent_type"))
        if agent is None:
            return {}
        domain = role_domain(agent)
        if domain is not None and not self.stopping():
            left = [lc for d, lcs in rules.holding(self.ctx) if d == domain for lc in lcs]
            key = f"{agent}@v{self.ctx.version}"  # counted per design version: each version is judged again
            n = self.subagent_stop_blocks.get(key, 0)
            if left and n < self.max_subagent_stop_blocks:
                self.subagent_stop_blocks[key] = n + 1
                reason = rules.stop_reason(self.ctx, domain) or ""  # the role's own domain only
                self.emit("hook.blocked_stop", agent=agent, domain=domain, text=reason, data={
                    "hook": "SubagentStop", "rule": rules.HK4, "count": n + 1, "limit": self.max_subagent_stop_blocks,
                    "design_version": self.ctx.version,
                })
                return {"decision": "block", "reason": reason}
        was_running = bool(self.running)
        self.running.pop(input_data.get("agent_id", ""), None)
        self.emit("agent.stopped", agent=agent, domain=agent.split(".", 1)[1] if agent.startswith("cae.") else None,
                  data=stopped_data(agent))
        if was_running and not self.running and self.on_roles_done is not None:
            self.on_roles_done()
        return {}

    # ------------------------------------------------------------------ wiring

    def matchers(self) -> dict:
        from claude_agent_sdk import HookMatcher

        return {
            "PreToolUse": [HookMatcher(matcher=None, hooks=[self.pre_tool_use])],
            "PostToolUse": [HookMatcher(matcher=None, hooks=[self.post_tool_use])],
            "Stop": [HookMatcher(matcher=None, hooks=[self.stop])],
            "SubagentStart": [HookMatcher(matcher=None, hooks=[self.subagent_start])],
            "SubagentStop": [HookMatcher(matcher=None, hooks=[self.subagent_stop])],
        }
