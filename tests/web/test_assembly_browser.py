"""The assembly order in a browser: the A7 tab and the video's cut N16.

Skipped unless HIRAKU_WEB_BROWSER=1 and Playwright is installed, as test_browser.
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

# Three steps on the sample design (a wheel part is added at the FL support, so a wheel
# drawn from its support is moved too). Step 2 has no room for the tool.
STEPS = [
    {"order": 1, "part": "battery", "direction": "-z", "depends_on": [], "no_interference": True, "tool_clearance": True, "hand_reach": True,
     "fastenings": [{"to": "node N1", "kind": "bolt", "at_mm": [-900.0, 300.0, 200.0]}],
     "path_mm": [[-900.0, 0.0, 1300.0], [-900.0, 0.0, 300.0]], "text": "電池を上から下ろし、節点にボルトで留める"},
    {"order": 2, "part": "seat", "direction": "-x", "depends_on": ["battery"], "no_interference": True, "tool_clearance": False, "hand_reach": True,
     "blocked_by": ["battery"], "fastenings": [{"to": "part battery", "kind": "rest"}],
     "path_mm": [[0.0, 0.0, 900.0], [-400.0, 0.0, 900.0], [-900.0, 0.0, 600.0]], "text": "座席を前から入れ、電池の上に載せる"},
    {"order": 3, "part": "wheel_FL", "direction": "-y", "depends_on": [], "no_interference": True, "tool_clearance": True, "hand_reach": False,
     "fastenings": [{"to": "member rail_L", "kind": "pin", "at_mm": [0.0, 650.0, 300.0]}],
     "path_mm": [[0.0, 1500.0, 300.0], [0.0, 650.0, 300.0]], "text": "前左の車輪を左から入れ、ピンで留める"},
]
INTERFERENCES = [{"a": "part:wheel_FL", "b": "member:rail_L", "depth_mm": 12.5, "state": "motion", "condition": "steer 30 deg"}]


def assembly_events(focus_a7=False):
    """The contract sample with an assembly analysis.finished (and screen.focus A7) before its end."""
    src = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    design = next(e for e in src if e["kind"] == "design.created")
    design["data"]["parts"].append({"id": "wheel_FL", "name": "前左の車輪 (タイヤ)", "kind": "wheel", "mass_kg": 12.0,
                                    "box": {"center_mm": [0.0, 650.0, 300.0], "size_mm": [500.0, 180.0, 500.0]}})
    ana = copy.deepcopy(next(e for e in src if e["kind"] == "analysis.finished"))
    end = src.pop()  # run.finished
    ana["t"] = end["t"] - 0.4
    ana["data"] = {"analysis_id": "ana-9001", "tool": "assembly", "base": "K4", "design_version": ana["data"]["design_version"],
                   "load_case_id": "H5-order", "prediction_ids": ana["data"]["prediction_ids"], "status": "ok",
                   "results": [{"quantity": "assembly_steps", "value": 3.0, "unit": "1"}],
                   "assembly": copy.deepcopy(STEPS), "interferences": copy.deepcopy(INTERFERENCES)}
    out = src + [ana]
    if focus_a7:
        out.append({**copy.deepcopy(next(e for e in src if e["kind"] == "screen.focus")), "t": end["t"] - 0.2, "data": {"screen": "A7"}})
    out.append(end)
    for i, ev in enumerate(out):
        ev["seq"] = i
    return out


def n16_layout():
    """Placeholder layout rules with the n16 keys the cut reads (the promo department's are to come)."""
    return {
        "stage": {"w": 1920, "h": 1080},
        "subtitle_band": {"y": 910, "h": 170},
        "min_text_px": 18,
        "n16": {
            "duration_s": 9.0,
            "view": {"x": 96, "y": 96, "w": 1180, "h": 790},
            "seek": {"x": 96, "y": 822, "w": 1180, "h": 64},
            "steps": {"x": 1310, "y": 96, "w": 514, "h": 790, "text_px": 22, "row_gap": 8},
            "times": {"lead_s": 0.5, "step_s": 2.0},
        },
    }


@unittest.skipUnless(ENABLED, "set HIRAKU_WEB_BROWSER=1 with Playwright installed")
class AssemblyBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.run_id = "20261102-203000-assembly"
        (cls.tmp / "runs" / cls.run_id).mkdir(parents=True)
        events = assembly_events()
        (cls.tmp / "runs" / cls.run_id / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
        cls.layout_file = cls.tmp / "layout.json"
        cls.layout_file.write_text(json.dumps(n16_layout()), encoding="utf-8")
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
        self.layout_file.write_text(json.dumps(n16_layout()), encoding="utf-8")

    def open(self, path):
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.goto(f"{self.base}/{path}")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
        page.evaluate("window.hiraku.ready")
        return page, errors

    def test_events_follow_the_contract(self):
        for ev in assembly_events(focus_a7=True):
            self.assertEqual(contract.errors("event", ev), [], ev["kind"])

    # ---- A7 ----------------------------------------------------------------------------
    A7_STATE = """() => {
      const a = window.hiraku.asm;
      const rows = [...document.querySelectorAll('#view-asm .asm-steps li')];
      return {
        tabOn: document.querySelector('.tab.on')?.dataset.tab ?? null,
        shown: !document.getElementById('view-asm').hidden,
        rows: rows.map((li) => ({
          order: Number(li.dataset.order), cur: li.classList.contains('cur'), text: li.textContent,
          checks: [...li.querySelectorAll('.ck')].map((c) => c.classList.contains('is-ok')),
        })),
        u: Number(document.querySelector('#view-asm .asm-seek').dataset.u),
        label: document.querySelector('#view-asm .as-label').textContent,
        caption: document.querySelector('#view-asm .asm-caption').textContent,
        visible: a.stage.movers.map((m) => m.obj.group.visible),
        kinds: a.stage.movers.map((m) => m.kind),
        canvas: (() => { const c = document.querySelector('#view-asm canvas.gl'); return c ? [c.width, c.height] : null; })(),
        sum: document.querySelector('#view-asm .asm-sum').textContent,
        intf: document.querySelector('#view-asm .asm-intf').textContent,
        none: !document.querySelector('#view-asm .asm-none').hidden,
      };
    }"""

    def test_a7_tab_shows_the_steps_and_the_3d(self):
        page, errors = self.open("?run=sample&pace=0&t=0")
        events = assembly_events()
        page.evaluate("async (ev) => { await window.hiraku.load(ev, ev.at(-1).t); }", events)
        page.click("button[data-tab=A7]")
        page.wait_for_function("window.hiraku.asm && document.querySelectorAll('#view-asm .asm-steps li').length === 3")
        got = page.evaluate(self.A7_STATE)
        self.assertEqual(errors, [])
        self.assertTrue(got["shown"])
        self.assertEqual(got["tabOn"], "A7")
        self.assertEqual([r["order"] for r in got["rows"]], [1, 2, 3])
        # the checks as recorded: interference, tool, hand
        self.assertEqual([r["checks"] for r in got["rows"]], [[s["no_interference"], s["tool_clearance"], s["hand_reach"]] for s in STEPS])
        self.assertIn("電池", got["rows"][0]["text"])
        self.assertIn(STEPS[0]["text"], got["rows"][0]["text"])
        self.assertIn("ボルト: 節点 N1", got["rows"][0]["text"])
        self.assertIn("載せる: 電池", got["rows"][1]["text"])  # a part fastened to is named
        self.assertIn("止めているもの: 電池", got["rows"][1]["text"])  # ✕ shows what blocks it
        self.assertIn("ピン: 部材 rail_L", got["rows"][2]["text"])
        # it opens on the vehicle built, the last step marked
        self.assertEqual(got["u"], 3)
        self.assertEqual([r["cur"] for r in got["rows"]], [False, False, True])
        self.assertEqual(got["label"], "手順 3 / 3")
        self.assertIn(STEPS[2]["text"], got["caption"])
        self.assertEqual(got["kinds"], ["part", "part", "wheel"])
        self.assertEqual(got["visible"], [True, True, True])
        self.assertTrue(got["canvas"] and got["canvas"][0] > 300 and got["canvas"][1] > 300)
        self.assertIn("工具 ✕ 1", got["sum"])
        self.assertIn("手 ✕ 1", got["sum"])
        self.assertIn("12.5 mm", got["intf"])
        self.assertFalse(got["none"])

        # the bar under the hand: its left end is the state before any step, 1/3 the first step in place
        box = page.locator("#view-asm .as-track").bounding_box()
        y = box["y"] + box["height"] / 2
        page.mouse.click(box["x"] + box["width"] / 3, y)
        page.wait_for_function("document.querySelector('#view-asm .asm-seek').dataset.u === '1'")
        got = page.evaluate(self.A7_STATE)
        self.assertEqual([r["cur"] for r in got["rows"]], [True, False, False])
        self.assertEqual(got["visible"], [True, False, False])
        self.assertIn(STEPS[0]["text"], got["caption"])
        page.mouse.click(box["x"] + box["width"] * 2 / 3, y)
        page.wait_for_function("document.querySelector('#view-asm .asm-seek').dataset.u === '2'")
        got = page.evaluate(self.A7_STATE)
        self.assertEqual([r["cur"] for r in got["rows"]], [False, True, False])
        self.assertEqual(got["visible"], [True, True, False])
        # a step's row also takes the order there
        page.click("#view-asm .asm-steps li[data-order='3']")
        page.wait_for_function("document.querySelector('#view-asm .asm-seek').dataset.u === '3'")

        # play from the end starts over and runs the parts in along their paths
        page.click("#view-asm .as-play")
        page.wait_for_function("(() => { const u = Number(document.querySelector('#view-asm .asm-seek').dataset.u); return u > 0.2 && u < 0.8; })()")
        mid = page.evaluate(
            """() => { const m = window.hiraku.asm.stage.movers[0]; const p = m.obj.group.position;
                       return { z: p.z, x: p.x, label: document.querySelector('#view-asm .as-play').textContent }; }"""
        )
        self.assertGreater(mid["z"], 0)  # the battery is above its place, on its way down (-z)
        self.assertAlmostEqual(mid["x"], 0, places=6)
        self.assertEqual(mid["label"], "❚❚")
        self.assertEqual(errors, [])
        page.close()

    def test_a7_opens_on_screen_focus(self):
        page, errors = self.open("?run=sample&pace=0&t=0")
        events = assembly_events(focus_a7=True)
        page.evaluate("async (ev) => { await window.hiraku.load(ev, ev.at(-1).t); }", events)
        page.wait_for_function("document.querySelectorAll('#view-asm .asm-steps li').length === 3")
        got = page.evaluate(self.A7_STATE)
        self.assertEqual(got["tabOn"], "A7")
        self.assertTrue(got["shown"])
        self.assertEqual(errors, [])
        page.close()

    def test_a7_without_a_record_says_so(self):
        page, errors = self.open("?run=sample&pace=0&t=9")
        page.click("button[data-tab=A7]")
        page.wait_for_function("window.hiraku.asm")
        got = page.evaluate(
            """() => ({ none: !document.querySelector('#view-asm .asm-none').hidden,
                       text: document.querySelector('#view-asm .asm-none').textContent,
                       body: !document.querySelector('#view-asm .asm-body').hidden })"""
        )
        self.assertEqual(got, {"none": True, "text": "組み立ての記録がまだ無い", "body": False})
        self.assertEqual(errors, [])
        page.close()

    # ---- N16 ---------------------------------------------------------------------------
    N16_STATE = """() => {
      const r = (el) => { const b = el.getBoundingClientRect(); return [b.left, b.top, b.width, b.height]; };
      const rows = [...document.querySelectorAll('[data-box^="n16.step."]')].filter((li) => li.style.display !== 'none');
      const texts = [...document.querySelectorAll('[data-box^="n16"] *, [data-box^="n16"]')]
        .filter((el) => [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim()));
      return {
        err: document.querySelector('.err')?.textContent ?? null,
        duration: window.hiraku.player.duration,
        step: Number(document.documentElement.dataset.n16Step),
        cur: [...document.querySelectorAll('[data-box^="n16.step."].cur')].map((li) => li.dataset.order),
        u: Number(document.querySelector('[data-box="n16.seek"]').dataset.u),
        fill: parseFloat(document.querySelector('[data-box="n16.seek"] .as-fill').style.width),
        caption: document.querySelector('[data-box="n16.caption"]').textContent,
        boxes: Object.fromEntries(['n16.view', 'n16.seek', 'n16.steps'].map((k) => [k, r(document.querySelector(`[data-box="${k}"]`))])),
        rows: rows.map((li) => [li.dataset.box, r(li)]),
        checks: rows.map((li) => [...li.querySelectorAll('.ck')].map((c) => c.classList.contains('is-ok'))),
        sizes: texts.map((el) => parseFloat(getComputedStyle(el).fontSize)),
        outside: document.querySelectorAll('[data-outside]').length,
        visible: window.hiraku.cut.asm.movers.map((m) => m.obj.group.visible),
      };
    }"""

    def test_n16_steps_follow_the_time(self):
        layout = n16_layout()
        T = layout["n16"]["times"]
        page, errors = self.open(f"cut.html?cut=n16&run={self.run_id}&t=0")
        at = lambda t: (page.evaluate(f"window.hiraku.seek({t})"), page.evaluate(self.N16_STATE))[1]

        got = at(0)
        self.assertIsNone(got["err"])
        self.assertEqual(got["duration"], layout["n16"]["duration_s"])
        # before the lead ends: step 1 at the start of its path
        self.assertEqual(got["step"], 1)
        self.assertEqual(got["u"], 0)
        self.assertEqual(got["visible"], [True, False, False])

        for k in (1, 2, 3):
            got = at(T["lead_s"] + (k - 0.5) * T["step_s"])
            self.assertEqual(got["step"], k)
            self.assertEqual(got["cur"], [str(k)])
            self.assertAlmostEqual(got["u"], k - 0.5)
            self.assertAlmostEqual(got["fill"], (k - 0.5) / 3 * 100, places=3)
            self.assertIn(STEPS[k - 1]["text"], got["caption"])
            self.assertEqual(got["visible"], [i < k for i in range(3)])

        got = at(layout["n16"]["duration_s"])
        self.assertEqual(got["step"], 3)
        self.assertEqual(got["u"], 3)
        self.assertEqual(got["fill"], 100)
        # the marks as recorded
        self.assertEqual(got["checks"], [[s["no_interference"], s["tool_clearance"], s["hand_reach"]] for s in STEPS])

        # in the layout's boxes, out of the subtitle band, no text below min_text_px
        band = layout["subtitle_band"]["y"]
        for key in ("view", "seek", "steps"):
            b = layout["n16"][key]
            self.assertEqual([round(v) for v in got["boxes"][f"n16.{key}"]], [b["x"], b["y"], b["w"], b["h"]])
        for box in [*got["boxes"].values(), *[r for _, r in got["rows"]]]:
            self.assertLessEqual(box[1] + box[3], band + 0.5)
        S = layout["n16"]["steps"]
        self.assertEqual([k for k, _ in got["rows"]], ["n16.step.1", "n16.step.2", "n16.step.3"])
        for _, r in got["rows"]:
            self.assertLessEqual(r[1] + r[3], S["y"] + S["h"] + 0.5)
        self.assertTrue(got["sizes"])
        self.assertGreaterEqual(min(got["sizes"]), layout["min_text_px"])
        self.assertEqual(got["outside"], 0)
        self.assertEqual(errors, [])
        page.close()

    def test_n16_names_missing_layout_keys(self):
        layout = n16_layout()
        del layout["n16"]["times"]
        del layout["n16"]["steps"]["row_gap"]
        self.layout_file.write_text(json.dumps(layout), encoding="utf-8")
        page, errors = self.open(f"cut.html?cut=n16&run={self.run_id}&t=1")
        err = page.evaluate("document.querySelector('.err')?.textContent ?? ''")
        self.assertIn("layout.json の n16 に無い: steps.row_gap, times.lead_s, times.step_s", err)
        page.close()


if __name__ == "__main__":
    unittest.main()
