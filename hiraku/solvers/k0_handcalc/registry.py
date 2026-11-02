"""The K0 formula ledger: ids, expressions, units, ranges, assumptions, sources.

Each formula is written once as sympy expressions in the formula's own units.
The registry converts the caller's quantities to those units, checks ranges
and preconditions, evaluates, and returns terms for contribution:

- formulas with additive `terms` return each term with its share of the total
- otherwise each input is returned with its elasticity d ln y / d ln x on the
  primary output (sympy derivative), which is the contribution of a factor in
  a product-type formula

A formula that is a small numeric model (a few degrees of freedom solved in
time, an integral) gives `fn` instead of sympy outputs; its elasticities come
from central differences.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable

import sympy as sp

from . import units

Record = dict[str, Any]


@dataclass
class Formula:
    formula_id: str
    title: str
    domain: str
    expression: str
    inputs: dict[str, dict]  # name -> {"unit", "description", "min"?, "max"?}
    outputs: dict[str, tuple[str, str, sp.Expr | None]]  # name -> (unit, description, expr of input symbols; None with fn)
    assumptions: list[str]
    source: str
    primary: str | None = None  # output used for terms; default: first output
    terms: dict[str, sp.Expr | None] | None = None  # additive pieces of the primary output (same unit); None values with fn
    requires: list[tuple[Any, str]] = field(default_factory=list)  # (sympy condition, or callable(values dict) -> bool; message)
    symbols: dict[str, sp.Symbol] = field(default_factory=dict)
    fn: Callable[[dict[str, float]], dict[str, float]] | None = None  # numeric model: input values -> output values

    def __post_init__(self) -> None:
        if not self.symbols:
            self.symbols = {name: sp.Symbol(name, real=True) for name in self.inputs}
        if self.primary is None:
            self.primary = next(iter(self.outputs))
        if self.fn is not None:
            names = list(self.inputs)
            self._req_fns = [((lambda *v, c=cond: bool(c(dict(zip(names, v))))), msg) for cond, msg in self.requires]
            return
        args = [self.symbols[n] for n in self.inputs]
        self._args = args
        self._out_fns = {n: sp.lambdify(args, expr, modules="math") for n, (_, _, expr) in self.outputs.items()}
        y = self.outputs[self.primary][2]
        self._dy_fns = {n: sp.lambdify(args, sp.diff(y, self.symbols[n]), modules="math") for n in self.inputs}
        self._term_fns = {n: sp.lambdify(args, e, modules="math") for n, e in (self.terms or {}).items()}
        self._req_fns = [(sp.lambdify(args, cond, modules="math"), msg) for cond, msg in self.requires]

    def record(self) -> Record:
        """The contract `formula` record."""
        return {
            "formula_id": self.formula_id,
            "title": self.title,
            "domain": self.domain,
            "expression": self.expression,
            "inputs": {n: dict(spec) for n, spec in self.inputs.items()},
            "outputs": {n: {"unit": u, "description": d} for n, (u, d, _) in self.outputs.items()},
            "assumptions": list(self.assumptions),
            "source": self.source,
        }

    def evaluate(self, inputs: dict[str, Record]) -> Record:
        unknown = sorted(set(inputs) - set(self.inputs))
        if unknown:
            raise ValueError(f"{self.formula_id}: unknown input(s) {', '.join(unknown)}; expected {', '.join(self.inputs)}")
        missing = [n for n in self.inputs if n not in inputs]
        if missing:
            raise ValueError(f"{self.formula_id}: missing input(s) {', '.join(missing)}")
        vals = []
        for name, spec in self.inputs.items():
            v = units.to_unit(inputs[name], spec["unit"], name)
            if not math.isfinite(v):
                raise ValueError(f"{self.formula_id}: input {name!r} is not a finite number")
            if "min" in spec and v < spec["min"]:
                raise ValueError(f"{self.formula_id}: input {name!r} = {v} {spec['unit']} is below the range minimum {spec['min']}")
            if "max" in spec and v > spec["max"]:
                raise ValueError(f"{self.formula_id}: input {name!r} = {v} {spec['unit']} is above the range maximum {spec['max']}")
            vals.append(v)
        for fn, msg in self._req_fns:
            if not bool(fn(*vals)):
                raise ValueError(f"{self.formula_id}: {msg}")
        outputs = {}
        try:
            raw = self._outputs(vals)
            for name, (unit, _, _) in self.outputs.items():
                outputs[name] = units.quantity(raw[name], unit)
        except (ValueError, ZeroDivisionError, OverflowError) as e:
            raise ValueError(f"{self.formula_id}: cannot evaluate with these inputs ({e})") from None
        bad = [n for n, q in outputs.items() if not math.isfinite(q["value"])]
        if bad:
            raise ValueError(f"{self.formula_id}: output(s) {', '.join(bad)} are not finite for these inputs")
        return {"outputs": outputs, "terms": self._terms(vals, outputs, raw), "expression": self.expression}

    def _outputs(self, vals: list[float]) -> dict[str, float]:
        if self.fn is not None:
            return self.fn(dict(zip(self.inputs, vals)))
        return {name: self._out_fns[name](*vals) for name in self.outputs}

    def _elasticity(self, i: int, vals: list[float], y: float) -> float | None:
        name = list(self.inputs)[i]
        if self.fn is None:
            return float(self._dy_fns[name](*vals)) * vals[i] / y
        x = vals[i]
        if x == 0:
            return None
        h = 1e-4 * abs(x)
        up, dn = list(vals), list(vals)
        up[i], dn[i] = x + h, x - h
        yp = self.fn(dict(zip(self.inputs, up)))[self.primary]
        ym = self.fn(dict(zip(self.inputs, dn)))[self.primary]
        return (yp - ym) / (2 * h) * x / y

    def _terms(self, vals: list[float], outputs: dict[str, Record], raw: dict[str, float]) -> list[Record]:
        """Additive terms (with fn: values returned by fn under the term names), else elasticities."""
        y = outputs[self.primary]["value"]
        if self.terms:
            unit = self.outputs[self.primary][0]
            out = []
            for name in self.terms:
                v = float(raw[name]) if self.fn is not None else float(self._term_fns[name](*vals))
                t = {"name": name, "value": v, "unit": unit}
                if y != 0:
                    t["share"] = v / y
                out.append(t)
            return out
        out = []
        for i, ((name, spec), x) in enumerate(zip(self.inputs.items(), vals)):
            t = {"name": name, "value": x, "unit": spec["unit"]}
            if y != 0:
                try:
                    e = self._elasticity(i, vals, y)
                    if e is not None and math.isfinite(e):
                        t["share"] = e
                except (ValueError, ZeroDivisionError, OverflowError):
                    pass
            out.append(t)
        return out


class Registry:
    """Implements hiraku.contract.protocols.FormulaRegistry."""

    def __init__(self, formulas: list[Formula]):
        self._by_id: dict[str, Formula] = {}
        for f in formulas:
            if f.formula_id in self._by_id:
                raise ValueError(f"duplicate formula id {f.formula_id}")
            self._by_id[f.formula_id] = f

    def __contains__(self, formula_id: str) -> bool:
        return formula_id in self._by_id

    def get(self, formula_id: str) -> Formula:
        try:
            return self._by_id[formula_id]
        except KeyError:
            raise ValueError(f"unknown formula {formula_id!r}; known: {', '.join(sorted(self._by_id))}") from None

    def list_formulas(self) -> list[Record]:
        return [f.record() for f in self._by_id.values()]

    def evaluate(self, formula_id: str, inputs: dict[str, Record]) -> Record:
        return self.get(formula_id).evaluate(inputs)
