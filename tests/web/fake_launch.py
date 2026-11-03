"""A stand-in for `python -m hiraku run` in tests of starting a run from the screen:
the child copies an events file into a new run folder, prints the folder like
the real command does, and stays up for a while (as a run that is still going).

With `decoy`, the child first makes another run's folder (as a run started at the same
time from the command line would) and waits a little before making and printing its own."""

from __future__ import annotations

import sys
from pathlib import Path

_CODE = r"""
import pathlib, shutil, sys, time
src, runs, run_id, hold, decoy = sys.argv[1], pathlib.Path(sys.argv[2]), sys.argv[3], float(sys.argv[4]), sys.argv[5]
if decoy:
    other = runs / decoy
    other.mkdir(parents=True)
    shutil.copyfile(src, other / "events.jsonl")
    time.sleep(0.5)
d = runs / run_id
d.mkdir(parents=True)
shutil.copyfile(src, d / "events.jsonl")
print("run folder:", d, flush=True)
time.sleep(hold)
"""


def fake_command(src: Path, run_id: str, hold_s: float = 1.0, decoy: str = ""):
    """A command factory for Launcher(command=...); a Claude run gets the id + "-claude"."""

    def command(requirements: Path, mode: str, runs_dir: Path) -> list[str]:
        rid = run_id + ("-claude" if mode == "claude" else "")
        return [sys.executable, "-c", _CODE, str(src), str(runs_dir), rid, str(hold_s), decoy]

    return command


def fake_reader(path: Path) -> dict:
    return {"product": "offroad-4w", "want": "見本の要件", "cost_limit_usd": 2.0, "round_limit": 1}
