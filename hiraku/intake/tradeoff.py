"""Trade-offs: two goals that one design number pulls in opposite directions, with the numbers worked out by code.

The input is what K5 gives (hiraku/solvers/k5_sensitivity): for each result quantity a list of contract `term` records
{name: "<quantity> to <path>", value: the number now, unit, share: the elasticity d ln y / d ln x}. A trade-off is a design
number whose elasticities to two results have opposite signs and are both large enough: raising it helps one result and
hurts the other (both results are of the kind "lower is better" unless the caller says otherwise). The sentences and
the numbers in them come from here; the model passes only the id of a pair it was given.
"""

from __future__ import annotations

import math
from typing import Any

from hiraku.intake.domains import IntakeError

Record = dict[str, Any]

MIN_ELASTICITY = 0.1  # a result that moves less than 0.1 % for 1 % of the number is not a side of a trade-off
STEP_PCT = 10.0  # the step the question talks about
CHOICES = ["favor_a", "favor_b", "keep_both"]

# What gathers the sensitivities of a run (hiraku.tools, above this package, registers it):
# collector(ctx, paths, only_quantities) -> {sens: {quantity: [term]}, meta: {quantity: {label, unit, lower_is_better}},
# bases: {quantity: {value, record_id}}, skipped: [why]}
collector = None


def register_collector(fn) -> None:
    global collector
    collector = fn


def _split(name: str) -> tuple[str, str] | None:
    """('<quantity>', '<path>') of a K5 term name, or None for a term that carries no elasticity (noise, zero)."""
    if " to " not in name or "(" in name:
        return None
    quantity, _, path = name.partition(" to ")
    return quantity.strip(), path.strip()


def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _side(meta: Record, quantity: str, elasticity: float, base: Record | None) -> Record:
    """One side of a pair: what the result is, and what it becomes when the number goes up STEP_PCT %."""
    out: Record = {"name": meta.get("label") or quantity, "quantity": quantity, "elasticity": round(elasticity, 3),
                   "change_pct": round(elasticity * STEP_PCT, 1)}
    if meta.get("unit"):
        out["unit"] = meta["unit"]
    if base and _num(base.get("value")):
        out["now"] = base["value"]
        out["after"] = round(base["value"] * (1.0 + elasticity * STEP_PCT / 100.0), 4)
        if base.get("record_id"):
            out["record_id"] = base["record_id"]
    return out


def _worse_when_up(elasticity: float, meta: Record) -> bool:
    lower_is_better = meta.get("lower_is_better", True)
    return (elasticity > 0) == bool(lower_is_better)


def find(sensitivities: dict[str, list[Record]], quantities: dict[str, Record] | None = None,
         bases: dict[str, Record] | None = None, *, min_elasticity: float = MIN_ELASTICITY,
         labels: dict[str, str] | None = None) -> list[Record]:
    """The trade-offs in `sensitivities` ({quantity: [term, ...]}), the strongest first.

    quantities: {quantity: {label, unit, lower_is_better}} (label is what the person reads; default lower is better).
    bases: {quantity: {value, record_id}} the result now (for the 'now' and 'after' numbers; optional).
    labels: {path or variable name: the name a person reads} for the number that is moved (without one, the variable's
    own name is shown, not the path).
    Each pair: {tradeoff_id, variable, variable_value, variable_unit, a, b, step_pct, text, choices}; `a` is the side that
    gets better when the number goes up, `b` the side that gets worse.
    """
    quantities = quantities or {}
    bases = bases or {}
    by_path: dict[str, dict[str, Record]] = {}
    for quantity, terms in sensitivities.items():
        for t in terms:
            split = _split(str(t.get("name", "")))
            if not split or split[0] != quantity or not _num(t.get("share")):
                continue
            by_path.setdefault(split[1], {})[quantity] = t
    out: list[Record] = []
    for path, per_quantity in sorted(by_path.items()):
        names = sorted(per_quantity)
        for i, qa in enumerate(names):
            for qb in names[i + 1:]:
                ta, tb = per_quantity[qa], per_quantity[qb]
                ea, eb = float(ta["share"]), float(tb["share"])
                if min(abs(ea), abs(eb)) < min_elasticity:
                    continue
                meta_a, meta_b = quantities.get(qa, {}), quantities.get(qb, {})
                bad_a, bad_b = _worse_when_up(ea, meta_a), _worse_when_up(eb, meta_b)
                if bad_a == bad_b:  # both better or both worse: no trade-off
                    continue
                better, worse = (qb, qa) if bad_a else (qa, qb)  # `a` gets better when the number goes up
                e_better, e_worse = (eb, ea) if bad_a else (ea, eb)
                pair = {
                    "tradeoff_id": f"{path}:{better}:{worse}", "variable": path, "variable_value": ta["value"] if "value" in ta else None,
                    "step_pct": STEP_PCT,
                    "a": _side(quantities.get(better, {}), better, e_better, bases.get(better)),
                    "b": _side(quantities.get(worse, {}), worse, e_worse, bases.get(worse)),
                    "choices": list(CHOICES),
                }
                unit = ta.get("unit")
                if unit and unit != "1":  # a number without a unit (a ratio) is written without one
                    pair["variable_unit"] = unit
                pair["variable_label"] = _label(path, labels or {})
                pair["text"] = text(pair)
                pair["strength"] = round(min(abs(ea), abs(eb)), 3)
                out.append(pair)
    out.sort(key=lambda p: (-p["strength"], p["tradeoff_id"]))
    return out


