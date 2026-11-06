"""write_load_case: a load case Claude writes for a test the catalog has no builder for (docs/contract.md
"Claude が書く設計 (2026-11-06)", hiraku.contract tool_input.write_load_case).

Claude gives the conditions (params), the judged quantity, the comparison and how the limit is made
(criterion.limit_from: an expression over requirement keys and paths in the design). The code computes the
limit from that expression (limit_value), sets tolerance_rel (the catalog's for the test, else
WRITTEN_TOLERANCE_REL) and names the load case. The model never writes the limit's number.

Where a written load case is kept: the contract's load_case has no place for limit_from, so the tool's input
is kept in the run's events, as an agent.said of the caller whose data.written_load_case holds the input and
the load case (WRITTEN_KEY). The load case itself goes where the catalog's go: project.load_cases for version
1 (project.updated), v<NNN>/load_cases.json from version 2 (load_cases.created). When a new version's load
cases are built again (functions.rebuild_load_cases), the written ones are made again from that input on the
new design (rebuilt_written): their limit follows the new version's materials and sections.
"""

from __future__ import annotations

import ast
import keyword
import math
import re
from typing import Any

from hiraku import contract
from hiraku.tools.context import RunContext, ToolError

Record = dict[str, Any]

WRITTEN_KEY = "written_load_case"  # agent.said data key that keeps a written load case's input

# tolerance_rel of a written load case whose test has none in the catalog (data/catalog/tests.yaml gives it
# for P1, R1 and R1s only). HIRAKU's choice, from no standard: the 15 % that most of the catalog's builders
# allow between a hand calculation and a tool (R1, R1s, C1, N1, the thermal tests: hiraku/catalog/builders).
WRITTEN_TOLERANCE_REL = 0.15

# ---------------------------------------------------------------------------- the limit's expression

MAX_EXPR = 300
REQ_KEY = re.compile(r"^Q[0-9]{2}$")
# field-name endings that say the unit (materials.steel.yield_MPa, sections.sq20.dims_mm.t, parts.x.mass_kg)
SUFFIX_UNITS = (
    ("_kg_m3", "kg/m^3"), ("_kg_m2", "kg*m^2"), ("_mm2", "mm^2"), ("_mm3", "mm^3"), ("_mm4", "mm^4"),
    ("_MPa", "MPa"), ("_Pa", "Pa"), ("_kN", "kN"), ("_Nm", "N*m"), ("_N_mm", "N/mm"), ("_N", "N"),
    ("_mm", "mm"), ("_km", "km"), ("_m", "m"), ("_kg", "kg"), ("_deg", "deg"), ("_rad", "rad"),
    ("_kmh", "km/h"), ("_mps", "m/s"), ("_s", "s"), ("_Hz", "Hz"), ("_rpm", "rpm"), ("_kW", "kW"), ("_W", "W"),
    ("_kWh", "kWh"), ("_Wh", "Wh"), ("_Ah", "Ah"), ("_V", "V"), ("_A", "A"), ("_degC", "degC"), ("_K", "K"),
)
DIMENSIONLESS_FIELDS = ("nu",)  # Poisson's ratio: a number by definition


class LimitError(ValueError):
    """The limit cannot be computed. The message says why and what to write instead."""


def _units() -> Any:
    from hiraku.solvers.k0_handcalc import units  # the contract's unit table (factor to SI, dimension)

    return units


def _unit_row(unit: str, name: str) -> tuple[float, float, tuple[int, ...]]:
    row = _units().UNIT_TABLE.get(unit)
    if row is None:
        raise LimitError(f"{name} の単位 {unit!r} は換算できません (使える単位: {', '.join(_units().UNIT_TABLE)})")
    return row


def _unit_of_field(keys: list[str]) -> str | None:
    """The unit a path's field names say: the last key's ending (yield_MPa), else an enclosing key's (dims_mm.t)."""
    for k in reversed(keys):
        if k in DIMENSIONLESS_FIELDS:
            return "1"
        for end, unit in SUFFIX_UNITS:
            if k.endswith(end) and len(k) > len(end):
                return unit
    return None


def _requirement_list(requirements: Any) -> list[Record]:
    if isinstance(requirements, dict):
        return list(requirements.get("requirements") or [])
    return list(requirements or [])


