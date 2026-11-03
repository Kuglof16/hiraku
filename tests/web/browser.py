"""Run the screen's tests with the browser tests on: python -m tests.web.browser [test names...]

Puts HIRAKU_WEB_BROWSER=1 into this process's environment, then runs tests/web (discover)
or only the named tests (e.g. tests.web.test_browser.BrowserReplayTest). The browser tests
need Playwright (not in requirements.txt). Other switches (HIRAKU_WEB_DEV_RUNS, ...) are
left as they are in the environment; nothing else is set here.

An entry point rather than `HIRAKU_WEB_BROWSER=1 python -m unittest ...`, so the switch does
not have to be put in front of the command.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[2]


def main(argv: list[str]) -> int:
    os.environ["HIRAKU_WEB_BROWSER"] = "1"  # before any test module is imported (they read it at import)
    loader = unittest.defaultTestLoader
    if argv:
        suite = loader.loadTestsFromNames(argv)
    else:
        suite = loader.discover(str(REPO_DIR / "tests" / "web"), top_level_dir=str(REPO_DIR))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
