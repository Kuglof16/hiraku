"""Standard tube sizes (data/sections/standard_tubes.json) and the designgen kit's pick."""

import json
import unittest
from pathlib import Path

from hiraku import contract
from hiraku.designgen.kit import sections as kit

DATA = Path(__file__).resolve().parents[2] / "data" / "sections" / "standard_tubes.json"


class StandardSizes(unittest.TestCase):
    def test_the_lists_say_where_they_come_from(self):
        data = json.loads(DATA.read_text(encoding="utf-8"))
        self.assertEqual(sorted(data), ["rect_tube", "round_tube"])
        for shape, block in data.items():
            self.assertIn("http", block["source"], shape)
            self.assertEqual(block["count"], len(block["sizes"]), shape)
            if not block["verified"]:
                self.assertIn("Not checked", block["note"], shape)
            sizes = [tuple(s) for s in block["sizes"]]
            self.assertEqual(len(sizes), len(set(sizes)), shape)
        rect = data["rect_tube"]["sizes"]
        self.assertTrue(all(a >= b > 2 * t > 0 for a, b, t in rect))
        self.assertIn([40, 40, 2.3], rect)  # the v1 cross members' size, and its rails' 80 x 40 x 2.3
        self.assertIn([80, 40, 2.3], rect)
        self.assertTrue(all(d > 2 * t > 0 for d, t in data["round_tube"]["sizes"]))

    def test_every_printed_unit_mass_follows_the_standards_formula(self):
        # the tables print W rounded to 3 significant figures (JIS Z 8401, rounding to the even neighbour on a tie);
        # recomputing every row with the standard's own formula and rounding the same way gives the printed value
        from decimal import ROUND_HALF_EVEN, Decimal

        def sig3(x: float) -> float:
            d = Decimal(repr(x))
            return float(d.quantize(Decimal(1).scaleb(d.adjusted() - 2), rounding=ROUND_HALF_EVEN))

        data = json.loads(DATA.read_text(encoding="utf-8"))
        formulas = {"rect_tube": lambda a, b, t: 0.0157 * t * (a + b - 3.287 * t),  # JIS G 3466 (STKR)
                    "round_tube": lambda d, t: 0.02466 * t * (d - t)}              # JIS G 3444 (STK)
        for shape, w in formulas.items():
            block = data[shape]
            self.assertEqual(len(block["unit_mass_kg_m"]), len(block["sizes"]), shape)
            bad = [(s, printed, sig3(w(*s))) for s, printed in zip(block["sizes"], block["unit_mass_kg_m"])
                   if abs(sig3(w(*s)) - printed) > 1e-9 * printed]
            self.assertEqual(bad, [], shape)

    def test_standard_records_fit_the_contract(self):
        for shape in ("rect_tube", "round_tube"):
            recs = kit.standard(shape)
            self.assertGreater(len(recs), 100)
            for key, sec in recs[:5] + recs[-5:]:
                self.assertEqual(contract.errors("section", sec), [], key)
                self.assertIn("JIS G", sec["source"])
        b, h = kit.standard("rect_tube")[-1][1]["dims_mm"]["b"], kit.standard("rect_tube")[-1][1]["dims_mm"]["h"]
        self.assertLessEqual(b, h)

    def test_pick_the_lightest_that_fits(self):
        key, sec = kit.pick("rect_tube", b=40, h=80, t_min=2.0)
        self.assertEqual(key, "rt40x80x2.3")
        key, sec = kit.pick("rect_tube", b=80, h=40)  # the same tube turned
        self.assertEqual((sec["dims_mm"]["b"], sec["dims_mm"]["h"]), (80.0, 40.0))
        self.assertEqual(sec["dims_mm"]["t"], 1.6)
        # by stiffness: the lightest standard size with at least that Iz, and nothing lighter fits
        need = {"Iz_mm4": 5.0e5}
        key, sec = kit.pick("round_tube", need=need)
        got = kit.table({key: sec})[key]
        self.assertGreaterEqual(got["Iz_mm4"], need["Iz_mm4"])
        lighter = [k for k, s in kit.standard("round_tube")
                   if kit.table({k: s})[k]["A_mm2"] < got["A_mm2"] and kit.table({k: s})[k]["Iz_mm4"] >= need["Iz_mm4"]]
        self.assertEqual(lighter, [])
        key, sec = kit.pick("rect_tube", need={"Iz_mm4": 1.0e6, "Iy_mm4": 2.0e5})
        got = kit.table({key: sec})[key]
        self.assertGreaterEqual(got["Iz_mm4"], 1.0e6)
        self.assertGreaterEqual(got["Iy_mm4"], 2.0e5)
        with self.assertRaisesRegex(ValueError, "no standard rect_tube"):
            kit.pick("rect_tube", b=41, h=80)
        with self.assertRaisesRegex(KeyError, "no standard sizes"):
            kit.standard("i_beam")


if __name__ == "__main__":
    unittest.main()
