import re
import unittest

from hiraku import contract
from hiraku.intake import (IntakeError, describe_condition, flags_text, load_product, pick_domains, product_files,
                           project_picks, reasons_missing, summary_line)
from hiraku.intake.products import answers_file


def pick(slug, **kw):
    req = load_product(slug)
    return pick_domains(req["want"], req["flags"], req.get("speed_class"), **kw)


class ProductFilesTest(unittest.TestCase):
    def test_four_products_and_no_answer_files_among_them(self):
        self.assertEqual(sorted(product_files()), ["care-bed", "electric-guitar", "offroad-4w", "serving-robot"])

    def test_every_product_file_fits_the_contract(self):
        for slug in product_files():
            with self.subTest(slug=slug):
                req = load_product(slug)  # load_requirements validates
                self.assertEqual(contract.errors("requirements_file", req), [])

    def test_external_values_say_whether_they_were_read(self):
        for slug in ("care-bed", "electric-guitar", "serving-robot"):
            req = load_product(slug)
            for r in req["requirements"]:
                if r["trust"] in (1, 2):
                    with self.subTest(slug=slug, key=r["key"]):
                        self.assertTrue("not checked" in r["basis"] or re.search(r"read 20\d\d-\d\d-\d\d", r["basis"]), r["basis"])

    def test_sample_answers_exist_for_the_products_that_have_must_questions(self):
        for slug in ("care-bed", "serving-robot", "offroad-4w"):
            self.assertIsNotNone(answers_file(slug), slug)
        self.assertIsNone(answers_file("electric-guitar"))