def _from_requirement(requirements: list[Record], key: str, name: str) -> Record:
    for r in requirements:
        if r.get("key") == key:
            v = r.get("value")
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise LimitError(f"{name}: 要件 {key} の値 {v!r} は数ではないので式に使えません")
            if not r.get("unit"):
                raise LimitError(f"{name}: 要件 {key} に単位がありません")
            return {"value": float(v), "unit": r["unit"], "ref": key}
    raise LimitError(f"{name}: 要件 {key} がこの実行にありません (get_context の requirements にある key を使う)")


def _step(node: Any, key: str) -> Any:
    if isinstance(node, dict):
        if key in node:
            return node[key]
        raise KeyError(key)
    if isinstance(node, list):
        if key.isdigit() and int(key) < len(node):
            return node[int(key)]
        for item in node:  # a list of records with ids (parts, members, supports): the one with this id
            if isinstance(item, dict) and item.get("id") == key:
                return item
    raise KeyError(key)


def _from_design(design: Record, path: str, name: str) -> Record:
    keys = [k for k in path.split(".")]
    if not path or any(not k for k in keys):
        raise LimitError(f"{name}: 道 {path!r} が読めません (例 materials.steel.yield_MPa)")
    node: Any = design
    for i, k in enumerate(keys):
        try:
            node = _step(node, k)
        except KeyError:
            where = ".".join(keys[:i]) or "設計"
            have = list(node)[:12] if isinstance(node, dict) else [x.get("id") for x in node if isinstance(x, dict)][:12] \
                if isinstance(node, list) else []
            raise LimitError(f"{name}: 設計に道 {path!r} がありません ({where} に {k!r} が無い"
                             + (f"。あるのは {', '.join(map(str, have))}" if have else "") + ")") from None
    if isinstance(node, dict) and "value" in node and "unit" in node:
        value, unit = node["value"], node["unit"]
    else:
        value, unit = node, _unit_of_field(keys)
        if unit is None:
            raise LimitError(f"{name}: 道 {path!r} の単位が読めません (欄の名前の末尾 _MPa・_mm・_kg などか、"
                             "{value, unit} の形の値を指す)")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LimitError(f"{name}: 道 {path!r} の値 {value!r} は数ではありません")
    return {"value": float(value), "unit": str(unit), "ref": path}


KEYWORD_ALIAS = "kw__"  # a name that is a Python keyword (yield, lambda ...) is parsed under this prefix
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _parse(expr: Any) -> ast.Expression:
    """The expression's tree. A name that is a Python keyword (yield in "yield / Q61") is read as a name
    (KEYWORD_ALIAS + name), so it needs a ref like any other: and, or, lambda ... never act as Python."""
    if not isinstance(expr, str) or not expr.strip():
        raise LimitError("limit_from.expr が空です")
    if len(expr) > MAX_EXPR:
        raise LimitError(f"limit_from.expr が長すぎます ({len(expr)} 字、{MAX_EXPR} 字まで)")
    text = _IDENT.sub(lambda m: KEYWORD_ALIAS + m.group(0) if keyword.iskeyword(m.group(0)) else m.group(0), expr.strip())
    try:
        return ast.parse(text, mode="eval")
    except SyntaxError:
        raise LimitError(f"limit_from.expr {expr!r} が式として読めません (四則・括弧・min・max と名前と数だけ)") from None


def _name(node_id: str) -> str:
    """The name as written (without KEYWORD_ALIAS)."""
    return node_id.removeprefix(KEYWORD_ALIAS)


def _names(tree: ast.AST) -> list[str]:
    """The tree's names (as parsed, with KEYWORD_ALIAS) but the called functions."""
    calls = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    return list(dict.fromkeys(n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and id(n) not in calls))


def _same_dim(a: tuple, b: tuple, what: str) -> None:
    if a[1] != b[1]:
        u = _units()
        raise LimitError(f"{what} の両側の次元が合いません ({u.describe_dimension(a[1])} と {u.describe_dimension(b[1])})")


