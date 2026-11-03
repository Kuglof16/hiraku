"""python -m tests.web.browser: puts HIRAKU_WEB_BROWSER=1 in by itself, runs the named tests
only, and leaves the other switches (HIRAKU_WEB_DEV_RUNS) as they were. Run in a child process
with an environment that has neither switch; the named test needs no browser."""

import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[2]
NAMED = "tests.web.test_webserve.ListRunsTest"


def _env():
    return {k: v for k, v in os.environ.items() if k not in ("HIRAKU_WEB_BROWSER", "HIRAKU_WEB_DEV_RUNS")}


class BrowserEntryTest(unittest.TestCase):
    def test_runs_only_the_named_tests(self):
        p = subprocess.run([sys.executable, "-m", "tests.web.browser", NAMED], cwd=REPO_DIR, env=_env(),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        self.assertIn("Ran 1 test", p.stderr)

    def test_sets_the_browser_switch_and_nothing_else(self):
        code = ("import os; from tests.web import browser; rc = browser.main([%r]); "
                "print(rc, os.environ.get('HIRAKU_WEB_BROWSER'), os.environ.get('HIRAKU_WEB_DEV_RUNS'))" % NAMED)
        p = subprocess.run([sys.executable, "-c", code], cwd=REPO_DIR, env=_env(), capture_output=True, text=True, timeout=120)
        self.assertEqual(p.stdout.strip().splitlines()[-1], "0 1 None", p.stderr[-800:])

    def test_a_failing_named_test_gives_a_non_zero_exit(self):
        p = subprocess.run([sys.executable, "-m", "tests.web.browser", "tests.web.no_such_module"], cwd=REPO_DIR, env=_env(),
                           capture_output=True, text=True, timeout=120)
        self.assertNotEqual(p.returncode, 0)


if __name__ == "__main__":
    unittest.main()
