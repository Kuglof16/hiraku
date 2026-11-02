import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hiraku import contract
from hiraku.contract.layout import RUN_ID_RE
from hiraku.webserve.drip import drip
from hiraku.webserve.server import SAMPLE_EVENTS


class DripTest(unittest.TestCase):
    def setUp(self):
        self.runs = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.runs)

    def test_copy_is_a_valid_run_at_the_new_pace(self):
        waits = []
        start = datetime(2026, 11, 2, 21, 0, 0, tzinfo=timezone(timedelta(hours=9)))
        out = drip(SAMPLE_EVENTS, self.runs, speed=2.0, sleep=waits.append, now=start)
        src = [json.loads(s) for s in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines() if s.strip()]
        got = [json.loads(s) for s in out.read_text(encoding="utf-8").splitlines()]
        run_id = out.parent.name
        self.assertRegex(run_id, RUN_ID_RE)
        self.assertEqual(len(got), len(src))
        self.assertAlmostEqual(sum(waits), src[-1]["t"] / 2.0)
        for a, b in zip(src, got):
            self.assertEqual(b["run_id"], run_id)
            self.assertAlmostEqual(b["t"], a["t"] / 2.0)
            self.assertEqual(b["kind"], a["kind"])
            self.assertEqual(contract.errors("event", b), [])
        self.assertEqual(got[0]["data"]["run_id"], run_id)
        project = json.loads((out.parent / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(contract.errors("project", project), [])


if __name__ == "__main__":
    unittest.main()
