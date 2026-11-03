"""The MCP tools of the intake department: pick_domains, ask_user, import_knowledge, rule_check.

The functions take (ctx, args, agent) like the functions in hiraku.tools.functions, and raise ToolError
(message = what is wrong and what to send instead). TOOLS gives, per tool, the description Claude reads,
the input schema and the function; hiraku/tools/mcp.py registers them in the "hiraku" server.

Each tool does its numbers in code. Claude supplies words: the flags a sentence implies and why, the
reason for each domain, the wording of a question, and the formula of a rule written as a sentence.
What a tool records is an event; until the contract has the event kinds the tool returns the same record
and says `recorded: false` with the reason (hiraku.intake.events).
"""

from __future__ import annotations

import weakref
from pathlib import Path
from typing import Any, Callable

from hiraku import contract
from hiraku.catalog import REPO_ROOT
from hiraku.contract import layout
from hiraku.intake import events as ev
from hiraku.intake import facts, hearing, knowledge, rulecheck
from hiraku.intake.domains import IntakeError, pick_domains, reasons_missing
from hiraku.intake.flags import FLAGS, SPEED_CLASSES
from hiraku.intake.products import PRODUCTS_DIR, load_product, product_files
from hiraku.tools.context import ToolError

Record = dict[str, Any]


def _guard(fn: Callable[..., Record]) -> Callable[..., Record]:
    def wrapped(ctx: Any, args: dict[str, Any] | None = None, agent: str | None = None) -> Record:
        try:
            return fn(ctx, dict(args or {}), agent)
        except IntakeError as e:
            raise ToolError(str(e)) from None

    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    return wrapped


def _store(ctx: Any) -> Any:
    return getattr(ctx, "store", None)


def _emit(ctx: Any, event: Record, *, design_version: int | None = None) -> str | None:
    return ev.emit_if_known(_store(ctx), event, design_version=design_version)


# ------------------------------------------------------------------------------------------ pick_domains


@_guard
def pick_domains_tool(ctx: Any, args: Record, agent: str | None) -> Record:
    record = pick_domains(
        args.get("want", ""), args.get("flags") or {}, args.get("speed_class"),
        flag_reasons=args.get("flag_reasons"), reasons=args.get("reasons"), shown_names=args.get("shown_names"),
        exclude=args.get("exclude"),
    )
    problem = _emit(ctx, ev.picked_event(record))
    out: Record = {**record, "recorded": problem is None}
    if problem:
        out["not_recorded"] = problem
    missing = reasons_missing(record, args.get("reasons"))
    if missing:
        names = "・".join(p["shown_name"] for p in record["picks"] if p["domain"] in missing)
        out["reasons_missing"] = missing
        out["next"] = (f"{names} の理由はコードが仮に書きました。この製品に合わせた理由 (reasons) を付けて"
                       "もう一度呼ぶと、画面の札に出ます")
    return out


# --------------------------------------------------------------------------------------------- ask_user


def _requirements_file(ctx: Any) -> Record:
    p = ctx.project
    req: Record = {"want": p["want"], "product": p["product"], "flags": p["flags"], "requirements": p["requirements"]}
    if "speed_class" in p:
        req["speed_class"] = p["speed_class"]
    return req


def _product_defaults(slug: str, current: Record) -> dict[str, Record]:
    """The values of the product's own requirements file, for a product HIRAKU knows (else none)."""
    if slug not in product_files():
        return {}
    have = {r["key"] for r in current["requirements"]}
    return {r["key"]: r for r in load_product(slug)["requirements"] if r["key"] not in have}


def _answers_from_file(path: str) -> tuple[dict[str, Any], str | None]:
    p = (REPO_ROOT / path).resolve()
    allowed = PRODUCTS_DIR.resolve()
    if not p.is_relative_to(allowed):
        raise IntakeError(f"答えのファイルは data/requirements/products/ の中のものだけ読めます ({path!r})")
    if not p.is_file():
        raise IntakeError(f"答えのファイル {path!r} がありません")
    return hearing.load_answers(p)


