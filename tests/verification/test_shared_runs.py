"""qa.shared_runs: one scripted run per requirements file and code version (the run itself faked)."""

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from qa import shared_runs


class SharedRuns(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.cache = self.tmp / "cache"
        self.req = self.tmp / "offroad-test.yaml"
        self.req.write_text("tests: [P1]\n", encoding="utf-8")
        self.calls = []
        self.code = 0
        env = mock.patch.dict(os.environ)
        env.start()
        os.environ.pop(shared_runs.ENV_SWITCH, None)
        self.addCleanup(env.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def fake_make(self, requirements, out, timeout):
        self.calls.append(out)
        time.sleep(0.3)  # long enough for a second caller to arrive while it runs
        run = out / "20261104-000000-x"
        run.mkdir(parents=True)
        end = "" if self.code == 3 else '{"kind": "run.finished"}\n'  # 3: a run that did not finish
        (run / "events.jsonl").write_text('{"kind": "run.started"}\n' + end, encoding="utf-8")
        return run, self.code, "out", "err", 0.3

    def run_one(self, **kw):
        with mock.patch.object(shared_runs, "_make", self.fake_make):
            return shared_runs.scripted_run(self.req, cache=self.cache, **kw)

    def test_two_callers_share_one_run(self):
        got = []
        threads = [threading.Thread(target=lambda: got.append(self.run_one())) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual({s.run_dir for s in got}, {got[0].run_dir})
        self.assertEqual(sorted(s.reused for s in got), [False, True])
        self.assertEqual(got[0].stdout, "out")

    def test_another_requirements_file_is_another_run(self):
        a = self.run_one()
        self.req.write_text("tests: [P1, R1]\n", encoding="utf-8")
        b = self.run_one()
        self.assertNotEqual(a.key, b.key)
        self.assertEqual(len(self.calls), 2)

    def test_a_run_ended_not_done_is_passed_on(self):
        self.code = 1  # hiraku run's exit code for stopped / not_judged
        self.run_one()
        self.assertTrue(self.run_one().reused)
        self.assertEqual(len(self.calls), 1)

    def test_a_run_that_did_not_finish_is_not_passed_on(self):
        self.code = 3
        self.assertEqual(self.run_one().returncode, 3)
        self.code = 0
        s = self.run_one()
        self.assertEqual((s.returncode, s.reused, len(self.calls)), (0, False, 2))
        self.assertTrue(self.run_one().reused)

    def test_switched_off_every_call_runs(self):
        os.environ[shared_runs.ENV_SWITCH] = "0"
        self.run_one()
        self.run_one()
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(self.cache.exists())

    def test_old_keys_are_pruned(self):
        for k in range(shared_runs.KEEP_KEYS + 2):
            (self.cache / f"old{k}").mkdir(parents=True)
            os.utime(self.cache / f"old{k}", (k, k))
        self.run_one()
        self.assertEqual(len([p for p in self.cache.iterdir() if p.is_dir()]), shared_runs.KEEP_KEYS)

    def test_the_runs_of_many_requirements_files_stay_together(self):
        # a slow suite runs every requirements file of one code version: none prunes another's run
        reqs = []
        for n in range(shared_runs.KEEP_KEYS + 2):
            r = self.tmp / f"req{n}.yaml"
            r.write_text(f"tests: [P{n}]\n", encoding="utf-8")
            reqs.append(r)
        with mock.patch.object(shared_runs, "_make", self.fake_make):
            first = [shared_runs.scripted_run(r, cache=self.cache) for r in reqs]
            again = [shared_runs.scripted_run(r, cache=self.cache) for r in reqs]
        self.assertEqual(len(self.calls), len(reqs))
        self.assertTrue(all(s.reused for s in again))
        self.assertTrue(all(s.run_dir.is_dir() for s in first))
        self.assertEqual(len([p for p in self.cache.iterdir() if p.is_dir()]), 1)

    def test_a_code_version_with_a_run_going_is_not_pruned(self):
        busy = self.cache / "busy"
        (busy / "req-0123").mkdir(parents=True)
        for k in range(shared_runs.KEEP_KEYS + 1):
            (self.cache / f"new{k}").mkdir(parents=True)
        os.utime(busy / "req-0123", (1, 1))
        os.utime(busy, (1, 1))
        shared_runs._prune(self.cache, "new0", now=1 + shared_runs.STALE_S - 1)  # touched lately: a run may go
        self.assertTrue(busy.is_dir())
        shared_runs._prune(self.cache, "new0", now=1 + shared_runs.STALE_S + 1)
        self.assertFalse(busy.is_dir())

    def test_copy_run(self):
        s = self.run_one()
        c = shared_runs.copy_run(s, self.tmp / "copy")
        self.assertTrue((c / "events.jsonl").is_file())
        self.assertNotEqual(c, s.run_dir)


if __name__ == "__main__":
    unittest.main()
