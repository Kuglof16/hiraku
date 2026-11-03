"""The MCP server "hiraku": a thin wrapper around hiraku.tools.functions.

Input schemas come from the contract (tool_input.*), with $refs inlined so
each schema stands alone; get_context, list_formulas and list_tools have
their own. Results are JSON text of what the function returns: the record
for record_prediction; for analyze the record without what only the screen
uses (for_agent); {comparison, verdict} for compare; {cause, verdict} for
record_cause; a summary of the new version for revise_design; the asked-for
parts for the reading tools. A ToolError comes back with is_error and its
message (what is wrong, what to do next).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from claude_agent_sdk import create_sdk_mcp_server, tool

from hiraku import contract
from hiraku.intake.tools import TOOLS as INTAKE_TOOLS
from hiraku.tools.attribution import Attribution
from hiraku.tools.context import RunContext, ToolError
from hiraku.tools.functions import CONTEXT_SECTIONS, FUNCTIONS, RUN_TOOLS

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
    "get_context": {"type": "object", "additionalProperties": False, "properties": {
        "sections": {"type": "array", "items": {"enum": list(CONTEXT_SECTIONS)}, "uniqueItems": True,
                     "description": "返してほしい部分。無ければ requirements・load_cases・judgements・records。"
                                    "手計算の入力は design_summary、設計の全体は design、設計役の変数は design_variables"},
    }},
    "list_formulas": _domain_filter({"tool": {"type": "string", "description": "この道具が予測に使う式だけ"}}),
    "list_tools": _domain_filter({
        "tool": {"type": "string", "description": "この道具の全体 (答えの分かっている例と式の一覧)"},
        "test_id": {"type": "string", "description": "この試験を解析できる道具だけ"},
    }),
    "record_prediction": contract_input_schema("record_prediction"),
    "analyze": contract_input_schema("analyze"),
    "compare": contract_input_schema("compare"),
    "record_cause": contract_input_schema("record_cause"),
    "revise_design": contract_input_schema("revise_design"),
    "propose_design": contract_input_schema("propose_design"),
    "make_tool": contract_input_schema("make_tool"),
    "check_tool": contract_input_schema("check_tool"),
    "write_report": contract_input_schema("write_report"),
}

DESCRIPTIONS = {
    "get_context": (
        "この実行の今の様子。いつも: 版・選んだ分野・原因の無い食い違い。sections で足す部分を選ぶ (無ければ "
        "requirements・load_cases (担当の役・基準・許す幅つき)・judgements・records)。手計算の入力の値は "
        "design_summary から、設計の全体は design、設計役の変数は design_variables。要る部分だけを頼む"
    ),
    "list_formulas": "手計算 (K0) の式の一覧。formula_id・入力の名前と単位・出力・前提。tool でその道具の式だけにする",
    "list_tools": (
        "解析の道具の棚 (1 行ずつ)。usable が true の道具だけ analyze で使える。test_id で絞る。"
        "tool を付けるとその道具の全体 (答えの分かっている例・式)"
    ),
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
    "propose_design": (
        "設計役だけが使う。設計がまだ無い (版 0) ときに、型 (archetype)・選択肢 (choices)・変数 (variables) と理由から"
        "最初の版を作る。座標は道具が出し、乗員・荷・最高速度は要件から入る。型と変数は get_context の archetypes。"
        "版 1 ができると試験の荷重条件と担当の役が決まる"
    ),
    "write_report": (
        "まとめ役が最後に 1 回呼ぶ。summary (分野ごとの結果を記録の ID と一緒に) と next (次に変えるなら: 分野・変更・理由) "
        "だけを書く。分野ごとの結果・効いている項・前提・食い違いと原因・確かめていない値は道具が記録から入れる。"
        "文の中の「数 + 単位」は記録にある値と合わなければ断られる"
    ),
    "check_tool": (
        "道具の Python の文 (code) を、その道具自身の答えの分かっている例で、別のプロセスで確かめる。棚には入れない。"
        "通らない例・使えない import・例外は problems に文で返る。make_tool の前の試しに使う"
    ),
    "make_tool": (
        "棚に試験に合う道具が無いときに、道具を書いて棚に入れる。check_tool と同じに確かめ、答えの分かっている例が"
        "全部通れば棚に入り (origin claude)、analyze で使える。code は hiraku.solvers.shelf.base の BuiltinTool を継ぐ"
        "クラス 1 つ (name・title・domain・base・tests・load_case_params、known_answers() は check_record で閉じた式や"
        "教科書の値と比べる例と出典、analyse() は {\"results\": [...]})。使える import は math・numpy・scipy の一部と"
        "hiraku.solvers の一部だけ"
    ),
}

# The intake department's tools (pick_domains, ask_user, import_knowledge, rule_check) bring their
# own description, input schema and function (hiraku.intake.tools.TOOLS).
HANDLERS: dict[str, Callable] = dict(FUNCTIONS)
for _name, _t in INTAKE_TOOLS.items():
    INPUT_SCHEMAS[_name] = _t["input_schema"]
    DESCRIPTIONS[_name] = _t["description"]
    HANDLERS[_name] = _t["function"]


def _text(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


# What an agent reads of an analysis: the run records the whole record (fields, vectors and
# series are for the screen), the agent gets what it predicts and compares against.
ANALYSIS_FOR_AGENT = ("analysis_id", "tool", "base", "design_version", "load_case_id", "prediction_ids", "status",
                      "error", "results", "inputs_from", "assumptions")


def for_agent(name: str, result: Any) -> Any:
    """The part of a tool's result that goes into the calling agent's context."""
    if name == "analyze" and isinstance(result, dict):
        return {k: result[k] for k in ANALYSIS_FOR_AGENT if k in result}
    return result


def make_handler(ctx: RunContext, attribution: Attribution, name: str, fn: Callable) -> Callable:
    full = contract.mcp_name(name)

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        agent = attribution.take(full, args)
        try:
            result = await asyncio.to_thread(fn, ctx, args, agent)
        except ToolError as e:
            return {"content": [{"type": "text", "text": str(e)}], "is_error": True}
        return {"content": [{"type": "text", "text": _text(for_agent(name, result))}]}

    return handler


def build_server(ctx: RunContext, attribution: Attribution, names: tuple[str, ...] = RUN_TOOLS):
    tools = []
    for name in names:
        handler = make_handler(ctx, attribution, name, HANDLERS[name])
        tools.append(tool(name, DESCRIPTIONS[name], INPUT_SCHEMAS[name])(handler))
    return create_sdk_mcp_server(name=contract.MCP_SERVER, version="0.1.0", tools=tools)
