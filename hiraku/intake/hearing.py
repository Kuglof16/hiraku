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
import json
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import yaml

from hiraku import contract
from hiraku.catalog import applies, read_data
from hiraku.contract import layout
from hiraku.contract.loading import LOADING_JA
from hiraku.contract.protocols import LOADINGS
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
    unasked: list[str] = field(default_factory=list)  # `confirm` items not asked (ask_confirm False) with no default
    present: list[str] = field(default_factory=list)  # keys the file already has


def plan_hearing(
    req: Record,
    *,
    product_defaults: dict[str, Record] | None = None,
    wording: dict[str, str] | None = None,
    choices: dict[str, list[Record]] | None = None,
    ask_confirm: bool = True,
) -> Plan:
    """What is missing from a requirements file.

    product_defaults: key -> requirement of the same product's own file (its values come before the
    items' generic defaults). wording / choices: Claude's rewording of a question for this product.
    ask_confirm False: the requirements came as a file somebody wrote, so a `confirm` item the file lacks is
    not asked; it takes its default (source default) when it has one, else stays out (plan.unasked). `must`
    items are asked either way: nothing proceeds without them.
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
        quiet = it["ask"] == "default" or (it["ask"] == "confirm" and not ask_confirm)
        if quiet:
            if default is not None:
                plan.defaulted.append(_default_requirement(it, default, known))
            elif it["ask"] == "default":
                plan.unfilled.append(key)
            else:
                plan.unasked.append(key)
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


LOADING_KEY = "Q30"  # the combination of people and load to check: one of hiraku.contract.protocols.LOADINGS
_LOADING_WORDS = {**{n: n for n in LOADINGS}, **{ja: n for n, ja in LOADING_JA.items()}, "1 人": "driver"}


def loading_answer(value: Any) -> str | None:
    """A loading name (empty, driver, two, two_cargo) for what was answered to Q30: the name itself, its Japanese
    words (空車・運転者だけ・2 人・2 人 + 荷) or "1 人", spaces ignored. None for anything else."""
    squeeze = lambda t: "".join(str(t).split()).lower()  # noqa: E731
    words = {squeeze(w): n for w, n in _LOADING_WORDS.items()}
    return words.get(squeeze(value))


def _answer_requirement(it: Record, answer: Any) -> Record:
    spec = answer if isinstance(answer, dict) else {"value": answer}
    if "value" not in spec:
        raise IntakeError(f"{it['key']} の答えに value がありません")
    if it["key"] == LOADING_KEY:  # the catalog and the analyses take only these four (hiraku.contract.loading)
        loading = loading_answer(spec["value"])
        if loading is None:
            raise IntakeError(
                f"{LOADING_KEY} は、見る組み合わせを {', '.join(LOADINGS)} のどれか "
                f"({'・'.join(LOADING_JA[n] for n in LOADINGS)}) で答えてください ({spec['value']!r} は組み合わせの名前ではありません)。"
                "介助者のように乗らない人は数えません (乗る人だけを数えます)")
        spec = {**spec, "value": loading}
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
    unasked: list[str] = field(default_factory=list)  # `confirm` items left out (ask_confirm False), no value anywhere

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
    ask_confirm: bool = True,
) -> Prepared:
    """Ask what is missing and settle it: answers first, then defaults. The file in is not changed.

    ask_confirm False is for requirements that arrive as a file somebody wrote (see plan_hearing): nothing is
    asked but the `must` items the file lacks, and the defaults it takes are in `answered` and in the file out.
    """
    answers = dict(answers or {})
    plan = plan_hearing(req, product_defaults=product_defaults, wording=wording, choices=choices, ask_confirm=ask_confirm)
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
    taken = {r["key"] for r in settled}
    return Prepared(out, {"questions": plan.questions}, answered, unresolved,
                    [k for k in plan.unfilled if k not in taken], [k for k in plan.unasked if k not in taken])


# ------------------------------------------------------------------- waiting for a person in a run
# The same plan and the same answers, but the answers arrive while the run waits: ask_user writes
# hearing.request.json into the run folder, the screen (A2) writes hearing.answer.json, and ask_user
# reads it. The shape is the one of A2's "answer" button: {answers, note, use_defaults}.
# (A file in the run folder, like stop.request: the run and the screen are different processes.)

REQUEST_FILE = layout.HEARING_REQUEST_FILE
ANSWER_FILE = layout.HEARING_ANSWER_FILE


def write_request(run_path: Path, asked: Record) -> Path:
    path = Path(run_path) / REQUEST_FILE
    path.write_text(json.dumps(asked, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


RULE_CHOICES = ("revise_design", "drop_rule")  # what a person can decide about a rule that hit


def check_rule_answers(raw: Any, asked: list[Record] | None = None) -> list[Record]:
    """The rule answers [{rule_id, choice, note?}] as the contract has them. `asked` are the run's rule_questions: the
    rule must be one of them and the choice one it offered (missing choices: both)."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise IntakeError("rule_answers は [{rule_id, choice, note}] の形で渡してください")
    offered = {q["rule_id"]: tuple(q.get("choices") or RULE_CHOICES) for q in asked or []}
    out: list[Record] = []
    for a in raw:
        if not isinstance(a, dict) or set(a) - {"rule_id", "choice", "note"} or not {"rule_id", "choice"} <= set(a):
            raise IntakeError("rule_answers の 1 件は {rule_id, choice, note} (note は任意) の形です")
        if a["choice"] not in RULE_CHOICES:
            raise IntakeError(f"{a['rule_id']} の choice は {' か '.join(RULE_CHOICES)} です ({a['choice']!r} は使えません)")
        if asked is not None and a["rule_id"] not in offered:
            raise IntakeError(f"規則 {a['rule_id']} は聞いていません。聞いたのは {', '.join(offered) or 'なし'} です")
        if asked is not None and a["choice"] not in offered[a["rule_id"]]:
            raise IntakeError(f"{a['rule_id']} の選択肢は {' か '.join(offered[a['rule_id']])} です")
        out.append({k: str(v) if k == "note" else v for k, v in a.items() if v not in (None, "")})
    return out


