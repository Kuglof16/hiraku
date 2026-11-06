"""The assembly's parts go in eased and overlapping (web/js/assembly.js easeInto, LEAD, partTime).

- easeInto is CSS's cubic-bezier(EASE_BEZIER): the same values as the browser's own easing of that
  name (Web Animations), 0 at the start and 1 at the end, rising all the way, not the straight line;
- on A7 driven by the replay's clock (test_assembly_replay_browser.clock_events: N parts, each one
  lowered from z 1500 mm to its place), a part's height off its place is the eased share of its path,
  not the even one; the next part starts LEAD of a step before the one ahead is in place (two move at
  once); at u = N every part is in place and does not move any more (it stops at the end);
- the same t gives the same positions, reached directly or after a seek elsewhere.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed, as test_browser.
"""

from __future__ import annotations

import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import make_server
from tests.web.test_assembly_browser import ENABLED, write_run
from tests.web.test_assembly_replay_browser import ANA_T, N, PART_S, clock_events

try:
    from tests.web.pages import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

RUN = "20261102-207100-asmease"
T_A7 = round(ANA_T + 1.0, 3)
DROP = 1500.0 - 300.0  # each part's path: from z 1500 mm down to its place at z 300 mm

POS = """() => window.hiraku.asm.stage.movers.map((m) => ({ z: Math.round(m.obj.group.position.z * 1000) / 1000 + 0, on: m.obj.group.visible }))"""


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class AssemblyEaseBrowserTest(unittest.TestCase):
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
        page = self.browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.base}/?run={RUN}&pace=0&{query}", wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
        page.evaluate("window.hiraku.ready")
        page.wait_for_function("window.hiraku.asm && window.hiraku.asm.rec")
        return page, errors

    def at(self, page, u):
        page.evaluate(f"window.hiraku.seek({T_A7 + u * PART_S})")
        page.evaluate("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
        return page.evaluate(POS)

    def test_the_easing_is_the_css_curve(self):
        page, errors = self.open(f"tabs=A7:{T_A7}&t={T_A7}")
        got = page.evaluate(
            """async () => {
              const A = await import('/js/assembly.js');
              const xs = Array.from({ length: 21 }, (_, i) => i / 20);
              // the browser's own cubic-bezier: an animation of opacity 0 -> 1 with that easing, paused at x
              const el = document.createElement('div');
              document.body.append(el);
              const anim = el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 1000, easing: `cubic-bezier(${A.EASE_BEZIER.join(',')})`, fill: 'both' });
              anim.pause();
              const css = xs.map((x) => { anim.currentTime = x * 1000; return Number(getComputedStyle(el).opacity); });
              anim.cancel();
              el.remove();
              return { bez: A.EASE_BEZIER, lead: A.LEAD, ours: xs.map((x) => A.easeInto(x)), css,
                       times: [[0, 1], [0.5, 1], [1, 1], [0.75, 2], [1, 2], [2, 2], [1.9, 3], [1.5, 3]].map(([u, k]) => A.partTime(u, k)) };
            }"""
        )
        self.assertEqual(errors, [])
        page.close()
        self.assertEqual(got["bez"], [0.22, 1, 0.36, 1])
        for a, b in zip(got["ours"], got["css"]):
            self.assertAlmostEqual(a, b, delta=2e-3)
        self.assertEqual((got["ours"][0], got["ours"][-1]), (0, 1))
        self.assertTrue(all(b > a for a, b in zip(got["ours"], got["ours"][1:])))  # rising all the way
        # not the straight line: well ahead of it early on, slowing down as it goes in
        self.assertGreater(got["ours"][5], 0.25 + 0.3)
        self.assertLess(got["ours"][20] - got["ours"][19], 0.05 / 4)
        lead = got["lead"]
        self.assertTrue(0 < lead < 0.5)
        self.assertEqual(got["times"][:3], [0, 0.5, 1])  # the first part: from u 0 to 1
        self.assertAlmostEqual(got["times"][3], 0, places=9)  # part 2 starts at u 1 - LEAD = 0.75
        self.assertAlmostEqual(got["times"][4], lead / (1 + lead), places=9)
        self.assertEqual(got["times"][5], 1)
        self.assertGreater(got["times"][6], 0)  # part 3 starts at 2 - LEAD = 1.75: under way at 1.9
        self.assertEqual(got["times"][7], 0)  # not yet at 1.5

    def test_parts_go_in_eased_and_overlapping_and_stop_at_the_end(self):
        page, errors = self.open(f"tabs=A7:{T_A7}&t={T_A7}")
        ease = lambda x: page.evaluate(f"import('/js/assembly.js').then((A) => A.easeInto({x}))")
        lead = page.evaluate("import('/js/assembly.js').then((A) => A.LEAD)")
        # u 0.5: part 1 halfway in time, its height off its place the eased share left of its drop
        got = self.at(page, 0.5)
        self.assertAlmostEqual(got[0]["z"], DROP * (1 - ease(0.5)), delta=0.01)
        self.assertLess(got[0]["z"], DROP * 0.5 - 300)  # well past the even half (600 mm off)
        self.assertEqual([p["on"] for p in got], [i == 0 for i in range(N)])
        # u 4.9: part 5 nearly in, part 6 already on its way: both moving at once
        got = self.at(page, 4.9)
        f5 = (4.9 - (4 - lead)) / (1 + lead)
        f6 = (4.9 - (5 - lead)) / (1 + lead)
        self.assertAlmostEqual(got[4]["z"], DROP * (1 - ease(f5)), delta=0.01)
        self.assertAlmostEqual(got[5]["z"], DROP * (1 - ease(f6)), delta=0.01)
        self.assertGreater(got[4]["z"], 0)
        self.assertGreater(got[5]["z"], 0)
        self.assertTrue(got[5]["on"] and not got[6]["on"])
        self.assertEqual([p["z"] for p in got[:4]], [0] * 4)
        # slowing down into the place: the last tenth of a step moves it a few mm only
        near = self.at(page, 4.95)[4]["z"]
        self.assertLess(near, 5.0)
        # the end: every part in place, and staying there after it
        end = self.at(page, N)
        self.assertEqual(end, [{"z": 0, "on": True}] * N)
        self.assertEqual(self.at(page, N + 5), end)
        # the same t gives the same positions, after a seek elsewhere too
        a = self.at(page, 7.3)
        self.at(page, 2)
        self.assertEqual(self.at(page, 7.3), a)
        self.assertEqual(errors, [])
        page.close()


if __name__ == "__main__":
    unittest.main()
