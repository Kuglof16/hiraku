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
from unittest import mock

from hiraku import contract
from hiraku.contract import layout
from qa import check_run as cr

RUN_ID = "20261102-203000-offroad-4w"
FILE_KINDS = layout.RECORD_OF_EVENT


def sample_events():
    with (contract.EXAMPLES_DIR / "events.sample.jsonl").open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_run(root: Path, events: list[dict]) -> Path:
    """Write project.json, events.jsonl and every record file the events describe."""
    run = root / RUN_ID
    run.mkdir(parents=True)
    updates = [e for e in events if e["kind"] == "project.updated"]
    project = copy.deepcopy((updates[-1] if updates else events[0])["data"])
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
            v = (d.get("design_version") or version.get(d.get("prediction_id")) or version.get(d.get("comparison_id"))
                 or version.get(d.get("analysis_id")) or 1)  # an unsupported_load verdict names its analysis
            version[rid] = v
            p = layout.record_path(run, v, kind, rid)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        elif e["kind"] == "judgement.updated":
            judgements.setdefault(d["design_version"], {})[d["domain"]] = d
        elif e["kind"] == "load_cases.created":
            p = layout.version_dir(run, d["design_version"]) / layout.VERSION_LOAD_CASES_FILE
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        elif e["kind"] == "report.written":
            (run / layout.REPORT_FILE).write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
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
        # the sample never records tool.checked, so HK2 can only be a warning
        self.assertEqual({f.rule for f in found if f.level == "warn"}, {"HK2_tool_usable"})

    def test_command_line_exit_codes(self):
        good = write_run(self.root / "good", sample_events())
        ev = sample_events()
        del ev[5]
        bad = write_run(self.root / "bad", ev)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cr.main([str(good)]), 0)
            self.assertEqual(cr.main([str(bad)]), 1)
            self.assertEqual(cr.main([str(good), "--strict"]), 1)  # the HK2 warning counts

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

    # --- project.updated, tools made in the run, stop.request ------------------------
    @staticmethod
    def _first_design(ev, before_design=False):
        """The run starts without load cases or picks; project.updated re-sends them after version 1."""
        full = copy.deepcopy(ev[0]["data"])
        ev[0]["data"].update(load_cases=[], domain_picks=[])
        at = by_kind(ev, "design.created")
        upd = {"seq": 0, "ts": at["ts"], "t": at["t"], "run_id": RUN_ID, "kind": "project.updated",
               "agent": "designer", "data": full}
        i = ev.index(at)
        ev.insert(i if before_design else i + 1, upd)
        return renumber(ev)

    def test_project_updated_after_the_first_version(self):
        self.assertEqual(self.rules(self._first_design(sample_events())), set())
        self.assertFlags(self._first_design(sample_events(), before_design=True), "project.updated")
        ev = self._first_design(sample_events())
        by_kind(ev, "project.updated")["data"]["want"] = "something else"
        self.assertFlags(ev, "project.changed")
        run = write_run(self.root / "x", self._first_design(sample_events()))
        p = run / layout.PROJECT_FILE
        proj = json.loads(p.read_text(encoding="utf-8"))
        proj["load_cases"] = []  # project.json must keep what project.updated re-sent
        p.write_text(json.dumps(proj, ensure_ascii=False), encoding="utf-8")
        self.assertIn("project.changed", {f.rule for f in cr.check_run(run) if f.level == "error"})

    def test_requirements_re_sent_before_the_first_version(self):
        # contract: requirements changed mid-run are re-sent any time (a hearing before the designer's version 1)
        ev = sample_events()
        upd = copy.deepcopy(ev[0])
        upd.update(kind="project.updated", agent="lead")
        reqs = upd["data"].get("requirements")
        self.assertTrue(reqs, "the sample run has requirements")
        upd["data"]["requirements"] = reqs[:-1]  # one requirement dropped in the hearing
        ev.insert(ev.index(by_kind(ev, "design.created")), upd)
        self.assertEqual(self.rules(renumber(ev)), set())

    def test_tools_made_in_the_run_and_the_stop_request(self):
        run = write_run(self.root / "x", sample_events())
        tools = run / layout.TOOLS_DIR
        tools.mkdir()
        for name in ("beam-1.py", "beam-1.check.json", "beam-2.py", "beam-2.check.json"):
            (tools / name).write_text("{}", encoding="utf-8")
        self.assertEqual([str(f) for f in cr.check_run(run) if f.rule.startswith(("files", "ids"))], [])
        (tools / "beam-2.check.json").unlink()
        (tools / "beam-4.py").write_text("", encoding="utf-8")
        (tools / "beam-4.check.json").write_text("{}", encoding="utf-8")
        found = {(f.rule, f.level) for f in cr.check_run(run)}
        self.assertIn(("files.missing", "error"), found)
        self.assertIn(("ids.numbering", "error"), found)
        (run / layout.STOP_REQUEST_FILE).write_text("", encoding="utf-8")
        self.assertIn("files.stop_request", {f.rule for f in cr.check_run(run) if f.level == "warn"})

    def test_requests_from_the_screen_and_knowledge_files(self):
        knowledge = json.loads((contract.EXAMPLES_DIR / "knowledge_imported.json").read_text(encoding="utf-8"))
        ev = self._insert(sample_events(), "design.created", "knowledge.imported", knowledge)
        run = write_run(self.root / "x", ev)
        (run / layout.KNOWLEDGE_DIR).mkdir()
        (run / layout.KNOWLEDGE_DIR / knowledge["doc"]["filename"]).write_text("# rules\n", encoding="utf-8")
        files = lambda: [(f.rule, f.level, f.where) for f in cr.check_run(run) if f.rule.startswith("files")]
        self.assertEqual(files(), [])
        (run / layout.KNOWLEDGE_DIR / "other.md").write_text("", encoding="utf-8")
        (run / layout.INBOX_DIR).mkdir()
        (run / layout.INBOX_DIR / "0001-user.said.json").write_text("{}", encoding="utf-8")
        (run / layout.INBOX_DIR / "note.txt").write_text("", encoding="utf-8")
        (run / layout.HEARING_ANSWER_FILE).write_text("{}", encoding="utf-8")
        self.assertEqual(sorted(files()), [
            ("files.request", "warn", layout.HEARING_ANSWER_FILE),
            ("files.request", "warn", f"{layout.INBOX_DIR}/0001-user.said.json"),  # the run has finished
            ("files.unknown", "warn", f"{layout.INBOX_DIR}/note.txt"),
            ("files.unknown", "warn", f"{layout.KNOWLEDGE_DIR}/other.md")])

    def test_plugin_files_and_the_tool_error_log_are_part_of_the_layout(self):
        run = write_run(self.root / "p", sample_events())
        for name in (layout.PLUGIN_HOOKS_FILE, layout.PLUGIN_OUTBOX_FILE, layout.TOOL_ERRORS_FILE):
            (run / name).write_text("", encoding="utf-8")
        (run / "other.log").write_text("", encoding="utf-8")
        self.assertEqual([f.where for f in cr.check_run(run) if f.rule == "files.unknown"], ["other.log"])

    # --- the report (report.json and report.written) ---------------------------------
    @staticmethod
    def _report(ev, **domain):
        """A report.written right after the last judgement, filled from the records."""
        j = [e for e in ev if e["kind"] == "judgement.updated"][-1]
        entry = {"domain": "strength", "state": j["data"]["state"], "line": "qa line",
                 "verdict_ids": list(j["data"]["verdict_ids"]), "differences": []}
        entry.update(domain)
        rep = {"design_version": 1, "agent": "lead", "domains": [entry], "unchecked": [], "next": [],
               "summary": "qa summary"}
        ev.insert(ev.index(j) + 1, {"seq": 0, "ts": j["ts"], "t": j["t"], "run_id": RUN_ID, "kind": "report.written",
                                    "agent": "lead", "design_version": 1, "data": rep})
        return renumber(ev)

    def test_report_follows_its_records(self):
        self.assertEqual(self.rules(self._report(sample_events())), set())
        self.assertFlags(self._report(sample_events(), state="red"), "report.state")
        self.assertFlags(self._report(sample_events(), verdict_ids=[]), "report.verdicts")
        match = {"comparison_id": "cmp-0001", "diff_rel": 0.0, "cause_id": None}
        self.assertFlags(self._report(sample_events(), differences=[match]), "report.differences")
        ev = self._report(sample_events())
        by_kind(ev, "report.written")["data"]["next"] = [{"domain": "aero", "change": "x", "reason": "y"}]
        self.assertFlags(ev, "report.domains")
        # an item answering a rule's hit may name a domain the run did not pick (warned when no hit)
        by_kind(ev, "report.written")["data"]["next"] = [{"domain": "aero", "change": "x", "reason": "y",
                                                          "rule_id": "SM-01"}]
        self.assertNotIn("report.domains", self.rules(ev))
        self.assertIn("report.domains", self.rules(ev, "warn"))  # the sample has no rule_check hit

    def test_report_file_is_the_last_report_written(self):
        run = write_run(self.root / "x", self._report(sample_events()))
        p = run / layout.REPORT_FILE
        rep = json.loads(p.read_text(encoding="utf-8"))
        rep["summary"] = "changed"
        p.write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
        self.assertIn("files.event_data", {f.rule for f in cr.check_run(run)})
        p.unlink()
        self.assertIn("files.missing", {f.rule for f in cr.check_run(run)})

    def test_report_lists_the_mismatches_with_their_causes(self):
        ev = self.safe_side_flow()
        c = [e for e in ev if e["kind"] == "comparison.recorded"][-1]["data"]
        diff = {"comparison_id": "cmp-0001", "diff_rel": c["diff_rel"], "cause_id": "cause-0001"}
        self.assertEqual(self.rules(self._report(copy.deepcopy(ev), differences=[diff])), set())
        self.assertIn("report.differences", self.rules(self._report(copy.deepcopy(ev)), "warn"))
        self.assertFlags(self._report(copy.deepcopy(ev), differences=[dict(diff, diff_rel=0.5)]), "report.differences")
        self.assertFlags(self._report(copy.deepcopy(ev), differences=[dict(diff, cause_id="cause-0002")]),
                         "report.differences")

    # --- hook rules ------------------------------------------------------------------
    def test_h1_analysis_without_its_prediction(self):
        ev = sample_events()
        for kind in ("analysis.started", "analysis.finished"):
            by_kind(ev, kind)["data"]["prediction_ids"] = ["pred-0002"]
        self.assertFlags(ev, "HK1_prediction_first")

    def test_h1_prediction_recorded_after_the_analysis(self):
        ev = sample_events()
        pred = ev.pop(6)
        ev.insert(8, pred)  # after analysis.started
        t = [e["t"] for e in ev]
        for i, e in enumerate(ev):
            e["t"] = sorted(t)[i]
        self.assertFlags(renumber(ev), "HK1_prediction_first")

    def test_h2_tool_used_while_not_usable(self):
        ev = sample_events()
        spec = json.loads((contract.EXAMPLES_DIR / "tool_spec.json").read_text(encoding="utf-8"))
        spec["usable"] = False
        spec["checks"][0]["pass"] = False
        spec["checks"][0]["got"] = 2.0
        checked = {"seq": 0, "ts": ev[1]["ts"], "t": ev[1]["t"], "run_id": RUN_ID, "kind": "tool.checked",
                   "agent": "system", "data": spec}
        ev.insert(2, checked)
        self.assertFlags(renumber(ev), "HK2_tool_usable")

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
        self.assertFlags(renumber(ev), "HK3_cause_before_continue")

    def test_a_run_that_stops_after_the_picks_is_done_without_a_design(self):
        # project.stop_after "picks" (from the requirements file): domains picked and the hearing, then done;
        # no design is made. A done without a design and without it is still wrong, whatever events it has.
        def run(stop_after):
            ev = [e for e in sample_events()
                  if e["kind"] in ("run.started", "agent.started", "agent.stopped", "run.finished")]
            ev.insert(1, {"seq": 0, "ts": ev[0]["ts"], "t": ev[0]["t"], "run_id": RUN_ID, "kind": "domains.picked",
                          "agent": "lead", "data": {"want": ev[0]["data"].get("want", ""), "flags": {},
                                                    "picks": ev[0]["data"].get("domain_picks", []), "off": []}})
            if stop_after:
                ev[0]["data"]["stop_after"] = stop_after
            return renumber(ev)
        self.assertNotIn("HK4_no_stop_before_judged", self.rules(run("picks")))
        self.assertIn("HK4_no_stop_before_judged", self.rules(run(None)))

    def test_h4_does_not_wait_for_a_domain_with_no_test_to_build(self):
        # a picked domain whose tests have no way to build their load cases: test_ids empty, not judged
        def run(test_ids, reason):
            ev = sample_events()
            picks = [*ev[0]["data"]["domain_picks"],
                     {"domain": "nvh", "shown_name": "音振", "reason": reason, "test_ids": test_ids}]
            ev[0]["data"]["domain_picks"] = picks
            return ev
        said = "騒音 N3 は荷重条件の作り方が無いので、この実行では見ない"
        self.assertNotIn("HK4_no_stop_before_judged", self.rules(run([], said)))
        self.assertNotIn("HK4_no_stop_before_judged", self.rules(run([], said), "warn"))
        self.assertIn("HK4_no_stop_before_judged", self.rules(run([], "騒音"), "warn"))
        self.assertIn("HK4_no_stop_before_judged", self.rules(run(["N3"], said)))  # tests to judge, no tile

    def test_h4_done_while_a_picked_domain_is_pending(self):
        ev = sample_events()
        j = by_kind(ev, "judgement.updated")["data"]
        j.update(state="pending")
        self.assertFlags(ev, "HK4_no_stop_before_judged")

    @staticmethod
    def _unjudged_case(ev):
        """A second strength load case with a criterion that never gets a verdict."""
        lcs = ev[0]["data"]["load_cases"]
        lc = copy.deepcopy(lcs[0])
        lc.update(load_case_id="R3-qa", test_id="R3", title="qa case without a verdict")
        lcs.append(lc)
        return ev

    @staticmethod
    def _red(ev):
        """The sample's R1 verdict fails: its limit is set 10 MPa under the analysed value."""
        v = by_kind(ev, "verdict.recorded")["data"]
        lim = v["value"]["value"] - 10.0
        ev[0]["data"]["load_cases"][0]["criterion"]["limit"]["value"] = lim
        v["limit"]["value"] = lim
        v.update({"pass": False, "margin_pct": -1000.0 / lim, "margin": {"value": -10.0, "unit": "MPa"}})
        by_kind(ev, "judgement.updated")["data"].update(state="red", red_count=1)
        return ev

    def test_tile_state_follows_the_load_cases(self):
        # red when a verdict fails, even with a load case left without one
        self.assertNotIn("judgement.state", self.rules(self._unjudged_case(self._red(sample_events()))))
        # else pending while a load case has no verdict, and green only when every one has
        self.assertIn("judgement.state", self.rules(self._unjudged_case(sample_events())))  # the sample says green
        ev = self._unjudged_case(sample_events())
        by_kind(ev, "judgement.updated")["data"]["state"] = "pending"
        self.assertNotIn("judgement.state", self.rules(ev))
        ev = sample_events()
        by_kind(ev, "judgement.updated")["data"]["state"] = "pending"  # its only load case passed
        self.assertIn("judgement.state", self.rules(ev))

    def test_h4_counts_load_cases_not_tiles(self):
        # a red tile does not end the domain while another of its load cases has no verdict
        self.assertEqual(self.rules(self._red(sample_events())), set())
        self.assertIn("HK4_no_stop_before_judged", self.rules(self._red(sample_events()), "warn"))
        self.assertFlags(self._unjudged_case(self._red(sample_events())), "HK4_no_stop_before_judged")
        self.assertFlags(self._unjudged_case(sample_events()), "HK4_no_stop_before_judged")

    # --- how the run ended (run.finished stop_code) ----------------------------------
    @staticmethod
    def _finish(ev, status, code=None, state=None):
        d = by_kind(ev, "run.finished")["data"]
        d["status"] = status
        if code is not None:
            d["stop_code"] = code
        if status != "done":
            d["stop_reason"] = "qa case"
            ev[0]["data"]["stop_reason"] = "qa case"
        if state is not None:
            by_kind(ev, "judgement.updated")["data"]["state"] = state
        return ev

    def test_stop_code_done_with_done(self):
        self.assertEqual(self.rules(self._finish(sample_events(), "done", "done")), set())

    def test_stop_code_must_agree_with_the_status(self):
        self.assertIn("run.stop_code", self.rules(self._finish(sample_events(), "done", "not_judged")))
        self.assertIn("run.stop_code", self.rules(self._finish(sample_events(), "stopped", "done")))

    def test_not_judged_needs_a_load_case_without_a_verdict(self):
        self.assertIn("run.stop_code", self.rules(self._finish(sample_events(), "stopped", "not_judged")))
        # a pending tile while every load case has a verdict is not what HK4 waits for
        self.assertIn("run.stop_code", self.rules(self._finish(sample_events(), "stopped", "not_judged", state="pending")))
        ev = self._finish(self._unjudged_case(sample_events()), "stopped", "not_judged", state="pending")
        self.assertEqual(self.rules(ev), set())
        # the red tile of the R1 failure with R3 still without a verdict ends as not_judged
        ev = self._finish(self._unjudged_case(self._red(sample_events())), "stopped", "not_judged")
        self.assertEqual(self.rules(ev), set())

    def test_not_judged_after_a_case_ended_without_a_verdict(self):
        # a load case whose mismatch was ended (cause ends_case, contract 08cf4d6) is named by the stop as not
        # judged, a -beyond case without a criterion too (the video run of t17's candidate): not_judged agrees
        cause = {"cause_id": "cause-0001", "comparison_id": "cmp-0001", "cause": "the hand calculation stops short",
                 "fix": "none in this run", "resolved": False, "ends_case": True}
        ev = self._insert(sample_events(), "comparison.recorded", "cause.recorded", cause)
        self.assertNotIn("run.stop_code", self.rules(self._finish(ev, "stopped", "not_judged")))
        # resolved later, it no longer stands
        ev = self._insert(ev, "cause.recorded", "cause.recorded", dict(cause, resolved=True, resolved_by="cmp-0001"))
        self.assertIn("run.stop_code", self.rules(self._finish(ev, "stopped", "not_judged")))

    def test_round_limit_needs_the_revisions_used(self):
        # one design version and round_limit 1: no revision was made
        self.assertIn("run.stop_code", self.rules(self._finish(sample_events(), "stopped", "round_limit", "pending")))

    def test_round_limit_still_judges_the_latest_version(self):
        # HK4: the round limit only ends revisions; the latest version is still judged (red is judged)
        def stopped(state):
            ev = self._finish(sample_events(), "stopped", "round_limit", state)
            ev[0]["data"]["round_limit"] = 0  # reached with the one version
            if state == "red":
                ev = self._finish(self._red(ev), "stopped", "round_limit", "red")
            return ev
        self.assertIn("HK4_no_stop_before_judged", self.rules(stopped("pending")))
        self.assertNotIn("HK4_no_stop_before_judged", self.rules(stopped("red")))
        self.assertNotIn("HK4_no_stop_before_judged", self.rules(stopped("red"), "warn"))

    def test_round_limit_may_leave_cases_behind_a_lower_stage_red(self):
        # HK4 / HK5's dead end: revisions used up with a red left, the heavier cases are not run (contract)
        def stopped(reason):
            ev = self._unjudged_case(self._red(sample_events()))  # R1 red, R3-qa without a verdict
            ev = self._finish(ev, "stopped", "round_limit", "red")
            ev[0]["data"]["round_limit"] = 0
            ev[0]["data"]["stop_reason"] = reason
            by_kind(ev, "run.finished")["data"]["stop_reason"] = reason
            return ev
        said = stopped("R1 が赤のまま直しの回数を使い切った。R3-qa は低い段の赤が残るので判定していない")
        self.assertNotIn("HK4_no_stop_before_judged", self.rules(said))
        self.assertNotIn("HK4_no_stop_before_judged", self.rules(said, "warn"))
        silent = stopped("R1 が赤のまま直しの回数を使い切った")
        self.assertNotIn("HK4_no_stop_before_judged", self.rules(silent))
        self.assertIn("HK4_no_stop_before_judged", self.rules(silent, "warn"))

    def test_comparison_where_picks_the_result(self):
        ev = sample_events()
        by_kind(ev, "comparison.recorded")["data"]["where"] = "rail_L0"  # the sample's result is at rail_L0
        self.assertNotIn("comparison.values", self.rules(ev))
        by_kind(ev, "comparison.recorded")["data"]["where"] = "rail_R1"
        self.assertFlags(ev, "comparison.values")

    def test_stopped_without_stop_code_is_a_warning(self):
        ev = self._finish(sample_events(), "stopped", state="pending")
        self.assertIn("run.stop_code", self.rules(ev, "warn"))

    # --- the test scene of an analysis ------------------------------------------------
    @staticmethod
    def _scene(ev, scene, series=None):
        a = by_kind(ev, "analysis.finished")["data"]
        a["scene"] = scene
        if series is not None:
            a["series"] = series
        return ev

    STEP = {"kind": "step", "label": "100 mm step", "position_mm": [600.0, 0.0, 0.0], "size_mm": [200.0, 3000.0, 100.0]}
    ROAD = {"kind": "road_profile", "profile": {"x_mm": [0.0, 100.0, 200.0], "z_mm": {"left": [0.0, 5.0, 0.0],
                                                                                    "right": [0.0, 3.0, 1.0]}}}
    TABLE = {"kind": "tilt_table", "size_mm": [3000.0, 2000.0, 50.0], "angle_channel": "table_angle"}
    ANGLE = {"channel": "table_angle", "unit": "deg", "t_s": [0.0, 1.0], "values": [0.0, 10.0]}

    def test_clean_scene(self):
        ev = self._scene(sample_events(), [self.STEP, self.ROAD, self.TABLE], [self.ANGLE])
        self.assertEqual(self.rules(ev), set())

    def test_road_profile_heights_and_order(self):
        road = copy.deepcopy(self.ROAD)
        road["profile"]["z_mm"]["right"] = [0.0, 3.0]
        self.assertIn("analysis.scene", self.rules(self._scene(sample_events(), [road])))
        road = copy.deepcopy(self.ROAD)
        road["profile"]["x_mm"] = [0.0, 200.0, 100.0]
        self.assertIn("analysis.scene", self.rules(self._scene(sample_events(), [road])))
        self.assertIn("analysis.scene", self.rules(self._scene(sample_events(), [{"kind": "road_profile"}])))

    def test_moving_object_names_a_series_of_its_analysis(self):
        self.assertIn("analysis.scene", self.rules(self._scene(sample_events(), [self.TABLE])))

    def test_negative_size(self):
        step = dict(self.STEP, size_mm=[200.0, -3000.0, 100.0])
        self.assertIn("analysis.scene", self.rules(self._scene(sample_events(), [step])))

    def test_unknown_hook_rule(self):
        ev = sample_events()
        by_kind(ev, "hook.denied")["data"]["rule"] = "H9_made_up"
        self.assertFlags(ev, "hook.rule")

    # --- assembly and interferences ---------------------------------------------------
    @staticmethod
    def _assembly(ev):
        """Put a clean assembly order and interference list (with the tool's own counts) on the analysis."""
        a = by_kind(ev, "analysis.finished")["data"]
        step = lambda n, part, deps=(): {"order": n, "part": part, "direction": "-z", "depends_on": list(deps),
                                         "no_interference": True, "tool_clearance": True, "hand_reach": True}
        a["assembly"] = [step(1, "battery"), step(2, "part:seat", ["battery", "member:cross1"])]
        a["assembly"][1]["tool_clearance"] = False  # goes in, but a tool cannot reach: not blocked
        a["interferences"] = [{"a": "seat", "b": "member cross1", "depth_mm": 4.0, "state": "rest"},
                              {"a": "FL", "b": "rail_L0", "depth_mm": 12.5, "state": "motion",
                               "condition": "steer 30 deg, bump 80 mm"}]
        a["results"] += [{"quantity": "interference_count", "value": 1.0, "unit": "1"},
                         {"quantity": "worst_interference", "value": 4.0, "unit": "mm"},
                         {"quantity": "motion_interference_count", "value": 1.0, "unit": "1"},
                         {"quantity": "worst_motion_interference", "value": 12.5, "unit": "mm"}]
        return a

    def test_clean_assembly_and_interferences(self):
        ev = sample_events()
        self._assembly(ev)
        found = self.findings(ev)
        self.assertEqual([str(f) for f in found if f.rule.startswith("analysis.")], [])
        self.assertEqual({f.rule for f in found if f.level == "error"}, set())

    def test_assembly_order_with_a_gap(self):
        ev = sample_events()
        self._assembly(ev)["assembly"][1]["order"] = 3
        self.assertFlags(ev, "analysis.assembly")

    def test_dependency_that_goes_in_later(self):
        ev = sample_events()
        steps = self._assembly(ev)["assembly"]
        steps[0]["depends_on"] = ["part:seat"]
        self.assertFlags(ev, "analysis.assembly")

    def test_blocked_step_that_goes_in_without_interference(self):
        ev = sample_events()
        self._assembly(ev)["assembly"][1]["blocked_by"] = ["cross1"]
        self.assertFlags(ev, "analysis.assembly")

    def test_step_that_cannot_go_in_without_blocked_by_is_a_warning(self):
        ev = sample_events()
        self._assembly(ev)["assembly"][1]["no_interference"] = False
        self.assertIn("analysis.assembly", self.rules(ev, "warn"))

    def test_part_interfering_with_itself(self):
        ev = sample_events()
        self._assembly(ev)["interferences"][0]["b"] = "seat"
        self.assertFlags(ev, "analysis.interference")

    def test_interference_count_disagrees_with_the_list(self):
        ev = sample_events()
        self._assembly(ev)["interferences"].pop(0)  # the rest count still says 1
        self.assertFlags(ev, "analysis.interference")

    def test_worst_interference_disagrees_with_the_list(self):
        ev = sample_events()
        self._assembly(ev)["interferences"][1]["depth_mm"] = 20.0
        self.assertFlags(ev, "analysis.interference")

    def test_unknown_name_in_an_interference_is_a_warning(self):
        ev = sample_events()
        self._assembly(ev)["interferences"][0]["a"] = "part:steering_rack"
        self.assertIn("analysis.interference", self.rules(ev, "warn"))

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

    @staticmethod
    def _predict(ev, value):
        """Set the sample's prediction to `value` and refill the comparison with signed diffs."""
        by_kind(ev, "prediction.recorded")["data"]["value"] = value
        c = by_kind(ev, "comparison.recorded")["data"]
        c["predicted"]["value"] = value
        c["diff_abs"] = c["analyzed"]["value"] - value
        c["diff_rel"] = c["diff_abs"] / abs(value)
        c["status"] = "match" if abs(c["diff_rel"]) <= c["tolerance_rel"] else "mismatch"
        return c

    def test_analysis_below_the_prediction_gives_negative_diffs(self):
        ev = sample_events()
        c = self._predict(ev, 130.0)
        self.assertLess(c["diff_abs"], 0)
        self.assertEqual(c["status"], "match")
        self.assertEqual(self.rules(ev), set())

    def test_unsigned_diffs_are_flagged(self):
        ev = sample_events()
        c = self._predict(ev, 130.0)
        c["diff_abs"], c["diff_rel"] = abs(c["diff_abs"]), abs(c["diff_rel"])
        self.assertFlags(ev, "comparison.math")

    def test_a_large_negative_diff_is_a_mismatch(self):
        ev = sample_events()
        c = self._predict(ev, 200.0)  # diff_rel = -0.38, tolerance 0.15
        self.assertEqual(c["status"], "mismatch")
        c["status"] = "match"
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

    def test_verdict_margin_in_the_limit_unit(self):
        ev = sample_events()
        v = by_kind(ev, "verdict.recorded")["data"]
        v["margin"] = {"value": v["limit"]["value"] - v["value"]["value"], "unit": "MPa"}
        self.assertEqual(self.rules(ev), set())
        v["margin"] = {"value": 30.0, "unit": "MPa"}
        self.assertFlags(ev, "verdict.math")
        v["margin"] = {"value": v["limit"]["value"] - v["value"]["value"], "unit": "N"}
        self.assertFlags(ev, "verdict.units")

    @staticmethod
    def _zero_limit(ev, margin_pct=None, margin=True):
        """R1 judged against a lower limit of 0 MPa: 123.6 >= 0 passes by 123.6 MPa."""
        lc = next(lc for lc in ev[0]["data"]["load_cases"] if lc["load_case_id"] == "R1-step100")
        lc["criterion"].update(op=">=", limit={"value": 0.0, "unit": "MPa"})
        v = by_kind(ev, "verdict.recorded")["data"]
        v.update(op=">=", limit={"value": 0.0, "unit": "MPa"}, margin_pct=margin_pct)
        if margin:
            v["margin"] = {"value": v["value"]["value"], "unit": "MPa"}
        return ev

    def test_limit_of_zero_takes_null_margin_pct_and_a_margin(self):
        self.assertEqual(self.rules(self._zero_limit(sample_events())), set())
        self.assertFlags(self._zero_limit(sample_events(), margin_pct=0.0), "verdict.math")
        self.assertIn("verdict.margin", self.rules(self._zero_limit(sample_events(), margin=False), "warn"))

    @staticmethod
    def _unsupported(ev, result=4.0, **change):
        """The sample's verdict as an unsupported_load one (contract, 荷重の通り道が無いとき): ana-0001 reports
        unsupported_load `result` (None: not at all), the verdict names the analysis instead of a comparison."""
        if result is not None:
            by_kind(ev, "analysis.finished")["data"]["results"].append(
                {"quantity": "unsupported_load", "value": result, "unit": "1", "where": "occupant.hip"})
        v = by_kind(ev, "verdict.recorded")["data"]
        v.pop("comparison_id")
        v.update(analysis_id="ana-0001", quantity="unsupported_load", value={"value": 4.0, "unit": "1"}, op="<=",
                 limit={"value": 0.0, "unit": "1"}, pass_=None, margin={"value": -4.0, "unit": "1"}, margin_pct=None,
                 basis="unsupported_load", value_from="analysis")
        v["pass"] = v.pop("pass_") or False
        v.update(change)
        return ev

    @staticmethod
    def _past_limit_without_formula(state="pending", status="stopped", code="not_judged"):
        """HK4 (contract, 67c3e74): a case no K0 formula predicts, with an analysis result past its criterion
        (R1 at 200 MPa against 156.7) and no comparison or verdict, does not hold the stop: the run ends
        not judged and the tile stays off green."""
        ev = [e for e in sample_events() if e["kind"] not in ("comparison.recorded", "verdict.recorded")]
        by_kind(ev, "analysis.finished")["data"]["results"][0]["value"] = 200.0
        by_kind(ev, "judgement.updated")["data"].update(state=state, verdict_ids=[], red_count=0)
        by_kind(ev, "run.finished")["data"].update(
            status=status, stop_code=code,
            stop_reason="R1-step100: 基準を超えた結果があるが、手計算 (K0) の式が無いので判定していない")
        return renumber(ev)

    def test_h4_a_past_limit_case_without_a_formula_ends_not_judged(self):
        self.assertEqual(self.rules(self._past_limit_without_formula()), set())
        self.assertFlags(self._past_limit_without_formula(state="green"), "judgement.state")  # never green
        self.assertFlags(self._past_limit_without_formula(status="done", code="done"), "HK4_no_stop_before_judged")

    def test_unsupported_load_verdict_comes_from_the_analysis(self):
        verdict_rules = lambda ev: {r for r in self.rules(ev) if r.startswith("verdict.")}
        self.assertEqual(verdict_rules(self._unsupported(sample_events())), set())
        self.assertFlags(self._unsupported(sample_events(), result=None), "verdict.unsupported_load")
        self.assertFlags(self._unsupported(sample_events(), result=2.0), "verdict.unsupported_load")
        self.assertFlags(self._unsupported(sample_events(), comparison_id="cmp-0001"), "verdict.unsupported_load")
        self.assertFlags(self._unsupported(sample_events(), analysis_id="ana-0009"), "verdict.unsupported_load")
        self.assertFlags(self._unsupported(sample_events(), **{"pass": True}), "verdict.unsupported_load")

    def test_verdict_limit_comes_from_the_criterion(self):
        ev = sample_events()
        v = by_kind(ev, "verdict.recorded")["data"]
        v["limit"] = {"value": 200.0, "unit": "MPa"}
        self.assertFlags(ev, "verdict.criterion")

    @staticmethod
    def _shell_after_green(ev, hot_spot, element=None, then=None, after="judgement.updated"):
        """A frame_shell analysis of R1 (ana-0002) after the sample's green tile (or after the first
        event of kind `after`), with the weld toe's hot-spot stress and the element maximum; then an
        optional judgement.updated state after it."""
        at = next(n for n, e in enumerate(ev) if e["kind"] == after) + 1
        started, finished = copy.deepcopy(by_kind(ev, "analysis.started")), copy.deepcopy(by_kind(ev, "analysis.finished"))
        for e in (started, finished):
            e["data"].update(analysis_id="ana-0002", tool="frame_shell")
            e["t"], e["ts"] = ev[at - 1]["t"], ev[at - 1]["ts"]
        finished["data"].pop("fields", None)
        finished["data"]["results"] = [{"quantity": "max_hot_spot_stress", "value": hot_spot, "unit": "MPa"},
                                       {"quantity": "max_stress", "value": element or hot_spot, "unit": "MPa"}]
        extra = [started, finished]
        if then:
            j = copy.deepcopy(by_kind(ev, "judgement.updated"))
            j["data"].update(state=then)
            j["t"], j["ts"] = ev[at - 1]["t"], ev[at - 1]["ts"]
            extra.append(j)
        ev[at:at] = extra
        return renumber(ev)

    def test_a_later_result_beyond_the_criterion_holds_a_green_tile(self):
        # the sample's limit is 156.7 MPa; a green tile left standing after a shell result beyond it
        self.assertFlags(self._shell_after_green(sample_events(), 200.0), "judgement.state")
        self.assertNotIn("judgement.state", self.rules(self._shell_after_green(sample_events(), 100.0)))
        # the tile turned pending after the result: right (HK4 then speaks for ending the run done)
        self.assertNotIn("judgement.state", self.rules(self._shell_after_green(sample_events(), 200.0, then="pending")))
        # turned green again after it: wrong at that judgement
        self.assertFlags(self._shell_after_green(sample_events(), 200.0, then="green"), "judgement.state")
        # frame_shell is judged on the hot spot, not on its element maximum (contract, 判定に使う量)
        self.assertNotIn("judgement.state", self.rules(self._shell_after_green(sample_events(), 100.0, element=300.0)))

    @staticmethod
    def _stages(ev, **stages):
        """tool.checked for each named tool with its stage (tool_spec.stage), before the design."""
        spec0 = json.loads((contract.EXAMPLES_DIR / "tool_spec.json").read_text(encoding="utf-8"))
        for k, (name, stage) in enumerate(stages.items()):
            ev.insert(1 + k, {"seq": 0, "ts": ev[0]["ts"], "t": ev[0]["t"], "run_id": RUN_ID, "kind": "tool.checked",
                              "agent": "system", "data": dict(copy.deepcopy(spec0), name=name, stage=stage)})
        return renumber(ev)

    def test_h5_heavier_tools_wait_for_the_lighter_load_cases(self):
        # frame_shell (stage 3) after frame_static's R1 (stage 1) passed: allowed
        ev = self._stages(self._shell_after_green(sample_events(), 100.0), frame_static=1, frame_shell=3)
        self.assertNotIn("HK5_light_first", self.rules(ev))
        # on R1 itself before R1 had its verdict: allowed (the heavier tool may be what settles it)
        early = self._stages(self._shell_after_green(sample_events(), 100.0, after="analysis.finished"),
                             frame_static=1, frame_shell=3)
        self.assertNotIn("HK5_light_first", self.rules(early))
        # on R1 after frame_static's verdict on R1 was red: HK5 denies it (revise first)
        red = sample_events()
        by_kind(red, "verdict.recorded")["data"]["pass"] = False
        red = self._stages(self._shell_after_green(red, 100.0, after="verdict.recorded"), frame_static=1, frame_shell=3)
        self.assertIn("HK5_light_first", self.rules(red))
        # on another load case while R1 has no verdict yet: HK5 denies it
        other = self._stages(self._shell_after_green(sample_events(), 100.0, after="analysis.finished"),
                             frame_static=1, frame_shell=3)
        for e in other:
            if e["kind"].startswith("analysis.") and e["data"].get("analysis_id") == "ana-0002":
                e["data"]["load_case_id"] = "R2-step100"
        self.assertFlags(other, "HK5_light_first")
        # a case past the limit (-beyond) is meant to fail: its red does not hold back the heavier tools
        beyond = json.loads(json.dumps(sample_events()).replace("R1-step100", "R1-step100-beyond"))
        by_kind(beyond, "verdict.recorded")["data"]["pass"] = False
        beyond = self._stages(self._shell_after_green(beyond, 100.0, after="verdict.recorded"),
                              frame_static=1, frame_shell=3)
        for e in beyond:
            if e["kind"].startswith("analysis.") and e["data"].get("analysis_id") == "ana-0002":
                e["data"]["load_case_id"] = "R2-step100"
        self.assertNotIn("HK5_light_first", self.rules(beyond))
        # tools of one stage may run side by side, and without stages every tool is stage 1
        self.assertNotIn("HK5_light_first", self.rules(self._stages(
            self._shell_after_green(sample_events(), 100.0, after="analysis.finished"), frame_static=1, frame_shell=1)))
        self.assertNotIn("HK5_light_first", self.rules(self._shell_after_green(sample_events(), 100.0,
                                                                                after="analysis.finished")))

    def _shell_on_r2_while_r1_waits(self, ev, after):
        """frame_shell (stage 3) on R2 started right after the first `after` event, while R1 (frame_static,
        stage 1) has no verdict."""
        ev = self._stages(self._shell_after_green(ev, 100.0, after=after), frame_static=1, frame_shell=3)
        for e in ev:
            if e["kind"].startswith("analysis.") and e["data"].get("analysis_id") == "ana-0002":
                e["data"]["load_case_id"] = "R2-step100"
        return ev

    def test_h5_a_case_that_cannot_be_judged_does_not_hold_the_heavier_tools(self):
        # contract 110b991: no K0 formula predicts R1's max_stress (its prediction is of another quantity)
        ev = sample_events()
        by_kind(ev, "prediction.recorded")["data"]["quantity"] = "max_deflection"
        self.assertNotIn("HK5_light_first", self.rules(self._shell_on_r2_while_r1_waits(ev, "analysis.finished")))
        # R1's mismatch ended with its cause (contract 08cf4d6: ends_case; a plan's expected_mismatch with end)
        base = [e for e in sample_events() if e["kind"] != "verdict.recorded"]
        by_kind(base, "comparison.recorded")["data"]["status"] = "mismatch"
        cause = {"cause_id": "cause-0001", "comparison_id": "cmp-0001", "cause": "parts not mounted",
                 "fix": "none in this run", "resolved": False}
        ev = self._insert(base, "comparison.recorded", "cause.recorded", dict(cause, ends_case=True))
        self.assertNotIn("HK5_light_first", self.rules(self._shell_on_r2_while_r1_waits(ev, "cause.recorded")))
        # before the cause is written, R1 still waits
        self.assertIn("HK5_light_first", self.rules(self._shell_on_r2_while_r1_waits(ev, "comparison.recorded")))
        # a cause without ends_case (or with it false) waits for its fix: R1 still holds the heavier tools
        for mark in ({}, {"ends_case": False}):
            with self.subTest(mark=mark):
                ev = self._insert(base, "comparison.recorded", "cause.recorded", dict(cause, **mark))
                self.assertIn("HK5_light_first",
                              self.rules(self._shell_on_r2_while_r1_waits(ev, "cause.recorded")))
        # ends_case goes without safe_side (the schema's rule)
        ev = self._insert(base, "comparison.recorded", "cause.recorded",
                          dict(cause, ends_case=True, safe_side=True, safe_side_reason="the beam model bounds it"))
        self.assertIn("schema", self.rules(ev))

    @staticmethod
    def _compare_again(ev, n, analyzed, cause=None):
        """After the sample's green tile: frame_static again (ana-000n, max_stress = analyzed) and its
        comparison with the sample's prediction (cmp-000n), with an optional cause of it."""
        at = next(k for k, e in enumerate(ev) if e["kind"] == "agent.stopped")
        started, finished = copy.deepcopy(by_kind(ev, "analysis.started")), copy.deepcopy(by_kind(ev, "analysis.finished"))
        c = copy.deepcopy(by_kind(ev, "comparison.recorded"))
        aid, cid = f"ana-{n:04d}", f"cmp-{n:04d}"
        started["data"]["analysis_id"] = aid
        finished["data"].update(analysis_id=aid, results=[{"quantity": "max_stress", "value": analyzed, "unit": "MPa"}])
        finished["data"].pop("fields", None)
        pv, tol = c["data"]["predicted"]["value"], c["data"]["tolerance_rel"]
        rel = (analyzed - pv) / abs(pv)
        c["data"].update(comparison_id=cid, analysis_id=aid, analyzed={"value": analyzed, "unit": "MPa"},
                         diff_abs=analyzed - pv, diff_rel=rel, status="match" if abs(rel) <= tol else "mismatch")
        new = [started, finished, c]
        if cause is not None:
            new.append({"seq": 0, "ts": c["ts"], "t": c["t"], "run_id": RUN_ID, "kind": "cause.recorded",
                        "agent": c["agent"], "data": dict({"cause_id": f"cause-{n - 1:04d}", "comparison_id": cid,
                                                           "hypothesis": "the joint is stiffer than the beam model",
                                                           "evidence": "local stress at the weld", "resolved": False,
                                                           "resolved_by": None}, **cause)})
        for e in new:
            e["t"], e["ts"] = ev[at - 1]["t"], ev[at - 1]["ts"]
        ev[at:at] = new
        return renumber(ev)

    def test_a_stand_in_quantity_decides_when_the_judged_one_is_near_zero(self):
        def near_zero(stand_in_matches):
            ev = self._compare_again(sample_events(), 2, 150.0)  # a later mismatch of max_stress, no cause
            crit = ev[0]["data"]["load_cases"][0]["criterion"]
            crit.update(limit={"value": 1e9, "unit": "MPa"}, stand_in_quantity="stress_range")  # 150 < 1e-6 x 1e9
            fin = next(e for e in ev if e["kind"] == "analysis.finished" and e["data"]["analysis_id"] == "ana-0002")
            c = copy.deepcopy(next(e for e in ev if e["kind"] == "comparison.recorded"
                                   and e["data"]["comparison_id"] == "cmp-0002"))
            pv = c["data"]["predicted"]["value"]
            got = pv if stand_in_matches else 2 * pv
            fin["data"]["results"].append({"quantity": "stress_range", "value": got, "unit": "MPa"})
            c["data"].update(comparison_id="cmp-0003", quantity="stress_range", analyzed={"value": got, "unit": "MPa"},
                             diff_abs=got - pv, diff_rel=(got - pv) / abs(pv),
                             status="match" if stand_in_matches else "mismatch")
            ev.insert(ev.index(next(e for e in ev if e["kind"] == "agent.stopped")), c)
            return renumber(ev)
        self.assertNotIn("judgement.state", self.rules(near_zero(True)))  # the stand-in matches
        self.assertIn("judgement.state", self.rules(near_zero(False)))

    def test_the_latest_comparison_of_the_judged_quantity_holds_a_green_tile(self):
        # the limit is 156.7 MPa: 150 MPa is within it, but 21 % off the prediction (a mismatch)
        self.assertIn("judgement.state", self.rules(self._compare_again(sample_events(), 2, 150.0)))
        self.assertIn("judgement.state", self.rules(self._compare_again(sample_events(), 2, 150.0, {"safe_side": False})))
        safe = {"safe_side": True, "safe_side_reason": "the prediction is on the safe side"}
        self.assertNotIn("judgement.state", self.rules(self._compare_again(sample_events(), 2, 150.0, safe)))
        # a later match of the same load case and quantity replaces the open mismatch (contract 5e016ee)
        ev = self._compare_again(self._compare_again(sample_events(), 2, 150.0), 3, 125.0)
        self.assertNotIn("judgement.state", self.rules(ev))

    def test_comparison_quantity_is_the_result_compared(self):
        def compared(name, value=None):
            ev = sample_events()
            a = by_kind(ev, "analysis.finished")["data"]
            analyzed = by_kind(ev, "comparison.recorded")["data"]["analyzed"]
            a["results"].append({"quantity": "max_hot_spot_stress", "value": value or analyzed["value"], "unit": "MPa"})
            by_kind(ev, "comparison.recorded")["data"]["quantity"] = name
            return ev
        self.assertNotIn("comparison.values", self.rules(compared("max_hot_spot_stress")))  # prediction says max_stress
        self.assertFlags(compared("no_such_result"), "comparison.values")
        self.assertFlags(compared("max_hot_spot_stress", value=99.0), "comparison.values")

    def test_analysis_ids_follow_analysis_started(self):
        def two(first_started_first):
            ev = self._shell_after_green(sample_events(), 100.0)
            s2 = next(e for e in ev if e["kind"] == "analysis.started" and e["data"]["analysis_id"] == "ana-0002")
            f2 = next(e for e in ev if e["kind"] == "analysis.finished" and e["data"]["analysis_id"] == "ana-0002")
            ev.remove(s2)
            ev.remove(f2)
            s1 = next(n for n, e in enumerate(ev) if e["kind"] == "analysis.started")
            for e in (s2, f2):
                e["t"], e["ts"] = ev[s1]["t"], ev[s1]["ts"]
            # ana-0002 starts (after or before ana-0001) and finishes before ana-0001 does
            ev[s1 + 1:s1 + 1] = [s2, f2] if first_started_first else []
            if not first_started_first:
                ev[s1:s1] = [s2, f2]
            return renumber(ev)
        self.assertNotIn("ids.order", self.rules(two(True), "warn"))
        self.assertIn("ids.order", self.rules(two(False), "warn"))

    def test_judgement_red_count(self):
        ev = sample_events()
        by_kind(ev, "judgement.updated")["data"]["red_count"] = 1
        self.assertFlags(ev, "judgement.state")

    # --- record updates (same id re-sent, last event wins) ---------------------------
    def _resent(self, ev, kind, **changes):
        again = copy.deepcopy(by_kind(ev, kind))
        again["data"].update(changes)
        again["t"], again["ts"] = ev[-2]["t"], ev[-2]["ts"]
        ev.insert(len(ev) - 1, again)  # before run.finished
        return renumber(ev)

    def test_comparison_gets_its_cause_id_by_an_update(self):
        ev = sample_events()
        cause = {"seq": 0, "ts": ev[-2]["ts"], "t": ev[-2]["t"], "run_id": RUN_ID, "kind": "cause.recorded",
                 "agent": "cae.strength", "data": {"cause_id": "cause-0001", "comparison_id": "cmp-0001",
                                                   "cause": "qa check", "fix": "none", "resolved": False}}
        ev.insert(len(ev) - 1, cause)
        ev = self._resent(ev, "comparison.recorded", cause_id="cause-0001")
        found = self.findings(ev)
        self.assertEqual([str(f) for f in found if f.level == "error"], [])

    def resolved_flow(self):
        """Mismatch -> cause -> comparison gets cause_id -> revised prediction -> match
        -> cause resolved -> verdict from the match, on the sample's R1 case."""
        ev = sample_events()
        at = by_kind(ev, "comparison.recorded")
        stamp = {"ts": at["ts"], "t": at["t"], "run_id": RUN_ID, "agent": "cae.strength", "seq": 0}
        first_pred = by_kind(ev, "prediction.recorded")["data"]
        first_pred["value"] = 100.0  # the first hand calculation is 19 % low
        cmp1 = at["data"]
        cmp1.update(predicted={"value": 100.0, "unit": "MPa"}, diff_abs=cmp1["analyzed"]["value"] - 100.0,
                    status="mismatch")
        cmp1["diff_rel"] = cmp1["diff_abs"] / 100.0
        cause = {"cause_id": "cause-0001", "comparison_id": "cmp-0001", "cause": "the step force couple was left out",
                 "fix": "add the couple of the push at the axle", "resolved": False}
        pred2 = dict(copy.deepcopy(first_pred), prediction_id="pred-0002", value=120.0)
        cmp2 = dict(copy.deepcopy(cmp1), comparison_id="cmp-0002", prediction_id="pred-0002",
                    predicted={"value": 120.0, "unit": "MPa"}, status="match")
        cmp2["diff_abs"] = cmp2["analyzed"]["value"] - 120.0
        cmp2["diff_rel"] = cmp2["diff_abs"] / 120.0
        added = [
            ("cause.recorded", cause),
            ("comparison.recorded", dict(copy.deepcopy(cmp1), cause_id="cause-0001")),
            ("prediction.recorded", pred2),
            ("comparison.recorded", cmp2),
            ("cause.recorded", dict(cause, resolved=True, resolved_by="cmp-0002")),
        ]
        i = ev.index(at) + 1
        ev[i:i] = [dict(stamp, kind=k, data=copy.deepcopy(d)) for k, d in added]
        by_kind(ev, "verdict.recorded")["data"]["comparison_id"] = "cmp-0002"
        for e in ev[i:]:
            e["t"], e["ts"] = max(e["t"], at["t"]), max(e["ts"], at["ts"])
        return renumber(ev)

    def test_mismatch_resolved_by_a_revised_prediction(self):
        found = self.findings(self.resolved_flow())
        self.assertEqual([str(f) for f in found if f.level == "error"], [])
        self.assertEqual({f.rule for f in found if f.level == "warn"}, {"HK2_tool_usable"})

    def test_revised_prediction_without_a_cause_is_flagged(self):
        ev = [e for e in self.resolved_flow() if not (e["kind"] == "cause.recorded")]
        for e in ev:
            if e["kind"] == "comparison.recorded":
                e["data"].pop("cause_id", None)
        rules = self.rules(renumber(ev))
        self.assertIn("HK3_cause_before_continue", rules)

    def test_resolved_by_must_be_a_later_match_of_the_same_quantity(self):
        ev = self.resolved_flow()
        last_cause = [e for e in ev if e["kind"] == "cause.recorded"][-1]
        last_cause["data"]["resolved_by"] = "cmp-0001"  # the mismatch itself
        self.assertFlags(ev, "cause.resolved")

    # --- a verdict from an explained safe-side mismatch (basis explained_safe_side) -----
    def safe_side_flow(self, predicted=150.0, op="<=", limit=156.66666666666666):
        """Mismatch -> cause with safe_side and its reason -> comparison gets cause_id ->
        verdict from the prediction, on the sample's R1 case (analysis 123.6 MPa)."""
        ev = sample_events()
        lc = next(lc for lc in ev[0]["data"]["load_cases"] if lc["load_case_id"] == "R1-step100")
        lc["criterion"].update(op=op, limit={"value": limit, "unit": "MPa"})
        c = self._predict(ev, predicted)
        self.assertEqual(c["status"], "mismatch")
        at = by_kind(ev, "comparison.recorded")
        stamp = {"ts": at["ts"], "t": at["t"], "run_id": RUN_ID, "agent": "cae.strength", "seq": 0}
        cause = {"cause_id": "cause-0001", "comparison_id": "cmp-0001", "cause": "the joints are taken as pinned",
                 "fix": "model the gusset stiffness", "resolved": False, "safe_side": True,
                 "safe_side_reason": "pinned joints give the larger rail moment for this load"}
        i = ev.index(at) + 1
        ev[i:i] = [dict(stamp, kind="cause.recorded", data=cause),
                   dict(stamp, kind="comparison.recorded", data=dict(copy.deepcopy(c), cause_id="cause-0001"))]
        val = c["predicted"]["value"]
        room = limit - val if op in ("<=", "<") else val - limit
        by_kind(ev, "verdict.recorded")["data"].update(
            basis="explained_safe_side", value_from="prediction", cause_id="cause-0001",
            value=dict(c["predicted"]), op=op, limit={"value": limit, "unit": "MPa"},
            margin_pct=100.0 * room / abs(limit), **{"pass": room >= 0})
        return renumber(ev)

    def test_safe_side_verdict_from_an_explained_mismatch(self):
        found = self.findings(self.safe_side_flow())
        self.assertEqual([str(f) for f in found if f.level == "error"], [])
        self.assertEqual({f.rule for f in found if f.level == "warn"}, {"HK2_tool_usable"})

    def test_safe_side_verdict_for_a_lower_limit(self):
        # criterion >= 90 MPa: the prediction 100 is below the analysis 123.6, the unfavourable side
        self.assertEqual(self.rules(self.safe_side_flow(100.0, ">=", 90.0)), set())

    def test_safe_side_with_the_prediction_on_the_favourable_side(self):
        # upper limit with the prediction 100 below the analysis 123.6, and the mirror case
        self.assertFlags(self.safe_side_flow(100.0), "verdict.safe_side")
        self.assertFlags(self.safe_side_flow(150.0, ">=", 90.0), "verdict.safe_side")

    def test_safe_side_verdict_needs_a_cause_that_says_so(self):
        ev = self.safe_side_flow()
        cause = by_kind(ev, "cause.recorded")["data"]
        del cause["safe_side"], cause["safe_side_reason"]
        self.assertFlags(ev, "verdict.safe_side")
        ev = self.safe_side_flow()
        by_kind(ev, "cause.recorded")["data"]["safe_side_reason"] = "  "
        self.assertFlags(ev, "verdict.safe_side")

    def test_safe_side_verdict_names_its_own_earlier_cause(self):
        ev = self.safe_side_flow()
        by_kind(ev, "verdict.recorded")["data"].pop("cause_id")
        self.assertFlags(ev, "verdict.safe_side")
        ev = self.safe_side_flow()
        cause = by_kind(ev, "cause.recorded")
        ev.remove(cause)
        ev.insert(len(ev) - 1, cause)  # after the verdict
        self.assertFlags(renumber(ev), "verdict.safe_side")

    def test_safe_side_verdict_from_a_match(self):
        ev = sample_events()
        by_kind(ev, "verdict.recorded")["data"].update(basis="explained_safe_side", value_from="prediction",
                                                        cause_id="cause-0001")
        found = [f.message for f in self.findings(ev) if f.rule == "verdict.safe_side"]
        self.assertTrue(any("is a match" in m for m in found), found)

    def test_safe_side_verdict_takes_the_prediction(self):
        ev = self.safe_side_flow()
        by_kind(ev, "verdict.recorded")["data"]["value_from"] = "analysis"
        self.assertFlags(ev, "verdict.values")
        ev = self.safe_side_flow()
        by_kind(ev, "verdict.recorded")["data"]["value"] = {"value": 123.60000000000001, "unit": "MPa"}
        self.assertFlags(ev, "verdict.values")

    # --- the listing of what the hooks did (--effects) -------------------------------
    def effects(self, events):
        return "\n".join(cr.run_effects(write_run(self.root / f"r{len(list(self.root.iterdir()))}", events)))

    def test_effects_of_the_sample(self):
        text = self.effects(sample_events())
        self.assertIn("HK1_prediction_first cae.strength strength analyze (live); "
                      "then prediction.recorded pred-0001 (seq=6)", text)
        self.assertIn("mismatches: 0", text)
        self.assertIn("ver-0001 R1 max_stress 123.6 MPa <= 156.7 MPa: pass, margin 21.1 %; match "
                      "(value from the analysis, cmp-0001)", text)
        self.assertIn("judgement v1 strength: green", text)

    def test_effects_of_a_safe_side_verdict(self):
        text = self.effects(self.safe_side_flow())
        self.assertIn("cmp-0001 R1 max_stress: predicted 150 MPa, analyzed 123.6 MPa, diff -17.6 % (tolerance 15 %)", text)
        self.assertIn("safe side: pinned joints give the larger rail moment", text)
        self.assertIn("explained_safe_side (value from the prediction, cause-0001, cmp-0001)", text)

    def test_run_finished_while_an_agent_was_still_working(self):
        ev = sample_events()
        ev.remove(by_kind(ev, "agent.stopped"))
        self.assertIn("run.agents", self.rules(renumber(ev), "warn"))

    def test_effects_show_the_stop_block_limit_and_who_was_still_working(self):
        ev = sample_events()
        at = by_kind(ev, "agent.said")
        block = {"ts": at["ts"], "t": at["t"], "run_id": RUN_ID, "seq": 0, "kind": "hook.blocked_stop", "agent": "lead",
                 "text": "pending", "data": {"hook": "Stop", "rule": "HK4_no_stop_before_judged", "count": 1, "limit": 1}}
        ev.insert(ev.index(at) + 1, block)
        ev.remove(by_kind(ev, "agent.stopped"))
        text = self.effects(renumber(ev))
        self.assertIn("block limit reached (1 of 1): the next stop ends the run, while cae.strength were still working", text)
        self.assertIn("still working at the end: cae.strength", text)

    def test_effects_name_a_denial_not_followed_as_asked(self):
        ev = sample_events()
        by_kind(ev, "hook.denied")["data"]["rule"] = "HK3_cause_before_continue"  # next is a prediction, not a cause
        self.assertIn("not the cause.recorded the rule asks for", self.effects(ev))

    def test_match_verdict_takes_the_analysis(self):
        ev = sample_events()
        by_kind(ev, "verdict.recorded")["data"]["value_from"] = "prediction"
        self.assertFlags(ev, "verdict.values")

    def test_update_may_not_change_a_recorded_number(self):
        ev = self._resent(sample_events(), "prediction.recorded", value=130.0)
        self.assertFlags(ev, "record.changed")

    def test_comparison_update_may_not_change_its_values(self):
        ev = sample_events()
        ev = self._resent(ev, "comparison.recorded", status="mismatch")
        self.assertFlags(ev, "record.changed")

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

    # --- series, knowledge rules and shell meshes ---------------------------------------
    @staticmethod
    def _insert(ev, after_kind, kind, data, **fields):
        """ev with an event of kind put right after the first after_kind (same time)."""
        at = next(n for n, e in enumerate(ev) if e["kind"] == after_kind) + 1
        prev = ev[at - 1]
        ev.insert(at, {"seq": 0, "ts": prev["ts"], "t": prev["t"], "run_id": RUN_ID, "kind": kind,
                       "agent": "system", **fields, "data": data})
        return renumber(ev)

    @staticmethod
    def _example(name):
        return json.loads((contract.EXAMPLES_DIR / f"{name}.json").read_text(encoding="utf-8"))

    def _rule_check(self, after="design.created", knowledge=False, **change):
        rc = self._example("rule_check")
        rc.update(targets=["battery", "seat"],
                  hits=[dict(rc["hits"][0], part_id="battery")],
                  not_evaluable=[dict(rc["not_evaluable"][0], part_id="seat")])
        rc.update(change)
        ev = self._insert(sample_events(), after, "rule_check.recorded", rc, design_version=1)
        if knowledge:
            ev = self._insert(ev, "design.created", "knowledge.imported", self._example("knowledge_imported"))
        return ev

    def test_rule_check(self):
        self.assertEqual(self.rules(self._rule_check(knowledge=True)), set())
        self.assertFlags(self._rule_check(**{"pass": True}), "rule_check.pass")
        self.assertFlags(self._rule_check(targets=["seat"]), "rule_check.targets")  # the hit is not a target
        both = self._rule_check()
        by_kind(both, "rule_check.recorded")["data"]["not_evaluable"][0]["part_id"] = "battery"
        self.assertFlags(both, "rule_check.targets")
        self.assertFlags(self._rule_check(after="run.started"), "rule_check.design")
        self.assertFlags(self._rule_check(knowledge=True, rule_id="SM-99"), "rule_check.rule")
        self.assertIn("rule_check.targets", self.rules(self._rule_check(targets=["battery", "seat", "gearbox"]), "warn"))

    def _system_red(self, category="system", red_count=1, state="red", hits=True):
        """The rule_check sample with its rule SM-01 of the given category, then the v1 assembly tile."""
        ev = self._rule_check(knowledge=True) if hits else self._rule_check(knowledge=True, hits=[], **{"pass": True})
        by_kind(ev, "knowledge.imported")["data"]["rules"][0]["category"] = category
        tile = {"design_version": 1, "domain": "assembly", "state": state, "red_count": red_count,
                "verdict_ids": [], "summary": "系の欠け 1 件"}
        return self._insert(ev, "rule_check.recorded", "judgement.updated", tile, design_version=1)

    def test_a_system_rule_hitting_a_part_turns_the_assembly_tile_red(self):
        # hiraku.tools.judge compute_judgement: red_count counts the system rules' misses too
        self.assertNotIn("judgement.state", self.rules(self._system_red()))
        self.assertIn("judgement.state", self.rules(self._system_red(category="sheet_metal")))  # not a system rule
        self.assertIn("judgement.state", self.rules(self._system_red(red_count=0)))
        self.assertIn("judgement.state", self.rules(self._system_red(state="pending", red_count=1)))
        self.assertIn("judgement.state", self.rules(self._system_red(hits=False)))  # nothing hits: not red

    def test_rule_check_on_the_whole_design(self):
        rc = self._example("rule_check")
        whole = dict(targets=["design"], hits=[dict(rc["hits"][0], part_id="design")], not_evaluable=[])
        events = self._rule_check(knowledge=True, **whole)
        self.assertEqual(self.rules(events), set())
        self.assertNotIn("rule_check.targets", self.rules(events, "warn"))  # "design" is not a missing part
        self.assertFlags(self._rule_check(**dict(whole, targets=["battery"])), "rule_check.targets")
        self.assertNotIn("rule_check.targets", self.rules(self._rule_check(targets=["battery", "seat", "design"]), "warn"))

    def _mesh(self, field=None, after="design.created", **change):
        m = self._example("mesh")
        m.update(element_member=["rail_L0", "rail_L0"], member_thickness_mm={"rail_L0": 2.3})
        m.update(change)
        ev = self._insert(sample_events(), after, "mesh.created", m, design_version=1)
        if field is not None:
            by_kind(ev, "analysis.finished")["data"]["fields"].append(
                dict({"kind": "stress", "unit": "MPa", "surface": "max", "mesh_id": "mesh-0001"}, **field))
        return ev

    def test_mesh(self):
        self.assertEqual(self.rules(self._mesh(field={"per_element": [80.0, 95.5]})), set())
        self.assertEqual(self.rules(self._mesh(field={"kind": "displacement", "unit": "mm",
                                                      "per_mesh_node": [[0.0, 0.0, 0.1]] * 6})), set())
        self.assertFlags(self._mesh(elements=[[0, 1, 4, 3], [1, 2, 6, 4]]), "mesh.elements")  # 6 nodes: 0..5
        self.assertFlags(self._mesh(elements=[[0, 1, 4, 4], [1, 2, 5, 4]]), "mesh.elements")
        self.assertFlags(self._mesh(element_member=["rail_L0"]), "mesh.members")
        self.assertFlags(self._mesh(element_member=["rail_L0", "rail_R0"]), "mesh.thickness")
        self.assertFlags(self._mesh(after="run.started"), "mesh.design")
        self.assertIn("mesh.nodes", self.rules(self._mesh(nodes_mm=self._example("mesh")["nodes_mm"] + [[0, 0, 0]]), "warn"))
        ev = self._mesh()
        self._insert(ev, "mesh.created", "mesh.created", dict(by_kind(ev, "mesh.created")["data"], mesh_id="mesh-0002"),
                     design_version=1)
        self.assertFlags(ev, "mesh.version")

    QUALITY = {"verdict": "ok",
               "metrics": {"aspect_ratio": {"worst": 1.0, "p95": 1.0, "limit": 5.0, "over_limit": 0, "worst_element": 0},
                           "min_angle_deg": {"worst": 90.0, "limit": 30.0, "over_limit": 0}},
               "per_element": {"aspect_ratio": [1.0, 1.0], "min_angle_deg": [90.0, 90.0]},
               "topology": {"free_edges": 6, "coincident_nodes": 0, "unconnected_groups": 1, "flipped_normals": 0}}

    def _quality(self, field=None, **change):
        q = copy.deepcopy(self.QUALITY)
        for path, value in change.items():  # metrics__aspect_ratio__worst=1.5
            *keys, last = path.split("__")
            d = q
            for k in keys:
                d = d[k]
            d[last] = value
        return self._mesh(field=field, quality=q)

    def test_mesh_quality(self):
        found = self.findings(self._quality())
        self.assertEqual([str(f) for f in found if f.rule.startswith("mesh")], [])
        self.assertFlags(self._quality(metrics__aspect_ratio__worst=2.0), "mesh.quality")  # per_element says 1
        self.assertFlags(self._quality(metrics__min_angle_deg__over_limit=1), "mesh.quality")  # none below 30
        self.assertFlags(self._quality(per_element__aspect_ratio=[1.0]), "mesh.quality")
        self.assertFlags(self._quality(metrics__aspect_ratio__worst_element=2), "mesh.quality")
        over = self._quality(metrics__aspect_ratio__limit=0.5, metrics__aspect_ratio__over_limit=2)
        self.assertFlags(over, "mesh.quality")  # past the limit with verdict ok
        self.assertEqual({f.rule for f in self.findings(self._quality(verdict="warn", metrics__aspect_ratio__limit=0.5,
                                                                      metrics__aspect_ratio__over_limit=2))
                          if f.level == "error"}, set())
        # measured apart: the solver's worst or topology differs -> warnings
        apart = self._quality(metrics__aspect_ratio__worst=1.5, per_element={}, topology__free_edges=4)
        self.assertEqual([f.rule for f in self.findings(apart) if f.level == "error"], [])
        warned = [f.message for f in self.findings(apart) if f.rule == "mesh.quality" and f.level == "warn"]
        self.assertTrue(any("aspect_ratio" in s for s in warned) and any("free_edges" in s for s in warned), warned)

    def test_collapsed_element_and_a_fail_verdict(self):
        nodes = self._example("mesh")["nodes_mm"]
        nodes[4] = list(nodes[1])  # element 0 [0, 1, 4, 3] loses its edge 1-4
        ev = self._mesh(nodes_mm=nodes)
        self.assertFlags(ev, "mesh.quality")
        ev = self._quality(field={"per_element": [80.0, 95.5]}, verdict="fail")
        self.assertFlags(ev, "mesh.quality")  # an ok analysis drew on a mesh the solver must refuse

    def test_fields_on_a_mesh(self):
        self.assertFlags(self._mesh(field={"per_element": [80.0]}), "field.mesh")  # 2 elements
        self.assertFlags(self._mesh(field={"kind": "displacement", "unit": "mm", "per_mesh_node": [0.1] * 5}), "field.mesh")
        self.assertFlags(self._mesh(field={"per_element": [80.0, 95.5], "mesh_id": "mesh-0002"}), "field.mesh")
        self.assertFlags(self._mesh(field={"per_element": [80.0, 95.5]}, after="analysis.finished"), "field.mesh")

    def test_series_axis_and_values(self):
        def with_series(series):
            ev = sample_events()
            by_kind(ev, "analysis.finished")["data"]["series"] = [series]
            return ev
        self.assertEqual(self.rules(with_series({"channel": "z", "unit": "mm", "t_s": [0.0, 0.1], "values": [0.0, 1.0]})), set())
        self.assertFlags(with_series({"channel": "z", "unit": "mm", "t_s": [0.0, 0.1, 0.2], "values": [0.0, 1.0]}),
                         "analysis.series")
        self.assertFlags(with_series({"channel": "camber", "unit": "deg", "values": [0.0, 1.0],
                                      "x": {"quantity": "wheel_travel", "unit": "mm", "values": [0.0]}}), "analysis.series")



