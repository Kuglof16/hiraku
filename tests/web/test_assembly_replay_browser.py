"""A7 on the replay's clock: opened by ?tabs= (or ?t= with &asm_from=), its parts go in one per
assembly.js PART_S seconds from when A7 came up; &asm_from= picks the step it starts from (the
parts before in place); the same t gives the same picture; video=1 keeps only the band
手順 n / N, and with present=1 nothing lies in the subtitle band. The layout lint finds nothing.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed, as test_browser.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku import contract
from hiraku.webserve.server import make_server
from tests.web.test_assembly_browser import ENABLED, assembly_events, write_run
from tests.web.test_layout_lint_browser import ASSEMBLY, WIDTHS, Screen, describe, lint, start_servers

try:
    from tests.web.pages import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

PART_S = 0.4  # assembly.js PART_S
N = 19
RUN = "20261102-207000-asmclock"


def clock_events():
    """assembly_events with N parts of their own (p01..), each one step lowered from above, and the
    run's end 30 s after the assembly record (room to play it)."""
    events = assembly_events()
    design = next(e for e in events if e["kind"] == "design.created")
    ana = next(e for e in events if e["kind"] == "analysis.finished" and e["data"].get("assembly"))
    steps = []
    for i in range(N):
        pid = f"p{i + 1:02d}"
        c = [-900.0 + 100.0 * i, 200.0 * (i % 3) - 200.0, 300.0]
        design["data"]["parts"].append({"id": pid, "name": f"部品 {i + 1}", "kind": "bracket", "mass_kg": 1.0,
                                        "box": {"center_mm": c, "size_mm": [80.0, 80.0, 80.0]}})
        steps.append({"order": i + 1, "part": pid, "direction": "-z", "depends_on": [], "no_interference": True,
                      "tool_clearance": True, "hand_reach": True, "fastenings": [{"to": "node N1", "kind": "bolt", "at_mm": c}],
                      "path_mm": [[c[0], c[1], 1500.0], c], "text": f"部品 {i + 1} を上から下ろす"})
    ana["data"]["assembly"] = steps
    ana["data"]["interferences"] = []
    ana["data"]["results"] = [{"quantity": "assembly_steps", "value": float(N), "unit": "1"}]
    events[-1]["t"] = ana["t"] + 30
    return events


ANA_T = next(e for e in clock_events() if e["kind"] == "analysis.finished" and e["data"].get("assembly"))["t"]
T_A7 = round(ANA_T + 1.0, 3)  # A7 comes up 1 s after its record (pace=0: the record's times)

STATE = """() => {
  const a = window.hiraku.asm;
  const lab = document.querySelector('#view-asm .as-label');
  return {
    tab: document.querySelector('.tab.on')?.dataset.tab ?? null,
    u: Number(document.querySelector('#view-asm .asm-seek').dataset.u),
    label: lab.textContent,
    visible: a.stage.movers.map((m) => m.obj.group.visible),
    kinds: a.stage.movers.map((m) => m.kind),
    pos: a.stage.movers.map((m) => m.obj.group.position.toArray().map((x) => Math.round(x * 1000) / 1000 + 0)),
    cam: [...a.view.camera.position.toArray(), ...a.view.controls.target.toArray()].map((x) => Math.round(x * 1000) / 1000),
  };
}"""

SHOWN = """(sel) => { const e = document.querySelector(sel); if (!e) return null;
  const b = e.getBoundingClientRect(); return b.width > 0 && b.height > 0 && getComputedStyle(e).visibility !== 'hidden'; }"""

