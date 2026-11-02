import unittest

from hiraku.agent.scripted import run_scripted
from hiraku.tools import ToolError
from hiraku.tools.report import build_report, recorded_numbers, unchecked, unknown_numbers
from tests.tools.helpers import RunCase


class NumberCheckTest(unittest.TestCase):
    def test_numbers_with_units_must_be_recorded(self):
        allowed = [(85.78, "MPa"), (163.33, "MPa"), (-20.7, "%"), (1220.0, "mm")]
        self.assertEqual(unknown_numbers("応力は 85.78 MPa で、限界 163.3 MPa に余裕がある", allowed), [])
        self.assertEqual(unknown_numbers("差は -20.7 % だった", allowed), [])
        self.assertEqual(unknown_numbers("差は 20.7 % だった", allowed), [])
        self.assertEqual(unknown_numbers("トレッドを 1220 mm にした", allowed), [])
        self.assertEqual(unknown_numbers("応力は 90 MPa くらい", allowed), ["90 MPa"])
        self.assertEqual(unknown_numbers("トレッドを 1220 MPa にした", allowed), ["1220 MPa"])  # the unit counts
        # not claims about the vehicle: ids, counts, versions
        self.assertEqual(unknown_numbers("P1 と R1 を 2 回、版 2 で cmp-0004 を見た", allowed), [])


class ReportTest(RunCase, unittest.TestCase):
    frame_factor = (1.5, 1.8, 1.03)  # R1: rail_step_bending mismatches, then the rail joint is judged safe side

    def setUp(self):
        super().setUp()
        self.assertEqual(run_scripted(self.ctx), "done")

    def test_parts_come_from_the_records(self):
        r = build_report(self.ctx, {"summary": "強度は合格", "next": []}, "lead")
        (strength,) = r["domains"]
        self.assertEqual((strength["domain"], strength["state"]), ("strength", "green"))
        (r1,) = strength["cases"]
        ver = self.ctx.store.records("verdict")[-1]
        self.assertEqual((r1["load_case_id"], r1["verdict_id"]), ("R1-step100", ver["verdict_id"]))
        self.assertTrue(r1["line"].startswith("R1 max_stress"))
        self.assertEqual(len(r1["differences"]), 2)  # rail_step_bending's mismatch, then the rail joint's
        self.assertTrue(all(d["cause"] for d in r1["differences"]))
        self.assertEqual(r["design_version"], 1)
        self.assertEqual(r["summary"], "強度は合格")

    def test_unchecked_values_are_listed(self):
        got = {u["what"].split()[0] for u in unchecked(self.ctx)}
        self.assertIn("Q61", got)  # safety factor: an assumption
        self.assertIn("material", got)  # the example design's steel is not verified

    def test_a_number_not_in_the_records_is_refused(self):
        with self.assertRaises(ToolError) as cm:
            build_report(self.ctx, {"summary": "応力は 999 MPa", "next": []}, "lead")
        self.assertIn("999 MPa", str(cm.exception))
        ver = self.ctx.store.records("verdict")[-1]
        ok = f"R1 は {ver['value']['value']:.4g} MPa で合格"
        self.assertEqual(build_report(self.ctx, {"summary": ok, "next": []}, "lead")["summary"], ok)
        with self.assertRaises(ToolError):
            build_report(self.ctx, {"summary": "x", "next": [{"domain": "crash", "change": "x", "reason": "y"}]}, "lead")

    def test_sensitivity_on_request(self):
        r = build_report(self.ctx, {"summary": "感度を見た", "next": [], "sensitivity": [
            {"load_case_id": "R1-step100", "paths": ["sections.rt40x80x2.dims_mm.t"]}]}, "lead")
        (s,) = r["sensitivity"]
        (term,) = s["terms"]
        self.assertEqual(s["quantity"], "max_stress")
        self.assertLess(term["share"], 0)  # a thicker rail wall lowers the stress
        self.assertIn((term["value"], "mm"), recorded_numbers(self.ctx, r["sensitivity"]))


if __name__ == "__main__":
    unittest.main()
