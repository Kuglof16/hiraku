"""The R4 roll bar push-over field (viewer/src/rops.py, viewer/js/rops.js) on the showcase run that made the tool
(showcase/20261105-160725-offroad-4w: its events.jsonl and tools/rops_pushover-1.py): the re-run gives the
record's clearances, the shape between hinge events is the tool's own solution, the data's shape, and the page in
headless Chromium when the system Python has Playwright (skipped otherwise)."""

import json
import shutil
import subprocess
import tempfile
import unittest

from tests.viewer import no_paths
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from viewer.src import common, names, rops
from tests.viewer.test_viewer import COMMON_KEYS, _system_python_with_playwright

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "showcase" / "20261105-160725-offroad-4w"
VIEWER = ROOT / "viewer"
# the records' clearances (events.jsonl analysis.finished: ana-0037 v3, ana-0067 v5, ana-0082 v6), worst case
RECORDED = {3: ("side_right", -3.547139397876226), 5: ("side_right", -2.9386088292028667),
            6: ("side_right", 77.04266024581794)}

_OBJ = {}


def obj_of(versions="5,6"):
    if versions not in _OBJ:
        _OBJ[versions] = rops.build(SimpleNamespace(run=str(RUN), versions=versions, tool_file=None))
    return _OBJ[versions]


class RerunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.events = rops.read_events(RUN)
        cls.mod = rops.load_tool(rops.tool_file(RUN, None))

    def test_tool_file_unchanged_and_imported_by_path(self):
        self.assertEqual(rops.tool_file(RUN, None).name, "rops_pushover-1.py")
        self.assertEqual(self.mod.RopsPushover.name, "rops_pushover")

    def test_rerun_clearances_equal_the_records(self):
        for v, (case, value) in RECORDED.items():
            design, lc, rec = rops.inputs_of(self.events, v)
            rr = rops.rerun(self.mod, design, lc)
            got = {n: r["clear"]["clearance_mm"] for n, r in rr["runs"].items()}
            self.assertAlmostEqual(got[case], value, delta=rops.CHECK_MM, msg=f"v{v}")
            self.assertEqual(min(got, key=got.get), case)
            for r in rec["results"]:   # every case of the record, and the deflections
                q = r["quantity"]
                if q.startswith("rops_occupant_clearance."):
                    self.assertAlmostEqual(got[q.split(".", 1)[1]], r["value"], delta=rops.CHECK_MM, msg=f"v{v} {q}")
            mine = {r["quantity"]: r["value"] for r in rr["results"]}
            for r in rec["results"]:
                self.assertAlmostEqual(mine[r["quantity"]], r["value"], delta=1e-6 * max(1.0, abs(r["value"])), msg=q)

    def test_shape_between_events_is_the_tools_line(self):
        """push() stopped half-way between two events is the straight line between their shapes (exact)."""
        design, lc, _ = rops.inputs_of(self.events, 5)
        rr = rops.rerun(self.mod, design, lc)
        run = rr["runs"]["side_right"]
        st = rops.states_of(self.mod, run)
        self.assertEqual(len(st["U"]), len(st["lams"]) + 2)
        # the force-capped push at each event's load factor ends at that load
        for k, lam in enumerate(st["lams"]):
            self.assertAlmostEqual(st["F"][k + 1], lam, delta=1e-9 * lam)
        # segments ending at an event (the last one runs to the final along the same stiffness or the mechanism)
        for seg in range(len(st["lams"])):
            self.assertLess(rops.segment_check(self.mod, run, st, seg), 1e-6, f"segment {seg}")
        # the final is the captured result
        fin = np.array([run["result"]["U"][n] for n in st["ids"]])
        self.assertTrue(np.allclose(st["U"][-1], fin))


class RopsDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.obj = obj_of()

    def test_common_keys(self):
        for k in COMMON_KEYS:
            self.assertIn(k, self.obj, k)
        self.assertEqual(self.obj["field"], "rops")
        self.assertTrue(self.obj["title"].startswith(names.field_labels()["rops"]))
        self.assertEqual(names.field_labels()["rops"], "強度信頼性 (ROPS)")
        self.assertNotRegex(json.dumps(self.obj, ensure_ascii=False), no_paths.IN_JSON_SHORT)
        self.assertEqual(self.obj["source"]["run"], RUN.name)

    def test_default_version_is_the_failing_one(self):
        self.assertEqual(self.obj["default_v"], 5)
        self.assertEqual([v["v"] for v in self.obj["versions"]], [5, 6])
        ok = {v["v"]: v["ok"] for v in self.obj["versions"]}
        self.assertEqual(ok, {5: False, 6: True})

    def test_states_hinges_and_clearances(self):
        for ver in self.obj["versions"]:
            n = len(ver["frame"]["node_ids"])
            self.assertIn("rollbar_top#0", ver["frame"]["member_ids"])
            occ = [s["occupant"] for s in ver["spaces"]]
            self.assertEqual(len(occ), 2)
            for name, c in ver["cases"].items():
                ns = c["n_states"]
                self.assertEqual(c["U"]["shape"], [ns, n, 3])
                self.assertEqual(len(common.unpack(c["U"])), ns * n * 3)
                self.assertEqual(len(c["F"]), ns)
                self.assertEqual(c["F"][0], 0.0)
                self.assertTrue(all(b >= a - 1e-9 for a, b in zip(c["d"], c["d"][1:])))
                states = [h["state"] for h in c["hinges"]]
                self.assertEqual(states, sorted(states))
                self.assertEqual(len(set(states)), ns - 2)
                for h in c["hinges"]:
                    self.assertAlmostEqual(h["F"], c["F"][h["state"]], delta=1e-6 * h["F"])
                    self.assertIn(h["axis"], ("x", "y", "z"))
                # the clearance curve: per occupant, (states - 1) x sub + 1 steps; its steps at the states are the
                # states' clearances; the final is the record's
                for sc in c["sub_clear"]:
                    self.assertEqual(len(sc), (ns - 1) * c["sub"] + 1)
                for s in range(ns):
                    self.assertAlmostEqual(min(x[s * c["sub"]] for x in c["sub_clear"]), c["clear"][s], places=9)
                self.assertAlmostEqual(c["clear"][-1], c["record_clear"], delta=rops.CHECK_MM)
                self.assertAlmostEqual(c["rerun_clear"], c["record_clear"], delta=rops.CHECK_MM)
                if c["kind"] == "energy":   # the energy pushes end at the case's energy
                    self.assertAlmostEqual(c["energy_J"][-1], c["target"], delta=1e-6 * c["target"])
            # the record's worst case is NG for v5 (the bar enters occupant_R) and OK for v6
            sr = ver["cases"]["side_right"]
            self.assertEqual(sr["occupant"][-1], "occupant_R")
            self.assertEqual(sr["clear"][-1] < 0, ver["v"] == 5)
            self.assertGreater(sr["clear"][0], 0.0)


