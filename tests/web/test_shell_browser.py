"""The shell views in a headless browser, on the two-version record of shell_fixture: the
legend's fields of the beam and the shell analyses, the zoom to the largest element,
before / after on one scale, and the N07 cut on stand-in layout rules.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed (see test_browser.py).
"""

import copy
import json
import math
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku import contract
from hiraku.webserve.server import make_server
from tests.web.shell_fixture import BEAM_TOOL, SHELL_TOOL, shell_events
from tests.web.test_browser import ENABLED, n05_layout, sync_playwright


def n07_layout():
    """The cut pages' stand-in layout rules with an n07 block: the promo department's
    proposed n07 values (not in layout.json yet); colors and overlay as n01's."""
    layout = n05_layout()
    layout["n07"] = {
        "duration_s": 15.3,
        "view": {"x": 96, "y": 96, "w": 1500, "h": 790},
        "legend": {"x": 1620, "y": 96, "w": 204, "h": 790, "title_px": 22, "text_px": 20},
        "label": {"x": 120, "y": 120, "size_px": 30, "of_view_h": 0.08},
        "ring": {"of_view_h": 0.08},
        "times": {"lead_s": 0.3, "turn_to_s": 6.5, "zoom_s": 6.5, "zoom_to_s": 8.5, "swap_s": 11.0, "swap_to_s": 13.0},
        "turn_deg": 90,
        "zoom": {"fill": 0.45},
        "colors": copy.deepcopy(layout["n01"]["colors"]),
        "overlay": copy.deepcopy(layout["n01"]["overlay"]),
    }
    return layout


def sig4(v):
    """A value as labels.js fmt() writes it with 4 significant digits (for these values)."""
    return f"{float(f'{v:.4g}'):g}"


# Distance from the camera to the largest element and the camera's position (three.js m).
CAMERA = """() => { const b = window.hiraku.bench; const f = b.maxFocus(b.keys.contour);
  const c = f ? f.center.clone().applyMatrix4(b.iso.matrix) : null;
  return { dist: c ? b.camera.position.distanceTo(c) : null, pos: b.camera.position.toArray(), target: b.controls.target.toArray(),
           focused: !!b.focused, elem: f ? f.elem : null }; }"""

LEGEND = """() => { const b = window.hiraku.bench; const ct = b.keys.contour;
  return { rows: [...document.querySelectorAll("#legend .variants")].map((r) => [r.dataset.analysis, [...r.querySelectorAll("button")].map((x) => [x.textContent, x.classList.contains("on")])]),
           ana: ct?.analysisId ?? null, shell: Object.values(b.shellObjs).some((o) => o.group.visible), beams: b.model.visible,
           src: document.querySelector("#legend .src")?.textContent ?? "", acts: [...document.querySelectorAll("#legend .acts button")].map((x) => x.dataset.act) }; }"""


