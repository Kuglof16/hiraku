"""The present mode (?present=1, the 3D's corner button #expand-btn and Esc) and the workbench's lighter labels.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed, as test_browser.
"""

import copy
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import SAMPLE_EVENTS, make_server
from tests.web.domains_fixture import domain_events
from tests.web.k2_fixture import tilt_events
from tests.web.shell_fixture import shell_events
from tests.web.test_assembly_browser import assembly_events

try:
    from tests.web.pages import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None

# What the page shows of the present mode: visible elements by data-kind, the hidden panels,
# the view's size, the scene and the card's words.
PROBE = """() => {
  const seen = (el) => !!el && el.checkVisibility() && el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().height > 0;
  const kinds = {};
  for (const k of ["number", "legend", "graph", "label"]) kinds[k] = [...document.querySelectorAll(`[data-kind="${k}"]`)].filter(seen).length;
  const rect = (el) => { const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; };
  const vp = document.getElementById("viewport");
  return {
    present: document.body.classList.contains("present"),
    scene: document.body.dataset.presentScene,
    kinds,
    shown: Object.fromEntries(["log", "card-cmp", "card-pred", "hooks", "hooks-more", "chips", "tabs", "toggles", "tests", "present-card"].map((id) => [id, seen(document.getElementById(id))])),
    view: rect(vp),
    legend: seen(document.getElementById("legend")) ? rect(document.getElementById("legend")) : null,
    graph: seen(document.getElementById("graph")) ? rect(document.getElementById("graph")) : null,
    legendIn: document.getElementById("legend").parentElement.id || document.getElementById("legend").parentElement.className,
    card: document.getElementById("present-card").textContent,
    compare: vp.classList.contains("compare"),
  };
}"""


def sample_events():
    return [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]


