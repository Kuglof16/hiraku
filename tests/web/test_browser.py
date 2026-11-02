"""Open the screen in a headless browser and replay the contract sample.

Skipped unless HIRAKU_WEB_BROWSER=1 and Playwright is installed (it is not
in requirements.txt: it brings a browser, and three.js comes from the CDN, so
this test also needs the network).
"""

import os
import threading
import unittest

from hiraku.webserve.server import make_server

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None


@unittest.skipUnless(ENABLED, "set HIRAKU_WEB_BROWSER=1 with Playwright installed")
class BrowserReplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server("127.0.0.1", 0, "runs-none", quiet=True)
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

    def open(self, query):
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.goto(f"{self.base}/?{query}")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=30000)
        page.evaluate("window.hiraku.ready")
        return page, errors

    def test_frozen_frame_at_the_end(self):
        page, errors = self.open("run=sample&t=9")
        got = page.evaluate(
            """() => {
              const s = window.hiraku.state();
              return {
                playhead: window.hiraku.player.playhead,
                applied: window.hiraku.player.applied,
                comparisons: Object.keys(s.comparisons).length,
                judgement: s.judgements["1|strength"]?.state,
                hookLines: document.querySelectorAll("#log li.hook").length,
                cmpCard: document.getElementById("card-cmp").className,
                legendShown: !document.getElementById("legend").hidden,
                members: Object.keys(window.hiraku.bench.members).length,
              };
            }"""
        )
        self.assertEqual(errors, [])
        self.assertEqual(got["playhead"], 9)
        self.assertEqual(got["applied"], 15)
        self.assertEqual(got["comparisons"], 1)
        self.assertEqual(got["judgement"], "green")
        self.assertEqual(got["hookLines"], 1)
        self.assertIn("ok", got["cmpCard"])
        self.assertTrue(got["legendShown"])
        self.assertEqual(got["members"], 7)
        page.close()

    def test_seek_back_rebuilds_from_events(self):
        page, errors = self.open("run=sample&t=9")
        got = page.evaluate(
            """async () => {
              await window.hiraku.seek(2.5);
              const s = window.hiraku.state();
              return { applied: window.hiraku.player.applied, preds: Object.keys(s.predictions).length,
                       denied: s.hooks.denied.length, legendShown: !document.getElementById("legend").hidden };
            }"""
        )
        self.assertEqual(errors, [])
        self.assertEqual(got, {"applied": 5, "preds": 0, "denied": 1, "legendShown": False})
        page.close()

    def test_video_layout_keeps_the_subtitle_band_empty(self):
        page, errors = self.open("run=sample&t=6&video=1")
        got = page.evaluate(
            """() => {
              const bottoms = [...document.querySelectorAll('#stage > *:not(.subband):not([hidden])')]
                .map((el) => el.getBoundingClientRect())
                .filter((r) => r.height > 0)
                .map((r) => r.bottom);
              return { maxBottom: Math.max(...bottoms), status: getComputedStyle(document.getElementById('statusrow')).display };
            }"""
        )
        self.assertEqual(errors, [])
        self.assertLessEqual(got["maxBottom"], 1080 - 170 + 0.5)
        self.assertEqual(got["status"], "none")
        page.close()


if __name__ == "__main__":
    unittest.main()
