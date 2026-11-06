"""No tracked file keeps a merge's conflict markers (a merge committed before every hunk was resolved).

Git's markers are a line starting with seven '<' or '>' and a space, or a line of exactly seven '='.
Markdown files are left out: a line of seven '=' is a heading underline there.
"""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MARKER = re.compile(r"^(?:<{7}|>{7}) |^={7}$")


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [p for p in out.decode("utf-8").split("\0") if p and not p.lower().endswith(".md")]


def markers_in(text: str) -> list[int]:
    return [n for n, line in enumerate(text.splitlines(), start=1) if MARKER.match(line)]


class NoConflictMarkers(unittest.TestCase):
    def test_no_tracked_file_keeps_conflict_markers(self):
        found = {}
        for rel in tracked_files():
            p = ROOT / rel
            try:
                data = p.read_bytes()
            except OSError:
                continue
            if b"\0" in data[:8192]:  # a binary file
                continue
            lines = markers_in(data.decode("utf-8", errors="replace"))
            if lines:
                found[rel] = lines[:5]
        self.assertEqual(found, {}, "conflict markers left from a merge")

    def test_the_pattern(self):
        self.assertEqual(markers_in("a\n<<<<<<< HEAD\nb\n=======\nc\n>>>>>>> branch\n"), [2, 4, 6])
        self.assertEqual(markers_in("x = '======='\n<<<<<<<< eight\n"), [])


if __name__ == "__main__":
    unittest.main()
