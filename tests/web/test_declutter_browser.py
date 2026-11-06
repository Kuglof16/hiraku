"""What shows first, and what is folded (B): the right column of ③ shows いま / 判定 / 次 (one line
each) and folds the four steps' cards under くわしく, which open with all of them; an arrow in the
3D has words only where it is chosen (pressed, or the pointer near it), all of them with ?words=1
or the present mode as before.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed, as test_browser.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from hiraku.webserve.server import SAMPLE_EVENTS
from tests.web.test_layout_lint_browser import ENABLED, READY, start_servers

try:
    from tests.web.pages import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


def sample_events() -> list[dict]:
    return [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]


SEEN = "(el) => !!el && el.checkVisibility() && el.getBoundingClientRect().height > 0"
WORDS = "() => [...document.querySelectorAll('#viewport .gl-labels .lbl-vec')].filter((e) => e.style.display !== 'none').map((e) => e.textContent)"
# an arrow's middle on the screen (page px): the bench's own list of the arrows (vecTips)
TIP = """(id) => { const b = window.hiraku.bench; const t = b.vecTips.find((x) => x.id === id);
  const p = b.screenOf(t.at); const r = b.renderer.domElement.getBoundingClientRect();
  const k = r.width / b.size.w; return { x: r.left + p.x * k, y: r.top + p.y * k }; }"""


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class DeclutterBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.servers, cls.bases = start_servers(cls.tmp)
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

    def open(self, query=""):
        """The sample run at its end (every step written), on ③."""
        page = self.browser.new_page(viewport={"width": 1440, "height": 810})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.bases['saved']}/?run=sample&autoplay=0&tab=bench{query}", wait_until="domcontentloaded")
        page.wait_for_function(READY, timeout=60000)
        page.evaluate("window.hiraku.ready")
        page.evaluate("async (ev) => { await window.hiraku.load(ev, 1e6); }", sample_events())
        self.addCleanup(page.close)
        return page, errors

    def test_the_first_lines_and_the_folded_cards(self):
        page, errors = self.open()
        head = page.evaluate(
            f"""() => {{ const seen = {SEEN};
              const rows = [...document.querySelectorAll('#chain-head .ch-row')];
              return {{ rows: rows.map((r) => r.querySelector('small').textContent), seen: rows.every(seen),
                        verdict: document.querySelector('#chain-head .ch-verdict')?.textContent ?? null,
                        open: document.getElementById('chain-more').open,
                        cards: ['card-pred', 'card-ana', 'card-cmp'].map((id) => seen(document.getElementById(id))) }}; }}"""
        )
        self.assertEqual(head["rows"], ["いま", "判定", "次"])
        self.assertTrue(head["seen"])
        self.assertIn(head["verdict"], ("OK", "NG"))
        self.assertFalse(head["open"])
        self.assertEqual(head["cards"], [False, False, False])  # folded
        # opened: every card there, with what it had (nothing is dropped)
        page.click("#chain-more > summary")
        cards = page.evaluate(f"() => ['card-pred', 'card-ana', 'card-cmp'].map((id) => [({SEEN})(document.getElementById(id)), document.getElementById(id).textContent])")
        self.assertTrue(all(seen for seen, _ in cards))
        self.assertIn("1. 予測", cards[0][1])
        self.assertIn("2. 解析", cards[1][1])
        self.assertIn("3. 比べる", cards[2][1])
        # it stays open across redraws
        page.evaluate("window.hiraku.seek(window.hiraku.player.playhead)")
        self.assertTrue(page.evaluate("document.getElementById('chain-more').open"))
        self.assertEqual(errors, [])

    def test_more_param_opens_them(self):
        page, errors = self.open("&more=1")
        self.assertTrue(page.evaluate("document.getElementById('chain-more').open"))
        self.assertEqual(errors, [])

    def test_arrow_words_only_where_chosen(self):
        page, errors = self.open()
        page.wait_for_function("window.hiraku.bench.vecTips.length > 0")
        self.assertEqual(page.evaluate(WORDS), [])  # none by default
        ids = page.evaluate("window.hiraku.bench.vecTips.map((t) => t.id)")
        tip = page.evaluate(TIP, ids[0])
        away = page.evaluate("(() => { const r = window.hiraku.bench.renderer.domElement.getBoundingClientRect(); return { x: r.left + 4, y: r.top + 4 }; })()")
        # (the arrow's middle can lie under the play bar and the overlays: they are put out of the way)
        page.add_style_tag(content="#playbar, #viewport > :not(canvas):not(.gl-labels):not(.gl-leaders) { visibility: hidden !important; }")
        self.assertEqual(page.evaluate("([x, y]) => document.elementFromPoint(x, y) === window.hiraku.bench.renderer.domElement", [tip["x"], tip["y"]]), True)
        # pressed: that arrow's words, and they stay when the pointer leaves
        page.mouse.click(tip["x"], tip["y"])
        page.wait_for_function(f"({WORDS})().length > 0")
        self.assertEqual(len(page.evaluate(WORDS)), 1)
        page.mouse.move(away["x"], away["y"])
        self.assertEqual(len(page.evaluate(WORDS)), 1)
        # pressed away from every arrow: gone again
        page.mouse.click(away["x"], away["y"])
        page.wait_for_function(f"({WORDS})().length === 0")
        # the pointer near it: its words while it stays
        page.mouse.move(tip["x"], tip["y"])
        page.wait_for_function(f"({WORDS})().length > 0")
        page.mouse.move(away["x"], away["y"])
        page.wait_for_function(f"({WORDS})().length === 0")
        self.assertEqual(errors, [])

    def test_words_param_shows_them_all(self):
        page, errors = self.open("&words=1")
        page.wait_for_function(f"({WORDS})().length > 0")
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
