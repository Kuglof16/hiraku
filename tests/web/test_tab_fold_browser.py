"""The tabs ① ② ⑤ ツールの棚 守るルール, as ③: on top いま / 大事 / 次 (one line each) and a くわしく;
the rest of the tab folded until it is opened (?more=1 opens them all), opened all of it as before.
The tool shelf's rows keep their words inside the row.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed, as test_browser.
"""

from __future__ import annotations

import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import make_server
from hiraku.webserve.shelf import Shelf
from tests.web.test_layout_lint_browser import ENABLED, READY, start_servers
from tests.web.test_shelf_browser import ARCHETYPES, FORMULAS, SHELF_TOOLS, check, tool

# (shape example) a tool with thirty checks: its mark 確かめ 30 / 30 is the widest a row has
MANY = tool("brake_many", "急制動 (形の例)", "handling", "K2", ["S4", "S4b"],
            [check(f"case {i}", 1.0, 1.0, True) for i in range(30)], ["frame_static.rail_bending"])

try:
    from tests.web.pages import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

HEAD = {"A1": "tab-head-intake", "A2": "tab-head-intake", "A5": "tab-head-intake", "A4": "tab-head-A4", "A7": "tab-head-A7"}
# what each tab folds (when the sample's record has it on the screen)
FOLDED = {"A1": ".in-side > .in-block:first-child", "A2": ".in-right", "A5": ".in-drop", "A4": ".sh-tests", "A7": ".asm-steps .fz"}
GOT = """([head, sel]) => { const el = document.getElementById(head); const view = el.parentElement;
  const seen = (e) => !!e && e.checkVisibility() && e.getBoundingClientRect().height > 0;
  return { rows: [...el.querySelectorAll('.ch-row')].map((r) => r.querySelector('small').textContent),
           rowsSeen: [...el.querySelectorAll('.ch-row')].every(seen), texts: [...el.querySelectorAll('.ch-row')].map((r) => r.textContent),
           folded: view.classList.contains('folded'), there: !!view.querySelector(sel), seen: seen(view.querySelector(sel)),
           button: el.querySelector('.th-more')?.textContent ?? null }; }"""


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class TabFoldBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.servers, cls.bases = start_servers(cls.tmp)
        # the shelf's tab on a small stand-in shelf (test_shelf_browser's shape examples), not the solvers
        shelf = make_server("127.0.0.1", 0, cls.tmp / "shelf-runs", quiet=True, saved_dir=None, launcher=False,
                            shelf=Shelf(lambda: {"tools": [*SHELF_TOOLS, MANY], "formulas": FORMULAS, "archetypes": ARCHETYPES}))
        threading.Thread(target=shelf.serve_forever, daemon=True).start()
        cls.servers = [*cls.servers, shelf]
        cls.shelf_base = f"http://127.0.0.1:{shelf.server_address[1]}"
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

    def open(self, tab, query=""):
        page = self.browser.new_page(viewport={"width": 1440, "height": 810})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        base = self.shelf_base if tab == "A4" else self.bases["saved"]
        page.goto(f"{base}/?run=sample&tab={tab}&t=999999{query}", wait_until="domcontentloaded")
        page.wait_for_function(READY, timeout=60000)
        page.evaluate("window.hiraku.ready")
        page.wait_for_function(f"document.querySelectorAll('#{HEAD[tab]} .ch-row').length === 3")
        self.addCleanup(page.close)
        return page, errors

    def test_first_lines_and_the_rest_folded(self):
        for tab in ("A1", "A2", "A5", "A4", "A7"):
            with self.subTest(tab=tab):
                page, errors = self.open(tab)
                if tab == "A4":
                    page.wait_for_function("document.querySelectorAll('#shelf-host .sh-tool').length > 0", timeout=30000)
                got = page.evaluate(GOT, [HEAD[tab], FOLDED[tab]])
                self.assertEqual(got["rows"][0], "いま")
                self.assertEqual(got["rows"][2], "次")
                self.assertTrue(got["rowsSeen"])
                self.assertTrue(all(t.strip() for t in got["texts"]))
                self.assertTrue(got["folded"])
                self.assertEqual(got["button"], "くわしく")
                if got["there"]:
                    self.assertFalse(got["seen"])  # folded
                # opened: the rest is there again
                page.click(f"#{HEAD[tab]} .th-more")
                page.wait_for_function(f"!document.getElementById('{HEAD[tab]}').parentElement.classList.contains('folded')")
                opened = page.evaluate(GOT, [HEAD[tab], FOLDED[tab]])
                self.assertEqual(opened["button"], "たたむ")
                if opened["there"]:
                    self.assertTrue(opened["seen"])
                self.assertEqual(errors, [])

    def test_more_param_opens_them(self):
        for tab in ("A1", "A4"):
            page, errors = self.open(tab, "&more=1")
            self.assertFalse(page.evaluate(GOT, [HEAD[tab], FOLDED[tab]])["folded"], tab)
            self.assertEqual(errors, [], tab)

    def test_shelf_rows_keep_their_words_inside(self):
        for more in ("", "&more=1"):
            page, errors = self.open("A4", more)
            page.wait_for_function("document.querySelectorAll('#shelf-host .sh-chk').length > 0", timeout=30000)
            out = page.evaluate(
                """() => [...document.querySelectorAll('#shelf-host .sh-tool')].flatMap((row) => {
                  const r = row.getBoundingClientRect();
                  return [...row.querySelectorAll('.sh-chk, .sh-base, .sh-f')].filter((e) => e.checkVisibility())
                    .filter((e) => e.getBoundingClientRect().right > r.right + 0.5 || e.scrollWidth > e.clientWidth + 1 && !e.classList.contains('sh-f'))
                    .map((e) => e.className + ': ' + e.textContent); })"""
            )
            self.assertEqual(out, [], more)
            self.assertEqual(errors, [], more)


if __name__ == "__main__":
    unittest.main()
