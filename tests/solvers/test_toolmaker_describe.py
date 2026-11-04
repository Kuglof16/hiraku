"""A tool made during a run keeps what its describe() gives (its load case params and predicts), so the roles see
which K0 formula predicts its results (list_formulas(tool=...)), as for the shelf's tools."""

import unittest

from hiraku.solvers import toolmaker

from tests.solvers.k2.test_toolmaker import GOOD

WITH_PREDICTS = GOOD.replace(
    "from hiraku.solvers.shelf.base import BuiltinTool, Param, check_record, param_value, result",
    "from hiraku.solvers.shelf.base import BuiltinTool, Param, Predicts, check_record, param_value, result").replace(
    '    def known_answers(self):',
    '    formulas = ["frame_static.cantilever_tip_load"]\n'
    '    predicts = (Predicts("tip_deflection", "frame_static.cantilever_tip_load", "max_displacement",\n'
    '                         "F, L, E, I of the bar"),)\n\n'
    '    def known_answers(self):', 1)

WITHOUT_DESCRIBE = '''
class Bare:
    name = "bare_tool"

    def spec(self):
        return {"name": "bare_tool", "title": "t", "domain": "strength", "base": "K0", "tests": ["R5"],
                "origin": "claude", "usable": True, "checks": [], "formulas": []}

    def check(self):
        return [{"case": "1 + 1", "quantity": "x", "expected": 2.0, "got": 2.0, "unit": "1",
                 "tolerance_rel": 0.0, "pass": True, "source": "arithmetic"}]

    def run(self, design, load_case, predictions, params):
        return {"results": []}
'''


class MadeToolDescribeTest(unittest.TestCase):
    def test_predicts_come_through(self):
        made = toolmaker.make_tool(WITH_PREDICTS)
        self.assertTrue(made.usable, made.problems)
        d = made.tool.describe()
        self.assertEqual(d["name"], "cantilever_tip")
        self.assertEqual([(p["quantity"], p["formula_id"]) for p in d["predicts"]],
                         [("tip_deflection", "frame_static.cantilever_tip_load")])
        self.assertEqual([p["name"] for p in d["params"]], ["loading", "force", "length", "width"][-len(d["params"]):])

    def test_a_tool_without_describe_gives_empty_predicts(self):
        made = toolmaker.make_tool(WITHOUT_DESCRIBE)
        self.assertTrue(made.usable, made.problems)
        d = made.tool.describe()
        self.assertEqual((d["name"], d["predicts"], d["params"]), ("bare_tool", [], []))


if __name__ == "__main__":
    unittest.main()
