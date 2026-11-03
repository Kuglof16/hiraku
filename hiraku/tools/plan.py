"""A run's plan of predictions and the readers of their inputs (data/catalog/scripted.yaml and the
builders' PLANS): which formula, which inputs, and the code that reads each input off the design,
the load case and the records. The scripted run (hiraku.agent.scripted) follows the plan; get_context's
formula_inputs (hiraku.tools.functions) shows the same inputs, worked out by the same readers, to the roles."""

from __future__ import annotations

import copy
import importlib
import re
from typing import Any

from hiraku.catalog import CATALOG_DIR, beyond_plans, external_builders, is_beyond, loading_of, read_data
from hiraku.tools.context import RunContext
from hiraku.tools.summary import design_summary

Record = dict[str, Any]


class ScriptError(RuntimeError):
    pass


def load_plan(product: str, requirements: str | None = None) -> Record:
    """The plan in data/catalog/scripted.yaml: the requirements file's own entry (its file name
    without .yaml) when there is one, else the product's (no steps when neither has one).

    A file's entry with `extends: <entry>` is that entry's plan with the file's keys over it;
    a step of the file replaces the step of the same load case and tool in place, and its
    other steps follow."""
    data = read_data(CATALOG_DIR / "scripted.yaml")
    own = data.get(requirements) if requirements else None
    if own is None:
        return data.get(product) or {"steps": []}
    base = copy.deepcopy(data.get(own["extends"]) or {"steps": []}) if own.get("extends") else {"steps": []}
    mine = {k: v for k, v in own.items() if k != "extends"}
    theirs = list(mine.get("steps") or [])
    key = {(s["load_case"], s["tool"]): s for s in theirs}
    steps = [key.pop((s["load_case"], s["tool"]), s) for s in base.get("steps") or []]
    steps += [s for s in theirs if (s["load_case"], s["tool"]) in key]
    return {**base, **mine, "steps": steps}


def has_plan(product: str, requirements: str | None = None) -> bool:
    """Whether data/catalog/scripted.yaml has steps for this requirements file (its name without
    .yaml) or product. Without them a scripted run has only the builders' default steps."""
    return bool(load_plan(product, requirements).get("steps"))


def plan_of(ctx: RunContext) -> Record:
    """This run's plan: its requirements file's entry, else its product's (load_plan)."""
    path = getattr(ctx, "requirements_path", None)
    return load_plan(ctx.project["product"], path.stem if path else None)


def _joint_wall_inputs(design: Record, load_case: Record) -> Record:
    """frame_static.joint_wall_bending's inputs off the design for this load case's loading and step
    (hiraku.solvers.k0_handcalc.frame_inputs.joint_wall_inputs, at the joint where it is largest)."""
    from hiraku.solvers.k0_handcalc.frame_inputs import joint_wall_inputs
    from hiraku.solvers.shelf.frame_static import front_supports

    params = load_case.get("params") or {}
    step = params.get("step_height")
    if step is not None and step.get("unit") != "mm":
        raise ScriptError(f"{load_case['load_case_id']}: step_height must be in mm, got {step.get('unit')!r}")
    # the supports on the step as frame_shell takes them: the load case's step_supports, else the front-most
    on = params.get("step_supports")
    on = [s.strip() for s in on.split(",")] if isinstance(on, str) else on or front_supports(design)
    return joint_wall_inputs(design, loading=loading_of(load_case), step_mm=step["value"] if step else None,
                             step_supports=on if step else None)["inputs"]


# Readers a plan may name in inputs_from that are not in hiraku.catalog.builders.
READERS = {"joint_wall_inputs": _joint_wall_inputs}


def _reader(name: str) -> Any:
    """A plan's inputs_from written by name in scripted.yaml: one of READERS, or "<module>.<function>"
    of hiraku.catalog.builders (the builders' readers of formula inputs off a design)."""
    if name in READERS:
        return READERS[name]
    module, _, func = name.rpartition(".")
    if not module or not re.fullmatch(r"[a-z0-9_]+", module):
        raise ScriptError(f"inputs_from {name!r}: write <module>.<function> of hiraku.catalog.builders")
    try:
        return getattr(importlib.import_module(f"hiraku.catalog.builders.{module}"), func)
    except (ImportError, AttributeError) as e:
        raise ScriptError(f"inputs_from {name!r} is not in hiraku.catalog.builders: {e}") from None


