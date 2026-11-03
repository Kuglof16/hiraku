"""The N04 and N15 cut pages (cut.html?cut=n04 / n15) in a headless browser.

Skipped unless HIRAKU_WEB_BROWSER=1 and Playwright is installed (as tests.web.test_browser).
The run is the A6 fixture (tests.web.domains_fixture: seven domains and assembly over two
versions, with judgement.updated); with HIRAKU_WEB_DEV_RUNS the newest run there is opened
too. The layout values are made up here in the keys the pages read (layout.json n04 / n15).
"""

import copy
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import make_server
from tests.web.domains_fixture import V1_RED, domain_events
from tests.web.test_browser import ENABLED, sync_playwright

TESTS = ["crash", "nvh", "strength", "thermal", "handling", "power", "aero", "assembly"]
DOMAIN = {"power": "powertrain"}


def grid_section(duration_s):
    return {
        "duration_s": duration_s,
        "grid": {"x": 108, "y": 100, "cols": 4, "rows": 2, "tile_w": 408, "tile_h": 382, "gap_x": 24, "gap_y": 24},
        "tests": list(TESTS),
        "tile": {"border_px": 2, "radius_px": 10, "label": {"x": 16, "y": 12, "size_px": 26}, "rate": {"size_px": 18}, "mark": {"size_px": 34}},
        "times": {"tiles_s": 0.1, "tile_in_s": 0.4, "tile_stagger_s": 0.06},
    }


def grid_layout():
    """Layout rules in the shape the grid cuts read; the lengths are N04 4.0 s and N15 5.1 s."""
    return {
        "stage": {"w": 1920, "h": 1080},
        "subtitle_band": {"y": 910, "h": 170},
        "min_text_px": 18,
        "colors": {"night": "#131311", "tile_bg": "#f7f4ec", "tile_border": "#3b3934"},
        "n04": grid_section(4.0),
        "n15": grid_section(5.1),
    }


def all_green_events():
    """The fixture with every judgement of version 2 green and no interference left in it."""
    out = copy.deepcopy(domain_events())
    for ev in out:
        d = ev.get("data") or {}
        if ev["kind"] == "judgement.updated" and d.get("design_version") == 2:
            d.update({"state": "green", "red_count": 0})
        if ev["kind"] == "analysis.finished" and d.get("design_version") == 2 and "interferences" in d:
            d["interferences"] = []
    return out


def without_judgement(events, version, domain):
    out = [e for e in events if not (e["kind"] == "judgement.updated" and e["data"].get("design_version") == version and e["data"].get("domain") == domain)]
    for i, ev in enumerate(out):
        ev["seq"] = i
    return out


