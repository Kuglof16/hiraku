"""The video's cut N17 (the report on one page) in a browser.

Skipped unless HIRAKU_WEB_BROWSER=1 and Playwright is installed, as test_browser.
The reports here are shape examples: their words and numbers are made up.
"""

import copy
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku import contract
from hiraku.webserve.server import SAMPLE_EVENTS, make_server

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None

REPORT = {
    "design_version": 1,
    "agent": "lead",
    "domains": [
        {
            "domain": "strength", "state": "green", "verdict_ids": ["ver-0001"],
            "line": "R1-step100: 最大応力は許容以内 (形の見本)",
            "effects": [
                {"name": "bending_vertical (rail_R_S2B)", "value": 48.1, "unit": "MPa", "share": 0.68},
                {"name": "torsion (rail_R_S2B)", "value": 10.2, "unit": "MPa", "share": 0.14},
                {"name": "bending_sideways (rail_R_S2B)", "value": 10.1, "unit": "MPa", "share": 0.14},
            ],
            "assumptions": ["前輪の力はフレームの節点に集中して入る (形の見本)"],
            "differences": [{"comparison_id": "cmp-0001", "diff_rel": -0.207, "cause_id": "cause-0001"}],
            "sensitivity": [
                {"name": "max_stress to parts.cargo.mass_kg", "value": 150.0, "share": 0.31},
                {"name": "max_stress to members.rail_R_S2B.section.t_mm", "value": 2.3, "share": -1.62},
                {"name": "max_stress to members.rail_R_S2B.section.h_mm", "value": 60.0, "share": -0.85},
            ],
        },
    ],
    "unchecked": [{"item": "yield 245 MPa", "source": "materials (形の見本)"}],
    "next": [{"domain": "strength", "change": "後ろのレールの板厚を上げる (形の見本)", "reason": "感度がいちばん大きい (形の見本)"}],
    "summary": "強度は合格。効いているのは後ろのレールの縦の曲げ (形の見本)",
}
STRONGEST = 1  # the index of the term with the largest |share| in REPORT's sensitivity


def long_report():
    """Eight domains with many terms each: more than the page holds at text_px."""
    rep = copy.deepcopy(REPORT)
    base = rep["domains"][0]
    rep["domains"] = []
    for d in contract.DOMAINS:
        e = copy.deepcopy(base)
        e["domain"] = d
        e["state"] = "red" if d == "handling" else "green"
        e["effects"] = base["effects"] * 2
        rep["domains"].append(e)
    rep["unchecked"] = [{"item": f"Q{10 + i} の値 (形の見本)", "source": "default"} for i in range(12)]
    return rep


def report_events(report):
    """The contract sample with a report.written before its end."""
    src = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    end = src.pop()
    written = {"seq": 0, "ts": end["ts"], "t": end["t"] - 0.1, "run_id": end["run_id"], "kind": "report.written",
               "agent": "lead", "design_version": report["design_version"], "data": copy.deepcopy(report)}
    out = src + [written, end]
    for i, ev in enumerate(out):
        ev["seq"] = i
    return out


