"""make_tool and check_tool around hiraku.solvers.toolmaker (the K2 team's sandbox).

The tool is the K2 team's small cantilever (tests.solvers.k2.test_toolmaker): GOOD passes its
known answer, WRONG has 2 where 3 belongs and does not.
"""

import json
import unittest

from hiraku.contract import layout
from hiraku.tools import ToolError, rules
from hiraku.tools.functions import check_tool, list_tools, make_tool
from tests.solvers.k2.test_toolmaker import GOOD, WRONG
from tests.tools.helpers import RunCase


class MadeToolTest(RunCase, unittest.TestCase):
    def checked(self):
        return [e for e in self.ctx.store.events() if e["kind"] == "tool.checked" and e["data"].get("origin") == "claude"]

    def test_check_only_leaves_the_shelf_alone(self):
        out = check_tool(self.ctx, {"code": GOOD}, "cae.strength")
        self.assertEqual((out["name"], out["usable"], out["problems"]), ("cantilever_tip", True, []))
        self.assertNotIn("cantilever_tip", self.ctx.tools)
        self.assertEqual(self.checked(), [])

    def test_a_failed_attempt_is_recorded_then_the_fixed_one_is_shelved(self):
        out = make_tool(self.ctx, {"code": WRONG}, "cae.strength")
        self.assertFalse(out["usable"])
        self.assertTrue(any("tip_deflection" in p for p in out["problems"]))
        self.assertNotIn("cantilever_tip", self.ctx.tools)
        out = make_tool(self.ctx, {"code": GOOD}, "cae.strength")
        self.assertTrue(out["usable"])
        self.assertIn("cantilever_tip", self.ctx.tools)
        self.assertEqual(self.ctx.specs["cantilever_tip"]["origin"], "claude")
        # both attempts shown, the failed one first; the code is kept in the run folder
        self.assertEqual([(e["agent"], e["data"]["usable"]) for e in self.checked()],
                         [("cae.strength", False), ("cae.strength", True)])
        self.assertEqual(sorted(p.name for p in (self.ctx.store.path / "tools").glob("*.py")),
                         ["cantilever_tip-1.py", "cantilever_tip-2.py"])
        # on the shelf like a built-in tool: listed, passes HK2, runs in its own process
        self.assertIn("cantilever_tip", [t["name"] for t in list_tools(self.ctx, {"test_id": "R5"})])
        d = rules.check_call(self.ctx, "analyze", {"tool": "cantilever_tip", "load_case_id": "R1-step100",
                                                   "prediction_ids": []})
        self.assertNotEqual(getattr(d, "rule", None), rules.HK2)
        rec = self.ctx.tools["cantilever_tip"].run(self.ctx.store.design(), self.ctx.load_case("R1-step100"),
                                                   [{"prediction_id": "pred-0001"}], {})  # tools run only after a prediction
        self.assertEqual((rec["status"], rec["results"][0]["quantity"]), ("ok", "tip_deflection"))
        self.assert_events_fit()

    def test_refusals(self):
        with self.assertRaisesRegex(ToolError, "設計役"):
            make_tool(self.ctx, {"code": GOOD}, "designer")
        for args in ({}, {"code": ""}, {"code": GOOD, "name": "x"}):
            with self.subTest(args=list(args)):
                with self.assertRaisesRegex(ToolError, "make_tool の"):
                    make_tool(self.ctx, args, "cae.strength")
        out = make_tool(self.ctx, {"code": "import os\nos.remove('x')\n"}, "cae.strength")
        self.assertFalse(out["usable"])
        self.assertIn("import os", " ".join(out["problems"]))
        # never ran, so no spec: still recorded, with a minimal spec that fits the contract
        (attempt,) = self.checked()
        self.assertEqual((attempt["data"]["name"], attempt["data"]["usable"], attempt["domain"]),
                         ("unnamed", False, "strength"))
        self.assert_events_fit()
        self.assertTrue(make_tool(self.ctx, {"code": GOOD, "reason": "R5 has no tool"}, "cae.strength")["usable"])
        (kept,) = (self.ctx.store.path / layout.TOOLS_DIR).glob("cantilever_tip-*.check.json")
        self.assertEqual(json.loads(kept.read_text(encoding="utf-8"))["reason"], "R5 has no tool")

    def test_a_spec_that_does_not_fit_the_contract_is_recorded_but_never_shelved(self):
        from types import SimpleNamespace

        bad = {"name": "Bad Name", "title": "x", "domain": "strength", "base": "K9", "tests": ["R5"],
               "origin": "claude", "usable": True, "checks": []}
        made = SimpleNamespace(name="Bad Name", spec=bad, usable=True, checks=[], problems=[], printed="", tool=object())
        self.ctx.made_tools = SimpleNamespace(make=lambda code, taken, reason: made)
        out = make_tool(self.ctx, {"code": GOOD}, "cae.strength")
        self.assertFalse(out["usable"])
        self.assertNotIn("Bad Name", self.ctx.tools)
        (attempt,) = self.checked()
        self.assertEqual((attempt["data"]["name"], attempt["data"]["base"], attempt["data"]["usable"]),
                         ("unnamed", "K0", False))
        self.assert_events_fit()


if __name__ == "__main__":
    unittest.main()
