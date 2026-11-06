"""N09 with Claude: the lead agent reads a request and picks the domains (analysis is not run).

This is the part of the first minute of a run that is Claude's: from a sentence, which properties the
product has (the flags) and why, then the reason and the name of each domain. Everything else (which
tests apply, which domains light up) is the pick_domains tool's, in code. It needs ANTHROPIC_API_KEY in
the window that runs it; nothing here reads or stores the key.

    python -m hiraku.intake pick --claude --product serving-robot
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any

from hiraku import contract
from hiraku.contract.protocols import ToolError
from hiraku.intake.domains import IntakeError, add_tests, summary_line
from hiraku.intake.tools import TOOLS

Record = dict[str, Any]
# the fields of a domains_picked record (what pick_domains returns besides them is its own: recorded, next, reasons_missing)
_RECORD_KEYS = ("want", "flags", "speed_class", "flag_reasons", "picks", "off", "assembly_test_ids", "added_tests", "no_addition_reason")

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_MAX_BUDGET_USD = 0.5
DEFAULT_MAX_TURNS = 8
TOOL = "pick_domains"
REVIEW_TOOL = "add_tests"

PROMPT = """あなたは HIRAKU のまとめ役です。人が作りたいものを一文で伝えます。まず、その製品が持つ性質を読み取り、使う解析の分野を選びます。

手順:
1. pick_domains を呼ぶ。flags には、文から読み取れる製品の性質を true で渡す (文に書かれていないことを足さない。迷ったら立てない)。
   speed_class も選ぶ。flag_reasons に、立てた性質ごとに文のどこから分かったかを 1 文で書く。
2. 結果の picks (使う分野と、その分野で当てはまる試験) と off (使わない分野) を見る。分野と試験はコードが決めたもので、あなたは変えられない。
   結果に reasons_missing があるときは、同じ引数に reasons (分野 → この製品でその分野を使う理由を 1 文) と、読み替えたい分野の
   shown_names (たとえば配膳ロボの空力は「液体のこぼれ」、衝突は「人との接触」) を足して、もう一度 pick_domains を呼ぶ。
3. add_tests で、選びを見直す (必ず 1 回)。コードが選んだ分野と試験を見て、この製品に要る試験がカタログにあるのに選びに入っていなければ、足す
   (tests: test_id と、その試験が要る理由を人の言葉で 1 文。試験の番号は理由に書かない)。足すものが無いときも、見直して何を確かめたかと足さない理由を
   no_addition_reason に書いて呼ぶ。足すだけで、コードの選びを外すことはできない。
4. 最後に、人への返事を 3 行以内で書く: どんな製品と受け取ったか、使う分野の数と名前、使わない分野があればその理由。

