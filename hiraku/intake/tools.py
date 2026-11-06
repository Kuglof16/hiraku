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

import json
import weakref
from pathlib import Path
from typing import Any, Callable

from hiraku import contract
from hiraku.catalog import REPO_ROOT
from hiraku.contract import layout
from hiraku.contract.protocols import ToolError
from hiraku.intake import events as ev
from hiraku.intake import facts, hearing, knowledge, rulecheck, tradeoff
from hiraku.intake.domains import IntakeError, pick_domains, reasons_missing
from hiraku.intake.flags import FLAGS, SPEED_CLASSES
from hiraku.intake.products import ANSWERS_SUFFIX, PRODUCTS_DIR, load_product, product_files

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


def _same_as_last(ctx: Any, event: Record) -> bool:
    """Is this event's data what the run's newest event of the same kind already says?

    A tool may be called again for the same thing (the agent asks the same questions twice, picks the same
    domains twice); the run records a change once, not each call."""
    store = _store(ctx)
    events = getattr(store, "events", None)
    if events is None:
        return False
    mine = json.loads(json.dumps(event["data"], ensure_ascii=False))
    for e in reversed(events()):
        if e.get("kind") == event["kind"]:
            return e.get("data") == mine
    return False


def _emit_changed(ctx: Any, event: Record) -> tuple[str | None, bool]:
    """(problem, repeated): emit unless the run's newest event of this kind already says the same."""
    if _same_as_last(ctx, event):
        return None, True
    return _emit(ctx, event), False


# ------------------------------------------------------------------------------------------ pick_domains


@_guard
def pick_domains_tool(ctx: Any, args: Record, agent: str | None) -> Record:
    record = pick_domains(
        args.get("want", ""), args.get("flags") or {}, args.get("speed_class"),
        flag_reasons=args.get("flag_reasons"), reasons=args.get("reasons"), shown_names=args.get("shown_names"),
        exclude=args.get("exclude"), product=(getattr(ctx, "project", None) or {}).get("product"),
    )
    problem, repeated = _emit_changed(ctx, ev.picked_event(record))
    out: Record = {**record, "recorded": problem is None}
    if repeated:
        out["unchanged"] = "前に記録した分野の選びと同じなので、記録は増やしていません"
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
    """The answers file named by `path`: relative to the repository (data/requirements/products/<name>), or just
    <name> (looked up in the products folder), with or without the .answers.yaml ending."""
    allowed = PRODUCTS_DIR.resolve()
    names = [path, f"{path}{ANSWERS_SUFFIX}"]
    tries = [(root / n).resolve() for n in names for root in (REPO_ROOT, PRODUCTS_DIR)]
    inside = [p for p in tries if p.is_relative_to(allowed)]
    if not inside:
        raise IntakeError(f"答えのファイルは data/requirements/products/ の中のものだけ読めます ({path!r})。"
                          "data/requirements/products/<名前>.answers.yaml のパスで渡してください")
    for p in inside:
        if p.is_file():
            return hearing.load_answers(p)
    have = ", ".join(sorted(f.name for f in allowed.glob(f"*{ANSWERS_SUFFIX}")))
    raise IntakeError(f"答えのファイル {path!r} がありません。data/requirements/products/ にあるのは: {have}。"
                      "data/requirements/products/<名前>.answers.yaml のパスで渡してください")


def _rule_questions(ctx: Any, raw: Any) -> list[Record]:
    """The rule hits the lead wants the person to decide on, checked: the contract's shape, rules this run has."""
    out: list[Record] = []
    known = {r["rule_id"] for r in _enabled_rules(ctx)}
    advice_ids = {r["rule_id"] for r in _enabled_rules(ctx) if r.get("level") == "advice"}
    for q in raw or []:
        problems = contract.errors("rule_question", q)
        if problems:
            raise IntakeError("rule_hits の 1 件が契約の形ではありません: " + "; ".join(str(p) for p in problems[:2]))
        if q["rule_id"] not in known:
            raise IntakeError(f"規則 {q['rule_id']} はこの実行にありません。ある規則: {', '.join(sorted(known))}")
        if q["rule_id"] in advice_ids:
            raise IntakeError(f"規則 {q['rule_id']} は助言 (level advice) なので、人には聞きません (記録に助言として残ります)")
        out.append({**q, "choices": list(q.get("choices") or hearing.RULE_CHOICES)})
    return out


