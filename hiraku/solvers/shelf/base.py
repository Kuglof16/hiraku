"""Common parts of the built-in analysis tools (hiraku.contract.protocols.AnalysisTool)."""

from __future__ import annotations

import copy
import time
from typing import Any

from ... import contract
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


def without_parts(design: Record, exclude: list[str] | None) -> Record:
    """A copy of the design without the named parts (e.g. empty vehicle cases)."""
    if not exclude:
        return design
    ids = {p["id"] for p in design.get("parts", [])}
    unknown = [p for p in exclude if p not in ids]
    if unknown:
        raise LoadCaseError(f"exclude_parts names part(s) {', '.join(unknown)} that are not in the design")
    out = copy.deepcopy(design)
    out["parts"] = [p for p in out["parts"] if p["id"] not in exclude]
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
        rec["wall_time_s"] = time.perf_counter() - t0
        return rec

    # -- for subclasses

    def known_answers(self) -> list[Record]:
        raise NotImplementedError

    def analyse(self, design: Record, load_case: Record, params: dict[str, Any]) -> Record:
        """Return the analysis fields (results, fields, vectors, effects, ...)."""
        raise NotImplementedError
