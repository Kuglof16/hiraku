"""The cut pages' 3D in the real look (cut.html; workbench3d.js cutView, look.js stage).

- the look of a cut is the layout's `look` key of that cut, "real" when it has none; "zu"
  keeps the diagram (ink lines, no sky); ?look= is over both
- the colours that are read (a contour on the frame, the shell or a part) are the legend's
  colours on the canvas the view is copied to (within 2 of 255 a channel), in the real look
  (an unlit twin over the lit picture, workbench3d.js contourTwin)
- the test's props: steps of concrete with yellow and black paint on the edge the vehicle meets
- without the Poly Haven files the stage falls back to the drawn textures and the
  RoomEnvironment (no sky behind, no haze); with them the sky, the haze and the gravel road
- N04 and N15 draw their tiles at the low quality through one renderer; with
  HIRAKU_WEB_FRAME_TIME=1 and a GPU (ANGLE on D3D11), a frame at 1920 x 1080 takes no more
  than 16 ms (the median of 30)

Skipped unless HIRAKU_WEB_BROWSER=1 and Playwright is installed (as tests.web.test_browser).
The records are the other cut tests' fixtures (shape examples); the layout is the story
test's copy of the promo department's layout.json.
"""

import copy
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import list_assets, make_server
from tests.web.test_browser import ENABLED, sync_playwright
from tests.web.test_cut_grid_browser import three_versions
from tests.web.test_cut_n03_n06_browser import n03_n06_events
from tests.web.test_cut_n07_browser import judged_events
from tests.web.test_cut_story_browser import story_layout

ROOT = Path(__file__).resolve().parents[2]
TOLERANCE = 2  # per channel, 0-255
FRAME_MS = 16.0
RUNS = {"n07": "20261103-160000-n07", "grid": "20261103-170000-grid", "n0306": "20261103-120000-n0306"}
GPU = ["--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist"]
SWIFTSHADER = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]

VIEWS = """() => window.hiraku.views().map((v) => {
  let ink = false;
  v.model.traverse((o) => { if (o === v.inkLines && o.visible) ink = true; });
  let ground = null;
  v.ground.traverse((x) => { if (!ground && x.isMesh) ground = x.material; });
  return {
    drawn: !!v.canvas2d, look: v.look.name, stage: v.look.stage, quality: v.look.quality, sharedPost: !!v.sharedPost,
    assets: v.look.assets, background: !!v.scene.background, fog: !!v.scene.fog, ink,
    envIntensity: v.scene.environmentIntensity,
    ground: ground && { stain: !!ground.userData.stain, mapW: ground.map?.image?.width ?? null, canvas: ground.map?.image instanceof HTMLCanvasElement, normal: !!ground.normalMap },
  };
})"""

