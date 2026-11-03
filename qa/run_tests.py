"""Run the whole test suite in parallel processes: the same tests as `python -m unittest`.

    python -m qa.run_tests [--jobs N] [--timeout S]
    HIRAKU_SLOW_TESTS=1 python -m qa.run_tests        # with the slow tests too

Finds the tests the way `python -m unittest` does (discovery from the repository
root), splits them by test class and runs each class with unittest in a process
of its own, N at a time (default: the number of CPUs minus 2, at most 24). The
slowest classes of the last run (runs/test_times.json, kept locally) start
first; without that file, the scripted runs, the K2 vehicle and the shell
solver start first. The environment is passed on as it is.

A class that failed for lack of memory (MemoryError, numpy's _ArrayMemoryError,
Windows' "paging file too small" WinError 1455: the machine was full, often
with other work) is run again alone after the pool, one at a time, and printed
as "rerun alone after a memory error" with its result. Only its alone result
counts. Any other failure stays a failure.

Prints one summary in unittest's words (Ran, OK or FAILED with the counts), each
failure and error with its last line, and checks that every test discovery
found either ran or was skipped with its class. Exit code 1 when a test failed,
a class could not be run, a test is unaccounted for or a class ran out of time.

The tests can share a machine: their servers take a free port (port 0), and
they write only into their own temporary folders. Several suites at once share
the CPUs and the memory, though, so each run leaves a lock file in the system
temp folder (hiraku-run-tests/<pid>.json) while it runs, warns when it starts
if another qa.run_tests is running on the PC, and repeats the warning in its
summary (also for one that started during it).
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from qa import procs

ROOT = Path(__file__).resolve().parents[1]
TIMES = ROOT / "runs" / "test_times.json"  # runs/ is local (not committed)
# started first when there are no times yet
HEAVY = ("tests.verification.test_real_runs", "tests.solvers.k2", "tests.verification.test_tools_protocol",
         "tests.solvers.test_frame_shell", "tests.solvers.test_k1_shell", "tests.tools.test_made_tools")
CLASS_SKIP_RE = re.compile(r"^setUpClass \(([\w.]+)\)$")
# out of memory: MemoryError and numpy's _ArrayMemoryError; WinError 1455 is "the paging file is too small";
# OpenBLAS gives up with "Memory allocation still failed"; a worker that died with Windows' 0xC000012D
# (commitment limit) or 0xC0000017 (no memory) leaves only its exit code
MEMORY_RE = re.compile(r"MemoryError|\[WinError 1455\]|Memory allocation still failed|\bexit (3221225773|3221225495)\b")
LOCK_DIR = Path(tempfile.gettempdir()) / "hiraku-run-tests"  # one file per qa.run_tests running on this PC


# --- other suites on the same PC ----------------------------------------------------------
def _alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # access denied: it exists
        code = ctypes.c_ulong()
        ok = kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _others() -> list[dict]:
    """The other qa.run_tests running on this PC now; lock files of runs that ended are removed."""
    found = []
    for p in sorted(LOCK_DIR.glob("*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            pid = int(rec["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if pid == os.getpid():
            continue
        if _alive(pid):
            found.append(rec)
        else:
            try:
                p.unlink()
            except OSError:
                pass
    return found


def _take_lock(jobs: int) -> None:
    try:
        LOCK_DIR.mkdir(exist_ok=True)
        path = LOCK_DIR / f"{os.getpid()}.json"
        path.write_text(json.dumps({"pid": os.getpid(), "root": str(ROOT), "jobs": jobs,
                                    "slow": os.environ.get("HIRAKU_SLOW_TESTS") == "1",
                                    "started": time.strftime("%H:%M")}), encoding="utf-8")
    except OSError:
        return
    atexit.register(path.unlink, missing_ok=True)


def _overlap(rec: dict) -> str:
    return (f"another qa.run_tests is running on this PC (pid {rec.get('pid')}, since {rec.get('started')}, "
            f"{rec.get('jobs')} processes{', slow tests' if rec.get('slow') else ''}, in {rec.get('root')}): "
            "CPUs and memory are shared, so times are longer and memory errors more likely")


# --- discovery (in this process) --------------------------------------------------------
def _flatten(suite):
    for x in suite:
        if isinstance(x, unittest.TestSuite):
            yield from _flatten(x)
        else:
            yield x


def discover() -> dict[str, list[str]]:
    """Test ids by the name each worker loads: module.Class, or the module whose import failed."""
    groups: dict[str, list[str]] = {}
    for t in _flatten(unittest.defaultTestLoader.discover(str(ROOT), top_level_dir=str(ROOT))):
        if type(t).__module__ == "unittest.loader":  # _FailedTest: the module did not import
            groups.setdefault(t._testMethodName, []).append(t.id())
        else:
            groups.setdefault(f"{type(t).__module__}.{type(t).__qualname__}", []).append(t.id())
    return groups


# --- one worker (a separate process) ----------------------------------------------------
class _Result(unittest.TextTestResult):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.ids_run: list[str] = []

    def startTest(self, test):
        self.ids_run.append(test.id())
        super().startTest(test)


def worker(out: Path, names: list[str]) -> int:
    """Run the named tests with unittest; write what ran, failed and was skipped to out (JSON)."""
    sys.path.insert(0, str(ROOT))
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    # utf-8: unittest writes each failure's text here, and the console's code page (cp932) cannot hold every character
    with open(os.devnull, "w", encoding="utf-8", errors="replace") as null:
        res = unittest.TextTestRunner(stream=null, resultclass=_Result, verbosity=0).run(suite)

    def last(tb: str) -> str:
        lines = [s for s in tb.strip().splitlines() if s.strip()]
        return lines[-1] if lines else ""

    out.write_text(json.dumps({
        "ran": res.testsRun, "ids_run": res.ids_run,
        "failures": [{"test": str(t), "kind": "FAIL", "last": last(tb), "traceback": tb} for t, tb in res.failures],
        "errors": [{"test": str(t), "kind": "ERROR", "last": last(tb), "traceback": tb} for t, tb in res.errors],
        "skipped": [{"test": str(t), "why": why} for t, why in res.skipped],
        "expected_failures": len(res.expectedFailures), "unexpected_successes": len(res.unexpectedSuccesses),
    }, ensure_ascii=False), encoding="utf-8")
    return 0


# --- the parent -------------------------------------------------------------------------
def _order(groups: dict[str, list[str]]) -> list[str]:
    try:
        last = json.loads(TIMES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last = {}

    def weight(name: str) -> float:
        if name in last:
            return float(last[name])
        return 1000.0 if name.startswith(HEAVY) else len(groups[name]) * 0.1

    return sorted(groups, key=weight, reverse=True)


def _run_one(name: str, tmp: Path, timeout: float, tag: str = "") -> dict:
    out = tmp / f"{name}{tag}.json"
    t0 = time.perf_counter()
    try:  # on timeout the worker and the runs it started are stopped (qa.procs)
        proc = procs.run([sys.executable, "-m", "qa.run_tests", "--worker", str(out), name], cwd=ROOT,
                         text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"name": name, "s": time.perf_counter() - t0, "crash": f"no result within {timeout:g} s"}
    s = time.perf_counter() - t0
    try:
        return dict(json.loads(out.read_text(encoding="utf-8")), name=name, s=s)
    except (OSError, ValueError):
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-5:]
        return {"name": name, "s": s, "crash": f"exit {proc.returncode}: " + " | ".join(tail)}


def _out_of_memory(r: dict) -> bool:
    """The class failed for lack of memory: in a failure's or error's traceback, or in its worker's crash."""
    texts = [x.get("traceback", "") for x in r.get("failures", []) + r.get("errors", [])] + [r.get("crash", "")]
    return any(MEMORY_RE.search(t) for t in texts)