# What the page shows, read in the page: each tile's box and visibility, its mark, the
# mark the run's judgements call for, the smallest text in it.
READ = """async (t) => {
  await window.hiraku.seek(t);
  const cut = window.hiraku.cut;
  const box = (el) => { const r = el.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; };
  const tiles = [...document.querySelectorAll(".grid-tile")];
  const textPx = (el) => {
    const sizes = [];
    for (const e of el.querySelectorAll("*")) {
      const own = [...e.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
      if (own) sizes.push(parseFloat(getComputedStyle(e).fontSize));
    }
    return sizes.length ? Math.min(...sizes) : null;
  };
  return {
    duration: window.hiraku.player.duration,
    version: document.documentElement.dataset.version,
    err: document.querySelector(".err")?.textContent ?? null,
    tiles: tiles.map((el) => {
      const mark = el.querySelector(".mark");
      const state = cut.s.judgements[`${cut.version}|${el.dataset.domain}`]?.state ?? null;
      return { key: el.dataset.box, box: box(el), shown: getComputedStyle(el).visibility, outside: el.dataset.outside ?? null,
               mark: mark ? mark.textContent : null, markInside: mark ? (() => { const a = mark.getBoundingClientRect(), b = el.getBoundingClientRect(); return a.left >= b.left && a.right <= b.right && a.top >= b.top; })() : null,
               want: state === "green" ? "✓" : state === "red" ? "✕" : null, minText: textPx(el),
               text: el.textContent, tags: el.querySelectorAll(".tag").length, canvas: !!el.querySelector("canvas") };
    }),
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

    def check_grid(self, got, layout, name):
        spec = layout[name]
        g = spec["grid"]
        self.assertIsNone(got["err"])
        self.assertEqual([t["key"] for t in got["tiles"]], [f"{name}.tile.{k}" for k in spec["tests"]])
        for i, t in enumerate(got["tiles"]):
            col, row = i % g["cols"], i // g["cols"]
            self.assertEqual(t["box"], [g["x"] + col * (g["tile_w"] + g["gap_x"]), g["y"] + row * (g["tile_h"] + g["gap_y"]), g["tile_w"], g["tile_h"]], t["key"])
            self.assertLessEqual(t["box"][1] + t["box"][3], layout["subtitle_band"]["y"], t["key"])
            self.assertIsNone(t["outside"], t["key"])
            self.assertEqual(t["shown"], "visible", t["key"])
            self.assertEqual(t["mark"], t["want"], t["key"])  # the mark is the run's judgement, and none without one
            if t["mark"]:
                self.assertTrue(t["markInside"], t["key"])
            self.assertGreaterEqual(t["minText"], layout["min_text_px"], t["key"])

    def test_n04_on_a_version_on_the_way(self):
        """?version=1: the eight tiles where the grid puts them, out of the subtitle band, hidden
        before tiles_s; ✕ on the domains judged red in version 1, ✓ on the green ones, none on a
        domain without a judgement (nvh's taken out here)."""
        layout = grid_layout()
        events = without_judgement(domain_events(), 1, "nvh")
        base = self.serve(layout, events={"20261102-120000-grid-v1": events})
        page, errors = self.open(f"{base}/cut.html?cut=n04&run=20261102-120000-grid-v1&version=1&t=0")
        early = page.evaluate(READ, 0.05)
        late = page.evaluate(READ, 3.9)
        self.assertEqual(errors, [])
        self.assertEqual(late["duration"], 4.0)
        self.assertEqual(late["version"], "1")
        self.assertTrue(all(t["shown"] == "hidden" for t in early["tiles"]))
        self.check_grid(late, layout, "n04")
        marks = {t["key"].split(".")[-1]: t["mark"] for t in late["tiles"]}
        for key in TESTS:
            d = DOMAIN.get(key, key)
            want = None if d == "nvh" else "✕" if d in V1_RED else "✓"
            self.assertEqual(marks[key], want, key)
        tiles = {t["key"].split(".")[-1]: t for t in late["tiles"]}
        self.assertIn("干渉 1 件", tiles["assembly"]["text"])  # the fixture's battery × cross1
        self.assertIn("23.4 Hz", tiles["nvh"]["text"])  # the mode's recorded frequency
        self.assertGreater(tiles["thermal"]["tags"], 0)  # part temperatures as text
        page.close()

    def test_n15_stops_all_green_on_the_last_version(self):
        """No ?version=: the run's last version; every tile ✓ because every judgement of it is green,
        and the assembly tile says there is no interference."""
        layout = grid_layout()
        base = self.serve(layout, events={"20261102-120000-grid-green": all_green_events()})
        page, errors = self.open(f"{base}/cut.html?cut=n15&run=20261102-120000-grid-green&t=0")
        got = page.evaluate(READ, 5.0)
        self.assertEqual(errors, [])
        self.assertEqual(got["duration"], 5.1)
        self.assertEqual(got["version"], "2")
        self.check_grid(got, layout, "n15")
        self.assertEqual([t["mark"] for t in got["tiles"]], ["✓"] * 8)
        self.assertIn("✓ 干渉なし", got["tiles"][TESTS.index("assembly")]["text"])
        page.close()

    def test_missing_layout_keys_are_named(self):
        layout = grid_layout()
        del layout["n04"]["tile"]["mark"]
        del layout["n04"]["grid"]["gap_y"]
        del layout["n04"]["times"]
        del layout["min_text_px"]
        base = self.serve(layout, events={"20261102-120000-grid-v1": domain_events()})
        page, errors = self.open(f"{base}/cut.html?cut=n04&run=20261102-120000-grid-v1&t=1")
        err = page.evaluate("document.querySelector('.err')?.textContent ?? ''")
        self.assertIn("layout.json の n04 に無い: grid.gap_y, tile.mark.size_px, times.tiles_s, times.tile_in_s, times.tile_stagger_s", err)
        self.assertIn("layout.json に無い: min_text_px", err)
        self.assertEqual(page.evaluate("document.querySelectorAll('.grid-tile').length"), 0)
        page.close()
        # a section that is not there at all names every key
        base = self.serve({k: v for k, v in grid_layout().items() if k != "n15"}, events={"20261102-120000-grid-v1": domain_events()})
        page, errors = self.open(f"{base}/cut.html?cut=n15&run=20261102-120000-grid-v1&t=1")
        self.assertIn("layout.json の n15 に無い: duration_s, grid.x", page.evaluate("document.querySelector('.err')?.textContent ?? ''"))
        page.close()

    def test_on_the_real_tools_records(self):
        """With HIRAKU_WEB_DEV_RUNS: the newest run there, its last version; the same layout checks
        and marks from its own judgements."""
        given = os.environ.get("HIRAKU_WEB_DEV_RUNS")
        if not given:
            self.skipTest("needs HIRAKU_WEB_DEV_RUNS (a folder of runs made by the real tools)")
        runs = Path(given)
        run = sorted(p.name for p in runs.iterdir() if p.is_dir())[-1]
        layout = grid_layout()
        base = self.serve(layout, runs=runs)
        for name in ("n04", "n15"):
            page, errors = self.open(f"{base}/cut.html?cut={name}&run={run}&t=0")
            got = page.evaluate(READ, layout[name]["duration_s"] - 0.1)
            self.assertEqual(errors, [], name)
            self.check_grid(got, layout, name)
            page.close()


if __name__ == "__main__":
    unittest.main()
