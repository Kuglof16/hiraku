"""Starting a run from a settled request (?new=1, the bar over A1/A2):

- the two ways say what they are (台本: 決まった手順で再現・費用なし / Claude: Claude が考える・費用がかかる),
  and without the key the Claude way says a setup is needed, without the key's name on the page;
- while /api/launch has not returned (the run checks its tools first), the bar says the run is
  starting with the seconds counted, and both buttons are disabled;
- a start while a run started here is still going (409) is said in Japanese with a link to that run;
  the server's English stays only in the title.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed, as test_browser.
"""

import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import SAMPLE_EVENTS, make_server

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None

RUN_ID = "20261105-120000-offroad-4w-wait"
WAIT_S = 3.0  # the stand-in's tool check before it prints its folder

# A stand-in for `python -m hiraku run`: waits (as the tool check does), then makes its folder
# with the sample's events, prints it as the real command does, and stays up (a run still going).
_CODE = r"""
import pathlib, shutil, sys, time
src, runs, run_id, wait = sys.argv[1], pathlib.Path(sys.argv[2]), sys.argv[3], float(sys.argv[4])
time.sleep(wait)
d = runs / run_id
d.mkdir(parents=True)
shutil.copyfile(src, d / "events.jsonl")
print("run folder:", d, flush=True)
time.sleep(30)
"""


def reader(path):
    return {"product": "offroad-4w", "want": "見本の要件", "cost_limit_usd": 2.0, "round_limit": 1}


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class LaunchWaitBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def serve(self, env):
        from hiraku.webserve.launch import Launcher

        tmp = Path(tempfile.mkdtemp())
        (tmp / "requirements").mkdir()

        def command(req, mode, runs, intake_events=None):
            return [sys.executable, "-c", _CODE, str(SAMPLE_EVENTS), str(runs), RUN_ID + ("-claude" if mode == "claude" else ""), str(WAIT_S)]

        lau = Launcher(tmp / "runs", tmp / "requirements", env=env, command=command, reader=reader, wait_s=30,
                       demo_runs_file=tmp / "no-demo-runs.yaml")
        server = make_server("127.0.0.1", 0, tmp / "runs", quiet=True, saved_dir=None, launcher=lau)
        threading.Thread(target=server.serve_forever, daemon=True).start()

        def close():
            server.shutdown()
            server.server_close()
            if lau.proc and lau.proc.poll() is None:
                lau.proc.kill()
                lau.proc.wait()
            shutil.rmtree(tmp, ignore_errors=True)

        self.addCleanup(close)
        return f"http://127.0.0.1:{server.server_address[1]}", lau

    def settled(self, base):
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{base}/?new=1")
        page.wait_for_selector("#draftbar")
        page.fill("#view-intake textarea", "林道を走れる 2 人乗りの小さな車両")
        page.click("#view-intake button:has-text('送る')")
        page.wait_for_function("window.hiraku.request.state.state && !window.hiraku.request.state.busy")
        page.evaluate("async () => { await window.hiraku.request.answer({ answers: { Q30: 'two_cargo', Q40: 150, Q50: 40, Q70: '私有地だけ' } }); }")
        page.wait_for_selector("#draftbar button[data-start='scripted']")
        return page, errors

    def test_the_two_ways_in_words_without_the_key(self):
        base, _ = self.serve({})
        page, errors = self.settled(base)
        text = page.inner_text("#draftbar")
        for words in ("要件がそろいました", "台本で始める", "決まった手順で再現します", "費用はかかりません",
                      "Claude で始める", "Claude が考えて進めます。費用がかかります", "Claude で始めるには準備が要ります"):
            self.assertIn(words, text)
        self.assertNotIn("ANTHROPIC_API_KEY", text)
        self.assertNotIn("Claude は呼ばない)", page.text_content("#draftbar button[data-start='scripted']"))
        self.assertTrue(page.is_disabled("#draftbar button:has-text('Claude で始める')"))
        # くわしく: the README's 動かし方
        self.assertIn("動かし方", page.evaluate("decodeURIComponent(document.querySelector('#draftbar a.readme').href)"))
        self.assertEqual(errors, [])
        page.close()

    def test_with_the_key_no_setup_line(self):
        base, _ = self.serve({"ANTHROPIC_API_KEY": "test-value-not-a-real-key"})
        page, errors = self.settled(base)
        text = page.inner_text("#draftbar")
        self.assertIn("Claude が考えて進めます。費用がかかります", text)
        self.assertNotIn("準備が要ります", text)
        self.assertFalse(page.is_disabled("#draftbar button[data-start='claude']"))
        self.assertEqual(errors, [])
        page.close()

    def test_while_it_starts_the_bar_says_so_and_the_buttons_wait(self):
        base, lau = self.serve({})
        page, errors = self.settled(base)
        page.click("#draftbar button[data-start='scripted']")
        page.wait_for_selector("#draftbar .starting")
        page.wait_for_function("document.querySelector('#draftbar .starting .secs')?.textContent === '1 秒'", timeout=5000)
        got = page.evaluate("""() => ({
            text: document.querySelector('#draftbar .starting').textContent,
            disabled: [...document.querySelectorAll('#draftbar .way button')].map((b) => b.disabled),
            opacity: getComputedStyle(document.querySelector("#draftbar button[data-start='scripted']")).opacity,
        })""")
        self.assertIn("台本で運転を始めています", got["text"])
        self.assertIn("使うツールの確かめ", got["text"])
        self.assertEqual(got["disabled"], [True, True])
        self.assertLess(float(got["opacity"]), 1.0)
        page.wait_for_url(f"**/?run={RUN_ID}", timeout=30000)
        self.assertEqual(len(lau.started), 1)
        self.assertEqual(errors, [])
        page.close()

    def test_a_start_while_a_run_is_going_says_so_in_japanese(self):
        base, lau = self.serve({})
        page, errors = self.settled(base)
        lau.start("ignored", "scripted", request=page.evaluate("window.hiraku.request.state.done.request_id"))  # a run going
        page.click("#draftbar button[data-start='scripted']")
        page.wait_for_selector("#draftbar .err a.going-run")
        err = page.locator("#draftbar .err")
        self.assertIn("いま走っている運転があります", err.inner_text())
        self.assertNotIn("still going", err.inner_text())
        self.assertIn(f"a run is still going: {RUN_ID}", err.get_attribute("title"))
        self.assertTrue(page.get_attribute("#draftbar a.going-run", "href").endswith(f"?run={RUN_ID}"))
        # the buttons are back, for when that run has ended
        self.assertFalse(page.is_disabled("#draftbar button[data-start='scripted']"))
        self.assertEqual(errors, [])
        page.close()


if __name__ == "__main__":
    unittest.main()
