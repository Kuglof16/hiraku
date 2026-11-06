"""Evaluate an applies_if expression over product flags, and an arithmetic expression over quantities.

The applies_if grammar is Python's boolean subset: names, `and`, `or`, `not`, parentheses.
Names are flag names (hiraku.contract "flags"), speed_static / speed_walk /
speed_vehicle, and `always`. A flag that is absent counts as false.
The arithmetic one (evaluate_quantity, for a load case's limit_from) is below.
"""

from __future__ import annotations

import ast
import keyword
import math
import re
from functools import lru_cache

from hiraku import contract

SPEED_NAMES = ("speed_static", "speed_walk", "speed_vehicle")


def _known_names() -> set[str]:
    flags = contract.schema()["$defs"]["flags"]["propertyNames"]["enum"]
    return set(flags) | set(SPEED_NAMES) | {"always"}


@lru_cache(maxsize=None)
def _parse(expr: str) -> ast.Expression:
    tree = ast.parse(expr, mode="eval")
    known = _known_names()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if node.id not in known:
                raise ValueError(f"unknown name {node.id!r} in applies_if {expr!r}")
        elif not isinstance(node, (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.Load)):
            raise ValueError(f"applies_if {expr!r} may only use names, and, or, not, parentheses")
    return tree


def _eval(node: ast.AST, env: dict[str, bool]) -> bool:
    if isinstance(node, ast.Expression):
        return _eval(node.body, env)
    if isinstance(node, ast.Name):
        return env.get(node.id, False)
    if isinstance(node, ast.UnaryOp):
        return not _eval(node.operand, env)
    if isinstance(node, ast.BoolOp):
        values = (_eval(v, env) for v in node.values)
        return all(values) if isinstance(node.op, ast.And) else any(values)
    raise ValueError(f"unexpected node {type(node).__name__}")


def applies(expr: str, flags: dict[str, bool], speed_class: str | None = None) -> bool:
    env = {k: bool(v) for k, v in flags.items()}
    for name in SPEED_NAMES:
        env[name] = speed_class == name.removeprefix("speed_")
    env["always"] = True
    return _eval(_parse(expr), env)


# ---------------------------------------------------------------- arithmetic over quantities (limit_from.expr)
#
# The same safe subset idea for a number: names, numbers, + - * /, unary minus, parentheses, ** with a
# number as the exponent, min(a, b, ...) and max(a, b, ...). Nothing else of Python is evaluated. Each name
# is a quantity given as (value in SI, dimension: the six exponents of hiraku.solvers.k0_handcalc.units);
# + - min max need the same dimension on each side. A name that is a Python keyword (yield, as in
# "yield / Q61") is read as a name.

MAX_ARITH_CHARS = 300
_KW_PREFIX = "kw_"
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
Quantity = tuple[float, tuple[int, ...]]


class ExprError(ValueError):
    """An arithmetic expression that cannot be read or evaluated; the message says why."""


def _arith_tree(expr: str) -> ast.Expression:
    if not isinstance(expr, str) or not expr.strip():
        raise ExprError("the expression is empty")
    if len(expr) > MAX_ARITH_CHARS:
        raise ExprError(f"the expression is {len(expr)} characters long (at most {MAX_ARITH_CHARS})")
    text = _WORD.sub(lambda m: _KW_PREFIX + m.group(0) if keyword.iskeyword(m.group(0)) else m.group(0), expr.strip())
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        raise ExprError(f"{expr!r} is not an expression (numbers, names, + - * /, **, parentheses, min, max)") from None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if not (isinstance(node.func, ast.Name) and node.func.id in ("min", "max")) or node.keywords \
                    or len(node.args) < 2:
                raise ExprError(f"{expr!r}: the only calls are min(a, b, ...) and max(a, b, ...)")
        elif not isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Name, ast.Constant, ast.Load,
                                   ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd)):
            raise ExprError(f"{expr!r} may only use numbers, names, + - * /, **, parentheses, min and max")
        if isinstance(node, ast.Constant) and (isinstance(node.value, bool) or not isinstance(node.value, (int, float))):
            raise ExprError(f"{expr!r}: {node.value!r} is not a number")
    return tree


def arith_names(expr: str) -> list[str]:
    """The names an arithmetic expression reads, as written, in order of first use (min and max left out)."""
    tree = _arith_tree(expr)
    funcs = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    names = [n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and id(n) not in funcs]
    return list(dict.fromkeys(n.removeprefix(_KW_PREFIX) if keyword.iskeyword(n.removeprefix(_KW_PREFIX)) else n
                              for n in names))


def _dims_text(d: tuple[int, ...]) -> str:
    from hiraku.solvers.k0_handcalc import units

    return units.describe_dimension(d)


def _arith(node: ast.AST, env: dict[str, Quantity]) -> Quantity:
    if isinstance(node, ast.Expression):
        return _arith(node.body, env)
    if isinstance(node, ast.Constant):
        return float(node.value), (0, 0, 0, 0, 0, 0)
    if isinstance(node, ast.Name):
        name = node.id.removeprefix(_KW_PREFIX) if keyword.iskeyword(node.id.removeprefix(_KW_PREFIX)) else node.id
        if name not in env:
            raise ExprError(f"no value for the name {name!r}")
        return env[name]
    if isinstance(node, ast.UnaryOp):
        v, d = _arith(node.operand, env)
        return (-v if isinstance(node.op, ast.USub) else v), d
    if isinstance(node, ast.Call):
        args = [_arith(a, env) for a in node.args]
        if any(d != args[0][1] for _, d in args):
            raise ExprError(f"{node.func.id}(...) of different dimensions ("
                            + ", ".join(_dims_text(d) for _, d in args) + ")")
        return (min if node.func.id == "min" else max)(v for v, _ in args), args[0][1]
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Pow):
            if not isinstance(node.right, ast.Constant):
                raise ExprError("the exponent of ** must be a number")
            v, d = _arith(node.left, env)
            p = float(node.right.value)
            dims = [x * p for x in d]
            if abs(p) > 4 or any(abs(x - round(x)) > 1e-9 for x in dims):
                raise ExprError(f"** {p:g}: the exponent must be at most 4 and leave whole dimensions")
            try:
                out = float(v) ** p
            except (OverflowError, ZeroDivisionError, ValueError):
                raise ExprError(f"** {p:g} cannot be computed for {v:g}") from None
            if isinstance(out, complex):
                raise ExprError(f"** {p:g} of a negative value")
            return out, tuple(int(round(x)) for x in dims)
        (a, da), (b, db) = _arith(node.left, env), _arith(node.right, env)
        if isinstance(node.op, (ast.Add, ast.Sub)):
            if da != db:
                raise ExprError(f"adding or subtracting {_dims_text(da)} and {_dims_text(db)}")
            return (a + b if isinstance(node.op, ast.Add) else a - b), da
        if isinstance(node.op, ast.Mult):
            return a * b, tuple(x + y for x, y in zip(da, db))
        if b == 0:
            raise ExprError("division by zero")
        return a / b, tuple(x - y for x, y in zip(da, db))
    raise ExprError(f"unexpected {type(node).__name__}")  # _arith_tree let only the nodes above through


def evaluate_quantity(expr: str, env: dict[str, Quantity]) -> Quantity:
    """(value in SI, dimension) of an arithmetic expression over named quantities (ExprError when it cannot)."""
    v, d = _arith(_arith_tree(expr), env)
    if not math.isfinite(v):
        raise ExprError(f"{expr!r} does not give a finite number")
    return v, d
