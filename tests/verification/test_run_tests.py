"""qa.run_tests: discovery by class, one worker's record, and the class-skip line."""

import io
import json
import tempfile
import unittest
from pathlib import Path

from qa import run_tests as rt


class RunTests(unittest.TestCase):
    def test_discovery_groups_by_class(self):
        groups = rt.discover()
        mine = groups.get(f"{__name__}.RunTests")
        self.assertIsNotNone(mine)
        self.assertIn(f"{__name__}.RunTests.test_discovery_groups_by_class", mine)
        self.assertTrue(all(i.startswith(name) or "_FailedTest" in i for name, ids in groups.items() for i in ids))

    def test_worker_writes_what_ran(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "w.json"
            rt.worker(out, ["tests.verification.test_compare_runs.CompareRuns.test_newest_baseline"])
            rec = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(rec["ran"], 1)
        self.assertEqual(rec["ids_run"], ["tests.verification.test_compare_runs.CompareRuns.test_newest_baseline"])
        self.assertEqual((rec["failures"], rec["errors"], rec["skipped"]), ([], [], []))

    def test_a_class_skipped_in_setupclass_is_named(self):
        def skip(cls):
            raise unittest.SkipTest("not here")
        cls = type("Skipped", (unittest.TestCase,), {"setUpClass": classmethod(skip), "test_a": lambda self: None,
                                                    "__module__": "tests.somewhere"})
        res = unittest.TextTestRunner(stream=io.StringIO()).run(unittest.TestSuite([cls("test_a")]))
        self.assertEqual(res.testsRun, 0)
        m = rt.CLASS_SKIP_RE.match(str(res.skipped[0][0]))
        self.assertEqual(m.group(1) if m else None, "tests.somewhere.Skipped")

    def test_heavy_classes_start_first_without_times(self):
        groups = {"tests.store.test_x.A": ["i"] * 30, "tests.solvers.k2.test_y.B": ["j"],
                  "tests.verification.test_real_runs.ScriptedRunCommand": ["k"]}
        saved, rt.TIMES = rt.TIMES, Path(tempfile.gettempdir()) / "no-such-times.json"
        try:
            order = rt._order(groups)
        finally:
            rt.TIMES = saved
        self.assertEqual(order[-1], "tests.store.test_x.A")


if __name__ == "__main__":
    unittest.main()
