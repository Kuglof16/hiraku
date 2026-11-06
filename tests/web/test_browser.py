"""Open the screen in a headless browser and replay the contract sample.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed (it is not
in requirements.txt: it brings a browser, and three.js comes from the CDN, so
this test also needs the network).
"""

import copy
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku import contract
from hiraku.webserve.server import SAMPLE_EVENTS, make_server
from tests.web.domains_fixture import domain_events
from tests.web.k2_fixture import tilt_events
from tests.web.mesh_fixture import mesh_events
from tests.web.safe_side_fixture import safe_side_events

try:
    from tests.web.pages import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None


def mismatch_events():
    """The sample run up to the analysis, then a mismatch, its cause and a stopped run.

    Shape example: the analysed value is raised so the comparison fails; the
    differences are computed here from those values.
    """
    src = {e["seq"]: e for e in map(json.loads, SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines())}
    out = [copy.deepcopy(src[i]) for i in (0, 1, 6, 7, 8)]
    ana = out[4]["data"]
    ana["results"][0]["value"] = 180.0
    pred_v, ana_v, tol = 120.0, 180.0, 0.15
    cmp = {
        "comparison_id": "cmp-0001", "prediction_id": "pred-0001", "analysis_id": "ana-0001", "quantity": "max_stress",
        "predicted": {"value": pred_v, "unit": "MPa"}, "analyzed": {"value": ana_v, "unit": "MPa"},
        "diff_abs": ana_v - pred_v, "diff_rel": (ana_v - pred_v) / pred_v, "tolerance_rel": tol,
        "status": "mismatch", "cause_id": None,
    }
    cause = {"cause_id": "cause-0001", "comparison_id": "cmp-0001", "cause": "unit mix-up (shape example)", "fix": "redo the input", "resolved": False}
    base = {"run_id": out[0]["run_id"], "ts": "2026-11-02T20:30:10.000+09:00"}
    more = [
        {"kind": "comparison.recorded", "agent": "cae.strength", "domain": "strength", "design_version": 1, "data": cmp},
        {"kind": "hook.denied", "agent": "cae.strength", "domain": "strength", "text": "cause first (shape example)",
         "data": {"hook": "PreToolUse", "tool": "mcp__hiraku__analyze", "rule": "HK3_cause_before_continue"}},
        {"kind": "cause.recorded", "agent": "cae.strength", "domain": "strength", "design_version": 1, "data": cause},
        {"kind": "comparison.recorded", "agent": "cae.strength", "domain": "strength", "design_version": 1, "data": {**cmp, "cause_id": "cause-0001"}},
        {"kind": "hook.blocked_stop", "agent": "lead", "text": "not judged yet (shape example)",
         "data": {"hook": "Stop", "rule": "HK4_no_stop_before_judged", "count": 1, "limit": 3}},
        {"kind": "run.finished", "agent": "system", "data": {"status": "stopped", "stop_reason": "round_limit"}},
    ]
    t = out[-1]["t"]
    for ev in more:
        t += 0.5
        out.append({"seq": 0, "t": t, **base, **ev})
    for i, ev in enumerate(out):
        ev["seq"] = i
    return out


LONG_SPEECH = (
    "## 次にやること\n\n"
    "**R1 の食い違い**は、梁の式が後ろの張り出しを見ていないためです。\n"
    "- `frame_static.rail_step_bending` で張り出しの曲げを入れて予測し直す\n"
    "- そのあと `analyze` → `compare`\n\n"
    + "前の車軸から後ろの車軸までの曲げと、後ろに張り出した荷とモーターの重さによる曲げを足し合わせます。" * 6
)


def claude_like_events():
    """The sample run with what a Claude run brings: every role started (ten agents)
    and a long Markdown speech. Shape example: the words are made up."""
    src = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    out = [copy.deepcopy(e) for e in src[:4]]
    base = {"run_id": out[0]["run_id"], "ts": "2026-11-02T20:30:10.000+09:00"}
    roles = ["lead", "designer", "packager"] + [f"cae.{d}" for d in contract.DOMAINS if d != "assembly"]
    more = [{"kind": "agent.started", "agent": a, "data": {"agent_id": a}} for a in roles if a != "cae.strength"]
    more.append({"kind": "agent.said", "agent": "cae.strength", "domain": "strength", "text": LONG_SPEECH})
    t = out[-1]["t"]
    for ev in more:
        t += 0.5
        out.append({"seq": 0, "t": t, **base, **ev})
    for i, ev in enumerate(out):
        ev["seq"] = i
    return out


