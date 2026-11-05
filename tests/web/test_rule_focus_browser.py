"""Rule hits asked of the person: the log's lines for hearing.asked / hearing.answered with
rule_questions / rule_answers, and the parts a hit names lit in 3D (Workbench3D.setRuleFocus,
A2's onRuleFocus and the peek in the intake screen's corner).

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed, as test_browser.
"""

import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku import contract
from hiraku.webserve.server import SAMPLE_EVENTS, make_server

try:
    from tests.web.pages import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None

# The ids a hit can name: a part, a frame member, the whole design, a support, and two with
# nothing in the 3D (a support that is not there, a made-up id).
HIT_A = {"rule_id": "R-shape-1", "parts": ["battery", "cross1"], "text": "電池が横の部材に近すぎる (形の見本)",
         "choices": ["revise_design", "drop_rule"]}
HIT_B = {"rule_id": "R-shape-2", "parts": ["design", "support:FL", "support:XX", "nothing_here"],
         "text": "車輪の周りの空きが足りない (形の見本)"}

# Pixels of the lit colours (workbench3d.js LIT_FILL over the picture, LIT_LINE) in the view.
COUNT_LIT = """(canvas) => {
  const c = document.createElement("canvas");
  c.width = canvas.width; c.height = canvas.height;
  const g = c.getContext("2d");
  g.drawImage(canvas, 0, 0);
  const px = g.getImageData(0, 0, c.width, c.height).data;
  let n = 0;
  for (let i = 0; i < px.length; i += 4) {
    const [r, gr, b, a] = [px[i], px[i + 1], px[i + 2], px[i + 3]];
    if (a > 200 && r > 225 && gr > 80 && gr < 215 && b < 70) n += 1;
  }
  return n;
}"""

# The look of the design's own meshes (colour, glow, opacity), to see that lighting leaves them as they were.
LOOKS = """(b) => {
  const out = [];
  for (const g of [b.model, b.parts, b.wheels]) g.traverse((o) => {
    if (o.isMesh && !o.isLineSegments2 && o.layers.isEnabled(0)) {
      const m = o.material;
      out.push([m.color?.getHexString() ?? "", m.emissive?.getHexString() ?? "", m.opacity, o.visible]);
    }
  });
  return JSON.stringify(out);
}"""


