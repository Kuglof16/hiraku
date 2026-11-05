"""The video's cut N17 (the report on one page) in a browser.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed, as test_browser.
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
    from tests.web.pages import sync_playwright
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
                {"name": "max_stress to sections.rt40x60x2.3.dims_mm.t", "value": 2.3, "unit": "mm", "share": -1.62},
                {"name": "max_stress to sections.rt40x60x2.3.dims_mm.h", "value": 60.0, "unit": "mm", "share": -0.85},
            ],
        },
    ],
    "unchecked": [{"item": "yield 245 MPa", "source": "materials (形の見本)"}],
    # three steps in the record's form (run 211933): one without a design number or a
    # direction (left out), and two with: the one whose sensitivity is stronger is lit,
    # though it comes later in the report
    "next": [
        {"domain": "strength", "change": "台本: sections.rt40x60x2.3.dims_mm.h を大きくする", "reason": "K5: 弾性 -0.85 (形の見本)"},
        {"domain": "strength", "change": "台本: cross_B が効いている (向きは出していない)", "reason": "割合 88 % (形の見本)"},
        {"domain": "strength", "change": "台本: sections.rt40x60x2.3.dims_mm.t を大きくする", "reason": "K5: 弾性 -1.62 (形の見本)"},
    ],
    "summary": "強度は合格。効いているのは後ろのレールの縦の曲げ (形の見本)",
}
STRONGEST = 1  # the index of the term with the largest |share| in REPORT's sensitivity
CHOSEN = "フレームの管の板厚を大きくする"  # REPORT's next[2], the step of that term
CHOSEN_WHY = "今 2.3 mm。1 % 大きくすると、最大応力が 1.6 % 減る"  # its value and share (2 figures, as its bar)


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


def failing_events():
    """REPORT with a handling domain whose verdicts are recorded: one passes, one fails
    (shape example, the numbers of run 153914's S5 and F1 lines): the line must write the
    sign that holds between each value and its limit."""
    rep = copy.deepcopy(REPORT)
    rep["domains"].append({"domain": "handling", "state": "red", "verdict_ids": ["ver-0101", "ver-0102"], "line": "S5 ... > ...",
                           "effects": [], "assumptions": ["the body rolls (English, for the makers)"], "differences": []})
    events = report_events(rep)
    end = events.pop()
    base = {"ts": end["ts"], "run_id": end["run_id"], "agent": "cae.handling", "domain": "handling", "design_version": 1}
    for vid, q, val, op, lim, unit, ok in (("ver-0101", "tip_slope_angle", 41.85, ">", 43.31, "deg", False),
                                           ("ver-0102", "critical_wind_speed", 56.4, ">=", 30.0, "m/s", True)):
        events.insert(len(events) - 1, {**base, "t": end["t"] - 0.2, "kind": "verdict.recorded",
                                        "data": {"verdict_id": vid, "comparison_id": f"cmp-{vid[-4:]}", "load_case_id": "S5-x", "test_id": "S5",
                                                 "domain": "handling", "quantity": q, "value": {"value": val, "unit": unit}, "op": op,
                                                 "limit": {"value": lim, "unit": unit}, "pass": ok}})
    events.append(end)
    for i, ev in enumerate(events):
        ev["seq"] = i
    return events


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
            "dim_opacity": 0.3,
            "page": {"x": 360, "y": 80, "w": 1200, "h": 806, "title_px": 34, "text_px": 22, "change_of_title": 1.5, "domain_of_title": 1.4},
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
    lineFont: Math.min(...[...page.querySelectorAll('.n17-domain h3')].map((e) => parseFloat(getComputedStyle(e).fontSize))),
    lines: [...page.querySelectorAll('.n17-line')].map((e) => e.textContent),
    chipPx: (() => { const e = page.querySelector('.n17-chips'); return e ? parseFloat(getComputedStyle(e).fontSize) : null; })(),
    bodyKind: body.dataset.kind ?? null,
    bodyOpacity: Number(getComputedStyle(body).opacity),
    lit: document.documentElement.dataset.n17Lit,
  };
}"""


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
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
        cls.run_fail = "20261102-203000-reportfail"
        write_run(cls.tmp / "runs", cls.run_fail, failing_events())
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
        page.goto(f"{self.base}/cut.html?cut=n17&run={run_id}&t={t}", wait_until="domcontentloaded")
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

    def test_n17_line_writes_the_sign_that_holds(self):
        """A verdict that fails is written with the sign that holds (41.85 ≤ 43.31 where the
        criterion asks >), a pass with the criterion's own; English assumptions are left out."""
        page, errors = self.open(self.run_fail, 1)
        got = self.at(page, 1)
        self.assertEqual(errors, [])
        line = next(x for x in got["lines"] if "斜面" in x)
        self.assertEqual(line, "山側の輪が浮く斜面の角度 41.85 ° ≤ 43.31 ° / 転倒する風速 56.4 m/s ≥ 30 m/s")
        self.assertNotIn("for the makers", got["text"])
        self.assertNotIn("S5", got["text"])
        page.close()

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
        # the rest steps down to leave room for the large change (not below min_text_px)
        self.assertLessEqual(got["textPx"], layout["n17"]["page"]["text_px"])
        self.assertGreaterEqual(got["textPx"], layout["min_text_px"])
        # the words are the report's; a domain's line is made from its verdicts in the viewers'
        # words (the sample's ver-0001: max_stress 123.6 MPa <= 156.67 MPa), not the report's line
        for s in [REPORT["summary"], CHOSEN, CHOSEN_WHY]:
            self.assertIn(s, got["text"])
        # one step only: not the weaker one, not the one without a name and a direction,
        # and no ids or paths from the report's words
        self.assertEqual(page.evaluate("[...document.querySelectorAll('.n17-next li')].map((e) => e.dataset.path)"),
                         ["sections.rt40x60x2.3.dims_mm.t"])
        for word in ("cross_B", "断面の高さを大きくする", "フレームの管の高さを大きくする", "sections.", "K5"):
            self.assertNotIn(word, got["text"])
        self.assertIn("最大応力 123.6 MPa ≤ 156.7 MPa", got["text"])
        self.assertNotIn(REPORT["domains"][0]["line"], got["text"])
        # no internal names or ids: effects in Japanese, mismatches counted, the unchecked
        # item's words without its requirement key
        for word in ("bending", "torsion", "rail_R_S2B", "cmp-0001", "cause-0001"):
            self.assertNotIn(word, got["text"])
        self.assertIn("上下の曲げ", got["text"])
        self.assertIn("途中で手計算と食い違った 1 件 (原因を書いたもの 1 件)", got["text"])
        self.assertIn(REPORT["domains"][0]["assumptions"][0], got["text"])
        # each domain's line at the story's body text size (this layout has no story: its
        # min_text_px; with the promo department's story, 24 px: test_cut_story_browser)
        self.assertGreaterEqual(got["lineFont"], n17_layout()["min_text_px"])
        # the result is the domains' chips, large (layout.json n17.page.domain_of_title x
        # page.title_px); the lines with the
        # figures are a pale note under them (not counted, may be small)
        P = n17_layout()["n17"]["page"]
        self.assertEqual(got["chipPx"], max(got["textPx"], round(P["title_px"] * P["domain_of_title"])))
        self.assertEqual(got["bodyKind"], "note")
        self.assertAlmostEqual(got["bodyOpacity"], 0.5, places=3)
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
        # from light_s the rest of the page is as pale as layout.json n17.dim_opacity
        self.assertEqual(page.evaluate("[...document.querySelectorAll('.n17-head, .n17-body')].map((e) => getComputedStyle(e).opacity)"),
                         [str(layout["n17"]["dim_opacity"])] * 2)
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
        self.assertIn(CHOSEN, got["text"])
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
        # no sensitivity to choose by: the first step with a name and a direction, without
        # figures (none in the record)
        self.assertIn("フレームの管の高さを大きくする", got["text"])
        self.assertNotIn("今 ", got["text"])
        self.assertEqual(errors, [])
        page.close()

    def test_n17_next_without_a_named_step(self):
        """A report whose next steps have no design number and direction lights no change
        and says so; one without next says that."""
        for run_id, nxt, words in (("20261102-203000-reportnoname", [REPORT["next"][1]], "報告の「次」に、名前と向きのある項が無い"),
                                   ("20261102-203000-reportnonext", [], "報告に「次」が書かれていない")):
            rep = copy.deepcopy(REPORT)
            rep["next"] = nxt
            if not (self.tmp / "runs" / run_id).exists():
                write_run(self.tmp / "runs", run_id, report_events(rep))
            page, errors = self.open(run_id, n17_layout()["n17"]["times"]["light_s"])
            got = page.evaluate(STATE)
            self.assertIn(words, got["text"])
            self.assertNotIn("cross_B", got["text"])
            self.assertEqual(errors, [])
            page.close()

    def test_n17_names_missing_layout_keys(self):
        layout = n17_layout()
        del layout["n17"]["times"]["light_s"]
        del layout["n17"]["page"]["title_px"]
        del layout["n17"]["page"]["domain_of_title"]
        del layout["n17"]["colors"]
        self.layout_file.write_text(json.dumps(layout), encoding="utf-8")
        page, errors = self.open(self.run_id, 1)
        got = page.evaluate(STATE)
        self.assertIn("layout.json の n17 に無い: page.title_px, page.domain_of_title, times.light_s, colors.bg, colors.panel, colors.rule", got["err"])
        page.close()
        # the amount the rest is made pale is the layout's too
        layout = n17_layout()
        del layout["n17"]["dim_opacity"]
        self.layout_file.write_text(json.dumps(layout), encoding="utf-8")
        page, errors = self.open(self.run_id, 1)
        self.assertIn("layout.json の n17 に無い: dim_opacity", page.evaluate(STATE)["err"])
        page.close()

    def test_n17_says_when_the_run_has_no_report(self):
        page, errors = self.open(self.run_none, 1)
        got = page.evaluate(STATE)
        self.assertIn("報告の記録がまだ無い (report.written)", got["err"])
        page.close()


if __name__ == "__main__":
    unittest.main()
