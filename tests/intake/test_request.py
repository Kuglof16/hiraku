import copy
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from hiraku import contract
from hiraku.catalog import load_requirements
from hiraku.intake import IntakeError, events as ev, knowledge, load_product, request
from hiraku.intake.hearing import load_answers
from hiraku.intake.pipeline import intake, save
from hiraku.intake.products import answers_file
from hiraku.intake.agent import PickRun
from hiraku.intake.flags import FLAGS
from hiraku.intake.guess import guess_flags

ROBOT = load_product("serving-robot")
BED = load_product("care-bed")
BED_ANSWERS = {"Q30": "driver", "Q40": {"value": 1700, "unit": "N"}}
NOW = datetime(2026, 11, 3, 14, 0, 0)


def claude_like(domain):
    """A picker that answers the way Claude does: the product's flags with a reason and a name of its own."""
    calls = []

    def picker(want):
        calls.append(want)
        return {"flags": ROBOT["flags"], "speed_class": "walk", "flag_reasons": {"liquid": "飲み物を運ぶので"},
                "reasons": {domain: "Claude の理由"}, "shown_names": {}, "cost_usd": 0.12}

    picker.calls = calls
    return picker


class BeginTest(unittest.TestCase):
    def test_a_product_file_lends_its_flags_and_asks_what_it_lacks(self):
        got = request.begin(ROBOT["want"], product="serving-robot")
        self.assertEqual((got["mode"], got["flags_from"], got["draft"]["product"]), ("code", "product", "serving-robot"))
        self.assertEqual(len(got["domains"]["picks"]), 7)
        keys = [q["req_key"] for q in got["hearing"]["questions"]]
        self.assertTrue({"Q40", "Q50"} <= set(keys))  # required, and the file does not hold them
        self.assertEqual([e["kind"] for e in got["events"]], [ev.DOMAINS_PICKED, ev.HEARING_ASKED])
        self.assertEqual(got["warnings"], [])

    def test_a_sentence_that_is_a_files_want_names_that_product(self):
        got = request.begin(BED["want"])
        self.assertEqual((got["flags_from"], got["draft"]["product"]), ("product", "care-bed"))

    def test_the_words_of_a_sentence_when_nothing_else_reads_it(self):
        got = request.begin("飲み物を運んで、お客さんのそばを歩く速さで動く配膳ロボ")
        flags = got["draft"]["flags"]
        self.assertEqual(got["flags_from"], "words")
        self.assertTrue(flags["liquid"] and flags["self_propelled"] and flags["near_people"] and not flags["rough"])
        self.assertEqual(got["draft"]["speed_class"], "walk")
        self.assertIn("「飲み物」", got["domains"]["flag_reasons"]["liquid"])
        self.assertGreater(len(got["domains"]["picks"]), 0)

    def test_a_sentence_no_word_matches_says_so(self):
        got = request.begin("あいうえお")
        self.assertFalse(any(got["draft"]["flags"].values()))
        self.assertTrue(any("読み取れませんでした" in w for w in got["warnings"]))

    def test_claude_gives_the_flags_and_the_reasons(self):
        first = request.begin("何かを運ぶ", picker=claude_like("strength"))
        self.assertEqual((first["mode"], first["flags_from"], first["cost_usd"]), ("claude", "claude", 0.12))
        picks = {p["domain"]: p for p in first["domains"]["picks"]}
        self.assertTrue(picks["strength"]["reason"].startswith("Claude の理由"))  # the code may add what cannot be run yet
        self.assertEqual(first["domains"]["flag_reasons"]["liquid"], "飲み物を運ぶので")

    def test_a_picker_that_fails_falls_back_to_the_words(self):
        def broken(want):
            raise RuntimeError("no key")

        got = request.begin("飲み物を運ぶロボ", picker=broken)
        self.assertEqual((got["mode"], got["flags_from"]), ("code", "words"))
        self.assertTrue(any("no key" in w for w in got["warnings"]))

    def test_the_persons_flags_come_first(self):
        picker = claude_like("strength")
        got = request.begin("何か", flags={"battery": True}, picker=picker)
        self.assertEqual((got["flags_from"], picker.calls), ("given", []))
        self.assertTrue(got["draft"]["flags"]["battery"])

    def test_bad_requests(self):
        for kwargs in ({"want": ""}, {"want": 3}, {"want": "x", "product": "日本語"}, {"want": "x", "limits": {"x": 1}}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(IntakeError):
                    request.begin(**kwargs)


class ReviseTest(unittest.TestCase):
    def test_a_chip_changes_the_domains_and_keeps_claudes_reasons_for_the_rest(self):
        first = request.begin("何かを運ぶ", picker=claude_like("strength"))
        lit = [p["domain"] for p in first["domains"]["picks"]]
        for flag in FLAGS:  # the first chip whose change lights or darkens a domain
            flags = dict(first["draft"]["flags"], **{flag: not first["draft"]["flags"][flag]})
            second = request.revise(first["draft"], flags=flags)
            if [p["domain"] for p in second["domains"]["picks"]] != lit:
                break
        else:
            self.fail("no chip changes the domains")
        self.assertEqual(second["flags_from"], "given")
        picks = {p["domain"]: p for p in second["domains"]["picks"]}
        self.assertTrue(picks["strength"]["reason"].startswith("Claude の理由"))  # strength stays lit with Claude's reason

    def test_taking_a_card_off_moves_it_to_off(self):
        first = request.begin(ROBOT["want"], product="serving-robot")
        domain = first["domains"]["picks"][0]["domain"]
        second = request.revise(first["draft"], exclude=[domain])
        self.assertNotIn(domain, [p["domain"] for p in second["domains"]["picks"]])
        self.assertIn(domain, [o["domain"] for o in second["domains"]["off"]])

    def test_only_a_few_things_can_be_changed(self):
        first = request.begin("x", flags={})
        with self.assertRaises(IntakeError):
            request.revise(first["draft"], want="y")
        with self.assertRaises(IntakeError):
            request.revise({"want": "x"})  # not a draft
        with self.assertRaises(IntakeError):
            request.revise(first["draft"], flags=[])


class FinishTest(unittest.TestCase):
    def test_answers_settle_the_hearing_and_make_a_valid_file(self):
        first = request.begin(BED["want"], product="care-bed")
        done = request.finish(first["draft"], BED_ANSWERS, "よろしく")
        self.assertTrue(done["ready"])
        rf = done["requirements_file"]
        self.assertEqual(contract.errors("requirements_file", rf), [])
        by = {r["key"]: r for r in rf["requirements"]}
        self.assertEqual((by["Q40"]["source"], by["Q40"]["value"]), ("hearing", 1700))
        self.assertEqual(by["Q60"]["source"], "default")
        self.assertEqual({k: rf.get(k) for k in request.LIMIT_KEYS}, {k: BED.get(k) for k in request.LIMIT_KEYS})  # the file's, if any
        self.assertNotIn("tests", rf)
        self.assertEqual([e["kind"] for e in done["events"]], [ev.DOMAINS_PICKED, ev.HEARING_ASKED, ev.HEARING_ANSWERED])  # A1 and A2 as a run draws them
        self.assertEqual(done["events"][-1]["data"]["note"], "よろしく")

    def test_a_required_item_without_an_answer_is_not_ready(self):
        first = request.begin(BED["want"], product="care-bed")
        done = request.finish(first["draft"])
        self.assertFalse(done["ready"])
        self.assertIn("Q30", done["next"])
        self.assertNotIn("Q03", done["next"])  # asked, but not required

    def test_limits_of_the_person_replace_the_files(self):
        first = request.begin(BED["want"], product="care-bed", limits={"round_limit": 2, "cost_limit_usd": 0.5})
        rf = request.finish(first["draft"], BED_ANSWERS)["requirements_file"]
        self.assertEqual((rf["round_limit"], rf["cost_limit_usd"]), (2, 0.5))

    def test_cards_taken_off_leave_their_tests_out(self):
        first = request.begin(BED["want"], product="care-bed")
        domain = first["domains"]["picks"][0]["domain"]
        gone = set(first["domains"]["picks"][0]["test_ids"])
        second = request.revise(first["draft"], exclude=[domain])
        tests = request.finish(second["draft"], BED_ANSWERS)["requirements_file"]["tests"]
        kept = {t for p in second["domains"]["picks"] for t in p["test_ids"]}
        self.assertTrue(kept <= set(tests))
        self.assertFalse((gone - kept - set(second["domains"]["assembly_test_ids"])) & set(tests))
        self.assertEqual(len(tests), len(set(tests)))

    def test_the_demo_vehicle_keeps_what_its_file_says_a_run_needs(self):
        own = load_product("offroad-4w")
        answers, _ = load_answers(answers_file("offroad-4w"))
        first = request.begin(own["want"], product="offroad-4w")
        rf = request.finish(first["draft"], answers)["requirements_file"]
        self.assertEqual((rf["design_file"], rf["tests"]), (own["design_file"], own["tests"]))
        taken = request.revise(first["draft"], exclude=["handling"])
        rf = request.finish(taken["draft"], answers)["requirements_file"]
        gone = set(first["domains"]["picks"][0]["test_ids"])
        self.assertEqual(first["domains"]["picks"][0]["domain"], "handling")
        self.assertEqual(rf["tests"], [t for t in own["tests"] if t not in gone])
        self.assertEqual(contract.errors("requirements_file", rf), [])

    def test_a_test_the_file_lists_but_the_flags_do_not_pick_stays_unless_its_own_domain_goes(self):
        from unittest import mock

        own = copy.deepcopy(load_product("offroad-4w"))
        own["tests"] = [t for t in own["tests"] if t != "R1s"] + ["R1s"]  # listed by the file; the flags never pick it (applies_if: not always)
        with mock.patch.object(request, "load_product", return_value=own):
            first = request.begin(own["want"], product="offroad-4w")
            answers, _ = load_answers(answers_file("offroad-4w"))
            kept = request.finish(request.revise(first["draft"], exclude=["handling"])["draft"], answers)["requirements_file"]["tests"]
            self.assertIn("R1s", kept)  # handling was taken off; R1s belongs to strength
            self.assertNotIn("S1", kept)
            gone = request.finish(request.revise(first["draft"], exclude=["strength"])["draft"], answers)["requirements_file"]["tests"]
            self.assertNotIn("R1s", gone)  # strength was taken off: its test goes with it
            self.assertNotIn("R1", gone)

    def test_a_product_nobody_has_a_file_for(self):
        first = request.begin("飲み物を運ぶロボ", product="my-robot")
        done = request.finish(first["draft"], {"Q40": 20, "Q50": 1.0, "Q30": "two_cargo"})
        self.assertEqual(done["requirements_file"]["product"], "my-robot")
        self.assertEqual(contract.errors("requirements_file", done["requirements_file"]), [])


class HandlersTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "_requests"

    def tearDown(self):
        self._tmp.cleanup()

    def test_request_then_a_chip_then_the_answer_writes_the_files_hiraku_run_takes(self):
        first = request.handle_request({"want": BED["want"], "product": "care-bed"})
        second = request.handle_request({"draft": first["draft"], "flags": first["draft"]["flags"]})
        done = request.handle_answer({"draft": second["draft"], "answers": BED_ANSWERS}, self.dir, now=NOW)
        self.assertTrue(done["ready"])
        self.assertEqual(done["request_id"], "20261103-140000-care-bed")
        self.assertEqual(Path(done["folder"]), self.dir / done["request_id"])
        self.assertEqual(load_requirements(Path(done["path"])), done["requirements_file"])  # folder/requirements.yaml
        events = json.loads(Path(done["intake_path"]).read_text(encoding="utf-8"))  # folder/intake.json
        builtin = len(knowledge.load_builtin())
        self.assertEqual([e["kind"] for e in events],  # the contract's order, the built-in knowledge included
                         [ev.DOMAINS_PICKED] + [ev.KNOWLEDGE_IMPORTED] * builtin + [ev.HEARING_ASKED, ev.HEARING_ANSWERED])
        answered = {a["key"]: a for a in events[-1]["data"]["answers"]}
        by = {r["key"]: r for r in done["requirements_file"]["requirements"]}
        self.assertTrue(answered and all(by[k] == a for k, a in answered.items()))  # project.requirements holds what was said
        again = request.handle_answer({"draft": second["draft"], "answers": BED_ANSWERS}, self.dir, now=NOW)
        self.assertEqual(again["request_id"], "20261103-140000-care-bed-2")  # nothing is overwritten

    def test_nothing_is_written_while_a_required_answer_is_missing(self):
        first = request.handle_request({"want": BED["want"], "product": "care-bed"})
        done = request.handle_answer({"draft": first["draft"]}, self.dir, now=NOW)
        self.assertFalse(done["ready"])
        self.assertNotIn("request_id", done)
        self.assertFalse(self.dir.exists())

    def test_bad_bodies(self):
        for call in (lambda: request.handle_request([]), lambda: request.handle_request({"want": None}),
                     lambda: request.handle_answer({"draft": None}, self.dir),
                     lambda: request.handle_answer({"draft": {"want": "x", "flags": {}}, "answers": []}, self.dir)):
            with self.assertRaises(IntakeError):
                call()

    def test_save_refuses_what_is_not_ready_and_a_file_that_breaks_the_contract(self):
        with self.assertRaises(IntakeError):
            save(intake(BED["want"], BED["flags"], product="care-bed"), self.dir / "a")  # Q30 and Q40 unanswered
        ready = intake(BED["want"], BED["flags"], product="care-bed", answers=BED_ANSWERS)
        with self.assertRaises(IntakeError):
            save(ready, self.dir / "b", requirements_file={"want": "x"})
        self.assertFalse(self.dir.exists())
        req_path, events_path = save(ready, self.dir / "c")
        self.assertEqual([p.name for p in (req_path, events_path)], ["requirements.yaml", "intake.json"])


class AddedTestsTest(unittest.TestCase):
    """What Claude adds to the code's choice (add_tests) reaches the draft, the picks and the requirements file's tests."""

    @staticmethod
    def picker(added, reason=""):
        def pick(want):
            return {"flags": ROBOT["flags"], "speed_class": "walk", "flag_reasons": {}, "reasons": {}, "shown_names": {},
                    "added_tests": added, "no_addition_reason": reason, "cost_usd": 0.1}
        return pick

    def left_out(self, built=True):
        """A catalog test the choice does not have: one a run can build (built) or one no builder makes."""
        first = request.begin("飲み物を運ぶロボ", product="free-robot", flags=ROBOT["flags"], speed_class="walk")
        have = {t for p in first["domains"]["picks"] for t in p["test_ids"]} | set(first["domains"]["assembly_test_ids"])
        from hiraku.catalog import load_catalog
        from hiraku.intake.domains import has_builder

        catalog = load_catalog()
        return next(t["id"] for t in catalog.tests if t["id"] not in have and has_builder(catalog, t["id"], None) == built)

    def test_an_added_test_is_in_the_picks_and_the_draft_keeps_it(self):
        tid = self.left_out()
        added = [{"test_id": tid, "reason": "運ぶものが倒れないかを見る"}]
        first = request.begin("飲み物を運ぶロボ", picker=self.picker(added))
        self.assertEqual(first["draft"]["added_tests"], added)
        self.assertEqual(first["domains"]["added_tests"], added)
        self.assertIn(tid, [t for p in first["domains"]["picks"] for t in p["test_ids"]] + first["domains"]["assembly_test_ids"])
        again = request.revise(first["draft"], exclude=[])
        self.assertEqual(again["domains"]["added_tests"], added)  # a change of the draft keeps what Claude added

    def test_the_file_of_a_free_request_lists_the_whole_choice_with_the_added_test(self):
        tid = self.left_out()
        first = request.begin("飲み物を運ぶロボ", picker=self.picker([{"test_id": tid, "reason": "要る"}]))
        done = request.finish(first["draft"], None, None)
        self.assertIn(tid, done["requirements_file"]["tests"])
        plain = request.begin("飲み物を運ぶロボ", picker=self.picker([], "足すものは無い"))
        self.assertEqual(plain["domains"]["no_addition_reason"], "足すものは無い")
        self.assertNotIn("tests", request.finish(plain["draft"], None, None)["requirements_file"])  # nothing added: the catalog chooses

    def test_an_added_test_no_builder_makes_is_not_listed_in_the_file_but_stays_in_the_choice(self):
        tid = self.left_out(built=False)
        first = request.begin("飲み物を運ぶロボ", picker=self.picker([{"test_id": tid, "reason": "要る"}]))  # an older draft may hold it
        self.assertNotIn(tid, [t for p in first["domains"]["picks"] for t in p["test_ids"]] + first["domains"]["assembly_test_ids"])
        tests = request.finish(first["draft"], None, None)["requirements_file"].get("tests") or []
        self.assertNotIn(tid, tests)  # a test with no builder is not in the choice and not in the file: it would refuse every v1
        self.assertEqual(tests, [])  # every one added was dropped: no tests at all, the run's own choice stands

    def test_a_known_products_file_keeps_its_tests_and_gets_the_added_one(self):
        own = load_product("offroad-4w")["tests"]
        tid = next(t for t in ("C1", "F1", "T1", "S2", "R4") if t not in own)
        first = request.begin("x", product="offroad-4w")
        draft = dict(first["draft"], added_tests=[{"test_id": tid, "reason": "要る"}])
        tests = request.finish(draft, None, None)["requirements_file"]["tests"]
        self.assertEqual(tests[:len(own)], own)
        self.assertIn(tid, tests)


class ClaudePickerTest(unittest.TestCase):
    def test_it_takes_the_pick_and_claudes_own_arguments(self):
        record = {"flags": {"liquid": True}, "speed_class": "walk", "picks": []}
        run = PickRun(record=record, calls=[{"reasons": {"strength": "r"}, "shown_names": {"strength": "強度"}, "flag_reasons": {"liquid": "f"}}],
                      cost_usd=0.2)
        with mock.patch("hiraku.intake.agent.pick_with_claude", return_value=run) as pick:
            got = request.claude_picker("x")
        pick.assert_called_once_with("x")
        self.assertEqual(got, {"flags": {"liquid": True}, "speed_class": "walk", "flag_reasons": {"liquid": "f"},
                               "reasons": {"strength": "r"}, "shown_names": {"strength": "強度"}, "added_tests": [], "no_addition_reason": "", "cost_usd": 0.2})

    def test_no_pick_is_an_error(self):
        with mock.patch("hiraku.intake.agent.pick_with_claude", return_value=PickRun(error="budget")):
            with self.assertRaises(IntakeError):
                request.claude_picker("x")


class GuessTest(unittest.TestCase):
    def test_speed_classes(self):
        self.assertEqual(guess_flags("最高 40 km/h の車")[2], "vehicle")
        self.assertEqual(guess_flags("最高 5 km/h")[2], "walk")  # 2 m/s is 7.2 km/h
        self.assertEqual(guess_flags("歩く速さで動く")[2], "walk")
        self.assertEqual(guess_flags("据え置きの棚")[2], "static")
        self.assertIsNone(guess_flags("何か")[2])

    def test_every_flag_it_sets_has_a_reason(self):
        flags, reasons, _ = guess_flags("電動の介護ベッド。背上げ・膝上げつき")
        self.assertEqual(set(flags), set(reasons))
        self.assertTrue({"lean", "electric", "pinch", "near_people"} <= set(flags))


if __name__ == "__main__":
    unittest.main()