# The canvas pixels of each view drawn with a contour, against the legend's colour of the value
# there (colormap.js colorOf: the legend's bands, or the colour over the allowable). Points:
# the middle of the top of a member with one value and the centre of a shell's element, each
# only where the ray from the camera meets that thing first at the point and 2 px round it
# (nothing in front, not on its outline); the centre of a part with a value (its colour is
# drawn over what is in front of it). Each view is drawn again for this without the marks
# laid over everything (the centre of gravity, arrows).
COLOURS = """async () => {
  const THREE = await import("three");
  const { colorOf, BAND_COLORS, OVER_COLOR } = await import("./js/colormap.js");
  const rgbOf = (h) => { const n = parseInt(h.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; };
  const legend = new Set([...BAND_COLORS, OVER_COLOR]);
  const out = [];
  const views = window.hiraku.views();
  views.forEach((b, vi) => {
    const ct = b.keys.contour;
    const c = b.canvas2d;
    if (!ct || !c || !c.isConnected || !c.checkVisibility({ opacityProperty: true, visibilityProperty: true }) || b.modeShape) return;
    const g = c.getContext("2d");
    const kx = c.width / b.size.w;
    const ky = c.height / b.size.h;
    // drawn again without what is laid over everything (the centre-of-gravity mark, arrows,
    // N07's outline round what the version after added)
    const hide = [b.cg, b.arrows, b.motionArrows, b.diffMarks].filter((o) => o && o.visible);
    for (const o of hide) o.visible = false;
    b.renderInto(g, c.width, c.height, kx);
    for (const o of hide) o.visible = true;
    b.scene.updateMatrixWorld();
    const solids = [];
    b.scene.traverseVisible((x) => x.isMesh && x.layers.test(b.camera.layers) && !x.material.transparent && solids.push(x));
    const rc = new THREE.Raycaster();
    const hitAt = (sx, sy) => {
      rc.setFromCamera(new THREE.Vector2((sx / b.size.w) * 2 - 1, -(sy / b.size.h) * 2 + 1), b.camera);
      return rc.intersectObjects(solids, false)[0] ?? null;
    };
    const clear = (p, ok) => p && p.x > 4 && p.y > 4 && p.x < b.size.w - 4 && p.y < b.size.h - 4 &&
      [[0, 0], [2, 0], [-2, 0], [0, 2], [0, -2]].every(([dx, dy]) => ok(hitAt(p.x + dx, p.y + dy)));
    const px = (p) => Array.from(g.getImageData(Math.round(p.x * kx), Math.round(p.y * ky), 1, 1).data.slice(0, 3));
    const push = (kind, id, p, hex) => out.push({ view: vi, look: b.look.name, kind, id, pixel: px(p), want: rgbOf(hex), legend: legend.has(hex) });
    let n = 0;
    const shell = Object.values(b.shellObjs).find((s) => s.group.visible);
    if (shell && ct.mesh) {
      const m = ct.mesh;
      const rec = m.rec;
      const step = Math.max(1, Math.floor(rec.elements.length / 400));
      for (let e = 0; e < rec.elements.length && n < 30; e += step) {
        if (!m.colors && !m.elem) break;
        const pts = rec.elements[e].map((k) => new THREE.Vector3(...rec.nodes_mm[k]).applyMatrix4(shell.faces.matrixWorld));
        const c3 = pts.reduce((a, q) => a.add(q), new THREE.Vector3()).multiplyScalar(1 / pts.length);
        const scr = (v) => { const q = v.clone().project(b.camera); return { x: (q.x * 0.5 + 0.5) * b.size.w, y: (-q.y * 0.5 + 0.5) * b.size.h }; };
        const p = scr(c3);
        // at least 4 px from the centre to each side on screen (the element edges are drawn there)
        const sc = pts.map(scr);
        const side = (a, d) => { const dx = d.x - a.x, dy = d.y - a.y, L = dx * dx + dy * dy || 1;
          const u = Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / L)); return Math.hypot(p.x - a.x - u * dx, p.y - a.y - u * dy); };
        if (sc.some((a, i) => side(a, sc[(i + 1) % sc.length]) < 4)) continue;
        if (!clear(p, (h) => h && h.object === shell.faces && shell.vElem[h.faceIndex * 3] === e)) continue;
        push("element", e, p, m.colors ? m.colors[e] : colorOf(m.elem[e], ct.max, ct.min));
        n += 1;
      }
    } else if (b.model.visible) {
      for (const [id, info] of Object.entries(b.members)) {
        const v = ct.values?.[id];
        if (typeof v !== "number" || n >= 30) continue;
        const top = info.mid.clone().add(new THREE.Vector3(0, 0, info.height / 2)).applyMatrix4(b.body.matrix);
        const p = b.screenOf(top);
        if (!clear(p, (h) => h && h.object === info.mesh)) continue;
        push("member", id, p, colorOf(v, ct.max, ct.min));
        n += 1;
      }
    }
    for (const [id, o] of Object.entries(b.partObjs)) {
      const v = ct.parts?.[id];
      if (typeof v !== "number" || !o.group.visible || !b.parts.visible) continue;
      const p = b.screenOf(o.center.clone().applyMatrix4(b.body.matrix));
      if (!p || p.x < 4 || p.y < 4 || p.x > b.size.w - 4 || p.y > b.size.h - 4) continue;
      push("part", id, p, colorOf(v, ct.max, ct.min));
    }
  });
  return out;
}"""

# The scene's props of every view: concrete steps and their painted edge.
PROPS = """() => window.hiraku.views().map((v) => {
  const steps = [];
  const paint = [];
  v.props.world.traverse((o) => {
    if (o.isMesh && o.material.userData.finish === "concrete") steps.push(o.material.type);
    if (o.isMesh && o.material.userData.paint === "hazard") paint.push({ w: o.material.map.image.width, colorSpace: o.material.map.colorSpace });
  });
  return { look: v.look.name, step: !!(v.sceneDesc?.step || (v.sceneDesc?.objects ?? []).some((o) => o.kind === "step")), steps, paint };
})"""