class PickDomainsTest(unittest.TestCase):
    """The catalog's expected numbers: guitar 2, care bed 5, serving robot 7, off-road vehicle 7."""

    def test_counts(self):
        expected = {"electric-guitar": 2, "care-bed": 5, "serving-robot": 7, "offroad-4w": 7}
        for slug, n in expected.items():
            with self.subTest(slug=slug):
                self.assertEqual(len(pick(slug)["picks"]), n)

    def test_which_domains(self):
        domains = lambda slug: [p["domain"] for p in pick(slug)["picks"]]  # noqa: E731
        self.assertEqual(domains("electric-guitar"), ["strength", "nvh"])
        self.assertEqual(domains("care-bed"), ["handling", "powertrain", "strength", "nvh", "thermal"])
        self.assertEqual(domains("offroad-4w"), list(contract.DOMAINS[:7]))
        self.assertEqual(domains("serving-robot"), list(contract.DOMAINS[:7]))

    def test_every_pick_and_off_covers_the_seven_domains_once(self):
        for slug in product_files():
            rec = pick(slug)
            got = [p["domain"] for p in rec["picks"]] + [o["domain"] for o in rec["off"]]
            self.assertEqual(sorted(got), sorted(contract.DOMAINS[:7]), slug)

    def test_test_ids_come_from_the_catalog(self):
        rec = pick("serving-robot")
        by = {p["domain"]: p["test_ids"] for p in rec["picks"]}
        self.assertEqual(by["aero"], ["F3"])
        self.assertEqual(by["crash"], ["C2"])
        self.assertEqual(by["powertrain"], ["P1", "P2", "P3", "P4"])
        self.assertIn("H4", rec["assembly_test_ids"])
        self.assertNotIn("H3", rec["assembly_test_ids"])  # nobody rides or sits on it

    def test_renames_for_the_serving_robot(self):
        names = {p["domain"]: p["shown_name"] for p in pick("serving-robot")["picks"]}
        self.assertEqual(names["aero"], "液体のこぼれ")
        self.assertEqual(names["crash"], "人との接触")
        self.assertEqual(names["handling"], "操安")

    def test_renames_for_the_others(self):
        bed = {p["domain"]: p["shown_name"] for p in pick("care-bed")["picks"]}
        self.assertEqual(bed["handling"], "安定 (転倒)")
        self.assertEqual(bed["nvh"], "騒音")
        guitar = {p["domain"]: p["shown_name"] for p in pick("electric-guitar")["picks"]}
        self.assertEqual(guitar["nvh"], "音振")
        car = {p["domain"]: p["shown_name"] for p in pick("offroad-4w")["picks"]}
        self.assertEqual(car, {d: contract.DOMAIN_NAMES_JA[d] for d in contract.DOMAINS[:7]})

    def test_claudes_words_win_and_the_code_fills_in_the_rest(self):
        rec = pick("electric-guitar", reasons={"strength": "弦の張力でネックが反る"}, shown_names={"nvh": "デッドスポット"})
        by = {p["domain"]: p for p in rec["picks"]}
        self.assertEqual(by["strength"]["reason"], "弦の張力でネックが反る")
        self.assertEqual(by["nvh"]["shown_name"], "デッドスポット")
        self.assertIn("N1", by["nvh"]["reason"])  # the code's reason names the test
        self.assertEqual(reasons_missing(rec, {"strength": "弦の張力でネックが反る"}), ["nvh"])
        self.assertEqual(reasons_missing(rec, None), ["strength", "nvh"])

    def test_the_person_can_take_a_domain_off(self):
        rec = pick("care-bed", exclude=["thermal"])
        self.assertNotIn("thermal", [p["domain"] for p in rec["picks"]])
        self.assertEqual([o["reason"] for o in rec["off"] if o["domain"] == "thermal"], ["人が外した"])

    def test_off_reasons_say_which_conditions_failed(self):
        reason = next(o["reason"] for o in pick("electric-guitar")["off"] if o["domain"] == "handling")
        self.assertIn("S1", reason)
        self.assertIn("置", reason)

    def test_flags_are_completed_and_flag_reasons_are_kept_in_order(self):
        rec = pick_domains("机", {"standing": True, "cargo": True}, "static",
                           flag_reasons={"cargo": "物を載せる", "standing": "床に置く"})
        self.assertEqual(len(rec["flags"]), 19)
        self.assertEqual(list(rec["flag_reasons"]), ["cargo", "standing"])
        self.assertEqual([p["domain"] for p in rec["picks"]], ["handling", "strength"])

    def test_bad_input_says_what_to_send(self):
        for kw, word in [
            ({"flags": {"fly": True}}, "fly"),
            ({"flags": {"ride": "yes"}}, "ride"),
            ({"flags": {}, "speed_class": "fast"}, "speed_class"),
            ({"flags": {}, "reasons": {"assembly": "x"}}, "assembly"),
            ({"flags": {}, "exclude": ["nvh2"]}, "nvh2"),
            ({"flags": {}, "want": " "}, "want"),
        ]:
            with self.subTest(kw=kw):
                args = {"want": "x", **kw}
                with self.assertRaises(IntakeError) as cm:
                    pick_domains(**args)
                self.assertIn(word, str(cm.exception))

    def test_project_picks_fit_the_contract(self):
        for slug in product_files():
            for p in project_picks(pick(slug)):
                self.assertEqual(contract.errors("domain_pick", p), [])

    def test_summary_line(self):
        self.assertEqual(summary_line(pick("electric-guitar")), "2 分野: 強度 (弦の張力) (2)・音振 (1)")


class FlagsTest(unittest.TestCase):
    def test_describe_condition(self):
        self.assertEqual(describe_condition("self_propelled and speed_vehicle"), "走∧速=車")
        self.assertEqual(describe_condition("(cargo or lean) and not self_propelled"), "(荷∨預)∧¬走")
        self.assertEqual(describe_condition("always"), "常")

    def test_flags_text(self):
        self.assertEqual(flags_text({"ride": True, "battery": True, "rough": False}), "乗・池")
        self.assertEqual(flags_text({}), "なし")


if __name__ == "__main__":
    unittest.main()
