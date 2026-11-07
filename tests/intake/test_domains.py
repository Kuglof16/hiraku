import re
import unittest
from unittest import mock

from hiraku import contract
from hiraku.intake import domains as domains_module
from hiraku.intake import (IntakeError, describe_condition, flags_text, load_product, pick_domains, product_files,
                           project_picks, reasons_missing, summary_line)
from hiraku.intake.products import answers_file


_SAVED_WRITER = []


def setUpModule():
    """These tests assume no load-case writer is registered (hiraku.tools registers one when it is imported, and
    stays registered in a process that ran other tests first): hold the state here and put it back after."""
    _SAVED_WRITER.append(domains_module.load_case_writer)
    domains_module.load_case_writer = None


def tearDownModule():
    domains_module.load_case_writer = _SAVED_WRITER.pop()


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


class NoBuilderTest(unittest.TestCase):
    """A test no builder can make a load case for would stay pending in the run: it is not in test_ids."""

    @staticmethod
    def without_builders(*ids):
        return mock.patch("hiraku.intake.domains.builder_for", lambda catalog, tid: None if tid in ids else object())

    def test_a_test_without_a_builder_is_not_picked_but_still_lights_its_domain_and_the_card_says_why(self):
        with self.without_builders("F3", "C2"):
            rec = pick("serving-robot")
        by = {p["domain"]: p for p in rec["picks"]}
        self.assertEqual((by["aero"]["test_ids"], by["crash"]["test_ids"]), ([], []))  # lit by F3 and C2, which cannot be run yet
        self.assertEqual(by["powertrain"]["test_ids"], ["P1", "P2", "P3", "P4"])
        for d, tid in (("aero", "F3"), ("crash", "C2")):
            self.assertIn(tid, by[d]["reason"])
            self.assertIn("荷重条件の作り方がまだ無い", by[d]["reason"])
        self.assertNotIn("荷重条件の作り方がまだ無い", by["powertrain"]["reason"])
        self.assertEqual(contract.errors("domains_picked", rec), [])

    def test_a_reason_given_by_claude_is_kept_and_gets_the_note(self):
        with self.without_builders("F3"):
            rec = pick("serving-robot", reasons={"aero": "こぼれるかを見る"})
        reason = {p["domain"]: p for p in rec["picks"]}["aero"]["reason"]
        self.assertTrue(reason.startswith("こぼれるかを見る。"))
        self.assertIn("F3", reason)

    def test_assembly_tests_without_a_builder_are_left_out_too(self):
        with self.without_builders("H4"):
            self.assertNotIn("H4", pick("serving-robot")["assembly_test_ids"])
        with self.without_builders():
            self.assertIn("H4", pick("serving-robot")["assembly_test_ids"])

    def test_a_product_with_builders_of_its_own_picks_its_tests_and_other_products_do_not(self):
        def dispatcher(spec, req, design):  # what hiraku.catalog.builders makes for a test only some products build
            raise AssertionError("not called")

        dispatcher.general = None  # no general builder: only the products that registered one
        own = {"care-bed": {"P5": object(), "T3": object()}}

        def fake_builder_for(catalog, tid):
            return dispatcher if tid in ("P5", "T3", "N3") else object()

        with mock.patch("hiraku.intake.domains.builder_for", fake_builder_for), \
                mock.patch("hiraku.intake.domains._product_builders", lambda: own):
            bed = pick("care-bed", product="care-bed")
            other = pick("care-bed", product="electric-guitar")
            unknown = pick("care-bed")
        ids = lambda rec: {p["domain"]: p["test_ids"] for p in rec["picks"]}  # noqa: E731
        self.assertEqual((ids(bed)["powertrain"], ids(bed)["thermal"], ids(bed)["nvh"]), (["P5"], ["T3"], []))  # N3: nobody has it
        for rec in (other, unknown):
            self.assertEqual((ids(rec)["powertrain"], ids(rec)["thermal"], ids(rec)["nvh"]), ([], [], []))
        self.assertEqual(ids(bed)["strength"], ids(other)["strength"])  # the general builders are for every product

    def test_without_product_modules_it_is_the_general_builders_as_before(self):
        with mock.patch("hiraku.intake.domains._product_builders", lambda: {}), \
                mock.patch("hiraku.intake.domains.builder_for", lambda catalog, tid: None if tid == "N3" else object()):
            rec = pick("care-bed", product="care-bed")
        self.assertEqual({p["domain"]: p["test_ids"] for p in rec["picks"]}["nvh"], [])
        self.assertIn("S1", {p["domain"]: p["test_ids"] for p in rec["picks"]}["handling"])

    def test_the_real_catalog_picks_only_tests_that_have_a_builder(self):
        from hiraku.catalog import builder_for, load_catalog

        catalog = load_catalog()
        for slug in product_files():
            rec = pick(slug)
            for p in rec["picks"]:
                for tid in p["test_ids"]:
                    with self.subTest(slug=slug, test=tid):
                        self.assertIsNotNone(builder_for(catalog, tid))