# The time of a frame: the cut drawn at t, then one pixel read back from the renderer (so
# the GPU has finished); 10 frames to warm up, then the median and the slowest of 30.
FRAME = """(t) => {
  const c = window.hiraku.cut;
  const gl = c.renderer.getContext();
  const px = new Uint8Array(4);
  const one = (tt) => { const t0 = performance.now(); c.draw(tt); gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px); return performance.now() - t0; };
  for (let i = 0; i < 10; i += 1) one(t + i / 60);
  const ms = [];
  for (let i = 0; i < 30; i += 1) ms.push(one(t + i / 30));
  ms.sort((a, b) => a - b);
  const e = gl.getExtension("WEBGL_debug_renderer_info");
  return { median: ms[15], max: ms[29], gpu: e ? gl.getParameter(e.UNMASKED_RENDERER_WEBGL) : "",
           drawn: window.hiraku.views().filter((v) => v.canvas2d && v.canvas2d.checkVisibility({ opacityProperty: true, visibilityProperty: true })).length };
}"""


def with_step_scene(events):
    """n03_n06_events with the high step's climb (ana-0005) carrying its scene as a tool writes
    it (contract: analysis.scene, a step's box). Shape example: the box is chosen, not computed."""
    out = copy.deepcopy(events)
    for e in out:
        if e["kind"] == "analysis.finished" and e["data"]["analysis_id"] == "ana-0005":
            e["data"]["scene"] = [{"kind": "step", "position_mm": [1500.0, 0.0, 125.0], "size_mm": [1000.0, 2400.0, 250.0]}]
    return out


