"""The events of the intake steps, and a way to emit them that waits for the contract.

The event kinds below are proposed to the lead (docs: intake-contract-request). Until the contract
lists a kind, `emit_if_known` does nothing for it and says so, so the functions and tests of this
package work today and start recording the day the contract has the kinds.
"""

from __future__ import annotations

from typing import Any

from hiraku import contract
from hiraku.intake.domains import summary_line as domains_line
from hiraku.intake.hearing import Prepared, hearing_text

Record = dict[str, Any]

DOMAINS_PICKED = "domains.picked"
HEARING_ASKED = "hearing.asked"
HEARING_ANSWERED = "hearing.answered"
KNOWLEDGE_IMPORTED = "knowledge.imported"
RULE_CHECK_RECORDED = "rule_check.recorded"
INTAKE_KINDS = (DOMAINS_PICKED, HEARING_ASKED, HEARING_ANSWERED, KNOWLEDGE_IMPORTED, RULE_CHECK_RECORDED)


def known(kind: str) -> bool:
    """Does the contract have this event kind (and, for rule_check, the record kind)?"""
    if kind not in contract.EVENT_KINDS:
        return False
    return kind != RULE_CHECK_RECORDED or "rule_check" in contract.kinds()


def store_records(store: Any, kind: str) -> bool:
    """Does this RunStore write records of this kind (a file and its event) through add_record?"""
    from hiraku.store import run_store

    return kind in getattr(run_store, "RECORD_EVENT", {})


def picked_event(record: Record) -> Record:
    return {"kind": DOMAINS_PICKED, "agent": "lead", "text": domains_line(record), "data": record}


def asked_event(asked: Record) -> Record:
    n = len(asked["questions"])
    return {"kind": HEARING_ASKED, "agent": "lead", "text": f"足りない要件が {n} 件あります。まとめて聞きます", "data": asked}


def hearing_events(prepared: Prepared) -> list[Record]:
    """hearing.asked (when anything was asked) then hearing.answered."""
    out: list[Record] = []
    if prepared.asked["questions"]:
        out.append(asked_event(prepared.asked))
    out.append({"kind": HEARING_ANSWERED, "agent": "lead", "text": hearing_text(prepared), "data": prepared.answered})
    return out


def knowledge_event(record: Record) -> Record:
    doc = record["doc"]
    where = "最初から入っている" if doc["origin"] == "builtin" else "渡された"
    return {"kind": KNOWLEDGE_IMPORTED, "agent": "lead",
            "text": f"{where}ナレッジ「{doc['filename']}」から {len(record['rules'])} 件の規則", "data": record}


def intake_events(domains: Record | None, prepared: Prepared | None, docs: list[Record] | None = None) -> list[Record]:
    """Everything the intake steps say before the analysis starts, in the order they happen."""
    out: list[Record] = []
    if domains is not None:
        out.append(picked_event(domains))
    if prepared is not None:
        out.extend(hearing_events(prepared))
    for rec in docs or []:
        out.append(knowledge_event(rec))
    return out


def emit_if_known(store: Any, event: Record, *, design_version: int | None = None) -> str | None:
    """Emit one event on a RunStore. Returns None when emitted, else the reason it was not.

    Not emitted: the contract has no such kind yet, or the data does not fit the contract's shape
    (the message says how, so the contract and this package can be brought together).
    """
    if store is None:
        return "no run"
    if not known(event["kind"]):
        return f"event kind {event['kind']} is not in the contract yet"
    try:
        store.emit(event["kind"], agent=event.get("agent"), text=event.get("text"), data=event["data"],
                   design_version=design_version)
    except ValueError as e:
        return f"the contract refused {event['kind']}: {e}"
    return None