class RopsPageTest(unittest.TestCase):
    """The page from disk in headless Chromium: no console errors, drawn, nothing over the 3D view; v5 at the largest
    load has its occupant space red, v6 not."""

    @classmethod
    def setUpClass(cls):
        cls.py = _system_python_with_playwright()
        if not cls.py:
            raise unittest.SkipTest("playwright is not available for the system Python (py)")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name) / "viewer"
        shutil.copytree(VIEWER, cls.dir, ignore=shutil.ignore_patterns("data", "src", "__pycache__", "*.py"))
        common.write_data("rops", obj_of(), cls.dir / "data")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_page(self):
        r = subprocess.run([self.py, str(Path(__file__).with_name("page_check.py")), str(self.dir), "rops", ""],
                           capture_output=True, text=True, encoding="utf-8", timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        out = json.loads(r.stdout)
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["shown_field"], "rops")
        self.assertIn("強度信頼性 (ROPS)", out["switch"])
        self.assertGreater(out["drawn_px"], 1000)
        self.assertEqual(out["over_view"], [])
        self.assertEqual(out["layout_overlap"], [])
        self.assertFalse(out["notes_open"])
        self.assertNotRegex(out["page_text"], no_paths.ON_PAGE)
        self.assertIn("部品と計算での扱い", out["panel"])
        self.assertIsNotNone(out["ground"])
        # opened at the largest load on v5's worst case: NG, the record's value
        self.assertIn("v5 横 (右の角を左へ)", out["hud"])
        self.assertIn("-2.94 mm NG", out["hud"])
        self.assertIn("最大の荷重", out["tline"])
        self.assertIn("ヒンジ (入った順)", out["panel"])

    def test_red_only_for_ng(self):
        r = subprocess.run([self.py, "-c", PAGE_RED, str(self.dir)], capture_output=True, text=True, encoding="utf-8",
                           timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        out = json.loads(r.stdout)
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["v5_end"], ["occupant_R"])     # red: the space the bar is in
        self.assertEqual(out["v5_start"], [])               # unloaded: nothing red
        self.assertEqual(out["v6_end"], [])                 # v6 OK: nothing red
        self.assertEqual(out["v6_source"][:2], "v6")        # the header names the shown version
        self.assertEqual(out["camera_kept"], True)          # the version switch keeps the camera


PAGE_RED = r"""
import json, sys
from pathlib import Path
from playwright.sync_api import sync_playwright
url = (Path(sys.argv[1]).resolve() / "index.html").as_uri() + "#rops"
errs, out = [], {}
with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    page = b.new_page(viewport={"width": 1480, "height": 900})
    page.on("console", lambda m: errs.append(m.text) if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(url)
    page.wait_for_function("window.__ready === true", timeout=120000)
    reds = "(() => { const o = []; HV.state.ctx.root.traverse(m => { if (m.visible && m.userData && m.userData.kind === 'space' && m.material.color.getHex() === 0xc8402a) { let v = true; for (let q = m; q; q = q.parent) if (!q.visible) v = false; if (v) o.push(m.userData.occupant); } }); return o; })()"
    cam = "HV.view.camera.position.toArray().map(v => Math.round(v))"
    out["v5_end"] = page.evaluate(reds)
    c0 = page.evaluate(cam)
    page.eval_on_selector("#time", "e => { e.value = 0; e.dispatchEvent(new Event('input')); }")
    out["v5_start"] = page.evaluate(reds)
    page.click("#ropsEnd")
    page.click("#ropsv_6")
    page.wait_for_timeout(300)
    out["v6_end"] = page.evaluate(reds)
    out["v6_source"] = page.text_content("#source")
    out["camera_kept"] = page.evaluate(cam) == c0
    out["errors"] = errs
    b.close()
print(json.dumps(out))
"""


if __name__ == "__main__":
    unittest.main()
