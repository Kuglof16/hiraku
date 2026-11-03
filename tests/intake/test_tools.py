import json
import tempfile
import unittest
from pathlib import Path

from hiraku import contract
from hiraku.contract.protocols import ToolError
from hiraku.intake import events as ev
from hiraku.intake.tools import TOOLS
from hiraku.store import RunStore
from hiraku.tools.context import RunContext
from tests.intake.designs import mini_design

FIXTURES = Path(__file__).with_name("fixtures")


def make_ctx(root: Path, product="care-bed", flags=None, requirements=None, with_design=True):
    flags = flags if flags is not None else {"lean": True, "electric": True, "cargo": True, "standing": True, "pinch": True}
    fields = {"want": "介護ベッド", "product": product, "flags": flags, "speed_class": "static", "round_limit": 1,
              "cost_limit_usd": 1.0, "requirements": requirements or [], "domain_picks": [], "load_cases": [], "agents": []}
    store = RunStore.create(fields, root=root, slug="intake-test")
    if with_design:
        store.write_design(mini_design())
    return RunContext(store=store, formulas=None, tools={})


def call(name, ctx, **args):
    return TOOLS[name]["function"](ctx, args, "lead")


class ToolsTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def kinds(self, ctx):
        return [e["kind"] for e in ctx.store.events()]


class DefinitionsTest(unittest.TestCase):
    def test_every_tool_has_a_description_a_schema_and_a_function(self):
        self.assertEqual(sorted(TOOLS), ["ask_user", "import_knowledge", "pick_domains", "rule_check"])
        for name, t in TOOLS.items():
            with self.subTest(tool=name):
                self.assertTrue(t["description"])
                self.assertEqual(t["input_schema"]["type"], "object")
                self.assertFalse(t["input_schema"]["additionalProperties"])
                self.assertTrue(callable(t["function"]))

    def test_the_names_match_the_contract_list(self):
        later = set(contract.MCP_TOOLS_LATER)
        for name in ("pick_domains", "ask_user", "import_knowledge"):
            self.assertIn(name, later)

    def test_input_schemas_are_json_schema(self):
        import jsonschema

        for name, t in TOOLS.items():
            with self.subTest(tool=name):
                jsonschema.Draft202012Validator.check_schema(t["input_schema"])

    def test_the_input_schemas_are_the_contracts(self):
        from hiraku.intake.tools import contract_input

        defs = contract.schema()["$defs"]
        for name, t in TOOLS.items():
            with self.subTest(tool=name):
                self.assertIn(f"tool_input.{name}", defs)
                self.assertEqual(t["input_schema"], contract_input(name))
                self.assertNotIn("$ref", json.dumps(t["input_schema"]))  # inlined: the schema stands alone
                self.assertNotIn("description", {k for k in t["input_schema"] if k == "description"})

    def test_what_the_tools_take_is_checked_by_the_schema(self):
        import jsonschema

        ok = {"pick_domains": {"want": "机", "flags": {"standing": True}},
              "ask_user": {"answers": {"Q40": 30}, "wait_seconds": 5},
              "import_knowledge": {"filename": "a.md", "text": "質量は 23 kg 以下", "formulas": {"c1": {"check": "mass_kg <= 23"}}},
              "rule_check": {"design_version": 1, "disable": ["AS-01"]}}
        bad = {"pick_domains": {"want": "机"},  # flags are required
               "ask_user": {"wait_seconds": 9999},
               "import_knowledge": {"formulas": {"x": {"check": "a"}}},  # not a sentence number
               "rule_check": {"design_version": 0}}
        for name, args in ok.items():
            self.assertEqual(list(jsonschema.Draft202012Validator(TOOLS[name]["input_schema"]).iter_errors(args)), [], name)
        for name, args in bad.items():
            self.assertTrue(list(jsonschema.Draft202012Validator(TOOLS[name]["input_schema"]).iter_errors(args)), name)

    def test_the_flag_questions_are_in_the_pick_description(self):
        d = TOOLS["pick_domains"]["description"]
        for word in ("self_propelled (走)", "liquid (液)", "vehicle (車)"):
            self.assertIn(word, d)


