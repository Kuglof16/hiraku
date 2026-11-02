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
            v = d.get("design_version") or version.get(d.get("prediction_id")) or version.get(d.get("comparison_id"))
            version[rid] = v
            p = layout.record_path(run, v, kind, rid)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        elif e["kind"] == "judgement.updated":
            judgements.setdefault(d["design_version"], {})[d["domain"]] = d
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

    def test_round_limit_needs_the_revisions_used(self):
        # one design version and round_limit 1: no revision was made
        self.assertIn("run.stop_code", self.rules(self._finish(sample_events(), "stopped", "round_limit", "pending")))

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

    def test_verdict_limit_comes_from_the_criterion(self):
        ev = sample_events()
        v = by_kind(ev, "verdict.recorded")["data"]
        v["limit"] = {"value": 200.0, "unit": "MPa"}
        self.assertFlags(ev, "verdict.criterion")

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


if __name__ == "__main__":
    unittest.main()
