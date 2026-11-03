"""Load-case builders, one per test: BUILDERS[test_id](spec, req, design) -> load_case.

spec is the test's entry in data/catalog/tests.yaml, req the requirements file
(hiraku.contract "requirements_file"), design the design record. Every number
in a load case comes from a requirement or the design, or is computed from them
by the builder; a missing requirement raises BuildError (a ValueError) naming
its key. tolerance_rel is the catalog's when the test gives one, else the
builder's (each module says why).

PLANS[test_id] is how the scripted run predicts for that test: one step in the
shape of data/catalog/scripted.yaml (tool, say, predictions with quantity,
formula_id, output, assumptions and input refs, optionally expected_mismatch),
without load_case (it is the built one). Refs are those of scripted.yaml plus
from.<name>: the value the prediction's inputs_from(design, load_case) returns
under that name. FEEDS[test_id] lists the tests that take its results.
A spec with "variant": "beyond" builds the case past the limit (load_case_id
ending -beyond); PLANS_BEYOND[test_id] is its step where it differs from PLANS.

The analysis department's tests are in analysis.py; the K2 team's in k2_*.py,
which this package collects by name. Each module exports BUILDERS and may
export PLANS, PLANS_BEYOND and FEEDS; a test id belongs to one module only.

A module product_<name>.py holds the builders of one product (its PRODUCT, a
requirements file's `product`): for that product they take the place of the
general builders of the same tests (BUILDERS[test_id] looks at req["product"]);
for other products those tests keep their general builder, or have none. Its
PLANS go into PLANS only for tests that have no general plan; PRODUCT_STEPS
[product](load_case) gives the scripted step of each of its load cases.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Callable

from hiraku.catalog.builders.common import BuildError, Record

BUILDERS: dict = {}
PLANS: dict[str, Record] = {}
PLANS_BEYOND: dict[str, Record] = {}  # steps of the cases past the limit that differ from PLANS
FEEDS: dict[str, list[str]] = {}
PRODUCT_BUILDERS: dict[str, dict] = {}  # product -> test id -> builder
PRODUCT_STEPS: dict[str, Callable[[Record], Record | None]] = {}  # product -> load case -> its scripted step


def _by_product(test_id: str, general):
    def build(spec: Record, req: Record, design: Record):
        own = PRODUCT_BUILDERS.get(req.get("product"), {}).get(test_id)
        if own is not None:
            return own(spec, req, design)
        if general is None:
            from hiraku.catalog.build import NO_BUILDER

            makers = sorted(p for p, b in PRODUCT_BUILDERS.items() if test_id in b)
            raise BuildError(f"test {test_id} is built only for {', '.join(makers)} here: {NO_BUILDER}")
        return general(spec, req, design)

    build.__name__ = f"{test_id.lower()}_by_product"
    build.general = general
    return build


def _collect() -> None:
    names = ["analysis"] + sorted(m.name for m in pkgutil.iter_modules(__path__) if m.name.startswith("k2_"))
    owner: dict[str, str] = {}
    for name in names:
        mod = importlib.import_module(f"{__name__}.{name}")
        for test_id, fn in getattr(mod, "BUILDERS", {}).items():
            if test_id in owner:
                raise RuntimeError(f"test {test_id} is built by both {owner[test_id]} and {name}")
            owner[test_id] = name
            BUILDERS[test_id] = fn
        PLANS.update(getattr(mod, "PLANS", {}))
        PLANS_BEYOND.update(getattr(mod, "PLANS_BEYOND", {}))
        for test_id, down in getattr(mod, "FEEDS", {}).items():
            FEEDS.setdefault(test_id, []).extend(t for t in down if t not in FEEDS.get(test_id, []))
    for m in sorted(m.name for m in pkgutil.iter_modules(__path__) if m.name.startswith("product_")):
        mod = importlib.import_module(f"{__name__}.{m}")
        product = mod.PRODUCT
        if product in PRODUCT_BUILDERS:
            raise RuntimeError(f"product {product} has builders in two modules")
        PRODUCT_BUILDERS[product] = dict(mod.BUILDERS)
        if hasattr(mod, "step_for"):
            PRODUCT_STEPS[product] = mod.step_for
        for test_id, plan in getattr(mod, "PLANS", {}).items():
            PLANS.setdefault(test_id, plan)
        for test_id in mod.BUILDERS:
            general = BUILDERS.get(test_id)
            BUILDERS[test_id] = _by_product(test_id, getattr(general, "general", general))


_collect()

__all__ = ["BUILDERS", "FEEDS", "PLANS", "PLANS_BEYOND", "PRODUCT_BUILDERS", "PRODUCT_STEPS", "BuildError"]
