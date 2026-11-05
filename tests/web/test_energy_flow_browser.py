"""The energy's flow over the 3D (web/js/energy_flow.js) on a run cut from a showcase record
(tests.web.heat_fixture): the same t gives the same picture, the widths and the tags are the
record's values, the heat's colour is not the verdict's red, a temperature reaches the red end
only above its limit, a part without a recorded loss has no branch, a second battery is not
coloured, and the video layout shows no button over it. The pure functions (flowModel, flowAt,
dashesAt) are also called in the page on made-up inputs: a series' part_id, per-part series in W
and kW on one scale.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
"""

from __future__ import annotations

import math
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import make_server
from tests.web import heat_fixture as hf

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None

OVER = hf.RUN_ID + "-over"
TWO = hf.RUN_ID + "-two"
MIN_W, MAX_W = 3, 14  # energy_flow.js
VERDICT_RED = (0xC8, 0x40, 0x2A)  # css --red

READ = """() => {
  const h = window.hiraku.heat.last;
  if (!h) return null;
  return { tRec: h.tRec, ref: h.ref, notes: h.notes, analysisId: h.analysisId, energyId: h.energyId, dashes: h.dashes,
    links: h.flow.links, branches: h.flow.branches, temps: h.flow.temps,
    tags: [...document.querySelectorAll('.lbl-heat')].filter((e) => e.offsetParent).map((e) => ({ part: e.dataset.part, w: Number(e.dataset.w), text: e.innerText, border: getComputedStyle(e).borderTopColor })),
    branchColors: window.hiraku.heat.items.branches.map((b) => '#' + b.base.material.color.getHexString()),
    boxColors: window.hiraku.heat.items.boxes.map((b) => [b.t.part, b.mesh.visible ? '#' + b.mesh.material.color.getHexString() : null]),
    legend: document.querySelector('.heat-legend') && !document.querySelector('.heat-legend').hidden ? document.querySelector('.heat-legend').innerText : null };
}"""


def rgb(hex_or_css: str) -> tuple[int, int, int]:
    s = hex_or_css.strip()
    if s.startswith("#"):
        return tuple(int(s[i : i + 2], 16) for i in (1, 3, 5))
    return tuple(int(float(x)) for x in s[s.index("(") + 1 : s.index(")")].split(",")[:3])


def far(a, b) -> float:
    return math.dist(rgb(a) if isinstance(a, str) else a, rgb(b) if isinstance(b, str) else b)


def trapezoid_mean(t, v) -> float:
    area = sum((v[i] + v[i - 1]) / 2 * (t[i] - t[i - 1]) for i in range(1, len(t)))
    return area / (t[-1] - t[0])


