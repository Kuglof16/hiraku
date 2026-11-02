import copy
import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path

from promo import bgm, layout_build, layout_check as lc

HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None


def box(x, y, w, h):
    return {"x": x, "y": y, "w": w, "h": h}


class GeneratedData(unittest.TestCase):
    def test_data_js_matches_layout_json(self):
        rules = layout_build.load()
        text = layout_build.TARGET.read_text(encoding="utf-8")
        self.assertEqual(layout_build.parse(text), rules,
                         "layout.data.js differs from layout.json: run py promo/layout_build.py")
        self.assertEqual(text, layout_build.render(rules))

    def test_data_js_says_generated(self):
        head = layout_build.TARGET.read_text(encoding="utf-8").splitlines()[0]
        self.assertIn("Generated file: do not edit", head)

    def test_render_parse_round_trip(self):
        rules = {"stage": {"w": 1920, "h": 1080}, "note": "日本語"}
        self.assertEqual(layout_build.parse(layout_build.render(rules)), rules)

    def test_check_times_name_every_cut(self):
        rules = layout_build.load()
        for cut in lc.CUTS:
            self.assertIn(cut, rules)
            self.assertTrue(rules["check"]["times"][cut])

    def test_every_allowance_gives_a_reason(self):
        chk = layout_build.load()["check"]
        for rule in chk["band_exempt"] + chk["allow_overlap"] + chk["allow_overflow"] + chk["allow_escape"]:
            self.assertTrue(rule.get("why"), rule)


