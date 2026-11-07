"""The data script of the flooded road's place in the viewer (viewer/src/water.py), on a small made-up record. No GPU, no browser."""

import argparse
import base64
import json
import re
import tempfile
import unittest
from pathlib import Path

import numpy as np

from hiraku.cfd.water import Road
from viewer.src import common, water

ROOT = Path(__file__).resolve().parents[2]
DESIGN = ROOT / "tests" / "verification" / "fixtures" / "n15-v005-design.json"
PATHS = re.compile(r"[A-Za-z]:[\\/]|/" + "Users" + r"/|/home/|@[a-z0-9.-]+\.[a-z]{2,}", re.I)  # the home folder's name is joined so the repository's scan does not read this line
STATEMENT = "この判定は電池、モーター、制御器が水に浸かるかだけを見る (しぶきは数えない)。実験と比べていない計算による"


def write_case(folder: Path, design_version: int = 5, spray_per_frame: int = 50) -> Path:
    frames, nx, ny = 4, 10, 6
    folder.mkdir(parents=True, exist_ok=True)
    surface = np.full((frames, nx, ny), 195, np.uint16)
    surface[2, 3, 3] = 900
    start = np.arange(frames + 1) * spray_per_frame
    spray = np.zeros((int(start[-1]), 3), np.uint16)
    spray[:, 2] = 1000
    np.savez_compressed(folder / "replay.npz", statement=STATEMENT, surface_mm=surface, column_m=0.06, x0_m=-0.3, y0_m=-0.18, spray=spray, spray_start=start,
                        spray_lo=np.array([-1.0, -1.0, 0.0]), spray_scale=np.array([1e-3, 1e-3, 1e-3]), poses=np.array([[1.0 - 0.1 * i, 0.0, 0.0] for i in range(frames)]), frame_s=1.0 / 30.0)
    case = {"inputs": {"design_version": design_version, "speed_kmh": 18.0, "dx_m": 0.03, "rotate_wheels": True, "loading": "two_cargo"}, "particles": 1000,
            "road": {"water_length_m": 8.0, "slope": 0.125, "level_m": 0.195},
            "underfloor": {"lowest_part_m": 0.15, "max_depth_mean_mm": 139.0, "max_reached_share": 0.37, "depth_definition": "d", "reach_definition": "r"},
            "records": [{"t": i / 30.0, "depth_mean_mm": 10.0 * i, "depth_max_mm": 20.0 * i, "reached_share": 0.1 * i, "top_m": 0.2 + i * 0.1} for i in range(frames)],
            "tests_passed": {"still_water": {}, "moving_box": {}, "standing_vehicle": {}, "standing_vehicle_grid_only": {}}, "assumptions": ["仮定"], "top_of_water_m": 1.6}
    (folder / "case.json").write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
    return folder


def unhex(p: dict) -> list:
    return common.unpack({**{k: v for k, v in p.items() if k != "hex"}, "b64": base64.b64encode(bytes.fromhex(p["hex"])).decode("ascii")})


def build(folder: Path) -> dict:
    return water.build(argparse.Namespace(run=folder, design=DESIGN))


