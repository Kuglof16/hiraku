"""The crash tool's note on the break angle's edge on the screen (web/js/edge_note.js), and the
analysis' assumptions under ③'s 2. 解析 card.

- parseBreakBand reads the note in break_band_note's form (the quantities whose verdict turns, the
  band, the members near the break angle) and nothing else.
- ③: the assumptions folded under くわしく (解析の前提), shut at first, the English as written, the
  note as one Japanese line; 4. 判定 says 境目 in orange (not red) and names the member near it.
- ④: the tile says 境目 near its number.

The records: tests/web/c1_edge_fixture.py (the contract sample with the note; shape example).
Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
"""

import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import make_server
from tests.web.c1_edge_fixture import BAND_DEG, LIM_DEG, NEAR, PLAIN, band_note, edge_events

try:
    from tests.web.pages import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None

EDGE = "境目: 壊れる角度が 1° 違うと判定が変わる (計算の最後の桁でも変わりうる)"
# the gaps: LIM_DEG minus each member's angle, 2 decimals; rail_L1 by its name before the bracket,
# cross1 (no name in the design) as 部材 and its id
NEAR_LINE = (f"参考: もう少しで壊れる部材がある (左のレール、あと {LIM_DEG - NEAR[0][1]:.2f} ° / "
             f"部材 cross1、あと {LIM_DEG - NEAR[1][1]:.2f} °)")
ORANGE = "rgb(178, 107, 0)"  # app.css --orange (#b26b00)
RED = "rgb(200, 64, 42)"  # app.css --red (#c8402a)

