"""The N04 and N15 cut pages (cut.html?cut=n04 / n15) in a headless browser.

Skipped unless HIRAKU_WEB_BROWSER=1 and Playwright is installed (as tests.web.test_browser).
The run is the A6 fixture (tests.web.domains_fixture: seven domains and assembly over two
versions, with judgement.updated), here also with a third version; with HIRAKU_WEB_DEV_RUNS
the newest run there is opened too. The layout values are the promo department's for
n04 / n15 / n16 (layout.json), with n01's colours and overlay.
"""

import copy
import json
import math
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import make_server
from tests.web.domains_fixture import V1_RED, V2_RED, domain_events
from tests.web.test_browser import ENABLED, sync_playwright

TESTS = ["handling", "powertrain", "aero", "crash", "strength", "nvh", "thermal", "assembly"]
COLORS = {"bg": "#f4f1ea", "panel": "#fbf9f4", "rule": "#c9c3b6"}
OVERLAY = {"color": "#c8402a", "opacity": 0.22}
CHECK_TIMES = {"n04": [0.5, 1.5, 3.5], "n15": [0.5, 2.5, 4.8]}
BAND_Y = 910


def grid_layout():
    header = {"x": 96, "y": 96, "w": 1728, "h": 48, "size_px": 30}
    grid = {"x": 96, "y": 160, "cols": 4, "rows": 2, "tile_w": 414, "tile_h": 351, "gap_x": 24, "gap_y": 24}
    tile = {"border_px": 2, "radius_px": 12, "label": {"x": 16, "y": 12, "size_px": 26}, "mark": {"size_px": 44, "right": 16, "top": 10},
            "pet": {"w": 96, "h": 96, "right": 12, "bottom": 12}, "rate": {"size_px": 18}}
    common = {"header": header, "grid": grid, "tests": list(TESTS), "tile": tile}
    return {
        "stage": {"w": 1920, "h": 1080},
        "subtitle_band": {"y": BAND_Y, "h": 170},
        "min_text_px": 18,
        "n01": {"colors": dict(COLORS), "overlay": dict(OVERLAY)},
        "n04": {"duration_s": 4.0, **copy.deepcopy(common), "times": {"lead_s": 0.3, "crash_s": 0.5, "nvh_s": 1.2, "assembly_s": 2.0},
                "colors": dict(COLORS), "overlay": dict(OVERLAY)},
        "n15": {"duration_s": 5.1, **copy.deepcopy(common), "times": {"lead_s": 0.3, "rounds_from_s": 0.4, "all_green_s": 4.0}},
        "n16": {"duration_s": 6.3, "view": {"x": 96, "y": 96, "w": 1200, "h": 790}, "seek": {"x": 120, "y": 814, "w": 1152, "h": 48},
                "steps": {"x": 1328, "y": 96, "w": 496, "h": 790, "title_px": 30, "text_px": 22, "row_gap": 12},
                "times": {"lead_s": 0.3, "steps_from_s": 0.5, "steps_to_s": 4.2, "scrub_s": 4.4, "scrub_to_s": 5.8}, "colors": dict(COLORS)},
    }


