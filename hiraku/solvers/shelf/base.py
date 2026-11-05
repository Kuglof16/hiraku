"""Common parts of the built-in analysis tools (hiraku.contract.protocols.AnalysisTool)."""

from __future__ import annotations

import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from ... import contract
from ...contract import loading
from ...contract.protocols import DEFAULT_LOADING, LOADINGS
from ..k0_handcalc import units
from ..k0_handcalc.vehicle import DesignError
from .hot_spots import mark_predicted

Record = dict[str, Any]


class LoadCaseError(DesignError):
    """The load case parameters cannot be used; the message says which and why."""


@dataclass(frozen=True)
class Param:
    """A load case parameter a tool reads (load_case.params.<name>).

    unit: the unit the tool works in; a quantity in any unit of the same
    dimension (or of a unit in `also`) is accepted. None: a string.
    default: in `unit`, filled in when the parameter is absent (None: no
    default; `meaning` says what happens then).
    """

    name: str
    unit: str | None
    meaning: str
    default: float | str | None = None
    required: bool = False
    choices: tuple[str, ...] = ()
    also: tuple[str, ...] = ()

    def describe(self) -> Record:
        out: Record = {"name": self.name, "kind": "string" if self.unit is None else "quantity",
                       "required": self.required, "meaning": self.meaning}
        if self.unit is not None:
            out["unit"] = self.unit
        if self.also:
            out["also_in"] = list(self.also)
        if self.default is not None:
            out["default"] = self.default if self.unit is None else units.quantity(float(self.default), self.unit)
        if self.choices:
            out["choices"] = list(self.choices)
        return out

    def filled(self) -> Any:
        return self.default if self.unit is None else units.quantity(float(self.default), self.unit)

    def check(self, value: Any, what: str = "load case parameter") -> None:
        if self.unit is None:
            if not isinstance(value, str):
                raise LoadCaseError(f"{what} {self.name!r} must be a string, got {value!r}")
            if self.choices and value not in self.choices:
                raise LoadCaseError(f"{what} {self.name!r} must be one of {', '.join(self.choices)}, got {value!r}")
            return
        if not isinstance(value, dict) or not isinstance(value.get("unit"), str):
            raise LoadCaseError(f"{what} {self.name!r} must be a quantity with a unit, got {value!r}")
        try:
            dim = units.dimension(value["unit"])
        except ValueError as e:
            raise LoadCaseError(f"{what} {self.name!r}: {e}") from None
        if dim not in {units.dimension(u) for u in (self.unit, *self.also)}:
            raise LoadCaseError(f"{what} {self.name!r} must be in {' or '.join((self.unit, *self.also))} "
                                f"(or a unit of the same kind), got {value['unit']!r}")


@dataclass(frozen=True)
class Predicts:
    """Output `output` of the K0 formula `formula_id` predicts the tool's result `quantity`.

    <support> and <part> in a quantity stand for a support id (FL, ...) and a
    part named in the result list (battery, motor, controller).
    """

    quantity: str
    formula_id: str
    output: str
    note: str = ""

    def describe(self) -> Record:
        out = {"quantity": self.quantity, "formula_id": self.formula_id, "output": self.output}
        if self.note:
            out["note"] = self.note
        return out


LOADING = Param("loading", None, "who and what is on board (requirement key Q30)", default=DEFAULT_LOADING,
                choices=tuple(LOADINGS))
G_FACTOR = Param("g_factor", "1", "vertical load factor on every weight (a number, or an acceleration such as 2 g)",
                 default=1.0, also=("g",))


