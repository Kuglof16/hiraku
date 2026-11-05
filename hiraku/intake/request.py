"""From a request typed in A1 to a requirements file: the calls the screen's server makes.

The server keeps nothing between the calls: every answer carries a `draft`, and the next call sends it back.

    first = begin(want, product=None, flags=None, picker=claude_picker)   # A1: the domains; A2: the questions
    first = revise(first["draft"], flags={...}, exclude=["noise"])        # a chip changed, a card taken off
    done = save_request(first["draft"], answers, note, runs_dir / "requests")   # the files `hiraku run` takes

`handle_request` and `handle_answer` are the same steps over a JSON body, for the server's POST routes.
Where the flags come from: the person's own (`flags`), else the product's requirements file, else Claude
(`picker`, when the server has an API key), else the words of the sentence (hiraku.intake.guess). The
domains and the hearing follow from the flags by code in every case; Claude adds the reasons and names.
"""

from __future__ import annotations

import copy
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import yaml

from hiraku import contract
from hiraku.intake import events as ev
from hiraku.intake import hearing
from hiraku.intake.domains import IntakeError
from hiraku.intake.guess import guess_flags
from hiraku.intake.pipeline import intake, save
from hiraku.intake.products import load_product, product_files

Record = dict[str, Any]
Picker = Callable[[str], Record]

CHANGES = ("flags", "speed_class", "exclude", "limits")
LIMIT_KEYS = ("round_limit", "cost_limit_usd")
DRAFT_KEYS = ("want", "product", "flags", "speed_class", "exclude", "requirements", "reasons", "shown_names",
              "flag_reasons", "flags_from", "mode", "limits")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def claude_picker(want: str, **options: Any) -> Record:
    """Let Claude read the sentence and pick the domains (hiraku.intake.agent; needs ANTHROPIC_API_KEY).

    Returns what begin() takes from a picker: flags, speed_class, flag_reasons, reasons, shown_names, cost_usd.
    """
    from hiraku.intake import agent

    run = agent.pick_with_claude(want, **options)
    if run.record is None:
        raise IntakeError(run.error or "Claude は分野を選びませんでした")
    args = run.calls[-1] if run.calls else {}
    return {
        "flags": run.record["flags"], "speed_class": run.record.get("speed_class"),
        "flag_reasons": args.get("flag_reasons") or run.record.get("flag_reasons") or {},
        "reasons": args.get("reasons") or {}, "shown_names": args.get("shown_names") or {},
        "cost_usd": run.cost_usd,
    }


# ------------------------------------------------------------------------------------------ the steps


def begin(
    want: Any,
    *,
    product: Any = None,
    flags: dict[str, Any] | None = None,
    speed_class: str | None = None,
    exclude: list[str] | None = None,
    limits: dict[str, Any] | None = None,
    requirements: list[Record] | None = None,
    picker: Picker | None = None,
) -> Record:
    """The first look at a request: the domains it lights and the questions it leaves.

    product: a product HIRAKU has a requirements file for (its flags and values are used), or a short name
    for the file to write. A sentence that is exactly a product file's `want` names that product.
    """
    if not isinstance(want, str) or not want.strip():
        raise IntakeError("依頼の文がありません。作りたいものを書いてください")
    want = want.strip()
    label = _label(product)
    slug = label if label in product_files() else _product_of_sentence(want)
    draft: Record = {"want": want, "product": label or slug, "flags": {}, "speed_class": speed_class,
                     "exclude": list(exclude or []), "requirements": list(requirements or []), "reasons": {},
                     "shown_names": {}, "flag_reasons": {}, "flags_from": "", "mode": "code", "limits": _limits(limits)}
    warnings: list[str] = []
    cost: float | None = None
    if flags is not None:
        draft.update(flags=flags, flags_from="given")
    elif slug:
        own = load_product(slug)
        draft.update(flags=own["flags"], flags_from="product", product=label or slug)
        draft["speed_class"] = speed_class or own.get("speed_class")
    elif picker is not None:
        try:
            got = picker(want)
        except Exception as e:  # no key, no network, a budget stop: the screen goes on with the words of the sentence
            warnings.append(f"Claude に分野を選ばせられませんでした ({type(e).__name__}: {e})。文の言葉から選びました")
        else:
            cost = got.get("cost_usd")
            draft.update(flags=got["flags"], flags_from="claude", mode="claude", reasons=dict(got.get("reasons") or {}),
                         shown_names=dict(got.get("shown_names") or {}), flag_reasons=dict(got.get("flag_reasons") or {}))
            draft["speed_class"] = speed_class or got.get("speed_class")
    if not draft["flags_from"]:
        guessed, why, guessed_speed = guess_flags(want)
        draft.update(flags=guessed, flags_from="words", flag_reasons=why)
        draft["speed_class"] = speed_class or guessed_speed
        if not guessed:
            warnings.append("文から製品の性質を読み取れませんでした。「読み取ったこと」の印から選んでください")
    return _look(draft, warnings=warnings, cost_usd=cost)


