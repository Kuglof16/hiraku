import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from hiraku.webserve.server import SAMPLE_EVENTS, list_runs, make_server, read_events

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


if __name__ == "__main__":
    unittest.main()
