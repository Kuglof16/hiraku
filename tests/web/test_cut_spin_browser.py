"""No 3D view of a cut page turns faster than 90 degrees a second.

Each cut with a 3D view (Workbench3D) is opened and stepped through its whole length every
0.05 s. At each step, for every view drawn at that time, the camera's orientation and the
vehicle body's orientation as seen from the camera are read; the angle between two steps
over 0.05 s is the turn rate. The camera may not turn faster than 90 deg/s, nor may the
vehicle turn on the screen faster than that (a turn that is the record's own motion is to be
followed by the camera, not shown spinning).

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser) and Playwright is installed.
The records are the other cut tests' (shape examples); N01 and N02 need a run made by the
real tools (HIRAKU_WEB_DEV_RUNS, as the story test) and are skipped without one. The layout
is the story test's copy (with n09 / n14 from the N09 / N14 test).
"""

import json
import math
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import make_server
from tests.web.domains_fixture import domain_events
from tests.web.shell_fixture import shell_events
from tests.web.test_assembly_browser import assembly_events
from tests.web.test_browser import ENABLED, sync_playwright
from tests.web.test_cut_grid_browser import three_versions
from tests.web.test_cut_n03_n06_browser import n03_n06_events
from tests.web.test_cut_n09_n14_browser import ASM, OFF, PICKS, WANT, n09_n14_layout
from tests.web.test_cut_n11_n13_browser import hooks_events
from tests.web.intake_cut_fixture import design_events, n09_events, n14_events, write_run

STEP_S = 0.05
LIMIT_DEG_S = 90.0

# Every Workbench3D reachable from the cut (six levels of its objects and arrays: the grid's tiles
# hold their views in layers); each is marked with the time it was last drawn at. Looked for again
# at every step: some views are made only when their tile comes in (N04, N06).
FIND = """() => {
  const c = window.hiraku.cut;
  const seen = new Set();
  window.__views = window.__views || [];
  const isView = (o) => o && typeof o === "object" && o.camera && o.body && typeof o.renderInto === "function";
  const walk = (o, path, depth) => {
    if (!o || typeof o !== "object" || seen.has(o) || depth > 6) return;
    seen.add(o);
    if (isView(o)) {
      if (!o.__spinWrapped) {
        const orig = o.renderInto.bind(o);
        o.renderInto = (...a) => { o.__drawn = window.__stamp; return orig(...a); };
        o.__spinWrapped = true;
        window.__views.push({ path, v: o });
      }
      return;
    }
    if (o instanceof Node) return;
    for (const k of Object.keys(o)) {
      if (["s", "layout", "spec", "renderer", "story"].includes(k)) continue;
      walk(o[k], `${path}.${k}`, depth + 1);
    }
  };
  walk(c, "cut", 0);
  return window.__views.map((x) => x.path);
}"""

# The orientations (quaternions) of the camera and of the body seen from it, for the views drawn at t.
# The cut is drawn at t as hiraku.seek draws it (cut.js drawAt: cut.draw(t)), without seek's wait for
# two frames on the screen and its story check: the views are drawn inside cut.draw. Over all the
# cuts here every reading was the same as with seek (2026-11-05), in about half the time for N07.
READ = """async (t) => {
  window.__stamp = t;
  window.hiraku.cut.draw(t);
  const out = [];
  for (const { path, v } of window.__views) {
    if (v.__drawn !== t) continue;
    v.camera.updateMatrixWorld(true);
    v.body.updateMatrixWorld(true);
    const qc = v.camera.getWorldQuaternion(v.camera.quaternion.clone());
    const qb = v.body.getWorldQuaternion(v.camera.quaternion.clone());
    out.push({ path, cam: qc.toArray(), screen: qc.clone().invert().multiply(qb).toArray() });
  }
  return out;
}"""


# The whole walk in the page, one step after another as before (FIND, then READ at t), in one call
# instead of two calls from here per step: the readings are the same, without the round trips.
STEPS = f"""async (ts) => {{
  const find = {FIND};
  const read = {READ};
  const readings = [];
  let paths = [];
  for (const t of ts) {{
    paths = find();
    readings.push(await read(t));
  }}
  return {{ readings, paths }};
}}"""


def turn_deg(a, b):
    """The angle (deg) of the rotation between two unit quaternions."""
    d = min(1.0, abs(sum(x * y for x, y in zip(a, b))))
    return math.degrees(2 * math.acos(d))