class PickDomainsToolTest(ToolsTestCase):
    def test_it_returns_the_record_and_says_what_is_missing(self):
        ctx = make_ctx(self.root)
        got = call("pick_domains", ctx, want="飲み物を運ぶ配膳ロボ",
                   flags={"self_propelled": True, "liquid": True, "near_people": True, "standing": True},
                   speed_class="walk", reasons={"aero": "運ぶ飲み物がこぼれるかを見る"}, shown_names={"aero": "液体のこぼれ"})
        by = {p["domain"]: p for p in got["picks"]}
        self.assertTrue(by["aero"]["reason"].startswith("運ぶ飲み物がこぼれるかを見る"))  # kept; the code may add what cannot be run yet
        self.assertEqual(by["aero"]["shown_name"], "液体のこぼれ")
        self.assertIn("aero", [p["domain"] for p in got["picks"]])
        self.assertNotIn("aero", got["reasons_missing"])
        self.assertIn("crash", got["reasons_missing"])
        self.assertIn("next", got)

    def test_the_same_pick_is_recorded_once_and_a_change_is_recorded_again(self):
        if not ev.known(ev.DOMAINS_PICKED):
            self.skipTest("the contract has no domains.picked")
        ctx = make_ctx(self.root)
        args = dict(want="机", flags={"standing": True, "cargo": True}, speed_class="static")
        first = call("pick_domains", ctx, **args)
        again = call("pick_domains", ctx, **args)
        self.assertTrue(first["recorded"] and again["recorded"])
        self.assertNotIn("unchanged", first)
        self.assertIn("同じ", again["unchanged"])
        self.assertEqual(self.kinds(ctx).count(ev.DOMAINS_PICKED), 1)
        call("pick_domains", ctx, **{**args, "flags": {"standing": True, "cargo": True, "near_people": True}})
        self.assertEqual(self.kinds(ctx).count(ev.DOMAINS_PICKED), 2)  # the flags changed

    def test_it_records_when_the_contract_has_the_kind(self):
        ctx = make_ctx(self.root)
        got = call("pick_domains", ctx, want="机", flags={"standing": True, "cargo": True}, speed_class="static")
        if ev.known(ev.DOMAINS_PICKED):
            self.assertTrue(got["recorded"], got.get("not_recorded"))
            self.assertIn(ev.DOMAINS_PICKED, self.kinds(ctx))
        else:
            self.assertFalse(got["recorded"])
            self.assertIn("not in the contract", got["not_recorded"])
            self.assertNotIn(ev.DOMAINS_PICKED, self.kinds(ctx))

    def test_bad_input_is_a_tool_error_that_says_what_to_send(self):
        ctx = make_ctx(self.root)
        with self.assertRaises(ToolError) as cm:
            call("pick_domains", ctx, want="x", flags={"fly": True})
        self.assertIn("fly", str(cm.exception))

    def test_it_works_without_a_run(self):
        got = call("pick_domains", None, want="ギター", flags={"vibration": True, "preload": True})
        self.assertEqual([p["domain"] for p in got["picks"]], ["strength", "nvh"])
        self.assertFalse(got["recorded"])