class ImpliedFlagsTest(unittest.TestCase):
    """What a reading of the sentence leaves out and the code adds (vibration, continuous) so N1, T1 and T2 are not lost."""

    FLAGS = {"ride": True, "self_propelled": True, "outdoor": True, "rough": True, "battery": True, "electric": True,
             "cargo": True, "standing": True}  # what Claude set for an electric off-road vehicle (a real run, 2026-11-06)

    def picked(self, flags, **kw):
        rec = pick_domains("林道を走る電動の車", flags, "vehicle", **kw)
        return rec, {p["domain"]: p["test_ids"] for p in rec["picks"]}

    def test_a_self_propelled_battery_vehicle_gets_vibration_and_continuous_and_the_tests_they_light(self):
        rec, by = self.picked(self.FLAGS)
        self.assertTrue(rec["flags"]["vibration"] and rec["flags"]["continuous"])
        self.assertIn("N1", by["nvh"])
        self.assertTrue({"T1", "T2"} <= set(by["thermal"]))
        self.assertIn("コードが補った", rec["flag_reasons"]["vibration"])
        self.assertEqual(contract.errors("domains_picked", rec), [])

    def test_a_flag_given_as_false_stays_false_and_a_given_reason_is_kept(self):
        rec, by = self.picked({**self.FLAGS, "continuous": False}, flag_reasons={"vibration": "Claude の理由"})
        self.assertFalse(rec["flags"]["continuous"])
        self.assertEqual(by["thermal"], [t for t in by["thermal"] if t not in ("T1", "T2")])
        self.assertEqual(rec["flag_reasons"]["vibration"], "Claude の理由")

    def test_a_product_that_does_not_move_by_itself_is_not_changed(self):
        rec, _ = self.picked({"lean": True, "electric": True, "standing": True})
        self.assertFalse(rec["flags"]["vibration"] or rec["flags"]["continuous"])

    def test_the_reason_has_one_full_stop_before_the_note_and_an_added_test_is_not_said_to_be_left_out(self):
        from hiraku.intake.domains import add_tests

        rec, _ = self.picked(self.FLAGS, reasons={"handling": "倒れないかを確かめる。"})
        reason = {p["domain"]: p["reason"] for p in rec["picks"]}["handling"]
        self.assertNotIn("。。", reason)
        left = [t for t in ("S2",) if "S2" in reason]
        if left:  # S2 has no builder here: the note says so, and adding it takes the sentence out
            self.assertIn("S2", reason)
            after, _, _ = add_tests(rec, [{"test_id": "S2", "reason": "転ぶ向きを見る"}])
            again = {p["domain"]: p for p in after["picks"]}["handling"]
            self.assertIn("S2", again["test_ids"])
            self.assertNotIn("S2 旋回", again["reason"])  # taken into the run: not "left out" any more
            self.assertTrue(again["reason"].startswith("倒れないかを確かめる"))


