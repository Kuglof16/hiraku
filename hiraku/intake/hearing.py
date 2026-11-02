"""N14 (left): ask the requirements that are missing, once, and settle the rest with defaults.

The items (hiraku/intake/requirement_items.yaml) say which requirement a product needs and how
it is asked. For a requirements file this module works out
  - the questions: items the product needs, asked as `must` or `confirm`, that the file lacks
  - the defaults: items asked as `default` that the file lacks, filled with a default that has its source
and, given the answers (an answers file, or what the person said in the chat), returns the complete
requirements. The first form is the answer file; waiting for a person in the middle of a run comes
later and uses the same plan and the same answers.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from hiraku import contract
from hiraku.catalog import applies, read_data
from hiraku.intake.domains import IntakeError, normalize_flags

Record = dict[str, Any]

ITEMS_FILE = Path(__file__).with_name("requirement_items.yaml")
DEFAULT_ANSWER_TRUST = 1
DEFAULT_ANSWER_BASIS = "answered in the hearing"


@lru_cache(maxsize=1)
def _items() -> tuple[Record, ...]:
    with ITEMS_FILE.open(encoding="utf-8") as f:
        items = yaml.safe_load(f)["items"]
    for it in items:
        if it["ask"] not in ("must", "confirm", "default"):
            raise ValueError(f"{it['key']}: ask must be must, confirm or default")
        if it["ask"] in ("must", "confirm") and "question" not in it:
            raise ValueError(f"{it['key']}: a {it['ask']} item needs a question")
        applies(it["applies_if"], {})  # fail early on a bad expression
    return tuple(items)


def load_items() -> list[Record]:
    return [dict(it) for it in _items()]


def item(key: str) -> Record:
    for it in _items():
        if it["key"] == key:
            return dict(it)
    raise IntakeError(f"{key} は要件の項目にありません。使えるのは {', '.join(it['key'] for it in _items())} です")


def applicable_items(flags: dict[str, bool], speed_class: str | None = None) -> list[Record]:
    return [dict(it) for it in _items() if applies(it["applies_if"], flags, speed_class)]


# ------------------------------------------------------------------------------------------ the plan


def _value_part(req: Record) -> Record:
    """{value, unit?, trust, basis} of a requirement, the part a question offers as its default."""
    out = {k: req[k] for k in ("value", "unit", "trust", "basis") if k in req}
    out.setdefault("basis", "")
    return out


@dataclass
class Plan:
    questions: list[Record] = field(default_factory=list)  # hearing_asked.questions
    defaulted: list[Record] = field(default_factory=list)  # requirement entries (source default) for `default` items
    unfilled: list[str] = field(default_factory=list)  # `default` items nobody has a value for
    present: list[str] = field(default_factory=list)  # keys the file already has


def plan_hearing(
    req: Record,
    *,
    product_defaults: dict[str, Record] | None = None,
    wording: dict[str, str] | None = None,
    choices: dict[str, list[Record]] | None = None,
) -> Plan:
    """What is missing from a requirements file.

    product_defaults: key -> requirement of the same product's own file (its values come before the
    items' generic defaults). wording / choices: Claude's rewording of a question for this product.
    """
    flags = normalize_flags(req.get("flags", {}))
    have = {r["key"] for r in req.get("requirements", [])}
    product_defaults = product_defaults or {}
    wording = wording or {}
    choices = choices or {}
    plan = Plan(present=sorted(have))
    for it in applicable_items(flags, req.get("speed_class")):
        key = it["key"]
        if key in have:
            continue
        known = product_defaults.get(key)
        default = _value_part(known) if known and "value" in known else it.get("default")
        if it["ask"] == "default":
            if default is None:
                plan.unfilled.append(key)
            else:
                plan.defaulted.append(_default_requirement(it, default, known))
            continue
        q: Record = {
            "req_key": key,
            "name": it["name"],
            "text": (wording.get(key) or it["question"]).strip(),
            "ask": it["ask"],
            "allow_other": True,
        }
        offered = choices.get(key) or it.get("choices")
        if offered:
            q["choices"] = [dict(c) for c in offered]
        if default is not None and it["ask"] == "confirm":
            q["default"] = dict(default)
        plan.questions.append(q)
    return plan


def _default_requirement(it: Record, default: Record, known: Record | None) -> Record:
    out: Record = {"key": it["key"], "name": (known or {}).get("name") or it["name"], "source": "default",
                   "trust": default["trust"], "basis": default["basis"], "value": default["value"]}
    if "unit" in default:
        out["unit"] = default["unit"]
    if known and known.get("note"):
        out["note"] = known["note"]
    return out


# ---------------------------------------------------------------------------------------- the answers


def load_answers(path: Path) -> tuple[dict[str, Any], str | None]:
    """Read an answers file: ({key: answer}, note). An answer is a value or {value, unit?, trust?, basis?}."""
    data = read_data(Path(path)) or {}
    unknown = [k for k in data if k not in ("answers", "note")]
    if unknown:
        raise IntakeError(f"答えのファイルに {', '.join(unknown)} は使えません (answers と note だけ)")
    return dict(data.get("answers") or {}), data.get("note")


def _answer_requirement(it: Record, answer: Any) -> Record:
    spec = answer if isinstance(answer, dict) else {"value": answer}
    if "value" not in spec:
        raise IntakeError(f"{it['key']} の答えに value がありません")
    out: Record = {"key": it["key"], "name": it["name"], "value": spec["value"], "source": "hearing",
                   "trust": spec.get("trust", DEFAULT_ANSWER_TRUST), "basis": spec.get("basis", DEFAULT_ANSWER_BASIS)}
    if "unit" in spec:
        out["unit"] = spec["unit"]
    if "note" in spec:
        out["note"] = spec["note"]
    problems = contract.errors("requirement", out)
    if problems:
        raise IntakeError("; ".join(problems))
    return out


@dataclass
class Prepared:
    """A requirements file after the hearing."""

    requirements_file: Record
    asked: Record  # hearing_asked data
    answered: Record  # hearing_answered data
    unresolved: list[str]  # asked, no answer, no default
    unfilled: list[str]  # `default` items with no value anywhere

    @property
    def ready(self) -> bool:
        """False while a `must` question has no answer."""
        must = {q["req_key"] for q in self.asked["questions"] if q["ask"] == "must"}
        return not (must & set(self.unresolved))


def prepare(
    req: Record,
    answers: dict[str, Any] | None = None,
    note: str | None = None,
    *,
    product_defaults: dict[str, Record] | None = None,
    wording: dict[str, str] | None = None,
    choices: dict[str, list[Record]] | None = None,
) -> Prepared:
    """Ask what is missing and settle it: answers first, then defaults. The file in is not changed."""
    answers = dict(answers or {})
    plan = plan_hearing(req, product_defaults=product_defaults, wording=wording, choices=choices)
    in_file = {r["key"] for r in req.get("requirements", [])}
    settled: list[Record] = []
    unresolved: list[str] = []
    for q in plan.questions:
        key = q["req_key"]
        if key in answers:
            settled.append(_answer_requirement(item(key), answers.pop(key)))
        elif "default" in q:
            d = q["default"]
            r = {"key": key, "name": q["name"], "source": "default", "trust": d["trust"], "basis": d["basis"],
                 "value": d["value"]}
            if "unit" in d:
                r["unit"] = d["unit"]
            settled.append(r)
        else:
            unresolved.append(key)
    defaulted = list(plan.defaulted)
    for key in list(answers):  # something the person said that was not asked (a `default` item, or a key nobody needed)
        if key in in_file:
            raise IntakeError(f"{key} は要件のファイルにもう有ります。答えで書き換えず、ファイルを直してください")
        settled.append(_answer_requirement(item(key), answers.pop(key)))
        defaulted = [r for r in defaulted if r["key"] != key]
    out = copy.deepcopy(req)
    merged = {r["key"]: r for r in out.get("requirements", [])}
    for r in [*defaulted, *settled]:
        merged[r["key"]] = r
    out["requirements"] = [merged[k] for k in sorted(merged)]
    contract.validate("requirements_file", out)
    answered: Record = {"answers": [*defaulted, *settled]}
    if note and note.strip():
        answered["note"] = note.strip()
    return Prepared(out, {"questions": plan.questions}, answered, unresolved,
                    [k for k in plan.unfilled if k not in {r["key"] for r in settled}])


def hearing_text(prepared: Prepared) -> str:
    """One line for the chat / log: how many questions, how many settled by answer, by default."""
    by = {"hearing": 0, "default": 0}
    for r in prepared.answered["answers"]:
        by[r["source"]] += 1
    left = f"、答えが無いまま {len(prepared.unresolved)} 件" if prepared.unresolved else ""
    return f"質問 {len(prepared.asked['questions'])} 件、答え {by['hearing']} 件、既定値 {by['default']} 件{left}"
