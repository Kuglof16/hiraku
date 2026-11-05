"""analysis.hot_spots: the model's hottest places, highest utilisation first (docs/contract.md, CAE の役目).

Shared by the frame tools (frame_static, frame_shell, frame_tubes). A row is one place (a member id, as the
tools' results' `where` and per_member fields use) with the quantity the tool is judged on for the load case
(hiraku.tools.judge.judged_quantity of the criterion's quantity), its value, the allowable there, value /
allowable, and whether a prediction of this run named it.

- Only criteria with op "<=" or "<" (a stress or a utilisation under a limit) give rows; a ">=" criterion has
  no "hottest" place (its failing end is the smallest) and gives none. A load case without a criterion, or
  with a limit that is not a positive stress, gives none either (no allowable to divide by).
- The allowable is the criterion's limit, the same at every place: that is how the tools' judged results are
  judged now (the R1 / R1s builders' limit is the frame's lowest yield / Q61, uniform over the members).
- predicted: the contract's predictions carry no place (prediction: quantity, formula_id, inputs of numbers
  only), so a prediction of quantity q names the place of the tool's main result of q, which is the overall
  largest of q over the places: the row of quantity q with the largest value (mark_predicted). Every other
  row is predicted false.
"""

from __future__ import annotations

from typing import Any

from ..k0_handcalc import units

Record = dict[str, Any]  # as base.Record (base imports this module)

TOP_N = 10
SHARP_NOTE = "尖り。細かくすると上がる"
LIMIT_UNIT = "MPa"


def judged_quantity(tool, criterion_quantity: str) -> str:
    """The rule of hiraku.tools.judge.judged_quantity for a tool object: its judged_as (the spec's), else the
    criterion's own quantity. Not imported from there: the solvers do not import hiraku.tools (layers); the
    judge's fallback table JUDGED_QUANTITY only repeats frame_shell's judged_as. tests/solvers/test_hot_spots
    checks the two agree."""
    return (getattr(tool, "judged_as", None) or {}).get(criterion_quantity) or criterion_quantity


def judged_limit(tool, load_case: Record) -> tuple[str, float] | None:
    """(the quantity the tool is judged on, the criterion's limit in MPa) for a "<=" / "<" criterion on a
    stress; None when the load case gives no rows (no criterion, ">=" / ">", not a stress, limit not positive)."""
    crit = (load_case or {}).get("criterion")
    if not crit or crit.get("op") not in ("<=", "<"):
        return None
    try:
        limit = units.to_unit(crit["limit"], LIMIT_UNIT, crit["quantity"])
    except (KeyError, ValueError, TypeError):
        return None  # a limit that is not a stress (a count, a utilisation): no per-place stress to compare
    if not limit > 0:
        return None
    return judged_quantity(tool, crit["quantity"]), float(limit)


UTILISATION_CAP = 1000.0  # the contract's maximum (46306b5)


def utilisation(value: float, limit: float) -> float:
    """value / limit for a "<=" / "<" criterion (the only ones that give rows): over 1 exactly when the row fails.
    Where the ratio is undefined (limit or value not above 0) or outside 0 to 1000: 1000 when failing, else 0
    (contract 46306b5)."""
    failing = value > limit
    if not (limit > 0 and value >= 0):
        return UTILISATION_CAP if failing else 0.0
    u = value / limit
    if not 0.0 <= u <= UTILISATION_CAP:
        return UTILISATION_CAP if failing else 0.0
    return u


def row(where: str, quantity: str, value: float, limit: float, note: str | None = None,
        unit: str = LIMIT_UNIT, at_mm=None) -> Record:
    """One hot_spots row (predicted false until mark_predicted); at_mm: the point the value was read at
    (design coordinates, mm), when the tool knows it."""
    out = {"where": str(where), "quantity": quantity, "value": {"value": float(value), "unit": unit},
           "limit": {"value": float(limit), "unit": unit}, "utilisation": utilisation(float(value), float(limit)),
           "predicted": False}
    if at_mm is not None:
        out["at_mm"] = [round(float(x), 3) for x in at_mm]
    if note:
        out["note"] = note
    return out


def top(rows: list[Record], keep: list[Record] = (), n: int = TOP_N) -> list[Record]:
    """The n rows of highest utilisation, highest first; rows in `keep` are always in (they take the place
    of the lowest of `rows`)."""
    keep = list(keep)
    ranked = sorted(rows, key=lambda r: -r["utilisation"])[:max(n - len(keep), 0)]
    return sorted(ranked + keep, key=lambda r: -r["utilisation"])[:n]


def mark_predicted(hot: list[Record], predictions: list[Record]) -> None:
    """Set predicted on the rows from this run's predictions (module notes): for each quantity a prediction
    names, the largest row of that quantity is the place of the tool's main result, so it is predicted."""
    for r in hot:
        r["predicted"] = False
    for q in {p.get("quantity") for p in predictions or [] if p.get("quantity")}:
        rows = [r for r in hot if r["quantity"] == q]
        if rows:
            max(rows, key=lambda r: r["value"]["value"])["predicted"] = True