class SharedShape(unittest.TestCase):
    """The names the screen department reads from layout.json."""

    def setUp(self):
        self.rules = layout_build.load()

    def test_top_level(self):
        keys = {k for k in self.rules if not k.startswith("_")}
        self.assertEqual(keys, {"stage", "subtitle_band", "safe", "min_text_px", "colors",
                                "n00", "n01", "n02", "n05", "n08b", "n18", "check"})
        r = self.rules
        self.assertEqual(r["stage"], {"w": 1920, "h": 1080})
        self.assertEqual(r["subtitle_band"]["y"] + r["subtitle_band"]["h"], r["stage"]["h"])
        self.assertEqual(r["safe"]["x"] * 2 + r["safe"]["w"], r["stage"]["w"])
        self.assertEqual(r["safe"]["y"] + r["safe"]["h"], r["subtitle_band"]["y"])
        self.assertEqual(r["min_text_px"], 18)
        for k in ("night", "ink", "tile_bg", "tile_border"):
            self.assertRegex(r["colors"][k], r"^#[0-9a-f]{6}$")

    def test_n01_boxes_stay_above_the_band(self):
        # N01 is drawn by the screens department from these values
        n, r = self.rules["n01"], self.rules
        for name in ("view", "pane"):
            b = n[name]
            self.assertLessEqual(b["y"] + b["h"], r["subtitle_band"]["y"], name)
            self.assertLessEqual(b["x"] + b["w"], r["stage"]["w"], name)
            self.assertGreaterEqual(b["x"], r["safe"]["x"], name)
        self.assertLessEqual(n["view"]["x"] + n["view"]["w"], n["pane"]["x"])  # side by side
        self.assertGreaterEqual(min(n["card"]["title_px"], n["card"]["text_px"]), r["min_text_px"])

    def test_n02_boxes_stay_above_the_band(self):
        # N02 is drawn by the screens department from these values
        n, r = self.rules["n02"], self.rules
        g = n["grid"]
        right = g["x"] + g["cols"] * g["tile_w"] + (g["cols"] - 1) * g["gap_x"]
        bottom = g["y"] + g["rows"] * g["tile_h"] + (g["rows"] - 1) * g["gap_y"]
        self.assertLessEqual(bottom, r["subtitle_band"]["y"])
        self.assertLessEqual(right, n["result"]["x"])  # the grid and the result column side by side
        res = n["result"]
        self.assertLessEqual(res["y"] + res["h"], r["subtitle_band"]["y"])
        self.assertLessEqual(res["x"] + res["w"], r["safe"]["x"] + r["safe"]["w"])
        self.assertEqual(len(n["panes"]), g["cols"] * g["rows"])
        self.assertGreaterEqual(min(res["title_px"], res["text_px"], n["pane"]["rate"]["size_px"]), r["min_text_px"])

    def test_n05(self):
        n = self.rules["n05"]
        self.assertEqual(set(n["grid"]) - {"_note"},
                         {"x", "y", "cols", "rows", "tile_w", "tile_h", "gap_x", "gap_y"})
        self.assertEqual(set(n["tile"]), {"border_px", "radius_px", "label", "rate"})
        self.assertEqual(set(n["tile"]["label"]), {"x", "y", "size_px"})
        self.assertEqual(n["hero"]["x"], (self.rules["stage"]["w"] - n["hero"]["w"]) / 2)
        self.assertEqual(set(n["title"]), {"name", "tag"})
        self.assertEqual(len(n["tests"]), n["grid"]["cols"] * n["grid"]["rows"])
        self.assertEqual(set(n["fly_from"]), {"x", "y"})
        for k in ("burst_s", "tiles_s", "tile_in_s", "tile_stagger_s", "name_s", "tag_s"):
            self.assertIn(k, n["times"])

    def test_units_in_names(self):
        def walk(obj, path):
            for k, v in obj.items():
                if k.startswith("_"):
                    continue
                if isinstance(v, dict):
                    walk(v, f"{path}.{k}")
                elif path.endswith(".times"):
                    self.assertTrue(k.endswith("_s"), f"{path}.{k}")
        for cut in lc.CUTS:
            walk(self.rules[cut], cut)
            self.assertIn("duration_s", self.rules[cut])

    def test_duration_matches_the_page(self):
        for cut in lc.CUTS:
            html = (lc.SCREENS / f"{cut}.html").read_text(encoding="utf-8")
            m = re.search(r'data-duration="([0-9.]+)"', html)
            self.assertEqual(float(m.group(1)), self.rules[cut]["duration_s"], cut)

    def test_pages_hold_no_times_or_old_names(self):
        for cut in lc.CUTS:
            html = (lc.SCREENS / f"{cut}.html").read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"--t-[a-z-]+\s*:", html), f"{cut}: a time set in the page")
            self.assertNotIn("data-check", html)
            for name in re.findall(r'data-box="([^"$]+)"', html):
                self.assertTrue(name.startswith(cut + "."), name)

    def test_bgm_reads_the_same_times(self):
        self.assertEqual(bgm.screen_time(bgm.SCREENS, "n05", "burst"), self.rules["n05"]["times"]["burst_s"])
        self.assertEqual(bgm.screen_time(bgm.SCREENS, "n18", "open"), self.rules["n18"]["times"]["open_s"])


