import math
import unittest

from hiraku.intake import facts, knowledge, rulecheck
from tests.intake.designs import changed, mini_design, requirements


def by_id(targets):
    return {t["id"]: t for t in targets}


class FactsTest(unittest.TestCase):
    def setUp(self):
        self.design = mini_design()
        self.t = by_id(facts.targets(self.design, requirements(Q15=100, Q19=40, Q70="私有地だけ")))

    def test_targets_are_parts_then_the_frame_then_supports(self):
        self.assertEqual(list(self.t), ["battery", "seat", "floor", "cargo", "frame", "support:FL", "support:FR"])
        self.assertEqual([self.t[k]["type"] for k in self.t],
                         ["part", "part", "part", "part", "frame", "support", "support"])

    def test_box_facts(self):
        f = self.t["battery"]["facts"]
        self.assertEqual((f["mass_kg"], f["size_x_mm"], f["size_y_mm"], f["size_z_mm"]), (50.0, 500, 600, 200))
        self.assertEqual((f["size_min_mm"], f["thickness_mm"], f["size_max_mm"]), (200, 200, 600))
        self.assertEqual((f["bottom_z_mm"], f["top_z_mm"], f["center_z_mm"]), (230.0, 430.0, 330))
        self.assertEqual(self.t["floor"]["facts"]["thickness_mm"], 20)

    def test_member_facts(self):
        f = self.t["frame"]["facts"]
        self.assertEqual((f["wall_mm"], f["outer_mm"], f["member_count"], f["yield_MPa"]), (1.6, 80.0, 2, 235))
        self.assertEqual(f["member_length_max_mm"], 1000.0)
        self.assertAlmostEqual(f["section_ratio_max"], 80 / 2.3)  # per member: not 80 / 1.6
        self.assertNotIn("mass_kg", f)  # the frame is not a box

    def test_support_facts(self):
        total = 50 + 12 + 8 + 150
        fl, fr = self.t["support:FL"]["facts"], self.t["support:FR"]["facts"]
        self.assertAlmostEqual(fl["static_load_N"], total * 9.80665 / 2)
        self.assertEqual((fl["travel_mm"], fl["stiffness_N_per_mm"], fl["radius_mm"], fl["width_mm"]), (150.0, 20.0, 300, 200))
        self.assertNotIn("travel_mm", fr)  # a rigid support has no travel
        self.assertNotIn("width_mm", fr)

    def test_design_wide_facts_are_on_every_target(self):
        for t in self.t.values():
            self.assertEqual(t["facts"]["total_mass_kg"], 220.0)
            self.assertEqual(t["facts"]["var_battery_voltage"], 51.2)
            self.assertEqual(t["facts"]["req_Q15"], 100)
            self.assertNotIn("req_Q70", t["facts"])  # words are not numbers

    def test_the_vocabulary(self):
        names = {v["name"] for v in facts.vocabulary()}
        self.assertTrue({"mass_kg", "req_Qnn", "var_<名前>", "bend_radius_mm"} <= names)
        self.assertTrue(facts.is_known("req_Q15") and facts.is_known("var_track") and facts.is_known("mass_kg"))
        self.assertEqual(facts.unknown_names(["mass_kg", "req_15", "weight", "var_x"]), ["req_15", "weight"])


def rule(check, kinds=None, text="x"):
    r = {"rule_id": "T-01", "text": text, "check": check}
    if kinds:
        r["applies_to"] = {"kinds": kinds}
    return r


