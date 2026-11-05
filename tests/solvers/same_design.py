"""Comparing a generated design with a stored one (a golden) on any operating system.

The generators compute in float64 through trigonometry and searches, whose last bits differ between operating
systems' maths libraries (a design built on Linux differed from the stored one, written on Windows, in the 16th digit:
...88449395 against ...8844941). Rounding the designs does not help: any rounding has values next to its edges, and a
design has some 50 000 numbers. So the goldens stay as written, and these tests compare numbers to REL_TOL of their
size (ABS_TOL near zero); everything else (keys and their order, strings, list lengths, booleans) exactly."""

from __future__ import annotations

import math
from typing import Any

REL_TOL = 1e-9
ABS_TOL = 1e-9


def differences(got: Any, want: Any, path: str = "", limit: int = 5) -> list[str]:
    """Where `got` and `want` differ (up to `limit` places, each "path: got != want")."""
    out: list[str] = []

    def walk(a: Any, b: Any, p: str) -> None:
        if len(out) >= limit:
            return
        num = (int, float)
        if isinstance(a, num) and isinstance(b, num) and not isinstance(a, bool) and not isinstance(b, bool):
            if not math.isclose(float(a), float(b), rel_tol=REL_TOL, abs_tol=ABS_TOL):
                out.append(f"{p}: {a!r} != {b!r}")
            return
        if isinstance(a, dict) and isinstance(b, dict):
            if list(a) != list(b):
                out.append(f"{p}: keys {list(a)[:8]}... != {list(b)[:8]}... (or their order)")
                return
            for k in a:
                walk(a[k], b[k], f"{p}.{k}")
            return
        if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
            if len(a) != len(b):
                out.append(f"{p}: {len(a)} items != {len(b)}")
                return
            for i, (x, y) in enumerate(zip(a, b)):
                walk(x, y, f"{p}[{i}]")
            return
        if type(a) is not type(b) or a != b:
            out.append(f"{p}: {a!r} != {b!r}")

    walk(got, want, path or "design")
    return out


def assert_same_design(test, got: Any, want: Any, msg: str | None = None) -> None:
    """test.fail unless `got` and `want` are the same design up to REL_TOL in their numbers."""
    diff = differences(got, want)
    if diff:
        test.fail("; ".join(diff) + (f" ({msg})" if msg else ""))
