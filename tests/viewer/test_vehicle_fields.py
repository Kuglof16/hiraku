"""The K2 / K3 vehicle fields of the viewer (handling, powertrain, aero) on fixtures trimmed from real records
(tests/viewer/fixtures/README.md): the data shape, the replay arrays against the record, the parts' K2 categories,
and the page in headless Chromium (skipped without Playwright for the system Python)."""

import json
import math
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.viewer.test_viewer import COMMON_KEYS, FX, VIEWER, _system_python_with_playwright
from viewer.src import aero, common, handling, powertrain

SCRIPTS = {"handling": handling, "aero": aero, "powertrain": powertrain}


def make(field, run=None, also=()):
    args = SimpleNamespace(run=FX / (run or field), version=None, analysis=None, also=[FX / a for a in also])
    return SCRIPTS[field].build(args)


def series(rec, ch):
    return common.series(rec, ch)["values"]


class VehicleDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.obj = {"handling": make("handling"), "aero": make("aero"),
                   "powertrain": make("powertrain", also=("powertrain_energy",))}

    def test_common_shape_and_no_paths(self):
        for f, o in self.obj.items():
            with self.subTest(field=f):
                self.assertTrue(COMMON_KEYS <= set(o), COMMON_KEYS - set(o))
                self.assertEqual(o["field"], f)
                self.assertTrue(o["cases"])
                for c in o["cases"]:
                    self.assertTrue(COMMON_KEYS <= set(c))
                    self.assertEqual([e["id"] for e in c["ledger"]["parts"]], [p["id"] for p in c["parts"]])
                    for e in c["ledger"]["parts"]:
                        self.assertIn(e["cat"], common.CATEGORIES)
                        self.assertAlmostEqual(sum(kg for _, kg in e["nodes"]), e["mass_kg"], places=9)
                text = json.dumps(o, ensure_ascii=False)
                self.assertNotRegex(text, r"[A-Za-z]:\\\\|/Users/|/home/|\\\\Temp\\\\")
                # the inputs' labels are Japanese (the param names translated)
                for r in o["inputs"]:
                    self.assertFalse(re.fullmatch(r"[a-z_]+", r["label"]), r)

    def test_replay_arrays_match_the_record(self):
        o = self.obj["handling"]
        rec = common.find_analysis(FX / "handling", "braking")[1]
        M = o["motion"]
        nT, nw, nc = M["nT"], len(M["corners"]), len(M["channels"])
        self.assertEqual(nT, len(common.series(rec, "body_x")["t_s"]))
        pose = common.unpack_f32(M["pose"])
        W = common.unpack_f32(M["wheels"])
        self.assertEqual((len(pose), len(W)), (6 * nT, nT * nw * nc))
        k = nT - 1
        for a, ch in enumerate(("body_x", "body_y", "body_z")):
            self.assertAlmostEqual(pose[6 * k + a], series(rec, ch)[k] * 1000, delta=0.05)
        self.assertAlmostEqual(pose[6 * k + 4], series(rec, "pitch")[k], places=4)
        ids = [c["id"] for c in M["corners"]]
        i = ids.index("RL")
        j = M["channels"].index("wheel_load")
        self.assertAlmostEqual(W[(k * nw + i) * nc + j], series(rec, "wheel_load.RL")[k], delta=1e-2)
        j = M["channels"].index("wheel_center_z")
        self.assertAlmostEqual(W[(k * nw + i) * nc + j], series(rec, "wheel_center_z.RL")[k] * 1000, delta=0.05)
        # the wheel centre in the body frame, from the first instant: R0^T (centre0 - origin0)
        R0 = common.rot_zyx(*(series(rec, ch)[0] for ch in ("roll", "pitch", "yaw")))
        c0 = [series(rec, f"wheel_center_{a}.RL")[0] * 1000 - series(rec, f"body_{a}")[0] * 1000 for a in "xyz"]
        for a in range(3):
            self.assertAlmostEqual(M["corners"][i]["cb"][a], sum(R0[r][a] * c0[r] for r in range(3)), places=6)
        self.assertEqual(o["judge"]["ok"], o["judge"]["value"] <= o["judge"]["limit"])
        self.assertEqual({s["kind"] for s in o["scene"]}, {"ground", "gate"})

    def test_rot_zyx(self):
        # yaw 90: x -> y; roll 90: y -> z; R orthonormal
        R = common.rot_zyx(0, 0, 90)
        self.assertAlmostEqual(R[1][0], 1.0)
        R = common.rot_zyx(90, 0, 0)
        self.assertAlmostEqual(R[2][1], 1.0)
        R = common.rot_zyx(10, -20, 35)
        for a in range(3):
            for b in range(3):
                self.assertAlmostEqual(sum(R[r][a] * R[r][b] for r in range(3)), 1.0 if a == b else 0.0)

    def test_k2_parts(self):
        from hiraku.solvers.k2_vehicle.model import HALF_UNSPRUNG_KINDS, UNSPRUNG_KINDS
        o = self.obj["handling"]
        L = o["ledger"]
        kinds = {p["id"]: p["kind"].lower() for p in o["parts"]}
        for e, mode in zip(L["parts"], o["motion"]["part_mode"]):
            if e["cat"] == "not_in":
                continue
            k = kinds[e["id"]]
            want = "unsprung" if k in UNSPRUNG_KINDS else "half" if k in HALF_UNSPRUNG_KINDS else "sprung"
            self.assertEqual(e["cat"], want, e["id"])
            self.assertEqual(mode, {"unsprung": 1, "half": 2}.get(want, 0))
        self.assertTrue({"sprung", "unsprung"} <= {e["cat"] for e in L["parts"]})
        self.assertIn("k2_vehicle", L["members"]["fn"])

    def test_powertrain(self):
        o = self.obj["powertrain"]
        launch, energy = o["cases"]
        rec = common.find_analysis(FX / "powertrain", "launch")[1]
        nT, nw = launch["motion"]["nT"], len(launch["motion"]["corners"])
        tq = common.unpack_f32(launch["drive"]["torque"])
        self.assertEqual(len(tq), nT * nw)
        driven = launch["drive"]["driven"]
        k = nT - 1
        total = series(rec, "wheel_torque")[k]
        ids = [c["id"] for c in launch["motion"]["corners"]]
        self.assertAlmostEqual(sum(tq[k * nw:(k + 1) * nw]), total, delta=abs(total) * 1e-5 + 1e-3)
        for i, cid in enumerate(ids):
            self.assertAlmostEqual(tq[k * nw + i], total / len(driven) if cid in driven else 0.0, delta=abs(total) * 1e-5 + 1e-3)
        # slip by hand at a middle sample: omega from the spin angle's central difference
        m = nT // 2
        t = common.series(rec, "wheel_spin.RL")["t_s"]
        sp = series(rec, "wheel_spin.RL")
        om = math.radians(sp[m + 1] - sp[m - 1]) / (t[m + 1] - t[m - 1])
        v = series(rec, "speed")[m] / 3.6
        r = next(c["r"] for c in launch["motion"]["corners"] if c["id"] == "RL") / 1000
        self.assertAlmostEqual(common.unpack_f32(launch["drive"]["slip"])[m * nw + ids.index("RL")], (om * r - v) / max(abs(v), 0.5), places=4)
        # drive_energy: no motion, the battery part and its share used
        erec = common.find_analysis(FX / "powertrain_energy", "drive_energy")[1]
        self.assertEqual(energy["kind"], "energy")
        self.assertNotIn("motion", energy)
        self.assertEqual(energy["battery"]["part"], "battery")
        self.assertEqual(energy["parts"][energy["battery"]["index"]]["id"], "battery")
        self.assertEqual(len(common.unpack_f32(energy["series"]["soc"])), len(series(erec, "soc")))
        st = {s["label"]: s for s in energy["ledger"]["stated"]}
        self.assertIn("vehicle_mass", st)

    def test_aero_wind(self):
        o = self.obj["aero"]
        rec = common.find_analysis(FX / "aero", "crosswind")[1]
        w = o["wind"]
        self.assertAlmostEqual(w["side_force_N"], common.result(rec, "side_force"))
        self.assertEqual(w["gust"], 30.0)
        self.assertAlmostEqual(sum(c * c for c in w["dir"]), 1.0, places=9)
        self.assertLess(w["dir"][1], 0)   # wind from the left pushes to -y
        self.assertIn("wind_speed", o["plots"])
        self.assertTrue(any("流れの場" in t for t in o["notes"]))

    def test_keys_the_page_reads(self):
        for f, o in self.obj.items():
            js = (VIEWER / "js" / f"{f}.js").read_text(encoding="utf-8")
            used = set(re.findall(r"\bD\.([A-Za-z_]+)", js))
            with self.subTest(field=f):
                self.assertTrue(used <= set(o), used - set(o))
        used = set(re.findall(r"\bD\.([A-Za-z_]+)", (VIEWER / "js" / "vehicle.js").read_text(encoding="utf-8")))
        self.assertTrue(used <= set(self.obj["handling"]), used - set(self.obj["handling"]))


class VehiclePageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.py = _system_python_with_playwright()
        if not cls.py:
            raise unittest.SkipTest("playwright is not available for the system Python (py)")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name) / "viewer"
        shutil.copytree(VIEWER, cls.dir, ignore=shutil.ignore_patterns("data", "src", "__pycache__", "*.py"))
        common.write_data("handling", make("handling"), cls.dir / "data")
        common.write_data("aero", make("aero"), cls.dir / "data")
        common.write_data("powertrain", make("powertrain", also=("powertrain_energy",)), cls.dir / "data")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def check(self, f):
        r = subprocess.run([self.py, str(Path(__file__).with_name("page_check.py")), str(self.dir), f, "", "10"],
                           capture_output=True, text=True, encoding="utf-8", timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        out = json.loads(r.stdout)
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["shown_field"], f)
        self.assertGreater(out["drawn_px"], 1000)
        self.assertEqual(out["over_view"], [])
        self.assertEqual(out["layout_overlap"], [])
        self.assertFalse(out["notes_open"])
        self.assertNotRegex(out["page_text"], r"[A-Za-z]:\\|/Users/|/home/")
        self.assertIn(out["part_pick"].get("cat"), set(common.CATEGORIES), out["part_pick"])
        self.assertIn("判定", out["hud"])
        return out

    def test_handling(self):
        out = self.check("handling")
        self.assertGreater(out["boxes_moved"], 0)
        self.assertTrue(out["speed_shown"])

    def test_aero(self):
        out = self.check("aero")
        self.assertIn("風速", out["tline"])

    def test_powertrain(self):
        out = self.check("powertrain")
        self.assertIn("輪のトルク", out["tline"])


if __name__ == "__main__":
    unittest.main()
