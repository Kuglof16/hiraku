"""The N03 and N06 cut pages (cut.html?cut=n03 / n06) in a headless browser.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed (as tests.web.test_browser).
The run is made here from the contract sample: the drive-pattern, thermal and launch
records N03 plays, and load cases past the limit (ids ending in -beyond) for N06. With
HIRAKU_WEB_DEV_RUNS the newest run there is opened too.

Shape example only: every value in these records is made up to show each kind of record
on the screen; none of it is a calculation result. The layout values are provisional,
in the keys the pages read (layout.json n03 / n06), until the promo department's arrive.
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

from hiraku import contract
from hiraku.webserve.server import SAMPLE_EVENTS, make_server
from tests.web.test_browser import ENABLED, sync_playwright
from tests.web.test_cut_grid_browser import box_of, list_rects

BAND_Y = 910
COLORS = {"bg": "#f4f1ea", "panel": "#fbf9f4", "rule": "#c9c3b6"}
OVERLAY = {"color": "#c8402a", "opacity": 0.22}
# around loop_to_s (2.8) and accel_s (2.9), and the end
N03_TIMES = [0.2, 1.0, 2.0, 2.8, 2.85, 2.9, 3.5, 4.97]
# before the first tile (0.6), each tile's start (0.6 + 0.55 k) and landing, the last landing (3.85), the end
N06_TIMES = [0.5, 0.7, 1.2, 2.0, 3.3, 3.4, 3.9, 6.0, 11.6]


def n03_n06_layout():
    """The promo department's proposed values for n03 / n06 (until they are written in
    layout.json), with the top keys the cut pages read; colours and overlay as n01."""
    return {
        "stage": {"w": 1920, "h": 1080},
        "subtitle_band": {"y": BAND_Y, "h": 170},
        "min_text_px": 18,
        "n03": {
            "duration_s": 5.0,
            "view": {"x": 96, "y": 96, "w": 1200, "h": 790},
            "popup": {"x": 1328, "y": 96, "w": 496, "h": 790, "title_px": 30, "text_px": 22, "reading_of_title": 2},
            "times": {"lead_s": 0.3, "loop_to_s": 2.8, "accel_s": 2.9},
            "colors": dict(COLORS),
        },
        "n06": {
            "duration_s": 11.7,
            "grid": {"x": 96, "y": 96, "cols": 3, "rows": 2, "tile_w": 560, "tile_h": 383, "gap_x": 24, "gap_y": 24},
            "feature": {"x": 96, "y": 96, "w": 1104, "h": 790},
            "list": {"x": 1224, "y": 96, "w": 600, "h": 790, "cols": 2, "rows": 3, "gap_x": 24, "gap_y": 24},
            "tests": ["step_break", "tilt_over", "step_stuck", "battery_hot", "parts_clash", "rollover"],
            "tile": {"border_px": 2, "radius_px": 12, "label": {"x": 16, "y": 12, "size_px": 26}},
            "times": {"tiles_s": 0.6, "tile_in_s": 0.5, "tile_stagger_s": 0.55},
            "colors": dict(COLORS),
            "overlay": dict(OVERLAY),
        },
    }


# ---- the run (shape example) -------------------------------------------------------

def _s(name, unit, t, values):
    return {"channel": name, "unit": unit, "t_s": [round(x, 6) for x in t], "values": [round(float(v), 6) for v in values]}


def _motion(design, t, body, roll=lambda x: 0.0, load=None, fx=lambda x: 0.0, extra=()):
    """Series of a rigid body moving by body(t) -> (x, y, z) m, rolled by roll(t) deg; each
    wheel's load by load(id, t) N (else a quarter of 5000 N); grip along x by fx(t) N."""
    wheels = {s["id"]: s["position_mm"] for s in design["supports"]}
    out = []
    pos = [body(x) for x in t]
    for k, n in enumerate(("body_x", "body_y", "body_z")):
        out.append(_s(n, "m", t, [p[k] for p in pos]))
    out += [_s("roll", "deg", t, [roll(x) for x in t]), _s("pitch", "deg", t, [0.0] * len(t)), _s("yaw", "deg", t, [0.0] * len(t))]
    for k, n in enumerate(("cg_x", "cg_y", "cg_z")):
        out.append(_s(n, "m", t, [p[k] + (-0.9, 0.0, 0.6)[k] for p in pos]))
    for wid, wp in wheels.items():
        ld = [(load(wid, x) if load else 1250.0) for x in t]
        cx = [p[0] + wp[0] / 1000 for p in pos]
        cy = [p[1] + wp[1] / 1000 for p in pos]
        out += [
            _s(f"wheel_load.{wid}", "N", t, ld),
            _s(f"wheel_center_x.{wid}", "m", t, cx), _s(f"wheel_center_y.{wid}", "m", t, cy),
            _s(f"wheel_center_z.{wid}", "m", t, [p[2] + wp[2] / 1000 for p in pos]),
            _s(f"contact_x.{wid}", "m", t, cx), _s(f"contact_y.{wid}", "m", t, cy), _s(f"contact_z.{wid}", "m", t, [0.0] * len(t)),
            _s(f"reaction_x.{wid}", "N", t, [0.0] * len(t)), _s(f"reaction_y.{wid}", "N", t, [0.0] * len(t)), _s(f"reaction_z.{wid}", "N", t, ld),
            _s(f"grip_x.{wid}", "N", t, [fx(x) / 4 for x in t]), _s(f"grip_y.{wid}", "N", t, [0.0] * len(t)), _s(f"grip_z.{wid}", "N", t, [0.0] * len(t)),
        ]
    return out + list(extra)


