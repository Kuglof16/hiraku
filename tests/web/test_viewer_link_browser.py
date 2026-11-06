"""「ビューアで見る」 (web/js/viewer_link.js) on the screen: on ③'s 「2. 解析」 card and in each row of a ④
tile's くわしく list. The button opens /viewer/index.html?data=/api/runs/<run>/viewer/<analysis>#<field>
in a new tab, with 「別のタブで開きます」 and the sample line beside it while the run's answers are
provisional; an analysis the viewer cannot draw has no button to press and says why in one line. The
viewer opens at that URL with no errors (when viewer/ is there).

Two runs: the scripted saved run (only events.jsonl: the provisional sample, even with the analysis
department's viewer.src.build.for_analysis there), and the same run with its records written back
(a v### folder per version: for_analysis draws the analysis itself). The viewer's sample (viewer/data
is not committed) and the records are made in a temporary folder (tests/web/viewer_fixture.py).
Pictures go to C:/tmp/hiraku-viewer/ when HIRAKU_VIEWER_SHOTS=1.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
"""

from __future__ import annotations

import os
import json
import shutil
import tempfile
import threading
import urllib.request
import unittest
from pathlib import Path
from urllib.parse import quote

from hiraku.webserve import viewer_link
from hiraku.webserve.server import REPO_DIR, make_server
from tests.web.viewer_fixture import SCRIPTED_RUN, assembly_sample, records_run

try:
    from tests.web.pages import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None
SHOTS = Path("C:/tmp/hiraku-viewer") if os.environ.get("HIRAKU_VIEWER_SHOTS") == "1" else None
HAS_VIEWER = (REPO_DIR / "viewer" / "index.html").is_file()
HAS_ENTRY = viewer_link.entry() is not None
RECORDS_RUN = "20261106-000003-offroad-4w-records"  # the scripted run with its records, in a temporary runs/
VIEWER_WAIT = 180000  # ms for the viewer's 3D page (software WebGL; slow when the machine is busy)
LINKS = """(sel) => [...document.querySelectorAll(sel + ' [data-role="viewer-link"]')].map((l) => {
  const a = l.querySelector('a.vbtn'), b = l.querySelector('button');
  return { ana: l.dataset.ana, field: l.dataset.field || null, href: a && a.getAttribute('href'), target: a && a.target,
    rel: a && a.rel, disabled: !!(b && b.disabled), why: (l.querySelector('.vwhy') || {}).textContent || null,
    note: (l.querySelector('.vnote') || {}).textContent || null, sample: (l.querySelector('.vsample') || {}).textContent || null }; })"""


