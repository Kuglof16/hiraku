"""The screen for people (no ?dev=1) without the record's ids and English names, on every tab.

What a first-time viewer read and could not follow: 「v—」 and 「費用 —」 in the top row, 「HK1」..
「HK6」 in the footer's list, the calculation's steps by number (「K2」), the record's ids (cmp-0016,
ana-0099), the quantities' and the design's English names (max_steer_angle, cross_B) and the tools'
ids (frame_shell). Checked on the showcase runs 160725 (Claude) and 150416-scripted, at 1440 x 900:

- ① .. ⑤, the shelf and the rules opened at the run's end (?t= past it), the footer's rules' list
  opened (#hooks-more), and ① opened as a link opens it (at the request's moment): the page's
  words as shown (document.body.innerText; what くわしく folds is not read) hold none of ID_FORMS;
- the top row: the version says 「設計はまだ」 at the request's moment, the last version at the end;
  no cost pill where the record has no cost (neither run has one);
- ④'s viewer opened from a tile: its head says the test and the tool by their names;
- under ?dev=1 the ids show (the footer's list says HK1, the shelf K2, the cards ids).

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import make_server
from tests.web.test_browser import ENABLED, sync_playwright
from tests.web.test_layout_lint_browser import REPO_DIR

SHOWCASE = REPO_DIR / "showcase"
RUNS = ("20261105-160725-offroad-4w", "20261105-150416-offroad-4w-scripted")
TABS = ("A1", "A2", "bench", "grid", "A7", "A4", "A5")
ID_FORMS = {
    "record id": re.compile(r"\b[a-z]+-\d{3,}\b"),
    "hooks rule": re.compile(r"\bHK\d\b"),
    "step number": re.compile(r"\bK[0-5]\b"),
    "snake_case": re.compile(r"\b[a-z]{2,}(?:_[a-z0-9]+)+\b"),
}
TEXT = """() => {
  document.getElementById('hooks-more')?.click();
  return document.body.innerText;
}"""


def found(text: str) -> list[str]:
    out = []
    for name, rx in ID_FORMS.items():
        for m in rx.finditer(text):
            out.append(f"{name}: …{text[max(0, m.start() - 20):m.end() + 20]}…".replace("\n", " "))
    return out


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class JudgeWordsBrowserTest(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.server = make_server("127.0.0.1", 0, cls.tmp, quiet=True, saved_dir=SHOWCASE, launcher=False)
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

    def open(self, query: str):
        page = self.browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.base}/?{query}", wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=120000)
        page.evaluate("window.hiraku.ready")
        page.wait_for_timeout(500)
        return page, errors

    def test_every_tab_at_the_end_without_ids(self):
        n = 0
        for run in RUNS:
            for tab in TABS:
                with self.subTest(run=run, tab=tab):
                    page, errors = self.open(f"run={run}&tab={tab}&pace=0&t=99999")
                    try:
                        text = page.evaluate(TEXT)
                        top = page.evaluate("[document.getElementById('pill-version').innerText, document.getElementById('pill-cost').checkVisibility()]")
                        rules = page.evaluate("document.getElementById('hooks').innerText")
                    finally:
                        page.close()
                    n += 1
                    self.assertEqual(errors, [])
                    self.assertEqual(found(text), [])
                    self.assertNotIn("v—", text)
                    self.assertNotIn("費用 —", text)
                    self.assertRegex(top[0], r"^v\d+・繰り返し")
                    self.assertFalse(top[1], "a record without cost: no cost pill")
                    self.assertIn("守るための決まり (6 つ)", rules)
        print(f"\n[judge words] {n} pages without ids")

    def test_request_moment_says_no_design_yet(self):
        for run in RUNS:
            with self.subTest(run=run):
                page, errors = self.open(f"run={run}&tab=A1&pace=0")
                try:
                    page.wait_for_function("document.documentElement.dataset.openedAt === 'request'", timeout=60000)
                    text = page.evaluate(TEXT)
                    pill = page.evaluate("document.getElementById('pill-version').innerText")
                finally:
                    page.close()
                self.assertEqual(errors, [])
                self.assertTrue(pill.startswith("設計はまだ・繰り返し"), pill)
                self.assertEqual(found(text), [])
                self.assertNotIn("v—", text)

    def test_the_viewer_s_head_by_names(self):
        for run in RUNS:
            with self.subTest(run=run):
                page, errors = self.open(f"run={run}&tab=grid&pace=0&t=99999")
                try:
                    page.evaluate("""document.querySelector('.tile[data-domain="strength"]').click()""")
                    page.wait_for_selector("#grid-viewer:not([hidden]) .gv-title", timeout=30000)
                    head = page.evaluate("document.querySelector('#grid-viewer .gv-title').innerText")
                finally:
                    page.close()
                self.assertEqual(errors, [])
                self.assertTrue(head.startswith("強度信頼性・"), head)
                self.assertEqual(found(head), [], head)
                self.assertNotRegex(head, r"・[A-Z]\d+[a-z]?・", head)

    def test_dev_shows_the_ids(self):
        page, errors = self.open(f"run={RUNS[0]}&tab=A4&pace=0&t=99999&dev=1")
        try:
            text = page.evaluate(TEXT)
            rules = page.evaluate("document.getElementById('hooks').innerText")
        finally:
            page.close()
        self.assertEqual(errors, [])
        self.assertIn("HK1", rules)
        self.assertRegex(text, r"\bK2\b")
        page, errors = self.open(f"run={RUNS[1]}&tab=grid&pace=0&t=99999&dev=1")
        try:
            text = page.evaluate(TEXT)
        finally:
            page.close()
        self.assertEqual(errors, [])
        self.assertTrue(found(text), "?dev=1 keeps the record's words")


if __name__ == "__main__":
    unittest.main()
