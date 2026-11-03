"""The real look of the parts a vehicle people ride in has (tests/web/body_fixture.py): body
panels, split seats, brackets and the roll cage take the materials the table in web/js/look.js
(MATERIAL_OF) gives by kind and id; part.material, when a part has it, wins; plain boxes have
rounded edges in the real look only; the diagram look (zu) of the designs that were there
before is drawn as it was; and a part's contour colour is the legend's colour on a rounded box.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
"""

import copy
import hashlib
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import SAMPLE_EVENTS, make_server
from tests.web.body_fixture import body_design, body_events

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None
ROOT = Path(__file__).resolve().parents[2]

# kind and id -> the finish the table gives (web/js/look.js MATERIAL_OF; the order is in its comment)
EXPECTED_FINISH = {
    "hood": "body_paint",
    "fender_FL": "body_paint",
    "fender_FR": "body_paint",
    "side_panel_L": "body_paint",
    "side_panel_R": "body_paint",
    "bulkhead": "body_paint",
    "front_body": "body_paint",
    "floor": "tread_plate",
    "floor_front": "tread_plate",
    "deck": "tread_plate",
    "seat_R_back": "vinyl",
    "seat_R_cushion": "vinyl",
    "seat_L_back": "vinyl",
    "seat_L_cushion": "vinyl",
    "bracket_seat_R_F": "black_steel",
    "bracket_seat_L_B": "black_steel",
    "bracket_column": "black_steel",
    "bracket_column_stay": "black_steel",
    "rollbar": "painted_steel",
}

# web/js/shapes.js ROUND: the corner radius is 0.35 of the thinnest side, at most 40 mm
def round_radius(size):
    return min(40.0, 0.35 * min(size))


# Each part's own meshes (not the pieces like a spring) and each member: finish, colour, rounding.
LOOK_OF = """async (events) => {
  await window.hiraku.load(events, 1);
  const b = window.hiraku.bench;
  await window.hiraku.seek(1);
  const parts = {};
  for (const [id, o] of Object.entries(b.partObjs)) {
    const meshes = [];
    o.group.traverse((x) => { if (x.isMesh && !x.isLineSegments2 && !x.material.userData.sub && x.layers.mask & 1) meshes.push(x); });
    parts[id] = meshes.map((x) => ({ finish: x.material.userData.finish ?? null, type: x.material.type, color: "#" + x.material.color.getHexString(),
                                     clearcoat: x.material.clearcoat ?? null, round: x.geometry.userData.round_mm ?? null, verts: x.geometry.attributes.position.count }));
  }
  const members = {};
  for (const [id, info] of Object.entries(b.members)) members[id] = { finish: info.mesh.material.userData.finish ?? null, color: "#" + info.mesh.material.color.getHexString() };
  return { parts, members };
}"""

