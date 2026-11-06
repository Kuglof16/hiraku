"""The contract shared by every department.

`hiraku.schema.json` is the single source of truth for record shapes.
This package only loads it, validates against it and exposes the closed
lists (event kinds, domains, units) so Python code never retypes them.
Change the contract only through the lead (統括部).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema

SCHEMA_PATH = Path(__file__).with_name("hiraku.schema.json")
EXAMPLES_DIR = Path(__file__).with_name("examples")


@lru_cache(maxsize=1)
def schema() -> dict[str, Any]:
    with SCHEMA_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def kinds() -> list[str]:
    """Names usable as `kind` in validate()."""
    return sorted(schema()["$defs"])


# Fast path for the big, plain parts of a record (a field's per_member / per_node / per_element,
# a series' values): jsonschema walks every element through descend(), about 4 s for a 10 MB
# analysis. When every element of a long array or map surely fits one of the few plain element
# schemas below, the keyword reports nothing, as jsonschema would; otherwise (and for short ones)
# jsonschema's own check runs, so the errors are word for word the same.
_BASE = jsonschema.Draft202012Validator
_FAST_MIN = 16
_VEC3 = {"$ref": "#/$defs/vec3"}


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _vec3(v: Any) -> bool:
    return type(v) is list and len(v) == 3 and _num(v[0]) and _num(v[1]) and _num(v[2])


def _plain_check(sub: Any):
    """A function that says True when a value surely fits the element schema `sub`, or None when
    `sub` is not one of the plain schemas handled here."""
    if sub == {"type": "number"}:
        return _num
    if sub == {"type": "string"}:
        return lambda v: type(v) is str
    if sub == _VEC3:
        return _vec3
    if sub == {"oneOf": [{"type": "number"}, _VEC3]}:  # a number is never a vec3: exactly one fits
        return lambda v: _num(v) or _vec3(v)
    return None


def _fast_items(validator, items, instance, schema):
    if type(instance) is list and len(instance) >= _FAST_MIN and isinstance(items, dict):
        ok = _plain_check(items)
        if ok is not None and all(ok(x) for x in instance):
            return
    yield from _BASE.VALIDATORS["items"](validator, items, instance, schema)


def _fast_additional(validator, extras, instance, schema):
    if (type(instance) is dict and len(instance) >= _FAST_MIN and isinstance(extras, dict)
            and not schema.get("properties") and not schema.get("patternProperties")):
        ok = _plain_check(extras)
        if ok is not None and all(ok(x) for x in instance.values()):
            return
    yield from _BASE.VALIDATORS["additionalProperties"](validator, extras, instance, schema)


_Validator = jsonschema.validators.extend(_BASE, {"items": _fast_items, "additionalProperties": _fast_additional})


@lru_cache(maxsize=None)
def _validator(kind: str) -> jsonschema.Draft202012Validator:
    root = schema()
    if kind not in root["$defs"]:
        raise KeyError(f"unknown contract kind {kind!r}; known: {', '.join(kinds())}")
    sub = {"$schema": root["$schema"], "$defs": root["$defs"], "$ref": f"#/$defs/{kind}"}
    return _Validator(sub, format_checker=jsonschema.FormatChecker())


def errors(kind: str, obj: Any) -> list[str]:
    """Return readable validation errors (empty when obj fits the contract)."""
    out = []
    for e in sorted(_validator(kind).iter_errors(obj), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(p) for p in e.absolute_path) or "(root)"
        out.append(f"{kind}: {where}: {e.message}")
    return out


def validate(kind: str, obj: Any) -> None:
    """Raise ValueError listing every mismatch when obj does not fit `kind`."""
    errs = errors(kind, obj)
    if errs:
        raise ValueError("\n".join(errs))


def _enum(name: str) -> tuple:
    return tuple(schema()["$defs"][name]["enum"])


EVENT_KINDS: tuple[str, ...] = _enum("event_kind")
DOMAINS: tuple[str, ...] = _enum("domain")
UNITS: tuple[str, ...] = _enum("unit")
BASES: tuple[str, ...] = _enum("base")
RULE_CATEGORIES: tuple[str, ...] = _enum("rule_category")

DOMAIN_NAMES_JA = {
    "handling": "操安",
    "powertrain": "動力",
    "aero": "空力",
    "crash": "衝突",
    "strength": "強度信頼性",
    "nvh": "音振",
    "thermal": "熱",
    "assembly": "組み立て",
}

RULE_CATEGORY_NAMES_JA = {
    "sheet_metal": "板金",
    "fastener": "締結",
    "weld": "溶接",
    "tolerance": "公差",
    "human": "人",
    "battery": "電池",
    "assembly": "組み立て",
    "suspension": "サス",
    "manufacturing": "製造",
    "system": "系",
}

# Agent-facing MCP tools (served by create_sdk_mcp_server under the name "hiraku").
MCP_SERVER = "hiraku"
MCP_TOOLS_MVP = ("get_context", "list_formulas", "list_tools", "record_prediction", "analyze", "compare", "record_cause")
MCP_TOOLS_DESIGNER = ("revise_design",)  # the designer, or each designer.<module> for designs with modules
MCP_TOOLS_PACKAGER = ("build_version",)  # designs with modules: the packager builds a round's module changes into a version

MODULES: tuple[str, ...] = _enum("module_id")
MODULE_NAMES_JA = {
    "package": "レイアウト",
    "frame": "枠",
    "suspension": "足まわり",
    "steering": "操作系",
    "occupant": "乗員まわり",
    "powertrain": "動力",
    "body": "車体",
}
# The design roles do not depend on what is being made. A design type's modules each name one of them as owner.
MODULE_DESIGNERS = ("designer.structure", "designer.mechanism", "designer.electric")
DESIGNER_ROLES_JA = {"structure": "構造", "mechanism": "機構", "electric": "電気"}
# The roles of runs recorded before the three: still read from saved records, never given new work.
LEGACY_MODULE_DESIGNERS = tuple(f"designer.{m}" for m in MODULES if m != "package")
MCP_TOOLS_LATER = ("pick_domains", "ask_user", "check_package", "import_knowledge", "rule_check", "write_report", "find_tradeoffs",
                   "write_design", "edit_design", "check_design", "write_load_case", "add_tests",
                   "export_step", "import_step", "edit_step")  # the CAD tools: hiraku/tools/cad_tools.py
MCP_TOOLS_TOOLMAKER = ("make_tool", "check_tool")  # Claude writes a tool in the run and checks it (in RUN_TOOLS)


def mcp_name(tool: str) -> str:
    return f"mcp__{MCP_SERVER}__{tool}"