def _eval(node: ast.AST, env: dict[str, tuple[float, tuple[int, ...]]]) -> tuple[float, tuple[int, ...]]:
    """(value in SI, dimension) of a node: + - * / with the dimensions checked, a constant power, min, max."""
    if isinstance(node, ast.Expression):
        return _eval(node.body, env)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value), (0,) * 6
    if isinstance(node, ast.Name):
        return env[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v, d = _eval(node.operand, env)
        return (-v if isinstance(node.op, ast.USub) else v), d
    if isinstance(node, ast.BinOp):
        a, b = _eval(node.left, env), _eval(node.right, env)
        if isinstance(node.op, (ast.Add, ast.Sub)):
            _same_dim(a, b, "足し算・引き算")
            return (a[0] + b[0] if isinstance(node.op, ast.Add) else a[0] - b[0]), a[1]
        if isinstance(node.op, ast.Mult):
            return a[0] * b[0], tuple(x + y for x, y in zip(a[1], b[1]))
        if isinstance(node.op, ast.Div):
            if b[0] == 0:
                raise LimitError("式の中で 0 で割っています")
            return a[0] / b[0], tuple(x - y for x, y in zip(a[1], b[1]))
        if isinstance(node.op, ast.Pow):
            if not (isinstance(node.right, ast.Constant) and isinstance(node.right.value, (int, float))):
                raise LimitError("べき (**) の指数は数で書きます")
            p = float(node.right.value)
            dims = tuple(x * p for x in a[1])
            if any(abs(x - round(x)) > 1e-9 for x in dims) or abs(p) > 4:
                raise LimitError("べき (**) は、次元が整数になる小さな指数 (4 まで) だけ使えます")
            try:
                return a[0] ** p, tuple(int(round(x)) for x in dims)
            except (OverflowError, ValueError, ZeroDivisionError):
                raise LimitError("べき (**) が計算できません") from None
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("min", "max")
            and not node.keywords and len(node.args) >= 2):
        vals = [_eval(x, env) for x in node.args]
        for x in vals[1:]:
            _same_dim(vals[0], x, node.func.id)
        pick = min if node.func.id == "min" else max
        return pick(v for v, _ in vals), vals[0][1]
    raise LimitError("limit_from.expr に使えるのは、数・名前・+ - * /・括弧・小さな指数の **・min(...)・max(...) だけです")


def _limit_value(limit_from: Record, requirements: Any, design: Record) -> Record:
    """The limit a written load case's criterion.limit_from gives, computed by code.

    limit_from: {"expr": "yield / Q61", "unit": "MPa", "refs": {"yield": "materials.steel.yield_MPa", "Q61": "Q61"}}.
    A ref that is a requirement key (Q01..Q99) reads project.requirements (value, unit); any other is a path in
    the design (materials.<key>.<field>, sections.<key>.dims_mm.<field>, variables.<name>, parts.<id>.mass_kg ...),
    whose unit is the value's own ({"value", "unit"}) or its field name's ending. A name of expr without a ref
    is read as a requirement when it is a requirement key. The result is converted to limit_from.unit (without
    one: the unit of the first ref of the same dimension, or "1" for a pure number).
    Returns {"value", "unit", "from": {name: {"value", "unit", "ref"}}}; LimitError says why it cannot."""
    if not isinstance(limit_from, dict):
        raise LimitError("limit_from は {expr, unit, refs} の形で書きます")
    tree = _parse(limit_from.get("expr"))
    refs = dict(limit_from.get("refs") or {})
    reqs = _requirement_list(requirements)
    read: dict[str, Record] = {}
    env = {}
    for parsed in _names(tree):
        name = _name(parsed)
        ref = refs.get(name, name if REQ_KEY.match(name) else None)
        if not isinstance(ref, str) or not ref.strip():
            raise LimitError(f"式の名前 {name!r} に refs がありません。refs に {name}: 要件の key (Q61 など) か設計の道 "
                             "(materials.<key>.yield_MPa など) を書いてください")
        ref = ref.strip()
        q = _from_requirement(reqs, ref, name) if REQ_KEY.match(ref) else _from_design(design, ref, name)
        read[name] = q
        f, o, d = _unit_row(q["unit"], name)
        env[parsed] = (q["value"] * f + o, d)
    si, dim = _eval(tree, env)
    unit = limit_from.get("unit")
    if not unit:
        unit = next((q["unit"] for q in read.values() if _unit_row(q["unit"], "")[2] == dim), None)
        if unit is None:
            if any(dim):
                raise LimitError(f"式の答えの次元は {_units().describe_dimension(dim)} です。limit_from.unit に単位を書いてください")
            unit = "1"
    f, o, d = _unit_row(unit, "limit_from.unit")
    if d != dim:
        u = _units()
        raise LimitError(f"式の答えの次元 ({u.describe_dimension(dim)}) は limit_from.unit {unit!r} "
                         f"({u.describe_dimension(d)}) に換算できません")
    value = (si - o) / f
    if not math.isfinite(value):
        raise LimitError("式の答えが有限の数になりません")
    return {"value": value, "unit": unit, "from": read}