def revise(draft: Any, **changes: Any) -> Record:
    """A chip changed (flags), a card taken off (exclude), a limit changed: the domains and questions again.

    Claude's reasons and names stay for the domains that stay lit; a domain that is new gets the code's.
    """
    d = _check_draft(draft)
    unknown = sorted(set(changes) - set(CHANGES))
    if unknown:
        raise IntakeError(f"直せるのは {', '.join(CHANGES)} だけです ({', '.join(unknown)} は直せません)")
    if "flags" in changes:
        if not isinstance(changes["flags"], dict):
            raise IntakeError("flags は {フラグ名: true / false} の形で渡してください")
        d["flags"] = changes["flags"]
        d["flags_from"] = "given"
    if "speed_class" in changes:
        d["speed_class"] = changes["speed_class"]
    if "exclude" in changes:
        d["exclude"] = list(changes["exclude"] or [])
    if "limits" in changes:
        d["limits"] = _limits(changes["limits"])
    return _look(d)


def finish(draft: Any, answers: Any = None, note: Any = None) -> Record:
    """The hearing's answers in: the requirements file to run (ready when every required item is settled).

    A2's "use the defaults" is the same call with no answers: what has a default takes it (source default).
    `events` are domains.picked, hearing.asked (if anything was asked) and hearing.answered, as a run has them.
    """
    return _settle(draft, answers, note)[0]


def save_request(draft: Any, answers: Any, note: Any, requests_dir: Path | str, *, now: datetime | None = None) -> Record:
    """finish(), and when the requirements are settled, the files `hiraku run` takes in
    <requests_dir>/<request_id>/: requirements.yaml and intake.json (the intake events of a run, with the
    built-in knowledge, in the contract's order). request_id is <YYYYmmdd-HHMMSS>-<product> (a number is added
    if the name is taken). Without a required answer nothing is written and `ready` is false.
    """
    out, res, rf = _settle(draft, answers, note)
    if not res.ready:
        return out
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    base = f"{stamp}-{rf['product']}"
    requests_dir = Path(requests_dir)
    request_id, n = base, 1
    while (requests_dir / request_id).exists():
        n += 1
        request_id = f"{base}-{n}"
    req_path, events_path = save(res, requests_dir / request_id, requirements_file=rf)
    out.update(request_id=request_id, folder=str(req_path.parent), path=str(req_path), intake_path=str(events_path))
    return out


# ------------------------------------------------------------------------ the same steps over a JSON body


def handle_request(body: Any, *, picker: Picker | None = None) -> Record:
    """POST /api/request: {want, product?, flags?, speed_class?, exclude?, limits?} the first time,
    {draft, flags? | speed_class? | exclude? | limits?} after a chip or a card was changed."""
    if not isinstance(body, dict):
        raise IntakeError("依頼は {want, ...} の形で渡してください")
    if "draft" in body:
        return revise(body["draft"], **{k: body[k] for k in CHANGES if k in body})
    return begin(body.get("want"), product=body.get("product"), flags=body.get("flags"), speed_class=body.get("speed_class"),
                 exclude=body.get("exclude"), limits=body.get("limits"), picker=picker)


def handle_answer(body: Any, requests_dir: Path | str, *, now: datetime | None = None) -> Record:
    """POST /api/request/answer: {draft, answers?, note?}. When the requirements are settled the run's files
    are written under requests_dir (save_request) and request_id, path (requirements.yaml) and intake_path
    (intake.json) are in the answer; else `ready` is false and `next` says what is missing."""
    if not isinstance(body, dict):
        raise IntakeError("答えは {draft, answers, note} の形で渡してください")
    return save_request(body.get("draft"), body.get("answers"), body.get("note"), requests_dir, now=now)


# ------------------------------------------------------------------------------------------- internals


def _label(product: Any) -> str | None:
    if product in (None, ""):
        return None
    if not isinstance(product, str) or not _SLUG.match(product):
        raise IntakeError("製品の名前は半角の英小文字・数字・- だけで書いてください (例: serving-robot)")
    return product


def _product_of_sentence(want: str) -> str | None:
    return next((slug for slug in product_files() if load_product(slug)["want"].strip() == want), None)


