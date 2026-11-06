"""Run sets as separate `python -m studies.lere.run_set` subprocesses, at most --jobs at a time.

  python -m studies.lere.runq --out DIR [--jobs 4] [--stagger 120] [NAMES...] [-- run_set flags]
  python -m studies.lere.runq --list

NAMES default to every set. Each set logs to DIR/NAME/log.txt; DIR/runq.log holds the queue's own lines. A set
whose DIR/NAME/result.json already holds "best" and "modal" is skipped (--force runs it again). Each run_set waits
for 15 GB free memory before each shell solve; the starts are staggered (--stagger seconds) so that the sets do not
all read the free memory before any of them has grown. The queue stops (kills its children) if the machine's python
process count grows by more than --jobs + 6 over the start (psutil or /proc; skipped if not known)."""
import argparse
import json
import os
import subprocess
import sys
import time


def _done(outdir, name):
    p = os.path.join(outdir, name, "result.json")
    if not os.path.exists(p):
        return False
    try:
        with open(p, encoding="utf-8") as f:
            s = json.load(f)
    except (OSError, ValueError):
        return False
    if s.get("adds") is not None:  # a whole-vehicle set: step hit only
        return "best" in s
    return "best" in s and "modal" in s


def main():
    argv = sys.argv[1:]
    passthru = []
    if "--" in argv:
        k = argv.index("--")
        argv, passthru = argv[:k], argv[k + 1:]
    ap = argparse.ArgumentParser(description="Run the LE/RE + N1 study's sets in parallel subprocesses.")
    ap.add_argument("names", nargs="*")
    ap.add_argument("--out", help="output folder")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--stagger", type=float, default=120.0, help="seconds between starts")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)

    from .common import ENV, REPO, py_count
    from .sets import SETS
    if a.list:
        for n, s in SETS.items():
            w = f"  adds {s['adds']}" if "adds" in s else ""
            print(f"{n:8s} tyre {s['tyre']:g}  {s['doc']}  {json.dumps(s['extra'])}{w}")
        return
    if not a.out:
        ap.error("--out is required")
    names = a.names or list(SETS)
    bad = [n for n in names if n not in SETS]
    if bad:
        ap.error(f"unknown sets {bad}")
    outdir = os.path.abspath(a.out)
    os.makedirs(outdir, exist_ok=True)
    qlog = open(os.path.join(outdir, "runq.log"), "a", encoding="utf-8")

    def say(*x):
        line = time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(v) for v in x)
        print(line, flush=True)
        qlog.write(line + "\n"); qlog.flush()

    todo = [n for n in names if a.force or not _done(outdir, n)]
    for n in names:
        if n not in todo:
            say(n, "already done, skipped")
    env = {**os.environ, **ENV}
    n0 = py_count()
    say("start", len(todo), "sets, jobs", a.jobs, "python processes", n0)
    running = {}  # name -> (Popen, log file, t0)
    last_start = 0.0
    try:
        while todo or running:
            for n, (p, lf, t0) in list(running.items()):
                if p.poll() is not None:
                    lf.close()
                    say(n, "rc", p.returncode, f"{time.time() - t0:.0f} s")
                    del running[n]
            if todo and len(running) < a.jobs and time.time() - last_start >= (a.stagger if running else 0):
                n = todo.pop(0)
                os.makedirs(os.path.join(outdir, n), exist_ok=True)
                lf = open(os.path.join(outdir, n, "log.txt"), "a", encoding="utf-8")
                cmd = [sys.executable, "-m", "studies.lere.run_set", n, "--out", outdir] + passthru
                lf.write(" ".join(cmd) + "\n"); lf.flush()
                running[n] = (subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, cwd=REPO, env=env), lf,
                              time.time())
                last_start = time.time()
                say(n, "started", f"({len(running)} running, {len(todo)} waiting)")
                continue
            if n0 is not None:
                c = py_count()
                if c is not None and c > n0 + a.jobs + 6:
                    say(f"STOP: python processes {c} (start {n0}); killing", list(running))
                    for p, lf, _ in running.values():
                        p.kill()
                    return 1
            time.sleep(10)
    except KeyboardInterrupt:
        say("interrupted; killing", list(running))
        for p, lf, _ in running.values():
            p.kill()
        raise
    say("queue done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
