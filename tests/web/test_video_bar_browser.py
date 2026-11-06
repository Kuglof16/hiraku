"""?video=1 has no replay bar: the bench's (▶, the time band, "21.5 / 21.5 秒", 1×) and A7's assembly
track, ▶ and switches are hidden (its band 手順 n / N stays), in the present mode too; without video=1 they are there. ?t= and
window.hiraku.seek move the replay without the bar. What stood above a bar (the colour legend,
A7's 接合 switch) comes down to the 3D's edge, and the layout lint finds nothing on these pages.
A seek to a time the bar shows as the end (0.1 s) lands on the end: the run is finished there.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed, as test_browser.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from tests.web.test_layout_lint_browser import ASSEMBLY, DOMAINS, ENABLED, READY, WIDTHS, Screen, describe, lint, start_servers

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

# shown: the element takes room on the screen (display: none gives no box)
SHOWN = """(sel) => { const e = document.querySelector(sel); if (!e) return null;
  const b = e.getBoundingClientRect(); return b.width > 0 && b.height > 0 && getComputedStyle(e).visibility !== "hidden"; }"""

# an element's distance from its box's bottom (its CSS bottom, in stage pixels: shown or not)
GAP = """(sel) => parseFloat(getComputedStyle(document.querySelector(sel)).bottom)"""

BENCH = f"run={DOMAINS}&tab=bench&pace=0"
A7 = f"run={ASSEMBLY}&tab=A7&pace=0&t=1e6"
VIDEO_SCREENS = (
    Screen("作業台 (動画)", f"?{BENCH}&t=1e6&video=1"),
    Screen("作業台 (動画・名前)", f"?{BENCH}&t=1e6&video=1&on=names"),
    Screen("見せる (動画)", f"?run={DOMAINS}&present=1&pace=0&t=1e6&video=1"),
    Screen("見せる・作業台 (動画)", f"?{BENCH}&present=1&t=1e6&video=1"),
    Screen("組み立て (動画)", f"?{A7}&video=1"),
    Screen("組み立て (動画・接合)", f"?{A7}&video=1&on=joints"),
)


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class VideoBarBrowserTest(unittest.TestCase):
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

    def open(self, query, size=(1920, 1080)):
        page = self.browser.new_page(viewport={"width": size[0], "height": size[1]})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.bases['saved']}/?{query}")
        page.wait_for_function(READY, timeout=60000)
        page.evaluate("window.hiraku.ready")
        page.evaluate("document.fonts.ready.then(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))))")
        return page, errors

    def test_bench_bar_only_without_video(self):
        for query, shown in ((f"{BENCH}&t=1e6", True), (f"{BENCH}&t=1e6&video=1", False),
                             (f"{BENCH}&t=1e6&present=1", True), (f"{BENCH}&t=1e6&present=1&video=1", False)):
            page, errors = self.open(query)
            got = {sel: page.evaluate(SHOWN, sel) for sel in ("#playbar", "#pb-time", "#pb-speed", "#pb-play")}
            view = page.evaluate(SHOWN, "#viewport")
            page.close()
            self.assertEqual(errors, [], query)
            self.assertTrue(view, query)
            self.assertEqual(got, {sel: shown for sel in got}, query)

    def test_a7_keeps_only_its_step_band_with_video(self):
        """A7 in the video keeps its band 手順 n / N; its track, ▶ and 接合 switch go."""
        sels = ("#view-asm .asm-bar", "#view-asm .as-label", "#view-asm .as-track", "#view-asm .as-play", "#view-asm .asm-joints-btn")
        for query, ctl in ((A7, True), (f"{A7}&video=1", False)):
            page, errors = self.open(query)
            got = {sel: page.evaluate(SHOWN, sel) for sel in sels}
            label = page.evaluate("() => document.querySelector('#view-asm .as-label').textContent")
            page.close()
            self.assertEqual(errors, [], query)
            self.assertEqual(got, {"#view-asm .asm-bar": True, "#view-asm .as-label": True, "#view-asm .as-track": ctl,
                                   "#view-asm .as-play": ctl, "#view-asm .asm-joints-btn": ctl}, query)
            self.assertIn("手順", label, query)

    def test_what_stood_above_the_bar_comes_down(self):
        """The colour legend: 76 px over the bench's bottom with the bar, 12 px without (video=1).
        A7's 接合 switch stays 66 px over its bar (the video hides it). (The CSS bottom: the
        legend is hidden on some pages.)"""
        for query, sel, gap in ((f"{BENCH}&t=1e6", "#legend", 76), (f"{BENCH}&t=1e6&video=1", "#legend", 12),
                                (A7, ".asm-joints-btn", 66)):
            page, errors = self.open(query)
            got = page.evaluate(GAP, sel)
            page.close()
            self.assertEqual(errors, [], query)
            self.assertEqual(got, gap, query)

    def test_seek_without_the_bar(self):
        """?t= opens at that time and window.hiraku.seek moves it, with the bar hidden: the same
        playheads and the same state as without video=1."""
        seen = {}
        for video in ("", "&video=1"):
            page, errors = self.open(f"{BENCH}&t=17.6{video}")
            opened = page.evaluate("window.hiraku.player.playhead")
            mid = page.evaluate("async () => { const t = await window.hiraku.seek(5); return { t, mark: document.documentElement.dataset.t, n: window.hiraku.state().events?.length ?? null }; }")
            end = page.evaluate("async () => await window.hiraku.seek(1e6)")
            dur = page.evaluate("window.hiraku.player.duration")
            page.close()
            self.assertEqual(errors, [], video)
            seen[video] = (opened, mid, end)
            self.assertAlmostEqual(opened, 17.6, delta=0.01, msg=video)
            self.assertAlmostEqual(mid["t"], 5, delta=0.01, msg=video)
            self.assertEqual(float(mid["mark"]), mid["t"], video)
            self.assertEqual(end, dur, video)
        self.assertEqual(seen[""], seen["&video=1"])

    def test_a_time_shown_as_the_end_is_the_end(self):
        """The bar shows 0.1 s: a seek less than 0.05 s before the last event's time (?t=2656 on a
        record ending at 2656.009) applies it, the run's end; a time further before does not."""
        for video in ("", "&video=1"):
            page, errors = self.open(f"run=sample&t=0{video}")
            got = page.evaluate("""async () => { const pl = window.hiraku.player, n = pl.events.length;
              const end = pl.duration, prev = pl.events[n - 2].t;
              if (end !== pl.events[n - 1].t) return { endIsNotTheLastEvent: [end, pl.events[n - 1].t] };
              await window.hiraku.seek(end - 0.03);
              const near = { applied: pl.applied, t: pl.playhead, status: pl.state.status };
              const mid = Math.max(prev, end - 0.5) + 0.06 < end - 0.06 ? (Math.max(prev, end - 0.5) + end) / 2 : null;
              let before = null;
              if (mid !== null) { await window.hiraku.seek(mid); before = { applied: pl.applied, t: pl.playhead }; }
              await window.hiraku.seek(1e6);
              return { n, end, near, mid, before, last: pl.events[n - 1].kind, endStatus: pl.state.status }; }""")
            page.close()
            self.assertEqual(errors, [], video)
            self.assertNotIn("endIsNotTheLastEvent", got, video)  # (the replay ends at the last event, as 150416)
            self.assertEqual(got["last"], "run.finished", video)
            self.assertEqual(got["near"], {"applied": got["n"], "t": got["end"], "status": got["endStatus"]}, video)
            self.assertIsNotNone(got["mid"], video)  # (the sample's end comes after a gap)
            self.assertEqual(got["before"], {"applied": got["n"] - 1, "t": got["mid"]}, video)

    def test_no_layout_breaks_with_video(self):
        problems = lint(self.browser, self.bases, VIDEO_SCREENS, WIDTHS)
        self.assertEqual(problems, [], "\n" + "\n".join(describe(p) for p in problems))


if __name__ == "__main__":
    unittest.main()
