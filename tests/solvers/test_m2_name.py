"""The constant-amplitude slope beyond the S-N knee is the design variable
fatigue_slope_after_knee_constant_amplitude (value 22) in every generator that carries it and in the
demo designs; the old name fatigue_slope_after_knee is gone."""

import json
import re
import unittest
from pathlib import Path

from hiraku.designgen import care_bed, ladder_4w, space_frame_utv

ROOT = Path(__file__).resolve().parents[2]
NEW = "fatigue_slope_after_knee_constant_amplitude"
OLD = re.compile(r"fatigue_slope_after_knee(?!_constant_amplitude)")


class M2NameTest(unittest.TestCase):
    def test_generators(self):
        for gen in (ladder_4w, space_frame_utv, care_bed):
            with self.subTest(gen=gen.__name__):
                self.assertEqual((gen.VARIABLES[NEW].unit, gen.VARIABLES[NEW].default), ("1", 22.0))
                self.assertNotIn("fatigue_slope_after_knee", gen.VARIABLES)

    def test_demo_designs(self):
        for p in sorted((ROOT / "data" / "designs").glob("*.json")):
            with self.subTest(design=p.name):
                v = json.loads(p.read_text(encoding="utf-8"))["variables"]
                # the assumed variables carry their basis in the design (aed92d35)
                self.assertEqual(v[NEW], {"value": 22.0, "unit": "1", "basis": "assumption"})
                self.assertNotIn("fatigue_slope_after_knee", v)

    def test_old_name_nowhere_in_the_code_and_the_demo_events(self):
        files = [*ROOT.glob("hiraku/**/*.py"), ROOT / "web" / "js" / "intake" / "demo-events.js",
                 *ROOT.glob("data/catalog/*.yaml")]
        left = [str(f.relative_to(ROOT)) for f in files if OLD.search(f.read_text(encoding="utf-8"))]
        # bed_use.py (a bed file another task is changing) still names it in two texts
        self.assertLessEqual(set(left), {str(Path("hiraku/solvers/shelf/bed_use.py"))})


if __name__ == "__main__":
    unittest.main()
