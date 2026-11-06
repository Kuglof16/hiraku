"""viewer.src.build: the viewer's data for any one analysis of a run (for_analysis), the same object each field's own
command writes, the reasons when a record cannot be shown, and the page reading it as JSON (index.html?data=)."""

import functools
import http.server
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import unittest.mock
from pathlib import Path

from viewer.src import build, common, from_events
from tests.viewer.test_viewer import _system_python_with_playwright

FX = Path(__file__).resolve().parent / "fixtures"
ROOT = Path(__file__).resolve().parents[2]
VIEWER = ROOT / "viewer"

# (fixture run folder, analysis id, the page's data key, the field's command and its arguments)
CASES = [
    ("nvh", "ana-0084", "nvh", ["nvh"]),
    ("shell", "ana-0001", "nvh_shell", ["nvh_shell"]),
    ("crash", "ana-0001", "crash", ["crash"]),
    ("crash_b", "ana-0002", "crash", ["crash"]),
    ("crash_contact", "ana-0001", "crash", ["crash"]),
    ("assembly", "ana-0006", "assembly", ["assembly"]),
    ("strength_beam", "ana-0083", "strength", ["strength"]),
    ("fatigue", "ana-0001", "fatigue", ["fatigue"]),
    ("thermal", "ana-0001", "thermal", ["thermal"]),
    ("handling", "ana-0001", "handling", ["handling"]),
    ("powertrain", "ana-0001", "powertrain", ["powertrain"]),
    ("powertrain_energy", "ana-0001", "powertrain", ["powertrain"]),
    ("aero", "ana-0001", "aero", ["aero"]),
]