class AskUserToolTest(ToolsTestCase):
    def test_a_product_the_run_knows_gets_its_own_values_as_defaults(self):
        ctx = make_ctx(self.root, product="care-bed")
        got = call("ask_user", ctx)
        keys = [q["req_key"] for q in got["questions"]]
        self.assertEqual(keys, ["Q03", "Q11", "Q19", "Q20", "Q30", "Q34", "Q40", "Q56", "Q61", "Q80"])
        self.assertFalse(got["ready"])
        self.assertIn("Q30", got["next"])
        q19 = next(q for q in got["questions"] if q["req_key"] == "Q19")
        self.assertEqual(q19["default"]["value"], 40)
        self.assertTrue(any(r["key"] == "Q60" and r["source"] == "default" for r in got["answers"]))
        self.assertTrue(got["requirements_recorded"])  # joined project.requirements through the store's update_project
        in_project = {r["key"]: r for r in ctx.project["requirements"]}
        self.assertEqual(in_project["Q60"]["source"], "default")
        self.assertIn("project.updated", self.kinds(ctx))
        self.assertEqual(contract.errors("project", ctx.project), [])
        again = call("ask_user", ctx)  # what is in the project is not asked again; what has no value still is
        self.assertEqual([q["req_key"] for q in again["questions"]], ["Q03", "Q30", "Q40"])

    def test_answers_make_it_ready(self):
        ctx = make_ctx(self.root, product="care-bed")
        got = call("ask_user", ctx, answers={"Q30": "driver", "Q40": {"value": 1700, "unit": "N"}},
                   answers_file="data/requirements/products/care-bed.answers.yaml")
        self.assertTrue(got["ready"])
        by = {r["key"]: r for r in got["answers"]}
        self.assertEqual(by["Q40"]["value"], 1700)  # the call's answers win over the file's
        self.assertEqual(by["Q03"]["source"], "hearing")  # from the file

    def test_asking_again_records_the_questions_and_the_empty_answers_once(self):
        if not (ev.known(ev.HEARING_ASKED) and ev.known(ev.HEARING_ANSWERED)):
            self.skipTest("the contract has no hearing events")
        ctx = make_ctx(self.root, product="care-bed")
        count = lambda: (self.kinds(ctx).count(ev.HEARING_ASKED), self.kinds(ctx).count(ev.HEARING_ANSWERED))  # noqa: E731
        first = call("ask_user", ctx)  # the defaults join the run's requirements: only Q30 and Q40 are still open
        self.assertNotIn("unchanged", first)
        self.assertEqual(count(), (1, 1))
        second = call("ask_user", ctx)  # asks the two that are left: a change
        self.assertNotIn("unchanged", second)
        self.assertEqual(count(), (2, 2))
        third = call("ask_user", ctx)  # the same two questions and the same (empty) answers: nothing new
        self.assertIn(ev.HEARING_ASKED, third["unchanged"])
        self.assertIn(ev.HEARING_ANSWERED, third["unchanged"])
        self.assertEqual(count(), (2, 2))
        done = call("ask_user", ctx, answers={"Q30": "driver", "Q40": 1700})
        self.assertNotIn(ev.HEARING_ANSWERED, done.get("unchanged", ""))  # an answer is new
        self.assertEqual(count()[1], 3)

    HIT = {"rule_id": "U1-01", "parts": ["frame"], "text": "曲げの内側の半径が板厚より小さい。設計を直しますか、この規則を外しますか"}

    def known_rule(self):
        return {**self.HIT, "rule_id": "SM-01", "parts": ["floor"]}

    def test_rule_hits_are_asked_with_the_requirements_and_recorded_in_the_contracts_shape(self):
        ctx = make_ctx(self.root, product="care-bed")
        got = call("ask_user", ctx, rule_hits=[self.known_rule()])
        self.assertEqual([q["rule_id"] for q in got["rule_questions"]], ["SM-01"])
        self.assertEqual(got["rule_questions"][0]["choices"], ["revise_design", "drop_rule"])  # missing: both
        self.assertEqual(got["rules_waiting"], ["SM-01"])
        asked = next(e for e in ctx.store.events() if e["kind"] == ev.HEARING_ASKED)
        self.assertEqual(contract.errors("hearing_asked", asked["data"]), [])
        self.assertTrue(asked["data"]["questions"] and asked["data"]["rule_questions"])  # one asking for both
        self.assertIn("当たった規則", asked["text"])

    def test_only_rule_hits_leave_out_the_empty_questions(self):
        event = ev.asked_event({"questions": [], "rule_questions": [self.known_rule()]})
        self.assertNotIn("questions", event["data"])
        self.assertEqual(contract.errors("hearing_asked", event["data"]), [])
        self.assertIn("当たった規則の相談が 1 件", event["text"])
        self.assertNotIn("足りない要件", event["text"])
        both = ev.asked_event({"questions": [{"req_key": "Q30"}], "rule_questions": []})
        self.assertNotIn("rule_questions", both["data"])  # nothing to say about rules: the contract's old shape

    def test_bad_rule_hits_are_refused(self):
        ctx = make_ctx(self.root, product="care-bed")
        for bad in ([{"rule_id": "SM-01", "parts": ["floor"]}], [self.HIT], [{**self.known_rule(), "choices": ["wait"]}]):
            with self.subTest(bad=bad):
                with self.assertRaises(ToolError):
                    call("ask_user", ctx, rule_hits=bad)

    def test_the_person_drops_a_rule_and_rule_check_leaves_it_out_with_the_reason(self):
        import threading

        from hiraku.intake import hearing

        ctx = make_ctx(self.root, product="care-bed")
        before = call("rule_check", ctx)
        self.assertGreater(before["summary"]["rules"], 0)

        def person():
            for _ in range(100):
                if (ctx.store.path / hearing.REQUEST_FILE).exists():
                    hearing.save_answer(ctx.store.path, {"answers": {}, "rule_answers": [
                        {"rule_id": "SM-01", "choice": "drop_rule", "note": "この車に板金の曲げは無い"}]})
                    return
                import time

                time.sleep(0.05)

        t = threading.Thread(target=person)
        t.start()
        got = call("ask_user", ctx, rule_hits=[self.known_rule()], wait_seconds=5)
        t.join()
        self.assertEqual(got["rule_answers"], [{"rule_id": "SM-01", "choice": "drop_rule", "note": "この車に板金の曲げは無い"}])
        self.assertEqual(got["rules_dropped"], ["SM-01"])
        self.assertNotIn("rules_waiting", got)
        answered = [e for e in ctx.store.events() if e["kind"] == ev.HEARING_ANSWERED][-1]
        self.assertEqual(contract.errors("hearing_answered", answered["data"]), [])
        after = call("rule_check", ctx)
        self.assertEqual(after["summary"]["rules"], before["summary"]["rules"] - 1)
        self.assertEqual(after["dropped_rules"], [{"rule_id": "SM-01", "reason": "この車に板金の曲げは無い"}])
        checked = [e["data"]["rule_id"] for e in ctx.store.events() if e["kind"] == "rule_check.recorded"]
        self.assertEqual(checked.count("SM-01"), 1)  # only the first check, before it was dropped

    def test_a_person_who_chooses_to_revise_the_design_keeps_the_rule(self):
        import threading
        import time

        from hiraku.intake import hearing

        ctx = make_ctx(self.root, product="care-bed")

        def person():
            for _ in range(100):
                if (ctx.store.path / hearing.REQUEST_FILE).exists():
                    hearing.save_answer(ctx.store.path, {"answers": {}, "rule_answers": [{"rule_id": "SM-01", "choice": "revise_design"}]})
                    return
                time.sleep(0.05)

        t = threading.Thread(target=person)
        t.start()
        got = call("ask_user", ctx, rule_hits=[self.known_rule()], wait_seconds=5)
        t.join()
        self.assertEqual(got["rules_to_revise"], ["SM-01"])
        self.assertNotIn("dropped_rules", call("rule_check", ctx))

    def test_the_answer_of_the_screen_is_checked_against_the_rules_that_were_asked(self):
        import tempfile as tf

        from hiraku.intake import hearing
        from hiraku.intake.domains import IntakeError

        with tf.TemporaryDirectory() as d:
            hearing.write_request(Path(d), {"rule_questions": [{**self.known_rule(), "choices": ["drop_rule"]}]})
            for bad in ([{"rule_id": "FS-01", "choice": "drop_rule"}], [{"rule_id": "SM-01", "choice": "revise_design"}],
                        [{"rule_id": "SM-01", "choice": "x"}], "SM-01", [{"rule_id": "SM-01"}]):
                with self.subTest(bad=bad), self.assertRaises(IntakeError):
                    hearing.save_answer(Path(d), {"answers": {}, "rule_answers": bad})
            path = hearing.save_answer(Path(d), {"answers": {}, "ruleAnswers": [{"rule_id": "SM-01", "choice": "drop_rule", "note": "n"}]})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["rule_answers"], [{"rule_id": "SM-01", "choice": "drop_rule", "note": "n"}])

    def test_the_answers_file_may_be_named_by_its_file_name_or_the_product(self):
        for i, name in enumerate(("care-bed.answers.yaml", "care-bed", "data/requirements/products/care-bed.answers.yaml")):
            with self.subTest(name=name):
                got = call("ask_user", make_ctx(self.root / f"r{i}", product="care-bed"), answers_file=name)
                self.assertTrue(got["ready"])
                self.assertEqual({r["key"]: r for r in got["answers"]}["Q30"]["value"], "driver")

    def test_a_refused_or_missing_answers_file_says_what_to_pass(self):
        ctx = make_ctx(self.root)
        with self.assertRaises(ToolError) as cm:
            call("ask_user", ctx, answers_file="README.md")
        self.assertIn("data/requirements/products/<名前>.answers.yaml", str(cm.exception))
        with self.assertRaises(ToolError) as cm:
            call("ask_user", ctx, answers_file="nobody.answers.yaml")
        self.assertIn("care-bed.answers.yaml", str(cm.exception))  # what there is

    def test_the_answers_file_must_be_one_of_the_products(self):
        ctx = make_ctx(self.root)
        for bad in ("README.md", "../../etc/passwd", "data/requirements/offroad-4w.yaml"):
            with self.subTest(bad=bad):
                with self.assertRaises(ToolError):
                    call("ask_user", ctx, answers_file=bad)

    def test_events_when_the_contract_has_them(self):
        ctx = make_ctx(self.root)
        got = call("ask_user", ctx, answers={"Q30": "two_cargo", "Q40": 1})
        if ev.known(ev.HEARING_ASKED):
            self.assertTrue(got["recorded"], got.get("not_recorded"))
            k = self.kinds(ctx)
            self.assertLess(k.index(ev.HEARING_ASKED), k.index(ev.HEARING_ANSWERED))
        else:
            self.assertFalse(got["recorded"])

    def test_a_product_nobody_knows_has_only_the_items_defaults(self):
        ctx = make_ctx(self.root, product="made-up-thing", flags={"standing": True})
        got = call("ask_user", ctx)
        self.assertEqual([q["req_key"] for q in got["questions"]], ["Q03", "Q11", "Q34", "Q61", "Q80"])
        self.assertTrue(got["ready"])


