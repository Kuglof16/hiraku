import io
import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from hiraku.webserve.launch import Launcher, LaunchError
from hiraku.webserve.server import SAMPLE_EVENTS, list_runs, make_server, read_events
from hiraku.webserve.shelf import Shelf
from tests.web.fake_launch import fake_command, fake_reader

RUN_ID = "20261102-203000-offroad-4w"
LF = chr(10)
CRLF = chr(13) + chr(10)


def _line(seq, kind, t=0.0, **extra):
    ev = {"seq": seq, "ts": "2026-11-02T20:30:00.000+09:00", "t": t, "run_id": RUN_ID, "kind": kind}
    ev.update(extra)
    return json.dumps(ev, ensure_ascii=False) + LF


class ReadEventsTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "events.jsonl"

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_reads_only_complete_lines(self):
        first = _line(0, "run.started", data={"want": "x"})
        self.path.write_bytes((first + '{"seq": 1, "kind": "agent.s').encode("utf-8"))
        out = read_events(self.path, 0)
        self.assertEqual([e["seq"] for e in out["events"]], [0])
        self.assertEqual(out["next_offset"], len(first.encode("utf-8")))
        # the half-written line is picked up once it is finished
        with self.path.open("ab") as f:
            f.write(('aid", "t": 1.0}' + LF).encode("utf-8"))
        out2 = read_events(self.path, out["next_offset"])
        self.assertEqual([e["seq"] for e in out2["events"]], [1])
        self.assertEqual(out2["next_offset"], self.path.stat().st_size)

    def test_crlf_lines(self):
        text = _line(0, "run.started") + _line(1, "cost.updated")
        self.path.write_bytes(text.replace(LF, CRLF).encode("utf-8"))
        out = read_events(self.path, 0)
        self.assertEqual([e["seq"] for e in out["events"]], [0, 1])
        self.assertEqual(out["next_offset"], self.path.stat().st_size)

    def test_offset_past_end_restarts(self):
        self.path.write_bytes(_line(0, "run.started").encode("utf-8"))
        out = read_events(self.path, 10_000)
        self.assertTrue(out["reset"])
        self.assertEqual(len(out["events"]), 1)

    def test_bad_lines_are_counted_not_raised(self):
        text = _line(0, "run.started") + "not json" + LF + "[1, 2]" + LF + _line(1, "cost.updated")
        self.path.write_bytes(text.encode("utf-8"))
        out = read_events(self.path, 0)
        self.assertEqual([e["seq"] for e in out["events"]], [0, 1])
        self.assertEqual(out["bad_lines"], 2)

    def test_multibyte_text_survives(self):
        self.path.write_bytes(_line(0, "agent.said", text="先にレールを梁とみて応力を見積もります").encode("utf-8"))
        out = read_events(self.path, 0)
        self.assertEqual(out["events"][0]["text"], "先にレールを梁とみて応力を見積もります")


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.runs = cls.tmp / "runs"
        run = cls.runs / RUN_ID
        run.mkdir(parents=True)
        (run / "events.jsonl").write_bytes(
            (
                _line(0, "run.started", data={"want": "オフロード車両", "product": "offroad-4w", "status": "running"})
                + _line(1, "agent.said", t=1.0, agent="lead", text="始めます")
            ).encode("utf-8")
        )
        (cls.runs / "not-a-run").mkdir()
        cls.web = cls.tmp / "web"
        (cls.web / "js").mkdir(parents=True)
        (cls.web / "index.html").write_text("<!doctype html><title>t</title>", encoding="utf-8")
        (cls.web / "js" / "app.js").write_text("export const x = 1;", encoding="utf-8")
        (cls.tmp / "secret.txt").write_text("outside web/", encoding="utf-8")
        cls.server = make_server("127.0.0.1", 0, cls.runs, cls.web, quiet=True)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp)

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            return r.status, r.headers, r.read()

    def get_error(self, path):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self.base + path, timeout=5)
        return cm.exception.code

    def test_index_and_js_content_type(self):
        status, headers, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"<title>t</title>", body)
        _, headers, _ = self.get("/js/app.js")
        self.assertTrue(headers["Content-Type"].startswith("text/javascript"))

    def test_runs_list(self):
        _, _, body = self.get("/api/runs")
        runs = json.loads(body)
        ids = [r["run_id"] for r in runs]
        self.assertEqual(ids[0], RUN_ID)
        self.assertNotIn("not-a-run", ids)
        self.assertIn("sample", ids)
        self.assertEqual(runs[0]["want"], "オフロード車両")
        self.assertFalse(runs[0]["finished"])

    def test_events_diff(self):
        _, _, body = self.get(f"/api/runs/{RUN_ID}/events?offset=0")
        out = json.loads(body)
        self.assertEqual([e["seq"] for e in out["events"]], [0, 1])
        _, _, body = self.get(f"/api/runs/{RUN_ID}/events?offset={out['next_offset']}")
        self.assertEqual(json.loads(body)["events"], [])

    def test_sample_events(self):
        _, _, body = self.get("/api/runs/sample/events")
        out = json.loads(body)
        self.assertEqual(out["events"][0]["kind"], "run.started")
        self.assertEqual(out["next_offset"], SAMPLE_EVENTS.stat().st_size)

    def test_rejects_bad_ids_and_paths(self):
        self.assertEqual(self.get_error("/api/runs/..%5Csecret/events"), 400)
        self.assertEqual(self.get_error("/api/runs/..%2F..%2Fsecret/events"), 404)
        self.assertEqual(self.get_error("/api/runs/20991231-000000-nothing/events"), 404)
        self.assertEqual(self.get_error(f"/api/runs/{RUN_ID}/events?offset=abc"), 400)
        self.assertEqual(self.get_error("/../secret.txt"), 404)
        self.assertEqual(self.get_error("/%2e%2e/secret.txt"), 404)
        self.assertEqual(self.get_error("/api/nothing"), 404)


