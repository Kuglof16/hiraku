import importlib.util
import json
import math
import re
import socket
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PETS = ROOT / "promo" / "pets"
PET3D = (PETS / "pet3d.js").read_text(encoding="utf-8")

HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None


def has_network(host="cdn.jsdelivr.net", port=443, timeout=3.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def roles_in_pet3d():
    block = PET3D[PET3D.index("const ROLES = ["):]
    block = block[: block.index("];")]
    return re.findall(r'\{ id: "([^"]+)", name: "([^"]+)", color: "(#[0-9a-f]{6})", prop: "(\w+)", propName: "([^"]+)" \}', block)


def bodies_in_pet3d():
    block = PET3D[PET3D.index("const BODIES = {"):]
    block = block[: block.index("};")]
    return re.findall(r'"([^"]+)": \{ kind: "([^"]+)", quantity: "([^"]+)", analysis: "([^"]+)" \}', block)


def stops(src):
    return re.findall(r"#[0-9a-f]{6}", re.search(r"STOPS = \[([^\]]+)\]", src).group(1))


def band_colors_from_colormap_js():
    """BAND_COLORS as web/js/colormap.js computes them (JS Math.round)."""
    cm = (ROOT / "web" / "js" / "colormap.js").read_text(encoding="utf-8")
    bands = int(re.search(r"BANDS = (\d+)", cm).group(1))
    rgb = [[int(h[i:i + 2], 16) for i in (1, 3, 5)] for h in stops(cm)]
    out = []
    for k in range(bands):
        x = min(1.0, max(0.0, (k + 0.5) / bands)) * (len(rgb) - 1)
        i = min(len(rgb) - 2, int(x // 1))
        f = x - i
        c = [math.floor(a + (b - a) * f + 0.5) for a, b in zip(rgb[i], rgb[i + 1])]
        out.append("#" + "".join("%02x" % v for v in c))
    return out


def app_role_ids():
    schema = json.loads((ROOT / "hiraku" / "contract" / "hiraku.schema.json").read_text(encoding="utf-8"))
    pattern = None

    def walk(node):
        nonlocal pattern
        if isinstance(node, dict):
            p = node.get("pattern")
            if isinstance(p, str) and "cae\\." in p and "lead" in p:
                pattern = p
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    m = re.match(r"\^\((.*)\|cae\\\.\((.*)\)\)\$", pattern)
    plain = [x for x in m.group(1).split("|") if x not in ("system", "user")]
    return plain + ["cae." + d for d in m.group(2).split("|")]


class Roles(unittest.TestCase):
    def test_ten_roles_match_the_app_ids(self):
        roles = roles_in_pet3d()
        self.assertEqual(len(roles), 10)
        self.assertEqual([r[0] for r in roles], app_role_ids())

    def test_props_are_distinct(self):
        roles = roles_in_pet3d()
        self.assertEqual(len({r[3] for r in roles}), 10)
        for r in roles:
            self.assertIn(f'case "{r[3]}"', PET3D, r[0])

    def test_colours_come_from_the_repo(self):
        css = (ROOT / "promo" / "screens" / "common.css").read_text(encoding="utf-8")
        app = (ROOT / "web" / "js" / "app.js").read_text(encoding="utf-8")
        for rid, _, colour, _, _ in roles_in_pet3d():
            if rid.startswith("cae."):
                m = re.search(r"--d-%s:\s*(#[0-9a-f]{6})" % rid[4:], css)
            else:
                m = re.search(r'%s: "(#[0-9a-f]{6})"' % rid, app)
            self.assertIsNotNone(m, rid)
            self.assertEqual(m.group(1), colour, rid)

    def test_stress_steps_match_common_css(self):
        css = (ROOT / "promo" / "screens" / "common.css").read_text(encoding="utf-8")
        want = [re.search(r"--s%d:\s*#([0-9a-f]{6})" % i, css).group(1) for i in range(1, 6)]
        got = re.search(r"s: \[([^\]]+)\]", PET3D).group(1)
        self.assertEqual([x.strip()[2:] for x in got.split(",")], want)

    def test_contour_bands_match_colormap_js(self):
        cm = (ROOT / "web" / "js" / "colormap.js").read_text(encoding="utf-8")
        self.assertEqual(re.search(r"BANDS = (\d+)", PET3D).group(1), re.search(r"BANDS = (\d+)", cm).group(1))
        self.assertEqual(stops(PET3D), stops(cm))
        self.assertIn("web/js/colormap.js", PET3D)

    def test_low_poly_mesh_levels(self):
        # three.js IcosahedronGeometry detail 1 -> 80 faces, detail 2 -> 180
        self.assertRegex(PET3D, r"DETAIL = \{ coarse: 1, fine: 2 \}")

    def test_version_mark_is_v3(self):
        viewer = (PETS / "viewer.html").read_text(encoding="utf-8")
        self.assertIn('<div id="ver">v3</div>', viewer)

    def test_each_role_has_its_own_body(self):
        bodies = bodies_in_pet3d()
        self.assertEqual([b[0] for b in bodies], [r[0] for r in roles_in_pet3d()])
        self.assertEqual(len({b[1] for b in bodies}), 10, "body kinds must differ")
        self.assertEqual(len({b[2] for b in bodies}), 10, "contour quantities must differ")
        block = PET3D[PET3D.index("const BUILDERS = {"):]
        block = block[: block.index("};")]
        for rid, kind, _, _ in bodies:
            self.assertRegex(block, r'"%s": body\w+,' % re.escape(kind), rid)
        quantity = {b[0]: b[2] for b in bodies}
        self.assertEqual(quantity["cae.aero"], "速さ (相対)")
        self.assertEqual(quantity["cae.crash"], "塑性ひずみ (相対)")
        self.assertEqual(quantity["cae.strength"], "相対の応力")
        self.assertEqual(quantity["cae.nvh"], "変位 (相対)")
        self.assertEqual(quantity["cae.thermal"], "温度 (相対)")
        # README carries the table of bodies
        readme = (PETS / "README.md").read_text(encoding="utf-8")
        for rid, kind, q, _ in bodies:
            self.assertRegex(readme, r"`%s`[^\n]*`%s`[^\n]*%s" % (re.escape(rid), re.escape(kind), re.escape(q)), rid)

    def test_pose_uses_no_clock_or_random(self):
        for word in ("Date.now", "performance.now", "Math.random", "requestAnimationFrame"):
            self.assertNotIn(word, PET3D)

    def test_plain_script(self):
        self.assertIsNone(re.search(r"^\s*(import|export)\s", PET3D, re.M))
        self.assertIn("window.HirakuPets", PET3D)


@unittest.skipUnless(HAS_PLAYWRIGHT, "needs playwright (the video Python, not the HIRAKU venv)")
@unittest.skipUnless(HAS_PLAYWRIGHT and has_network(), "needs the network (three.js comes from jsDelivr)")
class Viewer(unittest.TestCase):
    def test_ready_resolves(self):
        from playwright.sync_api import sync_playwright

        url = (PETS / "viewer.html").as_uri() + "?view=one&role=cae.crash&state=pass&bg=night&t=1.2"
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 800, "height": 600})
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(url)
                page.wait_for_function("window.petViewer !== undefined", timeout=30000)
                info = page.evaluate("window.petViewer.ready")
                self.assertEqual(info["roles"], 10)
                self.assertAlmostEqual(info["t"], 1.2)
                self.assertIn("t=1.2", page.evaluate("location.search"))
                self.assertEqual(errors, [])
            finally:
                browser.close()

    def test_contour_and_legend(self):
        from playwright.sync_api import sync_playwright

        url = (PETS / "viewer.html").as_uri() + "?view=one&role=cae.strength&state=pass&t=0.4"
        js = """() => {
          const HP = window.HirakuPets;
          const sw = [...document.querySelectorAll('#legend .sw')].map(e => getComputedStyle(e).backgroundColor);
          const tk = [...document.querySelectorAll('#legend .tk')].map(e => e.textContent);
          return { bands: HP.BANDS, colors: HP.BAND_COLORS, sw, tk,
                   legend: getComputedStyle(document.getElementById('legend')).display,
                   ramp: [0, 1.0, 2.0, 3.3, 4.5].map(t => HP.loadFactor(t, 'working')),
                   still: HP.loadFactor(2.0, 'pass') };
        }"""
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 800, "height": 600})
                page.goto(url)
                page.wait_for_function("window.petViewer !== undefined", timeout=30000)
                page.evaluate("window.petViewer.ready")
                r = page.evaluate(js)
                want = band_colors_from_colormap_js()
                self.assertEqual(r["bands"], 10)
                self.assertEqual(r["colors"], want)
                # legend: 10 swatches, highest band on top, 11 ticks 0.0 .. 1.0
                rgb = ["rgb(%d, %d, %d)" % tuple(int(h[i:i + 2], 16) for i in (1, 3, 5)) for h in want]
                self.assertEqual(r["sw"], rgb[::-1])
                self.assertEqual(r["tk"], ["%.1f" % (i / 10) for i in range(11)])
                self.assertEqual(r["legend"], "block")
                self.assertEqual(page.evaluate("document.querySelector('#legend .ttl').textContent"), "相対の応力")
                # working steps the load 0 -> 1, other states carry the full load
                self.assertEqual(r["ramp"][0], 0)
                self.assertTrue(0 < r["ramp"][1] < r["ramp"][2] < 1)
                self.assertEqual(r["ramp"][3], 1)
                self.assertEqual(r["ramp"][4], 0)
                self.assertEqual(r["still"], 1)

                # no legend in the grid view
                page.goto(url.replace("view=one", "view=grid"))
                page.wait_for_function("window.petViewer !== undefined", timeout=30000)
                page.evaluate("window.petViewer.ready")
                self.assertEqual(page.evaluate("getComputedStyle(document.getElementById('legend')).display"), "none")

                # the legend title is the quantity of the role
                for role, title in (("cae.aero", "速さ (相対)"), ("cae.crash", "塑性ひずみ (相対)"), ("cae.thermal", "温度 (相対)")):
                    page.goto(url.replace("cae.strength", role))
                    page.wait_for_function("window.petViewer !== undefined", timeout=30000)
                    page.evaluate("window.petViewer.ready")
                    self.assertEqual(page.evaluate("document.querySelector('#legend .ttl').textContent"), title, role)
            finally:
                browser.close()

    def test_values_follow_the_loads(self):
        # strength: the stress contour follows the loads (v2 model)
        from playwright.sync_api import sync_playwright

        # build every pet in the viewer page (it has three.js on its import map)
        page_url = (PETS / "viewer.html").as_uri() + "?view=grid&t=0"
        js = """async () => {
          const THREE = await import('three');
          const out = {};
          for (const mesh of ['coarse', 'fine']) {
            for (const r of window.HirakuPets.ROLES.filter((r) => r.id === 'cae.strength')) {
              const p = window.HirakuPets.create(THREE, { role: r.id, mesh });
              const f = p.field;
              const node = (n) => f.loads.find(l => l.name === n).node;
              const argmax = (a) => a.indexOf(Math.max(...a));
              const top = f.nodes.reduce((b, q, i) => (q[1] > f.nodes[b][1] ? i : b), 0);
              out[mesh + ':' + r.id] = {
                counts: p.counts,
                maxSettled: Math.max(...f.settled),
                argSettled: argmax(f.settled), armR: node('armR'),
                argMis: argmax(f.mismatch), legL: node('legL'),
                top: f.settled[top], legR: f.settled[node('legR')],
                mis: p.contour(1, 'mismatch').max, work: p.contour(0, 'working').max,
              };
              p.dispose();
            }
          }
          return out;
        }"""
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 400, "height": 300})
                page.goto(page_url)
                page.wait_for_function("window.petViewer !== undefined", timeout=30000)
                page.evaluate("window.petViewer.ready")
                res = page.evaluate(js)
            finally:
                browser.close()
        self.assertEqual(len(res), 2)
        for k, v in res.items():
            want = {"nodes": 42, "elements": 80} if k.startswith("coarse") else {"nodes": 92, "elements": 180}
            self.assertEqual(v["counts"], want, k)
            self.assertAlmostEqual(v["maxSettled"], 1.0, places=6, msg=k)
            # the prop arm root carries the largest value, the head top a small one
            self.assertEqual(v["argSettled"], v["armR"], k)
            self.assertLess(v["top"], 0.15, k)
            self.assertGreater(v["legR"], 0.5, k)
            # mismatch: the concentration moves to the left leg root
            self.assertEqual(v["argMis"], v["legL"], k)
            self.assertGreater(v["mis"], 1.0, k)
            self.assertEqual(v["work"], 0, k)

    def test_bodies_per_role(self):
        from playwright.sync_api import sync_playwright

        page_url = (PETS / "viewer.html").as_uri() + "?view=grid&t=0"
        js = """async () => {
          const THREE = await import('three');
          const HP = window.HirakuPets;
          // contour meshes = the body itself (not flow lines, props or marks)
          const isBody = (o) => o.isMesh && o.material && o.material.userData && o.material.userData.hk;
          const snap = (p) => {
            const out = [];
            p.group.updateMatrixWorld(true);
            p.group.traverse((o) => {
              if (isBody(o)) out.push(Array.from(o.geometry.attributes.position.array), Array.from(o.geometry.attributes.aVal.array));
            });
            return out;
          };
          const height = (p) => {
            const box = new THREE.Box3();
            p.group.updateMatrixWorld(true);
            p.group.traverse((o) => { if (isBody(o)) box.expandByObject(o); });
            return box.max.y - box.min.y;
          };
          const out = {};
          for (const r of HP.ROLES) {
            const a = HP.create(THREE, { role: r.id });
            const b = HP.create(THREE, { role: r.id });
            a.update(0, 'idle');
            const h = height(a);
            const states = {};
            let same = true;
            for (const [t, s] of [[1.37, 'working'], [2.9, 'working'], [1.2, 'mismatch'], [0.45, 'pass']]) {
              a.update(t, s); b.update(0.1, 'idle'); b.update(t, s);
              same = same && JSON.stringify(snap(a)) === JSON.stringify(snap(b));
              const c = a.contour(t, s);
              states[s + t] = { quantity: c.quantity, max: c.max, min: c.min, info: c.info.length };
            }
            out[r.id] = { kind: a.body.kind, quantity: a.body.quantity, h, same, states };
            a.dispose(); b.dispose();
          }
          return { out, bodies: HP.BODIES };
        }"""
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 400, "height": 300})
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(page_url)
                page.wait_for_function("window.petViewer !== undefined", timeout=30000)
                page.evaluate("window.petViewer.ready")
                res = page.evaluate(js)
                self.assertEqual(errors, [])
            finally:
                browser.close()
        out = res["out"]
        self.assertEqual(len(out), 10)
        self.assertEqual(len({v["kind"] for v in out.values()}), 10)
        heights = sorted(v["h"] for v in out.values())
        median = heights[5]
        for rid, v in out.items():
            self.assertEqual(v["kind"], res["bodies"][rid]["kind"], rid)
            # same height in the line-up (within 12 % of the median)
            self.assertLess(abs(v["h"] / median - 1), 0.12, (rid, v["h"], median))
            # same t and state -> same geometry and values
            self.assertTrue(v["same"], rid)
            for k, c in v["states"].items():
                self.assertEqual(c["quantity"], v["quantity"], (rid, k))
                self.assertTrue(math.isfinite(c["max"]) and math.isfinite(c["min"]), (rid, k))
                self.assertGreaterEqual(c["min"], -1e-9, (rid, k))
                self.assertGreaterEqual(c["max"], c["min"], (rid, k))
                self.assertGreater(c["info"], 0, (rid, k))


if __name__ == "__main__":
    unittest.main()
