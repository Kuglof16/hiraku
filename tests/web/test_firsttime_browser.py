"""What a first-time viewer could not read, made readable (tests.web.firsttime_fixture and the
screen's own functions in the page).

- the load cases' chips: many of them scroll sideways; ids shared by several get a short name
  from their titles (labels.js caseShortNames)
- names: a dotted quantity (max_stress.rail_L_FA), a series channel (mount_x.FL.lca_front),
  an axis suffix (inertia_force_x)
- the assembly's words: fastenings counted per target, an interference by names
- a dark module colour made lighter for the 3D (proposals.js readable)

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import make_server
from tests.web.firsttime_fixture import RUN_ID, firsttime_events
from tests.web.test_layout_lint_browser import ENABLED, _write_run

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class FirstTimeBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        _write_run(cls.tmp / "runs", RUN_ID, firsttime_events())
        cls.server = make_server("127.0.0.1", 0, cls.tmp / "runs", quiet=True, saved_dir=None)
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
        page = self.browser.new_page(viewport={"width": 1440, "height": 810})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("response", lambda r: r.status >= 400 and errors.append(f"{r.status} {r.url}"))
        page.goto(f"{self.base}/?{query}")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
        page.evaluate("window.hiraku.ready")
        return page, errors

    def test_the_chips_scroll_and_shared_ids_are_told_apart(self):
        page, errors = self.open(f"run={RUN_ID}&tab=bench&pace=0&t=1e6")
        got = page.evaluate(
            """() => {
              const row = document.getElementById('tests');
              const chip = (lc) => row.querySelector(`button[data-lc="${lc}"] .tid`).textContent;
              return { many: row.classList.contains('many'), scrolls: row.scrollWidth > row.clientWidth,
                       r8: ['R8-free', 'R8-stress', 'R8-push'].map(chip), t2: ['T2-motor', 'T2-controller'].map(chip),
                       h3: ['H3-body', 'H3-knee'].map(chip), single: chip('P4-20km') };
            }"""
        )
        self.assertEqual(errors, [])  # no 404 either (the icon is a data: URL)
        self.assertTrue(got["many"])
        self.assertTrue(got["scrolls"])
        self.assertEqual(got["r8"], ["R8どの部品も", "R8部品と乗員の重さと…", "R8乗員の肩・腰"])
        self.assertEqual(got["t2"], ["T2モーター", "T2制御器"])
        self.assertEqual(len(set(got["h3"])), 2)
        self.assertEqual(got["single"], "P4")  # an id of one load case: as it is
        page.close()

    def test_names_and_words(self):
        page, errors = self.open(f"run={RUN_ID}&tab=bench&pace=0&t=1e6")
        got = page.evaluate(
            """async () => {
              const L = await import('/js/labels.js');
              const S = await import('/js/series.js');
              const A = await import('/js/assembly.js');
              const P = await import('/js/proposals.js');
              const design = { parts: [{ id: 'battery', name: '電池 (LiFePO4)' }], members: [{ id: 'rail_L_HB' }] };
              const step = { fastenings: Array.from({ length: 9 }, () => ({ kind: 'bolt', to: 'node RB' })).concat([{ kind: 'bolt', to: 'node LB' }]) };
              const row = document.createElement('ul');
              row.innerHTML = A.interferenceRow({ a: 'member rail_L_HB', b: 'battery', depth_mm: 5.5, state: 'rest', condition: 'at rest' }, design);
              return {
                dotted: L.quantityName('max_stress.rail_L_FA'),
                axis: L.channelName('inertia_force_x'),
                mount: S.seriesName('mount_x.FL.lca_front'),
                temp: S.seriesName('temp.battery'),
                alone: S.seriesName('deceleration'),
                fasten: A.fastenText(step, {}),
                intf: row.textContent,
                dark: [P.luminance(P.moduleColor('powertrain')), P.readable('#57b8a0')],
              };
            }"""
        )
        self.assertEqual(errors, [])
        self.assertEqual(got["dotted"], "最大応力 (rail_L_FA)")
        self.assertEqual(got["axis"], "慣性力 (前後)")
        self.assertEqual(got["mount"], "左前 下アーム前")
        self.assertEqual(got["temp"], "電池")
        self.assertEqual(got["alone"], "減速度")
        self.assertEqual(got["fasten"], "ボルト: 節点 RB (9 か所)・節点 LB")
        # by names; the English condition that says the same as the state is left out
        self.assertEqual(got["intf"], "部材 rail_L_HB × 電池・5.5 mm・止まった状態")
        self.assertGreaterEqual(got["dark"][0], 0.1)
        self.assertEqual(got["dark"][1], "#57b8a0")  # light enough: as it is
        page.close()

    def test_the_change_line_and_the_tiles(self):
        page, errors = self.open(f"run={RUN_ID}&tab=grid&pace=0&t=1e6")
        got = page.evaluate(
            """() => {
              const ch = document.querySelector('#grid-head .change');
              return { long: ch.classList.contains('long'), lines: Math.round(ch.getBoundingClientRect().height / parseFloat(getComputedStyle(ch).lineHeight)),
                       words: [...document.querySelectorAll('#tiles .tile .state')].map((s) => s.textContent) };
            }"""
        )
        self.assertEqual(errors, [])
        self.assertTrue(got["long"])
        self.assertLessEqual(got["lines"], 2)  # wraps, up to two lines (not cut off on one)
        self.assertTrue(all(w for w in got["words"]))
        page.close()


if __name__ == "__main__":
    unittest.main()
