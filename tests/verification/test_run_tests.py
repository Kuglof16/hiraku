"""qa.run_tests: discovery by class, one worker's record, the class-skip line, and the rerun
alone of classes that ran out of memory; qa.procs: a timeout stops the whole process tree."""

import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from qa import procs
from qa import run_tests as rt


class RunTests(unittest.TestCase):
    def test_discovery_groups_by_class(self):
        groups = rt.discover()
        mine = groups.get(f"{__name__}.RunTests")
        self.assertIsNotNone(mine)
        self.assertIn(f"{__name__}.RunTests.test_discovery_groups_by_class", mine)
        self.assertTrue(all(i.startswith(name) or "_FailedTest" in i for name, ids in groups.items() for i in ids))

    def test_worker_writes_what_ran(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "w.json"
            rt.worker(out, ["tests.verification.test_compare_runs.CompareRuns.test_newest_baseline"])
            rec = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(rec["ran"], 1)
        self.assertEqual(rec["ids_run"], ["tests.verification.test_compare_runs.CompareRuns.test_newest_baseline"])
        self.assertEqual((rec["failures"], rec["errors"], rec["skipped"]), ([], [], []))

    def test_worker_keeps_a_failure_text_the_console_cannot_hold(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "qa_tmp_dash.py").write_text(
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_dash(self):\n"
                "        self.fail('a dash \\u2014 and a check \\u2713')\n", encoding="utf-8")
            sys.path.insert(0, tmp)
            try:
                out = Path(tmp) / "w.json"
                rt.worker(out, ["qa_tmp_dash.T"])
                rec = json.loads(out.read_text(encoding="utf-8"))
            finally:
                sys.path.remove(tmp)
                sys.modules.pop("qa_tmp_dash", None)
        self.assertEqual(rec["ran"], 1)
        self.assertIn("\u2014", rec["failures"][0]["last"])

    def test_a_class_skipped_in_setupclass_is_named(self):
        def skip(cls):
            raise unittest.SkipTest("not here")
        cls = type("Skipped", (unittest.TestCase,), {"setUpClass": classmethod(skip), "test_a": lambda self: None,
                                                    "__module__": "tests.somewhere"})
        res = unittest.TextTestRunner(stream=io.StringIO()).run(unittest.TestSuite([cls("test_a")]))
        self.assertEqual(res.testsRun, 0)
        m = rt.CLASS_SKIP_RE.match(str(res.skipped[0][0]))
        self.assertEqual(m.group(1) if m else None, "tests.somewhere.Skipped")

    def test_heavy_classes_start_first_without_times(self):
        groups = {"tests.store.test_x.A": ["i"] * 30, "tests.solvers.k2.test_y.B": ["j"],
                  "tests.verification.test_real_runs.ScriptedRunCommand": ["k"]}
        saved, rt.TIMES = rt.TIMES, Path(tempfile.gettempdir()) / "no-such-times.json"
        try:
            order = rt._order(groups)
        finally:
            rt.TIMES = saved
        self.assertEqual(order[-1], "tests.store.test_x.A")

    @staticmethod
    def _failed(name, kind, tb):
        return {"name": name, "s": 1.0, "ran": 2, "ids_run": [f"{name}.test_a", f"{name}.test_b"],
                "failures": [{"test": f"test_a ({name})", "kind": "FAIL", "last": tb, "traceback": tb}] if kind == "FAIL" else [],
                "errors": [{"test": f"test_a ({name})", "kind": "ERROR", "last": tb, "traceback": tb}] if kind == "ERROR" else [],
                "skipped": []}

    def test_memory_errors_are_told_apart(self):
        numpy = "numpy._core._exceptions._ArrayMemoryError: Unable to allocate 2.15 MiB for an array"
        self.assertTrue(rt._out_of_memory(self._failed("m.A", "ERROR", numpy)))
        self.assertTrue(rt._out_of_memory(self._failed("m.B", "ERROR", "MemoryError")))
        self.assertTrue(rt._out_of_memory(self._failed("m.C", "ERROR", "OSError: [WinError 1455] (paging file)")))
        self.assertTrue(rt._out_of_memory({"name": "m.D", "s": 1.0, "crash": "exit 1: ... | MemoryError"}))
        self.assertTrue(rt._out_of_memory({"name": "m.D2", "s": 1.0, "crash": "exit 3221225773: "}))  # 0xC000012D
        self.assertTrue(rt._out_of_memory({"name": "m.D3", "s": 1.0, "crash": "exit 1: OpenBLAS error: Memory "
                                                                            "allocation still failed after 10 retries"}))
        self.assertFalse(rt._out_of_memory({"name": "m.D4", "s": 1.0, "crash": "exit 1: 32212257730 lines"}))
        self.assertFalse(rt._out_of_memory(self._failed("m.E", "FAIL", "AssertionError: 10 != 9")))
        self.assertFalse(rt._out_of_memory(self._failed("m.F", "ERROR", "OSError: [WinError 5] Access is denied")))
        self.assertFalse(rt._out_of_memory({"name": "m.G", "s": 1.0, "ran": 1, "failures": [], "errors": []}))

    def test_only_classes_out_of_memory_are_rerun_alone(self):
        results = [self._failed("m.Oom", "ERROR", "_ArrayMemoryError: Unable to allocate"),
                   self._failed("m.Real", "FAIL", "AssertionError: 10 != 9"),
                   self._failed("m.Still", "ERROR", "OSError: [WinError 1455]"),
                   {"name": "m.Ok", "s": 1.0, "ran": 1, "ids_run": ["m.Ok.test_a"], "failures": [], "errors": [], "skipped": []}]
        calls = []

        def run(name, tmp, timeout, tag=""):
            calls.append((name, tag))
            if name == "m.Still":  # out of memory even alone: stays an error
                return self._failed(name, "ERROR", "MemoryError")
            return {"name": name, "s": 3.0, "ran": 2, "ids_run": [f"{name}.test_a", f"{name}.test_b"],
                    "failures": [], "errors": [], "skipped": []}

        again = rt._rerun_alone(results, Path(tempfile.gettempdir()), 60.0, run=run)
        self.assertEqual(calls, [("m.Oom", ".alone"), ("m.Still", ".alone")])
        self.assertEqual([r["name"] for r in again], ["m.Oom", "m.Still"])
        self.assertEqual([r["name"] for r in results], ["m.Oom", "m.Real", "m.Still", "m.Ok"])  # replaced in place
        self.assertEqual((results[0]["errors"], results[0]["s"], results[0]["rerun_alone"]), ([], 3.0, True))
        self.assertEqual(len(results[1]["failures"]), 1)  # any other failure stays
        self.assertNotIn("rerun_alone", results[1])
        self.assertEqual([rt._verdict(r) for r in results], ["OK", "FAILED (failures=1)", "FAILED (errors=1)", "OK"])
        self.assertEqual(sum(r["ran"] for r in results), 7)  # each class counted once


def _alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                             errors="replace").stdout
        return str(pid) in out.split()
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class OtherSuites(unittest.TestCase):
    """The lock files that tell one qa.run_tests about another on the same PC."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.saved, rt.LOCK_DIR = rt.LOCK_DIR, Path(self._tmp.name)

    def tearDown(self):
        rt.LOCK_DIR = self.saved
        self._tmp.cleanup()

    def test_alive(self):
        self.assertTrue(rt._alive(os.getpid()))
        ended = subprocess.Popen([sys.executable, "-c", "pass"])
        ended.wait()
        self.assertFalse(rt._alive(ended.pid))

    def test_a_running_suite_is_reported_and_an_ended_one_forgotten(self):
        running = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        ended = subprocess.Popen([sys.executable, "-c", "pass"])
        ended.wait()
        try:
            for proc in (running, ended):
                (rt.LOCK_DIR / f"{proc.pid}.json").write_text(json.dumps(
                    {"pid": proc.pid, "root": "elsewhere", "jobs": 8, "slow": False, "started": "17:04"}), encoding="utf-8")
            (rt.LOCK_DIR / f"{os.getpid()}.json").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
            others = rt._others()
            self.assertEqual([o["pid"] for o in others], [running.pid])  # not this process itself
            self.assertFalse((rt.LOCK_DIR / f"{ended.pid}.json").exists())  # the ended run's file is removed
            self.assertIn(f"pid {running.pid}, since 17:04, 8 processes, in elsewhere", rt._overlap(others[0]))
        finally:
            procs.kill_tree(running)
            running.wait()


class Procs(unittest.TestCase):
    """qa.procs.run stops the command and what it started when the time runs out (subprocess.run
    stops only the command itself, and a process the command started keeps running)."""

    def test_a_grandchild_is_stopped_on_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "grandchild.pid"
            grandchild = f"import os, time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(120)"
            child = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {grandchild!r}]); time.sleep(120)"
            t0 = time.perf_counter()
            with self.assertRaises(subprocess.TimeoutExpired):
                procs.run([sys.executable, "-c", child], timeout=8, text=True)
            self.assertLess(time.perf_counter() - t0, 60)
            pid = int(pid_file.read_text())
            for _ in range(20):  # taskkill may take a moment
                if not _alive(pid):
                    break
                time.sleep(0.25)
            self.assertFalse(_alive(pid), f"grandchild {pid} still running")

    def test_a_quick_command_returns_like_subprocess_run(self):
        done = procs.run([sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"],
                         timeout=60, text=True)
        self.assertEqual((done.returncode, done.stdout.strip(), done.stderr.strip()), (3, "out", "err"))


if __name__ == "__main__":
    unittest.main()
