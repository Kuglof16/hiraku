"""Open the screen in a headless browser and replay the contract sample.

Skipped unless HIRAKU_WEB_BROWSER=1 and Playwright is installed (it is not
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
from tests.web.safe_side_fixture import safe_side_events

try:
    from playwright.sync_api import sync_playwright
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
        "colors": {"night": "#131311", "tile_bg": "#f7f4ec", "tile_border": "#3b3934"},
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
    }


@unittest.skipUnless(ENABLED, "set HIRAKU_WEB_BROWSER=1 with Playwright installed")
class BrowserReplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server("127.0.0.1", 0, "runs-none", quiet=True)
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

    def open(self, query):
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.goto(f"{self.base}/?{query}")
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
                cmpLines: [...document.querySelectorAll("#log li")].filter((li) => li.textContent.includes("照合:")).map((li) => li.textContent),
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
        self.assertIn("bad", got["cmpCard"])
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
        self.assertIn("bad", first["cmpCard"])
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
        self.assertIn("bad", end["cmpCard"])  # the comparison stays a mismatch
        self.assertIn("食い違い", end["badge"])
        self.assertEqual(end["mark"], "安全側で判定")
        self.assertIn("one rail, so it stays above", end["why"])
        self.assertIn("予測", end["used"][0])
        self.assertIn("150", end["used"][1])
        self.assertIn("予測の値", end["verdict"])
        self.assertIn("合格", end["verdict"])
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
        page, errors = self.open("run=sample&pace=0&t=0")
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
                agents: document.querySelectorAll("#agents li").length,
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
        server = make_server("127.0.0.1", 0, runs, quiet=True, saved_dir=None)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            for look in ("zu", "real"):
                page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(f"{base}/?run={run.name}&pace=0&t=1e6&look={look}")
                page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=30000)
                page.evaluate("window.hiraku.ready")
                page.click('#tests button[data-lc="P1-step100"]')
                got = page.evaluate(
                    """async () => {
                      await window.hiraku.seek(window.hiraku.player.playhead);
                      const b = window.hiraku.bench;
                      return { step: b.sceneDesc?.step ?? null, shadows: b.renderer.shadowMap.enabled,
                               limitButton: !!document.querySelector('#toggles [data-toggle="limit"]'),
                               standard: b.members[Object.keys(b.members)[0]].mesh.material.type };
                    }"""
                )
                self.assertEqual(errors, [], look)
                self.assertEqual(got["step"]["h"], 100.0)
                self.assertGreater(got["step"]["x0"], 1000.0)  # the step stands ahead of the front wheels
                self.assertFalse(got["limitButton"])  # climbing lifts a wheel: not a limit on a step
                self.assertEqual(got["shadows"], look == "real")
                self.assertEqual(got["standard"], "MeshStandardMaterial" if look == "real" else "MeshLambertMaterial")
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
                       command=fake_command(SAMPLE_EVENTS, run_id, 2.0), reader=fake_reader, wait_s=20)
        server = make_server("127.0.0.1", 0, tmp / "runs", quiet=True, saved_dir=None, launcher=lau)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{base}/?list=1")
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
            page.wait_for_url(f"**/?run={run_id}", timeout=30000)
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
            page.goto(f"{base}/cut.html?cut=n05&run={run}&t=0.5")
            page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
            page.evaluate("window.hiraku.ready")
            js = """async (t) => {
              await window.hiraku.seek(t);
              const box = (el) => { const r = el.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; };
              const tiles = [...document.querySelectorAll('[data-box^="n05.tile."]')].filter((el) => el.classList.contains("tile"));
              return { duration: window.hiraku.player.duration,
                       hero: getComputedStyle(document.querySelector('[data-box="n05.hero"]')).visibility,
                       tiles: tiles.map((el) => ({ key: el.dataset.box, box: box(el), shown: getComputedStyle(el).visibility,
                                                    rate: el.querySelector(".rate").textContent })) };
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
            if given:
                self.assertTrue(all(t["rate"].startswith("×") for t in late["tiles"] if t["key"] not in ("n05.tile.nvh", "n05.tile.fatigue", "n05.tile.heat")))
            page.close()
        finally:
            server.shutdown()
            server.server_close()
            shutil.rmtree(tmp)

    def test_opens_the_saved_run_without_run_in_the_url(self):
        tmp = Path(tempfile.mkdtemp())
        run_id = "20261106-100000-offroad-4w"
        (tmp / "showcase" / run_id).mkdir(parents=True)
        shutil.copyfile(SAMPLE_EVENTS, tmp / "showcase" / run_id / "events.jsonl")
        server = make_server("127.0.0.1", 0, tmp / "runs", quiet=True, saved_dir=tmp / "showcase")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
            page.goto(f"{base}/?pace=0&t=9")
            page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=30000)
            page.evaluate("window.hiraku.ready")
            got = page.evaluate(
                """() => ({ applied: window.hiraku.player.applied, list: !document.getElementById("runlist").hidden,
                           link: document.querySelector("#mode a.runid").getAttribute("href") })"""
            )
            self.assertEqual(got, {"applied": 15, "list": False, "link": "?list=1"})
            page.goto(f"{base}/?list=1")
            page.wait_for_selector("#runlist h3")
            listing = page.evaluate(
                """() => ({ heads: [...document.querySelectorAll("#runlist h3")].map((h) => h.textContent),
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
                flow: [...document.querySelectorAll("#grid-head .vbox")].map((b) => b.textContent),
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
        self.assertEqual(got["flow"], ["版 1 赤 4", "版 2 赤 2"])
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
        self.assertEqual((got["min"], got["max"]), (31.0, 60.0))  # coldest part up to the criterion limit
        self.assertIn("許容 60 ℃", got["legend"])
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
