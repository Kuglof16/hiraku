"""qa.slot: a heavy job takes the machine-wide suite lock; a second one waits; the lock goes when the job
ends, fails or is cancelled. Every test uses a lock folder of its own (HIRAKU_RUN_TESTS_LOCK_DIR), never
the PC's real one."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from qa import run_tests, slot

ROOT = Path(__file__).resolve().parents[2]


class SlotTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.lock_dir = self.tmp / "locks"
        self.env = dict(os.environ, HIRAKU_RUN_TESTS_LOCK_DIR=str(self.lock_dir), PYTHONUTF8="1")
        patch = mock.patch.object(run_tests, "LOCK_DIR", self.lock_dir)
        patch.start()
        self.addCleanup(patch.stop)

    def slot(self, what, cmd, *opts):
        return subprocess.Popen([sys.executable, "-m", "qa.slot", what, *opts, "--", *cmd], cwd=ROOT, env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")

    def sleeper(self, seconds, stamp):
        # writes when it started and ended, then exits 0
        code = (f"import time, pathlib; p = pathlib.Path(r'{stamp}'); a = time.time(); time.sleep({seconds}); "
                f"p.write_text(f'{{a}} {{time.time()}}')")
        return [sys.executable, "-c", code]

    def wait_held(self, timeout=60.0):
        """Until the lock's holder file names a job (the first slot has the lock)."""
        end = time.time() + timeout
        while time.time() < end:
            try:
                if json.loads((self.lock_dir / run_tests.QUEUE_INFO).read_text(encoding="utf-8")).get("what"):
                    return
            except (OSError, ValueError):
                pass
            time.sleep(0.2)
        self.fail("the first job never took the lock")

    def free(self):
        """The lock can be taken now (and is given back at once)."""
        f = run_tests.take_queue(0, wait=False, say=lambda *a, **k: None, what="probe")
        if f is None:
            return False
        f.close()
        return True

    def test_a_second_job_waits_for_the_first(self):
        a_stamp, b_stamp = self.tmp / "a.txt", self.tmp / "b.txt"
        a = self.slot("first", self.sleeper(3, a_stamp))
        self.wait_held()
        b = self.slot("second", self.sleeper(0, b_stamp))
        _, b_err = b.communicate(timeout=120)
        a.communicate(timeout=120)
        self.assertEqual((a.returncode, b.returncode), (0, 0), b_err)
        a_end = float(a_stamp.read_text().split()[1])
        b_start = float(b_stamp.read_text().split()[0])
        self.assertGreaterEqual(b_start, a_end)
        self.assertIn("waiting for", b_err)
        self.assertIn("first", b_err)  # the waiting line names the holder's job
        self.assertTrue(self.free())

    def test_no_wait_says_busy_and_runs_nothing(self):
        a = self.slot("first", self.sleeper(3, self.tmp / "a.txt"))
        self.wait_held()
        b = self.slot("second", self.sleeper(0, self.tmp / "b.txt"), "--no-wait")
        _, err = b.communicate(timeout=60)
        a.communicate(timeout=120)
        self.assertEqual(b.returncode, slot.BUSY, err)
        self.assertFalse((self.tmp / "b.txt").exists())

    def test_a_failed_job_gives_its_exit_code_and_frees_the_lock(self):
        p = self.slot("fails", [sys.executable, "-c", "raise SystemExit(5)"])
        p.communicate(timeout=60)
        self.assertEqual(p.returncode, 5)
        self.assertTrue(self.free())

    def test_a_stopped_slot_frees_the_lock(self):
        a = self.slot("stopped", self.sleeper(60, self.tmp / "a.txt"))
        self.wait_held()
        self.assertFalse(self.free())
        a.kill()  # the OS frees a lock whose holder ends
        a.communicate(timeout=60)
        end = time.time() + 30
        while not self.free() and time.time() < end:
            time.sleep(0.2)
        self.assertTrue(self.free())

    def test_ctrl_c_stops_the_command_and_frees_the_lock(self):
        proc = mock.Mock()
        proc.wait.side_effect = [KeyboardInterrupt, 0]
        with mock.patch.object(slot.subprocess, "Popen", return_value=proc), \
                mock.patch.object(slot.procs, "kill_tree") as kill:
            code = slot.main(["cancelled", "--", "anything"])
        self.assertEqual(code, 130)
        kill.assert_called_once_with(proc)
        self.assertTrue(self.free())

    def test_a_wrong_call_runs_nothing(self):
        for argv in (["no-command"], ["--", "cmd"], ["what", "--"]):
            with self.subTest(argv=argv), mock.patch.object(slot.subprocess, "Popen") as popen:
                self.assertEqual(slot.main(argv), 2)
                popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
