"""The replay on the event clock (js/player.js, the default): the recorded gaps are dropped and
each event stays on screen for its own short time, so a run of an hour replays in one to two
minutes. The bar's marks by kind (解析, 比べる, 判定, 不一致, 原因, 次の v), the buttons
(前 / 次のイベント, 次の判定へ, 次の不一致へ), the switch to the recorded time (and ?pace=real,
?pace=0, ?pace=paced), and ?t= giving the same picture on the event clock.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed, as test_browser.
"""

from __future__ import annotations

import copy
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


def record() -> list[dict]:
    """The contract sample with an hour between its halves and a second round after it: v2, a
    mismatch with its cause, an NG verdict (so every mark and button has something to find)."""
    src = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    head, tail = src[:-1], src[-1]
    by = {e["kind"]: e for e in src}
    t = head[-1]["t"] + 3600.0  # an hour of thinking: the event clock skips it
    more = []

    def add(ev, **data):
        nonlocal t
        e = copy.deepcopy(ev)
        e["t"] = t
        e["data"] = {**(e.get("data") or {}), **data}
        more.append(e)
        t += 0.5

    add(by["design.created"], design_version=2, parent_version=1)
    add(by["prediction.recorded"], prediction_id="pred-0002", design_version=2)
    add(by["analysis.started"], analysis_id="ana-0002", design_version=2)
    add(by["analysis.finished"], analysis_id="ana-0002", design_version=2)
    add(by["comparison.recorded"], comparison_id="cmp-0002", prediction_id="pred-0002", analysis_id="ana-0002",
        status="mismatch", diff_rel=0.4, cause_id="cause-0001")
    cause = {"kind": "cause.recorded", "agent": by["comparison.recorded"].get("agent", "lead"), "run_id": src[0]["run_id"],
             "ts": src[0]["ts"], "data": {"cause_id": "cause-0001", "comparison_id": "cmp-0002", "text": "予測の式が支点を取り違えた"}}
    add(cause)
    add(by["verdict.recorded"], verdict_id="ver-0002", comparison_id="cmp-0002", design_version=2, **{"pass": False})
    end = copy.deepcopy(tail)
    end["t"] = t
    events = head + more + [end]
    for i, e in enumerate(events):
        e["seq"] = i
    return events


SUMMARY = """() => { const p = window.hiraku.player;
  return { clock: p.clock, playhead: p.playhead, applied: p.applied, now: document.getElementById('now').textContent,
           rec: document.getElementById('pb-rec').textContent, time: document.getElementById('pb-time').textContent }; }"""

# The frame once it no longer changes: SUMMARY read until it is the same SAME_READS times in a row,
# two animation frames apart (a title fetched late, e.g. a tool's, can still change いま after
# window.hiraku.ready). No time is fixed: under load it takes as many frames as it needs.
SAME_READS = 3
SETTLED = """async (n) => {
  const read = """ + SUMMARY + """;
  const frame = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  let last = JSON.stringify(read()), same = 1;
  while (same < n) { await frame(); const now = JSON.stringify(read()); same = now === last ? same + 1 : 1; last = now; }
  return JSON.parse(last);
}"""

