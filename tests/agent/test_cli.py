import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from hiraku.__main__ import main
from hiraku.agent.scripted import run_scripted
from tests.tools.helpers import EXAMPLE_DESIGN, REQUIREMENTS, RunCase


class CliTest(unittest.TestCase):
    def test_claude_run_stops_without_a_key_and_leaves_nothing(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {}, clear=True):
            err = io.StringIO()
            with redirect_stderr(err):
                code = main(["run", str(REQUIREMENTS), "--design", str(EXAMPLE_DESIGN), "--runs", tmp])
            self.assertEqual(code, 2)
            self.assertIn("ANTHROPIC_API_KEY", err.getvalue())
            self.assertEqual(list(Path(tmp).iterdir()), [])


class CheckCommandTest(RunCase, unittest.TestCase):
    def test_check_a_scripted_run(self):
        run_scripted(self.ctx)
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["check", str(self.ctx.store.path)])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("0 problems", out.getvalue())

    def test_check_finds_a_gap(self):
        run_scripted(self.ctx)
        p = self.ctx.store.events_path
        lines = p.read_text(encoding="utf-8").splitlines()
        p.write_text("\n".join(lines[:3] + lines[4:]) + "\n", encoding="utf-8")
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["check", str(self.ctx.store.path)])
        self.assertEqual(code, 1)
        self.assertIn("seq", out.getvalue())


if __name__ == "__main__":
    unittest.main()
