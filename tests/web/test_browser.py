"""Open the screen in a headless browser and replay the contract sample.

Skipped unless HIRAKU_WEB_BROWSER=1 and Playwright is installed (it is not
in requirements.txt: it brings a browser, and three.js comes from the CDN, so
this test also needs the network).
"""

import copy
import json
import os
import threading
import unittest

from hiraku import contract
from hiraku.webserve.server import SAMPLE_EVENTS, make_server

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None


def mismatch_events():
    """The sample run up to the analysis, then a mismatch, its cause and a stopped run.

    Shape example: the analysed value is raised so the comparison fails; the
    differences are computed here from those values.
    """
    src = {e["seq"]: e for e in map(json.loads, SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines())}
    out = [copy.deepcopy(src[i]) for i in (0, 1, 6, 7, 8)]
    ana = out[4]["data"]
    ana["results"][0]["value"] = 180.0
    pred_v, ana_v, tol = 120.0, 180.0, 0.15
    cmp = {
        "comparison_id": "cmp-0001", "prediction_id": "pred-0001", "analysis_id": "ana-0001", "quantity": "max_stress",
        "predicted": {"value": pred_v, "unit": "MPa"}, "analyzed": {"value": ana_v, "unit": "MPa"},
        "diff_abs": ana_v - pred_v, "diff_rel": (ana_v - pred_v) / pred_v, "tolerance_rel": tol,
        "status": "mismatch", "cause_id": None,
    }
    cause = {"cause_id": "cause-0001", "comparison_id": "cmp-0001", "cause": "unit mix-up (shape example)", "fix": "redo the input", "resolved": False}
    base = {"run_id": out[0]["run_id"], "ts": "2026-11-02T20:30:10.000+09:00"}
    more = [
        {"kind": "comparison.recorded", "agent": "cae.strength", "domain": "strength", "design_version": 1, "data": cmp},
        {"kind": "hook.denied", "agent": "cae.strength", "domain": "strength", "text": "cause first (shape example)",
         "data": {"hook": "PreToolUse", "tool": "mcp__hiraku__analyze", "rule": "H3_cause_before_continue"}},
        {"kind": "cause.recorded", "agent": "cae.strength", "domain": "strength", "design_version": 1, "data": cause},
        {"kind": "comparison.recorded", "agent": "cae.strength", "domain": "strength", "design_version": 1, "data": {**cmp, "cause_id": "cause-0001"}},
        {"kind": "hook.blocked_stop", "agent": "lead", "text": "not judged yet (shape example)",
         "data": {"hook": "Stop", "rule": "H4_no_stop_before_judged", "count": 1, "limit": 3}},
        {"kind": "run.finished", "agent": "system", "data": {"status": "stopped", "stop_reason": "round_limit"}},
    ]
    t = out[-1]["t"]
    for ev in more:
        t += 0.5
        out.append({"seq": 0, "t": t, **base, **ev})
    for i, ev in enumerate(out):
        ev["seq"] = i
    return out


