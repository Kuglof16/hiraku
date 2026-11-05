"""The run list's rows (no ?dev=1) each say, in one line under the want, when the run started, how
it ended and how it was driven: 「11/4 08:40 · 全部合格・版 4 · 決まった手順で再現」.

The runs are made here: one that passed everything (project.json and its last version's
judgements.json), one stopped at its cost limit, one still going, and one older record stopped
without a stop_code. The line is checked word for word, and the list goes through the layout lint
at 1280 and 900 (no wrap, nothing running out).

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import make_server
from tests.web.test_layout_lint_browser import LAUNCH_FILLED

try:
    from tests.web.pages import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None
LINT_JS = (Path(__file__).with_name("layout_lint.js")).read_text(encoding="utf-8")
WANT = "林道を走れる 2 人乗りの小さなオフロード車両 (4 輪、最高 40 km/h、積載 150 kg)"

PASSED = "20261104-084012-passed"
STOPPED = "20261104-091500-stopped"
GOING = "20261104-100000-going"
OLD = "20261103-013120-old"


def _ev(seq, kind, run_id, data=None, **extra):
    return json.dumps({"seq": seq, "ts": "2026-11-04T08:40:12.000+09:00", "t": float(seq), "run_id": run_id,
                       "kind": kind, "data": data or {}, **extra}, ensure_ascii=False) + "\n"


def _judged(seq, run_id, v, domain, state):
    return _ev(seq, "judgement.updated", run_id, {"design_version": v, "domain": domain, "state": state,
                                                  "red_count": 0, "verdict_ids": [], "summary": ""},
               design_version=v, domain=domain)


def build(runs: Path) -> None:
    def folder(run_id, lines, project=None, judgements=None):
        d = runs / run_id
        d.mkdir(parents=True)
        (d / "events.jsonl").write_text("".join(lines), encoding="utf-8")
        if project is not None:
            (d / "project.json").write_text(json.dumps({"run_id": run_id, "want": WANT, **project}, ensure_ascii=False), encoding="utf-8")
        for v, js in (judgements or {}).items():
            (d / f"v{v:03d}").mkdir()
            (d / f"v{v:03d}" / "judgements.json").write_text(json.dumps(js), encoding="utf-8")

    start = lambda rid, **more: _ev(0, "run.started", rid, {"want": WANT, **more})
    folder(PASSED, [start(PASSED), _ev(1, "run.finished", PASSED, {"status": "done", "stop_code": "done"})],
           project={"created_at": "2026-11-04T08:40:12.000+09:00", "status": "done", "driver": "scripted", "design_versions": [1, 2, 3, 4]},
           judgements={4: [{"domain": "strength", "state": "green"}, {"domain": "assembly", "state": "green"}]})
    folder(STOPPED, [start(STOPPED, driver="agent_sdk", created_at="2026-11-04T09:15:00.000+09:00"),
                     _judged(1, STOPPED, 2, "strength", "red"),
                     _ev(2, "run.finished", STOPPED, {"status": "stopped", "stop_code": "cost_limit", "stop_reason": "費用が上限の 2.00 USD に達したので止めました"})])
    folder(GOING, [start(GOING, driver="claude_code_plugin"), _judged(1, GOING, 1, "strength", "pending")])
    # an older record: no driver, no stop_code; the reason's opening says which stop it was
    folder(OLD, [start(OLD), _judged(1, OLD, 1, "strength", "green"),
                 _ev(2, "run.finished", OLD, {"status": "stopped", "stop_reason": "まだ判定の無い分野があるので終われません。強度信頼性: R1-step100 (…)"})])


EXPECTED = {
    PASSED: ["11/4 08:40", "全部合格・版 4", "決まった手順で再現"],
    STOPPED: ["11/4 09:15", "途中で止まった (上限の費用に達した)・版 2", "Claude で実行"],
    GOING: ["11/4 10:00", "実行中", "Claude で実行"],
    OLD: ["11/3 01:31", "途中で止まった (判定の無い分野を残した)・版 1"],
}


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class RunListRowsBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from hiraku.webserve.launch import Launcher

        cls.tmp = Path(tempfile.mkdtemp())
        runs = cls.tmp / "runs"
        build(runs)
        # the list's launcher with and without an API key, whatever the window running the test has
        # (the key changes the list's ways to run and its buttons; nothing is started here)
        cls.servers, cls.bases = [], {}
        for name, env in (("no key", {}), ("key", {"ANTHROPIC_API_KEY": "test-value-not-a-real-key"})):
            server = make_server("127.0.0.1", 0, runs, quiet=True, saved_dir=None, launcher=Launcher(runs, env=env))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            cls.servers.append(server)
            cls.bases[name] = f"http://127.0.0.1:{server.server_address[1]}"
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        for server in cls.servers:
            server.shutdown()
            server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def open(self, width, height, query="?list=1", key="no key"):
        page = self.browser.new_page(viewport={"width": width, "height": height})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.bases[key]}/{query}", wait_until="domcontentloaded")
        page.wait_for_selector("#runlist table")
        page.wait_for_function(LAUNCH_FILLED, timeout=60000)
        page.evaluate("document.fonts.ready")
        return page, errors

    def test_each_row_says_when_how_it_ended_and_how_it_was_driven(self):
        page, errors = self.open(1280, 720)
        got = page.evaluate(
            """() => Object.fromEntries([...document.querySelectorAll("#runlist tr[data-run]")].map((tr) =>
                 [tr.dataset.run, [...tr.querySelectorAll(".rmeta > span:not(.sep)")].map((s) => s.textContent)]))"""
        )
        line = page.evaluate("""(id) => document.querySelector(`#runlist tr[data-run="${id}"] .rmeta`).textContent""", PASSED)
        page.close()
        self.assertEqual(errors, [])
        self.assertEqual(got, EXPECTED)
        self.assertEqual(line, "11/4 08:40 · 全部合格・版 4 · 決まった手順で再現")

    def test_the_state_column_follows_how_the_run_ended(self):
        """The right column: 完了 / 途中で止まった / 実行中 (labels.js RUN_STATE_JA), not 完了 for every finished run."""
        page, errors = self.open(1280, 720)
        got = page.evaluate("""() => Object.fromEntries([...document.querySelectorAll("#runlist tr[data-run]")].map((tr) =>
                 [tr.dataset.run, [tr.querySelector("td.state").dataset.state, tr.querySelector("td.state").textContent]]))""")
        page.close()
        self.assertEqual(errors, [])
        self.assertEqual(got, {PASSED: ["done", "完了"], STOPPED: ["stopped", "途中で止まった"],
                               GOING: ["going", "実行中"], OLD: ["stopped", "途中で止まった"]})

    def test_the_rows_pass_the_layout_lint(self):
        for key in self.bases:
            for width, height in ((1280, 720), (900, 506)):
                page, errors = self.open(width, height, key=key)  # (open waits until #launch is filled)
                problems = page.evaluate(LINT_JS, {"root": "#runlist"})
                keyless = page.evaluate("document.querySelectorAll('#launch .keyless').length")
                page.close()
                self.assertEqual(errors, [])
                self.assertEqual(keyless, 1 if key == "no key" else 0, key)  # the server's launcher, not the window's
                self.assertEqual(problems, [], f"{key} @{width}")

    def test_the_dev_list_is_as_it_was(self):
        page, errors = self.open(1280, 720, "?list=1&dev=1")
        rows = page.evaluate("""() => [...document.querySelectorAll("#runlist tr[data-run]")].map((tr) => [tr.dataset.run, !!tr.querySelector(".rmeta"), tr.querySelector(".rid")?.textContent ?? ""])""")
        page.close()
        self.assertEqual(errors, [])
        for rid, meta, rid_text in rows:
            self.assertFalse(meta, rid)
            self.assertIn(rid, rid_text)


if __name__ == "__main__":
    unittest.main()
