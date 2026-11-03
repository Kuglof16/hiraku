"""The record checks of hiraku.agent.review, on scripted runs with the test stand-ins and on records
altered here to show each check failing."""

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from hiraku.__main__ import main
from hiraku.agent.review import N12_FILE, N12_KEY, RunRecord, check_n12, is_n12_run, review_run
from hiraku.agent.scripted import run_scripted
from hiraku.catalog import read_data
from hiraku.contract import layout
from tests.tools.helpers import RunCase


def by_name(findings):
    return {f.name: f for f in findings}


class ReviewCase(RunCase):
    # the scripted plan's R1 mismatches twice and matches on the third prediction (as in test_scripted)
    frame_factor = (1.5, 1.8, 1.03)

    def setUp(self):
        super().setUp()
        self.assertEqual(run_scripted(self.ctx), "done")
        self.path = self.ctx.store.path
        self.v = self.ctx.store.design_version

    def review(self, **kw):
        return by_name(review_run(self.path, **kw))

    def append_events(self, *events):
        with (self.path / layout.EVENTS_FILE).open("a", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

    def rewrite_events(self, change):
        p = self.path / layout.EVENTS_FILE
        events = [change(json.loads(line)) for line in p.read_text(encoding="utf-8").splitlines()]
        p.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")

    def record_file(self, kind, record_id, version=None):
        return layout.record_path(self.path, version or self.v, kind, record_id)

    def edit_record(self, kind, record_id, change, version=None):
        p = self.record_file(kind, record_id, version)
        p.write_text(json.dumps(change(json.loads(p.read_text(encoding="utf-8"))), ensure_ascii=False), encoding="utf-8")


class ScriptedRunReviewTest(ReviewCase, unittest.TestCase):
    def test_the_scripted_run_passes(self):
        f = self.review()
        self.assertEqual([n for n, x in f.items() if not x.ok], [], {n: x.detail for n, x in f.items()})
        self.assertIn("1 denied", f["HK1 denials (analyze without a prediction)"].detail)
        self.assertIn("1 denied", f["HK3 denials (going on before a cause)"].detail)
        self.assertIn("stop_code done", f["HK4 blocked stops and the final stop_code"].detail)
        self.assertIn("R1-step100 2", f["causes of the mismatches"].detail)  # two predict-again rounds
        self.assertIn("no model (scripted)", f["model per role"].detail)
        self.assertIn("no cost.updated", f["cost and turns"].detail)
        self.assertNotIn("N12 unit mistake", f)  # not a run of offroad-4w-n12.yaml

    def test_cli(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["review", str(self.path)]), 0)
        self.assertIn("[ok] verdicts on the last version", out.getvalue())
        self.append_events({"kind": "hook.denied", "agent": "cae.strength", "domain": "strength",
                            "data": {"rule": "HK1_prediction_first"}},
                           {"kind": "hook.denied", "agent": "cae.strength", "domain": "strength",
                            "data": {"rule": "HK1_prediction_first"}})
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["review", str(self.path)]), 1)
        self.assertIn("[NG] HK1 denials", out.getvalue())
        with tempfile.TemporaryDirectory() as empty, redirect_stdout(io.StringIO()), \
                redirect_stderr(io.StringIO()):
            self.assertEqual(main(["review", empty]), 2)


