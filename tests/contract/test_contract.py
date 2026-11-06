import json
import unittest
from datetime import datetime
from pathlib import Path

import jsonschema

from hiraku import contract
from hiraku.contract import layout
from hiraku.contract import loading as loading_rule

EXAMPLES = {
    "requirements_file": "requirements_file.json",
    "design": "design.json",
    "load_case": "load_case.json",
    "formula": "formula.json",
    "prediction": "prediction.json",
    "tool_spec": "tool_spec.json",
    "analysis": "analysis.json",
    "comparison": "comparison.json",
    "cause": "cause.json",
    "verdict": "verdict.json",
    "judgement": "judgement.json",
    "project": "project.json",
    "domains_picked": "domains_picked.json",
    "hearing_asked": "hearing_asked.json",
    "hearing_answered": "hearing_answered.json",
    "knowledge_imported": "knowledge_imported.json",
    "rule_check": "rule_check.json",
    "mesh": "mesh.json",
}

# intake event kind -> its data shape (docs/contract.md, 分野を選ぶ・ヒアリング・ナレッジ)
INTAKE_EVENTS = {
    "domains.picked": "domains_picked",
    "hearing.asked": "hearing_asked",
    "hearing.answered": "hearing_answered",
    "knowledge.imported": "knowledge_imported",
    "rule_check.recorded": "rule_check",
}


def load(name):
    with (contract.EXAMPLES_DIR / name).open(encoding="utf-8") as f:
        return json.load(f)


