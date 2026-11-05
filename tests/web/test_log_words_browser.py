"""The log's words for a tool and a load case: a tool by labels.js's name, else its title in the
record (tool.checked, up to the bracket), else its name; a load case by its title in the record.
A hearing whose asked questions got neither an answer nor a default says how many, and what
that is (答えが無いまま).

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed, as test_browser.
"""

import copy
import json
import os
import threading
import unittest

from hiraku import contract
from hiraku.webserve.server import SAMPLE_EVENTS, make_server

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None


def sample():
    return [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]


def with_check_and_hearing():
    """The sample with frame_static's tool.checked (title with a bracket) before its analysis, and a
    hearing of two questions of which one is answered (the other: no answer, no default)."""
    events = sample()
    ex = {n: json.loads((contract.EXAMPLES_DIR / f"{n}.json").read_text(encoding="utf-8")) for n in ("hearing_asked", "hearing_answered")}
    # (the shape of k2_fixture's tool.checked)
    check = {"name": "frame_static", "title": "フレームの静的 (3D の梁)", "domain": "strength", "base": "K1", "tests": ["R1"],
             "origin": "builtin", "usable": True, "checks": []}
    asked = copy.deepcopy(ex["hearing_asked"])
    q0 = asked["questions"][0]
    asked["questions"] = [q0, {**copy.deepcopy(q0), "req_key": "Q99"}]
    base = {"run_id": events[0]["run_id"], "ts": events[0]["ts"], "seq": 0}
    at = next(i for i, e in enumerate(events) if e["kind"] == "design.created") + 1
    t = events[at - 1]["t"]
    added = [
        {**base, "t": t + 0.01, "kind": "hearing.asked", "agent": "lead", "data": asked},
        {**base, "t": t + 0.02, "kind": "hearing.answered", "agent": "lead", "data": ex["hearing_answered"]},
        {**base, "t": t + 0.03, "kind": "tool.checked", "agent": "system", "data": check},
    ]
    events = events[:at] + added + events[at:]
    for i, e in enumerate(events):
        e["seq"] = i
    return events


LINES = """async (events) => {
  await window.hiraku.load(events, 1e6);
  const s = window.hiraku.state();
  return { lines: s.log.map((l) => l.text), title: s.project.load_cases.find((c) => c.load_case_id === "R1-step100").title };
}"""


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class LogWordsBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server("127.0.0.1", 0, "runs-none", quiet=True, saved_dir=None)
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

    def lines(self, events):
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.base}/?run=sample&pace=0&t=0")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=30000)
        page.evaluate("window.hiraku.ready")
        got = page.evaluate(LINES, events)
        page.close()
        self.assertEqual(errors, [])
        return got

    def test_the_fixture_is_the_contract_s(self):
        for ev in with_check_and_hearing():
            self.assertEqual(contract.errors("event", ev), [], ev["kind"])

    def test_tool_and_load_case_by_the_record_s_words(self):
        got = self.lines(with_check_and_hearing())
        started = [x for x in got["lines"] if "で解析を始めた" in x]
        self.assertEqual(len(started), 1, got["lines"])
        self.assertIn(f"フレームの静的 で解析を始めた: 「{got['title']}」", started[0])
        self.assertNotIn("frame_static", started[0])
        self.assertNotIn("(R1-step100)", started[0])

    def test_without_tool_checked_the_name_as_written(self):
        got = self.lines(sample())
        started = [x for x in got["lines"] if "で解析を始めた" in x]
        self.assertIn(f"frame_static で解析を始めた: 「{got['title']}」", started[0])

    def test_the_hearing_says_what_no_answer_is(self):
        got = self.lines(with_check_and_hearing())
        answered = [x for x in got["lines"] if "答えがそろった" in x]
        self.assertEqual(len(answered), 1, got["lines"])
        self.assertIn("答えが無いまま 1 個 (聞いたが、答えも既定値も無いので要件に入っていない)", answered[0])


    def caption(self, pred_change):
        """The prediction card's line over the bars, for the sample with its prediction changed."""
        events = sample()
        pred = next(e for e in events if e["kind"] == "prediction.recorded")
        pred["data"].update(pred_change)
        self.assertEqual(contract.errors("event", pred), [])
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.base}/?run=sample&pace=0&t=0")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=30000)
        page.evaluate("window.hiraku.ready")
        got = page.evaluate("""async (events) => {
            await window.hiraku.load(events, 5.2);
            await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
            const c = document.querySelector('#card-pred .terms-cap');
            return { cap: c?.textContent ?? null, kind: c?.dataset.terms ?? null,
                     rows: [...document.querySelectorAll('#card-pred .terms > span:not(.barw)')].map((x) => x.textContent) };
        }""", events)
        page.close()
        self.assertEqual(errors, [])
        return got

    def test_additive_terms_say_their_share_of_the_prediction(self):
        # the sample's prediction: a term M that is not an input (registry.py: an additive term, value / prediction)
        got = self.caption({})
        self.assertEqual(got["kind"], "share")
        self.assertIn("最大応力のうち、その項が占める割合 (項の値 ÷ 最大応力)", got["cap"])
        self.assertEqual(got["rows"], ["M", "100 %"])

    def test_input_terms_say_their_elasticity(self):
        # occupant.knee_room's shape (room = L - L_bk, no additive terms): each input's elasticity, signed
        L, Lbk = 60.0, 600.0 * 0.11  # (this test's own numbers)
        room = L - Lbk
        got = self.caption({"quantity": "knee_room", "unit": "mm", "value": room, "formula_id": "occupant.knee_room", "expression": "room = L - L_bk",
                            "inputs": {"L": {"value": L, "unit": "mm"}, "L_bk": {"value": Lbk, "unit": "mm"}},
                            "terms": [{"name": "L", "value": L, "unit": "mm", "share": L / room}, {"name": "L_bk", "value": Lbk, "unit": "mm", "share": -Lbk / room}]})
        self.assertEqual(got["kind"], "elasticity")
        self.assertIn("その入力を 1 % 大きくすると、ひざの前のすき間が何 % 変わるか", got["cap"])
        # L and L_bk have no name in labels.js (FORMULA_INPUT_JA): as written
        self.assertEqual(got["rows"], ["L", "-1000 %", "L_bk", "+1100 %"])


if __name__ == "__main__":
    unittest.main()