def command_data(fixture: str, cmd: list[str], out: Path) -> tuple[str, dict]:
    r = subprocess.run([sys.executable, "-m", f"viewer.src.{cmd[0]}", str(FX / fixture), *cmd[1:], "--out", str(out)],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise AssertionError(r.stderr[-2000:])
    files = list(out.glob("*.js"))
    assert len(files) == 1, files
    return common.read_data_js(files[0])


def copy_fixture(name: str, to: Path) -> Path:
    d = to / name
    shutil.copytree(FX / name, d)
    return d


class ForAnalysisTest(unittest.TestCase):

    def test_same_as_each_fields_command(self):
        for fixture, aid, key, cmd in CASES:
            with self.subTest(fixture=fixture), tempfile.TemporaryDirectory() as d:
                got = build.for_analysis(FX / fixture, aid)
                self.assertEqual(got["field"], key)
                field, data = command_data(fixture, cmd, Path(d))
                self.assertEqual(field, key)
                self.assertEqual(json.loads(json.dumps(got["data"])), data)
                self.assertEqual(got["title"], data["title"])
                self.assertEqual(got["source"], data["source"])
                self.assertEqual(got["source"]["analysis_id"], aid)

    def test_tool_map(self):
        for tool, key in (("frame_static", "strength"), ("modal", "nvh"), ("modal_shell", "nvh_shell"),
                          ("crash_beam", "crash"), ("tilt", "handling"), ("offroad_roll", "handling"),
                          ("step_climb_dynamic", "powertrain"), ("crosswind", "aero"), ("drive_energy", "powertrain"),
                          ("spray_reach", "assembly"), ("entry_opening", "assembly")):
            self.assertEqual(build.TOOL_FIELD[tool], key)

    def test_drive_energy_beside_thermal(self):
        # the thermal fixture's drive_energy record: the powertrain field's energy case
        got = build.for_analysis(FX / "thermal", "ana-0002")
        self.assertEqual(got["field"], "powertrain")
        self.assertEqual(got["data"]["kind"], "energy")
        self.assertEqual(got["source"]["analysis_id"], "ana-0002")

    def _edit(self, fixture, aid, change):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = copy_fixture(fixture, Path(tmp.name))
        f = next(d.glob(f"v*/analyses/{aid}.json"))
        rec = json.loads(f.read_text(encoding="utf-8"))
        change(rec)
        f.write_text(json.dumps(rec), encoding="utf-8")
        return d

    def assertReason(self, got):
        self.assertIsNone(got["field"])
        self.assertIsInstance(got["reason"], str)
        self.assertTrue(got["reason"])
        self.assertNotIn("\n", got["reason"])
        self.assertNotIn(str(FX.parent), got["reason"])
        self.assertNotIn("data", got)

    def test_unknown_tool(self):
        d = self._edit("fatigue", "ana-0001", lambda r: r.update(tool="no_such_tool"))
        got = build.for_analysis(d, "ana-0001")
        self.assertReason(got)
        self.assertIn("no_such_tool", got["reason"])

    def test_record_without_what_the_field_needs(self):
        for fixture, aid, drop in (("fatigue", "ana-0001", "fields"), ("nvh", "ana-0084", "fields"),
                                   ("handling", "ana-0001", "series"), ("thermal", "ana-0001", "series"),
                                   ("strength_beam", "ana-0083", "fields"), ("crash_b", "ana-0002", "fields")):
            with self.subTest(fixture=fixture):
                self.assertReason(build.for_analysis(self._edit(fixture, aid, lambda r: r.update({drop: []})), aid))

    def test_shell_mesh_missing(self):
        with tempfile.TemporaryDirectory() as t:
            d = copy_fixture("shell", Path(t))
            shutil.rmtree(d / "meshes")
            got = build.for_analysis(d, "ana-0001")
            self.assertReason(got)
            self.assertIn("mesh-0001", got["reason"])

    def test_tools_that_need_more_than_the_record(self):
        for tool in ("rops_pushover", "frame_shell"):
            with self.subTest(tool=tool):
                d = self._edit("strength_beam", "ana-0083", lambda r: r.update(tool=tool))
                got = build.for_analysis(d, "ana-0083")
                self.assertReason(got)
                self.assertEqual(got["reason"], build.NOT_HERE[tool])

    def test_no_such_analysis_and_not_ok(self):
        self.assertReason(build.for_analysis(FX / "fatigue", "ana-9999"))
        self.assertReason(build.for_analysis(self._edit("fatigue", "ana-0001", lambda r: r.update(status="failed")), "ana-0001"))

    def test_entry_opening_on_the_assembly_field(self):
        def add(d):
            v = d / "v001" / "analyses"
            rec = {"analysis_id": "ana-0007", "tool": "entry_opening", "load_case_id": "H7-entry", "status": "ok",
                   "results": [{"quantity": "entry_opening_width", "value": 520.0, "unit": "mm"}]}
            (v / "ana-0007.json").write_text(json.dumps(rec), encoding="utf-8")
        with tempfile.TemporaryDirectory() as t:
            d = copy_fixture("assembly", Path(t))
            add(d)
            got = build.for_analysis(d, "ana-0007")
            self.assertEqual(got["field"], "assembly")
            self.assertEqual(got["source"]["analysis_id"], "ana-0006")
            self.assertEqual([c["analysis_id"] for c in got["data"]["checks"]], ["ana-0007"])

    def test_command_json_and_out(self):
        with tempfile.TemporaryDirectory() as t:
            j = Path(t) / "x" / "fatigue.json"
            r = subprocess.run([sys.executable, "-m", "viewer.src.build", str(FX / "fatigue"), "ana-0001", "--json", str(j)],
                               cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stderr)
            got = json.loads(j.read_text(encoding="ascii"))
            self.assertEqual(got["field"], "fatigue")
            self.assertEqual(sorted(got), ["data", "field", "source", "title"])
            self.assertNotIn(str(FX.parent), j.read_text(encoding="ascii"))
            o = Path(t) / "page"
            r = subprocess.run([sys.executable, "-m", "viewer.src.build", str(FX / "fatigue"), "ana-0001", "--out", str(o)],
                               cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((o / "index.html").is_file() and (o / "js" / "main.js").is_file())
            field, data = common.read_data_js(o / "data" / "fatigue.js")
            self.assertEqual((field, data), ("fatigue", got["data"]))
            r = subprocess.run([sys.executable, "-m", "viewer.src.build", str(FX / "fatigue"), "ana-9999", "--json", str(j)],
                               cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 1)
            self.assertIsNone(json.loads(j.read_text(encoding="ascii"))["field"])


def _event(seq, kind, data, v=None):
    e = {"seq": seq, "ts": "2026-11-05T16:07:25.570+09:00", "t": float(seq), "run_id": "20261105-160725-test",
         "kind": kind, "agent": "system"}
    if v is not None:
        e["design_version"] = v
    e["data"] = data
    return e


class FromEventsTest(unittest.TestCase):
    """A run of events.jsonl only (as the showcase runs are): laid out as the store writes it, and for_analysis on it
    the same as on the fixture folder the records came from."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.cache = self.tmp / "cache"
        patcher = unittest.mock.patch.object(from_events, "cache_root", lambda: self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _thermal_run(self) -> tuple[Path, list[dict]]:
        """The thermal fixture (a design, its load case and two analyses) as the events of a run named thermal."""
        v = FX / "thermal" / "v001"
        design = common.read_json(v / "design.json")
        lc = common.read_json(v / "load_case.json")
        recs = [common.read_json(v / "analyses" / f"{a}.json") for a in ("ana-0001", "ana-0002")]
        events = [_event(0, "run.started", {"run_id": "20261105-160725-test", "load_cases": [lc]}),
                  _event(1, "design.created", design, 1),
                  _event(2, "agent.said", {"tests": []}),
                  *[_event(3 + i, "analysis.finished", r, 1) for i, r in enumerate(recs)]]
        run = self.tmp / "thermal"
        run.mkdir()
        self._write_events(run, events)
        return run, events

    @staticmethod
    def _write_events(run: Path, events: list[dict]):
        (run / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events),
                                          encoding="utf-8", newline="\n")

    def test_constants_are_the_contracts(self):
        from hiraku.contract import layout
        self.assertEqual(from_events.RECORD_EVENT, layout.RECORD_EVENT)
        self.assertEqual(from_events.RECORD_DIRS, layout.RECORD_DIRS)

    def test_same_files_as_the_fixture(self):
        run, _ = self._thermal_run()
        self.assertTrue(from_events.needs_materialize(run))
        self.assertFalse(from_events.needs_materialize(FX / "thermal"))
        out = from_events.materialize(run)
        self.assertEqual(out.name, "thermal")
        self.assertTrue(str(out).startswith(str(self.cache)))
        fx = FX / "thermal" / "v001"
        self.assertEqual(common.read_json(out / "v001" / "design.json"), common.read_json(fx / "design.json"))
        for a in ("ana-0001", "ana-0002"):
            self.assertEqual(common.read_json(out / "v001" / "analyses" / f"{a}.json"),
                             common.read_json(fx / "analyses" / f"{a}.json"))
        self.assertEqual(sorted(p.name for p in (out / "v001").iterdir()), ["analyses", "design.json"])
        self.assertEqual(common.read_json(out / "project.json")["load_cases"], [common.read_json(fx / "load_case.json")])
        self.assertEqual(common.load_case_of(out / "v001", "T1-40C"), common.load_case_of(fx, "T1-40C"))
        self.assertEqual(sorted(p.name for p in run.iterdir()), ["events.jsonl"])   # nothing written beside the run

    def test_for_analysis_same_as_on_the_fixture(self):
        run, _ = self._thermal_run()
        for aid, key in (("ana-0001", "thermal"), ("ana-0002", "powertrain")):
            with self.subTest(aid=aid):
                got, want = build.for_analysis(run, aid), build.for_analysis(FX / "thermal", aid)
                self.assertEqual(got["field"], key)
                self.assertEqual(json.loads(json.dumps(got)), json.loads(json.dumps(want)))
                self.assertEqual(got["source"]["run"], "thermal")
                self.assertNotIn(str(self.tmp), json.dumps(got, ensure_ascii=False))
        self.assertReason(build.for_analysis(run, "ana-9999"))

    def assertReason(self, got):
        self.assertIsNone(got["field"])
        self.assertTrue(got["reason"])

    def test_cache_reused_while_unchanged(self):
        run, events = self._thermal_run()
        a = from_events.materialize(run)
        stamp = (a / from_events.DONE).stat().st_mtime_ns
        (a / "v001" / "analyses" / "ana-0001.json").write_text("{}", encoding="utf-8")   # a reuse would keep this
        self.assertEqual(from_events.materialize(run), a)
        self.assertEqual((a / from_events.DONE).stat().st_mtime_ns, stamp)
        self.assertEqual(common.read_json(a / "v001" / "analyses" / "ana-0001.json"), {})
        # the events grow: a new folder, with the record written again (same id: the last event's)
        rec = dict(events[3]["data"], status="failed")
        self._write_events(run, events + [_event(9, "analysis.finished", rec, 1)])
        b = from_events.materialize(run)
        self.assertNotEqual(a.parent, b.parent)
        self.assertEqual(b.name, "thermal")
        self.assertEqual(common.read_json(b / "v001" / "analyses" / "ana-0001.json")["status"], "failed")
        self.assertIn("状態 failed", build.for_analysis(run, "ana-0001")["reason"])

    def test_version_load_cases(self):
        """load_cases.created writes v<NNN>/load_cases.json; a later version without its own set reads the nearest
        earlier one, version 1 reads project.json (RunStore.version_load_cases)."""
        run = self.tmp / "r"
        run.mkdir()
        lc = lambda tag: {"load_case_id": "S1-step", "tag": tag}
        self._write_events(run, [
            _event(0, "run.started", {"load_cases": [lc("v1")]}),
            *[_event(v, "design.created", {"design_version": v, "name": f"d{v}"}, v) for v in (1, 2, 3)],
            _event(4, "load_cases.created", {"design_version": 2, "load_cases": [lc("v2")]}, 2),
            _event(5, "mesh.created", {"mesh_id": "mesh-0001", "design_version": 3, "nodes_mm": []}, 3)])
        out = from_events.materialize(run)
        self.assertEqual(common.read_json(out / "v002" / "load_cases.json")["load_cases"], [lc("v2")])
        self.assertEqual(common.read_json(out / "v003" / "meshes" / "mesh-0001.json")["mesh_id"], "mesh-0001")
        self.assertEqual([common.load_case_of(out / f"v00{v}", "S1-step")["tag"] for v in (1, 2, 3)], ["v1", "v2", "v2"])


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class UrlPageTest(unittest.TestCase):
    """index.html?data=<url>#<field> served by a tiny http.server in a thread (fetch does not read file://)."""

    @classmethod
    def setUpClass(cls):
        cls.py = _system_python_with_playwright()
        if not cls.py:
            raise unittest.SkipTest("playwright is not available for the system Python (py)")
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "viewer"
        shutil.copytree(VIEWER, root, ignore=shutil.ignore_patterns("data", "src", "__pycache__", "*.py"))
        for name, fixture, aid in (("fatigue.json", "fatigue", "ana-0001"), ("shell.json", "shell", "ana-0001")):
            (root / name).write_text(json.dumps(build.for_analysis(FX / fixture, aid)), encoding="utf-8")
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(root)))
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}/index.html"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def _check(self, query, field):
        r = subprocess.run([self.py, str(Path(__file__).with_name("page_check.py")), self.base + query, field, "", "1"],
                           capture_output=True, text=True, encoding="utf-8", timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        out = json.loads(r.stdout)
        self.assertEqual(out["errors"], [])
        self.assertGreater(out["drawn_px"], 1000)
        return out

    def test_fatigue_from_json(self):
        out = self._check("?data=fatigue.json", "fatigue")
        self.assertEqual(out["shown_field"], "fatigue")
        self.assertIn("疲労", out["title"])

    def test_shell_variant_from_json(self):
        out = self._check("?data=shell.json", "nvh_shell")
        self.assertEqual(out["shown_field"], "nvh")
        self.assertEqual(out["variant"], "nvh_shell")


if __name__ == "__main__":
    unittest.main()
