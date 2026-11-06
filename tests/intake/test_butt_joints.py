import unittest

from hiraku.intake import facts, knowledge, rulecheck

SECTIONS = {"rt": {"shape": "rect_tube", "dims_mm": {"b": 40, "h": 80, "t": 2.3}}, "rd": {"shape": "round_tube", "dims_mm": {"d": 40, "t": 2.3}}}


def design(gusset_at=(), weld_extent=None, cross_section="rt"):
    """Two rail pieces in line (A - B - C) and a cross member from B; a cross member L-corner at A's end."""
    nodes = [{"id": "A", "xyz_mm": [0, 0, 0]}, {"id": "B", "xyz_mm": [1000, 0, 0]}, {"id": "C", "xyz_mm": [2000, 0, 0]},
             {"id": "D", "xyz_mm": [1000, 800, 0]}]
    if weld_extent is not None:
        for n in nodes:
            n["weld_extent"] = weld_extent
    m = lambda i, a, b, s="rt": {"id": i, "node_a": a, "node_b": b, "section": s, "material": "s", "part": "frame"}
    return {"design_version": 1, "nodes": nodes, "sections": SECTIONS, "materials": {"s": {"yield_MPa": 245.0}}, "supports": [], "variables": {},
            "members": [m("rail1", "A", "B"), m("rail2", "B", "C"), m("cross", "B", "D", cross_section)],
            "parts": [{"id": f"g_{n}", "name": "g", "kind": "gusset", "box": {"center_mm": [0, 0, 0], "size_mm": [60, 60, 2]},
                       "mass_kg": 0.1, "carried_by": [n]} for n in gusset_at]}


def member_facts(d):
    return {t["id"]: t["facts"] for t in facts.targets(d) if t["kind"] == "frame"}


class ButtJointFactsTest(unittest.TestCase):
    def test_the_cross_member_is_butted_and_the_rail_that_goes_on_is_not(self):
        f = member_facts(design())
        self.assertEqual(f["cross"]["butt_end_count"], 1)  # its end at B meets the side of the rail; D has nothing else
        self.assertEqual((f["rail1"]["butt_end_count"], f["rail2"]["butt_end_count"]), (0, 0))  # the other rail goes on past B

    def test_a_gusset_carried_by_the_node_is_counted(self):
        self.assertEqual(member_facts(design())["cross"]["butt_end_ungusseted"], 1)
        self.assertEqual(member_facts(design(gusset_at=["B"]))["cross"]["butt_end_ungusseted"], 0)

    def test_the_weld_is_known_only_when_the_nodes_say_it(self):
        self.assertNotIn("butt_end_partial_weld", member_facts(design())["cross"])
        self.assertEqual(member_facts(design(weld_extent="all_round"))["cross"]["butt_end_partial_weld"], 0)
        self.assertEqual(member_facts(design(weld_extent="partial"))["cross"]["butt_end_partial_weld"], 1)

    def test_a_round_tube_is_left_alone(self):
        f = member_facts(design(cross_section="rd"))
        self.assertEqual(f["cross"]["butt_end_count"], 0)

    def test_an_l_corner_counts_both_ends(self):
        d = design()
        d["nodes"].append({"id": "E", "xyz_mm": [0, 800, 0]})
        d["members"].append({"id": "corner", "node_a": "A", "node_b": "E", "section": "rt", "material": "s", "part": "frame"})
        f = member_facts(d)
        self.assertEqual((f["rail1"]["butt_end_count"], f["corner"]["butt_end_count"]), (1, 1))


class ButtJointRulesTest(unittest.TestCase):
    def rule(self, rule_id):
        return next(r for r in knowledge.all_rules(knowledge.load_builtin()) if r["rule_id"] == rule_id)

    def result(self, rule_id, d):
        return rulecheck.check_rule(self.rule(rule_id), facts.targets(d))

    def test_the_gusset_rule_hits_the_cross_member_without_one_and_passes_with_one(self):
        hit = self.result("WD-05", design())
        self.assertEqual([h["part_id"] for h in hit["hits"]], ["cross"])
        self.assertEqual(self.result("WD-05", design(gusset_at=["B"]))["hits"], [])

    def test_the_weld_rule_is_not_evaluable_until_the_design_says_how_it_is_welded(self):
        got = self.result("WD-04", design())
        self.assertEqual(got["hits"], [])
        self.assertTrue(got["not_evaluable"])
        self.assertEqual(self.result("WD-04", design(weld_extent="all_round"))["hits"], [])
        self.assertEqual([h["part_id"] for h in self.result("WD-04", design(weld_extent="partial"))["hits"]], ["cross"])

    def test_the_basis_says_it_is_the_authors_experience_and_names_no_one(self):
        for rid in ("WD-04", "WD-05"):
            basis = self.rule(rid)["basis"]
            self.assertIn("practical experience", basis)  # the basis the author asked for
            self.assertEqual(self.rule(rid)["trust"], 3)


if __name__ == "__main__":
    unittest.main()