def _fmt(x: float) -> str:
    return f"{x:.3g}"


def _side_text(side: Record, better: bool) -> str:
    word = "良くなり" if better else "悪くなり"
    pct = abs(side["change_pct"])
    base = f"{side['name']}は約 {_fmt(pct)} % {word}"
    if "now" in side:
        unit = f" {side['unit']}" if side.get("unit") else ""
        base += f" ({_fmt(side['now'])} → {_fmt(side['after'])}{unit})"
    return base


def _label(path: str, labels: dict[str, str]) -> str:
    """What the person reads for the moved number: the given name, else the variable's own name (not the path)."""
    short = path.split(".", 1)[1] if path.startswith("variables.") else path
    return labels.get(path) or labels.get(short) or short


def text(pair: Record) -> str:
    """The question in the person's words, with the numbers of the pair."""
    unit = f" {pair['variable_unit']}" if pair.get("variable_unit") else ""
    now = f" (いま {_fmt(pair['variable_value'])}{unit})" if _num(pair.get("variable_value")) else ""
    return (f"{pair.get('variable_label') or pair['variable']}{now} を {_fmt(pair['step_pct'])} % 上げると、{_side_text(pair['a'], True)}、"
            f"{_side_text(pair['b'], False)}。この 2 つは両立しにくいので、どちらを優先しますか。")


def check_answers(raw: Any, asked: list[Record] | None = None) -> list[Record]:
    """The trade-off answers [{tradeoff_id, choice, note?}]; `asked` are the run's tradeoff_questions."""
    if raw in (None, []):
        return []
    if not isinstance(raw, list):
        raise IntakeError("tradeoff_answers は [{tradeoff_id, choice, note}] の形で渡してください")
    offered = {q["tradeoff_id"]: q.get("choices") or CHOICES for q in asked or []}
    out = []
    for a in raw:
        if not isinstance(a, dict) or set(a) - {"tradeoff_id", "choice", "note"} or not {"tradeoff_id", "choice"} <= set(a):
            raise IntakeError("tradeoff_answers の 1 件は {tradeoff_id, choice, note} (note は任意) の形です")
        if asked is not None and a["tradeoff_id"] not in offered:
            raise IntakeError(f"トレードオフ {a['tradeoff_id']} は聞いていません。聞いたもの: {', '.join(offered) or 'なし'}")
        if a["choice"] not in offered.get(a["tradeoff_id"], CHOICES):
            raise IntakeError(f"{a['tradeoff_id']} の選択肢は {' か '.join(offered.get(a['tradeoff_id'], CHOICES))} です")
        out.append({k: str(v) if k == "note" else v for k, v in a.items() if v not in (None, "")})
    return out


def to_question(pair: Record) -> Record:
    """The pair in the contract's `tradeoff_question` shape (every number from the code; needs the base values)."""
    sides = {}
    for key in ("a", "b"):
        s = pair[key]
        if "now" not in s or "after" not in s or not s.get("record_id"):
            raise ValueError(f"{pair['tradeoff_id']}: no base value or record for {s.get('name')}")
        sides[key] = {"name": s["name"], "unit": s.get("unit") or "1", "now": s["now"], "after": s["after"], "record_id": s["record_id"]}
    return {"tradeoff_id": pair["tradeoff_id"], "variable": pair["variable"], "step_pct": pair["step_pct"], "text": pair["text"],
            **sides, "choices": list(pair.get("choices") or CHOICES)}