def overlaps(a, b):
    return a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class PresentBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())  # runs/ is not made: the server has no runs of its own
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

    def open(self, query):
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.goto(f"{self.base}/?{query}", wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=30000)
        page.evaluate("window.hiraku.ready")
        return page, errors

    def probe(self, page):
        page.evaluate("() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
        return page.evaluate(PROBE)

    def assert_present_counts(self, got):
        self.assertTrue(got["present"])
        self.assertEqual(got["kinds"]["number"], 1, got)
        self.assertLessEqual(got["kinds"]["legend"], 1, got)
        self.assertLessEqual(got["kinds"]["graph"], 1, got)
        self.assertLessEqual(got["kinds"]["label"], 3, got)
        for hidden in ("log", "card-cmp", "card-pred", "hooks", "hooks-more", "chips", "tabs", "toggles", "tests"):
            self.assertFalse(got["shown"][hidden], hidden)
        self.assertTrue(got["shown"]["present-card"])
        # the colour bar and the graph are beside the view, not over it
        for box in (got["legend"], got["graph"]):
            if box:
                self.assertFalse(overlaps(box, got["view"]), (box, got["view"]))

    def test_present_mode_hides_the_workbench_and_fills_the_view(self):
        page, errors = self.open("run=sample&pace=0&t=9")
        bench = self.probe(page)
        page.close()
        page, errors2 = self.open("run=sample&pace=0&t=9&present=1")
        got = self.probe(page)
        self.assertEqual(errors + errors2, [])
        self.assertFalse(bench["present"])
        self.assertTrue(bench["shown"]["log"])
        self.assert_present_counts(got)
        self.assertEqual(got["scene"], "step")
        # the 3D is larger than on the workbench, and takes most of the screen
        self.assertGreater(got["view"]["w"], bench["view"]["w"] * 1.3)
        self.assertGreater(got["view"]["w"] * got["view"]["h"], 1920 * 1080 * 0.6)
        self.assertEqual(got["legendIn"], "present-rail")
        # the reaction on the step (the sample's vector, |F| in kN), the verdict beside its criterion, in one sentence
        self.assertIn("反力 (FL)", got["card"])
        self.assertIn("1.8 kN", got["card"])
        self.assertIn("解析 123.6 MPa ≤ 許容 156.7 MPa → OK", got["card"])
        self.assertIn("実行が終わりました", got["card"])
        page.close()

    def test_the_switch_goes_back_to_the_workbench_and_details_open(self):
        page, errors = self.open("run=sample&pace=0&t=9&present=1")
        page.click("#detail-btn")
        detail = self.probe(page)
        self.assertTrue(detail["shown"]["log"])
        self.assertTrue(detail["shown"]["card-cmp"])
        page.click("#detail-btn")
        self.assertFalse(self.probe(page)["shown"]["log"])
        # the corner button of the 3D: back to the original size, then large again, then Esc
        self.assertEqual(page.get_attribute("#expand-btn", "title"), "元の大きさに戻す (Esc)")
        page.click("#expand-btn")
        back = self.probe(page)
        self.assertFalse(back["present"])
        # (the footer's hooks list is folded under 「くわしく」, test_topbar_browser)
        for shown in ("log", "card-cmp", "hooks-more", "tabs", "toggles"):
            self.assertTrue(back["shown"][shown], shown)
        self.assertEqual(back["legendIn"], "viewport")
        self.assertNotIn("present=1", page.url)
        self.assertEqual(page.get_attribute("#expand-btn", "title"), "大きく見る")
        self.assertEqual(page.locator("#viewswitch [data-view]").count(), 0)  # no 見せる / 作業台 words
        page.click("#expand-btn")
        again = self.probe(page)
        self.assert_present_counts(again)
        self.assertIn("present=1", page.url)
        page.keyboard.press("Escape")
        self.assertFalse(self.probe(page)["present"])
        self.assertNotIn("present=1", page.url)
        self.assertEqual(errors, [])
        page.close()

    def test_the_expand_button_sits_in_the_corner_and_not_in_the_film(self):
        """The button is inside the 3D's top right, clear of the toggles; ?video=1 has none."""
        page, errors = self.open("run=sample&pace=0&t=9")
        got = page.evaluate("""() => { const r = (id) => document.getElementById(id).getBoundingClientRect();
            const v = r("viewport"), b = r("expand-btn"), t = r("toggles");
            return { inside: b.left >= v.left && b.right <= v.right && b.top >= v.top && b.bottom <= v.bottom,
                     corner: v.right - b.right < 20 && b.top - v.top < 20, clear: t.right <= b.left || t.width === 0 }; }""")
        self.assertEqual(got, {"inside": True, "corner": True, "clear": True})
        page.close()
        page, errors2 = self.open("run=sample&pace=0&t=9&video=1")
        self.assertFalse(page.evaluate("document.getElementById('expand-btn').checkVisibility()"))
        page.close()
        self.assertEqual(errors + errors2, [])

    def test_four_scenes(self):
        shell, _, _ = shell_events()
        cases = [
            ("step", "run=sample&pace=0&t=0&present=1", sample_events(), ["反力", "→ OK"]),
            ("shell", "run=sample&pace=0&t=0&present=1", shell, ["の最大", "補強の前 (v1)", "補強の後 (v2)", "ガセット"]),
            ("assembly", "run=sample&pace=0&t=0&present=1", assembly_events(focus_a7=True), ["干渉", "1 か所", "手順 3"]),
            ("grid", "run=sample&pace=0&t=0&present=1&tab=grid", domain_events(), ["判定が OK の分野", " / "]),
        ]
        for key, query, events, words in cases:
            with self.subTest(scene=key):
                page, errors = self.open(query)
                page.evaluate("async (events) => { await window.hiraku.load(events, 1e6); }", events)
                got = self.probe(page)
                self.assertEqual(errors, [])
                self.assertEqual(got["scene"], key)
                self.assert_present_counts(got)
                for w in words:
                    self.assertIn(w, got["card"])
                if key == "shell":
                    self.assertTrue(got["compare"])  # before / after side by side
                page.close()

    def test_only_the_picked_wheel_has_words_on_the_workbench(self):
        events, _ = tilt_events()
        finished_t = next(e["t"] for e in events if e["kind"] == "analysis.finished")
        # words=1: the words where nothing is chosen (the largest wheel), as the present mode has them
        # (by default an arrow has words only where chosen: test_declutter_browser)
        page, errors = self.open("run=sample&pace=0&t=0&words=1")
        page.evaluate("async ([ev, t]) => { await window.hiraku.load(ev, t); }", [events, finished_t + 3])
        words = """() => {
          const b = window.hiraku.bench;
          const objs = Object.entries(b.motionArrowObjs);
          return { arrows: objs.filter(([, o]) => o.reaction.visible).length,
                   worded: objs.filter(([, o]) => o.tag.visible && o.tag.element.textContent.trim()).map(([id]) => id),
                   blank: objs.filter(([, o]) => !o.tag.visible || !o.tag.element.textContent.trim()).length,
                   shown: b.wheelShown(), ids: objs.map(([id]) => id) };
        }"""
        got = page.evaluate(words)
        self.assertEqual(got["arrows"], 4)  # every wheel keeps its arrow
        self.assertEqual(len(got["worded"]), 1)
        self.assertEqual(got["blank"], 3)
        self.assertEqual(got["worded"], [got["shown"]])
        other = next(i for i in got["ids"] if i != got["shown"])
        page.evaluate("async (id) => { window.hiraku.bench.pickWheel(id); await window.hiraku.seek(window.hiraku.player.playhead); }", other)
        picked = page.evaluate(words)
        self.assertEqual(picked["worded"], [other])
        # still vectors: of two wheels' reactions only the larger has words
        ev = sample_events()
        ana = next(e for e in ev if e["kind"] == "analysis.finished")["data"]
        ana["vectors"] = ana["vectors"] + [{"kind": "reaction", "at": "FR", "origin_mm": [0.0, -650.0, 0.0], "vector_N": [-600.0, 0.0, 700.0]}]
        page.evaluate("async (events) => { await window.hiraku.load(events, 1e6); }", ev)
        tags = page.evaluate("[...document.querySelectorAll('.lbl-reaction')].map((x) => x.textContent)")
        self.assertEqual(len(tags), 1, tags)
        self.assertIn("FL", tags[0])
        # the labels sit outside the vehicle with a leader line to their point
        self.assertGreater(page.evaluate("document.querySelectorAll('#viewport > .gl-leaders line').length"), 0)
        self.assertEqual(errors, [])
        page.close()

    def test_hooks_say_what_happened_and_resolved(self):
        page, errors = self.open("run=sample&pace=0&t=2.5")
        early = page.evaluate(
            """() => ({ line: document.querySelector('#log li.hook').textContent, done: document.querySelector('#log li.hook').classList.contains('done'),
                        flash: document.getElementById('hookline').textContent, row: document.getElementById('hooks').textContent,
                        now: document.getElementById('now').textContent })"""
        )
        self.assertIn("止めました", early["line"])
        self.assertIn("次に: 先に手計算で予測を記録してから", early["line"])
        self.assertIn("詳しい記録", early["line"])
        self.assertNotIn("解決済み", early["line"])
        self.assertFalse(early["done"])
        # the stop in one line (what was stopped, what the agent is made to do), without the rule's id
        self.assertEqual(early["flash"], "!予測を書く前の解析を止めました。先に予測を書かせます")
        self.assertNotIn("解決済み", early["row"])
        self.assertIn("hooks が止めた", early["now"])
        self.assertNotIn("HK1", early["now"])
        late = page.evaluate(
            """async () => { await window.hiraku.seek(9); return { line: document.querySelector('#log li.hook').textContent,
                  done: document.querySelector('#log li.hook').classList.contains('done'), row: document.getElementById('hooks').textContent,
                  big: document.querySelector('#card-cmp .verdict-big')?.textContent, vline: document.querySelector('#card-cmp .vline')?.textContent }; }"""
        )
        self.assertIn("解決済み", late["line"])  # the prediction came after the stop
        self.assertTrue(late["done"])
        self.assertIn("解決済み", late["row"])
        self.assertEqual(late["big"], "OK")
        self.assertIn("解析 123.6 MPa ≤ 許容 156.7 MPa → OK", late["vline"])
        running = page.evaluate("async () => { await window.hiraku.seek(5.7); return document.getElementById('now').textContent; }")
        self.assertIn("解析しています", running)
        self.assertEqual(errors, [])
        page.close()


if __name__ == "__main__":
    unittest.main()
