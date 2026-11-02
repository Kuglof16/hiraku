"""qa.check_run on a run folder built from the contract's sample events.

The clean folder must have no errors. Each test then breaks one rule the way a
buggy store, tool or hook would, and checks that the finding names that rule.
"""

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from hiraku import contract
from hiraku.contract import layout
from qa import check_run as cr

RUN_ID = "20261102-203000-offroad-4w"
FILE_KINDS = {"prediction.recorded": "prediction", "analysis.finished": "analysis",
              "comparison.recorded": "comparison", "cause.recorded": "cause", "verdict.recorded": "verdict"}


def sample_events():
    with (contract.EXAMPLES_DIR / "events.sample.jsonl").open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_run(root: Path, events: list[dict]) -> Path:
    """Write project.json, events.jsonl and every record file the events describe."""
    run = root / RUN_ID
    run.mkdir(parents=True)
    project = copy.deepcopy(events[0]["data"])
    fin = [e for e in events if e["kind"] == "run.finished"]
    if fin:
        project["status"] = fin[-1]["data"]["status"]
    (run / layout.PROJECT_FILE).write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    with (run / layout.EVENTS_FILE).open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    version = {}
    judgements = {}
    for e in events:
        d = e.get("data")
        if e["kind"] == "design.created":
            p = layout.version_dir(run, d["design_version"]) / layout.DESIGN_FILE
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        elif e["kind"] in FILE_KINDS:
            kind = FILE_KINDS[e["kind"]]
            rid = d[f"{kind}_id"]
            v = d.get("design_version") or version.get(d.get("prediction_id")) or version.get(d.get("comparison_id"))
            version[rid] = v
            p = layout.record_path(run, v, kind, rid)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        elif e["kind"] == "judgement.updated":
            judgements.setdefault(d["design_version"], {})[d["domain"]] = d
    for v, js in judgements.items():
        (layout.version_dir(run, v) / layout.JUDGEMENTS_FILE).write_text(
            json.dumps(list(js.values()), ensure_ascii=False), encoding="utf-8")
    return run


def renumber(events):
    for i, e in enumerate(events):
        e["seq"] = i
    return events


def by_kind(events, kind):
    return next(e for e in events if e["kind"] == kind)


class CheckRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def findings(self, events):
        run = write_run(self.root / f"r{len(list(self.root.iterdir()))}", events)
        return cr.check_run(run)

    def rules(self, events, level="error"):
        return {f.rule for f in self.findings(events) if f.level == level}

    def assertFlags(self, events, rule):
        found = self.findings(events)
        self.assertIn(rule, {f.rule for f in found if f.level == "error"},
                      "\n".join(str(f) for f in found) or "no findings")

    # --- the clean sample -----------------------------------------------------------
    def test_sample_run_has_no_errors(self):
        found = self.findings(sample_events())
        self.assertEqual([str(f) for f in found if f.level == "error"], [])
        # the sample never records tool.checked, so H2 can only be a warning
        self.assertEqual({f.rule for f in found if f.level == "warn"}, {"H2_tool_usable"})

    def test_command_line_exit_codes(self):
        good = write_run(self.root / "good", sample_events())
        ev = sample_events()
        del ev[5]
        bad = write_run(self.root / "bad", ev)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cr.main([str(good)]), 0)
            self.assertEqual(cr.main([str(bad)]), 1)
            self.assertEqual(cr.main([str(good), "--strict"]), 1)  # the H2 warning counts

    # --- shape and sequence ----------------------------------------------------------
    def test_seq_gap(self):
        ev = sample_events()
        del ev[5]
        self.assertFlags(ev, "events.seq")

    def test_time_going_back(self):
        ev = sample_events()
        ev[6]["t"] = 0.1
        self.assertFlags(ev, "events.t")

    def test_event_outside_the_schema(self):
        ev = sample_events()
        ev[3]["kind"] = "made.up"
        self.assertFlags(ev, "schema")

    def test_file_differs_from_its_event(self):
        run = write_run(self.root / "x", sample_events())
        p = layout.record_path(run, 1, "prediction", "pred-0001")
        rec = json.loads(p.read_text(encoding="utf-8"))
        rec["value"] = 99.0
        p.write_text(json.dumps(rec), encoding="utf-8")
        self.assertIn("files.event_data", {f.rule for f in cr.check_run(run)})

    def test_record_file_missing(self):
        run = write_run(self.root / "x", sample_events())
        layout.record_path(run, 1, "verdict", "ver-0001").unlink()
        self.assertIn("files.missing", {f.rule for f in cr.check_run(run)})

    def test_project_changed_outside_the_mutable_fields(self):
        run = write_run(self.root / "x", sample_events())
        p = run / layout.PROJECT_FILE
        proj = json.loads(p.read_text(encoding="utf-8"))
        proj["want"] = "something else"
        p.write_text(json.dumps(proj, ensure_ascii=False), encoding="utf-8")
        self.assertIn("project.changed", {f.rule for f in cr.check_run(run)})

    # --- hook rules ------------------------------------------------------------------
    def test_h1_analysis_without_its_prediction(self):
        ev = sample_events()
        for kind in ("analysis.started", "analysis.finished"):
            by_kind(ev, kind)["data"]["prediction_ids"] = ["pred-0002"]
        self.assertFlags(ev, "H1_prediction_first")

    def test_h1_prediction_recorded_after_the_analysis(self):
        ev = sample_events()
        pred = ev.pop(6)
        ev.insert(8, pred)  # after analysis.started
        t = [e["t"] for e in ev]
        for i, e in enumerate(ev):
            e["t"] = sorted(t)[i]
        self.assertFlags(renumber(ev), "H1_prediction_first")

    def test_h2_tool_used_while_not_usable(self):
        ev = sample_events()
        spec = json.loads((contract.EXAMPLES_DIR / "tool_spec.json").read_text(encoding="utf-8"))
        spec["usable"] = False
        spec["checks"][0]["pass"] = False
        spec["checks"][0]["got"] = 2.0
        checked = {"seq": 0, "ts": ev[1]["ts"], "t": ev[1]["t"], "run_id": RUN_ID, "kind": "tool.checked",
                   "agent": "system", "data": spec}
        ev.insert(2, checked)
        self.assertFlags(renumber(ev), "H2_tool_usable")

    def test_tool_check_that_lies_about_pass(self):
        ev = sample_events()
        spec = json.loads((contract.EXAMPLES_DIR / "tool_spec.json").read_text(encoding="utf-8"))
        spec["checks"][0]["got"] = 1.5  # 50 % off with a 1 % tolerance, still marked pass
        ev.insert(2, {"seq": 0, "ts": ev[1]["ts"], "t": ev[1]["t"], "run_id": RUN_ID, "kind": "tool.checked",
                      "agent": "system", "data": spec})
        self.assertFlags(renumber(ev), "tool.check")

    def test_h3_analysis_after_an_unexplained_mismatch(self):
        ev = sample_events()
        cmp_ev = by_kind(ev, "comparison.recorded")
        bad = copy.deepcopy(cmp_ev)
        bad["data"].update(comparison_id="cmp-0002", status="mismatch")
        later = copy.deepcopy(by_kind(ev, "analysis.started"))
        later["data"]["analysis_id"] = "ana-0002"
        i = ev.index(cmp_ev) + 1
        ev[i:i] = [bad, later]
        for e in ev[i:]:
            e["t"] = max(e["t"], cmp_ev["t"])
        self.assertFlags(renumber(ev), "H3_cause_before_continue")

    def test_h4_done_while_a_picked_domain_is_pending(self):
        ev = sample_events()
        j = by_kind(ev, "judgement.updated")["data"]
        j.update(state="pending")
        self.assertFlags(ev, "H4_no_stop_before_judged")

    def test_unknown_hook_rule(self):
        ev = sample_events()
        by_kind(ev, "hook.denied")["data"]["rule"] = "H9_made_up"
        self.assertFlags(ev, "hook.rule")

    # --- numbers ---------------------------------------------------------------------
    def test_verdict_from_a_mismatch(self):
        ev = sample_events()
        by_kind(ev, "comparison.recorded")["data"]["status"] = "mismatch"
        self.assertFlags(ev, "verdict.from_match")

    def test_comparison_status_recomputed(self):
        ev = sample_events()
        c = by_kind(ev, "comparison.recorded")["data"]
        c["diff_rel"] = 0.5
        self.assertFlags(ev, "comparison.math")

    def test_comparison_tolerance_comes_from_the_load_case(self):
        ev = sample_events()
        by_kind(ev, "comparison.recorded")["data"]["tolerance_rel"] = 0.5
        self.assertFlags(ev, "comparison.tolerance")

    def test_comparison_value_not_from_the_analysis(self):
        ev = sample_events()
        by_kind(ev, "comparison.recorded")["data"]["analyzed"]["value"] = 130.0
        self.assertFlags(ev, "comparison.values")

    def test_verdict_pass_and_margin_recomputed(self):
        ev = sample_events()
        v = by_kind(ev, "verdict.recorded")["data"]
        v["margin_pct"] = 30.0
        self.assertFlags(ev, "verdict.math")
        ev = sample_events()
        by_kind(ev, "verdict.recorded")["data"]["pass"] = False
        self.assertFlags(ev, "verdict.math")

    def test_verdict_limit_comes_from_the_criterion(self):
        ev = sample_events()
        v = by_kind(ev, "verdict.recorded")["data"]
        v["limit"] = {"value": 200.0, "unit": "MPa"}
        self.assertFlags(ev, "verdict.criterion")

    def test_judgement_red_count(self):
        ev = sample_events()
        by_kind(ev, "judgement.updated")["data"]["red_count"] = 1
        self.assertFlags(ev, "judgement.state")

    def test_ids_numbered_without_gaps(self):
        ev = sample_events()
        for e in ev:
            d = e.get("data") or {}
            for k in ("prediction_id",):
                if d.get(k) == "pred-0001":
                    d[k] = "pred-0002"
            if "prediction_ids" in d:
                d["prediction_ids"] = ["pred-0002"]
        self.assertFlags(ev, "ids.numbering")


if __name__ == "__main__":
    unittest.main()