@unittest.skipUnless(ENABLED, "set HIRAKU_WEB_BROWSER=1 with Playwright installed")
class BrowserReplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server("127.0.0.1", 0, "runs-none", quiet=True)
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

    def open(self, query):
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.goto(f"{self.base}/?{query}")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=30000)
        page.evaluate("window.hiraku.ready")
        return page, errors

    def test_frozen_frame_at_the_end(self):
        page, errors = self.open("run=sample&t=9")
        got = page.evaluate(
            """() => {
              const s = window.hiraku.state();
              return {
                playhead: window.hiraku.player.playhead,
                applied: window.hiraku.player.applied,
                comparisons: Object.keys(s.comparisons).length,
                judgement: s.judgements["1|strength"]?.state,
                hookLines: document.querySelectorAll("#log li.hook").length,
                cmpCard: document.getElementById("card-cmp").className,
                legendShown: !document.getElementById("legend").hidden,
                members: Object.keys(window.hiraku.bench.members).length,
              };
            }"""
        )
        self.assertEqual(errors, [])
        self.assertEqual(got["playhead"], 9)
        self.assertEqual(got["applied"], 15)
        self.assertEqual(got["comparisons"], 1)
        self.assertEqual(got["judgement"], "green")
        self.assertEqual(got["hookLines"], 1)
        self.assertIn("ok", got["cmpCard"])
        self.assertTrue(got["legendShown"])
        self.assertEqual(got["members"], 7)
        page.close()

    def test_seek_back_rebuilds_from_events(self):
        page, errors = self.open("run=sample&t=9")
        got = page.evaluate(
            """async () => {
              await window.hiraku.seek(2.5);
              const s = window.hiraku.state();
              return { applied: window.hiraku.player.applied, preds: Object.keys(s.predictions).length,
                       denied: s.hooks.denied.length, legendShown: !document.getElementById("legend").hidden };
            }"""
        )
        self.assertEqual(errors, [])
        self.assertEqual(got, {"applied": 5, "preds": 0, "denied": 1, "legendShown": False})
        page.close()

    def test_mismatch_cause_and_resent_records(self):
        events = mismatch_events()
        for ev in events:
            self.assertEqual(contract.errors("event", ev), [])
        page, errors = self.open("run=sample&t=0")
        got = page.evaluate(
            """async (events) => {
              await window.hiraku.load(events, 100);
              const s = window.hiraku.state();
              return {
                causeId: s.comparisons["cmp-0001"].cause_id,
                cmpOrder: s.order.comparisons,
                cmpCard: document.getElementById("card-cmp").className,
                causeBox: document.querySelector("#card-cmp .cause")?.textContent ?? "",
                hookLines: document.querySelectorAll("#log li.hook").length,
                cmpLines: [...document.querySelectorAll("#log li")].filter((li) => li.textContent.includes("照合:")).map((li) => li.textContent),
                logLen: s.log.length,
                status: s.status,
                stopReason: s.stopReason,
                hooksRow: document.getElementById("hooks").textContent,
              };
            }""",
            events,
        )
        self.assertEqual(errors, [])
        self.assertEqual(got["causeId"], "cause-0001")
        self.assertEqual(got["cmpOrder"], ["cmp-0001"])
        self.assertIn("bad", got["cmpCard"])
        self.assertIn("unit mix-up", got["causeBox"])
        self.assertEqual(got["hookLines"], 2)
        # the comparison sent again with its cause_id rewrites its own line
        self.assertEqual(len(got["cmpLines"]), 1)
        self.assertIn("cause-0001", got["cmpLines"][0])
        self.assertIn("更新", got["cmpLines"][0])
        self.assertEqual(got["logLen"], len(events) - 1)  # every event but the re-sent one has a line
        self.assertEqual(got["status"], "stopped")
        self.assertEqual(got["stopReason"], "round_limit")
        self.assertIn("H3", got["hooksRow"])
        self.assertIn("×1", got["hooksRow"])
        page.close()

    def test_loading_picks_who_is_on_board(self):
        src = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
        run, design_ev = copy.deepcopy(src[0]), copy.deepcopy(src[1])
        run["data"]["load_cases"][0]["params"]["loading"] = "driver"
        box = lambda x, y, z: {"center_mm": [x, y, z], "size_mm": [400.0, 400.0, 500.0]}
        design_ev["data"]["parts"] += [
            {"id": "occ_1", "name": "driver (shape example)", "kind": "occupant", "box": box(900.0, -250.0, 900.0), "mass_kg": 70.0},
            {"id": "occ_2", "name": "passenger (shape example)", "kind": "occupant", "box": box(900.0, 250.0, 900.0), "mass_kg": 70.0},
            {"id": "load", "name": "cargo (shape example)", "kind": "cargo", "box": box(1500.0, 0.0, 600.0), "mass_kg": 100.0},
        ]
        for ev in (run, design_ev):
            self.assertEqual(contract.errors("event", ev), [])
        page, errors = self.open("run=sample&t=0")
        got = page.evaluate(
            """async (events) => {
              await window.hiraku.load(events, 1);
              const objs = window.hiraku.bench.partObjs;
              return Object.fromEntries(Object.entries(objs).map(([id, o]) => [id, o.onBoard]));
            }""",
            [run, design_ev],
        )
        self.assertEqual(errors, [])
        self.assertEqual(got, {"battery": True, "seat": True, "occ_1": True, "occ_2": False, "load": False})
        page.close()

    def test_video_layout_keeps_the_subtitle_band_empty(self):
        page, errors = self.open("run=sample&t=6&video=1")
        got = page.evaluate(
            """() => {
              const bottoms = [...document.querySelectorAll('#stage > *:not(.subband):not([hidden])')]
                .map((el) => el.getBoundingClientRect())
                .filter((r) => r.height > 0)
                .map((r) => r.bottom);
              return { maxBottom: Math.max(...bottoms), status: getComputedStyle(document.getElementById('statusrow')).display };
            }"""
        )
        self.assertEqual(errors, [])
        self.assertLessEqual(got["maxBottom"], 1080 - 170 + 0.5)
        self.assertEqual(got["status"], "none")
        page.close()


if __name__ == "__main__":
    unittest.main()
