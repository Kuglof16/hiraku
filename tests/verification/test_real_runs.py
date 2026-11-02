"""qa.check_run on real run records.

1. The scripted run (`python -m hiraku run --scripted`, no API key) is made in
   a temporary folder and must have no errors.
2. Every folder already under runs/ in this checkout is checked too. runs/ is
   not committed, so this part only does something on a machine that has runs.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hiraku.contract import layout
from qa import check_run as cr

ROOT = Path(__file__).resolve().parents[2]


def _errors(run: Path) -> list[str]:
    return [str(f) for f in cr.check_run(run, recompute=True) if f.level == "error"]


class ScriptedRun(unittest.TestCase):
    def test_scripted_run_follows_the_contract(self):
        if not (ROOT / "hiraku" / "__main__.py").exists():
            self.skipTest("hiraku/__main__.py is not in this checkout yet (design dept): "
                          "needs `python -m hiraku run --scripted`")
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, PYTHONPATH=str(ROOT))
            env.pop("ANTHROPIC_API_KEY", None)  # the scripted run must not need it
            proc = subprocess.run([sys.executable, "-m", "hiraku", "run", "--scripted"], cwd=tmp, env=env,
                                  capture_output=True, text=True, encoding="utf-8", timeout=600)
            self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
            runs = sorted((Path(tmp) / layout.RUNS_DIR).glob("*/"))
            self.assertTrue(runs, f"no run folder under {tmp}/runs; stdout: {proc.stdout[-1000:]}")
            for run in runs:
                with self.subTest(run=run.name):
                    self.assertEqual(_errors(run), [])


class SavedRuns(unittest.TestCase):
    def test_saved_runs_follow_the_contract(self):
        runs = sorted(p for p in (ROOT / layout.RUNS_DIR).glob("*/") if p.is_dir())
        if not runs:
            self.skipTest("no saved runs under runs/ in this checkout")
        for run in runs:
            with self.subTest(run=run.name):
                self.assertEqual(_errors(run), [])