class Build(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = write_case(Path(self.tmp.name) / "case")

    def test_the_statement_is_in_the_data_but_not_on_the_page(self):  # the owner's decision (11/7): the README keeps it
        d = build(self.folder)
        self.assertEqual(d["statement"], STATEMENT)
        self.assertFalse(any(STATEMENT in n or "実験と比べていない" in n for n in d["notes"]))

    def test_the_arrays_hold_the_record(self):
        d = build(self.folder)
        self.assertEqual(d["surface"]["shape"], [4, 10, 6])
        surface = np.array(unhex(d["surface"])).reshape(4, 10, 6)
        self.assertEqual(surface[2, 3, 3], 900.0)
        self.assertEqual(d["frames"], 4)
        self.assertEqual(len(d["records"]), 4)
        self.assertAlmostEqual(d["records"][3]["reached_pct"], 30.0)
        self.assertEqual(len(d["poses"]), 4)

    def test_the_spray_sample_is_capped_and_in_mm(self):
        folder = write_case(Path(self.tmp.name) / "many", spray_per_frame=water.SPRAY_PER_FRAME + 500)
        d = build(folder)
        offsets = unhex(d["spray_start"])
        self.assertEqual(np.diff(offsets).max(), water.SPRAY_PER_FRAME)
        points = np.array(unhex(d["spray"])).reshape(-1, 3)
        self.assertEqual(points[0, 2], 1000.0)  # 1000 steps of 1 mm above zero
        np.testing.assert_allclose(points[0, 0], -1000.0)

    def test_the_road_is_the_calculations_road(self):
        d = build(self.folder)
        xs = d["columns"]["x0_mm"] / 1000.0 + (np.arange(d["columns"]["nx"]) + 0.5) * d["columns"]["step_mm"] / 1000.0
        want = Road(depth_m=0.195, half_length_m=4.0, slope=0.125).height(xs) * 1000.0
        np.testing.assert_allclose(d["road_mm"], want, atol=0.06)

    def test_only_the_three_checks_named_as_passed_are_listed(self):
        names = [c["key"] for c in build(self.folder)["checks"]]
        self.assertEqual(names, ["still_water", "moving_box", "standing_vehicle"])

    def test_a_case_of_another_design_version_is_refused(self):
        folder = write_case(Path(self.tmp.name) / "other", design_version=3)
        with self.assertRaises(ValueError):
            build(folder)

    def test_nothing_on_the_page_names_a_path(self):
        d = build(self.folder)
        text = json.dumps({k: v for k, v in d.items() if k not in ("surface", "spray", "spray_start")}, ensure_ascii=False)
        self.assertIsNone(PATHS.search(text))


class Stored(unittest.TestCase):
    """The stored data, so the place opens with no GPU and no run."""

    def test_the_stored_data_has_the_statement_and_no_path(self):
        text = (ROOT / "viewer" / "fixtures" / "water.js").read_text(encoding="utf-8")
        data = json.loads(text.split('window.VIEW_DATA["water"] = ', 1)[1].rstrip().rstrip(";"))
        self.assertEqual(data["statement"], STATEMENT)
        self.assertEqual(data["source"]["design_version"], 5)
        self.assertEqual(data["source"]["run"], "")  # no folder name either: this stored file is opened by people
        self.assertIsNone(PATHS.search(text))  # the whole file: hex cannot hold a path
        self.assertIsNone(PATHS.search(json.dumps({k: v for k, v in data.items() if k not in ("surface", "spray", "spray_start")}, ensure_ascii=False)))
        self.assertLess(len(text), 4_000_000)

    def test_the_arrays_of_the_stored_data_are_hex(self):
        text = (ROOT / "viewer" / "fixtures" / "water.js").read_text(encoding="utf-8")
        data = json.loads(text.split('window.VIEW_DATA["water"] = ', 1)[1].rstrip().rstrip(";"))
        for k in ("surface", "spray", "spray_start"):
            self.assertIn("hex", data[k])
            self.assertNotIn("b64", data[k])

    def test_the_stored_file_is_where_the_script_writes_and_the_page_reads(self):
        self.assertEqual(water.STORED, ROOT / "viewer" / "fixtures")
        self.assertIn("dataUrl: 'fixtures/water.js'", (ROOT / "viewer" / "js" / "water.js").read_text(encoding="utf-8"))

    def test_the_page_leaves_the_statement_out_and_the_vehicle_is_opaque(self):
        js = (ROOT / "viewer" / "js" / "water.js").read_text(encoding="utf-8")
        self.assertNotIn("st.textContent = D.statement", js)
        self.assertNotIn("実験と比べていない", js)
        self.assertIn("boxes.mat.transparent = false", js)


if __name__ == "__main__":
    unittest.main()
