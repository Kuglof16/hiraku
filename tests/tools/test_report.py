import json
import unittest

from hiraku import contract
from hiraku.agent.scripted import run_scripted
from hiraku.contract import layout
from hiraku.tools import ToolError
from hiraku.tools.functions import write_report
from hiraku.tools.report import build_report, unchecked, unknown_numbers
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


class VerdictLineTest(unittest.TestCase):
    def test_the_sign_is_the_relation_that_holds(self):
        from hiraku.tools.judge import verdict_line

        v = {"test_id": "S5", "quantity": "tip_slope_angle", "value": {"value": 41.85, "unit": "deg"}, "op": ">",
             "limit": {"value": 43.31, "unit": "deg"}, "pass": False, "margin_pct": -3.37}
        self.assertEqual(verdict_line(v), "S5 tip_slope_angle 41.85 deg ≤ 43.31 deg (基準 > 43.31 deg に届かない、余裕 -3 %)")
        ok = {**v, "value": {"value": 50.0, "unit": "deg"}, "pass": True, "margin_pct": 15.4}
        self.assertEqual(verdict_line(ok), "S5 tip_slope_angle 50 deg > 43.31 deg (余裕 15 %)")


class PersonsChoiceTest(RunCase, unittest.TestCase):
    """Run 17: only AS-01 was asked (ask_user rule_hits) and answered; the report must not say the person chose
    for HU-02 or U1-04 too."""

    def test_only_answered_rules_may_carry_the_persons_choice(self):
        from unittest import mock

        from hiraku.tools import report

        with mock.patch.object(report, "answered_rules", return_value={"AS-01"}):
            ok = ("規則の当たり HU-02 (seat_R・seat_L)、AS-01 と U1-04 (battery) は、設計の変数が無いため直せていません "
                  "(AS-01 は ask_user で人が「設計を直す」を選択)。")
            self.assertEqual(report.unanswered_choices(self.ctx, [ok]), [])
            wide = "HU-02・AS-01・U1-04 は、人が「設計を直す」を選びました。"
            self.assertEqual(report.unanswered_choices(self.ctx, [wide]), ["HU-02", "U1-04"])
            self.assertEqual(report.unanswered_choices(self.ctx, ["規則U1-04は人の判断で外した"]), ["U1-04"])
            self.assertEqual(report.unanswered_choices(self.ctx, ["U1-04 はまだ聞いていない"]), [])

    def test_write_report_refuses_the_claim(self):
        from unittest import mock

        from hiraku.tools import report
        from hiraku.tools.functions import write_report

        with mock.patch.object(report, "answered_rules", return_value=set()):
            with self.assertRaisesRegex(ToolError, "HU-02"):
                write_report(self.ctx, {"summary": "HU-02 は人が設計を直すを選んだ", "next": []}, "lead")


