"""The MCP server "hiraku": a thin wrapper around hiraku.tools.functions.

Input schemas come from the contract (tool_input.*), with $refs inlined so
each schema stands alone. Results are the records as JSON text; a ToolError
comes back with is_error and its message (what is wrong, what to do next).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from claude_agent_sdk import create_sdk_mcp_server, tool

from hiraku import contract
from hiraku.tools.attribution import Attribution
from hiraku.tools.context import RunContext, ToolError
from hiraku.tools.functions import FUNCTIONS, REVISE_DESIGN_INPUT, RUN_TOOLS

Record = dict[str, Any]


def inline_refs(node: Any, defs: dict[str, Any]) -> Any:
    """Replace every {"$ref": "#/$defs/x"} with a copy of defs[x], recursively."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            rest = {k: v for k, v in node.items() if k != "$ref"}
            return {**inline_refs(defs[ref.removeprefix("#/$defs/")], defs), **inline_refs(rest, defs)}
        return {k: inline_refs(v, defs) for k, v in node.items()}
    if isinstance(node, list):
        return [inline_refs(v, defs) for v in node]
    return node


def contract_input_schema(name: str) -> Record:
    defs = contract.schema()["$defs"]
    schema = inline_refs(defs[f"tool_input.{name}"], defs)
    schema.pop("description", None)
    return schema


def _domain_filter(extra: Record | None = None) -> Record:
    props: Record = {"domain": {"enum": list(contract.DOMAINS), "description": "この分野だけ"}}
    props.update(extra or {})
    return {"type": "object", "properties": props, "additionalProperties": False}


INPUT_SCHEMAS: dict[str, Record] = {
    "get_context": {"type": "object", "properties": {}, "additionalProperties": False},
    "list_formulas": _domain_filter({"tool": {"type": "string", "description": "この道具の式だけ (formula_id の . の前)"}}),
    "list_tools": _domain_filter(),
    "record_prediction": contract_input_schema("record_prediction"),
    "analyze": contract_input_schema("analyze"),
    "compare": contract_input_schema("compare"),
    "record_cause": contract_input_schema("record_cause"),
    "revise_design": REVISE_DESIGN_INPUT,
}

DESCRIPTIONS = {
    "get_context": (
        "この実行の要件・荷重条件 (load_cases、担当の役と基準と許す幅つき)・今の版の設計・判定・"
        "これまでの予測/解析/照合/原因/合否を返す。式の入力の値はここから取る"
    ),
    "list_formulas": "手計算 (K0) の式の一覧。formula_id・入力の名前と単位・出力・前提",
    "list_tools": "解析の道具の棚。usable が true の道具だけ analyze で使える",
    "record_prediction": (
        "解析の前に、手計算の予測を記録する。式 (formula_id) と入力 (inputs、値と単位) と前提を選ぶだけで、"
        "値は K0 が計算する。返った prediction_id を analyze に渡す。値を自分で計算して書かないこと"
    ),
    "analyze": (
        "解析の道具をすべてこの 1 つの入口から呼ぶ。tool・load_case_id・同じ版と同じ荷重条件の prediction_ids が要る。"
        "結果は analysis の記録 (analysis_id、results)"
    ),
    "compare": (
        "予測と解析の結果を照合する。許す幅は荷重条件で決まっている。一致して基準があれば合否 (verdict、値は解析) も作る。"
        "食い違えば record_cause を書くまでその分野は先に進めない"
    ),
    "record_cause": (
        "食い違った照合の原因と直し方を書く。これで分野の止まりが解ける。原因で差が説明でき、予測が本当の値を"
        "安全側から押さえていると判断できるときだけ safe_side: true と理由 (safe_side_reason) を付ける。"
        "道具が数字の向きを確かめ、合っていれば予測 (不利な方の値) で合否を作る。向きが逆なら断る"
    ),
    "revise_design": (
        "設計役だけが使う。設計の変数 (design.variables) の新しい値 (changes) と理由 (reason) から、"
        "次の版の設計を組み直して記録する。回数は round_limit まで。新しい版では全部の荷重条件の判定がやり直しになる"
    ),
}


def _text(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def make_handler(ctx: RunContext, attribution: Attribution, name: str, fn: Callable) -> Callable:
    full = contract.mcp_name(name)

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        agent = attribution.take(full, args)
        try:
            result = await asyncio.to_thread(fn, ctx, args, agent)
        except ToolError as e:
            return {"content": [{"type": "text", "text": str(e)}], "is_error": True}
        return {"content": [{"type": "text", "text": _text(result)}]}

    return handler


def build_server(ctx: RunContext, attribution: Attribution, names: tuple[str, ...] = RUN_TOOLS):
    tools = []
    for name in names:
        handler = make_handler(ctx, attribution, name, FUNCTIONS[name])
        tools.append(tool(name, DESCRIPTIONS[name], INPUT_SCHEMAS[name])(handler))
    return create_sdk_mcp_server(name=contract.MCP_SERVER, version="0.1.0", tools=tools)