def _lc(lc_id, test, domain, title, params=None, criterion=None):
    return {"load_case_id": lc_id, "test_id": test, "domain": domain, "title": title, "params": params or {"loading": "two_cargo"},
            "tolerance_rel": 0.15, **({"criterion": criterion} if criterion else {})}


def _crit(q, op, value, unit):
    return {"quantity": q, "op": op, "limit": {"value": value, "unit": unit}, "basis": "形の見本"}


def _r(q, v, unit, **kw):
    return {"quantity": q, "value": v, "unit": unit, **kw}


ALLOWABLE = 156.7  # MPa (shape example)
STOP_LIMIT = 14.0  # m (shape example)
STOP_GOT = 16.0  # m (shape example)


def n03_n06_events():
    """The contract sample's run and design with the records N03 and N06 draw (shape example)."""
    src = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    run, d_ev = copy.deepcopy(src[0]), copy.deepcopy(src[1])
    design = d_ev["data"]
    members = [m["id"] for m in design["members"]]
    run["data"]["load_cases"] = [
        _lc("P4-v", "P4", "powertrain", "長く走る (形の見本)", criterion=_crit("soc_end", ">", 0.0, "%")),
        _lc("T1-v", "T1", "thermal", "長く走ったときの電池の温度 (形の見本)", criterion=_crit("temp_battery_max", "<=", 55.0, "degC")),
        _lc("P2-v", "P2", "powertrain", "発進 (形の見本)"),
        _lc("R1-v", "R1", "strength", "段差の力 (形の見本)", criterion=_crit("max_stress", "<=", ALLOWABLE, "MPa")),
        _lc("P1-step-beyond", "P1", "powertrain", "限界の外: 高い段差 (形の見本)", params={"loading": "two_cargo", "step_height": {"value": 250.0, "unit": "mm"}}),
        _lc("R1-step-beyond", "R1", "strength", "限界の外: 高い段差の力 (形の見本)", criterion=_crit("max_stress", "<=", ALLOWABLE, "MPa")),
        _lc("S5-slope-beyond", "S5", "handling", "限界の外: 急な横斜面 (形の見本)",
            params={"loading": "two_cargo", "side_slope": {"value": 44.0, "unit": "deg"}, "slope_up": "left"}),
        _lc("F1-wind-beyond", "F1", "aero", "限界の外: 強い横風 (形の見本)", criterion=_crit("critical_wind_speed", ">=", 57.0, "m/s")),
        _lc("S4-brake-beyond", "S4", "handling", "限界の外: 滑りやすい路面で急ブレーキ (形の見本)", criterion=_crit("stopping_distance", "<=", STOP_LIMIT, "m")),
        _lc("P3-v", "P3", "powertrain", "平らな道で 40 km/h まで (形の見本)", params={"loading": "two_cargo", "grade": {"value": 0.0, "unit": "%"}}),
        _lc("S3-dlc-beyond", "S3", "handling", "限界の外: 速いダブルレーンチェンジ (形の見本)", criterion=_crit("min_side_load", ">", 0.0, "N")),
        _lc("T1-hot-beyond", "T1", "thermal", "限界の外: 暑い日に長い上り (形の見本)", criterion=_crit("temp_battery_max", "<=", 55.0, "degC")),
        _lc("H1-v", "H1", "assembly", "組み立ての干渉 (形の見本)"),
    ]
    t_long = [k * 60.0 for k in range(41)]  # 0 .. 2400 s
    t_short = [k * 0.2 for k in range(41)]  # 0 .. 8 s
    stress = {m: 60.0 + 25.0 * k for k, m in enumerate(members)}  # the last ones over ALLOWABLE
    anas = [
        {"analysis_id": "ana-0001", "tool": "drive_energy", "base": "K3", "load_case_id": "P4-v",
         "series": [_s("soc", "%", t_long, [100.0 - 45.0 * x / 2400.0 for x in t_long]), _s("speed", "km/h", t_long, [30.0 + 10.0 * math.sin(x / 300.0) for x in t_long])],
         "results": [_r("soc_end", 55.0, "%")]},
        {"analysis_id": "ana-0002", "tool": "thermal", "base": "K3", "load_case_id": "T1-v",
         "series": [_s("temp.battery", "degC", t_long, [40.0 + 8.4 * (1 - math.exp(-x / 1200.0)) / (1 - math.exp(-2.0)) for x in t_long]),
                    _s("temp.motor", "degC", t_long, [40.0 + 30.0 * (1 - math.exp(-x / 600.0)) for x in t_long])],
         "fields": [{"kind": "temperature", "label": "部品の温度", "unit": "degC", "per_part": {"battery": 48.4}}],
         "results": [_r("temp_battery_max", 48.4, "degC", where="battery")]},
        {"analysis_id": "ana-0003", "tool": "launch", "base": "K2", "load_case_id": "P2-v",
         "series": _motion(design, t_short, lambda x: (0.5 * 1.5 * x * x, 0.0, 0.0), fx=lambda x: 900.0,
                           extra=[_s("speed", "km/h", t_short, [1.5 * x * 3.6 for x in t_short])]),
         "results": [_r("top_speed", 43.2, "km/h")]},
        {"analysis_id": "ana-0004", "tool": "frame_static", "base": "K1", "load_case_id": "R1-v",
         "fields": [{"kind": "stress", "label": "von Mises", "unit": "MPa", "per_member": {m: 40.0 + 10.0 * k for k, m in enumerate(members)}}],
         "results": [_r("max_stress", 40.0 + 10.0 * (len(members) - 1), "MPa", where=members[-1])]},
        {"analysis_id": "ana-0005", "tool": "step_climb_dynamic", "base": "K2", "load_case_id": "P1-step-beyond",
         "series": _motion(design, t_short[:11], lambda x: (min(x, 1.0) * 0.4, 0.0, 0.0), fx=lambda x: 3000.0),
         "results": [_r("climbed", 0.0, "1")]},
        {"analysis_id": "ana-0006", "tool": "step_climb", "base": "K1", "load_case_id": "P1-step-beyond",
         "fields": [{"kind": "stress", "label": "von Mises", "unit": "MPa", "per_member": stress}],
         "results": [_r("max_stress", max(stress.values()), "MPa", where=members[-1])]},
        {"analysis_id": "ana-0007", "tool": "frame_static", "base": "K1", "load_case_id": "R1-step-beyond",
         "fields": [{"kind": "stress", "label": "von Mises", "unit": "MPa", "per_member": stress}],
         "results": [_r("max_stress", max(stress.values()), "MPa", where=members[-1])]},
        {"analysis_id": "ana-0008", "tool": "rough_slope", "base": "K2", "load_case_id": "S5-slope-beyond",
         "series": _motion(design, t_short, lambda x: (x * 5.5, 0.0, 0.0), roll=lambda x: max(0.0, x - 2.0) * 10.0,
                           load=lambda wid, x: (0.0 if x >= 2.0 else 1250.0 * (1 - x / 2.0)) if wid in ("FL", "RL") else 1250.0 * (1 + min(x, 2.0) / 2.0)),
         "results": [_r("wheel_lift", 1.0, "1"), _r("tipped", 1.0, "1")]},
        {"analysis_id": "ana-0009", "tool": "crosswind", "base": "K2", "load_case_id": "F1-wind-beyond",
         "series": _motion(design, t_short, lambda x: (x * 11.0, -0.12 * x * x, 0.0), extra=[_s("wind_speed", "m/s", t_short, [57.0] * len(t_short))]),
         "results": [_r("critical_wind_speed", 50.0, "m/s"), _r("slides_first", 1.0, "1")]},
        {"analysis_id": "ana-0010", "tool": "braking", "base": "K2", "load_case_id": "S4-brake-beyond",
         "series": _motion(design, t_short, lambda x: (STOP_GOT * (1 - (1 - min(x, 8.0) / 8.0) ** 2), 0.0, 0.0), fx=lambda x: -2000.0),
         "results": [_r("stopping_distance", STOP_GOT, "m")]},
        {"analysis_id": "ana-0011", "tool": "launch", "base": "K2", "load_case_id": "P3-v",
         "series": _motion(design, t_short[:31], lambda x: (0.5 * 1.9 * x * x, 0.0, 0.0), fx=lambda x: 1100.0,
                           extra=[_s("speed", "km/h", t_short[:31], [1.9 * x * 3.6 for x in t_short[:31]])]),
         "results": [_r("time_to_speed", 5.85, "s")]},
        {"analysis_id": "ana-0012", "tool": "lane_change", "base": "K2", "load_case_id": "S3-dlc-beyond",
         "series": _motion(design, t_short, lambda x: (x * 15.0, 1.75 * (1 - math.cos(math.pi * min(x, 4.0) / 4.0)), 0.0),
                           roll=lambda x: max(0.0, x - 3.0) * 12.0,
                           load=lambda wid, x: (0.0 if x >= 3.0 else 1250.0 * (1 - x / 3.0)) if wid in ("FR", "RR") else 1250.0 * (1 + min(x, 3.0) / 3.0)),
         "results": [_r("min_side_load", 0.0, "N"), _r("wheel_lift", 1.0, "1")]},
        {"analysis_id": "ana-0013", "tool": "thermal", "base": "K3", "load_case_id": "T1-hot-beyond",
         "fields": [{"kind": "temperature", "label": "部品の温度", "unit": "degC", "per_part": {"battery": 61.0, "seat": 35.0}}],
         "results": [_r("temp_battery_max", 61.0, "degC", where="battery")]},
        {"analysis_id": "ana-0014", "tool": "assembly", "base": "K4", "load_case_id": "H1-v",
         "assembly": [
             {"order": 1, "part": "seat", "direction": "-z", "no_interference": True, "tool_clearance": True, "hand_reach": True,
              "path_mm": [[-600.0, 0.0, 1400.0], [-600.0, 0.0, 600.0]]},
             {"order": 2, "part": "battery", "direction": "-z", "depends_on": ["seat"], "no_interference": False,
              "tool_clearance": True, "hand_reach": True, "blocked_by": ["cross1"]},
         ],
         "interferences": [{"a": "battery", "b": "member cross1", "depth_mm": 8.0, "state": "rest"}]},
    ]
    events = [run, d_ev]
    for i, a in enumerate(anas):
        a.update({"design_version": 1, "prediction_ids": [f"pred-{i + 1:04d}"], "status": "ok"})
        a.setdefault("results", [])
        events.append({"kind": "analysis.finished", "agent": "system", "design_version": 1, "data": a})
    events.append({"kind": "run.finished", "agent": "system", "data": {"status": "stopped", "stop_reason": "round_limit"}})
    base = {"run_id": run["run_id"], "ts": run["ts"]}
    out = []
    for k, ev in enumerate(events):
        if k >= 2:
            ev = {**base, **ev, "t": 1.0 + k * 0.5}
        ev["seq"] = k
        out.append(ev)
    return out


