"""numba's njit for K2's per-step scalar kernels, or the plain Python function when numba is not installed.

The kernels are compiled with cache=True (written next to the module under __pycache__), without
fastmath (the floating-point operations stay in the order the source gives) and without parallel.
HIRAKU_NO_JIT=1 runs the plain Python functions (for comparing the two)."""

from __future__ import annotations

import os

try:
    if os.environ.get("HIRAKU_NO_JIT") == "1":
        raise ImportError("HIRAKU_NO_JIT=1")
    from numba import njit as _njit

    ENABLED = True
except ImportError:  # numba is in requirements.txt; without it the kernels run as Python
    _njit = None
    ENABLED = False


def njit(fn):
    """fn compiled (nopython, cache=True) when numba is there, else fn itself."""
    if _njit is None:
        return fn
    return _njit(cache=True)(fn)


__all__ = ["ENABLED", "njit"]