class ListRunsTest(unittest.TestCase):
    def test_missing_runs_dir_still_lists_sample(self):
        runs = list_runs(Path(tempfile.gettempdir()) / "hiraku-no-such-runs-dir")
        self.assertEqual([r["run_id"] for r in runs], ["sample"])
        self.assertTrue(runs[0]["finished"])
        self.assertFalse(runs[0]["default"])  # no saved run: the screen opens on the list


class SavedRunsTest(unittest.TestCase):
    """Runs kept in the repository (saved_dir) are listed first and the newest is the default."""

    OLD = "20261105-100000-offroad-4w"
    NEW = "20261106-100000-offroad-4w"

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.saved = cls.tmp / "showcase"
        cls.runs = cls.tmp / "runs"
        for root, run_id, want in ((cls.saved, cls.OLD, "古い"), (cls.saved, cls.NEW, "保存した"), (cls.runs, RUN_ID, "手元"), (cls.runs, cls.OLD, "手元の同じ実行")):
            (root / run_id).mkdir(parents=True)
            (root / run_id / "events.jsonl").write_bytes(
                (_line(0, "run.started", data={"want": want}) + _line(1, "run.finished", t=1.0, data={"status": "done"})).encode("utf-8"))
        cls.server = make_server("127.0.0.1", 0, cls.runs, cls.tmp / "web", quiet=True, saved_dir=cls.saved)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp)

    def test_saved_first_and_newest_saved_is_default(self):
        runs = list_runs(self.runs, self.saved)
        self.assertEqual([r["run_id"] for r in runs], [self.NEW, self.OLD, RUN_ID, "sample"])
        self.assertEqual([r["run_id"] for r in runs if r["saved"]], [self.NEW])  # OLD is also in runs/: listed once, from there
        self.assertEqual(runs[1]["want"], "手元の同じ実行")
        self.assertEqual([r["run_id"] for r in runs if r["default"]], [self.NEW])

    def test_default_run_can_be_named(self):
        runs = list_runs(self.runs, self.saved, default_run=RUN_ID)
        self.assertEqual([r["run_id"] for r in runs if r["default"]], [RUN_ID])
        runs = list_runs(self.runs, self.saved, default_run="20991231-000000-nothing")
        self.assertEqual([r["run_id"] for r in runs if r["default"]], [self.NEW])  # unknown name: back to the newest saved

    def test_saved_run_events_are_served(self):
        with urllib.request.urlopen(f"{self.base}/api/runs/{self.NEW}/events", timeout=5) as r:
            out = json.loads(r.read())
        self.assertEqual(out["events"][0]["data"]["want"], "保存した")
        with urllib.request.urlopen(f"{self.base}/api/runs", timeout=5) as r:
            runs = json.loads(r.read())
        self.assertTrue(next(x for x in runs if x["run_id"] == self.NEW)["default"])