class _Declared(dict):
    """Load case params as a tool sees them: reading a name the tool did not
    declare in load_case_params is a fault of the tool."""

    def __init__(self, values: dict, names, tool: str) -> None:
        super().__init__(values)
        self._names = frozenset(names)
        self._tool = tool

    def _check(self, key) -> None:
        if key not in self._names:
            raise DesignError(f"tool {self._tool} reads the load case parameter {key!r} but does not declare it")

    def __getitem__(self, key):
        self._check(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        self._check(key)
        return super().get(key, default)

    def __contains__(self, key) -> bool:
        self._check(key)
        return super().__contains__(key)


def check_record(case: str, quantity: str, expected: float, got: float | None, unit: str,
                 tolerance_rel: float, source: str, case_ja: str | None = None,
                 source_ja: str | None = None) -> Record:
    """A `tool_check` record. Passes when |got - expected| <= tol * |expected|
    (or <= tol when the expected value is 0). case_ja, source_ja: the case and the source in plain
    Japanese for the screen (contract bf6712f), built beside the English from the same values."""
    if got is None:
        ok = False
    else:
        scale = abs(expected) if expected != 0 else 1.0
        ok = abs(got - expected) <= tolerance_rel * scale
    out = {"case": case, "quantity": quantity, "expected": float(expected),
           "got": None if got is None else float(got), "unit": unit,
           "tolerance_rel": float(tolerance_rel), "pass": bool(ok), "source": source}
    if case_ja:
        out["case_ja"] = case_ja
    if source_ja:
        out["source_ja"] = source_ja
    return out


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


def run_param(params: dict, p: Param) -> Any:
    """A run parameter (analyze's params.<name>, declared in the tool's run_params): a quantity in p.unit, or the
    string; p's default when absent."""
    if p.name not in params:
        return p.default
    v = params[p.name]
    p.check(v, "run parameter")
    if p.unit is None:
        return v
    try:
        return units.to_unit(v, p.unit, p.name)
    except ValueError as e:
        raise LoadCaseError(f"run parameter {e}") from None


def param_list(load_case: Record, name: str) -> list[str] | None:
    """A comma-separated string parameter as a list (None when absent)."""
    v = param_text(load_case, name)
    if v is None:
        return None
    return [s.strip() for s in v.split(",") if s.strip()]


def param_text(load_case: Record, name: str, default: str | None = None) -> str | None:
    """A string parameter of the load case (default when absent)."""
    v = load_case.get("params", {}).get(name)
    if v is None:
        return default
    if not isinstance(v, str):
        raise LoadCaseError(f"load case parameter {name!r} must be a string, got {v!r}")
    return v


def on_board(design: Record, load_case: Record) -> Record:
    """A copy of the design with only the occupants and cargo of params.loading
    (hiraku.contract.loading). Without params.loading: two_cargo."""
    param_text(load_case, "loading")  # a string, if given
    try:
        return loading.on_board(design, loading.loading_of(load_case))
    except ValueError as e:
        raise LoadCaseError(str(e)) from None


def mesh_of(design: Record, params: dict) -> tuple[Record | None, str | None]:
    """The version's shell mesh passed in params["mesh"] (tools with needs_mesh), or None and why
    it is not used: none passed (known-answer checks, unit tests), or made from another design. Its elements
    belong to the design's members or to its gusset parts."""
    mesh = (params or {}).get("mesh")
    if mesh is None:
        return None, None
    version = int(design.get("design_version", 1))
    if int(mesh.get("design_version", -1)) != version:
        return None, f"mesh {mesh.get('mesh_id')} is of design version {mesh.get('design_version')}, not {version}; not drawn"
    members = {m["id"] for m in design.get("members", [])}
    members |= {p["id"] for p in design.get("parts", []) if p.get("kind") == "gusset"}  # plates meshed with the frame
    stray = sorted(set(mesh.get("element_member", [])) - members)
    if stray:
        return None, f"mesh {mesh.get('mesh_id')} has elements of {', '.join(stray[:3])}, not members of this design; not drawn"
    return mesh, None


def result(quantity: str, value: float, unit: str, where: str | None = None) -> Record:
    r = {"quantity": quantity, "value": float(value), "unit": unit}
    if where:
        r["where"] = where
    return r


class BuiltinTool(ABC):
    """Base for tools whose known-answer checks are written in code.

    load_case_params declares every load case parameter the tool reads (name,
    unit, default, required); run() checks the given ones against it and fills
    in the defaults. run_params declares the settings analyze's params may carry
    (run_param reads them). predicts pairs the tool's result quantities with the
    K0 formula outputs that predict them; upstream says which other analyses the
    tool takes from run params["upstream_analyses"]. describe() returns all of
    it (the contract's tool_spec has no field for it).
    """

    name = ""
    title = ""
    domain = ""
    base = ""
    tests: list[str] = []
    formulas: list[str] = []
    load_case_params: tuple[Param, ...] = ()
    run_params: tuple[Param, ...] = ()
    predicts: tuple[Predicts, ...] = ()
    upstream = ""
    judged_as: dict[str, str] = {}  # criterion quantity -> the result this tool judges it on (contract)
    stage = 1  # the contract's tool_spec.stage: 0 layout and rules, 1 light, 2 vehicle dynamics, 3 heavy (HK5)
    applies_to: tuple[str, ...] = ()  # tool_spec.applies_to: the kinds of design (designgen.ARCHETYPES); () any

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
            "stage": self.stage,
            **({"applies_to": list(self.applies_to)} if self.applies_to else {}),
        }

    def check(self) -> list[Record]:
        self._checks = self.known_answers()
        return self._checks

    def describe(self) -> Record:
        """What a load case for this tool needs and which K0 formula outputs predict which results."""
        out: Record = {"name": self.name, "title": self.title, "domain": self.domain, "tests": list(self.tests),
                       "params": [p.describe() for p in self.load_case_params],
                       "predicts": [p.describe() for p in self.predicts],
                       "formulas": list(self.formulas), "stage": self.stage}
        if self.judged_as:
            out["judged_as"] = dict(self.judged_as)
        if self.applies_to:
            out["applies_to"] = list(self.applies_to)
        if self.run_params:
            out["run_params"] = [p.describe() for p in self.run_params]
        if self.upstream:
            out["upstream"] = self.upstream
        return out

    def declared(self, load_case: Record) -> Record:
        """The load case as analyse sees it: given parameters checked against
        load_case_params, defaults filled in, undeclared names not readable."""
        given = dict(load_case.get("params") or {})  # a plain copy, also of another tool's declared params
        values = dict(given)
        missing = []
        for p in self.load_case_params:
            if p.name in given:
                p.check(given[p.name])
            elif p.required:
                missing.append(f"{p.name} [{p.unit or 'string'}]: {p.meaning}")
            elif p.default is not None:
                values[p.name] = p.filled()
        if missing:
            raise LoadCaseError(f"{self.name} needs the load case parameters " + "; ".join(missing))
        return dict(load_case, params=_Declared(values, [p.name for p in self.load_case_params], self.name))

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
                rec.update(self.analyse(design, self.declared(load_case), params or {}))
            except DesignError as e:
                rec["status"] = "failed"
                rec["error"] = str(e)
                rec["results"] = []
        if rec.get("hot_spots"):  # predicted: from this run's predictions (hot_spots.mark_predicted)
            mark_predicted(rec["hot_spots"], predictions, self)
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

    # -- every tool writes these two

    @abstractmethod
    def known_answers(self) -> list[Record]:
        """The tool's checks against answers known in closed form (tool_check records)."""

    @abstractmethod
    def analyse(self, design: Record, load_case: Record, params: dict[str, Any]) -> Record:
        """Return the analysis fields (results, fields, vectors, effects, ...)."""