# the lowest bottom edge (in stage pixels) of anything seen on the stage (cut at the bottom of the
# boxes around it that clip their content)
LOWEST = """() => { const st = document.getElementById('stage'); const sr = st.getBoundingClientRect(); const k = sr.width / 1920;
  let low = 0, who = null;
  for (const el of st.querySelectorAll('*')) {
    const b = el.getBoundingClientRect(); if (!(b.width > 0 && b.height > 0) || getComputedStyle(el).visibility === 'hidden') continue;
    if (el.closest('#subband')) continue; // the subtitle band itself (empty)
    let bottom = b.bottom, top = b.top;
    for (let p = el.parentElement; p && p !== st.parentElement; p = p.parentElement) {
      if (getComputedStyle(p).overflow !== 'visible') { const pb = p.getBoundingClientRect(); bottom = Math.min(bottom, pb.bottom); top = Math.max(top, pb.top); }
    }
    if (bottom <= top) continue; // clipped away
    const y = (bottom - sr.top) / k; if (y > low) { low = y; who = el.className || el.id || el.tagName; }
  }
  return [low, String(who)]; }"""


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class AssemblyReplayBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        write_run(cls.tmp / "runs", RUN, clock_events())
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
        page.goto(f"{self.base}/?run={RUN}&pace=0&{query}", wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
        page.evaluate("window.hiraku.ready")
        page.wait_for_function("window.hiraku.asm && window.hiraku.asm.rec")
        self.frames(page)
        return page, errors

    @staticmethod
    def frames(page):
        page.evaluate("document.fonts.ready.then(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))))")

    def at(self, page, t):
        page.evaluate(f"window.hiraku.seek({t})")
        self.frames(page)
        return page.evaluate(STATE)

    def test_events_follow_the_contract(self):
        for ev in clock_events():
            self.assertEqual(contract.errors("event", ev), [], ev["kind"])

    def test_tabs_plan_sets_the_progress(self):
        page, errors = self.open(f"tabs=bench:0,A7:{T_A7}&t={T_A7}")
        got = self.at(page, T_A7)
        self.assertEqual(got["tab"], "A7")
        self.assertEqual(got["u"], 0)  # from the first step, at the start of its path
        self.assertEqual(got["visible"], [i == 0 for i in range(N)])
        for k in (1, 5, 19):
            got = self.at(page, T_A7 + (k - 0.5) * PART_S)
            self.assertAlmostEqual(got["u"], k - 0.5, places=6)
            self.assertEqual(got["label"], f"手順 {k} / {N}")
            self.assertEqual(got["visible"], [i < k for i in range(N)])
        got = self.at(page, T_A7 + N * PART_S + 3)
        self.assertEqual((got["u"], got["label"]), (N, f"手順 {N} / {N}"))
        # before A7 comes up (the bench) and back: A7 again from its start
        self.at(page, T_A7 - 0.5)
        self.assertEqual(self.at(page, T_A7)["u"], 0)
        self.assertEqual(errors, [])
        page.close()

    def test_asm_from_puts_the_parts_before_in_place(self):
        for arg, u0 in (("-6", N - 6), ("15", 14)):
            page, errors = self.open(f"tabs=A7:{T_A7}&asm_from={arg}&t={T_A7}")
            got = self.at(page, T_A7)
            self.assertEqual(got["u"], u0, arg)
            self.assertEqual(got["label"], f"手順 {u0} / {N}", arg)
            self.assertEqual(got["visible"], [i < u0 for i in range(N)], arg)
            self.assertEqual(got["kinds"], ["part"] * N, arg)
            self.assertEqual(got["pos"][:u0], [[0, 0, 0]] * u0, arg)  # in place from the start
            # the next part on its path, half in
            got = self.at(page, T_A7 + PART_S / 2)
            self.assertAlmostEqual(got["u"], u0 + 0.5, places=6)
            self.assertEqual(got["label"], f"手順 {u0 + 1} / {N}", arg)
            self.assertTrue(got["visible"][u0], arg)
            self.assertNotEqual(got["pos"][u0], [0, 0, 0], arg)  # off its place, on path_mm
            got = self.at(page, T_A7 + (N - u0) * PART_S)
            self.assertEqual(got["u"], N, arg)
            self.assertEqual(errors, [], arg)
            page.close()

    def test_same_t_gives_the_same_picture(self):
        t = round(T_A7 + 4.5 * PART_S, 3)
        q = f"tabs=A7:{T_A7}&asm_from=-6&t={t}&video=1&present=1"
        shots, states = [], []
        for _ in range(2):
            page, errors = self.open(q)
            states.append(page.evaluate(STATE))
            # reached by another way (seek elsewhere, then back): the same state
            self.at(page, T_A7 + 2)
            back = self.at(page, t)
            self.assertEqual(back, states[-1])
            shots.append(hashlib.sha256(page.locator("#view-asm .asm-3d").screenshot()).hexdigest())
            self.assertEqual(errors, [])
            page.close()
        self.assertEqual(states[0], states[1])
        self.assertAlmostEqual(states[0]["u"], N - 6 + 4.5, places=6)
        self.assertEqual(shots[0], shots[1])

    def test_camera_stays_at_the_default_view(self):
        page, errors = self.open(f"tabs=A7:{T_A7}&asm_from=-6&t={T_A7}&video=1")
        cams = [self.at(page, T_A7 + k * PART_S)["cam"] for k in (0, 1.5, 3.2, 6)]
        # a drag over the 3D does not turn it in the video
        box = page.locator("#view-asm .asm-gl").bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] / 2 + 300, box["y"] + box["height"] / 2 + 80, steps=8)
        page.mouse.up()
        cams.append(self.at(page, T_A7 + 6 * PART_S)["cam"])
        self.assertEqual(errors, [])
        page.close()
        self.assertTrue(all(c == cams[0] for c in cams), cams)

    def test_video_keeps_the_band_and_present_leaves_the_subtitle_band(self):
        page, errors = self.open(f"tabs=A7:{T_A7}&asm_from=-6&t={T_A7 + 1}&video=1&present=1")
        got = {sel: page.evaluate(SHOWN, sel) for sel in ("#view-asm .as-label", "#view-asm .as-track", "#view-asm .as-play",
                                                          "#view-asm .asm-joints-btn", "#playbar")}
        label = page.evaluate(STATE)["label"]
        low, who = page.evaluate(LOWEST)
        page.close()
        self.assertEqual(errors, [])
        self.assertEqual(got, {"#view-asm .as-label": True, "#view-asm .as-track": False, "#view-asm .as-play": False,
                               "#view-asm .asm-joints-btn": False, "#playbar": False})
        self.assertEqual(label, f"手順 {N - 6 + 3} / {N}")
        self.assertLessEqual(low, 1080 - 170 + 0.5, who)

    def test_without_the_plan_the_tab_keeps_its_own_bar(self):
        # ?t= alone (no plan, no asm_from): A7 opens on the vehicle built, its bar under the hand
        page, errors = self.open(f"tab=A7&t={T_A7}")
        got = page.evaluate(STATE)
        self.assertEqual(got["u"], N)
        box = page.locator("#view-asm .as-track").bounding_box()
        page.mouse.click(box["x"] + 2, box["y"] + box["height"] / 2)
        page.wait_for_function("document.querySelector('#view-asm .asm-seek').dataset.u === '0'")
        self.assertEqual(errors, [])
        page.close()


SAMPLE_END = assembly_events()[-1]["t"]
CLOCK_SCREENS = (
    Screen("組み立て (時刻・動画・見せる)", f"?run={ASSEMBLY}&pace=0&tabs=A7:{SAMPLE_END - 0.3:.3f}&asm_from=-2&t={SAMPLE_END:.3f}&video=1&present=1"),
    Screen("組み立て (時刻・動画)", f"?run={ASSEMBLY}&pace=0&tabs=A7:{SAMPLE_END - 0.3:.3f}&asm_from=-2&t={SAMPLE_END:.3f}&video=1"),
)


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class AssemblyReplayLintTest(unittest.TestCase):
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

    def test_no_layout_breaks(self):
        problems = lint(self.browser, self.bases, CLOCK_SCREENS, WIDTHS)
        self.assertEqual(problems, [], "\n" + "\n".join(describe(p) for p in problems))


if __name__ == "__main__":
    unittest.main()