def events():
    with (contract.EXAMPLES_DIR / "events.sample.jsonl").open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class SchemaTest(unittest.TestCase):
    def test_schema_is_valid_draft_2020_12(self):
        jsonschema.Draft202012Validator.check_schema(contract.schema())

    def test_every_example_fits(self):
        for kind, name in EXAMPLES.items():
            with self.subTest(kind=kind):
                self.assertEqual(contract.errors(kind, load(name)), [])

    def test_every_sample_event_fits(self):
        for e in events():
            with self.subTest(seq=e["seq"], kind=e["kind"]):
                self.assertEqual(contract.errors("event", e), [])

    def test_sample_events_are_ordered_without_gaps(self):
        evs = events()
        self.assertEqual([e["seq"] for e in evs], list(range(len(evs))))
        ts = [e["t"] for e in evs]
        self.assertEqual(ts, sorted(ts))
        self.assertEqual(evs[0]["kind"], "run.started")

    def test_event_data_must_match_its_kind(self):
        bad = dict(events()[0])
        bad["kind"] = "prediction.recorded"  # data is a project, not a prediction
        self.assertNotEqual(contract.errors("event", bad), [])

    def test_unknown_event_kind_is_rejected(self):
        bad = dict(events()[3])
        bad["kind"] = "made.up"
        self.assertNotEqual(contract.errors("event", bad), [])

    def test_example_design_points_x_forward(self):
        # Origin below the front axle, x forward: the rear axle has negative x.
        xs = {s["id"]: s["position_mm"][0] for s in load("design.json")["supports"]}
        self.assertEqual(xs["FL"], 0.0)
        self.assertLess(xs["RL"], 0.0)

    def test_safe_side_cause_needs_a_reason(self):
        c = load("cause.json")
        c["safe_side"] = True
        self.assertNotEqual(contract.errors("cause", c), [])
        c["safe_side_reason"] = "the joint is taken as fixed, so twist is over-estimated"
        self.assertEqual(contract.errors("cause", c), [])

    def test_verdict_basis(self):
        v = load("verdict.json")
        self.assertEqual(contract.errors("verdict", {**v, "basis": "explained_safe_side",
                                                     "value_from": "prediction", "cause_id": "cause-0001"}), [])
        self.assertNotEqual(contract.errors("verdict", {**v, "basis": "close_enough"}), [])

    def test_tool_decides_after_the_path_check(self):
        v = load("verdict.json")
        ok = {**v, "basis": "path_checked", "value_from": "analysis", "cause_id": "cause-0001"}
        self.assertEqual(contract.errors("verdict", ok), [])
        self.assertEqual(contract.errors("verdict", {**ok, "pass": False}), [])  # the tool's value may fail too
        self.assertNotEqual(contract.errors("verdict", {**ok, "value_from": "prediction"}), [])
        self.assertNotEqual(contract.errors("verdict", {k: x for k, x in ok.items() if k != "cause_id"}), [])
        c = load("cause.json")
        self.assertEqual(contract.errors("cause", {**c, "tool_decides": True, "tool_decides_reason": "no plates by hand"}), [])
        self.assertNotEqual(contract.errors("cause", {**c, "tool_decides": True, "ends_case": True}), [])
        self.assertEqual(contract.errors("cause", {**c, "tool_decides": False, "ends_case": True}), [])
        self.assertNotEqual(contract.errors("cause", {**c, "tool_decides": True, "safe_side": True}), [])
        call = {"comparison_id": "cmp-0001", "cause": "plates by hand: no", "fix": "the tool decides"}
        self.assertEqual(contract.errors("tool_input.record_cause", {**call, "tool_decides": True,
                                                                      "tool_decides_reason": "x"}), [])
        self.assertNotEqual(contract.errors("tool_input.record_cause", {**call, "tool_decides": True, "safe_side": True}), [])
        self.assertNotEqual(contract.errors("tool_input.record_cause", {**call, "tool_decides": True, "ends_case": True}), [])

    def test_analysis_hot_spots(self):
        a = load("analysis.json")
        row = {"where": "rail_R_BE", "quantity": "max_hot_spot_stress", "value": {"value": 151.0, "unit": "MPa"},
               "limit": {"value": 143.3, "unit": "MPa"}, "utilisation": 1.054, "predicted": False}
        self.assertEqual(contract.errors("analysis", {**a, "hot_spots": [row]}), [])
        self.assertNotEqual(contract.errors("analysis", {**a, "hot_spots": [{k: v for k, v in row.items() if k != "predicted"}]}), [])
        self.assertNotEqual(contract.errors("analysis", {**a, "hot_spots": [{**row, "utilisation": -1}]}), [])
        self.assertEqual(contract.errors("analysis", {**a, "hot_spots": [{**row, "at_mm": [1200.0, -310.0, 420.0]}]}), [])
        self.assertNotEqual(contract.errors("analysis", {**a, "hot_spots": [{**row, "at_mm": [1200.0, -310.0]}]}), [])
        self.assertEqual(contract.errors("analysis", {**a, "hot_spots": [{**row, "utilisation": 1000}]}), [])
        self.assertNotEqual(contract.errors("analysis", {**a, "hot_spots": [{**row, "utilisation": 1000.5}]}), [])

    def test_hot_spot_red_and_its_cause(self):
        v = load("verdict.json")
        red = {k: x for k, x in v.items() if k != "comparison_id"}
        red.update({"basis": "hot_spot", "analysis_id": "ana-0001", "where": "rail_R_BE", "value_from": "analysis",
                    "pass": False})
        self.assertEqual(contract.errors("verdict", red), [])
        self.assertNotEqual(contract.errors("verdict", {**red, "pass": True}), [])
        self.assertNotEqual(contract.errors("verdict", {k: x for k, x in red.items() if k != "where"}), [])
        c = load("cause.json")
        by_verdict = {k: x for k, x in c.items() if k != "comparison_id"}
        self.assertEqual(contract.errors("cause", {**by_verdict, "verdict_id": "ver-0001", "where": "rail_R_BE"}), [])
        self.assertNotEqual(contract.errors("cause", by_verdict), [])  # neither comparison_id nor verdict_id
        self.assertEqual(contract.errors("tool_input.record_cause", {"verdict_id": "ver-0001", "where": "rail_R_BE",
                                                                      "cause": "c", "fix": "f"}), [])
        self.assertNotEqual(contract.errors("tool_input.record_cause", {"cause": "c", "fix": "f"}), [])

    def test_bracket_joint_sizes(self):
        d = load("design.json")
        part = dict(d["parts"][0])
        ok = {**part, "joint": {"bolt_d_mm": 10.0, "bolts": 2, "plate_t_mm": 4.0, "weld_leg_mm": 4.0}}
        self.assertEqual(contract.errors("part", ok), [])
        self.assertNotEqual(contract.errors("part", {**part, "joint": {}}), [])
        self.assertNotEqual(contract.errors("part", {**part, "joint": {"bolt_d_mm": 0}}), [])
        self.assertNotEqual(contract.errors("part", {**part, "joint": {"bolts": 0}}), [])
        self.assertNotEqual(contract.errors("part", {**part, "joint": {"nut": 1}}), [])

    def test_declared_sizes_for_the_rules(self):
        from hiraku.intake.facts import FACTS

        root = contract.schema()
        decl = root["$defs"]["part"]["properties"]["declared"]
        names = set(decl["propertyNames"]["enum"])
        units = set(decl["additionalProperties"]["properties"]["unit"]["enum"])
        vocab = {n: unit for n, (_, unit, scope) in FACTS.items() if scope == "declared"}
        self.assertLessEqual(set(vocab), names)  # the contract may lead the vocabulary by a branch, never trail it
        self.assertLessEqual(set(vocab.values()), units)
        part = dict(load("design.json")["parts"][0])
        bend = {"value": 3.0, "unit": "mm", "basis": "assumption"}
        self.assertEqual(contract.errors("part", {**part, "declared": {"bend_radius_mm": bend}}), [])
        self.assertNotEqual(contract.errors("part", {**part, "declared": {"bend_r": bend}}), [])
        self.assertNotEqual(contract.errors("part", {**part, "declared": {"bend_radius_mm": {**bend, "unit": "in"}}}), [])
        uj = {"value": 12.5, "unit": "deg", "basis": "computed from the shaft geometry"}
        self.assertEqual(contract.errors("part", {**part, "declared": {"uj_angle_max_deg": uj}}), [])
        wire = {"wire_current_A": {"value": 60.0, "unit": "A", "basis": "computed from the motor's power"},
                "wire_voltage_rating_V": {"value": 60.0, "unit": "V", "basis": "not checked"},
                "wire_place_max_degC": {"value": 85.0, "unit": "degC", "basis": "assumption"}}
        self.assertEqual(contract.errors("part", {**part, "kind": "electrical", "declared": wire}), [])
        self.assertLessEqual(units, set(contract.UNITS))  # every declared unit is a contract unit
        self.assertNotEqual(contract.errors("part", {**part, "declared": {"bend_radius_mm": {"value": 3.0, "unit": "mm"}}}), [])
        joint = {"bolt_d_mm": 10.0, "bolts": 2, "plate_t_mm": 4.0, "weld_leg_mm": 4.0}
        self.assertEqual(contract.errors("part", {**part, "joint": joint, "declared": {"hole_edge_mm": bend}}), [])
        self.assertNotEqual(contract.errors("part", {**part, "joint": joint, "declared": {"bolt_diameter_mm": bend}}), [])
        self.assertEqual(contract.errors("part", {**part, "declared": {"bolt_diameter_mm": bend}}), [])  # no joint

    def test_node_motion_field(self):
        f = {"kind": "node_motion", "unit": "mm", "nodes": ["N1", "N2"], "members": [["N1", "N2", "rail_L_0"]],
             "frames": {"t_s": [0.0, 0.01], "positions_mm": [[[0, 0, 0], [500, 0, 0]], [[0, 0, 0], [480, 0, 5]]],
                        "broken": [[], ["rail_L_0"]]}}
        self.assertEqual(contract.errors("field", f), [])
        self.assertNotEqual(contract.errors("field", {k: v for k, v in f.items() if k != "members"}), [])
        self.assertNotEqual(contract.errors("field", {**f, "members": [["N1", "N2"]]}), [])
        many = {**f, "frames": {**f["frames"], "t_s": [0.0] * 151}}
        self.assertNotEqual(contract.errors("field", many), [])
        self.assertEqual(contract.errors("field", {"kind": "stress", "unit": "MPa", "per_member": {"a": 1.0}}), [])

    def test_part_rating(self):
        part = {**dict(load("design.json")["parts"][0]), "kind": "strap"}  # kind is free text
        ok = {**part, "rating": {"working_load_N": 2500.0, "basis": "EN 12195-2 LC (not checked)"}}
        self.assertEqual(contract.errors("part", ok), [])
        self.assertNotEqual(contract.errors("part", {**part, "rating": {"working_load_N": 0, "basis": "x"}}), [])
        self.assertNotEqual(contract.errors("part", {**part, "rating": {"working_load_N": 2500.0}}), [])

    def test_scene_circle(self):
        ring = {"kind": "circle", "label": "外側の前輪の円", "position_mm": [0.0, 4900.0, 0.0], "size_mm": [9800.0, 9800.0, 0.0]}
        self.assertEqual(contract.errors("scene_object", ring), [])
        self.assertNotEqual(contract.errors("scene_object", {**ring, "kind": "ring"}), [])

    def test_tool_check_in_japanese(self):
        chk = {"case": "static load FL", "quantity": "wheel_load", "expected": 1694.37, "got": 1694.93, "unit": "N",
               "tolerance_rel": 0.001, "pass": True, "source": "moment balance"}
        ja = {**chk, "case_ja": "止まっているときの左前輪の荷重", "source_ja": "前後の輪のまわりの力のつり合い"}
        self.assertEqual(contract.errors("tool_check", chk), [])  # both optional
        self.assertEqual(contract.errors("tool_check", ja), [])
        self.assertNotEqual(contract.errors("tool_check", {**ja, "case_ja": ""}), [])

    def test_rule_check_keeps_the_values_it_checked(self):
        rc = {"rule_check_id": "rchk-0001", "design_version": 5, "rule_id": "SM-01", "targets": ["bracket_lca_FL1"],
              "hits": [], "pass": True}
        row = {"part_id": "bracket_lca_FL1", "values": {"hole_edge_mm": 18.0, "hole_diameter_mm": 13.0},
               "unchecked": ["hole_diameter_mm"]}
        self.assertEqual(contract.errors("rule_check", rc), [])  # checked is optional
        self.assertEqual(contract.errors("rule_check", {**rc, "checked": [row]}), [])
        self.assertNotEqual(contract.errors("rule_check", {**rc, "checked": [{**row, "values": {}}]}), [])
        self.assertNotEqual(contract.errors("rule_check", {**rc, "checked": [{**row, "values": {"hole_edge_mm": "18"}}]}), [])

    def test_fastening_tool_gap(self):
        step = {"order": 1, "part": "susp_FL", "direction": "-y", "no_interference": True, "tool_clearance": False,
                "hand_reach": True}
        gap = {"to": "node LS1", "kind": "bolt", "at_mm": [0.0, 1.0, 2.0], "tool_side": "+z", "tool_gap_mm": -10.6}
        self.assertEqual(contract.errors("assembly_step", {**step, "fastenings": [gap]}), [])
        self.assertNotEqual(contract.errors("assembly_step", {**step, "fastenings": [{**gap, "tool_gap_mm": "10"}]}), [])
        self.assertNotEqual(contract.errors("assembly_step", {**step, "fastenings": [{**gap, "gap": 1.0}]}), [])

    def test_stop_code_goes_with_status(self):
        ev = {"seq": 9, "ts": "2026-11-03T01:00:00+09:00", "t": 1.0, "run_id": "x", "kind": "run.finished"}
        ok = [("done", "done"), ("failed", "error"), ("stopped", "not_judged"), ("stopped", "user_stop"), ("stopped", "red_left")]
        for status, code in ok:
            self.assertEqual(contract.errors("event", {**ev, "data": {"status": status, "stop_code": code}}), [])
        for status, code in [("done", "cost_limit"), ("failed", "done"), ("stopped", "error")]:
            self.assertNotEqual(contract.errors("event", {**ev, "data": {"status": status, "stop_code": code}}), [])
        self.assertEqual(contract.errors("event", {**ev, "data": {"status": "stopped"}}), [])  # stop_code optional

    def test_agent_stopped_says_why(self):
        ev = {"seq": 9, "ts": "2026-11-05T02:24:00+09:00", "t": 1.0, "run_id": "x", "kind": "agent.stopped",
              "agent": "cae.strength"}
        for code in ("done", "run_ended", "user_stop", "error"):
            self.assertEqual(contract.errors("event", {**ev, "data": {"agent_id": "cae.strength", "stop_code": code,
                                                                       "stop_reason": "words"}}), [])
        self.assertNotEqual(contract.errors("event", {**ev, "data": {"agent_id": "cae.strength",
                                                                      "stop_code": "red_left"}}), [])
        self.assertEqual(contract.errors("event", {**ev, "data": {"agent_id": "cae.strength"}}), [])  # optional

    def test_fast_path_gives_jsonschemas_errors(self):
        """The fast check of long plain arrays and maps reports exactly what jsonschema reports."""
        root = contract.schema()
        plain = jsonschema.Draft202012Validator({"$schema": root["$schema"], "$defs": root["$defs"],
                                                 "$ref": "#/$defs/field"}, format_checker=jsonschema.FormatChecker())

        def slow(obj):
            return [f"field: {'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"
                    for e in sorted(plain.iter_errors(obj), key=lambda e: list(e.absolute_path))]

        n = 200
        good = {"kind": "mode_shape", "unit": "1", "mesh_id": "mesh-0001",
                "per_member": {f"m{i}": i * 0.5 for i in range(n)},
                "per_node": {f"n{i}": ([0.1, 0.2, 1.0] if i % 2 else 3.0) for i in range(n)},
                "per_element": [float(i) for i in range(n)] + [7]}
        self.assertEqual(contract.errors("field", good), [])
        self.assertEqual(slow(good), [])
        for where, bad in [("per_member", "x"), ("per_member", True), ("per_node", [1.0, 2.0]),
                           ("per_node", [[1.0], 2.0, 3.0]), ("per_node", None), ("per_element", "1.5"),
                           ("per_element", False)]:
            with self.subTest(where=where, bad=bad):
                obj = json.loads(json.dumps(good))
                if where == "per_element":
                    obj[where][57] = bad
                else:
                    obj[where][f"{where[4]}57"] = bad
                self.assertNotEqual(contract.errors("field", obj), [])
                self.assertEqual(contract.errors("field", obj), slow(obj))

    def test_series_against_time_or_another_quantity(self):
        base = {"channel": "toe.FL", "unit": "deg", "values": [0.0, 0.1]}
        self.assertEqual(contract.errors("series", {**base, "t_s": [0.0, 1.0]}), [])
        x = {"quantity": "wheel_travel", "unit": "mm", "values": [-50.0, 50.0]}
        self.assertEqual(contract.errors("series", {**base, "x": x}), [])
        self.assertNotEqual(contract.errors("series", base), [])
        self.assertNotEqual(contract.errors("series", {**base, "t_s": [0.0, 1.0], "x": x}), [])

    def test_series_of_one_part(self):
        s = {"channel": "loss.motor", "unit": "W", "t_s": [0.0, 1.0], "values": [560.0, 570.0]}
        self.assertEqual(contract.errors("series", {**s, "part_id": "motor_RL"}), [])
        self.assertNotEqual(contract.errors("series", {**s, "part_id": 3}), [])

    def test_result_names_the_field_it_was_read_from(self):
        r = {"quantity": "first_frame_mode", "value": 8.5, "unit": "Hz"}
        self.assertEqual(contract.errors("result", {**r, "field_index": 0}), [])
        self.assertEqual(contract.errors("result", {**r, "field_index": 4}), [])
        self.assertNotEqual(contract.errors("result", {**r, "field_index": -1}), [])
        self.assertNotEqual(contract.errors("result", {**r, "field_index": 1.5}), [])

    def test_analysis_needs_a_prediction(self):
        a = load("analysis.json")
        a["prediction_ids"] = []
        self.assertNotEqual(contract.errors("analysis", a), [])

    def test_quantity_needs_a_known_unit(self):
        self.assertEqual(contract.errors("quantity", {"value": 1.0, "unit": "mm"}), [])
        self.assertNotEqual(contract.errors("quantity", {"value": 1.0, "unit": "inch"}), [])

    def test_unknown_flag_is_rejected(self):
        r = load("requirements_file.json")
        r["flags"]["made_up"] = True
        self.assertNotEqual(contract.errors("requirements_file", r), [])

    def test_validate_raises_with_every_error(self):
        with self.assertRaises(ValueError) as cm:
            contract.validate("comparison", {"comparison_id": "x"})
        self.assertIn("prediction_id", str(cm.exception))

    def test_unknown_kind_raises(self):
        with self.assertRaises(KeyError):
            contract.errors("nothing", {})

    def test_python_lists_come_from_the_schema(self):
        self.assertIn("prediction.recorded", contract.EVENT_KINDS)
        self.assertEqual(set(contract.DOMAINS), set(contract.DOMAIN_NAMES_JA))
        self.assertEqual(set(contract.RULE_CATEGORIES), set(contract.RULE_CATEGORY_NAMES_JA))
        self.assertIn("MPa", contract.UNITS)
        self.assertEqual(contract.mcp_name("analyze"), "mcp__hiraku__analyze")