@_guard
def ask_user_tool(ctx: Any, args: Record, agent: str | None) -> Record:
    answers: dict[str, Any] = dict(args.get("answers") or {})
    note = args.get("note")
    if args.get("answers_file"):
        file_answers, file_note = _answers_from_file(args["answers_file"])
        answers = {**file_answers, **answers}
        note = note or file_note
    req = _requirements_file(ctx)
    defaults = _product_defaults(req["product"], req)
    wording, choices = args.get("wording"), args.get("choices")
    problems: list[str] = []
    asked_early = False
    wait = float(args.get("wait_seconds") or 0)
    run_path = getattr(_store(ctx), "path", None)
    if wait > 0 and not answers and run_path is not None:
        # wait for a person: show the questions now (A2), then take what the screen writes into the run folder
        plan = hearing.plan_hearing(req, product_defaults=defaults, wording=wording, choices=choices)
        if plan.questions:
            hearing.clear_mailbox(run_path)
            if (p := _emit(ctx, ev.asked_event({"questions": plan.questions}))):
                problems.append(p)
            asked_early = True
            hearing.write_request(run_path, {"questions": plan.questions})
            got = hearing.wait_for_answer(run_path, wait)
            hearing.clear_mailbox(run_path)
            if got:
                answers = dict(got.get("answers") or {})
                note = note or got.get("note")
    prepared = hearing.prepare(req, answers, note, product_defaults=defaults, wording=wording, choices=choices)
    to_emit = [e for e in ev.hearing_events(prepared) if not (asked_early and e["kind"] == ev.HEARING_ASKED)]
    problems += [p for e in to_emit if (p := _emit(ctx, e))]
    new = prepared.answered["answers"]
    recorded_req = False
    update = getattr(_store(ctx), "update_project", None)
    if update is not None and new:
        # the answers and defaults join project.requirements (the same key is replaced); project.updated says so
        merged = {r["key"]: r for r in ctx.project["requirements"]}
        merged.update({r["key"]: r for r in new})
        update({"requirements": [merged[k] for k in sorted(merged)]}, agent="lead", text="ヒアリングの答えを要件に入れました")
        recorded_req = True
    out: Record = {
        "text": hearing.hearing_text(prepared),
        "questions": prepared.asked["questions"],
        "answers": new,
        "unresolved": prepared.unresolved,
        "ready": prepared.ready,
        "requirements_recorded": recorded_req,
        "recorded": not problems,
    }
    if not recorded_req and new:
        out["requirements_to_add"] = new  # the run store has no update_project: the caller adds them
    if problems:
        out["not_recorded"] = problems[0]
    if not prepared.ready:
        keys = "・".join(q["req_key"] for q in prepared.asked["questions"] if q["ask"] == "must" and q["req_key"] in prepared.unresolved)
        out["next"] = (f"必須の質問 {keys} に答えがありません。人に聞いて、言われたことだけを answers に入れてもう一度呼んでください "
                       "(推測で埋めない)")
    return out


# ------------------------------------------------------------------------------------ import_knowledge


# What this process imported for a run, kept next to the events: while the contract has no
# knowledge.imported event the events cannot carry the dropped files' rules from one tool call to the next.
_imported: "weakref.WeakKeyDictionary[Any, dict[str, Record]]" = weakref.WeakKeyDictionary()


def _remember(ctx: Any, rec: Record) -> None:
    store = _store(ctx)
    if store is not None:
        _imported.setdefault(store, {})[rec["doc"]["doc_id"]] = rec


def _knowledge_docs(ctx: Any) -> dict[str, Record]:
    """doc_id -> the last knowledge_imported record of this run (the last one for an id wins)."""
    docs: dict[str, Record] = {}
    store = _store(ctx)
    if store is None:
        return docs
    for e in store.events():
        if e["kind"] == ev.KNOWLEDGE_IMPORTED:
            docs[e["data"]["doc"]["doc_id"]] = e["data"]
    docs.update(_imported.get(store, {}))
    return docs


def _ensure_builtin(ctx: Any) -> list[str]:
    """Emit the built-in documents once per run, so the screen can show the tree from the events alone."""
    have = _knowledge_docs(ctx)
    problems = []
    for rec in knowledge.load_builtin():
        if rec["doc"]["doc_id"] not in have:
            _remember(ctx, rec)
            p = _emit(ctx, ev.knowledge_event(rec))
            if p:
                problems.append(p)
                break
    return problems


def _read_dropped(ctx: Any, args: Record) -> tuple[str, str]:
    if args.get("text") is not None:
        name = args.get("filename") or "dropped.txt"
        return name, str(args["text"])
    rel = args.get("path")
    store = _store(ctx)
    if not rel or store is None:
        raise IntakeError("読むファイルを text (中身) と filename で渡すか、実行のフォルダの中の path で渡してください")
    base = (Path(store.path) / layout.KNOWLEDGE_DIR).resolve()
    p = (Path(store.path) / rel).resolve()
    if not p.is_relative_to(base) or not p.is_file():
        raise IntakeError(f"path は実行のフォルダの {layout.KNOWLEDGE_DIR}/ の中のファイルだけです ({rel!r})")
    raw = p.read_bytes()
    try:
        return p.name, raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            return p.name, raw.decode("cp932")  # a CSV saved by Excel in Japan
        except UnicodeDecodeError:
            raise IntakeError(f"{p.name} を文字として読めません (UTF-8 か Shift_JIS で保存してください)") from None


