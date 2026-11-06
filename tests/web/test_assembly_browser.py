"""The assembly order in a browser: the A7 tab and the video's cut N16.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed, as test_browser.
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


N16_CHECK_TIMES = [0.5, 3.0, 5.0, 6.2]  # the promo department's check.times for n16


def many_steps_events(n=19):
    """assembly_events with n steps (the three parts taken in turn; for the list's layout only)."""
    events = assembly_events()
    ana = next(e for e in events if e["kind"] == "analysis.finished" and e["data"].get("assembly"))
    ana["data"]["assembly"] = [{**copy.deepcopy(STEPS[i % len(STEPS)]), "order": i + 1} for i in range(n)]
    return events


def write_run(root, run_id, events):
    (root / run_id).mkdir(parents=True)
    (root / run_id / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")


def n16_layout():
    """Layout rules with the n16 values the promo department set (colors as their n01)."""
    return {
        "stage": {"w": 1920, "h": 1080},
        "subtitle_band": {"y": 910, "h": 170},
        "min_text_px": 18,
        "n16": {
            "duration_s": 6.3,
            "view": {"x": 96, "y": 96, "w": 1400, "h": 790},
            "seek": {"x": 120, "y": 814, "w": 1352, "h": 48},
            "steps": {"x": 1520, "y": 96, "w": 304, "h": 790, "title_px": 30, "text_px": 22, "row_gap": 12},
            "times": {"lead_s": 0.3, "steps_from_s": 0.5, "steps_to_s": 4.2, "scrub_s": 4.4, "scrub_to_s": 5.8},
            "colors": {"bg": "#f4f1ea", "panel": "#fbf9f4", "rule": "#c9c3b6"},
        },
    }


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class AssemblyBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.run_id = "20261102-203000-assembly"
        cls.run19 = "20261102-203000-assembly19"
        write_run(cls.tmp / "runs", cls.run_id, assembly_events())
        write_run(cls.tmp / "runs", cls.run19, many_steps_events(19))
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
        page.goto(f"{self.base}/{path}", wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
        page.evaluate("window.hiraku.ready")
        return page, errors

    def test_events_follow_the_contract(self):
        for ev in assembly_events(focus_a7=True) + many_steps_events(19):
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

    def test_bushing_and_ball_joint_fastenings(self):
        # the contract's kinds since dcb3a90: named in the step's text, their balls in joints.js's colours
        page, errors = self.open("?run=sample&pace=0&t=0")
        events = assembly_events()
        ana = next(e for e in events if e["kind"] == "analysis.finished" and e["data"].get("assembly"))
        ana["data"]["assembly"][2]["fastenings"] = [{"to": "node N1", "kind": "bushing", "at_mm": [0.0, 600.0, 300.0]},
                                                    {"to": "node N1", "kind": "ball_joint", "at_mm": [0.0, 700.0, 300.0]}]
        self.assertEqual(contract.errors("event", ana), [])
        page.evaluate("async (ev) => { await window.hiraku.load(ev, ev.at(-1).t); }", events)
        page.click("button[data-tab=A7]")
        page.wait_for_function("window.hiraku.asm && document.querySelectorAll('#view-asm .asm-steps li').length === 3")
        page.evaluate("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
        text = page.evaluate("() => document.querySelectorAll('#view-asm .asm-steps li')[2].textContent")
        balls = page.evaluate("() => window.hiraku.asm.stage.fastens.children.map((o) => '#' + o.material.color.getHexString())")
        colors = page.evaluate("async () => { const j = await import('/js/joints.js'); return [j.JOINT_KINDS.bushing.color, j.JOINT_KINDS.ball_joint.color]; }")
        self.assertEqual(errors, [])
        page.close()
        self.assertIn("ブッシュ: 節点 N1", text)
        self.assertIn("ボールジョイント: 節点 N1", text)
        self.assertEqual(balls, colors)

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
        # (the next part has already started on its way in: assembly.js LEAD)
        self.assertEqual(got["visible"], [True, True, False])
        self.assertIn(STEPS[0]["text"], got["caption"])
        page.mouse.click(box["x"] + box["width"] * 2 / 3, y)
        page.wait_for_function("document.querySelector('#view-asm .asm-seek').dataset.u === '2'")
        got = page.evaluate(self.A7_STATE)
        self.assertEqual([r["cur"] for r in got["rows"]], [False, True, False])
        self.assertEqual(got["visible"], [True, True, True])
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
      const shown = (el) => el.offsetParent !== null && getComputedStyle(el).visibility !== 'hidden';
      const rows = [...document.querySelectorAll('[data-box^="n16.step."]')].filter(shown);
      const texts = [...document.querySelectorAll('[data-box^="n16"] *, [data-box^="n16"]')]
        .filter((el) => shown(el) && [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim()));
      // every box shown, and the data-box it sits in (none at the top)
      const all = [...document.querySelectorAll('[data-box]')].filter(shown).map((el) => {
        const up = el.parentElement?.closest('[data-box]');
        return { key: el.dataset.box, box: r(el), parent: up ? up.dataset.box : null, parentBox: up ? r(up) : null };
      });
      const column = [...document.querySelectorAll('[data-box="n16.steps"] *')]
        .filter((el) => shown(el) && !el.closest('[data-kind~="note"]') && [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim()));
      return {
        notes: [...document.querySelectorAll('[data-box="n16.steps"] [data-kind~="note"]')].map((el) => [el.innerText.trim(), parseFloat(getComputedStyle(el).fontSize)]),
        err: document.querySelector('.err')?.textContent ?? null,
        duration: window.hiraku.player.duration,
        step: Number(document.documentElement.dataset.n16Step),
        u: Number(document.querySelector('[data-box="n16.seek"]').dataset.u),
        fill: parseFloat(document.querySelector('[data-box="n16.seek"] .as-fill').style.width),
        caption: document.querySelector('[data-box="n16.caption"]').textContent,
        boxes: Object.fromEntries(['n16.view', 'n16.seek', 'n16.steps'].map((k) => [k, r(document.querySelector(`[data-box="${k}"]`))])),
        all,
        rows: rows.map((li) => li.dataset.box),
        sums: [...document.querySelectorAll('.n16-sum')].map((el) => el.innerText.replace(/\\s+/g, ' ').trim()),
        now: document.querySelector('.n16-now')?.innerText.replace(/\\s+/g, ' ').trim() ?? '',
        outline: window.hiraku.cut.asm.outline.visible ? '#' + window.hiraku.cut.asm.outline.material.color.getHexString() : null,
        redTicks: document.querySelectorAll('[data-box="n16.seek"] .as-ticks i.ng').length,
        seekKind: document.querySelector('[data-box="n16.seek"]').dataset.kind ?? null,
        columnSizes: column.map((el) => parseFloat(getComputedStyle(el).fontSize)),
        sizes: texts.map((el) => parseFloat(getComputedStyle(el).fontSize)),
        outside: document.querySelectorAll('[data-outside]').length,
        visible: window.hiraku.cut.asm.movers.map((m) => m.obj.group.visible),
      };
    }"""

    def n16_page(self, run_id):
        page, errors = self.open(f"cut.html?cut=n16&run={run_id}&t=0")
        at = lambda t: (page.evaluate(f"window.hiraku.seek({t})"), page.evaluate(self.N16_STATE))[1]
        return page, errors, at

    def assert_boxes_fit(self, got, layout, t):
        """Out of the subtitle band, inside the stage, each box inside the data-box it sits in,
        the bar over the view's lower edge, no text below min_text_px."""
        band = layout["subtitle_band"]["y"]
        W, H = layout["stage"]["w"], layout["stage"]["h"]
        eps = 0.5
        self.assertTrue(got["all"])
        for b in got["all"]:
            x, y, w, h = b["box"]
            self.assertLessEqual(y + h, band + eps, (t, b["key"]))
            self.assertGreaterEqual(x, -eps, (t, b["key"]))
            self.assertGreaterEqual(y, -eps, (t, b["key"]))
            self.assertLessEqual(x + w, W + eps, (t, b["key"]))
            self.assertLessEqual(y + h, H + eps, (t, b["key"]))
            if b["parent"]:
                px, py, pw, ph = b["parentBox"]
                self.assertGreaterEqual(x, px - eps, (t, b["key"], b["parent"]))
                self.assertGreaterEqual(y, py - eps, (t, b["key"], b["parent"]))
                self.assertLessEqual(x + w, px + pw + eps, (t, b["key"], b["parent"]))
                self.assertLessEqual(y + h, py + ph + eps, (t, b["key"], b["parent"]))
        sx, sy, sw, sh = got["boxes"]["n16.seek"]
        vx, vy, vw, vh = got["boxes"]["n16.view"]
        self.assertTrue(vx <= sx and sx + sw <= vx + vw and vy <= sy and sy + sh <= vy + vh + eps, t)
        self.assertTrue(got["sizes"])
        self.assertGreaterEqual(min(got["sizes"]), layout["min_text_px"], t)
        self.assertEqual(got["outside"], 0, t)

    def test_n16_steps_follow_the_time(self):
        layout = n16_layout()
        T = layout["n16"]["times"]
        n = len(STEPS)
        page, errors, at = self.n16_page(self.run_id)

        got = at(0)
        self.assertIsNone(got["err"])
        self.assertEqual(got["duration"], layout["n16"]["duration_s"])
        # before steps_from: step 1 at the start of its path
        self.assertEqual(got["step"], 1)
        self.assertEqual(got["u"], 0)
        self.assertEqual(got["visible"], [True, False, False])
        for key in ("view", "seek", "steps"):
            b = layout["n16"][key]
            self.assertEqual([round(v) for v in got["boxes"][f"n16.{key}"]], [b["x"], b["y"], b["w"], b["h"]])

        # before steps_from the caption names the first part by its name in the design (no
        # node or member ids, nothing in brackets), and the column says what it found
        self.assertEqual(got["caption"], "手順 1 電池を付ける")
        self.assertEqual(got["now"], "手順 1 電池 入る")
        # steps_from..steps_to: one step after another, as many as the record has; the
        # caption holds one sentence for the whole order (too fast to read step by step)
        dt = (T["steps_to_s"] - T["steps_from_s"]) / n
        for k in range(1, n + 1):
            got = at(T["steps_from_s"] + (k - 0.5) * dt)
            self.assertEqual(got["step"], k)
            self.assertAlmostEqual(got["u"], k - 0.5)
            self.assertAlmostEqual(got["fill"], (k - 0.5) / n * 100, places=3)
            self.assertEqual(got["caption"], f"全 {n} 手順を、入る順に組む")
            self.assertEqual(got["now"], "")
            self.assertEqual(got["visible"], [i < k for i in range(n)])
            # the part going in is outlined in the current colour (orange); red only for a part that does not fit
            self.assertEqual(got["outline"], "#e9893a", k)
        seen = []
        for i in range(41):
            seen.append(at(T["steps_from_s"] + (T["steps_to_s"] - T["steps_from_s"]) * i / 40 - 1e-3)["step"])
        self.assertEqual(sorted(set(seen)), list(range(1, n + 1)))
        self.assertTrue(all(0 <= b - a <= 1 for a, b in zip(seen, seen[1:])), seen)
        got = at((T["steps_to_s"] + T["scrub_s"]) / 2)
        self.assertEqual((got["step"], got["u"]), (n, n))

        # scrub_s..scrub_to_s: the bar goes back once, then forward to the last step
        us = [at(T["scrub_s"] + (T["scrub_to_s"] - T["scrub_s"]) * i / 28)["u"] for i in range(29)]
        low = us.index(min(us))
        self.assertEqual(us[0], n)
        self.assertLess(min(us), n)
        self.assertTrue(all(b <= a for a, b in zip(us[: low + 1], us[1 : low + 1])), us)
        self.assertTrue(all(b >= a for a, b in zip(us[low:], us[low + 1 :])), us)
        self.assertEqual(us[-1], n)
        self.assertTrue(all(u == round(u) for u in us), us)  # the state lands on whole steps, as under a hand
        got = at(T["scrub_s"] + (T["scrub_to_s"] - T["scrub_s"]) * low / 28)
        # (on a whole step u the next part has already started on its way in: assembly.js LEAD)
        self.assertEqual(got["visible"], [i <= got["u"] for i in range(n)])

        # held on the last step to the end: the caption and the column name it, and the
        # column says in words what the checks found over all the steps (counted from the record)
        got = at(layout["n16"]["duration_s"])
        self.assertEqual((got["step"], got["u"], got["fill"]), (n, n, 100))
        self.assertEqual(got["caption"], "手順 3 前左の車輪を付ける")
        self.assertEqual(got["now"], "手順 3 前左の車輪 入る 手が届きにくい")
        self.assertEqual(got["sums"], ["✓ ぶつからずに入る 全手順", "工具が入りにくい手順 1", "手が届きにくい手順 1"])
        # the main line is whether the assembly holds; steps hard for a tool or a hand are small
        # notes (data-kind "note", min_text_px), no cross
        self.assertEqual(got["notes"], [["工具が入りにくい手順 1", 18.0], ["手が届きにくい手順 1", 18.0], ["手が届きにくい", 18.0]])
        self.assertEqual(got["rows"], [])  # no list of steps (one per frame was not readable)
        self.assertEqual(got["redTicks"], 0)  # every part fits: no red tick on the bar
        self.assertEqual(got["seekKind"], "note")  # the bar's "手順 3 / 3" is the makers' note
        for name in ("節点", "N1", "rail_L", "ピン"):
            self.assertNotIn(name, got["caption"] + got["now"])
        # what is read in the column is at steps.text_px (this layout has no story; with one,
        # the story's body text size: test_cut_story_browser)
        self.assertGreaterEqual(min(got["columnSizes"]), layout["n16"]["steps"]["text_px"])

        for t in N16_CHECK_TIMES:
            self.assert_boxes_fit(at(t), layout, t)
        self.assertEqual(errors, [])
        page.close()

    def test_n16_nineteen_steps_fit_at_the_check_times(self):
        """Many steps (19): the column still ends inside its box, its counts from the record."""
        layout = n16_layout()
        page, errors, at = self.n16_page(self.run19)
        for t in N16_CHECK_TIMES:
            got = at(t)
            self.assertIsNone(got["err"])
            self.assertEqual(len(got["sums"]), 3)
            self.assertIn("工具が入りにくい手順 6", got["sums"])  # steps 2, 5, ... 17 (every third)
            self.assert_boxes_fit(got, layout, t)
        self.assertEqual(errors, [])
        page.close()

    def test_n16_names_missing_layout_keys(self):
        layout = n16_layout()
        del layout["n16"]["times"]["scrub_s"]
        del layout["n16"]["steps"]["title_px"]
        del layout["n16"]["colors"]
        self.layout_file.write_text(json.dumps(layout), encoding="utf-8")
        page, errors = self.open(f"cut.html?cut=n16&run={self.run_id}&t=1")
        err = page.evaluate("document.querySelector('.err')?.textContent ?? ''")
        self.assertIn("layout.json の n16 に無い: steps.title_px, times.scrub_s, colors.bg, colors.panel, colors.rule", err)
        page.close()


if __name__ == "__main__":
    unittest.main()
