"""Run a heavy job on this PC only when no test suite or other heavy job is running.

    python -m qa.slot <what> -- <command> [args...]
    python -m qa.slot "video export" -- python -m hiraku export runs/<run>
    python -m qa.slot "K2 convergence" --no-wait -- python -m unittest tests.solvers.test_fe_tubes

Takes the same machine-wide lock as `python -m qa.run_tests` (an OS lock on
hiraku-run-tests/suite.lock in the system temp folder). While a suite or another
qa.slot job holds it, it waits and prints "waiting for <pid> (<what>, <worktree>,
since hh:mm)"; a suite started meanwhile waits for this job the same way. The
command then runs with this console's input and output. The lock is the lock file
held open by this process, so the OS frees it whenever this process ends: the
command finished, failed, or was cancelled (Ctrl+C stops the command and every
process it started, qa.procs.kill_tree). --no-wait: exit 3 at once when the lock
is held, without running the command.

Exit code: the command's (130 when cancelled), 3 when --no-wait found the lock
held, 2 for a wrong call.
"""

from __future__ import annotations

import os
import subprocess
import sys

from qa import procs, run_tests

BUSY = 3


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if "--" not in argv:
        print(__doc__.split("\n\n")[0] + "\nusage: python -m qa.slot <what> [--no-wait] -- <command> [args...]",
              file=sys.stderr)
        return 2
    cut = argv.index("--")
    head, cmd = argv[:cut], argv[cut + 1:]
    wait = "--no-wait" not in head
    what = " ".join(a for a in head if a != "--no-wait").strip()
    if not what or not cmd:
        print("usage: python -m qa.slot <what> [--no-wait] -- <command> [args...]", file=sys.stderr)
        return 2
    lock = run_tests.take_queue(0, wait=wait, say=lambda *a, **k: print(*a, file=sys.stderr, **k), what=what)
    if lock is None:
        print(f"busy: a suite or another heavy job holds the lock ({run_tests.LOCK_DIR / run_tests.QUEUE_INFO})",
              file=sys.stderr)
        return BUSY
    try:
        print(f"qa.slot: {what}: running {' '.join(cmd)}", file=sys.stderr, flush=True)
        kw = {} if os.name == "nt" else {"start_new_session": True}  # its own group, so kill_tree reaches all
        proc = subprocess.Popen(cmd, **kw)
        try:
            return proc.wait()
        except KeyboardInterrupt:
            procs.kill_tree(proc)
            proc.wait()
            print(f"qa.slot: {what}: cancelled", file=sys.stderr)
            return 130
    finally:
        lock.close()  # the OS lock goes with the file


if __name__ == "__main__":
    sys.exit(main())
