"""The thermal, fatigue and strength fields of the viewer (viewer/src/{thermal,fatigue,strength}.py) on fixtures
trimmed from real records and saved frames (tests/viewer/fixtures/README.md), and their pages in headless Chromium
when the system Python has Playwright (skipped otherwise)."""

import json
import math
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.viewer import no_paths
from types import SimpleNamespace

import numpy as np

from viewer.src import common, fatigue, ledgers, strength, thermal
from tests.viewer.test_viewer import COMMON_KEYS, _system_python_with_playwright

ROOT = Path(__file__).resolve().parents[2]
FX = Path(__file__).resolve().parent / "fixtures"
VIEWER = ROOT / "viewer"
CATS = set(common.CATEGORIES) | set(ledgers.CATEGORIES_K3)


def args(run, **kw):
    return SimpleNamespace(run=FX / run, version=None, analysis=None, energy=None, frames=None, name=None, every=1, **kw)


def make(field):
    if field == "thermal":
        return thermal.build(args("thermal"))
    if field == "fatigue":
        return fatigue.build(args("fatigue"))
    if field == "strength_beam":
        return strength.build(args("strength_beam"))
    return strength.build(SimpleNamespace(**{**vars(args("strength_shell/v005")), "frames": str(FX / "strength_shell" / "frames"),
                                             "name": "shell-fixture"}))


FIELDS = {"thermal": "thermal", "fatigue": "fatigue", "strength_beam": "strength", "strength_shell": "strength"}

# Run with the system Python (Playwright): opens <viewer dir>/index.html#thermal and prints, as JSON, the thermal
# controller's probe (node boxes' opacity and colours, labels) at the first time and at the hottest one (the time of
# the highest node temperature), whether the 熱の部品へ button is there, and the console errors.
THERMAL_PROBE = r'''
import json, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

if __name__ == "__main__":
    url = (Path(sys.argv[1]).resolve() / "index.html").as_uri() + "#thermal"
    errors, out = [], {}
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        page = b.new_page(viewport={"width": 1480, "height": 900})
        page.on("console", lambda m: errors.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.goto(url)
        page.wait_for_function("window.__ready === true", timeout=120000)
        hot = page.evaluate("""async () => { const D = window.VIEW_DATA.thermal; let best = -1e9, k = 0;
            for (const n of D.nodes) { if (!n.temp) continue; const v = await HV.unpack(n.temp);
              v.forEach((x, i) => { if (x > best) { best = x; k = i; } }); } return k; }""")
        for name, k in (("start", 0), ("hot", hot)):
            page.eval_on_selector("#time", f"e => {{ e.value = {k}; e.dispatchEvent(new Event('input')); }}")
            page.wait_for_timeout(300)
            out[name] = {"k": k, **page.evaluate("HV.state.ctl.probe()")}
        out["toHot"] = page.evaluate("!!document.getElementById('toHot')")
        out["errors"] = errors
        b.close()
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False))
'''


class TfsDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.obj = {k: make(k) for k in FIELDS}

    def test_common_shape_and_ledger(self):
        for k, o in self.obj.items():
            with self.subTest(fixture=k):
                self.assertTrue(COMMON_KEYS <= set(o), COMMON_KEYS - set(o))
                self.assertEqual(o["field"], FIELDS[k])
                self.assertTrue(o["inputs"])
                self.assertNotRegex(json.dumps(o, ensure_ascii=False), no_paths.IN_JSON)
                L = o["ledger"]
                self.assertEqual([e["id"] for e in L["parts"]], [p["id"] for p in o["parts"]])
                for e in L["parts"]:
                    self.assertIn(e["cat"], CATS)
                    self.assertTrue(e["fn"].startswith("hiraku."), e["fn"])
                    if e["cat"] in ("frame_nodes", "unsprung"):
                        self.assertAlmostEqual(sum(kg for _, kg in e["nodes"]), e["mass_kg"], places=9)
                self.assertEqual(sum(r["count"] for r in L["table"]), len(L["parts"]))
                json.dumps(o)

    def test_thermal(self):
        o = self.obj["thermal"]
        rec = common.find_analysis(FX / "thermal", "thermal")[1]
        d = common.read_json(FX / "thermal" / "v001" / "design.json")
        t = common.unpack_f32(o["t"])
        self.assertEqual(len(t), len(common.series(rec, "temp.battery")["t_s"]))
        names = [n["name"] for n in o["nodes"]]
        self.assertEqual(names, ["battery", "motor", "controller"])
        for n in o["nodes"]:
            s = common.series(rec, f"temp.{n['name']}")["values"]
            self.assertEqual(len(common.unpack_f32(n["temp"])), len(t))
            self.assertAlmostEqual(n["max"], max(s))
            # the network as the design gives it: C and R from design.variables
            self.assertEqual(n["C_J_per_K"], d["variables"][f"{n['name']}_heat_capacity"]["value"])
            self.assertAlmostEqual(n["R_K_per_W"], d["variables"][f"{n['name']}_thermal_resistance"]["value"])
            self.assertAlmostEqual(n["tau_s"], n["C_J_per_K"] * n["R_K_per_W"])
            self.assertEqual(n["limit"]["value"], d["variables"][f"{n['name']}_max_temperature"]["value"])
            self.assertEqual(n["ok"], n["max"] <= n["limit"]["value"])
        # each part box coloured by the node of its kind; the criterion judged
        for p, nn in zip(o["parts"], o["part_node"]):
            self.assertEqual(nn, p["kind"] if p["kind"] in names else None)
        c = o["criterion"]
        self.assertEqual((c["quantity"], c["value"]), ("temp_battery_max", common.result(rec, "temp_battery_max")))
        self.assertEqual(c["ok"], c["value"] <= c["limit"])
        # the power / loss / heat series with their part_id; drive_energy found in the same run
        kinds = {s["kind"] for s in o["chain"]}
        self.assertEqual(kinds, {"power", "loss", "heat_to_air"})
        self.assertTrue(all(s["part_id"] for s in o["chain"]))
        self.assertIsNotNone(o["energy"])
        self.assertEqual(len(common.unpack_f32(o["energy"]["soc"])), len(common.unpack_f32(o["energy"]["t"])))
        cats = {e["id"]: e["cat"] for e in o["ledger"]["parts"]}
        self.assertEqual(cats["battery"], "heat_node")
        self.assertEqual(cats["reducer"], "power_stage")
        self.assertEqual(cats["cargo"], "mass_only")

    def test_fatigue(self):
        o = self.obj["fatigue"]
        rec = common.find_analysis(FX / "fatigue", "fatigue")[1]
        pm = rec["fields"][0]["per_member"]
        self.assertEqual(len(o["damage"]["per_member"]), len(o["frame"]["member_ids"]))
        self.assertAlmostEqual(o["damage"]["max"], max(pm[m] for m in o["frame"]["member_ids"] if m in pm))
        self.assertEqual(o["damage"]["worst"], next(r["where"] for r in rec["results"] if r["quantity"] == "fatigue_damage"))
        self.assertEqual(o["criterion"]["ok"], o["criterion"]["value"] < 1.0)
        self.assertEqual(len(o["wheels"]), 4)
        self.assertEqual(len(common.unpack_f32(o["stress"]["values"])), len(common.unpack_f32(o["stress"]["t"])))
        self.assertEqual(len(o["sn"]["x"]), len(o["sn"]["values"]))
        r = o["road"]
        self.assertEqual(len(r["tracks"]), 2)
        self.assertAlmostEqual(r["speed_mm_s"], 40.0 / 3.6 * 1000.0)
        self.assertEqual(r["x_front_mm"], max(s["position_mm"][0] for s in common.read_json(FX / "fatigue" / "v001" / "design.json")["supports"]))
        for tr in r["tracks"]:
            self.assertEqual(len(common.unpack_f32(tr["z"])), len(common.unpack_f32(r["x"])))

    def test_strength_beam(self):
        o = self.obj["strength_beam"]
        rec = common.find_analysis(FX / "strength_beam", "frame_static")[1]
        st = next(f for f in rec["fields"] if f["kind"] == "stress")["per_member"]
        self.assertEqual(o["model"], "beam")
        self.assertAlmostEqual(o["stress"]["max"], max(st[m] for m in o["frame"]["member_ids"] if m in st))
        self.assertEqual(o["criterion"]["ok"], o["criterion"]["value"] <= o["criterion"]["limit"])
        n = len(o["parts"])
        for k in ("c", "t", "w"):
            self.assertEqual(len(common.unpack_f32(o["part_motion"][k])), 3 * n)
        # the yield marks are the frame members' materials only
        d = common.read_json(FX / "strength_beam" / "v005" / "design.json")
        used = {m["material"] for m in d["members"]}
        frame_yields = {float(d["materials"][k]["yield_MPa"]) for k in used if d["materials"][k].get("yield_MPa")}
        self.assertTrue(o["marks"])
        self.assertTrue({m["v"] for m in o["marks"]} <= frame_yields)
        st_ = {s["label"]: s for s in o["ledger"]["stated"]}
        self.assertTrue(st_["frame_mass"]["agrees"], st_["frame_mass"])

    def test_strength_shell(self):
        o = self.obj["strength_shell"]
        z = np.load(FX / "strength_shell" / "frames" / "frames.npz")
        self.assertEqual(o["model"], "shell")
        self.assertEqual(o["source"]["run"], "shell-fixture")
        T, N = z["disp"].shape[:2]
        E = len(z["elements"])
        np.testing.assert_array_equal(strength.unpack_np(o["mesh"]["quads"]), z["elements"])
        vm = strength.unpack_np(o["vm"]) / 10.0
        self.assertLessEqual(np.abs(vm - z["vm_max"]).max(), 0.05 + 1e-4)   # 0.1 MPa steps, float32 values
        D = strength.decode_disp(o["disp"], o["disp_scale"])
        self.assertEqual(D.shape, (T, N, 3))
        self.assertLessEqual(np.abs(D - z["disp"]).max(), o["disp_scale"] * 0.5 + 1e-6)
        self.assertEqual(len(o["t"]), T)
        self.assertEqual(len(o["hot"]), T)
        self.assertEqual(len(o["el_max"]), T)
        excl = z["excluded"].astype(bool)
        for k in range(T):
            self.assertAlmostEqual(o["el_max"][k][0], float(z["vm_max"][k][~excl].max()), places=4)
        self.assertEqual(strength.unpack_np(o["part_off"]).shape, (T, len(o["parts"]), 3))
        lp = common.read_json(FX / "strength_shell" / "frames" / "load_paths.json")
        self.assertEqual(len(o["mounts"]), len(lp["mounts"]))
        for m, q in zip(o["mounts"], lp["mounts"]):
            self.assertEqual(strength.unpack_np(m["xyz"]).shape, (len(q["nodes"]), 3))
        # the members the saved mesh holds as elements count as meshed (stiffness); the wheels are left out
        cats = {e["id"]: e["cat"] for e in o["ledger"]["parts"]}
        self.assertEqual(cats.get("wheel_FL"), "not_in")
        self.assertEqual(E, len(strength.unpack_np(o["mesh"]["member"])))

    def test_disp_encoding_by_hand(self):
        rng = np.random.default_rng(0)
        D = np.cumsum(rng.normal(size=(5, 7, 3)), axis=0).astype(np.float32)
        p, s = strength.encode_disp(D)
        self.assertLessEqual(np.abs(strength.decode_disp(p, s) - D).max(), s * 0.5 + 1e-7)

    def test_design_changes_text(self):
        base = {"variables": {"tower_b": {"value": 30, "unit": "mm"}, "rail_b": {"value": 40, "unit": "mm"}}}
        ch, unknown = strength._changes("v005 + tower_b 35, gusset_ends 1, rail_b 45, tyre 60 N/mm", base)
        self.assertEqual(ch, {"tower_b": {"value": 35.0, "unit": "mm"}, "rail_b": {"value": 45.0, "unit": "mm"}})
        self.assertEqual(unknown, ["gusset_ends 1"])

    def test_keys_the_page_reads(self):
        for name, objs in (("thermal", [self.obj["thermal"]]), ("fatigue", [self.obj["fatigue"]])):
            used = set(re.findall(r"\bD\.([A-Za-z_]+)", (VIEWER / "js" / f"{name}.js").read_text(encoding="utf-8")))
            self.assertTrue(used <= set(objs[0]), used - set(objs[0]))
        js = (VIEWER / "js" / "strength.js").read_text(encoding="utf-8")
        cut, cut2 = js.index("HV.strengthShell = "), js.index("HV.strengthLoadPath = ")
        from viewer.src import build
        gap = Path(__file__).resolve().parent / "fixtures" / "strength_gap"
        rec_shell, path = (build.for_analysis(gap, a)["data"] for a in ("ana-0039", "ana-0017"))
        # the shell keys only a record's one instant has (the saved frames' shell reads them guarded)
        only_record = {"single", "member_plot"}
        for part, o, extra in ((js[js.index("HV.strengthBeam = "):cut], self.obj["strength_beam"], set()),
                               (js[cut:cut2], self.obj["strength_shell"], only_record), (js[cut:cut2], rec_shell, set()),
                               (js[cut2:], path, set())):
            used = set(re.findall(r"\bD\.([A-Za-z_]+)", part)) - extra
            self.assertTrue(used <= set(o), used - set(o))

    def test_registered_on_the_page(self):
        html = (VIEWER / "index.html").read_text(encoding="utf-8")
        main = (VIEWER / "js" / "main.js").read_text(encoding="utf-8")
        for f in ("strength", "fatigue", "thermal"):
            self.assertIn(f'<script src="js/{f}.js"></script>', html)
            self.assertIn(f"'{f}'", main)


