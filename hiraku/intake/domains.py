"""N09: pick the analysis domains a product needs.

The code decides, from the catalog (data/catalog/tests.yaml), which tests apply to a
product's flags and so which of the seven domains light up. Claude decides what the code
cannot: which flags a sentence implies, why each domain is (not) used, and what a domain
is called for this product (aero is "spilling" for a serving robot).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from hiraku import contract
from hiraku.catalog import Catalog, applies, builder_for, load_catalog
from hiraku.intake.flags import FLAGS, describe_condition, flags_in, symbol

Record = dict[str, Any]

NAMES_FILE = Path(__file__).with_name("domain_names.yaml")
ANALYSIS_DOMAINS: tuple[str, ...] = tuple(d for d in contract.DOMAINS if d != "assembly")


class IntakeError(ValueError):
    """Bad input to an intake function. The message says what is wrong and what to send instead."""


@lru_cache(maxsize=1)
def _renames() -> dict[str, list[Record]]:
    with NAMES_FILE.open(encoding="utf-8") as f:
        return yaml.safe_load(f)["renames"]


def normalize_flags(flags: dict[str, Any]) -> dict[str, bool]:
    """All the contract's flags, false where absent. Unknown names and non-booleans are errors."""
    unknown = [k for k in flags if k not in FLAGS]
    if unknown:
        raise IntakeError(f"フラグ {', '.join(unknown)} はありません。使えるのは {', '.join(FLAGS)} です")
    bad = [k for k, v in flags.items() if not isinstance(v, bool)]
    if bad:
        raise IntakeError(f"フラグ {', '.join(bad)} は true か false で渡してください")
    return {f: bool(flags.get(f, False)) for f in FLAGS}


def tests_by_domain(catalog: Catalog, flags: dict[str, bool], speed_class: str | None) -> dict[str, list[str]]:
    """The catalog's tests that apply to these flags, per domain (assembly included), in catalog order."""
    out: dict[str, list[str]] = {d: [] for d in contract.DOMAINS}
    for t in catalog.tests:
        if applies(t["applies_if"], flags, speed_class):
            out[t["domain"]].append(t["id"])
    return out


def default_name(domain: str, flags: dict[str, bool], speed_class: str | None, test_ids: list[str]) -> str:
    """The name the code proposes for a domain on this product."""
    for rule in _renames().get(domain, []):
        if "when" in rule and not applies(rule["when"], flags, speed_class):
            continue
        if "only_tests" in rule and not (test_ids and set(test_ids) <= set(rule["only_tests"])):
            continue
        return rule["name"]
    return contract.DOMAIN_NAMES_JA[domain]


def _lit_flags(catalog: Catalog, test_ids: list[str], flags: dict[str, bool]) -> list[str]:
    seen: list[str] = []
    for tid in test_ids:
        for f in flags_in(catalog.test(tid)["applies_if"]):
            if flags.get(f) and f not in seen:
                seen.append(f)
    return seen


def buildable(catalog: Catalog, test_ids: list[str]) -> list[str]:
    """The tests that have a load-case builder: the ones a run can build a case for (the others would stay pending)."""
    out = []
    for tid in test_ids:
        try:
            if builder_for(catalog, tid) is not None:
                out.append(tid)
        except KeyError:  # a `build:` name nobody has
            pass
    return out


def unbuilt_note(catalog: Catalog, test_ids: list[str]) -> str:
    """What the card says about applicable tests that cannot be run yet ("" when there are none)."""
    if not test_ids:
        return ""
    names = "・".join(f"{tid} {catalog.test(tid)['title']}" for tid in test_ids)
    return f"{names} は荷重条件の作り方がまだ無いので、この実行では見ない"


def code_reason(catalog: Catalog, domain: str, test_ids: list[str], flags: dict[str, bool],
                unbuilt: list[str] | None = None) -> str:
    """The reason the code writes when Claude gave none: which flags make which tests apply.
    `unbuilt` are applicable tests with no builder: they light the domain but are not run."""
    lit = "・".join(symbol(f) for f in _lit_flags(catalog, [*test_ids, *(unbuilt or [])], flags))
    tests = "・".join(f"{tid} {catalog.test(tid)['title']}" for tid in test_ids)
    base = (f"{lit} に当てはまるので、{tests} を当てる" if lit else f"{tests} を当てる") if tests else f"{lit} に当てはまる"
    note = unbuilt_note(catalog, unbuilt or [])
    return f"{base}。{note}" if note else base


