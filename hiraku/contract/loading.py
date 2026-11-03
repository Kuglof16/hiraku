"""Who and what is on board for a load case (params.loading, catalog Q30).

The one rule every department uses (hand calc, beam model, vehicle physics,
geometry, catalog): occupants board in the order of design.parts (the first
one drives); cargo parts ride only with two_cargo. A design with no occupant
parts at all (a product without seats) carries none; a design with some
occupant parts but fewer than the loading names does not fit that loading.
"""

from __future__ import annotations

from typing import Any

from hiraku.contract.protocols import DEFAULT_LOADING, LOADINGS

Record = dict[str, Any]

OCCUPANTS = {"empty": 0, "driver": 1, "two": 2, "two_cargo": 2}
CARGO = {"empty": False, "driver": False, "two": False, "two_cargo": True}
LOADING_JA = {"empty": "空車", "driver": "運転者だけ", "two": "2 人", "two_cargo": "2 人 + 荷"}

assert set(OCCUPANTS) == set(CARGO) == set(LOADING_JA) == set(LOADINGS)


def loading_of(load_case: Record) -> str:
    """params.loading of a load case, or the default (two_cargo)."""
    return (load_case.get("params") or {}).get("loading") or DEFAULT_LOADING


def parts_on_board(design: Record, loading: str | None = None) -> list[Record]:
    """design.parts without the occupants and cargo this loading leaves off.

    Raises ValueError for an unknown loading, and when the design has occupant
    parts but fewer than the loading names.
    """
    loading = loading or DEFAULT_LOADING
    if loading not in LOADINGS:
        raise ValueError(f"unknown loading {loading!r}; use one of {', '.join(LOADINGS)}")
    parts = design.get("parts", [])
    occupants = sum(1 for p in parts if p["kind"] == "occupant")
    if 0 < occupants < OCCUPANTS[loading]:
        raise ValueError(f"loading {loading} needs {OCCUPANTS[loading]} occupant parts, the design has {occupants}")
    out, seated = [], 0
    for p in parts:
        if p["kind"] == "occupant":
            if seated >= OCCUPANTS[loading]:
                continue
            seated += 1
        elif p["kind"] == "cargo" and not CARGO[loading]:
            continue
        out.append(p)
    return out


def on_board(design: Record, loading: str | None = None) -> Record:
    """A shallow copy of the design whose parts are parts_on_board (the part dicts are shared)."""
    return {**design, "parts": parts_on_board(design, loading)}
