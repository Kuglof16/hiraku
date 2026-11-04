"""The 保留 band: a load case without a criterion whose count is not 0 (js/held.js).

On the all-domains grid (the tile's band over its green) and on the workbench (the band above the
3D, the domain's chip marked). The number is the record's (tests.web.held_fixture); a count of 0
shows nothing.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed, as test_browser.
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
from tests.web.held_fixture import HELD_LC, held_events

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None

HELD = "20261104-200000-held"
ZERO = "20261104-200100-held-zero"


def write_run(root: Path, run_id: str, events: list[dict]) -> None:
    (root / run_id).mkdir(parents=True)
    (root / run_id / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class HeldBandBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        write_run(cls.tmp / "runs", HELD, held_events(200.0, 6.0))
        write_run(cls.tmp / "runs", ZERO, held_events(0.0, 0.0))
        cls.server = make_server("127.0.0.1", 0, cls.tmp / "runs", quiet=True, saved_dir=None, launcher=False)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def open(self, run_id, tab):
        page = self.browser.new_page(viewport={"width": 1280, "height": 720})
        page.goto(f"{self.base}/?run={run_id}&tab={tab}&pace=0&pets=0&t=1e6")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
        page.evaluate("window.hiraku.ready")
        return page

    def test_grid_tile_band(self):
        page = self.open(HELD, "grid")
        try:
            tile = page.locator('.tile[data-domain="assembly"]')
            self.assertIn("green", tile.get_attribute("class"))  # judged green ...
            self.assertIn("held", tile.get_attribute("class"))  # ... and held
            band = tile.locator(".heldband")
            self.assertTrue(band.is_visible())
            text = band.inner_text()
            self.assertIn("保留", text)
            self.assertIn("つながり 200 か所が離れている", text)
            self.assertIn("つながりの無い部品 6 個", text)
            row = band.locator(f'[data-held="{HELD_LC}"]')
            self.assertEqual(row.get_attribute("data-value"), "206")
            self.assertEqual(row.get_attribute("data-quantity"), "joint_gap_count")
            # the band is drawn over the tile's top, above its picture
            self.assertLess(band.bounding_box()["y"], tile.locator(".draw").bounding_box()["y"])
            # no other tile has one
            self.assertEqual(page.locator(".tile.held").count(), 1)
        finally:
            page.close()

    def test_workbench_band_and_chip(self):
        page = self.open(HELD, "bench")
        try:
            band = page.locator("#heldband")
            self.assertTrue(band.is_visible())
            text = band.inner_text()
            self.assertIn("保留", text)
            self.assertIn("組み立て", text)
            self.assertIn("つながり 200 か所が離れている", text)
            self.assertEqual(page.locator("#chips .chip.held").count(), 1)
            # the band sits above the 3D
            self.assertLessEqual(band.bounding_box()["y"] + band.bounding_box()["height"], page.locator("#viewport").bounding_box()["y"] + 1)
        finally:
            page.close()

    def test_zero_count_shows_nothing(self):
        for tab in ("grid", "bench"):
            page = self.open(ZERO, tab)
            try:
                self.assertEqual(page.locator(".tile.held").count(), 0)
                self.assertEqual(page.locator("#chips .chip.held").count(), 0)
                self.assertFalse(page.locator("#heldband").is_visible())
            finally:
                page.close()


if __name__ == "__main__":
    unittest.main()
