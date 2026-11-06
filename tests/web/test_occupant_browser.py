"""Occupants on the workbench (web/js/workbench3d.js, web/js/shapes.js occupantSegmentPieces).

An occupant given in body segments (the analysis department's designs from 9ee90a9 on:
"occupant_<seat>" the torso and "occupant_<seat>.<segment>" the head, neck, arms, forearms,
hands, thighs, shanks and feet, boxes with rpy_deg) is drawn one shape per segment, turned
with its box: the head a ball stretched to its box (touching the neck), the segments with a round section a capsule along the box's
longest side, the others a box with rounded edges; none reaches outside its box, and the
only ball is the head's. A person's segments board with the person: the seats are counted
per person, as hiraku/contract/loading.py does. An occupant given as one box (the designs
before) is still drawn as the capsule with a ball for the head, as it was.

The segmented design is frozen here (tests/web/occupant_designs/offroad-4w-v1-9ee90a9.json is
data/designs/offroad-4w-v1.json of 9ee90a9, byte for byte), so these tests do not follow
data/designs/ when the analysis department regenerates it.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
"""

import copy
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

import numpy as np

from hiraku.contract.loading import OCCUPANTS, parts_on_board, person_of
from hiraku.solvers.k0_handcalc.rigid import rpy_matrix
from hiraku.webserve.server import SAMPLE_EVENTS, make_server

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None

HERE = Path(__file__).resolve().parent
SEGMENTED = HERE / "occupant_designs" / "offroad-4w-v1-9ee90a9.json"
ZU_V1 = HERE / "zu_designs" / "offroad-4w-v1.json"  # torso box with the head in it, 8 segments per person
SEGMENTS = ("neck", "head", "arm_L", "forearm_L", "hand_L", "thigh_L", "shank_L", "foot_L",
            "arm_R", "forearm_R", "hand_R", "thigh_R", "shank_R", "foot_R")
ROUND_SECTION = 0.8  # web/js/shapes.js OCCUPANT_ROUND_SECTION
TOL_MM = 0.01

# Per occupant part: its meshes in the frame of the bench's parts group (ISO mm): geometry type,
# parameters, the vertices in that frame and the material's colour and opacity.
OCCUPANTS_OF = """async ([events, loading]) => {
  await window.hiraku.load(events, 1);
  const b = window.hiraku.bench;
  await window.hiraku.seek(1);
  if (loading !== null) b.setLoading(loading);
  b.parts.updateWorldMatrix(true, true);
  const inv = b.parts.matrixWorld.clone().invert();
  const out = {};
  for (const [id, o] of Object.entries(b.partObjs)) {
    if (o.kind !== "occupant") continue;
    const meshes = [];
    o.group.traverse((x) => {
      if (!x.isMesh || x.isLineSegments2) return;
      const m = x.matrixWorld.clone().premultiply(inv).elements;
      const p = x.geometry.attributes.position;
      const verts = [];
      for (let i = 0; i < p.count; i += 1) {
        const [vx, vy, vz] = [p.getX(i), p.getY(i), p.getZ(i)];
        verts.push([m[0] * vx + m[4] * vy + m[8] * vz + m[12], m[1] * vx + m[5] * vy + m[9] * vz + m[13], m[2] * vx + m[6] * vy + m[10] * vz + m[14]]);
      }
      meshes.push({ type: x.geometry.type, params: x.geometry.parameters ?? null, verts, color: "#" + x.material.color.getHexString(), opacity: x.material.opacity });
    });
    out[id] = { seat: o.seat, onBoard: o.onBoard, visible: o.group.visible, meshes };
  }
  return out;
}"""


def events_with(design):
    src = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    return [src[0], {**src[1], "data": design}]


def occupant_parts(design):
    return [p for p in design["parts"] if p["kind"] == "occupant"]


