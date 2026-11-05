"""The run list (?list=1, and ?list=1&dev=1): 「＋ 新しく依頼する」 comes first; on ?dev=1 a demo
row shows its title, and its cut tags, file, cost line and what it shows (tab names, hooks)
are inside its くわしく, after the title.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed, as test_browser.
"""

import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import make_server

try:
    from tests.web.pages import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None

PROBE = """() => {
  const box = document.getElementById("launch");
  const rows = [...box.querySelectorAll(".lreq.demo")];
  return {
    first: box.firstElementChild?.className ?? "",
    firstLink: box.firstElementChild?.querySelector("a.new-request")?.textContent ?? "",
    freshCount: box.querySelectorAll("a.new-request").length,
    rows: rows.length,
    // what is seen without opening くわしく (outside the closed details)
    outside: rows.map((r) => { const c = r.cloneNode(true); c.querySelectorAll("details").forEach((d) => d.remove()); return c.textContent; }),
    tagsInside: rows.every((r) => [...r.querySelectorAll(".cut, .rid, .cost, .shows")].every((e) => e.closest("details.more"))),
    tagsAny: rows.some((r) => r.querySelector(".cut")),
  };
}"""


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class RunListOrderBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from hiraku.webserve.launch import Launcher

        cls.tmp = Path(tempfile.mkdtemp())  # runs/ is not made: the servers have no runs of their own
        # the list's launcher with and without an API key, whatever the window running the test has
        # (the key changes the list's buttons; nothing is started here)
        cls.servers, cls.bases = [], {}
        for name, env in (("no key", {}), ("key", {"ANTHROPIC_API_KEY": "test-value-not-a-real-key"})):
            lau = Launcher(cls.tmp / "runs", env=env)
            server = make_server("127.0.0.1", 0, cls.tmp / "runs", quiet=True, saved_dir=None, launcher=lau)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            cls.servers.append(server)
            cls.bases[name] = f"http://127.0.0.1:{server.server_address[1]}"
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        for server in cls.servers:
            server.shutdown()
            server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def probe(self, query, key):
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.bases[key]}/?{query}", wait_until="domcontentloaded")
        page.wait_for_selector("#launch .demo-runs")
        got = page.evaluate(PROBE)
        page.close()
        self.assertEqual(errors, [])
        return got

    def test_a_new_request_first_for_people(self):
        for key in self.bases:
            with self.subTest(key):
                got = self.probe("list=1", key)
                self.assertEqual(got["first"], "fresh-top")
                self.assertEqual(got["firstLink"], "＋ 新しく依頼する")
                self.assertEqual(got["freshCount"], 1)

    def test_dev_rows_title_first_tags_under_more(self):
        for key in self.bases:
            with self.subTest(key):
                got = self.probe("list=1&dev=1", key)
                self.assertEqual(got["first"], "fresh-top")
                self.assertEqual(got["firstLink"], "＋ 新しく依頼する")
                self.assertGreater(got["rows"], 0)
                self.assertTrue(got["tagsAny"])  # the demo runs have cut tags (data/catalog/demo_runs.yaml)
                self.assertTrue(got["tagsInside"])
                for text in got["outside"]:
                    for w in (".yaml", "(A1)", "HK1", "N09", "N13"):
                        self.assertNotIn(w, text)


if __name__ == "__main__":
    unittest.main()
