"""Checks on web/ that need no browser: the screen keeps up with the contract,
external scripts are pinned, and no absolute local path is in the files."""

import re
import unittest
from pathlib import Path

from hiraku import contract
from hiraku.contract.protocols import HOOK_RULES

WEB = Path(__file__).resolve().parents[2] / "web"
JS = WEB / "js"
# the one page outside the CDN the screen links to: the repository's README, section 動かし方
README_RUN_URL = "https://github.com/Kuglof16/hiraku#%E5%8B%95%E3%81%8B%E3%81%97%E6%96%B9"


def read(p):
    return p.read_text(encoding="utf-8")


def keep_newlines(s):
    return "\n" * s.count("\n")


def strip_html_comments(src):
    return re.sub(r"<!--.*?-->", lambda m: keep_newlines(m.group(0)), src, flags=re.S)


def strip_js_comments(src):
    """The source without its // and /* */ comments (newlines kept, so line numbers stay).
    Strings, template literals (with ${} inside them, nested) and regex literals are kept
    whole, so a // in a URL or a regex is not taken for a comment."""
    out, i, n = [], 0, len(src)
    stack = []  # "`" inside a template literal, "{" inside a brace of code (in a ${} or not)
    last = ""  # the last non-space character of code, to tell a regex from a division
    while i < n:
        c = src[i]
        if stack and stack[-1] == "`":
            if c == "\\":
                out.append(src[i : i + 2]); i += 2; continue
            if c == "`":
                stack.pop(); out.append(c); i += 1; last = c; continue
            if src.startswith("${", i):
                stack.append("${"); out.append("${"); i += 2; last = "{"; continue
            out.append(c); i += 1; continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(keep_newlines(src[i:j])); i = j; continue
        if c in "\"'":
            j = i + 1
            while j < n and src[j] != c and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            out.append(src[i : j + 1]); i = j + 1; last = c; continue
        if c == "/" and (last == "" or last in "(,=:[!&|?{};+-*%<>~^"):
            j, in_class = i + 1, False
            while j < n and src[j] != "\n":
                if src[j] == "\\":
                    j += 2; continue
                if src[j] == "[":
                    in_class = True
                elif src[j] == "]":
                    in_class = False
                elif src[j] == "/" and not in_class:
                    break
                j += 1
            out.append(src[i : j + 1]); i = j + 1; last = "/"; continue
        if c == "`":
            stack.append("`")
        elif c == "{":
            stack.append("{")
        elif c == "}" and stack and stack[-1] in ("{", "${"):
            stack.pop()
        out.append(c)
        if not c.isspace():
            last = c
        i += 1
    return "".join(out)


class ContractCoverageTest(unittest.TestCase):
    def test_every_event_kind_has_a_case_in_the_reducer(self):
        src = read(JS / "state.js")
        missing = [k for k in contract.EVENT_KINDS if f'case "{k}"' not in src]
        self.assertEqual(missing, [], "event kinds the screen does not handle")

    def test_every_hook_rule_has_a_label(self):
        src = read(JS / "labels.js")
        missing = [k for k in HOOK_RULES if f'key: "{k}"' not in src]
        self.assertEqual(missing, [])

    def test_every_domain_has_a_name(self):
        src = read(JS / "labels.js")
        block = src[src.index("export const DOMAIN_JA") :]
        block = block[: block.index("};")]
        missing = [d for d in contract.DOMAINS if f"  {d}:" not in block]
        self.assertEqual(missing, [])
        order = re.search(r"export const DOMAINS = \[(.*?)\]", src).group(1)
        self.assertEqual([s.strip().strip('"') for s in order.split(",")], list(contract.DOMAINS))

    def test_every_field_kind_is_drawn(self):
        src = read(JS / "fields.js")
        order = re.search(r"export const FIELD_ORDER = \[(.*?)\]", src).group(1)
        drawn = sorted(s.strip().strip('"') for s in order.split(","))
        self.assertEqual(drawn, sorted(contract.schema()["$defs"]["field"]["properties"]["kind"]["enum"]))

    def test_every_unit_label_is_a_contract_unit(self):
        src = read(JS / "labels.js")
        block = re.search(r"const UNIT_LABEL = \{(.*?)\};", src, re.S).group(1)
        keys = [k.strip().strip('"') for k in re.findall(r'("?[^,:{}]+"?)\s*:', block)]
        self.assertTrue(keys)
        self.assertEqual([k for k in keys if k not in contract.UNITS], [])