class GroundTest(unittest.TestCase):
    """The surroundings under the vehicle (viewer/src/ground.py, the same rules as js/look.js HV.groundOf and
    js/vehicle.js): in every field, at t = 0, the tyres touch the ground or road drawn under them."""

    @classmethod
    def setUpClass(cls):
        from tests.viewer import test_vehicle_fields as tv
        from tests.viewer import test_viewer as t1
        cls.obj = {"thermal": make("thermal"), "fatigue": make("fatigue"), "strength_beam": make("strength_beam"),
                   "strength_shell": make("strength_shell"), "nvh": t1.make("nvh"), "nvh_shell": t1.make_shell(),
                   "assembly": t1.make("assembly"), "crash_a": t1.make("crash"), "crash_b": t1.make("crash", "crash_b"),
                   "crash_contact": t1.make("crash", "crash_contact"), "handling": tv.make("handling"),
                   "aero": tv.make("aero"), "powertrain": tv.make("powertrain", also=("powertrain_energy",))}

    def test_ground_of_the_supports(self):
        from viewer.src import ground
        for k, o in self.obj.items():
            with self.subTest(field=k):
                z, src = ground.static_ground(o)
                self.assertIsNotNone(z)
                g = o.get("ground") or (o.get("cases") or [{}])[0].get("ground")
                if g:   # data made now carry the supports' ground (common.ground_of)
                    self.assertEqual(src, "supports" if o.get("ground") else src)
                    self.assertAlmostEqual(g["z"], min(w["c"][2] - w["r"] for w in g["wheels"]), places=9)

    def test_ground_of_rules(self):
        d = {"supports": [{"id": "F", "position_mm": [0, 0, 300.0], "radius_mm": 300.0},
                          {"id": "R", "position_mm": [-2000, 0, 310.0], "radius_mm": 300.0, "loaded_radius_mm": 290.0}]}
        g = common.ground_of(d)
        self.assertEqual(g["z"], 0.0)
        self.assertEqual([w["gap_mm"] for w in g["wheels"]], [0.0, 20.0])
        self.assertIn("負荷時の半径", g["from"])
        self.assertIsNone(common.ground_of({"supports": []}))

    def test_step_edge_distance(self):
        from viewer.src import ground
        step = [{"kind": "step", "c": [1000.0, 0.0, 50.0], "s": [1000.0, 2000.0, 100.0]}]
        # a wheel centre 300 mm up, 200 mm before the step's face (x = 500): the edge (500, 100) is nearer than the road
        self.assertAlmostEqual(ground.k2_distance(step, 300.0, 0.0, 300.0), math.hypot(200.0, 200.0), places=9)
        self.assertAlmostEqual(ground.k2_distance(step, -500.0, 0.0, 300.0), 300.0, places=9)
        self.assertAlmostEqual(ground.k2_surface(step, 800.0, 0.0), 100.0, places=9)

    def test_tyres_on_the_ground_at_t0(self):
        from viewer.src import ground
        worst = {}
        for k, o in self.obj.items():
            gaps = ground.contact_gaps(o)
            with self.subTest(field=k):
                if not gaps and not any(str(p.get("kind")).lower() == "wheel" for p in o.get("parts") or []) and not o.get("wheels"):
                    continue   # a trimmed fixture with no wheels: no tyres drawn (the ground from the wall's foot)
                self.assertTrue(gaps, k)
                for g in gaps:
                    if "penetration_mm" in g:   # K2: the tyre sinks by its own deflection (wheel load / tyre stiffness)
                        self.assertLessEqual(abs(g["tyre_bottom_gap_mm"]), 2.0, g)
                        self.assertLess(g["penetration_mm"], 0.1 * 300.0, g)   # never visibly deep (< 10 % of r)
                    elif k == "fatigue":
                        # the tyres stand on the ground plane; the road profile's own height passes under them
                        w = next(w for w in o["wheels"] if w["support"] == g["id"])
                        self.assertLessEqual(abs(w["c"][2] - w["r"] - o["road"]["ground_z_mm"]), 2.0)
                    else:
                        self.assertLessEqual(abs(g["tyre_bottom_gap_mm"]), 2.0, g)
                worst[k] = ground.largest(gaps)[0]
        self.assertTrue(worst)


