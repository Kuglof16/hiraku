"""The N04 and N15 cut pages (cut.html?cut=n04 / n15) in a headless browser.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed (as tests.web.test_browser).
The run is the A6 fixture (tests.web.domains_fixture: seven domains and assembly over two
versions, with judgement.updated), here also with a third version; with HIRAKU_WEB_DEV_RUNS
the newest run there is opened too. The layout values are the promo department's for
n04 / n15 / n16 (layout.json 087fb59), with n01's colours and overlay.
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
            "pet": {"w": 84, "h": 84, "right": 12, "bottom": 12, "feature": {"w": 160, "h": 160}}, "rate": {"size_px": 18}}
    feature = {"x": 96, "y": 160, "w": 852, "h": 726}
    lst = {"x": 972, "y": 160, "w": 852, "h": 726, "cols": 2, "rows": 4, "gap_x": 24, "gap_y": 24}
    common = {"header": header, "grid": grid, "feature": feature, "list": lst, "tests": list(TESTS), "tile": tile}
    return {
        "stage": {"w": 1920, "h": 1080},
        "subtitle_band": {"y": BAND_Y, "h": 170},
        "min_text_px": 18,
        "n00": {"camera": {"fov_deg": 24, "azimuth_deg": 24, "elevation_deg": 12}, "motion": {"pass_dur_s": 0.9, "hop_period_s": 0.9, "idle_shift_s": 1.3}},
        "n01": {"colors": dict(COLORS), "overlay": dict(OVERLAY)},
        "n04": {"duration_s": 4.0, **copy.deepcopy(common), "assembly": {"x": 96, "y": 160, "w": 1728, "h": 726},
                "times": {"lead_s": 0.3, "crash_s": 0.5, "nvh_s": 1.2, "assembly_s": 2.0},
                "colors": dict(COLORS), "overlay": dict(OVERLAY)},
        "n15": {"duration_s": 5.1, **copy.deepcopy(common), "times": {"lead_s": 0.3, "rounds_from_s": 0.4, "all_green_s": 4.0}},
        "n16": {"duration_s": 6.3, "view": {"x": 96, "y": 96, "w": 1400, "h": 790}, "seek": {"x": 120, "y": 814, "w": 1352, "h": 48},
                "steps": {"x": 1520, "y": 96, "w": 304, "h": 790, "title_px": 30, "text_px": 22, "row_gap": 12},
                "times": {"lead_s": 0.3, "steps_from_s": 0.5, "steps_to_s": 4.2, "scrub_s": 4.4, "scrub_to_s": 5.8}, "colors": dict(COLORS)},
    }


def list_rects(L):
    """The slots [x, y, w, h] of a layout list {x, y, w, h, cols, rows, gap_x, gap_y}, row by
    row from the left: (w - (cols - 1) gap_x) / cols x (h - (rows - 1) gap_y) / rows each
    (as cut_n03.js listRects)."""
    w = (L["w"] - (L["cols"] - 1) * L["gap_x"]) / L["cols"]
    h = (L["h"] - (L["rows"] - 1) * L["gap_y"]) / L["rows"]
    return [[math.floor(L["x"] + c * (w + L["gap_x"]) + 0.5), math.floor(L["y"] + r * (h + L["gap_y"]) + 0.5), int(w), int(h)]
            for r in range(L["rows"]) for c in range(L["cols"])]


def box_of(b):
    return [b["x"], b["y"], b["w"], b["h"]]


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


def with_state(events, version, domain, state):
    """The fixture with the domain's judgements of the version set to state (shape example)."""
    out = copy.deepcopy(events)
    for e in out:
        d = e.get("data") or {}
        if e["kind"] == "judgement.updated" and d.get("design_version") == version and d.get("domain") == domain:
            d["state"] = state
            if "red_count" in d:
                d["red_count"] = 0 if state == "green" else max(1, d["red_count"])
    return out


