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
        (run / "events.jsonl").write_text("{}\n", encoding="utf-8")
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

    def test_a_failed_run_is_not_passed_on(self):
        self.code = 1
        self.assertEqual(self.run_one().returncode, 1)
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

    def test_copy_run(self):
        s = self.run_one()
        c = shared_runs.copy_run(s, self.tmp / "copy")
        self.assertTrue((c / "events.jsonl").is_file())
        self.assertNotEqual(c, s.run_dir)


if __name__ == "__main__":
    unittest.main()
