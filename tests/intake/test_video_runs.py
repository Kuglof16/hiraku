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