def _rerun_alone(results: list[dict], tmp: Path, timeout: float, run=_run_one) -> list[dict]:
    """Run each class that failed for lack of memory again, one at a time; its alone result
    replaces the pooled one in results. Returns the alone results."""
    again = []
    for k, r in sorted(enumerate(results), key=lambda kr: kr[1]["name"]):
        if _out_of_memory(r):
            results[k] = dict(run(r["name"], tmp, timeout, ".alone"), rerun_alone=True)
            again.append(results[k])
    return again


def _verdict(r: dict) -> str:
    if "crash" in r:
        return f"CLASS NOT RUN ({r['crash']})"
    counts = [f"{what}={len(r[key])}" for what, key in (("failures", "failures"), ("errors", "errors")) if r.get(key)]
    return f"FAILED ({', '.join(counts)})" if counts else "OK"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--worker"]:
        return worker(Path(argv[1]), argv[2:])
    ap = argparse.ArgumentParser(prog="python -m qa.run_tests", description=__doc__.split("\n\n")[0])
    ap.add_argument("--jobs", "-j", type=int, default=max(1, min(24, (os.cpu_count() or 4) - 2)),
                    help="processes at a time (default: CPUs - 2, at most 24)")
    ap.add_argument("--timeout", type=float, default=1800.0, help="seconds one class may take (default 1800)")
    args = ap.parse_args(argv)

    start = time.perf_counter()
    before = _others()
    for rec in before:
        print(f"warning: {_overlap(rec)}", file=sys.stderr, flush=True)
    _take_lock(args.jobs)
    groups = discover()
    expected = {i for ids in groups.values() for i in ids}
    results = []
    with tempfile.TemporaryDirectory() as tmp, ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(_run_one, name, Path(tmp), args.timeout) for name in _order(groups)]
        for f in as_completed(futures):
            results.append(f.result())
        again = _rerun_alone(results, Path(tmp), args.timeout)
    wall = time.perf_counter() - start

    ran = sum(r.get("ran", 0) for r in results)
    bad = [x for r in results for x in r.get("failures", []) + r.get("errors", [])]
    skipped = [x for r in results for x in r.get("skipped", [])]
    crashed = [r for r in results if "crash" in r]
    # every discovered test ran, or its class was skipped in setUpClass
    accounted = {i for r in results for i in r.get("ids_run", [])}
    for x in skipped:
        m = CLASS_SKIP_RE.match(x["test"])
        if m:
            accounted |= set(groups.get(m.group(1), []))
    missing = sorted(expected - accounted - {i for r in crashed for i in groups.get(r["name"], [])})

    for x in sorted(bad, key=lambda x: x["test"]):
        print(f"{x['kind']}: {x['test']}\n    {x['last']}")
    for r in sorted(crashed, key=lambda r: r["name"]):
        print(f"CLASS NOT RUN: {r['name']} ({len(groups.get(r['name'], []))} tests): {r['crash']}")
    for i in missing[:20]:
        print(f"NOT RUN: {i}")
    for r in again:
        print(f"rerun alone after a memory error: {r['name']}: {_verdict(r)} ({r.get('ran', 0)} tests, {r['s']:.0f} s)")
    print("-" * 70)
    print(f"Ran {ran} tests in {wall:.1f}s ({len(groups)} classes in {args.jobs} processes; "
          f"{sum(r['s'] for r in results):.0f} s of work)")
    print(f"discovered {len(expected)} tests: {len(accounted & expected)} ran or were skipped with their class"
          + (f", {len(missing)} not accounted for" if missing else ""))
    counts = [f"failures={sum(1 for x in bad if x['kind'] == 'FAIL')}" if any(x["kind"] == "FAIL" for x in bad) else "",
              f"errors={sum(1 for x in bad if x['kind'] == 'ERROR')}" if any(x["kind"] == "ERROR" for x in bad) else "",
              f"skipped={len(skipped)}" if skipped else "",
              f"classes not run={len(crashed)}" if crashed else ""]
    counts = ", ".join(c for c in counts if c)
    failed = bool(bad or crashed or missing)
    print(("FAILED" if failed else "OK") + (f" ({counts})" if counts else ""))
    slow = sorted(results, key=lambda r: -r["s"])[:5]
    print("slowest classes: " + ", ".join(f"{r['name'].rsplit('.', 2)[-2]}.{r['name'].rsplit('.', 1)[-1]} {r['s']:.0f}s"
                                          for r in slow))
    seen = {rec.get("pid") for rec in before}
    for rec in before + [rec for rec in _others() if rec.get("pid") not in seen]:
        print(f"warning: {_overlap(rec)}")
    try:
        TIMES.parent.mkdir(exist_ok=True)
        TIMES.write_text(json.dumps({r["name"]: round(r["s"], 2) for r in results}, indent=1), encoding="utf-8")
    except OSError:
        pass
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