def feature_rects(g, n):
    """As cut_n03.js featureRects: the large box [x, y, w, h] over the first ceil(cols / 2)
    columns and all rows, and n - 1 small ones filling the columns left, row by row."""
    W = g["cols"] * g["tile_w"] + (g["cols"] - 1) * g["gap_x"]
    H = g["rows"] * g["tile_h"] + (g["rows"] - 1) * g["gap_y"]
    bc = min(g["cols"] - 1, -(-g["cols"] // 2))
    big = [g["x"], g["y"], bc * g["tile_w"] + (bc - 1) * g["gap_x"], H]
    cols = max(1, g["cols"] - bc)
    rows = max(1, -(-(n - 1) // cols))
    left = g["x"] + big[2] + g["gap_x"]
    w = (W - big[2] - g["gap_x"] - (cols - 1) * g["gap_x"]) / cols
    h = (H - (rows - 1) * g["gap_y"]) / rows
    small = [[math.floor(left + (i % cols) * (w + g["gap_x"]) + 0.5), math.floor(g["y"] + (i // cols) * (h + g["gap_y"]) + 0.5), int(w), int(h)] for i in range(n - 1)]
    return big, small


def renumber(events):
    for i, ev in enumerate(events):
        ev["seq"] = i
        if i >= 2:
            ev["t"] = 1.0 + i * 0.5
    return events


def three_versions():
    """The fixture with a version 3 made from version 2: every judgement green, no interference.
    Shape example: version 3's records are version 2's, copied."""
    src = domain_events()
    tail = src[-2:]  # screen.focus, run.finished
    out = copy.deepcopy(src[:-2])
    more = []
    for ev in src[:-2]:
        d = ev.get("data") or {}
        if d.get("design_version") != 2 and ev.get("design_version") != 2:
            continue
        ev = copy.deepcopy(ev)
        d = ev["data"]
        ev["design_version"] = 3
        d["design_version"] = 3
        if ev["kind"] == "design.created":
            d.update({"parent_version": 2, "change_reason": "shape example"})
        elif ev["kind"] == "analysis.finished":
            d["analysis_id"] += "-v3"
            if "interferences" in d:
                d["interferences"] = []
        elif ev["kind"] == "judgement.updated":
            d.update({"state": "green", "red_count": 0})
        more.append(ev)
    return renumber(out + more + copy.deepcopy(tail))


def without_judgement(events, version, domain):
    out = [e for e in events if not (e["kind"] == "judgement.updated" and e["data"].get("design_version") == version and e["data"].get("domain") == domain)]
    return renumber(out)


# What the page shows at stage time t, read in the page.
READ = """async (t) => {
  await window.hiraku.seek(t);
  const cut = window.hiraku.cut;
  const v = Number(document.documentElement.dataset.version);
  const rect = (el) => { const r = el.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; };
  const shown = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility === "visible"; };
  const textPx = (el) => {
    const sizes = [];
    for (const e of el.querySelectorAll("*")) {
      if (!shown(e)) continue;
      if ([...e.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) sizes.push(parseFloat(getComputedStyle(e).fontSize));
    }
    return sizes.length ? Math.min(...sizes) : null;
  };
  const header = document.querySelector(".grid-header");
  const asm = document.querySelector(".n04-assembly");
  return {
    duration: window.hiraku.player.duration, version: v, err: document.querySelector(".err")?.textContent ?? null,
    header: { shown: shown(header), text: header.textContent, rect: rect(header) },
    assembly: asm ? { shown: shown(asm), rect: rect(asm), box: asm.dataset.box } : null,
    boxes: [...document.querySelectorAll("[data-box]")].filter(shown).map((el) => ({ box: el.dataset.box, rect: rect(el) })),
    tiles: [...document.querySelectorAll(".grid-tile")].map((el) => {
      const layer = [...el.querySelectorAll(".layer")].find((l) => l.style.display !== "none");
      const mark = layer?.querySelector(".mark");
      const label = layer?.querySelector("b");
      const state = cut.s.judgements[`${v}|${el.dataset.domain}`]?.state ?? null;
      return { key: el.dataset.box, rect: rect(el), shown: shown(el), hot: el.dataset.hot === "1", outside: el.dataset.outside ?? null,
               mark: mark ? mark.textContent : null, want: state === "green" ? "✓" : state === "red" ? "✕" : null,
               label: label ? rect(label) : null, markRect: mark ? rect(mark) : null, minText: textPx(el), text: layer?.textContent ?? "",
               tags: layer ? layer.querySelectorAll(".tag").length : 0, pet: !!el.querySelector('[data-box$=".pet"]'),
               hits: layer?.dataset.hits ?? null, featured: el.dataset.featured === "1" };
    }),
    charts: document.querySelectorAll(".grid-tile svg.chart").length,
  };
}"""


@unittest.skipUnless(ENABLED, "set HIRAKU_WEB_BROWSER=1 with Playwright installed")
class GridCutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.servers = []

    def tearDown(self):
        for s in self.servers:
            s.shutdown()
            s.server_close()
        shutil.rmtree(self.tmp)

    def serve(self, layout, runs=None, events=None):
        lp = self.tmp / f"layout-{len(self.servers)}.json"
        lp.write_text(json.dumps(layout), encoding="utf-8")
        if runs is None:
            runs = self.tmp / f"runs-{len(self.servers)}"
            for run_id, evs in (events or {}).items():
                (runs / run_id).mkdir(parents=True)
                (runs / run_id / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in evs), encoding="utf-8")
        server = make_server("127.0.0.1", 0, runs, quiet=True, saved_dir=None, launcher=False, layout_file=lp)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def open(self, url):
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url)
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
        page.evaluate("window.hiraku.ready")
        return page, errors

    def inside(self, a, b, what):
        """Rectangle a [l, t, r, b] lies within b (half a pixel of rounding)."""
        self.assertTrue(a[0] >= b[0] - 0.5 and a[1] >= b[1] - 0.5 and a[2] <= b[2] + 0.5 and a[3] <= b[3] + 0.5, f"{what}: {a} not inside {b}")

    def check_boxes(self, got, name):
        """Every box shown stays on the stage above the subtitle band, and a tile's parts in their tile."""
        tiles = {b["box"]: b["rect"] for b in got["boxes"] if b["box"].count(".") == 2 and ".tile." in b["box"]}
        for b in got["boxes"]:
            self.inside(b["rect"], [0, 0, 1920, BAND_Y], b["box"])
            owner = ".".join(b["box"].split(".")[:3])
            if b["box"].startswith(f"{name}.tile.") and owner != b["box"]:
                self.assertIn(owner, tiles, b["box"])
                self.inside(b["rect"], tiles[owner], b["box"])
        if got["header"]["shown"]:
            self.inside(got["header"]["rect"], [0, 0, 1920, BAND_Y], "header")
        for t in got["tiles"]:
            if t["shown"] and t["label"] and t["markRect"]:
                self.assertLessEqual(t["label"][2], t["markRect"][0], f"{t['key']}: the label runs into the mark")

    def check_grid(self, got, layout, name):
        spec = layout[name]
        g = spec["grid"]
        self.assertIsNone(got["err"])
        self.assertEqual([t["key"] for t in got["tiles"]], [f"{name}.tile.{k}" for k in spec["tests"]])
        # one tile large, the others as a small list in the grid's box (featureRects)
        featured = [t for t in got["tiles"] if t["featured"]]
        self.assertEqual(len(featured), 1)
        big, small = feature_rects(g, len(spec["tests"]))
        rest = iter(small)
        for t in got["tiles"]:
            r = big if t["featured"] else next(rest)
            self.assertEqual([round(v) for v in t["rect"]], [r[0], r[1], r[0] + r[2], r[1] + r[3]], t["key"])
            self.assertLessEqual(t["rect"][3], BAND_Y, t["key"])
            self.assertIsNone(t["outside"], t["key"])
            self.assertTrue(t["shown"], t["key"])
            self.assertTrue(t["pet"], t["key"])
            self.assertEqual(t["mark"], t["want"], t["key"])  # the mark is the run's judgement, and none without one
            if t["minText"] is not None:  # a tile with a record and no judgement holds no text
                self.assertGreaterEqual(t["minText"], layout["min_text_px"], t["key"])
            # a picture and its mark only: no name, no figure (a tile without its record says so)
            if "記録が無い" not in t["text"]:
                self.assertIn(t["text"], ("", "✓", "✕"), t["key"])
            self.assertIsNone(t["label"], t["key"])
        self.assertTrue(got["header"]["shown"])
        # one line with one number: N04 the tiles still red, N15 the rounds
        if name == "n15":
            self.assertEqual(got["header"]["text"], f"繰り返し {got['version'] - 1} 回")
        else:
            reds = sum(1 for t in got["tiles"] if t["want"] == "✕")
            self.assertEqual(got["header"]["text"], f"まだ赤が {reds} 枚" if reds else "全部が緑")

    def test_n04_on_a_version_on_the_way(self):
        """?version=1: the grid, the header, marks from version 1's judgements (none on nvh, its
        judgement taken out); crash lit from crash_s while red; from assembly_s the grid gives way
        to the empty assembly box in n16.view's place."""
        layout = grid_layout()
        events = without_judgement(domain_events(), 1, "nvh")
        base = self.serve(layout, events={"20261102-120000-grid-v1": events})
        page, errors = self.open(f"{base}/cut.html?cut=n04&run=20261102-120000-grid-v1&version=1&t=0")
        before = page.evaluate(READ, 0.4)
        shots = {t: page.evaluate(READ, t) for t in CHECK_TIMES["n04"]}
        self.assertEqual(errors, [])
        self.assertEqual(before["duration"], 4.0)
        for t, got in shots.items():
            self.check_boxes(got, "n04")
        early, mid, late = (shots[t] for t in CHECK_TIMES["n04"])
        for got in (before, early, mid):
            self.assertEqual(got["version"], 1)
            self.check_grid(got, layout, "n04")
            self.assertFalse(got["assembly"]["shown"])
        marks = {t["key"].split(".")[-1]: t["mark"] for t in mid["tiles"]}
        for key in TESTS:
            self.assertEqual(marks[key], None if key == "nvh" else "✕" if key in V1_RED else "✓", key)
        # the large tile is a red one (the message: still red), with its record drawn
        big = next(t for t in mid["tiles"] if t["featured"])
        self.assertIn(big["key"].split(".")[-1], V1_RED)
        self.assertEqual(big["mark"], "✕")
        self.assertNotIn("記録が無い", big["text"])
        hot = lambda got: {t["key"].split(".")[-1] for t in got["tiles"] if t["hot"]}  # noqa: E731
        self.assertEqual(hot(before), set())
        self.assertEqual(hot(early), {"crash"})  # nvh is red in V1_RED but has no judgement here
        tiles = {t["key"].split(".")[-1]: t for t in mid["tiles"]}
        # no words or figures on the tiles: the interference, the mode's frequency and the
        # part temperatures are in the pictures only (the story: a picture and a mark)
        self.assertEqual(tiles["assembly"]["hits"], "1")  # the fixture's battery × cross1, boxed in red
        self.assertNotIn("Hz", tiles["nvh"]["text"])
        self.assertEqual(tiles["thermal"]["tags"], 0)
        self.assertEqual(mid["charts"], 0)
        # from assembly_s: no tile, no header, the assembly box where n16.view is
        self.assertTrue(all(not t["shown"] for t in late["tiles"]))
        self.assertFalse(late["header"]["shown"])
        V = layout["n16"]["view"]
        self.assertEqual(late["assembly"]["box"], "n04.assembly")
        self.assertTrue(late["assembly"]["shown"])
        self.assertEqual([round(v) for v in late["assembly"]["rect"]], [V["x"], V["y"], V["x"] + V["w"], V["y"] + V["h"]])
        page.close()

    def test_n04_lights_nvh_when_red(self):
        layout = grid_layout()
        base = self.serve(layout, events={"20261102-120000-grid-v1": domain_events()})
        page, errors = self.open(f"{base}/cut.html?cut=n04&run=20261102-120000-grid-v1&version=1&t=0")
        lit = {t: {x["key"].split(".")[-1] for x in page.evaluate(READ, t)["tiles"] if x["hot"]} for t in (1.1, 1.3)}
        self.assertEqual(errors, [])
        self.assertEqual(lit, {1.1: {"crash"}, 1.3: {"crash", "nvh"}})
        page.close()

    def test_n15_steps_through_the_versions_and_stops_on_the_last(self):
        """Versions 1, 2 between rounds_from_s and all_green_s, version 3 (all green here) from
        all_green_s to the end; the marks and the header follow the version shown."""
        layout = grid_layout()
        base = self.serve(layout, events={"20261102-120000-grid-three": three_versions()})
        page, errors = self.open(f"{base}/cut.html?cut=n15&run=20261102-120000-grid-three&t=0")
        shots = {t: page.evaluate(READ, t) for t in [0.2, *CHECK_TIMES["n15"], 5.1]}
        self.assertEqual(errors, [])
        self.assertEqual(shots[0.2]["duration"], 5.1)
        # rounds_from 0.4, all_green 4.0: versions 1 and 2 share 3.6 s, then version 3
        self.assertEqual({t: g["version"] for t, g in shots.items()}, {0.2: 1, 0.5: 1, 2.5: 2, 4.8: 3, 5.1: 3})
        for t, got in shots.items():
            self.check_grid(got, layout, "n15")
            self.check_boxes(got, "n15")
            self.assertTrue(all(not x["hot"] for x in got["tiles"]))
        marks = lambda got: {t["key"].split(".")[-1]: t["mark"] for t in got["tiles"]}  # noqa: E731
        self.assertEqual(marks(shots[0.5]), {k: "✕" if k in V1_RED else "✓" for k in TESTS})
        self.assertEqual(marks(shots[2.5]), {k: "✕" if k in V2_RED else "✓" for k in TESTS})
        self.assertEqual(marks(shots[4.8]), {k: "✓" for k in TESTS})
        self.assertEqual(shots[4.8]["tiles"][TESTS.index("assembly")]["hits"], "0")
        self.assertEqual(shots[4.8]["header"]["text"], "繰り返し 2 回")
        # the large tile is one that turned green last (red in version 2, green in 3)
        big = next(t for t in shots[4.8]["tiles"] if t["featured"])
        self.assertIn(big["key"].split(".")[-1], V2_RED)
        page.close()

    def test_n15_last_version_not_green_stays_as_recorded(self):
        layout = grid_layout()
        base = self.serve(layout, events={"20261102-120000-grid-v1": domain_events()})
        page, errors = self.open(f"{base}/cut.html?cut=n15&run=20261102-120000-grid-v1&t=0")
        got = page.evaluate(READ, 4.8)
        self.assertEqual(errors, [])
        self.assertEqual(got["version"], 2)
        self.assertEqual({t["key"].split(".")[-1] for t in got["tiles"] if t["mark"] == "✕"}, V2_RED)
        page.close()

    def test_missing_layout_keys_are_named(self):
        layout = grid_layout()
        del layout["n04"]["tile"]["mark"]["right"]
        del layout["n04"]["tile"]["pet"]
        del layout["n04"]["times"]["crash_s"]
        del layout["n16"]
        del layout["min_text_px"]
        base = self.serve(layout, events={"20261102-120000-grid-v1": domain_events()})
        page, errors = self.open(f"{base}/cut.html?cut=n04&run=20261102-120000-grid-v1&t=1")
        err = page.evaluate("document.querySelector('.err')?.textContent ?? ''")
        self.assertIn("layout.json の n04 に無い: tile.mark.right, tile.pet.w, tile.pet.h, tile.pet.right, tile.pet.bottom, times.crash_s", err)
        self.assertIn("layout.json の n16 に無い: view.x, view.y, view.w, view.h", err)
        self.assertIn("layout.json に無い: min_text_px", err)
        self.assertEqual(page.evaluate("document.querySelectorAll('.grid-tile').length"), 0)
        page.close()
        # N15 takes N04's colours when it has none of its own; with neither, they are named
        layout = grid_layout()
        del layout["n04"]
        del layout["n15"]["times"]["all_green_s"]
        base = self.serve(layout, events={"20261102-120000-grid-v1": domain_events()})
        page, errors = self.open(f"{base}/cut.html?cut=n15&run=20261102-120000-grid-v1&t=1")
        self.assertIn("layout.json の n15 に無い: times.all_green_s, colors.bg, colors.panel, colors.rule", page.evaluate("document.querySelector('.err')?.textContent ?? ''"))
        page.close()

    def test_on_the_real_tools_records(self):
        """With HIRAKU_WEB_DEV_RUNS: the newest run there; the same checks at the check times,
        marks from its own judgements."""
        given = os.environ.get("HIRAKU_WEB_DEV_RUNS")
        if not given:
            self.skipTest("needs HIRAKU_WEB_DEV_RUNS (a folder of runs made by the real tools)")
        runs = Path(given)
        run = sorted(p.name for p in runs.iterdir() if p.is_dir())[-1]
        layout = grid_layout()
        base = self.serve(layout, runs=runs)
        for name in ("n04", "n15"):
            page, errors = self.open(f"{base}/cut.html?cut={name}&run={run}&t=0")
            for t in CHECK_TIMES[name]:
                got = page.evaluate(READ, t)
                self.assertEqual(errors, [], name)
                self.check_boxes(got, name)
                if name == "n15" or t < layout["n04"]["times"]["assembly_s"]:
                    self.check_grid(got, layout, name)
            page.close()


if __name__ == "__main__":
    unittest.main()
