"""Common parts of the built-in analysis tools (hiraku.contract.protocols.AnalysisTool)."""

from __future__ import annotations

import copy
import math
import time
from typing import Any

from ... import contract
from ...contract.protocols import DEFAULT_LOADING, LOADINGS
from ..k0_handcalc import units
from ..k0_handcalc.vehicle import DesignError

Record = dict[str, Any]


class LoadCaseError(DesignError):
    """The load case parameters cannot be used; the message says which and why."""


def check_record(case: str, quantity: str, expected: float, got: float | None, unit: str,
                 tolerance_rel: float, source: str) -> Record:
    """A `tool_check` record. Passes when |got - expected| <= tol * |expected|
    (or <= tol when the expected value is 0)."""
    if got is None:
        ok = False
    else:
        scale = abs(expected) if expected != 0 else 1.0
        ok = abs(got - expected) <= tolerance_rel * scale
    return {"case": case, "quantity": quantity, "expected": float(expected),
            "got": None if got is None else float(got), "unit": unit,
            "tolerance_rel": float(tolerance_rel), "pass": bool(ok), "source": source}


def param_value(load_case: Record, name: str, unit: str, default: float | None = None) -> float | None:
    """A quantity parameter of the load case converted to `unit` (default when absent)."""
    params = load_case.get("params", {})
    if name not in params:
        return default
    q = params[name]
    if not isinstance(q, dict):
        raise LoadCaseError(f"load case parameter {name!r} must be a quantity with a unit, got {q!r}")
    try:
        return units.to_unit(q, unit, name)
    except ValueError as e:
        raise LoadCaseError(f"load case parameter {e}") from None


def param_list(load_case: Record, name: str) -> list[str] | None:
    """A comma-separated string parameter as a list (None when absent)."""
    v = load_case.get("params", {}).get(name)
    if v is None:
        return None
    if not isinstance(v, str):
        raise LoadCaseError(f"load case parameter {name!r} must be a comma-separated string, got {v!r}")
    return [s.strip() for s in v.split(",") if s.strip()]


OCCUPANTS = {"empty": 0, "driver": 1, "two": 2, "two_cargo": 2}
CARGO = {"empty": False, "driver": False, "two": False, "two_cargo": True}
assert set(OCCUPANTS) == set(CARGO) == set(LOADINGS)


def on_board(design: Record, load_case: Record) -> Record:
    """A copy of the design with only the occupants and cargo of params.loading.

    Occupants board in the order they are listed (the first is the driver);
    cargo parts ride only with two_cargo. Without params.loading: two_cargo.
    """
    loading = load_case.get("params", {}).get("loading", DEFAULT_LOADING)
    if loading not in LOADINGS:
        raise LoadCaseError(f"unknown loading {loading!r}; use one of {', '.join(LOADINGS)}")
    parts, seated = [], 0
    for p in design.get("parts", []):
        if p["kind"] == "occupant":
            if seated >= OCCUPANTS[loading]:
                continue
            seated += 1
        elif p["kind"] == "cargo" and not CARGO[loading]:
            continue
        parts.append(p)
    # a design with fewer occupant parts than the loading names carries the ones it has
    # (products without seats); the catalog checks the loading against the requirements
    out = copy.copy(design)
    out["parts"] = parts
    return out


def result(quantity: str, value: float, unit: str, where: str | None = None) -> Record:
    r = {"quantity": quantity, "value": float(value), "unit": unit}
    if where:
        r["where"] = where
    return r


class BuiltinTool:
    """Base for tools whose known-answer checks are written in code."""

    name = ""
    title = ""
    domain = ""
    base = ""
    tests: list[str] = []
    formulas: list[str] = []

    def __init__(self) -> None:
        self._checks: list[Record] | None = None

    # -- AnalysisTool protocol

    def spec(self) -> Record:
        checks = self.check() if self._checks is None else self._checks
        return {
            "name": self.name,
            "title": self.title,
            "domain": self.domain,
            "base": self.base,
            "tests": list(self.tests),
            "origin": "builtin",
            "usable": bool(checks) and all(c["pass"] for c in checks),
            "checks": checks,
            "formulas": list(self.formulas),
        }

    def check(self) -> list[Record]:
        self._checks = self.known_answers()
        return self._checks

    def run(self, design: Record, load_case: Record, predictions: list[Record], params: dict[str, Any]) -> Record:
        ids = [p["prediction_id"] for p in predictions]
        if not ids:
            raise ValueError(f"{self.name}: run needs at least one prediction (record_prediction first)")
        rec: Record = {
            "tool": self.name,
            "base": self.base,
            "design_version": int(design.get("design_version", 1)),
            "load_case_id": load_case["load_case_id"],
            "prediction_ids": ids,
            "status": "ok",
            "results": [],
        }
        t0 = time.perf_counter()
        errs = contract.errors("design", design)
        if errs:
            rec["status"] = "failed"
            rec["error"] = "the design does not fit the contract: " + "; ".join(errs[:5])
        else:
            try:
                rec.update(self.analyse(design, load_case, params or {}))
            except DesignError as e:
                rec["status"] = "failed"
                rec["error"] = str(e)
                rec["results"] = []
        dropped = [r["quantity"] for r in rec["results"] if not math.isfinite(r["value"])]
        if dropped:  # JSON has no infinity; say what was left out instead
            rec["results"] = [r for r in rec["results"] if math.isfinite(r["value"])]
            rec.setdefault("effects", []).append({"name": "not finite, left out: " + ", ".join(dropped)})
        sens = (params or {}).get("sensitivity")
        if sens and rec["status"] == "ok":
            rec.setdefault("effects", []).extend(self._sensitivity(design, load_case, params, sens))
        rec["wall_time_s"] = time.perf_counter() - t0
        return rec

    def _sensitivity(self, design: Record, load_case: Record, params: dict, sens: dict) -> list[Record]:
        """K5 on this tool: params["sensitivity"] = {"quantity", "paths", "rel_step"?}."""
        from ..k5_sensitivity import sensitivity

        q, paths = sens.get("quantity"), sens.get("paths") or []
        if not q or not paths:
            return [{"name": "sensitivity skipped: give quantity and paths"}]
        try:
            return sensitivity(self, design, load_case, q, list(paths), float(sens.get("rel_step", 0.02)), params)
        except (KeyError, ValueError) as e:
            return [{"name": f"sensitivity not computed: {e}"}]

    # -- for subclasses

    def known_answers(self) -> list[Record]:
        raise NotImplementedError

    def analyse(self, design: Record, load_case: Record, params: dict[str, Any]) -> Record:
        """Return the analysis fields (results, fields, vectors, effects, ...)."""
        raise NotImplementedError