class IntakeTest(unittest.TestCase):
    ev = {"seq": 1, "ts": "2026-11-03T09:00:00+09:00", "t": 0.1, "run_id": "20261103-090000-offroad-4w", "agent": "lead"}

    def test_intake_events_carry_their_shapes(self):
        for kind, shape in INTAKE_EVENTS.items():
            with self.subTest(kind=kind):
                self.assertIn(kind, contract.EVENT_KINDS)
                data = load(EXAMPLES[shape])
                self.assertEqual(contract.errors("event", {**self.ev, "kind": kind, "data": data}), [])
                self.assertNotEqual(contract.errors("event", {**self.ev, "kind": kind, "data": load("cause.json")}), [])

    def test_domain_pick_may_list_its_tests(self):
        pick = load("domains_picked.json")["picks"][0]
        self.assertEqual(contract.errors("domain_pick", pick), [])
        self.assertNotEqual(contract.errors("domain_pick", {**pick, "test_ids": ["not a test"]}), [])
        del pick["test_ids"]
        self.assertEqual(contract.errors("domain_pick", pick), [])

    def test_flag_reasons_use_flag_names(self):
        d = load("domains_picked.json")
        d["flag_reasons"]["made_up"] = "no such flag"
        self.assertNotEqual(contract.errors("domains_picked", d), [])

    def test_hearing_answers_come_from_hearing_or_default(self):
        a = load("hearing_answered.json")
        a["answers"][0]["source"] = "hearing"
        self.assertEqual(contract.errors("hearing_answered", a), [])
        a["answers"][0]["source"] = "user"
        self.assertNotEqual(contract.errors("hearing_answered", a), [])

    def test_rule_check_passes_only_without_hits(self):
        r = load("rule_check.json")
        self.assertNotEqual(contract.errors("rule_check", {**r, "pass": True}), [])
        self.assertNotEqual(contract.errors("rule_check", {**r, "hits": [], "pass": False}), [])
        self.assertEqual(contract.errors("rule_check", {**r, "hits": [], "pass": True}), [])

    def test_mesh_event_and_field(self):
        m = load("mesh.json")
        ev = {**self.ev, "kind": "mesh.created", "agent": "cae.strength", "design_version": 1, "data": m}
        self.assertEqual(contract.errors("event", ev), [])
        field = {"kind": "stress", "unit": "MPa", "mesh_id": m["mesh_id"], "per_element": [12.5, 30.1], "surface": "max"}
        self.assertEqual(contract.errors("field", field), [])
        self.assertNotEqual(contract.errors("field", {"kind": "stress", "unit": "MPa", "per_element": [1.0]}), [])
        self.assertNotEqual(contract.errors("mesh", {**m, "elements": [[0, 1]]}), [])

    def test_report_issues(self):
        issue = {"kind": "missing_tool", "what": "no tool for the roll-over cage load", "where": "R4-rops",
                 "record_ids": ["ana-0003"], "workaround": "made a tool in the run"}
        self.assertEqual(contract.errors("issue", issue), [])
        self.assertNotEqual(contract.errors("issue", {**issue, "kind": "bug"}), [])
        self.assertEqual(contract.errors("tool_input.write_report", {"summary": "s", "next": [], "issues": [issue]}), [])

    def test_project_driver(self):
        ev = next(e for e in events() if e["kind"] == "run.started")
        for driver in ("scripted", "agent_sdk", "claude_code_plugin"):
            self.assertEqual(contract.errors("project", {**ev["data"], "driver": driver}), [])
        self.assertNotEqual(contract.errors("project", {**ev["data"], "driver": "proxy"}), [])

    def test_tool_applies_to(self):
        spec = load("tool_spec.json")
        self.assertEqual(contract.errors("tool_spec", {**spec, "applies_to": ["space_frame_utv"]}), [])
        for bad in ([], "space_frame_utv", ["Space Frame"], ["a", "a"]):
            with self.subTest(applies_to=bad):
                self.assertNotEqual(contract.errors("tool_spec", {**spec, "applies_to": bad}), [])

    def test_analysis_timings(self):
        a = load("analysis.json")
        self.assertEqual(contract.errors("analysis", {**a, "timings_s": {"factorisation": 1.25, "level_5mm": 33.0}}), [])
        for bad in ({"factorisation": -1.0}, {"factorisation": "1 s"}, [1.0]):
            with self.subTest(timings_s=bad):
                self.assertNotEqual(contract.errors("analysis", {**a, "timings_s": bad}), [])

    def test_criterion_stand_in_quantity(self):
        lc = load("load_case.json")
        ok = {**lc, "criterion": {**lc["criterion"], "stand_in_quantity": "stress_range"}}
        self.assertEqual(contract.errors("load_case", ok), [])
        bad = {**lc, "criterion": {**lc["criterion"], "stand_in_quantity": 3}}
        self.assertNotEqual(contract.errors("load_case", bad), [])

    def test_part_material_and_finish(self):
        des = load("design.json")
        part = {**des["parts"][0], "material": "SPCC", "finish": "painted"}
        self.assertEqual(contract.errors("design", {**des, "parts": [part, *des["parts"][1:]]}), [])
        bad = {**part, "finish": "shiny"}
        self.assertNotEqual(contract.errors("design", {**des, "parts": [bad, *des["parts"][1:]]}), [])

    def test_unsupported_load_verdict(self):
        ver = load("verdict.json")
        base = {k: v for k, v in ver.items() if k not in ("comparison_id", "basis", "margin")}
        ok = {**base, "analysis_id": "ana-0001", "basis": "unsupported_load", "quantity": "unsupported_load",
              "value": {"value": 2, "unit": "1"}, "op": "<=", "limit": {"value": 0, "unit": "1"}, "pass": False, "margin_pct": None}
        self.assertEqual(contract.errors("verdict", ok), [])
        self.assertNotEqual(contract.errors("verdict", {k: v for k, v in ok.items() if k != "analysis_id"}), [])
        self.assertNotEqual(contract.errors("verdict", {**ok, "pass": True}), [])
        self.assertNotEqual(contract.errors("verdict", {k: v for k, v in ver.items() if k != "comparison_id"}), [])

    def test_requirements_stop_after(self):
        req = load("requirements_file.json")
        self.assertEqual(contract.errors("requirements_file", {**req, "stop_after": "picks"}), [])
        self.assertNotEqual(contract.errors("requirements_file", {**req, "stop_after": "design"}), [])
        ev = next(e for e in events() if e["kind"] == "run.started")
        self.assertEqual(contract.errors("project", {**ev["data"], "stop_after": "picks"}), [])

    def test_hearing_by_claude(self):
        req = load("requirements_file.json")
        self.assertEqual(contract.errors("requirements_file", {**req, "hearing": "claude"}), [])
        self.assertNotEqual(contract.errors("requirements_file", {**req, "hearing": "person"}), [])
        ev = next(e for e in events() if e["kind"] == "run.started")
        self.assertEqual(contract.errors("project", {**ev["data"], "hearing": "claude"}), [])

    def test_rule_questions_in_the_hearing(self):
        q = {"rule_id": "U1-04", "parts": ["battery"], "text": "電池が 30 kg を超えます。設計を直しますか、この規則を外しますか"}
        self.assertEqual(contract.errors("hearing_asked", {"rule_questions": [q]}), [])
        self.assertNotEqual(contract.errors("hearing_asked", {}), [])
        self.assertEqual(contract.errors("hearing_answered", {"answers": [], "rule_answers": [{"rule_id": "U1-04", "choice": "revise_design"}]}), [])
        self.assertNotEqual(contract.errors("hearing_answered", {"answers": [], "rule_answers": [{"rule_id": "U1-04", "choice": "ignore"}]}), [])

    def test_revise_design_choices(self):
        ok = {"choices": {"part_mounts": "brackets"}, "reason": "R8-free: 16 parts float"}
        self.assertEqual(contract.errors("tool_input.revise_design", ok), [])
        both = {**ok, "changes": {"rail_t": {"value": 4.5, "unit": "mm"}}}
        self.assertEqual(contract.errors("tool_input.revise_design", both), [])
        self.assertNotEqual(contract.errors("tool_input.revise_design", {"reason": "nothing"}), [])
        self.assertNotEqual(contract.errors("tool_input.revise_design", {**ok, "choices": {}}), [])

    def test_solid_and_mixed_meshes(self):
        m = load("mesh.json")
        no_t = {k: v for k, v in m.items() if k != "member_thickness_mm"}
        self.assertNotEqual(contract.errors("mesh", no_t), [])
        hex20 = list(range(20))
        solid = {**no_t, "element_type": "solid", "nodes_mm": [[0.0, 0.0, float(i)] for i in range(20)],
                 "elements": [hex20], "element_member": ["bracket"]}
        self.assertEqual(contract.errors("mesh", solid), [])
        self.assertNotEqual(contract.errors("mesh", {**solid, "elements": [list(range(21))]}), [])
        mixed = {**solid, "element_type": "mixed"}
        self.assertNotEqual(contract.errors("mesh", mixed), [])
        links = [{"id": "c1", "kind": "rbe3", "master": 0, "nodes": [1, 2, 3], "weights": [1.0, 1.0, 1.0]},
                 {"id": "c2", "kind": "spring", "nodes": [4, 5], "stiffness": [1e3, 1e3, 1e3, 0.0, 0.0, 0.0]},
                 {"id": "c3", "kind": "rbe2", "master": 6, "nodes": [7, 8], "components": "123456"}]
        mixed = {**mixed, "element_kind": ["hex20"], "connections": links}
        self.assertEqual(contract.errors("mesh", mixed), [])
        self.assertNotEqual(contract.errors("mesh", {**mixed, "connections": [{"id": "c4", "kind": "glue"}]}), [])
        self.assertNotEqual(contract.errors("mesh", {**mixed, "element_kind": ["hex27"]}), [])

    def test_error_estimate_and_mesh_convergence(self):
        m = load("mesh.json")
        est = {"kind": "error_estimate", "unit": "%", "mesh_id": m["mesh_id"], "per_element": [3.2, 18.0]}
        self.assertEqual(contract.errors("field", est), [])
        conv = {"quantity": "hot-spot stress", "unit": "MPa", "verdict": "converged",
                "levels": [{"element_size_mm": 10.0, "value": 116.3, "mesh_id": m["mesh_id"]},
                           {"element_size_mm": 5.0, "elements": 40000, "value": 118.7}],
                "change_pct": 2.0, "limit_pct": 5.0}
        a = load("analysis.json")
        self.assertEqual(contract.errors("analysis", {**a, "mesh_convergence": conv}), [])
        one = {**conv, "levels": conv["levels"][:1]}
        self.assertNotEqual(contract.errors("analysis", {**a, "mesh_convergence": one}), [])
        self.assertNotEqual(contract.errors("analysis", {**a, "mesh_convergence": {**conv, "verdict": "ok"}}), [])

    def test_rule_needs_a_known_category_and_a_basis(self):
        rule = load("knowledge_imported.json")["rules"][0]
        self.assertNotEqual(contract.errors("rule", {**rule, "category": "paint"}), [])
        del rule["basis"]
        self.assertNotEqual(contract.errors("rule", rule), [])


