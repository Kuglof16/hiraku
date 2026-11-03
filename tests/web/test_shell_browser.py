"""The shell views in a headless browser, on the two-version record of shell_fixture: the
legend's fields of the beam and the shell analyses, the zoom to the largest element,
before / after on one scale, and the N07 cut on stand-in layout rules.

Skipped unless HIRAKU_WEB_BROWSER=1 and Playwright is installed (see test_browser.py).
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
    """The cut pages' stand-in layout rules with an n07 block in the shape cut_n07.js
    reads (the promo department's values are not there yet; these are made up)."""
    layout = n05_layout()
    layout["n07"] = {
        "duration_s": 11.0,
        "view": {"x": 96, "y": 72, "w": 1290, "h": 800},
        "legend": {"x": 1416, "y": 72, "w": 408, "h": 420, "title_px": 30, "text_px": 22},
        "label": {"x": 1416, "y": 520, "size_px": 26},
        "times": {"lead_s": 0.5, "turn_to_s": 5.0, "zoom_s": 5.0, "zoom_to_s": 7.0, "swap_s": 8.0, "swap_to_s": 9.5},
        "turn_deg": 120,
        "zoom": 0.6,
        "colors": {"bg": "#f4f1ea", "panel": "#fbf9f4", "rule": "#c9c3b6"},
        "overlay": {"color": "#c8402a", "opacity": 0.25},
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


@unittest.skipUnless(ENABLED, "set HIRAKU_WEB_BROWSER=1 with Playwright installed")
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
        page.click("#legend [data-act=zoom]")
        page.wait_for_function("!window.hiraku.bench.focused")
        back = page.evaluate(CAMERA)
        self.assertLess(math.dist(back["pos"], before["pos"]), 1e-9)
        self.assertLess(math.dist(back["target"], before["target"]), 1e-9)
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
        self.assertEqual(got["tags"], ["版 1", "版 2"])
        v1, v2 = (self.shells[v]["results"][0]["value"] for v in (1, 2))
        self.assertIn(f"版 1 (左): 最大 {sig4(v1)} MPa", got["cmp"])
        self.assertIn(f"版 2 (右): 最大 {sig4(v2)} MPa", got["cmp"])
        self.assertLess(got["left"][0], got["right"][0])
        self.assertAlmostEqual(got["left"][1], got["right"][1], delta=2)
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
        self.assertIn("上端 = 2 つの版の最大値", page.evaluate("() => document.querySelector('#legend .src').textContent"))
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
        js = """async (t) => { await window.hiraku.seek(t); const c = window.hiraku.cut; const [b, a] = c.cases;
          const f = c.focus.center.clone().applyMatrix4(b.view.iso.matrix);
          const off = b.view.camera.position.clone().sub(b.view.controls.target);
          return { now: c.now, dist: b.view.camera.position.distanceTo(f), yaw: Math.atan2(off.x, off.z) * 180 / Math.PI,
                   after: Number(a.el.style.opacity), version: document.documentElement.dataset.n07Version,
                   label: document.querySelector('[data-box="n07.label"]').textContent,
                   mark: Number(c.mark.style.opacity) }; }"""
        at = {t: page.evaluate(js, t) for t in (0, T["turn_to_s"] / 2, T["turn_to_s"], T["zoom_to_s"], T["swap_s"], T["swap_to_s"], sp["duration_s"])}
        self.assertEqual(errors, [])
        self.assertEqual(page.evaluate("window.hiraku.player.duration"), sp["duration_s"])
        # the turn: turn_deg in all by turn_to_s, half of it at the middle (the ease is symmetric)
        turned = lambda a, b: (at[b]["yaw"] - at[a]["yaw"] + 540) % 360 - 180  # noqa: E731 (turn_deg < 180 here)
        self.assertAlmostEqual(abs(turned(0, T["turn_to_s"])), sp["turn_deg"], delta=0.5)
        self.assertAlmostEqual(abs(turned(0, T["turn_to_s"] / 2)), sp["turn_deg"] / 2, delta=0.5)
        self.assertEqual(at[0]["now"]["sweep"], 0)
        self.assertEqual(at[T["turn_to_s"]]["now"]["sweep"], 1)
        # the zoom: much closer to the largest element by zoom_to_s, and the mark is on
        self.assertLess(at[T["zoom_to_s"]]["dist"], at[T["turn_to_s"]]["dist"] / 3)
        self.assertEqual(at[T["turn_to_s"]]["mark"], 0)
        self.assertEqual(at[T["zoom_to_s"]]["mark"], 1)
        # the swap: the version after fades in between swap_s and swap_to_s, same camera
        self.assertEqual(at[T["swap_s"]]["after"], 0)
        self.assertEqual(at[T["swap_to_s"]]["after"], 1)
        self.assertEqual((at[T["swap_s"]]["version"], at[T["swap_to_s"]]["version"]), ("1", "2"))
        self.assertTrue(at[T["swap_to_s"]]["label"].startswith("版 2 "))
        self.assertAlmostEqual(at[T["swap_to_s"]]["dist"], at[T["zoom_to_s"]]["dist"], places=6)
        # one scale for both versions; each version's maximum as recorded
        got = page.evaluate(
            """() => { const c = window.hiraku.cut; return { scales: c.cases.map((x) => [x.ct.min, x.ct.max]),
                rows: [...document.querySelectorAll('[data-box="n07.legend"] tr')].map((r) => r.textContent) }; }"""
        )
        self.assertEqual(got["scales"][0], got["scales"][1])
        self.assertEqual(got["rows"], [f"版 {v}最大 {sig4(self.shells[v]['results'][0]['value'])} MPa" for v in (1, 2)])
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
        self.assertEqual(sorted(got["boxes"]), ["n07.label", "n07.legend", "n07.view"])
        self.assertEqual(got["outside"], [])
        self.assertLessEqual(got["lowest"], layout["subtitle_band"]["y"])
        self.assertGreaterEqual(got["minText"], layout["min_text_px"])
        V = layout["n07"]["view"]
        self.assertEqual([round(v) for v in got["boxes"]["n07.view"]], [V["x"], V["y"], V["x"] + V["w"], V["y"] + V["h"]])
        page.close()

    def test_n07_names_the_missing_keys(self):
        layout = n07_layout()
        del layout["n07"]["zoom"]
        del layout["n07"]["times"]["swap_to_s"]
        del layout["n07"]["legend"]["text_px"]
        self.layout_file.write_text(json.dumps(layout), encoding="utf-8")
        try:
            page, errors = self.cut(0)
            err = page.evaluate("() => document.querySelector('.err')?.textContent ?? null")
            self.assertIn("layout.json の n07 に無い: ", err)
            for k in ("legend.text_px", "times.swap_to_s", "zoom"):
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
        self.assertIn("版 3 のシェルの解析の記録が無い", page.evaluate("() => document.querySelector('.err')?.textContent ?? ''"))
        page.close()


if __name__ == "__main__":
    unittest.main()
