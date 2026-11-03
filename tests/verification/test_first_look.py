"""qa.first_look without the judge: sheets from a folder of pictures, and the report by position."""

import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from qa import first_look as fl


class FirstLook(unittest.TestCase):
    def test_sheets_and_report(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("needs Pillow")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            pics = tmp / "pics"
            pics.mkdir()
            for k in range(10):
                Image.new("RGB", (1920, 1080), (20 * k, 100, 150)).save(pics / f"s{k:02d}.png")
            items = fl.collect(Namespace(assembly=None, work=None, video=None, images=[str(pics)]), tmp / "shots")
            self.assertEqual([it["id"] for it in items], [f"s{k:02d}" for k in range(10)])
            made = fl.sheets(items, tmp / "sheets")
            self.assertEqual([len(part) for _, part in made], [8, 2])
            with Image.open(made[0][0]) as sheet:
                self.assertEqual(sheet.width, 4 * fl.TILE_W + 3 * 8)  # 4 tiles a row
            results = [dict(it, what="x", clear_3s=k % 6, fix="y") for k, it in enumerate(items)]
            fl.report(results, "test judge", tmp)
            md = (tmp / "first_look.md").read_text(encoding="utf-8")
            self.assertIn("判定: test judge", md)
            self.assertEqual(json.loads((tmp / "first_look.json").read_text(encoding="utf-8"))["judge"], "test judge")
            self.assertLess(md.index("| s00 |"), md.index("| s05 |"))  # the least clear come first

    def test_prompt_counts_the_pictures(self):
        self.assertIn("8 枚", fl.PROMPT.format(n=8))
        self.assertIn("8 個", fl.PROMPT.format(n=8))


if __name__ == "__main__":
    unittest.main()
