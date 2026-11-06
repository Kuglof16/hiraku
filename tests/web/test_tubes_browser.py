"""The frame's tubes on the workbench (web/js/tubes.js, workbench3d.js memberGeometry).

A member whose section has dims_mm is drawn hollow (rect_tube: wall t, outer corners rounded
1.5 t; round_tube: wall t), a member butting on another is cut where it leaves the other's
outside, two members of one section in one line are one tube (no end face where they meet), and
a member whose section has no dims_mm is drawn as before (a solid box from node to node).
The design is the frozen zu copy of offroad-4w v1 (tests/web/zu_designs/), as test_joints_browser.

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

from hiraku.webserve.server import make_server
from tests.web.test_joints_browser import design, joints_events

try:
    from tests.web.pages import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None

TUBES_RUN = "20261104-230000-tubes"  # the frozen design alone
BARE_RUN = "20261104-230100-bare"  # the same with rt30x30x2.3's dims_mm taken out (bare_design)
BARE_SECTION = "rt30x30x2.3"
CORNER_R_OF_T = 1.5  # web/js/tubes.js TUBE.corner_r_of_t
EPS = 0.01

# every member on the bench: its geometry's points in its own frame (loc: unit x for a tube) and
# in the body (pts, ISO mm), the normals' x, the section and the ends worked out
MEMBERS = """() => Object.fromEntries(Object.entries(window.hiraku.bench.members).map(([id, i]) => {
  const g = i.mesh.geometry; const p = g.getAttribute('position'); const n = g.getAttribute('normal');
  const v = i.mesh.position.clone(); const loc = []; const pts = []; const nx = [];
  for (let k = 0; k < p.count; k += 1) { v.fromBufferAttribute(p, k); loc.push(v.toArray()); v.applyMatrix4(i.mesh.matrix); pts.push(v.toArray()); nx.push(n.getX(k)); }
  return [id, { type: g.type, uuid: g.uuid, tube: i.tube ?? null, ends: i.ends ?? null, x0: i.x0 ?? null, x1: i.x1 ?? null, length: i.length, loc, pts, nx }];
}))"""


def bare_design():
    d = design()
    d = copy.deepcopy(d)
    del d["sections"][BARE_SECTION]["dims_mm"]
    return d


def axes(a, b):
    """x, y, z of a member as workbench3d.js memberBasis (b along y, h along z)."""
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    x = (b - a) / np.linalg.norm(b - a)
    up = np.array([1.0, 0, 0]) if abs(x[2]) > 0.95 else np.array([0, 0, 1.0])
    z = up - x * (up @ x)
    z /= np.linalg.norm(z)
    return x, np.cross(z, x), z


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class TubesBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        runs = cls.tmp / "runs"
        for run_id, events in ((TUBES_RUN, joints_events()), (BARE_RUN, joints_events(bare_design()))):
            (runs / run_id).mkdir(parents=True)
            (runs / run_id / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
        cls.server = make_server("127.0.0.1", 0, runs, quiet=True, saved_dir=None, launcher=False, assets=False)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        cls.got = {run: cls.members(run) for run in (TUBES_RUN, BARE_RUN)}

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def members(cls, run):
        page = cls.browser.new_page(viewport={"width": 1280, "height": 720})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.goto(f"{cls.base}/?run={run}&tab=bench&pace=0&t=1e6&look=zu", wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
        page.evaluate("window.hiraku.ready")
        got = page.evaluate(MEMBERS)
        page.close()
        assert errors == [], errors
        return got

    def setUp(self):
        self.d = design()
        self.nodes = {n["id"]: np.asarray(n["xyz_mm"], dtype=float) for n in self.d["nodes"]}
        self.mem = {m["id"]: m for m in self.d["members"]}

    def frame(self, mid):
        m = self.mem[mid]
        return axes(self.nodes[m["node_a"]], self.nodes[m["node_b"]])

    def test_rect_tube_is_hollow_with_rounded_corners(self):
        got = self.got[TUBES_RUN]
        rect = [k for k, v in got.items() if v["tube"] and v["tube"]["kind"] == "rect"]
        self.assertGreater(len(rect), 50)
        for mid in rect:
            g = got[mid]
            dims = self.d["sections"][self.mem[mid]["section"]]["dims_mm"]
            w, h, t = dims["b"], dims["h"], dims["t"]
            yz = np.asarray(g["loc"])[:, 1:]
            cheb = np.maximum(np.abs(yz[:, 0]) - w / 2, np.abs(yz[:, 1]) - h / 2)
            # the outside on the section's sides, the inside's flats a wall t in (hollow); the deepest
            # points the inner corners' middles (radius r - t round the same centre as the outer r)
            r = min(CORNER_R_OF_T * t, w / 2, h / 2)
            self.assertAlmostEqual(float(cheb.max()), 0.0, delta=EPS, msg=mid)
            self.assertAlmostEqual(float(cheb.min()), -r + (r - t) / np.sqrt(2), delta=EPS, msg=mid)
            self.assertTrue(np.any(np.abs(cheb + t) < EPS) and np.any(np.abs(cheb) < EPS), mid)
            # the corners rounded: the nearest point to a sharp corner is r (sqrt 2 - 1) from it
            corner = np.hypot(np.abs(yz[:, 0]) - w / 2, np.abs(yz[:, 1]) - h / 2).min()
            self.assertAlmostEqual(float(corner), r * (np.sqrt(2) - 1), delta=EPS, msg=mid)
        # the round tubes: hollow, outer radius d/2, inner d/2 - t
        rounds = [k for k, v in got.items() if v["tube"] and v["tube"]["kind"] == "round"]
        self.assertTrue(rounds)
        for mid in rounds:
            dims = self.d["sections"][self.mem[mid]["section"]]["dims_mm"]
            rad = np.round(np.hypot(*np.asarray(got[mid]["loc"])[:, 1:].T), 3)
            self.assertEqual(sorted(set(rad.tolist())), sorted({round(dims["d"] / 2, 3), round(dims["d"] / 2 - dims["t"], 3)}), mid)

    def test_cut_ends_stay_out_of_the_receiver(self):
        got = self.got[TUBES_RUN]
        cuts = 0
        for mid, g in got.items():
            for end, ux in (("a", 0.0), ("b", 1.0)):
                e = (g["ends"] or {}).get(end)
                if not e or e["kind"] != "cut":
                    continue
                cuts += 1
                self.assertGreater(e["trim"], 0, (mid, end))
                rec = self.mem[e["onto"]]
                rx, ry, rz = self.frame(rec["id"])
                c = self.nodes[self.mem[mid]["node_a" if end == "a" else "node_b"]]
                dims = self.d["sections"][rec["section"]]["dims_mm"]
                pts = np.asarray(g["pts"])[np.abs(np.asarray(g["loc"])[:, 0] - ux) < 1e-6] - c
                if "d" in dims:
                    out = np.hypot(pts @ ry, pts @ rz) - dims["d"] / 2
                else:
                    out = np.maximum(np.abs(pts @ ry) - dims["b"] / 2, np.abs(pts @ rz) - dims["h"] / 2)
                # every point of the end outside the receiver (its endless prism along its axis)
                self.assertGreaterEqual(float(out.min()), -EPS, (mid, end, e))
        self.assertGreater(cuts, 30)

    def test_through_pair_is_one_tube(self):
        got = self.got[TUBES_RUN]
        pairs = 0
        at = {}
        for mid, g in got.items():
            for end in ("a", "b"):
                if g["ends"] and g["ends"][end]["kind"] == "through":
                    at.setdefault(self.mem[mid]["node_a" if end == "a" else "node_b"], []).append((mid, end))
        for node, es in at.items():
            self.assertEqual(len(es) % 2, 0, node)
            ring = []
            for mid, end in es:
                g = got[mid]
                ux = 0.0 if end == "a" else 1.0
                loc = np.asarray(g["loc"])
                here = np.abs(loc[:, 0] - ux) < 1e-6
                # no end face there (no normal along the tube at that end) ...
                self.assertFalse(np.any((np.abs(np.asarray(g["nx"])) > 0.9) & here), (mid, end))
                # ... and the tube runs to the node
                self.assertAlmostEqual(g["ends"][end]["trim"], 0.0, msg=(mid, end))
                ring.append({tuple(np.round(p, 2)) for p in np.asarray(g["pts"])[here]})
            # the two ends' outlines are the same points: one tube going on
            for k in range(0, len(ring), 2):
                self.assertEqual(ring[k], ring[k + 1], node)
            pairs += len(es) // 2
        self.assertGreater(pairs, 5)

    def test_same_section_shares_its_shape(self):
        got = self.got[TUBES_RUN]
        tubes = [g for g in got.values() if g["tube"]]
        keys = {(json.dumps(g["tube"], sort_keys=True), g["ends"]["a"]["kind"] == "through", g["ends"]["b"]["kind"] == "through") for g in tubes}
        self.assertEqual(len({g["uuid"] for g in tubes}), len(keys))
        self.assertLess(len(keys), len(tubes) / 4)

    def test_member_without_dims_as_before(self):
        got = self.got[BARE_RUN]
        bare = [m["id"] for m in self.d["members"] if m["section"] == BARE_SECTION]
        self.assertTrue(bare)
        for mid in bare:
            g = got[mid]
            m = self.mem[mid]
            self.assertEqual((g["type"], g["tube"], g["ends"]), ("BoxGeometry", None, None), mid)
            self.assertEqual(len(g["loc"]), 24, mid)
            # node to node, the section's sides (30 by 30 from sectionSize's default when dims are missing)
            loc = np.asarray(g["loc"])
            length = float(np.linalg.norm(self.nodes[m["node_b"]] - self.nodes[m["node_a"]]))
            self.assertAlmostEqual(float(loc[:, 0].max() - loc[:, 0].min()), length, places=3)
            mid_pt = (self.nodes[m["node_a"]] + self.nodes[m["node_b"]]) / 2
            np.testing.assert_allclose(np.asarray(g["pts"]).mean(axis=0), mid_pt, atol=1e-3)
        # the other members are tubes still
        self.assertTrue(any(g["tube"] for g in got.values()))


if __name__ == "__main__":
    unittest.main()
