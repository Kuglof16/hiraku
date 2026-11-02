import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from promo import layout_build, layout_check as lc

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
        rules = {"design": {"w": 1920, "h": 1080}, "note": "日本語"}
        self.assertEqual(layout_build.parse(layout_build.render(rules)), rules)

    def test_check_times_name_every_cut(self):
        rules = layout_build.load()
        for cut in lc.CUTS:
            self.assertIn(cut, rules["cuts"])
            self.assertTrue(rules["check"]["times"][cut])

    def test_every_allowance_gives_a_reason(self):
        chk = layout_build.load()["check"]
        for rule in chk["band_exempt"] + chk["allow_overlap"] + chk["allow_overflow"]:
            self.assertTrue(rule.get("why"), rule)


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
        mins = {"ja": 20, "latin": 16}
        self.assertTrue(lc.too_small(19.5, "ja", mins))
        self.assertFalse(lc.too_small(20, "ja", mins))
        self.assertFalse(lc.too_small(17, "latin", mins))
        self.assertTrue(lc.too_small(15, "latin", mins))
        self.assertFalse(lc.too_small(None, None, mins))

    def test_overlap_allowed(self):
        allow = [{"cut": "n18", "a": "light", "b": "vehicle"}, {"cut": "*", "a": "tile:*", "b": "hero"}]
        self.assertTrue(lc.overlap_allowed("n18", "vehicle", "light", allow))
        self.assertFalse(lc.overlap_allowed("n05", "light", "vehicle", allow))
        self.assertTrue(lc.overlap_allowed("n05", "hero", "tile:dlc", allow))

    def _layout(self):
        return {"design": {"w": 1920, "h": 1080}, "band": {"top": 910},
                "min_text": {"ja": 20, "latin": 16},
                "check": {"allow_overlap": [{"cut": "x", "a": "p", "b": "q"}]}}

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
            self.item(None, box(10, 950, 100, 30), text="字幕の帯", font=30, script="ja"),
            self.item(None, box(10, 950, 100, 30), text="guide", font=30, script="latin", band_exempt=True),
            self.item(None, box(1900, 10, 100, 30), text="edge", font=30, script="latin"),
            self.item(None, box(10, 10, 50, 30), text="long", font=30, script="latin",
                      scroll={"sw": 90, "cw": 50, "sh": 30, "ch": 30}),
            self.item(None, box(10, 300, 50, 12), text="小さい", font=12, script="ja"),
        ]
        probs = lc.judge(items, self._layout(), "x")
        kinds = sorted((p["kind"], p["element"]) for p in probs)
        self.assertEqual(kinds, [("band", "div"), ("outside", "div"), ("overflow", "div"),
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
<div class="f" data-check="a" style="left:100px;top:100px;width:200px;height:100px;background:#000;color:#fff">a</div>
<div class="f" data-check="b" style="left:250px;top:150px;width:200px;height:100px;background:#888"></div>
<div class="f" data-check="empty" style="left:250px;top:150px;width:200px;height:100px"></div>
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
              return { cells, tiles };
            }""")
        finally:
            cap.close()
        rules = lc.load_layout()
        grid = rules["cuts"]["n05"]["grid"]
        cells = got["cells"]
        self.assertEqual(len(cells), grid["cols"] * grid["rows"])
        self.assertEqual(len(got["tiles"]), len(cells))
        for cell, tile in zip(cells, got["tiles"]):
            for k in "xywh":
                self.assertAlmostEqual(cell[k], tile[k], places=3)
        self.assertEqual((cells[0]["x"], cells[0]["y"]), (grid["origin"]["x"], grid["origin"]["y"]))
        self.assertEqual(cells[-1]["x"] + cells[-1]["w"], rules["design"]["w"] - rules["margin"]["side"])
        for i, a in enumerate(cells):
            self.assertFalse(lc.in_band(a, rules["band"]["top"]))
            for b in cells[i + 1:]:
                self.assertIsNone(lc.intersection(a, b))

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
