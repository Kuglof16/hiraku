"""The run list (?list=1, and ?list=1&dev=1): 「＋ 新しく依頼する」 comes first; on ?dev=1 a demo
row shows its title, and its cut tags, file, cost line and what it shows (tab names, hooks)
are inside its くわしく, after the title.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed, as test_browser.
"""

import os
import threading
import unittest

from hiraku.webserve.server import make_server

try:
    from playwright.sync_api import sync_playwright
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
        cls.server = make_server("127.0.0.1", 0, "runs-none", quiet=True, saved_dir=None)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()

    def probe(self, query):
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.base}/?{query}")
        page.wait_for_selector("#launch .demo-runs")
        got = page.evaluate(PROBE)
        page.close()
        self.assertEqual(errors, [])
        return got

    def test_a_new_request_first_for_people(self):
        got = self.probe("list=1")
        self.assertEqual(got["first"], "fresh-top")
        self.assertEqual(got["firstLink"], "＋ 新しく依頼する")
        self.assertEqual(got["freshCount"], 1)

    def test_dev_rows_title_first_tags_under_more(self):
        got = self.probe("list=1&dev=1")
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
