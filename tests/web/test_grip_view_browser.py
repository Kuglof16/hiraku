"""The tyre view (js/grip_view.js, js/grip_panel.js, js/kc_view.js; toggle タイヤ, ?on=grip) on the
K2 records of tests/web/grip_fixture.py: the same t gives the same picture, the arrows, circles and
graph points are the record's values, a sliding tyre is orange (not red), what the record does not
have is not drawn, and video=1 shows no buttons.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import make_server
from tests.web.grip_fixture import FIXTURE, RUN_ID, TIMES, grip_events, write_run

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None

BARE = "20261106-120100-grip-bare"  # no friction circle, no mounts
NOKC = "20261106-120200-grip-nokc"  # no kc_bench
NOSLIP = "20261106-120300-grip-noslip"  # no slip channels, no tyre curves
ORANGE = (233, 137, 58)  # #e9893a
RED = (200, 64, 42)  # #c8402a, the screen's red (a failed verdict)
LANE_T = 7.0  # the replay's time: the lane change (arrives at 2.0) at about 5.1 s of its record
BRAKE_T = 20.9  # the braking (arrives at 20.0) at 0.9 s of its record: every wheel slides

DOC = json.loads(FIXTURE.read_text(encoding="utf-8"))
ANA = {a["tool"]: a for a in DOC["analyses"]}


def lerp(s: dict, t: float) -> float:
    ts, vs = s["t_s"], s["values"]
    if t <= ts[0]:
        return vs[0]
    if t >= ts[-1]:
        return vs[-1]
    for i in range(len(ts) - 1):
        if ts[i] <= t <= ts[i + 1]:
            span = ts[i + 1] - ts[i]
            return vs[i] + (vs[i + 1] - vs[i]) * ((t - ts[i]) / span if span > 0 else 0)
    return vs[-1]


def nearest(s: dict, t: float) -> float:
    ts = s["t_s"]
    i = min(range(len(ts)), key=lambda k: (abs(ts[k] - t), -ts[k]))
    return s["values"][i]


def channels(tool: str) -> dict:
    return {s["channel"]: s for s in ANA[tool]["series"] if "t_s" in s}


def scale_of(tool: str) -> float:
    """grip_view.js: CIRCLE_MM over the record's largest friction_limit and |(wheel_fx, wheel_fy)|."""
    ch = channels(tool)
    fmax = 0.0
    for w in ("FL", "FR", "RL", "RR"):
        fx, fy = ch[f"wheel_fx.{w}"], ch[f"wheel_fy.{w}"]
        fmax = max([fmax] + [math.hypot(v, lerp(fy, t)) for t, v in zip(fx["t_s"], fx["values"])] + ch[f"friction_limit.{w}"]["values"])
    return 450.0 / fmax