# What the page shows at stage time t, read in the page.
READ = """async (t) => {
  await window.hiraku.seek(t);
  const cut = window.hiraku.cut;
  const rect = (el) => { const r = el.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; };
  const shown = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility === "visible" && el.closest('[style*="display: none"]') === null; };
  const texts = [];
  for (const e of document.querySelectorAll("#stage *")) {
    if (!shown(e)) continue;
    const own = [...e.childNodes].filter((n) => n.nodeType === 3 && n.textContent.trim()).map((n) => n.textContent.trim()).join("");
    if (own) texts.push({ text: own, px: parseFloat(getComputedStyle(e).fontSize), box: e.closest("[data-box]")?.dataset.box ?? null });
  }
  return {
    duration: window.hiraku.player.duration, err: document.querySelector(".err")?.textContent ?? null,
    phase: document.documentElement.dataset.phase ?? null,
    boxes: [...document.querySelectorAll("[data-box]")].filter(shown).map((el) => ({ box: el.dataset.box, rect: rect(el) })),
    texts,
    view: (() => { const v = document.querySelector(".n03-view"); return v ? { tau: v.dataset.tau === "" ? null : Number(v.dataset.tau), view: v.dataset.view,
      rate: v.querySelector(".rate").textContent, outside: v.dataset.outside ?? null } : null; })(),
    battery: cut?.driveView?.keys.contour?.parts ? Object.values(cut.driveView.keys.contour.parts)[0] : null,
    pages: [...document.querySelectorAll(".n03-page")].filter((p) => p.style.display !== "none").map((p) => p.dataset.page),
    now: [...document.querySelectorAll(".n03-page")].filter((p) => p.style.display !== "none").flatMap((p) => [...p.querySelectorAll(".now")].map((n) => n.textContent)),
    title: [...document.querySelectorAll(".n03-page")].filter((p) => p.style.display !== "none").map((p) => p.querySelector("h3").textContent)[0] ?? null,
    tiles: [...document.querySelectorAll(".n06-tile")].map((el) => ({ box: el.dataset.box, rect: rect(el), shown: shown(el), p: Number(el.dataset.p),
      tau: el.dataset.tau === undefined ? null : Number(el.dataset.tau), miss: el.dataset.miss, over: el.dataset.over ?? null, lift: el.dataset.lift ?? null,
      past: el.dataset.past ?? null, clash: el.dataset.clash ?? null, marks: el.dataset.marks === undefined ? null : Number(el.dataset.marks),
      outside: el.dataset.outside ?? null, text: el.textContent, mark: el.querySelector(".mark")?.textContent ?? null,
      markColor: el.querySelector(".mark") ? getComputedStyle(el.querySelector(".mark")).color : null })),
    bigs: [...document.querySelectorAll(".n06-big")].map((el) => ({ box: el.dataset.box, rect: rect(el), shown: shown(el),
      tau: el.dataset.tau === undefined ? null : Number(el.dataset.tau), marks: el.dataset.marks === undefined ? null : Number(el.dataset.marks),
      text: el.textContent, label: (() => { const b = el.querySelector('[data-kind="label"]'); return b ? parseFloat(getComputedStyle(b).fontSize) : null; })() })),
  };
}"""


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class CutN03N06Test(unittest.TestCase):
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
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=120000)
        page.evaluate("window.hiraku.ready")
        return page, errors

    def inside(self, a, b, what):
        """Rectangle a [l, t, r, b] lies within b (half a pixel of rounding)."""
        self.assertTrue(a[0] >= b[0] - 0.5 and a[1] >= b[1] - 0.5 and a[2] <= b[2] + 0.5 and a[3] <= b[3] + 0.5, f"{what}: {a} not inside {b}")

    def at(self, got, box):
        return next((b["rect"] for b in got["boxes"] if b["box"] == box), None)

    def check_common(self, got, layout):
        """No error; every box shown on the stage above the subtitle band; no text under min_text_px."""
        self.assertIsNone(got["err"])
        for b in got["boxes"]:
            self.inside(b["rect"], [0, 0, 1920, BAND_Y], b["box"])
        for x in got["texts"]:
            self.assertGreaterEqual(x["px"], layout["min_text_px"], x)

    def test_events_follow_the_contract(self):
        for ev in n03_n06_events():
            self.assertEqual(contract.errors("event", ev), [], ev["kind"])

    def check_n03(self, shots, layout, launch_case=True):
        sp = layout["n03"]
        T = sp["times"]
        V, P = sp["view"], sp["popup"]
        for t, got in shots.items():
            self.check_common(got, layout)
            self.assertEqual(got["duration"], sp["duration_s"])
            self.assertEqual([round(v) for v in self.at(got, "n03.view")], [V["x"], V["y"], V["x"] + V["w"], V["y"] + V["h"]])
            self.assertEqual([round(v) for v in self.at(got, "n03.popup")], [P["x"], P["y"], P["x"] + P["w"], P["y"] + P["h"]])
            self.assertIsNone(got["view"]["outside"])
            # the popup's parts in the popup, the rate in the view's corner
            for b in got["boxes"]:
                if b["box"].startswith("n03.popup."):
                    self.inside(b["rect"], self.at(got, "n03.popup"), b["box"])
                if b["box"].startswith("n03.view."):
                    self.inside(b["rect"], self.at(got, "n03.view"), b["box"])
            phase = "launch" if t >= T["accel_s"] else "drive"
            self.assertEqual(got["phase"], phase, t)
            self.assertEqual(got["pages"], [phase], t)
            if launch_case:
                self.assertEqual(got["view"]["view"], phase, t)
            if t >= T["lead_s"]:
                self.assertTrue(got["view"]["rate"].startswith("×"), (t, got["view"]["rate"]))
                self.assertTrue(got["now"] and all(n for n in got["now"]), (t, got["now"]))

    def check_n03_times(self, shots, layout, launch_end):
        """The drive plays from lead_s to loop_to_s and holds its end until accel_s; the
        acceleration plays from accel_s (its first sample) to the end of the cut."""
        T = layout["n03"]["times"]
        self.assertLess(shots[1.0]["view"]["tau"], shots[2.0]["view"]["tau"])
        self.assertAlmostEqual(shots[T["loop_to_s"]]["view"]["tau"], 2400.0, places=6)
        self.assertEqual(shots[2.85]["phase"], "drive")
        self.assertAlmostEqual(shots[2.85]["view"]["tau"], 2400.0, places=6)
        self.assertAlmostEqual(shots[T["accel_s"]]["view"]["tau"], 0.0, places=6)
        self.assertLess(shots[3.5]["view"]["tau"], shots[4.97]["view"]["tau"])
        dur = layout["n03"]["duration_s"]
        self.assertAlmostEqual(shots[4.97]["view"]["tau"], launch_end * (4.97 - T["accel_s"]) / (dur - T["accel_s"]), places=6)
        # squeezed: 2400 s of record over loop_to_s - lead_s, as rateLabel says it
        self.assertEqual(shots[1.0]["view"]["rate"], f"×{round(2400.0 / (T['loop_to_s'] - T['lead_s']))}")

    def test_n03_plays_the_long_drive_then_the_straight_acceleration(self):
        layout = n03_n06_layout()
        base = self.serve(layout, events={"20261103-120000-n03": n03_n06_events()})
        page, errors = self.open(f"{base}/cut.html?cut=n03&run=20261103-120000-n03&t=0")
        shots = {t: page.evaluate(READ, t) for t in N03_TIMES}
        self.assertEqual(errors, [])
        self.check_n03(shots, layout)
        self.check_n03_times(shots, layout, launch_end=6.0)  # P3's record: 0 .. 6 s
        # the popup names the tests it charts: the flat acceleration (P3) is preferred to P2;
        # one graph at a time (the battery's temperature, then the speed)
        self.assertEqual(shots[1.0]["title"], "長く走ったときの電池の温度 (T1)")
        self.assertEqual(shots[3.5]["title"], "加速の速さ (P3)")
        self.assertEqual({t: len(g["now"]) for t, g in shots.items() if t >= layout["n03"]["times"]["lead_s"]}, {t: 1 for t in N03_TIMES if t >= layout["n03"]["times"]["lead_s"]})
        # the battery warms: its colour's value is the recorded temperature, rising
        self.assertLess(shots[1.0]["battery"], shots[2.0]["battery"])
        self.assertGreaterEqual(shots[1.0]["battery"], 40.0)
        self.assertLessEqual(shots[2.8]["battery"], 48.4 + 1e-6)
        self.assertEqual(page.evaluate("window.hiraku.cut.launchView.toggles.arrows && window.hiraku.cut.launchView.toggles.cg"), True)
        page.close()

    def test_n03_takes_p2_without_a_p3_record(self):
        layout = n03_n06_layout()
        events = [e for e in n03_n06_events() if not (e["kind"] == "analysis.finished" and e["data"]["load_case_id"] == "P3-v")]
        base = self.serve(layout, events={"20261103-120000-n03": events})
        page, errors = self.open(f"{base}/cut.html?cut=n03&run=20261103-120000-n03&t=0")
        shots = {t: page.evaluate(READ, t) for t in N03_TIMES}
        self.assertEqual(errors, [])
        self.check_n03(shots, layout)
        self.check_n03_times(shots, layout, launch_end=8.0)  # P2's record: 0 .. 8 s
        self.assertEqual(shots[3.5]["title"], "加速の速さ (P2)")
        page.close()

    def check_n06(self, shots, layout, recorded):
        sp = layout["n06"]
        T = sp["times"]
        for t, got in shots.items():
            self.check_common(got, layout)
            self.assertEqual(got["duration"], sp["duration_s"])
            self.assertEqual([x["box"] for x in got["tiles"]], [f"n06.tile.{k}" for k in sp["tests"]])
            for i, x in enumerate(got["tiles"]):
                # tile i comes in from tiles_s + i tile_stagger_s over tile_in_s
                start = T["tiles_s"] + i * T["tile_stagger_s"]
                self.assertEqual(x["shown"], t > start, (t, x["box"]))
                self.assertAlmostEqual(x["p"], max(0.0, min(1.0, (t - start) / T["tile_in_s"])), places=6, msg=(t, x["box"]))
                self.assertIsNone(x["outside"], x["box"])
                if t >= start + T["tile_in_s"]:
                    # the list: one slot per tile, row by row (layout.json n06.list)
                    r = list_rects(sp["list"])[i]
                    self.assertEqual([round(v) for v in x["rect"]], [r[0], r[1], r[0] + r[2], r[1] + r[3]], x["box"])
                    self.assertLessEqual(x["rect"][3], BAND_Y, x["box"])
                    for b in got["boxes"]:
                        if b["box"].startswith(x["box"] + "."):
                            self.inside(b["rect"], x["rect"], b["box"])
                key = x["box"].split(".")[-1]
                if key in recorded:
                    # no number on a tile that plays a record; its mark is the overlay's red
                    self.assertFalse(any(c.isdigit() for c in x["text"]), x["text"])
                    if x["mark"]:
                        self.assertEqual(x["mark"], "✕")
                        self.assertEqual(x["markColor"], "rgb(200, 64, 42)")
            # the large box: the tile whose turn it is (the turns share tiles_s .. the end), larger
            n = len(sp["tests"])
            turn = (sp["duration_s"] - T["tiles_s"]) / n
            k = min(n - 1, int((t - T["tiles_s"]) // turn)) if t >= T["tiles_s"] else None
            shown = [b["box"] for b in got["bigs"] if b["shown"]]
            self.assertEqual(shown, [] if k is None else [f"n06.big.{sp['tests'][k]}"], t)
            if k is not None:
                big = next(b for b in got["bigs"] if b["shown"])
                f = box_of(sp["feature"])
                self.assertEqual([round(v) for v in big["rect"]], [f[0], f[1], f[0] + f[2], f[1] + f[3]], t)
                small = got["tiles"][k]
                self.assertGreater((big["rect"][2] - big["rect"][0]) * (big["rect"][3] - big["rect"][1]),
                                   4 * (small["rect"][2] - small["rect"][0]) * (small["rect"][3] - small["rect"][1]), t)

    def test_n06_lays_out_the_cases_past_the_limit(self):
        layout = n03_n06_layout()
        base = self.serve(layout, events={"20261103-120000-n06": n03_n06_events()})
        page, errors = self.open(f"{base}/cut.html?cut=n06&run=20261103-120000-n06&t=0")
        shots = {t: page.evaluate(READ, t) for t in N06_TIMES}
        self.assertEqual(errors, [])
        keys = layout["n06"]["tests"]
        self.check_n06(shots, layout, set(keys))
        last = {x["box"].split(".")[-1]: x for x in shots[11.6]["tiles"]}
        for k in keys:
            self.assertEqual(last[k]["miss"], "1", k)
            self.assertEqual(last[k]["mark"], "✕", k)
        # what happened, read from the records (words only)
        self.assertIn("フレームが許容を超える", last["step_break"]["text"])
        self.assertNotIn("越えられない", last["step_break"]["text"])  # its words are the still's
        self.assertIn("越えられない", last["step_stuck"]["text"])
        self.assertNotIn("フレーム", last["step_stuck"]["text"])  # its words are the motion's
        self.assertIn("倒れる", last["tilt_over"]["text"])
        self.assertIn("輪が浮く", last["tilt_over"]["text"])
        self.assertIn("電池が熱くなりすぎる", last["battery_hot"]["text"])
        self.assertIn("部品がぶつかる", last["parts_clash"]["text"])
        self.assertIn("片側の輪が浮く", last["rollover"]["text"])
        # where the limit is passed, in red: members over the allowable, a part over its
        # limit, a lifted wheel, the parts that interfere
        self.assertEqual(last["step_break"]["over"], "1")
        self.assertEqual(last["battery_hot"]["over"], "1")
        self.assertEqual((last["tilt_over"]["lift"], last["rollover"]["lift"]), ("1", "1"))
        self.assertEqual(last["parts_clash"]["clash"], "1")
        self.assertEqual(last["parts_clash"]["marks"], 2)  # the battery and the cross member, both in place
        # each record plays in the large box in its turn (from tile_in_s after the turn starts
        # to its end); in the list a tile holds its first pose until its turn is over, then its last
        T = layout["n06"]["times"]
        dur = layout["n06"]["duration_s"]
        turn = (dur - T["tiles_s"]) / len(keys)
        self.assertAlmostEqual(last["tilt_over"]["tau"], 8.0, places=6)  # its record: 0 .. 8 s
        self.assertAlmostEqual(last["parts_clash"]["tau"], 2.0, places=6)  # two steps
        self.assertEqual(last["parts_clash"]["marks"], 2)
        at = shots[3.9]  # tilt_over's turn (the second)
        big = next(b for b in at["bigs"] if b["shown"])
        self.assertEqual(big["box"], "n06.big.tilt_over")
        start = T["tiles_s"] + turn + T["tile_in_s"]
        self.assertAlmostEqual(big["tau"], 8.0 * (3.9 - start) / (T["tiles_s"] + 2 * turn - start), places=6)
        self.assertGreater(big["label"], 26)  # the large box's tag is larger than the list's
        early = {x["box"].split(".")[-1]: x for x in at["tiles"]}
        self.assertEqual(early["tilt_over"]["tau"], 0.0)  # in the list: its first pose during its turn
        self.assertEqual(early["parts_clash"]["tau"], 0.0)
        self.assertEqual(early["parts_clash"]["marks"], 1)  # the cross member only: the battery is not in yet
        page.close()

    def test_missing_layout_keys_are_named(self):
        layout = n03_n06_layout()
        del layout["n03"]["popup"]["title_px"]
        del layout["n03"]["times"]["accel_s"]
        del layout["n06"]["tile"]["label"]
        del layout["n06"]["overlay"]["opacity"]
        del layout["min_text_px"]
        base = self.serve(layout, events={"20261103-120000-n03": n03_n06_events()})
        page, errors = self.open(f"{base}/cut.html?cut=n03&run=20261103-120000-n03&t=1")
        err = page.evaluate("document.querySelector('.err')?.textContent ?? ''")
        self.assertIn("layout.json の n03 に無い: popup.title_px, times.accel_s", err)
        self.assertIn("layout.json に無い: min_text_px", err)
        self.assertEqual(page.evaluate("document.querySelectorAll('.n03-view').length"), 0)
        page.close()
        page, errors = self.open(f"{base}/cut.html?cut=n06&run=20261103-120000-n03&t=1")
        err = page.evaluate("document.querySelector('.err')?.textContent ?? ''")
        self.assertIn("layout.json の n06 に無い: tile.label.x, tile.label.y, tile.label.size_px, overlay.opacity", err)
        self.assertEqual(page.evaluate("document.querySelectorAll('.n06-tile').length"), 0)
        page.close()
        # a whole section missing
        layout = n03_n06_layout()
        del layout["n03"]
        base = self.serve(layout, events={"20261103-120000-n03": n03_n06_events()})
        page, errors = self.open(f"{base}/cut.html?cut=n03&run=20261103-120000-n03&t=1")
        self.assertIn("layout.json の n03 に無い: duration_s, view.x", page.evaluate("document.querySelector('.err')?.textContent ?? ''"))
        page.close()

    def test_on_the_real_tools_records(self):
        """With HIRAKU_WEB_DEV_RUNS: the newest run there; the same layout checks, every tile of a
        case past the limit marked from its own records."""
        given = os.environ.get("HIRAKU_WEB_DEV_RUNS")
        if not given:
            self.skipTest("needs HIRAKU_WEB_DEV_RUNS (a folder of runs made by the real tools)")
        runs = Path(given)
        run = sorted(p.name for p in runs.iterdir() if (p / "events.jsonl").is_file() and p.name[:1].isdigit())[-1]
        layout = n03_n06_layout()
        base = self.serve(layout, runs=runs)
        page, errors = self.open(f"{base}/cut.html?cut=n03&run={run}&t=0")
        shots = {t: page.evaluate(READ, t) for t in N03_TIMES}
        self.assertEqual(errors, [])
        self.check_n03(shots, layout, launch_case=page.evaluate("!!window.hiraku.cut.launchView"))
        page.close()
        page, errors = self.open(f"{base}/cut.html?cut=n06&run={run}&t=0")
        shots = {t: page.evaluate(READ, t) for t in N06_TIMES}
        self.assertEqual(errors, [])
        recorded = {x["box"].split(".")[-1] for x in shots[11.6]["tiles"] if "記録がまだ無い" not in x["text"]}
        self.check_n06(shots, layout, recorded)
        page.close()


if __name__ == "__main__":
    unittest.main()