class FailingChecksTest(ReviewCase, unittest.TestCase):
    def test_hk1_denied_again_before_a_prediction(self):
        denial = {"kind": "hook.denied", "agent": "cae.strength", "domain": "strength",
                  "data": {"rule": "HK1_prediction_first"}}
        self.append_events(denial)
        self.assertTrue(self.review()["HK1 denials (analyze without a prediction)"].ok)  # a prediction came between
        self.append_events(denial)
        f = self.review()["HK1 denials (analyze without a prediction)"]
        self.assertFalse(f.ok)
        self.assertIn("denied again before a prediction was recorded: cae.strength 1", f.detail)

    def test_hk3_denied_again_before_a_cause(self):
        denial = {"kind": "hook.denied", "agent": "cae.strength", "domain": "strength",
                  "data": {"rule": "HK3_cause_before_continue"}}
        self.append_events(denial, {"kind": "prediction.recorded", "agent": "cae.strength", "domain": "strength",
                                    "data": {"prediction_id": "pred-9999"}}, denial)
        f = self.review()["HK3 denials (going on before a cause)"]
        self.assertFalse(f.ok)
        self.assertIn("strength 1", f.detail)

    def test_hk4_stopped_not_judged(self):
        self.append_events({"kind": "hook.blocked_stop", "agent": "lead", "data": {"rule": "HK4_no_stop_before_judged"}})
        self.rewrite_events(lambda e: dict(e, data=dict(e["data"], stop_code="not_judged"))
                            if e["kind"] == "run.finished" else e)
        f = self.review()["HK4 blocked stops and the final stop_code"]
        self.assertFalse(f.ok)
        self.assertIn("1 blocked stops (lead 1); stop_code not_judged", f.detail)

    def test_hk4_red_left_is_judged(self):
        self.rewrite_events(lambda e: dict(e, data=dict(e["data"], stop_code="red_left"))
                            if e["kind"] == "run.finished" else e)
        self.assertTrue(self.review()["HK4 blocked stops and the final stop_code"].ok)

    def test_a_load_case_without_a_verdict(self):
        verdicts = self.ctx.store.records("verdict", self.v)
        gone = next(v for v in verdicts if v["load_case_id"] == "R1-step100")
        self.record_file("verdict", gone["verdict_id"]).unlink()
        f = self.review()["verdicts on the last version"]
        self.assertFalse(f.ok)
        self.assertIn("no verdict for R1-step100", f.detail)

    def test_a_mismatch_without_a_cause(self):
        c = next(c for c in self.ctx.store.records("comparison") if c["status"] == "mismatch")
        self.record_file("cause", c["cause_id"]).unlink()
        self.edit_record("comparison", c["comparison_id"], lambda r: {k: v for k, v in r.items() if k != "cause_id"})
        f = self.review()["causes of the mismatches"]
        self.assertFalse(f.ok)
        self.assertIn(c["comparison_id"], f.detail)

    def test_a_failed_analysis(self):
        a = self.ctx.store.records("analysis")[0]
        self.edit_record("analysis", a["analysis_id"], lambda r: dict(r, status="failed", error="the tool stopped"))
        f = self.review()["failed analyses"]
        self.assertFalse(f.ok)
        self.assertIn("the tool stopped", f.detail)

    def test_a_prediction_never_compared(self):
        last = self.ctx.store.records("prediction")[-1]
        extra = dict(last, prediction_id="pred-0999", load_case_id="P1-step100", quantity="front_wheel_load")
        self.record_file("prediction", "pred-0999").write_text(json.dumps(extra), encoding="utf-8")
        f = self.review()["predictions never compared"]
        self.assertFalse(f.ok)
        self.assertIn("pred-0999 (P1-step100 front_wheel_load)", f.detail)

    def test_cost_turns_and_models_are_reported(self):
        self.append_events(
            {"kind": "agent.started", "agent": "cae.nvh", "data": {"agent_id": "cae.nvh", "model": "claude-haiku-5-5",
                                                                    "effort": "medium"}},
            {"kind": "cost.updated", "agent": "system", "data": {
                "cost_usd": 0.31, "num_turns": 12, "model_usage": {"claude-haiku-5-5": {
                    "inputTokens": 100, "outputTokens": 50, "cacheReadInputTokens": 900, "cacheCreationInputTokens": 30,
                    "costUSD": 0.01}}}})
        f = self.review()
        self.assertIn("cost_usd 0.3100; num_turns 12; claude-haiku-5-5: in 100, out 50, cache read 900",
                      f["cost and turns"].detail)
        self.assertIn("cae.nvh: claude-haiku-5-5 (medium)", f["model per role"].detail)
        self.assertTrue(f["cost and turns"].ok and f["model per role"].ok)