def _limits(limits: Any) -> Record:
    if limits in (None, {}):
        return {}
    if not isinstance(limits, dict) or set(limits) - set(LIMIT_KEYS):
        raise IntakeError(f"上限は {', '.join(LIMIT_KEYS)} だけです")
    return {k: v for k, v in limits.items() if v is not None}


def _check_draft(draft: Any) -> Record:
    if not isinstance(draft, dict) or not isinstance(draft.get("want"), str) or not isinstance(draft.get("flags"), dict):
        raise IntakeError("draft が違います。最初の答えの draft をそのまま送ってください")
    d = {k: copy.deepcopy(draft[k]) for k in DRAFT_KEYS if k in draft}
    for k, empty in (("exclude", []), ("requirements", []), ("reasons", {}), ("shown_names", {}), ("flag_reasons", {}),
                     ("limits", {})):
        d.setdefault(k, empty)
    d.setdefault("product", None)
    d.setdefault("speed_class", None)
    d.setdefault("flags_from", "given")
    d.setdefault("mode", "code")
    return d


def _known(d: Record) -> str | None:
    return d["product"] if d["product"] in product_files() else None


def _intake(d: Record, answers: Any = None, note: str | None = None, *, docs: bool = False):
    return intake(d["want"], d["flags"], d["speed_class"], product=_known(d), slug=d["product"], answers=answers, note=note,
                  requirements=d["requirements"], reasons=d["reasons"] or None, shown_names=d["shown_names"] or None,
                  flag_reasons=d["flag_reasons"] or None, exclude=d["exclude"], docs=docs)


def _look(d: Record, *, warnings: list[str] | None = None, cost_usd: float | None = None) -> Record:
    res = _intake(d)
    d["flags"] = res.domains["flags"]  # all flags, spelled out
    asked = [e for e in res.events if e["kind"] in (ev.DOMAINS_PICKED, ev.HEARING_ASKED)]
    out: Record = {
        "draft": d, "mode": d["mode"], "flags_from": d["flags_from"], "domains": res.domains,
        "hearing": {"questions": res.prepared.asked["questions"], "text": hearing.hearing_text(res.prepared)},
        "events": asked, "warnings": warnings or [],
    }
    if cost_usd is not None:
        out["cost_usd"] = cost_usd
    return out


def _file(d: Record, res: Any) -> Record:
    rf = copy.deepcopy(res.requirements_file)
    kept = list(dict.fromkeys([t for p in res.domains["picks"] for t in p["test_ids"]] + list(res.domains["assembly_test_ids"])))
    tests = None
    if (known := _known(d)):  # what a run needs besides the hearing, from the product's own file
        own = load_product(known)
        for k in (*LIMIT_KEYS, "design_file"):
            if k in own:
                rf[k] = own[k]
        tests = own.get("tests")
        if "beyond" in own:
            rf["beyond"] = list(own["beyond"])
    rf.update(d["limits"])
    if d["exclude"]:  # the catalog runs the tests of the domains left, and no others
        tests = [t for t in tests if t in kept] if tests else kept
    if tests:
        rf["tests"] = list(tests)
    if "beyond" in rf and "tests" in rf:
        rf["beyond"] = [t for t in rf["beyond"] if t in rf["tests"]]
    errors = contract.errors("requirements_file", rf)
    if errors:
        raise IntakeError("要件のファイルがきまりに合いません: " + "; ".join(str(e) for e in errors[:3]))
    return rf


def _settle(draft: Any, answers: Any, note: Any) -> tuple[Record, Any, Record]:
    d = _check_draft(draft)
    if answers is not None and not isinstance(answers, dict):
        raise IntakeError("answers は {Qxx: 値} の形で渡してください")
    res = _intake(d, answers, note if isinstance(note, str) else None, docs=True)  # the built-in knowledge goes into intake.json
    rf = _file(d, res)
    out: Record = {
        "draft": d, "ready": res.ready, "unresolved": list(res.prepared.unresolved), "text": hearing.hearing_text(res.prepared),
        "requirements_file": rf,
        "events": [e for e in res.events if e["kind"] in (ev.DOMAINS_PICKED, ev.HEARING_ASKED, ev.HEARING_ANSWERED)],
    }
    if not res.ready:
        keys = "・".join(q["req_key"] for q in res.prepared.asked["questions"]
                        if q["ask"] == "must" and q["req_key"] in res.prepared.unresolved)
        out["next"] = f"必須の質問 {keys} にまだ答えがありません。答えてから進めてください"
    return out, res, rf
