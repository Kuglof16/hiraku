"""④ 解析結果 (?tab=grid) as the run ended, and its analyses opened in the viewer inside the tab:

- ④ draws the whole record whatever the replay's time (?t=0 and the end draw the same tiles), at the
  version picked in its head (the last by default; a version's box picks it); no play bar shows;
- a tile, or a test's row in it, opens that analysis in the viewer (viewer/index.html?data=...#field)
  in an iframe over the list, in the same page; 「← 解析結果の一覧へ」 and Esc go back to the list;
- an analysis the viewer has no field for says its verdict (the quantity, value, limit, OK / NG) and
  why in one line on its tile, with no viewer;
- the same in the static site (data/viewer/<run>/<analysis>.json);
- ?video=1 keeps ④ on the replay's clock (test_grid_replay_browser, test_film_compat_browser).

The scripted saved run (every domain) on a server whose answers are the viewer's samples (only the
assembly's is made, tests/web/viewer_fixture.py: the other fields have no sample, so no viewer), the
contract's sample, and the static export of test_static_export. Pictures go to C:/tmp/hiraku-gridviewer/
when HIRAKU_GRIDVIEWER_SHOTS=1.
Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import unittest
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

from hiraku.webserve import export_static as ex
from hiraku.webserve.server import make_server
from tests.web.viewer_fixture import SCRIPTED_RUN, assembly_sample

try:
    from tests.web.pages import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None
SHOTS = Path("C:/tmp/hiraku-gridviewer") if os.environ.get("HIRAKU_GRIDVIEWER_SHOTS") == "1" else None
FRAME = "() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))"
VIEWER = """() => { const v = document.getElementById('grid-viewer');
  return v ? { shown: !v.hidden && v.offsetWidth > 0, src: v.querySelector('iframe').getAttribute('src'), ana: v.dataset.ana ?? null,
               title: v.querySelector('.gv-title').textContent, w: v.offsetWidth, h: v.offsetHeight,
               grid: !document.getElementById('view-grid').hidden } : null; }"""


def server_href(run: str, ana: str, field: str) -> str:
    return f"/viewer/index.html?data={quote(f'/api/runs/{run}/viewer/{ana}', safe='')}#{field}"


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class GridViewerBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        data = assembly_sample(cls.tmp)
        (cls.tmp / "runs").mkdir()
        cls.server = make_server("127.0.0.1", 0, cls.tmp / "runs", quiet=True, launcher=False, viewer_data_dir=data, viewer_build=False)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        if SHOTS:
            SHOTS.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def open(self, query):
        page = self.browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        self.addCleanup(page.close)
        page.goto(f"{self.base}/?pets=0&pace=0&{query}", wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
        page.evaluate("window.hiraku.ready")
        page.evaluate(FRAME)
        return page, errors

    def grid_text(self, page):
        page.evaluate(FRAME)
        return page.evaluate("() => document.getElementById('grid-head').textContent + '|' + document.getElementById('tiles').textContent")

    def test_the_grid_is_the_run_as_it_ended(self):
        a, errors_a = self.open(f"run={SCRIPTED_RUN}&tab=grid&t=0")
        b, errors_b = self.open(f"run={SCRIPTED_RUN}&tab=grid&t=1e6")
        self.assertEqual(self.grid_text(a), self.grid_text(b))
        last = b.evaluate("Math.max(...window.hiraku.state().versions)")  # (b is at the end)
        self.assertGreater(last, 1)
        self.assertEqual(a.inner_text("#grid-head h2"), f"v{last} の判定")
        # no play bar, and the time moving does not change ④
        for p in (a, b):
            self.assertFalse(p.is_visible("#playbar"))
            self.assertFalse(p.is_visible("#pb-track"))
        before = self.grid_text(a)
        a.evaluate("async () => { await window.hiraku.seek(40); }")
        self.assertEqual(self.grid_text(a), before)
        # no tile replays its domain any more
        self.assertEqual(a.locator(".tile .replay-tag").count(), 0)
        if SHOTS:
            b.evaluate(FRAME)
            b.wait_for_timeout(1500)
            b.screenshot(path=str(SHOTS / "grid_list_1440.png"))
        # a version's box picks the version shown
        a.click('#grid-head .vbox[data-ver="1"]')
        a.evaluate(FRAME)
        self.assertIn("v1 の判定", a.inner_text("#grid-head h2"))
        self.assertEqual(a.get_attribute("#grid-head .vbox.cur", "data-ver"), "1")
        self.assertNotEqual(self.grid_text(a), before)
        self.assertEqual(errors_a + errors_b, [])

    # (at the end: window.hiraku.state() is the replay's, and these read the record from it)
    def test_a_tile_opens_the_viewer_in_the_tab(self):
        page, errors = self.open(f"run={SCRIPTED_RUN}&tab=grid&t=1e6")
        pages = len(page.context.pages)
        url = page.url
        page.click('.tile[data-domain="assembly"] .th')
        got = page.evaluate(VIEWER)
        self.assertTrue(got["shown"] and got["grid"], got)
        self.assertEqual(got["src"], server_href(SCRIPTED_RUN, got["ana"], "assembly"))
        self.assertGreater(got["w"] * got["h"], 0.8 * page.evaluate("document.getElementById('view-grid').offsetWidth * document.getElementById('view-grid').offsetHeight"))
        tool = page.evaluate("(id) => window.hiraku.state().analyses[id]?.tool ?? null", got["ana"])
        self.assertIn("組み立て", got["title"])
        # the tool by its name, not its id (the id under ?dev=1: test_judge_words_browser)
        self.assertNotIn(tool, got["title"])
        # the same page: no new tab, the URL as it was
        self.assertEqual((len(page.context.pages), page.url), (pages, url))
        page.click("#grid-viewer .gv-back")
        self.assertFalse(page.evaluate(VIEWER)["shown"])
        self.assertTrue(page.is_visible('.tile[data-domain="assembly"]'))
        # Esc goes back too
        page.click('.tile[data-domain="assembly"] .th')
        self.assertTrue(page.evaluate(VIEWER)["shown"])
        page.keyboard.press("Escape")
        self.assertFalse(page.evaluate(VIEWER)["shown"])
        self.assertEqual(errors, [])

    def test_a_test_row_opens_its_own_analysis(self):
        page, errors = self.open(f"run={SCRIPTED_RUN}&tab=grid&t=1e6")
        page.evaluate("document.querySelector('.tile[data-domain=\"assembly\"] details.more').open = true")
        rows = page.evaluate("""() => [...document.querySelectorAll('.tile[data-domain="assembly"] [data-role="test-row"]')]
          .map((r) => ({ ana: r.dataset.ana, lc: r.dataset.lc, text: r.textContent }))""")
        self.assertTrue(rows)
        # each row: its test's last analysis of the version shown
        last = page.evaluate("""(lcs) => { const s = window.hiraku.state();
          return lcs.map((lc) => s.order.analyses.map((id) => s.analyses[id]).filter((a) => a.load_case_id === lc && a.design_version === s.currentVersion).at(-1)?.analysis_id); }""",
                             [r["lc"] for r in rows])
        self.assertEqual([r["ana"] for r in rows], last)
        self.assertEqual(page.locator('.tile [data-role="viewer-link"]').count(), 0)  # no 「ビューアで見る」 to look for
        row = rows[-1]
        page.click(f'.tile[data-domain="assembly"] [data-role="test-row"][data-ana="{row["ana"]}"]')
        got = page.evaluate(VIEWER)
        self.assertTrue(got["shown"], got)
        self.assertEqual(got["ana"], row["ana"])
        self.assertEqual(got["src"], server_href(SCRIPTED_RUN, row["ana"], "assembly"))
        self.assertEqual(errors, [])

    def test_an_analysis_with_no_viewer_says_its_value_and_why(self):
        page, errors = self.open(f"run={SCRIPTED_RUN}&tab=grid&t=1e6")
        page.click('.tile[data-domain="nvh"] .th')
        page.evaluate(FRAME)
        self.assertFalse(page.evaluate(VIEWER)["shown"])
        line = page.locator('.tile[data-domain="nvh"] [data-role="viewer-none"]')
        self.assertEqual(line.count(), 1)
        value = line.locator("b").text_content()
        why = line.locator("span").text_content()
        self.assertRegex(value, r" (OK|NG)$|判定は記録に無い$")
        self.assertEqual(why, "ビューアは無い: ビューアの見本のデータがまだ無い")
        self.assertNotIn("\n", line.text_content())
        # (the verdict as recorded: its value and limit)
        ana = line.get_attribute("data-ana")
        v = page.evaluate("""(id) => { const s = window.hiraku.state();
          return Object.values(s.verdicts).find((x) => (x.analysis_id ?? s.comparisons[x.comparison_id]?.analysis_id) === id) ?? null; }""", ana)
        if v:
            self.assertTrue(value.startswith(v.get("test_id") or v["load_case_id"]), value)
        if SHOTS:
            page.wait_for_timeout(1500)
            page.screenshot(path=str(SHOTS / "grid_no_viewer_1440.png"))
        page.keyboard.press("Escape")
        self.assertEqual(line.count(), 0)
        self.assertEqual(errors, [])

    def test_video_keeps_the_grid_on_the_replay_clock(self):
        page, errors = self.open(f"run={SCRIPTED_RUN}&tab=grid&t=0&video=1")
        at0 = self.grid_text(page)
        page2, errors2 = self.open(f"run={SCRIPTED_RUN}&tab=grid&t=1e6&video=1")
        self.assertNotEqual(at0, self.grid_text(page2))
        page2.click('.tile[data-domain="assembly"] .th')
        self.assertIsNone(page2.evaluate(VIEWER))
        self.assertEqual(errors + errors2, [])


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class StaticGridViewerBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.web.test_static_export import RUN_ID, QuietHandler, fake_shelf, make_saved

        cls.run_id = RUN_ID
        cls.tmp = Path(tempfile.mkdtemp())
        saved = make_saved(cls.tmp)
        site = cls.tmp / "site"
        ex.export(site / "hiraku", saved_dir=saved, shelf=fake_shelf())
        cls.site = site / "hiraku"
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(site)))
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}/hiraku/"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        if SHOTS:
            SHOTS.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_static_grid_opens_the_viewer_in_the_tab(self):
        page = self.browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        self.addCleanup(page.close)
        page.goto(f"{self.base}?run={self.run_id}&pace=0&tab=grid&t=0", wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
        page.evaluate("window.hiraku.ready")
        page.evaluate(FRAME)
        self.assertFalse(page.is_visible("#playbar"))
        self.assertIn("OK", page.inner_text('.tile[data-domain="strength"] .mark'))  # the end's verdict at t=0
        page.click('.tile[data-domain="strength"] .th')
        got = page.evaluate(VIEWER)
        self.assertTrue(got["shown"], got)
        want = f"viewer/index.html?data={quote(f'../data/viewer/{self.run_id}/{got['ana']}.json', safe='')}#strength"
        self.assertEqual(got["src"], want)
        self.assertTrue((self.site / "data" / "viewer" / self.run_id / f"{got['ana']}.json").is_file())
        if SHOTS:
            page.wait_for_timeout(4000)
            page.screenshot(path=str(SHOTS / "grid_viewer_static_1440.png"))
        page.click("#grid-viewer .gv-back")
        self.assertFalse(page.evaluate(VIEWER)["shown"])
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
