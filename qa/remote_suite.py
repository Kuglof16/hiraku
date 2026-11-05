"""Run the three suites one after another and write one summary: for a machine set up only to test.

    python -m qa.remote_suite [--jobs 8] [--slow-jobs 6] [--only fast,browser,slow] [--out FILE]

The suites, in this order:
  fast     python -m qa.run_tests --jobs JOBS --no-wait
  browser  python -m tests.web.browser            (HIRAKU_WEB_BROWSER=1; needs Playwright's chromium)
  slow     python -m qa.run_tests --jobs SLOW_JOBS --no-wait   with HIRAKU_SLOW_TESTS=1

Each child gets OPENBLAS_NUM_THREADS=1, OMP_NUM_THREADS=1 and PYTHONUTF8=1 unless they are
already set. Each suite's whole output goes to runs/remote_suite/<name>.log; the summary
(JSON) to runs/remote_suite/summary.json (or --out), and one line per suite is printed:

  fast: OK, ran 3412 tests (skipped 210) in 612 s, exit 0

ran, the result word, the failure/error/skip counts and unittest's own seconds are read
from unittest's last lines ("Ran N tests in Xs", "OK"/"FAILED (...)"); the wall seconds and
the exit code are measured here. A suite whose lines cannot be read is "UNREAD". Exit code
1 when any suite did not end OK.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "runs" / "remote_suite"
CHILD_ENV = {"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "PYTHONUTF8": "1"}
RAN_RE = re.compile(r"^Ran (\d+) tests? in ([\d.]+)s", re.M)
END_RE = re.compile(r"^(OK|FAILED)(?: \(([^)]*)\))?\s*$", re.M)


def suites(jobs: int, slow_jobs: int) -> dict[str, tuple[list[str], dict[str, str]]]:
    py = sys.executable
    return {
        "fast": ([py, "-m", "qa.run_tests", "--jobs", str(jobs), "--no-wait"], {}),
        "browser": ([py, "-m", "tests.web.browser"], {"HIRAKU_WEB_BROWSER": "1"}),
        "slow": ([py, "-m", "qa.run_tests", "--jobs", str(slow_jobs), "--no-wait"], {"HIRAKU_SLOW_TESTS": "1"}),
    }


def read_result(text: str) -> dict:
    """unittest's last 'Ran N tests in Xs' and 'OK'/'FAILED (...)' lines, as numbers."""
    ran, end = RAN_RE.findall(text), END_RE.findall(text)
    if not ran or not end:
        return {"result": "UNREAD"}
    out = {"ran": int(ran[-1][0]), "unittest_s": float(ran[-1][1]), "result": end[-1][0]}
    for part in (end[-1][1] or "").split(","):
        k, _, v = part.strip().partition("=")
        if v.strip().isdigit():
            out[k.replace(" ", "_")] = int(v)
    return out


def line(name: str, r: dict) -> str:
    extra = [f"{k} {r[k]}" for k in ("failures", "errors", "skipped", "classes_not_run") if r.get(k)]
    ran = f", ran {r['ran']} tests" if "ran" in r else ""
    return (f"{name}: {r['result']}{ran}{' (' + ', '.join(extra) + ')' if extra else ''} "
            f"in {r['wall_s']:.0f} s, exit {r['exit']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m qa.remote_suite", description=__doc__.split("\n\n")[0])
    ap.add_argument("--jobs", type=int, default=8, help="processes for the fast suite (default 8)")
    ap.add_argument("--slow-jobs", type=int, default=6, help="processes for the slow suite (default 6)")
    ap.add_argument("--only", default="fast,browser,slow", help="the suites to run, comma-separated, in this order")
    ap.add_argument("--out", type=Path, default=OUT_DIR / "summary.json", help="the summary's JSON file")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if callable(getattr(stream, "reconfigure", None)):
            stream.reconfigure(encoding="utf-8", errors="replace")
    plan = suites(args.jobs, args.slow_jobs)
    names = [n.strip() for n in args.only.split(",") if n.strip()]
    unknown = [n for n in names if n not in plan]
    if unknown:
        ap.error(f"unknown suite(s) {', '.join(unknown)}; choose from {', '.join(plan)}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = {"started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "platform": platform.platform(),
               "python": platform.python_version(), "cpus": os.cpu_count(), "commit": _commit(), "suites": {}}
    ok = True
    for name in names:
        cmd, extra = plan[name]
        env = dict(os.environ)
        for k, v in CHILD_ENV.items():
            env.setdefault(k, v)
        env.update(extra)
        log = OUT_DIR / f"{name}.log"
        print(f"{name}: {' '.join(cmd[1:])} ...", flush=True)
        t0 = time.perf_counter()
        with open(log, "w", encoding="utf-8", errors="replace") as f:
            proc = subprocess.run(cmd, cwd=ROOT, env=env, stdout=f, stderr=subprocess.STDOUT)
        r = dict(read_result(log.read_text(encoding="utf-8", errors="replace")),
                 wall_s=round(time.perf_counter() - t0, 1), exit=proc.returncode, log=str(log.relative_to(ROOT)))
        summary["suites"][name] = r
        ok = ok and r["result"] == "OK" and proc.returncode == 0
        print(line(name, r), flush=True)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(summary, indent=1), encoding="utf-8")  # after each suite
    summary["ok"] = ok
    args.out.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(f"{'OK' if ok else 'FAILED'}: summary in {args.out}")
    return 0 if ok else 1


def _commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip() or None
    except OSError:
        return None


if __name__ == "__main__":
    sys.exit(main())