class CheckRuleTest(unittest.TestCase):
    def setUp(self):
        self.ts = facts.targets(mini_design(), requirements(Q15=100))

    def test_a_part_is_hit_when_the_answer_is_no(self):
        res = rulecheck.check_rule(rule("mass_kg <= 23", ["battery", "seat", "body"], "1 人で持てる重さ"), self.ts)
        self.assertEqual(res["targets"], ["battery", "seat", "floor"])
        self.assertEqual([h["part_id"] for h in res["hits"]], ["battery"])
        self.assertEqual(res["hits"][0]["values"], {"mass_kg": 50.0})
        self.assertIn("1 人で持てる重さ に合わない (mass_kg = 50)", res["hits"][0]["message"])
        self.assertFalse(res["pass"])

    def test_no_kinds_means_every_part_and_nothing_else(self):
        res = rulecheck.check_rule(rule("bottom_z_mm >= 0"), self.ts)
        self.assertEqual(res["targets"], ["battery", "seat", "floor", "cargo"])
        self.assertTrue(res["pass"])

    def test_frame_and_support_are_named_by_type(self):
        self.assertEqual(rulecheck.check_rule(rule("wall_mm >= 1.6", ["frame"]), self.ts)["targets"], ["frame"])
        self.assertEqual(rulecheck.check_rule(rule("radius_mm >= 100", ["support"]), self.ts)["targets"],
                         ["support:FL", "support:FR"])

    def test_missing_values_are_reported_not_judged(self):
        res = rulecheck.check_rule(rule("bend_radius_mm >= thickness_mm", ["body"]), self.ts)
        self.assertEqual(res["hits"], [])
        self.assertEqual(res["not_evaluable"], [{"part_id": "floor", "missing": ["bend_radius_mm"]}])
        res = rulecheck.check_rule(rule("travel_mm >= req_Q15", ["support"]), self.ts)
        self.assertEqual(res["not_evaluable"], [{"part_id": "support:FR", "missing": ["travel_mm"]}])  # rigid
        self.assertEqual(res["hits"], [])  # FL: 150 >= 100

    def test_the_hit_changes_with_the_design(self):
        light = facts.targets(changed(mini_design(), battery=20.0), [])
        res = rulecheck.check_rule(rule("mass_kg <= 23", ["battery"]), light)
        self.assertTrue(res["pass"])

    def test_support_load_against_spring(self):
        r = rule("static_load_N <= stiffness_N_per_mm * travel_mm", ["support"])
        res = rulecheck.check_rule(r, self.ts)
        self.assertEqual(res["hits"], [])  # 1079 N <= 20 * 150
        soft = mini_design()
        soft["supports"][0]["suspension"]["stiffness_N_per_mm"] = 5.0
        res = rulecheck.check_rule(r, facts.targets(soft, []))
        self.assertEqual([h["part_id"] for h in res["hits"]], ["support:FL"])
        self.assertTrue(math.isclose(res["hits"][0]["values"]["static_load_N"], 220 * 9.80665 / 2))


class CheckAllTest(unittest.TestCase):
    def test_every_builtin_rule_runs_on_a_design(self):
        rules = knowledge.all_rules(knowledge.load_builtin())
        checks = rulecheck.check_all(rules, mini_design(), requirements(Q15=100, Q19=40))
        self.assertEqual([c["rule_id"] for c in checks], [r["rule_id"] for r in rules])
        self.assertEqual([c["rule_check_id"] for c in checks][:2], ["rchk-0001", "rchk-0002"])
        by = {c["rule_id"]: c for c in checks}
        self.assertEqual([h["part_id"] for h in by["AS-01"]["hits"]], ["battery"])
        self.assertEqual([h["part_id"] for h in by["HU-02"]["hits"]], ["seat"])  # depth 500 > 406
        self.assertEqual(by["HU-01"]["hits"], [])
        self.assertEqual(by["MF-01"]["hits"], [])  # the thinnest member is 1.6, not below
        self.assertEqual(by["BT-02"]["hits"], [])  # 51.2 V
        self.assertEqual(by["WD-01"]["hits"], [])  # 80 / 2.3 = 34.8 and 40 / 1.6 = 25, both under 35
        self.assertTrue(by["SM-01"]["not_evaluable"])

    def test_the_section_ratio_is_worked_out_member_by_member(self):
        rules = [r for r in knowledge.all_rules(knowledge.load_builtin()) if r["rule_id"] == "WD-01"]
        thin = mini_design()
        thin["sections"]["rt"]["dims_mm"]["t"] = 2.0  # 80 / 2.0 = 40
        (c,) = rulecheck.check_all(rules, thin, [])
        self.assertEqual([h["part_id"] for h in c["hits"]], ["frame"])
        self.assertEqual(c["hits"][0]["values"], {"section_ratio_max": 40.0})

    def test_disabled_rules_and_id_factory(self):
        rules = knowledge.all_rules(knowledge.load_builtin())
        ids = iter(f"x-{i}" for i in range(100))
        checks = rulecheck.check_all(rules, mini_design(2), [], disabled={"AS-01", "SM-01"}, id_factory=lambda: next(ids))
        self.assertEqual(len(checks), len(rules) - 2)
        self.assertEqual(checks[0]["rule_check_id"], "x-0")
        self.assertEqual({c["design_version"] for c in checks}, {2})
        self.assertNotIn("AS-01", [c["rule_id"] for c in checks])

    def test_summary(self):
        rules = knowledge.all_rules(knowledge.load_builtin())
        checks = rulecheck.check_all(rules, mini_design(), requirements(Q15=100, Q19=40))
        s = rulecheck.summarize(checks)
        self.assertEqual(s["rules"], 18)
        self.assertEqual(s["hits"], sum(len(c["hits"]) for c in checks))
        line = rulecheck.summary_line(checks, rules)
        self.assertIn("18 規則", line)
        self.assertIn("AS-01", line)


if __name__ == "__main__":
    unittest.main()
