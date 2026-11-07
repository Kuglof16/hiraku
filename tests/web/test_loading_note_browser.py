"""Opening a record (?run=) from hiraku.webserve: while it is read, the page is one line (「記録を読み込んでいます
NN %」) and the lead's pet, no frame (tabs, the top band, the 3D); when it cannot be read, the same line says
why (「記録を読み込めませんでした (what)。what to do」; the status and the address after it only under ?dev=1)
with 「↻ 開き直す」 and 「☰ 一覧へ」, not in red (web/index.html #boot, web/js/app.js boot, loadFailedWhy).

The read is held back by a fetch that never answers (an init script), or failed with Playwright's
routing of /api/runs/<id>/events.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import SAMPLE_EVENTS, make_server

try:
    from tests.web.pages import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None
RUN = "20261105-120000-loading"
EVENTS = "**/api/runs/*/events*"
NOTE = """() => { const n = document.getElementById("boot");
  return n && { failed: n.classList.contains("failed"), line: document.getElementById("boot-line").textContent,
    links: [...n.querySelectorAll("a")].map((a) => [a.textContent, a.getAttribute("href")]),
    frame: document.getElementById("stage").checkVisibility({ visibilityProperty: true }),
    color: getComputedStyle(document.getElementById("boot-line")).color }; }"""
HOLD = """(() => { const real = window.fetch; window.heldReads = 0;
  window.fetch = (u, o) => (String(u).includes("/events") ? (window.heldReads++, new Promise(() => {})) : real(u, o)); })()"""


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class LoadingNoteBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        (cls.tmp / "runs" / RUN).mkdir(parents=True)
        shutil.copyfile(SAMPLE_EVENTS, cls.tmp / "runs" / RUN / "events.jsonl")
        cls.server = make_server("127.0.0.1", 0, cls.tmp / "runs", quiet=True, saved_dir=None, launcher=False)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def page(self):
        page = self.browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        self.addCleanup(page.close)
        return page, errors

    def test_while_reading_one_line_and_no_frame(self):
        page, errors = self.page()
        page.add_init_script(HOLD)  # the read of the record never comes back (only on this page)
        page.goto(f"{self.base}/?run={RUN}", wait_until="domcontentloaded")
        page.wait_for_function("window.heldReads > 0")
        got = page.evaluate(NOTE)
        self.assertEqual((got["failed"], got["frame"], got["links"]), (False, False, []), got)
        self.assertTrue(got["line"].startswith("記録を読み込んでいます"), got)
        self.assertTrue(page.is_visible("#boot-pet"))
        self.assertEqual(errors, [])

    def test_the_line_goes_when_the_record_is_read(self):
        page, errors = self.page()
        page.goto(f"{self.base}/?run={RUN}&pace=0&t=9", wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=30000)
        page.wait_for_function("!document.getElementById('boot')")
        self.assertTrue(page.evaluate("document.getElementById('stage').checkVisibility({ visibilityProperty: true })"))
        self.assertEqual(errors, [])

    def test_the_list_shows_without_the_line(self):
        page, errors = self.page()
        page.goto(f"{self.base}/?list=1", wait_until="domcontentloaded")
        page.wait_for_selector("#runlist h2")
        self.assertFalse(page.evaluate("!!document.getElementById('boot')"))
        self.assertEqual(errors, [])

    def check_failed(self, page, why, href_run=RUN, dev=False):
        page.wait_for_selector("#boot.failed")
        got = page.evaluate(NOTE)
        q = "&dev=1" if dev else ""
        self.assertEqual(got["line"], why)
        self.assertFalse(got["frame"])
        self.assertEqual(got["links"], [["↻ 開き直す", f"?run={href_run}{q}"], ["☰ 一覧へ", f"?list=1{q}"]])
        r, g, b = (int(x) for x in re.findall(r"\d+", got["color"])[:3])
        self.assertLess(r - max(g, b), 60, got["color"])  # (not red)
        return got

    def test_a_server_error_says_why_with_two_links(self):
        page, errors = self.page()
        page.route(EVENTS, lambda route: route.fulfill(status=500, body='{"error": "boom"}', content_type="application/json"))
        page.goto(f"{self.base}/?run={RUN}", wait_until="domcontentloaded")
        self.check_failed(page, "記録を読み込めませんでした (サーバー 500)。ページを読み込み直してください")
        self.assertNotIn("/api/", page.inner_text("#boot"))
        # 開き直す reads it again (now it can be read): the line goes
        page.unroute(EVENTS)
        page.click("#boot a:has-text('開き直す')")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=30000)
        page.wait_for_function("!document.getElementById('boot')")
        self.assertEqual(errors, [])

    def test_a_record_not_there_and_no_server(self):
        page, errors = self.page()
        page.goto(f"{self.base}/?run=20991231-000000-nothing", wait_until="domcontentloaded")
        self.check_failed(page, "記録を読み込めませんでした (見つかりません)。消されたか、名前が違います。一覧から選び直してください",
                          "20991231-000000-nothing")
        page.route(EVENTS, lambda route: route.abort())
        page.goto(f"{self.base}/?run={RUN}", wait_until="domcontentloaded")
        self.check_failed(page, "記録を読み込めませんでした (ネットワーク)。HIRAKU が動いているか確かめて、ページを読み込み直してください")
        page.click("#boot a:has-text('一覧へ')")
        page.wait_for_url("**/?list=1")
        self.assertEqual(errors, [])

    def test_dev_shows_the_status_and_the_address(self):
        page, errors = self.page()
        page.route(EVENTS, lambda route: route.fulfill(status=500, body="{}", content_type="application/json"))
        page.goto(f"{self.base}/?run={RUN}&dev=1", wait_until="domcontentloaded")
        self.check_failed(page, f"記録を読み込めませんでした (サーバー 500)。ページを読み込み直してください — 500 /api/runs/{RUN}/events?offset=0", dev=True)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
