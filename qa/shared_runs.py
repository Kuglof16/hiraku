"""One scripted run per requirements file and code version, shared by the tests that read it.

    from qa import shared_runs
    s = shared_runs.scripted_run(Path("data/requirements/offroad-4w-n15.yaml"))
    s.run_dir, s.returncode, s.stdout, s.stderr, s.seconds, s.key, s.reused

Several slow test classes made the same `python -m hiraku run REQ --scripted` each on their
own (the N15 run in tests/verification/test_real_runs.py and tests/agent/test_scene_n15.py,
the demo run there and in tests/tools/test_export.py). scripted_run() makes it once and keeps
it under runs/.test-cache/<key>/<requirements stem>/ (runs/ is local, never committed):

- the key is a sha256 of the source the run reads (every file under hiraku/, data/ and
  plugins/ outside __pycache__, by path and bytes, so uncommitted edits count), the
  requirements file's bytes, the Python, numpy and scipy versions, whether pypardiso can be
  imported and HIRAKU_SOLVER. Another key never reads this run.
- the run is made as tests/verification/test_real_runs.py made its own: one BLAS thread,
  no ANTHROPIC_API_KEY, HIRAKU_TOOL_CHECK_REUSE=0 (every tool check computed in the run),
  PYTHONUTF8=1, the tests' environment otherwise (HIRAKU_CHECK_CACHE as tests/__init__ sets it).
- an OS lock on runs/.test-cache/<key>/<stem>.lock: a second caller waits for the first
  and reads the same folder. result.json is written last; a folder without it (a run that
  was stopped) is made again.
- only a run that ended with exit code 0 and one run folder is kept for later callers;
  another result is returned to its caller and the next caller makes the run again (a run
  stopped for lack of memory is not passed on).
- the run folder is for reading: a test that writes into a run copies it first (copy_run).
- the folders of keys other than the newest KEEP_KEYS are removed when a run is kept.
- HIRAKU_SHARED_RUNS=0 makes every call run on its own, in a fresh temporary folder
  (removed when the process ends).

A determinism check (the same records twice) must not use it: it compares two runs made
on purpose (qa.compare_runs).
"""

from __future__ import annotations

import atexit
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from qa import procs

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "runs" / ".test-cache"
SOURCES = ("hiraku", "data", "plugins")  # what a scripted run reads, besides the requirements file
RESULT = "result.json"
KEEP_KEYS = 3
TIMEOUT_S = 1200  # tests/verification/test_real_runs.py's RUN_TIMEOUT_S
ENV_SWITCH = "HIRAKU_SHARED_RUNS"
POLL_S = 2.0


@dataclass
class Shared:
    run_dir: Path | None  # the run's folder, or None when the run made none (or more than one)
    returncode: int | None  # None: stopped after the timeout
    stdout: str
    stderr: str
    seconds: float  # how long the run took when it was made
    key: str
    reused: bool  # read from an earlier caller's run


def _versions() -> dict:
    out = {"python": platform.python_version(), "implementation": platform.python_implementation()}
    for mod in ("numpy", "scipy"):
        try:
            out[mod] = __import__(mod).__version__
        except ImportError:
            out[mod] = None
    out["pypardiso"] = importlib.util.find_spec("pypardiso") is not None
    out["HIRAKU_SOLVER"] = os.environ.get("HIRAKU_SOLVER")
    return out


def key(requirements: Path, root: Path = ROOT) -> str:
    """sha256 (hex) of what the run of requirements depends on: the source folders, the
    requirements file and the interpreter and packages that compute it."""
    h = hashlib.sha256()
    for top in SOURCES:
        base = root / top
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                h.update(p.relative_to(root).as_posix().encode("utf-8") + b"\0")
                h.update(p.read_bytes() + b"\0")
    h.update(b"requirements\0" + Path(requirements).read_bytes() + b"\0")
    h.update(json.dumps(_versions(), sort_keys=True).encode("utf-8"))
    return h.hexdigest()


