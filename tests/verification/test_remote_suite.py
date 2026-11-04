"""qa.remote_suite: reading unittest's last lines and the order of the suites (no suite is run)."""

import unittest

from qa import remote_suite as rs


class ReadResult(unittest.TestCase):
    def test_qa_run_tests_lines(self):
        text = ("FAIL: x\n----\nRan 3412 tests in 612.3s (590 classes in 8 processes; 4100 s of work)\n"
                "discovered 3412 tests\nFAILED (failures=2, errors=1, skipped=210, classes not run=1)\n"
                "slowest classes: A 1s\n")
        self.assertEqual(rs.read_result(text), {"ran": 3412, "unittest_s": 612.3, "result": "FAILED", "failures": 2,
                                                "errors": 1, "skipped": 210, "classes_not_run": 1})

    def test_unittest_lines(self):
        self.assertEqual(rs.read_result("....\n------\nRan 88 tests in 1265.004s\n\nOK (skipped=3)\n"),
                         {"ran": 88, "unittest_s": 1265.004, "result": "OK", "skipped": 3})
        self.assertEqual(rs.read_result("Ran 1 test in 0.5s\nOK\n")["result"], "OK")

    def test_unread(self):
        self.assertEqual(rs.read_result("Traceback ...\nImportError: x\n"), {"result": "UNREAD"})

    def test_the_three_suites(self):
        plan = rs.suites(8, 6)
        self.assertEqual(list(plan), ["fast", "browser", "slow"])
        self.assertEqual(plan["slow"][1], {"HIRAKU_SLOW_TESTS": "1"})
        self.assertIn("--no-wait", plan["fast"][0])
        self.assertEqual(plan["browser"][0][-1], "tests.web.browser")

    def test_line(self):
        r = {"result": "OK", "ran": 5, "skipped": 2, "wall_s": 10.4, "exit": 0}
        self.assertEqual(rs.line("fast", r), "fast: OK, ran 5 tests (skipped 2) in 10 s, exit 0")


if __name__ == "__main__":
    unittest.main()
