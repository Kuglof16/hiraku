"""qa.ci_light: the light tests GitHub Actions runs on main name real test modules, once each."""

import importlib
import unittest
from pathlib import Path

from qa import ci_light

ROOT = Path(__file__).resolve().parents[2]
LEFT_OUT = ("tests.solvers.", "tests.web.", "tests.verification.test_real_runs", "tests.verification.test_rapier")


class CiLight(unittest.TestCase):
    def test_each_module_is_named_once_and_has_tests(self):
        self.assertEqual(len(ci_light.LIGHT), len(set(ci_light.LIGHT)))
        for name in ci_light.LIGHT:
            with self.subTest(name):
                self.assertTrue((ROOT / (name.replace(".", "/") + ".py")).is_file())
                self.assertGreater(unittest.defaultTestLoader.loadTestsFromModule(importlib.import_module(name))
                                   .countTestCases(), 0)

    def test_no_analysis_browser_or_scripted_run_tests(self):
        self.assertEqual([n for n in ci_light.LIGHT if n.startswith(LEFT_OUT)], [])

    def test_list(self):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(ci_light.main(["--list"]), 0)
        self.assertEqual(out.getvalue().split(), ci_light.LIGHT)