def one_box_design():
    """The frozen v1 with each occupant as its one torso box (the segments taken out)."""
    d = copy.deepcopy(json.loads(ZU_V1.read_text(encoding="utf-8")))
    d["parts"] = [p for p in d["parts"] if not (p["kind"] == "occupant" and "." in p["id"])]
    return d


def in_box(part, verts):
    """The vertices in the box's own frame (centre at 0, axes of rpy_deg), and the box's half sizes."""
    box = part["box"]
    R = rpy_matrix(box.get("rpy_deg", [0.0, 0.0, 0.0]))
    return (np.asarray(verts) - np.asarray(box["center_mm"])) @ R, np.asarray(box["size_mm"]) / 2.0


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class OccupantBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.server = make_server("127.0.0.1", 0, cls.tmp / "runs", quiet=True, saved_dir=None, launcher=False, assets=False)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        cls.segmented = json.loads(SEGMENTED.read_text(encoding="utf-8"))
        cls.drawn = {look: cls.draw(look, cls.segmented) for look in ("real", "zu")}

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp)

    @classmethod
    def draw(cls, look, design, loading=None):
        page = cls.browser.new_page(viewport={"width": 1280, "height": 800})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.goto(f"{cls.base}/?run=sample&pace=0&t=0&look={look}")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
        page.evaluate("window.hiraku.ready")
        got = page.evaluate(OCCUPANTS_OF, [events_with(design), loading])
        page.close()
        return {"errors": errors, "parts": got}

    def test_fixture_is_the_segmented_design(self):
        # 2 persons of 15 parts each: the torso and the 14 segments, every one a box
        parts = occupant_parts(self.segmented)
        people = list(dict.fromkeys(person_of(p) for p in parts))
        self.assertEqual(people, ["occupant_R", "occupant_L"])
        for person in people:
            self.assertEqual(sorted(p["id"] for p in parts if person_of(p) == person),
                             sorted([person] + [f"{person}.{s}" for s in SEGMENTS]))
        self.assertTrue(any("rpy_deg" in p["box"] for p in parts))

    def test_each_segment_is_one_shape(self):
        for look, drawn in self.drawn.items():
            self.assertEqual(drawn["errors"], [], look)
            got = drawn["parts"]
            self.assertEqual(set(got), {p["id"] for p in occupant_parts(self.segmented)}, look)
            for p in occupant_parts(self.segmented):
                meshes = got[p["id"]]["meshes"]
                self.assertEqual(len(meshes), 1, (look, p["id"]))
                kind = meshes[0]["type"]
                size = sorted(p["box"]["size_mm"])
                if p["id"].endswith(".head"):
                    # a unit ball stretched to the box: in the box's frame it spans each side end to end
                    self.assertEqual(kind, "SphereGeometry", (look, p["id"]))
                    self.assertEqual(meshes[0]["params"]["radius"], 1, (look, p["id"]))
                    local, half = in_box(p, meshes[0]["verts"])
                    np.testing.assert_allclose(local.max(axis=0), half, atol=TOL_MM, err_msg=f"{look} {p['id']}")
                    np.testing.assert_allclose(local.min(axis=0), -half, atol=TOL_MM, err_msg=f"{look} {p['id']}")
                elif size[0] / size[1] >= ROUND_SECTION:
                    self.assertEqual(kind, "CapsuleGeometry", (look, p["id"]))
                else:
                    self.assertEqual(kind, "BoxGeometry", (look, p["id"]))  # RoundedBoxGeometry's type
            # the limbs are capsules; the torso, neck, hands and feet rounded boxes
            caps = sorted(i.split(".", 1)[1] for i, o in got.items() if o["meshes"][0]["type"] == "CapsuleGeometry")
            self.assertEqual(caps, sorted([s for s in SEGMENTS if s.split("_")[0] in ("arm", "forearm", "thigh", "shank")] * 2), look)
            # the diagram look: the occupant's one colour of look.js on every segment. The real look
            # dresses a person by segment (look.js occupantSub: skin, jacket, trousers, shoes), so a
            # person there has those four colours, the same for both persons; the shapes are unchanged
            colours = {m["color"] for o in got.values() for m in o["meshes"]}
            self.assertEqual(len(colours), 4 if look == "real" else 1, look)

    def test_one_ball_per_person_the_head(self):
        for look, drawn in self.drawn.items():
            balls = [i for i, o in drawn["parts"].items() for m in o["meshes"] if m["type"] == "SphereGeometry"]
            self.assertEqual(sorted(balls), ["occupant_L.head", "occupant_R.head"], look)

    def test_shapes_stay_in_their_boxes(self):
        for look, drawn in self.drawn.items():
            for p in occupant_parts(self.segmented):
                (m,) = drawn["parts"][p["id"]]["meshes"]
                local, half = in_box(p, m["verts"])
                over = np.abs(local).max(axis=0) - half
                self.assertTrue((over <= TOL_MM).all(), (look, p["id"], over.tolist()))

    def test_head_meets_the_neck(self):
        # the head's box sits on the neck's (data); drawn, the head's lowest point is at the neck's top
        # (before, a ball of the box's smallest side left about 40 mm between them)
        boxes = {p["id"]: p["box"] for p in occupant_parts(self.segmented)}
        for look, drawn in self.drawn.items():
            for person in ("occupant_R", "occupant_L"):
                head, neck = boxes[f"{person}.head"], boxes[f"{person}.neck"]
                self.assertAlmostEqual(head["center_mm"][2] - head["size_mm"][2] / 2, neck["center_mm"][2] + neck["size_mm"][2] / 2, places=6)
                (h,) = drawn["parts"][f"{person}.head"]["meshes"]
                (n,) = drawn["parts"][f"{person}.neck"]["meshes"]
                gap = np.asarray(h["verts"])[:, 2].min() - np.asarray(n["verts"])[:, 2].max()
                self.assertLess(abs(gap), 1.0, (look, person, gap))

    def test_capsule_axis_follows_rpy(self):
        # the capsule's axis (the vertices' principal direction) is the box's longest side turned
        # by rpy_deg (R = Rz Ry Rx, k0_handcalc.rigid.rpy_matrix, as the analysis reads boxes);
        # the capsule spans that side end to end
        checked = 0
        for look, drawn in self.drawn.items():
            for p in occupant_parts(self.segmented):
                (m,) = drawn["parts"][p["id"]]["meshes"]
                if m["type"] != "CapsuleGeometry":
                    continue
                v = np.asarray(m["verts"])
                w, vec = np.linalg.eigh(np.cov((v - v.mean(axis=0)).T))
                size = p["box"]["size_mm"]
                long = int(np.argmax(size))
                want = rpy_matrix(p["box"].get("rpy_deg", [0.0, 0.0, 0.0]))[:, long]
                self.assertGreater(abs(float(vec[:, -1] @ want)), 0.99999, (look, p["id"]))
                self.assertGreater(w[-1], 2 * w[-2], (look, p["id"]))
                local, _ = in_box(p, v)
                self.assertAlmostEqual(np.ptp(local[:, long]), size[long], delta=TOL_MM, msg=(look, p["id"]))
                checked += 1
        self.assertEqual(checked, 2 * 2 * 8)  # 2 looks x 2 persons x 8 capsules
        # the rotations are not all trivial: some capsule axis is off every ISO axis
        arm = next(p for p in occupant_parts(self.segmented) if p["id"] == "occupant_L.arm_L")
        self.assertLess(np.abs(rpy_matrix(arm["box"]["rpy_deg"])[:, 0]).max(), 0.95)

    def test_seats_are_counted_per_person(self):
        parts = occupant_parts(self.segmented)
        for look, drawn in self.drawn.items():
            got = drawn["parts"]
            persons = {person_of(p) for p in parts}
            self.assertEqual(len({o["seat"] for o in got.values()}), len(persons), look)
            for p in parts:
                self.assertEqual(got[p["id"]]["seat"], ["occupant_R", "occupant_L"].index(person_of(p)), (look, p["id"]))
            # two_cargo (the default): everyone on board
            self.assertTrue(all(o["onBoard"] for o in got.values()), look)
        # each loading: on board are the occupant parts the contract's parts_on_board keeps
        for loading in OCCUPANTS:
            drawn = self.draw("real", self.segmented, loading)
            self.assertEqual(drawn["errors"], [], loading)
            want = {p["id"] for p in parts_on_board(self.segmented, loading) if p["kind"] == "occupant"}
            got = {i for i, o in drawn["parts"].items() if o["onBoard"]}
            self.assertEqual(got, want, loading)
            # in the real look a person off board is not drawn (all of the person)
            self.assertEqual({i for i, o in drawn["parts"].items() if o["visible"]}, want, loading)

    def test_one_box_occupant_is_drawn_as_before(self):
        # an occupant as one box: the capsule and the ball inside it, as the code before drew it
        # (radius 0.26 and head 0.2 of the box's smaller side across, ISO z up), one seat each
        design = one_box_design()
        for look in ("real", "zu"):
            drawn = self.draw(look, design)
            self.assertEqual(drawn["errors"], [], look)
            got = drawn["parts"]
            self.assertEqual(set(got), {"occupant_R", "occupant_L"}, look)
            for p in occupant_parts(design):
                o = got[p["id"]]
                self.assertEqual([m["type"] for m in o["meshes"]], ["CapsuleGeometry", "SphereGeometry"], (look, p["id"]))
                sx, sy, sz = p["box"]["size_mm"]
                cx, cy, cz = p["box"]["center_mm"]
                r, head = min(sx, sy) * 0.26, min(sx, sy) * 0.2
                length = max(10, sz - 2 * head - 2 * r)
                cap, ball = o["meshes"]
                self.assertAlmostEqual(cap["params"]["radius"], r, places=6)
                self.assertAlmostEqual(cap["params"]["length"], length, places=6)
                self.assertAlmostEqual(ball["params"]["radius"], head, places=6)
                v = np.asarray(cap["verts"])
                np.testing.assert_allclose(v.min(axis=0), [cx - r, cy - r, cz - sz / 2], atol=1e-3)
                np.testing.assert_allclose(v.max(axis=0), [cx + r, cy + r, cz - sz / 2 + 2 * r + length], atol=1e-3)
                np.testing.assert_allclose(np.asarray(ball["verts"]).mean(axis=0)[2], cz + sz / 2 - head, atol=1e-3)
            self.assertEqual(sorted(o["seat"] for o in got.values()), [0, 1], look)

    def test_torso_with_the_head_in_it_keeps_its_ball(self):
        # the frozen zu copies: limbs as segments but no head part, the torso box holding the head;
        # the torso stays the capsule with its ball (the person's one ball), the limbs are capsules
        # or rounded boxes, and the person's parts share a seat
        design = json.loads(ZU_V1.read_text(encoding="utf-8"))
        drawn = self.draw("zu", design)
        self.assertEqual(drawn["errors"], [])
        got = drawn["parts"]
        for person in ("occupant_R", "occupant_L"):
            self.assertEqual([m["type"] for m in got[person]["meshes"]], ["CapsuleGeometry", "SphereGeometry"])
            mine = {i: o for i, o in got.items() if i.startswith(person + ".")}
            self.assertEqual(len(mine), 8)
            for i, o in mine.items():
                self.assertEqual(len(o["meshes"]), 1, i)
                self.assertNotEqual(o["meshes"][0]["type"], "SphereGeometry", i)
            self.assertEqual({o["seat"] for o in mine.values()}, {got[person]["seat"]})


if __name__ == "__main__":
    unittest.main()
