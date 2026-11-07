"""A shell mode shape moves the parts with their carried_by nodes (web/js/workbench3d.js
modeParts / poseParts).

modal_shell's mode_shape field gives in per_node the design nodes (the mean of their joint's mesh
nodes, on per_mesh_node's scale) beside the linkage's points. At k of the swing a part's box moves,
without turning, by the mean of its carried_by nodes' per_node times k and the scale the shell's
nodes take (poseShell); the real look's dummy moves with its occupant's parts; a wheel by its
"<support>.wheel_center". A record whose per_node has no design nodes leaves the parts at rest,
without errors.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
"""

import math
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import make_server
from tests.web.mode_parts_fixture import design_of, mode_parts_events
from tests.web.test_browser import ENABLED, sync_playwright

TOL_MM = 0.01

# The bench at a playhead: the mode's scale, k (sin 2π playhead), the parts' and wheels' offsets
# from rest, a shell node's offset, and the occupant's meshes' world offsets (the dummy's pieces).
POSE = """([playhead, shellNode]) => {
  const b = window.hiraku.bench;
  const ms = b.modeShape;
  b.animate(playhead, false);
  b.scene.updateMatrixWorld(true);
  const parts = {};
  for (const [id, o] of Object.entries(b.partObjs)) parts[id] = o.group.position.toArray();
  const wheels = {};
  for (const [id, o] of Object.entries(b.wheelObjs)) {
    const rest = b.wheelPose(id, null, b.body.matrix);
    wheels[id] = [12, 13, 14].map((i) => o.group.matrix.elements[i] - rest.elements[i]);
  }
  const o = Object.values(b.shellObjs).find((x) => x.group.visible);
  const ep = o ? o.edges.geometry.getAttribute("position").array : null;
  const shell = ep ? [0, 1, 2].map((j) => ep[shellNode * 3 + j] - o.nodeRest[shellNode * 3 + j]) : null;
  const dummy = [];
  const inv = b.parts.matrixWorld.clone().invert();
  b.partObjs["occupant_R.head"]?.group.traverse((x) => {
    if (x.isMesh && !x.isLineSegments2) {
      const m = x.matrixWorld.clone().premultiply(inv).elements;
      dummy.push([m[12], m[13], m[14]]);
    }
  });
  return { kind: b.keys.contour?.kind ?? null, scale: ms?.scale ?? null, k: Math.sin(playhead * Math.PI * 2),
           parts, wheels, shell, dummy, dummyDrawn: b.dummyDrawn };
}"""


def mean(vs):
    return [sum(v[j] for v in vs) / len(vs) for j in range(3)]


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class ModePartsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.server = make_server("127.0.0.1", 0, cls.tmp / "runs", quiet=True, saved_dir=None, launcher=False)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        cls.design = design_of()
        cls.events, cls.mesh, cls.field = mode_parts_events()
        cls.bare_events, _, cls.bare_field = mode_parts_events(with_design_nodes=False)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def open(self, events):
        page = self.browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.goto(f"{self.base}/?run=sample&pace=0&t=0&look=real", wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
        page.evaluate("window.hiraku.ready")
        page.evaluate("async (events) => { await window.hiraku.load(events, 1e6); }", events)
        page.wait_for_function("window.hiraku.bench.keys.contour?.kind === 'mode_shape'", timeout=30000)
        return page, errors

    def part(self, pid):
        return next(p for p in self.design["parts"] if p["id"] == pid)

    def expected(self, pid, s):
        per = self.field["per_node"]
        return [c * s for c in mean([per[n] for n in self.part(pid)["carried_by"]])]

    def assertNear(self, got, want, msg=None):
        self.assertEqual(len(got), len(want), msg)
        for g, w in zip(got, want):
            self.assertAlmostEqual(g, w, delta=TOL_MM, msg=f"{msg}: {got} != {want}")

    def test_parts_move_with_their_carried_by_nodes(self):
        page, errors = self.open(self.events)
        node = max(range(len(self.mesh["nodes_mm"])), key=lambda i: math.hypot(*self.field["per_mesh_node"][i]))
        rest = page.evaluate(POSE, [0.0, node])
        for playhead in (0.25, 0.1):
            got = page.evaluate(POSE, [playhead, node])
            self.assertEqual(got["kind"], "mode_shape")
            s = got["k"] * got["scale"]
            self.assertGreater(abs(s), 1.0)
            # the shell's node as poseShell moves it: per_mesh_node times the same k x scale
            self.assertNear(got["shell"], [c * s for c in self.field["per_mesh_node"][node]], "shell node")
            for pid in ("seat_R", "seat_L", "battery", "steering_wheel", "cargo", "occupant_R", "occupant_R.head"):
                self.assertNear(got["parts"][pid], self.expected(pid, s), pid)
            self.assertGreater(math.hypot(*got["parts"]["seat_R"]), 1.0)
            # the dummy's pieces (the real look) by the same as their occupant part's box
            self.assertTrue(got["dummyDrawn"])
            self.assertTrue(rest["dummy"])
            want = self.expected("occupant_R.head", s)
            for a, b in zip(got["dummy"], rest["dummy"]):
                self.assertNear([a[j] - b[j] for j in range(3)], want, "dummy piece")
            # a part without carried_by at a wheel: that wheel's point; the wheel too
            fl = [c * s for c in self.field["per_node"]["FL.wheel_center"]]
            self.assertNear(got["parts"]["knuckle_FL"], fl, "knuckle_FL")
            self.assertNear(got["wheels"]["FL"], fl, "wheel FL")
            # the roll bar's nodes are not all in per_node (its tubes are round, not meshed here): it stays
            self.assertFalse({"LHT", "RHT"} <= set(self.field["per_node"]))
            self.assertNear(got["parts"]["rollbar"], [0, 0, 0], "rollbar")
        back = page.evaluate(POSE, [0.0, node])
        for pid, v in back["parts"].items():
            self.assertNear(v, [0, 0, 0], pid)
        for sid, v in back["wheels"].items():
            self.assertNear(v, [0, 0, 0], sid)
        # another contour: the parts go back to rest
        page.evaluate("() => window.hiraku.bench.setContour(null)")
        after = page.evaluate("() => Object.values(window.hiraku.bench.partObjs).map((o) => o.group.position.length())")
        self.assertLess(max(after), 1e-9)
        self.assertEqual(errors, [])
        page.close()

    def test_without_design_nodes_the_parts_stay(self):
        self.assertNotIn("per_node", self.bare_field)
        page, errors = self.open(self.bare_events)
        node = 0
        for playhead in (0.25, 0.1):
            got = page.evaluate(POSE, [playhead, node])
            self.assertEqual(got["kind"], "mode_shape")
            for pid, v in got["parts"].items():
                self.assertNear(v, [0, 0, 0], pid)
            for sid, v in got["wheels"].items():
                self.assertNear(v, [0, 0, 0], sid)
        self.assertEqual(errors, [])
        page.close()


if __name__ == "__main__":
    unittest.main()
