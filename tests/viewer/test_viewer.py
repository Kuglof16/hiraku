"""The viewer's data scripts (viewer/src/<field>.py) on small fixtures trimmed from real records
(tests/viewer/fixtures/README.md), the data shape the page reads, and the page itself in headless
Chromium when the system Python has Playwright (skipped otherwise)."""

import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from viewer.src import assembly, common, crash, nvh

ROOT = Path(__file__).resolve().parents[2]
FX = Path(__file__).resolve().parent / "fixtures"
VIEWER = ROOT / "viewer"
SCRIPTS = {"nvh": nvh, "crash": crash, "assembly": assembly}
COMMON_KEYS = {"field", "title", "source", "frame", "parts", "scene", "vectors", "results", "assumptions",
               "inputs", "not_computed", "notes"}


def make(field, run=None, tool=None):
    args = SimpleNamespace(run=FX / (run or field), version=None, analysis=None, load_case=None, tool=tool)
    return SCRIPTS[field].build(args)


class PackTest(unittest.TestCase):
    def test_round_trip_and_same_bytes(self):
        vals = [0.0, 1.5, -2.25, 1e6, 3.14159]
        p = common.pack_f32(vals, [5])
        got = common.unpack_f32(p)
        for a, b in zip(vals, got):
            self.assertAlmostEqual(a, b, delta=abs(a) * 1e-6 + 1e-6)
        self.assertEqual(p, common.pack_f32(vals, [5]))   # gzip without a time stamp: same bytes each run

    def test_data_js_reads_back(self):
        with tempfile.TemporaryDirectory() as d:
            path = common.write_data("x", {"a": [1, 2], "s": "壁"}, Path(d))
            self.assertEqual(common.read_data_js(path), ("x", {"a": [1, 2], "s": "壁"}))
            path.read_text(encoding="ascii")


class RunFolderTest(unittest.TestCase):
    def test_version_folder_and_run_folder(self):
        self.assertEqual(common.version_dirs(FX / "nvh"), [FX / "nvh" / "v001"])
        self.assertEqual(common.version_dirs(FX / "nvh" / "v001"), [FX / "nvh" / "v001"])
        with self.assertRaises(LookupError):
            common.find_analysis(FX / "nvh", "crash")

    def test_load_case_from_project_or_file(self):
        self.assertEqual(common.load_case_of(FX / "nvh" / "v001", "N1-40kmh")["test_id"], "N1")
        self.assertEqual(common.load_case_of(FX / "crash" / "v001", "C1-40kmh")["test_id"], "C1")
        self.assertIsNone(common.load_case_of(FX / "assembly" / "v001", "H1-interference"))


class FieldDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.obj = {f: make(f) for f in SCRIPTS}

    def test_common_shape(self):
        for f, o in self.obj.items():
            with self.subTest(field=f):
                self.assertTrue(COMMON_KEYS <= set(o), COMMON_KEYS - set(o))
                self.assertEqual(o["field"], f)
                fr = o["frame"]
                n = len(fr["node_ids"])
                self.assertEqual(len(common.unpack_f32(fr["nodes"])), 3 * n)
                self.assertEqual(len(fr["members"]), len(fr["member_ids"]))
                self.assertEqual(len(fr["members"]), len(fr["radius_mm"]))
                for a, b in fr["members"]:
                    self.assertTrue(0 <= a < n and 0 <= b < n)
                for p in o["parts"]:
                    self.assertEqual((len(p["c"]), len(p["s"]), len(p["r"])), (3, 3, 3))
                self.assertTrue(o["inputs"] and o["not_computed"])
                json.dumps(o)

    def test_nvh(self):
        o = self.obj["nvh"]
        rec = common.find_analysis(FX / "nvh", "modal")[1]
        n = len(o["frame"]["node_ids"])
        self.assertEqual(len(o["modes"]), 3)
        for m, f in zip(o["modes"], rec["fields"]):
            shape = common.unpack_f32(m["shape"])
            self.assertEqual(len(shape), 3 * n)
            self.assertAlmostEqual(m["f_Hz"], f["frequency_Hz"])
            amp = max(math.sqrt(sum(c * c for c in v)) for v in f["per_node"].values())
            self.assertAlmostEqual(m["amp_max"], amp, places=9)
            # the node order follows frame.node_ids
            i = o["frame"]["node_ids"].index("LHT")
            for a in range(3):
                self.assertAlmostEqual(shape[3 * i + a], f["per_node"]["LHT"][a], places=5)
        self.assertEqual(len(common.unpack_f32(o["frf"]["x"])), len(common.unpack_f32(o["frf"]["y"])))
        self.assertEqual({l["label"] for l in o["lines"]}, {"wheel rotation", "motor rotation"})

    def test_crash(self):
        o = self.obj["crash"]
        rec = common.find_analysis(FX / "crash", "crash")[1]
        self.assertEqual(len(o["stress"]["per_member"]), len(o["frame"]["member_ids"]))
        st = rec["fields"][0]["per_member"]
        self.assertAlmostEqual(o["stress"]["max"], max(st[m] for m in o["frame"]["member_ids"] if m in st))
        t = common.unpack_f32(o["t"])
        for k in ("decel", "crush", "wall_force"):
            self.assertEqual(len(common.unpack_f32(o[k])), len(t))
        self.assertAlmostEqual(o["a_max"], max(common.series(rec, "deceleration")["values"]))
        self.assertEqual([s["kind"] for s in o["scene"]], ["wall"])
        self.assertEqual(len(o["vectors"]), len(rec["vectors"]))
        self.assertEqual(o["model"], "A")
        self.assertTrue(any("A is a 1-D model: no deformed shape is drawn" in t for t in o["not_computed"]))
        self.assertTrue(any("a(t) / a_max" in t for t in o["not_computed"]))
        self.assertEqual(o["wall_x_mm"], o["scene"][0]["c"][0] - o["scene"][0]["s"][0] / 2)
        labels = {r["label"] for r in o["inputs"]}
        self.assertTrue({"speed", "wall", "mass"} <= labels, labels)
        # the page never moves the A vehicle: no crush(t) translation in its code path
        js = (VIEWER / "js" / "crash.js").read_text(encoding="utf-8")
        a_part = js[js.index("HV.crashA"):js.index("HV.crashB = ")]
        self.assertNotIn("boxes.off", a_part)
        self.assertNotIn("ghost.position", a_part)

    def test_crash_b(self):
        o = make("crash", "crash_b")
        rec = common.find_analysis(FX / "crash_b", "crash_beam")[1]
        nm = rec["fields"][0]
        self.assertEqual(o["model"], "B")
        fr, mo = o["frame"], o["motion"]
        n, ne, nF = len(nm["nodes"]), len(nm["members"]), len(nm["frames"]["t_s"])
        self.assertEqual((len(fr["node_ids"]), len(fr["members"]), len(fr["member_ids"])), (n, ne, ne))
        pos = common.unpack_f32(mo["pos"])
        self.assertEqual(len(pos), nF * n * 3)
        self.assertEqual(len(common.unpack_f32(mo["t"])), nF)
        self.assertEqual(len(mo["broken"]), nF)
        # frame 0 is the mesh at rest; the last frame is the record's last, node by node
        self.assertEqual(common.unpack_f32(fr["nodes"]), pos[:n * 3])
        last = nm["frames"]["positions_mm"][-1]
        for i in (0, n // 2, n - 1):
            for a in range(3):
                self.assertAlmostEqual(pos[((nF - 1) * n + i) * 3 + a], last[i][a], places=2)
        for k in ("strain", "fold"):
            v = common.unpack_f32(o["colour"][k])
            self.assertEqual(len(v), nF * ne)
            self.assertTrue(all(abs(x) < 1e-3 for x in v[:ne]), k)   # nothing at t = 0
        # an element's strain recomputed by hand at the last frame
        ia, ib = fr["members"][0]
        P0, PL = nm["frames"]["positions_mm"][0], last
        want = 100 * (math.dist(PL[ia], PL[ib]) - math.dist(P0[ia], P0[ib])) / math.dist(P0[ia], P0[ib])
        self.assertAlmostEqual(common.unpack_f32(o["colour"]["strain"])[(nF - 1) * ne], want, places=3)
        self.assertEqual(len(common.unpack_f32(o["part_off"])), nF * len(o["parts"]) * 3)
        b = o["beyond"]
        wx = o["wall_x_mm"]
        self.assertEqual(wx, o["scene"][0]["c"][0] - o["scene"][0]["s"][0] / 2)
        want_n = [sum(1 for p in F if p[0] > wx + b["tol_mm"]) for F in nm["frames"]["positions_mm"]]
        self.assertEqual(b["nodes"], want_n)
        self.assertEqual(len(b["parts"]), nF)
        self.assertTrue(any("Wall penetration" in t for t in o["notes"]))
        self.assertTrue(any("Member stresses are not in the crash_beam record" in t for t in o["not_computed"]))
        self.assertTrue({"speed", "wall", "wall friction", "beam break", "mass", "mesh"} <= {r["label"] for r in o["inputs"]})
        for k in ("t", "decel", "crush"):
            self.assertEqual(len(common.unpack_f32(o["series"][k])), len(rec["series"][0]["t_s"]))
        # --tool picks the kind; a run with only A falls back to A
        with self.assertRaises(LookupError):
            make("crash", "crash_b", tool="crash")
        self.assertEqual(make("crash", "crash", tool=None)["model"], "A")

    def test_nvh_strain_by_hand(self):
        d = {"nodes": [{"id": "a", "xyz_mm": [0, 0, 0]}, {"id": "b", "xyz_mm": [1000, 0, 0]}, {"id": "c", "xyz_mm": [2000, 0, 0]}],
             "members": [{"id": "m1", "node_a": "a", "node_b": "b", "section": "s", "material": "st"},
                         {"id": "m2", "node_a": "b", "node_b": "c", "section": "s", "material": "st"}],
             "sections": {"s": {"shape": "rect_tube", "dims_mm": {"b": 40, "h": 80, "t": 2}}},
             "materials": {"st": {"E_MPa": 200000.0}}}
        fr = common.frame_of(d)
        # rigid translation and rigid rotation about z: no strain
        for shape in ({"a": [1, 2, 3], "b": [1, 2, 3], "c": [1, 2, 3]},
                      {"a": [0, 0, 0], "b": [0, 1e-3 * 1000, 0], "c": [0, 1e-3 * 2000, 0]}):
            ms = nvh.member_strain(d, fr, shape)
            self.assertLess(max(ms["total"]), 1e-9)
        # stretch: axial = du / L
        ms = nvh.member_strain(d, fr, {"a": [0, 0, 0], "b": [1, 0, 0], "c": [3, 0, 0]})
        self.assertAlmostEqual(ms["axial"][0], 1e-3)
        self.assertAlmostEqual(ms["axial"][1], 2e-3)
        self.assertAlmostEqual(ms["bending"][0], 0.0)
        # a kink at b (b up by w): chord rotations +-w/L, node b's rotation is their mean 0, ends a and c
        # take their own member's chord; phi_b = -+w/L, curvature at b = 4 w / L^2, strain x h/2
        w, L = 1.0, 1000.0
        ms = nvh.member_strain(d, fr, {"a": [0, 0, 0], "b": [0, 0, w], "c": [0, 0, 0]})
        self.assertAlmostEqual(ms["bending"][0], 4 * w / L ** 2 * 40.0, places=12)
        sp = nvh.section_props(d["sections"]["s"])
        self.assertAlmostEqual(sp["A"], 40 * 80 - 36 * 76)
        self.assertAlmostEqual(sp["I_v"], (40 * 80 ** 3 - 36 * 76 ** 3) / 12)
        self.assertAlmostEqual(ms["energy"][0], 200000.0 * sp["I_v"] / L * 2 * (w / L) ** 2, places=6)

    def test_nvh_strain_data(self):
        o = self.obj["nvh"]
        ne = len(o["frame"]["member_ids"])
        self.assertIn("translations only", o["strain_method"])
        for m in o["modes"]:
            st, en = m["strain"], m["energy"]
            for k in ("axial", "bending", "total"):
                self.assertEqual(len(st[k]), ne)
            self.assertAlmostEqual(st["max"], max(st["total"]))
            self.assertGreater(st["max"], 0)
            self.assertAlmostEqual(st["per_mm"], st["max"] / m["amp_max"])
            self.assertEqual(len(en["share"]), ne)
            self.assertAlmostEqual(sum(s for s in en["share"] if s is not None), 1.0, places=9)
            shares = [t["share"] for t in en["top"]]
            self.assertEqual(shares, sorted(shares, reverse=True))
            self.assertTrue(0 < len(en["top"]) <= nvh.TOP)
            for t in en["top"]:
                self.assertIn(t["member"], o["frame"]["member_ids"])
                self.assertTrue(0 <= t["axial_part"] <= 1 + 1e-12)

    def test_assembly(self):
        o = self.obj["assembly"]
        rec = common.find_analysis(FX / "assembly", "assembly")[1]
        steps = o["steps"]
        self.assertEqual(len(steps), len(rec["assembly"]))
        self.assertEqual([s["order"] for s in steps], sorted(s["order"] for s in steps))
        for s in steps:
            self.assertIn(s["status"], ("ok", "blocked", "no_tool", "no_reach"))
            if s["pi"] >= 0 and s["path"]:
                p = o["parts"][s["pi"]]
                self.assertEqual(p["id"], s["part"])
                for a in range(3):   # the last path point plus off is the design's box centre
                    self.assertAlmostEqual(s["path"][-1][a] + s["off"][a], p["c"][a], places=6)
        n_tool = sum(1 for s in rec["assembly"] if s["tool_clearance"] is False and s["no_interference"] is not False)
        self.assertEqual(sum(1 for s in steps if s["status"] == "no_tool"), n_tool)
        self.assertEqual(len(o["interferences"]), 4)
        for it in o["interferences"]:
            kinds = [e["kind"] for e in it["ends"]]
            self.assertEqual(kinds, ["part", "member"])
            self.assertEqual(o["frame"]["member_ids"][it["ends"][1]["index"]], it["b"][len("member "):])
            self.assertGreater(it["depth"], 0)

    def test_keys_the_page_reads(self):
        # every D.<key> a field's script reads exists in that field's data; main.js and core.js read common keys
        crash_js = (VIEWER / "js" / "crash.js").read_text(encoding="utf-8")
        cut = crash_js.index("HV.crashB = ")
        parts = {f: [((VIEWER / "js" / f"{f}.js").read_text(encoding="utf-8"), o)] for f, o in self.obj.items()}
        parts["crash"] = [(crash_js[:cut], self.obj["crash"]), (crash_js[cut:], make("crash", "crash_b"))]
        for f, lst in parts.items():
            for js, o in lst:
                with self.subTest(field=f, model=o.get("model")):
                    used = set(re.findall(r"\bD\.([A-Za-z_]+)", js))
                    self.assertTrue(used <= set(o), used - set(o))
        for name in ("main.js", "core.js"):
            used = set(re.findall(r"\bD\.([A-Za-z_]+)", (VIEWER / "js" / name).read_text(encoding="utf-8")))
            self.assertTrue(used <= COMMON_KEYS, used - COMMON_KEYS)

    def test_command_line(self):
        with tempfile.TemporaryDirectory() as d:
            for f in SCRIPTS:
                r = subprocess.run([sys.executable, "-m", f"viewer.src.{f}", str(FX / f), "--out", d],
                                   cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(common.read_data_js(Path(d) / f"{f}.js")[0], f)

    def test_data_folder_is_ignored(self):
        self.assertIn("data/", (VIEWER / ".gitignore").read_text(encoding="utf-8").split())


def _system_python_with_playwright():
    exe = shutil.which("py")
    if not exe:
        return None
    r = subprocess.run([exe, "-c", "import playwright.sync_api"], capture_output=True)
    return exe if r.returncode == 0 else None


class PageTest(unittest.TestCase):
    """The page opened from disk in headless Chromium, with each field's data made from the fixtures.
    Needs Playwright for the system Python (py) and the network (three.js from jsDelivr)."""

    @classmethod
    def setUpClass(cls):
        cls.py = _system_python_with_playwright()
        if not cls.py:
            raise unittest.SkipTest("playwright is not available for the system Python (py)")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name) / "viewer"
        shutil.copytree(VIEWER, cls.dir, ignore=shutil.ignore_patterns("data", "src", "__pycache__", "*.py"))
        for f in SCRIPTS:
            common.write_data(f, make(f), cls.dir / "data")
        # the same page with the crash_beam (B) record as the crash field
        cls.dir_b = Path(cls.tmp.name) / "viewer_b"
        shutil.copytree(cls.dir, cls.dir_b)
        common.write_data("crash", make("crash", "crash_b"), cls.dir_b / "data")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _check(self, d, f):
        r = subprocess.run([self.py, str(Path(__file__).with_name("page_check.py")), str(d), f, "", "3"],
                           capture_output=True, text=True, encoding="utf-8", timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        out = json.loads(r.stdout)
        self.assertEqual(out["errors"], [])
        self.assertGreater(out["drawn_px"], 1000)
        return out

    def test_crash_b_page(self):
        out = self._check(self.dir_b, "crash")
        self.assertIn("B: beams", out["title"])
        self.assertIn("frame nodes beyond the wall face", out["hud"])
        self.assertIn("Member stresses are not in the crash_beam record", out["notes"])

    def test_crash_a_page_says_no_shape(self):
        out = self._check(self.dir, "crash")
        self.assertIn("A is a 1-D model: no deformed shape is drawn", out["hud"])

    def test_nvh_page_strain(self):
        out = self._check(self.dir, "nvh")
        self.assertIn("member strain", out["hud"])
        self.assertIn("strain energy, top members", out["hud"])
        self.assertIn("translations only", out["notes"])

    def test_fields_load_without_console_errors(self):
        for f in SCRIPTS:
            with self.subTest(field=f):
                r = subprocess.run([self.py, str(Path(__file__).with_name("page_check.py")), str(self.dir), f, "", "3"],
                                   capture_output=True, text=True, encoding="utf-8", timeout=300)
                self.assertEqual(r.returncode, 0, r.stderr[-2000:])
                out = json.loads(r.stdout)
                self.assertEqual(out["errors"], [])
                self.assertEqual(out["shown_field"], f)
                self.assertGreater(out["nT"], 1)
                self.assertGreater(out["drawn_px"], 1000)
                self.assertIn("NOT COMPUTED", out["notes"])


if __name__ == "__main__":
    unittest.main()
