"""Run the whole test suite in parallel processes: the same tests as `python -m unittest`.

    python -m qa.run_tests [--jobs N] [--timeout S] [--no-wait]
    HIRAKU_SLOW_TESTS=1 python -m qa.run_tests        # with the slow tests too

One full suite runs on the PC at a time: a run first takes a machine-wide lock
(an OS lock on hiraku-run-tests/suite.lock in the system temp folder, freed when
its process ends), and while another run holds it, it waits and prints
"waiting for <pid> (<worktree>, since hh:mm)". --no-wait runs at once without
the lock (for emergencies; it warns about the overlap and uses 8 processes).

Finds the tests the way `python -m unittest` does (discovery from the repository
root), splits them by test class and runs each class with unittest in a process
of its own, N at a time (default: the number of CPUs minus 4, at least 8). The
slowest classes of the last run (runs/test_times.json, kept locally) start
first; without that file, the scripted runs, the K2 vehicle and the shell
solver start first. The environment is passed on as it is. A class that took SPLIT_S
or more last time and shares nothing between its methods (no setUpClass, tearDownClass
or setUpModule) runs one process per method (--no-split keeps it whole). --only PREFIX
runs only the classes whose name starts with PREFIX (its times are added to the file,
not put in place of it).

Memory: each worker reports its peak (runs/test_memory.json, local). A class whose peak
was HEAVY_GB or more (or one of KNOWN_HEAVY_GB, whose value is a floor) is heavy: at most
--max-heavy (default 2) heavy classes run at once, and a heavy class waits while another
runs and free memory is under its peak plus RESERVE_GB ("waiting for memory: ..."); the
lighter classes after it go first meanwhile. A scripted run's own process is not in its
class's peak on Windows. Each worker gets HIRAKU_CONVERGENCE_JOBS=1 unless it is set: the
convergence test's mesh levels run one at a time, as its KNOWN_HEAVY_GB value counts them.

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
from pathlib import Path

from qa import procs

ROOT = Path(__file__).resolve().parents[1]
TIMES = ROOT / "runs" / "test_times.json"  # runs/ is local (not committed)
MEMORY = ROOT / "runs" / "test_memory.json"  # each class's peak memory (GB) last time, local too
HEAVY_GB = 4.0  # a class whose peak was this much or more is "heavy": at most --max-heavy of them at once
RESERVE_GB = 4.0  # a heavy class starts only when free memory is its last peak plus this (unless none runs)
# heavy whatever their own process's peak says (the floor of memory_of): measured on 11/5 00:32 (four
# at once took about 34 GB). SpaceFrameConvergenceTest runs each mesh level in a child process (one at a
# time by default), whose memory is not in the worker's peak: 14.4 GB is the 14 mm level's (11/5 02:26).
KNOWN_HEAVY_GB = {"tests.solvers.test_fe_tubes.RearLinkRevisionTest": 8.7,
                  "tests.solvers.test_fe_tubes.SpaceFrameConvergenceTest": 14.4,
                  "tests.solvers.test_frame_tubes.SpaceFrameK2Test": 8.6,
                  "tests.solvers.test_frame_tubes.MeshFlowTest": 7.6}
CONVERGENCE_JOBS = "HIRAKU_CONVERGENCE_JOBS"  # mesh levels the convergence test runs side by side (worker_env)
# started first when there are no times yet
HEAVY = ("tests.verification.test_real_runs", "tests.solvers.k2", "tests.verification.test_tools_protocol",
         "tests.solvers.test_frame_shell", "tests.solvers.test_k1_shell", "tests.tools.test_made_tools")
CLASS_SKIP_RE = re.compile(r"^setUpClass \(([\w.]+)\)$")
# out of memory: MemoryError and numpy's _ArrayMemoryError; WinError 1455 is "the paging file is too small";
# OpenBLAS gives up with "Memory allocation still failed"; a worker that died with Windows' 0xC000012D
# (commitment limit) or 0xC0000017 (no memory) leaves only its exit code; on Linux the OOM killer's SIGKILL is exit -9
MEMORY_RE = re.compile(r"MemoryError|\[WinError 1455\]|Memory allocation still failed"
                       r"|\bexit (3221225773|3221225495|-9)\b")
# one file per qa.run_tests running on this PC; HIRAKU_RUN_TESTS_LOCK_DIR moves it (tests of the lock itself)
LOCK_DIR = Path(os.environ.get("HIRAKU_RUN_TESTS_LOCK_DIR") or Path(tempfile.gettempdir()) / "hiraku-run-tests")


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


QUEUE_FILE = "suite.lock"  # in LOCK_DIR: one full suite at a time, an OS lock on its first byte
QUEUE_INFO = "suite.json"  # in LOCK_DIR: who holds the lock (pid, root, since)
POLL_S = 5.0


def _lock_byte(f) -> bool:
    """Take the OS lock on the file's first byte without waiting; False when another process holds it."""
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def take_queue(jobs: int, wait: bool = True, say=print, what: str = "qa.run_tests"):
    """The machine-wide suite lock: the open lock file (keep it open while the suite runs; the OS frees
    the lock when the process ends), or None when another run holds it and wait is False. what: the
    holder's job, shown to those waiting (qa.slot takes the same lock for other heavy jobs)."""
    LOCK_DIR.mkdir(exist_ok=True)
    f = open(LOCK_DIR / QUEUE_FILE, "a+b")
    shown = None
    while not _lock_byte(f):
        if not wait:
            f.close()
            return None
        try:
            holder = json.loads((LOCK_DIR / QUEUE_INFO).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            holder = {}
        if holder.get("pid") != shown:
            shown = holder.get("pid")
            say(f"waiting for {holder.get('pid', '?')} ({holder.get('what', 'qa.run_tests')}, {holder.get('root', '?')}, "
                f"since {holder.get('started', '?')})", flush=True)
        time.sleep(POLL_S)
    (LOCK_DIR / QUEUE_INFO).write_text(json.dumps({"pid": os.getpid(), "root": str(ROOT), "jobs": jobs, "what": what,
                                                   "started": time.strftime("%H:%M")}), encoding="utf-8")
    return f


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


SPLIT_S = 60.0  # a class that took this long last time runs one process per method, when nothing is shared


def _shares_setup(cls: type) -> bool:
    """The class (or a base other than TestCase) has setUpClass/tearDownClass, or its module setUpModule:
    its methods share what that makes, so they stay in one process."""
    for base in cls.__mro__:
        if base in (unittest.TestCase, object):
            continue
        if "setUpClass" in vars(base) or "tearDownClass" in vars(base):
            return True
    mod = sys.modules.get(cls.__module__)
    return bool(mod and (hasattr(mod, "setUpModule") or hasattr(mod, "tearDownModule")))


def discover(only: list[str] | None = None, split: dict[str, float] | None = None) -> dict[str, list[str]]:
    """Test ids by the name each worker loads: module.Class, or the module whose import failed.
    only: keep the names starting with one of these prefixes. split: last times by name; a class
    whose time (or its methods' sum) is SPLIT_S or more and that shares no setup is given as one
    name per method (module.Class.method)."""
    groups: dict[str, list[str]] = {}
    classes: dict[str, type] = {}
    for t in _flatten(unittest.defaultTestLoader.discover(str(ROOT), top_level_dir=str(ROOT))):
        if type(t).__module__ == "unittest.loader":  # _FailedTest: the module did not import
            groups.setdefault(t._testMethodName, []).append(t.id())
        else:
            name = f"{type(t).__module__}.{type(t).__qualname__}"
            classes[name] = type(t)
            groups.setdefault(name, []).append(t.id())
    if only:
        groups = {k: v for k, v in groups.items() if k.startswith(tuple(only))}
    if split:
        for name in [k for k, ids in groups.items() if k in classes and len(ids) > 1]:
            if (_weight(name, split) or 0.0) >= SPLIT_S and not _shares_setup(classes[name]):  # None: never timed
                for i in groups.pop(name):
                    groups[i] = [i]
    return groups


def _weight(name: str, last: dict[str, float]) -> float | None:
    """The last time of a class, or the sum of its methods' when it ran split; None when unknown."""
    if name in last:
        return float(last[name])
    parts = [float(v) for k, v in last.items() if k.startswith(name + ".")]
    return sum(parts) if parts else None


# --- one worker (a separate process) ----------------------------------------------------
class _Result(unittest.TextTestResult):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.ids_run: list[str] = []

    def startTest(self, test):
        self.ids_run.append(test.id())
        super().startTest(test)


def peak_gb() -> float | None:
    """This process's peak memory in GB (Windows: peak working set; elsewhere: max RSS of it and its
    waited-for children), or None when it cannot be read."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                    (n, ctypes.c_size_t) for n in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                                   "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                                                   "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
            c = Counters()
            c.cb = ctypes.sizeof(c)
            psapi = ctypes.WinDLL("psapi")
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            kernel = ctypes.WinDLL("kernel32")
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(c), c.cb):
                return None
            return c.PeakWorkingSetSize / 2**30
        import resource
        kb = max(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                 resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
        return kb / 2**20  # Linux reports KB
    except Exception:
        return None


def free_gb() -> float | None:
    """Free physical memory in GB (Windows: available; Linux: MemAvailable), or None."""
    try:
        if os.name == "nt":
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + [
                    (n, ctypes.c_ulonglong) for n in ("ullTotalPhys", "ullAvailPhys", "ullTotalPageFile",
                                                      "ullAvailPageFile", "ullTotalVirtual", "ullAvailVirtual",
                                                      "ullAvailExtendedVirtual")]
            st = Status()
            st.dwLength = ctypes.sizeof(st)
            if not ctypes.WinDLL("kernel32").GlobalMemoryStatusEx(ctypes.byref(st)):
                return None
            return st.ullAvailPhys / 2**30
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 2**20
    except Exception:
        return None
    return None


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
        "peak_gb": peak_gb(),
    }, ensure_ascii=False), encoding="utf-8")
    return 0


# --- the parent -------------------------------------------------------------------------
def _last_times() -> dict[str, float]:
    try:
        return json.loads(TIMES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _order(groups: dict[str, list[str]], last: dict[str, float] | None = None) -> list[str]:
    last = _last_times() if last is None else last

    def weight(name: str) -> float:
        w = _weight(name, last)
        if w is not None:
            return w
        cls = name.rsplit(".", 1)[0]  # a method run on its own for the first time: its share of the class
        if len(groups[name]) == 1 and groups[name][0] == name and (w := _weight(cls, last)) is not None:
            return w / max(1, sum(1 for k in groups if k.startswith(cls + ".")))
        return 1000.0 if name.startswith(HEAVY) else len(groups[name]) * 0.1

    return sorted(groups, key=weight, reverse=True)


def _last_memory() -> dict[str, float]:
    try:
        return json.loads(MEMORY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def memory_of(name: str, last: dict[str, float]) -> float | None:
    """The last peak (GB) of a class or method (a method that never ran alone: its class's), and never less
    than the known one (KNOWN_HEAVY_GB: a class whose heavy work runs in child processes)."""
    known = next((KNOWN_HEAVY_GB[k] for k in (name, name.rsplit(".", 1)[0]) if k in KNOWN_HEAVY_GB), None)
    for k in (name, name.rsplit(".", 1)[0]):
        if k in last:
            return max(float(last[k]), known or 0.0)
    return known


def schedule(names: list[str], jobs: int, run, memory: dict, max_heavy: int = 2,
             free=free_gb, poll_s: float = 5.0, say=None) -> list[dict]:
    """Run names (in this order of preference) on `jobs` threads with run(name) -> result. A heavy
    name (memory HEAVY_GB or more) starts only while fewer than max_heavy heavy ones run and, when
    another heavy one runs, free memory is at least its peak plus RESERVE_GB; while it waits, the
    lighter names after it go first. Returns the results in the order they ended."""
    import threading
    pending = list(names)
    results: list[dict] = []
    state = {"heavy": 0}
    cond = threading.Condition()
    told: set = set()

    def heavy(n: str) -> bool:
        m = memory.get(n)
        return m is not None and m >= HEAVY_GB

    def pick():
        for n in pending:
            if not heavy(n):
                return n
            if state["heavy"] >= max_heavy:
                continue
            if state["heavy"] > 0:
                f = free()
                if f is not None and f < memory[n] + RESERVE_GB:
                    if say and n not in told:
                        told.add(n)
                        say(f"waiting for memory: {n} ({memory[n]:.1f} GB last time, {f:.1f} GB free)")
                    continue
            return n
        return None

    def loop():
        while True:
            with cond:
                while True:
                    if not pending:
                        return
                    n = pick()
                    if n is not None:
                        break
                    cond.wait(timeout=poll_s)
                pending.remove(n)
                h = heavy(n)
                state["heavy"] += h
            try:
                r = run(n)
            except Exception as e:  # never lose a class: it is reported as not run
                r = {"name": n, "s": 0.0, "crash": f"the runner failed: {e!r}"}
            finally:
                with cond:
                    state["heavy"] -= h
                    cond.notify_all()
            with cond:
                results.append(r)

    threads = [threading.Thread(target=loop, daemon=True) for _ in range(max(1, jobs))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def worker_env() -> dict:
    """A worker's environment: one BLAS thread (qa.procs), and one mesh level at a time in the convergence
    test (HIRAKU_CONVERGENCE_JOBS=1 unless set): KNOWN_HEAVY_GB counts that class as one level, and levels
    side by side (up to 46.9 GB for four) would not be seen by the heavy classes started after it."""
    env = procs.one_blas_thread()
    env.setdefault(CONVERGENCE_JOBS, "1")
    return env


def _run_one(name: str, tmp: Path, timeout: float, tag: str = "") -> dict:
    out = tmp / f"{name}{tag}.json"
    t0 = time.perf_counter()
    try:  # on timeout the worker and the runs it started are stopped; one BLAS thread each (qa.procs)
        proc = procs.run([sys.executable, "-m", "qa.run_tests", "--worker", str(out), name], cwd=ROOT,
                         env=worker_env(), text=True, encoding="utf-8", errors="replace", timeout=timeout)
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


def _utf8_out() -> None:
    """stdout and stderr in utf-8: failure texts hold characters the console's code page (cp932) lacks."""
    for stream in (sys.stdout, sys.stderr):
        if callable(getattr(stream, "reconfigure", None)):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--worker"]:
        return worker(Path(argv[1]), argv[2:])
    _utf8_out()
    ap = argparse.ArgumentParser(prog="python -m qa.run_tests", description=__doc__.split("\n\n")[0])
    ap.add_argument("--jobs", "-j", type=int, default=None,
                    help="processes at a time (default: CPUs - 4, at least 8; 8 with --no-wait)")
    ap.add_argument("--timeout", type=float, default=3600.0, help="seconds one class may take (default 3600)")
    ap.add_argument("--only", action="append", default=[], metavar="PREFIX",
                    help="run only the classes whose name starts with PREFIX (e.g. tests.web); may be repeated")
    ap.add_argument("--no-split", action="store_true",
                    help=f"keep every class in one process (by default a class that took {SPLIT_S:g} s or more "
                         "last time and shares no setup runs one process per method)")
    ap.add_argument("--max-heavy", type=int, default=2,
                    help=f"classes whose last peak was {HEAVY_GB:g} GB or more that may run at once (default 2)")
    ap.add_argument("--no-wait", action="store_true",
                    help="run at once, without waiting for the machine-wide suite lock (emergencies only)")
    args = ap.parse_args(argv)
    if args.jobs is None:
        args.jobs = 8 if args.no_wait else max(8, (os.cpu_count() or 8) - 4)
    # kept open for the whole run: the lock is held while this file is open (the OS frees it at exit)
    queue = None if args.no_wait else take_queue(args.jobs, say=lambda *a, **k: print(*a, file=sys.stderr, **k))

    start = time.perf_counter()
    before = _others()
    for rec in before:
        print(f"warning: {_overlap(rec)}", file=sys.stderr, flush=True)
    _take_lock(args.jobs)
    last = _last_times()
    groups = discover(args.only, None if args.no_split else last)
    expected = {i for ids in groups.values() for i in ids}
    results = []
    mem_last = _last_memory()
    memory = {name: memory_of(name, mem_last) for name in groups}
    with tempfile.TemporaryDirectory() as tmp:
        results = schedule(_order(groups, last), args.jobs, lambda n: _run_one(n, Path(tmp), args.timeout), memory,
                           max_heavy=args.max_heavy, say=lambda m: print(m, file=sys.stderr, flush=True))
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
        if not args.only:  # a whole suite: its names replace the last ones (a class split now drops its old entry)
            last = {}
        last.update({r["name"]: round(r["s"], 2) for r in results})
        TIMES.write_text(json.dumps(last, indent=1), encoding="utf-8")
        if not args.only:
            mem_last = {}
        mem_last.update({r["name"]: round(r["peak_gb"], 2) for r in results if r.get("peak_gb") is not None})
        MEMORY.write_text(json.dumps(mem_last, indent=1), encoding="utf-8")
    except OSError:
        pass
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