# The zu look's fingerprint: per part, its meshes' geometry (type, vertex count, bounding box) and material
# (type, colour, opacity); per member, the same. Drawn with the code before this change
# (commit 5d87d49) for the designs in ZU_PRINT, and again with this change: the same.
ZU_FINGERPRINT = """async (events) => {
  await window.hiraku.load(events, 1);
  const b = window.hiraku.bench;
  await window.hiraku.seek(1);
  const out = {};
  const bbox = (g) => { g.computeBoundingBox(); return [...g.boundingBox.min.toArray(), ...g.boundingBox.max.toArray()].map((v) => Math.round(v * 100) / 100); };
  const mesh = (x) => [x.geometry.type, x.geometry.attributes.position.count, bbox(x.geometry), x.material.type, "#" + x.material.color.getHexString(), x.material.opacity, !!x.material.transparent];
  for (const [id, o] of Object.entries(b.partObjs)) { const l = []; o.group.traverse((x) => x.isMesh && l.push(mesh(x))); out["part:" + id] = l; }
  for (const [id, info] of Object.entries(b.members)) out["member:" + id] = mesh(info.mesh);
  return out;
}"""
ZU_PRINT = {
    "offroad-4w-v1": "85a522669957dcaa482d0c8617a509f6a6e125552a4ac980cee3c95e2e7dab20",
    "offroad-4w-nodriveshaft": "73df093e3aef3b2b79df5e824c7ec458b64c21c6a1ed8d552298807b4e9e08e8",
    "offroad-4w-tallcargo": "1c5cd4ef2bd913c86679e9dfd0c13844e1b5ad5ac60c14001a4d2a2d5b208508",
}


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class BodyLookBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.server = make_server("127.0.0.1", 0, cls.tmp / "runs", quiet=True, saved_dir=None, launcher=False, assets=False)
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
        shutil.rmtree(cls.tmp)

    def open(self, look):
        page = self.browser.new_page(viewport={"width": 1280, "height": 800})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.goto(f"{self.base}/?run=sample&pace=0&t=0&look={look}")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
        page.evaluate("window.hiraku.ready")
        return page, errors

    def look_of(self, look, design):
        page, errors = self.open(look)
        got = page.evaluate(LOOK_OF, body_events(design))
        page.close()
        self.assertEqual(errors, [], look)
        return got

    def test_parts_take_the_finish_of_their_kind_and_id(self):
        design = body_design()
        got = self.look_of("real", design)
        parts = got["parts"]
        for pid, finish in EXPECTED_FINISH.items():
            self.assertTrue(parts[pid], pid)
            self.assertEqual({m["finish"] for m in parts[pid]}, {finish}, pid)
        colour = lambda pid: {m["color"] for m in parts[pid]}
        # one body colour on every panel; a thin clearcoat
        panels = ["hood", "fender_FL", "fender_FR", "side_panel_L", "side_panel_R", "bulkhead"]
        self.assertEqual(len(set().union(*(colour(p) for p in panels))), 1)
        self.assertTrue(all(0 < m["clearcoat"] < 1 for p in panels for m in parts[p]))
        # the backrest and the cushion: the same cover
        for side in "RL":
            self.assertEqual(parts[f"seat_{side}_back"][0]["color"], parts[f"seat_{side}_cushion"][0]["color"])
        # brackets are dark (black coat)
        dark = int(parts["bracket_column"][0]["color"][1:3], 16)
        self.assertLess(dark, 0x40)
        # the cage: painted tube, a colour apart from the frame's, the same on its members and its part
        members = got["members"]
        cage = {k: v for k, v in members.items() if k.startswith("rollbar_")}
        frame = {k: v for k, v in members.items() if not k.startswith("rollbar_")}
        self.assertTrue(cage and frame)
        self.assertEqual({v["finish"] for v in cage.values()}, {"painted_steel"})
        self.assertEqual({v["finish"] for v in frame.values()}, {"painted_steel"})
        cage_colours = {v["color"] for v in cage.values()}
        self.assertEqual(len(cage_colours), 1)
        self.assertTrue(cage_colours.isdisjoint({v["color"] for v in frame.values()}))
        self.assertEqual(cage_colours, colour("rollbar"))
        # a split seat is drawn as its boxes (one mesh each), not the one-box seat shape
        self.assertEqual(len(parts["seat_R_back"]), 1)

    def test_part_material_wins_over_kind_and_id(self):
        design = body_design()
        by_id = {p["id"]: p for p in design["parts"]}
        by_id["hood"]["material"] = "A5052 aluminium sheet"
        by_id["fender_FL"]["material"] = "PP (polypropylene)"
        by_id["side_panel_L"]["material"] = "SPCC"  # steel: stays the body's paint
        by_id["bracket_column"]["material"] = "SS400"  # steel: stays the black coat
        by_id["floor"]["material"] = "EPDM rubber mat"
        by_id["seat_R_back"]["finish"] = "fabric"
        got = self.look_of("real", design)["parts"]
        finish = lambda pid: {m["finish"] for m in got[pid]}
        self.assertEqual(finish("hood"), {"aluminium"})
        self.assertEqual(finish("fender_FL"), {"resin"})
        self.assertEqual(finish("side_panel_L"), {"body_paint"})
        self.assertEqual(finish("bracket_column"), {"black_steel"})
        self.assertEqual(finish("floor"), {"rubber"})
        self.assertEqual(finish("seat_R_back"), {"fabric"})
        # the parts without them keep the table's
        self.assertEqual(finish("fender_FR"), {"body_paint"})
        self.assertEqual(finish("seat_R_cushion"), {"vinyl"})

    def test_boxes_are_rounded_in_the_real_look_only(self):
        design = body_design()
        size = {p["id"]: p["box"]["size_mm"] for p in design["parts"]}
        real = self.look_of("real", design)["parts"]
        zu = self.look_of("zu", design)["parts"]
        boxes = ["hood", "fender_FL", "side_panel_R", "bulkhead", "floor", "seat_R_back", "seat_L_cushion", "bracket_column", "bracket_seat_R_F", "rollbar", "reducer"]
        for pid in boxes:
            (r,) = real[pid]
            (z,) = zu[pid]
            self.assertAlmostEqual(r["round"], round_radius(size[pid]), places=6, msg=pid)
            self.assertGreater(r["verts"], 24, pid)
            self.assertEqual((z["round"], z["verts"]), (0, 24), pid)
        # a panel's edge is small, a cushion's large
        self.assertLess(real["hood"][0]["round"], 1)
        self.assertEqual(real["seat_R_cushion"][0]["round"], 35)

    def test_zu_is_drawn_as_before(self):
        src = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
        for name, want in ZU_PRINT.items():
            design = json.loads((ROOT / "data" / "designs" / f"{name}.json").read_text(encoding="utf-8"))
            page, errors = self.open("zu")
            got = page.evaluate(ZU_FINGERPRINT, [src[0], {**src[1], "data": design}])
            page.close()
            self.assertEqual(errors, [], name)
            digest = hashlib.sha256(json.dumps(got, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            self.assertEqual(digest, want, name)

    def test_part_contour_is_the_legend_colour_on_a_rounded_box(self):
        """A value on a part paints its unlit twin (on the rounded geometry) in the colour scale's colour."""
        page, errors = self.open("real")
        got = page.evaluate(
            """async (events) => {
              await window.hiraku.load(events, 1);
              const b = window.hiraku.bench;
              await window.hiraku.seek(1);
              b.setLoading("empty");
              for (const k of ["arrows", "cg"]) {
                const btn = document.querySelector(`#toggles [data-toggle=${k}]`);
                if (btn?.classList.contains("on")) btn.click();
              }
              const THREE = await import("three");
              const { colorOf } = await import("./js/colormap.js");
              const values = { hood: 45, bulkhead: 75 };
              b.keys.contour = { key: "test", kind: "temperature", parts: values, values: {}, nodes: {}, min: 0, max: 100, limitKnown: false, partLimit: null };
              b.applyPartLook();
              b.render();
              const c = b.renderer.domElement;
              const copy = document.createElement("canvas");
              copy.width = c.width; copy.height = c.height;
              const g = copy.getContext("2d");
              g.drawImage(c, 0, 0);
              const out = [];
              for (const id of ["hood"]) {
                const p = events[1].data.parts.find((q) => q.id === id);
                const r = (p.box.rpy_deg ?? [0, 0, 0]).map((d) => (d * Math.PI) / 180);
                const top = new THREE.Vector3(0, 0, p.box.size_mm[2] / 2).applyEuler(new THREE.Euler(r[0], r[1], r[2], "ZYX")).add(new THREE.Vector3(...p.box.center_mm));
                const s = b.screenOf(top.applyMatrix4(b.body.matrix));
                const px = Array.from(g.getImageData(Math.round(s.x * c.width / b.size.w), Math.round(s.y * c.height / b.size.h), 1, 1).data.slice(0, 3));
                let twin = null;
                b.partObjs[id].group.traverse((x) => { if (x.isMesh && x.userData.twin) twin = x.userData.twin; });
                const want = new THREE.Color(colorOf(values[id], 100, 0));
                // the colour as drawn (sRGB, 0-255): the twin is not tone mapped
                const hex = want.getHex();
                out.push({ id, pixel: px, want: [hex >> 16, (hex >> 8) & 255, hex & 255], twinVisible: !!twin?.visible,
                           twinColor: twin && "#" + twin.material.color.getHexString(), wantHex: "#" + want.getHexString(),
                           sameGeometry: twin?.geometry === twin?.parent?.geometry, rounded: twin?.geometry.userData.round_mm > 0 });
              }
              return out;
            }""",
            body_events(),
        )
        page.close()
        self.assertEqual(errors, [])
        for c in got:
            self.assertTrue(c["twinVisible"], c)
            self.assertTrue(c["sameGeometry"] and c["rounded"], c)
            self.assertEqual(c["twinColor"], c["wantHex"], c)
            for ch in range(3):
                self.assertLessEqual(abs(c["pixel"][ch] - c["want"][ch]), 2, c)


class BodyFixtureTest(unittest.TestCase):
    def test_fixture_parts_are_boxes_in_the_given_shapes(self):
        design = body_design()
        ids = {p["id"]: p for p in design["parts"]}
        self.assertNotIn("seat_R", ids)
        for pid in EXPECTED_FINISH:
            self.assertIn(pid, ids)
            box = ids[pid]["box"]
            self.assertEqual(len(box["center_mm"]), 3)
            self.assertEqual(len(box["size_mm"]), 3)
        # panels: one side is the sheet's thickness
        for pid in ("hood", "fender_FL", "side_panel_L"):
            self.assertEqual(min(ids[pid]["box"]["size_mm"]), 1.2)
        self.assertEqual(ids["hood"]["box"]["rpy_deg"], [0, 8, 0])
        self.assertTrue(any(m.get("part") == "rollbar" for m in design["members"]))
        # the base design file is not changed
        self.assertIsNot(design, body_design())
        self.assertEqual(copy.deepcopy(design), body_design())


if __name__ == "__main__":
    unittest.main()
