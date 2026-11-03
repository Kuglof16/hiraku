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

    def test_the_k1_example_passes_check_tool_and_runs_on_the_runs_design(self):
        t = get_tool_template(self.ctx, {}, "cae.strength")
        code = t["examples"]["k1_frame"]
        out = check_tool(self.ctx, {"code": code}, "cae.strength")
        self.assertEqual(out["problems"], [])
        self.assertTrue(out["usable"])
        self.assertEqual(out["name"], "example_frame_at_rest")
        self.assertEqual([c["quantity"] for c in out["checks"]], ["max_stress", "support_force_total"])
        self.assertTrue(all(c["pass"] for c in out["checks"]))
        self.assertTrue(make_tool(self.ctx, {"code": code, "reason": "test"}, "cae.strength")["usable"])
        tool = self.ctx.tools["example_frame_at_rest"]
        lc = dict(self.ctx.load_case("R1-step100"), params={"loading": "driver", "g_factor": {"value": 1.0, "unit": "1"}})
        rec = tool.run(self.ctx.store.design(), lc, [{"prediction_id": "pred-0001"}], {})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        got = {r["quantity"]: r for r in rec["results"]}
        self.assertGreater(got["max_stress"]["value"], 0)
        self.assertIn(got["max_stress"]["where"], {m["id"] for m in self.ctx.store.design()["members"]})

    def test_check_tool_lists_the_api_from_the_toolmakers_allow_list(self):
        from hiraku.solvers.toolmaker import guard

        out = check_tool(self.ctx, {"code": get_tool_template(self.ctx)["code"]}, "cae.strength")
        api = out["api"]
        self.assertEqual(set(api["hiraku"]), {m for m in guard.ALLOWED_MODULES if m.startswith("hiraku.")})
        self.assertEqual(set(api["modules"]), {m for m in guard.ALLOWED_MODULES if not m.startswith(("hiraku.", "__"))})
        self.assertIn("analyze", api["hiraku"]["hiraku.solvers.k1_frame"])
        self.assertIn("build", api["hiraku"]["hiraku.solvers.k1_frame"])
        self.assertIn("check_record", api["hiraku"]["hiraku.solvers.shelf.base"])
        self.assertIn("rule", out)

    def test_a_tool_without_formulas_or_a_referenced_answer_is_refused(self):
        code = get_tool_template(self.ctx)["code"]
        cases = {
            "formulas": (code.replace('    formulas = ["frame_static.rail_bending"]\n', ""), "formulas"),
            "unknown": (code.replace('"frame_static.rail_bending"', '"frame_static.nope"'), "frame_static.nope"),
            "own source": (code.replace('"closed form sigma = (F L / 4)(h / 2) / I, "', '"frame_static.rail_bending "')
                           .replace('"I = (b h^3 - (b - 2t)(h - 2t)^3) / 12, worked out by hand"', '"of this tool"'),
                           "出典"),
        }
        for what, (bad, word) in cases.items():
            with self.subTest(what=what):
                self.assertNotEqual(bad, code)
                out = check_tool(self.ctx, {"code": bad}, "cae.strength")
                self.assertFalse(out["usable"])
                self.assertIn(word, " ".join(out["problems"]))
        out = make_tool(self.ctx, {"code": cases["formulas"][0], "reason": "test"}, "cae.strength")
        self.assertFalse(out["usable"])
        self.assertNotIn("example_midspan_bending", self.ctx.tools)
        checked = [e for e in self.ctx.store.events() if e["kind"] == "tool.checked" and e["data"].get("origin") == "claude"]
        self.assertFalse(checked[-1]["data"]["usable"])
        # fixed, the same name goes on the shelf (the refused attempt is not kept as taken)
        self.assertTrue(make_tool(self.ctx, {"code": code, "reason": "test"}, "cae.strength")["usable"])
        self.assertIn("rule", get_tool_template(self.ctx))

    def test_the_designer_still_cannot_make_tools(self):
        t = get_tool_template(self.ctx)
        with self.assertRaisesRegex(ToolError, "設計役"):
            make_tool(self.ctx, {"code": t["code"]}, "designer")


if __name__ == "__main__":
    unittest.main()