def code_reason_off(catalog: Catalog, domain: str) -> str:
    """Why no test of a domain applies: the conditions they need."""
    tests = [t for t in catalog.tests if t["domain"] == domain]
    shown = "・".join(f"{t['id']} {t['title']} ({describe_condition(t['applies_if'])})" for t in tests[:4])
    more = "…" if len(tests) > 4 else ""
    return f"この製品の性質では当てはまる試験が無い: {shown}{more}"


def _check_keys(what: str, mapping: dict[str, Any] | None, allowed: tuple[str, ...]) -> dict[str, Any]:
    mapping = dict(mapping or {})
    bad = [k for k in mapping if k not in allowed]
    if bad:
        raise IntakeError(f"{what} に {', '.join(bad)} は使えません。使えるのは {', '.join(allowed)} です")
    return mapping


def pick_domains(
    want: str,
    flags: dict[str, Any],
    speed_class: str | None = None,
    *,
    flag_reasons: dict[str, str] | None = None,
    reasons: dict[str, str] | None = None,
    shown_names: dict[str, str] | None = None,
    exclude: list[str] | None = None,
    catalog: Catalog | None = None,
) -> Record:
    """The `domains_picked` record (see intake-contract-request) for a sentence and its flags.

    The domains and their tests come from the catalog and the flags only. `reasons` and
    `shown_names` (Claude's words) are used when given; otherwise the code writes both.
    `exclude`: domains the person took off in A1.
    """
    if not want or not want.strip():
        raise IntakeError("want (作りたいものの文) が要ります")
    if speed_class is not None and speed_class not in contract.schema()["$defs"]["speed_class"]["enum"]:
        raise IntakeError(f"speed_class は static・walk・vehicle のどれか ({speed_class!r} は使えません)")
    catalog = catalog or load_catalog()
    flags = normalize_flags(flags)
    flag_reasons = _check_keys("flag_reasons", flag_reasons, tuple(FLAGS))
    reasons = _check_keys("reasons", reasons, ANALYSIS_DOMAINS)
    shown_names = _check_keys("shown_names", shown_names, ANALYSIS_DOMAINS)
    exclude = list(exclude or [])
    _check_keys("exclude", dict.fromkeys(exclude), ANALYSIS_DOMAINS)

    by_domain = tests_by_domain(catalog, flags, speed_class)
    picks: list[Record] = []
    off: list[Record] = []
    for d in ANALYSIS_DOMAINS:
        ids = by_domain[d]
        if not ids:
            off.append({"domain": d, "reason": code_reason_off(catalog, d)})
        elif d in exclude:
            off.append({"domain": d, "reason": "人が外した"})
        else:
            name = (shown_names.get(d) or "").strip() or default_name(d, flags, speed_class, ids)
            runnable = buildable(catalog, ids)  # test_ids are the ones a run can build a case for
            unbuilt = [t for t in ids if t not in runnable]
            given = (reasons.get(d) or "").strip()
            note = unbuilt_note(catalog, unbuilt)
            reason = (f"{given}。{note}" if given and note else given) or code_reason(catalog, d, runnable, flags, unbuilt)
            picks.append({"domain": d, "shown_name": name, "reason": reason, "test_ids": runnable})
    record: Record = {"want": want.strip(), "flags": flags}
    if speed_class is not None:
        record["speed_class"] = speed_class
    if flag_reasons:
        record["flag_reasons"] = {f: flag_reasons[f] for f in FLAGS if f in flag_reasons}
    record["picks"] = picks
    record["off"] = off
    record["assembly_test_ids"] = buildable(catalog, by_domain["assembly"])
    return record


def reasons_missing(record: Record, reasons: dict[str, str] | None) -> list[str]:
    """Picked domains for which the caller gave no reason (the code wrote one)."""
    given = {d for d, r in (reasons or {}).items() if r and r.strip()}
    return [p["domain"] for p in record["picks"] if p["domain"] not in given]


def project_picks(record: Record) -> list[Record]:
    """The picks in the shape project.json holds (domain_pick without test_ids)."""
    return [{k: p[k] for k in ("domain", "shown_name", "reason")} for p in record["picks"]]


def summary_line(record: Record) -> str:
    """"7 分野: 操安 (5)・動力 (4)…" for logs and the chat reply."""
    names = "・".join(f"{p['shown_name']} ({len(p['test_ids'])})" for p in record["picks"])
    return f"{len(record['picks'])} 分野: {names}"
