"""The video's frames as promo/export.py films them (software rendering, a fresh page, ready, one
seek, the screenshot at once), on the saved runs:

- ④'s grid (present=1&video=1): every tile's picture is drawn when the seek returns, not blank;
- ③ with video=1: the tests are the one-line row of chips (no board), the 3D as tall as before the
  board (720 px of the 1920x1080 stage with present=0); ?board=1 gives the board;
- more=1: the right column's くわしく is open, the small くわしく inside the cards and under
  次に変えるもの are shut.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
"""

from __future__ import annotations

import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import make_server
from tests.web.test_tile_pictures_browser import ENABLED, ROOT, sync_playwright

SCRIPTED = "20261105-150416-offroad-4w-scripted"
CLAUDE = "20261105-160725-offroad-4w"
FILM = "&video=1&pace=0&more=1"
# a canvas with something drawn: some pixel not transparent and not one flat colour
NOT_BLANK = """(c) => { const g = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
  let seen = 0; const first = [g[0], g[1], g[2], g[3]];
  for (let i = 0; i < g.length; i += 4) if (g[i + 3] > 0 && (g[i] !== first[0] || g[i + 1] !== first[1] || g[i + 2] !== first[2] || g[i + 3] !== first[3])) seen++;
  return seen > 100; }"""
# the 3D's height before the board (1cffb5e3, ?run=...160725&tab=bench&present=0&video=1&t=3872.5 at 1920x1080)
VIEWPORT_H_BEFORE = 720


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class FilmCompatBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.server = make_server("127.0.0.1", 0, cls.tmp / "runs", quiet=True, saved_dir=ROOT / "showcase", launcher=False)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=[])  # (as promo/export.py without --gpu)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def film(self, query, t):
        """export.py's Capture and shoot: ready, seek(t); returns the page as the frame is taken."""
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.base}/?{query}", wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku !== undefined", timeout=120000)
        page.evaluate("window.hiraku.ready")
        page.evaluate("t => window.hiraku.seek(t)", t)
        self.addCleanup(page.close)
        return page, errors

    def test_grid_pictures_are_there_at_the_seek(self):
        for t in (89.27, 120.0):
            page, errors = self.film(f"run={SCRIPTED}&tab=grid&t={t}&present=1{FILM}", t)
            got = page.evaluate(f"() => [...document.querySelectorAll('canvas.tile3d')].map((c) => [c.dataset.domain, !!c.dataset.key, ({NOT_BLANK})(c)])")
            self.assertEqual(len(got), 8, t)
            self.assertEqual([d for d, key, seen in got if not (key and seen)], [], t)
            self.assertEqual(errors, [])

    def test_video_has_the_row_of_chips_and_the_3d_keeps_its_height(self):
        q = f"run={CLAUDE}&tab=bench&present=0&t=3872.5{FILM}"
        page, errors = self.film(q, 3872.5)
        got = page.evaluate("""() => ({ board: document.getElementById('tests').classList.contains('board'),
          groups: document.querySelectorAll('#tests .tb-group').length, chips: document.querySelectorAll('#tests > button.test').length,
          cases: window.hiraku.state().project.load_cases.length,
          testsH: document.getElementById('tests').getBoundingClientRect().height,
          vp: document.getElementById('viewport').getBoundingClientRect().height })""")
        self.assertFalse(got["board"])
        self.assertEqual(got["groups"], 0)
        self.assertEqual(got["chips"], got["cases"])
        self.assertLessEqual(got["testsH"], 40)  # one line
        self.assertGreaterEqual(got["vp"], VIEWPORT_H_BEFORE - 10)
        self.assertEqual(errors, [])
        # ?board=1: the board, as without video=1
        page, errors = self.film(q + "&board=1", 3872.5)
        self.assertTrue(page.evaluate("document.querySelectorAll('#tests.board .tb-group').length > 0"))
        self.assertEqual(errors, [])

    def test_more_opens_the_outer_kuwashiku_only(self):
        page, errors = self.film(f"run={CLAUDE}&tab=bench&present=0&t=3872.5{FILM}", 3872.5)
        got = page.evaluate("""() => ({ outer: document.getElementById('chain-more').open,
          keeps: [...document.querySelectorAll('#chain-more details[data-keep]')].map((d) => [d.dataset.keep, d.open]) })""")
        self.assertTrue(got["outer"])
        self.assertTrue(got["keeps"])  # (the cards have their small くわしく)
        self.assertEqual([k for k, o in got["keeps"] if o], [])
        # 次に変えるもの on ④ (report_next.js): shut too
        page, errors2 = self.film(f"run={SCRIPTED}&tab=grid&t=1e6&present=1{FILM}", 1e6)
        self.assertEqual(page.evaluate("[...document.querySelectorAll('details[data-keep^=\"report-next-\"]')].filter((d) => d.open).length"), 0)
        self.assertEqual(errors + errors2, [])


if __name__ == "__main__":
    unittest.main()