class WrittenLoadCaseTest(unittest.TestCase):
    """When a run can have Claude write a load case (a writer is registered), the tests no builder makes are picked too."""

    def ids(self, rec):
        return {p["domain"]: p["test_ids"] for p in rec["picks"]}

    def test_nothing_registered_the_picks_are_as_before(self):
        self.assertFalse(domains_module.can_write_load_cases())
        rec = pick("serving-robot", product="serving-robot")
        self.assertNotIn("F3", sum(self.ids(rec).values(), []))  # no builder for it: left out
        self.assertNotIn("Claude が荷重条件を書く", " ".join(p["reason"] for p in rec["picks"]))

    def test_with_a_writer_the_tests_without_a_builder_are_in_and_the_card_says_who_writes_them(self):
        with mock.patch.object(domains_module, "load_case_writer", lambda *a: None):
            self.assertTrue(domains_module.can_write_load_cases())
            rec = pick("serving-robot", product="serving-robot")
            bare = pick("serving-robot")
        every = sum(self.ids(rec).values(), [])
        for tid in ("R1", "N1", "S1", "F3", "C2", "S2", "R2", "R6"):  # with a builder, and the ones Claude writes
            self.assertIn(tid, every)
        self.assertTrue({"H1", "H5", "H2", "H4", "G2"} <= set(rec["assembly_test_ids"]))
        reasons = {p["domain"]: p["reason"] for p in rec["picks"]}
        self.assertIn("F3", reasons["aero"])
        self.assertIn("Claude が荷重条件を書く", reasons["aero"])
        self.assertNotIn("Claude が荷重条件を書く", reasons["nvh"])  # N1 has a builder
        self.assertEqual(contract.errors("domains_picked", rec), [])
        self.assertEqual(self.ids(bare)["aero"], ["F3"])  # no product named: the general builders only, the rest is Claude's to write

    def test_a_test_with_a_builder_is_not_said_to_be_written(self):
        with mock.patch.object(domains_module, "load_case_writer", lambda *a: None):
            rec = pick("serving-robot", product="serving-robot")
        power = {p["domain"]: p["reason"] for p in rec["picks"]}["powertrain"]
        self.assertNotIn("Claude が荷重条件を書く", power)

    def test_a_reason_given_by_claude_gets_the_note(self):
        with mock.patch.object(domains_module, "load_case_writer", lambda *a: None):
            rec = pick("serving-robot", product="serving-robot", reasons={"aero": "こぼれるかを見る"})
        reason = {p["domain"]: p["reason"] for p in rec["picks"]}["aero"]
        self.assertTrue(reason.startswith("こぼれるかを見る。"))
        self.assertIn("Claude が荷重条件を書く", reason)


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
        with mock.patch("hiraku.intake.domains.builder_for", lambda catalog, tid: object()):  # every test has a builder
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
        # Claude's reason is kept; the code adds one line about the tests that have no load-case builder yet
        # (R8 today), which goes away when the builder exists
        self.assertTrue(by["strength"]["reason"].startswith("弦の張力でネックが反る"))
        unbuilt = [t for t in ("R8", "R7", "R2") if t in by["strength"]["reason"]]
        self.assertEqual(by["strength"]["reason"] == "弦の張力でネックが反る", not unbuilt)
        if unbuilt:
            self.assertIn("荷重条件の作り方がまだ無い", by["strength"]["reason"])
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
        rec = pick("electric-guitar")  # the counts follow the catalog and the builders: not written here
        tests = {t for p in rec["picks"] for t in p["test_ids"]} | set(rec["assembly_test_ids"])
        self.assertEqual(summary_line(rec), f"確かめる分野を {len(rec['picks'])} つ、試験を {len(tests)} 個選びました")
        added = dict(rec, added_tests=[{"test_id": "N1", "reason": "x"}])
        self.assertTrue(summary_line(added).endswith("(Claude が足した 1 個を含む)"))
        self.assertEqual([p["domain"] for p in rec["picks"]], ["strength", "nvh"])


