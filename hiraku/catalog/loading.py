"""Who and what is on board: params["loading"] of a load case (catalog Q30).

Occupants and cargo are design parts (kind occupant / cargo). Occupants board
in the order they are listed in design.parts, so the first one is the driver.
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
    return load_case.get("params", {}).get("loading", DEFAULT_LOADING)


def parts_on_board(design: Record, loading: str = DEFAULT_LOADING) -> list[Record]:
    """design.parts without the occupants and cargo that this loading leaves off."""
    if loading not in LOADINGS:
        raise ValueError(f"unknown loading {loading!r}; use one of {', '.join(LOADINGS)}")
    out, seated = [], 0
    for p in design.get("parts", []):
        if p["kind"] == "occupant":
            if seated >= OCCUPANTS[loading]:
                continue
            seated += 1
        elif p["kind"] == "cargo" and not CARGO[loading]:
            continue
        out.append(p)
    return out
