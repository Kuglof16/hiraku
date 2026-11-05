"""The real look of the parts a vehicle people ride in has (tests/web/body_fixture.py): body
panels, split seats, brackets and the roll cage take the materials the table in web/js/look.js
(MATERIAL_OF) gives by kind and id, by part.material (a key into design.materials) and by
part.finish (the contract's six values); plain boxes have
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
from tests.web.body_fixture import ADDED, BASE, added_ids, body_design, body_events, schema_has_part_material

try:
    from tests.web.pages import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None

# kind and id -> the finish the table gives (web/js/look.js MATERIAL_OF; the order is in its comment)
EXPECTED_FINISH = {
    "hood": "body_paint",  # spcc, painted: the body's paint
    "fender_FL": "resin",  # pp, molded: moulded plastic in the body's colour
    "fender_FR": "resin",
    "side_panel_L": "body_paint",
    "side_panel_R": "body_paint",
    "bulkhead": "body_paint",
    "front_body": "body_paint",
    # the diamond plate: the pattern stays whatever the material and finish (look.js MATERIAL_OF, 4)
    "floor": "tread_plate",  # a5052, bare: still the aluminium diamond plate
    "floor_front": "painted_steel",  # ss400, painted (TREAD_SET): painted plate, the paint's colour
    "deck": "tread_plate_steel",  # ss400, no finish (TREAD_SET): the steel diamond plate
    "seat_R_back": "vinyl",
    "seat_R_cushion": "vinyl",
    "seat_L_back": "vinyl",
    "seat_L_cushion": "vinyl",
    "bracket_seat_R_F": "powder_coat",  # ss400, powder_coat
    "bracket_seat_L_B": "powder_coat",
    "bracket_column": "black_steel",  # ss400, painted: the bracket's black coat
    # (bracket_column_stay is gone: the fixture is now on v1, whose column bracket is held by
    # the dash cross member, so the fixture no longer adds a stay part under it)
    "rollbar": "painted_steel",
}
# floor_front and deck are set here, so the expectation does not hang on what data/designs
# gives them (the analysis department's designs give them ss400, painted, since ea47a32)
TREAD_SET = {"floor_front": ("ss400", "painted"), "deck": ("ss400", None)}
TREAD_IDS = ("floor", "floor_front", "deck")


def tread_design():
    """body_design() with floor_front and deck given TREAD_SET's material and finish."""
    design = body_design()
    for p in design["parts"]:
        if p["id"] in TREAD_SET:
            p["material"], finish = TREAD_SET[p["id"]]
            if finish:
                p["finish"] = finish
            else:
                p.pop("finish", None)
    return design

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
    parts[id] = meshes.map((x) => ({ finish: x.material.userData.finish ?? null, normal: x.material.userData.normal ?? null, type: x.material.type, color: "#" + x.material.color.getHexString(),
                                     clearcoat: x.material.clearcoat ?? null, round: x.geometry.userData.round_mm ?? null, verts: x.geometry.attributes.position.count }));
  }
  const members = {};
  for (const [id, info] of Object.entries(b.members)) members[id] = { finish: info.mesh.material.userData.finish ?? null, color: "#" + info.mesh.material.color.getHexString() };
  return { parts, members };
}"""

# The zu look's fingerprint: per part, its meshes' geometry (type, vertex count, bounding box) and material
# (type, colour, opacity); per member, the same. The test is that a change to the real look does
# not change the zu picture, so it draws frozen copies of the designs (tests/web/zu_designs/),
# not data/designs/, which the analysis department regenerates.
# The first hashes were taken with the code before the real look (commit 5d87d49) on the designs
# then in data/designs/. 92a97c6 (analysis: ladder frame, mounts, footwell, occupants as parts)
# regenerated those designs, and the hashes changed with the data alone: with the code of fdc7948,
# the designs of 92a97c6~1 and of 2acab8b still give the first hashes (v1 85a52266..., nodriveshaft
# 73df093e..., tallcargo 1c5cd4ef...), those of 92a97c6 / fdc7948 give new ones. The hashes below
# are retaken with the code of fdc7948 on the copies, which are data/designs/ of fdc7948.
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
ZU_DESIGNS = Path(__file__).resolve().parent / "zu_designs"
# Taken again for two changes together: the zu is drawn from the frozen design copies in
# ZU_DESIGNS (claude/screen-body-fix), and shapes the design does not list are no longer
# drawn (shapes.js from claude/screen-cut-real, in claude/dept-screen-812e37 at 26cc6d9).
# Taken again when occupant segments got their own shapes (tests/web/test_occupant_browser.py).
# The frozen copies are not one box per occupant: each has the torso box (head included) and
# 8 segments per person (thigh, shank, arm, foot, left and right), with rpy_deg. Before, every
# segment was drawn as an upright capsule with a ball and was counted as a seat of its own, so
# under two_cargo the segments were off board (opacity 0.07). Now each segment is one capsule
# or rounded box turned with its box, on board with its person. With the code before the change
# (5708693) the copies still give the old hashes (v1 9ed7fc65..., nodriveshaft c94992b4...,
# tallcargo acf1eb37...); comparing the two fingerprints part by part, only the 16
# "part:occupant_?.<segment>" entries differ in each design: the torsos (no head part, so
# still the capsule and ball), the other parts and the members are the same.
# Taken again when the frame's members became hollow tubes (web/js/tubes.js): with the code before
# (a0c2b23) the copies still give the hashes above (v1 fcef1c24..., nodriveshaft b1936a80...,
# tallcargo 7daf4c4a...); part by part, only the 54 "member:" entries of each design differ (a solid
# BoxGeometry of 24 points from node to node -> the shared unit tube, a BufferGeometry along x 0..1
# stretched by the member's matrix, 120 points for a rect tube with both ends, ...); the parts are the same.
ZU_PRINT = {
    "offroad-4w-v1": "fca41bda2f3f5fe6220e1b69aaf3b683500dbbe06353e9efad14f45cbfc4ba29",
    "offroad-4w-nodriveshaft": "795890128ddecbabcb78ab48c4972204f0b47e12478f2e4828b70b7b059e008c",
    "offroad-4w-tallcargo": "ea452149c5d1219ce8dff62ae336e093622ecda6d9aed085ecaffd3a940bcc75",
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
        page.goto(f"{self.base}/?run=sample&pace=0&t=0&look={look}", wait_until="domcontentloaded")
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
        design = tread_design()
        got = self.look_of("real", design)
        parts = got["parts"]
        for pid, finish in EXPECTED_FINISH.items():
            self.assertTrue(parts[pid], pid)
            self.assertEqual({m["finish"] for m in parts[pid]}, {finish}, pid)
        colour = lambda pid: {m["color"] for m in parts[pid]}
        # the floors and the deck: the diamond plate's pattern on every one; the material and the
        # finish give the colour (aluminium plate, steel plate, the paint's own colour)
        for pid in TREAD_IDS:
            self.assertEqual({m["normal"] for m in parts[pid]}, {"plate"}, pid)
        self.assertEqual(colour("floor"), {"#a9aeb3"})  # look.js FINISH.tread_plate
        self.assertEqual(colour("deck"), {"#7e8388"})  # FINISH.tread_plate_steel
        self.assertEqual(colour("floor_front"), {"#3d4840"})  # FINISH.painted_steel: the paint's colour
        self.assertEqual(len(set().union(*(colour(p) for p in TREAD_IDS))), 3)
        # one body colour on every panel; a thin clearcoat
        panels = ["hood", "fender_FL", "fender_FR", "side_panel_L", "side_panel_R", "bulkhead"]
        self.assertEqual(len(set().union(*(colour(p) for p in panels))), 1)
        self.assertTrue(all(0 < m["clearcoat"] < 1 for p in panels for m in parts[p]))
        # the backrest and the cushion: the same cover
        for side in "RL":
            self.assertEqual(parts[f"seat_{side}_back"][0]["color"], parts[f"seat_{side}_cushion"][0]["color"])
        # brackets are dark (black coat, painted or powder coated in the bracket's colour)
        for pid in ("bracket_column", "bracket_seat_R_F"):
            self.assertLess(int(parts[pid][0]["color"][1:3], 16), 0x40, pid)
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

    def test_part_material_is_looked_up_in_the_design_materials(self):
        """part.material is a key into design.materials; the material there decides (no finish given)."""
        design = body_design()
        by_id = {p["id"]: p for p in design["parts"]}
        for pid, key in (("hood", "a5052"), ("fender_FL", "pp"), ("floor", "epdm"), ("side_panel_L", "no_such_key"), ("bracket_column", "stock_77")):
            by_id[pid]["material"] = key
            by_id[pid].pop("finish", None)
        got = self.look_of("real", design)["parts"]
        finish = lambda pid: {m["finish"] for m in got[pid]}
        self.assertEqual(finish("hood"), {"aluminium"})  # A5052 by its name
        self.assertEqual(finish("fender_FL"), {"resin"})  # PP
        self.assertEqual({m["color"] for m in got["fender_FL"]}, {"#2b2e33"})  # the plastic's own colour
        self.assertEqual(finish("floor"), {"rubber"})  # EPDM
        self.assertEqual(finish("side_panel_L"), {"body_paint"})  # the key is not in materials: kind and id
        self.assertEqual(finish("bracket_column"), {"aluminium"})  # a name that says nothing: its numbers
        # the parts without them keep the table's
        self.assertEqual(finish("seat_R_cushion"), {"vinyl"})

    def test_each_finish_and_material(self):
        """look.js partEntry for each part.finish of the contract, over the kind and id's entry and the material's sort."""
        cases = [
            # (part, expected {finish, color})
            ({"id": "bracket_x", "kind": "bracket", "material": "ss400", "finish": "painted"}, {"finish": "black_steel"}),
            ({"id": "susp", "kind": "suspension", "material": "ss400", "finish": "painted"}, {"finish": "painted_steel", "color": "#2f3438"}),
            ({"id": "motor", "kind": "motor", "material": "a5052", "finish": "painted"}, {"finish": "painted_steel", "color": "#a9b0b6"}),
            ({"id": "bracket_x", "kind": "bracket", "material": "ss400", "finish": "powder_coat"}, {"finish": "powder_coat", "color": "#1b1c1e"}),
            ({"id": "hood", "kind": "body", "material": "spcc", "finish": "powder_coat"}, {"finish": "powder_coat", "color": "#40535f"}),
            ({"id": "bracket_x", "kind": "bracket", "material": "ss400", "finish": "bare"}, {"finish": "bare_steel"}),
            ({"id": "bracket_x", "kind": "bracket", "material": "a5052", "finish": "bare"}, {"finish": "aluminium"}),
            ({"id": "floor", "kind": "body", "material": "a5052", "finish": "bare"}, {"finish": "tread_plate", "normal": "plate"}),
            # the diamond plate keeps its pattern; the material and finish decide colour and metal
            ({"id": "floor", "kind": "body", "material": "ss400"}, {"finish": "tread_plate_steel", "normal": "plate"}),
            ({"id": "floor", "kind": "body", "material": "a5052"}, {"finish": "tread_plate", "normal": "plate"}),
            ({"id": "deck", "kind": "body", "material": "ss400", "finish": "bare"}, {"finish": "tread_plate_steel", "normal": "plate"}),
            ({"id": "floor_front", "kind": "body", "material": "ss400", "finish": "painted"}, {"finish": "painted_steel", "normal": "plate"}),
            ({"id": "step_L", "kind": "body", "material": "ss400", "finish": "galvanized"}, {"finish": "galvanized", "normal": "plate"}),
            ({"id": "deck", "kind": "body", "material": "ss400", "finish": "powder_coat"}, {"finish": "powder_coat", "normal": "plate"}),
            ({"id": "deck", "kind": "body", "finish": "bare"}, {"finish": "tread_plate_steel", "normal": "plate"}),
            ({"id": "bracket_x", "kind": "bracket", "finish": "bare"}, {"finish": "bare_steel"}),
            ({"id": "bracket_x", "kind": "bracket", "material": "a5052", "finish": "anodized"}, {"finish": "anodized"}),
            ({"id": "bracket_x", "kind": "bracket", "material": "ss400", "finish": "galvanized"}, {"finish": "galvanized"}),
            ({"id": "fender_FL", "kind": "body", "material": "pp", "finish": "molded"}, {"finish": "resin", "color": "#40535f"}),
            ({"id": "switch", "kind": "switch", "material": "pp", "finish": "molded"}, {"finish": "resin", "color": "#b33a2a"}),
            ({"id": "bracket_x", "kind": "bracket", "material": "pp", "finish": "molded"}, {"finish": "resin"}),
            # a material and no finish: as the material is used
            ({"id": "hood", "kind": "body", "material": "spcc"}, {"finish": "body_paint"}),
            ({"id": "seat_R_back", "kind": "seat", "material": "pp"}, {"finish": "resin"}),
            ({"id": "rollbar", "kind": "rollbar", "material": "stkm13a"}, {"finish": "painted_steel", "color": "#c4c1b8"}),
            # neither, or a key that is not there: kind and id
            ({"id": "seat_R_back", "kind": "seat"}, {"finish": "vinyl"}),
            ({"id": "hood", "kind": "body", "material": "no_such_key"}, {"finish": "body_paint"}),
            ({"id": "deck", "kind": "body"}, {"finish": "tread_plate", "normal": "plate"}),
        ]
        page, errors = self.open("real")
        got = page.evaluate(
            """async ([cases, materials]) => {
              const { partEntry, PART_FINISHES } = await import("./js/look.js");
              return { finishes: PART_FINISHES, entries: cases.map((p) => {
                const e = partEntry(p, materials);
                return { finish: e.finish, ...(e.color ? { color: e.color } : {}), ...(e.normal ? { normal: e.normal } : {}) };
              }) };
            }""",
            [[c[0] for c in cases], body_design()["materials"]],
        )
        page.close()
        self.assertEqual(errors, [])
        self.assertEqual(set(got["finishes"]), {"painted", "powder_coat", "bare", "anodized", "galvanized", "molded"})
        self.assertEqual({c[0]["finish"] for c in cases if "finish" in c[0]}, set(got["finishes"]))  # every value is tried
        for (part, want), entry in zip(cases, got["entries"]):
            self.assertEqual(entry, want, part)

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
            design = json.loads((ZU_DESIGNS / f"{name}.json").read_text(encoding="utf-8"))
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

    def test_fixture_adds_no_part_the_base_has_and_nothing_that_clashes(self):
        """The added boxes are new ids and overlap none of the base design's parts or members
        (hiraku.solvers.k4_geometry.model.interferences, read here, not changed)."""
        from hiraku.solvers.k4_geometry.model import interferences

        base = json.loads(BASE.read_text(encoding="utf-8"))
        self.assertTrue(added_ids())
        self.assertTrue({p["id"] for p in ADDED}.isdisjoint(p["id"] for p in base["parts"]))
        before = {(a, b) for a, b, _ in interferences(base)}
        self.assertEqual([t for t in interferences(body_design()) if (t[0], t[1]) not in before], [])

    def test_fixture_material_and_finish_are_in_the_contract_shape(self):
        """part.material is a key of design.materials; part.finish is one of the contract's six values."""
        design = body_design()
        finishes = {"painted", "powder_coat", "bare", "anodized", "galvanized", "molded"}
        with_material = [p for p in design["parts"] if "material" in p]
        self.assertGreaterEqual(len(with_material), 10)
        for p in with_material:
            self.assertIn(p["material"], design["materials"], p["id"])
        self.assertEqual({p["finish"] for p in design["parts"] if "finish" in p}, {"painted", "powder_coat", "bare", "molded"})
        self.assertLessEqual({p["finish"] for p in design["parts"] if "finish" in p}, finishes)

    def test_fixture_passes_the_contract_schema(self):
        if not schema_has_part_material():
            self.skipTest("this branch's contract has no part.material / part.finish yet (lead commit a4ad2f6 not merged)")
        from hiraku import contract

        self.assertEqual(contract.errors("design", body_design()), [])


if __name__ == "__main__":
    unittest.main()
