"""④'s aero tile: the records the analysis department keeps in viewer/fixtures/ for one design (the air's
flow by CFD, and the water of a flooded road; web/js/viewer_link.js VIEWER_EXTRAS), and ②'s rule cards
naming the parts a rule hit (the host's api.partName):

- the aero tile of a run whose designs have the fixtures' design_key (150416-scripted's v5; the server
  says each run's keys, hiraku/webserve/viewer_link.py design_keys) has the two rows; no other run, no
  other tile;
- a row pressed opens it in the viewer inside ④ (viewer/index.html?data=fixtures/aero_cfd.json#aero_cfd,
  viewer/index.html#water), the files read only then (not on the first screen, nor on ④'s list);
- the same in the static site (relative paths, viewer/fixtures/ written by the export);
- ②'s rule card says the parts by the design's names (091217: the battery), not by their ids.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import unittest
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

from hiraku.webserve import export_static as ex
from hiraku.webserve.server import SAVED_DIR, make_server
from tests.web.viewer_fixture import SCRIPTED_RUN

try:
    from tests.web.pages import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None
OTHER_RUN = "20261105-160725-offroad-4w"  # (its designs are not the fixtures')
RULE_RUN = "20261105-091217-offroad-4w"  # (a rule hit the battery: ②'s rule card)
FRAME = "() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))"
VIEWER = """() => { const v = document.getElementById('grid-viewer');
  return v ? { shown: !v.hidden && v.offsetWidth > 0, src: v.querySelector('iframe').getAttribute('src'), extra: v.dataset.extra ?? null,
               title: v.querySelector('.gv-title').textContent, note: v.querySelector('.gv-sample').textContent } : null; }"""
ROWS = """() => [...document.querySelectorAll('#tiles [data-role="viewer-extras"]')].map((u) => ({
  domain: u.closest('.tile').dataset.domain, rows: [...u.querySelectorAll('[data-extra]')].map((li) => [li.dataset.extra, li.textContent]) }))"""
CFD_ROW = "空気の流れ (CFD) — この v5 の形で計算。実験と比べて確かめていない"
WATER_ROW = "冠水路の水 — 見せるための計算で、走破の判定ではない"


class _Pages:
    def start(self):
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])

    def stop(self):
        self.browser.close()
        self.pw.stop()

    def open(self, url):
        page = self.browser.new_page(viewport={"width": 1440, "height": 900})
        errors, fixtures = [], []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("requestfinished", lambda r: "/fixtures/" in r.url and fixtures.append((r.url.rsplit("/", 1)[-1], r.response().status)))
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=90000)
        page.evaluate("window.hiraku.ready")
        page.evaluate(FRAME)
        return page, errors, fixtures


def wait_for(page, cond, ms=30000):
    """Python's side waits (the iframe's own fetch ends after its load)."""
    for _ in range(ms // 100):
        if cond():
            return True
        page.wait_for_timeout(100)
    return cond()


def check_rows_and_viewer(test, page, fixtures, prefix):
    """150416-scripted's ④: the aero tile's two rows; each opens the viewer inside ④ at its page."""
    page.wait_for_function("() => document.querySelectorAll('#tiles [data-role=\"viewer-extras\"] [data-extra]').length === 2", timeout=30000)
    test.assertEqual(page.evaluate(ROWS), [{"domain": "aero", "rows": [["aero_cfd", CFD_ROW], ["water", WATER_ROW]]}])
    test.assertEqual(fixtures, [])  # (nothing read before a row is pressed)
    row = page.locator('.tile[data-domain="aero"] [data-extra="aero_cfd"]')
    test.assertTrue(row.is_visible())
    row.click()
    got = page.evaluate(VIEWER)
    test.assertTrue(got["shown"], got)
    test.assertEqual((got["src"], got["extra"]), (f"{prefix}index.html?data=fixtures/aero_cfd.json#aero_cfd", "aero_cfd"))
    test.assertEqual(got["note"], "この v5 の形で計算。実験と比べて確かめていない")
    test.assertIn("空気の流れ (CFD)", got["title"])
    page.wait_for_function("() => document.querySelector('#grid-viewer iframe').contentDocument?.readyState === 'complete'", timeout=30000)
    test.assertTrue(wait_for(page, lambda: ("aero_cfd.json", 200) in fixtures), fixtures)
    frame = page.frame_locator("#grid-viewer iframe")
    test.assertEqual(page.evaluate("() => document.querySelector('#grid-viewer iframe').contentWindow.location.hash"), "#aero_cfd")
    test.assertIsNotNone(frame)
    page.click("#grid-viewer .gv-back")
    test.assertFalse(page.evaluate(VIEWER)["shown"])
    page.click('.tile[data-domain="aero"] [data-extra="water"]')
    got = page.evaluate(VIEWER)
    test.assertTrue(got["shown"], got)
    test.assertEqual((got["src"], got["extra"]), (f"{prefix}index.html#water", "water"))
    page.wait_for_function("() => document.querySelector('#grid-viewer iframe').contentDocument?.readyState === 'complete'", timeout=30000)
    page.wait_for_function("() => document.querySelector('#grid-viewer iframe').contentWindow.VIEW_DATA?.water", timeout=30000)
    test.assertTrue(wait_for(page, lambda: ("water.js", 200) in fixtures), fixtures)
    page.keyboard.press("Escape")
    test.assertFalse(page.evaluate(VIEWER)["shown"])


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class ViewerExtrasServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        (cls.tmp / "runs").mkdir()
        cls.server = make_server("127.0.0.1", 0, cls.tmp / "runs", quiet=True, launcher=False)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.pages = _Pages()
        cls.pages.start()

    @classmethod
    def tearDownClass(cls):
        cls.pages.stop()
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_aero_tile_of_150416_opens_the_cfd_and_the_water(self):
        page, errors, fixtures = self.pages.open(f"{self.base}/?pets=0&pace=0&run={SCRIPTED_RUN}&tab=grid")
        self.addCleanup(page.close)
        check_rows_and_viewer(self, page, fixtures, "/viewer/")
        self.assertEqual(errors, [])

    def test_no_rows_on_another_run(self):
        page, errors, fixtures = self.pages.open(f"{self.base}/?pets=0&pace=0&run={OTHER_RUN}&tab=grid")
        self.addCleanup(page.close)
        page.wait_for_function("() => document.querySelector('#tiles .tile[data-domain]')", timeout=30000)
        page.wait_for_timeout(1500)  # (the server's info in)
        self.assertEqual(page.evaluate(ROWS), [])
        self.assertEqual((fixtures, errors), ([], []))

    def test_a_rule_card_names_the_parts(self):
        page, errors, _ = self.pages.open(f"{self.base}/?pets=0&pace=0&run={RULE_RUN}&tab=A2&t=1e6")
        self.addCleanup(page.close)
        page.wait_for_selector(".in-parts-hit", timeout=30000)
        cards = page.evaluate("""() => [...document.querySelectorAll('.in-parts-hit')].map((p) => ({
          ids: p.title.split(', '), names: [...p.querySelectorAll('.in-partname')].map((n) => n.textContent) }))""")
        self.assertTrue(cards)
        names = page.evaluate("""() => { const s = window.hiraku.state(); const d = s.designs[s.currentVersion];
          return Object.fromEntries((d?.parts ?? []).map((p) => [p.id, p.name])); }""")
        for c in cards:
            self.assertEqual(c["names"], [names[i] for i in c["ids"]], c)
            self.assertTrue(all(n not in c["ids"] for n in c["names"]), c)
        self.assertIn("電池", "".join(cards[0]["names"]))
        self.assertEqual(errors, [])


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class ViewerExtrasStaticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.web.test_static_export import QuietHandler, fake_shelf

        cls.tmp = Path(tempfile.mkdtemp())
        saved = cls.tmp / "saved"
        for run in (SCRIPTED_RUN, OTHER_RUN):
            (saved / run).mkdir(parents=True)
            shutil.copyfile(SAVED_DIR / run / "events.jsonl", saved / run / "events.jsonl")
        site = cls.tmp / "site"
        ex.export(site / "hiraku", saved_dir=saved, shelf=fake_shelf())
        cls.site = site / "hiraku"
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(site)))
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}/hiraku/"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.pages = _Pages()
        cls.pages.start()

    @classmethod
    def tearDownClass(cls):
        cls.pages.stop()
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_export_has_the_fixtures(self):
        for name in ("aero_cfd.json", "water.js"):
            self.assertTrue((self.site / "viewer" / "fixtures" / name).is_file(), name)
        info = json.loads((self.site / "data" / "viewer.json").read_text(encoding="utf-8"))
        self.assertIn("7caab1a546ac", info["runs"][SCRIPTED_RUN]["design_keys"])
        self.assertNotIn("7caab1a546ac", info["runs"][OTHER_RUN]["design_keys"])

    def test_the_first_screen_reads_no_fixture(self):
        page, errors, fixtures = self.pages.open(self.base)
        self.addCleanup(page.close)
        page.wait_for_timeout(1500)
        self.assertEqual((fixtures, errors), ([], []))

    def test_the_static_aero_tile_opens_the_cfd_and_the_water(self):
        page, errors, fixtures = self.pages.open(f"{self.base}?run={SCRIPTED_RUN}&pace=0&pets=0&tab=grid")
        self.addCleanup(page.close)
        check_rows_and_viewer(self, page, fixtures, "viewer/")
        self.assertEqual(errors, [])

    def test_no_rows_on_another_static_run(self):
        page, errors, fixtures = self.pages.open(f"{self.base}?run={OTHER_RUN}&pace=0&pets=0&tab=grid")
        self.addCleanup(page.close)
        page.wait_for_timeout(1500)
        self.assertEqual(page.evaluate(ROWS), [])
        self.assertEqual((fixtures, errors), ([], []))


if __name__ == "__main__":
    unittest.main()