def save_answer(run_path: Path, body: Record) -> Path:
    """What the screen's server calls with the body of A2's answer: checks it and writes hearing.answer.json."""
    body = dict(body)
    if "useDefaults" in body:  # A2's button sends the JavaScript spelling
        body["use_defaults"] = body.pop("useDefaults")
    if "ruleAnswers" in body:
        body["rule_answers"] = body.pop("ruleAnswers")
    unknown = [k for k in body if k not in ("answers", "note", "use_defaults", "rule_answers")]
    if unknown:
        raise IntakeError(f"答えに {', '.join(unknown)} は使えません (answers・note・use_defaults・rule_answers だけ)")
    answers = body.get("answers", {})
    if not isinstance(answers, dict):
        raise IntakeError("answers は {要件の番号: 値} の形で渡してください")
    for key, answer in answers.items():
        _answer_requirement(item(key), answer)  # the same checks as when it is applied
    asked = None
    request = Path(run_path) / REQUEST_FILE
    if request.exists():  # the questions this answer is for: a rule answer must be to one of them
        asked = json.loads(request.read_text(encoding="utf-8")).get("rule_questions") or []
    rule_answers = check_rule_answers(body.get("rule_answers"), asked)
    clean = {"answers": answers, "note": str(body.get("note") or ""), "use_defaults": bool(body.get("use_defaults", True))}
    if rule_answers:
        clean["rule_answers"] = rule_answers
    path = Path(run_path) / ANSWER_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(clean, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)  # the reader never sees half a file
    return path


def read_answer(run_path: Path) -> Record | None:
    path = Path(run_path) / ANSWER_FILE
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def wait_for_answer(run_path: Path, timeout_s: float, *, poll_s: float = 0.5,
                    sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> Record | None:
    """Wait up to timeout_s for hearing.answer.json. None when it did not come."""
    deadline = clock() + timeout_s
    while True:
        got = read_answer(run_path)
        if got is not None:
            return got
        if clock() >= deadline:
            return None
        sleep(poll_s)


def clear_mailbox(run_path: Path) -> None:
    for name in (REQUEST_FILE, ANSWER_FILE):
        (Path(run_path) / name).unlink(missing_ok=True)


def hearing_text(prepared: Prepared) -> str:
    """One line for the chat / log: how many questions, how many settled by answer, by default."""
    by = {"hearing": 0, "default": 0}
    for r in prepared.answered["answers"]:
        by[r["source"]] += 1
    rules = prepared.asked.get("rule_questions") or []
    if rules and not prepared.asked["questions"]:  # only rule hits were asked
        got = {a["rule_id"]: a["choice"] for a in prepared.answered.get("rule_answers", [])}
        dropped = [r for r, c in got.items() if c == "drop_rule"]
        return (f"当たった規則の相談 {len(rules)} 件、答え {len(got)} 件" + (f"、この実行で外す規則 {'・'.join(dropped)}" if dropped else "")
                + (f"、答えが無いまま {len(rules) - len(got)} 件" if len(got) < len(rules) else ""))
    if not prepared.asked["questions"]:  # nothing to ask: the requirements file stands
        keys = "・".join(r["key"] for r in prepared.answered["answers"] if r["source"] == "default")
        return "要件のファイルのまま進みます" + (f" (既定値で足した {by['default']} 件: {keys})" if by["default"] else "")
    left = f"、答えが無いまま {len(prepared.unresolved)} 件" if prepared.unresolved else ""
    more = (f"。当たった規則の相談 {len(rules)} 件、答え {len(prepared.answered.get('rule_answers', []))} 件") if rules else ""
    return f"質問 {len(prepared.asked['questions'])} 件、答え {by['hearing']} 件、既定値 {by['default']} 件{left}{more}"
