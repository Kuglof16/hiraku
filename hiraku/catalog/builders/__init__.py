"""Load-case builders, one per test: BUILDERS[test_id](spec, req, design) -> load_case.

spec is the test's entry in data/catalog/tests.yaml, req the requirements file
(hiraku.contract "requirements_file"), design the design record. Every number
in a load case comes from a requirement or the design, or is computed from them
by the builder; a missing requirement raises BuildError (a ValueError) naming
its key. tolerance_rel is the catalog's when the test gives one, else the
builder's (each module says why).

PLANS[test_id] is how the scripted run predicts for that test: one step in the
shape of data/catalog/scripted.yaml (tool, say, predictions with formula_id,
output, assumptions and input refs), without load_case (it is the built one).
Input refs are those of scripted.yaml plus calc.<name>, the values
calc(test_id, design, load_case) returns.

The analysis department's tests are in analysis.py; the K2 team's in k2_*.py,
which this package collects by name. Each module exports BUILDERS and may
export PLANS and CALC; a test id belongs to one module only.
"""

from __future__ import annotations

import importlib
import pkgutil

from hiraku.catalog.builders.common import BuildError, Record

BUILDERS: dict = {}
PLANS: dict[str, Record] = {}
CALC: dict = {}


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
        CALC.update(getattr(mod, "CALC", {}))


_collect()


def calc(test_id: str, design: Record, load_case: Record) -> Record:
    """The calc.<name> values of a test's plan ({} when it needs none)."""
    fn = CALC.get(test_id)
    return fn(design, load_case) if fn else {}


__all__ = ["BUILDERS", "CALC", "PLANS", "BuildError", "calc"]