def crash_drawn(events):
    """The fixture with its crash records' channel named as the crash tile charts it
    (deceleration; the fixture calls it decel, so its crash tile has nothing to draw)."""
    out = copy.deepcopy(events)
    for e in out:
        if e["kind"] == "analysis.finished" and e["data"].get("tool") == "crash_shape_example":
            for s in e["data"].get("series", []):
                if s["channel"] == "decel":
                    s["channel"] = "deceleration"
    return out


def without_assembly(events):
    """The fixture without the analyses that carry assembly steps or interferences."""
    out = [e for e in events if not (e["kind"] == "analysis.finished" and ("assembly" in e["data"] or "interferences" in e["data"]))]
    return renumber(out)


def switch_times(events, times):
    """N15: when each tile takes each version after the first (cut_grid.js switchTimes):
    {domain: {version: stage time}}. Round k (version index k) has slot k of the time from
    rounds_from_s to all_green_s shared equally; a tile's time in the slot is as far along
    as its judgement's event time is between the version's earliest and latest judgement."""
    last = {}
    for e in events:
        if e["kind"] == "judgement.updated":
            d = e["data"]
            last[(d["design_version"], d["domain"])] = e["t"]
    versions = sorted({v for v, _ in last})
    span = (times["all_green_s"] - times["rounds_from_s"]) / max(1, len(versions) - 1)
    out = {}
    for k, v in enumerate(versions[1:], start=1):
        ts = [t for (vv, _), t in last.items() if vv == v]
        lo, hi = min(ts), max(ts)
        a = times["rounds_from_s"] + (k - 1) * span
        for (vv, dom), t in last.items():
            if vv == v:
                f = (t - lo) / (hi - lo) if hi > lo else 1.0
                out.setdefault(dom, {})[v] = a + f * span
    return out