def run_env() -> dict:
    """The scripted run's environment (as test_real_runs._scripted_run's)."""
    env = procs.one_blas_thread(dict(os.environ, PYTHONPATH=str(ROOT), PYTHONUTF8="1", HIRAKU_TOOL_CHECK_REUSE="0"))
    env.pop("ANTHROPIC_API_KEY", None)
    return env


def _make(requirements: Path, out: Path, timeout: float) -> tuple[Path | None, int | None, str, str, float]:
    cmd = [sys.executable, "-m", "hiraku", "run", str(requirements), "--scripted", "--runs", str(out)]
    t0 = time.perf_counter()
    try:
        proc = procs.run(cmd, cwd=ROOT, env=run_env(), text=True, encoding="utf-8", errors="replace", timeout=timeout)
        code, so, se = proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired as e:
        code, so, se = None, _text(e.output), _text(e.stderr) + f"\nno result within {timeout:g} s (stopped)"
    seconds = time.perf_counter() - t0
    folders = sorted(p for p in out.iterdir() if p.is_dir()) if out.is_dir() else []
    return (folders[0] if len(folders) == 1 else None), code, so, se, seconds


def _text(x) -> str:
    if x is None:
        return ""
    return x.decode("utf-8", "replace") if isinstance(x, bytes) else x


class _Lock:
    """An OS lock on a file's first byte, waited for (freed by the OS if the holder dies)."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(path, "a+b")

    def __enter__(self):
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    self.f.seek(0)
                    msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                time.sleep(POLL_S)

    def __exit__(self, *exc):
        try:
            if os.name == "nt":
                import msvcrt
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f.fileno(), fcntl.LOCK_UN)
        finally:
            self.f.close()


def _read(folder: Path, k: str) -> Shared | None:
    try:
        r = json.loads((folder / RESULT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    run = folder / r["run_dir"] if r.get("run_dir") else None
    if r.get("key") != k or run is None or not run.is_dir():
        return None
    return Shared(run, r.get("returncode"), r.get("stdout", ""), r.get("stderr", ""), float(r.get("seconds", 0.0)),
                  k, True)


def _prune(cache: Path, keep: str) -> None:
    """Remove the folders of keys other than the newest KEEP_KEYS (keep among them)."""
    try:
        dirs = sorted((p for p in cache.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    kept = [p for p in dirs if p.name == keep] + [p for p in dirs if p.name != keep][:KEEP_KEYS - 1]
    for p in dirs:
        if p not in kept:
            shutil.rmtree(p, ignore_errors=True)


def scripted_run(requirements: Path, timeout: float = TIMEOUT_S, cache: Path | None = None) -> Shared:
    """The scripted run of requirements for the code as it is now: made here, or read from the
    caller that made it (waiting for it while it runs)."""
    requirements = Path(requirements).resolve()
    k = key(requirements)
    if os.environ.get(ENV_SWITCH) == "0":
        tmp = tempfile.TemporaryDirectory()
        atexit.register(tmp.cleanup)
        run, code, so, se, s = _make(requirements, Path(tmp.name) / requirements.stem, timeout)
        return Shared(run, code, so, se, s, k, False)
    cache = CACHE if cache is None else cache
    folder = cache / k[:16] / requirements.stem
    with _Lock(cache / k[:16] / f"{requirements.stem}.lock"):
        got = _read(folder, k)
        if got is not None:
            return got
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        run, code, so, se, s = _make(requirements, folder / "runs", timeout)
        shared = Shared(run, code, so, se, s, k, False)
        if code == 0 and run is not None:
            (folder / RESULT).write_text(json.dumps({
                "key": k, "requirements": str(requirements), "run_dir": run.relative_to(folder).as_posix(),
                "returncode": code, "stdout": so, "stderr": se, "seconds": round(s, 2),
                "made": time.strftime("%Y-%m-%dT%H:%M:%S")}, ensure_ascii=False), encoding="utf-8")
            _prune(cache, k[:16])
        return shared


def copy_run(shared: Shared, dest: Path) -> Path:
    """A copy of the shared run's folder under dest, for a test that writes into it."""
    if shared.run_dir is None:
        raise ValueError("the scripted run made no run folder")
    return Path(shutil.copytree(shared.run_dir, Path(dest) / shared.run_dir.name))