class FlagsTest(unittest.TestCase):
    def test_describe_condition(self):
        self.assertEqual(describe_condition("self_propelled and speed_vehicle"), "走∧速=車")
        self.assertEqual(describe_condition("(cargo or lean) and not self_propelled"), "(荷∨預)∧¬走")
        self.assertEqual(describe_condition("always"), "常")

    def test_flags_text(self):
        self.assertEqual(flags_text({"ride": True, "battery": True, "rough": False}), "乗・池")
        self.assertEqual(flags_text({}), "なし")


class LoadChoiceWordsTest(unittest.TestCase):
    """Q30's choices keep the contract's names (empty, driver, two, two_cargo); the words a person reads follow the flags."""

    def labels(self, flags):
        from hiraku.intake.hearing import plan_hearing

        q = {q["req_key"]: q for q in plan_hearing({"flags": flags, "speed_class": "static", "requirements": []}).questions}["Q30"]
        return [c["value"] for c in q["choices"]], [c["label"] for c in q["choices"]], q["text"]

    def test_a_vehicle_has_a_driver_and_something_lain_on_has_one_person(self):
        values, labels, text = self.labels({"ride": True, "self_propelled": True})
        self.assertEqual(values, ["empty", "driver", "two", "two_cargo"])
        self.assertEqual(labels[1], "運転者だけ")
        self.assertIn("運転者だけ", text)
        values, labels, text = self.labels({"lean": True, "standing": True})
        self.assertEqual(values, ["empty", "driver", "two", "two_cargo"])  # the value is the contract's, unchanged
        self.assertEqual(labels[1], "1 人だけ")
        self.assertIn("1 人だけ", text)
        self.assertNotIn("運転者", text)
        self.assertEqual(self.labels({"lean": True, "ride": True})[1][1], "運転者だけ")  # a ridden bed (a wheelchair): a driver

    def test_the_screen_gets_the_same_rule_as_data(self):
        from hiraku.intake import webdata

        self.assertEqual(webdata.tables()["ITEM_CHOICES_ALT"]["Q30"],
                         {"labels": {"driver": "1 人だけ"}, "on": ["lean"], "off": ["ride"]})
        with self.assertRaises(ValueError):
            webdata._flag_terms("lean or ride")


class TableWordsTest(unittest.TestCase):
    def test_a_table_or_a_desk_stands_on_the_floor_and_carries_things_and_nobody_rides_it(self):
        from hiraku.intake.guess import guess_flags

        for word in ("テーブル", "デスク", "作業台", "机"):
            flags, why, _ = guess_flags(f"{word}を作りたい")
            self.assertEqual((flags.get("standing"), flags.get("cargo")), (True, True), word)
            self.assertNotIn("lean", flags, word)  # nobody lies on it: the questions about people on a bed or a vehicle stay out
            self.assertNotIn("ride", flags, word)
            self.assertIn(word, why["standing"])
        self.assertEqual(guess_flags("ソファを作りたい")[0].get("lean"), True)
        self.assertEqual(guess_flags("台車を作りたい")[0].get("standing"), None)  # a bare 台 is not a word of this list

    def test_a_table_is_not_asked_how_many_people_ride_it_but_how_much_it_carries(self):
        from hiraku.intake.guess import guess_flags
        from hiraku.intake.hearing import plan_hearing

        flags, _, speed = guess_flags("テーブルを作りたい")
        asked = {q["req_key"]: q for q in plan_hearing({"flags": flags, "speed_class": speed or "static", "requirements": []}).questions}
        self.assertNotIn("Q30", asked)  # the people-and-load combination is for what is ridden or lain on
        self.assertNotIn("乗る人", asked["Q40"]["text"])
        self.assertIn("載せる荷の重さの上限", asked["Q40"]["text"])
        bed, _, _ = guess_flags("介護ベッドを作りたい (荷物も載せる)")
        asked = {q["req_key"]: q for q in plan_hearing({"flags": bed, "speed_class": "static", "requirements": []}).questions}
        self.assertIn("Q30", asked)
        self.assertIn("乗る人とは別に", asked["Q40"]["text"])  # for a bed or a vehicle the question keeps its extra sentence


if __name__ == "__main__":
    unittest.main()