@_guard
def import_knowledge_tool(ctx: Any, args: Record, agent: str | None) -> Record:
    filename, text = _read_dropped(ctx, args)
    docs = _knowledge_docs(ctx)
    existing = {d["doc"]["filename"]: d["doc"]["doc_id"] for d in docs.values() if d["doc"]["origin"] == "dropped"}
    doc_id = knowledge.doc_id_for(filename, existing)
    got = knowledge.import_text(filename, text, doc_id=doc_id, formulas=args.get("formulas"), skip=args.get("skip"))
    problems = _ensure_builtin(ctx)
    if got.record["rules"] or got.record["doc"].get("skipped"):
        _remember(ctx, got.record)
        p = _emit(ctx, ev.knowledge_event(got.record))
        if p:
            problems.append(p)
    out: Record = {
        "doc_id": doc_id,
        "rules": [{k: r[k] for k in ("rule_id", "category", "text", "check", "check_by") if k in r} for r in got.record["rules"]],
        "needs_formula": got.needs_formula,
        "recorded": not problems,
    }
    if problems:
        out["not_recorded"] = problems[0]
    if got.needs_formula:
        out["next"] = ("needs_formula の文は、式 (check) を書いて formulas に入れてもう一度呼んでください "
                       "(同じ filename と text で。式は 1 つの部品について、はい / いいえで答える形)。規則にならない文は skip に理由を書きます")
        out["facts"] = [{k: v[k] for k in ("name", "label", "unit")} for v in facts.vocabulary() if v["in_designs"]]
        out["categories"] = knowledge.CATEGORIES
    return out


# ------------------------------------------------------------------------------------------ rule_check


def _enabled_rules(ctx: Any) -> list[Record]:
    rules = knowledge.all_rules(knowledge.load_builtin())
    for rec in _knowledge_docs(ctx).values():
        if rec["doc"]["origin"] == "dropped":
            rules.extend(rec["rules"])
    return rules


@_guard
def rule_check_tool(ctx: Any, args: Record, agent: str | None) -> Record:
    store = _store(ctx)
    if store is None or not store.design_version:
        raise IntakeError("規則を当てる設計がまだありません。設計の版ができてから呼んでください")
    version = args.get("design_version") or store.design_version
    design = store.design(version)
    rules = _enabled_rules(ctx)
    disabled = set(args.get("disable") or [])
    stray = sorted(disabled - {r["rule_id"] for r in rules})
    if stray:
        raise IntakeError(f"規則 {', '.join(stray)} はありません。使えるのは {', '.join(r['rule_id'] for r in rules)} です")
    problems = _ensure_builtin(ctx)
    persist = ev.known(ev.RULE_CHECK_RECORDED)
    ids = (lambda: store.next_id("rule_check")) if persist else None
    checks = rulecheck.check_all(rules, design, ctx.project["requirements"], disabled=disabled, id_factory=ids)
    if persist:
        through_store = ev.store_records(store, "rule_check")  # the store writes rchk-NNNN.json and emits the event
        for c in checks:
            if through_store:
                try:
                    store.add_record("rule_check", c, design_version=version, agent="packager")
                    continue
                except KeyError:  # the store's table changed shape: the event alone from here on
                    through_store = False
            if (p := _emit(ctx, {"kind": ev.RULE_CHECK_RECORDED, "agent": "packager", "data": c}, design_version=version)):
                problems.append(p)
                break
    else:
        problems.append(f"event kind {ev.RULE_CHECK_RECORDED} is not in the contract yet")
    summary = rulecheck.summarize(checks)
    return {
        "design_version": version,
        "summary": summary,
        "text": rulecheck.summary_line(checks, rules),
        "hits": [{"rule_id": c["rule_id"], "text": next(r["text"] for r in rules if r["rule_id"] == c["rule_id"]),
                  "parts": [h["part_id"] for h in c["hits"]]} for c in checks if c["hits"]],
        "not_evaluable": [{"rule_id": c["rule_id"], "missing": sorted({m for n in c["not_evaluable"] for m in n["missing"]})}
                          for c in checks if c["not_evaluable"]],
        "recorded": persist and not problems,
        **({"not_recorded": problems[0]} if problems else {}),
    }


