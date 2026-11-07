"""qa.judge_scan finds what it promises, in a throwaway git repository.

The planted strings are assembled at run time so this file itself stays clean
when the scan reads the real repository.
"""

import contextlib
import io
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
        self.commit({"a.txt": "x\n"}, message=f"private.word fixture: {WORD}")
        self.assertIn("private.word", self.rules())

    def test_email_home_path_and_key(self):
        self.commit({"hiraku/b.py": "\n".join([
            f'CONTACT = "someone{AT}mail.example.net"',
            'P = r"C:' + "\\" + "Users" + "\\" + 'taro\\x"',
            'K = "sk-' + 'ant-' + "a" * 24 + '"',
        ]) + "\n"})
        self.assertTrue({"email", "home.path", "secret"} <= self.rules())

    def test_a_long_run_without_an_at_is_quick_and_an_address_after_it_is_found(self):
        import time
        scan = js.Scan(self.repo, "main", self.hook)
        line = "A" * 300_000 + f" someone{AT}mail.example.net"  # (a base64 run as in the viewer fixtures)
        t = time.perf_counter()
        scan.check_line("x.json:1", line, doc=False)
        self.assertLess(time.perf_counter() - t, 5.0)  # unbounded, it took minutes (11/7)
        self.assertEqual([f.text for f in scan.findings if f.rule == "email"], [f"someone{AT}mail.example.net"])

    def test_a_pattern_that_names_the_home_folders_is_not_a_path(self):
        home = "/" + "Users" + "/"
        self.commit({"tests/test_x.py": "\n".join([
            f'B = r"[A-Za-z]:\\\\|{home}|' + '/' + 'home/|x"',  # as tests/viewer/no_paths.py
            f'C = "{home}taro/x"',  # a folder with a name still counts
        ]) + "\n"})
        scan = js.Scan(self.repo, "main", self.hook)
        self.assertEqual([f.text for f in scan.run() if f.rule == "home.path"], [home + "taro"])

    def test_made_up_addresses_on_reserved_domains_are_left_out(self):
        self.commit({"promo/tests/test_x.py": "\n".join([
            f'A = "viewer{AT}example.invalid"',
            f'B = "qa{AT}runs.test"',
            f'C = "someone{AT}mail.example"',
            f'D = "real{AT}mail.example.net"',  # a real-looking domain still counts
        ]) + "\n"})
        scan = js.Scan(self.repo, "main", self.hook)
        found = [f for f in scan.run() if f.rule == "email"]
        self.assertEqual([f.text for f in found], [f"real{AT}mail.example.net"])
        self.assertEqual(scan.reserved_emails, 3)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            js.main(["--repo", str(self.repo), "--words-from", str(self.hook)])
        self.assertIn("left out 3 address(es) on domains reserved for tests", out.getvalue())

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
        }, message="speech.diary fixture: devlog entry")
        review = [f for f in self.scan() if f.level == "review"]
        rules = {f.rule for f in review}
        self.assertTrue({"stub", "number.source", "unverified", "data.source", "speech.diary"} <= rules, review)
        numbered = [f.where for f in review if f.rule == "number.source"]
        self.assertIn("hiraku/solvers/c.py:5", numbered)
        self.assertNotIn("hiraku/solvers/c.py:6", numbered)  # has a source note

    def test_numbers_that_are_not_values_from_outside(self):
        self.commit({
            "hiraku/solvers/d.py": "\n".join([
                '"""The quarter car of the check gives 1.891 Hz.',
                '"""',
                "G = 9.80665",
                "",
                "def deflection(w, L, E, I):",
                "    return 5 * w * L**4 / (384 * E * I)",
                "",
                "def known_answers():",
                "    return [(150.0, 1.732)]",
                "",
                "def settled(h):",
                "    return h[-1001] * 0.999",
                "",
                "class Base:",
                "    def run(self):",
                '        """Subclasses do it."""',
                "        raise NotImplementedError",
                "",
                "OCCUPANT_KG = 76.6  # Q33",
                "SCENE_POINTS = 801",
            ]) + "\n",
            "hiraku/designgen/d.py": 'V = {"seat_x": Var("mm", -725.0, -1100.0, -500.0, "seat centre")}\n',
            "data/designs/d.json": '{\n  "variables": {\n    "air_density": {\n      "value": 1.225\n    }\n  }\n}\n',
            # a revision whose source is written further down in the same list item
            "data/catalog/plan.yaml": "plan:\n  revise:\n    - say: wider\n      changes:\n"
                                      "        track: {value: 1220, unit: mm}\n      reason: >-\n"
                                      + "".join(f"        line {i} of the reason\n" for i in range(6))
                                      + "      source: the assembly tool before and after\n",
        })
        self.assertEqual([str(f) for f in self.scan() if f.rule in ("number.source", "stub")], [])

    def test_values_from_outside_are_still_listed(self):
        self.commit({
            "hiraku/solvers/e.py": "RHO = 1.225\n\n\ndef unfinished():\n    raise NotImplementedError\n",
            "hiraku/designgen/e.py": 'V = {"air_density": Var("kg/m^3", 1.225, meaning="")}\n',
            "data/materials/e.json": '{\n  "air_density": {\n    "value": 1.225,\n    "unit": "kg/m^3"\n  }\n}\n',
            # the source belongs to another list item, out of the lines around the number
            "data/catalog/e.yaml": "a:\n  - name: x\n    source: handbook\n"
                                   + "".join(f"    k{i}: v\n" for i in range(8))
                                   + "b:\n  rho: {value: 1.225, unit: kg/m^3}\n",
        })
        found = {f.where for f in self.scan() if f.rule in ("number.source", "stub")}
        self.assertEqual(found, {"hiraku/solvers/e.py:1", "hiraku/solvers/e.py:5", "hiraku/designgen/e.py:1",
                                 "data/materials/e.json:3", "data/catalog/e.yaml:13"})

    def test_unverified_data_named_in_the_readme_list(self):
        unverified = '{"E_MPa": 69000.0, "source": "handbook", "verified": false}\n'
        self.commit({"data/materials/m.json": unverified, "data/designs/d.json": unverified})
        self.assertEqual({f.where for f in self.scan() if f.rule == "unverified"},
                         {"data/materials/m.json", "data/designs/d.json"})
        self.commit({"README.md": "# x\n\n## 確かめていない値\n\n| 値 | 場所 |\n|---|---|\n"
                                  "| E | `data/materials/m.json` |\n| 寸法 | `hiraku/designgen/g.py` |\n\n## License\n"})
        self.assertEqual([f.where for f in self.scan() if f.rule == "unverified"], [])

    def test_the_list_is_read_from_its_heading_past_an_earlier_mention(self):
        # a sentence before the list uses the same word; the list under the heading still names the file
        self.commit({"data/materials/m.json": '{"source": "handbook", "verified": false}\n',
                     "README.md": "# x\n\n出典を確かめていない値は、下の表に書きます。\n\n## 使い方\n\n...\n\n"
                                  "### 確かめていない値\n\n| 値 | 場所 |\n|---|---|\n| E | `data/materials/m.json` |\n\n"
                                  "## License\n"})
        self.assertEqual([f.where for f in self.scan() if f.rule == "unverified"], [])

    def test_without_a_heading_the_first_line_with_the_word_starts_the_list(self):
        self.assertEqual(js._unchecked_list("# x\n\n確かめていない: `a.json`\n\n## y\n`b.json`\n"),
                         "確かめていない: `a.json`\n")

    def test_unverified_named_outside_the_list_still_counts(self):
        self.commit({"data/materials/m.json": '{"source": "handbook", "verified": false}\n',
                     "README.md": "# x\n\n`data/materials/m.json` holds the materials.\n\n## 確かめていない値\n\nなし\n"})
        self.assertIn("unverified", self.rules("review"))

    def test_significant_digits(self):
        self.assertEqual([js._significant(t) for t in ("205000.0", "0.15", "900.0", "1341.64", "7")], [3, 2, 1, 6, 1])

    def test_patterns_are_read_from_the_hook(self):
        self.assertEqual(len(js.private_patterns(self.hook)), 2)
        self.assertEqual(js.private_patterns(Path(self.tmp.name) / "missing"), [])
