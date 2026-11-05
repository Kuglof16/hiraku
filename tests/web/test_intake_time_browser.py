"""The replay's time on the intake screens (web/js/app.js syncIntakeTime -> intake.setTime): a lit
card's glow in A1 follows the playback time, so opening the same ?tab=A1 or ?tab=A2 with the same
?t= twice gives the same picture (shot at the stage's own size, 1920x1080: at 1280x720 the
background grid's rounding moves, 0299313); seek and plain playback pass the time too. And
?tabs=A1:0,A2:40,bench:55 (app.js tabPlan): the tab of the replay's time, at ?t=, through seek
back and forth and while playing; it wins over ?tab=, unknown names are left out, a tab pressed by
hand ends it. With ?present=1&video=1 too (the films): the same tabs, the present mode and the
subtitles' band kept, the intake screens shown left of the card, the same picture twice.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed, as test_browser.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku import contract
from hiraku.webserve.server import make_server
from tests.web.intake_cut_fixture import n09_claude_events, write_run
from tests.web.test_browser import ENABLED, sync_playwright
from tests.web.test_cut_n09_n14_browser import CATALOG_OFF, CATALOG_PICKS, CLAUDE_ASM, CLAUDE_PICKS, FLAG_REASONS, WANT

RUN = "20261105-120000-intake-time"
STEP = 10.0  # seconds between the events (pace=0 keeps them)
LIT_AT = 3 * STEP  # Claude's domains.picked: the cards light here
ASKED_AT = 4 * STEP  # hearing.asked
STAGE = {"width": 1920, "height": 1080}


def timed_events(run_id):
    """n09_claude_events with the hearing asked after Claude's pick, an event every STEP seconds:
    0 run.started, 10 the catalog's pick, 20 agent.started, 30 Claude's pick, 40 hearing.asked,
    50 agent.stopped, 60 run.finished."""
    events = n09_claude_events(run_id, WANT, (CATALOG_PICKS, CATALOG_OFF), (CLAUDE_PICKS, []), FLAG_REASONS, CLAUDE_ASM)
    asked = json.loads((contract.EXAMPLES_DIR / "hearing_asked.json").read_text(encoding="utf-8"))
    events.insert(4, {**events[3], "kind": "hearing.asked", "text": "確かめたいことがあります", "data": asked})
    for i, e in enumerate(events):
        e["seq"] = i
        e["t"] = i * STEP
    return events


SHADOW = """() => {
  const cards = [...document.querySelectorAll('#view-intake .in-card')];
  return { lit: cards.filter((c) => c.classList.contains('lit')).length, just: cards.filter((c) => c.classList.contains('just')).length,
           shadows: [...new Set(cards.filter((c) => c.classList.contains('lit')).map((c) => c.style.boxShadow))] };
}"""


class _RunPage(unittest.TestCase):
    """The server on the timed run and a browser (no tests of its own)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        runs = cls.tmp / "runs"
        write_run(runs, RUN, timed_events(RUN))
        cls.server = make_server("127.0.0.1", 0, runs, quiet=True, saved_dir=None, launcher=False)
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

    def open(self, query):
        page = self.browser.new_page(viewport=STAGE)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.base}/?run={RUN}&live=0&pace=0&{query}")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
        page.evaluate("window.hiraku.ready")
        page.evaluate("document.fonts.ready.then(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))))")
        return page, errors


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class IntakeTimeBrowserTest(_RunPage):
    def shot(self, query):
        page, errors = self.open(query)
        got = page.evaluate(SHADOW)
        png = page.screenshot()
        page.close()
        self.assertEqual(errors, [], query)
        return got, png

    def test_the_glow_at_t_is_a_function_of_t(self):
        mid, _ = self.shot(f"tab=A1&t={LIT_AT + 0.25}")
        self.assertGreater(mid["lit"], 0)
        self.assertEqual(mid["just"], 0)  # no CSS animation: the glow is drawn from the time
        self.assertEqual(len(mid["shadows"]), 1)
        self.assertIn("3.00px", mid["shadows"][0])  # half of the 0.5 s glow: 6 px * 0.5
        late, _ = self.shot(f"tab=A1&t={LIT_AT + 1}")
        self.assertEqual((late["just"], late["shadows"]), (0, [""]))  # the glow is over

    def test_the_same_t_twice_is_the_same_picture(self):
        # pets=0: the picture of the intake screens alone, not of whichever pet3d.js the repository has
        # (the pets' own same picture at the same t: test_agent_pets_browser)
        for query in (f"tab=A1&pets=0&t={LIT_AT + 0.25}", f"tab=A2&pets=0&t={ASKED_AT + 2.5}"):
            _, first = self.shot(query)
            _, again = self.shot(query)
            self.assertTrue(first == again, f"{query}: the two pictures differ")

    def test_seek_and_playing_pass_the_time(self):
        page, errors = self.open(f"tab=A1&t={LIT_AT - 5}")
        before = page.evaluate(SHADOW)
        at = page.evaluate(f"async () => {{ await window.hiraku.seek({LIT_AT + 0.25}); return ({SHADOW})(); }}")
        back = page.evaluate(f"async () => {{ await window.hiraku.seek({LIT_AT + 0.4}); return ({SHADOW})(); }}")
        # plain playback from just before the pick: once past the glow, the cards are drawn without it
        page.evaluate(f"() => {{ window.hiraku.player.seek({LIT_AT - 0.5}); document.getElementById('pb-play').click(); }}")
        page.wait_for_function(f"window.hiraku.player.playhead > {LIT_AT + 1}", timeout=30000)
        page.evaluate("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
        played = page.evaluate(SHADOW)
        page.close()
        self.assertEqual(errors, [])
        self.assertEqual(before["lit"], 0)  # Claude has not picked yet
        self.assertIn("3.00px", at["shadows"][0])
        self.assertIn("1.20px", back["shadows"][0])  # 6 px * (1 - 0.4 / 0.5)
        self.assertGreater(played["lit"], 0)
        self.assertEqual((played["just"], played["shadows"]), (0, [""]))


TAB_ON = """() => ({ on: document.querySelector('.tab.on')?.dataset.tab, intake: !document.getElementById('view-intake').hidden,
  bench: !document.getElementById('view-bench').hidden,
  screen: [...document.querySelectorAll('#view-intake .in-pane')].find((p) => !p.hidden)?.dataset.screen ?? null })"""

SEEK_TAB = f"async (t) => {{ await window.hiraku.seek(t); return ({TAB_ON})(); }}"


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class TabPlanBrowserTest(_RunPage):
    """?tabs=A1:0,A2:40,bench:55: the tab follows the replay's time, back and forth."""

    PLAN = f"tabs=A1:0,A2:{ASKED_AT},bench:55"

    def test_the_tab_at_t(self):
        for t, tab in ((0, "A1"), (LIT_AT + 0.25, "A1"), (ASKED_AT, "A2"), (ASKED_AT + 2.5, "A2"), (57, "bench")):
            page, errors = self.open(f"{self.PLAN}&t={t}")
            got = page.evaluate(TAB_ON)
            page.close()
            self.assertEqual(errors, [])
            self.assertEqual(got["on"], tab, t)
            if tab == "bench":
                self.assertEqual((got["intake"], got["bench"]), (False, True), t)
            else:
                self.assertEqual((got["intake"], got["screen"]), (True, tab), t)

    def test_seek_back_and_forth(self):
        page, errors = self.open(f"{self.PLAN}&t=0")
        seen = [(t, page.evaluate(SEEK_TAB, t)) for t in (57, ASKED_AT + 1, 5, 60, ASKED_AT - 0.01, ASKED_AT)]
        page.close()
        self.assertEqual(errors, [])
        want = {57: "bench", ASKED_AT + 1: "A2", 5: "A1", 60: "bench", ASKED_AT - 0.01: "A1", ASKED_AT: "A2"}
        self.assertEqual([(t, g["on"]) for t, g in seen], list(want.items()))
        for t, g in seen:
            if g["on"] != "bench":
                self.assertEqual(g["screen"], g["on"], t)  # the intake screen shown is the tab's

    def test_tabs_wins_over_tab_and_playing_moves_it(self):
        page, errors = self.open(f"tab=grid&{self.PLAN}&t={ASKED_AT - 0.5}")
        first = page.evaluate(TAB_ON)
        page.evaluate("() => document.getElementById('pb-play').click()")
        page.wait_for_function(f"window.hiraku.player.playhead > {ASKED_AT + 0.2}", timeout=30000)
        page.wait_for_function("document.querySelector('.tab.on')?.dataset.tab === 'A2'", timeout=5000)
        page.close()
        self.assertEqual(errors, [])
        self.assertEqual(first["on"], "A1")

    def test_unknown_names_are_left_out(self):
        page, errors = self.open(f"tabs=nope:0,A2:{ASKED_AT},A9:45,bench:x,:3,grid&t=47")
        at = page.evaluate(TAB_ON)
        early = page.evaluate(SEEK_TAB, 5)  # before the only kept entry: as without ?tabs= (the run's own focus)
        plain, errs = self.open("t=5")
        alone = plain.evaluate(TAB_ON)
        plain.close()
        page.close()
        self.assertEqual(errors + errs, [])
        self.assertEqual(at["on"], "A2")
        self.assertEqual(early["on"], alone["on"])

    def test_a_tab_pressed_ends_the_plan(self):
        page, errors = self.open(f"{self.PLAN}&t=5")
        page.click('.tab[data-tab="grid"]')
        page.wait_for_function("document.querySelector('.tab.on')?.dataset.tab === 'grid'")
        got = page.evaluate(SEEK_TAB, ASKED_AT + 1)
        page.close()
        self.assertEqual(errors, [])
        self.assertEqual(got["on"], "grid")


# What the film sees in the present mode with the video layout: the tab, the classes, and where the
# shown view, the card's rail and the stage's rows lie.
PRESENT_PROBE = """() => {
  const seen = (el) => !!el && el.checkVisibility() && el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().height > 0;
  const rect = (el) => { const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; };
  const view = ["view-intake", "view-bench"].find((id) => seen(document.getElementById(id)));
  return { on: document.querySelector('.tab.on')?.dataset.tab, present: document.body.classList.contains("present"),
    video: document.body.classList.contains("video"), view, viewRect: view ? rect(document.getElementById(view)) : null,
    screen: [...document.querySelectorAll('#view-intake .in-pane')].find((p) => !p.hidden)?.dataset.screen ?? null,
    card: seen(document.getElementById("present-card")) ? rect(document.getElementById("present-card")) : null,
    rail: rect(document.getElementById("present-rail")), main: rect(document.getElementById("main")),
    hidden: ["tabs", "statusrow", "viewswitch"].filter((id) => !seen(document.getElementById(id))) };
}"""


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class TabPlanPresentBrowserTest(_RunPage):
    """?present=1&video=1&tabs=A1:0,A2:40,bench:55 (how the films are shot): the tab of the replay's
    time, the present mode kept (the intake screens shown as they are, left of the card), the bottom
    170 px empty, and the same picture twice at the same t."""

    QUERY = f"present=1&video=1&tabs=A1:0,A2:{ASKED_AT},bench:55"

    def check_layout(self, got, tab, t):
        self.assertEqual(got["on"], tab, t)
        self.assertEqual((got["present"], got["video"]), (True, True), t)
        self.assertEqual(got["hidden"], ["tabs", "statusrow", "viewswitch"], t)
        self.assertEqual(got["view"], "view-bench" if tab == "bench" else "view-intake", t)
        if tab != "bench":
            self.assertEqual(got["screen"], tab, t)
        self.assertIsNotNone(got["card"], t)
        v, r = got["viewRect"], got["rail"]
        self.assertLessEqual(v["x"] + v["w"], r["x"], t)  # the view lies left of the card's rail
        self.assertLessEqual(got["main"]["y"] + got["main"]["h"], STAGE["height"] - 170, t)  # the subtitles' band
        self.assertLessEqual(v["y"] + v["h"], STAGE["height"] - 170, t)

    def test_the_tab_at_t_in_the_present_mode(self):
        for t, tab in ((5, "A1"), (LIT_AT + 0.25, "A1"), (ASKED_AT + 2.5, "A2"), (57, "bench")):
            page, errors = self.open(f"{self.QUERY}&t={t}")
            got = page.evaluate(PRESENT_PROBE)
            page.close()
            self.assertEqual(errors, [], t)
            self.check_layout(got, tab, t)

    def test_the_same_t_twice_is_the_same_picture(self):
        for t in (LIT_AT + 0.25, ASKED_AT + 2.5, 57):
            shots = []
            for _ in range(2):
                page, errors = self.open(f"{self.QUERY}&pets=0&t={t}")  # (pets=0: as in IntakeTimeBrowserTest)
                shots.append(page.screenshot())
                page.close()
                self.assertEqual(errors, [], t)
            self.assertTrue(shots[0] == shots[1], f"t={t}: the two pictures differ")

    def test_seek_and_playing_keep_the_present_mode(self):
        page, errors = self.open(f"{self.QUERY}&t=5")
        seen = [(t, page.evaluate(f"async (t) => {{ await window.hiraku.seek(t); return ({PRESENT_PROBE})(); }}", t)) for t in (57, ASKED_AT + 1, 5)]
        page.evaluate(f"() => {{ window.hiraku.player.seek({ASKED_AT - 0.5}); document.getElementById('pb-play').click(); }}")
        page.wait_for_function("document.querySelector('.tab.on')?.dataset.tab === 'A2'", timeout=30000)
        played = page.evaluate(PRESENT_PROBE)
        page.close()
        self.assertEqual(errors, [])
        for (t, got), tab in zip(seen, ("bench", "A2", "A1")):
            self.check_layout(got, tab, t)
        self.check_layout(played, "A2", "played")


if __name__ == "__main__":
    unittest.main()