class ImportKnowledgeToolTest(ToolsTestCase):
    def md(self):
        return (FIXTURES / "bending-notes.md").read_text(encoding="utf-8")

    def test_the_first_call_hands_back_what_needs_a_formula(self):
        ctx = make_ctx(self.root)
        got = call("import_knowledge", ctx, filename="bending-notes.md", text=self.md())
        self.assertEqual(got["doc_id"], "U1")
        self.assertEqual([n["cand_id"] for n in got["needs_formula"]], ["c3", "c5"])
        self.assertIn("mass_kg", [f["name"] for f in got["facts"]])
        self.assertIn("next", got)

    def test_the_second_call_with_formulas_finishes_it(self):
        ctx = make_ctx(self.root)
        call("import_knowledge", ctx, filename="bending-notes.md", text=self.md())
        got = call("import_knowledge", ctx, filename="bending-notes.md", text=self.md(), skip={"c3": "式にできない"},
                   formulas={"c5": {"check": "bottom_z_mm >= 100", "kinds": ["battery"]}})
        self.assertEqual(got["doc_id"], "U1")  # the same file updates its document
        self.assertEqual(got["needs_formula"], [])
        self.assertIn("U1-05", [r["rule_id"] for r in got["rules"]])
        if ev.known(ev.KNOWLEDGE_IMPORTED):
            docs = [e["data"]["doc"]["doc_id"] for e in ctx.store.events() if e["kind"] == ev.KNOWLEDGE_IMPORTED]
            self.assertEqual(docs.count("U1"), 2)  # the last event for an id wins
            self.assertEqual(docs.count("KN-SM"), 1)  # the built-in documents once

    def test_another_file_gets_the_next_document_number(self):
        ctx = make_ctx(self.root)
        call("import_knowledge", ctx, filename="a.txt", text="質量は 23 kg 以下")
        got = call("import_knowledge", ctx, filename="b.txt", text="質量は 10 kg 以下")
        self.assertEqual(got["doc_id"], "U2")

    def test_a_path_must_stay_inside_the_runs_knowledge_folder(self):
        ctx = make_ctx(self.root)
        folder = Path(ctx.store.path) / "knowledge"
        folder.mkdir()
        (folder / "rules.csv").write_text((FIXTURES / "rules.csv").read_text(encoding="utf-8"), encoding="utf-8")
        got = call("import_knowledge", ctx, path="knowledge/rules.csv")
        self.assertEqual(len(got["rules"]), 1)
        for bad in ("project.json", "knowledge/../project.json", "../x"):
            with self.subTest(bad=bad):
                with self.assertRaises(ToolError):
                    call("import_knowledge", ctx, path=bad)

    def test_the_same_path_twice_is_the_same_document_named_after_the_file(self):
        ctx = make_ctx(self.root)
        folder = Path(ctx.store.path) / "knowledge"
        folder.mkdir()
        (folder / "notes.md").write_text(self.md(), encoding="utf-8")
        first = call("import_knowledge", ctx, path="knowledge/notes.md")
        second = call("import_knowledge", ctx, path="knowledge/notes.md", skip={"c3": "x"},
                      formulas={"c5": {"check": "bottom_z_mm >= 100", "kinds": ["battery"]}})
        self.assertEqual((first["doc_id"], second["doc_id"]), ("U1", "U1"))
        self.assertEqual(second["needs_formula"], [])
        if ev.known(ev.KNOWLEDGE_IMPORTED):
            docs = [e["data"]["doc"] for e in ctx.store.events() if e["kind"] == ev.KNOWLEDGE_IMPORTED and e["data"]["doc"]["origin"] == "dropped"]
            self.assertEqual([d["filename"] for d in docs], ["notes.md", "notes.md"])  # the file's name, not the path

    def test_a_shift_jis_file_is_read(self):
        ctx = make_ctx(self.root)
        folder = Path(ctx.store.path) / "knowledge"
        folder.mkdir()
        (folder / "excel.csv").write_bytes("規則,式,分類\n座面の幅は 400 mm 以上,size_y_mm >= 400,人\n".encode("cp932"))
        got = call("import_knowledge", ctx, path="knowledge/excel.csv")
        self.assertEqual([r["text"] for r in got["rules"]], ["座面の幅は 400 mm 以上"])
        (folder / "binary.bin").write_bytes(b"\xff\xfe\x00\x80\x81")
        with self.assertRaises(ToolError):
            call("import_knowledge", ctx, path="knowledge/binary.bin")

    def test_nothing_to_read(self):
        with self.assertRaises(ToolError):
            call("import_knowledge", make_ctx(self.root))