class LayoutTest(unittest.TestCase):
    def test_run_id(self):
        rid = layout.new_run_id(datetime(2026, 11, 2, 20, 30, 0), "Offroad 4W")
        self.assertEqual(rid, "20261102-203000-offroad-4w")
        self.assertEqual(contract.errors("project", {**load("project.json"), "run_id": rid}), [])

    def test_paths(self):
        rd = layout.run_dir("20261102-203000-offroad-4w", Path("runs"))
        self.assertEqual(layout.record_path(rd, 1, "prediction", layout.format_id("prediction", 1)),
                         Path("runs/20261102-203000-offroad-4w/v001/predictions/pred-0001.json"))
        with self.assertRaises(ValueError):
            layout.run_dir("../etc")
        self.assertEqual(layout.record_path(rd, 2, "rule_check", layout.format_id("rule_check", 3)),
                         Path("runs/20261102-203000-offroad-4w/v002/rule_checks/rchk-0003.json"))

    def test_record_kinds_agree(self):
        self.assertEqual(set(layout.RECORD_DIRS), set(layout.ID_PREFIX))
        self.assertEqual(set(layout.RECORD_EVENT), set(layout.ID_PREFIX))
        self.assertTrue(set(layout.RECORD_EVENT.values()) <= set(contract.EVENT_KINDS))
        self.assertEqual(layout.RECORD_OF_EVENT["rule_check.recorded"], "rule_check")


