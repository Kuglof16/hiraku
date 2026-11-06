"""The static export (python -m hiraku.webserve.export_static): a saved run made from the contract's
sample (with a tool the run wrote and an N10 folder), written out and

- compared with what hiraku.webserve answers for the same saved runs (every GET the screen makes);
- read for what must not be published (a private word, a home folder, a drive path planted in a copy);
- in a headless browser, served by Python's http.server under /hiraku/: the run opens and replays
  (3D, ④'s tile replay, the shelf, the formulas' titles), the run list has no button that would start
  or send something but one line to the README, and no request is a POST, none fails, no console error.

The browser part is skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and
Playwright is installed. The servers listen on 127.0.0.1:8826 (http.server) and 8827 (hiraku.webserve).
"""

from __future__ import annotations

import gzip
import json
import re
import shutil
import tempfile
import threading
import time
import unittest
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from hiraku.webserve import export_static as ex
from hiraku.webserve.server import SAMPLE_EVENTS, make_server
from hiraku.webserve.shelf import Shelf
from tests.web.test_browser import ENABLED, sync_playwright
from tests.web.test_shelf_browser import ARCHETYPES, FORMULAS, SHELF_TOOLS

REPO = Path(__file__).resolve().parents[2]
RUN_ID = "20261102-203000-static-fixture"
STATIC_PORT, SERVER_PORT = 8826, 8827
CODE_1 = "def run():\n    return 1\n"
CHECK_1 = json.dumps({"tool": "rollbar", "checks": []})
# the README's section 動かし方 (app.js README_RUN): the static run list's line points there
README_START = "https://github.com/Kuglof16/hiraku#%E5%8B%95%E3%81%8B%E3%81%97%E6%96%B9"


def fake_shelf() -> Shelf:
    return Shelf(lambda: {"tools": SHELF_TOOLS, "formulas": FORMULAS, "archetypes": ARCHETYPES})


def make_saved(root: Path, extra_line: str | None = None) -> Path:
    """saved/<RUN_ID>/ (the sample's events, two tool files and one other file in tools/) and saved/n10/."""
    saved = root / "saved"
    run = saved / RUN_ID
    (run / "tools").mkdir(parents=True)
    data = SAMPLE_EVENTS.read_bytes()
    if extra_line is not None:
        data += extra_line.encode("utf-8") + b"\n"
    (run / "events.jsonl").write_bytes(data)
    (run / "tools" / "rollbar-1.py").write_text(CODE_1, encoding="utf-8")
    (run / "tools" / "rollbar-1.check.json").write_text(CHECK_1, encoding="utf-8")
    (run / "tools" / "notes.txt").write_text("not a tool file\n", encoding="utf-8")
    n10 = saved / "n10"
    n10.mkdir()
    (n10 / "prompt.txt").write_text("段差の問い (形の例)\n", encoding="utf-8")
    (n10 / "answer_1.json").write_text(json.dumps({"result": "答え <scratchpad> (形の例)"}, ensure_ascii=False), encoding="utf-8")
    return saved


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read()


class ExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.saved = make_saved(cls.tmp)
        cls.out = cls.tmp / "site" / "hiraku"
        cls.got = ex.export(cls.out, saved_dir=cls.saved, shelf=fake_shelf())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_pages_are_marked_static(self):
        for page in ex.PAGES:
            self.assertIn(ex.STATIC_META, (self.out / page).read_text(encoding="utf-8"), page)
            self.assertNotIn(ex.STATIC_META, (REPO / "web" / page).read_text(encoding="utf-8"), page)
        self.assertTrue((self.out / "js" / "source.js").is_file())

    def test_same_answers_as_the_server(self):
        server = make_server("127.0.0.1", SERVER_PORT, self.tmp / "runs-none", quiet=True, saved_dir=self.saved,
                             launcher=False, shelf=fake_shelf())
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{SERVER_PORT}"
            data = self.out / "data"
            pairs = {"/api/runs": data / "runs.json", "/api/launch": data / "launch.json",
                     "/api/assets": data / "assets.json", "/api/modules": data / "modules.json",
                     "/api/formulas": data / "formulas.json", f"/api/runs/{RUN_ID}/tools": data / "runs" / RUN_ID / "tools.json",
                     "/layout.json": self.out / "layout.json"}
            for route, f in pairs.items():
                self.assertEqual(json.loads(get(base + route)), json.loads(f.read_text(encoding="utf-8")), route)
            for _ in range(100):  # the server's shelf is computed in the background
                shelf = json.loads(get(base + "/api/shelf"))
                if shelf.get("ready"):
                    break
                time.sleep(0.05)
            self.assertEqual(shelf, json.loads((data / "shelf.json").read_text(encoding="utf-8")))
            for name in ("rollbar-1.py", "rollbar-1.check.json"):
                self.assertEqual(get(f"{base}/api/runs/{RUN_ID}/tools/{name}"), (data / "runs" / RUN_ID / "tools" / name).read_bytes())
            self.assertEqual(get(base + "/pets/pet3d.js"), (self.out / "pets" / "pet3d.js").read_bytes())
            for rid in (RUN_ID, "sample"):
                read, offset = b"", 0
                while True:
                    with urllib.request.urlopen(f"{base}/api/runs/{rid}/events?offset={offset}&format=ndjson") as r:
                        read += r.read()
                        nxt, size = int(r.headers["X-Hiraku-Next-Offset"]), int(r.headers["X-Hiraku-Size"])
                    if nxt >= size or nxt == offset:
                        break
                    offset = nxt
                self.assertEqual(gzip.decompress((data / "runs" / rid / "events.jsonl.gz").read_bytes()), read, rid)
        finally:
            server.shutdown()
            server.server_close()

    def test_only_tool_files_and_the_runs_taken(self):
        tools = sorted(p.name for p in (self.out / "data" / "runs" / RUN_ID / "tools").iterdir())
        self.assertEqual(tools, ["rollbar-1.check.json", "rollbar-1.py"])
        runs = json.loads((self.out / "data" / "runs.json").read_text(encoding="utf-8"))
        self.assertEqual([r["run_id"] for r in runs], [RUN_ID, "sample"])
        self.assertEqual([r["run_id"] for r in runs if r["default"]], [RUN_ID])
        self.assertEqual(json.loads((self.out / "data" / "launch.json").read_text(encoding="utf-8")), {"available": False})

    def test_n10_page(self):
        page = (self.out / "n10" / "index.html").read_text(encoding="utf-8")
        self.assertIn("&lt;scratchpad&gt;", page)
        self.assertNotIn("<scratchpad>", page)
        extras = json.loads((self.out / "data" / "static.json").read_text(encoding="utf-8"))["extras"]
        self.assertEqual([x["href"] for x in extras], ["n10/index.html"])

    def test_refuses_a_folder_that_is_not_an_export(self):
        other = self.tmp / "other"
        other.mkdir()
        (other / "keep.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(SystemExit):
            ex.export(other, saved_dir=self.saved, shelf=fake_shelf())
        self.assertTrue((other / "keep.txt").is_file())

    def test_scan_clean_and_planted(self):
        hook = self.tmp / "hook"
        hook.write_text("WORDS='zqprivatezq'\nPATHS='zqpathzq'\n", encoding="utf-8")
        findings, checked = ex.scan(self.out, hook)
        self.assertTrue(checked)
        self.assertEqual([str(f) for f in findings if f.level == "error"], [])
        root = self.tmp / "planted"
        root.mkdir()
        home = "C:" + "/Users/someone/x"  # (split so this file holds no such path)
        line = json.dumps({"seq": 999, "t": 99.0, "ts": "", "run_id": RUN_ID, "kind": "agent.said", "agent": "lead",
                           "text": f"zqprivatezq {home} D:\\\\work\\\\a"})
        saved = make_saved(root, extra_line=line)
        out = root / "site"
        ex.export(out, saved_dir=saved, shelf=fake_shelf(), sample=False)
        rules = sorted({f.rule for f in ex.scan(out, hook)[0] if f.level == "error"})
        self.assertEqual(rules, ["home.path", "local.path", "private.word"])

    def test_readme_link_is_a_heading(self):
        app = (REPO / "web" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn(f'const README_RUN = "{README_START}"', app)
        heading = unquote(README_START.split("#", 1)[1])
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertRegex(readme, rf"(?m)^#+ {re.escape(heading)}$")


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class StaticBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        saved = make_saved(cls.tmp)
        site = cls.tmp / "site"
        ex.export(site / "hiraku", saved_dir=saved, shelf=fake_shelf())
        cls.formulas = {f["formula_id"]: f["title"] for f in json.loads((site / "hiraku" / "data" / "formulas.json").read_text(encoding="utf-8"))["formulas"]}
        cls.server = ThreadingHTTPServer(("127.0.0.1", STATIC_PORT), partial(QuietHandler, directory=str(site)))
        cls.base = f"http://127.0.0.1:{STATIC_PORT}/hiraku/"
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

    def open(self, query, wait_ready=True):
        page = self.browser.new_page(viewport={"width": 1440, "height": 900})
        seen = {"posts": [], "failed": [], "errors": []}
        page.on("request", lambda r: r.method != "GET" and seen["posts"].append(f"{r.method} {r.url}"))
        page.on("response", lambda r: r.status >= 400 and seen["failed"].append(f"{r.status} {r.url}"))
        page.on("console", lambda m: m.type == "error" and seen["errors"].append(m.text))
        page.on("pageerror", lambda e: seen["errors"].append(str(e)))
        self.addCleanup(page.close)
        page.goto(self.base + query)
        if wait_ready:
            page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
            page.evaluate("window.hiraku.ready")
        page.evaluate("() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
        return page, seen

    def clean(self, page, seen):
        page.wait_for_load_state("networkidle")
        self.assertEqual(seen, {"posts": [], "failed": [], "errors": []})

    def test_a_run_replays(self):
        page, seen = self.open(f"?run={RUN_ID}&pace=0&t=1e6")
        got = page.evaluate("""() => ({
          events: window.hiraku.player.events.length,
          canvas: !!document.querySelector('#viewport canvas'),
          mode: document.getElementById('mode').innerText,
          stop: !!document.querySelector('[data-stop]'),
        })""")
        self.assertEqual(got["events"], len(SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()))
        self.assertTrue(got["canvas"])
        self.assertIn("再生", got["mode"])
        self.assertFalse(got["stop"])
        self.assertEqual(page.get_attribute("#mode a.launch-link", "href"), "?list=1")
        # ④: the strength tile replays its analyses in the tab; the prediction names its formula
        page.click('.tab[data-tab="grid"]')
        page.click('.tile[data-domain="strength"]')
        page.wait_for_function("!document.getElementById('domain-head').hidden")
        page.evaluate("window.hiraku.seek(1e6)")
        title = self.formulas["frame_static.rail_bending"]
        page.wait_for_function("(t) => document.getElementById('card-pred').innerText.includes(t)", arg=title, timeout=10000)
        # A4: the shelf from data/shelf.json, the run's tool code from data/runs/<id>/tools/
        page.click('.tab[data-tab="A4"]')
        page.wait_for_function(f"document.querySelectorAll('#view-shelf .sh-tool').length >= {len(SHELF_TOOLS)}", timeout=10000)
        self.assertIn("レールの曲げ", page.inner_text("#view-shelf"))
        self.clean(page, seen)

    def test_the_run_list_only_replays(self):
        for query in ("", "?new=1", "?list=1&dev=1"):
            with self.subTest(query=query):
                page, seen = self.open(query, wait_ready=False)
                page.wait_for_selector("#runlist .local-run a", timeout=20000)
                got = page.evaluate("""() => ({
                  buttons: [...document.querySelectorAll('#runlist button')].map((b) => b.textContent),
                  starts: document.querySelectorAll('#runlist .new-request, #runlist .demo-a1, #draftbar').length,
                  readme: document.querySelector('#runlist .local-run a').href,
                  line: document.querySelector('#runlist .local-run').innerText,
                  extras: [...document.querySelectorAll('#runlist .extra a')].map((a) => a.getAttribute('href')),
                  runs: [...document.querySelectorAll('#runlist a')].filter((a) => a.getAttribute('href')?.startsWith('?run=')).length,
                })""")
                self.assertEqual((got["buttons"], got["starts"]), ([], 0))
                self.assertEqual(got["readme"], README_START)
                self.assertTrue(got["line"].startswith("手元で動かすには"), got["line"])
                self.assertEqual(got["extras"], ["n10/index.html"])
                self.assertGreaterEqual(got["runs"], 1)
                self.clean(page, seen)

    def test_n10_page_opens(self):
        page, seen = self.open("n10/index.html", wait_ready=False)
        self.assertIn("ツールなしの Claude", page.inner_text("h1"))
        self.clean(page, seen)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 (stdlib name)
        pass


if __name__ == "__main__":
    unittest.main()