def compose_plan(ctx: RunContext, plan: Record | None = None) -> Record:
    """The product's plan plus a step from the builders' PLANS for each load case it lacks
    (PLANS_BEYOND first for a case past the limit), ordered upstream first. `missing` lists
    the load cases nobody gave a step for. A product step whose load case this run did not
    build (another step height, say) is left out: its causes were written for that load case.
    So is a step whose tool is not on this run's shelf; `unshelved` names those (load case, tool)."""
    out = copy.deepcopy(plan if plan is not None else plan_of(ctx))
    # each step its own copy: steps may share parts through YAML aliases
    built = [copy.deepcopy(s) for s in out["steps"] if ctx.has_load_case(s["load_case"])]
    out["steps"] = [s for s in built if s["tool"] in ctx.tools]
    out["unshelved"] = [(s["load_case"], s["tool"]) for s in built if s["tool"] not in ctx.tools]
    have = {s["load_case"] for s in out["steps"]}
    recipes, past = external_builders()[2], beyond_plans()
    out["missing"] = []
    for lc in ctx.load_cases:
        if lc["load_case_id"] in have:
            continue
        recipe = (is_beyond(lc) and past.get(lc["test_id"])) or recipes.get(lc["test_id"])
        if recipe is None:
            out["missing"].append(lc["load_case_id"])
        else:
            out["steps"].append({**recipe, "load_case": lc["load_case_id"]})
    ordered: list[Record] = []
    left = list(out["steps"])
    in_plan = {s["load_case"] for s in left}
    while left:  # a step goes once the load cases feeding it (and in the plan) have gone
        done = {s["load_case"] for s in ordered}
        ready = next((s for s in left if all(u["load_case_id"] in done or u["load_case_id"] not in in_plan
                                             for u in ctx.upstream_of(s["load_case"]))), left[0])
        ordered.append(ready)
        left.remove(ready)
    out["steps"] = ordered
    return out


_SUFFIX_UNITS = {"mm": "mm", "kg": "kg", "MPa": "MPa", "N": "N", "deg": "deg"}

_NAME_REF = re.compile(r"\{([^{}]+)\}")


def _load_case_of(ctx: RunContext, ref: str, load_case: Record) -> str:
    """A load case id, or for a test id (ana.P1.x) the run's load case of that test that feeds
    this one (a case past the limit is fed by the upstream case past the limit), else the first."""
    if ctx.has_load_case(ref):
        return ref
    of_test = [lc for lc in ctx.load_cases if lc["test_id"] == ref]
    feeding = [lc for lc in of_test if load_case["load_case_id"] in lc.get("feeds", [])]
    return (feeding or of_test)[0]["load_case_id"]


def _lookup(ctx: RunContext, ref: str, load_case: Record, given: Record | None = None) -> Any:
    """What one plan ref points at: a quantity record, or a name (e.g. load.rail_joint.support).
    `given` is what the prediction's inputs_from read off the design (from.<name>)."""
    head, _, rest = ref.partition(".")
    design = ctx.store.design()
    if head == "from":
        if given is None:
            raise ScriptError(f"{ref!r}: the prediction has no inputs_from")
        return given[rest]
    if head == "lc":
        return load_case["params"][rest]
    if head == "req":
        return next(x for x in ctx.project["requirements"] if x["key"] == rest)
    if head in ("load", "summary"):
        node: Any = design_summary(design)
        if head == "load":
            node = node["loadings"][loading_of(load_case)]
        for part in rest.split("."):
            node = node[part]
        return node
    if head == "design":
        kind, _, rest = rest.partition(".")
        if kind == "var":
            return design["variables"][rest]
        key, field = rest.split(".", 1)
        if kind == "support":
            s = next(x for x in design["supports"] if x["id"] == key)
            return {"value": s[field], "unit": _SUFFIX_UNITS[field.rsplit("_", 1)[1]]}
        if kind == "section":
            return {"value": design["sections"][key]["dims_mm"][field], "unit": "mm"}
    if head == "pred":
        return [p for p in ctx.store.records("prediction", ctx.version)
                if p["quantity"] == rest and p["load_case_id"] == load_case["load_case_id"]][-1]
    if head == "ana":
        lc_id, quantity = rest.rsplit(".", 1)
        return next(r for r in _trusted_analysis(ctx, _load_case_of(ctx, lc_id, load_case))["results"]
                    if r["quantity"] == quantity)
    raise ScriptError(f"unknown input ref {ref!r}")


def _name(ctx: RunContext, ref: str, load_case: Record, given: Record | None) -> str:
    got = _lookup(ctx, ref, load_case, given)
    if not isinstance(got, str):
        raise ScriptError(f"{{{ref}}} must point at a name, not {got!r}")
    return got


def resolve(ctx: RunContext, ref: Any, load_case: Record, given: Record | None = None) -> Record:
    """Turn one input ref of the plan into a quantity {value, unit}.

    `{ref}` inside a ref is replaced by the name it points at first, e.g.
    ana.P1-step100.wheel_load_{load.rail_joint.support}.
    """
    if isinstance(ref, dict):
        return {"value": float(ref["value"]), "unit": ref["unit"]}
    try:
        text = _NAME_REF.sub(lambda m: _name(ctx, m.group(1), load_case, given), str(ref))
        q = _lookup(ctx, text, load_case, given)
        return {"value": float(q["value"]), "unit": q["unit"]}
    except (KeyError, StopIteration, IndexError, ValueError, TypeError) as e:
        raise ScriptError(f"cannot resolve input ref {ref!r}: {type(e).__name__} {e}") from None


