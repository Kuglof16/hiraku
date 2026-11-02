"""qa.check_run on real run records.

1. The scripted run from the command line (`python -m hiraku run <requirements>
   --scripted --runs <tmp>`, no API key), once the solvers are in.
2. The scripted plan driven with the design dept's fake solvers
   (tests/tools/fakes.py), so the store's output is checked before the real
   solvers land: a run that ends done, one stopped by a mismatch, one stopped
   by HK2.
3. The assembly tool's assembly and interferences on the demo design, through
   check_run's rules for them (no run needed).
4. Every folder already under runs/ in this checkout. runs/ is not committed,
   so this part only does something on a machine that has runs.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hiraku import contract
from hiraku.contract import layout
from qa import check_run as cr

from ._solvers import tool_or_skip

ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS = sorted((ROOT / "data" / "requirements").glob("*.yaml"))


def _errors(run: Path, recompute: bool = True) -> list[str]:
    return [str(f) for f in cr.check_run(run, recompute=recompute) if f.level == "error"]


class ScriptedRunCommand(unittest.TestCase):
    """One scripted run per requirements file, made once, with the real solvers."""

    @classmethod
    def setUpClass(cls):
        if not (ROOT / "hiraku" / "__main__.py").exists() or not REQUIREMENTS:
            raise unittest.SkipTest("needs hiraku/__main__.py and data/requirements/*.yaml (design dept)")
        cls._tmp = tempfile.TemporaryDirectory()
        cls.runs = {}  # requirements file name -> (run folder, process)
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONUTF8="1")  # Japanese status lines
        env.pop("ANTHROPIC_API_KEY", None)  # the scripted run must not need it
        for req in REQUIREMENTS:
            out = Path(cls._tmp.name) / req.stem
            proc = subprocess.run([sys.executable, "-m", "hiraku", "run", str(req), "--scripted", "--runs", str(out)],
                                  cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=600)
            if proc.returncode == 2 and "cannot start the run" in proc.stderr:
                cls._tmp.cleanup()
                raise unittest.SkipTest(f"scripted run cannot start yet: {proc.stderr.strip()[-300:]}")
            folders = sorted(p for p in out.iterdir() if p.is_dir()) if out.is_dir() else []
            cls.runs[req.name] = (folders[0] if len(folders) == 1 else None, proc)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def assertMadeRun(self, run, proc):
        self.assertIsNotNone(run, f"expected one run folder; stdout: {proc.stdout[-800:]} stderr: {proc.stderr[-800:]}")

    def test_records_follow_the_contract(self):
        for name, (run, proc) in self.runs.items():
            with self.subTest(requirements=name):
                self.assertMadeRun(run, proc)
                self.assertEqual(_errors(run), [], run.name)

    def test_mvp_scripted_run_goes_through(self):
        # fixed requirements -> prediction -> step climb -> frame -> comparison -> judgement, ending done
        for name, (run, proc) in self.runs.items():
            with self.subTest(requirements=name):
                self.assertMadeRun(run, proc)
                project = json.loads((run / layout.PROJECT_FILE).read_text(encoding="utf-8"))
                self.assertEqual(project["status"], "done", f"{project.get('stop_reason')}; stdout: {proc.stdout[-800:]}")

    def test_each_denial_is_followed_as_the_hook_asks(self):
        for name, (run, proc) in self.runs.items():
            with self.subTest(requirements=name):
                self.assertMadeRun(run, proc)
                lines = cr.run_effects(run)
                denials = [s for s in lines if "_prediction_first" in s or "_cause_before_continue" in s]
                self.assertTrue(denials, "\n".join(lines))
                self.assertEqual([s for s in denials if "not the" in s or "nothing recorded" in s], [], "\n".join(lines))


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
                design = contract.EXAMPLES_DIR / "design.json"
                if callable(getattr(fakes, "loaded_design", None)):  # the design the fakes are made for
                    design = Path(tmp) / "design.json"
                    design.write_text(json.dumps(fakes.loaded_design()), encoding="utf-8")
                ctx = start_run(req, design_path=design, root=Path(tmp),
                                slug=f"qa-{name}", formulas=fakes.FakeFormulas(),
                                tools=fakes.shelf(step, frame, checks_pass))
                run_scripted(ctx)
                self.assertEqual(_errors(ctx.store.path, recompute=False), [])  # fake formulas


class AssemblyToolRecord(unittest.TestCase):
    """The assembly tool's own `assembly` and `interferences` on the demo design pass
    check_run's rules for them (names in the design, steps 1..n, counts agree)."""

    def test_assembly_record_on_the_demo_design(self):
        tool = tool_or_skip("assembly")
        path = ROOT / "data" / "designs" / "offroad-4w-v1.json"
        if not path.exists():
            self.skipTest("needs data/designs/offroad-4w-v1.json (design dept)")
        design = json.loads(path.read_text(encoding="utf-8"))
        lc = json.loads((contract.EXAMPLES_DIR / "load_case.json").read_text(encoding="utf-8"))
        lc.update(load_case_id="H1", test_id="H1", domain="assembly", params={"loading": "two_cargo"})
        pred = json.loads((contract.EXAMPLES_DIR / "prediction.json").read_text(encoding="utf-8"))
        pred.update(load_case_id="H1", test_id="H1", domain="assembly")
        out = tool.run(design, lc, [pred], {})
        self.assertEqual(out.get("status"), "ok", out.get("error"))
        self.assertIn("assembly", out)
        self.assertIn("interferences", out)
        checker = cr.RunChecker(ROOT)
        checker.where_of = ["assembly analysis"]
        checker.designs[design["design_version"]] = (0, design)
        checker.recs["analysis"]["ana-0001"] = (0, dict(out, analysis_id="ana-0001", design_version=design["design_version"]))
        checker.check_assembly()
        self.assertEqual([str(f) for f in checker.findings], [])


class SavedRuns(unittest.TestCase):
    def test_saved_runs_follow_the_contract(self):
        runs = sorted(p for p in (ROOT / layout.RUNS_DIR).glob("*/") if p.is_dir())
        if not runs:
            self.skipTest("no saved runs under runs/ in this checkout")
        for run in runs:
            with self.subTest(run=run.name):
                self.assertEqual(_errors(run), [])