def write_runs(root, events_by_run):
    for run_id, events in events_by_run.items():
        (root / run_id).mkdir(parents=True, exist_ok=True)
        (root / run_id / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")


def with_run_id(events, run_id):
    out = copy.deepcopy(events)
    for e in out:
        e["run_id"] = run_id
    return out


@unittest.skipUnless(ENABLED, "set HIRAKU_WEB_BROWSER=1 with Playwright installed")
class CutRealLookTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.runs = cls.tmp / "runs"
        write_runs(cls.runs, {
            RUNS["n07"]: judged_events(RUNS["n07"])[0],
            RUNS["grid"]: with_run_id(three_versions(), RUNS["grid"]),
            RUNS["n0306"]: with_run_id(with_step_scene(n03_n06_events()), RUNS["n0306"]),
        })
        cls.layout = story_layout()
        cls.servers = []
        cls.base = cls.serve(cls.layout)
        cls.bare = cls.serve(cls.layout, assets=False)
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=SWIFTSHADER)

    @classmethod
    def serve(cls, layout, assets=True):
        lp = cls.tmp / f"layout-{len(cls.servers)}.json"
        lp.write_text(json.dumps(layout, ensure_ascii=False), encoding="utf-8")
        server = make_server("127.0.0.1", 0, cls.runs, quiet=True, saved_dir=None, launcher=False, layout_file=lp, assets=assets)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        for s in cls.servers:
            s.shutdown()
            s.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def open(self, base, query, browser=None):
        page = (browser or self.browser).new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.goto(f"{base}/cut.html?{query}&t=0")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=120000)
        ready = page.evaluate("window.hiraku.ready")
        self.assertNotIn("error", ready, query)
        return page, errors

    def times(self, cut, n=6):
        dur = self.layout[cut]["duration_s"]
        return [round(dur * (k + 0.5) / n, 3) for k in range(n)]

    # ---- the look key -------------------------------------------------------------
    def test_a_cut_without_a_look_key_is_real_and_zu_keeps_the_diagram(self):
        self.assertNotIn("look", self.layout["n07"])
        page, errors = self.open(self.base, f"cut=n07&run={RUNS['n07']}")
        views = page.evaluate(VIEWS)
        page.close()
        self.assertEqual(errors, [])
        self.assertTrue(views)
        for v in views:
            self.assertEqual((v["look"], v["stage"]), ("real", True))
            self.assertFalse(v["ink"])
        # the layout says zu: the diagram as before (ink lines, no sky, no haze)
        layout = copy.deepcopy(self.layout)
        layout["n07"]["look"] = "zu"
        base = self.serve(layout)
        page, errors = self.open(base, f"cut=n07&run={RUNS['n07']}")
        views = page.evaluate(VIEWS)
        page.close()
        self.assertEqual(errors, [])
        for v in views:
            self.assertEqual((v["look"], v["stage"], v["background"], v["fog"], v["sharedPost"]), ("zu", False, False, False, False))
        # ?look= over the layout's key
        page, errors = self.open(base, f"cut=n07&run={RUNS['n07']}&look=real")
        self.assertEqual({v["look"] for v in page.evaluate(VIEWS)}, {"real"})
        page.close()
        page, errors = self.open(self.base, f"cut=n07&run={RUNS['n07']}&look=zu")
        self.assertEqual({v["look"] for v in page.evaluate(VIEWS)}, {"zu"})
        page.close()

    def test_every_cut_with_3d_takes_the_key(self):
        """Each cut page's views are made by cutView with the cut's own name in the layout."""
        layout = copy.deepcopy(self.layout)
        for cut in ("n03", "n04", "n06", "n15"):
            layout[cut]["look"] = "zu"
        base = self.serve(layout)
        for cut, run in (("n03", "n0306"), ("n06", "n0306"), ("n04", "grid"), ("n15", "grid")):
            for b, want in ((self.base, "real"), (base, "zu")):
                page, errors = self.open(b, f"cut={cut}&run={RUNS[run]}")
                views = page.evaluate(VIEWS)
                page.close()
                self.assertEqual(errors, [], cut)
                self.assertTrue(views, cut)
                self.assertEqual({v["look"] for v in views}, {want}, cut)

    # ---- colours that are read -------------------------------------------------------
    def check_colours(self, cut, run, times):
        page, errors = self.open(self.base, f"cut={cut}&run={RUNS[run]}")
        checks = []
        for t in times:
            page.evaluate("(t) => window.hiraku.seek(t)", t)
            checks += [{**c, "t": t} for c in page.evaluate(COLOURS)]
        page.close()
        self.assertEqual(errors, [], cut)
        self.assertTrue(checks, f"{cut}: no point of a contour seen in the real look")
        for c in checks:
            self.assertEqual(c["look"], "real")
            self.assertTrue(c["legend"], c)
            for ch in range(3):
                self.assertLessEqual(abs(c["pixel"][ch] - c["want"][ch]), TOLERANCE, f"{cut} {c}")
        return checks

    def test_n07_shell_stress_has_the_legend_colours(self):
        checks = self.check_colours("n07", "n07", self.times("n07", 4))
        self.assertGreaterEqual(sum(1 for c in checks if c["kind"] == "element"), 20)

    def test_n04_and_n15_tiles_have_the_legend_colours(self):
        # N04 before its assembly box takes the grid's place; N15 through its round
        self.check_colours("n04", "grid", [0.6, 1.0, 1.6])
        self.check_colours("n15", "grid", [1.0, 2.5, 4.0])

    def test_n06_tiles_have_the_legend_colours(self):
        self.check_colours("n06", "n0306", self.times("n06", 8))

    def test_n03_battery_has_the_legend_colours(self):
        checks = self.check_colours("n03", "n0306", self.times("n03", 8))
        self.assertTrue(any(c["kind"] == "part" for c in checks), checks)

    # ---- props ---------------------------------------------------------------------------
    def test_steps_are_concrete_with_a_painted_edge(self):
        page, errors = self.open(self.base, f"cut=n06&run={RUNS['n0306']}")
        props = page.evaluate(PROPS)
        page.close()
        self.assertEqual(errors, [])
        with_step = [p for p in props if p["step"]]
        self.assertTrue(with_step, props)
        for p in with_step:
            self.assertTrue(p["steps"], p)
            self.assertTrue(set(p["steps"]) <= {"MeshStandardMaterial"}, p)
            # the face the vehicle meets and the top along that edge: the stripes (a canvas, sRGB)
            self.assertEqual(len(p["paint"]), 2, p)
            self.assertEqual({(x["w"], x["colorSpace"]) for x in p["paint"]}, {(128, "srgb")})

    # ---- assets ----------------------------------------------------------------------------
    def test_without_the_poly_haven_files_it_falls_back(self):
        page, errors = self.open(self.bare, f"cut=n07&run={RUNS['n07']}")
        checks = []
        for t in self.times("n07", 4):
            page.evaluate("(t) => window.hiraku.seek(t)", t)
            checks += page.evaluate(COLOURS)
        views = page.evaluate(VIEWS)
        page.close()
        self.assertEqual(errors, [])
        self.assertTrue(any(v["drawn"] for v in views))
        for v in views:
            self.assertEqual(v["assets"], {"sky": False, "horizon": False, "gravel": False, "concrete": False})
            self.assertEqual(v["envIntensity"], 0.55)  # look.js: the RoomEnvironment's
            self.assertFalse(v["background"])
            self.assertFalse(v["fog"])
            if v["ground"]:
                self.assertTrue(v["ground"]["canvas"], v)  # the drawn earth
                self.assertTrue(v["ground"]["stain"], v)
        self.assertTrue(checks)
        for c in checks:
            for ch in range(3):
                self.assertLessEqual(abs(c["pixel"][ch] - c["want"][ch]), TOLERANCE, c)

    def test_with_the_poly_haven_files_the_sky_haze_and_gravel(self):
        files = set(list_assets(ROOT / "web"))
        need = {"kloofendal_48d_partly_cloudy/kloofendal_48d_partly_cloudy_1k.hdr", "gravel_road/gravel_road_diff_1k.jpg",
                "gravel_road/gravel_road_nor_gl_1k.jpg", "gravel_road/gravel_road_arm_1k.jpg"}
        if not need <= files:
            self.skipTest("the stage's Poly Haven files are not in web/assets/polyhaven/")
        page, errors = self.open(self.base, f"cut=n07&run={RUNS['n07']}")
        page.evaluate("() => window.hiraku.seek(1)")
        views = page.evaluate(VIEWS)
        page.close()
        self.assertEqual(errors, [])
        self.assertTrue(any(v["drawn"] for v in views))
        for v in views:
            self.assertTrue(v["assets"]["horizon"] and v["assets"]["gravel"], v)
            self.assertFalse(v["assets"]["sky"])  # the horizon's HDRI lights it in the sky's place
            if v["drawn"]:
                self.assertTrue(v["background"] and v["fog"], v)  # set when the view is drawn
            self.assertEqual(v["envIntensity"], 0.45)  # look.js HDRI_INTENSITY
            if v["ground"]:
                self.assertEqual(v["ground"]["mapW"], 1024)
                self.assertTrue(v["ground"]["normal"] and v["ground"]["stain"], v)

    # ---- time of a frame -----------------------------------------------------------------
    def test_n04_and_n15_tiles_are_low_quality_through_one_renderer(self):
        for cut in ("n04", "n15"):
            page, errors = self.open(self.base, f"cut={cut}&run={RUNS['grid']}")
            page.evaluate("() => window.hiraku.seek(1.0)")
            views = page.evaluate(VIEWS)
            one = page.evaluate("() => new Set(window.hiraku.views().map((v) => v.renderer)).size")
            page.close()
            self.assertEqual(errors, [], cut)
            self.assertEqual({(v["look"], v["quality"], v["sharedPost"]) for v in views}, {("real", "low", False)}, cut)
            self.assertEqual(one, 1, cut)

    @unittest.skipUnless(os.environ.get("HIRAKU_WEB_FRAME_TIME") == "1", "set HIRAKU_WEB_FRAME_TIME=1 (needs a GPU and a machine not busy with other work)")
    def test_n04_and_n15_frame_within_16_ms_on_a_gpu(self):
        """The median of 30 frames at 1920 x 1080 (the best of three tries: other work on the
        machine slows some), at times when the tiles are on the stage."""
        browser = self.pw.chromium.launch(args=GPU)
        try:
            for cut, t in (("n04", 1.0), ("n15", 2.5)):
                best = None
                for _ in range(3):
                    page, errors = self.open(self.base, f"cut={cut}&run={RUNS['grid']}", browser)
                    got = page.evaluate(FRAME, t)
                    page.close()
                    self.assertEqual(errors, [], cut)
                    if "SwiftShader" in got["gpu"] or not got["gpu"]:
                        self.skipTest(f"no GPU for the browser ({got['gpu']})")
                    best = got if best is None or got["median"] < best["median"] else best
                print(f"\n  {cut} at {t} s: median {best['median']:.2f} ms, slowest {best['max']:.2f} ms of 30 ({best['gpu']}, {best['drawn']} views)")
                self.assertLessEqual(best["median"], FRAME_MS, f"{cut} {best}")
        finally:
            browser.close()


if __name__ == "__main__":
    unittest.main()