# Screen boxes [left, top, right, bottom] of each compare view's vehicle (its outline points
# through the view's camera), of the two canvases, of the legend and of the largest element.
VEHICLE_BOXES = """() => { const vp = document.getElementById("viewport");
  const R = window.hiraku.bench; const L = window.hiraku.cmpBench;
  const rc = vp.querySelector(":scope > canvas.gl").getBoundingClientRect(); const lc = vp.querySelector(".cmp-view canvas.gl").getBoundingClientRect();
  const at = (b, r, p) => { const q = p.clone().applyMatrix4(b.iso.matrixWorld).project(b.camera); return [r.left + (q.x + 1) / 2 * r.width, r.top + (1 - q.y) / 2 * r.height]; };
  const box = (b, r) => { let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const p of b.contentPoints()) { const [x, y] = at(b, r, p); x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
    return [x0, y0, x1, y1]; };
  const lg = document.getElementById("legend").getBoundingClientRect();
  const f = R.maxFocus(R.keys.contour);
  return { right: box(R, rc), left: box(L, lc), rightCanvas: [rc.left, rc.top, rc.right, rc.bottom], leftCanvas: [lc.left, lc.top, lc.right, lc.bottom],
           legend: [lg.left, lg.top, lg.right, lg.bottom], focus: at(R, rc, f.center) }; }"""


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class ShellBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.events, cls.meshes, cls.shells = shell_events()
        cls.tmp = Path(tempfile.mkdtemp())
        runs = cls.tmp / "runs"
        cls.run_id = cls.events[0]["run_id"]
        (runs / cls.run_id).mkdir(parents=True)
        (runs / cls.run_id / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in cls.events), encoding="utf-8")
        cls.layout_file = cls.tmp / "layout.json"
        cls.layout_file.write_text(json.dumps(n07_layout()), encoding="utf-8")
        cls.server = make_server("127.0.0.1", 0, runs, quiet=True, saved_dir=None, launcher=False, layout_file=cls.layout_file)
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
        shutil.rmtree(cls.tmp)

    def open(self, query):
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.goto(f"{self.base}/{query}")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
        page.evaluate("window.hiraku.ready")
        return page, errors

    def bench(self):
        page, errors = self.open(f"?run={self.run_id}&pace=0&t=1e6")
        page.evaluate("() => window.hiraku.seek(1e6)")
        return page, errors

    def test_fixture_fits_the_contract(self):
        for e in self.events:
            self.assertEqual(contract.errors("event", e), [], e["kind"])
        self.assertEqual([e["data"]["design_version"] for e in self.events if e["kind"] == "mesh.created"], [1, 2])
        limit = self.events[0]["data"]["load_cases"][0]["criterion"]["limit"]["value"]
        self.assertGreater(self.shells[1]["results"][0]["value"], limit)
        self.assertLess(self.shells[2]["results"][0]["value"], limit)

    def test_legend_lists_the_beam_and_the_shell_analyses(self):
        page, errors = self.bench()
        got = page.evaluate(LEGEND)
        self.assertEqual(errors, [])
        # version 2: the beam analysis (ana-0003) and the shell analysis (ana-0004), each on its row
        self.assertEqual([r[0] for r in got["rows"]], ["ana-0003", "ana-0004"])
        beam = BEAM_TOOL["title"]
        shell = SHELL_TOOL["title"]
        self.assertEqual(got["rows"][0][1], [[f"{beam}・梁", False]])
        self.assertEqual([b[0] for b in got["rows"][1][1]], [f"{shell}・メッシュ・{s}" for s in ("外面", "中面", "内面", "外面と内面の大きい方")])
        self.assertEqual(got["rows"][1][1][3][1], True)
        self.assertEqual((got["ana"], got["shell"], got["beams"]), ("ana-0004", True, False))
        self.assertIn("色の元: ana-0004・" + shell, got["src"])
        page.click(f"#legend .variants button:text-is('{beam}・梁')")
        page.wait_for_function("window.hiraku.bench.keys.contour?.analysisId === 'ana-0003'")
        beams = page.evaluate(LEGEND)
        self.assertEqual((beams["shell"], beams["beams"]), (False, True))
        self.assertIn("色の元: ana-0003・" + beam, beams["src"])
        self.assertNotIn("zoom", beams["acts"])  # the beams have no element to go to
        # the right pane still follows the shown analysis
        self.assertEqual(page.evaluate("() => window.hiraku.state().analyses['ana-0004'].tool"), "frame_shell")
        page.click(f"#legend .variants button:text-is('{shell}・メッシュ・内面')")
        page.wait_for_function("window.hiraku.bench.keys.contour?.field.surface === 'bottom'")
        self.assertEqual(page.evaluate(LEGEND)["ana"], "ana-0004")
        self.assertEqual(errors, [])
        page.close()

    def test_zoom_to_the_largest_element_and_back(self):
        page, errors = self.bench()
        before = page.evaluate(CAMERA)
        mx = self.shells[2]["fields"][3]["per_element"]
        self.assertEqual(before["elem"], max(range(len(mx)), key=mx.__getitem__))
        page.click("#legend [data-act=zoom]")
        page.wait_for_function("!!window.hiraku.bench.focused")
        near = page.evaluate(CAMERA)
        self.assertLess(near["dist"], before["dist"] / 3)
        # the camera looks at the element's centre
        self.assertLess(math.dist(near["target"], page.evaluate("() => { const b = window.hiraku.bench; return b.maxFocus(b.keys.contour).center.clone().applyMatrix4(b.iso.matrix).toArray(); }")), 1e-9)
        self.assertEqual(page.evaluate("() => document.querySelector('#legend [data-act=zoom]').textContent"), "元の見え方に戻る")
        # close up the part boxes and the centre-of-gravity mark are left out; their buttons stay on
        seen = """() => { const b = window.hiraku.bench; return { parts: b.parts.visible, cg: b.cg.visible,
          buttons: ["parts", "cg"].map((k) => document.querySelector(`#toggles [data-toggle=${k}]`).classList.contains("on")) }; }"""
        self.assertEqual(page.evaluate(seen), {"parts": False, "cg": False, "buttons": [True, True]})
        page.click("#legend [data-act=zoom]")
        page.wait_for_function("!window.hiraku.bench.focused")
        back = page.evaluate(CAMERA)
        self.assertLess(math.dist(back["pos"], before["pos"]), 1e-9)
        self.assertLess(math.dist(back["target"], before["target"]), 1e-9)
        self.assertEqual(page.evaluate(seen), {"parts": True, "cg": True, "buttons": [True, True]})
        self.assertEqual(errors, [])
        page.close()

    def test_compare_two_versions_on_one_scale(self):
        page, errors = self.bench()
        self.assertIn("compare", page.evaluate(LEGEND)["acts"])
        page.click("#legend [data-act=compare]")
        page.wait_for_function("document.getElementById('viewport').classList.contains('compare')")
        got = page.evaluate(
            """() => { const b = window.hiraku.bench; const vp = document.getElementById("viewport");
              const left = vp.querySelector(".cmp-view canvas.gl").getBoundingClientRect(); const right = vp.querySelector(":scope > canvas.gl").getBoundingClientRect();
              const l = window.hiraku.cmpBench.keys.contour;
              return { main: [b.keys.contour.min, b.keys.contour.max, b.keys.contour.analysisId],
                       other: [l.min, l.max, l.analysisId, window.hiraku.cmpBench.design.design_version],
                       tags: [...vp.querySelectorAll(".cmp-tags span")].map((s) => s.textContent),
                       cmp: document.querySelector("#legend .cmp").textContent,
                       left: [left.left, left.width], right: [right.left, right.width] }; }"""
        )
        self.assertEqual(got["other"][2:], ["ana-0002", 1])
        self.assertEqual(got["other"][:2], got["main"][:2])
        limit = self.events[0]["data"]["load_cases"][0]["criterion"]["limit"]["value"]
        self.assertEqual(got["main"][2], "ana-0004")
        self.assertAlmostEqual(got["main"][1], limit)  # both versions' top: the allowable stress
        self.assertEqual(got["tags"], ["v1", "v2"])
        v1, v2 = (self.shells[v]["results"][0]["value"] for v in (1, 2))
        self.assertIn(f"v1 (左): 最大 {sig4(v1)} MPa", got["cmp"])
        self.assertIn(f"v2 (右): 最大 {sig4(v2)} MPa", got["cmp"])
        self.assertLess(got["left"][0], got["right"][0])
        self.assertAlmostEqual(got["left"][1], got["right"][1], delta=2)
        # each vehicle on screen (its outline points projected through its view's camera) stays
        # inside its half, and the right one clear of the legend's box
        boxes = page.evaluate(VEHICLE_BOXES)
        for side in ("right", "left"):
            v, c = boxes[side], boxes[f"{side}Canvas"]
            self.assertGreaterEqual(v[0], c[0] - 1, side)
            self.assertLessEqual(v[2], c[2] + 1, side)
        v, lg = boxes["right"], boxes["legend"]
        self.assertFalse(v[0] < lg[2] and lg[0] < v[2] and v[1] < lg[3] and lg[1] < v[3], (v, lg))
        # zoomed in, the largest element (the middle of the view's free part) is left of the legend
        page.click("#legend [data-act=zoom]")
        page.wait_for_function("!!window.hiraku.bench.focused")
        page.evaluate("() => window.hiraku.seek(1e6)")
        boxes = page.evaluate(VEHICLE_BOXES)
        self.assertLess(boxes["focus"][0], boxes["legend"][0])
        self.assertGreater(boxes["focus"][0], boxes["rightCanvas"][0])
        self.assertEqual(errors, [])
        page.close()

    def test_compare_scale_without_a_limit_is_the_larger_maximum(self):
        """With no allowable value in the load case, the shared top is the larger of the two
        versions' recorded maxima, and the left view is coloured on it."""
        events = copy.deepcopy(self.events)
        del events[0]["data"]["load_cases"][0]["criterion"]["limit"]
        events[0]["data"]["load_cases"][0]["criterion"]["op"] = "<="
        page, errors = self.bench()
        page.evaluate("async (events) => { await window.hiraku.load(events, 1e6); }", events)
        page.click("#legend [data-act=compare]")
        page.wait_for_function("document.getElementById('viewport').classList.contains('compare')")
        got = page.evaluate("() => { const ct = window.hiraku.bench.keys.contour; return [ct.min, ct.max, ct.limitKnown]; }")
        v1 = self.shells[1]["results"][0]["value"]
        self.assertAlmostEqual(got[1], v1)
        self.assertFalse(got[2])
        self.assertIn("上端 = 2 つの設計の最大値", page.evaluate("() => document.querySelector('#legend .src').textContent"))
        page.close()

    # ---- N07 ----------------------------------------------------------------------

    def cut(self, t, query=""):
        page, errors = self.open(f"cut.html?cut=n07&run={self.run_id}&t={t}{query}")
        return page, errors

    def test_n07_turns_zooms_and_swaps_on_time(self):
        layout = n07_layout()
        sp = layout["n07"]
        T = sp["times"]
        page, errors = self.cut(0)
        self.assertIsNone(page.evaluate("() => document.querySelector('.err')?.textContent ?? null"))
        js = """async (t) => { await window.hiraku.seek(t); const c = window.hiraku.cut; const [b, a] = c.cases; const cam = b.view.camera;
          const f = c.focus.center.clone().applyMatrix4(b.view.iso.matrix);
          const off = cam.position.clone().sub(b.view.controls.target);
          const dist = cam.position.distanceTo(f);
          return { now: c.now, dist, yaw: Math.atan2(off.x, off.z) * 180 / Math.PI,
                   // the share of the view's height the largest element's neighbourhood (2 x radius) takes
                   fill: (c.focus.radius * 0.001) / (dist * Math.tan((cam.fov * Math.PI) / 360)),
                   sides: [...document.querySelectorAll(".n07-side")].map((el) => { const r = el.getBoundingClientRect(); return [el.dataset.box, Math.round(r.left), Math.round(r.width)]; }),
                   version: document.documentElement.dataset.n07Version,
                   names: [...document.querySelectorAll(".n07-label .name")].map((el) => el.textContent),
                   diff: [...document.querySelectorAll(".n07-label .diff")].map((el) => el.textContent),
                   fixed: document.querySelectorAll(".n07-label .fixed").length,
                   text: document.getElementById("stage").innerText,
                   mark: Number(c.mark.style.opacity), markW: parseFloat(c.mark.style.width), tone: c.mark.dataset.tone,
                   marks: c.cases.map((x) => [x.mark.style.left, x.mark.style.top, x.mark.dataset.tone]),
                   cams: c.cases.map((x) => x.view.camera.position.toArray().map((v) => Math.round(v * 1e6))),
                   wheels: b.view.wheels.visible }; }"""
        times = (0, T["lead_s"], (T["lead_s"] + T["turn_to_s"]) / 2, T["turn_to_s"], T["zoom_to_s"], T["swap_s"], (T["swap_s"] + T["swap_to_s"]) / 2, T["swap_to_s"], sp["duration_s"])
        at = {t: page.evaluate(js, t) for t in times}
        self.assertEqual(errors, [])
        self.assertEqual(page.evaluate("window.hiraku.player.duration"), sp["duration_s"])
        # the turn: still until lead_s, turn_deg in all by turn_to_s, half of it at the middle (the ease is symmetric)
        turned = lambda a, b: (at[b]["yaw"] - at[a]["yaw"] + 540) % 360 - 180  # noqa: E731 (turn_deg < 180 here)
        self.assertAlmostEqual(turned(0, T["lead_s"]), 0, places=6)
        self.assertAlmostEqual(abs(turned(0, T["turn_to_s"])), sp["turn_deg"], delta=0.5)
        self.assertAlmostEqual(abs(turned(0, (T["lead_s"] + T["turn_to_s"]) / 2)), sp["turn_deg"] / 2, delta=0.5)
        # the two versions side by side all the time, through one camera, the ring at the same
        # place on both; the version after says what it added (the fixture's two gusset plates)
        V = sp["view"]
        w = (V["w"] - 16) // 2
        for t in times:
            self.assertEqual(at[t]["sides"], [["n07.view.before", V["x"], w], ["n07.view.after", V["x"] + w + 16, w]], t)
            self.assertEqual(at[t]["names"], ["補強の前", "補強の後"], t)
            self.assertEqual(at[t]["diff"], ["足した部品 2"], t)
            self.assertEqual(at[t]["version"], "1 2", t)
            self.assertEqual(at[t]["cams"][0], at[t]["cams"][1], t)
            self.assertEqual(at[t]["marks"][0][:2], at[t]["marks"][1][:2], t)
            self.assertEqual(at[t]["fixed"], 0, t)  # no verdict on the version after in this run
        # the zoom: from turn_to_s to zoom_to_s; there the neighbourhood takes zoom.fill of the height
        self.assertLess(at[T["zoom_to_s"]]["dist"], at[T["turn_to_s"]]["dist"] / 3)
        self.assertAlmostEqual(at[T["zoom_to_s"]]["fill"], sp["zoom"]["fill"], places=6)
        # the ring round the largest element: hidden before lead_s, small while the vehicle
        # turns (it shows where the weak spot is), as wide as zoom.fill of the view's height
        # once zoomed in; the wheels are left out close in (the near tyre would hide the joint)
        inner_h = V["h"] - 4
        self.assertEqual(at[0]["mark"], 0)
        self.assertEqual(at[T["turn_to_s"]]["mark"], 1)
        self.assertAlmostEqual(at[T["turn_to_s"]]["markW"], sp["ring"]["of_view_h"] * inner_h, places=3)  # far: ring.of_view_h
        self.assertAlmostEqual(at[T["zoom_to_s"]]["markW"], sp["zoom"]["fill"] * inner_h, places=3)
        self.assertTrue(at[T["turn_to_s"]]["wheels"])
        self.assertFalse(at[T["zoom_to_s"]]["wheels"])
        # red on both; this run has no verdict on the version after, so it stays red
        self.assertEqual({x[2] for t in times if t >= T["lead_s"] for x in at[t]["marks"]}, {"red"})
        # after the zoom the camera stays
        for t in (T["swap_to_s"], sp["duration_s"]):
            self.assertAlmostEqual(at[t]["dist"], at[T["zoom_to_s"]]["dist"], places=6)
        # the highlight boxes on the version after: one outline per gusset plate (no fill)
        self.assertEqual(page.evaluate("() => window.hiraku.cut.cases[1].marks.children.map((o) => o.type)"), ["LineSegments", "LineSegments"])
        self.assertIsNone(page.evaluate("() => window.hiraku.cut.cases[0].marks ?? null"))
        # no word names the kind of analysis
        for t in times:
            for word in ("梁", "シェル"):
                self.assertNotIn(word, at[t]["text"], t)
        # one scale for both versions; the legend is the stress's name and the allowable only
        got = page.evaluate(
            """() => { const c = window.hiraku.cut; const lg = document.querySelector('[data-box="n07.legend"]');
                return { scales: c.cases.map((x) => [x.ct.min, x.ct.max]), title: lg.querySelector("h3").textContent,
                         text: lg.innerText.split("\\n").map((s) => s.trim()).filter(Boolean) }; }"""
        )
        self.assertEqual(got["scales"][0], got["scales"][1])
        self.assertEqual(got["title"], "応力 [MPa]")
        limit = self.events[0]["data"]["load_cases"][0]["criterion"]["limit"]["value"]
        self.assertEqual(got["text"], ["応力 [MPa]", f"許容 {sig4(limit)}"])
        page.close()

    def test_n07_boxes_stay_out_of_the_subtitle_band(self):
        layout = n07_layout()
        page, errors = self.cut(9.5)
        got = page.evaluate(
            """() => { const box = (el) => { const r = el.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; };
              const els = [...document.querySelectorAll("#stage *")].filter((el) => getComputedStyle(el).display !== "none");
              return { boxes: Object.fromEntries([...document.querySelectorAll("[data-box]")].map((el) => [el.dataset.box, box(el)])),
                       outside: [...document.querySelectorAll("[data-outside]")].map((el) => el.dataset.box),
                       lowest: Math.max(...els.map((el) => el.getBoundingClientRect().bottom)),
                       minText: Math.min(...els.filter((el) => [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim()))
                                               .map((el) => parseFloat(getComputedStyle(el).fontSize))) }; }"""
        )
        self.assertEqual(errors, [])
        self.assertEqual(sorted(got["boxes"]), ["n07.label.after", "n07.label.before", "n07.legend", "n07.view", "n07.view.after", "n07.view.before"])
        self.assertEqual(got["outside"], [])
        self.assertLessEqual(got["lowest"], layout["subtitle_band"]["y"])
        self.assertGreaterEqual(got["minText"], layout["min_text_px"])
        for key in ("view", "legend"):
            B = layout["n07"][key]
            self.assertEqual([round(v) for v in got["boxes"][f"n07.{key}"]], [B["x"], B["y"], B["x"] + B["w"], B["y"] + B["h"]])
        # each label sits inside its side, at label.x / label.y from the side's top left (as
        # from the view's); the legend does not touch the view
        L, VB = layout["n07"]["label"], layout["n07"]["view"]
        for side in ("before", "after"):
            S, lb = got["boxes"][f"n07.view.{side}"], got["boxes"][f"n07.label.{side}"]
            self.assertEqual([round(lb[0] - S[0]), round(lb[1] - S[1])], [L["x"] - VB["x"], L["y"] - VB["y"]], side)
            self.assertTrue(S[0] <= lb[0] and lb[2] <= S[2] and S[1] <= lb[1] and lb[3] <= S[3], side)
        self.assertLessEqual(got["boxes"]["n07.view.before"][2], got["boxes"]["n07.view.after"][0])
        self.assertLessEqual(got["boxes"]["n07.view"][2], got["boxes"]["n07.legend"][0])
        page.close()

    def test_n07_names_the_missing_ring_size(self):
        """Without ring.of_view_h (the ring's size while far) the page names it and draws nothing."""
        layout = n07_layout()
        del layout["n07"]["ring"]
        self.layout_file.write_text(json.dumps(layout), encoding="utf-8")
        try:
            page, errors = self.cut(0)
            err = page.evaluate("() => document.querySelector('.err')?.textContent ?? null")
            self.assertIn("layout.json の n07 に無い: ring.of_view_h", err)
            self.assertEqual(page.evaluate("() => document.querySelectorAll('.n07-view').length"), 0)
            page.close()
        finally:
            self.layout_file.write_text(json.dumps(n07_layout()), encoding="utf-8")

    def test_n07_names_the_missing_keys(self):
        layout = n07_layout()
        layout["n07"]["zoom"] = 0.45  # the old form: a number, not {fill}
        del layout["n07"]["times"]["swap_to_s"]
        del layout["n07"]["legend"]["text_px"]
        self.layout_file.write_text(json.dumps(layout), encoding="utf-8")
        try:
            page, errors = self.cut(0)
            err = page.evaluate("() => document.querySelector('.err')?.textContent ?? null")
            self.assertIn("layout.json の n07 に無い: ", err)
            for k in ("legend.text_px", "times.swap_to_s", "zoom.fill"):
                self.assertIn(k, err)
            page.close()
        finally:
            self.layout_file.write_text(json.dumps(n07_layout()), encoding="utf-8")

    def test_n07_versions_from_the_url(self):
        page, errors = self.cut(9.5, "&before=2&after=2")
        got = page.evaluate("() => ({ err: document.querySelector('.err')?.textContent ?? null, n: window.hiraku.cut.cases.length, v: window.hiraku.cut.cases[0].version })")
        self.assertEqual(got, {"err": None, "n": 1, "v": 2})
        page.close()
        page, errors = self.cut(0, "&before=3")
        self.assertIn("v3 のメッシュの上の値の記録が無い", page.evaluate("() => document.querySelector('.err')?.textContent ?? ''"))
        page.close()


if __name__ == "__main__":
    unittest.main()
