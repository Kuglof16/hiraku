"""The screen's events reader (web/js/eventread.js) parses the ndjson form of
/api/runs/<id>/events (the lines' bytes as they are) into what the JSON form gives:
the same events, next_offset, size, reset and bad_lines at every read, on a record
with every kind of line the server's parser treats specially (CRLF, a lone CR, white
space, a BOM, bytes that are not UTF-8, JSON that is not an object, a line over the
line limit, a half-written last line), read in small pieces.

Browser tests: python -m tests.web.browser (needs Playwright).
"""

import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve import server as webserver
from hiraku.webserve.server import make_server
from tests.web.test_browser import ENABLED, sync_playwright

RUN_ID = "20261105-120000-eventread"


def _ev(seq, **extra):
    ev = {"seq": seq, "ts": "2026-11-05T12:00:00.000+09:00", "t": seq * 0.5, "run_id": RUN_ID, "kind": "agent.said", "agent": "lead"}
    ev.update(extra)
    return json.dumps(ev, ensure_ascii=False).encode("utf-8")


def tricky_record() -> bytes:
    lines = [
        _ev(0, kind="run.started") + b"\n",
        _ev(1, text="CRLF") + b"\r\n",
        _ev(2, text="a lone CR next") + b"\r" + _ev(3, text="after the CR") + b"\n",
        b"not json\n",
        b"[1, 2]\n",
        b"null\n",
        b'"a string"\n',
        b"\n",
        b"   \n",
        b" \t" + _ev(4, text="white space around") + b"  \n",
        b"\x0b" + _ev(5, text="vertical tab and form feed") + b"\x0c\n",
        b"\xef\xbb\xbf" + _ev(6, text="a BOM in front") + b"\n",
        b'{"seq": 7, "text": "\xff not UTF-8"}\n',
        _ev(8, text="先にレールを梁とみて応力を見積もります", big=12345678901234567890, f=1.0, tiny=1e-300) + b"\n",
        b'{"seq": 9, "seq": 10, "kind": "agent.said"}\n',
        b'{"a": 1}{"b": 2}\n',
        _ev(11, text="x" * 900) + b"\n",  # over the line limit set below: skipped
        _ev(12, text="y" * 300) + b"\n",  # longer than one read, under the line limit: read whole
        _ev(13, text="  and   inside a string") + b"\n",
        _ev(14, text="last whole line") + b"\n",
        b'{"seq": 15, "kind": "agent.sa',  # still being written
    ]
    return b"".join(lines)


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class EventReadBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        run = cls.tmp / "runs" / RUN_ID
        run.mkdir(parents=True)
        (run / "events.jsonl").write_bytes(tricky_record())
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
        shutil.rmtree(cls.tmp)

    def _limits(self, read, line):
        saved = (webserver.MAX_READ_BYTES, webserver.MAX_LINE_BYTES)
        webserver.MAX_READ_BYTES, webserver.MAX_LINE_BYTES = read, line
        self.addCleanup(lambda: setattr(webserver, "MAX_READ_BYTES", saved[0]) or setattr(webserver, "MAX_LINE_BYTES", saved[1]))

    def _reads(self, run_id):
        """Every read of both forms from offset 0 until a read does not move on (in the browser)."""
        page = self.browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.base}/api/runs")  # any page of the server (same origin)
        got = page.evaluate(
            """async (runId) => {
              const { fetchEvents, fetchAllEvents } = await import('/js/eventread.js');
              const steps = []; let offset = 0;
              for (let i = 0; i < 100; i++) {
                const json = await (await fetch(`/api/runs/${runId}/events?offset=${offset}`)).json();
                const nd = await fetchEvents(runId, offset);
                steps.push({ offset, json, nd });
                if (nd.next_offset === offset) break;
                offset = nd.next_offset;
              }
              return { steps, all: await fetchAllEvents(runId) };
            }""",
            run_id,
        )
        page.close()
        self.assertEqual(errors, [])
        return got

    def test_the_ndjson_form_parses_to_the_json_forms_reads(self):
        for read, line in ((48, 600), (100_000, 600), (8 * 1024 * 1024, 64 * 1024 * 1024)):
            with self.subTest(read=read, line=line):
                self._limits(read, line)
                got = self._reads(RUN_ID)
                every = []
                for step in got["steps"]:
                    js, nd = step["json"], step["nd"]
                    self.assertEqual(nd["run_id"], js["run_id"])
                    for k in ("events", "next_offset", "size", "reset", "bad_lines"):
                        self.assertEqual(nd[k], js[k], (step["offset"], k))
                    every += js["events"]
                self.assertEqual(got["all"], every)
                # line 11 (about 1 kB) is skipped only when it starts a read and is longer than
                # one read and than the line limit; 7 lines are bad as JSON
                skipped = read < 1000 and line < 1000
                seqs = [e["seq"] for e in every]
                self.assertEqual(seqs, [0, 1, 2, 3, 4, 5, 8, 10] + ([] if skipped else [11]) + [12, 13, 14])
                self.assertEqual(sum(s["json"]["bad_lines"] for s in got["steps"]), 7 + skipped)

    def test_the_contract_sample_reads_the_same(self):
        got = self._reads("sample")
        js = [e for s in got["steps"] for e in s["json"]["events"]]
        self.assertGreater(len(js), 10)
        self.assertEqual(got["all"], js)


if __name__ == "__main__":
    unittest.main()