try:  # the analysis department's (hiraku/catalog/limits.py) when it is there; the same shape
    from hiraku.catalog.limits import limit_value  # type: ignore[import-not-found]
except ImportError:
    limit_value = _limit_value


def _short(x: float) -> str:
    return f"{x:.4g}"


def limit_basis(limit_from: Record, limit: Record, design: Record) -> str:
    """criterion.basis: the expression with each name followed by the value it read, e.g.
    "yield 245 MPa / Q61 1.5", and the materials whose values are not checked."""
    got = limit.get("from") or {}

    def one(m: re.Match) -> str:
        q = got.get(m.group(0))
        if q is None:
            return m.group(0)
        return f"{m.group(0)} {_short(q['value'])}" + ("" if q["unit"] == "1" else f" {q['unit']}")

    text = re.sub(r"[A-Za-z_][A-Za-z0-9_]*", one, str(limit_from.get("expr", "")).strip())
    unchecked = []
    for q in got.values():
        parts = str(q["ref"]).split(".")
        if parts[0] == "materials" and len(parts) > 1:
            mat = (design.get("materials") or {}).get(parts[1]) or {}
            if mat.get("verified") is False and parts[1] not in unchecked:
                unchecked.append(parts[1])
    if unchecked:
        text += f" (materials {', '.join(unchecked)}: not checked)"
    return text


# ---------------------------------------------------------------------------- the tool


def _has_builder(catalog: Any, test_id: str, product: str | None) -> bool:
    """The intake department's has_builder (a product's own builder counts), else the catalog's builder_for."""
    try:
        from hiraku.intake.domains import has_builder
    except ImportError:
        has_builder = None
    if has_builder is not None:
        return bool(has_builder(catalog, test_id, product))
    from hiraku.catalog.build import builder_for

    try:
        return builder_for(catalog, test_id) is not None
    except KeyError:
        return False


def owner_role(domain: str) -> str:
    """The role that works on a load case of this domain (hiraku.catalog.owner_agent for a picked domain)."""
    return "packager" if domain == "assembly" else f"cae.{domain}"


