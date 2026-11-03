"""qa.check_run on real run records.

1. The scripted run from the command line (`python -m hiraku run <requirements>
   --scripted --runs <tmp>`, no API key), once the solvers are in. One class per
   requirements file (ScriptedRun_<file stem>), so python -m qa.run_tests runs
   them in parallel. With HIRAKU_SLOW_TESTS=1 the video requirements' class makes
   a second run at the same time as its first, and the two runs are compared
   byte for byte (qa.compare_runs).
2. The scripted plan driven with the design dept's fake solvers
   (tests/tools/fakes.py), so the store's output is checked before the real
   solvers land: a run that ends done, one stopped by a mismatch, one stopped
   by HK2.
3. The assembly tool's assembly and interferences on the demo design, through
   check_run's rules for them (no run needed). With HIRAKU_SLOW_TESTS=1, each
   tool that needs_mesh solved on the demo design's mesh, one class per tool
   (MeshFieldRecord_<tool name>), so python -m qa.run_tests runs them in parallel.
4. Every example run committed under showcase/ (runs/ is local and is checked by
   hand with python -m qa.check_run --all).
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from hiraku import contract
from hiraku.contract import layout
from qa import check_run as cr
from qa import compare_runs, procs

from ._solvers import tool_or_skip

ROOT = Path(__file__).resolve().parents[2]
SLOW = os.environ.get("HIRAKU_SLOW_TESTS") == "1"  # tests that add whole scripted runs or whole-frame solves
# every requirements file with HIRAKU_SLOW_TESTS=1; otherwise the video requirements only (one run)
REQUIREMENTS = [r for r in sorted((ROOT / "data" / "requirements").glob("*.yaml"))
                if SLOW or r.name == "offroad-4w-video.yaml"]
CLAUDE_ONLY = ".claude.yaml"  # requirements only Claude can run (no scripted way to predict a load case)
NO_SCRIPT = "台本の予測のしかたが無いので回せない荷重条件"  # what the scripted run says for them
VIDEO = "offroad-4w-video.yaml"  # the requirements whose run is also made twice and compared
# Subprocess timeout of one scripted run. The video run alone takes about 680 s (600 s
# erred with TimeoutExpired); this stays below qa.run_tests' 1800 s per-class timeout.
RUN_TIMEOUT_S = 1200
DEMO_DESIGN = ROOT / "data" / "designs" / "offroad-4w-v1.json"


def _errors(run: Path, recompute: bool = True) -> list[str]:
    return [str(f) for f in cr.check_run(run, recompute=recompute) if f.level == "error"]


def _class_name(prefix: str, name: str) -> str:
    return prefix + re.sub(r"\W", "_", name)


def _scripted_run(req: Path, out: Path):
    """One `python -m hiraku run REQ --scripted --runs OUT`; (run folder or None, process, seconds).
    A run still going after RUN_TIMEOUT_S is stopped with everything it started (qa.procs) and
    gives no run folder, so its tests fail with the seconds instead of the class erring."""
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONUTF8="1")  # Japanese status lines
    env.pop("ANTHROPIC_API_KEY", None)  # the scripted run must not need it
    cmd = [sys.executable, "-m", "hiraku", "run", str(req), "--scripted", "--runs", str(out)]
    t0 = time.perf_counter()
    try:
        proc = procs.run(cmd, cwd=ROOT, env=env, text=True, encoding="utf-8", errors="replace", timeout=RUN_TIMEOUT_S)
    except subprocess.TimeoutExpired as e:
        proc = subprocess.CompletedProcess(cmd, None, e.output or "",
                                           (e.stderr or "") + f"\nno result within {RUN_TIMEOUT_S} s (stopped)")
    finally:
        seconds = time.perf_counter() - t0
        print(f"scripted run {req.name}: {seconds:.0f} s", file=sys.stderr)
    folders = sorted(p for p in out.iterdir() if p.is_dir()) if out.is_dir() else []
    return (folders[0] if len(folders) == 1 else None), proc, seconds


def _scripted_run_again(req: Path, out: Path):
    """The second run for the determinism check; (run folder or the RuntimeError, seconds)."""
    t0 = time.perf_counter()
    try:
        again = compare_runs.scripted_run(req, out, timeout=RUN_TIMEOUT_S)
    except RuntimeError as e:
        again = e
    finally:
        seconds = time.perf_counter() - t0
        print(f"scripted run {req.name} (again): {seconds:.0f} s", file=sys.stderr)
    return again, seconds


class _ScriptedRunTests:
    """One scripted run of REQ, made once, with the real solvers (mixed into a TestCase per file)."""

    REQ: Path = None  # the requirements file

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        try:
            cls._make_runs()
        except BaseException:
            cls._tmp.cleanup()
            raise
        if cls.proc.returncode == 2 and "cannot start the run" in cls.proc.stderr:
            cls._tmp.cleanup()
            raise unittest.SkipTest(f"scripted run cannot start yet: {cls.proc.stderr.strip()[-300:]}")

    @classmethod
    def _make_runs(cls):
        # run_dir, not run: an attribute named run would hide TestCase.run
        cls.run_dir, cls.proc, cls.seconds = _scripted_run(cls.REQ, Path(cls._tmp.name) / cls.REQ.stem)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _where(self):
        return self.subTest(requirements=self.REQ.name, run_s=round(self.seconds))

    def assertMadeRun(self, run, proc):
        self.assertIsNotNone(run, f"expected one run folder ({self.REQ.name}, {self.seconds:.0f} s); "
                                  f"stdout: {proc.stdout[-800:]} stderr: {proc.stderr[-800:]}")

    def test_records_follow_the_contract(self):
        run, proc = self.run_dir, self.proc
        with self._where():
            self.assertMadeRun(run, proc)
            self.assertEqual(_errors(run), [], f"{run.name} ({self.REQ.name}, {self.seconds:.0f} s)")

    def test_mvp_scripted_run_goes_through(self):
        # fixed requirements -> prediction -> step climb -> frame -> comparison -> judgement, ending done.
        # A run with load cases past the limit (load_case_id ending -beyond, whose verdicts are meant to
        # fail) does not end done: it stops at round_limit, or not_judged while a load case is left
        # without a verdict. A *.claude.yaml file is a scene only Claude can run (no tool or scripted
        # step for its load cases): the scripted run says so and stops not_judged.
        name, run, proc = self.REQ.name, self.run_dir, self.proc
        with self._where():
            self.assertMadeRun(run, proc)
            project =json.loads((run / layout.PROJECT_FILE).read_text(encoding="utf-8"))
            with (run / layout.EVENTS_FILE).open(encoding="utf-8") as f:
                events = [json.loads(s) for s in f if s.strip()]
            fin = [e for e in events if e["kind"] == "run.finished"]
            self.assertEqual(len(fin), 1, f"stdout: {proc.stdout[-800:]}")
            why = f"{project.get('stop_reason')}; stdout: {proc.stdout[-800:]}"
            if name.endswith(CLAUDE_ONLY):
                self.assertEqual((project["status"], fin[0]["data"].get("stop_code")), ("stopped", "not_judged"), why)
                said = [e.get("text", "") for e in events if e["kind"] == "agent.said"]
                self.assertTrue(any(NO_SCRIPT in s for s in said), f"no agent.said with {NO_SCRIPT!r}; {why}")
            elif any(lc["load_case_id"].endswith("-beyond") for lc in project["load_cases"]):
                self.assertEqual(project["status"], "stopped", why)
                self.assertIn(fin[0]["data"].get("stop_code"), ("not_judged", "round_limit"), why)
            else:
                self.assertEqual(project["status"], "done", why)

    def test_each_denial_is_followed_as_the_hook_asks(self):
        name, run, proc = self.REQ.name, self.run_dir, self.proc
        with self._where():
            self.assertMadeRun(run, proc)
            lines =cr.run_effects(run)
            denials = [s for s in lines if "_prediction_first" in s or "_cause_before_continue" in s]
            if not name.endswith(CLAUDE_ONLY):  # the scripted plan stages denials; a Claude-only file stops first
                self.assertTrue(denials, "\n".join(lines))
            self.assertEqual([s for s in denials if "not the" in s or "nothing recorded" in s], [], "\n".join(lines))


class _ScriptedRunTwice(_ScriptedRunTests):
    """The video requirements' class: with HIRAKU_SLOW_TESTS=1 a second run is made at the
    same time as the first (two threads) and the two are compared."""

    @classmethod
    def _make_runs(cls):
        cls.again, cls.again_seconds = None, 0.0  # the second run (HIRAKU_SLOW_TESTS=1)
        if not SLOW:
            super()._make_runs()
            return
        tmp = Path(cls._tmp.name)
        with ThreadPoolExecutor(max_workers=2) as pool:  # leaving the block waits for both runs
            first = pool.submit(_scripted_run, cls.REQ, tmp / cls.REQ.stem)
            second = pool.submit(_scripted_run_again, cls.REQ, tmp / "again")
        cls.run_dir, cls.proc, cls.seconds = first.result()
        cls.again, cls.again_seconds = second.result()
        if cls.run_dir is None:  # nothing to repeat when the first run made no run folder
            cls.again = None

    def test_scripted_run_gives_the_same_records_twice(self):
        # Byte for byte apart from times, run id and cost (the modal tool starts its eigen
        # solver from a fixed vector and signs its mode shapes). One more scripted run, so
        # only with HIRAKU_SLOW_TESTS=1 (qa/README.md says when).
        if not SLOW:
            self.skipTest("a second scripted run: set HIRAKU_SLOW_TESTS=1, or run "
                          "python -m qa.compare_runs --twice data/requirements/offroad-4w-video.yaml")
        if self.again is None:
            self.skipTest("no run of offroad-4w-video.yaml to repeat")
        with self.subTest(requirements=self.REQ.name, run_s=round(self.seconds), again_s=round(self.again_seconds)):
            self.assertNotIsInstance(self.again, RuntimeError, str(self.again))
            r = compare_runs.compare(self.run_dir, self.again)
            self.assertTrue(r.same, "\n".join(r.lines + [r.summary()]))


if (ROOT / "hiraku" / "__main__.py").exists() and REQUIREMENTS:
    for _req in REQUIREMENTS:
        _name = _class_name("ScriptedRun_", _req.stem)
        globals()[_name] = type(_name, (_ScriptedRunTwice if _req.name == VIDEO else _ScriptedRunTests,
                                        unittest.TestCase),
                                {"REQ": _req, "__module__": __name__,
                                 "__doc__": f"The scripted run of data/requirements/{_req.name}."})
    del _req, _name
else:
    class ScriptedRunCommand(_ScriptedRunTwice, unittest.TestCase):
        """Stands for the scripted-run classes while hiraku/__main__.py or the requirements are missing."""

        @classmethod
        def setUpClass(cls):
            raise unittest.SkipTest("needs hiraku/__main__.py and data/requirements/*.yaml (design dept)")


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


MESH_TOOLS = []  # the tools that needs_mesh, listed only with HIRAKU_SLOW_TESTS=1 and a frame_mesh()
if SLOW:
    try:
        import hiraku.solvers as _solvers
    except ImportError:
        _solvers = None
    if _solvers is not None and callable(getattr(_solvers, "frame_mesh", None)):
        MESH_TOOLS = [t for t in _solvers.builtin_tools() if getattr(t, "needs_mesh", False)]


class _MeshFieldTests:
    """A tool that needs_mesh (TOOL), given the demo design's frame_mesh as params["mesh"]: the
    mesh and the tool's analysis pass check_run's rules for meshes and the fields on
    them (one value per element or node of a mesh created before, of the same version).
    Mixed into a TestCase per tool."""

    TOOL = None

    @classmethod
    def setUpClass(cls):
        import hiraku.solvers as solvers
        cls.design = json.loads(DEMO_DESIGN.read_text(encoding="utf-8"))
        cls.mesh = dict(solvers.frame_mesh(cls.design), mesh_id="mesh-0001")

    def test_each_mesh_tool_on_the_demo_mesh(self):
        design, mesh, tool = self.design, self.mesh, self.TOOL
        lc = {"load_case_id": "R1-step100", "test_id": "R1", "domain": "strength", "title": "R1",
              "params": {"step_height": {"value": 100.0, "unit": "mm"}}, "tolerance_rel": 0.15}
        with self.subTest(tool=tool.name):
            out = tool.run(design, lc, [{"prediction_id": "pred-0001"}], {"mesh": mesh})
            self.assertEqual(out.get("status"), "ok", out.get("error"))
            self.assertTrue(any(f.get("mesh_id") == "mesh-0001" for f in out.get("fields", [])), "no field on the mesh")
            rec = dict(out, analysis_id="ana-0001", prediction_ids=["pred-0001"])
            checker = cr.RunChecker(ROOT)
            checker.where_of = ["mesh.created", "analysis.finished"]
            checker.designs[design["design_version"]] = (-1, design)
            checker.recs["mesh"]["mesh-0001"] = (0, mesh)
            checker.recs["analysis"]["ana-0001"] = (1, rec)
            checker.check_meshes()
            self.assertEqual([str(f) for f in checker.findings if f.level == "error"], [],
                             "\n".join(str(f) for f in checker.findings))
            self.assertEqual(contract.errors("analysis", rec), [])


if MESH_TOOLS:
    for _tool in MESH_TOOLS:
        _name = _class_name("MeshFieldRecord_", _tool.name)
        globals()[_name] = type(_name, (_MeshFieldTests, unittest.TestCase),
                                {"TOOL": _tool, "__module__": __name__,
                                 "__doc__": f"The needs_mesh tool {_tool.name} on the demo design's mesh."})
    del _tool, _name
else:
    class MeshFieldRecord(unittest.TestCase):
        """Stands for the per-tool mesh classes while they cannot run; says why in its skip."""

        def test_each_mesh_tool_on_the_demo_mesh(self):
            if not SLOW:
                self.skipTest("whole-frame shell solves: set HIRAKU_SLOW_TESTS=1")
            import hiraku.solvers as solvers
            if not callable(getattr(solvers, "frame_mesh", None)):
                self.skipTest("hiraku.solvers.frame_mesh() is not there yet (analysis dept)")
            self.skipTest("no tool with needs_mesh yet (analysis dept)")


class SavedRuns(unittest.TestCase):
    """The example runs committed under showcase/<run_id>/. runs/ is local and not
    committed, so it is not checked here (python -m qa.check_run --all does that by hand)."""

    def test_showcase_runs_follow_the_contract(self):
        runs = sorted(p for p in (ROOT / layout.SHOWCASE_DIR).glob("*/") if p.is_dir())
        if not runs:
            self.skipTest(f"no runs under {layout.SHOWCASE_DIR.as_posix()}/ yet")
        for run in runs:
            with self.subTest(run=run.name):
                self.assertEqual(_errors(run), [])