数字を自分で計算したり、試験の数を数え直したりしない。結果の数字をそのまま使う。"""


@dataclass
class PickRun:
    record: Record | None = None  # the last pick_domains result (the domains_picked record + recorded / next)
    calls: list[Record] = field(default_factory=list)  # the arguments of each pick_domains call
    added_tests: list[Record] = field(default_factory=list)  # the tests Claude added (add_tests), each {test_id, reason}
    no_addition_reason: str = ""  # why it added none (add_tests)
    reply: str = ""  # the lead's answer to the person
    cost_usd: float | None = None
    error: str | None = None

    def summary(self) -> str:
        return summary_line(self.record) if self.record else "分野は選ばれませんでした"


class MissingApiKey(RuntimeError):
    pass


def require_api_key() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise MissingApiKey(
            "ANTHROPIC_API_KEY is not set in this window, so HIRAKU will not call Claude. "
            "Set it only in the window that runs this, or leave out --claude to pick the domains from the product file's flags."
        )


def build_server(run: PickRun, ctx: Any = None):
    """An SDK MCP server named hiraku with only pick_domains and add_tests; it keeps what Claude sent and got back."""
    from claude_agent_sdk import create_sdk_mcp_server, tool

    spec = TOOLS[TOOL]
    review = TOOLS[REVIEW_TOOL]

    async def review_handler(args: dict[str, Any]) -> dict[str, Any]:
        run.calls.append({"tool": REVIEW_TOOL, **dict(args)})
        if run.record is None:
            return {"content": [{"type": "text", "text": "まだ分野を選んでいません。先に pick_domains を呼んでください"}], "is_error": True}
        try:
            record, ignored, no_builder = await asyncio.to_thread(
                add_tests, {k: v for k, v in run.record.items() if k in _RECORD_KEYS}, args.get("tests"), args.get("no_addition_reason"))
        except IntakeError as e:
            return {"content": [{"type": "text", "text": str(e)}], "is_error": True}
        run.added_tests = [dict(a) for a in record.get("added_tests", [])]
        run.no_addition_reason = record.get("no_addition_reason", "")
        run.record = {**run.record, **record}
        out = {"added_tests": run.added_tests, "no_addition_reason": run.no_addition_reason, "ignored": ignored, "no_builder": no_builder}
        return {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}]}

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        run.calls.append(dict(args))
        try:
            result = await asyncio.to_thread(spec["function"], ctx, args, "lead")
        except ToolError as e:
            return {"content": [{"type": "text", "text": str(e)}], "is_error": True}
        run.record = result
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}

    return create_sdk_mcp_server(name=contract.MCP_SERVER, version="0.1.0",
                                 tools=[tool(TOOL, spec["description"], spec["input_schema"])(handler),
                                        tool(REVIEW_TOOL, review["description"], review["input_schema"])(review_handler)])


def build_options(server: Any, *, model: str = DEFAULT_MODEL, max_budget_usd: float = DEFAULT_MAX_BUDGET_USD,
                  max_turns: int = DEFAULT_MAX_TURNS):
    from claude_agent_sdk import ClaudeAgentOptions

    return ClaudeAgentOptions(
        tools=[],  # none of the built-in tools: no files, no shell
        setting_sources=[],  # never read this machine's CLAUDE.md, settings or hooks
        mcp_servers={contract.MCP_SERVER: server},
        strict_mcp_config=True,
        allowed_tools=[contract.mcp_name(TOOL), contract.mcp_name(REVIEW_TOOL)],
        permission_mode="dontAsk",
        system_prompt=PROMPT,
        model=model,
        max_budget_usd=max_budget_usd,
        max_turns=max_turns,
    )


async def _converse(options: Any, want: str, run: PickRun) -> None:
    from claude_agent_sdk import AssistantMessage, ClaudeSDKClient, ResultMessage, TextBlock

    async with ClaudeSDKClient(options=options) as client:
        await client.query(want)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        run.reply = block.text.strip()  # the last thing the lead says is the reply
            elif isinstance(msg, ResultMessage):
                run.cost_usd = msg.total_cost_usd
                if msg.is_error:
                    run.error = str(getattr(msg, "result", None) or getattr(msg, "subtype", "error"))


def pick_with_claude(want: str, *, ctx: Any = None, model: str = DEFAULT_MODEL,
                     max_budget_usd: float = DEFAULT_MAX_BUDGET_USD, max_turns: int = DEFAULT_MAX_TURNS) -> PickRun:
    """Let Claude read `want` and call pick_domains. Needs ANTHROPIC_API_KEY."""
    require_api_key()
    run = PickRun()
    options = build_options(build_server(run, ctx), model=model, max_budget_usd=max_budget_usd, max_turns=max_turns)
    asyncio.run(_converse(options, want, run))
    return run


def compare_with_product(run: PickRun, flags: dict[str, bool], domains: list[str]) -> Record:
    """How Claude's pick compares with a product file: flags it set that the file does not, flags it left out,
    and whether the same domains lit up. (A check for people reading the result, not a score.)"""
    got = (run.record or {}).get("flags", {})
    return {
        "flags_only_claude": [f for f, v in got.items() if v and not flags.get(f)],
        "flags_only_file": [f for f, v in flags.items() if v and not got.get(f)],
        "domains_claude": [p["domain"] for p in (run.record or {}).get("picks", [])],
        "domains_file": domains,
        "same_domains": [p["domain"] for p in (run.record or {}).get("picks", [])] == domains,
    }