class RuleCheckToolTest(ToolsTestCase):
    def test_it_applies_builtin_and_dropped_rules_to_the_latest_design(self):
        ctx = make_ctx(self.root, requirements=[{"key": "Q15", "name": "x", "value": 100, "unit": "mm", "source": "default", "trust": 3}])
        call("import_knowledge", ctx, filename="a.txt", text="質量は 40 kg 以下")
        got = call("rule_check", ctx)
        self.assertEqual(got["design_version"], 1)
        parts = {h["rule_id"]: h["parts"] for h in got["hits"]}
        self.assertEqual(parts["AS-01"], ["battery"])
        self.assertEqual(parts["HU-02"], ["seat"])
        self.assertEqual(parts["U1-01"], ["battery", "cargo"])  # 50 kg and 150 kg
        self.assertIn("SM-01", [n["rule_id"] for n in got["not_evaluable"]])
        if ev.known(ev.RULE_CHECK_RECORDED):
            self.assertTrue(got["recorded"], got.get("not_recorded"))
            events = [e for e in ctx.store.events() if e["kind"] == ev.RULE_CHECK_RECORDED]
            self.assertEqual(len(events), got["summary"]["rules"])  # always as events, the screen draws from them
            files = len(ctx.store.records("rule_check"))  # and as files once the run store writes this kind
            self.assertIn(files, (0, got["summary"]["rules"]))
            self.assertEqual([e["data"]["rule_check_id"] for e in events], [f"rchk-{i:04d}" for i in range(1, len(events) + 1)])
        else:
            self.assertFalse(got["recorded"])

    def test_disable_leaves_a_rule_out(self):
        ctx = make_ctx(self.root)
        got = call("rule_check", ctx, disable=["AS-01"])
        self.assertNotIn("AS-01", [h["rule_id"] for h in got["hits"]])
        with self.assertRaises(ToolError):
            call("rule_check", ctx, disable=["ZZ-99"])

    def test_without_a_design_it_says_so(self):
        ctx = make_ctx(self.root, with_design=False)
        with self.assertRaises(ToolError) as cm:
            call("rule_check", ctx)
        self.assertIn("設計", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
