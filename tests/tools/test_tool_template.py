"""get_tool_template: its example tool passes check_tool and goes on the shelf like a tool written in a run."""

import inspect
import unittest

from hiraku.solvers.shelf.base import check_record
from hiraku.tools import ToolError
from hiraku.tools.functions import check_tool, get_tool_template, make_tool
from hiraku.tools.mcp import DESCRIPTIONS, INPUT_SCHEMAS
from tests.tools.helpers import RunCase


class ToolTemplateTest(RunCase, unittest.TestCase):
    def test_the_example_passes_check_tool(self):
        t = get_tool_template(self.ctx, {}, "cae.strength")
        out = check_tool(self.ctx, {"code": t["code"]}, "cae.strength")
        self.assertEqual(out["problems"], [])
        self.assertTrue(out["usable"])
        self.assertEqual(out["name"], "example_midspan_bending")
        (c,) = out["checks"]
        self.assertTrue(c["pass"])
        self.assertEqual((c["quantity"], c["unit"]), ("max_stress", "MPa"))

    def test_the_example_runs_on_the_runs_design_after_make_tool(self):
        t = get_tool_template(self.ctx, {}, "cae.strength")
        self.assertTrue(make_tool(self.ctx, {"code": t["code"], "reason": "test"}, "cae.strength")["usable"])
        lc = dict(self.ctx.load_case("R1-step100"), params={"force": {"value": 2.0, "unit": "kN"}, "member": "rail_L0"})
        rec = self.ctx.tools["example_midspan_bending"].run(self.ctx.store.design(), lc,
                                                           [{"prediction_id": "pred-0001"}], {})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        (r,) = rec["results"]
        self.assertEqual((r["quantity"], r["unit"], r["where"]), ("max_stress", "MPa", "rail_L0"))
        bad = dict(lc, params={"member": "nope"})
        rec = self.ctx.tools["example_midspan_bending"].run(self.ctx.store.design(), bad, [{"prediction_id": "pred-0001"}], {})
        self.assertEqual(rec["status"], "failed")
        self.assertIn("nope", rec["error"])

    def test_the_explanations_come_from_the_code(self):
        t = get_tool_template(self.ctx)
        self.assertIn(str(inspect.signature(check_record)), t["known_answers"])
        self.assertIn("known_answers()", t["define"])
        self.assertEqual(set(t["analyse_args"]), {"design", "load_case", "params", "predictions"})
        self.assertEqual(t["result_fields"]["required"], ["quantity", "value", "unit"])
        self.assertIn("where", t["result_fields"]["all"])
        self.assertIn("numpy", t["imports"])
        self.assertIn("get_tool_template", DESCRIPTIONS)
        self.assertEqual(INPUT_SCHEMAS["get_tool_template"]["properties"], {})

    def test_the_designer_still_cannot_make_tools(self):
        t = get_tool_template(self.ctx)
        with self.assertRaisesRegex(ToolError, "設計役"):
            make_tool(self.ctx, {"code": t["code"]}, "designer")


if __name__ == "__main__":
    unittest.main()
