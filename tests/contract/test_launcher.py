"""HIRAKU.cmd, the double-click launcher: cmd.exe must be able to read it."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LAUNCHER = Path(__file__).resolve().parents[2] / "HIRAKU.cmd"


class LauncherTest(unittest.TestCase):
    def test_no_bare_parenthesis_inside_a_block(self):
        # cmd.exe ends an if ( ... ) block at the first ")" it meets, even inside an echo line.
        depth = 0
        for n, line in enumerate(LAUNCHER.read_text(encoding="ascii").splitlines(), 1):
            text = line.strip()
            if depth and text.lower().startswith("echo"):
                self.assertIsNone(re.search(r"(?<!\^)[()]", text[4:]), f"line {n}: escape ( and ) as ^( ^) inside a block")
            depth += text.count("(") - text.count("^(") - text.count(")") + text.count("^)")

    @unittest.skipUnless(sys.platform == "win32", "cmd.exe is Windows only")
    def test_without_a_venv_it_says_so_and_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "HIRAKU.cmd"
            shutil.copyfile(LAUNCHER, copy)
            out = subprocess.run(["cmd", "/c", str(copy)], capture_output=True, stdin=subprocess.DEVNULL,
                                 timeout=30, cwd=tmp, env={**os.environ})
        text = out.stdout.decode("mbcs", errors="replace")
        self.assertIn("was not found", text)
        self.assertEqual(out.returncode, 1)


if __name__ == "__main__":
    unittest.main()
