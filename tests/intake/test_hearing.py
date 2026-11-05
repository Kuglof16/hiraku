import copy
import re
import unittest

from hiraku import contract
from hiraku.intake import IntakeError, load_product, product_files
from hiraku.catalog.build import loading_requirement
from hiraku.contract.protocols import LOADINGS
from hiraku.intake.hearing import (applicable_items, hearing_text, item, load_answers, load_items, loading_answer, plan_hearing,
                                   prepare, save_answer)
from hiraku.intake.products import answers_file


def bare(slug):
    """The request as a person would first give it: the sentence and the flags, no requirements yet."""
    req = copy.deepcopy(load_product(slug))
    own = {r["key"]: r for r in req["requirements"]}
    req["requirements"] = []
    return req, own


class ItemsTest(unittest.TestCase):
    def test_every_item_is_well_formed(self):
        keys = [it["key"] for it in load_items()]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(len(keys), len(set(keys)))
        for it in load_items():
            self.assertTrue(contract.errors("req_key", it["key"]) == [], it["key"])
            for c in it.get("choices", []):
                self.assertIn("label", c)
                self.assertIn("value", c)
            d = it.get("default")
            if d:
                self.assertIn(d["trust"], (1, 2, 3))
                self.assertTrue(d["basis"], it["key"])
                if d["trust"] in (1, 2):
                    self.assertTrue("not checked" in d["basis"] or re.search(r"read 20\d\d-\d\d-\d\d", d["basis"]), it["key"])

    def test_catalog_asks_must_for_these(self):
        must = {it["key"] for it in load_items() if it["ask"] == "must"}
        self.assertEqual(must, {"Q30", "Q40", "Q50", "Q70"})

    def test_items_follow_the_flags(self):
        keys = lambda flags, sc=None: {it["key"] for it in applicable_items(flags, sc)}  # noqa: E731
        self.assertNotIn("Q90", keys({"self_propelled": True}))  # no rough road, no kilometres
        self.assertIn("Q90", keys({"self_propelled": True, "rough": True}))
        self.assertIn("Q42", keys({"liquid": True}))
        self.assertNotIn("Q42", keys({}))
        self.assertIn("Q92", keys({"lean": True}))
        self.assertNotIn("Q92", keys({"lean": True, "self_propelled": True}))  # lifetime in km, not in count

    def test_unknown_item(self):
        with self.assertRaises(IntakeError):
            item("Q98")


class PlanTest(unittest.TestCase):
    def test_a_bare_care_bed_asks_the_required_ones_and_defaults_the_rest(self):
        req, own = bare("care-bed")
        plan = plan_hearing(req, product_defaults=own)
        asked = {q["req_key"]: q for q in plan.questions}
        self.assertEqual(list(asked), ["Q03", "Q11", "Q19", "Q20", "Q30", "Q34", "Q40", "Q56", "Q61", "Q80"])
        self.assertEqual({k for k, q in asked.items() if q["ask"] == "must"}, {"Q30", "Q40"})
        self.assertNotIn("default", asked["Q30"])  # a required answer has no default to fall back on
        self.assertNotIn("default", asked["Q03"])  # nobody has a value for it
        self.assertEqual(asked["Q19"]["default"]["value"], 40)  # the bed file's own value comes before the item's
        self.assertEqual(asked["Q19"]["default"]["trust"], 1)
        self.assertTrue(all(q["allow_other"] for q in plan.questions))
        defaulted = {r["key"]: r for r in plan.defaulted}
        self.assertEqual(defaulted["Q60"]["value"], 1350)
        self.assertEqual(defaulted["Q60"]["source"], "default")
        self.assertIn("read 2026-11-03", defaulted["Q60"]["basis"])

    def test_a_confirm_question_carries_its_default_with_the_source(self):
        req, own = bare("electric-guitar")
        plan = plan_hearing(req, product_defaults=own)
        q61 = next(q for q in plan.questions if q["req_key"] == "Q61")
        self.assertEqual(q61["ask"], "confirm")
        self.assertEqual(q61["default"], {"value": 1.5, "unit": "1", "trust": 3, "basis": "assumption"})

    def test_what_the_file_has_is_not_asked(self):
        req = load_product("care-bed")
        plan = plan_hearing(req)
        self.assertNotIn("Q34", [q["req_key"] for q in plan.questions])
        self.assertIn("Q34", plan.present)

    def test_claude_can_reword_a_question_and_offer_choices(self):
        req, own = bare("care-bed")
        plan = plan_hearing(req, product_defaults=own, wording={"Q40": "ベッドの安全動作荷重 (SWL) は何 N にしますか。"},
                            choices={"Q40": [{"label": "1700 N", "value": 1700, "unit": "N"}]})
        q = next(q for q in plan.questions if q["req_key"] == "Q40")
        self.assertEqual(q["text"], "ベッドの安全動作荷重 (SWL) は何 N にしますか。")
        self.assertEqual(q["choices"], [{"label": "1700 N", "value": 1700, "unit": "N"}])


