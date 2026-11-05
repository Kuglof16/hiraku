"""The suspension's own parts on the workbench (web/js/shapes.js tube, damper, ball, clevis; armsFromHardpoints).

The K2 arms' design is frozen here (tests/web/arms_designs/offroad-4w-v5-arms.json: the v5
design with the arms, dampers, ball joints and clevis brackets as parts, byte for byte as it
was handed over), so these tests do not follow data/designs/.

- link: one round tube along its box's longest side (turned by rpy_deg), as thick as the
  smaller of the other two sides
- damper: a cylinder and a coil round it, both along the longest side
- ball_joint: one ball inscribed in its box
- a clevis bracket (kind bracket, "クレビス" or clevis in its name or id): two parallel plates
  at the two ends of the longest side, a gap between them
- none outside its box; with these parts in the design, the suspension drawn from the
  mounting points (a suspension part's arms and coil) is not drawn; without them (the zu copy
  of v1) it is drawn as before
- the joint marks: no second ball where a ball joint is a part

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

from hiraku.solvers.k0_handcalc.rigid import rpy_matrix
from hiraku.webserve.server import SAMPLE_EVENTS, make_server

try:
    from tests.web.pages import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None

HERE = Path(__file__).resolve().parent
V5 = HERE / "arms_designs" / "offroad-4w-v5-arms.json"
ZU_V1 = HERE / "zu_designs" / "offroad-4w-v1.json"  # suspension parts drawn from the mounting points
TOL_MM = 0.01
KINDS = ("link", "damper", "ball_joint", "bracket", "suspension")

# Per part of KINDS: its meshes in the frame of the bench's parts group (ISO mm).
PARTS_OF = """async ([events, kinds]) => {
  await window.hiraku.load(events, 1);
  const b = window.hiraku.bench;
  await window.hiraku.seek(1);
  b.parts.updateWorldMatrix(true, true);
  const inv = b.parts.matrixWorld.clone().invert();
  const out = {};
  for (const [id, o] of Object.entries(b.partObjs)) {
    if (!kinds.includes(o.kind)) continue;
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
      meshes.push({ type: x.geometry.type, data: x.geometry.userData, sub: x.material.userData.sub ?? null, verts });
    });
    out[id] = meshes;
  }
  return out;
}"""


def events_with(design):
    src = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    return [src[0], {**src[1], "data": design}]


def v5():
    return json.loads(V5.read_text(encoding="utf-8"))


def v5_with_suspension():
    """v5 with v1's suspension parts (kind suspension, drawn from the mounting points) put back."""
    d = v5()
    d["parts"] += [copy.deepcopy(p) for p in json.loads(ZU_V1.read_text(encoding="utf-8"))["parts"] if p["kind"] == "suspension"]
    return d


def is_clevis(p):
    return p["kind"] == "bracket" and ("クレビス" in (p.get("name") or "") or "clevis" in (p.get("name") or "").lower() or "clevis" in p["id"].lower())


def in_box(part, verts):
    box = part["box"]
    R = rpy_matrix(box.get("rpy_deg", [0.0, 0.0, 0.0]))
    return (np.asarray(verts) - np.asarray(box["center_mm"])) @ R, np.asarray(box["size_mm"]) / 2.0


