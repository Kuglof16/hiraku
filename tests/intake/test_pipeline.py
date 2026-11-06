import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from hiraku import contract
from hiraku.intake import IntakeError, events as ev, hearing, load_product
from hiraku.intake.pipeline import intake, slug_for
from tests.intake.test_tools import ToolsTestCase, call, make_ctx


class PipelineTest(unittest.TestCase):
    def test_a_known_product_from_its_sentence_and_flags(self):
        req = load_product("serving-robot")
        got = intake(req["want"], req["flags"], "walk", product="serving-robot",
                     answers={"Q40": {"value": 40, "unit": "kg"}, "Q50": {"value": 1.2, "unit": "m/s"}})
        self.assertEqual(len(got.domains["picks"]), 7)
        self.assertTrue(got.ready)
        self.assertEqual(contract.errors("requirements_file", got.requirements_file), [])
        keys = {r["key"] for r in got.requirements_file["requirements"]}
        self.assertTrue({"Q40", "Q50", "Q54", "Q60", "Q62"} <= keys)  # the answers, and the product's own values as defaults
        kinds = [e["kind"] for e in got.events]
        self.assertEqual(kinds, [ev.DOMAINS_PICKED] + [ev.KNOWLEDGE_IMPORTED] * 10 + [ev.HEARING_ASKED, ev.HEARING_ANSWERED])  # the contract's order

    def test_a_run_started_from_a_requirements_file_asks_nothing_when_the_file_has_what_is_required(self):
        req = load_product("serving-robot")
        file_reqs = [r for r in req["requirements"] if r["key"] != "Q63"] + [
            {"key": "Q40", "name": "積載", "value": 40, "unit": "kg", "source": "user", "trust": 1},
            {"key": "Q50", "name": "最高速度", "value": 1.2, "unit": "m/s", "source": "user", "trust": 1},
            {"key": "Q58", "name": "最小回転半径の上限", "value": 3, "unit": "m", "source": "user", "trust": 1}]
        got = intake(req["want"], req["flags"], "walk", product="serving-robot", requirements=file_reqs, from_file=True)
        kinds = [e["kind"] for e in got.events]
        self.assertEqual(kinds, [ev.DOMAINS_PICKED] + [ev.KNOWLEDGE_IMPORTED] * 10 + [ev.HEARING_ANSWERED])  # no hearing.asked
        answered = got.events[-1]
        self.assertEqual(answered["text"], "要件のファイルのまま進みます (既定値で足した 1 件: Q63)")
        self.assertEqual([a["key"] for a in answered["data"]["answers"]], ["Q63"])
        merged = {r["key"]: r for r in got.requirements_file["requirements"]}
        self.assertEqual(merged["Q63"], answered["data"]["answers"][0])  # what project.requirements takes is what was said
        self.assertEqual(len(merged), len(file_reqs) + 1)
        self.assertEqual(contract.errors("event", {"seq": 1, "ts": "2026-11-03T10:00:00+09:00", "t": 1, "run_id": "r", **{k: answered[k] for k in ("kind", "agent", "text", "data")}}), [])

    def test_a_required_question_without_an_answer_is_not_ready(self):
        req = load_product("care-bed")
        got = intake(req["want"], req["flags"], "static", product="care-bed")
        self.assertFalse(got.ready)
        self.assertIn("Q40", got.prepared.unresolved)

    def test_a_sentence_nobody_has_a_file_for(self):
        got = intake("Standing desk", {"standing": True, "cargo": True}, "static", answers={"Q40": 30})
        self.assertEqual(got.requirements_file["product"], "standing-desk")
        self.assertEqual([p["domain"] for p in got.domains["picks"]], ["handling", "strength"])
        by = {r["key"]: r for r in got.requirements_file["requirements"]}
        self.assertEqual((by["Q40"]["source"], by["Q40"]["value"]), ("hearing", 30))
        self.assertNotIn("Q60", by)  # no value anywhere: left out, not made up
        self.assertEqual(slug_for("机を作りたい"), "product")

    def test_an_unknown_product_slug_is_refused(self):
        with self.assertRaises(IntakeError):
            intake("x", {}, product="nope")

    def test_what_the_request_already_says_is_not_asked(self):
        got = intake("Standing desk", {"standing": True, "cargo": True}, "static",
                     requirements=[{"key": "Q40", "name": "積載", "value": 30, "unit": "kg", "source": "user", "trust": 1}])
        self.assertNotIn("Q40", [q["req_key"] for q in got.prepared.asked["questions"]])

    def test_without_docs(self):
        got = intake("x", {"standing": True}, docs=False)
        self.assertNotIn(ev.KNOWLEDGE_IMPORTED, [e["kind"] for e in got.events])


class MailboxTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.run_path = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_save_answer_checks_and_writes_atomically(self):
        path = hearing.save_answer(self.run_path, {"answers": {"Q40": {"value": 1700, "unit": "N"}}, "note": "ok"})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")),
                         {"answers": {"Q40": {"value": 1700, "unit": "N"}}, "note": "ok", "use_defaults": True})
        self.assertEqual(list(self.run_path.glob("*.tmp")), [])

    def test_save_answer_takes_the_buttons_spelling(self):
        path = hearing.save_answer(self.run_path, {"answers": {}, "note": "", "useDefaults": False})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["use_defaults"], False)

    def test_save_answer_refuses_bad_bodies(self):
        for body in ({"stuff": 1}, {"answers": []}, {"answers": {"Q98": 1}}, {"answers": {"Q40": {"value": 1, "unit": "lbf"}}}):
            with self.subTest(body=body):
                with self.assertRaises(IntakeError):
                    hearing.save_answer(self.run_path, body)

    def test_wait_gives_up_at_the_deadline(self):
        t = [0.0]
        naps = []
        got = hearing.wait_for_answer(self.run_path, 2.0, poll_s=0.5, sleep=lambda s: (naps.append(s), t.__setitem__(0, t[0] + s)),
                                      clock=lambda: t[0])
        self.assertIsNone(got)
        self.assertEqual(sum(naps), 2.0)

    def test_read_waits_out_a_busy_file(self):
        hearing.save_answer(self.run_path, {"answers": {"Q40": 1}})
        real = Path.read_text
        busy = [2]

        def flaky(self_, *a, **k):  # Windows: the file is being replaced; open() says "Permission denied" for a moment
            if self_.name == hearing.ANSWER_FILE and busy[0]:
                busy[0] -= 1
                raise PermissionError(13, "Permission denied")
            return real(self_, *a, **k)

        with mock.patch.object(Path, "read_text", flaky):
            got = hearing.read_answer(self.run_path)
        self.assertEqual(got["answers"], {"Q40": 1})
        self.assertEqual(busy[0], 0)

    def test_wait_returns_what_arrives(self):
        def late():
            time.sleep(0.2)
            hearing.save_answer(self.run_path, {"answers": {"Q40": 1}})

        threading.Thread(target=late).start()
        got = hearing.wait_for_answer(self.run_path, 5.0, poll_s=0.05)
        self.assertEqual(got["answers"], {"Q40": 1})
        hearing.clear_mailbox(self.run_path)
        self.assertIsNone(hearing.read_answer(self.run_path))


class AskUserWaitTest(ToolsTestCase):
    def test_it_asks_on_screen_waits_and_uses_the_answer(self):
        ctx = make_ctx(self.root, product="care-bed")
        seen = {}

        def person():
            for _ in range(100):
                if (Path(ctx.store.path) / hearing.REQUEST_FILE).exists():
                    break
                time.sleep(0.05)
            seen["asked"] = json.loads((Path(ctx.store.path) / hearing.REQUEST_FILE).read_text(encoding="utf-8"))
            hearing.save_answer(ctx.store.path, {"answers": {"Q30": "運転者だけ", "Q40": {"value": 1700, "unit": "N"}}, "note": "よろしく"})

        t = threading.Thread(target=person)
        t.start()
        got = call("ask_user", ctx, wait_seconds=10)
        t.join()
        self.assertTrue(got["ready"])
        self.assertEqual([q["req_key"] for q in seen["asked"]["questions"]][:2], ["Q03", "Q11"])
        by = {r["key"]: r for r in got["answers"]}
        self.assertEqual((by["Q40"]["source"], by["Q40"]["value"]), ("hearing", 1700))
        self.assertEqual(by["Q19"]["source"], "default")
        self.assertFalse((Path(ctx.store.path) / hearing.REQUEST_FILE).exists())  # the mailbox is emptied
        self.assertFalse((Path(ctx.store.path) / hearing.ANSWER_FILE).exists())
        if ev.known(ev.HEARING_ASKED):
            kinds = self.kinds(ctx)
            self.assertEqual(kinds.count(ev.HEARING_ASKED), 1)  # asked once, before the wait
            self.assertLess(kinds.index(ev.HEARING_ASKED), kinds.index(ev.HEARING_ANSWERED))

    def test_no_answer_in_time_means_the_defaults_and_a_required_question_still_stops(self):
        ctx = make_ctx(self.root, product="care-bed")
        got = call("ask_user", ctx, wait_seconds=0.2)
        self.assertFalse(got["ready"])
        self.assertIn("Q30", got["unresolved"])
        self.assertFalse((Path(ctx.store.path) / hearing.REQUEST_FILE).exists())

    def test_without_wait_nothing_is_written(self):
        ctx = make_ctx(self.root, product="care-bed")
        call("ask_user", ctx)
        self.assertFalse((Path(ctx.store.path) / hearing.REQUEST_FILE).exists())


if __name__ == "__main__":
    unittest.main()