class PrepareTest(unittest.TestCase):
    def test_answers_become_hearing_requirements_and_the_file_in_is_untouched(self):
        req, own = bare("care-bed")
        before = copy.deepcopy(req)
        p = prepare(req, {"Q30": "driver", "Q40": {"value": 1700, "unit": "N", "trust": 2, "basis": "JIS"}},
                    "ありがとう", product_defaults=own)
        self.assertEqual(req, before)
        self.assertTrue(p.ready)
        by = {r["key"]: r for r in p.requirements_file["requirements"]}
        self.assertEqual((by["Q40"]["source"], by["Q40"]["value"], by["Q40"]["trust"]), ("hearing", 1700, 2))
        self.assertEqual((by["Q30"]["trust"], by["Q30"]["basis"]), (1, "answered in the hearing"))
        self.assertEqual(by["Q60"]["source"], "default")
        self.assertEqual(p.answered["note"], "ありがとう")
        self.assertEqual(contract.errors("requirements_file", p.requirements_file), [])
        self.assertEqual([r["key"] for r in p.requirements_file["requirements"]], sorted(by))

    def test_a_required_question_without_an_answer_stops_the_run(self):
        req, own = bare("care-bed")
        p = prepare(req, {"Q40": 1700}, product_defaults=own)
        self.assertFalse(p.ready)
        self.assertIn("Q30", p.unresolved)
        self.assertNotIn("Q30", {r["key"] for r in p.requirements_file["requirements"]})

    def test_an_unanswered_confirm_question_uses_its_default(self):
        req, own = bare("electric-guitar")
        p = prepare(req, {}, product_defaults=own)
        by = {r["key"]: r for r in p.requirements_file["requirements"]}
        self.assertEqual((by["Q61"]["source"], by["Q61"]["value"], by["Q61"]["trust"]), ("default", 1.5, 3))
        self.assertEqual(p.unresolved, ["Q03", "Q34", "Q80"])  # asked, no answer, no default: reported, not guessed
        self.assertTrue(p.ready)

    def test_an_answer_overrides_the_default_of_a_default_item(self):
        req, own = bare("care-bed")
        p = prepare(req, {"Q30": "two_cargo", "Q40": 1700, "Q60": 1500}, product_defaults=own)
        by = {r["key"]: r for r in p.requirements_file["requirements"]}
        self.assertEqual((by["Q60"]["source"], by["Q60"]["value"]), ("hearing", 1500))
        self.assertEqual(sum(1 for r in p.answered["answers"] if r["key"] == "Q60"), 1)

    def test_bad_answers(self):
        req, own = bare("care-bed")
        for answers, word in [({"Q98": 1}, "Q98"), ({"Q40": {"unit": "N"}}, "value"), ({"Q40": {"value": 1, "unit": "lbf"}}, "unit")]:
            with self.subTest(answers=answers):
                with self.assertRaises(IntakeError) as cm:
                    prepare(req, answers, product_defaults=own)
                self.assertIn(word, str(cm.exception))
        full = load_product("care-bed")
        with self.assertRaises(IntakeError):
            prepare(full, {"Q34": "子ども"})  # already in the file: fix the file, do not overwrite it silently

    def test_an_answer_from_the_screen_to_a_key_the_file_has_is_left_out_not_the_whole_answer(self):
        full = load_product("care-bed")
        got = prepare(full, {"Q34": "子ども"}, skip_present=True)
        self.assertEqual(got.ignored, ["Q34"])
        self.assertIn("Q34", got.answered["note"])
        self.assertEqual({r["key"]: r["value"] for r in got.requirements_file["requirements"]}["Q34"],
                         {r["key"]: r["value"] for r in full["requirements"]}["Q34"])  # the file's value stays

    def test_hearing_text(self):
        req, own = bare("care-bed")
        p = prepare(req, {"Q30": "two_cargo", "Q40": 1}, product_defaults=own)
        self.assertEqual(hearing_text(p).split("、")[0], "質問 10 件")
        self.assertIn("答えが無いまま 1 件", hearing_text(p))  # Q03


