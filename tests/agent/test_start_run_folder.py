"""start_run(on_folder=...): the run folder is named as soon as it exists, before the tool shelf is checked (the screen
opens the run on `python -m hiraku run`'s first "run folder:" line instead of waiting for the checks)."""

import json
import tempfile
import unittest
from pathlib import Path

from hiraku.agent.session import start_run
from hiraku.contract import layout
from tests.tools import fakes
from tests.tools.helpers import requirements_with_tests


class OnFolderTest(unittest.TestCase):
    def test_the_folder_is_named_before_the_tools_are_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design = root / "design.json"
            design.write_text(json.dumps(fakes.loaded_design()), encoding="utf-8")
            seen = []

            def on_folder(path):
                events = (path / layout.EVENTS_FILE).read_text(encoding="utf-8").splitlines()
                seen.append((path, [json.loads(e)["kind"] for e in events if e.strip()]))

            ctx = start_run(requirements_with_tests(root, ["P1", "R1"]), design_path=design, root=root,
                            slug="offroad-4w-test", formulas=fakes.FakeFormulas(), tools=fakes.shelf(1.03, 1.03, True),
                            designgen=fakes.FakeDesigngen(), on_folder=on_folder)
            self.assertEqual(len(seen), 1)
            path, kinds = seen[0]
            self.assertEqual(path, ctx.store.path)
            self.assertIn("run.started", kinds)
            self.assertNotIn("tool.checked", kinds)  # the shelf is checked after
            self.assertIn("tool.checked", [e["kind"] for e in ctx.store.events()])


if __name__ == "__main__":
    unittest.main()
