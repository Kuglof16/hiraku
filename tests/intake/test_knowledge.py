import unittest
from pathlib import Path

from hiraku.catalog import REPO_ROOT, read_data
from hiraku.intake import IntakeError, expression, facts, knowledge, rulecheck
from hiraku.intake.knowledge import (CATEGORIES, convert_sentence, doc_id_for, import_text, load_builtin, make_rule,
                                     parse_candidates)
from hiraku.intake.products import load_product
from tests.intake.designs import mini_design

FIXTURES = Path(__file__).with_name("fixtures")


def read(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class BuiltinTest(unittest.TestCase):
    def setUp(self):
        self.docs = load_builtin()
        self.rules = knowledge.all_rules(self.docs)

    def test_nine_categories_one_document_each(self):
        self.assertEqual([r["rules"][0]["category"] for r in self.docs], list(CATEGORIES))
        self.assertEqual(len(self.rules), 18)
        self.assertEqual(len({r["rule_id"] for r in self.rules}), 18)
        for rec in self.docs:
            self.assertEqual(rec["doc"]["origin"], "builtin")

    def test_every_check_is_valid_and_uses_known_names(self):
        for r in self.rules:
            with self.subTest(rule=r["rule_id"]):
                self.assertEqual(facts.unknown_names(expression.names(r["check"])), [])
                self.assertEqual(r["origin"], "builtin")
                self.assertNotIn("check_by", r)  # the formula came with the data: nobody turned a sentence into it

    def test_a_source_is_written_and_unchecked_ones_say_so(self):
        for r in self.rules:
            with self.subTest(rule=r["rule_id"]):
                self.assertIn(r["trust"], (1, 2, 3))
                self.assertTrue(r["basis"])
                if r["trust"] in (1, 2):
                    self.assertIn("not checked", r["basis"])
                else:
                    self.assertIn("assumption", r["basis"])

    def test_make_rule_refuses_bad_input(self):
        ok = dict(origin="dropped", check_by="claude", trust=3, basis="b")
        for kwargs, word in [
            (dict(category="paint", text="x", check="mass_kg > 1"), "paint"),
            (dict(category="human", text="x", check="weight > 1"), "weight"),
            (dict(category="human", text="x", check="mass_kg +"), "読めません"),
            (dict(category="human", text="x", check="mass_kg"), "はい / いいえ"),
            (dict(category="human", text=" ", check="mass_kg > 1"), "空"),
        ]:
            with self.subTest(word=word):
                with self.assertRaises(IntakeError) as cm:
                    make_rule("U1-01", "U1", **kwargs, **ok)
                self.assertIn(word, str(cm.exception))


class SentenceTest(unittest.TestCase):
    def test_the_sentence_from_the_storyboard(self):
        self.assertEqual(convert_sentence("曲げの内側の半径は板厚以上"), "bend_radius_mm >= thickness_mm")

    def test_with_a_multiplier(self):
        self.assertEqual(convert_sentence("穴の中心から板の縁までの距離は板厚の 2 倍以上"), "hole_edge_mm >= 2 * thickness_mm")
        self.assertEqual(convert_sentence("溶接の長さはのど厚の 6 倍以上"), "weld_length_mm >= 6 * weld_throat_mm")

    def test_with_a_number_and_a_unit(self):
        self.assertEqual(convert_sentence("1 人で持ち上げる部品の質量は 23 kg 以下"), "mass_kg <= 23")
        self.assertEqual(convert_sentence("板厚は 1.6mm 以上。"), "thickness_mm >= 1.6")
        self.assertEqual(convert_sentence("板厚は 0.5 cm 以上"), "thickness_mm >= 5")
        self.assertEqual(convert_sentence("質量は 2000 g 以下"), "mass_kg <= 2")
        self.assertEqual(convert_sentence("質量は ２３ kg 以下"), "mass_kg <= 23")  # full-width digits
        self.assertEqual(convert_sentence("閉じる隙間は 25 mm 以上"), "closing_gap_mm >= 25")  # not 隙間
        self.assertEqual(convert_sentence("サスのストロークは 100 mm より大きい"), "travel_mm > 100")

    def test_what_the_patterns_do_not_understand_is_left_alone(self):
        for s in ("板金の部品は、傷が見えない向きに置く", "質量は 23 lb 以下", "質量は板厚以上", "寸法は図面による",
                  "ボルトは M8 以上"):
            with self.subTest(s=s):
                self.assertIsNone(convert_sentence(s))


class KindsTest(unittest.TestCase):
    def test_a_converted_sentence_is_about_the_part_it_names(self):
        k = knowledge.kinds_for_sentence
        self.assertEqual(k("電池の質量は 30 kg 以下", "mass_kg <= 30"), ["battery"])
        self.assertEqual(k("座面の厚みは 20 mm 以上", "thickness_mm >= 20"), ["seat"])
        self.assertIsNone(k("質量は 23 kg 以下", "mass_kg <= 23"))  # every part

    def test_a_fact_of_a_support_or_a_frame_is_not_a_parts(self):
        k = knowledge.kinds_for_sentence
        self.assertEqual(k("サスのストロークは 100 mm 以上", "travel_mm >= 100"), ["support"])
        self.assertEqual(k("肉厚は 1.6 mm 以上", "wall_mm >= 1.6"), ["frame"])

    def test_sheet_metal_talk_is_about_the_plates(self):
        k = knowledge.kinds_for_sentence
        self.assertEqual(k("曲げの内側の半径は板厚以上", "bend_radius_mm >= thickness_mm"), ["body"])
        self.assertEqual(k("穴の中心から板の縁までの距離は板厚の 2 倍以上", "hole_edge_mm >= 2 * thickness_mm"), ["body"])
        self.assertEqual(k("座面の曲げ半径は 5 mm 以上", "bend_radius_mm >= 5"), ["seat"])  # a named part comes first

    def test_import_uses_it(self):
        got = import_text("a.md", "- 電池の質量は 30 kg 以下\n- 質量は 200 kg 以下\n", doc_id="U1")
        by = {r["rule_id"]: r for r in got.record["rules"]}
        self.assertEqual(by["U1-01"]["applies_to"], {"kinds": ["battery"]})
        self.assertNotIn("applies_to", by["U1-02"])
        rules = got.record["rules"]
        hits = rulecheck.check_all(rules, mini_design(), [])
        self.assertEqual([h["part_id"] for h in hits[0]["hits"]], ["battery"])  # the cargo is not a battery
        self.assertEqual([h["part_id"] for h in hits[1]["hits"]], [])


class CandidatesTest(unittest.TestCase):
    def test_markdown(self):
        c = parse_candidates("bending-notes.md", read("bending-notes.md"))
        texts = [x.text for x in c]
        self.assertEqual(texts[:3], ["曲げの内側の半径は板厚以上", "穴の中心から板の縁までの距離は板厚の 2 倍以上", "板金の部品は、傷が見えない向きに置く"])
        self.assertEqual([x.cand_id for x in c], [f"c{i}" for i in range(1, len(c) + 1)])
        self.assertNotIn("この文書は社内向けのメモです。", texts)  # a plain paragraph, the file has bullets
        self.assertEqual(c[0].category, "sheet_metal")  # from the heading 板金
        by = {x.text: x for x in c}
        self.assertEqual(by["電池の質量は 30 kg 以下"].category, "battery")  # numbered line under 電池
        self.assertEqual(by["電池は乗員の足元から離す"].category, "battery")
        t = by["座面の幅は 400 mm 以上"]
        self.assertEqual((t.check, t.category, t.kinds), ("size_y_mm >= 400", "human", ["seat"]))

    def test_csv(self):
        c = parse_candidates("rules.csv", read("rules.csv"))
        self.assertEqual([(x.text, x.check, x.category) for x in c],
                         [("座面の幅は 400 mm 以上", "size_y_mm >= 400", "human"), ("電池は 30 kg 以下にする", None, "battery")])

    def test_plain_text_takes_every_line(self):
        c = parse_candidates("notes.txt", "質量は 23 kg 以下\n\nはい\n板厚は 1.6 mm 以上\n")
        self.assertEqual([x.text for x in c], ["質量は 23 kg 以下", "板厚は 1.6 mm 以上"])  # 'はい' is too short to be a rule

    def test_code_fences_are_skipped(self):
        c = parse_candidates("a.md", "- 質量は 23 kg 以下\n```\n- これは例\n```\n")
        self.assertEqual([x.text for x in c], ["質量は 23 kg 以下"])


class ImportTest(unittest.TestCase):
    def setUp(self):
        self.text = read("bending-notes.md")

    def test_the_first_call_converts_what_it_can_and_hands_back_the_rest(self):
        got = import_text("bending-notes.md", self.text, doc_id="U1")
        by = {r["rule_id"]: r for r in got.record["rules"]}
        self.assertEqual(sorted(by), ["U1-01", "U1-02", "U1-04", "U1-06", "U1-07"])
        self.assertEqual(by["U1-01"]["check"], "bend_radius_mm >= thickness_mm")
        self.assertEqual((by["U1-01"]["check_by"], by["U1-01"]["category"], by["U1-01"]["origin"]), ("code", "sheet_metal", "dropped"))
        self.assertNotIn("check_by", by["U1-06"])  # the table brought its formula
        self.assertEqual(by["U1-06"]["applies_to"], {"kinds": ["seat"]})
        self.assertEqual([n["cand_id"] for n in got.needs_formula], ["c3", "c5"])
        self.assertEqual(got.needs_formula[0]["category"], "sheet_metal")
        self.assertEqual(got.record["doc"], {"doc_id": "U1", "filename": "bending-notes.md", "origin": "dropped"})
        for r in got.record["rules"]:
            self.assertEqual(r["trust"], 3)
            self.assertIn("bending-notes.md", r["basis"])

    def test_claudes_formulas_complete_it_and_keep_the_ids(self):
        first = import_text("bending-notes.md", self.text, doc_id="U1")
        second = import_text("bending-notes.md", self.text, doc_id="U1", formulas={
            "c5": {"check": "bottom_z_mm >= 100", "kinds": ["battery"]},
        }, skip={"c3": "表面の向きは式にできない"})
        self.assertEqual(second.needs_formula, [])
        ids1 = {r["rule_id"] for r in first.record["rules"]}
        ids2 = {r["rule_id"]: r for r in second.record["rules"]}
        self.assertTrue(ids1 < set(ids2))
        self.assertEqual(sorted(set(ids2) - ids1), ["U1-05"])
        self.assertEqual(ids2["U1-05"]["check_by"], "claude")
        self.assertEqual(ids2["U1-05"]["applies_to"], {"kinds": ["battery"]})
        self.assertEqual(second.record["doc"]["skipped"], [{"text": "板金の部品は、傷が見えない向きに置く", "reason": "表面の向きは式にできない"}])

    def test_a_bad_formula_stays_open_with_the_reason(self):
        got = import_text("bending-notes.md", self.text, doc_id="U1", formulas={"c3": {"check": "surface_flaw > 0"}, "c5": {"check": "mass_kg"}})
        problems = {n["cand_id"]: n["problem"] for n in got.needs_formula}
        self.assertIn("surface_flaw", problems["c3"])
        self.assertIn("mass_kg", problems["c3"])  # the message lists the usable names
        self.assertIn("はい / いいえ", problems["c5"])
        self.assertNotIn("U1-03", [r["rule_id"] for r in got.record["rules"]])

    def test_a_sentence_that_is_not_in_the_file(self):
        with self.assertRaises(IntakeError) as cm:
            import_text("bending-notes.md", self.text, doc_id="U1", formulas={"c99": {"check": "mass_kg > 1"}})
        self.assertIn("c99", str(cm.exception))

    def test_the_rules_run_on_a_design(self):
        got = import_text("rules.csv", read("rules.csv"), doc_id="U2", formulas={"c2": {"check": "mass_kg <= 30", "kinds": ["battery"]}})
        checks = rulecheck.check_all(got.record["rules"], mini_design(), [])
        by = {c["rule_id"]: c for c in checks}
        self.assertEqual(by["U2-01"]["hits"], [])  # the seat is 450 wide, the rule wants 400 or more
        self.assertEqual(by["U2-01"]["targets"], ["battery", "seat", "floor", "cargo"])  # no kinds in the CSV: every part
        self.assertEqual([h["part_id"] for h in by["U2-02"]["hits"]], ["battery"])  # 50 kg > 30

    def test_doc_ids(self):
        self.assertEqual(doc_id_for("a.md", {}), "U1")
        self.assertEqual(doc_id_for("b.md", {"a.md": "U1"}), "U2")
        self.assertEqual(doc_id_for("a.md", {"a.md": "U1", "b.md": "U2"}), "U1")  # the same file again updates its document


class SampleNotesTest(unittest.TestCase):
    """data/knowledge/samples/bending-notes.md is the file the video's script drops on a run; what the script relies on."""

    def test_its_rules_the_skips_and_the_one_hit_on_the_demo_design(self):
        text = (REPO_ROOT / "data" / "knowledge" / "samples" / "bending-notes.md").read_text(encoding="utf-8")
        first = import_text("bending-notes.md", text, doc_id="U1")
        self.assertEqual([n["cand_id"] for n in first.needs_formula], ["c3", "c5"])  # numbers the script's skip uses
        skip = {"c3": "向きは式にできない", "c5": "部品どうしの距離を測る値が設計に無い"}
        got = import_text("bending-notes.md", text, doc_id="U1", skip=skip)
        self.assertEqual(got.needs_formula, [])
        self.assertEqual([s["reason"] for s in got.record["doc"]["skipped"]], list(skip.values()))
        by = {r["rule_id"]: r for r in got.record["rules"]}
        self.assertEqual(sorted(by), ["U1-01", "U1-02", "U1-04", "U1-06", "U1-07"])
        self.assertEqual(by["U1-01"]["applies_to"], {"kinds": ["body"]})
        self.assertEqual(by["U1-02"]["applies_to"], {"kinds": ["body"]})
        design = read_data(REPO_ROOT / "data" / "designs" / "offroad-4w-v1.json")
        checks = {c["rule_id"]: c for c in rulecheck.check_all(got.record["rules"], design, load_product("offroad-4w")["requirements"])}
        self.assertEqual({k: [h["part_id"] for h in c["hits"]] for k, c in checks.items() if c["hits"]}, {"U1-04": ["battery"]})
        for rid in ("U1-01", "U1-02"):  # the sheet-metal rules look at the same four plates as SM-01 and judge none of them
            self.assertEqual(checks[rid]["targets"], ["floor_front", "floor", "deck", "front_body"])
            self.assertEqual([n["part_id"] for n in checks[rid]["not_evaluable"]], checks[rid]["targets"])


if __name__ == "__main__":
    unittest.main()