class Judging(unittest.TestCase):
    def test_band(self):
        self.assertFalse(lc.in_band(box(0, 800, 100, 110), 910))      # ends on the band's top
        self.assertTrue(lc.in_band(box(0, 800, 100, 111), 910))
        self.assertTrue(lc.in_band(box(0, 950, 100, 20), 910))

    def test_outside(self):
        self.assertFalse(lc.outside(box(0, 0, 1920, 1080), 1920, 1080))
        self.assertTrue(lc.outside(box(-2, 10, 50, 50), 1920, 1080))
        self.assertTrue(lc.outside(box(1900, 10, 30, 50), 1920, 1080))
        self.assertTrue(lc.outside(box(10, 1070, 30, 20), 1920, 1080))
        self.assertFalse(lc.outside(box(-0.3, 0, 10, 10), 1920, 1080))  # rounding is not a problem

    def test_intersection(self):
        self.assertEqual(lc.intersection(box(0, 0, 100, 100), box(50, 60, 100, 100)), box(50, 60, 50, 40))
        self.assertIsNone(lc.intersection(box(0, 0, 100, 100), box(100, 0, 100, 100)))  # touching
        self.assertIsNone(lc.intersection(box(0, 0, 100, 100), box(0, 200, 100, 100)))

    def test_overflow(self):
        fits = {"sw": 100, "cw": 100, "sh": 31, "ch": 30}
        self.assertEqual(lc.overflows(fits), {})
        wide = {"sw": 140, "cw": 100, "sh": 30, "ch": 30}
        self.assertEqual(lc.overflows(wide), {"x": 40})
        tall = {"sw": 100, "cw": 100, "sh": 36, "ch": 30}
        self.assertEqual(lc.overflows(tall), {"y": 6})
        self.assertEqual(lc.overflows(None), {})

    def test_overflow_allowance_scales_with_font(self):
        tall = {"sw": 100, "cw": 100, "sh": 36, "ch": 30}
        allow = [{"axis": "y", "max_em": 0.25}]
        self.assertEqual(lc.overflows(tall, 30, allow), {})            # 6 px <= 7.5 px
        self.assertEqual(lc.overflows(tall, 16, allow), {"y": 6})      # 6 px > 4 px + 1
        self.assertEqual(lc.overflows({"sw": 140, "cw": 100, "sh": 30, "ch": 30}, 30, allow), {"x": 40})

    def test_small(self):
        self.assertTrue(lc.too_small(17.5, 18))
        self.assertFalse(lc.too_small(18, 18))
        self.assertFalse(lc.too_small(None, 18))

    def test_escapes(self):
        outer = box(100, 100, 200, 100)
        self.assertEqual(lc.escapes(box(110, 110, 50, 50), outer), {})
        self.assertEqual(lc.escapes(box(280, 110, 50, 50), outer), {"right": 30})
        self.assertEqual(lc.escapes(box(90, 180, 50, 30), outer), {"left": 10, "bottom": 10})
        self.assertEqual(lc.escapes(box(100.3, 100, 200, 100), outer), {})  # rounding

    def test_escape_room(self):
        allow = [{"cut": "n08b", "box": "n08b.fig.*.cap", "max_px": 14}]
        self.assertEqual(lc.escape_room("n08b", "n08b.fig.left.cap", allow), 14)
        self.assertEqual(lc.escape_room("n08b", "n08b.bars.large.value", allow), 0)
        self.assertEqual(lc.escape_room("n05", "n08b.fig.left.cap", allow), 0)

    def test_overlap_allowed(self):
        allow = [{"cut": "n18", "a": "light", "b": "vehicle"}, {"cut": "*", "a": "tile:*", "b": "hero"}]
        self.assertTrue(lc.overlap_allowed("n18", "vehicle", "light", allow))
        self.assertFalse(lc.overlap_allowed("n05", "light", "vehicle", allow))
        self.assertTrue(lc.overlap_allowed("n05", "hero", "tile:dlc", allow))

    def _layout(self):
        return {"stage": {"w": 1920, "h": 1080}, "subtitle_band": {"y": 910, "h": 170},
                "min_text_px": 18,
                "check": {"allow_overlap": [{"cut": "x", "a": "p", "b": "q"}],
                          "allow_escape": [{"cut": "x", "box": "p.*", "max_px": 5}]}}

    def item(self, name, b, **kw):
        return {"name": name, "label": name or "div", "text": kw.pop("text", ""), "box": b,
                "inside": kw.pop("inside", []), **kw}

    def test_judge_lists_each_kind(self):
        items = [
            self.item("a", box(100, 100, 200, 100)),
            self.item("b", box(250, 150, 200, 100)),                             # overlaps a
            self.item("p", box(1000, 100, 100, 100)),
            self.item("q", box(1050, 100, 100, 100)),                            # allowed with p
            self.item("in", box(120, 120, 20, 20), inside=["a"]),                # inside a: not an overlap
            self.item("out", box(280, 105, 40, 20), inside=["a"]),               # sticks out of a (right +20)
            self.item("p.k", box(995, 150, 10, 10), inside=["p"]),               # 5 px out of p: allowed
            self.item(None, box(10, 950, 100, 30), text="字幕の帯", font=30),
            self.item(None, box(10, 950, 100, 30), text="guide", font=30, band_exempt=True),
            self.item(None, box(1900, 10, 100, 30), text="edge", font=30),
            self.item(None, box(10, 10, 50, 30), text="long", font=30,
                      scroll={"sw": 90, "cw": 50, "sh": 30, "ch": 30}),
            self.item(None, box(10, 300, 50, 12), text="小さい", font=12),
        ]
        probs = lc.judge(items, self._layout(), "x")
        kinds = sorted((p["kind"], p["element"]) for p in probs)
        self.assertEqual(kinds, [("band", "div"), ("escape", "out"), ("outside", "div"), ("overflow", "div"),
                                 ("overlap", "a × b"), ("small", "div")])
        overlap = next(p for p in probs if p["kind"] == "overlap")
        self.assertEqual(overlap["box"], box(250, 150, 50, 50))

    def test_judge_clean(self):
        items = [self.item("a", box(0, 0, 100, 100)), self.item("b", box(200, 0, 100, 100))]
        self.assertEqual(lc.judge(items, self._layout(), "x"), [])

    def test_markdown_lists_problems(self):
        res = {"problems": 1, "screens": [{"cut": "n05", "errors": [], "frames": [
            {"t": 2.0, "items": 3, "problems": [{"kind": "band", "element": "tag", "text": "a|b",
                                                 "box": box(0, 900, 10, 20), "detail": "d"}]}]}]}
        md = lc.markdown(res)
        self.assertIn("| n05 | 2 | band | `tag` | a\\|b |", md)