class LayoutTest(unittest.TestCase):
    def test_layout_rules_are_served_or_missing_is_said(self):
        tmp = Path(tempfile.mkdtemp())
        servers = []
        try:
            for name, body in (("layout.json", {"stage": {"w": 1920, "h": 1080}}), ("absent.json", None)):
                if body:
                    (tmp / name).write_text(json.dumps(body), encoding="utf-8")
                s = make_server("127.0.0.1", 0, tmp, tmp, quiet=True, saved_dir=None, launcher=False, layout_file=tmp / name)
                threading.Thread(target=s.serve_forever, daemon=True).start()
                servers.append(s)
                url = f"http://127.0.0.1:{s.server_address[1]}/layout.json"
                if body:
                    with urllib.request.urlopen(url, timeout=5) as r:
                        self.assertEqual(json.loads(r.read()), body)
                else:
                    with self.assertRaises(urllib.error.HTTPError) as cm:
                        urllib.request.urlopen(url, timeout=5)
                    self.assertEqual(cm.exception.code, 404)
        finally:
            for s in servers:
                s.shutdown()
                s.server_close()
            shutil.rmtree(tmp)


class OpenTest(unittest.TestCase):
    """python -m hiraku.webserve --open (what HIRAKU.cmd runs): the browser opens on the server's page."""

    def test_open_starts_the_browser_on_the_page(self):
        from unittest import mock

        from hiraku.webserve import __main__ as entry

        class Fake:
            server_address = ("127.0.0.1", 8765)
            RequestHandlerClass = type("H", (), {"launcher": None})

            def serve_forever(self):
                raise KeyboardInterrupt

            def server_close(self):
                pass

        tmp = Path(tempfile.mkdtemp())
        try:
            with mock.patch.object(entry, "make_server", return_value=Fake()), mock.patch.object(entry.webbrowser, "open") as opened, \
                    mock.patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(entry.main(["--open", "--runs", str(tmp), "--saved", str(tmp)]), 0)
                opened.assert_called_once_with("http://127.0.0.1:8765/")
            with mock.patch.object(entry, "make_server", return_value=Fake()), mock.patch.object(entry.webbrowser, "open") as opened, \
                    mock.patch("sys.stdout", new_callable=io.StringIO):
                entry.main(["--runs", str(tmp), "--saved", str(tmp)])
                opened.assert_not_called()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class ShelfTest(unittest.TestCase):
    """GET /api/shelf (the A4 shelf, computed once in the background) and a run's tool files."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.release = threading.Event()
        self.calls = 0

        def load():
            self.calls += 1
            self.release.wait(10)
            return {"tools": [{"name": "frame_static"}], "formulas": [{"formula_id": "frame_static.rail_bending"}]}

        run = self.tmp / "runs" / RUN_ID
        (run / "tools").mkdir(parents=True)
        (run / "events.jsonl").write_text(_line(0, "run.started"), encoding="utf-8")
        (run / "tools" / "rollbar_load-1.py").write_bytes(b"def run():\n    return 1\n")
        (run / "tools" / "rollbar_load-1.check.json").write_text('[{"case": "a"}]', encoding="utf-8")
        self.server = make_server("127.0.0.1", 0, self.tmp / "runs", quiet=True, saved_dir=None, launcher=False, shelf=Shelf(load))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp)

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=10) as r:
                return r.status, r.headers.get("Content-Type"), r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type"), e.read().decode("utf-8")

    def test_shelf_is_ready_after_its_one_load(self):
        self.assertEqual(json.loads(self.get("/api/shelf")[2]), {"ready": False})
        self.assertEqual(json.loads(self.get("/api/shelf")[2]), {"ready": False})  # still loading, not loaded twice
        self.release.set()
        for _ in range(100):
            got = json.loads(self.get("/api/shelf")[2])
            if got["ready"]:
                break
            threading.Event().wait(0.05)
        self.assertEqual([t["name"] for t in got["tools"]], ["frame_static"])
        self.assertEqual(got["formulas"][0]["formula_id"], "frame_static.rail_bending")
        self.assertEqual(self.calls, 1)

    def test_a_runs_tool_files(self):
        status, ctype, text = self.get(f"/api/runs/{RUN_ID}/tools/rollbar_load-1.py")
        self.assertEqual((status, ctype.split(";")[0], text), (200, "text/plain", "def run():\n    return 1\n"))
        status, ctype, text = self.get(f"/api/runs/{RUN_ID}/tools/rollbar_load-1.check.json")
        self.assertEqual((status, json.loads(text)), (200, [{"case": "a"}]))
        for bad in ("rollbar_load-2.py", "rollbar_load-1.txt", "..%2Fevents.jsonl", "Rollbar-1.py"):
            self.assertEqual(self.get(f"/api/runs/{RUN_ID}/tools/{bad}")[0], 404, bad)
        self.assertEqual(self.get("/api/runs/sample/tools/rollbar_load-1.py")[0], 404)


class LaunchTest(unittest.TestCase):
    """Starting a run from the screen (the child is a stand-in for python -m hiraku run)."""

    RUN = "20261103-120000-offroad-4w-fake"
    SECRET = "test-value-not-a-real-key"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.runs = self.tmp / "runs"
        req = self.tmp / "requirements"
        req.mkdir()
        (req / "offroad-4w.yaml").write_text("product: offroad-4w\n", encoding="utf-8")
        self.servers = []

    def tearDown(self):
        for s in self.servers:
            s.shutdown()
            s.server_close()
        for lau in getattr(self, "launchers", []):
            if lau.proc and lau.proc.poll() is None:
                lau.proc.kill()
                lau.proc.wait()
        shutil.rmtree(self.tmp)

    def serve(self, env, hold_s=3.0):
        lau = Launcher(self.runs, self.tmp / "requirements", env=env, command=fake_command(SAMPLE_EVENTS, self.RUN, hold_s),
                       reader=fake_reader, wait_s=20)
        self.launchers = [*getattr(self, "launchers", []), lau]
        server = make_server("127.0.0.1", 0, self.runs, self.tmp, quiet=True, saved_dir=None, launcher=lau)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def post(self, base, body, headers=None):
        req = urllib.request.Request(base + "/api/launch", data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_status_lists_requirements_and_never_the_key(self):
        base = self.serve({"ANTHROPIC_API_KEY": self.SECRET})
        with urllib.request.urlopen(base + "/api/launch", timeout=5) as r:
            raw = r.read().decode("utf-8")
        info = json.loads(raw)
        self.assertEqual([x["file"] for x in info["requirements"]], ["offroad-4w.yaml"])
        self.assertEqual(info["requirements"][0]["cost_limit_usd"], 2.0)
        self.assertTrue(info["claude_ready"])
        self.assertNotIn(self.SECRET, raw)

    def test_scripted_run_starts_and_only_one_at_a_time(self):
        base = self.serve({})
        status, out = self.post(base, {"requirements": "offroad-4w.yaml", "mode": "scripted"})
        self.assertEqual((status, out["run_id"]), (200, self.RUN))
        self.assertTrue((self.runs / self.RUN / "events.jsonl").exists())
        status, out = self.post(base, {"requirements": "offroad-4w.yaml", "mode": "scripted"})
        self.assertEqual(status, 409)  # the first one is still going

    def test_stop_puts_the_request_in_the_started_run_only(self):
        base = self.serve({})
        _, out = self.post(base, {"requirements": "offroad-4w.yaml", "mode": "scripted"})
        req = urllib.request.Request(base + "/api/launch/stop", data=json.dumps({"run_id": "20991231-000000-other"}).encode(),
                                     method="POST", headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(cm.exception.code, 409)  # not the run started here
        req = urllib.request.Request(base + "/api/launch/stop", data=json.dumps({"run_id": out["run_id"]}).encode(),
                                     method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertTrue(json.loads(r.read())["stop_requested"])
        self.assertTrue((self.runs / out["run_id"] / "stop.request").exists())

    def test_hearing_answer_goes_to_the_started_run_only(self):
        base = self.serve({})
        _, out = self.post(base, {"requirements": "offroad-4w.yaml", "mode": "scripted"})

        def answer(run_id, body):
            req = urllib.request.Request(base + "/api/launch/hearing", data=json.dumps({"run_id": run_id, "answer": body}).encode(),
                                         method="POST", headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())

        self.assertEqual(answer("20991231-000000-other", {"answers": {}})[0], 409)  # not the run started here
        status, got = answer(out["run_id"], {"answers": {}, "note": "林道", "colour": "red"})
        self.assertEqual(status, 400)  # the hearing's own check
        self.assertIn("colour", got["error"])
        self.assertFalse((self.runs / out["run_id"] / "hearing.answer.json").exists())
        status, got = answer(out["run_id"], {"answers": {}, "note": "林道", "useDefaults": True})
        self.assertEqual((status, got["answered"]), (200, True))
        written = json.loads((self.runs / out["run_id"] / "hearing.answer.json").read_text(encoding="utf-8"))
        self.assertEqual(written, {"answers": {}, "note": "林道", "use_defaults": True})

    def test_dropped_knowledge_goes_to_the_started_run_through_its_inbox(self):
        base = self.serve({})
        _, out = self.post(base, {"requirements": "offroad-4w.yaml", "mode": "scripted"})
        run = self.runs / out["run_id"]

        def drop(body, raw=None):
            data = raw if raw is not None else json.dumps(body).encode()
            req = urllib.request.Request(base + "/api/launch/knowledge", data=data, method="POST", headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())

        self.assertEqual(drop({"run_id": "20991231-000000-other", "name": "a.md", "text": "x"})[0], 409)
        for bad in (".hidden", "C:x.md", "", "..", "x/..", 7):
            self.assertEqual(drop({"run_id": out["run_id"], "name": bad, "text": "x"})[0], 400, bad)
        self.assertFalse((run / "inbox").exists())
        status, got = drop({"run_id": out["run_id"], "name": "dir/曲げの決まり.md", "text": "曲げの内側の半径は板厚以上"})
        self.assertEqual((status, got["file"], got["request"]), (200, "曲げの決まり.md", "inbox/1-knowledge.json"))
        self.assertEqual((run / "knowledge" / "曲げの決まり.md").read_text(encoding="utf-8"), "曲げの内側の半径は板厚以上")
        self.assertEqual(json.loads((run / "inbox" / "1-knowledge.json").read_text(encoding="utf-8")), {"kind": "knowledge", "file": "曲げの決まり.md"})
        self.assertEqual(drop({"run_id": out["run_id"], "name": "rules.csv", "text": "a,b"})[1]["request"], "inbox/2-knowledge.json")
        self.assertEqual(sorted(p.name for p in (run / "inbox").iterdir()), ["1-knowledge.json", "2-knowledge.json"])
        # only the name's last part is kept: nothing lands outside knowledge/
        self.assertEqual(drop({"run_id": out["run_id"], "name": "../../evil.md", "text": "x"})[1]["file"], "evil.md")
        self.assertTrue((run / "knowledge" / "evil.md").exists())
        self.assertFalse((run / "evil.md").exists() or (self.runs / "evil.md").exists())
        big = json.dumps({"run_id": out["run_id"], "name": "big.md", "text": "x" * 2_100_000}).encode()
        self.assertEqual(drop(None, raw=big)[0], 413)
        self.assertFalse((run / "knowledge" / "big.md").exists())

    def test_a_request_from_A1_runs_with_its_intake_events(self):
        """A1 -> A2 -> 始める without a command: /api/request (the domains and the hearing),
        /api/request/answer (the request's files under runs/_requests/<id>/), then /api/launch
        {request} starts hiraku run with --intake-events."""
        seen = []
        fake = fake_command(SAMPLE_EVENTS, self.RUN, 3.0)

        def command(req, mode, runs, intake_events=None):
            seen.append((Path(req), intake_events and Path(intake_events)))
            return fake(req, mode, runs)

        lau = Launcher(self.runs, self.tmp / "requirements", env={}, command=command, reader=fake_reader, wait_s=20)
        self.launchers = [*getattr(self, "launchers", []), lau]
        server = make_server("127.0.0.1", 0, self.runs, self.tmp, quiet=True, saved_dir=None, launcher=lau)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.servers.append(server)
        base = f"http://127.0.0.1:{server.server_address[1]}"

        def post(path, body):
            req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())

        status, first = post("/api/request", {"want": "林道を走れる 2 人乗りの小さな車両", "product": "offroad-4w", "claude": True})
        self.assertEqual(status, 200)
        self.assertEqual(first["mode"], "code")  # no key here: Claude is not asked
        self.assertEqual([e["kind"] for e in first["events"]], ["domains.picked", "hearing.asked"])
        status, half = post("/api/request/answer", {"draft": first["draft"], "answers": {}})
        self.assertEqual((status, half["ready"]), (200, False))  # required items unanswered
        self.assertIn("Q30", half["next"])
        answers = {"Q30": "two_cargo", "Q40": 150, "Q50": 40, "Q70": "私有地だけ"}
        status, done = post("/api/request/answer", {"draft": first["draft"], "answers": answers})
        self.assertEqual((status, done["ready"], done["scripted_plan"], done["designable"]), (200, True, True, True))
        folder = self.runs / "_requests" / done["request_id"]
        self.assertTrue((folder / "requirements.yaml").is_file() and (folder / "intake.json").is_file())
        self.assertEqual(post("/api/launch", {"request": "../evil", "mode": "scripted"})[0], 400)
        status, out = post("/api/launch", {"request": done["request_id"], "mode": "scripted"})
        self.assertEqual((status, out["run_id"], out["requirements"]), (200, self.RUN, f"_requests/{done['request_id']}"))
        self.assertEqual(seen, [(folder / "requirements.yaml", folder / "intake.json")])
        status, bad = post("/api/request", {"want": 7})
        self.assertEqual(status, 400)
        self.assertTrue(bad["error"])

    def test_a_request_with_no_kind_of_design_is_refused(self):
        lau = Launcher(self.runs, self.tmp / "requirements", env={}, command=fake_command(SAMPLE_EVENTS, self.RUN, 1.0), reader=fake_reader)
        folder = lau.requests_dir / "20261103-120000-guitar"
        folder.mkdir(parents=True)
        (folder / "requirements.yaml").write_text("product: guitar\nflags: {}\n", encoding="utf-8")
        (folder / "intake.json").write_text("[]", encoding="utf-8")
        with self.assertRaises(LaunchError) as cm:
            lau.start(None, "scripted", "20261103-120000-guitar")
        self.assertEqual(cm.exception.status, 422)
        self.assertIn("設計の作り方がまだ無い", str(cm.exception))

    def test_claude_needs_the_key_where_the_server_runs(self):
        base = self.serve({})
        status, out = self.post(base, {"requirements": "offroad-4w.yaml", "mode": "claude"})
        self.assertEqual(status, 403)
        self.assertIn("ANTHROPIC_API_KEY", out["error"])
        base = self.serve({"ANTHROPIC_API_KEY": self.SECRET})
        status, out = self.post(base, {"requirements": "offroad-4w.yaml", "mode": "claude"})
        self.assertEqual((status, out["run_id"]), (200, self.RUN + "-claude"))

    def test_refuses_other_files_and_other_pages(self):
        base = self.serve({"ANTHROPIC_API_KEY": self.SECRET})
        port = base.rsplit(":", 1)[1]
        self.assertEqual(self.post(base, {"requirements": "../secret.yaml", "mode": "scripted"})[0], 400)
        self.assertEqual(self.post(base, {"requirements": "none.yaml", "mode": "scripted"})[0], 404)
        self.assertEqual(self.post(base, {"requirements": "offroad-4w.yaml", "mode": "other"})[0], 400)
        body = {"requirements": "offroad-4w.yaml", "mode": "claude"}
        self.assertEqual(self.post(base, body, {"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.post(base, body, {"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.post(base, body, {"Host": f"evil.example:{port}", "Origin": f"http://evil.example:{port}"})[0], 403)
        self.assertFalse(self.runs.exists() and any(self.runs.iterdir()))  # nothing was started


if __name__ == "__main__":
    unittest.main()