def n12_record(root: Path, *, cause: str, second: str = "match", q41=None) -> Path:
    """A minimal record of an N12 run: S1 compared, mismatched, the cause, predicted again and compared."""
    want = next(q for q in read_data(N12_FILE)["requirements"] if q["key"] == N12_KEY)
    path = root / "n12"
    v = layout.version_dir(path, 1)
    project = {"run_id": "20261103-000000-offroad-4w", "design_versions": [1],
               "requirements": [dict(want, **(q41 or {}))],
               "load_cases": [{"load_case_id": "S1-tilt-two_cargo", "test_id": "S1", "domain": "handling",
                               "criterion": {"quantity": "tilt_angle"}}]}
    records = {
        "prediction": [{"prediction_id": f"pred-000{i}", "design_version": 1, "load_case_id": "S1-tilt-two_cargo",
                        "test_id": "S1", "quantity": "tilt_angle"} for i in (1, 2)],
        "analysis": [{"analysis_id": f"ana-000{i}", "load_case_id": "S1-tilt-two_cargo", "status": "ok",
                      "prediction_ids": [f"pred-000{i}"], "results": [{"quantity": "tilt_angle"}]} for i in (1, 2)],
        "comparison": [{"comparison_id": "cmp-0001", "prediction_id": "pred-0001", "analysis_id": "ana-0001",
                        "status": "mismatch", "cause_id": "cause-0001"},
                       {"comparison_id": "cmp-0002", "prediction_id": "pred-0002", "analysis_id": "ana-0002",
                        "status": second}],
        "cause": [{"cause_id": "cause-0001", "comparison_id": "cmp-0001", "cause": cause}],
        "verdict": [{"verdict_id": "ver-0001", "load_case_id": "S1-tilt-two_cargo"}],
    }
    for kind, recs in records.items():
        (v / layout.RECORD_DIRS[kind]).mkdir(parents=True)
        for r in recs:
            (v / layout.RECORD_DIRS[kind] / f"{r[kind + '_id']}.json").write_text(json.dumps(r, ensure_ascii=False),
                                                                                  encoding="utf-8")
    (path / layout.PROJECT_FILE).write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    (path / layout.EVENTS_FILE).write_text(json.dumps({"kind": "run.finished", "data": {"stop_code": "done"}}) + "\n",
                                           encoding="utf-8")
    return path


class N12Test(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def check(self, **kw):
        path = n12_record(self.root, **kw)
        try:
            return check_n12(RunRecord(path)), is_n12_run(RunRecord(path)), by_name(review_run(path))
        finally:
            shutil.rmtree(path)

    def test_the_unit_mistake_found(self):
        for cause in ("62 cm のつもりが 62 mm と書かれていた", "Q41 の単位の取り違え"):
            f, is_n12, all_ = self.check(cause=cause)
            self.assertTrue(f.ok, f.detail)
            self.assertTrue(is_n12)
            self.assertIn("N12 unit mistake", all_)

    def test_a_cause_that_misses_the_unit(self):
        f, _, _ = self.check(cause="解析の道具の許す幅が狭すぎる")
        self.assertFalse(f.ok)
        self.assertIn("does not name the unit mistake", f.detail)

    def test_the_second_comparison_still_off(self):
        f, _, _ = self.check(cause="62 cm と 62 mm の取り違え", second="mismatch")
        self.assertFalse(f.ok)
        self.assertIn("cmp-0002 mismatch", f.detail)

    def test_not_an_n12_run_when_q41_is_right(self):
        _, is_n12, all_ = self.check(cause="-", q41={"value": 620})
        self.assertFalse(is_n12)
        self.assertNotIn("N12 unit mistake", all_)


if __name__ == "__main__":
    unittest.main()