class _CutSpinPage(unittest.TestCase):
    """The records, the servers and a browser (no tests of its own). The cuts are in four classes
    below, about as long as each other (a runner that puts each class in its own process runs them
    side by side)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        runs = cls.tmp / "runs"
        shell, _meshes, _shells = shell_events()
        cls.runs = {
            "n03": "20261103-130000-spin-n03n06", "n04": "20261103-130000-spin-grid", "n15": "20261103-130000-spin-grid3",
            "n07": shell[0]["run_id"], "n16": "20261103-130000-spin-asm", "n11": "20261103-130000-spin-n11",
            "n09": "20261103-130000-spin-n09", "n09_design": "20261103-130000-spin-design", "n14": "20261103-130000-spin-n14",
        }
        cls.runs["n06"] = cls.runs["n03"]
        for key, events in (("n03", n03_n06_events()), ("n04", domain_events()), ("n15", three_versions()), ("n07", shell),
                            ("n16", assembly_events()), ("n11", hooks_events(cls.runs["n11"]))):
            (runs / cls.runs[key]).mkdir(parents=True)
            (runs / cls.runs[key] / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
        write_run(runs, cls.runs["n09"], n09_events(cls.runs["n09"], WANT, PICKS, OFF, ASM))
        write_run(runs, cls.runs["n09_design"], design_events(cls.runs["n09_design"]))
        write_run(runs, cls.runs["n14"], n14_events(cls.runs["n14"]))
        cls.layout = n09_n14_layout()
        lf = cls.tmp / "layout.json"
        lf.write_text(json.dumps(cls.layout, ensure_ascii=False), encoding="utf-8")
        cls.servers = []
        cls.base = cls.serve(runs, lf)
        given = os.environ.get("HIRAKU_WEB_DEV_RUNS")
        cls.dev = None
        if given:
            dev = Path(given)
            run = sorted(p.name for p in dev.iterdir() if (p / "events.jsonl").is_file() and p.name[:1].isdigit())[-1]
            cls.dev = (cls.serve(dev, lf), run)
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])

    @classmethod
    def serve(cls, runs, lf):
        server = make_server("127.0.0.1", 0, runs, quiet=True, saved_dir=None, launcher=False, layout_file=lf)
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

    def fastest(self, cut, base, query):
        """The largest turn rates (deg/s) of the camera and of the body on the screen over the
        cut's length, with when and in which view; and how many views were found."""
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{base}/cut.html?cut={cut}&{query}&t=0", wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=180000)
        page.evaluate("window.hiraku.ready")
        self.assertIsNone(page.evaluate("document.querySelector('.err')?.textContent ?? null"), cut)
        dur = page.evaluate("window.hiraku.player.duration")
        prev = {}
        best = {"cam": (0.0, None), "screen": (0.0, None)}
        ts = [round(min(dur, i * STEP_S), 4) for i in range(int(round(dur / STEP_S)) + 1)]
        got = page.evaluate(STEPS, ts)
        paths = got["paths"]
        for t, readings in zip(ts, got["readings"]):
            for x in readings:
                p = prev.get(x["path"])
                if p and abs(t - p[0] - STEP_S) < 1e-6:
                    for k in best:
                        w = turn_deg(p[1][k], x[k]) / STEP_S
                        if w > best[k][0]:
                            best[k] = (w, (p[0], x["path"]))
                prev[x["path"]] = (t, x)
        self.assertEqual(errors, [], cut)
        page.close()
        return best, paths

    def check(self, cut, base, query):
        best, paths = self.fastest(cut, base, query)
        self.assertTrue(paths, f"{cut}: no 3D view found")
        for k, (w, where) in best.items():
            self.assertLessEqual(w, LIMIT_DEG_S, (cut, k, round(w, 1), where))
        return best


# (seconds of each cut with READ's cut.draw, 2026-11-05, beside other tests on this PC: n03 32, n04 10,
# n06 162, n07 91, n09 53, n11 71, n14 84, n15 27, n16 87; N06 alone is the longest)


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class CutSpinN06Test(_CutSpinPage):
    def test_n06(self):
        self.check("n06", self.base, f"run={self.runs['n06']}")


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class CutSpinN04N07N09Test(_CutSpinPage):
    def test_n04(self):
        self.check("n04", self.base, f"run={self.runs['n04']}&version=1")

    def test_n07(self):
        self.check("n07", self.base, f"run={self.runs['n07']}")

    def test_n09(self):
        self.check("n09", self.base, f"run={self.runs['n09']}&design_run={self.runs['n09_design']}")


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class CutSpinN11N14Test(_CutSpinPage):
    def test_n11(self):
        self.check("n11", self.base, f"run={self.runs['n11']}")

    def test_n14(self):
        self.check("n14", self.base, f"run={self.runs['n14']}")


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class CutSpinN03N15N16Test(_CutSpinPage):
    def test_n01_n02(self):
        if not self.dev:
            self.skipTest("needs HIRAKU_WEB_DEV_RUNS (a folder made by tests.web.dev_k2_run)")
        for cut in ("n01", "n02"):
            with self.subTest(cut=cut):
                self.check(cut, self.dev[0], f"run={self.dev[1]}")

    def test_n03(self):
        self.check("n03", self.base, f"run={self.runs['n03']}")

    def test_n15(self):
        self.check("n15", self.base, f"run={self.runs['n15']}")

    def test_n16(self):
        self.check("n16", self.base, f"run={self.runs['n16']}")


class TurnRateTest(unittest.TestCase):
    def test_a_fast_turn_is_caught(self):
        """The check itself: a quarter turn of the camera within one step reads as 1800 deg/s."""
        q0 = [0.0, 0.0, 0.0, 1.0]
        q1 = [0.0, math.sin(math.pi / 4), 0.0, math.cos(math.pi / 4)]  # 90 deg about y
        self.assertAlmostEqual(turn_deg(q0, q1) / STEP_S, 1800.0, places=6)
        self.assertAlmostEqual(turn_deg(q0, q0), 0.0, places=9)


if __name__ == "__main__":
    unittest.main()
