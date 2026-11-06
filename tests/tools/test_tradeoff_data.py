"""hiraku.tools.tradeoff_data.collect: which load cases find_tradeoffs runs K5 on."""

import unittest
from types import SimpleNamespace
from unittest import mock

from hiraku.tools import tradeoff_data

LCS = [
    {"load_case_id": "R1-step100", "title": "段差", "criterion": {"quantity": "max_stress", "op": "<="}},
    {"load_case_id": "R1s-step100", "title": "継ぎ目", "criterion": {"quantity": "max_hot_spot_stress", "op": "<="}},
    {"load_case_id": "P3-accel40", "title": "加速", "criterion": {"quantity": "time_to_speed", "op": "<="}},
]
TOOLS = {"R1-step100": "frame_static", "R1s-step100": "frame_shell", "P3-accel40": "launch"}
STAGES = {"frame_static": 1, "frame_shell": 3, "launch": 2}


def analysis(lc_id):
    q = next(lc["criterion"]["quantity"] for lc in LCS if lc["load_case_id"] == lc_id)
    return {"analysis_id": f"ana-{lc_id}", "tool": TOOLS[lc_id], "results": [{"quantity": q, "value": 1.0, "unit": "1"}]}


class CollectTest(unittest.TestCase):
    def collect(self, only):
        ctx = SimpleNamespace(version=2, load_cases=LCS)
        asked = []

        def sensitivities(_ctx, a):
            asked.append(a[0]["load_case_id"])
            return [{"terms": [{"name": "x to variables.v", "share": 0.5}]}]

        with mock.patch.object(tradeoff_data, "latest_verdicts", lambda c, v: {lc["load_case_id"]: lc for lc in LCS}), \
                mock.patch.object(tradeoff_data.report, "verdict_sources", lambda c, ver: (None, analysis(ver["load_case_id"]))), \
                mock.patch.object(tradeoff_data, "tool_stage", lambda c, t: STAGES[t]), \
                mock.patch.object(tradeoff_data.report, "sensitivities", sensitivities):
            got = tradeoff_data.collect(ctx, ["variables.v"], only)
        return got, asked

    def test_no_quantities_leaves_the_heavy_tools_out(self):
        got, asked = self.collect(set())
        self.assertEqual(asked, ["R1-step100", "P3-accel40"])  # K5 never ran the shell
        self.assertEqual(set(got["sens"]), {"max_stress", "time_to_speed"})
        self.assertEqual(len(got["skipped"]), 1)
        self.assertIn("R1s-step100", got["skipped"][0])
        self.assertIn("quantities", got["skipped"][0])

    def test_a_named_quantity_is_looked_at_whatever_its_tool(self):
        got, asked = self.collect({"max_hot_spot_stress"})
        self.assertEqual(asked, ["R1s-step100"])
        self.assertEqual(got["skipped"], [])


if __name__ == "__main__":
    unittest.main()