def modular(red=True, case_modules=("suspension",), proposals=None, build=None, modules=True):
    """The sample run split into modules (contract d9631a5): R1 red in v1 (when red), the suspension
    designer proposes wheel_radius 320 mm, the packager builds v2 from it. proposals: the
    module_change data to propose instead; build: changes to v2's design data."""
    ev = sample_events()
    d1 = by_kind(ev, "design.created")["data"]
    if modules:
        d1["modules"] = [
            {"module_id": "package", "owner": "packager", "envelope": [], "variables": ["wheelbase", "track"]},
            {"module_id": "frame", "owner": "designer.frame", "envelope": [], "variables": []},
            {"module_id": "suspension", "owner": "designer.suspension", "envelope": [],
             "variables": ["wheel_radius"], "choices": ["tyre"]},
            {"module_id": "powertrain", "owner": "designer.powertrain", "envelope": [], "variables": []}]
    for e in ev:
        if e["kind"] in ("run.started", "project.updated"):
            for lc in e["data"]["load_cases"]:
                lc["modules"] = list(case_modules)
    if red:
        by_kind(ev, "verdict.recorded")["data"]["pass"] = False
    if proposals is None:
        proposals = [{"change_id": "chg-0001", "module_id": "suspension", "agent_id": "designer.suspension",
                      "reason": "a larger wheel takes the step", "load_case_ids": ["R1-step100"],
                      "changes": {"wheel_radius": {"value": 320.0, "unit": "mm"}}}]
    at = next(k for k, e in enumerate(ev) if e["kind"] == "agent.stopped")
    stamp = {"seq": 0, "ts": ev[at]["ts"], "t": ev[at]["t"], "run_id": RUN_ID}
    new = [dict(stamp, kind="design.change_proposed", agent=x.get("agent_id"), data=x) for x in proposals]
    d2 = copy.deepcopy(d1)
    d2.update(design_version=2, parent_version=1, change_reason="the round's proposals: chg-0001",
              module_changes=copy.deepcopy(proposals) if modules else [])
    for x in proposals:
        d2["variables"].update(x.get("changes") or {})
    d2.update(build or {})
    new.append(dict(stamp, kind="design.created", agent="packager", data=d2))
    ev[at + 1:at + 1] = new
    return renumber(ev)