# ------------------------------------------------------------------------------------- the definitions
# The input of each tool is the contract's tool_input.<name> (hiraku.contract: the lead owns the shapes);
# only the description Claude reads and the function live here.

_FLAG_LINES = "\n".join(f"- {name} ({sym}): {q}" for name, (sym, q) in FLAGS.items())
_SPEED_LINES = "\n".join(f"- {name} ({sym}): {m}" for name, (sym, m) in SPEED_CLASSES.items())


def _inline_refs(node: Any, defs: Record) -> Any:
    """Replace every {"$ref": "#/$defs/x"} with a copy of defs[x], so the schema stands alone."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            rest = {k: v for k, v in node.items() if k != "$ref"}
            return {**_inline_refs(defs[ref.removeprefix("#/$defs/")], defs), **_inline_refs(rest, defs)}
        return {k: _inline_refs(v, defs) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline_refs(v, defs) for v in node]
    return node


def contract_input(name: str) -> Record:
    """The input schema of a tool from the contract, with its $refs inlined and its def description dropped."""
    defs = contract.schema()["$defs"]
    schema = _inline_refs(defs[f"tool_input.{name}"], defs)
    schema.pop("description", None)
    return schema


TOOLS: dict[str, Record] = {
    "pick_domains": {
        "description": (
            "作りたいもの (文) から、使う解析の分野を選ぶ。まとめ役が使う。flags は文から読み取った製品の性質 "
            "(true の物だけでよい。理由は flag_reasons に 1 つずつ)。分野とその試験は、カタログの発動条件からコードが決める "
            "(あなたは決めない)。あなたが書くのは: 各分野を使う (使わない) 理由 reasons、製品に合わせた分野の呼び名 shown_names "
            "(たとえば配膳ロボの空力は「液体のこぼれ」)。理由が足りない分野はコードの仮の理由が付くので、結果の reasons_missing "
            "を見て付け足す。人が札を外したときは exclude。\n製品の性質:\n" + _FLAG_LINES + "\n速さの域 speed_class:\n" + _SPEED_LINES
        ),
        "input_schema": contract_input("pick_domains"),
        "function": pick_domains_tool,
    },
    "ask_user": {
        "description": (
            "要件のうち、この製品に要るのに実行の要件に無いもの (必須・確認) を、まとめて 1 回聞く。質問と選択肢はコードが "
            "項目の表から作る。答えが無い確認の質問は、出どころつきの既定値を使う。必須の質問は答えが無いと進めない。"
            "answers には、人が実際に言ったことだけを {要件の番号: 値 か {value, unit, basis}} で入れる (推測で埋めない)。"
            "answers_file は data/requirements/products/ の答えのファイル。質問を製品に合わせて言い換えたいときは wording "
            "({番号: 文})、選択肢を変えたいときは choices。画面の人の答えを待ちたいときは wait_seconds (質問を画面に出し、"
            "答えが来るか時間が切れるまで待つ)。"
        ),
        "input_schema": contract_input("ask_user"),
        "function": ask_user_tool,
    },
    "import_knowledge": {
        "description": (
            "人が渡したファイル (Markdown・CSV/TSV の表・テキスト) を読み、規則にする。text と filename で渡す "
            "(または実行のフォルダの knowledge/ の中の path)。「A は B 以上」のような文はコードが式にする。式にできなかった文は "
            "結果の needs_formula に番号 (c3 など) つきで返る: それぞれ、1 つの部品について はい / いいえ で答える式 check "
            "(結果の facts の名前だけ。要件は req_Q15、設計の変数は var_<名前>) を書いて formulas={番号: {check, kinds?, category?}} "
            "に入れ、同じ filename と text (path で渡したなら同じ path) でもう一度呼ぶ。規則にならない文は skip={番号: 理由}。"
            "式の計算はコードがする。kinds は対象の部品の種類 (battery, seat, body など。frame は骨組み、support は接地点)。"
        ),
        "input_schema": contract_input("import_knowledge"),
        "function": import_knowledge_tool,
    },
    "rule_check": {
        "description": (
            "ナレッジの規則 (最初から入っているものと、渡されたファイルのもの) を設計の部品に当てる。パッケージ役が使う。"
            "結果は、当たった (規則に合わない) 部品、式に要る値が設計に無くて判定できなかった規則、数。disable は使わない規則の番号。"
            "規則が増えたとき、設計を直したあとにもう一度呼ぶ。"
        ),
        "input_schema": contract_input("rule_check"),
        "function": rule_check_tool,
    },
}

assert all(t in contract.MCP_TOOLS_LATER for t in TOOLS)