class RequirementsFileStandsTest(unittest.TestCase):
    """ask_confirm False: the requirements came as a file somebody wrote (a run started from it)."""

    def file(self, drop=()):
        req = copy.deepcopy(load_product("serving-robot"))
        req["requirements"] = [r for r in req["requirements"] if r["key"] not in drop]
        req["requirements"] += [{"key": "Q40", "name": "積載", "value": 40, "unit": "kg", "source": "user", "trust": 1},
                                {"key": "Q50", "name": "最高速度", "value": 1.2, "unit": "m/s", "source": "user", "trust": 1}]
        return req

    def test_nothing_is_asked_when_the_file_has_every_required_item(self):
        p = prepare(self.file(), ask_confirm=False)
        self.assertEqual(p.asked["questions"], [])
        self.assertTrue(p.ready)
        self.assertEqual(p.unresolved, [])
        self.assertEqual(hearing_text(p), "要件のファイルのまま進みます")
        self.assertEqual(p.unasked, ["Q20", "Q42", "Q51", "Q74"])  # confirm items with no value anywhere: left out, not made up

    def test_a_default_is_added_with_its_source_and_lands_in_the_file_out(self):
        req = self.file(drop=("Q63",))
        p = prepare(req, ask_confirm=False)
        self.assertEqual(p.asked["questions"], [])
        (added,) = p.answered["answers"]
        self.assertEqual((added["key"], added["source"], added["trust"]), ("Q63", "default", 1))
        self.assertIn("read 2026-11-03", added["basis"])
        out = {r["key"]: r for r in p.requirements_file["requirements"]}
        self.assertEqual(out["Q63"], added)  # the same record in the answered list and in the requirements
        self.assertEqual(hearing_text(p), "要件のファイルのまま進みます (既定値で足した 1 件: Q63)")
        self.assertEqual(len(p.requirements_file["requirements"]), len(req["requirements"]) + 1)

    def test_a_confirm_item_with_a_default_takes_it_instead_of_asking(self):
        p = prepare(self.file(drop=("Q61",)), ask_confirm=False)
        added = {r["key"]: r for r in p.answered["answers"]}["Q61"]
        self.assertEqual((added["value"], added["source"], added["trust"]), (1.5, "default", 3))
        self.assertEqual(p.asked["questions"], [])

    def test_a_required_item_the_file_lacks_is_still_asked(self):
        req = self.file()
        req["requirements"] = [r for r in req["requirements"] if r["key"] != "Q50"]
        p = prepare(req, ask_confirm=False)
        self.assertEqual([q["req_key"] for q in p.asked["questions"]], ["Q50"])
        self.assertFalse(p.ready)

    def test_the_default_still_asks_the_way_it_did(self):
        p = prepare(self.file(), ask_confirm=True)
        self.assertEqual([q["req_key"] for q in p.asked["questions"]], ["Q20", "Q42", "Q51", "Q74"])


