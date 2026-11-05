"""The judged number on the present card and the colour bar: the large number is the verdict's
value (the judged quantity), a value the verdict does not use is small under 参考, and a
colour bar of another quantity does not draw an allowable line.

The records: the shell fixture (tests/web/shell_fixture.py), where version 2's load case has
its own criterion (load_cases.created, a lower limit than version 1's, as when the material
changes) and its shell analysis records the hot-spot stress the frame_shell verdict is made on
(docs/contract.md, 判定に使う量), well under the shell's element von Mises. Shape example:
the numbers are chosen here, not results.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed, as test_browser.
"""

import copy
import json
import os
import re
import threading
import unittest

from hiraku.webserve.server import SAMPLE_EVENTS, make_server
from tests.web.shell_fixture import shell_events

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None

MOUNTS = "取付点 (金具・溶接・ボルト) は判定していない。取付の範囲は、金具が補強する仮定で判定から外している"
LIMIT_V2 = 140.0  # version 2's allowable (shape example; version 1's is the sample's 156.7 MPa)
HOT_SHARE = 0.5  # the hot-spot stress as a share of the shell's largest element value (shape example)
RED_RGB = "rgb(200, 64, 42)"  # app.css --red (#c8402a), as getComputedStyle gives it

PROBE = """() => {
  const card = document.getElementById("present-card");
  const q = (sel) => card.querySelector(sel);
  const px = (el) => el ? parseFloat(getComputedStyle(el).fontSize) : null;
  const legend = document.getElementById("legend");
  return {
    scene: document.body.dataset.presentScene,
    what: q(".pc-what")?.textContent ?? null, val: q(".pc-val")?.textContent ?? null,
    crit: q(".pc-crit")?.textContent ?? null, verdict: q(".pc-verdict")?.textContent ?? null,
    ref: q(".pc-ref")?.textContent ?? null, refPx: px(q(".pc-ref li")), valPx: px(q(".pc-val")),
    legend: legend.hidden ? null : legend.textContent, lim: legend.querySelector(".lim")?.textContent ?? null,
    note: legend.querySelector(".ref-note")?.textContent ?? null,
    tags: [...document.querySelectorAll("#viewport .lbl-max")].map((e) => e.textContent),
    tagTones: [...document.querySelectorAll("#viewport .lbl-max")].map((e) => ({ text: e.textContent, fail: e.classList.contains("lbl-fail"), ref: e.classList.contains("lbl-ref"), color: getComputedStyle(e).color })),
    extra: q(".pc-extra")?.textContent ?? null,
    dev: q(".pc-dev")?.textContent ?? null,
    cardText: card.textContent,
    pcNote: q(".pc-note")?.textContent ?? null,
    anaRef: [...document.querySelectorAll("#card-ana tr.ref")].map((e) => ({ text: e.textContent, px: parseFloat(getComputedStyle(e.querySelector("td")).fontSize) })),
    anaRows: [...document.querySelectorAll("#card-ana tr:not(.ref) td:first-child")].map((e) => parseFloat(getComputedStyle(e).fontSize)),
    anaNote: document.querySelector("#card-ana .not-judged")?.textContent ?? null,
    tileNote: document.querySelector('.tile[data-domain="strength"] .not-judged')?.textContent ?? null,
    top: window.hiraku.bench.keys.contour ? { max: window.hiraku.bench.keys.contour.max, fieldMax: window.hiraku.bench.keys.contour.fieldMax, limitKnown: window.hiraku.bench.keys.contour.limitKnown } : null,
  };
}"""


def fmt4(v):
    """labels.js fmt(v, 4) for 1 <= v < 1e6 (4 significant digits, no trailing zeros)."""
    s = f"{v:.{max(0, 3 - len(str(int(abs(v)))) + 1)}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


CHANGED_TRACK = 1350.0  # version 2's track in the change test (shape example)
TRACK_NAME = "車輪の左右の間隔 (形の見本)"  # version 1's variable_names entry (shape example)