def main_axis(verts):
    v = np.asarray(verts)
    w, vec = np.linalg.eigh(np.cov((v - v.mean(axis=0)).T))
    return vec[:, -1]


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class ArmsBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.server = make_server("127.0.0.1", 0, cls.tmp / "runs", quiet=True, saved_dir=None, launcher=False, assets=False)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        cls.design = v5()
        cls.drawn = {look: cls.draw(look, cls.design) for look in ("real", "zu")}

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def draw(cls, look, design, script=PARTS_OF):
        page = cls.browser.new_page(viewport={"width": 1280, "height": 800})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.goto(f"{cls.base}/?run=sample&pace=0&t=0&look={look}")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
        page.evaluate("window.hiraku.ready")
        got = page.evaluate(script, [events_with(design), list(KINDS)])
        page.close()
        return {"errors": errors, "parts": got}

    def parts(self, pred):
        return [p for p in self.design["parts"] if pred(p)]

    def test_fixture(self):
        kinds = [p["kind"] for p in self.design["parts"]]
        self.assertEqual((kinds.count("link"), kinds.count("damper"), kinds.count("ball_joint")), (20, 4, 8))
        self.assertEqual(len(self.parts(is_clevis)), 8)
        self.assertFalse(self.parts(lambda p: p["kind"] == "suspension"))
        self.assertTrue(any(p["box"].get("rpy_deg") for p in self.parts(lambda p: p["kind"] in ("link", "damper"))))

    def check_axis(self, part, verts, look):
        size = part["box"]["size_mm"]
        k = int(np.argmax(size))
        want = rpy_matrix(part["box"].get("rpy_deg", [0.0, 0.0, 0.0]))[:, k]
        self.assertGreater(abs(float(main_axis(verts) @ want)), 0.9999, (look, part["id"]))

    def check_in_box(self, part, verts, look):
        local, half = in_box(part, verts)
        over = np.abs(local).max(axis=0) - half
        self.assertTrue((over <= TOL_MM).all(), (look, part["id"], over.tolist()))

    def test_link_is_one_tube(self):
        for look, drawn in self.drawn.items():
            self.assertEqual(drawn["errors"], [], look)
            for p in self.parts(lambda p: p["kind"] == "link"):
                (m,) = drawn["parts"][p["id"]]
                self.assertEqual(m["type"], "CylinderGeometry", (look, p["id"]))
                self.assertEqual(m["data"]["shape"], "tube")
                size = p["box"]["size_mm"]
                k = int(np.argmax(size))
                self.assertAlmostEqual(m["data"]["r"], min(s for i, s in enumerate(size) if i != k) / 2, places=6)
                self.check_in_box(p, m["verts"], look)
                self.check_axis(p, m["verts"], look)
                local, half = in_box(p, m["verts"])
                self.assertAlmostEqual(np.ptp(local[:, k]), size[k], delta=TOL_MM)  # end to end

    def test_damper_is_a_cylinder_with_its_coil(self):
        for look, drawn in self.drawn.items():
            for p in self.parts(lambda p: p["kind"] == "damper"):
                meshes = drawn["parts"][p["id"]]
                self.assertEqual(sorted(m["data"]["shape"] for m in meshes), ["coil", "damper_body"], (look, p["id"]))
                self.assertEqual({m["sub"] for m in meshes}, {"damper", "spring"})
                for m in meshes:
                    self.check_in_box(p, m["verts"], look)
                    self.check_axis(p, m["verts"], look)
                coil = next(m for m in meshes if m["data"]["shape"] == "coil")
                self.assertGreaterEqual(coil["data"]["turns"], 4)

    def test_ball_joint_is_one_ball(self):
        for look, drawn in self.drawn.items():
            for p in self.parts(lambda p: p["kind"] == "ball_joint"):
                (m,) = drawn["parts"][p["id"]]
                self.assertEqual(m["type"], "SphereGeometry", (look, p["id"]))
                local, half = in_box(p, m["verts"])
                self.assertAlmostEqual(float(np.linalg.norm(local, axis=1).max()), min(p["box"]["size_mm"]) / 2, delta=0.01)
                self.check_in_box(p, m["verts"], look)

    def test_clevis_is_two_plates(self):
        for look, drawn in self.drawn.items():
            for p in self.parts(is_clevis):
                (m,) = drawn["parts"][p["id"]]
                self.assertEqual(m["data"]["shape"], "clevis", (look, p["id"]))
                self.check_in_box(p, m["verts"], look)
                size = p["box"]["size_mm"]
                k = int(np.argmax(size))
                local, half = in_box(p, m["verts"])
                t = m["data"]["plate_mm"]
                # every vertex in one of the two plates at the ends of the long side, both plates there
                along = local[:, k]
                self.assertTrue((np.abs(along) >= half[k] - t - TOL_MM).all(), (look, p["id"]))
                self.assertTrue((along > 0).any() and (along < 0).any())
                self.assertGreater(m["data"]["gap_mm"], 0)
            # the other brackets stay their boxes
            for p in self.parts(lambda p: p["kind"] == "bracket" and not is_clevis(p)):
                self.assertNotEqual(drawn["parts"][p["id"]][0]["data"].get("shape"), "clevis")

    def test_no_arms_from_the_mounting_points(self):
        # v5 with suspension parts put back: they draw nothing (the arms are the link parts)
        d = v5_with_suspension()
        susp = [p["id"] for p in d["parts"] if p["kind"] == "suspension"]
        self.assertTrue(susp)
        for look in ("real", "zu"):
            drawn = self.draw(look, d)
            self.assertEqual(drawn["errors"], [], look)
            for pid in susp:
                self.assertEqual(drawn["parts"][pid], [], (look, pid))
        # the zu copy of v1 (no link, no damper): the arms and the coil from the mounting points, as before
        v1 = json.loads(ZU_V1.read_text(encoding="utf-8"))
        for look in ("real", "zu"):
            drawn = self.draw(look, v1)
            for p in v1["parts"]:
                if p["kind"] != "suspension":
                    continue
                meshes = drawn["parts"][p["id"]]
                self.assertEqual(sorted(str(m["sub"]) for m in meshes), ["None", "hub", "spring"], (look, p["id"]))
                # the arms reach the outer mounting point, outside the part's box
                local, half = in_box(p, np.concatenate([m["verts"] for m in meshes]))
                self.assertTrue((np.abs(local).max(axis=0) > half + 1).any(), (look, p["id"]))

    def test_joint_marks_no_second_ball(self):
        d = self.design
        bj = {p["id"] for p in d["parts"] if p["kind"] == "ball_joint"}
        pairs = {(c["via"], tuple(sorted((p["id"], c["to"])))) for p in d["parts"] for c in p.get("connects_to", [])}
        by_part = sum(1 for via, ends in pairs if via == "ball_joint" and set(ends) & bj)
        self.assertGreater(by_part, 0)
        script = "async ([events]) => { await window.hiraku.load(events, 1); const j = window.hiraku.bench.joints; j.setOn(true); return j.summary(); }"
        page = self.browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(f"{self.base}/?run=sample&pace=0&t=0&look=zu")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
        page.evaluate("window.hiraku.ready")
        got = page.evaluate(script, [events_with(d)])
        page.close()
        self.assertEqual(got["byPart"], by_part)
        self.assertEqual(got["counts"]["ball_joint"], sum(1 for via, _ in pairs if via == "ball_joint"))
        balls = [m for m in got["marks"] if m["shape"] == "ball"]
        self.assertEqual(len(balls), got["counts"]["ball_joint"] - by_part)
        self.assertFalse([m for m in balls if m["owner"] in bj or m["to"] in bj])


if __name__ == "__main__":
    unittest.main()
