"""Designs built from their variables, for revise_design.

    from hiraku import designgen
    designgen.variables(v1)         # [{name, unit, value, default, min, max, revisable, meaning}]
    v2 = designgen.regenerate(v1, {"track": {"value": 1220, "unit": "mm"}})
    # v2 is the whole design rebuilt; its version fields are v1's (the caller sets them)

regenerate() raises DesignGenError (a ValueError) saying why for a variable that
cannot be changed, a value out of its range, or a layout that does not close;
interference between parts is left to the K4 tools. generator(product).build()
with no values builds the product's first version (data/designs/offroad-4w-v1.json).
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

from hiraku.designgen import ladder_4w
from hiraku.designgen.ladder_4w import DesignGenError

Record = dict[str, Any]
GENERATORS: dict[str, ModuleType] = {"offroad-4w": ladder_4w}  # by product
ARCHETYPES: dict[str, ModuleType] = {"ladder_4w": ladder_4w}  # by kind of design
# requirement keys that set variables of the design (key -> (variable, unit))
FROM_REQUIREMENTS = {"Q33": ("occupant_mass", "kg"), "Q40": ("cargo_mass", "kg"), "Q50": ("max_speed", "km/h")}


def generator(product: str) -> ModuleType:
    try:
        return GENERATORS[product]
    except KeyError:
        raise DesignGenError(f"no design generator for product {product!r}; known: {', '.join(GENERATORS)}") from None


def generator_of(design: Record) -> ModuleType:
    """The generator that made a design: the one whose signature variables the design carries."""
    have = set(design.get("variables", {}))
    for gen in GENERATORS.values():
        if set(gen.SIGNATURE) <= have:
            return gen
    raise DesignGenError("this design was not built from variables here: its variables match no design generator")


def variables(design: Record) -> list[Record]:
    """Every variable of the design's generator with its current value in the design (the
    default for one the design predates): name, unit, value, default, min and max (revisable
    ones), revisable, meaning."""
    gen = generator_of(design)
    now = design["variables"]
    out = []
    for v in gen.spec():
        if v["name"] in now:
            out.append(dict(v, value=now[v["name"]]["value"]))
        elif not v.get("derived"):
            out.append(dict(v, value=v["default"]))
    return out


def regenerate(design: Record, changes: dict[str, Record]) -> Record:
    """The design rebuilt with some of its variables changed ({name: {value, unit}}), the
    others as they are. A choice of the generator may be given too ({"joints": "gusset"}):
    it stands for the variables it sets. design_version, parent_version, name and
    change_reason are copied from `design`; the caller sets them for the new version."""
    gen = generator_of(design)
    choices = {k: v for k, v in changes.items() if k in getattr(gen, "CHOICES", {})}
    changes = {**gen.choice_values(choices), **{k: v for k, v in changes.items() if k not in choices}} if choices else changes
    for name in changes:
        var = gen.VARIABLES.get(name)
        if var is None:
            raise DesignGenError(f"unknown variable {name!r}; the variables are listed by designgen.variables(design)")
        if not var.revisable:
            raise DesignGenError(f"{name} cannot be changed by revising the design: it is a requirement or a fixed "
                                 f"value ({var.meaning})")
    return gen.build({**gen.values_of(design), **changes}, version=design["design_version"],
                     parent=design.get("parent_version"), reason=design.get("change_reason"), name=design.get("name"))


def archetypes(flags: dict) -> list[Record]:
    """The kinds of design that suit a product's flags: name, title, choices {name: [options]}, variables (as spec())."""
    return [{"name": name, "title": gen.TITLE, "choices": {k: list(v) for k, v in gen.CHOICES.items()}, "variables": gen.spec()}
            for name, gen in ARCHETYPES.items() if gen.applies(flags)]


def propose(archetype: str, choices: dict, values: dict, requirements: Record) -> Record:
    """The first version of a design of this kind: the variables given (the others their defaults),
    with the occupants' and cargo's masses and the top speed from the requirements (Q33, Q40, Q50).
    The caller sets design_version, parent_version and change_reason."""
    from hiraku.solvers.k0_handcalc import units

    gen = ARCHETYPES.get(archetype)
    if gen is None:
        raise DesignGenError(f"no kind of design {archetype!r}; known: {', '.join(ARCHETYPES)}")
    for k, opt in (choices or {}).items():
        if k not in gen.CHOICES:
            raise DesignGenError(f"{archetype} has no choice {k!r}" + (f"; its choices: {', '.join(gen.CHOICES)}" if gen.CHOICES else ""))
        if opt not in gen.CHOICES[k]:
            raise DesignGenError(f"{k} must be one of {', '.join(gen.CHOICES[k])}, got {opt!r}")
    vals = dict(values or {})
    if choices:
        set_by = gen.choice_values(choices)
        clash = sorted(k for k in set_by if k in vals)
        if clash:
            raise DesignGenError(f"{', '.join(clash)} come from the choices {choices}; do not give them as values")
        vals.update(set_by)
    for r in requirements.get("requirements", []):
        if r["key"] in FROM_REQUIREMENTS and isinstance(r.get("value"), (int, float)) and not isinstance(r["value"], bool):
            name, unit = FROM_REQUIREMENTS[r["key"]]
            if name in vals:
                raise DesignGenError(f"{name} comes from requirement {r['key']}; do not give it as a value")
            try:
                vals[name] = {"value": units.convert(float(r["value"]), r.get("unit", unit), unit, r["key"]), "unit": unit}
            except ValueError as e:
                raise DesignGenError(f"requirement {r['key']}: {e}") from None
    product = requirements.get("product", archetype)
    return gen.build(vals, version=1, parent=None, reason="first version", name=f"{product} v1: {gen.DESCRIPTION}")


__all__ = ["ARCHETYPES", "GENERATORS", "DesignGenError", "archetypes", "generator", "generator_of", "ladder_4w", "propose",
           "regenerate", "variables"]
