"""The boxes laid over the 3D of ③ do not cross: the toggles and the full-screen button, the colour
legend (with its long list of fields), the いま: line, the graph at the bottom left and the cause note.

The layout lint's apart rule (tests/web/layout_lint.js) on:
- showcase/20261105-160725-offroad-4w (N13, done at version 7; its legend lists eight fields of
  the frame's static analyses) at its end (t=1e6) and at the version 7 verdict of R1 (t=4641),
  at 1440 x 900 and at 1920 x 1080 with video=1;
- the grip shape example (tests.web.grip_fixture: a motion, so the graph is out) with a cause
  of a mismatch written (shape example), so the cause note is out over the bottom of the 3D.
Then that the いま: line keeps to one line, cut, with the whole sentence in its title, when the
legend is beside it.

The present mode in the video (present=1&video=1&more=1, the 4K film's scenes of the showcase):
no line cut in half by a frame (the lint's cut rule: the card's last line), the legend's title
clear of the top tick's number on ③ and ④, no colour legend on A7 and no English word in its
joints' legend, and A1's domains all inside the view (it does not scroll in a film).

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku import contract
from hiraku.webserve.server import make_server
from tests.web.test_browser import ENABLED, sync_playwright
from tests.web.test_layout_lint_browser import APART, LINT_JS, REPO_DIR

SHOWCASE = "20261105-160725-offroad-4w"
GRIP_CAUSE = "20261107-120000-grip-cause"
SIZES = ((1440, 900, ""), (1920, 1080, "&video=1"))
FILM = f"run={SHOWCASE}&present=1&video=1&pace=0&more=1"
# the legend's title (its last line) and the top tick's number, by their text boxes
TITLE_TICK = """() => { const l = document.getElementById('legend'); if (l.hidden || !l.offsetWidth) return null;
  const box = (e) => { const r = document.createRange(); r.selectNodeContents(e); const rs = [...r.getClientRects()].filter((b) => b.width > 0.5);
    return rs.length ? { top: Math.min(...rs.map((b) => b.top)), bottom: Math.max(...rs.map((b) => b.bottom)) } : null; };
  const tk = [...l.querySelectorAll('.bar .tk')].find((e) => e.textContent.trim());
  return { title: box(l.querySelector('.ttl')), tick: tk ? box(tk) : null }; }"""


def grip_cause_events() -> list[dict]:
    from tests.web.grip_fixture import grip_events

    events = grip_events(run_id=GRIP_CAUSE)
    end = events.pop()  # run.finished: the cause before it
    cause = json.loads((contract.EXAMPLES_DIR / "cause.json").read_text(encoding="utf-8"))
    events.append({**end, "seq": len(events), "t": 1.0, "kind": "cause.recorded", "agent": "cae.handling", "domain": "handling", "data": cause})
    events.sort(key=lambda e: e["t"])
    events.append({**end, "seq": len(events)})
    for i, e in enumerate(events):
        e["seq"] = i
    return events


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class OverlayFitBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        run = cls.tmp / GRIP_CAUSE
        run.mkdir()
        run.joinpath("events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in grip_cause_events()), encoding="utf-8")
        cls.server = make_server("127.0.0.1", 0, cls.tmp, quiet=True, saved_dir=REPO_DIR / "showcase", launcher=False)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def open(self, query: str, w: int, h: int):
        page = self.browser.new_page(viewport={"width": w, "height": h})
        page.goto(f"{self.base}/?{query}", wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=120000)
        page.evaluate("window.hiraku.ready")
        page.evaluate("document.fonts.ready.then(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))))")
        # the boxes settle over a few frames (the heavy parts read in, the いま: line laid by the toggles' row):
        # wait until the boxes stand still for 0.5 s
        page.evaluate("""async (ids) => { const at = () => JSON.stringify(ids.map((id) => { const e = document.getElementById(id);
            const r = e.getBoundingClientRect(); return [e.hidden, r.left, r.top, r.width, r.height]; }));
          let was = at(), still = 0; for (let i = 0; i < 100 && still < 5; i++) { await new Promise((r) => setTimeout(r, 100)); const now = at(); still = now === was ? still + 1 : 0; was = now; } }""",
                      ["toggles", "legend", "now", "graph", "causenote"])
        return page

    def crossings(self, page) -> list[str]:
        got = page.evaluate(LINT_JS, {"root": "#viewport", "apart": APART})
        return [f"{p['sel']} 「{p['text'][:30]}」 x {p['with']} 「{p.get('withText', '')[:30]}」 {p['by']}" for p in got if p["rule"] == "apart"]

    def test_the_showcase_legend_the_toggles_and_the_now_line_do_not_cross(self):
        for w, h, extra in SIZES:
            for t in ("1e6", "4641"):
                with self.subTest(width=w, t=t):
                    page = self.open(f"run={SHOWCASE}&tab=bench&pace=0&t={t}{extra}", w, h)
                    try:
                        self.assertEqual(self.crossings(page), [])
                        got = page.evaluate("""() => { const n = document.getElementById('now'), l = document.getElementById('legend');
                          const nr = n.getBoundingClientRect(), lr = l.getBoundingClientRect();
                          return { hidden: n.hidden, text: n.textContent, title: n.title, legend: !l.hidden,
                                   beside: nr.bottom > lr.top && nr.top < lr.bottom, height: nr.height, line: parseFloat(getComputedStyle(n).lineHeight) }; }""")
                        self.assertTrue(got["legend"])
                        if not got["hidden"]:
                            self.assertEqual(got["title"], got["text"])  # the whole sentence under the pointer
                            if got["beside"]:  # beside the legend: one line, cut
                                self.assertLess(got["height"], 1.5 * got["line"] + 16, got)
                    finally:
                        page.close()

    def test_the_legends_fields_scroll_and_the_bar_stays_in_sight(self):
        page = self.open(f"run={SHOWCASE}&tab=bench&pace=0&t=4641", 1440, 900)
        try:
            got = page.evaluate("""() => { const l = document.getElementById('legend'), v = document.getElementById('viewport');
              const tg = document.getElementById('toggles').getBoundingClientRect(), lr = l.getBoundingClientRect(), vr = v.getBoundingClientRect();
              const bar = l.querySelector('.bar').getBoundingClientRect(), on = l.querySelector('.variants button.on').getBoundingClientRect();
              const f = l.querySelector('.fields').getBoundingClientRect();
              return { below: lr.top >= tg.bottom, inside: lr.top >= vr.top && lr.bottom <= vr.bottom, bar: bar.top >= lr.top && bar.bottom <= lr.bottom,
                       on: on.top >= f.top - 1 && on.bottom <= f.bottom + 1, n: l.querySelectorAll('.variants button').length }; }""")
            self.assertEqual(got, {"below": True, "inside": True, "bar": True, "on": True, "n": 8})
        finally:
            page.close()

    def lint(self, page, rules=("cut", "apart")) -> list[str]:
        got = page.evaluate(LINT_JS, {"apart": APART, "unseen": True})
        return [f"{p['rule']}: {p['sel']} 「{p['text'][:30]}」 {p.get('with', '')} {p.get('by', '')}" for p in got if p["rule"] in rules]

    def test_the_legends_title_is_clear_of_the_top_tick(self):
        for query, w, h in ((f"run={SHOWCASE}&tab=bench&pace=0&t=4641", 1440, 900), (f"{FILM}&tab=bench&t=4641", 1920, 1080),
                            (f"{FILM}&tab=grid&t=4700", 1920, 1080)):
            with self.subTest(query=query):
                page = self.open(query, w, h)
                try:
                    got = page.evaluate(TITLE_TICK)
                    self.assertIsNotNone(got)
                    self.assertGreaterEqual(got["tick"]["top"], got["title"]["bottom"] - 0.5, got)
                finally:
                    page.close()

    def test_the_film_scenes_cut_no_line_in_half(self):
        for name, q in (("bench", "&tab=bench&t=4641"), ("grid", "&tab=grid&t=4700"), ("A7", "&tabs=A7:4700&t=4703"), ("A1", "&tab=A1&t=20")):
            with self.subTest(scene=name):
                page = self.open(FILM + q, 1920, 1080)
                try:
                    self.assertEqual(self.lint(page), [])
                    if name == "A1":  # nothing of A1 under the frame: the view does not scroll in the film
                        got = page.evaluate("() => { const v = document.getElementById('view-intake'); return [v.scrollHeight, v.clientHeight]; }")
                        self.assertLessEqual(got[0], got[1] + 1, got)
                    if name == "A7":  # the assembly has no colours: no colour legend; the joints' kinds in Japanese
                        got = page.evaluate("""() => ({ legend: !!document.getElementById('legend').offsetWidth,
                          names: [...document.querySelectorAll('.jl-nm')].filter((e) => e.offsetWidth).map((e) => e.textContent) })""")
                        self.assertFalse(got["legend"])
                        self.assertTrue(got["names"])
                        self.assertEqual([n for n in got["names"] if re.search(r"[A-Za-z]{3,}", n)], [])
                finally:
                    page.close()

    def test_the_cause_note_goes_above_the_graph(self):
        for w, h, extra in SIZES:
            with self.subTest(width=w):
                page = self.open(f"run={GRIP_CAUSE}&tab=bench&pace=0&t=7{extra}", w, h)
                try:
                    shown = page.evaluate("() => ['graph', 'causenote'].map((id) => !document.getElementById(id).hidden)")
                    self.assertEqual(shown, [True, True])
                    self.assertEqual(self.crossings(page), [])
                finally:
                    page.close()


if __name__ == "__main__":
    unittest.main()