def viewer_href(ana: str, field: str, run: str = SCRIPTED_RUN) -> str:
    return f"/viewer/index.html?data={quote(f'/api/runs/{run}/viewer/{ana}', safe='')}#{field}"


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class ViewerLinkBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        data = assembly_sample(cls.tmp)
        records_run(cls.tmp / "runs", RECORDS_RUN)
        # the analysis department's entry when it is there (viewer_build None)
        cls.server = make_server("127.0.0.1", 0, cls.tmp / "runs", quiet=True, launcher=False,
                                 viewer_data_dir=data)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        # the same runs with the provisional answers (viewer_build False: the field's sample)
        cls.prov_server = make_server("127.0.0.1", 0, cls.tmp / "runs", quiet=True, launcher=False,
                                      viewer_data_dir=data, viewer_build=False)
        threading.Thread(target=cls.prov_server.serve_forever, daemon=True).start()
        cls.prov_base = f"http://127.0.0.1:{cls.prov_server.server_address[1]}"
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        if SHOTS:
            SHOTS.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        for srv in (cls.server, cls.prov_server):
            srv.shutdown()
            srv.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def open(self, query, run=SCRIPTED_RUN, base=None):
        ctx = self.browser.new_context(viewport={"width": 1440, "height": 900})
        self.addCleanup(ctx.close)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{base or self.base}/?run={run}&pace=0&{query}", wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
        page.evaluate("window.hiraku.ready")
        page.evaluate("async () => { await window.hiraku.seek(1e9); }")  # the run's end: every analysis in
        return ctx, page, errors

    def test_bench_card_button_opens_the_viewer_in_a_new_tab(self):
        # provisional answers: the field's sample, said beside the button
        got, tab, tab_errors = self.open_viewer_from_the_bench(SCRIPTED_RUN, "H1-interference", "sample", base=self.prov_base)
        self.assertEqual(got["field"], "assembly")
        self.assertEqual(got["sample"], "見本 (この解析から作った絵ではない)")
        if HAS_VIEWER:
            tab.wait_for_function("window.__ready === true", timeout=VIEWER_WAIT)
            title = tab.evaluate("document.getElementById('title').textContent")
            self.assertTrue(title and "データがない" not in title and "見せられない" not in title, title)
            self.assertEqual(tab.evaluate("window.__field"), "assembly")
            if SHOTS:
                tab.wait_for_timeout(1500)
                tab.screenshot(path=str(SHOTS / "sample_viewer_tab_1440.png"))
        self.assertEqual(tab_errors, [])

    def test_bench_card_of_an_analysis_the_viewer_cannot_draw_says_why(self):
        _, page, errors = self.open("tab=bench&more=1", base=self.prov_base)
        # provisional answers, the scripted run's modal: a field with no sample here (only assembly is made)
        lc = page.evaluate("() => (window.hiraku.state().project.load_cases.find((c) => c.domain === 'nvh') || {}).load_case_id")
        self.assertTrue(lc)
        page.click(f'#tests button[data-lc="{lc}"]')
        page.wait_for_selector('#card-ana [data-role="viewer-link"] button[disabled]', timeout=30000)
        got = page.evaluate(LINKS, "#card-ana")[0]
        self.assertTrue(got["disabled"])
        self.assertIsNone(got["href"])
        self.assertEqual(got["why"], "ビューアの見本のデータがまだ無い")
        self.assertEqual(errors, [])

    @unittest.skipUnless(HAS_ENTRY, "needs viewer.src.build.for_analysis")
    def test_a_saved_run_with_only_events_opens_the_analysis_itself(self):
        # a saved run keeps only events.jsonl: the entry builds the records from the events, no sample
        _, page, errors = self.open("tab=bench&more=1")
        page.click('#tests button[data-lc="H1-interference"]')
        page.wait_for_selector('#card-ana [data-role="viewer-link"] a.vbtn', timeout=30000)
        got = page.evaluate(LINKS, "#card-ana")[0]
        self.assertIsNone(got["sample"])
        with urllib.request.urlopen(self.base + f"/api/runs/{SCRIPTED_RUN}/viewer/{got['ana']}", timeout=120) as r:
            ans = json.loads(r.read())
        self.assertEqual(ans["field"], "assembly")
        self.assertNotEqual(ans.get("source"), viewer_link.SAMPLE_SOURCE)
        self.assertEqual(errors, [])

    def test_tile_test_rows_open_each_analysis_in_the_tab(self):
        # ④'s tiles have no 「ビューアで見る」 button: a test's row opens its analysis over the list
        # (test_grid_viewer_browser), at the field of the tool table in use (viewer.src.build's when
        # there: entry_opening / spray_reach on the assembly field; else viewer_link.TOOL_FIELD)
        _, page, errors = self.open("tab=grid")
        page.wait_for_selector('.tile[data-domain="assembly"] [data-role="test-rows"]', state="attached", timeout=30000)
        self.assertEqual(page.locator('.tile [data-role="viewer-link"]').count(), 0)
        page.evaluate("document.querySelector('.tile[data-domain=\"assembly\"] details.more').open = true")
        rows = page.evaluate("""() => [...document.querySelectorAll('.tile[data-domain="assembly"] [data-role="test-row"]')].map((r) => r.dataset.ana)""")
        self.assertTrue(rows)
        tools = page.evaluate("() => Object.fromEntries(Object.values(window.hiraku.state().analyses).map((a) => [a.analysis_id, a.tool]))")
        table = viewer_link.tool_table()
        for ana in rows:
            want = viewer_link.field_of(tools[ana], table)
            page.click(f'.tile[data-domain="assembly"] [data-role="test-row"][data-ana="{ana}"]')
            if want is None:
                self.assertEqual(page.locator('[data-role="viewer-none"]').get_attribute("data-ana"), ana)
                self.assertIn(viewer_link.NO_FIELD, page.locator('[data-role="viewer-none"] span').text_content())
                page.keyboard.press("Escape")
                continue
            got = page.evaluate("() => { const v = document.getElementById('grid-viewer'); return { shown: !v.hidden, src: v.querySelector('iframe').getAttribute('src') }; }")
            self.assertTrue(got["shown"], ana)
            self.assertEqual(got["src"], viewer_href(ana, want))
            page.click("#grid-viewer .gv-back")
            page.evaluate("document.querySelector('.tile[data-domain=\"assembly\"] details.more').open = true")
        if SHOTS:
            # the whole page (the grid redraws its tiles, so a tile's own element may be replaced mid-shot)
            page.screenshot(path=str(SHOTS / "grid_1440.png"))
        self.assertEqual(errors, [])

    def open_viewer_from_the_bench(self, run, lc, shots=None, base=None):
        """③ on run, test lc: the card's button (one), then the tab it opens; returns (link, tab, errors).
        (more=1: ③'s cards, folded under くわしく by default, open)"""
        ctx, page, errors = self.open("tab=bench&more=1", run, base)
        page.click(f'#tests button[data-lc="{lc}"]')
        # the card drawn for this test (not still the test shown before)
        page.wait_for_function("""(lc) => { const l = document.querySelector('#card-ana [data-role="viewer-link"] a.vbtn');
          const a = l && window.hiraku.state().analyses[l.closest('[data-role="viewer-link"]').dataset.ana];
          return !!a && a.load_case_id === lc; }""", arg=lc, timeout=30000)
        links = page.evaluate(LINKS, "#card-ana")
        self.assertEqual(len(links), 1)
        got = links[0]
        self.assertEqual(got["href"], viewer_href(got["ana"], got["field"], run))
        self.assertEqual(got["target"], "_blank")
        self.assertIn("noopener", got["rel"])
        self.assertEqual(got["note"], "別のタブで開きます")
        if SHOTS and shots:
            page.locator("#card-ana").screenshot(path=str(SHOTS / f"{shots}_card.png"))
            page.screenshot(path=str(SHOTS / f"{shots}_bench_1440.png"))
        with ctx.expect_page(timeout=30000) as info:
            page.click('#card-ana a.vbtn')
        tab = info.value
        tab_errors = []
        tab.on("pageerror", lambda e: tab_errors.append(str(e)))
        tab.wait_for_load_state("domcontentloaded")
        self.assertEqual(tab.url, (base or self.base) + viewer_href(got["ana"], got["field"], run))
        self.assertEqual(errors, [])
        return got, tab, tab_errors

    @unittest.skipUnless(HAS_ENTRY and HAS_VIEWER, "needs viewer.src.build.for_analysis and viewer/")
    def test_a_run_with_its_records_opens_the_analysis_itself(self):
        got, tab, tab_errors = self.open_viewer_from_the_bench(RECORDS_RUN, "H1-interference", "records")
        self.assertEqual(got["field"], "assembly")
        self.assertIsNone(got["sample"])  # made from this analysis: no sample line
        tab.wait_for_function("window.__ready === true", timeout=VIEWER_WAIT)
        self.assertEqual(tab.evaluate("window.__field"), "assembly")
        title = tab.evaluate("document.getElementById('title').textContent")
        self.assertTrue(title and "見せられない" not in title and "データがない" not in title, title)
        answer = tab.evaluate(f"fetch('/api/runs/{RECORDS_RUN}/viewer/{got['ana']}').then((r) => r.json())")
        self.assertEqual(answer["field"], "assembly")
        self.assertNotEqual(answer.get("source"), viewer_link.SAMPLE_SOURCE)
        if SHOTS:
            tab.wait_for_timeout(1500)
            tab.screenshot(path=str(SHOTS / "records_viewer_tab_1440.png"))
        self.assertEqual(tab_errors, [])

    @unittest.skipUnless(HAS_VIEWER, "needs viewer/")
    def test_the_viewer_page_says_why_when_the_answer_has_no_field(self):
        ctx = self.browser.new_context(viewport={"width": 1440, "height": 900})
        self.addCleanup(ctx.close)
        tab = ctx.new_page()
        errors = []
        tab.on("pageerror", lambda e: errors.append(str(e)))
        tab.goto(self.base + viewer_href("ana-9999999", "assembly", RECORDS_RUN), wait_until="domcontentloaded")
        tab.wait_for_function("document.getElementById('title').textContent === '見せられない解析'", timeout=60000)
        why = tab.evaluate("document.getElementById('source').textContent")
        self.assertTrue(why and "\n" not in why, why)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