class LoadingTest(unittest.TestCase):
    @staticmethod
    def design(*kinds):
        return {"parts": [{"id": f"{k}{i}", "kind": k} for i, k in enumerate(kinds)]}

    def ids(self, design, loading):
        return [p["id"] for p in loading_rule.parts_on_board(design, loading)]

    def test_occupants_board_in_order_and_cargo_only_with_two_cargo(self):
        d = self.design("frame", "occupant", "occupant", "cargo")
        self.assertEqual(self.ids(d, "empty"), ["frame0"])
        self.assertEqual(self.ids(d, "driver"), ["frame0", "occupant1"])
        self.assertEqual(self.ids(d, "two"), ["frame0", "occupant1", "occupant2"])
        self.assertEqual(self.ids(d, "two_cargo"), ["frame0", "occupant1", "occupant2", "cargo3"])
        self.assertEqual(self.ids(d, None), self.ids(d, "two_cargo"))

    def test_too_few_occupants_does_not_fit(self):
        with self.assertRaises(ValueError):
            loading_rule.parts_on_board(self.design("frame", "occupant"), "two")
        self.assertEqual(self.ids(self.design("frame", "cargo"), "two_cargo"), ["frame0", "cargo1"])  # no seats

    def test_body_segments_are_one_person(self):
        seg = ["torso", "thigh_l", "shin_l"]
        d = {"parts": [{"id": "frame", "kind": "frame"}]
             + [{"id": f"{who}.{s}", "kind": "occupant"} for who in ("driver", "passenger") for s in seg]}
        self.assertEqual(self.ids(d, "driver"), ["frame"] + [f"driver.{s}" for s in seg])
        self.assertEqual(len(self.ids(d, "two")), 7)
        self.assertEqual(self.ids(d, "empty"), ["frame"])
        one = {"parts": d["parts"][:4]}
        with self.assertRaises(ValueError):
            loading_rule.parts_on_board(one, "two")
        self.assertEqual(loading_rule.person_of({"id": "occupant1"}), "occupant1")

    def test_unknown_loading(self):
        with self.assertRaises(ValueError):
            loading_rule.parts_on_board(self.design("frame"), "three")

    def test_on_board_copies_the_design(self):
        d = {**self.design("occupant", "cargo"), "name": "x"}
        out = loading_rule.on_board(d, "driver")
        self.assertEqual([p["id"] for p in out["parts"]], ["occupant0"])
        self.assertEqual(len(d["parts"]), 2)
        self.assertEqual(out["name"], "x")

    def test_loading_of(self):
        self.assertEqual(loading_rule.loading_of({"params": {"loading": "two"}}), "two")
        self.assertEqual(loading_rule.loading_of({}), "two_cargo")

    def test_ids_fit_the_schema_patterns(self):
        for kind in layout.ID_PREFIX:
            rid = layout.format_id(kind, 12)
            pattern = contract.schema()["$defs"][kind]["properties"][f"{kind}_id"]["pattern"]
            self.assertRegex(rid, pattern)

    def test_owners(self):
        self.assertEqual(layout.owner_of("hiraku/solvers/k1_frame/model.py"), "analysis")
        self.assertEqual(layout.owner_of("hiraku/agent/hooks.py"), "design")
        self.assertEqual(layout.owner_of("web/index.html"), "screen")
        self.assertEqual(layout.owner_of("tests/verification/test_step.py"), "qa")
        self.assertEqual(layout.owner_of("hiraku/contract/hiraku.schema.json"), "lead")
        self.assertIsNone(layout.owner_of("somewhere/else.py"))

    def test_no_path_has_two_owners(self):
        seen = {}
        for dept, prefixes in layout.OWNERS.items():
            for p in prefixes:
                self.assertNotIn(p, seen, f"{p} owned by {seen.get(p)} and {dept}")
                seen[p] = dept


if __name__ == "__main__":
    unittest.main()