class Modules(unittest.TestCase):
    """HK6_module_scope (contract d9631a5): module designers propose, the packager builds."""

    setUp, tearDown, findings = CheckRun.setUp, CheckRun.tearDown, CheckRun.findings

    def hk6(self, ev, level="error"):
        return {f.message for f in self.findings(ev) if f.rule == "HK6_module_scope" and f.level == level}

    def test_a_round_by_the_rules(self):
        ev = modular()
        self.assertEqual(self.hk6(ev), set())
        self.assertEqual(self.hk6(ev, "warn"), set())
        # the packager's own change of a package variable, listed once in module_changes
        pkg = {"change_id": "chg-0002", "module_id": "package", "agent_id": "packager", "reason": "longer",
               "design_version": 1,
               "changes": {"wheelbase": {"value": 1900.0, "unit": "mm"}}}
        ev = modular()
        d2 = [e for e in ev if e["kind"] == "design.created"][-1]["data"]
        d2["module_changes"].append(pkg)
        d2["variables"]["wheelbase"] = pkg["changes"]["wheelbase"]
        self.assertEqual(self.hk6(ev), set())
        pkg["design_version"] = 2  # the package change is made on the version before
        self.assertTrue(any("package change chg-0002 says design_version 2" in m for m in self.hk6(ev)))

    def test_a_proposal_stays_in_its_module(self):
        base = modular(proposals=None)
        good = [e for e in base if e["kind"] == "design.change_proposed"][0]["data"]
        for why, change in (("another designer", {"agent_id": "designer.frame"}),
                            ("a package variable", {"changes": {"wheelbase": {"value": 1900.0, "unit": "mm"}}}),
                            ("a choice not its own", {"choices": {"frame_type": "spaceframe"}}),
                            ("the package itself", {"module_id": "package", "agent_id": "packager"}),
                            ("made on another version", {"design_version": 2})):
            with self.subTest(why):
                self.assertTrue(self.hk6(modular(proposals=[dict(good, **change)])), why)
        self.assertEqual(self.hk6(modular(proposals=[dict(good, choices={"tyre": "wide"})])), set())
        self.assertEqual(self.hk6(modular(proposals=[dict(good, design_version=1)])), set())  # contract 18c2679

    def test_a_red_goes_to_the_modules_its_load_case_names(self):
        self.assertTrue(self.hk6(modular(case_modules=("frame",))))
        self.assertTrue(self.hk6(modular(red=False)))

    def test_a_red_system_rule_lets_a_designer_propose(self):
        nothing = "chg-0001 proposed while nothing in v1 is red"
        self.assertIn(nothing, self.hk6(modular(red=False, proposals=[self._no_case()])))

        def with_rule(category):
            ev = modular(red=False, proposals=[self._no_case()])
            rc = CheckRun._example("rule_check")
            rc.update(targets=["battery"], hits=[dict(rc["hits"][0], part_id="battery")], not_evaluable=[])
            ev = CheckRun._insert(ev, "design.created", "rule_check.recorded", rc, design_version=1)
            k = CheckRun._example("knowledge_imported")
            k["rules"][0]["category"] = category
            return CheckRun._insert(ev, "design.created", "knowledge.imported", k)
        self.assertEqual(self.hk6(with_rule("system")), set())
        self.assertIn(nothing, self.hk6(with_rule("sheet_metal")))

    def test_a_person_s_answer_to_a_rule_hit_lets_its_part_s_module_propose(self):
        # docs/contract.md (規則の当たりを人に聞く): rule_answers revise_design for AS-01 on the battery lets the
        # powertrain designer propose, though no red load case names powertrain (N13 run 20261105-094333)
        prop = {"change_id": "chg-0001", "module_id": "powertrain", "agent_id": "designer.powertrain",
                "reason": "AS-01: split the battery", "changes": {}}

        def run(answer):
            ev = modular(case_modules=("suspension",), proposals=[prop])
            for e in ev:
                if e["kind"] == "design.created":
                    e["data"]["parts"][0]["module"] = "powertrain"  # the battery
            ev = CheckRun._insert(ev, "agent.stopped", "hearing.asked", {"questions": [], "rule_questions": [
                {"rule_id": "AS-01", "parts": ["battery"], "text": "split it or drop the rule?",
                 "choices": ["revise_design", "drop_rule"]}]}, agent="lead")
            ev = CheckRun._insert(ev, "hearing.asked", "hearing.answered", {"answers": [], "rule_answers": [
                {"rule_id": "AS-01", "choice": answer}]}, agent="lead")
            return {m for m in self.hk6(ev) if "chg-0001 from powertrain" in m}
        self.assertEqual(run("revise_design"), set())
        self.assertTrue(run("drop_rule"))

    @staticmethod
    def _no_case():
        return {"change_id": "chg-0001", "module_id": "suspension", "agent_id": "designer.suspension",
                "reason": "a system rule is red", "changes": {"wheel_radius": {"value": 320.0, "unit": "mm"}}}

    def test_an_optional_variable_left_out_is_its_default(self):
        # the generator writes an optional variable only when it differs from its default
        from unittest import mock

        def warns(default):
            ev = modular()
            del [e for e in ev if e["kind"] == "design.created"][-1]["data"]["variables"]["wheel_radius"]
            spec = [{"name": "wheel_radius", "unit": "mm", "default": default, "value": default}]
            with mock.patch("hiraku.designgen.variables", lambda d: spec):
                return {m for m in self.hk6(ev, "warn") if "as chg-0001 set it" in m}
        self.assertEqual(warns(320.0), set())
        self.assertEqual(len(warns(300.0)), 1)

    def test_only_the_packager_builds_the_round(self):
        for why, build in (("a designer made it", None),
                           ("module_changes missing", {"module_changes": []}),
                           ("module_changes unlike the proposal", "unlike"),
                           ("a package variable moved without a package change",
                            {"variables": {"wheelbase": {"value": 1900.0, "unit": "mm"},
                                           "track": {"value": 1300.0, "unit": "mm"},
                                           "wheel_radius": {"value": 320.0, "unit": "mm"}}})):
            with self.subTest(why):
                ev = modular(build=build if isinstance(build, dict) else None)
                v2 = [e for e in ev if e["kind"] == "design.created"][-1]
                if build is None:
                    v2["agent"] = "designer"
                elif build == "unlike":
                    v2["data"]["module_changes"][0]["reason"] = "another reason"
                self.assertTrue(self.hk6(ev), why)

    def test_a_later_proposal_of_a_module_replaces_its_earlier_one(self):
        # docs/contract.md (8762a06): the same module proposing again in the round replaces its proposal;
        # build_version lists only the later one
        first = {"change_id": "chg-0001", "module_id": "suspension", "agent_id": "designer.suspension",
                 "reason": "a larger wheel takes the step", "load_case_ids": ["R1-step100"],
                 "changes": {"wheel_radius": {"value": 340.0, "unit": "mm"}}}
        again = dict(first, change_id="chg-0002", reason="refused: smaller",
                     changes={"wheel_radius": {"value": 320.0, "unit": "mm"}})
        ev = modular(proposals=[first, again])
        d2 = [e for e in ev if e["kind"] == "design.created"][-1]["data"]
        d2["module_changes"] = [copy.deepcopy(again)]
        d2["variables"]["wheel_radius"] = again["changes"]["wheel_radius"]
        self.assertEqual(self.hk6(ev), set())
        # the two listed (the earlier one not replaced) is wrong, and so is the earlier one alone
        for listed in ([first, again], [first]):
            with self.subTest(listed=[x["change_id"] for x in listed]):
                d2["module_changes"] = copy.deepcopy(listed)
                self.assertTrue(any("each module's last" in m for m in self.hk6(ev)), self.hk6(ev))
        # another module's proposal stays
        other = {"change_id": "chg-0003", "module_id": "frame", "agent_id": "designer.frame", "reason": "x",
                 "load_case_ids": ["R1-step100"], "changes": {}}
        ev = modular(case_modules=("suspension", "frame"), proposals=[first, other, again])
        d2 = [e for e in ev if e["kind"] == "design.created"][-1]["data"]
        d2["module_changes"] = [copy.deepcopy(again), copy.deepcopy(other)]
        d2["variables"]["wheel_radius"] = again["changes"]["wheel_radius"]
        self.assertEqual(self.hk6(ev), set())

    def test_a_variable_no_module_lists_may_follow_with_a_warning(self):
        ev = modular()
        d2 = [e for e in ev if e["kind"] == "design.created"][-1]["data"]
        d2["variables"]["wheel_inertia"] = {"value": 2.0, "unit": "kg*m^2"}  # worked out from wheel_radius
        self.assertEqual(self.hk6(ev), set())
        self.assertTrue(any("wheel_inertia, which no module lists" in m for m in self.hk6(ev, "warn")))
        # one the generator marks derived (spec derived: True) follows without a word
        from unittest import mock
        with mock.patch.object(cr.RunChecker, "_derived", staticmethod(lambda d: {"wheel_inertia"})):
            self.assertEqual(self.hk6(ev, "warn"), set())

    def test_one_variable_is_changed_once_a_round(self):
        one = {"change_id": "chg-0001", "module_id": "suspension", "agent_id": "designer.suspension",
               "reason": "r", "changes": {"wheel_radius": {"value": 320.0, "unit": "mm"}}}
        two = dict(one, change_id="chg-0002", changes={"wheel_radius": {"value": 330.0, "unit": "mm"}})
        # the same module twice: the later proposal replaces the earlier (docs/contract.md, 8762a06), no clash
        ev = modular(proposals=[one, two])
        [e for e in ev if e["kind"] == "design.created"][-1]["data"]["module_changes"] = [copy.deepcopy(two)]
        self.assertFalse(any("all change" in m for m in self.hk6(ev)))
        # what is left: the packager's change and a module's proposal setting the same layout variable
        pkg = {"change_id": "chg-0003", "module_id": "package", "agent_id": "packager", "reason": "longer",
               "design_version": 1, "changes": {"wheelbase": {"value": 1900.0, "unit": "mm"}}}
        stray = dict(one, changes={"wheelbase": {"value": 1950.0, "unit": "mm"}})
        ev = modular(proposals=[stray])
        [e for e in ev if e["kind"] == "design.created"][-1]["data"]["module_changes"].append(pkg)
        self.assertTrue(any("all change wheelbase" in m for m in self.hk6(ev)))

    def test_no_proposal_without_modules(self):
        ev = modular(modules=False)
        for e in ev:
            if e["kind"] == "design.created":
                e["agent"] = "designer"
        self.assertTrue(any("without modules" in m for m in self.hk6(ev)))

    def test_parts_stay_in_their_envelope(self):
        def run(box):
            ev = modular()
            for e in ev:
                if e["kind"] == "design.created":
                    d = e["data"]
                    d["parts"][0]["module"] = "powertrain"  # the battery: centre (-900, 0, 300), 500 x 400 x 200
                    next(m for m in d["modules"] if m["module_id"] == "powertrain")["envelope"] = [box]
            return {f.message for f in self.findings(ev) if f.rule == "module.envelope"}
        self.assertEqual(run({"center_mm": [-900.0, 0.0, 300.0], "size_mm": [520.0, 420.0, 220.0]}), set())
        # the same space turned 90 degrees about z holds it only with x and y swapped
        self.assertEqual(run({"center_mm": [-900.0, 0.0, 300.0], "size_mm": [420.0, 520.0, 220.0],
                              "rpy_deg": [0.0, 0.0, 90.0]}), set())
        self.assertEqual(len(run({"center_mm": [-900.0, 0.0, 300.0], "size_mm": [480.0, 420.0, 220.0]})), 2)
        self.assertEqual(len(run({"center_mm": [-900.0, 0.0, 300.0], "size_mm": [420.0, 520.0, 220.0]})), 2)

    def test_a_module_given_no_room_has_no_place_for_its_parts(self):
        # as the assembly tool (H1-envelope): a module other than the package with no envelope box
        ev = modular()
        for e in ev:
            if e["kind"] == "design.created":
                e["data"]["parts"][0]["module"] = "powertrain"
        said = {f.message for f in self.findings(ev) if f.rule == "module.envelope"}
        self.assertTrue(said)
        self.assertTrue(all("which has no envelope box" in m for m in said), said)
        self.assertTrue(any("part battery is in module powertrain" in m for m in said), said)

    def test_a_member_is_its_tube_not_its_line(self):
        # the N15 plan's last design with node LS2 raised 60 mm: the assembly tool finds four members out of
        # their module's room (cross_S2, side_foot_LS2 and two rail pieces whose line stays inside but whose
        # 80 mm tube does not)
        from qa.check_run import RunChecker

        d = json.loads((Path(__file__).resolve().parent / "fixtures" / "n15-v005-design.json")
                       .read_text(encoding="utf-8"))
        next(n for n in d["nodes"] if n["id"] == "LS2")["xyz_mm"][2] += 60.0
        said = []
        probe = mock.Mock(warn=lambda rule, w, text: said.append(text), error=lambda *a: said.append(a))
        RunChecker._check_envelopes(probe, 5, d, "v005/design.json")
        self.assertEqual(sorted(said), sorted(f"v5 member {m} leaves the envelope of {mod}" for m, mod in (
            ("cross_S2", "frame"), ("rail_L_LSG_LS2", "frame"), ("rail_L_S2H", "frame"),
            ("side_foot_LS2", "occupant"))))



