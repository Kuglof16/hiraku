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
# (A1's くわしく and, in a static export's run list, the line in place of the run buttons)
README_RUN_URL = "https://github.com/Kuglof16/hiraku#%E5%8B%95%E3%81%8B%E3%81%97%E6%96%B9"


def read(p):
    return p.read_text(encoding="utf-8")


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

    def test_no_absolute_local_paths(self):
        drive_path = re.compile(r"\b[A-Za-z]:[\\/][A-Za-z]")
        for p in self.web_files():
            self.assertIsNone(drive_path.search(read(p)), f"{p.relative_to(WEB)} has an absolute local path")


if __name__ == "__main__":
    unittest.main()
