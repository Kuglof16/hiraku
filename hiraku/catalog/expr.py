"""Evaluate an applies_if expression over product flags.

The grammar is Python's boolean subset: names, `and`, `or`, `not`, parentheses.
Names are flag names (hiraku.contract "flags"), speed_static / speed_walk /
speed_vehicle, and `always`. A flag that is absent counts as false.
"""

from __future__ import annotations

import ast
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
