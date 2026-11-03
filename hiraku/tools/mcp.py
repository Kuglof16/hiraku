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
from hiraku.tools.functions import CONTEXT_SECTIONS, DESIGN_PAGE, DESIGN_PARTS, FUNCTIONS, RECORD_KINDS, RUN_TOOLS, rule_check

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


COMPARE_WHERE = {"type": "string", "description": (
    "場所 (部材・部品・支持の ID)。解析の結果が場所ごとに出ていて、予測がその場所の式 (レールの式ならレールの部材) の"
    "ときだけ付ける。その場所の結果と照合する。合否は全部の場所のうち不合格に近い方でしか作らない")}


def compare_input_schema() -> Record:
    """tool_input.compare and `where` (the contract's compare input has no place yet; the tool takes it)."""
    schema = contract_input_schema("compare")
    schema["properties"] = {**schema["properties"], "where": COMPARE_WHERE}
    return schema


API_OF = {"type": "array", "items": {"type": "string"}, "maxItems": 20, "description": (
    "入出力と説明 (docstring) を見たい名前: ツールが import できる HIRAKU のモジュール (例 hiraku.solvers.k4_geometry.rollbar) "
    "か、その中の関数・クラス (例 hiraku.solvers.k4_geometry.rollbar.occupant_clearance)。モジュールなら中の関数の一覧")}


def check_tool_input_schema() -> Record:
    """tool_input.check_tool and `api_of` (the contract's input has no api_of; the tool takes it)."""
    schema = contract_input_schema("check_tool")
    schema["properties"] = {**schema["properties"], "api_of": API_OF}
    return schema


def _domain_filter(extra: Record | None = None) -> Record:
    props: Record = {"domain": {"enum": list(contract.DOMAINS), "description": "この分野だけ"}}
    props.update(extra or {})
    return {"type": "object", "properties": props, "additionalProperties": False}


INPUT_SCHEMAS: dict[str, Record] = {
    "get_context": {"type": "object", "additionalProperties": False, "properties": {
        "sections": {"type": "array", "items": {"enum": list(CONTEXT_SECTIONS)}, "uniqueItems": True,
                     "description": "返してほしい部分。無ければ小さな一覧 (overview: 荷重条件の ID・担当・段、判定の札、記録の数、"
                                    "部分の名前と中身) だけ。手計算の入力は design_summary、設計の要約 (数・ID・変数の値・断面・材料) は design "
                                    "(中身は design_part で少しずつ)、設計役の変数は design_variables。"
                                    "formula_inputs は荷重条件ごとの式の入力 (コードが出した値・単位・出どころ)。"
                                    "joint_wall_inputs は load_case_id の荷重条件での、つなぎ目の壁の曲げの式の入力"},
        "load_case_id": {"type": "string",
                         "description": "load_cases・records・formula_inputs をこの荷重条件だけに絞る。joint_wall_inputs の荷重条件"},
        "domain": {"enum": list(contract.DOMAINS), "description": "load_cases と formula_inputs をこの分野だけに絞る"},
        "record_kinds": {"type": "array", "items": {"enum": list(RECORD_KINDS)}, "uniqueItems": True,
                         "description": "records をこの種類だけに (prediction・analysis・comparison・cause・verdict)"},
        "requirement_keys": {"type": "array", "items": {"type": "string"}, "uniqueItems": True,
                             "description": "requirements をこの要件の key (Q15 など) だけに"},
        "archetype": {"type": "string", "description": "archetypes をこの型だけに"},
        "design_part": {"enum": list(DESIGN_PARTS),
                        "description": "design の一部だけを、offset と limit で少しずつ (無ければ design は全体の要約)"},
        "offset": {"type": "integer", "minimum": 0, "description": "design_part の何番目から (0 から)"},
        "limit": {"type": "integer", "minimum": 1, "description": f"design_part をいくつ (既定 {DESIGN_PAGE})"},
    }},
    "get_tool_template": {"type": "object", "additionalProperties": False, "properties": {"api_of": API_OF}},
    "list_formulas": _domain_filter({"tool": {"type": "string", "description": "このツールが予測に使う式だけ"}}),
    "list_tools": _domain_filter({
        "tool": {"type": "string", "description": "このツールの全体 (答えの分かっている例と式の一覧)"},
        "test_id": {"type": "string", "description": "この試験を解析できるツールだけ"},
    }),
    "record_prediction": contract_input_schema("record_prediction"),
    "analyze": contract_input_schema("analyze"),
    "compare": compare_input_schema(),
    "record_cause": contract_input_schema("record_cause"),
    "revise_design": contract_input_schema("revise_design"),
    "propose_design": contract_input_schema("propose_design"),
    "make_tool": contract_input_schema("make_tool"),
    "check_tool": check_tool_input_schema(),
    "write_report": contract_input_schema("write_report"),
}