def judged_events(hot_share=HOT_SHARE, passed=True):
    """The shell fixture with version 2's own load case, the hot-spot result on its shell
    analysis, and its comparison, verdict (on the hot-spot value) and judgement.
    hot_share 1.0 makes the hot-spot value the shell field's largest value (the field is then
    the judged quantity); passed False puts the allowable under it and records a fail.
    Returns (events, hot-spot value, the shell's largest element value)."""
    events, _, shells = shell_events()
    run = events[0]
    lc = copy.deepcopy(run["data"]["load_cases"][0])
    shell2 = shells[2]
    field_max = max(next(f for f in shell2["fields"] if f.get("surface") == "max")["per_element"])
    hot = round(hot_share * field_max, 2)
    limit = LIMIT_V2 if passed else round(0.5 * hot, 2)
    lc["criterion"]["limit"] = {"value": limit, "unit": "MPa"}
    ana = next(e for e in events if e["kind"] == "analysis.finished" and e["data"]["analysis_id"] == shell2["analysis_id"])
    ana["data"]["results"].append({"quantity": "max_hot_spot_stress", "value": hot, "unit": "MPa", "where": "rail_L0"})
    base = {"run_id": run["run_id"], "ts": ana["ts"], "agent": "cae.strength", "domain": "strength", "design_version": 2}
    cmp = {"comparison_id": "cmp-0002", "prediction_id": "pred-0002", "analysis_id": shell2["analysis_id"], "quantity": "max_hot_spot_stress",
           "predicted": {"value": hot, "unit": "MPa"}, "analyzed": {"value": hot, "unit": "MPa"}, "diff_abs": 0.0, "diff_rel": 0.0,
           "tolerance_rel": 0.15, "status": "match", "cause_id": None}
    ver = {"verdict_id": "ver-0002", "comparison_id": "cmp-0002", "load_case_id": lc["load_case_id"], "test_id": lc["test_id"], "domain": "strength",
           "quantity": "max_stress", "value": {"value": hot, "unit": "MPa"}, "op": "<=", "limit": {"value": limit, "unit": "MPa"}, "pass": hot <= limit,
           "margin": {"value": limit - hot, "unit": "MPa"}, "margin_pct": 100 * (limit - hot) / limit, "requirement_keys": [], "basis": "match", "value_from": "analysis"}
    judgement = {"design_version": 2, "domain": "strength", "state": "green" if hot <= limit else "red", "red_count": 0 if hot <= limit else 1,
                 "verdict_ids": ["ver-0002"], "summary": "shape example"}
    t = ana["t"]
    added = [
        {**base, "t": t + 0.1, "kind": "comparison.recorded", "data": cmp},
        {**base, "t": t + 0.2, "kind": "verdict.recorded", "data": ver},
        {**base, "t": t + 0.3, "kind": "judgement.updated", "data": judgement},
    ]
    i = events.index(ana)
    created = {"run_id": run["run_id"], "ts": ana["ts"], "agent": "system", "design_version": 2, "t": events[i - 1]["t"],
               "kind": "load_cases.created", "data": {"design_version": 2, "load_cases": [lc]}}
    events = events[: i - 1] + [created] + events[i - 1 : i + 1] + added + events[i + 1 :]
    for k, e in enumerate(events):
        e["seq"] = k
    return events, hot, field_max