def write_run(root, run_id, events):
    (root / run_id).mkdir(parents=True)
    (root / run_id / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")


def n17_layout():
    """Layout rules with the n17 values the promo department proposed (colors as their n01)."""
    return {
        "stage": {"w": 1920, "h": 1080},
        "subtitle_band": {"y": 910, "h": 170},
        "min_text_px": 18,
        "n17": {
            "duration_s": 8.7,
            "page": {"x": 360, "y": 80, "w": 1200, "h": 806, "title_px": 34, "text_px": 22},
            "times": {"lead_s": 0.3, "light_s": 5.8},
            "colors": {"bg": "#f4f1ea", "panel": "#fbf9f4", "rule": "#c9c3b6"},
        },
    }


STATE = """() => {
  const r = (el) => { const b = el.getBoundingClientRect(); return [b.x, b.y, b.width, b.height]; };
  const page = document.querySelector('[data-box="n17.page"]');
  if (!page) return { err: document.querySelector('.err')?.textContent ?? null };
  const pb = page.getBoundingClientRect();
  const all = [...page.querySelectorAll('*')].filter((e) => e.getClientRects().length);
  const outside = all.filter((e) => { const b = e.getBoundingClientRect();
    return b.width > 0 && b.height > 0 && (b.left < pb.left - 0.5 || b.right > pb.right + 0.5 || b.top < pb.top - 0.5 || b.bottom > pb.bottom + 0.5); })
    .map((e) => e.dataset.box || e.className || e.tagName);
  const texts = all.filter((e) => [...e.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim()));
  const body = page.querySelector('.n17-body');
  return {
    duration: window.hiraku.player.duration,
    page: r(page),
    visible: getComputedStyle(page).visibility,
    outside,
    bandBottom: Math.max(...[page, ...all].map((e) => e.getBoundingClientRect().bottom)),
    minFont: Math.min(...texts.map((e) => parseFloat(getComputedStyle(e).fontSize))),
    bodyOver: body.scrollHeight - body.clientHeight,
    textPx: Number(page.dataset.textPx),
    dropped: page.dataset.dropped ?? null,
    folded: page.dataset.folded ?? null,
    text: page.textContent,
    nextLit: page.querySelector('[data-box="n17.next"]').classList.contains('lit'),
    litBars: [...page.querySelectorAll('.n17-bar.lit')].map((e) => e.dataset.box),
    strongest: [...page.querySelectorAll('[data-strongest="1"]')].map((e) => e.dataset.box),
    sensBars: page.querySelectorAll('.n17-bar.sens').length,
    effBars: page.querySelectorAll('[data-box$=".effects"] .n17-bar').length,
    lit: document.documentElement.dataset.n17Lit,
  };
}"""


@unittest.skipUnless(ENABLED, "set HIRAKU_WEB_BROWSER=1 with Playwright installed")
class CutN17BrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.run_id = "20261102-203000-report"
        cls.run_long = "20261102-203000-reportlong"
        cls.run_none = "20261102-203000-noreport"
        write_run(cls.tmp / "runs", cls.run_id, report_events(REPORT))
        write_run(cls.tmp / "runs", cls.run_long, report_events(long_report()))
        write_run(cls.tmp / "runs", cls.run_none, [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()])
        cls.layout_file = cls.tmp / "layout.json"
        cls.layout_file.write_text(json.dumps(n17_layout()), encoding="utf-8")
        cls.server = make_server("127.0.0.1", 0, cls.tmp / "runs", quiet=True, saved_dir=None, launcher=False, layout_file=cls.layout_file)
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

    def setUp(self):
        self.layout_file.write_text(json.dumps(n17_layout()), encoding="utf-8")

    def open(self, run_id, t):
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.goto(f"{self.base}/cut.html?cut=n17&run={run_id}&t={t}")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
        page.evaluate("window.hiraku.ready")
        return page, errors

    def at(self, page, t):
        page.evaluate("(t) => window.hiraku.seek(t)", t)
        return page.evaluate(STATE)

    def test_events_follow_the_contract(self):
        for ev in report_events(REPORT) + report_events(long_report()):
            self.assertEqual(contract.errors("event", ev), [], ev["kind"])

    def assert_inside(self, got, layout):
        P = layout["n17"]["page"]
        self.assertEqual([round(v) for v in got["page"]], [P["x"], P["y"], P["w"], P["h"]])
        self.assertLessEqual(got["bandBottom"], layout["subtitle_band"]["y"])
        self.assertEqual(got["outside"], [])
        self.assertLessEqual(got["bodyOver"], 0)
        self.assertGreaterEqual(got["minFont"], layout["min_text_px"])

    def test_n17_page_in_its_box_and_lit_at_light_s(self):
        layout = n17_layout()
        T = layout["n17"]["times"]
        page, errors = self.open(self.run_id, 0)
        got = self.at(page, 0)
        self.assertEqual(got["duration"], layout["n17"]["duration_s"])
        self.assertEqual(got["visible"], "hidden")  # before lead_s
        got = self.at(page, T["lead_s"])
        self.assertEqual(got["visible"], "visible")
        self.assert_inside(got, layout)
        self.assertEqual(got["textPx"], layout["n17"]["page"]["text_px"])  # fits without stepping down
        # the words are the report's
        for s in [REPORT["summary"], REPORT["domains"][0]["line"], REPORT["next"][0]["change"], REPORT["next"][0]["reason"]]:
            self.assertIn(s, got["text"])
        self.assertEqual(got["effBars"], len(REPORT["domains"][0]["effects"]))
        self.assertEqual(got["sensBars"], len(REPORT["domains"][0]["sensitivity"]))
        strongest = f"n17.domain.strength.sens.{STRONGEST}"
        self.assertEqual(got["strongest"], [strongest])
        # not lit before light_s, lit from it on
        got = self.at(page, T["light_s"] - 0.05)
        self.assertFalse(got["nextLit"])
        self.assertEqual(got["litBars"], [])
        got = self.at(page, T["light_s"])
        self.assertTrue(got["nextLit"])
        self.assertEqual(got["litBars"], [strongest])
        self.assertEqual(got["lit"], "1")
        got = self.at(page, layout["n17"]["duration_s"])
        self.assertTrue(got["nextLit"])
        self.assert_inside(got, layout)
        self.assertEqual(errors, [])
        page.close()

    def test_n17_long_report_steps_down_and_stays_inside(self):
        layout = n17_layout()
        page, errors = self.open(self.run_long, layout["n17"]["times"]["light_s"])
        got = page.evaluate(STATE)
        self.assert_inside(got, layout)
        self.assertLess(got["textPx"], layout["n17"]["page"]["text_px"])
        self.assertGreaterEqual(got["textPx"], layout["min_text_px"])
        self.assertIn(long_report()["next"][0]["change"], got["text"])
        self.assertTrue(got["nextLit"])
        # the domain the next step names keeps its bars, and its strongest sensitivity is lit
        self.assertEqual(got["litBars"], [f"n17.domain.strength.sens.{STRONGEST}"])
        self.assertNotIn("strength", got["folded"] or "")
        self.assertNotIn("strength", got["dropped"] or "")
        self.assertTrue(got["folded"])  # the other domains gave up their details first
        self.assertEqual(errors, [])
        page.close()

    def test_n17_without_sensitivity_draws_no_sensitivity_bars(self):
        rep = copy.deepcopy(REPORT)
        del rep["domains"][0]["sensitivity"]
        run_id = "20261102-203000-reportnosens"
        if not (self.tmp / "runs" / run_id).exists():
            write_run(self.tmp / "runs", run_id, report_events(rep))
        page, errors = self.open(run_id, n17_layout()["n17"]["times"]["light_s"])
        got = page.evaluate(STATE)
        self.assertEqual(got["sensBars"], 0)
        self.assertEqual(got["litBars"], [])
        self.assertTrue(got["nextLit"])
        self.assertNotIn("その数を 1 % 変えると", got["text"])
        self.assertEqual(errors, [])
        page.close()

    def test_n17_names_missing_layout_keys(self):
        layout = n17_layout()
        del layout["n17"]["times"]["light_s"]
        del layout["n17"]["page"]["title_px"]
        del layout["n17"]["colors"]
        self.layout_file.write_text(json.dumps(layout), encoding="utf-8")
        page, errors = self.open(self.run_id, 1)
        got = page.evaluate(STATE)
        self.assertIn("layout.json の n17 に無い: page.title_px, times.light_s, colors.bg, colors.panel, colors.rule", got["err"])
        page.close()

    def test_n17_says_when_the_run_has_no_report(self):
        page, errors = self.open(self.run_none, 1)
        got = page.evaluate(STATE)
        self.assertIn("報告の記録がまだ無い (report.written)", got["err"])
        page.close()


if __name__ == "__main__":
    unittest.main()