def _trusted_analysis(ctx: RunContext, load_case_id: str) -> Record | None:
    from hiraku.tools.functions import trusted_analysis  # functions reads this module too
    return trusted_analysis(ctx, load_case_id)


class ReaderError(ScriptError):
    """A prediction's inputs_from does not apply to this design; `reason` is the reader's own."""

    def __init__(self, reader: str, formula_id: str, reason: str):
        super().__init__(f"inputs_from of {formula_id} cannot read this design: {reason}")
        self.reader, self.formula_id, self.reason = reader, formula_id, reason


def reader_name(src: Any) -> str:
    """How a prediction's inputs_from is named: as written in scripted.yaml, else <module>.<function>
    of the builder's function."""
    if isinstance(src, str):
        return src
    return f"{getattr(src, '__module__', '').rsplit('.', 1)[-1]}.{getattr(src, '__name__', repr(src))}"


def given_inputs(ctx: RunContext, spec: Record, load_case: Record) -> Record | None:
    """What a prediction's inputs_from reads off the current version's design for this load case
    (None when the prediction has none). Raises ReaderError when the reader does not apply."""
    src = spec.get("inputs_from")
    if not src:
        return None
    reader = _reader(src) if isinstance(src, str) else src
    try:
        return reader(ctx.store.design(), load_case)
    except (KeyError, ValueError, TypeError) as e:
        raise ReaderError(reader_name(src), spec["formula_id"], str(e) or type(e).__name__) from None


# ---------------------------------------------------------------- formula_inputs (get_context)


def _where(ctx: RunContext, ref: Any, load_case: Record, given: Record | None, reader: str | None) -> str:
    """Where one input ref of the plan takes its value from, in Japanese for the roles."""
    if isinstance(ref, dict):
        return "計画 (data/catalog/scripted.yaml) に書いた値"
    text = _NAME_REF.sub(lambda m: _name(ctx, m.group(1), load_case, given), str(ref))
    head, _, rest = text.partition(".")
    if head == "from":
        return f"{reader} がコードで設計と荷重条件から計算した {rest}"
    if head == "lc":
        return f"荷重条件 {load_case['load_case_id']} の params.{rest}"
    if head == "req":
        return f"要件 {rest}"
    if head == "load":
        return f"design_summary の loadings.{loading_of(load_case)}.{rest} (この荷重条件で載せる組)"
    if head == "summary":
        return f"design_summary の {rest}"
    if head == "design":
        kind, _, rest = rest.partition(".")
        if kind == "var":
            return f"設計の変数 {rest} (design.variables)"
        return f"設計の {kind} {rest}"
    if head == "pred":
        p = _lookup(ctx, text, load_case, given)
        return f"この版のこの荷重条件の予測 {p['prediction_id']} ({rest}、{p['formula_id']})"
    if head == "ana":
        lc_id, quantity = rest.rsplit(".", 1)
        up = _load_case_of(ctx, lc_id, load_case)
        a = _trusted_analysis(ctx, up)
        return f"{up} の照合で一致した解析 {a['analysis_id'] if a else '?'} の結果 {quantity}"
    return text


def _missing(ctx: RunContext, ref: Any, load_case: Record, given: Record | None) -> str:
    """Why an input ref has no value yet (a record it reads is not there), in Japanese."""
    try:
        text = _NAME_REF.sub(lambda m: _name(ctx, m.group(1), load_case, given), str(ref))
    except (ScriptError, KeyError, StopIteration, IndexError, ValueError, TypeError):
        text = str(ref)
    head, _, rest = text.partition(".")
    if head == "ana":
        up = rest.rsplit(".", 1)[0]
        try:
            up = _load_case_of(ctx, up, load_case)
        except IndexError:
            pass
        return (f"まだ値がありません: 上流の {up} の解析の結果 {rest.rsplit('.', 1)[-1]} を使います。先に {up} を"
                f"予測 → analyze → compare で一致させてから、get_context をもう一度呼んでください")
    if head == "pred":
        return (f"まだ値がありません: この荷重条件の {rest} の予測を使います。先にその予測を record_prediction "
                f"してから、get_context をもう一度呼んでください")
    return f"設計か荷重条件に値がありません ({text})"


