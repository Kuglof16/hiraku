import json
import tempfile
import unittest
from pathlib import Path

from hiraku import contract
from hiraku.catalog import load_requirements
from hiraku.intake import video_runs


class N09Test(unittest.TestCase):
    def test_each_product_gets_a_run_that_stops_after_the_picks(self):
        with tempfile.TemporaryDirectory() as tmp:
            for product in video_runs.N09_PRODUCTS:
                with self.subTest(product=product):
                    path = video_runs.make_n09(product, tmp)
                    events = [json.loads(line) for line in (path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
                    self.assertEqual([e["kind"] for e in events], ["run.started", "domains.picked", "run.finished"])
                    self.assertEqual(events[2]["data"]["status"], "stopped")
                    picks = events[1]["data"]["picks"]
                    self.assertTrue(picks)
                    for p in picks:  # a domain with no runnable test still says why on its card
                        if not p["test_ids"]:
                            self.assertIn("荷重条件の作り方がまだ無い", p["reason"])


class N09SentenceTest(unittest.TestCase):
    def test_the_vehicles_sentence_says_what_its_flags_say_about_the_power(self):
        req = video_runs.n09_requirements("offroad-4w")
        self.assertTrue(req["flags"]["battery"] and req["flags"]["electric"])
        self.assertIn("電動", req["want"])
        self.assertIn("電池", req["want"])
        for key in ("design_file", "tests", "beyond"):
            self.assertNotIn(key, req)
        self.assertEqual(req["stop_after"], "picks")
        self.assertEqual(contract.errors("requirements_file", req), [])
        self.assertEqual(video_runs.n09_requirements("care-bed")["want"], load_requirements_want("care-bed"))

    def test_the_file_for_a_real_run_is_written_with_that_sentence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = video_runs.write_n09_file("offroad-4w", tmp)
            self.assertEqual(path.name, "offroad-4w-n09.yaml")
            self.assertEqual(load_requirements(path)["want"], video_runs.N09_WANTS["offroad-4w"])


def load_requirements_want(product):
    from hiraku.intake import load_product

    return load_product(product)["want"]


class N14RealTest(unittest.TestCase):
    """The materials for a run of the N14 scene by a real Claude: the file, the answers, the dropped notes."""

    def test_the_requirements_file_lacks_what_the_hearing_asks_and_has_no_stop(self):
        from hiraku.intake import hearing, load_product

        with tempfile.TemporaryDirectory() as tmp:
            path = video_runs.prepare_n14_real(tmp)
            req = load_requirements(path)
        self.assertNotIn("stop_after", req)
        self.assertEqual(req["hearing"], "claude")
        self.assertEqual(contract.errors("requirements_file", req), [])
        have = {r["key"] for r in req["requirements"]}
        asked = {q["req_key"] for q in hearing.plan_hearing(req, product_defaults=load_product("offroad-4w")).questions}
        self.assertTrue({"Q34", "Q64", "Q74", "Q80"} <= asked)
        self.assertFalse(asked & have)  # what is asked is not in the file

    def test_with_hearing_claude_the_intake_leaves_the_items_open_and_ask_user_asks_all_of_them(self):
        from hiraku.intake import pipeline
        from hiraku.intake.tools import TOOLS
        from tests.intake.test_tools import make_ctx

        with tempfile.TemporaryDirectory() as tmp:
            req = load_requirements(video_runs.prepare_n14_real(tmp))
        left = pipeline.intake(req["want"], req["flags"], req.get("speed_class"), product="offroad-4w",
                               requirements=req["requirements"], from_file=True, hearing_mode=req["hearing"])
        self.assertEqual([e["kind"] for e in left.events][0], "domains.picked")
        self.assertNotIn("hearing.asked", [e["kind"] for e in left.events])
        self.assertNotIn("hearing.answered", [e["kind"] for e in left.events])
        self.assertEqual(left.requirements_file["requirements"], req["requirements"])  # nothing filled in
        done = pipeline.intake(req["want"], req["flags"], req.get("speed_class"), product="offroad-4w",
                               requirements=req["requirements"], from_file=True)  # hearing code: as before
        self.assertIn("hearing.answered", [e["kind"] for e in done.events])
        self.assertGreater(len(done.requirements_file["requirements"]), len(req["requirements"]))
        with self.assertRaises(Exception):
            pipeline.intake(req["want"], req["flags"], product="offroad-4w", requirements=[], from_file=True, hearing_mode="x")
        with tempfile.TemporaryDirectory() as root:  # ask_user then asks the must and the confirm items the run lacks
            ctx = make_ctx(Path(root), product="offroad-4w", flags=req["flags"], requirements=req["requirements"])
            got = TOOLS["ask_user"]["function"](ctx, {}, "lead")
        asked = {q["req_key"]: q["ask"] for q in got["questions"]}
        self.assertTrue({"Q34", "Q64", "Q74", "Q80"} <= set(asked))
        self.assertEqual({"confirm"}, {asked[k] for k in ("Q34", "Q64")})
        self.assertIn("Q03", asked)

    def test_the_answers_are_written_as_the_screen_writes_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = video_runs.answer_hearing(tmp)
            body = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(path.name, "hearing.answer.json")
        self.assertTrue({"Q34", "Q64", "Q74", "Q80"} <= set(body["answers"]))
        self.assertTrue(body["use_defaults"])

    def test_answer_waits_for_the_question(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(TimeoutError):
                video_runs.answer_hearing(tmp, wait_s=0.6)

    def test_the_notes_are_dropped_as_a_request_in_the_inbox_and_numbered(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            first = video_runs.drop_knowledge(run)
            second = video_runs.drop_knowledge(run)
            self.assertEqual((first.name, second.name), ("1-knowledge.json", "2-knowledge.json"))
            body = json.loads(first.read_text(encoding="utf-8"))
            self.assertEqual((body["kind"], body["file"]), ("knowledge", "bending-notes.md"))
            self.assertTrue((run / "knowledge" / "bending-notes.md").is_file())

    def test_the_drop_can_wait_for_an_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "events.jsonl").write_text(json.dumps({"kind": "run.started"}) + "\n", encoding="utf-8")
            with self.assertRaises(TimeoutError):
                video_runs.drop_knowledge(run, after_kind="comparison.recorded", wait_s=0.6)
            with (run / "events.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"kind": "comparison.recorded"}) + "\n")
            self.assertEqual(video_runs.drop_knowledge(run, after_kind="comparison.recorded", wait_s=2).name, "1-knowledge.json")


class N14Test(unittest.TestCase):
    def test_the_files_have_the_hearing_and_the_name_the_plan_is_found_by(self):
        with tempfile.TemporaryDirectory() as tmp:
            req_path, events_path = video_runs.prepare_n14(tmp)
            self.assertEqual(req_path.name, "offroad-4w-n14.yaml")
            req = load_requirements(req_path)
            self.assertEqual(req["tests"], ["P1", "R1"])
            events = json.loads(events_path.read_text(encoding="utf-8"))
            kinds = [e["kind"] for e in events]
            self.assertEqual(kinds[0], "domains.picked")
            self.assertEqual(kinds[-2:], ["hearing.asked", "hearing.answered"])
            asked = [q["req_key"] for q in events[-2]["data"]["questions"]]
            answers = {a["key"]: a["source"] for a in events[-1]["data"]["answers"]}
            self.assertIn("Q34", asked)
            self.assertEqual(answers["Q34"], "hearing")
            have = {r["key"] for r in req["requirements"]}
            self.assertLessEqual(set(answers), have)  # what the hearing settled is in the run's requirements


if __name__ == "__main__":
    unittest.main()