class TfsPageTest(unittest.TestCase):
    """The three fields' pages from disk in headless Chromium: no console errors, drawn, nothing over the 3D view."""

    @classmethod
    def setUpClass(cls):
        cls.py = _system_python_with_playwright()
        if not cls.py:
            raise unittest.SkipTest("playwright is not available for the system Python (py)")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dirs = {}
        for k, f in FIELDS.items():
            d = Path(cls.tmp.name) / k / "viewer"
            shutil.copytree(VIEWER, d, ignore=shutil.ignore_patterns("data", "src", "__pycache__", "*.py"))
            common.write_data(f, make(k), d / "data")
            cls.dirs[k] = d

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _check(self, k):
        r = subprocess.run([self.py, str(Path(__file__).with_name("page_check.py")), str(self.dirs[k]), FIELDS[k], "", "1"],
                           capture_output=True, text=True, encoding="utf-8", timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        out = json.loads(r.stdout)
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["shown_field"], FIELDS[k])
        self.assertGreater(out["drawn_px"], 1000)
        self.assertEqual(out["over_view"], [])
        self.assertEqual(out["layout_overlap"], [])
        self.assertFalse(out["notes_open"])
        self.assertNotRegex(out["page_text"], no_paths.ON_PAGE)
        self.assertIn("部品と計算での扱い", out["panel"])
        # the ground is drawn and the tyres stand on it (their lowest point within 2 mm of it)
        self.assertIsNotNone(out["ground"])
        self.assertTrue(out["ground"]["visible"])
        for g in out["ground"]["tyre_gaps"]:
            self.assertLessEqual(abs(g), 2.0, out["ground"])
        self.assertIsNotNone(out["surround"])   # where the surroundings come from, in a closed panel
        self.assertFalse(out["surround"]["open"])
        self.assertIn("地面", out["surround"]["text"])
        return out

    def test_thermal_page(self):
        out = self._check("thermal")
        self.assertIn("判定", out["hud"])
        self.assertIn("熱回路", out["panel"])

    def _thermal_probe(self, d):
        """The thermal page's node boxes and labels (ctl.probe) at the first time and at the hottest one."""
        script = Path(self.tmp.name) / "thermal_probe.py"
        script.write_text(THERMAL_PROBE, encoding="utf-8")
        r = subprocess.run([self.py, str(script), str(d)], capture_output=True, text=True, encoding="utf-8", timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        return json.loads(r.stdout)

    def test_thermal_node_parts(self):
        out = self._thermal_probe(self.dirs["thermal"])
        self.assertEqual(out["errors"], [])
        self.assertTrue(out["toHot"])
        o = make("thermal")
        names = {n["name"] for n in o["nodes"] if n["temp"] and n["name"] in o["part_node"]}
        for k in ("start", "hot"):
            p = out[k]
            self.assertGreaterEqual(p["opacity"], 0.8)
            self.assertEqual({L["name"] for L in p["labels"]}, names)
            for L in p["labels"]:
                self.assertTrue(L["visible"])
                self.assertRegex(L["text"], r"^(電池|モーター|制御器) -?\d+\.\d ℃")
                self.assertNotIn("を超え", L["text"])   # the fixture is within its limits
        cs, ch = dict(map(tuple, ((c[0], tuple(c[1])) for c in out["start"]["colours"]))), {c[0]: tuple(c[1]) for c in out["hot"]["colours"]}
        self.assertTrue(any(max(abs(a - b) for a, b in zip(cs[n], ch[n])) > 0.05 for n in ch), (cs, ch))
        red = (0.784, 0.251, 0.165)
        for c in ch.values():
            self.assertGreater(max(abs(a - b) for a, b in zip(c, red)), 0.02)   # no red within the limits

    def test_thermal_over_limit_red(self):
        o = make("thermal")
        n0 = next(n for n in o["nodes"] if n["temp"] and n["name"] in o["part_node"])
        n0["limit"] = {"value": (o["ambient_C"] + n0["max"]) / 2, "source": "test"}
        d = Path(self.tmp.name) / "thermal_red" / "viewer"
        shutil.copytree(VIEWER, d, ignore=shutil.ignore_patterns("data", "src", "__pycache__", "*.py"))
        common.write_data("thermal", o, d / "data")
        out = self._thermal_probe(d)
        self.assertEqual(out["errors"], [])
        col = {c[0]: c[1] for c in out["hot"]["colours"]}
        for a, b in zip(col[n0["name"]], (0.784, 0.251, 0.165)):
            self.assertAlmostEqual(a, b, places=3)
        lab = next(L for L in out["hot"]["labels"] if L["name"] == n0["name"])
        self.assertRegex(lab["text"], r"上限 [\d.]+ ℃ を超え$")

    def test_fatigue_page(self):
        out = self._check("fatigue")
        self.assertIn("マイナー損傷", out["hud"])

    def test_strength_beam_page(self):
        out = self._check("strength_beam")
        self.assertIn("実寸ではない", out["hud"])
        self.assertGreater(out["boxes_moved"], 0)

    def test_strength_shell_page(self):
        out = self._check("strength_shell")
        self.assertIn("ホットスポット", out["tline"])
        self.assertIn("部位へ", out["panel"])


if __name__ == "__main__":
    unittest.main()
