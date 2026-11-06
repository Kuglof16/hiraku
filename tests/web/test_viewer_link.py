"""The viewer link's server side (hiraku/webserve/viewer_link.py): GET /viewer/..., GET /api/viewer and
GET /api/runs/<run>/viewer/<analysis>, provisional (the field's sample) and with the analysis
department's entry put in its place; the static site's files; no POST; the screen's tool table
(web/js/viewer_link.js) the same as the server's."""

from __future__ import annotations

import http.client
import json
import re
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve import viewer_link
from hiraku.webserve.server import REPO_DIR, SAVED_DIR, make_server
from tests.web.viewer_fixture import SCRIPTED_RUN

SAMPLE = {"field": "assembly", "title": "組み立て: 見本", "source": {"run": "x"}, "parts": [1, 2]}


def analyses_of(events: Path) -> dict[str, str]:
    """{analysis_id: tool} of a run's analysis.finished events."""
    out = {}
    with open(events, encoding="utf-8") as f:
        for line in f:
            if '"analysis.finished"' in line:
                d = json.loads(line)["data"]
                out[d["analysis_id"]] = d["tool"]
    return out


class ViewerLinkServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.data = cls.tmp / "data"
        cls.data.mkdir()
        (cls.data / "assembly.js").write_text(
            "window.VIEW_DATA = window.VIEW_DATA || {};\n"
            f'window.VIEW_DATA["assembly"] = {json.dumps(SAMPLE, ensure_ascii=True)};\n', encoding="ascii")
        cls.anas = analyses_of(SAVED_DIR / SCRIPTED_RUN / "events.jsonl")
        cls.calls = []
        # a run that keeps its records (a v001 folder): the scripted run's events with an empty version folder
        cls.kept = "20261106-000001-kept-run"
        (cls.tmp / "runs" / cls.kept / "v001").mkdir(parents=True)
        shutil.copy(SAVED_DIR / SCRIPTED_RUN / "events.jsonl", cls.tmp / "runs" / cls.kept / "events.jsonl")
        # the analysis department's own fixture run (tests/viewer/fixtures/fatigue) with an empty events.jsonl
        cls.fx = "20261106-000002-fixture-fatigue"
        fx = REPO_DIR / "tests" / "viewer" / "fixtures" / "fatigue"
        if fx.is_dir():
            shutil.copytree(fx, cls.tmp / "runs" / cls.fx)
            (cls.tmp / "runs" / cls.fx / "events.jsonl").write_text("", encoding="utf-8")

        def build(run_dir, analysis_id):
            cls.calls.append((Path(run_dir), analysis_id))
            if analysis_id == "ana-none":
                return {"field": None, "reason": "この解析は絵にしない"}
            return {"field": "crash", "data": {"t": [0, 1]}, "title": "衝突: 本物", "source": "この解析から作った絵"}

        cls.servers = {}
        for key, kw in {"sample": {"viewer_build": False}, "built": {"viewer_build": build}, "entry": {},
                        "noviewer": {"viewer_build": False, "viewer_dir": cls.tmp / "no-viewer"}}.items():
            kw.setdefault("viewer_data_dir", cls.data)
            srv = make_server("127.0.0.1", 0, cls.tmp / "runs", quiet=True, launcher=False, **kw)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            cls.servers[key] = srv

    @classmethod
    def tearDownClass(cls):
        for srv in cls.servers.values():
            srv.shutdown()
            srv.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def get(self, key, path, method="GET"):
        conn = http.client.HTTPConnection("127.0.0.1", self.servers[key].server_address[1], timeout=30)
        try:
            conn.request(method, path, body=b"{}" if method == "POST" else None,
                         headers={"Content-Type": "application/json"} if method == "POST" else {})
            r = conn.getresponse()
            body = r.read()
            return r.status, r.getheader("Content-Type"), body
        finally:
            conn.close()

    def json(self, key, path):
        status, ctype, body = self.get(key, path)
        self.assertEqual(status, 200, body[:200])
        self.assertTrue(ctype.startswith("application/json"))
        return json.loads(body)

    def first(self, tool):
        return next(a for a, t in self.anas.items() if t == tool)

    def test_provisional_answer_is_the_fields_sample_as_it_is(self):
        aid = self.first("assembly")
        got = self.json("sample", f"/api/runs/{SCRIPTED_RUN}/viewer/{aid}")
        self.assertEqual(got["field"], "assembly")
        self.assertEqual(got["data"], SAMPLE)
        self.assertEqual(got["title"], SAMPLE["title"])
        self.assertEqual(got["source"], "見本 (この解析から作った絵ではない)")
        self.assertTrue(got["provisional"])

    def test_a_tool_without_a_field_a_missing_sample_and_an_unknown_analysis_give_a_reason(self):
        no_field = self.json("sample", f"/api/runs/{SCRIPTED_RUN}/viewer/{self.first('load_path')}")
        self.assertEqual(no_field, {"field": None, "reason": viewer_link.NO_FIELD})
        no_sample = self.json("sample", f"/api/runs/{SCRIPTED_RUN}/viewer/{self.first('modal')}")
        self.assertEqual(no_sample, {"field": None, "reason": viewer_link.NO_SAMPLE})
        unknown = self.json("sample", f"/api/runs/{SCRIPTED_RUN}/viewer/ana-9999999")
        self.assertEqual(unknown, {"field": None, "reason": viewer_link.NO_ANALYSIS})
        for r in (no_field, no_sample, unknown):
            self.assertNotIn("\n", r["reason"])
        status, _, _ = self.get("sample", "/api/runs/no-such-run/viewer/ana-0001")
        self.assertIn(status, (400, 404))

    def test_the_analysis_departments_entry_is_used_when_there(self):
        aid = self.first("crash")
        got = self.json("built", f"/api/runs/{self.kept}/viewer/{aid}")
        self.assertEqual(got, {"field": "crash", "data": {"t": [0, 1]}, "title": "衝突: 本物", "source": "この解析から作った絵"})
        self.assertEqual(self.calls[-1][1], aid)
        self.assertEqual(self.calls[-1][0].resolve(), (self.tmp / "runs" / self.kept).resolve())
        none = self.json("built", f"/api/runs/{self.kept}/viewer/ana-none")
        self.assertEqual(none, {"field": None, "reason": "この解析は絵にしない"})
        got = self.json("built", f"/api/viewer?run={self.kept}")
        self.assertFalse(got["provisional"])
        self.assertEqual(got["runs"], {self.kept: {"provisional": False}})

    def test_a_run_without_records_keeps_the_provisional_answer_with_the_entry(self):
        # a saved run of showcase/ keeps only events.jsonl: the entry has nothing to read
        n = len(self.calls)
        got = self.json("built", f"/api/runs/{SCRIPTED_RUN}/viewer/{self.first('assembly')}")
        self.assertEqual(len(self.calls), n)
        self.assertEqual(got["data"], SAMPLE)
        self.assertEqual(got["source"], viewer_link.SAMPLE_SOURCE)
        info = self.json("built", f"/api/viewer?run={SCRIPTED_RUN}")
        self.assertEqual(info["runs"], {SCRIPTED_RUN: {"provisional": True}})
        self.assertIn("nvh", info["missing"])

    @unittest.skipUnless(viewer_link.entry() is not None, "viewer.src.build is not here")
    def test_the_real_entry_on_the_analysis_departments_fixture(self):
        got = self.json("entry", f"/api/runs/{self.fx}/viewer/ana-0001")
        self.assertEqual(got["field"], "fatigue")
        self.assertTrue(got["data"])
        self.assertNotEqual(got.get("source"), viewer_link.SAMPLE_SOURCE)
        none = self.json("entry", f"/api/runs/{self.fx}/viewer/ana-9999")
        self.assertIsNone(none["field"])
        self.assertTrue(none["reason"])
        self.assertNotIn("\n", none["reason"])
        info = self.json("entry", f"/api/viewer?run={self.fx}")
        self.assertFalse(info["provisional"])
        self.assertEqual(info["runs"], {self.fx: {"provisional": False}})
        tables = viewer_link.entry_tables()
        self.assertEqual(info["fields"], tables[0])
        self.assertEqual(info["not_here"], tables[1])

    def test_api_viewer_says_provisional_and_the_fields_without_a_sample(self):
        got = self.json("sample", "/api/viewer")
        self.assertTrue(got["provisional"])
        self.assertNotIn("assembly", got["missing"])
        self.assertIn("nvh", got["missing"])
        self.assertEqual(got["fields"], viewer_link.tool_table())
        self.assertEqual(got["not_here"], {})

    def test_viewer_files_are_served_and_404_without_the_viewer(self):
        status, ctype, body = self.get("sample", "/viewer/index.html")
        if (REPO_DIR / "viewer" / "index.html").is_file():
            self.assertEqual(status, 200)
            self.assertTrue(ctype.startswith("text/html"))
            status, ctype, _ = self.get("sample", "/viewer/js/main.js")
            self.assertEqual(status, 200)
            self.assertTrue(ctype.startswith("text/javascript"))
        status, _, body = self.get("sample", "/viewer/data/assembly.js")
        self.assertEqual(status, 200)
        self.assertIn(b"VIEW_DATA", body)
        self.assertEqual(self.get("noviewer", "/viewer/index.html")[0], 404)
        self.assertEqual(self.get("sample", "/viewer/../hiraku/webserve/server.py")[0], 404)

    def test_there_is_no_post(self):
        aid = self.first("assembly")
        for path in (f"/api/runs/{SCRIPTED_RUN}/viewer/{aid}", "/api/viewer", "/viewer/index.html"):
            self.assertEqual(self.get("sample", path, "POST")[0], 404, path)

    def test_the_screens_tool_table_is_the_servers(self):
        js = (REPO_DIR / "web" / "js" / "viewer_link.js").read_text(encoding="utf-8")
        body = re.search(r"export const VIEWER_FIELD = \{(.*?)\};", js, re.S).group(1)
        table = dict(re.findall(r"(\w+): \"(\w+)\"", body))
        self.assertEqual(table, viewer_link.TOOL_FIELD)
        self.assertIn(f'ROPS_PREFIX = "{viewer_link.ROPS_PREFIX}"', js)
        for key, text in (("noField", viewer_link.NO_FIELD), ("noSample", viewer_link.NO_SAMPLE), ("notOk", viewer_link.NOT_OK), ("sample", viewer_link.SAMPLE_SOURCE)):
            self.assertIn(f'{key}: "{text}"', js)
        self.assertEqual(viewer_link.field_of("rops_pushover_frame_all"), "rops")
        self.assertIsNone(viewer_link.field_of("step_climb"))

    def test_static_export_writes_every_analysis_and_the_viewer(self):
        out = self.tmp / "static"
        events = SAVED_DIR / SCRIPTED_RUN / "events.jsonl"
        total = viewer_link.export(out, {SCRIPTED_RUN: events}, self.data, build=False)
        files = sorted(p.name for p in (out / "data" / "viewer" / SCRIPTED_RUN).iterdir())
        self.assertEqual(files, sorted(f"{a}.json" for a in self.anas))
        one = json.loads((out / "data" / "viewer" / SCRIPTED_RUN / f"{self.first('assembly')}.json").read_text(encoding="utf-8"))
        self.assertEqual(one["data"], SAMPLE)
        self.assertTrue(json.loads((out / "data" / "viewer.json").read_text(encoding="utf-8"))["provisional"])
        if (REPO_DIR / "viewer" / "index.html").is_file():
            self.assertTrue((out / "viewer" / "index.html").is_file())
            self.assertFalse((out / "viewer" / "src").exists())
        self.assertEqual(total["files"], sum(1 for p in out.rglob("*") if p.is_file()))


if __name__ == "__main__":
    unittest.main()
