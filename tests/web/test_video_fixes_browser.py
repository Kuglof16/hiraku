"""The fixes for the video's pictures: the present card's いま: line (long decimals rounded, two
lines under ?video=1), the cause of a mismatch on a card until its load case's verdict is OK, and
the hooks' stops and a mismatch in orange (red is for a NG judgement only).

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
The events are shape examples (safe_side_fixture, and the contract sample with one more line).
"""

import json
import threading
import unittest

from hiraku import contract
from hiraku.webserve.server import SAMPLE_EVENTS, make_server
from tests.web.safe_side_fixture import safe_side_events
from tests.web.test_browser import ENABLED, sync_playwright

ORANGE = "rgb(178, 107, 0)"  # #b26b00
RED = "rgb(200, 64, 42)"  # --red #c8402a

LONG = ("ロールバーの隙間を確かめた: knee room -69.25456961280076 mm、head 4.172068104172202 mm、"
        "力 12345.678901 N、25.0 kg のまま。" + "上押しの崩れの形をもう一度見て、次の版で確かめる。" * 4)


def said_events():
    """The sample run without its end, then a line an agent said with long decimals (shape example)."""
    events = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    events = [e for e in events if e["kind"] != "run.finished"]
    last = events[-1]
    events.append({"seq": len(events), "t": last["t"] + 0.5, "run_id": last["run_id"], "ts": last["ts"],
                   "kind": "agent.said", "agent": "cae.strength", "text": LONG, "data": {}})
    return events


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class VideoFixesBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server("127.0.0.1", 0, "runs-none", quiet=True, saved_dir=None, launcher=False)
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
        page.goto(f"{self.base}/?{query}", wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=30000)
        page.evaluate("window.hiraku.ready")
        return page, errors

    def test_round_long_decimals(self):
        page, errors = self.open("run=sample&pace=0&t=0")
        got = page.evaluate("""async () => { const { roundLongDecimals: r } = await import('/js/present.js');
            return ['knee room -69.25456961280076 mm', '4.172068104172202', '1333.3333333333335', '0.000123456789',
                    '25.0 kg', '1.118', 'pred-0017.12345', 'v1.23456.7', '12 13.5'].map(r); }""")
        self.assertEqual(got, ["knee room -69.25 mm", "4.172", "1333", "0.0001235", "25.0 kg", "1.118",
                               "pred-0017.12345", "v1.23456.7", "12 13.5"])
        self.assertEqual(errors, [])
        page.close()

    def test_now_line_rounded_and_cut_in_two_lines(self):
        events = said_events()
        for ev in events:
            self.assertEqual(contract.errors("event", ev), [], ev["kind"])
        probe = """() => { const el = document.querySelector('#present-card .pc-now'); const cs = getComputedStyle(el);
            return { text: el.textContent, size: cs.fontSize, lines: el.getBoundingClientRect().height / parseFloat(cs.lineHeight),
                     cut: el.scrollHeight > el.clientHeight + 1 }; }"""
        got = {}
        for video in ("", "&video=1"):
            page, errors = self.open(f"run=sample&pace=0&t=0&present=1{video}")
            page.evaluate("async (events) => { await window.hiraku.load(events, 1e6); }", events)
            got[video] = page.evaluate(probe)
            self.assertEqual(errors, [])
            page.close()
        for g in got.values():
            self.assertIn("knee room -69.25 mm", g["text"])
            self.assertIn("head 4.172 mm", g["text"])
            self.assertIn("12346 N", g["text"])
            self.assertIn("25.0 kg", g["text"])
            self.assertNotIn("69.2545", g["text"])
        self.assertEqual(got[""]["size"], got["&video=1"]["size"])  # the size as it was
        self.assertGreater(got[""]["lines"], 2.5)  # without ?video=1: whole
        self.assertLessEqual(round(got["&video=1"]["lines"], 1), 2.0)  # ?video=1: two lines, cut
        self.assertTrue(got["&video=1"]["cut"])

    def test_cause_note_until_the_verdict_is_ok_and_hooks_orange(self):
        events = safe_side_events()
        t_of = {}
        for ev in events:
            t_of.setdefault((ev["kind"], ev["data"].get("cause_id") or ev["data"].get("rule")), ev["t"])
        t_hook = t_of[("hook.denied", "HK3_cause_before_continue")]
        t_c1 = t_of[("cause.recorded", "cause-0001")]
        t_c2 = t_of[("cause.recorded", "cause-0002")]
        t_ver = next(e["t"] for e in events if e["kind"] == "verdict.recorded")
        probe = """() => { const n = document.getElementById('causenote'); const h = document.getElementById('hookflash');
            const st = (el) => el ? getComputedStyle(el) : null;
            const hookLi = document.querySelector('#log li.hook'); const cmp = document.getElementById('card-cmp');
            return { shown: !n.hidden && n.checkVisibility(), text: n.textContent, size: st(n.querySelector('.tx'))?.fontSize,
                     flashBorder: st(h).borderTopColor, flashColor: st(h).color, flashBg: st(h).backgroundColor,
                     hookLi: hookLi ? st(hookLi).color : null, hookLiBg: hookLi ? st(hookLi).backgroundColor : null,
                     cmpBorder: st(cmp).borderTopColor, cmpClass: cmp.className,
                     hit: [...document.querySelectorAll('#hooks .rule.hit')].map((r) => st(r).color) }; }"""
        for present in ("", "&present=1&video=1"):
            with self.subTest(present=present or "workbench"):
                page, errors = self.open(f"run=sample&pace=0&t=0{present}")
                at = lambda t: (page.evaluate("async ([ev, t]) => { await window.hiraku.load(ev, t); }", [events, t]), page.evaluate(probe))[1]  # noqa: E731
                # the hooks' stop: no cause yet, the band orange
                g = at(t_hook + 0.01)
                self.assertFalse(g["shown"])
                if not present:
                    self.assertEqual((g["flashBorder"], g["flashColor"]), (ORANGE, ORANGE))
                    self.assertEqual(g["hookLi"], ORANGE)
                    self.assertNotIn(RED, (g["flashBorder"], g["flashColor"], g["hookLi"], g["cmpBorder"], *g["hit"]))
                    self.assertIn("warn", g["cmpClass"])
                    self.assertEqual(g["cmpBorder"], ORANGE)
                # the cause written: on its card, and stays while no verdict of the load case is OK
                g = at(t_c1 + 0.01)
                self.assertTrue(g["shown"])
                self.assertIn("食い違いの原因", g["text"])
                self.assertIn("one rail only (shape example)", g["text"])
                self.assertIn("v1", g["text"])
                self.assertGreaterEqual(float(g["size"].rstrip("px")), 18)
                g = at(t_c2 - 0.01)
                self.assertIn("one rail only", g["text"])
                # a newer cause: that one
                g = at(t_c2 + 0.01)
                self.assertIn("the cross members share the load", g["text"])
                # the verdict OK: gone
                g = at(t_ver + 0.01)
                self.assertFalse(g["shown"])
                g = at(1e6)
                self.assertFalse(g["shown"])
                self.assertEqual(errors, [])
                page.close()

    def test_zoom_moves_in_on_the_element_by_time(self):
        """?zoom=cmp:<s>: before that time the stage as it is; from it the comparison card moves to the
        middle and grows (a CSS transform); the same ?t= gives the same transform."""
        probe = """() => { const r = document.getElementById('card-cmp').getBoundingClientRect();
            return { tf: document.getElementById('stage').style.transform, cx: r.left + r.width / 2, cy: r.top + r.height / 2, w: r.width }; }"""
        got = {}
        for q in ("t=9", "t=9&zoom=cmp:20", "t=9&zoom=cmp:5", "t=9&zoom=cmp:8.4", "t=9&zoom=cmp:8.4&again"):
            page, errors = self.open(f"run=sample&pace=0&{q}")
            page.evaluate("() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
            got[q] = page.evaluate(probe)
            self.assertEqual(errors, [])
            page.close()
        plain, before, full, half = got["t=9"], got["t=9&zoom=cmp:20"], got["t=9&zoom=cmp:5"], got["t=9&zoom=cmp:8.4"]
        self.assertEqual(before, plain)  # not yet
        self.assertGreater(full["w"], plain["w"] * 1.5)
        self.assertLess(abs(full["cx"] - 960), 3)
        self.assertLess(abs(full["cy"] - 540), 3)
        self.assertGreater(half["w"], plain["w"])  # on its way
        self.assertLess(half["w"], full["w"])
        self.assertEqual(got["t=9&zoom=cmp:8.4&again"], half)  # time only

    def test_cause_note_same_picture_at_t(self):
        """Opened at ?t= the card is there in the first frame, the same as after a seek."""
        page, errors = self.open("run=sample&pace=0&t=0")
        events = safe_side_events()
        t_c1 = next(e["t"] for e in events if e["kind"] == "cause.recorded")
        page.evaluate("async ([ev, t]) => { await window.hiraku.load(ev, t); }", [events, t_c1 + 0.2])
        first = page.evaluate("document.getElementById('causenote').textContent")
        page.evaluate("async () => { await window.hiraku.seek(1e6); await window.hiraku.seek(%r); }" % (t_c1 + 0.2))
        self.assertEqual(page.evaluate("document.getElementById('causenote').textContent"), first)
        self.assertIn("one rail only", first)
        self.assertEqual(errors, [])
        page.close()


if __name__ == "__main__":
    unittest.main()
