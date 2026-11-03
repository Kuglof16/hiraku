import json
import os
import re
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from promo import n17b_data as nd

# Identity and settings for the throwaway repository only (no real names).
ENV = {
    "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
}


class FakeRepo:
    """A small git history made in a temporary folder, with set times (JST)."""

    def __init__(self, path: Path):
        self.path = path
        self.git("init", "-q", "-b", "main")

    def git(self, *args, when=None):
        env = dict(os.environ, **ENV)
        if when:
            env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when
        return subprocess.run(["git", "-C", str(self.path), "-c", "commit.gpgsign=false",
                               "-c", "core.hooksPath=.no-hooks", *args],
                              env=env, capture_output=True, text=True, check=True).stdout

    def write(self, name, text):
        p = self.path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")

    def commit(self, name, text, msg, when):
        self.write(name, text)
        self.git("add", name)
        self.git("commit", "-q", "-m", msg, when=when)

    def merge(self, branch, msg, when):
        self.git("merge", "-q", "--no-ff", "-m", msg, branch, when=when)


def make_history(root: Path) -> FakeRepo:
    r = FakeRepo(root)
    # main: the start, with code, a generated file and a test file
    r.write("hiraku/a.py", "import os\n\n# a comment\ndef test_one():\n    pass\n\n\ndef test_two():\n    pass\n")
    r.write("web/b.js", "// app\nconst x = 1;\nexport { x };")  # 3 lines, no newline at the end
    r.write("web/gen.js", "// Generated file: do not edit.\nconst DATA = {};\n")
    r.write("README.md", "# readme\n")
    r.git("add", ".")
    r.git("commit", "-q", "-m", "Start the repository", when="2026-11-02T18:00:00+09:00")

    r.git("checkout", "-q", "-b", "claude/dept-qa-ab12")
    r.commit("qa/test_q.py", "def test_q():\n    pass\n", "QA: first check", "2026-11-02T19:00:00+09:00")
    r.commit("qa/run.py", "x = 1\n", "QA: runner", "2026-11-02T19:30:00+09:00")

    r.git("checkout", "-q", "main")
    r.git("checkout", "-q", "-b", "claude/dept-design-cd34")
    r.commit("design/d.py", "y = 2\n", "Design: kit", "2026-11-02T19:10:00+09:00")

    r.git("checkout", "-q", "main")
    r.commit("hiraku/contract.py", "Z = 3\n", "Contract: units", "2026-11-02T19:20:00+09:00")

    r.git("checkout", "-q", "claude/dept-design-cd34")
    r.merge("main", "Merge branch 'main' into claude/dept-design-cd34", "2026-11-02T19:25:00+09:00")

    r.git("checkout", "-q", "main")
    r.merge("claude/dept-qa-ab12", "Merge claude/dept-qa-ab12: first checks", "2026-11-02T20:00:00+09:00")

    # the screen department works on a side branch of its own and merges it in
    r.git("checkout", "-q", "-b", "claude/dept-screen-ef56")
    r.commit("web/screen.js", "const s = 1;\n", "Screen: view", "2026-11-02T21:00:00+09:00")
    r.git("checkout", "-q", "-b", "claude/screen-grid")
    r.commit("web/grid.css", "a { b: c; }\n", "Screen: grid", "2026-11-02T21:30:00+09:00")
    r.git("checkout", "-q", "claude/dept-screen-ef56")
    r.merge("claude/screen-grid", "Merge branch 'claude/screen-grid' into claude/dept-screen-ef56",
            "2026-11-02T21:40:00+09:00")

    r.git("checkout", "-q", "main")
    r.merge("claude/dept-screen-ef56", "Merge the screen department: view and grid", "2026-11-03T09:00:00+09:00")
    r.merge("claude/dept-design-cd34", "Merge claude/dept-design-cd34: kit", "2026-11-03T10:00:00+09:00")
    return r


class ParseMerge(unittest.TestCase):
    def test_forms(self):
        cases = {
            "Merge claude/dept-qa-ba0596: margin checks": ("qa", "main"),
            "Merge claude/dept-qa-ba0596 up to 28aaaa2": ("qa", "main"),
            "Merge claude/lead-refactor: field.frames": ("lead", "main"),
            "Merge branch 'main' into claude/dept-k2-0c4e6b": ("main", "k2"),
            "Merge remote-tracking branch 'origin/main' into claude/dept-screen-812e37": ("main", "screen"),
            "Merge branch 'claude/dept-analysis-mesh' into claude/dept-analysis-cf6cf8": ("analysis", "analysis"),
            "Merge branch 'claude/screen-n17' into claude/dept-screen-812e37": ("screen", "screen"),
            "Merge branch 'claude/dept-analysis-cf6cf8' into claude/dept-design-90c266": ("analysis", "design"),
            "Merge the design department: run store": ("design", "main"),
            "Merge the K2 team: assumptions field": ("k2", "main"),
            "Merge the QA department: margin checks": ("qa", "main"),
            "Merge the analysis department and K2 team: builders": ("analysis", "main"),
            "Merge main into claude/dept-design-90c266": ("main", "design"),
            "Merge main into the intake branch": ("main", "intake"),
            "Merge main: intake events in the scripted run": ("main", "main"),
            "Merge claude/dept-promo-f2ae3c: narration": ("promo", "main"),
            "Merge claude/dept-intake-932455: A1 request": ("intake", "main"),
        }
        for subject, want in cases.items():
            self.assertEqual(nd.parse_merge(subject), want, subject)

    def test_unknown_branch(self):
        self.assertEqual(nd.parse_merge("Merge claude/someone-else: x"), (None, "main"))
        self.assertEqual(nd.parse_merge("Add a file"), (None, None))

    def test_lane_table(self):
        names = [name for _k, name, _p, _w in nd.LANES]
        self.assertEqual(names, ["統括部", "設計部", "解析部", "解析部 K2 班", "画面部", "品質保証部", "受付部", "広報部"])
        self.assertEqual(nd.MAIN_LANE, "lead")


