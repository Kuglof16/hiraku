"""How a static export is read (hiraku.webserve.export_static split_record / version_site, web/js/heavy.js,
the loading screen in web/index.html and web/js/app.js):

- the record is split into a light record (what the first screen and the state need) and heavy parts
  named by their hash; put back together they are the record; the light record's counts and pace are
  what the screen computes from the heavy values;
- every reference to a JS or CSS file (the pages' tags, every relative import of every module, an
  inline module) carries ?v=, a module's version follows everything it imports, and the data files'
  versions are in the pages;
- in a headless browser: while the record is read the page is one line and a pet, no frame; a read
  that fails says why in one line (not red); the first screen reads no heavy part and little data;
  ③ reads the shown analysis' parts and draws its field.

The browser part is skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and
Playwright is installed. The static files are served by Python's http.server on 127.0.0.1:8828.
"""

from __future__ import annotations

import gzip
import json
import re
import shutil
import tempfile
import threading
import unittest
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

from hiraku.webserve import export_static as ex
from tests.web.test_browser import ENABLED, sync_playwright
from tests.web.test_static_export import RUN_ID, QuietHandler, fake_shelf, make_saved

PORT = 8828
SMALL = 100  # heavy_min_bytes for the contract sample (its fields: some 250 bytes of compact JSON)
# the most the first screen of the fixture may read from data/ (the run list's files, the light record)
FIRST_DATA_BYTES = 40_000
# a relative module specifier in a JS file or an inline module, with or without ?v=
SPEC_RE = re.compile(r"""(?:\bfrom\s*|\bimport\s*\(?\s*|\bnew\s+URL\(\s*)(["'])(\.\.?/[^"'\s]+)\1""")
TAG_RE = re.compile(r"""<(?:script|link)\b[^>]*?\b(?:src|href)=(["'])([^"':#]+\.(?:js|css)(?:\?[^"']*)?)\1""")


def big_event(seq: int, kind: str, data: dict) -> bytes:
    return json.dumps({"seq": seq, "t": float(seq), "run_id": "r", "kind": kind, "data": data}).encode() + b"\n"


class SplitTest(unittest.TestCase):
    def record(self) -> bytes:
        nodes = [[float(i), 0.0, 0.0] for i in range(2000)]
        elements = [[i, i + 1, i + 2, i + 3] for i in range(1990)] + [[0, 1, 2]] * 7 + [[0] * 8]
        series = [{"channel": "body_x", "unit": "m", "t_s": [0.5, 1.0, 3.25], "values": [0, 1, 2]},
                  {"channel": "pad", "unit": "m", "t_s": list(range(3000)), "values": list(range(3000))}]
        motion = {"kind": "node_motion", "nodes": [1, 2], "members": [[1, 2, "m"]],
                  "frames": {"t_s": [0.0, 0.1], "positions_mm": [[[0, 0, 0], [1, 0, 0]], [[0, 0, 0], [1, 1, 0]]]},
                  "pad": "x" * 20000}
        return (b'{"seq":0,"t":0.0,"run_id":"r","kind":"run.started","data":{"want":"w"}}\n'
                + b"not json\n"
                + big_event(1, "mesh.created", {"mesh_id": "m1", "nodes_mm": nodes, "elements": elements})
                + big_event(2, "analysis.finished", {"analysis_id": "a1", "status": "ok", "series": series, "results": [{"x": "y" * 20000}]})
                + big_event(3, "analysis.finished", {"analysis_id": "a2", "status": "ok", "fields": [motion]})
                + big_event(4, "analysis.finished", {"analysis_id": "a3", "status": "ok", "assembly": [{"order": 1}] * 3000})
                + big_event(5, "analysis.finished", {"analysis_id": "a4", "status": "ok", "assembly": [{"order": 1}] * 3000}))

    def test_split_and_join(self):
        data = self.record()
        light, parts = ex.split_record(data)
        lines = light.splitlines()
        self.assertEqual(lines[:2], data.splitlines()[:2])  # (kept byte for byte)
        evs = [json.loads(line) for line in lines if line.startswith(b"{")]
        mesh, a1, a2, a3, a4 = evs[1:]
        self.assertEqual(sorted(mesh["_heavy"]), ["elements", "nodes_mm"])
        self.assertNotIn("nodes_mm", mesh["data"])
        self.assertEqual(mesh["_lite"]["nodes"], 2000)
        self.assertEqual(mesh["_lite"]["kinds"], {"quad4": 1990, "tri3": 7, "hex8": 1})
        self.assertEqual(mesh["_lite"]["arrays"], {"nodes_mm": 2000, "elements": 1998})
        self.assertIn("results", a1["data"])  # (the log reads them)
        self.assertEqual(a1["_lite"]["pace"], 2999)  # (series.js motionSpan: the latest end of any channel - the earliest start)
        self.assertEqual(a2["_lite"]["pace"], ex.NODE_MOTION_PLAY_S)
        # the same assembly twice: one file, named by its hash
        self.assertEqual(a3["_heavy"], a4["_heavy"])
        self.assertEqual(len(parts), 5)
        for name in parts:
            self.assertRegex(name, r"^[0-9a-f]{16}\.json\.gz$")
        self.assertEqual(ex.join_record(light, parts)[2:], [json.loads(line) for line in data.splitlines()[2:]])

    def test_small_values_stay(self):
        light, parts = ex.split_record(self.record(), min_bytes=10**9)
        self.assertEqual((light, parts), (self.record(), {}))


class VersionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        saved = make_saved(cls.tmp)
        cls.out = cls.tmp / "site"
        cls.got = ex.export(cls.out, saved_dir=saved, shelf=fake_shelf(), heavy_min_bytes=SMALL)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_every_reference_has_a_version(self):
        """index.html's and cut.html's tags and every relative import of every JS file (and inline
        module) carry ?v=, and the version is the one the pages list for that file."""
        versions = json.loads(re.search(r'<script type="application/json" id="hiraku-versions">(.*?)</script>',
                                        (self.out / "index.html").read_text(encoding="utf-8")).group(1))
        seen = 0
        for f in sorted(self.out.rglob("*")):
            if f.suffix not in (".js", ".html") or f.parent.name == "pets":
                continue
            text = f.read_text(encoding="utf-8")
            refs = [m.group(2) for m in SPEC_RE.finditer(text)]
            if f.suffix == ".html":
                refs += [m.group(2) for m in TAG_RE.finditer(text)]
            for ref in refs:
                path, _, v = ref.partition("?v=")
                target = (f.parent / path).resolve().relative_to(self.out.resolve()).as_posix()
                if not (self.out / target).is_file():
                    continue  # (a word in a comment that looks like a path)
                seen += 1
                self.assertTrue(v, f"{f.relative_to(self.out)}: {ref}")
                self.assertEqual(v, versions[target], f"{f.relative_to(self.out)}: {ref}")
        self.assertGreater(seen, 100)
        for page in ex.PAGES:
            self.assertIn('id="hiraku-versions"', (self.out / page).read_text(encoding="utf-8"), page)
        self.assertIn("data/static.json", versions)
        self.assertIn(f"data/runs/{RUN_ID}/{ex.LIGHT_FILE}", versions)

    def test_a_change_below_changes_the_importers(self):
        """Two exports of the screen, one with labels.js changed: labels.js and every module that reaches it
        get a new version (so a browser holding the first export's app.js reads none of it again)."""
        web = self.tmp / "web2"
        shutil.copytree(ex.WEB_DIR, web)

        def versions(out):
            ex.export(out, saved_dir=self.tmp / "saved", shelf=fake_shelf(), web_dir=web, sample=False)
            text = (out / "index.html").read_text(encoding="utf-8")
            return json.loads(re.search(r'id="hiraku-versions">(.*?)</script>', text).group(1))

        before = versions(self.tmp / "e1")
        js = web / "js" / "labels.js"
        js.write_text(js.read_text(encoding="utf-8") + "\n// changed\n", encoding="utf-8")
        after = versions(self.tmp / "e2")
        for f in ("js/labels.js", "js/state.js", "js/app.js"):
            self.assertNotEqual(before[f], after[f], f)
        self.assertEqual(before["js/colormap.js"], after["js/colormap.js"])  # (reaches nothing that changed)
        state = (self.tmp / "e2" / "js" / "state.js").read_text(encoding="utf-8")
        self.assertIn('./labels.js?v=' + after["js/labels.js"] + '"', state)

    def test_the_first_read_is_small(self):
        """The light record of a run is all the first screen reads of it; its heavy parts are apart."""
        run = self.out / "data" / "runs" / RUN_ID
        self.assertFalse((run / "events.jsonl.gz").exists())
        self.assertTrue(list((run / ex.HEAVY_DIR).glob("*.json.gz")))
        light = gzip.decompress((run / ex.LIGHT_FILE).read_bytes())
        ana = [json.loads(line) for line in light.splitlines() if b'"analysis.finished"' in line]
        self.assertTrue(ana)
        for ev in ana:
            self.assertNotIn("fields", ev["data"])
            self.assertIn("fields", ev["_heavy"])


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class StaticLoadBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        saved = make_saved(cls.tmp)
        site = cls.tmp / "site"
        ex.export(site / "hiraku", saved_dir=saved, shelf=fake_shelf(), heavy_min_bytes=SMALL)
        cls.server = ThreadingHTTPServer(("127.0.0.1", PORT), partial(QuietHandler, directory=str(site)))
        cls.base = f"http://127.0.0.1:{PORT}/hiraku/"
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

    def page(self):
        page = self.browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        self.addCleanup(page.close)
        return page, errors

    BOOT = """() => ({ boot: !!document.getElementById('boot')?.checkVisibility(),
      line: document.getElementById('boot-line')?.textContent ?? null,
      stage: document.getElementById('stage').checkVisibility({ visibilityProperty: true }),
      tabs: [...document.querySelectorAll('.tab')].filter((t) => t.checkVisibility({ visibilityProperty: true })).length,
      pet: !!document.getElementById('boot-pet'),
      color: document.getElementById('boot-line') ? getComputedStyle(document.getElementById('boot-line')).color : null })"""

    def test_while_reading_one_line_and_no_frame(self):
        page, errors = self.page()
        page.route("**/light.jsonl.gz*", lambda route: None)  # never answered
        page.goto(self.base, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        got = page.evaluate(self.BOOT)
        self.assertTrue(got["boot"], got)
        self.assertTrue(got["line"].startswith("記録を読み込んでいます"), got)
        self.assertEqual((got["stage"], got["tabs"], got["pet"]), (False, 0, True), got)
        self.assertEqual(errors, [])

    def failed(self, handler):
        page, errors = self.page()
        page.route("**/light.jsonl.gz*", handler)
        page.goto(self.base, wait_until="domcontentloaded")
        page.wait_for_selector("#boot.failed")
        got = page.evaluate(self.BOOT)
        self.assertEqual((got["stage"], got["tabs"]), (False, 0), got)
        r, g, b = (int(x) for x in re.findall(r"\d+", got["color"])[:3])
        self.assertLess(r - max(g, b), 60, got["color"])  # (not red)
        self.assertEqual(errors, [])
        return got["line"], page

    def test_a_failed_read_says_why(self):
        line, page = self.failed(lambda route: route.abort())
        self.assertEqual(line, "記録を読み込めませんでした (ネットワーク)。ページを読み込み直してください")
        self.assertEqual([a.get_attribute("href") for a in page.query_selector_all("#boot .boot-links a")],
                         [f"?run={RUN_ID}", "?list=1"])
        line, _ = self.failed(lambda route: route.fulfill(status=200, body=b"\x1f\x8b not gzip", content_type="application/gzip"))
        self.assertEqual(line, "記録を読み込めませんでした (記録が壊れています)。ページを読み込み直してください")
        line, _ = self.failed(lambda route: route.fulfill(status=404, body=b""))
        self.assertTrue(line.startswith("記録を読み込めませんでした (見つかりません)"), line)

    def test_first_screen_reads_no_heavy_part_and_little_data(self):
        page, errors = self.page()
        page.goto(self.base)
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
        page.evaluate("window.hiraku.ready")
        page.wait_for_function("document.getElementById('replaybar')?.innerText.includes('この記録を再生する')")
        self.assertFalse(page.evaluate("!!document.getElementById('boot')"))
        res = page.evaluate("""() => performance.getEntriesByType('resource').map((r) => [new URL(r.name).pathname, r.encodedBodySize])""")
        self.assertEqual([p for p, _ in res if "/heavy/" in p], [])
        data = sum(n for p, n in res if "/hiraku/data/" in p)
        self.assertLess(data, FIRST_DATA_BYTES, res)
        # ③: the shown analysis' fields are read and drawn
        page.click('.tab[data-tab="bench"]')
        page.wait_for_function("performance.getEntriesByType('resource').some((r) => r.name.includes('/heavy/'))", timeout=20000)
        page.wait_for_function("(document.getElementById('legend')?.innerText ?? '').length > 0", timeout=20000)
        page.wait_for_function("!document.getElementById('heavy-note') || document.getElementById('heavy-note').hidden", timeout=20000)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