@unittest.skipUnless(ENABLED, "browser tests: set HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser) with Playwright installed")
class GripViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        runs = cls.tmp / "runs"
        write_run(runs, grip_events())
        write_run(runs, grip_events(drop=("friction_", "sliding", "mount_"), run_id=BARE))
        write_run(runs, grip_events(drop=("kc_bench",), run_id=NOKC))
        write_run(runs, grip_events(drop=("slip_", "tyre_"), run_id=NOSLIP))
        cls.server = make_server("127.0.0.1", 0, runs, quiet=True, saved_dir=None, launcher=False, assets=False)
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

    def open(self, query: str, size=(1440, 810), keep: bool = False):
        """A page at query; the page opened before it is closed first (a drawing page each in
        software GL is slow) unless keep."""
        if not keep:
            self.close_pages()
        page = self.browser.new_page(viewport={"width": size[0], "height": size[1]})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.base}/{query}")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=120000)
        page.evaluate("window.hiraku.ready")
        page.evaluate("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
        self.pages.append((page, errors))
        return page

    def setUp(self):
        self.pages = []
        self.addCleanup(self.close_pages)

    def close_pages(self):
        while self.pages:
            page, errors = self.pages.pop()
            page.close()
            self.assertEqual(errors, [], "page errors")

    def frame(self, page) -> dict:
        return page.evaluate("JSON.parse(JSON.stringify(window.hiraku.grip.frame))")

    def drawn(self, page) -> dict:
        """The 3D objects of the layer: per wheel the circle, the disc, the arrow and the dot."""
        return page.evaluate(
            """(() => { const L = window.hiraku.grip.layer; const out = {};
              for (const [id, o] of Object.entries(L.objs)) out[id] = {
                circle: o.circle ? { v: o.circle.visible, p: o.circle.position.toArray(), r: o.circle.scale.x, color: '#' + o.circle.material.color.getHexString() } : null,
                disc: o.disc ? { v: o.disc.visible, color: '#' + o.disc.material.color.getHexString() } : null,
                dot: { p: o.dot.position.toArray(), color: '#' + o.dot.material.color.getHexString() },
                force: { v: o.force.visible, color: '#' + o.force.children[0].material.color.getHexString() },
                load: o.load ? { v: o.load.visible, h: o.load.scale.z } : null };
              return { wheels: out, mounts: L.mountObjs.length, visible: L.group.visible,
                       colors: (() => { const c = new Set(); L.group.traverse((x) => x.material && x.material.color && c.add('#' + x.material.color.getHexString())); return [...c]; })() }; })()"""
        )

    # ---- the same t, the same picture -------------------------------------------------

    def test_the_same_t_gives_the_same_picture(self):
        q = f"?run={RUN_ID}&tab=bench&pace=0&t={LANE_T}&on=grip&off=graph"
        a = self.open(q)
        b = self.open(q, keep=True)
        self.assertEqual(self.frame(a), self.frame(b))
        self.assertEqual(a.inner_html(".grip-panel"), b.inner_html(".grip-panel"))
        self.assertEqual(self.drawn(a), self.drawn(b))
        # any seek order: away to the braking and back
        f0, html0 = self.frame(a), a.inner_html(".grip-panel")
        a.evaluate(f"window.hiraku.seek({BRAKE_T})")
        self.assertNotEqual(self.frame(a)["t"], f0["t"])
        a.evaluate(f"window.hiraku.seek({LANE_T})")
        self.assertEqual(self.frame(a), f0)
        self.assertEqual(a.inner_html(".grip-panel"), html0)
        self.assertEqual(a.locator(".grip-panel").screenshot(), b.locator(".grip-panel").screenshot())

    # ---- the record's values -----------------------------------------------------------

    def test_arrows_circles_and_points_are_the_records_values(self):
        page = self.open(f"?run={RUN_ID}&tab=bench&pace=0&t={LANE_T}&on=grip")
        f = self.frame(page)
        d = self.drawn(page)
        ch = channels("lane_change")
        tau = f["t"]
        self.assertGreater(tau, 1.0)
        K = scale_of("lane_change")
        for w in f["wheels"]:
            i = w["id"]
            fx, fy = lerp(ch[f"wheel_fx.{i}"], tau), lerp(ch[f"wheel_fy.{i}"], tau)
            self.assertAlmostEqual(w["fx"], fx, places=6)
            self.assertAlmostEqual(w["fy"], fy, places=6)
            contact = [lerp(ch[f"contact_{a}.{i}"], tau) * 1000 for a in "xyz"]
            for got, want in zip(w["contact"], contact):
                self.assertAlmostEqual(got, want, places=3)
            # the arrow's tip is |F| K from the contact point; the circle's radius is friction_limit K
            self.assertAlmostEqual(math.hypot(w["tip"][0] - contact[0], w["tip"][1] - contact[1]), math.hypot(fx, fy) * K, places=3)
            self.assertAlmostEqual(d["wheels"][i]["circle"]["r"], lerp(ch[f"friction_limit.{i}"], tau) * K, places=3)
            self.assertAlmostEqual(d["wheels"][i]["circle"]["p"][0], contact[0], places=3)
            self.assertAlmostEqual(d["wheels"][i]["circle"]["p"][1], contact[1], places=3)
            self.assertAlmostEqual(d["wheels"][i]["dot"]["p"][0], w["tip"][0], places=3)
            self.assertAlmostEqual(w["use"], lerp(ch[f"friction_use.{i}"], tau), places=6)
            # the tyre graph's point: (slip_angle, wheel_fy) at the time
            pt = page.locator(f'.grip-panel svg[data-tyre="fy"] .ty-pt[data-wheel="{i}"]')
            self.assertAlmostEqual(float(pt.get_attribute("data-x")), lerp(ch[f"slip_angle.{i}"], tau), places=6)
            self.assertAlmostEqual(float(pt.get_attribute("data-y")), fy, places=6)
            # the load bar: the largest load is 600 mm tall
            loads = [v for k in ("FL", "FR", "RL", "RR") for v in ch[f"wheel_load.{k}"]["values"]]
            self.assertAlmostEqual(d["wheels"][i]["load"]["h"], lerp(ch[f"wheel_load.{i}"], tau) * 600 / max(loads), places=3)
        # one tyre curve per recorded load, per wheel
        curves = [s for s in ANA["lane_change"]["series"] if s["channel"].startswith("tyre_fy_curve.FL.")]
        self.assertEqual(page.locator('.grip-panel svg[data-tyre="fy"][data-wheel="FL"] .ty-ln').count(), len(curves))
        # the roll: the record's nearest sample in the text, between the samples on the scale
        roll_text = nearest(ch["roll"], tau)
        self.assertIn(f"{roll_text:+.3g}"[:5], page.inner_text(".gp-roll"))
        self.assertAlmostEqual(float(page.get_attribute(".gp-roll svg", "data-roll")), lerp(ch["roll"], tau), places=6)
        # K&C: each wheel's point on the bench's line at its stroke
        kc = {s["channel"]: s for s in ANA["kc_bench"]["series"]}
        for w in f["wheels"]:
            pt = page.locator(f'.grip-panel svg[data-kc="camber"] .kc-pt[data-wheel="{w["id"]}"]')
            stroke = lerp(ch[f"wheel_stroke.{w['id']}"], tau)
            self.assertAlmostEqual(float(pt.get_attribute("data-x")), stroke, places=6)
            line = {"t_s": kc["camber"]["x"]["values"], "values": kc["camber"]["values"]}
            self.assertAlmostEqual(float(pt.get_attribute("data-y")), lerp(line, stroke), places=6)

    def test_the_mount_peak_is_labelled_at_its_instant(self):
        peak = next(r for r in ANA["braking"]["results"] if r["quantity"] == "mount_force_peak")
        t_peak = float(peak["where"].split(" at t ")[1].split()[0])
        page = self.open(f"?run={RUN_ID}&tab=bench&pace=0&t={TIMES['braking'] + t_peak - 0.001:.4f}&on=grip")
        tag = page.locator(".grip-tag.grip-peak")
        self.assertTrue(tag.is_visible())
        self.assertIn(f"{peak['value'] / 1000:.3g} kN", tag.inner_text())
        self.assertEqual(self.drawn(page)["mounts"], len([s for s in ANA["braking"]["series"] if s["channel"].startswith("mount_x.")]))
        page.evaluate(f"window.hiraku.seek({BRAKE_T + 1.5})")
        self.assertFalse(tag.is_visible())

    # ---- sliding is orange, not red ----------------------------------------------------

    def test_a_sliding_tyre_is_orange_not_red(self):
        page = self.open(f"?run={RUN_ID}&tab=bench&pace=0&t={BRAKE_T}&on=grip")
        f = self.frame(page)
        d = self.drawn(page)
        ch = channels("braking")
        sliding = [w for w in f["wheels"] if w["sliding"]]
        self.assertTrue(sliding, "the braking record slides at this time")
        for w in f["wheels"]:
            self.assertEqual(w["sliding"], nearest(ch[f"sliding.{w['id']}"], f["t"]) >= 0.5)
            o = d["wheels"][w["id"]]
            self.assertEqual(o["disc"]["v"], w["sliding"])
            self.assertEqual(o["circle"]["color"], "#e9893a")
            self.assertEqual(o["disc"]["color"], "#e9893a")
            if w["sliding"]:
                self.assertEqual(o["dot"]["color"], "#e9893a")
        self.assertNotIn("#c8402a", d["colors"])
        self.assertEqual(page.locator(".grip-tag.grip-slide:visible").count(), len(sliding))
        # the panel: the sliding points and words orange, nothing red
        reds = page.evaluate(
            """(red) => [...document.querySelectorAll('.grip-panel *, .grip-tag')].filter((e) => {
                 const s = getComputedStyle(e); return [s.color, s.fill, s.stroke, s.backgroundColor].includes(red); }).length""",
            f"rgb({RED[0]}, {RED[1]}, {RED[2]})",
        )
        self.assertEqual(reds, 0)
        fill = page.evaluate("getComputedStyle(document.querySelector('.grip-panel .ty-pt.slide')).fill")
        self.assertEqual(fill, f"rgb({ORANGE[0]}, {ORANGE[1]}, {ORANGE[2]})")
        # braking: the larger force is Fx, so the panel opens on Fx against slip ratio
        self.assertGreater(page.locator('.grip-panel svg[data-tyre="fx"]').count(), 0)

    # ---- what the record does not have ---------------------------------------------------

    def test_what_the_record_lacks_is_not_drawn(self):
        page = self.open(f"?run={BARE}&tab=bench&pace=0&t={LANE_T}&on=grip")
        d = self.drawn(page)
        self.assertTrue(d["visible"])
        self.assertTrue(all(o["circle"] is None and o["disc"] is None for o in d["wheels"].values()))
        self.assertEqual(d["mounts"], 0)
        self.assertEqual(page.locator(".grip-tag.grip-peak").count(), 0)
        self.assertIn("摩擦円の記録が無い", page.inner_text(".grip-panel"))
        self.assertIn("取付点の力", page.inner_text('.grip-panel [data-none*="取付点"]'))
        # no kc_bench: no K&C graphs
        page = self.open(f"?run={NOKC}&tab=bench&pace=0&t={LANE_T}&on=grip")
        self.assertEqual(page.locator(".grip-panel .kc-grid").count(), 0)
        self.assertEqual(page.locator(".grip-panel .ty-grid").count(), 1)
        # no slip channels: no tyre graphs, the note says so
        page = self.open(f"?run={NOSLIP}&tab=bench&pace=0&t={LANE_T}&on=grip")
        self.assertEqual(page.locator(".grip-panel .ty-grid").count(), 0)
        self.assertIn("スリップ角の記録が無い", page.inner_text(".grip-panel"))

    # ---- the toggle and the video layout ---------------------------------------------------

    def test_the_toggle_turns_it_on_and_video_shows_no_buttons(self):
        page = self.open(f"?run={RUN_ID}&tab=bench&pace=0&t={LANE_T}")
        self.assertEqual(page.locator(".grip-panel:visible").count(), 0)
        page.click('#toggles [data-toggle="grip"]')
        page.wait_for_selector(".grip-panel", state="visible")
        self.assertTrue(self.drawn(page)["visible"])
        page = self.open(f"?run={RUN_ID}&tab=bench&pace=0&t={LANE_T}&on=grip&video=1")
        self.assertTrue(page.locator(".grip-panel").is_visible())
        self.assertEqual(page.locator(".grip-panel button:visible").count(), 0)
        # ?tyre=fx opens on Fx against slip ratio
        page = self.open(f"?run={RUN_ID}&tab=bench&pace=0&t={LANE_T}&on=grip&tyre=fx")
        self.assertGreater(page.locator('.grip-panel svg[data-tyre="fx"]').count(), 0)


if __name__ == "__main__":
    unittest.main()
