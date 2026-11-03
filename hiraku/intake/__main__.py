"""Command line for the intake steps.

    python -m hiraku.intake pick --product serving-robot            domains from the product file's flags (code only)
    python -m hiraku.intake pick --product serving-robot --claude   Claude reads the sentence and picks (needs ANTHROPIC_API_KEY)
    python -m hiraku.intake pick --want "..." --claude              any sentence
    python -m hiraku.intake hearing --product care-bed [--bare]     the questions, settled with the sample answers
    python -m hiraku.intake prepare --product care-bed --bare --out <file>   request -> requirements file (pick + hearing)
    python -m hiraku.intake rules --design data/designs/offroad-4w-v1.json --product offroad-4w [--drop notes.md]
    python -m hiraku.intake gen-js                                  rewrite web/js/intake/data.js
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

from hiraku.catalog import REPO_ROOT, load_requirements, read_data
from hiraku.intake import (IntakeError, flags_text, load_product, pick_domains, product_files, rulecheck, summary_line)
from hiraku.intake import demo, hearing, knowledge, webdata
from hiraku.intake.products import answers_file


def _product(slug: str) -> dict:
    try:
        return load_product(slug)
    except KeyError as e:
        raise SystemExit(str(e).strip("'\""))


def _print_pick(record: dict) -> None:
    print(f"性質: {flags_text(record['flags'])}  速さ: {record.get('speed_class', '-')}")
    print(summary_line(record))
    for p in record["picks"]:
        print(f"  ✓ {p['shown_name']} [{p['domain']}] 試験 {len(p['test_ids'])}: {'・'.join(p['test_ids'])}\n      {p['reason']}")
    for o in record["off"]:
        print(f"  × {o['domain']}: {o['reason']}")
    print(f"  組み立て・人: {'・'.join(record['assembly_test_ids'])}")


def cmd_pick(a: argparse.Namespace) -> int:
    file_flags = None
    if a.product:
        req = _product(a.product)
        want, file_flags, speed = req["want"], req["flags"], req.get("speed_class")
    elif a.want:
        want, speed = a.want, None
    else:
        raise SystemExit("--product か --want のどちらかが要ります")
    if a.claude:
        from hiraku.intake import agent

        try:
            run = agent.pick_with_claude(want, model=a.model, max_budget_usd=a.max_budget)
        except agent.MissingApiKey as e:
            raise SystemExit(str(e))
        if run.error:
            print(f"Claude の実行が失敗: {run.error}", file=sys.stderr)
        if run.record is None:
            print("Claude は pick_domains を呼びませんでした。返事:", run.reply)
            return 1
        print("依頼:", want)
        print("Claude の返事:\n  " + run.reply.replace("\n", "\n  "))
        _print_pick(run.record)
        if file_flags is not None:
            req_domains = [p["domain"] for p in pick_domains(want, file_flags, speed)["picks"]]
            print("製品のファイルとの比べ:", json.dumps(agent.compare_with_product(run, file_flags, req_domains), ensure_ascii=False))
        print(f"pick_domains の呼び出し {len(run.calls)} 回、費用 {run.cost_usd} USD")
        return 0
    record = pick_domains(want, file_flags or {}, speed)
    if a.json:
        print(json.dumps(record, ensure_ascii=False, indent=2))
    else:
        print("依頼:", want)
        _print_pick(record)
    return 0


def cmd_hearing(a: argparse.Namespace) -> int:
    req = _product(a.product)
    own = {r["key"]: r for r in req["requirements"]}
    if a.bare:
        req = copy.deepcopy(req)
        req["requirements"] = []
    answers, note = {}, None
    path = Path(a.answers) if a.answers else (None if a.no_answers else answers_file(a.product))
    if path:
        answers, note = hearing.load_answers(path)
    prepared = hearing.prepare(req, answers, note, product_defaults=own if a.bare else None)
    print(hearing.hearing_text(prepared), "/ 進める" if prepared.ready else "/ 必須の答えが無いので進めない")
    for q in prepared.asked["questions"]:
        d = q.get("default")
        tail = f"  既定値 {d['value']}{(' ' + d['unit']) if 'unit' in d else ''} (信頼度 {d['trust']})" if d else ""
        print(f"  [{'必須' if q['ask'] == 'must' else '確認'}] {q['req_key']} {q['text']}{tail}")
    for r in prepared.answered["answers"]:
        print(f"  {r['source']:8} {r['key']} {r['name']} = {r['value']}{(' ' + r['unit']) if 'unit' in r else ''} (信頼度 {r['trust']})")
    if prepared.unresolved:
        print("  答えが無いまま:", "・".join(prepared.unresolved))
    return 0 if prepared.ready else 1


def cmd_rules(a: argparse.Namespace) -> int:
    design = read_data(Path(a.design))
    requirements = _product(a.product)["requirements"] if a.product else (load_requirements(Path(a.requirements))["requirements"] if a.requirements else [])
    docs = knowledge.load_builtin()
    rules = knowledge.all_rules(docs)
    for n, drop in enumerate(a.drop or [], 1):
        p = Path(drop)
        got = knowledge.import_text(p.name, p.read_text(encoding="utf-8-sig"), doc_id=f"U{n}")
        rules += got.record["rules"]
        print(f"{p.name}: 規則 {len(got.record['rules'])} 件" + (f"、式が要る文 {len(got.needs_formula)} 件 (Claude に書かせる)" if got.needs_formula else ""))
        for nf in got.needs_formula:
            print(f"    {nf['cand_id']}: {nf['text']}" + (f"  [{nf['problem']}]" if "problem" in nf else ""))
    checks = rulecheck.check_all(rules, design, requirements)
    text = {r["rule_id"]: r["text"] for r in rules}
    print(rulecheck.summary_line(checks, rules))
    for c in checks:
        for h in c["hits"]:
            print(f"  当たり {c['rule_id']} {h['part_id']}: {h['message']}")
    for c in checks:
        if c["not_evaluable"]:
            missing = sorted({m for n in c["not_evaluable"] for m in n["missing"]})
            print(f"  評価できない {c['rule_id']} {text[c['rule_id']]}: 設計に {'・'.join(missing)} が無い ({len(c['not_evaluable'])}/{len(c['targets'])} 部品)")
    return 0


def cmd_prepare(a: argparse.Namespace) -> int:
    """Request -> requirements file: the pick, the hearing with an answers file, and the file a run can start from."""
    import yaml

    from hiraku.intake.pipeline import intake

    if a.product:
        req = _product(a.product)
        want, flags, speed = req["want"], req["flags"], req.get("speed_class")
        base_requirements = [] if a.bare else req["requirements"]
    else:
        if not a.want:
            raise SystemExit("--product か --want のどちらかが要ります")
        want, speed, base_requirements = a.want, a.speed, []
        flags = {f: True for f in (a.flags or "").split(",") if f}
    path = Path(a.answers) if a.answers else (answers_file(a.product) if a.product and not a.no_answers else None)
    answers, note = hearing.load_answers(path) if path else ({}, None)
    got = intake(want, flags, speed, product=a.product, answers=answers, note=note, requirements=base_requirements, docs=False)
    print(summary_line(got.domains))
    print(hearing.hearing_text(got.prepared), "/ 進める" if got.ready else "/ 必須の答えが無いので進めない")
    out = got.requirements_file
    if a.out:
        Path(a.out).write_text(yaml.safe_dump(out, allow_unicode=True, sort_keys=False, width=100), encoding="utf-8")
        print("書きました:", a.out)
    return 0 if got.ready else 1


def cmd_gen_js(a: argparse.Namespace) -> int:
    print(webdata.write())
    print(demo.write())
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="python -m hiraku.intake", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    slugs = list(product_files())

    p = sub.add_parser("pick", help="N09: pick the analysis domains")
    p.add_argument("--product", choices=slugs)
    p.add_argument("--want", help="a request in a sentence (with --claude)")
    p.add_argument("--claude", action="store_true", help="let Claude read the sentence (needs ANTHROPIC_API_KEY in this window)")
    p.add_argument("--model", default=None)
    p.add_argument("--max-budget", type=float, default=0.5, help="USD")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_pick)

    h = sub.add_parser("hearing", help="N14: the questions for a product and how they settle")
    h.add_argument("--product", choices=slugs, required=True)
    h.add_argument("--bare", action="store_true", help="start from the sentence and flags only")
    h.add_argument("--answers", help="an answers file (default: the product's sample answers)")
    h.add_argument("--no-answers", action="store_true")
    h.set_defaults(fn=cmd_hearing)

    r = sub.add_parser("rules", help="N14: apply the knowledge rules to a design")
    r.add_argument("--design", required=True, help="a design JSON")
    r.add_argument("--product", choices=slugs, help="take the requirements from this product's file")
    r.add_argument("--requirements", help="a requirements file")
    r.add_argument("--drop", action="append", help="a file of rules, as if dragged in (repeatable)")
    r.set_defaults(fn=cmd_rules)

    pr = sub.add_parser("prepare", help="a request -> a requirements file a run can start from (pick + hearing)")
    pr.add_argument("--product", choices=slugs)
    pr.add_argument("--bare", action="store_true", help="with --product: start from its sentence and flags only")
    pr.add_argument("--want", help="a request in a sentence (without --product)")
    pr.add_argument("--flags", help="comma separated flag names, e.g. standing,cargo (without --product)")
    pr.add_argument("--speed", choices=["static", "walk", "vehicle"])
    pr.add_argument("--answers", help="an answers file (default: the product's sample answers)")
    pr.add_argument("--no-answers", action="store_true")
    pr.add_argument("--out", help="write the requirements file here (YAML)")
    pr.set_defaults(fn=cmd_prepare)

    g = sub.add_parser("gen-js", help="rewrite web/js/intake/data.js from the Python tables")
    g.set_defaults(fn=cmd_gen_js)

    a = ap.parse_args(argv)
    if getattr(a, "model", None) is None and a.cmd == "pick":
        from hiraku.intake.agent import DEFAULT_MODEL

        a.model = DEFAULT_MODEL
    try:
        return a.fn(a)
    except IntakeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
