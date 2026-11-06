"""hiraku.tools.tradeoff_data.collect: which load cases find_tradeoffs runs K5 on (the bound a call keeps)."""

import unittest
from types import SimpleNamespace
from unittest import mock

from hiraku.tools import tradeoff_data


def lc(lc_id, quantity, tool, op="<=", limit=None):
    return {"load_case_id": lc_id, "title": lc_id, "tool": tool,
            "criterion": {"quantity": quantity, "op": op, "limit": limit or {"value": 100.0, "unit": "MPa"}}}


LCS = [
    lc("H1-interference", "interference_count", "assembly", limit={"value": 0, "unit": "1"}),  # a count held at 0
    lc("H3-body-size", "knee_room", "assembly", op=">=", limit={"value": 0, "unit": "mm"}),  # measured on the shapes
    lc("R1-step100", "max_stress", "frame_static"),
    lc("R1s-step100", "max_hot_spot_stress", "frame_shell"),
    lc("P3-accel40", "time_to_speed", "launch", limit={"value": 6.0, "unit": "s"}),
] + [lc(f"X{i}", f"q{i}", "launch") for i in range(6)]  # more light cases than DEFAULT_CASES_MAX
STAGES = {"assembly": 0, "frame_static": 1, "frame_shell": 3, "launch": 2}


class CollectTest(unittest.TestCase):
    def collect(self, only, paths=("variables.v",)):
        ctx = SimpleNamespace(version=2, load_cases=LCS)
        by_id = {x["load_case_id"]: x for x in LCS}
        asked = []

        def analysis(ver):
            x = by_id[ver["load_case_id"]]
            return {"analysis_id": f"ana-{x['load_case_id']}", "tool": x["tool"],
                    "results": [{"quantity": x["criterion"]["quantity"], "value": 1.0, "unit": "1"}]}

        def sensitivities(_ctx, a):
            asked.append(a[0]["load_case_id"])
            return [{"terms": [{"name": "x to variables.v", "share": 0.5}]}]

        with mock.patch.object(tradeoff_data, "latest_verdicts", lambda c, v: dict(by_id)), \
                mock.patch.object(tradeoff_data.report, "verdict_sources", lambda c, ver: (None, analysis(ver))), \
                mock.patch.object(tradeoff_data, "tool_stage", lambda c, t: STAGES[t]), \
                mock.patch.object(tradeoff_data.report, "sensitivities", sensitivities):
            got = tradeoff_data.collect(ctx, list(paths), only)
        return got, asked

    def test_no_quantities_keeps_to_light_tools_and_a_few_cases(self):
        got, asked = self.collect(set())
        self.assertEqual(asked, ["R1-step100", "P3-accel40", "X0", "X1", "X2", "X3"])  # DEFAULT_CASES_MAX of them
        self.assertEqual(len(asked), tradeoff_data.DEFAULT_CASES_MAX)
        self.assertTrue(all(STAGES[next(x["tool"] for x in LCS if x["load_case_id"] == i)] in (1, 2) for i in asked))
        said = " ".join(got["skipped"])
        for named in ("H3-body-size", "R1s-step100", "X4", "X5"):
            self.assertIn(named, said)
        self.assertNotIn("H1-interference", said)  # a count held at 0 is left out without a word

    def test_a_number_that_moved_nothing_is_named(self):
        got, _ = self.collect({"max_stress"}, paths=("variables.v", "variables.wheelbase"))
        said = " ".join(got["skipped"])
        self.assertIn("wheelbase", said)  # the stub's terms move only variables.v
        self.assertIn("形の変数", said)
        self.assertNotIn("v:", said)

    def test_default_variables_are_the_direct_ones_the_design_has(self):
        design = {"archetype": "ladder_4w", "variables": {"gear_ratio": {}, "motor_peak_power": {}, "wheelbase": {}}}
        ctx = SimpleNamespace(version=1, store=SimpleNamespace(design=lambda: design))
        self.assertEqual(tradeoff_data.default_variables(ctx), ["variables.motor_peak_power", "variables.gear_ratio"])
        ctx = SimpleNamespace(version=1, store=SimpleNamespace(design=lambda: {"archetype": "care_bed", "variables": {}}))
        self.assertEqual(tradeoff_data.default_variables(ctx), [])

    def test_a_named_quantity_is_looked_at_whatever_its_tool(self):
        got, asked = self.collect({"max_hot_spot_stress", "knee_room"})
        self.assertEqual(asked, ["H3-body-size", "R1s-step100"])
        self.assertEqual(got["skipped"], [])


if __name__ == "__main__":
    unittest.main()
