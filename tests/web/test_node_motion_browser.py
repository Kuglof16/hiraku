"""The nodes' motion (a field of kind node_motion, web/js/node_motion.js) on the workbench's 3D.

On the crash_beam record of tests.web.node_motion_fixture: the pure functions in the page (a kept
frame gives its recorded positions and broken members, halfway between two frames gives the middle
positions), the replay's time alone sets the picture (two pages at the same ?t= draw the same), the
analysis' 0 .. 94.9 ms over NODE_MOTION_PLAY_S seconds of the replay from its arrival, the broken
members drawn grey and dashed (not red), the tag saying 実寸, no colour field or legend made of it,
no buttons with video=1, and the layout lint finding nothing on these pages.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed, as test_browser.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import make_server
from tests.web.node_motion_fixture import RUN_ID, T_ANALYSIS, node_motion_events, record
from tests.web.test_layout_lint_browser import ENABLED, READY, WIDTHS, Screen, describe, lint

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

PLAY_S = 4.0  # web/js/node_motion.js NODE_MOTION_PLAY_S
BENCH = f"run={RUN_ID}&tab=bench&pace=0"

SHOWN = """(sel) => { const e = document.querySelector(sel); if (!e) return null;
  const b = e.getBoundingClientRect(); return b.width > 0 && b.height > 0 && getComputedStyle(e).visibility !== "hidden"; }"""

# what the view draws, its materials and tag, and a hash-able picture of the 3D
DRAWN = """() => { const v = window.hiraku.nodeMotion, b = window.hiraku.bench;
  return { state: v.state, tag: v.tag.hidden ? null : v.tag.textContent, body: b.body.visible, wheels: b.wheels.visible, cg: b.cg.visible,
    intactColor: '#' + v.intactMat.color.getHexString(), brokenColor: '#' + v.brokenMat.color.getHexString(),
    brokenDashed: v.brokenMat.dashed, intactDashed: v.intactMat.dashed,
    nIntact: v.intact.visible ? v.intact.geometry.instanceCount ?? null : 0,
    nBroken: v.brokenLines.visible ? v.brokenLines.geometry.instanceCount ?? null : 0,
    legend: !document.getElementById('legend').hidden, legendText: document.getElementById('legend').textContent }; }"""

PICTURE = "() => document.querySelector('#viewport canvas.gl').toDataURL('image/png')"


def playhead_for(tau: float, rec: dict) -> float:
    t = rec["fields"][0]["frames"]["t_s"]
    return T_ANALYSIS + PLAY_S * (tau - t[0]) / (t[-1] - t[0])


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class NodeMotionBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rec = record()
        cls.frames = cls.rec["fields"][0]["frames"]
        cls.tmp = Path(tempfile.mkdtemp())
        runs = cls.tmp / "runs"
        (runs / RUN_ID).mkdir(parents=True)
        (runs / RUN_ID / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in node_motion_events()), encoding="utf-8")
        cls.server = make_server("127.0.0.1", 0, runs, quiet=True)
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

    def open(self, query, size=(1440, 810)):
        page = self.browser.new_page(viewport={"width": size[0], "height": size[1]})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.base}/?{query}")
        page.wait_for_function(READY, timeout=60000)
        page.evaluate("window.hiraku.ready")
        page.evaluate("document.fonts.ready.then(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))))")
        return page, errors

    def first_break(self):
        return next(i for i, b in enumerate(self.frames["broken"]) if b)

    def test_pure_functions_in_the_page(self):
        page, errors = self.open(f"{BENCH}&t=1e6")
        got = page.evaluate("""async () => {
          const m = await import('/js/node_motion.js');
          const ana = window.hiraku.state().analyses?.['ana-0001'] ?? null;
          const nm = m.nodeMotionOf(ana);
          if (!nm) return { none: true, keys: Object.keys(window.hiraku.state()) };
          const t = nm.t_s, k = 3;
          const at = m.nodeMotionAt(nm, t[k]);
          const mid = m.nodeMotionAt(nm, (t[k] + t[k + 1]) / 2);
          const again = m.nodeMotionAt(nm, (t[k] + t[k + 1]) / 2);
          return { n: nm.nodes.length, members: nm.members.length, frames: t.length,
            atK: at.positions, atKBroken: [...at.broken].sort(), mid: mid.positions, midU: mid.u, midK: mid.k,
            same: JSON.stringify(mid.positions) === JSON.stringify(again.positions),
            before: m.nodeMotionAt(nm, -1).positions[0], after: m.nodeMotionAt(nm, 1e9).positions[0],
            tau0: m.nodeMotionTau(nm, 6, 6), tauHalf: m.nodeMotionTau(nm, 6 + m.NODE_MOTION_PLAY_S / 2, 6), tauEnd: m.nodeMotionTau(nm, 1e6, 6),
            playS: m.NODE_MOTION_PLAY_S, span: nm.span, t0: nm.t0,
            noField: m.nodeMotionOf({ ...ana, fields: [] }), offShape: m.nodeMotionOf({ ...ana, fields: [{ ...nm.field, frames: { ...nm.field.frames, positions_mm: nm.field.frames.positions_mm.slice(1) } }] }) };
        }""")
        page.close()
        self.assertEqual(errors, [])
        self.assertNotIn("none", got, got.get("keys"))
        fr, f = self.frames, self.rec["fields"][0]
        self.assertEqual((got["n"], got["members"], got["frames"]), (len(f["nodes"]), len(f["members"]), len(fr["t_s"])))
        self.assertEqual(got["playS"], PLAY_S)
        k = 3
        self.assertEqual(got["atK"], fr["positions_mm"][k])
        self.assertEqual(got["atKBroken"], sorted(fr["broken"][k]))
        self.assertEqual(got["midK"], k)
        self.assertAlmostEqual(got["midU"], 0.5, places=12)
        for p, a, b in zip(got["mid"], fr["positions_mm"][k], fr["positions_mm"][k + 1]):
            for j in range(3):
                self.assertAlmostEqual(p[j], (a[j] + b[j]) / 2, places=9)
        self.assertTrue(got["same"])
        self.assertEqual(got["before"], fr["positions_mm"][0][0])
        self.assertEqual(got["after"], fr["positions_mm"][-1][0])
        self.assertEqual(got["tau0"], got["t0"])
        self.assertAlmostEqual(got["tauHalf"], got["t0"] + got["span"] / 2, places=12)
        self.assertAlmostEqual(got["tauEnd"], fr["t_s"][-1], places=12)
        self.assertIsNone(got["noField"])
        self.assertIsNone(got["offShape"])

    def test_the_same_t_draws_the_same(self):
        t = playhead_for((self.frames["t_s"][5] + self.frames["t_s"][6]) / 2, self.rec)
        seen = []
        for _ in range(2):
            page, errors = self.open(f"{BENCH}&t={t}")
            d = page.evaluate(DRAWN)
            pic = hashlib.sha256(page.evaluate(PICTURE).encode()).hexdigest()
            page.close()
            self.assertEqual(errors, [])
            seen.append((d, pic))
        self.assertEqual(seen[0], seen[1])
        d = seen[0][0]
        self.assertEqual(d["state"]["k"], 5)
        self.assertAlmostEqual(d["state"]["u"], 0.5, places=6)
        self.assertFalse(d["body"])  # the design's own frame does not move with it: hidden
        self.assertFalse(d["wheels"])
        self.assertFalse(d["cg"])

    def test_broken_members_grey_and_dashed(self):
        k = self.first_break()
        before = playhead_for(self.frames["t_s"][k - 1], self.rec)
        after = playhead_for(self.frames["t_s"][k], self.rec)
        page, errors = self.open(f"{BENCH}&t={before}")
        b = page.evaluate(DRAWN)
        page.evaluate(f"window.hiraku.seek({after})")
        a = page.evaluate(DRAWN)
        page.close()
        self.assertEqual(errors, [])
        self.assertEqual(b["state"]["broken"], [])
        self.assertEqual(b["nBroken"], 0)
        self.assertEqual(a["state"]["broken"], sorted(self.frames["broken"][k]))
        n_lines = len(self.rec["fields"][0]["members"])
        broken_lines = sum(1 for m in self.rec["fields"][0]["members"] if m[2] in self.frames["broken"][k])
        self.assertGreater(broken_lines, 0)
        self.assertEqual((a["nIntact"], a["nBroken"]), (n_lines - broken_lines, broken_lines))
        self.assertTrue(a["brokenDashed"])
        self.assertFalse(a["intactDashed"])
        self.assertNotEqual(a["brokenColor"], a["intactColor"])
        r, g, bl = (int(a["brokenColor"][i:i + 2], 16) for i in (1, 3, 5))
        self.assertLess(max(r, g, bl) - min(r, g, bl), 24, a["brokenColor"])  # a grey, not a red
        self.assertIn("折れた梁 1 本", a["tag"])
        self.assertIn("折れた梁 0 本", b["tag"])

    def test_tag_says_real_size_and_no_colour_field(self):
        page, errors = self.open(f"{BENCH}&t=1e6")
        d = page.evaluate(DRAWN)
        page.close()
        self.assertEqual(errors, [])
        self.assertIn("節の動き・実寸", d["tag"])
        self.assertIn(f"t = {self.frames['t_s'][-1] * 1000:.2f} ms", d["tag"])
        self.assertAlmostEqual(d["state"]["tau"], self.frames["t_s"][-1], places=12)
        self.assertFalse(d["legend"])  # node_motion is not a colour field: no legend, no field button

    def test_before_the_analysis_nothing_and_the_design_shows(self):
        page, errors = self.open(f"{BENCH}&t={T_ANALYSIS - 0.5}")
        d = page.evaluate(DRAWN)
        page.close()
        self.assertEqual(errors, [])
        self.assertIsNone(d["state"])
        self.assertIsNone(d["tag"])
        self.assertTrue(d["body"])

    def test_video_has_no_buttons(self):
        for video, shown in (("", True), ("&video=1", False)):
            page, errors = self.open(f"{BENCH}&t=8{video}")
            got = {sel: page.evaluate(SHOWN, sel) for sel in ("#playbar", "#pb-play", "#ab-play")}
            tag_buttons = page.evaluate("document.querySelectorAll('.nm-tag button').length")
            tag = page.evaluate(SHOWN, ".nm-tag")
            gap = page.evaluate("parseFloat(getComputedStyle(document.querySelector('.nm-tag')).bottom)")
            page.close()
            self.assertEqual(errors, [], video)
            self.assertEqual(got, {"#playbar": shown, "#pb-play": shown, "#ab-play": False}, video)
            self.assertEqual(tag_buttons, 0, video)
            self.assertTrue(tag, video)
            self.assertEqual(gap, 76 if shown else 12, video)

    def test_no_layout_breaks(self):
        k = self.first_break()
        screens = (
            Screen("節の動き (折れる前)", f"?{BENCH}&t={playhead_for(self.frames['t_s'][k - 1], self.rec)}"),
            Screen("節の動き (終わり)", f"?{BENCH}&t=1e6"),
            Screen("節の動き (動画)", f"?{BENCH}&t=1e6&video=1"),
            Screen("節の動き (名前)", f"?{BENCH}&t=1e6&on=names"),
        )
        problems = lint(self.browser, {"saved": self.base}, screens, WIDTHS)
        self.assertEqual(problems, [], "\n" + "\n".join(describe(p) for p in problems))


if __name__ == "__main__":
    unittest.main()
