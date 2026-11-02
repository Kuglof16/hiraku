"""The small expression language of a rule's check: a yes/no question about numbers.

    bend_radius_mm >= thickness_mm
    outer_mm <= 35 * wall_mm
    weld_length_mm >= max(30, 6 * weld_throat_mm)

Names are facts about a part (hiraku.intake.facts). The grammar is Python's: numbers, names,
+ - * / and ** (small exponents), comparisons (chains allowed), and / or / not, min / max / abs.
Nothing else is evaluated, so a formula written by Claude from a sentence (or read from a file)
can be checked by code before it is used, and the code does the arithmetic.
"""

from __future__ import annotations

import ast
import re
from functools import lru_cache
from typing import Any

MAX_LENGTH = 300
FUNCTIONS = {"min": min, "max": max, "abs": abs}
_BIN = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
        ast.Div: lambda a, b: a / b, ast.Pow: lambda a, b: a ** b}
_CMP = {ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b, ast.Gt: lambda a, b: a > b,
        ast.GtE: lambda a, b: a >= b, ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b}


class ExprError(ValueError):
    """The formula is not in the language. The message says why and what is allowed."""


@lru_cache(maxsize=512)
def parse(expr: str) -> ast.Expression:
    if not isinstance(expr, str) or not expr.strip():
        raise ExprError("式が空です")
    if len(expr) > MAX_LENGTH:
        raise ExprError(f"式が長すぎます ({len(expr)} 字、{MAX_LENGTH} 字まで)")
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as e:
        raise ExprError(f"式 {expr!r} を読めません: {e.msg}") from None
    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.UAdd,
                             ast.BinOp, ast.Compare, ast.Load, *_BIN, *_CMP)):
            continue
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ExprError(f"式 {expr!r} に数字以外の定数 {node.value!r} は使えません")
        elif isinstance(node, ast.Name):
            continue
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS or node.keywords:
                raise ExprError(f"式 {expr!r} で呼べるのは {', '.join(FUNCTIONS)} だけです")
        else:
            raise ExprError(f"式 {expr!r} に {type(node).__name__} は使えません。"
                            "使えるのは数字・名前・+ - * / **・比較・and / or / not・min / max / abs です")
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            if not (isinstance(node.right, ast.Constant) and 0 <= node.right.value <= 4):
                raise ExprError(f"式 {expr!r}: ** の右は 0 から 4 の数字だけです")
    body = tree.body
    if not (isinstance(body, (ast.Compare, ast.BoolOp)) or (isinstance(body, ast.UnaryOp) and isinstance(body.op, ast.Not))):
        raise ExprError(f"式 {expr!r} は、はい / いいえで答える形 (比較か and / or / not) にしてください")
    return tree


def names(expr: str) -> list[str]:
    """The fact names a formula uses, in order of appearance."""
    used = {n.id for n in ast.walk(parse(expr)) if isinstance(n, ast.Name) and n.id not in FUNCTIONS}
    out: list[str] = []
    for m in re.finditer(r"[A-Za-z_][A-Za-z0-9_]*", expr):  # the order they are written
        if m.group() in used and m.group() not in out:
            out.append(m.group())
    return out


def _eval(node: ast.AST, values: dict[str, Any], missing: list[str]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval(node.body, values, missing)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        v = values.get(node.id)
        if v is None:
            if node.id not in missing:
                missing.append(node.id)
            return None
        return v
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, values, missing)
        if v is None:
            return None
        return {ast.Not: lambda x: not x, ast.USub: lambda x: -x, ast.UAdd: lambda x: +x}[type(node.op)](v)
    if isinstance(node, ast.BinOp):
        a, b = _eval(node.left, values, missing), _eval(node.right, values, missing)
        return None if a is None or b is None else _BIN[type(node.op)](a, b)
    if isinstance(node, ast.BoolOp):
        vals = [_eval(v, values, missing) for v in node.values]  # every name is looked at, so every gap is reported
        if any(v is None for v in vals):
            return None
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, values, missing)
        ok: Any = True
        for op, right_node in zip(node.ops, node.comparators):
            right = _eval(right_node, values, missing)
            if left is None or right is None:
                ok = None
            elif ok is not None:
                ok = ok and _CMP[type(op)](left, right)
            left = right
        return ok
    if isinstance(node, ast.Call):
        args = [_eval(a, values, missing) for a in node.args]
        return None if any(a is None for a in args) else FUNCTIONS[node.func.id](*args)
    raise ExprError(f"unexpected node {type(node).__name__}")


def evaluate(expr: str, values: dict[str, Any]) -> tuple[bool | None, list[str]]:
    """(answer, missing names). The answer is None while a name the formula needs has no value.

    Dividing by zero is reported as a missing value named "0 で割る".
    """
    missing: list[str] = []
    try:
        result = _eval(parse(expr), values, missing)
    except ZeroDivisionError:
        return None, ["0 で割る"]
    if missing:
        return None, missing
    return bool(result), []
