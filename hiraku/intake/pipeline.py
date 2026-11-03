"""From a request to a requirements file: N09 then N14, in code, for whoever starts a run.

    result = intake(want, flags, "walk", product="serving-robot", answers=...)
    result.requirements_file   a requirements file (hiraku.contract) with every requirement settled
    result.events              the events of the steps, to emit after run.started (hiraku.intake.events)

`flags` are what Claude (or the person, in A1's chips) said the product is; the domains and the
hearing follow from them by code. A product HIRAKU has a requirements file for (product=slug) lends its
own values as the defaults of the hearing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from hiraku.intake import events as ev
from hiraku.intake import hearing, knowledge
from hiraku.intake.domains import IntakeError, pick_domains
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
) -> IntakeResult:
    """Pick the domains, settle the hearing, and list the events. `requirements`: what the request already says.

    from_file: the requirements are a file somebody wrote (a run started from a requirements file): the file
    stands, nothing is asked but a `must` item it lacks, and a default is added (source default) for an item
    it lacks and has one for. Without it (a request typed in A1) every missing `must` and `confirm` item is asked.
    The merged requirements are `result.requirements_file["requirements"]`: use them as project.requirements.
    """
    domains = pick_domains(want, flags, speed_class, flag_reasons=flag_reasons, reasons=reasons, shown_names=shown_names,
                           exclude=exclude)
    slug = product or slug_for(want)
    own: dict[str, Record] = {}
    if product and product in product_files():
        own = {r["key"]: r for r in load_product(product)["requirements"]}
    elif product:
        raise IntakeError(f"製品 {product!r} の要件のファイルはありません。ある製品: {', '.join(product_files())}")
    req: Record = {"want": domains["want"], "product": slug, "flags": domains["flags"], "requirements": list(requirements or [])}
    if speed_class:
        req["speed_class"] = speed_class
    prepared = hearing.prepare(req, answers, note, product_defaults=own, ask_confirm=not from_file)
    built_in = knowledge.load_builtin() if docs else []
    return IntakeResult(domains, prepared, built_in, ev.intake_events(domains, prepared, built_in))