def dropped_rules(ctx: Any) -> dict[str, str]:
    """rule_id -> why, for the rules a person dropped for this run (rule_answers of hearing.answered, the newest wins)."""
    events = getattr(_store(ctx), "events", None)
    out: dict[str, str] = {}
    for e in events() if events else []:
        if e.get("kind") != ev.HEARING_ANSWERED:
            continue
        for a in (e.get("data") or {}).get("rule_answers", []):
            if a.get("choice") == "drop_rule":
                out[a["rule_id"]] = a.get("note") or "人が、この実行ではこの規則を外すと答えた"
            else:
                out.pop(a["rule_id"], None)
    return out


@_guard
def ask_user_tool(ctx: Any, args: Record, agent: str | None) -> Record:
    rule_questions = _rule_questions(ctx, args.get("rule_hits"))
    rule_answers: list[Record] = []
    tradeoff_questions = _tradeoff_questions(ctx, args.get("tradeoff_ids"))
    tradeoff_answers: list[Record] = []
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
    got_answers: dict[str, Any] = {}
    plan = None
    wait = float(args.get("wait_seconds") or 0)
    run_path = getattr(_store(ctx), "path", None)
    if wait > 0 and not answers and run_path is not None:
        # wait for a person: show the questions now (A2), then take what the screen writes into the run folder
        plan = hearing.plan_hearing(req, product_defaults=defaults, wording=wording, choices=choices)
        if plan.questions or rule_questions or tradeoff_questions:
            hearing.clear_mailbox(run_path)
            asked = {"questions": plan.questions, "rule_questions": rule_questions, "tradeoff_questions": tradeoff_questions}
            problem, repeated = _emit_changed(ctx, ev.asked_event(asked))
            if problem:
                problems.append(problem)
            asked_early = True
            hearing.write_request(run_path, {k: v for k, v in asked.items() if v})
            got = hearing.wait_for_answer(run_path, wait)
            hearing.clear_mailbox(run_path)
            if got:
                answers = dict(got.get("answers") or {})
                got_answers = answers
                note = note or got.get("note")
                rule_answers = hearing.check_rule_answers(got.get("rule_answers"), rule_questions)
                tradeoff_answers = tradeoff.check_answers(got.get("tradeoff_answers"), tradeoff_questions)
    from_screen = bool(got_answers)
    try:
        prepared = hearing.prepare(req, answers, note, product_defaults=defaults, wording=wording, choices=choices,
                                   skip_present=from_screen)
    except IntakeError as e:
        if not (from_screen and run_path is not None and asked_early):
            raise
        # the screen's answer was refused here: keep the question open (A2 still asks) and say what to do, instead of
        # closing the hearing with no answers
        hearing.write_request(run_path, {k: v for k, v in {"questions": plan.questions, "rule_questions": rule_questions,
                                                           "tradeoff_questions": tradeoff_questions}.items() if v})
        return {"recorded": False, "answers": [], "ready": False, "refused": str(e),
                "next": f"画面の答えを受け付けられませんでした ({e})。聞き取りは開いたままです。もう一度 wait_seconds をつけて呼ぶと、答え直しを待ちます"}
    if rule_questions:
        prepared.asked["rule_questions"] = rule_questions
    if rule_answers:
        prepared.answered["rule_answers"] = rule_answers
    if tradeoff_questions:
        prepared.asked["tradeoff_questions"] = tradeoff_questions
    if tradeoff_answers:
        prepared.answered["tradeoff_answers"] = tradeoff_answers
    to_emit = [e for e in ev.hearing_events(prepared) if not (asked_early and e["kind"] == ev.HEARING_ASKED)]
    unchanged = []
    for e in to_emit:  # the same questions, or the same (empty) answers, are not recorded again
        problem, repeated = _emit_changed(ctx, e)
        if repeated:
            unchanged.append(e["kind"])
        elif problem:
            problems.append(problem)
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
    if rule_questions:
        out["rule_questions"] = rule_questions
        out["rule_answers"] = rule_answers
        waiting = [q["rule_id"] for q in rule_questions if q["rule_id"] not in {a["rule_id"] for a in rule_answers}]
        if waiting:
            out["rules_waiting"] = waiting
        dropped = [a["rule_id"] for a in rule_answers if a["choice"] == "drop_rule"]
        revise = [a["rule_id"] for a in rule_answers if a["choice"] == "revise_design"]
        if dropped:
            out["rules_dropped"] = dropped  # rule_check leaves them out of this run from now on
        if revise:
            out["rules_to_revise"] = revise  # the designer changes the design for these
    if tradeoff_questions:
        out["tradeoff_questions"] = tradeoff_questions
        out["tradeoff_answers"] = tradeoff_answers
        waiting = [q["tradeoff_id"] for q in tradeoff_questions if q["tradeoff_id"] not in {a["tradeoff_id"] for a in tradeoff_answers}]
        if waiting:
            out["tradeoffs_waiting"] = waiting
        out["tradeoff_choices"] = {a["tradeoff_id"]: a["choice"] for a in tradeoff_answers}  # the designer is told these
    if prepared.ignored:
        out["ignored"] = prepared.ignored  # answers to keys the requirements file already has
    if unchanged:
        out["unchanged"] = f"{' と '.join(unchanged)} は前に記録した内容と同じなので、記録は増やしていません"
    if not recorded_req and new:
        out["requirements_to_add"] = new  # the run store has no update_project: the caller adds them
    if problems:
        out["not_recorded"] = problems[0]
    if not prepared.ready:
        keys = "・".join(q["req_key"] for q in prepared.asked["questions"] if q["ask"] == "must" and q["req_key"] in prepared.unresolved)
        out["next"] = (f"必須の質問 {keys} に答えがありません。人に聞いて、言われたことだけを answers に入れてもう一度呼んでください "
                       "(推測で埋めない)")
    return out


