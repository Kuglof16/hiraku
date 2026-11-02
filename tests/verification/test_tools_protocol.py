"""Every AnalysisTool on the shelf keeps the promises in protocols.py.

These hold for any tool, built in or made by Claude during a run: the spec fits
the contract, the known-answer checks are real (pass is computed from expected,
got and tolerance), a tool is usable only when every check passes, and run()
returns a record instead of raising on a bad design.
"""

import copy
import json
import math
import unittest

from hiraku import contract

from ._solvers import analysis_tools


def example(name):
    with (contract.EXAMPLES_DIR / name).open(encoding="utf-8") as f:
        return json.load(f)


class ToolProtocol(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tools = analysis_tools()
        if not cls.tools:
            raise unittest.SkipTest("hiraku.solvers has no AnalysisTool objects yet")

    def test_spec_fits_the_contract(self):
        for name, tool in self.tools.items():
            with self.subTest(tool=name):
                spec = tool.spec()
                self.assertEqual(contract.errors("tool_spec", spec), [])
                self.assertEqual(spec["name"], name)

    def test_checks_are_honest(self):
        for name, tool in self.tools.items():
            checks = tool.check()
            with self.subTest(tool=name):
                self.assertTrue(checks, "a tool with no known-answer checks cannot be trusted")
            for c in checks:
                with self.subTest(tool=name, case=c.get("case")):
                    self.assertEqual(contract.errors("tool_check", c), [])
                    self.assertTrue(c["source"].strip(), "check without a source")
                    if c["got"] is None or not math.isfinite(c["got"]):
                        self.assertFalse(c["pass"], "pass without a value")
                        continue
                    within = abs(c["got"] - c["expected"]) <= c["tolerance_rel"] * abs(c["expected"])
                    self.assertEqual(c["pass"], within, "pass flag does not follow from expected/got/tolerance")

    def test_usable_means_every_check_passes(self):
        for name, tool in self.tools.items():
            with self.subTest(tool=name):
                spec = tool.spec()
                checks = tool.check()
                self.assertEqual(spec["usable"], bool(checks) and all(c["pass"] for c in checks))

    def test_frame_tool_on_a_bad_design_returns_failed_not_an_exception(self):
        design = example("design.json")
        load_case = example("load_case.json")
        prediction = example("prediction.json")
        broken = copy.deepcopy(design)
        for m in broken["members"]:
            m["section"] = "no_such_section"
        frame_tools = {n: t for n, t in self.tools.items() if t.spec()["base"] == "K1"}
        if not frame_tools:
            self.skipTest("no K1 (frame) tool yet")
        for name, tool in frame_tools.items():
            with self.subTest(tool=name):
                out = tool.run(broken, load_case, [prediction], {})
                self.assertEqual(out.get("status"), "failed")
                self.assertTrue(out.get("error", "").strip(), "failed without saying what to change")
                rec = dict(out, analysis_id="ana-0001")
                self.assertEqual(contract.errors("analysis", rec), [])