def _known_specs(ctx: RunContext) -> dict[str, Record]:
    """formula_id -> a prediction of some plan that reads that formula's inputs by code (inputs_from):
    the run's own plan (also its steps for load cases this run did not build), the builders' PLANS and
    PLANS_BEYOND. Lets a tool's pairing on another load case (the joint wall on R1, say) be filled."""
    steps = list(plan_of(ctx).get("steps") or []) + list(external_builders()[2].values()) + list(beyond_plans().values())
    out: dict[str, Record] = {}
    for step in steps:
        for spec, _ in _specs(step):
            if spec.get("inputs_from"):
                out.setdefault(spec["formula_id"], spec)
    return out


def _entry(ctx: RunContext, lc: Record, tool: str, spec: Record, when: str | None) -> Record:
    crit = (lc.get("criterion") or {}).get("quantity")
    out: Record = {"tool": tool, "formula_id": spec["formula_id"], "quantity": spec["quantity"],
                   "role": "criterion" if spec["quantity"] == crit else "intermediate"}
    if spec.get("output"):
        out["output"] = spec["output"]
    if when:
        out["when"] = when
    reader = reader_name(spec["inputs_from"]) if spec.get("inputs_from") else None
    if reader:
        out["reader"] = reader
    try:
        given = given_inputs(ctx, spec, lc)
    except ReaderError as e:
        out["refused"] = (f"{e.reader} はこの設計に当てはまらないので、{e.formula_id} の入力を出せません: {e.reason}。"
                          f"この式は使えません。list_formulas(tool=\"{tool}\") でこの設計に合う式を選んでください")
        return out
    except ScriptError as e:
        out["refused"] = f"入力を読むコードが見つかりません: {e}"
        return out
    inputs, missing = {}, {}
    for name, ref in spec["inputs"].items():
        try:
            q = resolve(ctx, ref, lc, given)
            inputs[name] = {**q, "from": _where(ctx, ref, lc, given, reader)}
        except (ScriptError, KeyError, StopIteration, IndexError, ValueError, TypeError):
            missing[name] = _missing(ctx, ref, lc, given)
    out["inputs"] = inputs
    if missing:
        out["missing"] = missing
    if spec.get("assumptions"):
        out["assumptions"] = list(spec["assumptions"])
    return out


def _specs(step: Record, when: str | None = None) -> list[tuple[Record, str | None]]:
    """A step's predictions, then those of its retries (after the expected mismatch's cause)."""
    out = [(p, when) for p in step.get("predictions") or []]
    exp = step.get("expected_mismatch") or {}
    if exp.get("retry"):
        out += _specs(exp["retry"], f"{exp.get('quantity')} が食い違って原因を書いたあとの予測")
    return out


def formula_inputs(ctx: RunContext, load_case_id: str | None = None, pairs: Any = None) -> list[Record]:
    """Per load case (one, or all of the run): for each formula the run's plan uses on it (the scripted
    run's plan: data/catalog/scripted.yaml and the builders' PLANS), its inputs with values and units
    worked out by the same code the scripted run calls, and where each value comes from. `pairs(tool)`
    gives the tool's own pairing of results with formulas (functions.tool_predicts); a pairing no plan
    has inputs for is listed with no_reader."""
    cases = [ctx.load_case(load_case_id)] if load_case_id else list(ctx.load_cases)
    plan = compose_plan(ctx)
    out = []
    for lc in cases:
        steps = [s for s in plan["steps"] if s["load_case"] == lc["load_case_id"]]
        entries: list[Record] = []
        for step in steps:
            for spec, when in _specs(step):
                entries.append(_entry(ctx, lc, step["tool"], spec, when))
        seen = {e["formula_id"] for e in entries}
        known = None
        crit = (lc.get("criterion") or {}).get("quantity")
        for tool in [s["tool"] for s in steps]:
            for pair in (pairs(tool) if pairs else []):
                if pair.get("formula_id") in seen:
                    continue
                seen.add(pair["formula_id"])
                known = _known_specs(ctx) if known is None else known
                if pair["formula_id"] in known:  # another plan reads this formula's inputs by code
                    spec = {**known[pair["formula_id"]], "quantity": pair["quantity"]}
                    if pair.get("output"):
                        spec["output"] = pair["output"]
                    entries.append(_entry(ctx, lc, tool, spec, None))
                    continue
                role = "criterion" if pair.get("quantity") == crit else "intermediate"
                entries.append({"tool": tool, **pair, "role": role, "no_reader": (
                    "この式の入力をコードで読む手順はありません。list_formulas で入力の名前と単位を見て、"
                    "get_context の design_summary と荷重条件から自分で入れ、どこから取ったかを assumptions に書いてください")})
        item: Record = {"load_case_id": lc["load_case_id"], "test_id": lc["test_id"], "domain": lc["domain"],
                        "criterion": lc.get("criterion"), "formulas": entries}
        if not steps:
            item["note"] = ("この荷重条件には、式の入力をコードで読む手順がありません。list_tools と list_formulas で"
                            "道具と式を選び、入力の出どころを assumptions に書いてください")
        out.append(item)
    return out
