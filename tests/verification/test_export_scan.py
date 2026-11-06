"""qa.export_scan: a static export is read for what must not be published (hiraku/ runs it as a process)."""

import gzip
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from qa import export_scan as es

ROOT = Path(__file__).resolve().parents[2]


class ExportScanTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.hook = self.tmp / "hook"
        self.hook.write_text("WORDS='zqprivatezq'\nPATHS='zqpathzq'\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _export(self, name, files):
        out = self.tmp / name
        out.mkdir()
        for rel, text in files.items():
            p = out / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if rel.endswith(".gz"):
                p.write_bytes(gzip.compress(text.encode("utf-8")))
            else:
                p.write_text(text, encoding="utf-8")
        return out

    def test_a_clean_export_has_no_errors(self):
        out = self._export("clean", {"index.html": "<p>HIRAKU</p>", "data/run.json": json.dumps({"a": 1})})
        findings, checked = es.scan(out, self.hook)
        self.assertTrue(checked)
        self.assertEqual([str(f) for f in findings if f.level == "error"], [])

    def test_planted_words_and_paths_are_found(self):
        home = "C:" + "/Users/someone/x"  # (split so this file holds no such path)
        out = self._export("planted", {
            "data/events.jsonl.gz": json.dumps({"text": f"zqprivatezq {home}"}),
            "notes.txt": "D:\\\\work\\\\a",
        })
        rules = sorted({f.rule for f in es.scan(out, self.hook)[0] if f.level == "error"})
        self.assertEqual(rules, ["home.path", "local.path", "private.word"])

    def test_the_command_exits_1_on_an_error(self):
        out = self._export("cmd", {"notes.txt": "zqprivatezq"})
        run = subprocess.run([sys.executable, "-m", "qa.export_scan", str(out), "--words-from", str(self.hook)],
                             cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn("private.word", run.stdout)


if __name__ == "__main__":
    unittest.main()