DESCRIPTIONS = {
    "get_context": (
        "この実行の今の様子。いつも: 版・選んだ分野・原因の無い食い違い。sections を付けなければ小さな一覧 (overview: "
        "荷重条件の ID・分野・担当・段・feeds、分野ごとの判定の札、記録の種類ごとの数、部分の名前と中身) だけが返る。"
        "sections で要る部分を名前で頼み、大きな部分は絞る (load_cases と formula_inputs は domain か load_case_id、records は "
        "record_kinds と load_case_id、requirements は requirement_keys、archetypes は archetype)。設計を直した版では、最初に "
        "design_changes (前の版から変わった変数の前と後) が付く。手計算の入力の値は "
        "design_summary から、設計の要約は design (部品・部材などの中身は design_part・offset・limit で少しずつ)、設計役の変数は design_variables。要る部分だけを頼む。"
        "record_prediction の前に sections=[\"formula_inputs\"] (と load_case_id) を読む: 荷重条件ごとに、計画の式の"
        "入力を、台本の運転と同じコードがこの版の設計から出した値・単位・出どころ (from) で返す。"
        "シェルのホットスポット (max_hot_spot_stress) を式 frame_static.joint_wall_bending で予測するときは "
        "sections=[\"joint_wall_inputs\"] と load_case_id で、その入力 (横の部材の端のモーメント M など、コードが設計と"
        "荷重条件から出したもの) が返る"
    ),
    "get_tool_template": (
        "ツールを書く (check_tool・make_tool) ときの見本。強度の試験のツール 1 つ分の通して動くコード (code) と、"
        "決まりの説明: 定義するクラスと欄、analyse() の引数 (design・load_case・params) の中身、結果 (results) の欄、"
        "答えの分かっている例 known_answers() と check_record の引数、使える import。examples.k1_frame は K1 の骨組で解く"
        "強度のツールの見本。rule はツールが使えるための決まり (formulas に予測の式、出典つきで 0 でない答えの例)"
    ),
    "list_formulas": (
        "手計算 (K0) の式の一覧。formula_id・入力の名前と単位・出力・前提。tool を付けると {formulas: そのツールの式, "
        "predicts: ツールの結果の量と式の出力の対 (note つき)}"
    ),
    "list_tools": (
        "解析のツールの棚 (1 行ずつ)。usable が true のツールだけ analyze で使える。test_id で絞る。"
        "各行の params は荷重条件でツールが読む名前の短い一覧 (単位 [..]・選べる値 (..)、run は analyze の params)。"
        "tool を付けるとそのツールの全体 (答えの分かっている例・式・params の名前・単位・意味・既定・選べる値)"
    ),
    "record_prediction": (
        "解析の前に、手計算の予測を記録する。式 (formula_id) と入力 (inputs、値と単位) と前提を選ぶだけで、"
        "値は K0 が計算する。返った prediction_id を analyze に渡す。値を自分で計算して書かないこと"
    ),
    "analyze": (
        "解析のツールをすべてこの 1 つの入口から呼ぶ。tool・load_case_id・同じ版と同じ荷重条件の prediction_ids が要る。"
        "結果は analysis の記録 (analysis_id、results)"
    ),
    "compare": (
        "予測と解析の結果を照合する。許す幅は荷重条件で決まっている。一致して基準があれば合否 (verdict、値は解析) も作る。"
        "食い違えば record_cause を書くまでその分野は先に進めない。結果が場所 (where) ごとに出ていて、予測が 1 つの場所の"
        "式 (レールの式など) のときは where に部材の ID を付け、その場所の結果と比べる (付けないと全部の場所の不利な方、"
        "別の部材の最大と比べてしまう)。合否は全部の場所のうち不合格に近い方でしか作らない"
    ),
    "record_cause": (
        "食い違った照合の原因と直し方を書く。これで分野の止まりが解ける。原因で差が説明でき、予測が本当の値を"
        "安全側から押さえていると判断できるときだけ safe_side: true と理由 (safe_side_reason) を付ける。"
        "ツールが数字の向きを確かめ、合っていれば予測 (不利な方の値) で合否を作る。向きが逆なら断る"
    ),
    "revise_design": (
        "設計役だけが使う。設計の変数 (design.variables) の新しい値 (changes) と理由 (reason) から、"
        "次の版の設計を組み直して記録する。回数は round_limit まで。新しい版では全部の荷重条件の判定がやり直しになる"
    ),
    "propose_design": (
        "設計役だけが使う。設計がまだ無い (版 0) ときに、型 (archetype)・選択肢 (choices)・変数 (variables) と理由から"
        "最初の版を作る。座標はツールが出し、乗員・荷・最高速度は要件から入る。型と変数は get_context の archetypes。"
        "版 1 ができると試験の荷重条件と担当の役が決まる"
    ),
    "write_report": (
        "まとめ役が最後に 1 回呼ぶ。summary (分野ごとの結果を記録の ID と一緒に) と next (次に変えるなら: 分野・変更・理由) "
        "だけを書く。分野ごとの結果・効いている項・前提・食い違いと原因・確かめていない値はツールが記録から入れる。"
        "文の中の「数 + 単位」は記録にある値と合わなければ断られる"
    ),
    "check_tool": (
        "ツールの Python の文 (code) を、そのツール自身の答えの分かっている例で、別のプロセスで確かめる。棚には入れない。"
        "通らない例・使えない import・例外・決まり (rule) に合わないことは problems に文で返る。api はツールが import して"
        "呼べるモジュール・関数・クラス (toolmaker の許す一覧から)。api_of に名前を付けると、その関数の入出力と説明が返る。"
        "make_tool の前の試しに使う"
    ),
    "make_tool": (
        "棚に試験に合うツールが無いときに、ツールを書いて棚に入れる。check_tool と同じに確かめ、答えの分かっている例が"
        "全部通れば棚に入り (origin claude)、analyze で使える。code は hiraku.solvers.shelf.base の BuiltinTool を継ぐ"
        "クラス 1 つ (name・title・domain・base・tests・formulas・load_case_params、known_answers() は check_record で閉じた式や"
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
HANDLERS["rule_check"] = rule_check  # the intake's, then the assembly tile again (system rules)


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


# Tools only one role may call; the functions check the caller's agent id. Through MCP the caller is
# known only from the PreToolUse hook's record (Attribution), so a call without one is refused
# instead of reaching the function with agent None (which it would take as the allowed role).
ROLE_LIMITED_TOOLS = ("propose_design", "revise_design", "write_report")
NO_CALLER = ("{tool} は、どの役が呼んだのかを確かめられなかったので断りました (この入力の呼び出しの記録が"
             "見つかりません)。同じ入力でもう一度呼んでください")


# Refusals kept in the record as an agent.said of system (data.refused, data.reason): a refused first
# version leaves the run with nothing to judge, and HK4 lets the stop through on it (rules.design_refusal).
RECORDED_REFUSALS = ("propose_design",)


TOOL_ERRORS_FILE = "tool.errors.log"  # in the run folder: the traceback of each exception a tool let out


def tool_failure(ctx: RunContext, name: str, args: dict[str, Any], agent: str | None, e: BaseException) -> str:
    """Write the exception's traceback to <run>/TOOL_ERRORS_FILE, say it in the record (agent.said of system,
    data.tool_error), and give the caller a sentence to act on (the same call can be tried again)."""
    import traceback
    from datetime import datetime

    what = f"{type(e).__name__}: {e}"
    try:
        with (ctx.store.path / TOOL_ERRORS_FILE).open("a", encoding="utf-8") as f:
            f.write(f"--- {datetime.now().isoformat(timespec='seconds')} {name} by {agent or '?'}\n"
                    f"args: {json.dumps(args, ensure_ascii=False, default=str)[:2000]}\n"
                    + "".join(traceback.format_exception(type(e), e, e.__traceback__)))
        ctx.store.emit("agent.said", agent="system", text=f"ツール {name} の中で思わぬ失敗がありました ({what[:300]})",
                       data={"tool_error": name, "agent": agent, "error": what[:2000], "log": TOOL_ERRORS_FILE})
    except Exception:  # the answer below still goes back
        pass
    return (f"ツール {name} の中で思わぬ失敗がありました ({what[:500]})。HIRAKU の側の不具合で、記録 ({TOOL_ERRORS_FILE}) に"
            "残しました。同じ入力でもう一度呼んでください。同じ失敗が続くときは、そう報告して先へ進んでください")


def make_handler(ctx: RunContext, attribution: Attribution, name: str, fn: Callable) -> Callable:
    full = contract.mcp_name(name)

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        agent = attribution.take(full, args)
        if agent is None and name in ROLE_LIMITED_TOOLS:
            return {"content": [{"type": "text", "text": NO_CALLER.format(tool=name)}], "is_error": True}
        try:
            result = await asyncio.to_thread(fn, ctx, args, agent)
        except ToolError as e:
            if name in RECORDED_REFUSALS:  # HK4 reads it (rules.design_refusal): the run may end on it
                ctx.store.emit("agent.said", agent="system", text=f"{name} は断られました: {e}",
                               data={"refused": name, "agent": agent, "reason": str(e)})
            return {"content": [{"type": "text", "text": str(e)}], "is_error": True}
        except Exception as e:  # a bug in a tool: kept with its traceback in the run folder, the caller told
            return {"content": [{"type": "text", "text": tool_failure(ctx, name, args, agent, e)}], "is_error": True}
        return {"content": [{"type": "text", "text": _text(for_agent(name, result))}]}

    return handler


def build_server(ctx: RunContext, attribution: Attribution, names: tuple[str, ...] = RUN_TOOLS):
    tools = []
    for name in names:
        handler = make_handler(ctx, attribution, name, HANDLERS[name])
        tools.append(tool(name, DESCRIPTIONS[name], INPUT_SCHEMAS[name])(handler))
    return create_sdk_mcp_server(name=contract.MCP_SERVER, version="0.1.0", tools=tools)