class ReportTest(RunCase, unittest.TestCase):
    frame_factor = (1.5, 1.8, 1.03)  # R1: rail_step_bending mismatches, then the rail joint is judged safe side

    def setUp(self):
        super().setUp()
        self.assertEqual(run_scripted(self.ctx), "done")

    def test_parts_come_from_the_records(self):
        r = build_report(self.ctx, {"summary": "強度は合格", "next": []}, "lead")
        self.assertEqual(contract.errors("report", r), [])
        (strength,) = r["domains"]
        self.assertEqual((strength["domain"], strength["state"]), ("strength", "green"))
        ver = self.ctx.store.records("verdict")[-1]
        self.assertEqual(strength["verdict_ids"], [ver["verdict_id"]])
        self.assertTrue(strength["line"].startswith("R1 max_stress"))
        self.assertEqual(len(strength["differences"]), 2)  # rail_step_bending's mismatch, then the rail joint's
        self.assertTrue(all(d["cause_id"] for d in strength["differences"]))
        self.assertEqual(r["design_version"], 1)
        self.assertEqual(r["summary"], "強度は合格")

    def test_write_report_is_the_leads_and_is_recorded(self):
        with self.assertRaisesRegex(ToolError, "まとめ役"):
            write_report(self.ctx, {"summary": "x", "next": []}, "cae.strength")
        with self.assertRaisesRegex(ToolError, "入力の形"):
            write_report(self.ctx, {"summary": "x"}, "lead")
        rep = write_report(self.ctx, {"summary": "強度は合格", "next": [
            {"domain": "strength", "change": "レールの肉厚", "reason": "横の曲げとねじりの分が大きい"}]}, "lead")
        stored = json.loads((self.ctx.store.path / layout.REPORT_FILE).read_text(encoding="utf-8"))
        self.assertEqual(stored, rep)
        last = self.ctx.store.events()[-1]
        self.assertEqual((last["kind"], last["agent"], last["data"]), ("report.written", "lead", rep))
        self.assert_events_fit()

    def test_issues_are_kept_as_the_lead_wrote_them(self):
        issues = [{"kind": "missing_tool", "what": "横転の保護枠を当てるツールが棚に無い", "where": "R4-rops",
                   "workaround": "make_tool で作った"},
                  {"kind": "unexplained_mismatch", "what": "差の原因を言えない", "record_ids": ["cmp-0001"]}]
        rep = write_report(self.ctx, {"summary": "強度は合格", "next": [], "issues": issues}, "lead")
        self.assertEqual(rep["issues"], issues)
        self.assertNotIn("issues", write_report(self.ctx, {"summary": "強度は合格", "next": []}, "lead"))
        with self.assertRaisesRegex(ToolError, "入力の形"):
            write_report(self.ctx, {"summary": "x", "next": [], "issues": [{"kind": "complaint", "what": "y"}]}, "lead")
        self.assert_events_fit()

    def test_unchecked_values_are_listed(self):
        got = {u["what"].split()[0] for u in unchecked(self.ctx)}
        items = {u["item"].split()[0] for u in build_report(self.ctx, {"summary": "x", "next": []}, "lead")["unchecked"]}
        self.assertEqual(items, got)
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

    def add_rule_check(self, rule_id, hit):
        s, v = self.ctx.store, self.ctx.version
        hits = [{"part_id": "seat", "values": {"depth_mm": 380.0}, "message": "座面の奥行きが足りない"}] if hit else []
        return s.add_record("rule_check", {"rule_check_id": s.next_id("rule_check"), "design_version": v,
                                           "rule_id": rule_id, "targets": ["seat"], "hits": hits, "pass": not hit},
                            design_version=v, agent="packager")

    def test_a_next_item_answering_a_rule_hit_may_name_a_domain_not_picked(self):
        item = {"domain": "assembly", "change": "座面を深くする", "reason": "HU-02 に当たった", "rule_id": "HU-02"}
        self.assertNotIn("assembly", self.ctx.picked)
        with self.assertRaisesRegex(ToolError, "rule_check で当たった規則ではありません"):
            build_report(self.ctx, {"summary": "x", "next": [item]}, "lead")  # no rule_check yet
        self.add_rule_check("HU-02", hit=True)
        r = build_report(self.ctx, {"summary": "x", "next": [item]}, "lead")
        self.assertEqual(r["next"], [item])  # the rule_id is kept
        self.assertEqual(contract.errors("report", r), [])
        with self.assertRaisesRegex(ToolError, "rule_id を付けて"):  # without the rule_id, still refused
            build_report(self.ctx, {"summary": "x", "next": [{k: item[k] for k in ("domain", "change", "reason")}]},
                         "lead")
        self.add_rule_check("HU-02", hit=False)  # the latest rule_check of HU-02 passes: no hit to answer
        with self.assertRaisesRegex(ToolError, "HU-02"):
            build_report(self.ctx, {"summary": "x", "next": [item]}, "lead")

    def test_the_scripted_runs_summary_is_for_the_reader(self):
        rep = json.loads((self.ctx.store.path / layout.REPORT_FILE).read_text(encoding="utf-8"))
        self.assertEqual(rep["summary"], "台本の運転で、設計の版 1 は選んだ 1 分野 (強度信頼性) がすべて合格")

    def test_the_scripted_run_fills_next_by_code(self):
        rep = json.loads((self.ctx.store.path / layout.REPORT_FILE).read_text(encoding="utf-8"))
        self.assertEqual(contract.errors("report", rep), [])
        (item,) = rep["next"]  # one picked domain (strength), its one verdict
        ver = self.ctx.store.records("verdict")[-1]
        ana = self.ctx.store.get("analysis", self.ctx.store.get("comparison", ver["comparison_id"])["analysis_id"])
        self.assertEqual(item["domain"], "strength")
        self.assertTrue(item["change"].startswith("台本: "))
        # the stand-in frame tool's stress falls with a thicker or taller rail: make it bigger
        self.assertRegex(item["change"], r"^台本: sections\.[^ ]+\.dims_mm\.[th] を大きくする$")
        for part in (ver["verdict_id"], ana["analysis_id"], "R1 max_stress", "K5", "弾性"):
            self.assertIn(part, item["reason"])
        sens = rep["domains"][0]["sensitivity"]
        path = item["change"].removeprefix("台本: ").split(" ", 1)[0]
        self.assertTrue(any(t["name"].endswith(path) and t["share"] < 0 for t in sens))  # the K5 terms are kept

    def test_governing_verdicts_and_the_effects_fallback(self):
        from unittest import mock

        from hiraku.tools import report

        self.assertEqual([v["load_case_id"] for v in report.governing_verdicts(self.ctx, "strength")], ["R1-step100"])
        self.assertEqual(report.governing_verdicts(self.ctx, "handling"), [])
        ver = self.ctx.store.records("verdict")[-1]
        ana = self.ctx.store.get("analysis", self.ctx.store.get("comparison", ver["comparison_id"])["analysis_id"])
        heavy = dict(ana, wall_time_s=999.0,  # a measured time decides nothing
                     effects=[{"name": "bending (rail_L0)", "value": 10.0, "unit": "MPa", "share": 0.75}])
        self.ctx.specs[ana["tool"]] = dict(self.ctx.specs[ana["tool"]], stage=report.K5_MAX_STAGE)  # a heavy tool
        real_get = self.ctx.store.get
        with mock.patch.object(self.ctx.store, "get",
                               lambda kind, i: heavy if kind == "analysis" and i == ana["analysis_id"] else real_get(kind, i)), \
                mock.patch.object(report, "sensitivities", side_effect=AssertionError("no K5 for a heavy tool")):
            (item,), computed = report.scripted_next(self.ctx)
        self.assertEqual(computed, [])
        self.assertEqual(item["change"], "台本: rail_L0 が効いている (向きは出していない)")
        self.assertIn("75 %", item["reason"])
        self.assertIn("重い", item["reason"])

    def test_design_coordinates_are_not_citable(self):
        coords = {abs(x) for n in self.ctx.store.design()["nodes"] for x in n["xyz_mm"]}
        self.assertIn(400.0, coords)  # a node coordinate of the example design, and no scalar of the records
        with self.assertRaises(ToolError) as cm:
            build_report(self.ctx, {"summary": "レールの節は 400 mm にある", "next": []}, "lead")
        self.assertIn("400 mm", str(cm.exception))

    def test_sensitivity_uses_the_verdicts_analysis_and_the_trusted_upstream(self):
        from unittest import mock

        from hiraku.tools.functions import trusted_analysis

        s, v = self.ctx.store, self.ctx.version
        ver = [x for x in s.records("verdict", v) if x["load_case_id"] == "R1-step100"][-1]
        behind = s.get("analysis", s.get("comparison", ver["comparison_id"])["analysis_id"])
        trusted_p1 = trusted_analysis(self.ctx, "P1-step100")
        # newer ok analyses that are not behind the verdict / not matched
        for lc_id, tool in (("R1-step100", "step_climb"), ("P1-step100", "step_climb")):
            last = [a for a in s.records("analysis", v) if a["load_case_id"] == lc_id][-1]
            s.add_record("analysis", dict(last, analysis_id=s.next_id("analysis"), tool=tool), design_version=v,
                         agent="cae.strength", domain="strength")
        seen = {}

        def fake(tool, design, lc, quantity, paths, params):
            seen.update(tool=tool, upstream=params["upstream_analyses"])
            return []

        with mock.patch("hiraku.solvers.k5_sensitivity.sensitivity", fake):
            build_report(self.ctx, {"summary": "x", "next": [], "sensitivity": [
                {"load_case_id": "R1-step100", "paths": ["sections.rt40x80x2.dims_mm.t"]}]}, "lead")
        self.assertIs(seen["tool"], self.ctx.tools[behind["tool"]])
        self.assertEqual({k: a["analysis_id"] for k, a in seen["upstream"].items()},
                         {"P1-step100": trusted_p1["analysis_id"]})

    def test_sensitivity_on_request(self):
        r = build_report(self.ctx, {"summary": "感度を見た", "next": [], "sensitivity": [
            {"load_case_id": "R1-step100", "paths": ["sections.rt40x80x2.dims_mm.t"]}]}, "lead")
        (term,) = r["domains"][0]["sensitivity"]
        self.assertTrue(term["name"].startswith("max_stress to sections.rt40x80x2.dims_mm.t"))
        self.assertLess(term["share"], 0)  # a thicker rail wall lowers the stress
        self.assertEqual(contract.errors("report", r), [])


if __name__ == "__main__":
    unittest.main()
