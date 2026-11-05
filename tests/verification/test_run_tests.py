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

    def test_the_summary_prints_what_the_console_code_page_lacks(self):
        from unittest import mock
        out = io.TextIOWrapper(io.BytesIO(), encoding="cp932")
        with mock.patch.object(sys, "stdout", out):
            rt._utf8_out()
            print("FAIL: x\n    AssertionError: 1.2 not ≥ 2 — ✓")
            out.flush()
            text = out.buffer.getvalue().decode("utf-8")
        self.assertIn("≥ 2 — ✓", text)

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
        self.assertTrue(rt._out_of_memory({"name": "m.D5", "s": 1.0, "crash": "exit -9: "}))  # Linux's OOM killer
        self.assertFalse(rt._out_of_memory({"name": "m.D6", "s": 1.0, "crash": "exit -90: "}))
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
        # its own lock folder, apart from the real suite's; a killed holder can keep a file open a moment
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
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


class SuiteQueue(unittest.TestCase):
    """One full suite at a time: the machine-wide lock in the system temp folder."""

    HOLD = ("import sys, time, pathlib\n"
            "import qa.run_tests as rt\n"
            "rt.LOCK_DIR = pathlib.Path(sys.argv[1])\n"
            "f = rt.take_queue(8)\n"
            "print('held', flush=True)\n"
            "time.sleep(float(sys.argv[2]))\n")

    def setUp(self):
        # its own lock folder, apart from the real suite's; a killed holder can keep a file open a moment
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.saved, rt.LOCK_DIR = rt.LOCK_DIR, Path(self._tmp.name)

    def tearDown(self):
        rt.LOCK_DIR = self.saved
        self._tmp.cleanup()

    def _holder(self, seconds):
        p = subprocess.Popen([sys.executable, "-c", self.HOLD, str(rt.LOCK_DIR), str(seconds)], cwd=rt.ROOT,
                             stdout=subprocess.PIPE, text=True)
        self.addCleanup(p.stdout.close)
        self.assertEqual(p.stdout.readline().strip(), "held")
        return p

    def test_a_second_suite_does_not_get_the_lock_while_one_runs(self):
        p = self._holder(30)
        try:
            self.assertIsNone(rt.take_queue(8, wait=False))
        finally:
            procs.kill_tree(p)
            p.wait()
        # the OS frees the lock when the holder's process is gone, which on a busy machine can take a moment
        # after the kill (the venv launcher ends before the interpreter behind it)
        f, until = None, time.monotonic() + 30
        while f is None and time.monotonic() < until:
            f = rt.take_queue(8, wait=False)
            if f is None:
                time.sleep(0.2)
        self.assertIsNotNone(f, "the lock was not freed within 30 s of the holder's end")
        f.close()

    def test_a_waiting_suite_says_whom_it_waits_for_and_then_runs(self):
        p = self._holder(3)
        said = []
        saved, rt.POLL_S = rt.POLL_S, 0.2
        try:
            f = rt.take_queue(8, say=lambda *a, **k: said.append(" ".join(map(str, a))))
        finally:
            rt.POLL_S = saved
            p.wait()
        self.assertIsNotNone(f)
        f.close()
        self.assertEqual(len(said), 1, said)  # said once per holder, not at every poll
        self.assertTrue(said[0].startswith("waiting for ") and str(rt.ROOT) in said[0], said)


class _Alone:  # stands for a TestCase (not one, so discovery does not run it)
    def test_a(self): pass
    def test_b(self): pass


class _Shared(_Alone):
    @classmethod
    def setUpClass(cls): pass


class _SharedChild(_Shared):
    pass


class SplitByMethod(unittest.TestCase):
    def test_only_a_class_sharing_nothing_is_split(self):
        self.assertFalse(rt._shares_setup(_Alone))
        self.assertTrue(rt._shares_setup(_Shared))
        self.assertTrue(rt._shares_setup(_SharedChild))  # inherited from a base

    def test_weight_of_a_class_run_split(self):
        last = {"m.A": 70.0, "m.B.test_x": 50.0, "m.B.test_y": 30.0, "m.BC": 5.0}
        self.assertEqual(rt._weight("m.A", last), 70.0)
        self.assertEqual(rt._weight("m.B", last), 80.0)  # not m.BC
        self.assertIsNone(rt._weight("m.C", last))

    def test_a_class_never_timed_is_not_split(self):
        groups = rt.discover(["tests.verification.test_remote_suite"], {})
        self.assertEqual(list(groups), ["tests.verification.test_remote_suite.ReadResult"])

    def test_a_method_split_for_the_first_time_gets_its_share(self):
        groups = {"m.A.test_a": ["m.A.test_a"], "m.A.test_b": ["m.A.test_b"], "m.Z": ["m.Z.t1"]}
        self.assertEqual(rt._order(groups, {"m.A": 100.0, "m.Z": 40.0}), ["m.A.test_a", "m.A.test_b", "m.Z"])
        self.assertEqual(rt._order(groups, {"m.A": 100.0, "m.Z": 51.0})[0], "m.Z")


