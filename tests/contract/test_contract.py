import json
import unittest
from datetime import datetime
from pathlib import Path

import jsonschema

from hiraku import contract
from hiraku.contract import layout

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
        self.assertIn("MPa", contract.UNITS)
        self.assertEqual(contract.mcp_name("analyze"), "mcp__hiraku__analyze")


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
