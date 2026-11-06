import unittest
from unittest import mock

from hiraku import contract
from hiraku.intake import IntakeError, events as ev, tools, tradeoff
from hiraku.contract.protocols import ToolError
from tests.intake import test_tools
from tests.intake.test_tools import ToolsTestCase, call, make_ctx

LOAD_CASES = [
    {"load_case_id": "P1-run", "title": "走って当たる", "criterion": {"quantity": "utilisation", "op": "<=", "limit": {"value": 1.0, "unit": "1"}, "basis": "x"}},
    {"load_case_id": "M1", "title": "質量", "criterion": {"quantity": "mass_kg", "op": "<=", "limit": {"value": 500.0, "unit": "kg"}, "basis": "x"}},
]
# test numbers (not results of a run)
ANALYSES = {"P1-run": {"analysis_id": "ana-0001", "results": [{"quantity": "utilisation", "value": 0.8, "unit": "1"}]},
            "M1": {"analysis_id": "ana-0002", "results": [{"quantity": "mass_kg", "value": 400.0, "unit": "kg"}]}}
TERMS = {"P1-run": [{"name": "utilisation to variables.tower_t", "value": 3.0, "unit": "mm", "share": -1.2}],
         "M1": [{"name": "mass_kg to variables.tower_t", "value": 3.0, "unit": "mm", "share": 0.8}]}


def fake_collector(ctx, paths, only):
    """What hiraku.tools gathers from a run (the sensitivities, the results now, the analyses they come from)."""
    sens, meta, bases = {}, {}, {}
    for lc in LOAD_CASES:
        q = lc["criterion"]["quantity"]
        if only and q not in only:
            continue
        sens[q] = TERMS[lc["load_case_id"]]
        value = ANALYSES[lc["load_case_id"]]["results"][0]
        meta[q] = {"label": lc["title"], "unit": value["unit"], "lower_is_better": True}
        bases[q] = {"value": value["value"], "record_id": ANALYSES[lc["load_case_id"]]["analysis_id"]}
    return {"sens": sens, "meta": meta, "bases": bases, "skipped": []}


def patched():
    return [mock.patch.object(tradeoff, "collector", fake_collector)]


class FindTradeoffsToolTest(ToolsTestCase):
    def run_tool(self, ctx, **args):
        ps = patched()
        for p in ps:
            p.start()
        try:
            return call("find_tradeoffs", ctx, **args)
        finally:
            for p in ps:
                p.stop()

    def test_a_pair_comes_back_in_the_contracts_shape_with_numbers_from_the_records(self):
        ctx = make_ctx(self.root)
        got = self.run_tool(ctx, variables=["tower_t"])
        self.assertEqual(len(got["tradeoffs"]), 1)
        q = got["tradeoffs"][0]
        self.assertEqual(contract.errors("tradeoff_question", q), [])
        self.assertEqual(q["variable"], "variables.tower_t")
        self.assertEqual((q["a"]["name"], q["a"]["now"], q["a"]["after"], q["a"]["record_id"]), ("走って当たる", 0.8, 0.704, "ana-0001"))
        self.assertEqual((q["b"]["name"], q["b"]["now"], q["b"]["after"], q["b"]["unit"]), ("質量", 400.0, 432.0, "kg"))
        self.assertIn("どちらを優先しますか", q["text"])

    def test_the_collectors_variable_labels_name_the_moved_number(self):
        def collector(ctx, paths, only):
            return dict(fake_collector(ctx, paths, only), variable_labels={"tower_t": "塔の板厚"})

        with mock.patch.object(tradeoff, "collector", collector):
            q = call("find_tradeoffs", make_ctx(self.root), variables=["tower_t"])["tradeoffs"][0]
        self.assertTrue(q["text"].startswith("塔の板厚 (いま 3 mm) を"), q["text"])
        self.assertEqual(q["variable"], "variables.tower_t")  # the id and the path stay

    def test_a_quantity_can_be_left_out(self):
        got = self.run_tool(make_ctx(self.root), variables=["tower_t"], quantities=["mass_kg"])
        self.assertEqual(got["tradeoffs"], [])
        self.assertIn("見つかりませんでした", got["next"])

    def test_without_a_collector_the_tool_says_so(self):
        with mock.patch.object(tradeoff, "collector", None):
            with self.assertRaises(ToolError):
                call("find_tradeoffs", make_ctx(self.root), variables=["tower_t"])

    def test_the_tool_is_in_the_contract(self):
        self.assertIn("find_tradeoffs", tools.TOOLS)
        self.assertEqual(tools.TOOLS["find_tradeoffs"]["input_schema"]["additionalProperties"], False)


class AskTradeoffTest(ToolsTestCase):
    screen_answers = test_tools.AskUserToolTest.screen_answers

    def pair(self, ctx):
        ps = patched()
        for p in ps:
            p.start()
        try:
            return call("find_tradeoffs", ctx, variables=["tower_t"])["tradeoffs"][0]
        finally:
            for p in ps:
                p.stop()

    def test_an_id_that_find_tradeoffs_did_not_return_is_refused(self):
        ctx = make_ctx(self.root)
        with self.assertRaises(ToolError):
            call("ask_user", ctx, tradeoff_ids=["variables.x:a:b"])

    def test_the_question_is_asked_and_the_screens_answer_is_recorded(self):
        ctx = make_ctx(self.root, product="serving-robot")
        pair = self.pair(ctx)
        got = self.screen_answers(ctx, {"answers": {}, "tradeoff_answers": [{"tradeoff_id": pair["tradeoff_id"], "choice": "favor_a", "note": "軽さより強さ"}]},
                                  tradeoff_ids=[pair["tradeoff_id"]])
        self.assertEqual(got["tradeoff_choices"], {pair["tradeoff_id"]: "favor_a"})
        self.assertNotIn("tradeoffs_waiting", got)
        events = ctx.store.events()
        asked = [e for e in events if e["kind"] == ev.HEARING_ASKED][-1]["data"]
        answered = [e for e in events if e["kind"] == ev.HEARING_ANSWERED][-1]["data"]
        self.assertEqual(asked["tradeoff_questions"][0]["tradeoff_id"], pair["tradeoff_id"])
        self.assertEqual(answered["tradeoff_answers"], [{"tradeoff_id": pair["tradeoff_id"], "choice": "favor_a", "note": "軽さより強さ"}])
        self.assertEqual([e for e in events if e["kind"] in (ev.HEARING_ASKED, ev.HEARING_ANSWERED)
                          and contract.errors("event", {**e}) ], [])

    def test_no_answer_leaves_it_waiting(self):
        ctx = make_ctx(self.root, product="serving-robot")
        pair = self.pair(ctx)
        got = call("ask_user", ctx, tradeoff_ids=[pair["tradeoff_id"]])
        self.assertEqual(got["tradeoffs_waiting"], [pair["tradeoff_id"]])

    def test_the_screen_cannot_answer_a_question_that_was_not_asked(self):
        from hiraku.intake import hearing

        ctx = make_ctx(self.root, product="serving-robot")
        pair = self.pair(ctx)
        hearing.write_request(ctx.store.path, {"tradeoff_questions": [pair]})
        with self.assertRaises(IntakeError):
            hearing.save_answer(ctx.store.path, {"answers": {}, "tradeoffAnswers": [{"tradeoff_id": "other", "choice": "favor_a"}]})
        path = hearing.save_answer(ctx.store.path, {"answers": {}, "tradeoffAnswers": [{"tradeoff_id": pair["tradeoff_id"], "choice": "keep_both"}]})
        self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