class Counting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.repo = make_history(Path(cls.tmp.name))
        cls.data = nd.collect(cls.repo.path, "main", now=datetime.fromisoformat("2026-11-04T08:15:00+09:00"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def lane(self, key):
        return next(ln for ln in self.data["lanes"] if ln["key"] == key)

    def test_lanes_from_the_merges(self):
        ts = lambda s: int(datetime.fromisoformat(s).timestamp())  # noqa: E731
        self.assertEqual(self.lane("lead")["commits"], [ts("2026-11-02T18:00:00+09:00"), ts("2026-11-02T19:20:00+09:00")])
        self.assertEqual(self.lane("qa")["commits"], [ts("2026-11-02T19:00:00+09:00"), ts("2026-11-02T19:30:00+09:00")])
        self.assertEqual(self.lane("design")["commits"], [ts("2026-11-02T19:10:00+09:00")])
        # the side branch claude/screen-grid belongs to the screen department
        self.assertEqual(self.lane("screen")["commits"], [ts("2026-11-02T21:00:00+09:00"), ts("2026-11-02T21:30:00+09:00")])
        for key in ("analysis", "k2", "intake", "promo"):
            self.assertEqual(self.lane(key)["commits"], [], key)
        # merges made on a branch are in that branch's lane; merges into main are the lead's
        self.assertEqual(self.lane("design")["merges_made"], 1)
        self.assertEqual(self.lane("screen")["merges_made"], 1)
        self.assertEqual(self.lane("lead")["merges_made"], 3)

    def test_merges_into_main(self):
        ts = lambda s: int(datetime.fromisoformat(s).timestamp())  # noqa: E731
        self.assertEqual(self.lane("qa")["into_main"], [ts("2026-11-02T20:00:00+09:00")])
        self.assertEqual(self.lane("screen")["into_main"], [ts("2026-11-03T09:00:00+09:00")])
        self.assertEqual(self.lane("design")["into_main"], [ts("2026-11-03T10:00:00+09:00")])
        self.assertEqual(self.data["merges_into_main"], 3)

    def test_totals(self):
        d = self.data
        self.assertEqual(d["commits"], 12)            # 7 commits and 5 merges
        self.assertEqual(d["commits_no_merges"], 7)
        self.assertEqual(sum(len(ln["commits"]) for ln in d["lanes"]), 7)
        self.assertEqual(d["days"], 2)                # 11/2 and 11/3
        self.assertEqual(d["first_commit"], "2026-11-02T18:00:00+09:00")
        self.assertEqual(d["head_time"], "2026-11-03T10:00:00+09:00")
        self.assertEqual(d["counted_at"], "2026-11-04T08:15+09:00")

    def test_lines_and_tests(self):
        d = self.data
        # a.py 9 + test_q.py 2 + run.py 1 + d.py 1 + contract.py 1; b.js 3 + screen.js 1; grid.css 1
        self.assertEqual(d["lines"], {"Python": 14, "JavaScript": 4, "CSS": 1})
        self.assertEqual(d["lines_total"], 19)
        self.assertEqual(d["generated_skipped"], ["web/gen.js"])
        self.assertEqual(d["tests"], 3)

    def test_no_names_in_the_data(self):
        text = nd.render_js(self.data)
        self.assertNotIn("test@example.invalid", text)
        self.assertNotIn('"test"', text)
        self.assertTrue(text.startswith("// Generated file: do not edit."))
        body = json.loads(text[text.index("{"):text.rindex("}") + 1])
        self.assertEqual(body, self.data)


class Helpers(unittest.TestCase):
    def test_count_lines(self):
        self.assertEqual(nd.count_lines(b""), 0)
        self.assertEqual(nd.count_lines(b"a\n"), 1)
        self.assertEqual(nd.count_lines(b"a\n\nb"), 3)

    def test_generated(self):
        self.assertTrue(nd.is_generated(b"// Generated by `python -m x`.\n"))
        self.assertTrue(nd.is_generated(b"# generated file\n"))
        self.assertFalse(nd.is_generated(b"// Coordinates of the screens\n// (window.X, generated from layout.json)\n"))

    def test_data_file_in_the_repo(self):
        # the screen's data file is written by this script (counted from main)
        text = nd.OUT.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("// Generated file: do not edit. Written by promo/n17b_data.py"))
        d = json.loads(text[text.index("{"):text.rindex("}") + 1])
        self.assertEqual([ln["name"] for ln in d["lanes"]], [name for _k, name, _p, _w in nd.LANES])
        self.assertRegex(d["counted_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d\+09:00$")
        self.assertEqual(d["lines_total"], sum(d["lines"].values()))
        self.assertGreater(d["commits"], d["commits_no_merges"])
        self.assertIsNone(re.search(r"@[a-z0-9.-]+\.[a-z]+", text))  # no e-mail addresses


if __name__ == "__main__":
    unittest.main()
