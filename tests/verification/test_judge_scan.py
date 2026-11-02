"""qa.judge_scan finds what it promises, in a throwaway git repository.

The planted strings are assembled at run time so this file itself stays clean
when the scan reads the real repository.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from qa import judge_scan as js

AT = "@"
WORD = "Planted" + "Word"


def git(repo, *args, date="2026-11-02T13:00:00+09:00"):
    env = dict(os.environ, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    subprocess.run(["git", "-c", "user.name=qa", "-c", f"user.email=qa{AT}users.noreply.github.com",
                    "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
                   cwd=repo, env=env, check=True, capture_output=True)


class JudgeScan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.hook = Path(self.tmp.name) / "pre-commit"
        self.hook.write_text(f"WORDS='{WORD}|other'\nPATHS='q:[\\\\/]+secret'\n", encoding="utf-8")
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "LICENSE").write_text("MIT\n", encoding="utf-8")
        (self.repo / "README.md").write_text("確かめていない値: なし\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "Start")

    def tearDown(self):
        self.tmp.cleanup()

    def scan(self):
        return js.Scan(self.repo, "main", self.hook).run()

    def commit(self, files, message="Add files", date="2026-11-02T14:00:00+09:00"):
        for name, text in files.items():
            p = self.repo / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", message, date=date)

    def rules(self, level="error"):
        return {f.rule for f in self.scan() if f.level == level}

    def test_clean_repository(self):
        self.assertEqual([str(f) for f in self.scan()], [])

    def test_private_word_from_the_hook(self):
        self.commit({"hiraku/a.py": f"# notes from {WORD}\n"})
        self.assertIn("private.word", self.rules())

    def test_private_path_from_the_hook(self):
        self.commit({"docs/a.md": "see Q:" + "\\" + "secret\\x\n"})
        self.assertIn("private.word", self.rules())

    def test_private_word_in_a_commit_message(self):
        self.commit({"a.txt": "x\n"}, message=f"Copy the plan from {WORD}")
        self.assertIn("private.word", self.rules())

    def test_email_home_path_and_key(self):
        self.commit({"hiraku/b.py": "\n".join([
            f'CONTACT = "someone{AT}mail.example.net"',
            'P = r"C:' + "\\" + "Users" + "\\" + 'someone\\x"',
            'K = "sk-' + 'ant-' + "a" * 24 + '"',
        ]) + "\n"})
        self.assertTrue({"email", "home.path", "secret"} <= self.rules())

    def test_tracked_local_files(self):
        self.commit({".env": "X=1\n", "runs/r/events.jsonl": "{}\n"})
        self.assertIn("local.file", self.rules())

    def test_commit_before_the_start(self):
        self.commit({"a.txt": "x\n"}, date="2026-11-02T11:59:00+09:00")
        self.assertIn("date", self.rules())

    def test_review_items(self):
        self.commit({
            "hiraku/solvers/c.py": "def f():\n    pass\n\n\nYIELD = 235.0\nE = 205000.0  # source: check input\n# TODO later\n",
            "data/m.json": '{"E_MPa": 69000.0, "verified": false}\n',
        }, message="今日は本人と話して決めた")
        review = [f for f in self.scan() if f.level == "review"]
        rules = {f.rule for f in review}
        self.assertTrue({"stub", "number.source", "unverified", "data.source", "speech.diary"} <= rules, review)
        numbered = [f.where for f in review if f.rule == "number.source"]
        self.assertIn("hiraku/solvers/c.py:5", numbered)
        self.assertNotIn("hiraku/solvers/c.py:6", numbered)  # has a source note

    def test_significant_digits(self):
        self.assertEqual([js._significant(t) for t in ("205000.0", "0.15", "900.0", "1341.64", "7")], [3, 2, 1, 6, 1])

    def test_patterns_are_read_from_the_hook(self):
        self.assertEqual(len(js.private_patterns(self.hook)), 2)
        self.assertEqual(js.private_patterns(Path(self.tmp.name) / "missing"), [])
