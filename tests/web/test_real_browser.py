"""The real look (?look=real): its materials, the Poly Haven files when they are in
web/assets/polyhaven/ (they are added by hand, not committed), its fall-back light and
textures when they are not (a server that lists none), and the contour keeping the
legend's colours (unlit, outside the tone mapping and the post-processing) in both looks.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed (see test_browser.py).
"""

import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import SAMPLE_EVENTS, list_assets, make_server
from tests.web.shell_fixture import shell_events

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None
ROOT = Path(__file__).resolve().parents[2]
DESIGN = ROOT / "data" / "designs" / "offroad-4w-v1.json"
TOLERANCE = 2  # per channel, 0-255
# web/js/look.js ASSETS: role -> (Poly Haven id, files)
ASSET_FILES = {
    "sky": ("kloofendal_48d_partly_cloudy_puresky", ["kloofendal_48d_partly_cloudy_puresky_1k.hdr"]),
    "dirt": ("dirt_floor", ["dirt_floor_diff_1k.jpg", "dirt_floor_nor_gl_1k.jpg", "dirt_floor_arm_1k.jpg"]),
    "concrete": ("brushed_concrete", ["brushed_concrete_diff_1k.jpg", "brushed_concrete_nor_gl_1k.jpg", "brushed_concrete_arm_1k.jpg"]),
}