BENCH = """() => {
  const a = document.getElementById("card-ana"), c = document.getElementById("card-cmp");
  const d = a.querySelector("details[data-keep='ana-asm']");
  const e = c.querySelector(".edge-line");
  return {
    summary: d?.querySelector("summary")?.textContent ?? null, open: d?.open ?? null,
    items: d ? [...d.querySelectorAll("li")].map((x) => x.textContent) : null,
    bold: d ? d.querySelectorAll("b").length : null,
    edge: e?.textContent ?? null, edgeColor: e ? getComputedStyle(e).color : null,
    near: c.querySelector(".edge-near")?.textContent ?? null,
    // a line cut short (an ellipsis or a hidden overflow): wider inside than its box
    clipped: [e, c.querySelector(".edge-near")].filter(Boolean).some((x) => x.scrollWidth > x.clientWidth + 1),
  };
}"""


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class C1EdgeBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
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

    def open(self, query, events=None):
        page = self.browser.new_page(viewport={"width": 1440, "height": 810})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.goto(f"{self.base}/?{query}", wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=30000)
        page.evaluate("window.hiraku.ready")
        if events is not None:
            page.evaluate("async (events) => { await window.hiraku.load(events, 1e6); }", events)
        page.evaluate("() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
        return page, errors

    def parse(self, page, text):
        return page.evaluate("async (t) => (await import('/js/edge_note.js')).parseBreakBand(t)", text)

    def test_parse_reads_the_note_and_nothing_else(self):
        page, errors = self.open("run=sample&pace=0&t=0")
        got = self.parse(page, band_note())
        self.assertEqual(got["flips"], ["max_stress"])
        self.assertEqual(got["bandDeg"], BAND_DEG)
        self.assertEqual(got["limDeg"], LIM_DEG)
        self.assertEqual(got["near"], [{"member": m, "deg": d} for m, d in NEAR])
        # C1's two quantities, a band of 0.5 deg, no member near
        two = band_note(flips=("occupant_space_margin >= 0", "frame_split_fraction <= 0.5"), near=[], band=0.5)
        got = self.parse(page, two)
        self.assertEqual(got["flips"], ["occupant_space_margin", "frame_split_fraction"])
        self.assertEqual(got["bandDeg"], 0.5)
        self.assertEqual(got["near"], [])
        self.assertIsNone(got["limDeg"])
        # the verdicts the same: nothing turns, the members near are still read
        got = self.parse(page, band_note(flips=()))
        self.assertEqual(got["flips"], [])
        self.assertEqual(len(got["near"]), 2)
        # not the note
        self.assertIsNone(self.parse(page, PLAIN[0]))
        self.assertIsNone(self.parse(page, ""))
        self.assertIsNone(self.parse(page, None))
        self.assertEqual(errors, [])
        page.close()

    def test_bench_folds_the_assumptions_and_marks_the_edge(self):
        page, errors = self.open("run=sample&tab=bench&pace=0&t=0", edge_events())
        got = page.evaluate(BENCH)
        self.assertEqual(errors, [])
        self.assertEqual(got["summary"], "くわしく (解析の前提)")
        self.assertFalse(got["open"])
        # English as written; the note as the 境目 line; 照合 in judged words, <b> as text (escaped)
        self.assertEqual(got["items"][0], PLAIN[0])
        self.assertEqual(got["items"][1], EDGE)
        self.assertNotIn("照合", got["items"][2])
        self.assertIn("比べ", got["items"][2])
        self.assertIn("<b>", got["items"][2])
        self.assertEqual(got["bold"], 0)
        self.assertEqual(len(got["items"]), 3)
        # 4. 判定: 境目 in orange, not red; the members near it, named
        self.assertEqual(got["edge"], EDGE)
        self.assertEqual(got["edgeColor"], ORANGE)
        self.assertNotEqual(got["edgeColor"], RED)
        self.assertEqual(got["near"], NEAR_LINE)
        self.assertFalse(got["clipped"])
        # ③ folded (the right column's いま / 判定 / 次): 境目 still shows, right under the verdict's row
        head = page.evaluate("""() => {
          const j = document.querySelector('#chain-head [data-role="verdict"]');
          const e = document.querySelector('#chain-head [data-role="edge-head"]');
          const r = (x) => x.getBoundingClientRect();
          return { open: document.getElementById('chain-more').open, text: e?.textContent ?? null,
                   next: e ? e.previousElementSibling === j : null, below: e ? r(e).top >= r(j).bottom - 1 : null,
                   seen: e ? r(e).height > 0 && getComputedStyle(e).visibility !== 'hidden' : null,
                   color: e ? getComputedStyle(e).color : null,
                   clipped: e ? e.scrollWidth > e.clientWidth + 1 : null };
        }""")
        self.assertFalse(head["open"])
        self.assertEqual(head["text"], EDGE)
        self.assertTrue(head["next"] and head["below"] and head["seen"], head)
        self.assertEqual(head["color"], ORANGE)
        self.assertFalse(head["clipped"])
        # the fold stays open across a redraw
        page.click("#card-ana details[data-keep='ana-asm'] summary")
        page.evaluate("async (events) => { await window.hiraku.load(events, 1e6); }", edge_events())
        self.assertTrue(page.evaluate("document.querySelector(\"#card-ana details[data-keep='ana-asm']\").open"))
        page.close()

    def test_no_turn_no_edge_line(self):
        page, errors = self.open("run=sample&tab=bench&pace=0&t=0", edge_events(flips=()))
        got = page.evaluate(BENCH)
        self.assertEqual(errors, [])
        self.assertIsNone(got["edge"])
        self.assertEqual(got["near"], NEAR_LINE)
        self.assertEqual(got["items"][1], NEAR_LINE)  # the note as its 参考 line
        self.assertEqual(page.locator('#chain-head [data-role="edge-head"]').count(), 0)
        page.close()

    def test_without_assumptions_no_fold(self):
        page, errors = self.open("run=sample&tab=bench&pace=0&t=1e6")
        got = page.evaluate(BENCH)
        self.assertEqual(errors, [])
        self.assertIsNone(got["summary"])
        self.assertIsNone(got["edge"])
        self.assertIsNone(got["near"])
        page.close()

    def test_tile_says_the_edge(self):
        page, errors = self.open("run=sample&tab=grid&pace=0&pets=0&t=0", edge_events())
        e = page.wait_for_selector('.tile[data-domain="strength"] .side .edge-line', timeout=10000)
        self.assertEqual(e.text_content(), EDGE)
        self.assertEqual(e.evaluate("(x) => getComputedStyle(x).color"), ORANGE)
        self.assertTrue(e.is_visible())
        self.assertEqual(errors, [])
        page.close()
        page, errors = self.open("run=sample&tab=grid&pace=0&pets=0&t=0", edge_events(flips=()))
        self.assertEqual(page.locator(".tile .edge-line").count(), 0)
        self.assertEqual(errors, [])
        page.close()


if __name__ == "__main__":
    unittest.main()
