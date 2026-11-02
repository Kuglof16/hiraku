"""Tools made during a run (hiraku.solvers.toolmaker): checked in their own process on their
own known answers, refused with sentences when something is wrong, kept on the run's shelf.

The tool used here is a small cantilever (tip deflection F L^3 / 3 E I), not a tool the
product needs.
"""

import json
import tempfile
import unittest
from pathlib import Path

from hiraku import contract
from hiraku.solvers import toolmaker
from hiraku.solvers.toolmaker import guard

# 10 N at the tip of a 1 m steel bar, 10 x 10 mm (I = 10^4 / 12 mm^4, E = 205000 MPa):
# d = 10 * 1000^3 / (3 * 205000 * 833.333) = 19.5122 mm, worked out by hand for the check
CANTILEVER = '''
from hiraku.solvers.shelf.base import BuiltinTool, Param, check_record, param_value, result


def tip_deflection(F, L, E, I):
    return F * L ** 3 / (DENOMINATOR * E * I)


class Cantilever(BuiltinTool):
    name = "cantilever_tip"
    title = "片持ちの梁の先のたわみ"
    domain = "strength"
    base = "K0"
    tests = ["R5"]
    load_case_params = (Param("force", "N", "tip force", default=100.0),
                        Param("length", "mm", "length of the bar", default=500.0),
                        Param("width", "mm", "side of the square bar", default=20.0))

    def known_answers(self):
        got = tip_deflection(10.0, 1000.0, 205000.0, 10.0 ** 4 / 12.0)
        return [check_record("10 N at the tip of a 1 m, 10 x 10 mm steel bar", "tip_deflection", 19.5122, got,
                             "mm", 1e-4, "closed form d = F L^3 / (3 E I), worked out by hand")]

    def analyse(self, design, load_case, params):
        F = param_value(load_case, "force", "N", 100.0)
        L = param_value(load_case, "length", "mm", 500.0)
        b = param_value(load_case, "width", "mm", 20.0)
        return {"results": [result("tip_deflection", tip_deflection(F, L, 205000.0, b ** 4 / 12.0), "mm")]}
'''
GOOD = CANTILEVER.replace("DENOMINATOR", "3.0")
WRONG = CANTILEVER.replace("DENOMINATOR", "2.0")


def example(name):
    with (contract.EXAMPLES_DIR / name).open(encoding="utf-8") as f:
        return json.load(f)


class Guard(unittest.TestCase):
    def test_imports_and_names_outside_the_guard_are_refused(self):
        cases = {"import os\n": "import os", "import subprocess\n": "import subprocess",
                 "from scipy import io\n": "import scipy.io", "from . import x\n": "相対",
                 "f = open('x')\n": "open", "x = ().__class__\n": "__class__",
                 "import numpy as np\nnp.zeros(2).tofile('x')\n": "tofile",
                 "import hiraku.solvers.k0_handcalc as k\nk.os\n": "属性 os",
                 "eval('1')\n": "eval", "def f(:\n": "読めません"}
        for code, word in cases.items():
            with self.subTest(code=code):
                problems = guard.static_problems(code)
                self.assertTrue(problems)
                self.assertIn(word, " ".join(problems))
        self.assertEqual(guard.static_problems(GOOD), [])
        self.assertEqual(guard.static_problems("import numpy as np\nfrom scipy.optimize import brentq\n"
                                               "class A:\n    def __init__(self):\n        super().__init__()\n"), [])

    def test_a_refused_tool_never_runs(self):
        made = toolmaker.make_tool("import os\nos.remove('x')\n")
        self.assertFalse(made.usable)
        self.assertIsNone(made.spec)


