"""?play=loop&analysis=<id> (web/js/anim_loop.js) on a small step-climb record (tests.web.anim_loop_fixture):
the replay's playhead stays where the analysis arrived, the motion's time is t0 + (t mod duration)
from the page's t (?t=, window.hiraku.seek) or ?loop_t=, so one t gives one picture and a t past
the motion's length starts it again; ?cam=follow (the default) keeps the camera's target on the
vehicle, ?cam=side holds it beside the step's edge; video=1 has no controls (present=1 too); the
layout lint finds nothing on these pages.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed, as test_browser.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from tests.web.anim_loop_fixture import ANA, RISE, RUN_ID, SPEED, STEP_X, T_ARRIVE, T_END, body_x, body_z, loop_events
from tests.web.test_layout_lint_browser import ENABLED, READY, WIDTHS, Screen, describe, lint

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

LOOP = f"run={RUN_ID}&tab=bench&pace=0&analysis={ANA}&play=loop"

SHOWN = """(sel) => { const e = document.querySelector(sel); if (!e) return null;
  const b = e.getBoundingClientRect(); return b.width > 0 && b.height > 0 && getComputedStyle(e).visibility !== "hidden"; }"""

# what one frame shows: the replay's playhead, the motion's time, the camera
LOOK = """() => { const h = window.hiraku, b = h.bench;
  return { playhead: h.player.playhead, playing: h.player.playing, tau: b.frame ? b.frame.t : null,
           body: b.frame ? b.frame.body : null, target: b.controls.target.toArray(), camera: b.camera.position.toArray(),
           duration: b.motion ? b.motion.duration : null, t0: b.motion ? b.motion.t0 : null }; }"""
SEEK = "async (t) => { await window.hiraku.seek(t); return (" + LOOK + ")(); }"


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class AnimLoopBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from hiraku.webserve.server import make_server

        cls.tmp = Path(tempfile.mkdtemp())
        runs = cls.tmp / "runs"
        (runs / RUN_ID).mkdir(parents=True)
        (runs / RUN_ID / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in loop_events()), encoding="utf-8")
        cls.server = make_server("127.0.0.1", 0, runs, quiet=True)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.bases = {"saved": f"http://127.0.0.1:{cls.server.server_address[1]}"}
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def open(self, query, size=(1440, 810)):
        page = self.browser.new_page(viewport={"width": size[0], "height": size[1]})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.bases['saved']}/?{query}")
        page.wait_for_function(READY, timeout=60000)
        page.evaluate("window.hiraku.ready")
        page.evaluate("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
        return page, errors

    def assertSame(self, a, b, path="", tol=1e-9):
        """a and b alike, numbers to tol."""
        if isinstance(a, dict) and isinstance(b, dict):
            self.assertEqual(sorted(a), sorted(b), path)
            for k in a:
                self.assertSame(a[k], b[k], f"{path}.{k}", tol)
        elif isinstance(a, list) and isinstance(b, list):
            self.assertEqual(len(a), len(b), path)
            for i, (x, y) in enumerate(zip(a, b)):
                self.assertSame(x, y, f"{path}[{i}]", tol)
        elif isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
            self.assertAlmostEqual(a, b, delta=tol, msg=path)
        else:
            self.assertEqual(a, b, path)

    def test_playhead_held_while_the_motion_runs(self):
        """Without a t the loop runs on the page's clock: the motion's time moves, the playhead does not."""
        page, errors = self.open(LOOP)
        a = page.evaluate(LOOK)
        page.wait_for_timeout(700)
        b = page.evaluate(LOOK)
        page.close()
        self.assertEqual(errors, [])
        for x in (a, b):
            self.assertAlmostEqual(x["playhead"], T_ARRIVE, delta=1e-9)
            self.assertFalse(x["playing"])
        self.assertAlmostEqual(a["duration"], T_END, delta=1e-9)
        self.assertNotEqual(a["tau"], b["tau"])

    def test_tau_from_t_and_wraps(self):
        """?t= and seek(t) set the loop's clock: tau = t0 + (t mod duration); the same t, the same picture."""
        page, errors = self.open(f"{LOOP}&t=0.5")
        opened = page.evaluate(LOOK)
        page.wait_for_timeout(400)
        still = page.evaluate(LOOK)  # a fixed clock does not run
        at = {t: page.evaluate(SEEK, t) for t in (0.3, 1.2, T_END, T_END + 0.3, 3 * T_END + 1.2)}
        again = page.evaluate(SEEK, 1.2)
        page.close()
        self.assertEqual(errors, [])
        self.assertAlmostEqual(opened["tau"], opened["t0"] + 0.5, delta=1e-9)
        self.assertEqual(still["tau"], opened["tau"])
        for t, x in at.items():
            self.assertAlmostEqual(x["playhead"], T_ARRIVE, delta=1e-9, msg=t)
        self.assertAlmostEqual(at[0.3]["tau"], opened["t0"] + 0.3, delta=1e-9)
        self.assertAlmostEqual(at[T_END]["tau"], opened["t0"], delta=1e-9)  # past the end: from the start
        self.assertAlmostEqual(at[T_END + 0.3]["tau"], at[0.3]["tau"], delta=1e-9)
        self.assertAlmostEqual(at[3 * T_END + 1.2]["tau"], at[1.2]["tau"], delta=1e-9)
        # the same t: the same frame and camera (to 1e-9; the follow camera adds its shifts, so
        # another seek order may leave a rounding of 1e-16)
        self.assertSame(again, at[1.2])
        self.assertSame(again["target"], at[3 * T_END + 1.2]["target"])
        # the pose is the record's (body_x at a sample time)
        self.assertAlmostEqual(at[1.2]["body"][0], body_x(1.2) * 1000, delta=1e-6)

    def test_loop_t_fixes_the_clock(self):
        page, errors = self.open(f"{LOOP}&loop_t=0.7")
        a = page.evaluate(LOOK)
        b = page.evaluate(SEEK, 1.5)  # ?loop_t= wins over a seek
        page.close()
        self.assertEqual(errors, [])
        self.assertAlmostEqual(a["tau"], a["t0"] + 0.7, delta=1e-9)
        self.assertEqual(b["tau"], a["tau"])
        self.assertAlmostEqual(b["playhead"], T_ARRIVE, delta=1e-9)

    def test_cam_follow_keeps_the_target_on_the_vehicle(self):
        for query in (LOOP, f"{LOOP}&cam=follow"):
            page, errors = self.open(f"{query}&t=0.2")
            a = page.evaluate(SEEK, 0.2)
            b = page.evaluate(SEEK, 1.8)
            page.close()
            self.assertEqual(errors, [], query)
            # three.js X = ISO x, Y = ISO z (m)
            self.assertAlmostEqual(b["target"][0] - a["target"][0], body_x(1.8) - body_x(0.2), delta=1e-6, msg=query)
            self.assertAlmostEqual(b["target"][1] - a["target"][1], body_z(1.8) - body_z(0.2), delta=1e-6, msg=query)
            self.assertAlmostEqual(b["camera"][0] - a["camera"][0], body_x(1.8) - body_x(0.2), delta=1e-6, msg=query)

    def test_cam_side_stands_beside_the_step(self):
        page, errors = self.open(f"{LOOP}&cam=side&t=0.2")
        a = page.evaluate(SEEK, 0.2)
        b = page.evaluate(SEEK, RISE[1])
        page.close()
        self.assertEqual(errors, [])
        self.assertEqual(a["target"], b["target"])
        self.assertEqual(a["camera"], b["camera"])
        self.assertAlmostEqual(a["target"][0], STEP_X / 1000, delta=1e-6)
        self.assertGreater(abs(a["camera"][2] - a["target"][2]), abs(a["camera"][0] - a["target"][0]))  # from the side (-ISO y)

    def test_video_has_no_controls(self):
        for query, controls in ((f"{LOOP}&t=0.5", True), (f"{LOOP}&t=0.5&video=1", False), (f"{LOOP}&t=0.5&present=1&video=1", False)):
            page, errors = self.open(query)
            got = {sel: page.evaluate(SHOWN, sel) for sel in ("#playbar", "#pb-play", "#expand-btn")}
            play = page.evaluate(SHOWN, "#ab-play")
            look = page.evaluate(LOOK)
            page.close()
            self.assertEqual(errors, [], query)
            self.assertEqual(got, {sel: controls for sel in got}, query)
            self.assertFalse(play, query)  # the loop's ▶ is never there (the clock moves the motion)
            self.assertAlmostEqual(look["tau"], look["t0"] + 0.5, delta=1e-9, msg=query)

    def test_no_layout_breaks(self):
        # (#card-pred: its heading "...・cae.offroadの CAE 役" takes two lines at 900 wide, at times
        # on one screen, at times on another; a heading this branch does not touch)
        known = ("#card-pred",)
        screens = (
            Screen("作業台 (繰り返し)", f"?{LOOP}&t=1.2", known=known),
            Screen("作業台 (繰り返し・横)", f"?{LOOP}&t=1.2&cam=side", known=known),
            Screen("作業台 (繰り返し・動画)", f"?{LOOP}&t=1.2&video=1", known=known),
            Screen("見せる (繰り返し・動画)", f"?{LOOP}&t=1.2&present=1&video=1", known=known),
        )
        problems = lint(self.browser, self.bases, screens, WIDTHS)
        self.assertEqual(problems, [], "\n" + "\n".join(describe(p) for p in problems))


if __name__ == "__main__":
    unittest.main()
