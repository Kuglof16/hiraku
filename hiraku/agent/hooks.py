"""SDK hooks: the contract's HK1-HK4, agent start/stop events and next-step hints.

- PreToolUse: HK1-HK3 (hiraku.tools.rules.check_call). A denial is recorded as a
  hook.denied event and its reason (what is missing, what to call next) goes to Claude.
  Allowed calls leave the caller's agent id for the tool handler (Attribution).
- PostToolUse: after analyze and compare, additionalContext says what to do next.
- Stop / SubagentStop: HK4. While a picked domain is pending the stop is blocked
  (hook.blocked_stop); blocks are counted and capped so a run always ends. The
  lead's stop while a role is still running is neither blocked nor counted: the
  run waits for the role (hiraku.agent.runner.converse).
- SubagentStart / SubagentStop: agent.started / agent.stopped.
"""

from __future__ import annotations

from typing import Any, Callable

from hiraku import contract
from hiraku.agent.roles import agent_id_from_sdk
from hiraku.tools import Attribution, RunContext, rules

PREFIX = f"mcp__{contract.MCP_SERVER}__"
MAX_STOP_BLOCKS = 3
MAX_SUBAGENT_STOP_BLOCKS = 2


class Hooks:
    def __init__(self, ctx: RunContext, attribution: Attribution, *, max_stop_blocks: int = MAX_STOP_BLOCKS,
                 max_subagent_stop_blocks: int = MAX_SUBAGENT_STOP_BLOCKS) -> None:
        self.ctx = ctx
        self.attribution = attribution
        self.max_stop_blocks = max_stop_blocks
        self.max_subagent_stop_blocks = max_subagent_stop_blocks
        self.stop_blocks = 0
        self.subagent_stop_blocks: dict[str, int] = {}
        self.running: dict[str, str] = {}  # SDK agent_id -> contract agent id
        self.on_roles_done: Callable[[], None] | None = None  # called when the last running role stops

    # ------------------------------------------------------------------ helpers

    def caller(self, input_data: dict[str, Any]) -> str:
        """Contract agent id of whoever triggered the hook (main thread = lead)."""
        return agent_id_from_sdk(input_data.get("agent_type")) or self.running.get(input_data.get("agent_id", "")) or "lead"

    @staticmethod
    def _short(tool_name: str) -> str | None:
        return tool_name[len(PREFIX):] if tool_name.startswith(PREFIX) else None

    # ------------------------------------------------------------------ PreToolUse

    async def pre_tool_use(self, input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict:
        name = input_data.get("tool_name", "")
        short = self._short(name)
        if short is None:
            return {}
        args = input_data.get("tool_input") or {}
        agent = self.caller(input_data)
        try:
            denial = rules.check_call(self.ctx, short, args)
        except Exception:
            denial = None  # the tool function checks again and reports the problem itself
        if denial is not None:
            self.ctx.store.emit("hook.denied", agent=agent, domain=denial.domain, text=denial.reason,
                                data={"hook": "PreToolUse", "tool": name, "rule": denial.rule, "tool_input": args})
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": denial.reason,
            }}
        self.attribution.push(name, args, agent)
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
        if short == "revise_design" and v > 1 and s.design(v).get("parent_version") == v - 1:
            return (f"設計の版 {v} ができました。判定は版ごとなので、まとめ役は全部の荷重条件を版 {v} で"
                    f"やり直すよう各役に頼んでください")
        return None

    async def post_tool_use(self, input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict:
        short = self._short(input_data.get("tool_name", ""))
        if short is None:
            return {}
        try:
            hint = self.next_step(short, input_data.get("tool_input") or {})
        except Exception:
            hint = None
        if not hint:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": hint}}

    # ------------------------------------------------------------------ Stop (HK4)

    async def stop(self, input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict:
        if self.running:  # the lead waits for roles still at work: not giving up, so neither blocked nor counted
            return {}
        reason = rules.stop_reason(self.ctx)
        if reason is None or self.stop_blocks >= self.max_stop_blocks:
            return {}
        self.stop_blocks += 1
        self.ctx.store.emit("hook.blocked_stop", agent="lead", text=reason, data={
            "hook": "Stop", "rule": rules.HK4, "count": self.stop_blocks, "limit": self.max_stop_blocks,
        })
        return {"decision": "block", "reason": reason}

    # ------------------------------------------------------------------ subagents

    async def subagent_start(self, input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict:
        agent = agent_id_from_sdk(input_data.get("agent_type"))
        if agent is None:
            return {}
        self.running[input_data.get("agent_id", "")] = agent
        domain = agent.split(".", 1)[1] if agent.startswith("cae.") else None
        self.ctx.store.emit("agent.started", agent=agent, domain=domain, data={"agent_id": agent})
        return {}

    async def subagent_stop(self, input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict:
        agent = agent_id_from_sdk(input_data.get("agent_type"))
        if agent is None:
            return {}
        domain = agent.split(".", 1)[1] if agent.startswith("cae.") else None
        if domain is not None:
            left = [lc for d, lcs in rules.pending(self.ctx) if d == domain for lc in lcs]
            n = self.subagent_stop_blocks.get(agent, 0)
            if left and n < self.max_subagent_stop_blocks:
                self.subagent_stop_blocks[agent] = n + 1
                reason = rules.stop_reason(self.ctx) or ""
                self.ctx.store.emit("hook.blocked_stop", agent=agent, domain=domain, text=reason, data={
                    "hook": "SubagentStop", "rule": rules.HK4, "count": n + 1, "limit": self.max_subagent_stop_blocks,
                })
                return {"decision": "block", "reason": reason}
        was_running = bool(self.running)
        self.running.pop(input_data.get("agent_id", ""), None)
        self.ctx.store.emit("agent.stopped", agent=agent, domain=domain, data={"agent_id": agent})
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