class LoadingAnswerTest(unittest.TestCase):
    """Q30 is the combination to check: the catalog takes only empty, driver, two and two_cargo."""

    def test_the_names_and_their_japanese_words_map_to_a_loading(self):
        for said, want in [("driver", "driver"), ("two_cargo", "two_cargo"), ("空車", "empty"), ("運転者だけ", "driver"),
                           ("2 人", "two"), ("2人", "two"), ("2 人 + 荷", "two_cargo"), ("2人+荷", "two_cargo"), ("1 人", "driver"),
                           ("EMPTY", "empty")]:
            with self.subTest(said=said):
                self.assertEqual(loading_answer(said), want)
        for said in ("利用者 1 人 + 介助者 1 人", "周りの客", "x", "", 3, None):
            with self.subTest(said=said):
                self.assertIsNone(loading_answer(said))

    def test_the_question_offers_the_four_and_an_answer_becomes_one_of_them(self):
        self.assertEqual([c["value"] for c in item("Q30")["choices"]], list(LOADINGS))
        req, own = bare("care-bed")
        p = prepare(req, {"Q30": "運転者だけ", "Q40": 1700}, product_defaults=own)
        by = {r["key"]: r for r in p.requirements_file["requirements"]}
        self.assertEqual(by["Q30"]["value"], "driver")
        self.assertEqual(loading_requirement(p.requirements_file), "driver")  # what the catalog takes

    def test_an_answer_that_is_not_a_combination_is_refused_and_says_what_to_say(self):
        req, own = bare("care-bed")
        with self.assertRaises(IntakeError) as cm:
            prepare(req, {"Q30": "利用者 1 人 + 介助者 1 人", "Q40": 1700}, product_defaults=own)
        for word in ("empty", "driver", "two", "two_cargo", "介助者"):
            self.assertIn(word, str(cm.exception))
        import tempfile

        with tempfile.TemporaryDirectory() as d, self.assertRaises(IntakeError):  # the screen's answer is checked the same way
            save_answer(d, {"answers": {"Q30": "周りの客"}})

    def test_the_sample_answers_give_a_loading_the_catalog_takes(self):
        for slug in product_files():
            path = answers_file(slug)
            answers = load_answers(path)[0] if path else {}
            if "Q30" in answers:
                with self.subTest(slug=slug):
                    value = answers["Q30"]["value"] if isinstance(answers["Q30"], dict) else answers["Q30"]
                    self.assertIn(value, LOADINGS)