class VersionLoadCases(unittest.TestCase):
    """Load cases built per version (contract 39b6a8c): load_cases.created and v00N/load_cases.json."""

    setUp, tearDown, findings = CheckRun.setUp, CheckRun.tearDown, CheckRun.findings

    @staticmethod
    def with_v2_cases(load_cases):
        ev = modular()
        v2 = [k for k, e in enumerate(ev) if e["kind"] == "design.created"][-1]
        ev.insert(v2 + 1, dict(copy.deepcopy(ev[v2]), kind="load_cases.created", agent="system",
                               data={"design_version": 2, "load_cases": load_cases}))
        return renumber(ev)

    def test_the_last_version_is_judged_by_its_own_load_cases(self):
        r1 = copy.deepcopy(sample_events()[0]["data"]["load_cases"])
        hk4 = [f.message for f in self.findings(self.with_v2_cases(r1)) if f.rule == "HK4_no_stop_before_judged"]
        self.assertTrue(any("no verdict for R1-step100 in version 2" in m for m in hk4), hk4)
        # version 2 builds no load case with a criterion: nothing of it is left to judge
        for lc in r1:
            lc.pop("criterion", None)
        found = self.findings(self.with_v2_cases(r1))
        self.assertEqual([f.message for f in found if "no verdict for" in f.message], [])  # (v2 has no tile yet)
        self.assertEqual([str(f) for f in found if f.rule.startswith(("files.", "load_cases."))], [])

    def test_the_file_is_the_event(self):
        ev = self.with_v2_cases(copy.deepcopy(sample_events()[0]["data"]["load_cases"]))
        run = write_run(self.root / "r", ev)
        f = layout.version_dir(run, 2) / layout.VERSION_LOAD_CASES_FILE
        d = json.loads(f.read_text(encoding="utf-8"))
        d["load_cases"][0]["tolerance_rel"] = 0.5
        f.write_text(json.dumps(d), encoding="utf-8")
        self.assertIn("files.event_data", {x.rule for x in cr.check_run(run) if x.level == "error"})
        f.unlink()
        self.assertIn("files.missing", {x.rule for x in cr.check_run(run) if x.level == "error"})


if __name__ == "__main__":
    unittest.main()
