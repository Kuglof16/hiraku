import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from hiraku.__main__ import main
from hiraku.agent.scripted import run_scripted
from hiraku.agent.session import STOPPED_BY_BUTTON, STOPPED_BY_USER, start_run
from hiraku.contract import layout
from tests.tools import fakes
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


class RunCommandTest(RunCase, unittest.TestCase):
    """python -m hiraku run --scripted on the run with the test stand-ins."""

    def run_cli(self):
        out = io.StringIO()
        with mock.patch("hiraku.agent.session.start_run", return_value=self.ctx), redirect_stdout(out):
            code = main(["run", str(REQUIREMENTS), "--scripted", "--runs", str(self.root)])
        return code, out.getvalue()

    def test_end_shows_run_id_status_and_cost(self):
        code, out = self.run_cli()
        p = self.ctx.project
        self.assertEqual(code, 0 if p["status"] == "done" else 1)
        self.assertIn(f"run_id: {p['run_id']}", out)
        self.assertIn(f"status: {p['status']}", out)
        self.assertIn("cost_usd: 0 (scripted", out)

    def test_ctrl_c_finishes_the_run_as_stopped(self):
        with mock.patch("hiraku.agent.scripted.run_scripted", side_effect=KeyboardInterrupt):
            code, out = self.run_cli()
        self.assertEqual(code, 130)
        self.assertEqual((self.ctx.project["status"], self.ctx.project["stop_reason"]), ("stopped", STOPPED_BY_USER))
        self.assertIn(f"run_id: {self.ctx.project['run_id']}", out)
        self.assertIn("status: stopped", out)
        self.assertEqual(self.kinds()[-1], "run.finished")

    def test_stop_button_in_the_scripted_run(self):
        orig = self.tools["step_climb"].run

        def press_then_run(*a):  # the button is pressed while P1 is being analysed
            (self.ctx.store.path / layout.STOP_REQUEST_FILE).write_text("", encoding="utf-8")
            return orig(*a)

        self.tools["step_climb"].run = press_then_run
        self.assertEqual(run_scripted(self.ctx), "stopped")
        finished = self.ctx.store.events()[-1]
        self.assertEqual((finished["data"]["status"], finished["data"]["stop_code"]), ("stopped", "user_stop"))
        self.assertEqual(self.ctx.project["stop_reason"], STOPPED_BY_BUTTON)
        self.assertFalse((self.ctx.store.path / layout.STOP_REQUEST_FILE).exists())
        self.assertEqual(len(self.tools["step_climb"].runs), 1)  # nothing analysed after the press
        self.assertEqual(self.tools["frame_static"].runs, [])
        self.assert_events_fit()

    def test_ctrl_c_in_the_scripted_run(self):
        def interrupt(*a):
            raise KeyboardInterrupt

        self.tools["step_climb"].run = interrupt
        with self.assertRaises(KeyboardInterrupt):
            run_scripted(self.ctx)
        self.assertEqual((self.ctx.project["status"], self.ctx.project["stop_reason"]), ("stopped", STOPPED_BY_USER))
        stopped = [e["agent"] for e in self.ctx.store.events() if e["kind"] == "agent.stopped"]
        self.assertEqual(stopped, ["cae.strength", "lead"])
        self.assertEqual(self.kinds()[-1], "run.finished")
        self.assert_events_fit()


class StartInterruptTest(unittest.TestCase):
    def test_ctrl_c_while_checking_tools(self):
        tools = fakes.shelf()

        def interrupt():
            raise KeyboardInterrupt

        tools["frame_static"].check = interrupt
        with tempfile.TemporaryDirectory() as tmp:
            design = Path(tmp) / "design.json"
            design.write_text(json.dumps(fakes.loaded_design()), encoding="utf-8")
            with self.assertRaises(KeyboardInterrupt):
                start_run(REQUIREMENTS, design_path=design, root=Path(tmp) / "runs", formulas=fakes.FakeFormulas(),
                          tools=tools)
            (folder,) = (Path(tmp) / "runs").iterdir()
            project = json.loads((folder / layout.PROJECT_FILE).read_text(encoding="utf-8"))
            self.assertEqual((project["status"], project["stop_reason"]), ("stopped", STOPPED_BY_USER))


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
