"""The NVH field's 「接地面の加振」 variant (viewer/src/nvh_frf.py, js/nvh.js HV.nvhFrf) on a small made-up output of
studies/lere/frf.py (two sets, a flat plate of quads for each), and its page in headless Chromium when the system
Python has Playwright (skipped otherwise)."""

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

from viewer.src import common, nvh_frf
from tests.viewer.test_viewer import _system_python_with_playwright

ROOT = Path(__file__).resolve().parents[2]
VIEWER = ROOT / "viewer"
PATTERNS = ("in_phase", "roll", "pitch")
OUTPUTS = ("seat", "column", "rollbar_top", "rollbar_top_lat", "frame_mid")


def fake_set(folder: Path, name: str, f_peak: float):
    """frf.json and frf_shapes.npz of a plate (1000 x 2100 mm, 20 x 12 quads) with a one-mode response peaking at
    f_peak."""
    folder.mkdir(parents=True)
    nx, ny = 21, 13
    X, Y = np.meshgrid(np.linspace(-1000, 0, nx), np.linspace(-1050, 1050, ny), indexing="ij")  # the sets touch
    nodes = np.stack([X.ravel(), Y.ravel(), np.full(X.size, 400.0)], axis=1).astype(np.float32)
    q = [[i * ny + j, (i + 1) * ny + j, (i + 1) * ny + j + 1, i * ny + j + 1] for i in range(nx - 1) for j in range(ny - 1)]
    elements = np.asarray(q, np.int32)
    f = np.round(np.arange(0.1, 30.0001, 0.1), 4)
    H = 1.0 / (1 - (f / f_peak) ** 2 + 0.1j * f / f_peak)
    curves = {p: {o: {"disp_mm": np.abs(H).tolist(), "acc_mps2": ((2 * math.pi * f) ** 2 * np.abs(H) / 1000).tolist(),
                      "phase_deg": np.degrees(np.angle(H)).tolist()} for o in OUTPUTS} for p in PATTERNS}
    ip = int(np.argmax(np.abs(H)))
    pk = [{"f_Hz": float(f[ip]), "acc_mps2": float(curves["in_phase"]["seat"]["acc_mps2"][ip]), "disp_mm": float(abs(H[ip])),
           "mode": 3, "mode_f_Hz": f_peak, "mode_kind": "roll bar"}]
    chosen = sorted({round(float(f[ip]), 2), 5.0, 10.0})
    keys, disp, vm = [], [], []
    for p in PATTERNS:
        for fq in chosen:
            h = 1.0 / (1 - (fq / f_peak) ** 2 + 0.1j * fq / f_peak)
            u = np.zeros((len(nodes), 3), complex)
            u[:, 2] = h * np.sin(np.pi * (nodes[:, 0] + 1000) / 1000)
            keys.append(f"{p}@{fq:g}")
            disp.append(u.astype(np.complex64))
            vm.append(np.abs(h) * np.linspace(0, 10, len(elements)).astype(np.float32))
    np.savez_compressed(folder / "frf_shapes.npz", nodes=nodes, elements=elements, member=np.array(["plate"] * len(elements)),
                        keys=np.asarray(keys), disp=np.stack(disp), vm=np.stack(vm))
    sc = {o: {"direct_mm": 1.0, "modal_0Hz_mm": 1.0, "modal_0.1Hz_mm": 1.0} for o in OUTPUTS}
    res = {"name": name, "doc": f"plate {name}", "load_case": "N1", "tyre_N_per_mm": 60.0, "element_size_mm": 10.0,
           "frequencies_Hz": f.tolist(), "curves": curves,
           "peaks": {p: {o: pk for o in OUTPUTS} for p in PATTERNS},
           "outputs": {o: {"label": o, "where": o} for o in OUTPUTS},
           "modes": [{"i": 0, "f_Hz": 0.0, "kind": "rigid (free in x, y, yaw)"},
                     {"i": 1, "f_Hz": 1.4, "kind": "body bounce"},
                     {"i": 2, "f_Hz": 9.8, "kind": "wheel hop (rear, in phase)"},
                     {"i": 3, "f_Hz": f_peak, "kind": "roll bar"}],
           "rollbar_mode_Hz": f_peak, "first_frame_mode_Hz": 12.0, "hop_band_Hz": [9.6, 10.1],
           "damping": {"struct_zeta": 0.02, "sources": [],
                       "corners": {"FL": {"c_wheel": 1.2, "c_axis": 2.53, "motion_ratio": 0.689, "c_tyre": 0.105}}},
           "zero_modes_input_share": {p: 1e-9 for p in PATTERNS}, "n_modes": 53, "n_zero": 3,
           "static_check": {p: dict(sc, **{"largest_rel_err_0Hz": 0.001, "largest_rel_err_0.1Hz": 0.004}) for p in PATTERNS},
           "chosen_Hz": {p: chosen for p in PATTERNS}, "shape_keys": keys}
    (folder / "frf.json").write_text(json.dumps(res), encoding="utf-8")


class FrfDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name) / "lere_frf"
        fake_set(base / "A", "A", 7.2)
        fake_set(base / "REC", "REC", 8.4)
        cls.base = base
        cls.obj = nvh_frf.build(SimpleNamespace(dir=base, sets=["A", "REC"], labels=None))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_data_shape(self):
        o = self.obj
        self.assertEqual(o["model"], "frf")
        self.assertEqual(o["input_mm"], 1.0)
        self.assertEqual(set(o["patterns"]), set(PATTERNS))
        self.assertEqual(set(o["outputs"]), set(OUTPUTS))
        self.assertEqual([s["name"] for s in o["sets"]], ["A", "REC"])
        self.assertNotEqual(o["sets"][0]["offset_y"], o["sets"][1]["offset_y"])
        for s in o["sets"]:
            x = common.unpack(s["x"])
            self.assertEqual(len(common.unpack(s["curves"]["roll"]["seat"]["acc"])), len(x))
            nn = s["shell"]["n_nodes"]
            for k, sh in s["shapes"].items():
                self.assertEqual(len(common.unpack(sh["re"])), 3 * nn, k)
                self.assertEqual(len(common.unpack(sh["vm"])), s["shell"]["n_elements"], k)
            self.assertEqual(s["peaks"]["in_phase"]["seat"][0]["kind"], "ロールバー")
        # every set has a shape at every listed frequency (the union is the same for all sets here)
        for p, fl in o["keys"].items():
            for f in fl:
                self.assertIn(f"{p}@{f:g}", o["sets"][0]["shapes"])
        text = json.dumps(o["inputs"], ensure_ascii=False) + json.dumps(o["notes"], ensure_ascii=False)
        self.assertIn("確かめていない", text)
        self.assertIn("1 mm", text)

    def test_shape_encoding_by_hand(self):
        sh = self.obj["sets"][0]["shapes"]["in_phase@5"]
        re_ = np.asarray(common.unpack(sh["re"])).reshape(-1, 3)
        z = np.load(self.base / "A" / "frf_shapes.npz")
        d = z["disp"][[str(k) for k in z["keys"]].index("in_phase@5")]
        np.testing.assert_allclose(re_[:, 2], d.real[:, 2], atol=float(np.abs(d).max()) / 30000)

    def test_keys_the_page_reads(self):
        js = (VIEWER / "js" / "nvh.js").read_text(encoding="utf-8")
        used = set(re.findall(r"\bD\.([A-Za-z_]+)", js[js.index("HV.FRF_COL = "):]))
        self.assertTrue(used <= set(self.obj), used - set(self.obj))

    def test_kind_ja(self):
        self.assertEqual(nvh_frf.kind_ja("wheel hop (front, in phase)"), "ばね下の上下 (ホップ) (前, 同相)")
        self.assertEqual(nvh_frf.kind_ja("frame (coupled with the wheels)"), "フレーム (車輪と連成)")


class FrfPageTest(unittest.TestCase):
    """The page from disk in headless Chromium: no console errors, the two shells drawn, the plot's panel."""

    @classmethod
    def setUpClass(cls):
        cls.py = _system_python_with_playwright()
        if not cls.py:
            raise unittest.SkipTest("playwright is not available for the system Python (py)")
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name) / "lere_frf"
        fake_set(base / "A", "A", 7.2)
        fake_set(base / "REC", "REC", 8.4)
        cls.dir = Path(cls.tmp.name) / "viewer"
        shutil.copytree(VIEWER, cls.dir, ignore=shutil.ignore_patterns("data", "src", "__pycache__", "*.py"))
        common.write_data("nvh_frf", nvh_frf.build(SimpleNamespace(dir=base, sets=["A", "REC"], labels=None)), cls.dir / "data")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_frf_page(self):
        r = subprocess.run([self.py, str(Path(__file__).with_name("page_check.py")), str(self.dir), "nvh_frf", "", "5"],
                           capture_output=True, text=True, encoding="utf-8", timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        out = json.loads(r.stdout)
        # the NVH field's other variants (data/nvh.js, data/nvh_shell.js) are not in this test's viewer: the page
        # tries them first and the browser says so; nothing else may be said
        self.assertEqual([e for e in out["errors"] if "ERR_FILE_NOT_FOUND" not in e], [])
        self.assertLessEqual(len(out["errors"]), 2)
        self.assertEqual(out["shown_field"], "nvh")
        self.assertEqual(out["variant"], "nvh_frf")
        self.assertGreater(out["drawn_px"], 1000)
        self.assertEqual(out["over_view"], [])
        self.assertEqual(out["layout_overlap"], [])
        self.assertFalse(out["notes_open"])
        self.assertIn("Hz", out["tline"])
        self.assertIn("1 mm", out["hud"])
        self.assertIn("入力の並び", out["panel"])
        self.assertIn("確かめていない", out["inputs"])
        self.assertIn("von Mises", out["info"])
        self.assertNotRegex(out["page_text"], no_paths.ON_PAGE)


if __name__ == "__main__":
    unittest.main()
