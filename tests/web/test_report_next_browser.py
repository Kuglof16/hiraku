"""The report's next changes on the real screen: the grid (?tab=grid, on the workbench and with
?present=1) shows "次に変えるもの" from the newest report.written's next (domain in DOMAIN_JA,
change large, reason and rule under くわしく); none without a next or before the report.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed, as test_browser.
"""

import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku import contract
from hiraku.webserve.server import SAMPLE_EVENTS, make_server

try:
    from tests.web.pages import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None

# A small report in the shape of a scripted run's (two changes, one answering a rule); the
# words are this test's own.
NEXT = [
    {"domain": "strength", "change": "段差の試験をやり直す (見本)", "reason": "前の試験の荷重が記録に無い (見本)"},
    {"domain": "assembly", "change": "座面の奥行きを変数にする (見本)", "reason": "決まりの当たりが続いた (見本)", "rule_id": "HU-02"},
]


def events_with_report(next_=NEXT):
    events = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    report = {"design_version": 1, "domains": [{"domain": "strength", "state": "green", "line": "段差で降伏しない (見本)"}],
              "unchecked": [], "next": next_, "summary": "強度は合格 (見本)"}
    written = {"run_id": events[0]["run_id"], "ts": events[-1]["ts"], "seq": 0, "t": events[-1]["t"] - 0.1,
               "kind": "report.written", "agent": "lead", "data": report}
    events = events[:-1] + [written, events[-1]]
    for i, ev in enumerate(events):
        ev["seq"] = i
    return events


PROBE = """async ([events, t]) => {
  await window.hiraku.load(events, t);
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  const el = document.getElementById("report-next");
  const r = el.getBoundingClientRect();
  return {
    shown: el.checkVisibility() && r.height > 0,
    head: el.querySelector("h3")?.textContent ?? "",
    rows: [...el.querySelectorAll("li")].map((li) => ({
      dom: li.querySelector(".rn-dom").textContent,
      change: li.querySelector(".rn-change").textContent,
      changePx: parseFloat(getComputedStyle(li.querySelector(".rn-change")).fontSize),
      more: li.querySelector("details")?.textContent ?? "",
      open: li.querySelector("details")?.open ?? null,
    })),
    domPx: el.querySelector(".rn-dom") ? parseFloat(getComputedStyle(el.querySelector(".rn-dom")).fontSize) : 0,
    inView: r.bottom <= innerHeight && r.right <= innerWidth,
  };
}"""


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class ReportNextBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())  # runs/ is not made: the server has no runs of its own
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

    def open(self, query):
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.goto(f"{self.base}/?{query}")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=30000)
        page.evaluate("window.hiraku.ready")
        return page, errors

    def test_the_fixture_is_the_contract_s(self):
        for ev in events_with_report():
            self.assertEqual(contract.errors("event", ev), [], ev["kind"])

    def test_the_grid_shows_the_next_changes(self):
        for query in ("run=sample&pace=0&t=0&tab=grid", "run=sample&pace=0&t=0&tab=grid&present=1"):
            with self.subTest(query=query):
                page, errors = self.open(query)
                got = page.evaluate(PROBE, [events_with_report(), 1e6])
                self.assertEqual(errors, [])
                self.assertTrue(got["shown"], got)
                self.assertTrue(got["inView"], got)
                self.assertTrue(got["head"].startswith("次に変えるもの"), got)
                self.assertIn("版 1 の報告から", got["head"])
                self.assertEqual([r["dom"] for r in got["rows"]], ["強度信頼性", "組み立て"])
                self.assertEqual([r["change"] for r in got["rows"]], [n["change"] for n in NEXT])
                # the change large, the reason small and shut under くわしく
                for r in got["rows"]:
                    self.assertGreater(r["changePx"], got["domPx"])
                    self.assertFalse(r["open"])
                self.assertIn(NEXT[0]["reason"], got["rows"][0]["more"])
                self.assertIn("決まり HU-02 への答え", got["rows"][1]["more"])
                self.assertNotIn("HU-02", got["rows"][0]["more"])
                page.close()

    def test_no_next_or_before_the_report_no_block(self):
        page, errors = self.open("run=sample&pace=0&t=0&tab=grid")
        events = events_with_report()
        before = page.evaluate(PROBE, [events, 0.5])
        none = page.evaluate(PROBE, [events_with_report([]), 1e6])
        self.assertEqual(errors, [])
        self.assertFalse(before["shown"], before)
        self.assertFalse(none["shown"], none)
        page.close()


if __name__ == "__main__":
    unittest.main()