def changed_events(events):
    """The events with version 2 built from a module designer's proposal, as build_version records
    it: module_changes[].changes (the track's new value) and a change_reason in the packager's
    words with record ids; version 1 names the variable (variable_names). Shape example."""
    events = copy.deepcopy(events)
    d1, d2 = [e["data"] for e in events if e["kind"] == "design.created"][:2]
    d1["variable_names"] = {"track": TRACK_NAME}
    d2["variables"]["track"] = {"value": CHANGED_TRACK, "unit": "mm"}
    d2["module_changes"] = [{"change_id": "chg-0013", "module_id": "frame", "agent_id": "designer.frame", "design_version": 1,
                             "reason": "形の見本", "changes": {"track": {"value": CHANGED_TRACK, "unit": "mm"}}}]
    d2["change_reason"] = "その回の提案をまとめた (chg-0013)。chg-0013 (frame: track 1300→1350 mm) をまとめる。答える赤は R1 (ver-0001、シェル ana-0002)"
    return events


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class VerdictCardBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server("127.0.0.1", 0, "runs-none", quiet=True)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        cls.events, cls.hot, cls.field_max = judged_events()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()

    def open(self, query, events=None):
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.goto(f"{self.base}/?{query}")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=30000)
        page.evaluate("window.hiraku.ready")
        if events is not None:
            page.evaluate("async (events) => { await window.hiraku.load(events, 1e6); }", events)
        page.evaluate("() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
        return page, errors

    def test_the_large_number_is_the_judged_value_and_the_shell_maximum_is_reference(self):
        page, errors = self.open("run=sample&pace=0&t=0&present=1", self.events)
        got = page.evaluate(PROBE)
        self.assertEqual(errors, [])
        self.assertEqual(got["scene"], "shell")
        # the large number: the verdict's value, named by the quantity compared
        self.assertEqual(got["val"], f"{fmt4(self.hot)} MPa")
        self.assertIn("判定の量", got["what"])
        self.assertIn("構造的ホットスポット応力", got["what"])
        # the verdict's line with version 2's allowable, not version 1's
        self.assertEqual(got["crit"], f"解析 {fmt4(self.hot)} MPa ≤ 許容 {fmt4(LIMIT_V2)} MPa → 合格")
        self.assertEqual(got["verdict"], "基準を満たした")
        # the shell's largest element value: small, under 参考
        self.assertIsNotNone(got["ref"], got)
        self.assertIn("参考 (判定に使わない)", got["ref"])
        self.assertIn(f"{fmt4(self.field_max)} MPa", got["ref"])
        self.assertNotIn(fmt4(self.field_max), got["val"])
        self.assertLess(got["refPx"], got["valPx"] / 2)
        # the colour bar of the shell's von Mises: reference, no allowable line, on its own range
        self.assertIn("参考の色:", got["legend"])
        self.assertIsNone(got["lim"])
        self.assertNotIn("許容", got["legend"])
        self.assertEqual(got["note"], "判定は別の量: 構造的ホットスポット応力 (溶接線)")
        self.assertFalse(got["top"]["limitKnown"])
        # the largest-value tag on the 3D says it is for reference
        self.assertTrue(any(t.startswith("参考 最大") for t in got["tags"]), got["tags"])
        # ... in the reference colour; no tag is red: the version passed and the value is not judged
        # (version 1's tag beside it has no verdict to be judged by: plain)
        self.assertTrue(all(t["ref"] for t in got["tagTones"] if t["text"].startswith("参考")), got["tagTones"])
        for t in got["tagTones"]:
            self.assertFalse(t["fail"], t)
            self.assertNotEqual(t["color"], RED_RGB, t)
        # the shell's element max_stress (a result in the criterion's quantity, not judged): under 参考 too
        # (the fixture's max_stress is the field's largest value: it is also in the field's lines)
        self.assertRegex(got["ref"], rf"最大応力 \(.*\) {fmt4(self.field_max)} MPa")
        # the mounts are not judged: one line on a strength load case's card
        self.assertEqual(got["pcNote"], MOUNTS)
        page.close()

    def test_the_analysis_card_and_the_strength_tile_say_what_is_not_judged(self):
        page, errors = self.open("run=sample&pace=0&t=0", self.events)
        got = page.evaluate(PROBE)
        self.assertEqual(errors, [])
        # the shell's max_stress row: marked 参考, smaller than the judged rows
        self.assertEqual(len(got["anaRef"]), 1, got["anaRef"])
        self.assertIn("参考 (判定に使わない)", got["anaRef"][0]["text"])
        self.assertIn("最大応力", got["anaRef"][0]["text"])
        self.assertIn(f"{fmt4(self.field_max)} MPa", got["anaRef"][0]["text"])
        self.assertTrue(got["anaRows"])
        self.assertLess(got["anaRef"][0]["px"], min(got["anaRows"]))
        self.assertEqual(got["anaNote"], MOUNTS)
        page.close()
        page, errors = self.open("run=sample&pace=0&t=0&tab=grid", self.events)
        got = page.evaluate(PROBE)
        self.assertEqual(errors, [])
        self.assertEqual(got["tileNote"], MOUNTS)
        page.close()

    def test_a_field_of_the_judged_quantity_keeps_the_versions_allowable_line(self):
        page, errors = self.open("run=sample&pace=0&t=0", self.events)
        page.click('#legend [data-variant^="frame_static|"]')
        page.evaluate("() => window.hiraku.seek(1e6)")
        got = page.evaluate(PROBE)
        self.assertEqual(errors, [])
        # the beam's von Mises is its analysis' judged result (max_stress): held to version 2's allowable
        self.assertEqual(got["lim"], f"許容 {fmt4(LIMIT_V2)} MPa を超えた所")
        self.assertNotIn("参考の色", got["legend"])
        self.assertNotIn("156.7", got["legend"])
        self.assertEqual(got["top"]["max"], LIMIT_V2)
        page.close()

    def test_the_judged_tag_is_red_only_when_the_version_failed(self):
        # the shell field's largest value is the judged hot-spot value, and version 2 fails on it
        events, hot, _ = judged_events(hot_share=1.0, passed=False)
        page, errors = self.open("run=sample&pace=0&t=0&present=1", events)
        got = page.evaluate(PROBE)
        self.assertEqual(errors, [])
        self.assertEqual(got["verdict"], "基準を満たさない")
        now = [t for t in got["tagTones"] if t["text"] == f"最大 {fmt4(hot)} MPa"]
        self.assertEqual(len(now), 1, got["tagTones"])
        self.assertTrue(now[0]["fail"], now)
        self.assertEqual(now[0]["color"], RED_RGB)
        # no tag of a value the verdict does not use is red
        for t in got["tagTones"]:
            if t["text"].startswith("参考"):
                self.assertFalse(t["fail"], t)
        page.close()

    def test_what_changed_is_made_from_the_records_values_and_ids_only_under_dev(self):
        events = changed_events(self.events)
        page, errors = self.open("run=sample&pace=0&t=0&present=1", events)
        got = page.evaluate(PROBE)
        self.assertEqual(errors, [])
        # version 1's name for the variable, version 1's value, the change's value and unit
        self.assertRegex(got["extra"], rf"変えたこと: v1 → v2 で、{re.escape(TRACK_NAME)}を 1,?300 → 1,?350 mm")
        # no record id and no English summary on the card for viewers
        self.assertNotRegex(got["cardText"], r"(chg|ver|ana|pred|cmp)-\d")
        self.assertNotIn("frame:", got["cardText"])
        self.assertIsNone(got["dev"])
        page.close()
        page, errors = self.open("run=sample&pace=0&t=0&present=1&dev=1", events)
        got = page.evaluate(PROBE)
        self.assertEqual(errors, [])
        # ?dev=1: the packager's words with the ids, folded under くわしく
        self.assertIn("くわしく", got["dev"])
        self.assertIn("chg-0013 (frame: track 1300→1350 mm)", got["dev"])
        page.close()

    def test_the_step_card_puts_the_wheel_force_under_reference(self):
        page, errors = self.open("run=sample&pace=0&t=9&present=1")
        got = page.evaluate(PROBE)
        self.assertEqual(errors, [])
        self.assertEqual(got["scene"], "step")
        self.assertEqual(got["val"], "123.6 MPa")
        self.assertIn("判定の量", got["what"])
        self.assertIn("反力 (FL)", got["ref"])
        self.assertIn("1.8 kN", got["ref"])
        page.close()


if __name__ == "__main__":
    unittest.main()
