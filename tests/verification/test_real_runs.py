"""qa.check_run on real run records.

1. The scripted run from the command line (`python -m hiraku run <requirements>
   --scripted --runs <tmp>`, no API key), once the solvers are in.
2. The scripted plan driven with the design dept's fake solvers
   (tests/tools/fakes.py), so the store's output is checked before the real
   solvers land: a run that ends done, one stopped by a mismatch, one stopped
   by H2.
3. Every folder already under runs/ in this checkout. runs/ is not committed,
   so this part only does something on a machine that has runs.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hiraku import contract
from hiraku.contract import layout
from qa import check_run as cr

ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS = sorted((ROOT / "data" / "requirements").glob("*.yaml"))


def _errors(run: Path) -> list[str]:
    return [str(f) for f in cr.check_run(run, recompute=True) if f.level == "error"]


class ScriptedRunCommand(unittest.TestCase):
    def test_scripted_run_follows_the_contract(self):
        if not (ROOT / "hiraku" / "__main__.py").exists() or not REQUIREMENTS:
            self.skipTest("needs hiraku/__main__.py and data/requirements/*.yaml (design dept)")
        for req in REQUIREMENTS:
            with self.subTest(requirements=req.name), tempfile.TemporaryDirectory() as tmp:
                env = dict(os.environ, PYTHONPATH=str(ROOT))
                env.pop("ANTHROPIC_API_KEY", None)  # the scripted run must not need it
                proc = subprocess.run([sys.executable, "-m", "hiraku", "run", str(req), "--scripted", "--runs", tmp],
                                      cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
                                      timeout=600)
                if proc.returncode == 2 and "cannot start the run" in proc.stderr:
                    self.skipTest(f"scripted run cannot start yet: {proc.stderr.strip()[-300:]}")
                runs = sorted(p for p in Path(tmp).iterdir() if p.is_dir())
                self.assertTrue(runs, f"no run folder in {tmp}; stdout: {proc.stdout[-1000:]} {proc.stderr[-1000:]}")
                for run in runs:
                    self.assertEqual(_errors(run), [], run.name)


class ScriptedRunWithFakes(unittest.TestCase):
    CASES = {"done": (1.03, 1.03, True), "mismatch": (1.03, 1.4, True), "unusable": (1.03, 1.03, False)}

    def test_store_output_follows_the_contract(self):
        try:
            from hiraku.agent.scripted import run_scripted
            from hiraku.agent.session import start_run
            from tests.tools import fakes
        except ImportError as e:
            self.skipTest(f"needs hiraku.agent and tests/tools/fakes.py (design dept): {e}")
        req = ROOT / "data" / "requirements" / "offroad-4w.yaml"
        for name, (step, frame, checks_pass) in self.CASES.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                ctx = start_run(req, design_path=contract.EXAMPLES_DIR / "design.json", root=Path(tmp),
                                slug=f"qa-{name}", formulas=fakes.FakeFormulas(),
                                tools=fakes.shelf(step, frame, checks_pass))
                run_scripted(ctx)
                self.assertEqual(_errors(ctx.store.path), [])


class SavedRuns(unittest.TestCase):
    def test_saved_runs_follow_the_contract(self):
        runs = sorted(p for p in (ROOT / layout.RUNS_DIR).glob("*/") if p.is_dir())
        if not runs:
            self.skipTest("no saved runs under runs/ in this checkout")
        for run in runs:
            with self.subTest(run=run.name):
                self.assertEqual(_errors(run), [])