def rule_events(second=True):
    """second: also the later ask of rule hits only (A2 shows the latest ask, so its test leaves it out)."""
    events = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    ex = {n: json.loads((contract.EXAMPLES_DIR / f"{n}.json").read_text(encoding="utf-8")) for n in ("hearing_asked", "hearing_answered")}
    base = {"run_id": events[0]["run_id"], "ts": events[0]["ts"], "seq": 0}
    added = [
        # requirements and rule hits in one ask, then an ask of rule hits only (no questions)
        {"kind": "hearing.asked", "agent": "lead", "data": {**ex["hearing_asked"], "rule_questions": [HIT_A, HIT_B]}},
        {"kind": "hearing.answered", "agent": "lead", "data": {**ex["hearing_answered"], "rule_answers": [
            {"rule_id": HIT_A["rule_id"], "choice": "revise_design"},
            {"rule_id": HIT_B["rule_id"], "choice": "drop_rule", "note": "この実行では外す (形の見本)"}]}},
    ]
    if second:
        added += [
            {"kind": "hearing.asked", "agent": "lead", "data": {"rule_questions": [HIT_A]}},
            {"kind": "hearing.answered", "agent": "lead", "data": {"rule_answers": [{"rule_id": HIT_A["rule_id"], "choice": "drop_rule"}]}},
        ]
    t0 = events[0]["t"]
    # after the design (so the 3D has it), before the analyses
    at = next(i for i, e in enumerate(events) if e["kind"] == "design.created") + 1
    events = events[:at] + [{**base, "t": events[at - 1]["t"] + 0.01 * (i + 1), **ev} for i, ev in enumerate(added)] + events[at:]
    for i, e in enumerate(events):
        e["seq"] = i
    assert events[0]["t"] == t0
    return events


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class RuleFocusBrowserTest(unittest.TestCase):
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

    def open(self, query, second=True):
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.goto(f"{self.base}/?{query}", wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=30000)
        page.evaluate("window.hiraku.ready")
        page.evaluate("async (events) => { await window.hiraku.load(events, 1e6); }", rule_events(second))
        return page, errors

    @staticmethod
    def frames(page, n=2):
        for _ in range(n):
            page.evaluate("() => new Promise((r) => requestAnimationFrame(() => r()))")

    def test_log_lines(self):
        page, errors = self.open("run=sample&pace=0&t=0")
        lines = page.evaluate("() => window.hiraku.state().log.filter((l) => l.kind.startsWith('hearing.')).map((l) => l.text)")
        self.assertEqual(errors, [])
        self.assertEqual(len(lines), 4, lines)
        self.assertIn("足りない要件を 1 個 (値がまだ無い 0・既定値を確かめる 1)・規則の当たりを 2 個聞いた", lines[0])
        self.assertIn("答えがそろった: 1 個 (既定値のまま 1)・規則の当たりの答え: 設計を直す 1・規則を外す 1", lines[1])
        # without questions / answers: only the rule hits, and nothing breaks
        self.assertIn("規則の当たりを 1 個聞いた (設計を直すか、規則を外すか)", lines[2])
        self.assertNotIn("足りない要件", lines[2])
        self.assertIn("規則の当たりの答え: 設計を直す 0・規則を外す 1", lines[3])
        self.assertNotIn("答えがそろった", lines[3])
        page.close()

    def check_light(self, look):
        page, errors = self.open(f"run=sample&pace=0&t=0&tab=bench&look={look}")
        self.frames(page)
        canvas = "window.hiraku.bench.renderer.domElement"
        before = page.evaluate(f"() => ({COUNT_LIT})({canvas})")
        looks = page.evaluate(f"() => ({LOOKS})(window.hiraku.bench)")
        lit = page.evaluate("() => window.hiraku.bench.setRuleFocus(['battery', 'cross1', 'design', 'support:FL', 'support:XX', 'nothing_here', 'FL'])")
        # a part, a member, the whole design (its box) and the FL wheel; the rest are left out quietly
        self.assertEqual(lit, ["battery", "cross1", "design", "support:FL"])
        self.frames(page)
        on = page.evaluate(f"() => ({COUNT_LIT})({canvas})")
        objs = page.evaluate("() => window.hiraku.bench.litObjs.length")
        self.assertGreater(objs, 0)
        self.assertGreater(on, before + 500, (look, before, on))
        # only drawn over: the design's own meshes keep their colour, glow and opacity
        self.assertEqual(page.evaluate(f"() => ({LOOKS})(window.hiraku.bench)"), looks)
        # the running glow (animate) still runs on the members, and the lit copies are not in it
        page.evaluate("() => { window.hiraku.bench.animate(0.25, true); }")
        self.assertEqual(page.evaluate("() => window.hiraku.bench.litObjs.every((o) => o.material !== window.hiraku.bench.members.cross1.mesh.material)"), True)
        self.assertEqual(page.evaluate("() => window.hiraku.bench.setRuleFocus([])"), [])
        page.evaluate("() => { window.hiraku.bench.animate(0, false); }")
        self.frames(page)
        self.assertEqual(page.evaluate("() => window.hiraku.bench.litObjs.length"), 0)
        self.assertEqual(page.evaluate("() => window.hiraku.bench.lit.children.length"), 0)
        off = page.evaluate(f"() => ({COUNT_LIT})({canvas})")
        self.assertLessEqual(abs(off - before), 20, (look, before, off))
        self.assertEqual(page.evaluate(f"() => ({LOOKS})(window.hiraku.bench)"), looks)
        self.assertEqual(errors, [])
        page.close()

    def test_light_and_back_zu(self):
        self.check_light("zu")

    def test_light_and_back_real(self):
        self.check_light("real")

    def test_a2_points_at_a_hit(self):
        """Pointing at a rule hit in A2 lights it on the workbench and shows the peek over the intake screen; leaving puts both back."""
        page, errors = self.open("run=sample&pace=0&t=0", second=False)
        page.click('.tab[data-tab="A2"]')
        page.wait_for_function("document.querySelector('.tab.on')?.dataset.tab === 'A2'")
        card = f'#view-intake .in-rq[data-rule="{HIT_B["rule_id"]}"]'
        page.wait_for_selector(card)
        page.hover(card)
        page.wait_for_function("() => { const p = document.getElementById('rule-peek'); return p && !p.hidden; }")
        self.frames(page)
        got = page.evaluate("""() => { const p = document.getElementById('rule-peek'); const r = p.getBoundingClientRect();
          const v = document.getElementById('view-intake').getBoundingClientRect();
          return { lit: p.dataset.lit, cap: p.textContent, bench: window.hiraku.bench.litShown,
                   inside: r.left >= v.left && r.right <= v.right && r.top >= v.top && r.bottom <= v.bottom }; }""")
        self.assertEqual(got["lit"], "design,support:FL")
        self.assertEqual(got["bench"], ["design", "support:FL"])
        self.assertIn("規則の当たり: 設計の全体・接地点 FL", got["cap"])
        self.assertTrue(got["inside"], got)
        peek = page.evaluate(f"() => ({COUNT_LIT})(window.hiraku.peekBench.renderer.domElement)")
        self.assertGreater(peek, 200)
        # leave: back to nothing
        page.mouse.move(5, 5)
        page.wait_for_function("() => document.getElementById('rule-peek').hidden")
        self.assertEqual(page.evaluate("() => window.hiraku.bench.litShown"), [])
        self.assertEqual(page.evaluate("() => window.hiraku.bench.litObjs.length"), 0)
        # the other hit: a part and a member
        page.hover(f'#view-intake .in-rq[data-rule="{HIT_A["rule_id"]}"]')
        page.wait_for_function("() => document.getElementById('rule-peek').dataset.lit === 'battery,cross1'")
        # on another tab the peek is not shown
        page.click('.tab[data-tab="bench"]')
        self.frames(page)
        self.assertTrue(page.evaluate("() => document.getElementById('rule-peek').hidden"))
        self.assertEqual(errors, [])
        page.close()


if __name__ == "__main__":
    unittest.main()
