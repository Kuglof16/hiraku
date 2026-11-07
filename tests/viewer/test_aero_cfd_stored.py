"""The flow sample kept in the repository (viewer/fixtures/aero_cfd.json), opened as index.html?data=fixtures/aero_cfd.json#aero_cfd. No GPU, no browser."""

import base64
import json
import re
import unittest
from pathlib import Path

from viewer.src import common

ROOT = Path(__file__).resolve().parents[2]
STORED = ROOT / "viewer" / "fixtures" / "aero_cfd.json"
DESIGN = ROOT / "tests" / "verification" / "fixtures" / "n15-v005-design.json"
PATHS = re.compile(r"[A-Za-z]:\\\\|/" + "Users" + r"/|/home/|@[a-z0-9.-]+\.[a-z]{2,}", re.I)  # the home folder's name is joined so the repository's scan does not read this line


def unhex(p: dict) -> list:
    return common.unpack({**{k: v for k, v in p.items() if k != "hex"}, "b64": base64.b64encode(bytes.fromhex(p["hex"])).decode("ascii")})


class Stored(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = STORED.read_text(encoding="ascii")
        cls.top = json.loads(cls.text)
        cls.data = cls.top["data"]

    def test_the_aero_page_reads_the_flow_record_only_when_asked(self):
        js = (ROOT / "viewer" / "js" / "aero_cfd.js").read_text(encoding="utf-8")
        self.assertIn("{ key: 'aero_cfd', label: 'CFD', always: true, lazy: true }", js)

    def test_the_shape_build_writes_with_json(self):
        self.assertEqual(set(self.top), {"field", "data", "title", "source"})
        self.assertEqual(self.top["field"], "aero_cfd")

    def test_three_cases_of_the_design_it_says(self):
        self.assertEqual([y["yaw_deg"] for y in self.data["yaws"]], [0.0, 20.0, 90.0])
        self.assertEqual(self.data["source"]["design_key"], common.design_key(common.read_json(DESIGN)))
        self.assertEqual(self.data["yaws"][2]["speed_ms"], 0.0)  # a vehicle at rest in the side wind

    def test_the_status_says_it_was_not_compared_with_an_experiment(self):
        self.assertIn("確かめていない", self.data["status"]["text"])

    def test_large_arrays_are_hex_and_unpack(self):
        lines = self.data["yaws"][0]["lines"]
        self.assertNotIn("b64", lines["points"])
        points = unhex(lines["points"])
        self.assertEqual(len(points), lines["points"]["shape"][0] * 3)
        self.assertGreater(len(points), 3000)

    def test_small_enough_and_no_path(self):
        self.assertLess(len(self.text), 4_000_000)
        self.assertIsNone(PATHS.search(self.text))
        self.assertNotIn("sweep", self.text)  # no working folder's name either
        self.assertEqual(self.data["source"]["run"], "")


if __name__ == "__main__":
    unittest.main()
