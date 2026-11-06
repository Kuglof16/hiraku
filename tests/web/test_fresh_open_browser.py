"""What a first-time viewer of a saved run sees (two places that looked empty or gone back):

- a saved run that is over, opened with no ?t= (nor ?tab=, ?tabs=, ?autoplay=, ?video=1): it opens
  at its end, where ④ has every domain's verdict (the tiles as at ?t=1e6) and ⑤ the vehicle built;
  ▶ there plays it from its start; with ?t= or ?tab= it opens as before;
- ④ replaying a domain (a tile pressed): the top row keeps the whole run's version and status (not
  the domain's replay's), and one line beside it says which domain plays at which version; it goes
  when the domain is closed. Under ?video=1 the top row is as before.

Records: the saved Claude run (showcase/20261105-160725-offroad-4w, v1 to v7) and the contract's
sample (one version; strength from 5.0 s to 6.5 s at pace=0, where the record is still going).
Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import make_server

try:
    from tests.web.pages import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None

ROOT = Path(__file__).resolve().parents[2]
RUN = "20261105-160725-offroad-4w"  # the saved Claude run that ends at v7

TOP = """() => ({
  pill: document.getElementById('pill-version').textContent,
  mode: document.getElementById('mode').innerText,
  line: document.getElementById('domain-playing')?.innerText ?? null,
  ver: window.hiraku.state().currentVersion,
  playhead: window.hiraku.player.playhead, duration: window.hiraku.player.duration,
  playing: window.hiraku.player.playing,
  tiles: [...document.querySelectorAll('#tiles .tile')].map((t) => [t.dataset.domain, t.className]),
  asmNone: document.querySelector('.asm-none') ? document.querySelector('.asm-none').hidden : null,
  asmLabel: document.querySelector('.as-label')?.textContent ?? null,
  openedAt: document.documentElement.dataset.openedAt ?? null,
})"""


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class FreshOpenBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        (cls.tmp / "runs").mkdir()
        cls.server = make_server("127.0.0.1", 0, cls.tmp / "runs", quiet=True, saved_dir=ROOT / "showcase", launcher=False)
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

    def open(self, query):
        page = self.browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.base}/?pets=0&{query}", wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=120000)
        page.evaluate("window.hiraku.ready")
        self.addCleanup(page.close)
        return page, errors

    def top(self, page):
        page.evaluate("() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
        return page.evaluate(TOP)

    # ---- the end first ---------------------------------------------------------------------

    def test_a_saved_run_opens_at_its_end(self):
        page, errors = self.open(f"run={RUN}")
        got = self.top(page)
        self.assertEqual(got["openedAt"], "end")
        self.assertAlmostEqual(got["playhead"], got["duration"], places=3)
        self.assertFalse(got["playing"])
        self.assertTrue(got["pill"].startswith("v7"), got["pill"])
        self.assertIn("完了", got["mode"])
        # ④: the tiles as at the end (?t=1e6), with verdicts in (not the 選んでいない / まだ of 50 s)
        page.click('.tab[data-tab="grid"]')
        end_tiles = self.top(page)["tiles"]
        ref, errors_ref = self.open(f"run={RUN}&tab=grid&t=1e6")
        self.assertEqual(end_tiles, self.top(ref)["tiles"])
        self.assertTrue(any(" green" in c for _, c in end_tiles), end_tiles)
        # ⑤: the assembly is there (its steps), not 組み立ての記録がまだ無い
        page.click('.tab[data-tab="A7"]')
        got = self.top(page)
        self.assertTrue(got["asmNone"], got)
        self.assertRegex(got["asmLabel"] or "", r"^手順 \d+ / \d+$")
        # ▶ at the end (the play bar of ③) plays it from its start
        page.click('.tab[data-tab="bench"]')
        page.click("#pb-play")
        got = self.top(page)
        self.assertTrue(got["playing"])
        self.assertLess(got["playhead"], 5)
        self.assertEqual(errors + errors_ref, [])

    def test_t_or_tab_opens_as_before(self):
        page, errors = self.open(f"run={RUN}&t=50")
        got = self.top(page)
        self.assertAlmostEqual(got["playhead"], 50, places=3)
        self.assertIsNone(got["openedAt"])
        page2, errors2 = self.open(f"run={RUN}&tab=grid")
        got = self.top(page2)
        self.assertIsNone(got["openedAt"])
        self.assertTrue(got["playing"])
        self.assertLess(got["playhead"], 30)
        # the contract's sample with ?autoplay=0: at its start, as before
        page3, errors3 = self.open("run=sample&pace=0&autoplay=0")
        got = self.top(page3)
        self.assertEqual((got["openedAt"], got["playhead"], got["playing"]), (None, 0, False))
        self.assertEqual(errors + errors2 + errors3, [])

    # ---- ④'s domain replay and the top row ----------------------------------------------------

    def test_a_domain_replay_keeps_the_top_row(self):
        page, errors = self.open(f"run={RUN}&tab=grid&t=1e6")
        before = self.top(page)
        self.assertTrue(before["pill"].startswith("v7"), before["pill"])
        self.assertIsNone(before["line"])
        page.click('.tile[data-domain="strength"]')
        got = self.top(page)
        self.assertLess(got["ver"], 7)  # (the domain's replay is at an earlier version)
        self.assertEqual(got["pill"], before["pill"])
        self.assertIn("完了", got["mode"])
        self.assertNotIn("記録の途中", got["mode"])
        self.assertEqual(got["line"], f"④ で 強度信頼性 を再生中 (v{got['ver']})")
        page.click("#domain-head .dh-close")
        got = self.top(page)
        self.assertIsNone(got["line"])
        self.assertEqual((got["pill"], got["mode"]), (before["pill"], before["mode"]))
        self.assertEqual(errors, [])

    def test_the_sample_still_going_at_the_domain_says_its_end(self):
        # the sample's strength is at 5.0 to 6.5 s, where the record says 記録の途中: the top keeps 完了
        page, errors = self.open("run=sample&pace=0&tab=grid&t=1e6")
        page.click('.tile[data-domain="strength"]')
        got = self.top(page)
        self.assertIn("完了", got["mode"])
        self.assertNotIn("記録の途中", got["mode"])
        self.assertEqual(got["line"], "④ で 強度信頼性 を再生中 (v1)")
        # ?domain= opens it the same way
        page2, errors2 = self.open("run=sample&pace=0&tab=grid&domain=strength&t=6.0")
        got = self.top(page2)
        self.assertIn("完了", got["mode"])
        self.assertEqual(got["line"], "④ で 強度信頼性 を再生中 (v1)")
        self.assertEqual(errors + errors2, [])

    def test_video_keeps_the_top_row_as_before(self):
        page, errors = self.open("run=sample&pace=0&tab=grid&domain=strength&t=6.0&video=1")
        got = self.top(page)
        self.assertIsNone(got["line"])
        self.assertIn("記録の途中", got["mode"])
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
