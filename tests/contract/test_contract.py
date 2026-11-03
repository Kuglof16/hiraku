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

    def test_stop_code_goes_with_status(self):
        ev = {"seq": 9, "ts": "2026-11-03T01:00:00+09:00", "t": 1.0, "run_id": "x", "kind": "run.finished"}
        ok = [("done", "done"), ("failed", "error"), ("stopped", "not_judged"), ("stopped", "user_stop"), ("stopped", "red_left")]
        for status, code in ok:
            self.assertEqual(contract.errors("event", {**ev, "data": {"status": status, "stop_code": code}}), [])
        for status, code in [("done", "cost_limit"), ("failed", "done"), ("stopped", "error")]:
            self.assertNotEqual(contract.errors("event", {**ev, "data": {"status": status, "stop_code": code}}), [])
        self.assertEqual(contract.errors("event", {**ev, "data": {"status": "stopped"}}), [])  # stop_code optional

    def test_series_against_time_or_another_quantity(self):
        base = {"channel": "toe.FL", "unit": "deg", "values": [0.0, 0.1]}
        self.assertEqual(contract.errors("series", {**base, "t_s": [0.0, 1.0]}), [])
        x = {"quantity": "wheel_travel", "unit": "mm", "values": [-50.0, 50.0]}
        self.assertEqual(contract.errors("series", {**base, "x": x}), [])
        self.assertNotEqual(contract.errors("series", base), [])
        self.assertNotEqual(contract.errors("series", {**base, "t_s": [0.0, 1.0], "x": x}), [])

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
