"""Does numba's on-disk cache hold up when many test processes compile at once? (needs numba)

    python -m qa.numba_cache [--procs 12] [--keep]

Writes a small module with @njit(cache=True) functions (a loop sum and a 2D stencil, no
fastmath or parallel, as the solvers use) into a fresh temporary folder, then starts
--procs processes at the same moment, each importing it and calling the functions once
(the first call compiles to machine code and writes __pycache__/*.nbi and *.nbc), and then
a second wave of --procs processes that should load the cache. Prints per wave: processes
that failed, the results that differ, warnings numba gave about its cache (e.g. "cannot
cache"), and the seconds of the first call (min / median / max). Exit code 1 when a process
failed, a result differed or numba warned about its cache.

qa.run_tests runs up to CPUs - 4 workers; the solvers' cache files sit beside their source
(hiraku/**/__pycache__) unless NUMBA_CACHE_DIR says otherwise, so the first suite after a
change compiles in many processes at once.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

KERNELS = '''
import numpy as np
from numba import njit


@njit(cache=True)
def loop_sum(a):
    s = 0.0
    for i in range(a.shape[0]):
        s += a[i] * a[i]
    return s


@njit(cache=True)
def stencil(u):
    out = np.zeros_like(u)
    for i in range(1, u.shape[0] - 1):
        for j in range(1, u.shape[1] - 1):
            out[i, j] = u[i - 1, j] + u[i + 1, j] + u[i, j - 1] + u[i, j + 1] - 4.0 * u[i, j]
    return out
'''

CHILD = '''
import json, sys, time, warnings
sys.path.insert(0, sys.argv[1])
t0 = time.perf_counter()
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    import numpy as np
    import kernels
    a = np.arange(1000, dtype=np.float64)
    u = np.add.outer(np.arange(50.0), np.arange(40.0)) ** 2
    r = [kernels.loop_sum(a), float(kernels.stencil(u).sum())]
first = time.perf_counter() - t0
cache_warnings = [str(w.message)[:200] for w in caught if "cach" in str(w.message).lower()]
print(json.dumps({"first_s": first, "result": r, "warnings": cache_warnings}))
'''


def _child(folder: Path, start_at: float) -> dict:
    time.sleep(max(0.0, start_at - time.time()))  # all of a wave start together
    p = subprocess.run([sys.executable, "-c", CHILD, str(folder)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=dict(os.environ, PYTHONWARNINGS="always"))
    try:
        return dict(json.loads(p.stdout.strip().splitlines()[-1]), exit=p.returncode)
    except (ValueError, IndexError):
        return {"exit": p.returncode, "error": (p.stderr or p.stdout).strip()[-400:]}


def wave(folder: Path, procs: int) -> list[dict]:
    start_at = time.time() + 1.0
    with ThreadPoolExecutor(max_workers=procs) as pool:
        return list(pool.map(lambda _: _child(folder, start_at), range(procs)))


def summary(name: str, rs: list[dict]) -> tuple[str, bool]:
    failed = [r for r in rs if r.get("exit") != 0 or "error" in r]
    results = {json.dumps(r["result"]) for r in rs if "result" in r}
    warned = sorted({w for r in rs for w in r.get("warnings", [])})
    secs = sorted(r["first_s"] for r in rs if "first_s" in r)
    ok = not failed and len(results) <= 1 and not warned
    line = (f"{name}: {len(rs)} processes, failed {len(failed)}, distinct results {len(results)}, "
            f"cache warnings {len(warned)}")
    if secs:
        line += f", first call {secs[0]:.2f} / {statistics.median(secs):.2f} / {secs[-1]:.2f} s (min/median/max)"
    for r in failed[:3]:
        line += f"\n  failed: {r.get('error', r)}"
    for w in warned[:3]:
        line += f"\n  warning: {w}"
    return line, ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m qa.numba_cache", description=__doc__.split("\n\n")[0])
    ap.add_argument("--procs", type=int, default=12)
    ap.add_argument("--keep", action="store_true", help="keep the temporary folder (prints it)")
    args = ap.parse_args(argv)
    try:
        import numba
    except ImportError:
        print("numba is not installed")
        return 2
    tmp = tempfile.mkdtemp(prefix="numba-cache-")
    folder = Path(tmp)
    (folder / "kernels.py").write_text(KERNELS, encoding="utf-8")
    print(f"numba {numba.__version__}, {args.procs} processes a wave, NUMBA_CACHE_DIR="
          f"{os.environ.get('NUMBA_CACHE_DIR') or '(unset: __pycache__ beside the source)'}")
    ok = True
    for name in ("compile", "cached"):
        line, good = summary(name, wave(folder, args.procs))
        print(line, flush=True)
        ok = ok and good
    files = sorted(p.name for p in (folder / "__pycache__").glob("*.nb*")) if (folder / "__pycache__").is_dir() else []
    print(f"cache files: {len(files)} ({', '.join(files[:6])})")
    if args.keep:
        print(f"kept: {folder}")
    else:
        import shutil
        shutil.rmtree(folder, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