def lerp(t, v, x) -> float:
    if x <= t[0]:
        return v[0]
    for i in range(1, len(t)):
        if x <= t[i]:
            f = (x - t[i - 1]) / (t[i] - t[i - 1]) if t[i] > t[i - 1] else 0.0
            return v[i - 1] + (v[i] - v[i - 1]) * f
    return v[-1]


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class EnergyFlowBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        runs = cls.tmp / "runs"
        cls.events = hf.heat_events()
        hf.write_run(runs, cls.events)
        hf.write_run(runs, hf.over_limit_events(), OVER)
        hf.write_run(runs, hf.second_battery_events(), TWO)
        cls.server = make_server("127.0.0.1", 0, runs, quiet=True, saved_dir=None, launcher=False)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        # the last thermal analysis (T2 controller) and the drive_energy one, as recorded
        cls.thermal = hf.finished(cls.events, "ana-0117")["data"]
        cls.energy = hf.finished(cls.events, "ana-0114")["data"]
        cls.t_last = hf.arrival(cls.events, "ana-0117")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def open(self, query: str, size=(1440, 810)):
        # one page at a time (each draws the 3D every frame): a page is closed before the next opens
        if getattr(self, "page", None) and not self.page.is_closed():
            self.page.close()
        page = self.page = self.browser.new_page(viewport={"width": size[0], "height": size[1]})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.base}/?{query}", timeout=90000)
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=90000)
        page.evaluate("window.hiraku.ready")
        page.evaluate("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
        self.addCleanup(lambda: page.is_closed() or page.close())
        self.addCleanup(lambda: self.assertEqual(errors, []))
        return page

    def at(self, t: float, run: str = hf.RUN_ID, extra: str = ""):
        page = self.open(f"run={run}&tab=bench&pace=0&t={t}{extra}")
        return page, page.evaluate(READ)

    def test_same_t_same_picture(self):
        t = self.t_last + 4.0
        page, first = self.at(t)
        self.assertIsNotNone(first)
        page.evaluate("(t) => window.hiraku.seek(t)", t + 3.0)
        moved = page.evaluate(READ)
        self.assertNotEqual(moved["dashes"], first["dashes"])  # the dashes run
        page.evaluate("(t) => window.hiraku.seek(t)", t)
        back = page.evaluate(READ)
        _, fresh = self.at(t)  # (closes the first page)
        for got in (back, fresh):
            self.assertEqual(got["dashes"], first["dashes"])
            self.assertEqual(got["boxColors"], first["boxColors"])
            self.assertEqual([x["color"] for x in got["temps"]], [x["color"] for x in first["temps"]])
            self.assertEqual(got["tRec"], first["tRec"])

    def test_widths_and_tags_are_the_records_values(self):
        _, got = self.at(self.t_last + 4.0)
        self.assertEqual(got["analysisId"], "ana-0117")
        self.assertEqual(got["energyId"], "ana-0114")
        pb = next(s for s in self.energy["series"] if s["channel"] == "power.battery")
        self.assertEqual(pb["unit"], "kW")
        mean_w = trapezoid_mean(pb["t_s"], pb["values"]) * 1000
        losses = {r["where"]: r["value"] for r in self.thermal["results"] if r["quantity"].startswith("loss_")}
        self.assertEqual(set(losses), {"battery", "motor", "controller"})
        ref = max([mean_w, *losses.values()])
        self.assertAlmostEqual(got["ref"], ref, places=6)
        width = lambda w: MIN_W + (MAX_W - MIN_W) * math.sqrt(abs(w) / ref)
        link = {(l["from"], l["to"]): l for l in got["links"]}
        bat = link[("battery", "main_switch")]
        self.assertAlmostEqual(bat["w"], mean_w, places=6)
        self.assertAlmostEqual(bat["width"], width(mean_w), places=9)
        self.assertTrue(bat["mean"])
        # the drive chain from the design's connects_to; the links after the switch have no recorded power
        self.assertEqual(set(link), {("battery", "main_switch"), ("main_switch", "controller"), ("controller", "motor"), ("motor", "reducer"),
                                     ("reducer", "driveshaft_RL"), ("reducer", "driveshaft_RR"), ("driveshaft_RL", "wheel_RL"), ("driveshaft_RR", "wheel_RR")})
        for k, l in link.items():
            if k != ("battery", "main_switch"):
                self.assertIsNone(l["w"], k)
                self.assertEqual(l["width"], MIN_W, k)
                self.assertFalse(l["recorded"], k)
        self.assertEqual(link[("controller", "motor")]["kind"], "elec")
        self.assertEqual(link[("motor", "reducer")]["kind"], "rot")
        for b in got["branches"]:
            self.assertEqual(b["w"], losses[b["part"]])
            self.assertAlmostEqual(b["width"], width(losses[b["part"]]), places=9)
        tags = {x["part"]: x for x in got["tags"]}
        self.assertEqual(set(tags), set(losses))
        for part, x in tags.items():
            self.assertEqual(x["w"], losses[part])
            self.assertIn(f"{round(losses[part])} W", x["text"].replace(",", ""))
        # the temperatures: the record's series at the record time shown
        for p in got["temps"]:
            s = next(s for s in self.thermal["series"] if s["channel"] == f"temp.{p['part']}")
            self.assertAlmostEqual(p["T"], lerp(s["t_s"], s["values"], got["tRec"]), places=9)

    def test_heat_colour_is_not_the_verdicts_red(self):
        page, got = self.at(self.t_last + 4.0)
        heat = page.evaluate("import('/js/energy_flow.js').then((m) => [m.HEAT_COLOR, m.VERDICT_RED])")
        self.assertEqual(rgb(heat[1]), VERDICT_RED)
        self.assertGreater(far(heat[0], VERDICT_RED), 60)
        self.assertTrue(got["branchColors"])
        for c in got["branchColors"]:
            self.assertEqual(c, heat[0])
        for x in got["tags"]:
            self.assertEqual(rgb(x["border"]), rgb(heat[0]))
        self.assertIn("熱 (損失)", got["legend"])

    def test_temperature_reaches_the_red_end_only_above_its_limit(self):
        # as recorded every part stays under its limit: no box red, none near it, at the start and the end
        for t in (self.t_last + 0.3, self.t_last + 30):
            _, got = self.at(t)
            self.assertEqual(len(got["temps"]), 3)
            for p in got["temps"]:
                self.assertLess(p["T"], p["limit"])
                self.assertFalse(p["over"])
                self.assertGreater(far(p["color"], VERDICT_RED), 60, p)
        # 8 degC up (shape example): the battery is red once it is above 55 degC, not before
        _, early = self.at(self.t_last + 0.3, OVER)
        _, late = self.at(self.t_last + 30, OVER)
        bat = lambda got: next(p for p in got["temps"] if p["part"] == "battery")
        self.assertLess(bat(early)["T"], 55)
        self.assertNotEqual(rgb(bat(early)["color"]), VERDICT_RED)
        self.assertGreater(bat(late)["T"], 55)
        self.assertEqual(rgb(bat(late)["color"]), VERDICT_RED)
        self.assertEqual(dict(late["boxColors"])["battery"], "#c8402a")

    def test_no_branch_where_no_loss_is_recorded(self):
        _, got = self.at(self.t_last + 4.0)
        self.assertEqual({b["part"] for b in got["branches"]}, {"battery", "motor", "controller"})
        note = next(n for n in got["notes"] if "損失は記録に無い" in n)
        self.assertIn("減速機", note)
        self.assertIn("減速機", got["legend"])

    def test_a_second_battery_is_not_coloured(self):
        _, got = self.at(self.t_last + 4.0, TWO)
        self.assertEqual({p["part"] for p in got["temps"]}, {"battery", "motor", "controller"})
        self.assertTrue(any("battery2" in n and "塗らない" in n for n in got["notes"]), got["notes"])

    def test_video_layout_has_no_button_over_it(self):
        page, got = self.at(self.t_last + 4.0, extra="&video=1")
        self.assertIsNotNone(got)
        shown = page.evaluate("() => [...document.querySelectorAll('#viewport button')].filter((b) => b.checkVisibility()).map((b) => b.textContent)")
        self.assertEqual(shown, [])
        # without video=1 the toggle 熱の流れ is there, and ?off=heat takes the flow off (the contour comes back)
        page, _ = self.at(self.t_last + 4.0)
        self.assertIn("熱の流れ", page.evaluate("() => document.querySelector('#toggles').innerText"))
        page, off = self.at(self.t_last + 4.0, extra="&off=heat")
        self.assertIsNone(off)
        self.assertFalse(page.evaluate("() => document.querySelector('#legend').hidden"))

    def test_on_heat_shows_it_with_another_analysis_picked(self):
        # P4 (drive_energy) picked: nothing without ?on=heat; with it, the newest thermal analysis of the version
        for extra, want in (("", None), ("&on=heat", "ana-0117")):
            page, _ = self.at(self.t_last + 4.0, extra=extra)
            page.click('#tests button[data-lc="P4-20km"]')
            page.evaluate("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
            got = page.evaluate(READ)
            self.assertEqual(got and got["analysisId"], want, extra)

    def test_the_pure_functions_on_per_part_series(self):
        """Made-up inputs (not a record): part_id ties a series to its part; power in kW and W
        and losses in W on one scale, read at the record time; dashes from t and their number."""
        page = self.open(f"run={hf.RUN_ID}&tab=bench&pace=0&t=1")
        got = page.evaluate("""async () => {
          const m = await import('/js/energy_flow.js');
          const box = (c) => ({ center_mm: c, size_mm: [100, 100, 100] });
          const design = { design_version: 1, variables: {}, parts: [
            { id: 'bat', kind: 'battery', box: box([0, 0, 0]), connects_to: [{ to: 'ctl', via: 'wire', carries: 'power' }] },
            { id: 'bat2', kind: 'battery', box: box([0, 300, 0]) },
            { id: 'ctl', kind: 'controller', box: box([1000, 0, 0]), connects_to: [{ to: 'mot', via: 'wire', carries: 'power' }] },
            { id: 'mot', kind: 'motor', box: box([2000, 0, 0]), connects_to: [{ to: 'whl', via: 'spline', carries: 'torque' }] },
            { id: 'whl', kind: 'wheel', box: box([3000, 0, 0]) } ] };
          const s = (channel, unit, values, part_id) => ({ channel, unit, t_s: [0, 10], values, ...(part_id ? { part_id } : {}) });
          const thermal = { analysis_id: 'a1', design_version: 1, status: 'ok', results: [], series: [
            s('power.battery', 'kW', [2, 4], 'bat'), s('power.controller', 'W', [1900, 3800]), s('power.motor', 'W', [1700, 3400]),
            s('loss.motor', 'W', [200, 400]), s('heat_to_air.motor', 'W', [100, 300]),
            s('temp.battery', 'degC', [20, 40], 'bat2'), s('temp.motor', 'degC', [20, 60]) ] };
          const model = m.flowModel({ design, thermal, verdicts: [{ quantity: 'temp_motor_max', op: '<', limit: { value: 50, unit: 'degC' }, pass: false }] });
          const at5 = m.flowAt(model, 5), at10 = m.flowAt(model, 10);
          return { ref: model.ref, at5, at10, temps: model.temps.map((t) => t.part), notes: model.notes,
            d1: m.dashesAt(model, at5, 3.25), d2: m.dashesAt(model, at5, 3.25), d3: m.dashesAt(model, at5, 3.5) };
        }""")
        self.assertEqual(got["ref"], 4000)  # 4 kW = 4000 W, the largest over the run
        w5 = {(l["from"], l["to"]): l["w"] for l in got["at5"]["links"]}
        self.assertEqual(w5, {("bat", "ctl"): 3000, ("ctl", "mot"): 2850, ("mot", "whl"): 2550})
        self.assertEqual(got["at10"]["links"][0]["width"], MAX_W)
        self.assertEqual(got["at5"]["branches"][0]["w"], 300)
        self.assertEqual(got["at5"]["branches"][0]["air"], 200)
        self.assertEqual(got["temps"], ["bat2", "mot"])  # part_id wins: the series names bat2, not bat
        mot = got["at10"]["temps"][1]
        self.assertTrue(mot["over"])  # 60 degC against < 50 degC
        self.assertFalse(got["at5"]["temps"][1]["over"])  # 40 degC
        self.assertEqual(got["d1"], got["d2"])
        self.assertNotEqual(got["d1"], got["d3"])


if __name__ == "__main__":
    unittest.main()
