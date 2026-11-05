"""tests/web/pages.py: the three.js cache every test page goes through, and the map of which
web/js functions a page ran (HIRAKU_WEB_MAP).

The first class needs no browser. The second is skipped unless HIRAKU_WEB_BROWSER=1
(python -m tests.web.browser sets it) and Playwright is installed, as test_browser; one of its
tests fetches three.module.js from the CDN once.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from tests.web import pages
from tests.web.pages import cache_path, coverage_map, repo_path, sync_playwright

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None
TESTS_DIR = Path(__file__).resolve().parent
THREE = "https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js"


class PagesTest(unittest.TestCase):
    def test_cache_path_keeps_the_version_and_refuses_other_urls(self):
        with mock.patch.dict(os.environ, {"HIRAKU_THREE_CACHE": "C:/x/cache"}):
            self.assertEqual(cache_path(THREE), Path("C:/x/cache/three@0.170.0/build/three.module.js"))
            self.assertEqual(cache_path(THREE + "?v=1"), Path("C:/x/cache/three@0.170.0/build/three.module.js"))
            self.assertEqual(cache_path("https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/lines/Line2.js"),
                             Path("C:/x/cache/three@0.170.0/examples/jsm/lines/Line2.js"))
            for url in ("https://cdn.jsdelivr.net/npm/three@0.170.0/../../etc/x.js", "https://cdn.jsdelivr.net/npm/other@1/x.js",
                        "https://example.com/npm/three@0.170.0/build/three.module.js"):
                self.assertIsNone(cache_path(url), url)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HIRAKU_THREE_CACHE", None)
            self.assertEqual(cache_path(THREE).parts[-4:], ("hiraku-three-cache", "three@0.170.0", "build", "three.module.js"))

    def test_store_from_many_threads_leaves_one_whole_file(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = tmp / "three@0.170.0" / "build" / "three.module.js"
        body = b"x" * 2_000_000
        seen = []

        def put():
            pages._store(path, body)
            seen.append(pages._read(path) == body)  # the route's read, while others replace it

        threads = [threading.Thread(target=put) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(path.read_bytes(), body)
        self.assertEqual(seen, [True] * 8)
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), ["three.module.js"])  # no .part left

    def test_coverage_map_keeps_ran_outer_ranges_of_the_screens_scripts(self):
        result = [
            {"url": "http://127.0.0.1:5000/js/app.js", "functions": [
                {"functionName": "", "ranges": [{"startOffset": 0, "endOffset": 900, "count": 1}]},
                {"functionName": "draw", "ranges": [{"startOffset": 10, "endOffset": 50, "count": 3}, {"startOffset": 20, "endOffset": 30, "count": 0}]},
                {"functionName": "never", "ranges": [{"startOffset": 60, "endOffset": 80, "count": 0}]}]},
            {"url": THREE, "functions": [{"functionName": "x", "ranges": [{"startOffset": 0, "endOffset": 5, "count": 1}]}]},
            {"url": "http://127.0.0.1:5000/css/app.css", "functions": []},
            {"url": "http://127.0.0.1:5000/js/app.js", "functions": [  # the same script again (a second document)
                {"functionName": "draw", "ranges": [{"startOffset": 10, "endOffset": 50, "count": 1}]}]},
        ]
        self.assertEqual(coverage_map(result), {"web/js/app.js": [[0, 900, ""], [10, 50, "draw"]]})
        self.assertEqual(repo_path("http://127.0.0.1:1/js/intake/a.js?x=1"), "web/js/intake/a.js")
        self.assertIsNone(repo_path("http://127.0.0.1:1/index.html"))

    def test_every_test_file_takes_playwright_from_pages(self):
        """A test that imports playwright.sync_api itself would skip the cache and the map."""
        direct = []
        for p in sorted(TESTS_DIR.glob("*.py")):
            if p.name == "pages.py":
                continue
            for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("playwright"):
                    direct.append(f"{p.name}:{node.lineno}")
                if isinstance(node, ast.Import) and any(a.name.startswith("playwright") for a in node.names):
                    direct.append(f"{p.name}:{node.lineno}")
        self.assertEqual(direct, [])


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class PagesBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from hiraku.webserve.server import make_server

        cls.tmp = Path(tempfile.mkdtemp())
        cls.server = make_server("127.0.0.1", 0, cls.tmp / "runs", quiet=True, saved_dir=None, launcher=False)
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
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def import_three(self, page):
        page.goto(f"{self.base}/no-such-page")  # an http origin, as the screen's
        return page.evaluate(f"import({json.dumps(THREE)}).then((m) => String(m.REVISION))")

    def test_three_comes_from_the_cache_folder(self):
        cache = self.tmp / "cache-planted"
        planted = cache / "three@0.170.0" / "build" / "three.module.js"
        planted.parent.mkdir(parents=True)
        planted.write_text('export const REVISION = "planted";\n', encoding="utf-8")
        before = dict(pages.STATS)
        with mock.patch.dict(os.environ, {"HIRAKU_THREE_CACHE": str(cache)}):
            for opener in (self.browser.new_page, lambda: self.browser.new_context().new_page()):
                page = opener()
                got = self.import_three(page)
                page.context.close()
                self.assertEqual(got, "planted")
        self.assertEqual(pages.STATS["cache"] - before["cache"], 2)
        self.assertEqual(pages.STATS["fetched"], before["fetched"])

    def test_a_missing_file_is_fetched_once_and_kept(self):
        cache = self.tmp / "cache-empty"
        with mock.patch.dict(os.environ, {"HIRAKU_THREE_CACHE": str(cache)}):
            page = self.browser.new_page()
            first = self.import_three(page)
            page.close()
            kept = cache / "three@0.170.0" / "build" / "three.module.js"
            self.assertTrue(kept.is_file())
            before = dict(pages.STATS)
            page = self.browser.new_page()
            second = self.import_three(page)
            page.close()
        self.assertEqual(first, "170")
        self.assertEqual(second, "170")
        self.assertEqual(pages.STATS["cache"] - before["cache"], 1)
        self.assertEqual(pages.STATS["fetched"], before["fetched"])
        self.assertEqual([p.name for p in kept.parent.iterdir()], ["three.module.js"])

    def test_the_map_has_a_file_per_page_with_app_js(self):
        out = self.tmp / "map"
        with mock.patch.dict(os.environ, {"HIRAKU_WEB_MAP": str(out)}):
            page = self.browser.new_page()
            page.goto(f"{self.base}/?run=sample&pace=0&t=1e6")
            page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
            page.evaluate("window.hiraku.ready")
            page.close()
            ctx = self.browser.new_context()
            left_open = ctx.new_page()  # closed with its context
            left_open.goto(f"{self.base}/?list=1")
            ctx.close()
            own = self.browser.new_page()  # closed by closing its own context
            own.goto(f"{self.base}/?list=1")
            own.context.close()
        files = sorted(out.glob("*.json"), key=lambda p: int(p.stem.split("-")[1]))
        self.assertEqual(len(files), 3)
        self.assertTrue(all(p.stem.split("-")[0] == str(os.getpid()) for p in files))
        doc = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(doc["test"], self.id())
        self.assertIn("run=sample", doc["url"])
        app = doc["scripts"]["web/js/app.js"]
        self.assertTrue(app)
        self.assertTrue(all(len(r) == 3 and r[0] < r[1] for r in app))
        self.assertFalse([k for k in doc["scripts"] if not k.startswith("web/js/")])
        for f in files[1:]:
            self.assertIn("web/js/app.js", json.loads(f.read_text(encoding="utf-8"))["scripts"])

    def test_without_the_variable_nothing_is_written(self):
        out = self.tmp / "map-off"
        env = {k: v for k, v in os.environ.items() if k != "HIRAKU_WEB_MAP"}
        with mock.patch.dict(os.environ, env, clear=True):
            page = self.browser.new_page()
            self.assertNotIn("close", vars(page))  # no coverage session, page.close is Playwright's own
            page.goto(f"{self.base}/?list=1")
            page.close()
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