def _id_part(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.'-]", "", text)


def load_case_id(test_id: str, params: Record, criterion: Record, taken: set[str]) -> str:
    """<test_id>-<short name>: the first quantity of params, e.g. drop_height 1 m -> R2-drop1m, g_factor 3 -> R2-g3
    (a unit "1" is left out); without one, the judged quantity's first word. -2, -3 ... when the id is taken."""
    short = ""
    for name, v in params.items():
        if isinstance(v, dict) and isinstance(v.get("value"), (int, float)) and not isinstance(v.get("value"), bool):
            unit = "" if v.get("unit") in (None, "1") else str(v["unit"]).replace("/", "p").replace("^", "")
            short = _id_part(name.split("_")[0][:8] + f"{v['value']:g}" + unit)
            break
    short = short or _id_part(str(criterion.get("quantity", "")).split("_")[0][:12]) or "case"
    base = f"{test_id}-{short}"
    out, n = base, 2
    while out in taken:
        out, n = f"{base}-{n}", n + 1
    return out


def _make(ctx: RunContext, args: Record, design: Record, lc_id: str, tolerance: float) -> Record:
    """The load case of a write_load_case input on a design (its limit computed from limit_from)."""
    from hiraku.tools.functions import requirements_now

    crit_in = args["criterion"]
    try:
        limit = limit_value(crit_in["limit_from"], requirements_now(ctx), design)
    except LimitError as e:
        raise ToolError(f"限度を計算できません: {e}") from None
    except ValueError as e:  # the analysis department's limit_value may raise its own ValueError
        raise ToolError(f"限度を計算できません: {e}") from None
    q_refs = [q["ref"] for q in (limit.get("from") or {}).values() if REQ_KEY.match(str(q["ref"]))]
    keys = list(dict.fromkeys([*(args.get("requirement_keys") or []), *q_refs]))
    have = {r.get("key") for r in requirements_now(ctx).get("requirements") or []}
    missing = [k for k in keys if k not in have]
    if missing:
        raise ToolError(f"requirement_keys の {', '.join(missing)} はこの実行の要件にありません (get_context の requirements)")
    criterion: Record = {"quantity": crit_in["quantity"], "op": crit_in["op"],
                         "limit": {"value": float(limit["value"]), "unit": limit["unit"]},
                         "basis": limit_basis(crit_in["limit_from"], limit, design)}
    if q_refs:
        criterion["requirement_keys"] = list(dict.fromkeys(q_refs))
    lc: Record = {"load_case_id": lc_id, "test_id": args["test_id"], "domain": args["domain"],
                  "title": args["title"].strip(), "params": dict(args["params"])}
    if keys:
        lc["requirement_keys"] = keys
    lc["criterion"] = criterion
    lc["tolerance_rel"] = float(tolerance)
    if design.get("modules"):  # as the catalog's builders set it on a design with modules
        from hiraku.catalog.build import _builders_modules

        lc["modules"] = _builders_modules().modules_for(lc)
    errs = contract.errors("load_case", lc)
    if errs:
        raise ToolError("この荷重条件は契約の形 (load_case) に合いません: " + "; ".join(errs)[:1500]
                        + "。params は {value, unit} の量か、文字か、真偽で書きます")
    return lc


def _tolerance(catalog: Any, test_id: str) -> float:
    return float(catalog.test(test_id).get("tolerance_rel", WRITTEN_TOLERANCE_REL))


def _save(ctx: RunContext, load_cases: list[Record], agent: str, text: str) -> None:
    """The version's load cases as the catalog's are kept: project.load_cases for version 1 (project.updated),
    v<NNN>/load_cases.json from version 2 (load_cases.created)."""
    v = ctx.version
    if v >= 2:
        ctx.store.write_version_load_cases(v, load_cases, agent=agent, text=text)
    else:
        ctx.store.update_project({"load_cases": load_cases}, agent=agent, text=text)


def _add_role(ctx: RunContext, catalog: Any, role: str, domain: str, agent: str) -> None:
    """project.agents gets the load case's role when it has none yet (a domain whose only tests had no builder)."""
    agents = list(ctx.project.get("agents") or [])
    if any(a["agent_id"] == role for a in agents):
        return
    entry = ({"agent_id": "packager", "role": "packager", "prop": catalog.prop("packager")} if role == "packager"
             else {"agent_id": role, "role": "cae", "domain": domain, "prop": catalog.prop(role)})
    ctx.store.update_project({"agents": agents + [entry]}, agent=agent, text=f"{role} がこの実行の役に入りました")


def write_load_case(ctx: RunContext, args: dict[str, Any], agent: str | None = None) -> Record:
    """A load case Claude writes for a test the catalog has no builder for (tool_input.write_load_case). Called
    by the test's domain role (cae.<domain>, the packager for assembly) or the lead. The tool names it, computes
    the limit (limit_value), sets tolerance_rel, adds it to the version's load cases, keeps the input for the
    next versions (WRITTEN_KEY) and updates the domain's tile. Returns {load_case_id, criterion, tolerance_rel,
    owner, stage}."""
    from hiraku.catalog import load_catalog
    from hiraku.tools.functions import _check_input
    from hiraku.tools.judge import update_judgement
    from hiraku.tools.shelf import load_case_stage

    _check_input("write_load_case", args)
    domain, test_id = args["domain"], args["test_id"]
    role = owner_role(domain)
    agent = agent or "lead"
    if agent not in ("lead", role):
        raise ToolError(f"{test_id} ({domain}) の荷重条件を書けるのは、その分野の役 ({role}) かまとめ役 (lead) です。"
                        f"{agent} は書きません")
    if not ctx.version:
        raise ToolError("まだ設計の版がありません。限度は設計 (材料・断面) から計算するので、版 1 ができてから書いてください")
    catalog = load_catalog()
    try:
        spec = catalog.test(test_id)
    except KeyError:
        raise ToolError(f"試験 {test_id} はカタログにありません (pick_domains の試験の ID を使う)") from None
    if spec["domain"] != domain:
        raise ToolError(f"試験 {test_id} の分野は {spec['domain']} です (domain に {domain} と書かれています)")
    if _has_builder(catalog, test_id, ctx.project.get("product")):
        raise ToolError(f"試験 {test_id} にはカタログの荷重条件の作り方 (builder) があるので、カタログの荷重条件を使います "
                        "(get_context の load_cases)。write_load_case は builder の無い試験のためのものです")
    if domain not in ctx.picked:
        raise ToolError(f"分野 {domain} はこの実行で選ばれていません (選んだ分野: {', '.join(ctx.picked) or 'なし'})")
    if not args["title"].strip() or not args["basis"].strip():
        raise ToolError("title と basis (この荷重条件が使い方を代表する理由: 出典か、前提と言える前提) を書いてください")
    design = ctx.store.design()
    before = list(ctx.load_cases)
    lc_id = load_case_id(test_id, args["params"], args["criterion"], {lc["load_case_id"] for lc in before})
    lc = _make(ctx, args, design, lc_id, _tolerance(catalog, test_id))
    v = ctx.version
    crit = lc["criterion"]
    limit = f"{crit['quantity']} {crit['op']} {_short(crit['limit']['value'])} {crit['limit']['unit']}"
    _save(ctx, before + [lc], agent, f"版 {v} に、{test_id} の荷重条件 {lc_id} を書きました ({limit}、{crit['basis']})")
    _add_role(ctx, catalog, role, domain, agent)
    ctx.store.emit("agent.said", agent=agent, domain=domain, design_version=v,
                   text=f"{test_id} の荷重条件 {lc_id}「{lc['title']}」を書きました。限度は {limit} ({crit['basis']})。"
                        f"根拠: {args['basis'].strip()}",
                   data={WRITTEN_KEY: {"load_case_id": lc_id, "design_version": v, "input": args, "load_case": lc}})
    update_judgement(ctx, domain, v)
    return {"load_case_id": lc_id, "criterion": crit, "tolerance_rel": lc["tolerance_rel"],
            "owner": ctx.owner(lc_id), "stage": load_case_stage(ctx, lc)}


def written_inputs(ctx: RunContext) -> dict[str, Record]:
    """load case id -> the write_load_case input it was written from (the run's agent.said WRITTEN_KEY events)."""
    out: dict[str, Record] = {}
    for e in ctx.store.events():
        if e["kind"] == "agent.said":
            w = (e.get("data") or {}).get(WRITTEN_KEY)
            if isinstance(w, dict) and isinstance(w.get("input"), dict):
                out[w["load_case_id"]] = w["input"]
    return out


def rebuilt_written(ctx: RunContext, design: Record, load_cases: list[Record],
                    written: dict[str, Record]) -> tuple[list[Record], list[str]]:
    """A new version's load cases with each written one made again from its input on the new design (the limit
    follows its materials and sections). One that cannot be made on it keeps the earlier version's; returns
    (load cases, the ids kept so)."""
    from hiraku.catalog import load_catalog

    catalog = load_catalog()
    out, kept = [], []
    for lc in load_cases:
        args = written.get(lc["load_case_id"])
        if args is None:
            out.append(lc)
            continue
        try:
            out.append(_make(ctx, args, design, lc["load_case_id"], _tolerance(catalog, lc["test_id"])))
        except (ToolError, KeyError):
            out.append(lc)
            kept.append(lc["load_case_id"])
    return out, kept


# The intake department's pick_domains takes the tests no builder makes when a writer is registered
# (hiraku.intake register_load_case_writer, can_write_load_cases); nothing to register where it is not yet.
try:
    from hiraku import intake as _intake
except ImportError:  # pragma: no cover
    _intake = None
if _intake is not None and hasattr(_intake, "register_load_case_writer"):
    _intake.register_load_case_writer(write_load_case)