# ------------------------------------------------------------------------------------ find_tradeoffs


# The trade-offs find_tradeoffs returned for a run: ask_user takes their ids and writes the question from these records,
# so the numbers in a question are never the model's.
_tradeoffs: "weakref.WeakKeyDictionary[Any, dict[str, Record]]" = weakref.WeakKeyDictionary()
_PATH_ROOTS = ("variables.", "sections.", "materials.", "parts.", "supports.")
_MAX_VARIABLES = 8  # K5 runs the tool twice for each number and each load case


def find_tradeoffs_tool(ctx: Any, args: Record, agent: str | None) -> Record:
    if args.get("design_version") not in (None, ctx.version):
        raise ToolError(f"感度は今の版 {ctx.version} でだけ出せます (版 {args['design_version']} は指定できません)")
    if tradeoff.collector is None:
        raise ToolError("この実行には、感度を集めるしくみ (hiraku.tools 側の collector) がまだ登録されていません")
    design = _store(ctx).design()
    if args.get("variables"):
        names = list(args["variables"])
    elif tradeoff.default_variables is not None:
        names = list(tradeoff.default_variables(ctx) or [])
    else:
        names = [f"variables.{k}" for k in (design.get("variables") or {})]
    paths = [p if p.startswith(_PATH_ROOTS) else f"variables.{p}" for p in names][:_MAX_VARIABLES]
    if not paths:
        raise ToolError("動かす設計の数がありません。variables に設計の変数の名前 (または variables.<名前> などの道) を渡してください")
    got = tradeoff.collector(ctx, paths, set(args.get("quantities") or []))
    sens, meta, bases = got["sens"], got["meta"], got["bases"]
    pairs = [p for p in tradeoff.find(sens, meta, bases) if all(k in p[s] for s in "ab" for k in ("now", "after"))]
    questions = [tradeoff.to_question(p) for p in pairs]
    store = _tradeoffs.setdefault(_store(ctx), {})
    for q in questions:
        store[q["tradeoff_id"]] = q
    out: Record = {"tradeoffs": questions, "looked_at": {"variables": paths, "quantities": sorted(sens)}}
    if got.get("skipped"):
        out["skipped"] = got["skipped"]
    out["next"] = ("人に優先を聞くなら、返った tradeoff_id を ask_user の tradeoff_ids に渡す (数は書かない)" if questions
                   else "両立しにくい組は見つかりませんでした (聞くことはありません)")
    return out


