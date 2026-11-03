"""Shelf checks reused across runs while the hiraku source is unchanged (docs/contract.md 照合の使い回し)."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hiraku import contract
from hiraku.agent.session import start_run
from hiraku.tools import shelf
from tests.tools import fakes
from tests.tools.helpers import requirements_with_tests


class CountingTool(fakes.FakeStepClimb):
    """The stand-in step_climb, counting its known-answer runs."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.checked = 0

    def check(self):
        self.checked += 1
        return super().check()


class HirakuTool(CountingTool):
    pass


HirakuTool.__module__ = "hiraku.solvers.stand_in"  # as if its code were in the package: the hash covers it


class ShelfReuseTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.design = self.root / "design.json"
        self.design.write_text(json.dumps(fakes.loaded_design()), encoding="utf-8")
        self.req = requirements_with_tests(self.root, ["P1"])

    def run_with(self, tool):
        self.n = getattr(self, "n", 0) + 1  # run ids go by the second: one slug per run
        return start_run(self.req, design_path=self.design, root=self.root / "runs", slug=f"reuse{self.n}",
                         formulas=fakes.FakeFormulas(), tools={"step_climb": tool}, designgen=fakes.FakeDesigngen())

    @staticmethod
    def checked(ctx):
        (e,) = [e for e in ctx.store.events() if e["kind"] == "tool.checked"]
        return e

    def test_a_second_run_reuses_the_check_of_the_same_source(self):
        tool = HirakuTool()
        first = self.run_with(tool)
        e1 = self.checked(first)
        self.assertEqual(tool.checked, 1)
        self.assertTrue(all(c["code_hash"] == shelf.source_hash() and "cached_from" not in c for c in e1["data"]["checks"]))
        kept = list((self.root / "runs").glob(shelf.TOOL_CHECK_GLOB))
        self.assertEqual(len(kept), 1)
        self.assertEqual([p for p in (self.root / "runs").iterdir() if p.is_dir()], [first.store.path])  # a file, not a folder

        second = self.run_with(tool)
        e2 = self.checked(second)
        self.assertEqual(tool.checked, 1)  # not run again
        self.assertTrue(all(c["cached_from"] == first.project["run_id"] for c in e2["data"]["checks"]))
        self.assertIn(first.project["run_id"], e2["text"])
        self.assertEqual(contract.errors("tool_spec", e2["data"]), [])
        self.assertTrue(second.specs["step_climb"]["usable"])

        with mock.patch.object(shelf, "source_hash", return_value="f" * 64):  # one line of code changed
            third = self.run_with(tool)
        self.assertEqual(tool.checked, 2)
        self.assertNotIn("cached_from", self.checked(third)["data"]["checks"][0])
        self.assertEqual([p.name for p in (self.root / "runs").glob(shelf.TOOL_CHECK_GLOB)],
                         [shelf.TOOL_CHECK_FILE.format(hash="f" * shelf.HASH_CHARS, tool="step_climb")])

    def test_a_tool_whose_code_is_not_in_the_package_is_checked_every_time(self):
        tool = CountingTool()
        self.run_with(tool)
        self.run_with(tool)
        self.assertEqual(tool.checked, 2)
        self.assertEqual(list((self.root / "runs").glob(shelf.TOOL_CHECK_GLOB)), [])

    def test_the_hash_changes_with_any_source_file(self):
        code = shelf.source_hash()
        self.assertEqual(len(code), 64)
        shelf.source_hash.cache_clear()
        self.assertEqual(shelf.source_hash(), code)


if __name__ == "__main__":
    unittest.main()