def n05_layout():
    """Layout rules in the shape the cut pages read (promo/screens/layout.json); the
    values are those of promo/screens/n05.html."""
    return {
        "stage": {"w": 1920, "h": 1080},
        "subtitle_band": {"y": 910, "h": 170},
        "min_text_px": 18,
        "colors": {"night": "#131311", "tile_bg": "#f7f4ec", "tile_border": "#3b3934"},
        # N02's pets: n00's camera and motion (as promo/screens/layout.json)
        "n00": {"camera": {"fov_deg": 24, "azimuth_deg": 24, "elevation_deg": 12}, "motion": {"pass_dur_s": 0.9, "hop_period_s": 0.9, "idle_shift_s": 1.3}},
        "n05": {
            "duration_s": 5.0,
            "grid": {"x": 96, "y": 280, "cols": 4, "rows": 3, "tile_w": 408, "tile_h": 180, "gap_x": 32, "gap_y": 26},
            "tile": {"border_px": 2, "radius_px": 10, "label": {"x": 16, "y": 12, "size_px": 26}, "rate": {"size_px": 18}},
            "hero": {"x": 210, "y": 60, "w": 1500, "h": 820},
            "hero_motion": {"push_scale": 1.04, "burst_scale": 1.7, "burst_dur_s": 0.4, "ease": [0.5, 0, 0.9, 0.4]},
            "times": {"burst_s": 0.95, "tiles_s": 1.05, "tile_in_s": 0.6, "tile_stagger_s": 0.05},
            "ease": {"tile": [0.2, 0.9, 0.3, 1.15]},
            "fly_from": {"x": 960, "y": 470},
            "tests": ["step", "tilt", "wind", "rough", "dlc", "brake", "long", "launch", "crash", "nvh", "fatigue", "heat"],
        },
        "n01": {
            "duration_s": 4.0,
            "view": {"x": 96, "y": 96, "w": 1400, "h": 790},
            "pane": {"x": 1520, "y": 96, "w": 304, "h": 790},
            "card": {"gap": 20, "pad": 20, "title_px": 30, "text_px": 22, "radius_px": 12, "border_px": 2},
            "times": {"lead_s": 0.4, "zoom_s": 1.6},
            "zoom": {"from": 1.6, "to": 1.06},
            "overlay": {"color": "#c8402a", "opacity": 0.22},
            "colors": {"bg": "#f4f1ea", "panel": "#fbf9f4", "rule": "#c9c3b6"},
        },
        "n02": {
            "duration_s": 7.0,
            "grid": {"x": 96, "y": 96, "cols": 3, "rows": 2, "tile_w": 560, "tile_h": 383, "gap_x": 24, "gap_y": 24},
            "panes": ["tilt", "wind", "rough", "dlc", "brake", "legend"],
            "pane": {"border_px": 2, "radius_px": 12, "label": {"x": 16, "y": 12, "size_px": 26}, "rate": {"size_px": 18}, "verdict": {"of_label": 2},
                     "pet": {"w": 96, "h": 96, "right": 12, "bottom": 12}},
            "times": {"lead_s": 0.3},
            "overlay": {"color": "#c8402a", "opacity": 0.22},
            "colors": {"bg": "#f4f1ea", "panel": "#fbf9f4", "rule": "#c9c3b6"},
        },
    }


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class BrowserReplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())  # runs/ is not made: the server has no runs of its own
        cls.server = make_server("127.0.0.1", 0, cls.tmp / "runs", quiet=True, saved_dir=None, launcher=False)
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

    def open(self, query):
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.goto(f"{self.base}/?{query}", wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=30000)
        page.evaluate("window.hiraku.ready")
        return page, errors

    def test_frozen_frame_at_the_end(self):
        page, errors = self.open("run=sample&pace=0&t=9")
        got = page.evaluate(
            """() => {
              const s = window.hiraku.state();
              return {
                playhead: window.hiraku.player.playhead,
                applied: window.hiraku.player.applied,
                comparisons: Object.keys(s.comparisons).length,
                judgement: s.judgements["1|strength"]?.state,
                hookLines: document.querySelectorAll("#log li.hook").length,
                cmpCard: document.getElementById("card-cmp").className,
                legendShown: !document.getElementById("legend").hidden,
                members: Object.keys(window.hiraku.bench.members).length,
              };
            }"""
        )
        self.assertEqual(errors, [])
        self.assertEqual(got["playhead"], 9)
        self.assertEqual(got["applied"], 15)
        self.assertEqual(got["comparisons"], 1)
        self.assertEqual(got["judgement"], "green")
        self.assertEqual(got["hookLines"], 1)
        self.assertIn("ok", got["cmpCard"])
        self.assertTrue(got["legendShown"])
        self.assertEqual(got["members"], 7)
        page.close()

    def test_seek_back_rebuilds_from_events(self):
        page, errors = self.open("run=sample&pace=0&t=9")
        got = page.evaluate(
            """async () => {
              await window.hiraku.seek(2.5);
              const s = window.hiraku.state();
              return { applied: window.hiraku.player.applied, preds: Object.keys(s.predictions).length,
                       denied: s.hooks.denied.length, legendShown: !document.getElementById("legend").hidden };
            }"""
        )
        self.assertEqual(errors, [])
        self.assertEqual(got, {"applied": 5, "preds": 0, "denied": 1, "legendShown": False})
        page.close()

    def test_mismatch_cause_and_resent_records(self):
        events = mismatch_events()
        for ev in events:
            self.assertEqual(contract.errors("event", ev), [])
        page, errors = self.open("run=sample&t=0")
        got = page.evaluate(
            """async (events) => {
              await window.hiraku.load(events, 100);
              const s = window.hiraku.state();
              return {
                causeId: s.comparisons["cmp-0001"].cause_id,
                cmpOrder: s.order.comparisons,
                cmpCard: document.getElementById("card-cmp").className,
                causeBox: document.querySelector("#card-cmp .cause")?.textContent ?? "",
                hookLines: document.querySelectorAll("#log li.hook").length,
                cmpLines: [...document.querySelectorAll("#log li")].filter((li) => li.textContent.includes("判定: 差")).map((li) => li.textContent),
                logLen: s.log.length,
                status: s.status,
                stopReason: s.stopReason,
                hooksRow: document.getElementById("hooks").textContent,
              };
            }""",
            events,
        )
        self.assertEqual(errors, [])
        self.assertEqual(got["causeId"], "cause-0001")
        self.assertEqual(got["cmpOrder"], ["cmp-0001"])
        self.assertIn("warn", got["cmpCard"])  # a mismatch: attention (orange), not red
        self.assertIn("unit mix-up", got["causeBox"])
        self.assertEqual(got["hookLines"], 2)
        # the comparison sent again with its cause_id rewrites its own line
        self.assertEqual(len(got["cmpLines"]), 1)
        self.assertIn("cause-0001", got["cmpLines"][0])
        self.assertIn("更新", got["cmpLines"][0])
        self.assertEqual(got["logLen"], len(events) - 1)  # every event but the re-sent one has a line
        self.assertEqual(got["status"], "stopped")
        self.assertEqual(got["stopReason"], "round_limit")
        self.assertIn("HK3 食い違いは原因が先", got["hooksRow"])
        self.assertIn("×1", got["hooksRow"])
        page.close()

    def test_safe_side_verdict(self):
        events = safe_side_events()
        for ev in events:
            self.assertEqual(contract.errors("event", ev), [])
        t_of = {(ev["kind"], ev["data"].get("comparison_id")): ev["t"] for ev in events}
        page, errors = self.open("run=sample&pace=0&t=0")
        first = page.evaluate(
            """async ([events, t]) => {
              await window.hiraku.load(events, t);
              return { pred: document.getElementById("card-pred").textContent,
                       cmp: document.getElementById("card-cmp").textContent,
                       cmpCard: document.getElementById("card-cmp").className };
            }""",
            [events, t_of[("comparison.recorded", "cmp-0001")]],
        )
        # two predictions went into the analysis; the card follows the one that was compared
        self.assertIn("最大応力", first["pred"])
        self.assertNotIn("最大変位", first["pred"])
        self.assertIn("warn", first["cmpCard"])
        self.assertIn("90", first["cmp"])
        end = page.evaluate(
            """async () => {
              await window.hiraku.seek(1e6);
              const card = document.getElementById("card-cmp");
              return {
                cmpCard: card.className,
                badge: card.querySelector(".badge").textContent,
                mark: card.querySelector(".safe-mark")?.textContent ?? "",
                why: card.querySelector(".safe-why")?.textContent ?? "",
                used: [...card.querySelectorAll("tr.used td")].map((td) => td.textContent),
                verdict: card.querySelector(".verdict").textContent,
                logLine: [...document.querySelectorAll("#log li")].map((li) => li.textContent).find((x) => x.includes("判定 (")) ?? "",
              };
            }"""
        )
        self.assertIn("warn", end["cmpCard"])  # the comparison stays a mismatch
        self.assertIn("食い違い", end["badge"])
        self.assertEqual(end["mark"], "安全側で判定")
        self.assertIn("one rail, so it stays above", end["why"])
        self.assertIn("予測", end["used"][0])
        self.assertIn("150", end["used"][1])
        self.assertIn("予測の値", end["verdict"])
        self.assertIn("→ OK", end["verdict"])
        self.assertIn("安全側で判定", end["logLine"])
        page.click("button[data-tab=grid]")
        tile = page.evaluate(
            """async () => {
              await window.hiraku.seek(window.hiraku.player.playhead);
              const t = document.querySelector('#tiles .tile[data-domain="strength"]');
              return { cls: t.className, safe: t.querySelector(".tb.safe")?.textContent ?? "", sum: t.querySelector(".sum").textContent };
            }"""
        )
        self.assertEqual(errors, [])
        self.assertIn("green", tile["cls"])
        self.assertEqual(tile["safe"], "安全側 1")
        self.assertIn("うち安全側 1 件", tile["sum"])
        page.close()

    def test_long_speech_and_many_agents(self):
        events = claude_like_events()
        for ev in events:
            self.assertEqual(contract.errors("event", ev), [])
        page, errors = self.open("run=sample&pace=0&t=0&agents=open")  # (one row per agent, the bundles open)
        got = page.evaluate(
            """async (events) => {
              await window.hiraku.load(events, events.at(-1).t + 0.5);
              const b = document.querySelector("#bubble .tx");
              const li = [...document.querySelectorAll("#log li.said .tx")].at(-1);
              const lineH = (el) => parseFloat(getComputedStyle(el).lineHeight);
              return {
                bubble: b.textContent,
                bubbleLines: Math.round(b.getBoundingClientRect().height / lineH(b) / (window.innerWidth / 1920)),
                logLines: Math.round(li.getBoundingClientRect().height / lineH(li) / (window.innerWidth / 1920)),
                fullInTitle: li.title.length,
                agents: document.querySelectorAll("#agents li:not(.agroup)").length,
                many: document.getElementById("agents").classList.contains("many"),
              };
            }""",
            events,
        )
        self.assertEqual(errors, [])
        self.assertNotIn("**", got["bubble"])
        self.assertNotIn("##", got["bubble"])
        self.assertNotIn("`", got["bubble"])
        self.assertIn("・frame_static.rail_step_bending", got["bubble"])
        self.assertLessEqual(got["bubbleLines"], 7)
        self.assertLessEqual(got["logLines"], 5)
        self.assertGreater(got["fullInTitle"], 300)  # the whole speech stays on the line (as its title)
        self.assertEqual(got["agents"], 10)
        self.assertTrue(got["many"])
        page.close()

    def test_step_scene_from_the_reaction_on_its_corner(self):
        events = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
        ana = next(e for e in events if e["kind"] == "analysis.finished")["data"]
        # shape example: the front wheel pushes on the step's corner, 223.6 mm ahead at the step's height
        ana["vectors"] = [{"kind": "reaction", "at": "FL", "origin_mm": [223.6, 650.0, 100.0], "vector_N": [-1100.0, 0.0, 1000.0]}]
        page, errors = self.open("run=sample&pace=0&t=0")
        got = page.evaluate(
            """async (events) => {
              await window.hiraku.load(events, 1e6);
              const b = window.hiraku.bench;
              return { step: b.sceneDesc.step, view: b.view, props: b.props.world.children.length };
            }""",
            events,
        )
        self.assertEqual(errors, [])
        self.assertEqual(got["step"]["h"], 100.0)  # params.step_height
        self.assertAlmostEqual(got["step"]["x0"], 223.6)  # the corner the reaction pushes on
        self.assertEqual(got["view"], "side")
        self.assertGreater(got["props"], 0)
        page.close()

    def test_tilt_table_turns_the_world(self):
        events, info = tilt_events()
        page, errors = self.open("run=sample&pace=0&t=0")
        got = page.evaluate(
            """async (events) => {
              await window.hiraku.load(events, 1e6);
              const b = window.hiraku.bench;
              return { table: b.sceneDesc.table, frameTable: b.frame?.table ?? null, world: b.world.rotation.x,
                       fixed: b.props.fixed.children.length };
            }""",
            events,
        )
        self.assertEqual(errors, [])
        self.assertTrue(got["table"])
        self.assertAlmostEqual(got["world"], got["frameTable"])
        self.assertGreater(got["fixed"], 0)  # the post and floor that stay level
        page.close()

    def test_real_tools_scenes_in_both_looks(self):
        """The real K2 step tool's record on the screen. The run is built here when this Python
        has the solvers; else HIRAKU_WEB_DEV_RUNS names a folder made by tests.web.dev_k2_run."""
        given = os.environ.get("HIRAKU_WEB_DEV_RUNS")
        tmp = None
        if given:
            runs = Path(given)
            run = sorted(p for p in runs.iterdir() if p.is_dir())[-1]
        else:
            try:
                from tests.web.dev_k2_run import build

                runs = tmp = Path(tempfile.mkdtemp())
                run = build(tmp, ["P1-step100", "S4-brake"], log=lambda *_: None)
            except ImportError as e:
                if tmp:
                    shutil.rmtree(tmp)
                self.skipTest(f"the solvers do not import in this Python ({e}); set HIRAKU_WEB_DEV_RUNS")
        server = make_server("127.0.0.1", 0, runs, quiet=True, saved_dir=None, launcher=False)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            for look in ("zu", "real"):
                page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(f"{base}/?run={run.name}&pace=0&t=1e6&look={look}", wait_until="domcontentloaded")
                page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=30000)
                page.evaluate("window.hiraku.ready")
                page.click('#tests button[data-lc="P1-step100"]')
                got = page.evaluate(
                    """async () => {
                      await window.hiraku.seek(window.hiraku.player.playhead);
                      const b = window.hiraku.bench;
                      // the step from the tool's own scene, or read off the contact points
                      const rec = (b.sceneDesc?.objects ?? []).find((o) => o.kind === "step");
                      const step = rec ? { h: rec.size_mm[2], x0: rec.position_mm[0] - rec.size_mm[0] / 2 } : b.sceneDesc?.step ?? null;
                      return { step, shadows: b.renderer.shadowMap.enabled,
                               limitButton: !!document.querySelector('#toggles [data-toggle="limit"]'),
                               standard: b.members[Object.keys(b.members)[0]].mesh.material.type };
                    }"""
                )
                self.assertEqual(errors, [], look)
                self.assertEqual(got["step"]["h"], 100.0)
                self.assertGreater(got["step"]["x0"], 0.0)  # the step stands ahead of the front axle (x = 0)
                self.assertFalse(got["limitButton"])  # climbing lifts a wheel: not a limit on a step
                self.assertEqual(got["shadows"], look == "real")
                # the real look's painted steel tubes have a clearcoat (look.js MATERIAL_OF)
                self.assertEqual(got["standard"], "MeshPhysicalMaterial" if look == "real" else "MeshLambertMaterial")
                page.close()
        finally:
            server.shutdown()
            server.server_close()
            if tmp:
                shutil.rmtree(tmp)

    def test_scene_objects_and_stop_code_from_the_record(self):
        events = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
        ana = next(e for e in events if e["kind"] == "analysis.finished")["data"]
        # shape example of a tool's scene: a step ahead, a cone, the road sloping 10 deg to the right
        ana["scene"] = [
            {"kind": "step", "position_mm": [900.0, 0.0, 50.0], "size_mm": [600.0, 2000.0, 100.0]},
            {"kind": "cone", "position_mm": [2000.0, 1500.0, 350.0], "size_mm": [300.0, 300.0, 700.0]},
            {"kind": "slope", "rpy_deg": [10.0, 0.0, 0.0]},
        ]
        end = next(e for e in events if e["kind"] == "run.finished")
        end["data"] = {"status": "stopped", "stop_code": "turn_limit", "stop_reason": "turn limit reached"}
        for ev in events:
            self.assertEqual(contract.errors("event", ev), [])
        page, errors = self.open("run=sample&pace=0&t=0")
        got = page.evaluate(
            """async (events) => {
              await window.hiraku.load(events, 1e6);
              const b = window.hiraku.bench;
              return { recorded: b.sceneDesc.recorded, kinds: b.sceneDesc.objects.map((o) => o.kind), view: b.view,
                       world: b.world.rotation.x,
                       end: [...document.querySelectorAll("#log li")].map((li) => li.textContent).find((x) => x.includes("実行が終わった")) };
            }""",
            events,
        )
        self.assertEqual(errors, [])
        self.assertTrue(got["recorded"])
        self.assertEqual(got["kinds"], ["step", "cone"])
        self.assertEqual(got["view"], "front")  # cones mark a course, seen from the front
        self.assertAlmostEqual(got["world"], 10 * 3.141592653589793 / 180)
        self.assertIn("ターン数の上限に達した", got["end"])
        page.close()

    def test_start_a_run_from_the_screen(self):
        from hiraku.webserve.launch import Launcher
        from tests.web.fake_launch import fake_command, fake_reader

        tmp = Path(tempfile.mkdtemp())
        (tmp / "requirements").mkdir()
        (tmp / "requirements" / "offroad-4w.yaml").write_text("product: offroad-4w\n", encoding="utf-8")
        run_id = "20261103-120000-offroad-4w-fake"
        lau = Launcher(tmp / "runs", tmp / "requirements", env={"ANTHROPIC_API_KEY": "test-value-not-a-real-key"},
                       command=fake_command(SAMPLE_EVENTS, run_id, 2.0), reader=fake_reader, wait_s=20,
                       demo_runs_file=tmp / "no-demo-runs.yaml")
        server = make_server("127.0.0.1", 0, tmp / "runs", quiet=True, saved_dir=None, launcher=lau)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{base}/?list=1&dev=1", wait_until="domcontentloaded")
            page.wait_for_selector("#launch .lreq")
            ask = page.locator("#launch button[data-launch-ask]")
            self.assertIn("$2", ask.text_content())  # the cost limit shows before anything starts
            ask.click()
            page.wait_for_selector("#launch .confirm")
            self.assertIn("費用の上限は $2", page.text_content("#launch .confirm"))
            page.click("#launch button[data-launch-cancel]")
            page.wait_for_selector("#launch button[data-launch-ask]")
            self.assertFalse(lau.started)  # nothing ran yet
            page.click('#launch button[data-launch="scripted"]')
            page.wait_for_url(f"**/?run={run_id}&dev=1", timeout=30000)
            page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=30000)
            page.evaluate("window.hiraku.ready")
            self.assertGreater(page.evaluate("window.hiraku.player.events.length"), 0)
            self.assertEqual([s["mode"] for s in lau.started], ["scripted"])
            self.assertEqual(errors, [])
            page.close()
        finally:
            server.shutdown()
            server.server_close()
            if lau.proc and lau.proc.poll() is None:
                lau.proc.kill()
                lau.proc.wait()
            shutil.rmtree(tmp)

    def _demo_server(self, env):
        """A launcher on a requirements folder and a demo runs list: file rows under the limit (N12),
        without an estimate and Claude only (N13), over the limit (demo); a request row (N09).
        (fake_reader gives every file a limit of $2.)"""
        from hiraku.webserve.launch import Launcher
        from tests.web.fake_launch import fake_command, fake_reader

        tmp = Path(tempfile.mkdtemp())
        req = tmp / "requirements"
        (req / "products").mkdir(parents=True)
        for name in ("offroad-4w.yaml", "offroad-4w-n12.yaml", "offroad-4w-n13.claude.yaml"):
            (req / name).write_text("product: offroad-4w\n", encoding="utf-8")
        (req / "products" / "care-bed.yaml").write_text("product: care-bed\nwant: 在宅で使う電動の介護ベッドを作りたい\n", encoding="utf-8")
        demo = tmp / "catalog" / "demo_runs.yaml"
        demo.parent.mkdir()
        demo.write_text(
            "runs:\n"
            "  - id: n12\n    title: 要件の間違い\n    shows: 要件の誤りを見つけて直す\n    file: offroad-4w-n12.yaml\n"
            "    modes: [claude, scripted]\n    estimate_usd: 1.1\n    cuts: [N11, N12]\n"
            "  - id: n13\n    title: ツールを作る\n    shows: 棚に無いツールを書く\n    file: offroad-4w-n13.claude.yaml\n"
            "    modes: [claude]\n    estimate_usd: null\n    cuts: [N13]\n"
            "  - id: demo\n    title: デモの車\n    shows: 6 つの試験\n    file: offroad-4w.yaml\n    modes: [scripted, claude]\n    estimate_usd: 12.5\n"
            "  - id: n09\n    title: 依頼から始める\n    shows: 依頼の文から分野を選ぶ\n    request: {product: care-bed}\n    modes: [claude]\n    cuts: [N09]\n",
            encoding="utf-8")
        run_id = "20261103-120000-offroad-4w-fake"
        lau = Launcher(tmp / "runs", req, env=env, command=fake_command(SAMPLE_EVENTS, run_id, 2.0), reader=fake_reader, wait_s=20,
                       demo_runs_file=demo)
        server = make_server("127.0.0.1", 0, tmp / "runs", quiet=True, saved_dir=None, launcher=lau)
        threading.Thread(target=server.serve_forever, daemon=True).start()

        def close():
            server.shutdown()
            server.server_close()
            if lau.proc and lau.proc.poll() is None:
                lau.proc.kill()
                lau.proc.wait()
            shutil.rmtree(tmp, ignore_errors=True)

        return f"http://127.0.0.1:{server.server_address[1]}", lau, run_id, close

    def test_demo_runs_on_the_run_list(self):
        """デモの実行 sits first in the launch box, in demo_runs.yaml's order: a file row runs
        through the same Claude confirmation, a request row opens A1 with the product's want in."""
        base, lau, run_id, close = self._demo_server({"ANTHROPIC_API_KEY": "test-value-not-a-real-key"})
        try:
            page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{base}/?list=1&dev=1", wait_until="domcontentloaded")
            page.wait_for_selector("#launch .demo-runs")
            got = page.evaluate("""() => ({
                first: document.querySelector('#launch h3').textContent,
                ids: [...document.querySelectorAll('#launch .lreq.demo')].map((e) => e.dataset.demo),
                text: document.querySelector('#launch .demo-runs').textContent,
                files: [...document.querySelectorAll('#launch .lreq:not(.demo) .rid')].map((e) => e.textContent),
            })""")
            self.assertEqual(got["first"], "デモの実行")
            self.assertEqual(got["ids"], ["n12", "n13", "demo", "n09"])
            for words in ("要件の間違い", "要件の誤りを見つけて直す", "N11", "N12", "依頼から始める", "N09", "在宅で使う電動の介護ベッドを作りたい"):
                self.assertIn(words, got["text"])
            # the files below stay as they were
            self.assertEqual(got["files"], ["offroad-4w-n12.yaml", "offroad-4w-n13.claude.yaml", "offroad-4w.yaml"])
            self.assertEqual(page.locator("#launch .lreq.demo[data-demo='n12'] a.demo-a1").count(), 0)  # a file row runs, not A1
            # the estimate next to the limit, as written; over the limit only on the demo row
            row = lambda i: f"#launch .lreq.demo[data-demo='{i}']"
            self.assertIn("見込み $1.10・上限 $2.00", page.text_content(f"{row('n12')} .cost"))
            self.assertIn("見込み —・上限 $2.00", page.text_content(f"{row('n13')} .cost"))
            self.assertIn("見込み $12.50・上限 $2.00", page.text_content(f"{row('demo')} .cost"))
            self.assertEqual([page.locator(f"{row(i)} .lbtns .over").count() for i in ("n12", "n13", "demo")], [0, 0, 1])
            self.assertIn("見込みが上限を超えるので、上限 $2.00 で止まる", page.text_content(f"{row('demo')} .lbtns .over"))
            # modes: N13 is Claude only, here and in the files below
            self.assertEqual(page.locator(f"{row('n13')} button[data-launch='scripted']").count(), 0)
            self.assertEqual(page.locator(f"{row('n13')} button[data-launch-ask]").count(), 1)
            n13_file = page.locator("#launch .lreq:not(.demo)", has_text="offroad-4w-n13.claude.yaml")
            self.assertEqual(n13_file.locator("button[data-launch='scripted']").count(), 0)
            ask = page.locator(f"{row('n12')} button[data-launch-ask]")
            self.assertIn("見込み $1.10・上限 $2.00", ask.text_content())
            # over the limit: it can be started, and the confirmation says where it stops
            page.click(f"{row('demo')} button[data-launch-ask]")
            page.wait_for_selector(f"{row('demo')} .confirm")
            confirm = page.text_content(f"{row('demo')} .confirm")
            self.assertIn("見込みは $12.50", confirm)
            self.assertIn("見込みが上限を超えるので、上限 $2.00 で止まります", confirm)
            page.click("#launch button[data-launch-cancel]")
            ask.click()
            page.wait_for_selector(f"{row('n12')} .confirm")
            self.assertIn("費用の上限は $2", page.text_content("#launch .confirm"))
            self.assertNotIn("超える", page.text_content("#launch .confirm"))
            self.assertEqual(page.locator("#launch .confirm").count(), 1)  # only the demo row asks
            page.click("#launch button[data-launch-cancel]")
            page.wait_for_selector("#launch .lreq.demo[data-demo='n12'] button[data-launch-ask]")
            self.assertFalse(lau.started)  # nothing ran yet
            # the request row: A1 opens with the want written in, and nothing is sent
            posted = []
            page.on("request", lambda r: posted.append(r.url) if r.method == "POST" else None)
            page.click("#launch .lreq.demo[data-demo='n09'] a.demo-a1")
            page.wait_for_url("**/?new=1&demo=n09&dev=1")
            page.wait_for_function("document.querySelector('#view-intake textarea')?.value.length > 0")
            self.assertEqual(page.input_value("#view-intake textarea"), "在宅で使う電動の介護ベッドを作りたい")
            self.assertIn("依頼から始める", page.text_content("#draftbar"))
            self.assertIsNone(page.evaluate("window.hiraku.request.state.state"))
            self.assertEqual(posted, [])
            self.assertFalse(lau.started)
            # the file row's Claude run, confirmed, starts on its file
            page.goto(f"{base}/?list=1&dev=1", wait_until="domcontentloaded")
            page.click("#launch .lreq.demo[data-demo='n12'] button[data-launch-ask]")
            page.click("#launch .lreq.demo[data-demo='n12'] button[data-launch='claude']")
            page.wait_for_url(f"**/?run={run_id}-claude&dev=1", timeout=30000)
            self.assertEqual([(s["mode"], s["requirements"]) for s in lau.started], [("claude", "offroad-4w-n12.yaml")])
            self.assertEqual(errors, [])
            page.close()
        finally:
            close()

    def test_demo_runs_without_the_key(self):
        base, lau, _, close = self._demo_server({})
        try:
            page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{base}/?list=1&dev=1", wait_until="domcontentloaded")
            page.wait_for_selector("#launch .demo-runs")
            row = "#launch .lreq.demo[data-demo='n12']"
            self.assertTrue(page.is_disabled(f"{row} button:has-text('Claude で走らせる')"))
            self.assertIn("ANTHROPIC_API_KEY", page.text_content(f"{row} .why"))
            self.assertEqual(page.locator(f"{row} button[data-launch-ask]").count(), 0)
            self.assertFalse(page.is_disabled(f"{row} button[data-launch='scripted']"))
            self.assertEqual(page.locator("#launch .lreq.demo[data-demo='n09'] a.demo-a1").count(), 1)
            self.assertIn("上限 $2.00 で止まる", page.text_content("#launch .lreq.demo[data-demo='demo'] .lbtns .over"))
            self.assertEqual(errors, [])
            page.close()
        finally:
            close()

    DEV_WORDS = ("products/", ".yaml", "ANTHROPIC_API_KEY", "A1", "N0")
    NEEDS_KEY = "Claude のキーが要ります"

    def test_run_list_for_people_without_the_key(self):
        """No ?dev=1: no files, key names, intake tab names or cut numbers on the list; no list of the
        ways and no README link — one short word beside each 実行する that cannot be pressed; ?dev=1
        shows them all."""
        base, lau, _, close = self._demo_server({})
        try:
            page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{base}/?list=1", wait_until="domcontentloaded")
            page.wait_for_selector("#launch .demo-runs")
            text = page.inner_text("#runlist")
            for w in self.DEV_WORDS:
                self.assertNotIn(w, text)
            self.assertEqual(page.locator("#runlist .keyless, #runlist ul.ways, #runlist a.readme").count(), 0)
            for w in ("README", "動かし方", "(無料・すぐ)", "記録を再生する", "はじめから頼みます"):
                self.assertNotIn(w, text)
            row = lambda i: f"#launch .lreq.demo[data-demo='{i}']"
            # one line per row: an id without a line of its own shows its title; one verb per button
            self.assertIn("要件の間違い", page.inner_text(row("n12")))
            self.assertTrue(page.is_disabled(f"{row('n12')} button.claude"))
            # beside each 実行する that cannot be pressed: one short word, nothing else about the key
            disabled = page.locator("#launch button.claude:disabled").count()
            self.assertGreater(disabled, 0)
            self.assertEqual(page.locator("#launch button.claude:disabled + .needs-key").count(), disabled)
            self.assertEqual(text.count(self.NEEDS_KEY), disabled)
            self.assertEqual(page.locator("#launch button[data-launch-ask]").count(), 0)
            self.assertEqual([page.text_content(f"{row('n12')} button[data-launch='scripted']"), page.text_content(f"{row('n09')} a.demo-a1")], ["再現する", "試す"])
            self.assertEqual(page.locator("#launch .pick").count(), 0)  # no saved run, no demo row `video`
            # くわしく opens the developers' lines of that row
            page.click(f"{row('n12')} details.more summary")
            more = page.inner_text(f"{row('n12')} details.more")
            for w in ("offroad-4w-n12.yaml", "N11", "要件の誤りを見つけて直す", "上限 $2.00"):
                self.assertIn(w, more)
            # ?dev=1: all there (the demo rows' files and tags inside their closed くわしく: the text, not
            # only what is drawn)
            page.goto(f"{base}/?list=1&dev=1", wait_until="domcontentloaded")
            page.wait_for_selector("#launch .demo-runs")
            text = page.text_content("#runlist")
            for w in self.DEV_WORDS:
                self.assertIn(w, text)
            self.assertEqual(page.locator("#runlist .keyless").count(), 1)
            self.assertNotIn(self.NEEDS_KEY, text)
            self.assertEqual(errors, [])
            page.close()
        finally:
            close()

    def test_run_list_for_people_with_the_key(self):
        """With the key: no line about it, 実行する asks with the cost limit first, 試す opens A1 without ?dev=1."""
        base, lau, run_id, close = self._demo_server({"ANTHROPIC_API_KEY": "test-value-not-a-real-key"})
        try:
            page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{base}/?list=1", wait_until="domcontentloaded")
            page.wait_for_selector("#launch .demo-runs")
            text = page.inner_text("#runlist")
            self.assertEqual(page.locator("#runlist .keyless, #runlist .needs-key").count(), 0)
            for w in self.DEV_WORDS:
                self.assertNotIn(w, text)
            row = lambda i: f"#launch .lreq.demo[data-demo='{i}']"
            page.click(f"{row('demo')} button[data-launch-ask]")
            page.wait_for_selector(f"{row('demo')} .confirm")
            confirm = page.text_content(f"{row('demo')} .confirm")
            for w in ("見込みは $12.50", "費用の上限は $2.00", "途中で止まります"):
                self.assertIn(w, confirm)
            page.click("#launch button[data-launch-cancel]")
            page.wait_for_selector(f"{row('demo')} button[data-launch-ask]")
            self.assertFalse(lau.started)
            page.click(f"{row('n09')} a.demo-a1")
            page.wait_for_url("**/?new=1&demo=n09")
            page.goto(f"{base}/?list=1", wait_until="domcontentloaded")
            page.click(f"{row('n12')} button[data-launch-ask]")
            page.click(f"{row('n12')} button[data-launch='claude']")
            page.wait_for_url(f"**/?run={run_id}-claude", timeout=30000)
            self.assertEqual([(s["mode"], s["requirements"]) for s in lau.started], [("claude", "offroad-4w-n12.yaml")])
            self.assertEqual(errors, [])
            page.close()
        finally:
            close()

    def test_project_updated_and_report_written(self):
        events = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
        project = copy.deepcopy(events[0]["data"])
        extra = {**copy.deepcopy(project["load_cases"][0]), "load_case_id": "R1-step150", "title": "段差 150 mm (形の見本)"}
        extra["params"] = {**extra["params"], "step_height": {"value": 150.0, "unit": "mm"}}
        project["load_cases"].append(extra)
        report = {"design_version": 1, "domains": [{"domain": "strength", "state": "green", "line": "段差で降伏しない (形の見本)"}],
                  "unchecked": [{"item": "yield 235 MPa", "source": "materials (形の見本)"}],
                  "next": [{"domain": "strength", "change": "段差 150 mm も確かめる", "reason": "形の見本"}], "summary": "強度は合格 (形の見本)"}
        base = {"run_id": events[0]["run_id"], "ts": "2026-11-02T20:30:10.000+09:00"}
        updated = {**base, "seq": 0, "t": 0.7, "kind": "project.updated", "agent": "designer", "data": project}
        written = {**base, "seq": 0, "t": events[-1]["t"] - 0.1, "kind": "report.written", "agent": "lead", "data": report}
        events = events[:2] + [updated] + events[2:-1] + [written, events[-1]]
        for i, ev in enumerate(events):
            ev["seq"] = i
            self.assertEqual(contract.errors("event", ev), [], ev["kind"])
        page, errors = self.open("run=sample&pace=0&t=0")
        got = page.evaluate(
            """async (events) => {
              await window.hiraku.load(events, 1e6);
              const s = window.hiraku.state();
              return { cases: s.project.load_cases.map((c) => c.load_case_id), tests: document.querySelectorAll("#tests button.test").length,
                       report: s.reports[1]?.summary ?? null,
                       lines: [...document.querySelectorAll("#log li")].map((li) => li.textContent).filter((x) => x.includes("試験と分野を決めた") || x.includes("報告を書いた")) };
            }""",
            events,
        )
        self.assertEqual(errors, [])
        self.assertEqual(got["cases"], ["R1-step100", "R1-step150"])
        self.assertEqual(got["tests"], 2)
        self.assertEqual(got["report"], "強度は合格 (形の見本)")
        self.assertEqual(len(got["lines"]), 2)
        page.close()

    @unittest.skipUnless("mesh.created" in contract.EVENT_KINDS, "contract without meshes")
    def test_mesh_created_is_kept(self):
        """mesh.created (the contract's example) lands in state.meshes by mesh_id with one log line."""
        events = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
        mesh = json.loads((contract.EXAMPLES_DIR / "mesh.json").read_text(encoding="utf-8"))
        ev = {"run_id": events[0]["run_id"], "ts": "2026-11-02T20:30:10.000+09:00", "seq": 0, "t": events[1]["t"] + 0.05,
              "kind": "mesh.created", "agent": "system", "design_version": 1, "data": mesh}
        events = events[:2] + [ev] + events[2:]
        for i, e in enumerate(events):
            e["seq"] = i
            self.assertEqual(contract.errors("event", e), [], e["kind"])
        page, errors = self.open("run=sample&pace=0&t=0")
        got = page.evaluate(
            """async (events) => {
              await window.hiraku.load(events, 1e6);
              const s = window.hiraku.state();
              return { ids: s.order.meshes, elements: s.meshes["mesh-0001"].elements.length,
                       lines: [...document.querySelectorAll("#log li")].map((li) => li.textContent).filter((x) => x.includes("メッシュを作った")) };
            }""",
            events,
        )
        self.assertEqual(errors, [])
        self.assertEqual(got["ids"], ["mesh-0001"])
        self.assertEqual(got["elements"], len(mesh["elements"]))
        self.assertEqual(len(got["lines"]), 1)
        self.assertIn(f"節 {len(mesh['nodes_mm'])}・要素 {len(mesh['elements'])}", got["lines"][0])
        page.close()

    def test_field_frames_follow_the_motion(self):
        """A field with frames (the same field at moments of the motion that gave its loads) is
        coloured by the moment nearest to the motion's time, on one scale for every moment, and
        the legend names the moment."""
        events, _ = tilt_events()
        ana = next(e for e in events if e["kind"] == "analysis.finished" and e["data"].get("series"))["data"]
        design = next(e for e in events if e["kind"] == "design.created")["data"]
        members = [m["id"] for m in design["members"]]
        t = [s for s in ana["series"] if s.get("t_s")][0]["t_s"]
        moments = [t[0], t[len(t) // 2], t[-1]]
        # shape example: the stress grows over the moments, not a result
        per = [{m: 10.0 * (k + 1) + i for i, m in enumerate(members)} for k in range(3)]
        ana["fields"] = [{"kind": "stress", "label": "応力 (形の見本)", "unit": "MPa",
                          "frames": {"t_s": moments, "per_member": per, "max": [max(p.values()) for p in per], "source_analysis_id": ana["analysis_id"]}}]
        page, errors = self.open("run=sample&pace=0&t=0")
        page.evaluate("async (events) => { await window.hiraku.load(events, 1e6); }", events)
        page.wait_for_function("document.getElementById('legend-t')?.textContent")
        got = page.evaluate("""() => { const c = window.hiraku.bench.keys.contour;
            return { k: c.frameIndex, max: c.max, min: c.min, top: c.frameMax, t: document.getElementById('legend-t').textContent }; }""")
        self.assertEqual(errors, [])
        self.assertEqual(got["k"], 2)  # the replay is at the end: the last moment
        self.assertEqual(got["top"], max(per[2].values()))
        self.assertEqual(got["max"], max(per[2].values()))  # the scale holds every moment
        self.assertEqual(got["min"], 0)
        self.assertIn(f"t = ", got["t"])
        page.close()

    def test_mesh_convergence_line(self):
        """analysis.mesh_convergence is one line under the analysis' results: the change between the
        two finest meshes against its limit, or, when singular, that the value is not judged."""
        events = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
        ana = next(e for e in events if e["kind"] == "analysis.finished")["data"]
        # shape example: three mesh sizes, not a result
        ana["mesh_convergence"] = {"quantity": "max_stress", "unit": "MPa", "verdict": "converged", "change_pct": 2.0, "limit_pct": 5.0,
                                   "levels": [{"element_size_mm": 20, "value": 118.0}, {"element_size_mm": 10, "value": 121.0}, {"element_size_mm": 5, "value": 123.5}]}
        page, errors = self.open("run=sample&pace=0&t=0")
        page.evaluate("async (events) => { await window.hiraku.load(events, 1e6); }", events)
        line = page.text_content("#card-ana .conv")
        self.assertEqual(line, "メッシュ 10 → 5 mm で 2 % (限度 5 %): 収まった")
        # with three meshes, the Richardson extrapolated value when the record gives it
        ana["mesh_convergence"].update({"observed_order": 1.3, "extrapolated": 125.2, "error_pct": 1.4})
        page.evaluate("async (events) => { await window.hiraku.load(events, 1e6); }", events)
        self.assertEqual(page.text_content("#card-ana .conv"), "メッシュ 10 → 5 mm で 2 % (限度 5 %): 収まった・外挿 125.2 MPa")
        ana["mesh_convergence"]["verdict"] = "singular"
        page.evaluate("async (events) => { await window.hiraku.load(events, 1e6); }", events)
        self.assertIn("細かくするほど上がる (特異点): この値では判定しない", page.text_content("#card-ana .conv"))
        self.assertEqual(errors, [])
        page.close()

    def test_joint_vectors_are_drawn(self):
        """A force at a suspension mounting point (vector kind joint, at "<support>.<point>")
        is drawn with its own colour and name, beside the reactions."""
        events = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
        ana = next(e for e in events if e["kind"] == "analysis.finished")["data"]
        # shape example: one mounting point's force, not a result
        ana["vectors"] = list(ana.get("vectors") or []) + [{"kind": "joint", "at": "FL.lca_front", "origin_mm": [-150.0, 300.0, 330.0], "vector_N": [0.0, -800.0, 1200.0]}]
        page, errors = self.open("run=sample&pace=0&t=0")
        page.evaluate("async (events) => { await window.hiraku.load(events, 1e6); }", events)
        tags = page.evaluate("[...document.querySelectorAll('.lbl-vec')].map((x) => x.textContent)")
        self.assertEqual(errors, [])
        self.assertTrue(any(t.startswith("取付点の力 FL.lca_front") for t in tags), tags)
        page.close()

    def test_a_part_limit_holds_only_its_part(self):
        """Part temperatures against T1's criterion (the battery's limit, recorded at the battery):
        only the battery is held to it and the scale is the field's own range."""
        page, errors = self.open("run=sample&pace=0&t=0")
        got = page.evaluate(
            """async () => {
              const { contourOf, overOf } = await import("/js/fields.js");
              // shape example: three parts, a criterion on the battery's highest temperature
              const ana = { analysis_id: "ana-0001", status: "ok",
                results: [{ quantity: "temp_battery_max", value: 48.4, unit: "degC", where: "battery" }],
                fields: [{ kind: "temperature", unit: "degC", per_part: { battery: 48.4, motor: 69.9, controller: 57.9 } }] };
              const crit = (v) => ({ quantity: "temp_battery_max", op: "<=", limit: { value: v, unit: "degC" }, basis: "shape example" });
              const ct = contourOf(ana, ana.fields[0], crit(55));
              const hot = contourOf(ana, ana.fields[0], crit(45));
              const lost = contourOf({ ...ana, results: [] }, ana.fields[0], crit(55));
              return { max: ct.max, limitKnown: ct.limitKnown, part: ct.partLimit?.part,
                       over: ["battery", "motor", "controller"].map((id) => overOf(ct, id, ct.parts[id])),
                       hot: ["battery", "motor"].map((id) => overOf(hot, id, hot.parts[id])),
                       lost: [lost.partLimit, lost.limitKnown, overOf(lost, "motor", 69.9)] };
            }"""
        )
        self.assertEqual(errors, [])
        self.assertEqual((got["max"], got["limitKnown"], got["part"]), (69.9, False, "battery"))
        self.assertEqual(got["over"], [False, False, False])  # the motor at 69.9 is not held to the battery's 55
        self.assertEqual(got["hot"], [True, False])
        self.assertEqual(got["lost"], [None, False, False])  # no recorded part for the limit: no part held to it
        page.close()

    def test_a_new_request_from_A1_to_a_run(self):
        """?new=1: write the request in A1 and send it, answer the hearing, then 決まった手順で始める starts a
        run on the saved request (with its intake events) and the screen follows it."""
        from hiraku.webserve.launch import Launcher
        from tests.web.fake_launch import fake_command, fake_reader

        tmp = Path(tempfile.mkdtemp())
        (tmp / "requirements").mkdir()
        run_id = "20261103-120000-offroad-4w-fake"
        seen = []
        fake = fake_command(SAMPLE_EVENTS, run_id, 20.0)

        def command(req, mode, runs, intake_events=None):
            seen.append((Path(req).name, intake_events and Path(intake_events).name, mode))
            return fake(req, mode, runs)

        lau = Launcher(tmp / "runs", tmp / "requirements", env={}, command=command, reader=fake_reader, wait_s=20,
                       demo_runs_file=tmp / "no-demo-runs.yaml")
        server = make_server("127.0.0.1", 0, tmp / "runs", quiet=True, saved_dir=None, launcher=lau)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{base}/?new=1", wait_until="domcontentloaded")
            page.wait_for_selector("#draftbar")
            self.assertIn("作りたいものを書いて", page.text_content("#draftbar"))
            page.fill("#view-intake textarea", "林道を走れる 2 人乗りの小さな車両")
            page.click("#view-intake button:has-text('送る')")
            page.wait_for_function("window.hiraku.request.state.state && !window.hiraku.request.state.busy")
            self.assertGreater(page.evaluate("window.hiraku.request.state.events.length"), 0)
            # the domains are picked: A1's question box has 答えて進める / 標準の値のまま進める, so the bar's
            # 質問に答える / 標準の値のまま進める are not shown twice; on A5 (no box) the bar has them
            page.wait_for_selector("#intake-host .in-ask-top")
            self.assertTrue(page.evaluate("document.querySelector('#draftbar .draft-ask').hidden"))
            self.assertNotIn("分野が決まりました", page.text_content("#draftbar"))
            page.click(".tab[data-tab='A5']")
            page.wait_for_function("!document.querySelector('#draftbar .draft-ask').hidden")
            self.assertEqual(page.evaluate("[...document.querySelectorAll('#draftbar .draft-ask button')].map((b) => b.textContent)"),
                             ["質問に答える", "標準の値のまま進める"])
            page.click("#draftbar button[data-draft-ask='questions']")  # back to A1, the questions in sight
            page.wait_for_function("document.querySelector('.tab.on')?.dataset.tab === 'A1' && document.querySelector('#intake-host .in-ask-top')?.checkVisibility()")
            page.wait_for_function("document.querySelector('#draftbar .draft-ask').hidden")
            page.evaluate("async () => { await window.hiraku.request.answer({ answers: { Q30: 'two_cargo', Q40: 150, Q50: 40, Q70: '私有地だけ' } }); }")
            page.wait_for_selector("#draftbar button[data-start='scripted']")
            self.assertIn("要件がそろいました", page.text_content("#draftbar"))
            self.assertTrue(page.is_disabled("#draftbar button:has-text('Claude で始める')"))  # no key in this server's window
            page.click("#draftbar button[data-start='scripted']")
            page.wait_for_url(f"**/?run={run_id}", timeout=30000)
            self.assertEqual(seen, [("requirements.yaml", "intake.json", "scripted")])
            self.assertEqual(errors, [])
            page.close()
        finally:
            server.shutdown()
            server.server_close()
            if lau.proc and lau.proc.poll() is None:
                lau.proc.kill()
                lau.proc.wait()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_draft_bar_sends_the_standard_values(self):
        """標準の値のまま進める on the bar (shown on A5, where there is no question box) answers the hearing
        with no answers (the intake's useDefaults): the answer goes to the server. Before a run the
        version and cost pills are not shown."""
        from hiraku.webserve.launch import Launcher
        from tests.web.fake_launch import fake_command, fake_reader

        tmp = Path(tempfile.mkdtemp())
        (tmp / "requirements").mkdir()
        lau = Launcher(tmp / "runs", tmp / "requirements", env={}, command=fake_command(SAMPLE_EVENTS, "20261103-120000-offroad-4w-fake", 20.0),
                       reader=fake_reader, wait_s=20, demo_runs_file=tmp / "no-demo-runs.yaml")
        server = make_server("127.0.0.1", 0, tmp / "runs", quiet=True, saved_dir=None, launcher=lau)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            posted = []
            page.on("request", lambda r: posted.append((r.url.split("/api/")[-1], r.post_data_json)) if r.method == "POST" else None)
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/?new=1")
            page.wait_for_selector("#draftbar")
            self.assertEqual(page.evaluate("[document.getElementById('pill-version').checkVisibility(), document.getElementById('pill-cost').checkVisibility()]"), [False, False])
            page.fill("#view-intake textarea", "林道を走れる 2 人乗りの小さな車両")
            page.click("#view-intake button:has-text('送る')")
            page.wait_for_selector("#intake-host .in-ask-top")
            page.click(".tab[data-tab='A5']")
            page.click("#draftbar button[data-draft-ask='defaults']")
            page.wait_for_function("window.hiraku.request.state.done")
            answer = [body for url, body in posted if url == "request/answer"]
            self.assertEqual(len(answer), 1)
            self.assertEqual(answer[0]["answers"], {})
            self.assertEqual(page.evaluate("[document.getElementById('pill-version').checkVisibility(), document.getElementById('pill-cost').checkVisibility()]"), [False, False])
            self.assertEqual(errors, [])
            page.close()
        finally:
            server.shutdown()
            server.server_close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_file_dropped_on_A5_reaches_the_run_started_here(self):
        """On a run this screen started and still going, a file given to A5 lands in the run's
        knowledge/ with a request in its inbox/ (the run turns it into events), and A5 says so."""
        from hiraku.webserve.launch import Launcher
        from tests.web.fake_launch import fake_command, fake_reader

        tmp = Path(tempfile.mkdtemp())
        (tmp / "requirements").mkdir()
        (tmp / "requirements" / "offroad-4w.yaml").write_text("product: offroad-4w\n", encoding="utf-8")
        run_id = "20261103-120000-offroad-4w-fake"
        # the sample run under this run's id, without its run.finished: still going
        events = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()][:-1]
        for e in events:
            e["run_id"] = run_id
        events[0]["data"]["run_id"] = run_id
        going = tmp / "going.jsonl"
        going.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
        # the child stays until the test kills it (finally below): with other tests beside it, opening
        # the page took longer than a 30 s hold, the run had ended and the file was refused
        lau = Launcher(tmp / "runs", tmp / "requirements", env={}, command=fake_command(going, run_id, 300.0), reader=fake_reader, wait_s=20,
                       demo_runs_file=tmp / "no-demo-runs.yaml")
        server = make_server("127.0.0.1", 0, tmp / "runs", quiet=True, saved_dir=None, launcher=lau)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            lau.start("offroad-4w.yaml", "scripted")
            page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{base}/?run={run_id}&tab=A5", wait_until="domcontentloaded")
            page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=30000)
            page.wait_for_selector(".stop-btn")  # the screen knows the run is its own
            note = tmp / "曲げの決まり.md"
            note.write_text("曲げの内側の半径は板厚以上\n", encoding="utf-8")
            page.set_input_files("#view-intake .in-drop input[type=file]", str(note))
            page.wait_for_function("document.getElementById('view-intake').textContent.includes('渡しました')")
            run = tmp / "runs" / run_id
            self.assertEqual((run / "knowledge" / "曲げの決まり.md").read_bytes(), note.read_bytes())  # kept as given
            self.assertEqual(json.loads((run / "inbox" / "1-knowledge.json").read_text(encoding="utf-8")), {"kind": "knowledge", "file": "曲げの決まり.md"})
            self.assertEqual(errors, [])
            page.close()
        finally:
            server.shutdown()
            server.server_close()
            if lau.proc and lau.proc.poll() is None:
                lau.proc.kill()
                lau.proc.wait()
            shutil.rmtree(tmp, ignore_errors=True)

    @unittest.skipUnless("rule_check.recorded" in contract.EVENT_KINDS, "contract without the intake events")
    def test_a_going_run_started_elsewhere_takes_the_answer_and_the_drop(self):
        """A run this screen did not start (a Claude Code plugin run with a fresh plugin.alive),
        followed live while its record says running: A2 can answer and A5 can take a file,
        and there is no stop button (stopping stays with the run started here)."""
        from hiraku.webserve.launch import PLUGIN_ALIVE_FILE, Launcher

        tmp = Path(tempfile.mkdtemp())
        run_id = "20261103-160000-plugin-run"
        run = tmp / "runs" / run_id
        run.mkdir(parents=True)
        # the sample run under this run's id, with the hearing asked and without its run.finished: still going
        events = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()][:-1]
        asked = json.loads((contract.EXAMPLES_DIR / "hearing_asked.json").read_text(encoding="utf-8"))
        hearing = {"seq": 0, "ts": events[0]["ts"], "t": events[0]["t"] + 0.1, "run_id": run_id, "kind": "hearing.asked", "agent": "lead", "data": asked}
        events = events[:1] + [hearing] + events[1:]
        for i, e in enumerate(events):
            e["run_id"], e["seq"] = run_id, i
        events[0]["data"]["run_id"] = run_id
        (run / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
        (run / "project.json").write_text(json.dumps({**events[0]["data"], "status": "running", "driver": "claude_code_plugin"}), encoding="utf-8")
        # the plugin's session touches plugin.alive while it holds the run (younger than PLUGIN_ALIVE_S):
        # touched here every second until the test ends, as the session would, so a slow page
        # (other tests beside it) does not outlast it
        alive = run / PLUGIN_ALIVE_FILE
        alive.write_bytes(b"")
        done = threading.Event()

        def keep_alive():
            while not done.wait(1.0):
                try:
                    os.utime(alive)
                except OSError:  # the folder is gone (the test's end)
                    return

        threading.Thread(target=keep_alive, daemon=True).start()
        lau = Launcher(tmp / "runs", tmp / "requirements", env={}, demo_runs_file=tmp / "no-demo-runs.yaml")
        server = make_server("127.0.0.1", 0, tmp / "runs", quiet=True, saved_dir=None, launcher=lau)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{base}/?run={run_id}&tab=A2", wait_until="domcontentloaded")
            page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=30000)
            button = "#view-intake .in-pane[data-screen=A2] button.in-btn"
            page.wait_for_function(f"[...document.querySelectorAll('{button}')].some((b) => /標準の値のまま進める/.test(b.textContent) && !b.disabled)")
            self.assertEqual(page.locator(".stop-btn").count(), 0)  # not started here: no stop
            # wait for the server's answer to the POST (written by then), not a fixed time: a busy
            # PC (tests run side by side) took longer than the 10 s the loop used to give
            with page.expect_response(lambda r: "/api/launch/hearing" in r.url, timeout=60000) as answered:
                page.locator(button, has_text="標準の値のまま進める").click()
            self.assertTrue(answered.value.ok, answered.value.status)
            self.assertTrue(json.loads((run / "hearing.answer.json").read_text(encoding="utf-8"))["use_defaults"])
            page.click('.tab[data-tab="A5"]')
            note = tmp / "曲げの決まり.md"
            note.write_text("曲げの内側の半径は板厚以上\n", encoding="utf-8")
            page.set_input_files("#view-intake .in-drop input[type=file]", str(note))
            page.wait_for_function("document.getElementById('view-intake').textContent.includes('渡しました')")
            self.assertEqual((run / "knowledge" / "曲げの決まり.md").read_bytes(), note.read_bytes())
            self.assertEqual(json.loads((run / "inbox" / "1-knowledge.json").read_text(encoding="utf-8")), {"kind": "knowledge", "file": "曲げの決まり.md"})
            self.assertEqual(errors, [])
            page.close()
            # the same run opened as a replay (?live=0) stays read-only
            page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
            page.goto(f"{base}/?run={run_id}&tab=A2&live=0&pace=0", wait_until="domcontentloaded")
            page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=30000)
            page.wait_for_selector(button)
            self.assertTrue(all(page.eval_on_selector_all(button, "(bs) => bs.map((b) => b.disabled)")))
            page.click('.tab[data-tab="A5"]')
            other = tmp / "replay.md"
            other.write_text("x\n", encoding="utf-8")
            page.set_input_files("#view-intake .in-drop input[type=file]", str(other))
            page.wait_for_function("document.getElementById('view-intake').textContent.includes('この画面では受け取れません')")
            self.assertFalse((run / "knowledge" / "replay.md").exists())
            page.close()
        finally:
            done.set()
            server.shutdown()
            server.server_close()
            shutil.rmtree(tmp, ignore_errors=True)

    @unittest.skipUnless("rule_check.recorded" in contract.EVENT_KINDS, "contract without the intake events")
    def test_intake_screens_in_their_tabs(self):
        """① 作りたいもの・② 確認したいこと・守るルール (A1, A2, A5) show the intake screens (js/intake/) on the events so far;
        a replay is read-only (the hearing's buttons are off) and a rewind redraws them."""
        events = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
        ex = {n: json.loads((contract.EXAMPLES_DIR / f"{n}.json").read_text(encoding="utf-8"))
              for n in ("domains_picked", "hearing_asked", "hearing_answered", "knowledge_imported")}
        base = {"run_id": events[0]["run_id"], "ts": "2026-11-02T20:30:10.000+09:00", "seq": 0}
        intake = [
            {"kind": "domains.picked", "agent": "lead", "data": ex["domains_picked"]},
            {"kind": "hearing.asked", "agent": "lead", "data": ex["hearing_asked"]},
            {"kind": "hearing.answered", "agent": "lead", "data": ex["hearing_answered"]},
            {"kind": "knowledge.imported", "agent": "lead", "data": ex["knowledge_imported"]},
        ]
        t0 = events[0]["t"]
        events = events[:1] + [{**base, "t": t0 + 0.1 * (i + 1), **ev} for i, ev in enumerate(intake)] + events[1:]
        for i, e in enumerate(events):
            e["seq"] = i
        page, errors = self.open("run=sample&pace=0&t=0")
        page.evaluate("async (events) => { await window.hiraku.load(events, 1e6); }", events)
        read = """() => { const v = document.getElementById("view-intake");
          const pane = [...v.querySelectorAll(".in-pane")].find((p) => !p.hidden);
          return { shown: !v.hidden, bench: !document.getElementById("view-bench").hidden, screen: pane?.dataset.screen ?? null,
                   text: pane?.textContent ?? "", on: document.querySelector(".tab.on")?.dataset.tab,
                   answer: [...(pane?.querySelectorAll("button") ?? [])].filter((b) => /進める/.test(b.textContent)).map((b) => b.disabled) }; }"""
        def tab(key):
            page.click(f'.tab[data-tab="{key}"]')
            page.wait_for_function(f"document.querySelector('.tab.on')?.dataset.tab === '{key}'")
            return page.evaluate(read)

        a1 = tab("A1")
        a2 = tab("A2")
        a5 = tab("A5")
        self.assertEqual(errors, [])
        self.assertEqual((a1["shown"], a1["bench"], a1["screen"], a1["on"]), (True, False, "A1", "A1"))
        self.assertIn(ex["domains_picked"]["picks"][0]["shown_name"], a1["text"])
        self.assertEqual((a2["screen"], a2["on"]), ("A2", "A2"))
        self.assertIn(ex["hearing_asked"]["questions"][0]["text"], a2["text"])
        self.assertTrue(a2["answer"] and all(a2["answer"]))  # a replay cannot answer
        self.assertEqual(a5["screen"], "A5")
        self.assertIn(ex["knowledge_imported"]["rules"][0]["text"], a5["text"])
        page.evaluate("async () => { await window.hiraku.seek(0); }")  # back before the knowledge came
        page.wait_for_function(f"!document.querySelector('#view-intake .in-pane[data-screen=A5]').textContent.includes({json.dumps(ex['knowledge_imported']['rules'][0]['text'])})")
        self.assertEqual(tab("bench")["shown"], False)
        self.assertEqual(errors, [])
        page.close()

    @unittest.skipUnless("x" in contract.schema()["$defs"]["series"]["properties"], "contract without series against x")
    def test_series_against_another_quantity_are_charted(self):
        """Series with x (a K&C bench) give a graph without a motion: one chart per channel
        and abscissa, the axis named from x.quantity and x.unit, and no time cursor."""
        events = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
        ana = next(e for e in events if e["kind"] == "analysis.finished")["data"]
        travel = [float(v) for v in range(-60, 61, 10)]
        force = [v / 4 for v in range(0, 9)]
        # shape example: straight lines, not a result (the camber slope is made up; the toe slope is
        # the K2 team's demo value, -0.056 deg at 1 kN); channel and axis names as kc_bench writes them
        ana["series"] = [
            {"channel": f"camber.{w}", "unit": "deg", "x": {"quantity": "travel", "unit": "mm", "values": travel}, "values": [k * v for v in travel]}
            for w, k in (("FL", -0.02), ("FR", 0.02))
        ] + [{"channel": "toe_lateral", "unit": "deg", "x": {"quantity": "lateral_force", "unit": "kN", "values": force}, "values": [-0.056 * v for v in force]}]
        for e in events:
            self.assertEqual(contract.errors("event", e), [], e["kind"])
        page, errors = self.open("run=sample&pace=0&t=0")
        read = """() => ({ toggle: !!document.querySelector('#toggles button[data-toggle="graph"]'), shown: !document.getElementById("graph").hidden,
                   chips: [...document.querySelectorAll("#graph .gr-chips button")].map((b) => [b.textContent, b.classList.contains("on")]),
                   unit: document.querySelector("#graph .gr-unit")?.textContent ?? null, lines: document.querySelectorAll("#graph polyline").length,
                   cursor: document.querySelectorAll("#graph .cur").length })"""
        page.evaluate("async (events) => { await window.hiraku.load(events, 1e6); }", events)
        first = page.evaluate(read)
        self.assertEqual(errors, [])
        self.assertTrue(first["toggle"])
        self.assertTrue(first["shown"])
        self.assertEqual(first["chips"], [["キャンバーの変化", True], ["横力によるトー", False]])
        self.assertEqual(first["unit"], "[°]・ストローク [mm]")
        self.assertEqual(first["lines"], 2)
        self.assertEqual(first["cursor"], 0)
        page.click("#graph .gr-chips button:text-is('横力によるトー')")
        page.wait_for_function("document.querySelector('#graph .gr-unit')?.textContent.includes('横力')")
        second = page.evaluate(read)
        self.assertEqual(second["unit"], "[°]・横力 [kN]")
        self.assertEqual(second["lines"], 1)
        self.assertEqual(errors, [])
        page.close()

    @unittest.skipUnless("mesh.created" in contract.EVENT_KINDS, "contract without meshes")
    def test_mesh_fields_draw_the_shell(self):
        """A field on a shell mesh (about 20 000 nodes and quads) draws the mesh in place of
        the beams, one colour per element; the legend switches between the beams and the
        mesh's surfaces, and a displacement on the mesh nodes moves the mesh."""
        events, mesh = mesh_events()
        for e in events:
            self.assertEqual(contract.errors("event", e), [], e["kind"])
        page, errors = self.open("run=sample&pace=0&t=0")
        page.evaluate("async (events) => { await window.hiraku.load(events, 1e6); }", events)
        state = """() => { const b = window.hiraku.bench; const o = Object.values(b.shellObjs)[0];
          const col = o.faces.geometry.getAttribute("color").array;
          const colours = new Set(); for (let i = 0; i < col.length; i += 3 * 6) colours.add(`${col[i]},${col[i + 1]},${col[i + 2]}`);
          return { shell: o.group.visible, beams: b.model.visible, corners: o.vNode.length, colours: colours.size,
                   surface: b.keys.contour?.field.surface ?? null, kind: b.keys.contour?.kind ?? null,
                   variants: [...document.querySelectorAll("#legend .variants button")].map((x) => [x.textContent, x.classList.contains("on")]) }; }"""
        first = page.evaluate(state)
        self.assertEqual(errors, [])
        self.assertTrue(first["shell"])
        self.assertFalse(first["beams"])
        self.assertEqual(first["corners"], 6 * len(mesh["elements"]))
        self.assertGreater(first["colours"], 3)
        self.assertEqual(first["surface"], "max")
        self.assertEqual(first["variants"], [["梁", False], ["メッシュ・外面", False], ["メッシュ・中面", False], ["メッシュ・内面", False], ["メッシュ・外面と内面の大きい方", True]])
        page.click("#legend .variants button:text-is('梁')")
        page.wait_for_function("window.hiraku.bench.model.visible")
        beams = page.evaluate(state)
        self.assertEqual((beams["shell"], beams["beams"]), (False, True))
        page.click("#legend .variants button:text-is('メッシュ・内面')")
        page.wait_for_function("window.hiraku.bench.keys.contour?.field.surface === 'bottom'")
        self.assertTrue(page.evaluate(state)["shell"])
        page.click("#legend .kinds:not(.variants) button:text-is('変位')")
        page.wait_for_function("window.hiraku.bench.keys.contour?.kind === 'displacement'")
        moved = page.evaluate(
            """() => { const b = window.hiraku.bench; const o = b.modeShape.shell;
              b.animate(0.25, false);
              const p = o.faces.geometry.getAttribute("position").array; let d = 0;
              for (let i = 0; i < p.length; i += 1) d = Math.max(d, Math.abs(p[i] - o.rest[i]));
              b.animate(0, false);
              const q = o.faces.geometry.getAttribute("position").array; let back = 0;
              for (let i = 0; i < q.length; i += 1) back = Math.max(back, Math.abs(q[i] - o.rest[i]));
              return { d, back }; }"""
        )
        self.assertGreater(moved["d"], 1.0)
        self.assertLess(moved["back"], 1e-6)
        self.assertEqual(errors, [])
        page.close()

    @unittest.skipUnless("rule_check.recorded" in contract.EVENT_KINDS, "contract without the intake events")
    def test_intake_events_are_kept(self):
        """domains.picked, hearing.*, knowledge.imported and rule_check.recorded (the
        contract's examples) land in state.intake; a rule check sent again replaces its line."""
        events = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
        ex = {n: json.loads((contract.EXAMPLES_DIR / f"{n}.json").read_text(encoding="utf-8"))
              for n in ("domains_picked", "hearing_asked", "hearing_answered", "knowledge_imported", "rule_check")}
        passed = {**copy.deepcopy(ex["rule_check"]), "hits": [], "pass": True}
        base = {"run_id": events[0]["run_id"], "ts": "2026-11-02T20:30:10.000+09:00", "seq": 0}
        intake = [
            {"kind": "domains.picked", "agent": "lead", "data": ex["domains_picked"]},
            {"kind": "hearing.asked", "agent": "lead", "data": ex["hearing_asked"]},
            {"kind": "hearing.answered", "agent": "lead", "data": ex["hearing_answered"]},
            {"kind": "knowledge.imported", "agent": "lead", "data": ex["knowledge_imported"]},
        ]
        checks = [
            {"kind": "rule_check.recorded", "agent": "packager", "design_version": 1, "data": ex["rule_check"]},
            {"kind": "rule_check.recorded", "agent": "packager", "design_version": 1, "data": passed},
        ]
        t0 = events[0]["t"]
        intake = [{**base, "t": t0 + 0.1 * (i + 1), **ev} for i, ev in enumerate(intake)]
        t1 = events[-1]["t"]
        checks = [{**base, "t": t1 - 0.2 + 0.1 * i, **ev} for i, ev in enumerate(checks)]
        events = events[:1] + intake + events[1:-1] + checks + [events[-1]]
        for i, ev in enumerate(events):
            ev["seq"] = i
            self.assertEqual(contract.errors("event", ev), [], ev["kind"])
        page, errors = self.open("run=sample&pace=0&t=0")
        got = page.evaluate(
            """async (events) => {
              await window.hiraku.load(events, 1e6);
              const k = window.hiraku.state().intake;
              return { picks: k.picked.picks.map((p) => p.domain), questions: k.questions.map((q) => q.req_key),
                       answers: k.answers.map((a) => a.key), note: k.note, docs: k.docs.map((x) => x.doc.doc_id),
                       rules: Object.keys(k.rules), checks: Object.values(k.ruleChecks).map((c) => [c.rule_check_id, c.pass]),
                       lines: [...document.querySelectorAll("#log li")].map((li) => li.textContent)
                         .filter((x) => ["分野を選んだ", "要件を", "答えがそろった", "規則の束", "規則 SM-01"].some((w) => x.includes(w))) };
            }""",
            events,
        )
        self.assertEqual(errors, [])
        self.assertEqual(got["picks"], ["strength"])
        self.assertEqual(got["questions"], ["Q15"])
        self.assertEqual(got["answers"], ["Q15"])
        self.assertEqual(got["note"], ex["hearing_answered"]["note"])
        self.assertEqual(got["docs"], ["builtin-sheet-metal"])
        self.assertEqual(got["rules"], ["SM-01"])
        self.assertEqual(got["checks"], [["rchk-0001", True]])
        self.assertEqual(len(got["lines"]), 5, got["lines"])
        self.assertIn("当てはまる試験 2", got["lines"][0])
        self.assertIn("値が無くて当てられない部品 1", got["lines"][4])
        self.assertNotIn("守れていない", got["lines"][4])
        page.close()

    def test_n05_cut_is_laid_out_from_the_layout_rules(self):
        """cut.html?cut=n05: tiles where layout.json puts them, the hero before the burst and
        the grid after, nothing in the subtitle band. With HIRAKU_WEB_DEV_RUNS the tiles play
        the real tools' records; without it the sample run leaves them empty."""
        tmp = Path(tempfile.mkdtemp())
        layout = n05_layout()
        (tmp / "layout.json").write_text(json.dumps(layout), encoding="utf-8")
        given = os.environ.get("HIRAKU_WEB_DEV_RUNS")
        runs = Path(given) if given else tmp / "runs"
        run = sorted(p.name for p in runs.iterdir() if p.is_dir())[-1] if given else "sample"
        server = make_server("127.0.0.1", 0, runs, quiet=True, saved_dir=None, launcher=False, layout_file=tmp / "layout.json")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{base}/cut.html?cut=n05&run={run}&t=0.5", wait_until="domcontentloaded")
            page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
            page.evaluate("window.hiraku.ready")
            js = """async (t) => {
              await window.hiraku.seek(t);
              const box = (el) => { const r = el.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; };
              const tiles = [...document.querySelectorAll('[data-box^="n05.tile."]')].filter((el) => el.classList.contains("tile"));
              return { duration: window.hiraku.player.duration,
                       hero: getComputedStyle(document.querySelector('[data-box="n05.hero"]')).visibility,
                       tiles: tiles.map((el) => ({ key: el.dataset.box, box: box(el), shown: getComputedStyle(el).visibility,
                                                    rate: el.querySelector(".rate")?.textContent ?? null, charts: el.querySelectorAll("svg.chart").length,
                                                    label: el.querySelector("b")?.textContent ?? null })) };
            }"""
            early = page.evaluate(js, 0.5)
            late = page.evaluate(js, 4.9)
            self.assertEqual(errors, [])
            self.assertEqual(early["duration"], 5.0)
            self.assertEqual(early["hero"], "visible")
            self.assertTrue(all(t["shown"] == "hidden" for t in early["tiles"]))
            self.assertEqual(late["hero"], "hidden")
            g = layout["n05"]["grid"]
            self.assertEqual([t["key"] for t in late["tiles"]], [f"n05.tile.{k}" for k in layout["n05"]["tests"]])
            for i, t in enumerate(late["tiles"]):
                col, row = i % g["cols"], i // g["cols"]
                self.assertEqual(t["box"], [g["x"] + col * (g["tile_w"] + g["gap_x"]), g["y"] + row * (g["tile_h"] + g["gap_y"]), g["tile_w"], g["tile_h"]])
                self.assertLessEqual(t["box"][1] + t["box"][3], layout["subtitle_band"]["y"])
                self.assertEqual(t["shown"], "visible")
                # the name only: no squeeze, chart or figure on a tile (the story: pictures, no figures)
                self.assertIsNone(t["rate"], t["key"])
                self.assertEqual(t["charts"], 0, t["key"])
                self.assertFalse(any(c.isdigit() for c in t["label"]), t["key"])
            page.close()
        finally:
            server.shutdown()
            server.server_close()
            shutil.rmtree(tmp)

    def test_n05_without_the_hero_and_the_hero_alone(self):
        """?hero=0: the tiles fly in as before with no big vehicle. ?only=hero: the big vehicle
        alone on a clear page, and window.hiraku.png() gives its box as a PNG at the page's
        scale (2 here) with clear corners."""
        tmp = Path(tempfile.mkdtemp())
        layout = n05_layout()
        (tmp / "layout.json").write_text(json.dumps(layout), encoding="utf-8")
        server = make_server("127.0.0.1", 0, tmp / "runs", quiet=True, saved_dir=None, launcher=False, layout_file=tmp / "layout.json")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"

        def open_cut(query, dpr=1):
            page = self.browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=dpr)
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{base}/cut.html?cut=n05&run=sample&{query}", wait_until="domcontentloaded")
            page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
            page.evaluate("window.hiraku.ready")
            return page, errors

        try:
            page, errors = open_cut("hero=0&t=0")
            got = page.evaluate(
                """async () => {
                  await window.hiraku.seek(4.9);
                  const tiles = [...document.querySelectorAll(".tile")];
                  return { hero: document.querySelectorAll('[data-box="n05.hero"]').length, png: window.hiraku.png(),
                           tiles: tiles.length, shown: tiles.filter((el) => getComputedStyle(el).visibility === "visible").length };
                }"""
            )
            self.assertEqual(errors, [])
            self.assertEqual(got, {"hero": 0, "png": None, "tiles": 12, "shown": 12})
            page.close()

            page, errors = open_cut("only=hero&t=0", dpr=2)
            got = page.evaluate(
                """async () => {
                  await window.hiraku.seek(0);
                  const bg = (el) => getComputedStyle(el).backgroundColor;
                  const img = new Image();
                  img.src = window.hiraku.png();
                  await img.decode();
                  const c = document.createElement("canvas");
                  c.width = img.width;
                  c.height = img.height;
                  const g = c.getContext("2d");
                  g.drawImage(img, 0, 0);
                  const px = g.getImageData(0, 0, c.width, c.height).data;
                  let solid = 0;
                  for (let i = 3; i < px.length; i += 4 * 7) if (px[i] > 200) solid += 1;
                  const a = (x, y) => px[(y * c.width + x) * 4 + 3];
                  return { size: [img.width, img.height], corners: [a(0, 0), a(c.width - 1, 0), a(0, c.height - 1), a(c.width - 1, c.height - 1)], solid,
                           page: [bg(document.documentElement), bg(document.body), bg(document.getElementById("stage"))],
                           boxes: [...document.querySelectorAll("[data-box]")].map((el) => el.dataset.box) };
                }"""
            )
            self.assertEqual(errors, [])
            hero = layout["n05"]["hero"]
            self.assertEqual(got["size"], [hero["w"] * 2, hero["h"] * 2])
            self.assertEqual(got["corners"], [0, 0, 0, 0])
            self.assertGreater(got["solid"], 0)
            self.assertEqual(got["page"], ["rgba(0, 0, 0, 0)"] * 3)
            self.assertEqual(got["boxes"], ["n05.hero"])
            page.close()
        finally:
            server.shutdown()
            server.server_close()
            shutil.rmtree(tmp)

    def test_n01_cut_plays_the_step_and_the_case_past_the_limit(self):
        """cut.html?cut=n01 on the real tools' records (HIRAKU_WEB_DEV_RUNS): the main view, the
        red overlay of the higher step, one card per case filled from the records."""
        given = os.environ.get("HIRAKU_WEB_DEV_RUNS")
        if not given:
            self.skipTest("needs HIRAKU_WEB_DEV_RUNS (a folder made by tests.web.dev_k2_run)")
        tmp = Path(tempfile.mkdtemp())
        (tmp / "layout.json").write_text(json.dumps(n05_layout()), encoding="utf-8")
        runs = Path(given)
        run = sorted(p.name for p in runs.iterdir() if p.is_dir())[-1]
        server = make_server("127.0.0.1", 0, runs, quiet=True, saved_dir=None, launcher=False, layout_file=tmp / "layout.json")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/cut.html?cut=n01&run={run}&t=2", wait_until="domcontentloaded")
            page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
            page.evaluate("window.hiraku.ready")
            got = page.evaluate(
                """() => ({ duration: window.hiraku.player.duration, err: document.querySelector(".err")?.textContent ?? null,
                           cards: [...document.querySelectorAll(".n01-card")].map((c) => [c.dataset.box, c.querySelector("h3").textContent]),
                           tables: document.querySelectorAll(".n01-card table").length,
                           pane: document.querySelector('[data-box="n01.pane"]').innerText,
                           ghost: !!window.hiraku.cut.ghost, tinted: window.hiraku.cut.ghost?.view.tinted ?? false,
                           contour: window.hiraku.cut.contour?.kind ?? null,
                           view: document.querySelector('[data-box="n01.view"]').getBoundingClientRect().bottom,
                           // the smallest text in the pane (every element that holds text of its own)
                           minText: Math.min(...[...document.querySelectorAll('[data-box="n01.pane"] *')]
                             .filter((el) => [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim()))
                             .map((el) => parseFloat(getComputedStyle(el).fontSize))) })"""
            )
            self.assertGreaterEqual(got["minText"], 18)
            self.assertEqual(errors, [])
            self.assertIsNone(got["err"])
            self.assertEqual(got["duration"], 4.0)
            self.assertEqual([c[0] for c in got["cards"]], ["n01.card.ok", "n01.card.over"])
            # each card is the step's height and whether it climbed (no table of values)
            self.assertEqual(got["cards"][0][1], "段差 100 mm")
            self.assertEqual(got["cards"][1][1], "段差 250 mm")
            self.assertEqual(got["tables"], 0)
            # no explaining notes in the pane (許容を超えた部材, 半透明の赤 ...): the cards and the colour bar only
            for w in ("許容を超えた部材", "半透明"):
                self.assertNotIn(w, got["pane"])
            self.assertTrue(got["ghost"] and got["tinted"])
            self.assertEqual(got["contour"], "stress")  # the K1 static analysis of the same load case
            self.assertLessEqual(got["view"], 910)
            page.close()
        finally:
            server.shutdown()
            server.server_close()
            shutil.rmtree(tmp)

    def test_n02_cut_lays_out_five_tests_and_their_limits(self):
        """cut.html?cut=n02 on the real tools' records (HIRAKU_WEB_DEV_RUNS): panes where the grid
        puts them, each test playing its record under its one tag."""
        given = os.environ.get("HIRAKU_WEB_DEV_RUNS")
        if not given:
            self.skipTest("needs HIRAKU_WEB_DEV_RUNS (a folder made by tests.web.dev_k2_run)")
        tmp = Path(tempfile.mkdtemp())
        layout = n05_layout()
        (tmp / "layout.json").write_text(json.dumps(layout), encoding="utf-8")
        runs = Path(given)
        run = sorted(p.name for p in runs.iterdir() if p.is_dir())[-1]
        server = make_server("127.0.0.1", 0, runs, quiet=True, saved_dir=None, launcher=False, layout_file=tmp / "layout.json")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/cut.html?cut=n02&run={run}&t=3", wait_until="domcontentloaded")
            page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
            page.evaluate("window.hiraku.ready")
            got = page.evaluate(
                """() => ({ err: document.querySelector(".err")?.textContent ?? null, duration: window.hiraku.player.duration,
                           panes: [...document.querySelectorAll(".n02-pane")].map((p) => { const r = p.getBoundingClientRect();
                             return [p.dataset.box, Math.round(r.left), Math.round(r.top), Math.round(r.bottom)]; }),
                           views: window.hiraku.cut.panes.filter((p) => p.view).length,
                           results: document.querySelectorAll(".n02-res, .n02-result").length,
                           labels: [...document.querySelectorAll('.n02-pane b[data-kind="label"]')].map((b) => b.textContent) })"""
            )
            self.assertEqual(errors, [])
            self.assertIsNone(got["err"])
            self.assertEqual(got["duration"], 7.0)
            g = layout["n02"]["grid"]
            self.assertEqual([p[0] for p in got["panes"]], [f"n02.pane.{k}" for k in layout["n02"]["panes"]])
            for i, p in enumerate(got["panes"]):
                self.assertEqual(p[1:3], [g["x"] + (i % 3) * (g["tile_w"] + g["gap_x"]), g["y"] + (i // 3) * (g["tile_h"] + g["gap_y"])])
                self.assertLessEqual(p[3], layout["subtitle_band"]["y"])
            self.assertEqual(got["views"], 5)  # every test has its record in the dev run
            # the panes only: no column of results, one tag per test, no figures
            self.assertEqual(got["results"], 0)
            self.assertEqual(got["labels"], [n for n in got["labels"] if not any(c.isdigit() for c in n)])
            self.assertEqual(len(got["labels"]), 5)
            page.close()
        finally:
            server.shutdown()
            server.server_close()
            shutil.rmtree(tmp)

    def test_opens_the_run_list_without_run_in_the_url(self):
        """No ?run= (nor ?src=, ?list=, a draft): the run list, the same as ?list=1, with the saved run
        recommended; its 見る replays it."""
        tmp = Path(tempfile.mkdtemp())
        run_id = "20261106-100000-offroad-4w"
        (tmp / "showcase" / run_id).mkdir(parents=True)
        shutil.copyfile(SAMPLE_EVENTS, tmp / "showcase" / run_id / "events.jsonl")
        from hiraku.webserve.launch import Launcher

        # the list's launcher without an API key, whatever the window running the test has (nothing is started)
        lau = Launcher(tmp / "runs", env={})
        server = make_server("127.0.0.1", 0, tmp / "runs", quiet=True, saved_dir=tmp / "showcase", launcher=lau)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
            asked = []
            page.on("request", lambda r: asked.append(r.url) if "/events" in r.url else None)
            page.goto(f"{base}/", wait_until="domcontentloaded")
            page.wait_for_selector("#launch .pick")
            first = page.evaluate("""() => ({ list: !document.getElementById("runlist").hidden,
                                              head: document.querySelector("#runlist h2").textContent })""")
            self.assertEqual(first, {"list": True, "head": "HIRAKU でできること"})
            self.assertEqual(asked, [])  # no record is read before 見る
            page.click("#launch .pick a.go")
            page.wait_for_url(f"**/?run={run_id}")
            page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=30000)
            page.wait_for_function("document.getElementById('runlist').hidden && !document.getElementById('loading-note')")
            # back to the list by 「☰ 一覧へ」; the run's id is not on the screen without ?dev=1
            back = page.evaluate("""() => ({ link: [...document.querySelectorAll("#mode a")].map((a) => [a.textContent, a.getAttribute("href")]),
                                             text: document.getElementById("mode").textContent })""")
            self.assertEqual(back["link"], [["☰ 一覧へ", "?list=1"]])
            self.assertNotIn(run_id, back["text"])
            page.goto(f"{base}/?list=1", wait_until="domcontentloaded")
            page.wait_for_selector("#launch .pick")
            # the recommendation comes first after 「＋ 新しく依頼する」 (the list's first line, test_runlist_order_browser)
            pick = page.evaluate("""() => { const p = document.querySelector("#launch .pick");
                return { n: document.querySelectorAll("#launch .pick").length, first: document.querySelector("#runlist .pick") === document.querySelector("#runlist h2 ~ #launch > .fresh-top + *"),
                         run: p.dataset.run, href: p.querySelector("a.go").getAttribute("href"), verb: p.querySelector("a.go").textContent }; }""")
            self.assertEqual(pick, {"n": 1, "first": True, "run": run_id, "href": f"?run={run_id}", "verb": "見る"})
            page.goto(f"{base}/?list=1&dev=1", wait_until="domcontentloaded")
            page.wait_for_selector("#runlist h3")
            listing = page.evaluate(
                """() => ({ heads: [...document.querySelectorAll("#runlist > h3")].map((h) => h.textContent),
                           def: document.querySelector("#runlist .def")?.closest("tr").querySelector(".rid").textContent })"""
            )
            self.assertEqual(listing["heads"], ["保存した実行", "契約の形の見本"])
            self.assertEqual(listing["def"], run_id)
            page.close()
        finally:
            server.shutdown()
            server.server_close()
            shutil.rmtree(tmp)

    def test_loading_picks_who_is_on_board(self):
        src = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
        run, design_ev = copy.deepcopy(src[0]), copy.deepcopy(src[1])
        run["data"]["load_cases"][0]["params"]["loading"] = "driver"
        box = lambda x, y, z: {"center_mm": [x, y, z], "size_mm": [400.0, 400.0, 500.0]}
        design_ev["data"]["parts"] += [
            {"id": "occ_1", "name": "driver (shape example)", "kind": "occupant", "box": box(-900.0, -250.0, 900.0), "mass_kg": 70.0},
            {"id": "occ_2", "name": "passenger (shape example)", "kind": "occupant", "box": box(-900.0, 250.0, 900.0), "mass_kg": 70.0},
            {"id": "load", "name": "cargo (shape example)", "kind": "cargo", "box": box(-1500.0, 0.0, 600.0), "mass_kg": 100.0},
        ]
        for ev in (run, design_ev):
            self.assertEqual(contract.errors("event", ev), [])
        page, errors = self.open("run=sample&t=0")
        got = page.evaluate(
            """async (events) => {
              await window.hiraku.load(events, 100);
              const objs = window.hiraku.bench.partObjs;
              return Object.fromEntries(Object.entries(objs).map(([id, o]) => [id, o.onBoard]));
            }""",
            [run, design_ev],
        )
        self.assertEqual(errors, [])
        self.assertEqual(got, {"battery": True, "seat": True, "occ_1": True, "occ_2": False, "load": False})
        page.close()

    def test_front_is_larger_x(self):
        # ISO 8855: x forward, y left, z up; the rear axle is at x = -wheelbase.
        # The default camera looks from the front left, so the front-left wheel is
        # nearest, the front is on the left of the picture and up is up.
        page, errors = self.open("run=sample&t=9")
        got = page.evaluate(
            """() => {
              const b = window.hiraku.bench;
              const V = b.camera.position.constructor;
              const d = window.hiraku.state().designs[1];
              const at = (id) => d.supports.find((s) => s.id === id).position_mm;
              const world = (p) => new V(p[0], p[1], p[2]).applyMatrix4(b.iso.matrix);
              const screen = (p) => world(p).project(b.camera);
              const dist = (p) => world(p).distanceTo(b.camera.position);
              const cam = b.camera.position;
              const camIso = [cam.x * 1000, -cam.z * 1000, cam.y * 1000];
              const c = b.bboxIso.getCenter(new V());
              const fl = at("FL"), fr = at("FR"), rl = at("RL"), rr = at("RR");
              return {
                rearX: [rl[0], rr[0]], frontX: [fl[0], fr[0]],
                camRel: [camIso[0] - c.x, camIso[1] - c.y, camIso[2] - c.z],
                nearest: ["FL", "FR", "RL", "RR"].sort((a, z) => dist(at(a)) - dist(at(z)))[0],
                frontLeftOfRear: screen(fl).x < screen(rl).x && screen(fr).x < screen(rr).x,
                upIsUp: screen([fl[0], fl[1], fl[2] + 500]).y > screen(fl).y,
              };
            }"""
        )
        self.assertEqual(errors, [])
        self.assertTrue(all(x < 0 for x in got["rearX"]), got)
        self.assertEqual(got["frontX"], [0, 0])
        self.assertTrue(got["camRel"][0] > 0 and got["camRel"][1] > 0 and got["camRel"][2] > 0, got)
        self.assertEqual(got["nearest"], "FL")
        self.assertTrue(got["frontLeftOfRear"])
        self.assertTrue(got["upIsUp"])
        page.close()

    def test_all_domains_tiles(self):
        events = domain_events()
        for ev in events:
            self.assertEqual(contract.errors("event", ev), [])
        page, errors = self.open("run=sample&t=0")
        got = page.evaluate(
            """async (events) => {
              await window.hiraku.load(events, 1e6);
              const tiles = [...document.querySelectorAll("#tiles .tile")];
              const out = {
                order: tiles.map((t) => t.dataset.domain),
                red: tiles.filter((t) => t.classList.contains("red")).map((t) => t.dataset.domain),
                flow: [...document.querySelectorAll("#grid-head .vbox")].map((b) => b.firstChild.textContent),
                flowRed: [...document.querySelectorAll("#grid-head .vbox")].map((b) => b.querySelector(".vred")?.textContent ?? ""),
                banner: document.getElementById("grid-banner").hidden ? "" : document.getElementById("grid-banner").textContent,
                assembly: document.querySelector('#tiles .tile[data-domain="assembly"] .cap').textContent,
                thermal: document.querySelector('#tiles svg.side[data-domain="thermal"]').textContent,
                wide: document.getElementById("main").classList.contains("wide"),
              };
              // the last quarter second is after every event (run.finished waits 0.3 s after screen.focus)
              // the first member runs from the front axle (which does not move in this mode) backward
              const nvhLine = () => document.querySelector('#tiles svg.side[data-domain="nvh"] line[data-a]').getAttribute("y2");
              const p = window.hiraku.player.playhead;
              const y1a = nvhLine();
              await window.hiraku.seek(p - 0.25);
              out.swing = [y1a, nvhLine()];
              return out;
            }""",
            events,
        )
        self.assertEqual(errors, [])
        self.assertEqual(got["order"], ["crash", "nvh", "strength", "thermal", "handling", "powertrain", "aero", "assembly"])
        self.assertEqual(sorted(got["red"]), ["assembly", "crash"])
        self.assertEqual(got["flow"], ["v1 NG 4", "v2 NG 2"])
        self.assertIn("繰り返しの上限", got["banner"])
        self.assertIn("電池 × cross1 8 mm 干渉", got["assembly"])
        self.assertIn("電池 43 ℃", got["thermal"])
        self.assertTrue(got["wide"])
        self.assertNotEqual(got["swing"][0], got["swing"][1])  # the mode shape swings with the replay clock
        page.close()

    def test_temperature_contour_on_the_workbench(self):
        page, errors = self.open("run=sample&t=0")
        page.evaluate("async (events) => { await window.hiraku.load(events, 1e6); }", domain_events())
        page.click("button[data-tab=bench]")
        page.click('#tests button[data-lc="T1-v"]')
        got = page.evaluate(
            """async () => {
              await window.hiraku.seek(window.hiraku.player.playhead);
              const ct = window.hiraku.bench.keys.contour;
              return { kind: ct && ct.kind, min: ct && ct.min, max: ct && ct.max, legend: document.getElementById("legend").textContent };
            }"""
        )
        self.assertEqual(errors, [])
        self.assertEqual(got["kind"], "temperature")
        # the parts' own range; the criterion is the battery's limit, held to the battery only
        self.assertEqual((got["min"], got["max"]), (31.0, 43.0))
        self.assertIn("battery の許容 60 ℃", got["legend"])
        page.close()

    def test_pacing_spreads_and_cuts_gaps(self):
        # A scripted-looking run: everything within 0.3 s, then a 60 s silence.
        src = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
        events = copy.deepcopy(src)
        for i, ev in enumerate(events):
            ev["t"] = round(i * 0.02, 3)
        events[-1]["t"] = events[-2]["t"] + 60.0
        page, errors = self.open("run=sample&t=0")
        got = page.evaluate(
            """async (events) => {
              await window.hiraku.load(events, 0);
              const p = window.hiraku.player;
              const out = { shown: p.events.map((e) => e.t), recorded: p.events.map((e) => e.t_rec),
                            kinds: p.events.map((e) => e.kind), duration: p.duration };
              await window.hiraku.seek(p.duration);
              out.applied = p.applied;
              out.lastLogTime = [...document.querySelectorAll("#log li .tm")].at(-1).textContent;
              return out;
            }""",
            events,
        )
        self.assertEqual(errors, [])
        self.assertEqual(got["recorded"], [ev["t"] for ev in events])
        gaps = [b - a for a, b in zip(got["shown"], got["shown"][1:])]
        # every gap is stretched past the recorded 0.02 s, and the 60 s silence is cut
        self.assertTrue(all(g >= 0.1 - 1e-9 for g in gaps), gaps)
        self.assertLessEqual(gaps[-1], 3.0 + 1e-9)
        # the agent's words stay on screen at least 2.2 s before the next event
        said = got["kinds"].index("agent.said")
        self.assertGreaterEqual(gaps[said], 2.2 - 1e-9)
        self.assertEqual(got["applied"], len(events))
        # the log shows the recorded t, not the display time
        self.assertEqual(got["lastLogTime"], f"{events[-1]['t']:.1f}s")
        page.close()

    def test_frame_time_is_display_time(self):
        page, errors = self.open("run=sample&t=4")
        got = page.evaluate("() => ({ playhead: window.hiraku.player.playhead, pacing: window.hiraku.player.pacing })")
        self.assertEqual(errors, [])
        self.assertEqual(got, {"playhead": 4, "pacing": True})
        page.close()

    def test_tilt_motion_from_series(self):
        events, info = tilt_events()
        for ev in events:
            self.assertEqual(contract.errors("event", ev), [])
        page, errors = self.open("run=sample&pace=0&t=0")  # recorded t, so times below are the fixture's
        finished_t = next(e["t"] for e in events if e["kind"] == "analysis.finished")
        probe = """() => {
          const b = window.hiraku.bench;
          const lifted = Object.entries(b.wheelObjs).filter(([, o]) => o.ring.visible).map(([id]) => id).sort();
          const arrows = Object.values(b.motionArrowObjs).filter((o) => o.reaction.visible).length;
          return {
            duration: window.hiraku.player.duration,
            animbar: !document.getElementById("animbar").hidden,
            table: b.world.rotation.x,
            tau: b.frame && b.frame.t,
            lifted, arrows,
            lines: document.querySelectorAll("#graph polyline").length,
            note: document.getElementById("limitnote").textContent,
            cam: b.camera.position.toArray(),
          };
        }"""
        page.evaluate("async (ev) => { await window.hiraku.load(ev, 0); }", events)

        # the replay is lengthened so the motion can play to its end
        before = page.evaluate("async (t) => { await window.hiraku.seek(t); return 0; }", finished_t + 3)
        early = page.evaluate(probe)
        self.assertEqual(before, 0)
        self.assertAlmostEqual(early["duration"], finished_t + min(info["t_end"], 12), places=6)
        self.assertTrue(early["animbar"])
        self.assertEqual(early["lifted"], [])
        self.assertEqual(early["arrows"], 4)
        self.assertEqual(early["lines"], 4)

        page.evaluate("async (t) => { await window.hiraku.seek(t); }", early["duration"])
        end = page.evaluate(probe)
        self.assertAlmostEqual(end["tau"], info["t_end"], places=6)
        self.assertEqual(end["lifted"], sorted(info["upper"]))
        self.assertEqual(end["arrows"], 2)
        self.assertGreater(end["table"], 0)  # left side up
        self.assertIn("左前", end["note"])

        # the camera follows the vehicle the same way whatever order frames are drawn in
        page.evaluate("async (t) => { await window.hiraku.seek(t); }", finished_t + 3)
        again = page.evaluate(probe)
        for a, b in zip(early["cam"], again["cam"]):
            self.assertAlmostEqual(a, b, places=6)
        self.assertEqual(errors, [])
        page.close()

    def test_video_layout_keeps_the_subtitle_band_empty(self):
        page, errors = self.open("run=sample&t=6&video=1")
        got = page.evaluate(
            """() => {
              const bottoms = [...document.querySelectorAll('#stage > *:not(.subband):not([hidden])')]
                .map((el) => el.getBoundingClientRect())
                .filter((r) => r.height > 0)
                .map((r) => r.bottom);
              return { maxBottom: Math.max(...bottoms), status: getComputedStyle(document.getElementById('statusrow')).display };
            }"""
        )
        self.assertEqual(errors, [])
        self.assertLessEqual(got["maxBottom"], 1080 - 170 + 0.5)
        self.assertEqual(got["status"], "none")
        page.close()


if __name__ == "__main__":
    unittest.main()