FIXTURE = """<!doctype html><html><head><meta charset="utf-8"><style>
html, body { margin: 0; overflow: hidden; } .f { position: absolute; font: 30px/2 sans-serif; margin: 0; }
</style></head><body>
<div class="f" data-box="a" style="left:100px;top:100px;width:200px;height:100px;background:#000;color:#fff">a</div>
<div class="f" data-box="b" style="left:250px;top:150px;width:200px;height:100px;background:#888"></div>
<div class="f" data-box="empty" style="left:250px;top:150px;width:200px;height:100px"></div>
<div class="f" style="left:100px;top:950px">字幕の帯</div>
<div class="f guide-band" style="left:300px;top:950px">guide</div>
<div class="f" style="left:100px;top:960px;opacity:0.02">ghost</div>
<div class="f" style="left:100px;top:980px;visibility:hidden">hidden</div>
<div class="f" style="left:1850px;top:400px;white-space:nowrap">edge text</div>
<div class="f" style="left:600px;top:400px;width:50px;white-space:nowrap">overflowing</div>
<div class="f" style="left:600px;top:600px;font-size:12px">tiny</div>
<div class="f" style="left:900px;top:600px;width:60px;height:60px;overflow:hidden">
  <span style="position:absolute;left:0;top:200px">clipped</span></div>
<script>window.promo = { ready: async () => {}, seek: async (t) => t, duration: 1 };</script>
</body></html>"""