class MakeTool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.good = toolmaker.make_tool(GOOD)
        cls.wrong = toolmaker.make_tool(WRONG)

    def test_a_tool_that_passes_its_checks_is_usable(self):
        made = self.good
        self.assertEqual(made.problems, [])
        self.assertTrue(made.usable)
        self.assertEqual(made.spec["origin"], "claude")
        self.assertEqual(made.spec["name"], "cantilever_tip")
        self.assertEqual(contract.errors("tool_spec", made.spec), [])
        tool = made.tool
        self.assertEqual(tool.name, "cantilever_tip")
        self.assertTrue(tool.spec()["usable"])
        self.assertEqual(len(tool.check()), 1)

    def test_it_runs_like_a_shelf_tool(self):
        lc = dict(example("load_case.json"), params={"force": {"value": 0.1, "unit": "kN"},
                                                     "length": {"value": 1.0, "unit": "m"},
                                                     "width": {"value": 10.0, "unit": "mm"}})
        rec = self.good.tool.run(example("design.json"), lc, [{"prediction_id": "pred-0001"}], {})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        got = {r["quantity"]: r["value"] for r in rec["results"]}
        self.assertAlmostEqual(got["tip_deflection"], 100.0 * 1000.0 ** 3 / (3 * 205000.0 * 10.0 ** 4 / 12.0), places=9)
        self.assertEqual(contract.errors("analysis", dict(rec, analysis_id="ana-0001")), [])

    def test_a_wrong_answer_is_explained(self):
        made = self.wrong
        self.assertFalse(made.usable)
        self.assertIsNone(made.tool)
        text = " ".join(made.problems)
        self.assertIn("10 N at the tip", text)
        self.assertIn("答えは 19.5122 mm", text)
        self.assertIn("+50.00 %", text)

    def test_a_pass_flag_is_worked_out_again(self):
        code = GOOD.replace("return [check_record(", "return [dict(check_record(").replace(
            '"closed form d = F L^3 / (3 E I), worked out by hand")]',
            '"closed form d = F L^3 / (3 E I), worked out by hand"), got=1.0, **{"pass": True})]')
        made = toolmaker.make_tool(code)
        self.assertFalse(made.usable)
        self.assertIn("計算すると False", " ".join(made.problems))

    def test_errors_in_the_process_come_back_as_sentences(self):
        cases = {
            "known_answers": (GOOD.replace("        got = tip_deflection", "        import numpy as np\n"
                                                                        "        np.save('x', np.zeros(1))\n"
                                                                        "        got = tip_deflection"),
                              ["PermissionError", "行目"]),
            "no tool": ("x = 1\n", ["道具が 1 つに決まりません"]),
            "no checks": (GOOD.replace("    def known_answers(self):\n", "    def known_answers(self):\n        return []\n"),
                          ["答えの分かっている例がありません"]),
        }
        for label, (code, words) in cases.items():
            with self.subTest(case=label):
                made = toolmaker.make_tool(code)
                self.assertFalse(made.usable)
                for w in words:
                    self.assertIn(w, " ".join(made.problems))

    def test_the_time_limit_stops_a_tool(self):
        code = GOOD.replace("        got = tip_deflection", "        while True:\n            pass\n        got = tip_deflection")
        made = toolmaker.make_tool(code, timeout_s=5.0)
        self.assertFalse(made.usable)
        self.assertIn("時間の上限 5 s", " ".join(made.problems))

    def test_a_failing_run_is_a_failed_record(self):
        code = GOOD.replace('        F = param_value(load_case, "force", "N", 100.0)',
                            '        F = param_value(load_case, "force", "N", 100.0) / 0.0')
        made = toolmaker.make_tool(code)
        self.assertTrue(made.usable, made.problems)
        rec = made.tool.run(example("design.json"), example("load_case.json"), [{"prediction_id": "pred-0001"}], {})
        self.assertEqual(rec["status"], "failed")
        self.assertIn("ZeroDivisionError", rec["error"])


class Process(unittest.TestCase):
    def test_the_api_key_and_the_working_folder_stay_out(self):
        import os
        from unittest import mock

        from hiraku.solvers.toolmaker import make

        seen = {}

        def fake_run(cmd, **kwargs):
            seen.update(kwargs, cmd=cmd)
            raise make.subprocess.TimeoutExpired(cmd, kwargs["timeout"])

        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "not-a-real-key"}), \
                mock.patch.object(make.subprocess, "run", fake_run):
            out = make._child("x = 1\n", "check", None, 1.0)
        self.assertFalse(out["ok"])
        self.assertNotIn("ANTHROPIC_API_KEY", seen["env"])
        self.assertIn("-I", seen["cmd"])
        self.assertNotEqual(Path(seen["cwd"]).resolve(), Path.cwd().resolve())


class Shelf(unittest.TestCase):
    def test_the_run_shelf_keeps_the_code_and_the_check_record(self):
        events = []
        with tempfile.TemporaryDirectory() as d:
            shelf = toolmaker.RunShelf(d, emit=lambda kind, data: events.append((kind, data["name"], data["usable"])))
            shelf.make("import os\n")  # refused before it runs: no spec, no event
            bad = shelf.make(WRONG, reason="R5: the shelf has no cantilever")
            good = shelf.make(GOOD)
            again = shelf.make(GOOD)  # the name is taken now
            files = sorted(p.name for p in (Path(d) / "tools").iterdir())
            record = json.loads((Path(d) / "tools" / "cantilever_tip-1.check.json").read_text(encoding="utf-8"))
        self.assertFalse(bad.usable)
        self.assertTrue(good.usable)
        self.assertFalse(again.usable)
        self.assertIn("棚にもうあります", " ".join(again.problems))
        self.assertEqual([t.name for t in shelf.tools()], ["cantilever_tip"])
        self.assertEqual(events, [("tool.checked", "cantilever_tip", False), ("tool.checked", "cantilever_tip", True),
                                  ("tool.checked", "cantilever_tip", False)])
        self.assertEqual(files, ["cantilever_tip-1.check.json", "cantilever_tip-1.py", "cantilever_tip-2.check.json",
                                 "cantilever_tip-2.py", "cantilever_tip-3.check.json", "cantilever_tip-3.py",
                                 "unnamed-1.check.json", "unnamed-1.py"])
        self.assertFalse(record["usable"])
        self.assertTrue(record["problems"])
        self.assertEqual(record["reason"], "R5: the shelf has no cantilever")


if __name__ == "__main__":
    unittest.main()
