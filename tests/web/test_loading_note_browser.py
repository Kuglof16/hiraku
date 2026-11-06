"""Opening a record (?run=): while it is read, a note says so at once with 「☰ 一覧へ」 that can be
pressed; when it cannot be read, the note says why in words (the status and the address only under
?dev=1) with 「↻ 開き直す」 and 「☰ 一覧へ」 (web/js/app.js loadingNote, loadFailedNote).

The read is held back by a fetch that never answers (an init script), or failed with Playwright's
routing of /api/runs/<id>/events.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
"""

from __future__ import annotations

import os
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
NOTE = """() => { const n = document.getElementById("loading-note");
  return n && { failed: n.classList.contains("failed"), lines: [...n.children].map((c) => c.textContent),
    links: [...n.querySelectorAll("a")].map((a) => [a.textContent, a.getAttribute("href")]) }; }"""


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

    def test_while_reading_the_note_and_the_list_link_show_at_once(self):
        page, errors = self.page()
        # the read of the record never comes back (only on this page, before app.js runs)
        page.add_init_script("""(() => { const real = window.fetch; window.heldReads = 0;
          window.fetch = (u, o) => (String(u).includes("/events") ? (window.heldReads++, new Promise(() => {})) : real(u, o)); })()""")
        page.goto(f"{self.base}/?run={RUN}", wait_until="domcontentloaded")
        page.wait_for_selector("#loading-note")
        got = page.evaluate(NOTE)
        self.assertEqual(got["failed"], False)
        self.assertEqual(got["lines"][0], "記録を読み込んでいます…")  # (the size is not known yet)
        self.assertEqual(got["links"], [["☰ 一覧へ", "?list=1"]])
        self.assertGreater(page.evaluate("window.heldReads"), 0)
        self.assertTrue(page.is_visible("#loading-note a"))
        # the link can be pressed (the note itself lets clicks through, its link does not)
        page.click("#loading-note a")
        page.wait_for_url("**/?list=1")
        page.wait_for_selector("#runlist h2")
        self.assertFalse(page.evaluate("() => document.getElementById('runlist').hidden"))
        self.assertEqual(errors, [])

    def test_the_note_is_on_the_3d_only(self):
        """While reading, the note is inside the 3D (#viewport): over the workbench it shows within the
        3D's box; on another tab (④) it is not seen, and the tabs are never under it."""
        held = """(() => { const real = window.fetch;
          window.fetch = (u, o) => (String(u).includes("/events") ? new Promise(() => {}) : real(u, o)); })()"""
        for tab, seen in (("bench", True), ("grid", False)):
            with self.subTest(tab=tab):
                page, errors = self.page()
                page.add_init_script(held)
                page.goto(f"{self.base}/?run={RUN}&tab={tab}", wait_until="domcontentloaded")
                page.wait_for_selector("#loading-note", state="attached")
                got = page.evaluate("""() => { const n = document.getElementById("loading-note");
                  const r = n.getBoundingClientRect(), v = document.getElementById("viewport").getBoundingClientRect();
                  const tabs = [...document.querySelectorAll(".tab")].map((b) => { const q = b.getBoundingClientRect();
                    return document.elementFromPoint(q.left + q.width / 2, q.top + q.height / 2)?.closest(".tab") === b; });
                  return { inView: n.parentElement.id, seen: n.checkVisibility(), within: r.left >= v.left && r.right <= v.right && r.top >= v.top && r.bottom <= v.bottom, tabs }; }""")
                self.assertEqual(got["inView"], "viewport")
                self.assertEqual(got["seen"], seen)
                if seen:
                    self.assertTrue(got["within"], got)
                self.assertTrue(all(got["tabs"]), got)
                self.assertEqual(errors, [])

    def test_the_note_goes_when_the_record_is_read(self):
        page, errors = self.page()
        page.goto(f"{self.base}/?run={RUN}&pace=0&t=9", wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=30000)
        page.wait_for_function("!document.getElementById('loading-note')")
        self.assertEqual(errors, [])

    def check_failed(self, page, why, href_run=RUN, dev=False):
        page.wait_for_selector("#loading-note.failed")
        got = page.evaluate(NOTE)
        q = "&dev=1" if dev else ""
        self.assertEqual(got["lines"][:2], ["記録を読み込めませんでした", why])
        self.assertEqual(got["links"], [["↻ 開き直す", f"?run={href_run}{q}"], ["☰ 一覧へ", f"?list=1{q}"]])
        return got

    def test_a_server_error_says_why_with_two_links(self):
        page, errors = self.page()
        page.route(EVENTS, lambda route: route.fulfill(status=500, body='{"error": "boom"}', content_type="application/json"))
        page.goto(f"{self.base}/?run={RUN}", wait_until="domcontentloaded")
        self.check_failed(page, "サーバーが記録を返せませんでした (500)。")
        self.assertNotIn("/api/", page.inner_text("#loading-note"))
        # 開き直す reads it again (now it can be read): the note goes
        page.unroute(EVENTS)
        page.click("#loading-note a:has-text('開き直す')")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=30000)
        page.wait_for_function("!document.getElementById('loading-note')")
        self.assertEqual(errors, [])

    def test_a_record_not_there_and_no_server(self):
        page, errors = self.page()
        page.goto(f"{self.base}/?run=20991231-000000-nothing", wait_until="domcontentloaded")
        self.check_failed(page, "この記録は見つかりませんでした。消されたか、名前が違います。", "20991231-000000-nothing")
        page.route(EVENTS, lambda route: route.abort())
        page.goto(f"{self.base}/?run={RUN}", wait_until="domcontentloaded")
        self.check_failed(page, "サーバーにつながりませんでした。HIRAKU が動いているか確かめてください。")
        page.click("#loading-note a:has-text('一覧へ')")
        page.wait_for_url("**/?list=1")
        self.assertEqual(errors, [])

    def test_dev_shows_the_status_and_the_address(self):
        page, errors = self.page()
        page.route(EVENTS, lambda route: route.fulfill(status=500, body="{}", content_type="application/json"))
        page.goto(f"{self.base}/?run={RUN}&dev=1", wait_until="domcontentloaded")
        self.check_failed(page, f"500 /api/runs/{RUN}/events?offset=0", dev=True)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