class MemoryAware(unittest.TestCase):
    """qa.run_tests.schedule: at most max_heavy heavy classes at once, and room in memory for the next."""

    def run_all(self, names, memory, jobs=4, max_heavy=2, free=lambda: 100.0):
        import threading
        lock = threading.Lock()
        now = {"heavy": 0, "max": 0}
        order = []

        def run(n):
            with lock:
                order.append(n)
                if (memory.get(n) or 0) >= rt.HEAVY_GB:
                    now["heavy"] += 1
                    now["max"] = max(now["max"], now["heavy"])
            time.sleep(0.05)
            with lock:
                if (memory.get(n) or 0) >= rt.HEAVY_GB:
                    now["heavy"] -= 1
            return {"name": n}

        res = rt.schedule(names, jobs, run, memory, max_heavy=max_heavy, free=free, poll_s=0.01)
        return res, order, now["max"]

    def test_at_most_two_heavy_classes_at_once(self):
        names = ["h1", "h2", "h3", "h4", "l1", "l2"]
        memory = {"h1": 8.7, "h2": 8.6, "h3": 8.6, "h4": 7.6, "l1": 0.5, "l2": None}
        res, order, most = self.run_all(names, memory, jobs=6)
        self.assertEqual(sorted(r["name"] for r in res), sorted(names))
        self.assertEqual(most, 2)
        self.assertLess(order.index("l1"), order.index("h3"))  # the light ones go first meanwhile

    def test_a_heavy_class_waits_for_free_memory_while_another_runs(self):
        res, order, most = self.run_all(["h1", "h2"], {"h1": 8.0, "h2": 8.0}, jobs=2, free=lambda: 5.0)
        self.assertEqual(most, 1)  # h2 waited (5 GB free < 8 + 4) until h1 ended, then started alone
        self.assertEqual(len(res), 2)

    def test_the_first_heavy_class_starts_even_when_memory_is_short(self):
        res, _, _ = self.run_all(["h1"], {"h1": 8.0}, jobs=2, free=lambda: 1.0)
        self.assertEqual(len(res), 1)

    def test_a_class_whose_runner_raised_is_reported_not_lost(self):
        def run(n):
            raise RuntimeError("boom")
        res = rt.schedule(["a"], 1, run, {"a": None}, poll_s=0.01)
        self.assertEqual(len(res), 1)
        self.assertIn("boom", res[0]["crash"])

    def test_a_worker_runs_the_convergence_levels_one_at_a_time(self):
        # KNOWN_HEAVY_GB counts SpaceFrameConvergenceTest as one 15.7 GB level; side by side they reach 46.9 GB
        from unittest import mock

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(rt.CONVERGENCE_JOBS, None)
            self.assertEqual(rt.worker_env()[rt.CONVERGENCE_JOBS], "1")
            os.environ[rt.CONVERGENCE_JOBS] = "3"  # set on purpose (a run of that class alone): kept
            self.assertEqual(rt.worker_env()[rt.CONVERGENCE_JOBS], "3")

    def test_memory_of(self):
        last = {"m.A": 5.0, "m.B.test_x": 1.0}
        self.assertEqual(rt.memory_of("m.A", last), 5.0)
        self.assertEqual(rt.memory_of("m.A.test_y", last), 5.0)  # split for the first time: the class's peak
        self.assertEqual(rt.memory_of("m.B.test_x", last), 1.0)
        self.assertEqual(rt.memory_of("tests.solvers.test_frame_tubes.MeshFlowTest", {}), 7.6)
        # a known one is a floor: its levels run in child processes the worker's peak does not see
        conv = "tests.solvers.test_fe_tubes.SpaceFrameConvergenceTest"
        self.assertEqual(rt.memory_of(conv, {conv: 0.6}), 15.7)
        self.assertEqual(rt.memory_of(conv + ".test_x", {conv: 0.6}), 15.7)
        self.assertEqual(rt.memory_of(conv, {conv: 20.0}), 20.0)
        self.assertIsNone(rt.memory_of("m.C", last))

    def test_this_process_peak_and_free_memory_read(self):
        self.assertGreater(rt.peak_gb() or 0.0, 0.0)
        self.assertGreater(rt.free_gb() or 0.0, 0.0)


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

    def test_one_blas_thread_unless_set(self):
        env = procs.one_blas_thread({"PATH": "x"})
        self.assertEqual((env["OPENBLAS_NUM_THREADS"], env["OMP_NUM_THREADS"], env["PATH"]), ("1", "1", "x"))
        self.assertEqual(procs.one_blas_thread({"OPENBLAS_NUM_THREADS": "4"})["OPENBLAS_NUM_THREADS"], "4")

    def test_workers_get_one_blas_thread(self):
        from unittest import mock
        seen = {}

        def fake_run(cmd, **kw):
            seen.update(kw.get("env") or {})
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(rt.procs, "run", fake_run), \
                mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("OPENBLAS_NUM_THREADS", None)
            os.environ.pop("OMP_NUM_THREADS", None)
            rt._run_one("tests.x.Y", Path(tmp), 60.0)
        self.assertEqual((seen.get("OPENBLAS_NUM_THREADS"), seen.get("OMP_NUM_THREADS")), ("1", "1"))

    def test_a_quick_command_returns_like_subprocess_run(self):
        done = procs.run([sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"],
                         timeout=60, text=True)
        self.assertEqual((done.returncode, done.stdout.strip(), done.stderr.strip()), (3, "out", "err"))


if __name__ == "__main__":
    unittest.main()