@unittest.skipUnless(HAS_PLAYWRIGHT, "needs playwright (the video Python, not the HIRAKU venv)")
class InBrowser(unittest.TestCase):
    def test_fixture_finds_each_kind(self):
        layout = copy.deepcopy(lc.load_layout())
        with tempfile.TemporaryDirectory() as d:
            page = Path(d) / "fixture.html"
            page.write_text(FIXTURE, encoding="utf-8")
            res = lc.check_screen(page, Path(d), layout, [0])
        probs = res["frames"][0]["problems"]
        found = sorted((p["kind"], p["text"] or p["element"]) for p in probs)
        self.assertEqual(found, [
            ("band", "字幕の帯"),
            ("outside", "edge text"),
            ("overflow", "overflowing"),
            ("overlap", "a × b"),
            ("small", "tiny"),
        ])

    def test_n05_tiles_sit_on_grid_cells(self):
        from promo import export

        cap = export.Capture(lc.SCREENS / "n05.html", scale=1.0)
        try:
            cap.page.evaluate("t => window.__export.seek(t)", 4.5)
            got = cap.page.evaluate("""() => {
              const L = window.promoLayout;
              const cells = L.gridCells(L.cutRules.grid);
              const tiles = [...document.querySelectorAll('.tile')].map((el) => {
                const r = el.getBoundingClientRect();
                return { x: r.left, y: r.top, w: r.width, h: r.height };
              });
              const keys = [...document.querySelectorAll('.tile')].map((el) => el.dataset.box.split('.')[2]);
              return { cells, tiles, keys };
            }""")
        finally:
            cap.close()
        rules = lc.load_layout()
        grid = rules["n05"]["grid"]
        cells = got["cells"]
        self.assertEqual(len(cells), grid["cols"] * grid["rows"])
        self.assertEqual(len(got["tiles"]), len(cells))
        for cell, tile in zip(cells, got["tiles"]):
            for k in "xywh":
                self.assertAlmostEqual(cell[k], tile[k], places=3)
        for c in cells:
            self.assertEqual(c["x"], grid["x"] + c["col"] * (grid["tile_w"] + grid["gap_x"]))
            self.assertEqual(c["y"], grid["y"] + c["row"] * (grid["tile_h"] + grid["gap_y"]))
        self.assertEqual(cells[-1]["x"] + cells[-1]["w"], rules["stage"]["w"] - rules["safe"]["x"])
        self.assertEqual(got["keys"], rules["n05"]["tests"])
        for i, a in enumerate(cells):
            self.assertFalse(lc.in_band(a, rules["subtitle_band"]["y"]))
            for b in cells[i + 1:]:
                self.assertIsNone(lc.intersection(a, b))

    def test_n08b_bars_to_scale_and_inside(self):
        from promo import export

        cap = export.Capture(lc.SCREENS / "n08b.html", scale=1.0)
        try:
            cap.page.evaluate("t => window.__export.seek(t)", 9.8)
            got = cap.page.evaluate("""() => {
              const r = (s) => { const b = document.querySelector(`[data-box="${s}"]`).getBoundingClientRect();
                                 return { x: b.left, w: b.width, right: b.right, h: b.height }; };
              return { small: r("n08b.bars.small.bar"), large: r("n08b.bars.large.bar"),
                       value: r("n08b.bars.large.value"), bars: r("n08b.bars"),
                       times: window.promoLayout.times("n08b") };
            }""")
        finally:
            cap.close()
        rules = lc.load_layout()["n08b"]
        k = rules["bars"]["px_per_pct"]
        self.assertAlmostEqual(got["small"]["w"], 16.0 * k, places=2)
        self.assertAlmostEqual(got["large"]["w"], 67.9 * k, places=2)
        self.assertLessEqual(got["value"]["right"], got["bars"]["right"])
        self.assertLess(got["value"]["h"], 1.5 * rules["bars"]["value_size_px"])  # one line
        self.assertEqual(got["times"]["t-right"], 4.8)

    def test_cuts_have_no_problems(self):
        layout = lc.load_layout()
        with tempfile.TemporaryDirectory() as d:
            for cut in lc.CUTS:
                res = lc.check_screen(lc.SCREENS / f"{cut}.html", Path(d), layout, None)
                probs = [p for f in res["frames"] for p in f["problems"]]
                self.assertEqual(probs, [], f"{cut}: {json.dumps(probs, ensure_ascii=False)}")
                self.assertEqual(res["errors"], [])


if __name__ == "__main__":
    unittest.main()
