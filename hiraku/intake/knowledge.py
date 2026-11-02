"""N14 (right): knowledge becomes rules that are applied to a design.

Two ways in:
  built in   data/knowledge/*.yaml, one file per category, public knowledge with its source
  dropped    a file the person drags in (Markdown, CSV/TSV table, plain text): each rule sentence in it
             becomes a rule. A sentence the code can turn into a formula by its patterns ("曲げの内側の
             半径は板厚以上") is converted by code; the others are handed to Claude, who writes the
             formula; a table with a formula column carries it. The code checks every formula
             (hiraku.intake.expression, names from hiraku.intake.facts) before it becomes a rule, and the
             code computes it (hiraku.intake.rulecheck). Claude never computes.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hiraku.catalog import REPO_ROOT, read_data
from hiraku.intake import expression, facts
from hiraku.intake.domains import IntakeError

Record = dict[str, Any]

KNOWLEDGE_DIR = REPO_ROOT / "data" / "knowledge"

CATEGORIES: dict[str, str] = {
    "sheet_metal": "板金",
    "fastener": "締結",
    "weld": "溶接",
    "tolerance": "公差",
    "human": "人",
    "battery": "電池",
    "assembly": "組み立て",
    "suspension": "サス",
    "manufacturing": "製造",
}
CHECK_BY = ("file", "code", "claude")  # where the formula came from: a data file, the code's patterns, Claude


# --------------------------------------------------------------------------------------- rules


def make_rule(rule_id: str, doc_id: str, category: str, text: str, check: str, *, origin: str, check_by: str,
              trust: int, basis: str, kinds: list[str] | None = None) -> Record:
    """A validated rule record. Raises IntakeError when the category, the formula or its names are wrong."""
    if category not in CATEGORIES:
        raise IntakeError(f"分類 {category!r} はありません。使えるのは {', '.join(CATEGORIES)} です")
    if not text or not text.strip():
        raise IntakeError(f"{rule_id}: 規則の文が空です")
    try:
        names = expression.names(check)
    except expression.ExprError as e:
        raise IntakeError(f"{rule_id}: {e}") from None
    bad = facts.unknown_names(names)
    if bad:
        known = ", ".join(n for n in facts.FACTS)
        raise IntakeError(f"{rule_id}: 式 {check!r} の名前 {', '.join(bad)} は使えません。使える名前: {known}、"
                          "req_Qnn (要件の数値)、var_<設計の変数名>")
    if check_by not in CHECK_BY or origin not in ("builtin", "dropped"):
        raise ValueError(f"{rule_id}: bad origin or check_by")
    rule: Record = {"rule_id": rule_id, "doc_id": doc_id, "category": category, "text": text.strip(), "check": check.strip(),
                    "origin": origin, "check_by": check_by, "trust": trust, "basis": basis.strip()}
    if kinds:
        rule["applies_to"] = {"kinds": list(kinds)}
    return rule


def load_builtin(folder: Path = KNOWLEDGE_DIR) -> list[Record]:
    """The built-in documents as `knowledge_imported` records, in the order of CATEGORIES."""
    order = list(CATEGORIES)
    docs = []
    for path in sorted(Path(folder).glob("*.yaml")):
        data = read_data(path)
        docs.append(data)
    docs.sort(key=lambda d: order.index(d["category"]))
    out: list[Record] = []
    for d in docs:
        rules = [make_rule(r["id"], d["doc_id"], d["category"], r["text"], r["check"], origin="builtin", check_by="file",
                           trust=r["trust"], basis=r["basis"], kinds=r.get("kinds")) for r in d["rules"]]
        out.append({"doc": {"doc_id": d["doc_id"], "filename": d["title"], "origin": "builtin"}, "rules": rules})
    ids = [r["rule_id"] for rec in out for r in rec["rules"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate rule ids in data/knowledge")
    return out


def all_rules(docs: list[Record]) -> list[Record]:
    return [r for rec in docs for r in rec["rules"]]


# ------------------------------------------------------------------ reading a dropped file

_CATEGORY_WORDS = [  # the first group with a hit wins
    ("weld", r"溶接|ビード|のど厚|脚長"),
    ("fastener", r"ボルト|ねじ|ナット|締結|座金|リベット"),
    ("sheet_metal", r"板金|曲げ|板厚|プレス"),
    ("tolerance", r"公差|はめあい|積み上げ"),
    ("battery", r"電池|セル|充電|電圧"),
    ("suspension", r"サス|ばね|ストローク|ダンパ"),
    ("assembly", r"組み立て|組立|工具|工程|手順"),
    ("human", r"人|手|指|座|乗員|体格|持ち上げ|挟"),
    ("manufacturing", r"製造|加工|成形|鋳造|材料"),
]


def guess_category(text: str) -> str | None:
    for cat, words in _CATEGORY_WORDS:
        if re.search(words, text):
            return cat
    return None


@dataclass
class Candidate:
    cand_id: str  # c1, c2 ... in the order of the file: stable between calls
    text: str
    line: int
    category: str | None = None  # from a heading or a column
    check: str | None = None  # a formula that came with the file
    kinds: list[str] | None = None


_BULLET = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+(.*\S)\s*$")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*\S)\s*$")
_RULE_HEADER = {"規則", "ルール", "rule", "text", "内容", "文", "決まり"}
_FORMULA_HEADER = {"式", "check", "formula", "条件式", "チェック"}
_CATEGORY_HEADER = {"分類", "category", "カテゴリ", "カテゴリー"}
_KINDS_HEADER = {"対象", "部品", "kinds", "target", "対象の部品"}


def _category_from_label(label: str) -> str | None:
    label = label.strip()
    for cat, ja in CATEGORIES.items():
        if label == cat or label == ja:
            return cat
    return guess_category(label)


def _split_kinds(cell: str) -> list[str] | None:
    parts = [p.strip() for p in re.split(r"[,、，/・\s]+", cell) if p.strip()]
    return parts or None


def _table_candidates(rows: list[list[str]], first_line: int, heading_cat: str | None) -> list[Candidate]:
    header = [c.strip().lower() for c in rows[0]]
    col = {name: next((i for i, h in enumerate(header) if h in names), None)
           for name, names in (("text", {w.lower() for w in _RULE_HEADER}), ("check", {w.lower() for w in _FORMULA_HEADER}),
                               ("category", {w.lower() for w in _CATEGORY_HEADER}), ("kinds", {w.lower() for w in _KINDS_HEADER}))}
    has_header = col["text"] is not None
    body = rows[1:] if has_header else rows
    out: list[Candidate] = []
    for k, row in enumerate(body):
        cells = [c.strip() for c in row]
        text = cells[col["text"]] if has_header and col["text"] < len(cells) else " ".join(c for c in cells if c)
        if not text:
            continue
        cand = Candidate("", text, first_line + k + (1 if has_header else 0), category=heading_cat)
        if has_header:
            if col["check"] is not None and col["check"] < len(cells) and cells[col["check"]]:
                cand.check = cells[col["check"]]
            if col["category"] is not None and col["category"] < len(cells) and cells[col["category"]]:
                cand.category = _category_from_label(cells[col["category"]]) or heading_cat
            if col["kinds"] is not None and col["kinds"] < len(cells) and cells[col["kinds"]]:
                cand.kinds = _split_kinds(cells[col["kinds"]])
        out.append(cand)
    return out


def parse_candidates(filename: str, text: str) -> list[Candidate]:
    """Pull the rule sentences out of a dropped file.

    .csv / .tsv: a table (header with 規則 / 式 / 分類 / 対象 columns when present).
    otherwise: Markdown or plain text - bullets and numbered lines, table rows, and (when the file
    has neither) every non-empty line. Headings set the category of what follows.
    """
    text = unicodedata.normalize("NFC", text.lstrip("﻿"))
    suffix = Path(filename).suffix.lower()
    if suffix in (".csv", ".tsv"):
        delim = "\t" if suffix == ".tsv" else ","
        rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]
        found = _table_candidates(rows, 1, None) if rows else []
    else:
        found = []
        lines = text.splitlines()
        plain = [i for i, ln in enumerate(lines) if ln.strip() and not _HEADING.match(ln) and not ln.strip().startswith(("|", "```"))]
        has_structure = any(_BULLET.match(lines[i]) for i in plain) or any(ln.strip().startswith("|") for ln in lines)
        heading_cat: str | None = None
        fenced = False
        i = 0
        while i < len(lines):
            ln = lines[i]
            if ln.strip().startswith("```"):
                fenced = not fenced
            elif fenced or not ln.strip():
                pass
            elif (m := _HEADING.match(ln)):
                heading_cat = _category_from_label(m.group(1)) or heading_cat
            elif ln.strip().startswith("|"):
                start = i
                block: list[list[str]] = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                    if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                        block.append(cells)
                    i += 1
                if block:
                    found.extend(_table_candidates(block, start + 1, heading_cat))
                continue
            elif (m := _BULLET.match(ln)):
                found.append(Candidate("", m.group(1), i + 1, category=heading_cat))
            elif not has_structure:
                found.append(Candidate("", ln.strip(), i + 1, category=heading_cat))
            i += 1
    out = []
    for n, c in enumerate((c for c in found if len(c.text) >= 4), 1):
        c.cand_id = f"c{n}"
        out.append(c)
    return out


# --------------------------------------------------------- a sentence into a formula, by patterns

_ALIASES: list[tuple[str, str]] = [  # (how a sentence says it, fact)
    ("質量", "mass_kg"), ("重さ", "mass_kg"), ("重量", "mass_kg"),
    ("板厚", "thickness_mm"), ("厚み", "thickness_mm"), ("厚さ", "thickness_mm"),
    ("肉厚", "wall_mm"), ("外径", "outer_mm"), ("外形", "outer_mm"),
    ("曲げの内側の半径", "bend_radius_mm"), ("曲げ半径", "bend_radius_mm"),
    ("縁までの距離", "hole_edge_mm"), ("縁端距離", "hole_edge_mm"),
    ("穴の径", "hole_diameter_mm"), ("穴径", "hole_diameter_mm"),
    ("ボルトの呼び径", "bolt_diameter_mm"), ("ボルトの径", "bolt_diameter_mm"), ("呼び径", "bolt_diameter_mm"),
    ("かみ合い長さ", "thread_engagement_mm"),
    ("溶接の長さ", "weld_length_mm"), ("溶接長さ", "weld_length_mm"), ("のど厚", "weld_throat_mm"),
    ("公差の積み上げ", "tolerance_stack_mm"), ("閉じる隙間", "closing_gap_mm"), ("隙間", "clearance_mm"),
    ("ストローク", "travel_mm"), ("ばね定数", "stiffness_N_per_mm"),
    ("地上高", "bottom_z_mm"), ("下端の高さ", "bottom_z_mm"),
]
_ALIASES.sort(key=lambda a: -len(a[0]))  # the longest phrase first: 閉じる隙間 before 隙間
_CMP = {"以上": ">=", "以下": "<=", "未満": "<", "超": ">", "より大きい": ">", "より小さい": "<"}
_CMP_WORDS = "|".join(sorted(_CMP, key=len, reverse=True))
_UNIT_FACTOR = {"mm": {"mm": 1.0, "cm": 10.0, "m": 1000.0}, "kg": {"kg": 1.0, "g": 0.001, "t": 1000.0},
                "N": {"N": 1.0, "kN": 1000.0}}
_NUMBER = r"\d+(?:\.\d+)?"
_VS_NUMBER = re.compile(rf"^(?P<subj>.+?)は[、,]?\s*(?P<num>{_NUMBER})\s*(?P<unit>mm|cm|m|kg|g|t|N|kN)?\s*(?P<cmp>{_CMP_WORDS})")
_VS_OTHER = re.compile(rf"^(?P<subj>.+?)は[、,]?\s*(?P<other>.+?)(?:の\s*(?P<k>{_NUMBER})\s*倍)?\s*(?P<cmp>{_CMP_WORDS})")


def _fact_of(phrase: str) -> str | None:
    phrase = phrase.strip()
    for alias, fact in _ALIASES:
        if phrase.endswith(alias):
            return fact
    return None


def convert_sentence(text: str) -> str | None:
    """A formula for a sentence of the forms "<A> は <数> <単位> 以上" or "<A> は <B> の <k> 倍 以上", else None.

    Only the names in the alias table are understood; anything else is left for Claude to write.
    """
    s = unicodedata.normalize("NFKC", text).strip().rstrip("。.")
    m = _VS_NUMBER.match(s)
    if m:
        fact = _fact_of(m.group("subj"))
        if fact is None:
            return None
        fact_unit = facts.FACTS[fact][1]
        factor = _UNIT_FACTOR.get(fact_unit, {fact_unit: 1.0}).get(m.group("unit") or fact_unit)
        if factor is None:
            return None
        value = float(m.group("num")) * factor
        return f"{fact} {_CMP[m.group('cmp')]} {value:g}"
    m = _VS_OTHER.match(s)
    if m:
        a, b = _fact_of(m.group("subj")), _fact_of(m.group("other"))
        if a is None or b is None or a == b:
            return None
        if facts.FACTS[a][1] != facts.FACTS[b][1]:
            return None
        k = m.group("k")
        rhs = f"{float(k):g} * {b}" if k else b
        return f"{a} {_CMP[m.group('cmp')]} {rhs}"
    return None


# ------------------------------------------------------------------------- importing a file


@dataclass
class Imported:
    record: Record  # knowledge_imported data: {doc, rules}
    needs_formula: list[Record] = field(default_factory=list)  # [{cand_id, text, category, line, problem?}]


def import_text(
    filename: str,
    text: str,
    *,
    doc_id: str,
    formulas: dict[str, Record] | None = None,
    skip: dict[str, str] | None = None,
) -> Imported:
    """Make rules of a dropped file.

    formulas: Claude's formulas for the sentences the code could not convert: {cand_id: {check, kinds?, category?}}.
    skip: sentences that are not rules (a heading line, a remark): {cand_id: reason}.
    Rule ids are `<doc_id>-<nn>` with nn the sentence's number in the file, so a second call with more
    formulas gives the same ids to the rules of the first.
    """
    formulas = formulas or {}
    skip = skip or {}
    cands = parse_candidates(filename, text)
    known = {c.cand_id for c in cands}
    stray = [k for k in [*formulas, *skip] if k not in known]
    if stray:
        raise IntakeError(f"文 {', '.join(stray)} はこのファイルにありません (文の番号は c1 から c{len(cands)} まで)")
    rules: list[Record] = []
    skipped: list[Record] = []
    needs: list[Record] = []
    for n, c in enumerate(cands, 1):
        if c.cand_id in skip:
            skipped.append({"text": c.text, "reason": skip[c.cand_id]})
            continue
        category = c.category or guess_category(c.text) or "manufacturing"
        kinds = c.kinds
        check, by = c.check, "file"
        given = formulas.get(c.cand_id)
        if given:
            check, by = given.get("check"), "claude"
            category = given.get("category") or category
            kinds = given.get("kinds") or kinds
        if check is None:
            check = convert_sentence(c.text)
            by = "code"
        rid = f"{doc_id}-{n:02d}"
        basis = f"dropped file {filename}, line {c.line}; the person's own know-how, not checked against a source"
        if check is not None:
            try:
                rules.append(make_rule(rid, doc_id, category, c.text, check, origin="dropped", check_by=by, trust=3,
                                       basis=basis, kinds=kinds))
                continue
            except IntakeError as e:
                needs.append({"cand_id": c.cand_id, "text": c.text, "category": category, "line": c.line, "problem": str(e)})
                continue
        needs.append({"cand_id": c.cand_id, "text": c.text, "category": category, "line": c.line})
    doc: Record = {"doc_id": doc_id, "filename": filename, "origin": "dropped"}
    if skipped:
        doc["skipped"] = skipped
    return Imported({"doc": doc, "rules": rules}, needs)


def doc_id_for(filename: str, existing: dict[str, str]) -> str:
    """The doc id of a dropped file: the one it had when it came before, else the next U<n>.

    existing: {filename: doc_id} of the dropped files already in the run.
    """
    if filename in existing:
        return existing[filename]
    return f"U{len(existing) + 1}"