def _tradeoff_questions(ctx: Any, ids: Any) -> list[Record]:
    """The questions for the ids ask_user was given: the ones find_tradeoffs returned in this run."""
    have = _tradeoffs.get(_store(ctx), {})
    out = []
    for i in ids or []:
        if i not in have:
            raise IntakeError(f"トレードオフ {i} は find_tradeoffs が返していません。返ったもの: {', '.join(have) or 'なし'}")
        out.append(have[i])
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
    dropped = dropped_rules(ctx)  # a person dropped these for this run (hearing.answered rule_answers)
    disabled = set(args.get("disable") or []) | set(dropped)
    stray = sorted(disabled - {r["rule_id"] for r in rules})
    if stray:
        raise IntakeError(f"規則 {', '.join(stray)} はありません。使えるのは {', '.join(r['rule_id'] for r in rules)} です")
    problems = _ensure_builtin(ctx)
    persist = ev.known(ev.RULE_CHECK_RECORDED)
    ids = (lambda: store.next_id("rule_check")) if persist else None
    checks = rulecheck.check_all(rules, design, ctx.project["requirements"], disabled=disabled, id_factory=ids,
                                  flags=ctx.project.get("flags"))
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
                  "parts": [h["part_id"] for h in c["hits"]]} for c in checks if c["hits"] and not rulecheck.is_advice(c)],
        "advice": [{"rule_id": c["rule_id"], "text": next(r["text"] for r in rules if r["rule_id"] == c["rule_id"]),
                    "parts": [h["part_id"] for h in c["hits"]]} for c in checks if c["hits"] and rulecheck.is_advice(c)],
        "not_evaluable": [{"rule_id": c["rule_id"], "missing": sorted({m for n in c["not_evaluable"] for m in n["missing"]})}
                          for c in checks if c["not_evaluable"] and not rulecheck.is_advice(c)],
        "recorded": persist and not problems,
        **({"not_recorded": problems[0]} if problems else {}),
        **({"dropped_rules": [{"rule_id": r, "reason": why} for r, why in dropped.items()]} if dropped else {}),
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
            "answers_file は data/requirements/products/ の答えのファイル (パス data/requirements/products/<名前>.answers.yaml か、名前だけ)。"
            "Q30 の答えは見る組み合わせ (empty・driver・two・two_cargo か、空車・運転者だけ・2 人・2 人 + 荷)。質問を製品に合わせて言い換えたいときは wording "
            "({番号: 文})、選択肢を変えたいときは choices。画面の人の答えを待ちたいときは wait_seconds (質問を画面に出し、"
            "答えが来るか時間が切れるまで待つ)。"
        ),
        "input_schema": contract_input("ask_user"),
        "function": ask_user_tool,
    },
    "find_tradeoffs": {
        "description": (
            "設計の数を動かすと、1 つの結果は良くなり別の結果は悪くなる組 (両立しにくい 2 つ) を、感度 (K5、解析を動かして測る) から探す。"
            "数 (今の値・動かした後の値・10 % 動かしたときの変化) はコードが計算する。あなたは数を書かない。見つかった組を人に聞くなら、"
            "返った tradeoff_id を ask_user の tradeoff_ids に渡す。人の答え (favor_a=良くなる方を優先 / favor_b=悪くなる方を悪くしない / "
            "keep_both=どちらも守る) は設計役への指示に入れる。variables は動かす設計の数 (設計の変数の名前か、variables.<名前> などの道)、"
            "quantities は比べる結果の名前。解析を何回も回すので、変数は絞る (最大 8)。"
        ),
        "input_schema": contract_input("find_tradeoffs"),
        "function": find_tradeoffs_tool,
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
