"""From a request to a requirements file: N09 then N14, in code, for whoever starts a run.

    result = intake(want, flags, "walk", product="serving-robot", answers=...)
    result.requirements_file   a requirements file (hiraku.contract) with every requirement settled
    result.events              the events of the steps, to emit after run.started (hiraku.intake.events)

`flags` are what Claude (or the person, in A1's chips) said the product is; the domains and the
hearing follow from them by code. A product HIRAKU has a requirements file for (product=slug) lends its
own values as the defaults of the hearing.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from hiraku import contract
from hiraku.intake import events as ev
from hiraku.intake import hearing, knowledge
from hiraku.intake.domains import IntakeError, add_tests, pick_domains
from hiraku.intake.products import load_product, product_files

Record = dict[str, Any]


@dataclass
class IntakeResult:
    domains: Record  # domains_picked
    prepared: hearing.Prepared
    knowledge_docs: list[Record]
    events: list[Record]

    @property
    def requirements_file(self) -> Record:
        return self.prepared.requirements_file

    @property
    def ready(self) -> bool:
        return self.prepared.ready


def slug_for(want: str) -> str:
    """A product slug for a sentence nobody has a file for: ascii letters and digits of it, else "product"."""
    words = re.findall(r"[a-z0-9]+", want.lower())
    return "-".join(words[:4]) or "product"


def intake(
    want: str,
    flags: dict[str, Any],
    speed_class: str | None = None,
    *,
    product: str | None = None,
    answers: dict[str, Any] | None = None,
    note: str | None = None,
    requirements: list[Record] | None = None,
    reasons: dict[str, str] | None = None,
    shown_names: dict[str, str] | None = None,
    flag_reasons: dict[str, str] | None = None,
    exclude: list[str] | None = None,
    docs: bool = True,
    from_file: bool = False,
    slug: str | None = None,
    hearing_mode: str = "code",
    added_tests: list[Record] | None = None,
    no_addition_reason: str | None = None,
) -> IntakeResult:
    """Pick the domains, settle the hearing, and list the events. `requirements`: what the request already says.

    from_file: the requirements are a file somebody wrote (a run started from a requirements file): the file
    stands, nothing is asked but a `must` item it lacks, and a default is added (source default) for an item
    it lacks and has one for. Without it (a request typed in A1) every missing `must` and `confirm` item is asked.
    The merged requirements are `result.requirements_file["requirements"]`: use them as project.requirements.
    slug: what the requirements file calls the product (default: `product`, else a name made of the sentence).
    added_tests, no_addition_reason: Claude's review of the code's choice (add_tests): tests it adds, each with its reason,
    or why it adds none; they are put into the picks after the code has chosen (adding only).
    hearing_mode: the requirements file's `hearing` (contract). "code" (default): the hearing is settled here, from the
    file and the defaults. "claude" (with from_file): nothing is settled or asked here; the requirements stay as the file has
    them and the events are the domains and the knowledge only: the lead asks with ask_user, which asks the `must` and the
    `confirm` items the run lacks.
    """
    if hearing_mode not in ("code", "claude"):
        raise IntakeError(f"hearing は code か claude です ({hearing_mode!r} は使えません)")
    domains = pick_domains(want, flags, speed_class, flag_reasons=flag_reasons, reasons=reasons, shown_names=shown_names,
                           exclude=exclude, product=product)
    if added_tests or no_addition_reason:
        from hiraku.catalog import load_catalog
        from hiraku.intake.domains import has_builder

        catalog = load_catalog()  # a draft may hold a test no builder makes (an older one): it is left out, not an error
        keep = [a for a in (added_tests or []) if has_builder(catalog, a["test_id"], product)]
        if keep or no_addition_reason:
            domains = add_tests(domains, keep, no_addition_reason, product=product)[0]
    slug = slug or product or slug_for(want)  # slug: the file's name for the product when no file of its own lends values
    own: dict[str, Record] = {}
    if product and product in product_files():
        own = {r["key"]: r for r in load_product(product)["requirements"]}
    elif product:
        raise IntakeError(f"製品 {product!r} の要件のファイルはありません。ある製品: {', '.join(product_files())}")
    req: Record = {"want": domains["want"], "product": slug, "flags": domains["flags"], "requirements": list(requirements or [])}
    if speed_class:
        req["speed_class"] = speed_class
    built_in = knowledge.load_builtin() if docs else []
    if from_file and hearing_mode == "claude":  # left open for ask_user: no hearing events, the file's requirements as they are
        left = hearing.Prepared(copy.deepcopy(req), {"questions": []}, {"answers": []}, [], [])
        return IntakeResult(domains, left, built_in, ev.intake_events(domains, None, built_in))
    prepared = hearing.prepare(req, answers, note, product_defaults=own, ask_confirm=not from_file)
    return IntakeResult(domains, prepared, built_in, ev.intake_events(domains, prepared, built_in))


def save(result: IntakeResult, folder: Path | str, *, requirements_file: Record | None = None) -> tuple[Path, Path]:
    """Write a settled intake for `hiraku run`: folder/requirements.yaml and folder/intake.json (the events, in order).

    requirements_file: the file to write when it holds more than the hearing's result (a run's design_file, limits, tests).
    """
    if not result.ready:
        raise IntakeError("必須の質問にまだ答えがありません。答えてから保存してください")
    rf = requirements_file if requirements_file is not None else result.requirements_file
    errors = contract.errors("requirements_file", rf)
    if errors:
        raise IntakeError("要件のファイルとして書けません: " + "; ".join(str(e) for e in errors[:3]))
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    req_path, events_path = folder / "requirements.yaml", folder / "intake.json"
    req_path.write_text(yaml.safe_dump(rf, allow_unicode=True, sort_keys=False), encoding="utf-8", newline="\n")
    events_path.write_text(json.dumps(result.events, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    return req_path, events_path