GREEN_RGB = "rgb(47, 125, 79)"  # the pages' colour of a pass
RED_RGB = "rgb(200, 64, 42)"  # n04.overlay.color


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
    assembly: asm ? { shown: shown(asm), rect: rect(asm), box: asm.dataset.box, lit: asm.dataset.lit ?? "", border: asm.style.borderColor, version: asm.dataset.version ?? null } : null,
    boxes: [...document.querySelectorAll("[data-box]")].filter(shown).map((el) => ({ box: el.dataset.box, rect: rect(el) })),
    tiles: [...document.querySelectorAll(".grid-tile")].map((el) => {
      const layer = [...el.querySelectorAll(".layer")].find((l) => l.style.display !== "none");
      const mark = layer?.querySelector(".mark");
      const label = layer?.querySelector("b");
      // the tile's own version (N15: each tile takes the next version at its own time)
      const tv = Number(el.dataset.version ?? v);
      const state = cut.s.judgements[`${tv}|${el.dataset.domain}`]?.state ?? null;
      return { key: el.dataset.box, rect: rect(el), shown: shown(el), hot: el.dataset.hot === "1", lit: el.dataset.lit ?? "", border: el.style.borderColor, outside: el.dataset.outside ?? null,
               version: tv, dim: mark ? mark.classList.contains("dim") : null, scale: layer?.dataset.scale ?? null,
               contour: (() => { const t = cut.tiles.find((x) => x.key === el.dataset.domain); const l = t?.layers.find((x) => x.el === layer);
                 const ct = l?.view?.keys?.contour; return ct ? { max: ct.max, limitKnown: ct.limitKnown, parts: ct.parts, values: Object.keys(ct.values ?? {}).length } : null; })(),
               mark: mark ? mark.textContent : null, want: state === "green" ? "✓" : state === "red" ? "✕" : null,
               label: label ? rect(label) : null, markRect: mark ? rect(mark) : null, minText: textPx(el), text: layer?.textContent ?? "",
               tags: layer ? layer.querySelectorAll(".tag").length : 0, pet: !!el.querySelector('[data-box$=".pet"]'),
               hits: layer?.dataset.hits ?? null, featured: el.dataset.featured === "1" };
    }),
    charts: document.querySelectorAll(".grid-tile svg.chart").length,
  };
}"""


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
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
        self.assertIsNone(got["err"])
        self.assertEqual([t["key"] for t in got["tiles"]], [f"{name}.tile.{k}" for k in spec["tests"]])
        # one tile in the feature box, the others in the list's slots in order
        featured = [t for t in got["tiles"] if t["featured"]]
        self.assertEqual(len(featured), 1)
        big, small = box_of(spec["feature"]), list_rects(spec["list"])
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
        # one line with one number: N15 the round in progress, N04 the version and its rounds
        # (no count of red tiles: the voice says what can be checked)
        if name == "n15":
            self.assertEqual(got["header"]["text"], f"繰り返し {got['version'] - 1} 回")
        else:
            self.assertEqual(got["header"]["text"], f"設計の版 {got['version']}・直し {got['version'] - 1} 回")

    def test_n04_on_a_version_on_the_way(self):
        """?version=1: the grid, the header, marks from version 1's judgements (none on nvh, its
        judgement taken out); crash lit from crash_s while red; from assembly_s the grid gives way
        to the assembly box in n04.assembly (the grid's place)."""
        layout = grid_layout()
        events = without_judgement(crash_drawn(domain_events()), 1, "nvh")
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
        # the large tile is the one the voice is naming (crash from crash_s, NVH from nvh_s;
        # before crash_s the first, crash), with its record drawn
        big = {t: next(x["key"] for x in g["tiles"] if x["featured"]) for t, g in ((0.4, before), (0.5, early), (1.5, mid))}
        self.assertEqual(big, {0.4: "n04.tile.crash", 0.5: "n04.tile.crash", 1.5: "n04.tile.nvh"})
        self.assertNotIn("記録が無い", next(x for x in mid["tiles"] if x["featured"])["text"])
        # the ticks and crosses are pale until their tile is lit (crash from crash_s)
        dims = {x["key"].split(".")[-1]: x["dim"] for x in mid["tiles"] if x["mark"]}
        self.assertEqual(dims, {k: k != "crash" for k in TESTS if k != "nvh"})
        hot = lambda got: {t["key"].split(".")[-1] for t in got["tiles"] if t["hot"]}  # noqa: E731
        self.assertEqual(hot(before), set())
        self.assertEqual(hot(early), {"crash"})  # nvh has no judgement here: not lit
        self.assertEqual(hot(mid), {"crash"})
        tiles = {t["key"].split(".")[-1]: t for t in mid["tiles"]}
        # the colours say what the mark says: a field in its criterion's unit has the
        # allowable at the bands' top (thermal: the battery at 60 degC); a field in another
        # unit (damage against a stress limit, a mode shape without one) is not coloured
        self.assertEqual({k: tiles[k]["scale"] for k in ("thermal", "strength", "nvh")}, {"thermal": "allowable", "strength": "shape", "nvh": "shape"})
        self.assertEqual(tiles["thermal"]["contour"]["max"], 60.0)
        self.assertEqual(set(tiles["thermal"]["contour"]["parts"]), {"battery"})  # only the part its criterion judges
        self.assertEqual(tiles["strength"]["contour"]["values"], 0)
        # no words or figures on the tiles: the interference, the mode's frequency and the
        # part temperatures are in the pictures only (the story: a picture and a mark)
        self.assertEqual(tiles["assembly"]["hits"], "1")  # the fixture's battery × cross1, boxed in red
        self.assertNotIn("Hz", tiles["nvh"]["text"])
        self.assertEqual(tiles["thermal"]["tags"], 0)
        self.assertEqual(mid["charts"], 0)
        # from assembly_s: no tile, no header, the assembly box in n04.assembly
        self.assertTrue(all(not t["shown"] for t in late["tiles"]))
        self.assertFalse(late["header"]["shown"])
        V = layout["n04"]["assembly"]
        self.assertEqual(late["assembly"]["box"], "n04.assembly")
        self.assertTrue(late["assembly"]["shown"])
        self.assertEqual(late["assembly"]["version"], "1")  # the version drawn, not the run's newest
        self.assertEqual((late["assembly"]["lit"], late["assembly"]["border"]), ("red", RED_RGB))  # assembly red in version 1
        self.assertEqual([round(v) for v in late["assembly"]["rect"]], [V["x"], V["y"], V["x"] + V["w"], V["y"] + V["h"]])
        page.close()

    def test_n04_lights_the_named_tiles_in_their_recorded_colours(self):
        """Crash judged green and NVH red in version 1: each lights from its time in its own
        colour (nothing turned red); from assembly_s the assembly's box is lit in the
        assembly's colour (red in version 1)."""
        layout = grid_layout()
        events = with_state(crash_drawn(domain_events()), 1, "crash", "green")
        base = self.serve(layout, events={"20261102-120000-grid-v1": events})
        page, errors = self.open(f"{base}/cut.html?cut=n04&run=20261102-120000-grid-v1&version=1&t=0")
        shots = {t: page.evaluate(READ, t) for t in (0.4, 1.1, 1.3, 3.5)}
        self.assertEqual(errors, [])
        lit = {t: {x["key"].split(".")[-1]: (x["lit"], x["border"]) for x in g["tiles"] if x["hot"]} for t, g in shots.items()}
        self.assertEqual(lit[0.4], {})
        self.assertEqual(lit[1.1], {"crash": ("green", GREEN_RGB)})
        self.assertEqual(lit[1.3], {"crash": ("green", GREEN_RGB), "nvh": ("red", RED_RGB)})
        marks = {x["key"].split(".")[-1]: x["mark"] for x in shots[1.3]["tiles"]}
        self.assertEqual((marks["crash"], marks["nvh"]), ("✓", "✕"))  # the colour is the record's judgement
        self.assertEqual(shots[1.3]["header"]["text"], "設計の版 1・直し 0 回")
        asm = shots[3.5]["assembly"]
        self.assertTrue(asm["shown"])
        self.assertEqual((asm["lit"], asm["border"]), ("red", RED_RGB))
        # the large tile: the one being named, whatever its colour (NVH, red, from nvh_s)
        self.assertEqual(next(x["key"] for x in shots[1.3]["tiles"] if x["featured"]), "n04.tile.nvh")
        page.close()

    def test_n04_large_tile_follows_the_voice(self):
        """The large tile changes as the voice names crash, then NVH: each in turn in the
        feature box (its views fitted again to it), the others in the list's slots in order;
        a named tile without a record is passed over (no assembly records here: it never was
        the large one, as the grid gives way at assembly_s)."""
        layout = grid_layout()
        T = layout["n04"]["times"]
        events = without_assembly(crash_drawn(domain_events()))
        base = self.serve(layout, events={"20261102-120000-grid-v1": events})
        page, errors = self.open(f"{base}/cut.html?cut=n04&run=20261102-120000-grid-v1&version=1&t=0")
        shots = {t: page.evaluate(READ, t) for t in (0.3, T["crash_s"] + 0.05, T["nvh_s"] - 0.05, T["nvh_s"] + 0.05, T["assembly_s"] - 0.05)}
        self.assertEqual(errors, [])
        big = {t: next(x["key"].split(".")[-1] for x in g["tiles"] if x["featured"]) for t, g in shots.items()}
        self.assertEqual(list(big.values()), ["crash", "crash", "crash", "nvh", "nvh"])
        for t, got in shots.items():
            self.check_grid(got, layout, "n04")  # the large box and the slots, in order
        # the canvas of the large tile's drawing fills its new box
        canvas = page.evaluate("""() => { const el = document.querySelector('[data-domain="nvh"] .layer canvas'); const r = el.getBoundingClientRect(); return [r.width, r.height]; }""")
        F = layout["n04"]["feature"]
        B = layout["n04"]["tile"]["border_px"]
        self.assertEqual([round(v) for v in canvas], [F["w"] - 2 * B, F["h"] - 2 * B])
        page.close()
        # a named tile without a record to draw is passed over: the raw fixture's crash
        # records chart nothing the tile draws, so NVH is the large tile from the start
        base = self.serve(layout, events={"20261102-120000-grid-raw": without_assembly(domain_events())})
        page, errors = self.open(f"{base}/cut.html?cut=n04&run=20261102-120000-grid-raw&version=1&t=0")
        got = page.evaluate(READ, 0.3)
        self.assertEqual(errors, [])
        self.assertEqual(next(x["key"] for x in got["tiles"] if x["featured"]), "n04.tile.nvh")
        page.close()

    def test_n15_steps_through_the_versions_and_stops_on_the_last(self):
        """Two rounds share rounds_from_s .. all_green_s (one slot each): in a round's slot the
        tiles take the next version one by one, in the order the record judged them; from
        all_green_s every tile is on version 3 (all green here). The marks follow each tile's
        version, the header the round in progress."""
        layout = grid_layout()
        T = layout["n15"]["times"]
        events = three_versions()
        base = self.serve(layout, events={"20261102-120000-grid-three": events})
        page, errors = self.open(f"{base}/cut.html?cut=n15&run=20261102-120000-grid-three&t=0")
        sw = switch_times(events, T)
        mid1 = T["rounds_from_s"] + (T["all_green_s"] - T["rounds_from_s"]) / 4  # the middle of round 1's slot
        times = [0.2, mid1, *CHECK_TIMES["n15"], 5.1]
        shots = {t: page.evaluate(READ, t) for t in times}
        self.assertEqual(errors, [])
        self.assertEqual(shots[0.2]["duration"], 5.1)
        # the round in progress: 0 before rounds_from_s, 1 in the first slot, 2 in the second
        self.assertEqual({t: g["version"] for t, g in shots.items()}, {0.2: 1, mid1: 2, 0.5: 2, 2.5: 3, 4.8: 3, 5.1: 3})
        for t, got in shots.items():
            self.check_grid(got, layout, "n15")
            self.check_boxes(got, "n15")
            self.assertTrue(all(not x["hot"] for x in got["tiles"]))
            # each tile on the version its time in the slots says (the record's order)
            want = {k: max([1] + [v for v, at in sw[k].items() if t >= at]) for k in TESTS}
            self.assertEqual({x["key"].split(".")[-1]: x["version"] for x in got["tiles"]}, want, t)
        # in the middle of round 1, some tiles are on version 2 and some still on 1
        self.assertEqual({x["version"] for x in shots[mid1]["tiles"]}, {1, 2})
        marks = lambda got: {t["key"].split(".")[-1]: t["mark"] for t in got["tiles"]}  # noqa: E731
        self.assertEqual(marks(shots[0.2]), {k: "✕" if k in V1_RED else "✓" for k in TESTS})
        self.assertEqual(marks(shots[4.8]), {k: "✓" for k in TESTS})
        self.assertEqual(shots[4.8]["tiles"][TESTS.index("assembly")]["hits"], "0")
        self.assertEqual(shots[4.8]["header"]["text"], "繰り返し 2 回")
        # the large tile turned green last: red in version 2, and the latest of those in round 2
        latest = max(V2_RED, key=lambda k: sw[k][3])
        big = next(t for t in shots[4.8]["tiles"] if t["featured"])
        self.assertEqual(big["key"].split(".")[-1], latest)
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
        del layout["n04"]["assembly"]
        del layout["min_text_px"]
        base = self.serve(layout, events={"20261102-120000-grid-v1": domain_events()})
        page, errors = self.open(f"{base}/cut.html?cut=n04&run=20261102-120000-grid-v1&t=1")
        err = page.evaluate("document.querySelector('.err')?.textContent ?? ''")
        self.assertIn("layout.json の n04 に無い: tile.mark.right, tile.pet.w, tile.pet.h, tile.pet.right, tile.pet.bottom, tile.pet.feature.w, tile.pet.feature.h, times.crash_s, assembly.x, assembly.y, assembly.w, assembly.h", err)
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