class FilesTest(unittest.TestCase):
    def web_files(self):
        # the screen's own files; the Poly Haven assets (images, an HDRI and their NOTES.md
        # with the assets' pages) are added by hand under web/assets/polyhaven/
        assets = WEB / "assets" / "polyhaven"
        return [p for p in WEB.rglob("*") if p.is_file() and not p.is_relative_to(assets)]

    def test_asset_notes_name_each_asset_file(self):
        """When the Poly Haven files are there, NOTES.md names each one and says CC0."""
        assets = WEB / "assets" / "polyhaven"
        files = [p for p in assets.glob("*/*") if p.is_file()] if assets.is_dir() else []
        if not files:
            self.skipTest("no Poly Haven file in web/assets/polyhaven/")
        notes = read(assets / "NOTES.md")
        self.assertIn("CC0", notes)
        for p in files:
            self.assertIn(p.name, notes)
            self.assertIn(f"https://polyhaven.com/a/{p.parent.name}", notes)

    def test_external_urls_are_pinned(self):
        for p in self.web_files():
            for url in re.findall(r"https?://[^\s\"')]+", read(p)):
                if "cdn.jsdelivr.net/npm/" in url:
                    self.assertRegex(url, r"/npm/[^/@]+@\d+\.\d+\.\d+/", f"{p.name}: unpinned {url}")
                elif p.name == "app.js" and url == README_RUN_URL:
                    # the run list's link to the README (a page the person opens; nothing is loaded from it)
                    self.assertIn(f'const README_RUN = "{url}"', read(p))
                else:
                    self.fail(f"{p.name}: external URL outside the pinned CDN: {url}")

    def test_three_is_one_version(self):
        versions = set()
        for p in self.web_files():
            versions |= set(re.findall(r"three@(\d+\.\d+\.\d+)", read(p)))
        self.assertEqual(len(versions), 1, versions)

    def test_relative_imports_resolve(self):
        for p in JS.glob("*.js"):
            for spec in re.findall(r'from "(\./[^"]+)"', read(p)):
                self.assertTrue((p.parent / spec).resolve().is_file(), f"{p.name} imports missing {spec}")

    def test_index_loads_the_app(self):
        html = read(WEB / "index.html")
        self.assertIn('<script type="module" src="js/app.js">', html)
        self.assertIn('type="importmap"', html)

    def test_screen_words_use_v_and_hantei(self):
        """The screen says v3 (not 版 3) and 判定 (not 照合). Comments are not shown, so they
        are left out; the intake's screen (web/js/intake/) is the intake's own."""
        intake = JS / "intake"
        banned = [re.compile(r"版\s*\d"), re.compile(r"\d\s*版"), re.compile(r"版\s*\$\{"), re.compile("照合")]
        found = []
        for p in self.web_files():
            if p.suffix not in (".js", ".html") or p.is_relative_to(intake):
                continue
            text = strip_html_comments(read(p)) if p.suffix == ".html" else strip_js_comments(read(p))
            for n, line in enumerate(text.splitlines(), 1):
                if any(b.search(line) for b in banned):
                    found.append(f"{p.relative_to(WEB)}:{n}: {line.strip()[:80]}")
        self.assertEqual(found, [])

    def test_comment_stripper_keeps_strings(self):
        src = 'const u = "https://x.org/a"; // 照合\nconst t = `a ${b ? `c//d` : "/*e*/"} f`; /* 版 1 */ const r = /"\\//g;\nx = "g"'
        out = strip_js_comments(src)
        self.assertNotIn("照合", out)
        self.assertNotIn("版 1", out)
        for kept in ('"https://x.org/a"', "`c//d`", '"/*e*/"', 'x = "g"'):
            self.assertIn(kept, out)
        self.assertEqual(out.count("\n"), src.count("\n"))  # line numbers stay
        self.assertEqual(strip_html_comments("a<!-- 照合\n-->b"), "a\nb")

    def test_no_absolute_local_paths(self):
        drive_path = re.compile(r"\b[A-Za-z]:[\\/][A-Za-z]")
        for p in self.web_files():
            self.assertIsNone(drive_path.search(read(p)), f"{p.relative_to(WEB)} has an absolute local path")


if __name__ == "__main__":
    unittest.main()