class SampleAnswersTest(unittest.TestCase):
    def test_each_product_with_its_sample_answers_is_ready_and_valid(self):
        for slug in product_files():
            with self.subTest(slug=slug):
                req = load_product(slug)
                path = answers_file(slug)
                answers, note = load_answers(path) if path else ({}, None)
                p = prepare(req, answers, note)
                self.assertTrue(p.ready)
                self.assertEqual(contract.errors("requirements_file", p.requirements_file), [])
                for r in p.answered["answers"]:
                    self.assertIn(r["source"], ("hearing", "default"))
                    self.assertEqual(contract.errors("requirement", r), [])

    def test_the_demo_vehicle_answers_every_question_of_the_hearing(self):
        req = load_product("offroad-4w")
        answers, note = load_answers(answers_file("offroad-4w"))
        p = prepare(req, answers, note)
        self.assertEqual(p.unresolved, [])
        by = {a["key"]: a["source"] for a in p.answered["answers"]}
        for key in ("Q03", "Q20", "Q91"):
            self.assertEqual(by[key], "hearing", key)
        self.assertEqual(p.answered["note"], note)

    def test_answers_file_with_a_stray_key_is_refused(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "a.yaml"
            path.write_text("answers: {}\nstuff: 1\n", encoding="utf-8")
            with self.assertRaises(IntakeError):
                load_answers(path)


class EntryExitItemsTest(unittest.TestCase):
    """The items for getting in and out: a rider's vehicle only, borrowed numbers are assumptions that say where they were read."""

    OPENING = ["Q93", "Q94", "Q95", "Q96"]
    KEYS = OPENING + ["Q97", "Q44", "Q45", "Q46", "Q47", "Q48", "Q49", "Q89"]

    def test_they_apply_to_a_vehicle_people_ride_that_is_rough(self):
        keys = {i["key"] for i in applicable_items({"ride": True, "rough": True, "self_propelled": True})}
        self.assertTrue(set(self.KEYS) <= keys)
        for flags in ({"ride": True}, {"rough": True}, {"lean": True}):
            self.assertFalse(set(self.KEYS) & {i["key"] for i in applicable_items(flags)}, flags)

    def test_each_has_a_value_a_basis_that_says_what_was_read_and_trust_3(self):
        for key in self.KEYS:
            it = item(key)
            with self.subTest(key=key):
                self.assertEqual(it["ask"], "default")  # not asked: the default is used and shown
                self.assertEqual(it["default"]["trust"], 3)
                basis = it["default"]["basis"]
                self.assertTrue("not checked" in basis or "read 2026-11-04" in basis, basis)
                self.assertTrue(it["why"] and it["example"])

    def test_the_opening_items_say_that_a_net_does_not_count_as_the_side_guard(self):
        for key in self.OPENING + ["Q47"]:
            self.assertIn("付けて働く網", item(key)["why"], key)


class TypedNumberTest(unittest.TestCase):
    """A person types a number into a box: '150 kg' must reach the run as 150 with the unit kg (the run once stopped at its
    start with 'requirement Q40 must be in kg, got None')."""

    def answer(self, key, value):
        from hiraku.intake.hearing import _answer_requirement
        return _answer_requirement(item(key), value)

    def test_a_number_with_its_unit_in_words_becomes_a_value_and_a_unit(self):
        got = self.answer("Q40", "150 kg")
        self.assertEqual((got["value"], got["unit"]), (150, "kg"))
        got = self.answer("Q50", "40km/h")
        self.assertEqual((got["value"], got["unit"]), (40, "km/h"))

    def test_a_number_alone_takes_the_unit_of_the_item_and_a_known_other_unit_is_converted(self):
        self.assertEqual(self.answer("Q40", "150")["unit"], "kg")
        self.assertEqual(self.answer("Q40", 150)["unit"], "kg")
        self.assertEqual(self.answer("Q14", "12")["unit"], "deg")  # the unit comes from the choices
        got = self.answer("Q50", "10 m/s")
        self.assertEqual((got["value"], got["unit"]), (36, "km/h"))
        got = self.answer("Q40", "0.2 t")
        self.assertEqual((got["value"], got["unit"]), (200, "kg"))

    def test_text_that_is_not_a_number_or_a_unit_that_cannot_be_read_is_refused_in_plain_words(self):
        for bad in ("たくさん", "150 lbf", "kg 150"):
            with self.assertRaises(IntakeError, msg=bad) as ctx:
                self.answer("Q40", bad)
            self.assertIn("積載", str(ctx.exception))
            self.assertIn("kg", str(ctx.exception))

    def test_an_answer_that_names_its_unit_and_an_item_without_a_unit_are_left_alone(self):
        self.assertEqual(self.answer("Q40", {"value": 150, "unit": "kg"})["unit"], "kg")
        self.assertNotIn("unit", self.answer("Q11", "砂利"))  # a surface is a word, not a number


if __name__ == "__main__":
    unittest.main()