# How long a page may take to say it is ready. Not a pace: the wait ends as soon as
# window.hiraku.ready is there and settled. With browser tests side by side (qa.run_tests --browser)
# a page took more than the 60 s this test used to give it; 10 minutes only stops a page that never
# comes (the class's own limit in qa.run_tests is an hour).
READY_TIMEOUT_MS = 600_000


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class EventPlayBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.servers, cls.bases = start_servers(cls.tmp)
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        cls.events = record()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        for server in cls.servers:
            server.shutdown()
            server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def open(self, query="", t=0.0):
        """The page with the record loaded (window.hiraku.load) and stopped at t."""
        page = self.browser.new_page(viewport={"width": 1440, "height": 810})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.bases['saved']}/?run=sample&autoplay=0{query}", wait_until="domcontentloaded", timeout=READY_TIMEOUT_MS)
        page.wait_for_function(READY, timeout=READY_TIMEOUT_MS)
        page.evaluate("window.hiraku.ready")
        page.evaluate("([ev, t]) => window.hiraku.load(ev, t)", [self.events, t])
        self.addCleanup(page.close)
        return page, errors

    def times(self, page):
        return page.evaluate("window.hiraku.player.events.map((e) => ({ kind: e.kind, t: e.t, rec: e.t_rec, dwell: e.dwell }))")

    def test_the_gaps_are_dropped(self):
        page, errors = self.open()
        evs = self.times(page)
        self.assertEqual(page.evaluate("window.hiraku.player.clock"), "event")
        self.assertEqual([e["rec"] for e in evs], [e["t"] for e in self.events])  # the recorded times kept
        for a, b in zip(evs, evs[1:]):
            self.assertAlmostEqual(b["t"] - a["t"], a["dwell"], places=6)
            self.assertLessEqual(a["dwell"], 3.0, a["kind"])  # no event holds the screen long
        # the hour between the halves is gone: the whole record plays in seconds, not an hour
        self.assertLess(evs[-1]["t"], 40)
        self.assertGreater(self.events[-1]["t"], 3600)
        # bookkeeping takes no time; a mismatch or a cause more than a routine match
        dw = {e["kind"]: e["dwell"] for e in evs}
        self.assertEqual(dw["judgement.updated"], 0)
        mism = [e for e, src in zip(evs, self.events) if src["kind"] == "comparison.recorded" and src["data"]["status"] == "mismatch"][0]
        match = [e for e, src in zip(evs, self.events) if src["kind"] == "comparison.recorded" and src["data"]["status"] == "match"][0]
        self.assertGreater(mism["dwell"], match["dwell"])
        self.assertEqual(errors, [])

    def test_same_t_same_picture(self):
        """?t= on the event clock: two pages at the same t show the same frame (a seek there from
        elsewhere too)."""
        t = self.times(self.open()[0])[-3]["t"] + 0.05
        a, ea = self.open(t=t)
        b, eb = self.open(t=0)
        b.evaluate("(t) => window.hiraku.seek(t)", t)
        got = a.evaluate(SETTLED, SAME_READS)
        self.assertEqual(got, b.evaluate(SETTLED, SAME_READS))
        self.assertTrue(got["rec"].startswith("実時間 1:00:"))  # the recorded time, small beside
        self.assertEqual(ea + eb, [])

    def test_the_buttons_jump(self):
        page, errors = self.open()
        evs = self.times(page)
        distinct = sorted({e["t"] for e in evs})
        page.click("#pb-next")
        self.assertAlmostEqual(page.evaluate("window.hiraku.player.playhead"), distinct[1], places=6)
        page.click("#pb-next")
        page.click("#pb-prev")
        self.assertAlmostEqual(page.evaluate("window.hiraku.player.playhead"), distinct[1], places=6)
        self.assertFalse(page.evaluate("window.hiraku.player.playing"))
        # 次の判定へ: the first verdict (OK), then the second (NG); then none is left: the button is off
        verdicts = [e["t"] for e in evs if e["kind"] == "verdict.recorded"]
        page.click("#pb-next-verdict")
        self.assertAlmostEqual(page.evaluate("window.hiraku.player.playhead"), verdicts[0], places=6)
        page.click("#pb-next-verdict")
        self.assertAlmostEqual(page.evaluate("window.hiraku.player.playhead"), verdicts[1], places=6)
        page.wait_for_function("document.getElementById('pb-next-verdict').disabled")
        # 次の不一致へ from the start: the mismatch, its comparison on the screen
        page.evaluate("window.hiraku.seek(0)")
        page.click("#pb-next-mismatch")
        mism = [e["t"] for e, src in zip(evs, self.events) if src["kind"] == "comparison.recorded" and src["data"]["status"] == "mismatch"][0]
        self.assertAlmostEqual(page.evaluate("window.hiraku.player.playhead"), mism, places=6)
        self.assertEqual(page.evaluate("window.hiraku.state().comparisons['cmp-0002']?.status ?? null"), "mismatch")
        self.assertEqual(errors, [])

    def test_the_marks(self):
        page, errors = self.open()
        page.wait_for_function("document.querySelectorAll('#pb-marks i').length > 0")
        got = page.evaluate("""() => { const n = {}; for (const i of document.querySelectorAll('#pb-marks i')) n[i.className] = (n[i.className] ?? 0) + 1;
          return { n, v: [...document.querySelectorAll('#pb-marks .m-vl')].map((b) => b.textContent) }; }""")
        self.assertEqual(got["n"], {"m-v": 2, "m-ana": 2, "m-cmp": 1, "m-mis": 1, "m-ok": 1, "m-ng": 1, "m-cause": 1})
        self.assertEqual(got["v"], ["v1", "v2"])
        self.assertEqual(errors, [])

    def test_switch_to_the_recorded_time(self):
        page, errors = self.open()
        evs = self.times(page)
        mism_i = [i for i, src in enumerate(self.events) if src["kind"] == "comparison.recorded" and src["data"]["status"] == "mismatch"][0]
        page.evaluate("(t) => window.hiraku.seek(t)", evs[mism_i]["t"])
        page.click("#pb-clock")
        got = page.evaluate(SUMMARY)
        self.assertEqual(got["clock"], "real")
        self.assertEqual(page.evaluate("document.getElementById('pb-clock').textContent"), "実時間")
        # the same event on the screen, at its recorded time
        self.assertAlmostEqual(got["playhead"], self.events[mism_i]["t"], places=6)
        self.assertEqual(got["applied"], mism_i + 1)
        self.assertAlmostEqual(page.evaluate("window.hiraku.player.duration"), self.events[-1]["t"], places=6)
        page.click("#pb-clock")
        self.assertEqual(page.evaluate("window.hiraku.player.clock"), "event")
        self.assertAlmostEqual(page.evaluate("window.hiraku.player.playhead"), evs[mism_i]["t"], places=6)
        self.assertEqual(errors, [])

    def url_page(self, query):
        """The sample run as the URL gives it (no load): ?t= in the URL freezes the frame."""
        page = self.browser.new_page(viewport={"width": 1440, "height": 810})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.bases['saved']}/?run=sample{query}", wait_until="domcontentloaded", timeout=READY_TIMEOUT_MS)
        page.wait_for_function(READY, timeout=READY_TIMEOUT_MS)
        page.evaluate("window.hiraku.ready")
        self.addCleanup(page.close)
        return page, errors

    def test_url_t_gives_the_same_frame_with_and_without_video(self):
        """?t= in the URL and a seek to the same t give the same frame: on the event clock (the
        screen's default) and on the paced clock (?video=1's default, the filming's ?t= values).
        Each frame is read once it has settled (SETTLED), and each pair of pages is closed before
        the next opens, so the test does not keep four drawing pages at once."""
        for video, clock in (("", "event"), ("&video=1", "paced")):
            ref, e1 = self.url_page(f"{video}&autoplay=0")
            self.assertEqual(ref.evaluate("window.hiraku.player.clock"), clock, video)
            ts = ref.evaluate("window.hiraku.player.events.map((e) => e.t)")
            t = round((ts[len(ts) // 2] + ts[len(ts) // 2 + 1]) / 2, 3)
            ref.evaluate("(t) => window.hiraku.seek(t)", t)
            want = ref.evaluate(SETTLED, SAME_READS)
            ref.close()
            at, e2 = self.url_page(f"{video}&t={t}")
            self.assertEqual(at.evaluate(SETTLED, SAME_READS), want, video)
            at.close()
            self.assertEqual(e1 + e2, [], video)
        # ?pace= is followed under ?video=1 too
        page, errors = self.url_page("&video=1&pace=event&autoplay=0")
        self.assertEqual(page.evaluate("window.hiraku.player.clock"), "event")
        self.assertEqual(errors, [])

    def test_pace_params(self):
        for q, clock in (("&pace=real", "real"), ("&pace=0", "real"), ("&pace=paced", "paced"), ("", "event"), ("&pace=event", "event")):
            page, errors = self.open(q)
            self.assertEqual(page.evaluate("window.hiraku.player.clock"), clock, q)
            if clock == "real":
                self.assertEqual(page.evaluate("window.hiraku.player.events.map((e) => e.t)"), [e["t"] for e in self.events])
            self.assertEqual(errors, [], q)


if __name__ == "__main__":
    unittest.main()
