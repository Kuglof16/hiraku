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
from pathlib import Path

from viewer.src import build, common
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