# The canvas pixel at points of the contour and the legend's swatch for the same value.
# Members: the middle of the top of each member with one value (a member coloured from its two
# nodes' values blends between bands along its length). Shell: the centres of elements
# that are big enough on screen and seen first along the camera's ray. The arrows, the centre-of-gravity
# mark and the part boxes are turned off first: they may stand in front of a member.
PIXELS = """async (shell) => {
  const b = window.hiraku.bench;
  for (const k of ["arrows", "cg", "parts"]) {
    const btn = document.querySelector(`#toggles [data-toggle=${k}]`);
    if (btn?.classList.contains("on")) btn.click();
  }
  // a shell's elements are big enough on screen near its largest one (the legend's 最大の所に寄る)
  if (shell && !b.focused) document.querySelector("#legend [data-act=zoom]").click();
  await window.hiraku.seek(window.hiraku.player.playhead);
  const ct = b.keys.contour;
  const rgb = (el) => getComputedStyle(el).backgroundColor.match(/\\d+/g).slice(0, 3).map(Number);
  const bands = [...document.querySelectorAll("#legend .bar .sw")].map(rgb).reverse(); // band 0 first
  const overEl = document.querySelector("#legend .lim span");
  const over = overEl ? rgb(overEl) : null;
  const swatch = (v) => {
    if (v > ct.max + Math.abs(ct.max) * 1e-9) return over;
    const n = bands.length;
    return bands[Math.min(n - 1, Math.max(0, Math.floor(((v - ct.min) / (ct.max - ct.min)) * n)))];
  };
  // the element edges (ink at 0.3) are left out: the faces' colours are what is compared
  // (under a gusset plate the wall's edges show through it, as they did before the overlay)
  for (const s of Object.values(b.shellObjs)) s.edges.visible = false;
  b.render();
  for (const s of Object.values(b.shellObjs)) s.edges.visible = true;
  const c = b.renderer.domElement;
  const copy = document.createElement("canvas");
  copy.width = c.width;
  copy.height = c.height;
  const g = copy.getContext("2d");
  g.drawImage(c, 0, 0);
  const px = (p) => Array.from(g.getImageData(Math.round(p.x * c.width / b.size.w), Math.round(p.y * c.height / b.size.h), 1, 1).data.slice(0, 3));
  const out = [];
  if (shell) {
    // the centres of elements that are the first thing seen there (a ray from the camera)
    const THREE = await import("three");
    const o = Object.values(b.shellObjs).find((s) => s.group.visible);
    const rec = ct.mesh.rec;
    const solids = [];
    b.scene.traverseVisible((x) => x.isMesh && x.layers.test(b.camera.layers) && !x.material.transparent && solids.push(x));
    const rc = new THREE.Raycaster();
    const step = Math.max(1, Math.floor(rec.elements.length / 1500));
    for (let e = 0; e < rec.elements.length; e += step) {
      const pts = rec.elements[e].map((n) => new THREE.Vector3(...rec.nodes_mm[n]).applyMatrix4(o.faces.matrixWorld));
      const c3 = pts.reduce((a, p) => a.add(p), new THREE.Vector3()).multiplyScalar(1 / pts.length);
      const scr = (p) => { const q = p.clone().project(b.camera); return { x: (q.x * 0.5 + 0.5) * b.size.w, y: (-q.y * 0.5 + 0.5) * b.size.h, q }; };
      const c = scr(c3);
      if (Math.abs(c.q.x) > 0.95 || Math.abs(c.q.y) > 0.95) continue;
      // at least 4 px from the centre to each side on screen (the element edges are drawn there)
      const sc = pts.map(scr);
      const side = (a, d) => { const dx = d.x - a.x, dy = d.y - a.y, L = dx * dx + dy * dy || 1;
        const u = Math.max(0, Math.min(1, ((c.x - a.x) * dx + (c.y - a.y) * dy) / L)); return Math.hypot(c.x - a.x - u * dx, c.y - a.y - u * dy); };
      if (sc.some((a, i) => side(a, sc[(i + 1) % sc.length]) < 4)) continue;
      rc.setFromCamera(new THREE.Vector2(c.q.x, c.q.y), b.camera);
      const hit = rc.intersectObjects(solids, false)[0];
      if (!hit || hit.object !== o.faces || o.vElem[hit.faceIndex * 3] !== e) continue;
      const v = ct.mesh.elem[e];
      out.push({ id: `elem ${e}`, v, pixel: px(c), legend: swatch(v) });
    }
  } else {
    for (const [id, info] of Object.entries(b.members)) {
      const v = ct.values?.[id];
      if (typeof v !== "number") continue;
      // the middle of the member's top face (its centre can fall on the ink edge between two faces)
      const top = info.mid.clone().add(new info.mid.constructor(0, 0, info.height / 2));
      out.push({ id, v, pixel: px(b.screenOf(top.applyMatrix4(b.body.matrix))), legend: swatch(v) });
    }
  }
  return { checks: out, bands: bands.length, post: !!b.post, toneMapping: b.renderer.toneMapping };
}"""


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class RealLookBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        runs = cls.tmp / "runs"
        cls.shell_events = shell_events()[0]
        cls.shell_run = cls.shell_events[0]["run_id"]
        (runs / cls.shell_run).mkdir(parents=True)
        (runs / cls.shell_run / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in cls.shell_events), encoding="utf-8")
        cls.server = make_server("127.0.0.1", 0, runs, quiet=True, saved_dir=None, launcher=False)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        # the same screen with no asset listed: the fall-back path
        cls.bare = make_server("127.0.0.1", 0, runs, quiet=True, saved_dir=None, launcher=False, assets=False)
        cls.bare_base = f"http://127.0.0.1:{cls.bare.server_address[1]}"
        threading.Thread(target=cls.bare.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        for srv in (cls.server, cls.bare):
            srv.shutdown()
            srv.server_close()
        shutil.rmtree(cls.tmp)

    def open(self, query, base=None):
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.goto(f"{base or self.base}/?{query}")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
        page.evaluate("window.hiraku.ready")
        return page, errors

    def assert_legend_colours(self, got, look):
        self.assertTrue(got["checks"], look)
        for c in got["checks"]:
            self.assertIsNotNone(c["legend"], c)
            for ch in range(3):
                self.assertLessEqual(abs(c["pixel"][ch] - c["legend"][ch]), TOLERANCE, f"{look} {c}")

    def test_beam_contour_has_the_legend_colours(self):
        for look, quality in (("zu", None), ("real", None), ("real", "low")):
            q = f"&quality={quality}" if quality else ""
            page, errors = self.open(f"run=sample&pace=0&t=9&look={look}{q}")
            got = page.evaluate(PIXELS, False)
            self.assertEqual(errors, [], look)
            self.assertEqual(got["bands"], 10)
            self.assertEqual(got["post"], look == "real" and quality != "low")
            self.assert_legend_colours(got, f"{look} {quality}")
            # every member of the sample has a value
            self.assertEqual(len(got["checks"]), 7)
            page.close()

    def test_shell_contour_has_the_legend_colours(self):
        for look in ("zu", "real"):
            page, errors = self.open(f"run={self.shell_run}&pace=0&t=1e6&look={look}")
            page.evaluate("() => window.hiraku.seek(1e6)")
            got = page.evaluate(PIXELS, True)
            self.assertEqual(errors, [], look)
            self.assertGreaterEqual(len(got["checks"]), 20, look)
            self.assert_legend_colours(got, look)
            page.close()

    def demo_events(self):
        design = json.loads(DESIGN.read_text(encoding="utf-8"))
        src = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
        return [src[0], {**src[1], "data": design}]

    def test_assets_are_used_when_they_are_there(self):
        files = list_assets(ROOT / "web")
        if not files:
            self.skipTest("no Poly Haven file in web/assets/polyhaven/ (they are added by hand)")
        page, errors = self.open("run=sample&pace=0&t=0&look=real")
        got = page.evaluate(
            """async (events) => {
              await window.hiraku.load(events, 1);
              const b = window.hiraku.bench;
              await b.look.assetsReady;
              await window.hiraku.seek(1);
              let ground = null;
              b.ground.traverse((x) => { if (!ground && x.isMesh) ground = x.material; });
              return { assets: b.look.assets, intensity: b.scene.environmentIntensity,
                       ground: ground && { map: ground.map?.image?.width ?? null, normal: !!ground.normalMap, arm: ground.roughnessMap === ground.aoMap && !!ground.aoMap } };
            }""",
            self.demo_events(),
        )
        self.assertEqual(errors, [])
        expect = {role: all(f"{a}/{f}" in files for f in names) for role, (a, names) in ASSET_FILES.items()}
        self.assertEqual(got["assets"], expect)
        if expect["sky"]:
            self.assertEqual(got["intensity"], 0.8)  # look.js: the HDRI's intensity
        if expect["dirt"]:
            self.assertEqual(got["ground"], {"map": 1024, "normal": True, "arm": True})
        page.close()

    def test_materials_and_fall_backs_without_assets(self):
        events = self.demo_events()
        page, errors = self.open("run=sample&pace=0&t=0&look=real", self.bare_base)
        got = page.evaluate(
            """async (events) => {
              await window.hiraku.load(events, 1);
              const b = window.hiraku.bench;
              await b.look.assetsReady;
              const files = await (await fetch("api/assets")).json();
              const finishOf = (kind, sub) => {
                const o = b.partObjs[Object.keys(b.partObjs).find((id) => b.partObjs[id].kind === kind)];
                let m = null;
                o?.group.traverse((x) => { if (!m && x.isMesh && (sub === undefined || x.material.userData.sub === sub)) m = x.material; });
                return m && { type: m.type, finish: m.userData.finish, clearcoat: m.clearcoat ?? null, sheen: m.sheen ?? null, normal: !!m.normalMap };
              };
              let tyre = null;
              b.wheels.traverse((x) => { if (!tyre && x.isMesh && x.material.userData.finish === "rubber") tyre = x.material; });
              const member = b.members[Object.keys(b.members)[0]].mesh.material;
              const lights = []; b.scene.traverse((o) => o.isDirectionalLight && lights.push(o));
              const sun = lights.find((l) => l.castShadow);
              const cam = sun.shadow.camera;
              return {
                files: files.files, assets: b.look.assets,
                envIsRoom: !!b.scene.environment && b.scene.environmentIntensity === 0.55, // look.js: the RoomEnvironment's intensity
                member: { type: member.type, finish: member.userData.finish, clearcoat: member.clearcoat },
                tyre: { finish: tyre.userData.finish, normal: !!tyre.normalMap, normalSize: [tyre.normalMap.image.width, tyre.normalMap.image.height] },
                seat: finishOf("seat"), motor: finishOf("motor"), battery: finishOf("battery"), cargo: finishOf("cargo"),
                shadow: { type: b.renderer.shadowMap.type, w: cam.right - cam.left, h: cam.top - cam.bottom, size: sun.shadow.mapSize.x },
                vehicle: b.bboxIso.getSize(b.bboxIso.min.clone()).toArray(),
                post: !!b.post, ao: !!b.post?.ao, samples: b.post?.composer.renderTarget1.samples,
              };
            }""",
            events,
        )
        self.assertEqual(errors, [])
        # no Poly Haven file listed: the drawn textures and the room light are used
        self.assertEqual(got["files"], [])
        self.assertEqual(got["assets"], {"sky": False, "dirt": False, "concrete": False})
        self.assertTrue(got["envIsRoom"])
        # painted steel tubes (the design's STKR400 / STKM13A): clearcoat over the paint
        self.assertEqual(got["member"], {"type": "MeshPhysicalMaterial", "finish": "painted_steel", "clearcoat": 1})
        self.assertEqual(got["tyre"]["finish"], "rubber")
        self.assertTrue(got["tyre"]["normal"])
        self.assertEqual(got["seat"]["finish"], "vinyl")  # look.js MATERIAL_OF: a seat's cover
        self.assertGreater(got["seat"]["sheen"], 0)
        self.assertEqual(got["motor"]["finish"], "aluminium")
        self.assertEqual(got["battery"]["finish"], "resin")
        self.assertEqual(got["cargo"]["finish"], "wood")
        # the sun's shadow box is fitted to the vehicle (its longest side + the 100 mm and 0.1 m
        # margins on each side, seen from the sun), not a fixed square
        longest = max(got["vehicle"]) / 1000
        self.assertLess(max(got["shadow"]["w"], got["shadow"]["h"]), 1.6 * (longest + 0.4))
        self.assertEqual(got["shadow"]["type"], 2)  # THREE.PCFSoftShadowMap
        self.assertEqual((got["post"], got["ao"], got["samples"]), (True, True, 4))
        page.close()


class AssetListTest(unittest.TestCase):
    def test_lists_only_asset_files_under_polyhaven(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            self.assertEqual(list_assets(tmp), [])
            d = tmp / "assets" / "polyhaven" / "dirt_floor"
            d.mkdir(parents=True)
            for name in ("dirt_floor_diff_1k.jpg", "notes.txt"):
                (d / name).write_bytes(b"x")
            self.assertEqual(list_assets(tmp), ["dirt_floor/dirt_floor_diff_1k.jpg"])
        finally:
            shutil.rmtree(tmp)

    def test_listed_files_are_the_ones_the_screen_reads(self):
        """Whatever is in web/assets/polyhaven/ (it may be empty) is a file look.js names."""
        known = {f"{a}/{f}" for a, names in ASSET_FILES.values() for f in names}
        self.assertLessEqual(set(list_assets(ROOT / "web")), known)


if __name__ == "__main__":
    unittest.main()
