"""The footer's line and the left column's log in plain words (web/js/plain.js), as ③'s card.

The words checked: none holds a record's id (chg-0006, ver-0048, rule_check, side_post_LS2_1 ...)
nor English record words (plain.js hasRecordWords, the rule test_clear_3d_browser checks the card
by: it takes in /\\b[a-z]+-\\d{3,}\\b/ and three English words in a row), and none is left empty
(a footer line is more than who did it).

- every event of the showcase runs (showcase/2026*): applied in order (state.js applyEvent), the
  footer's line (hookline.js footerActivity) and each log line written or rewritten by the event
  (its text and its note) are made as app.js makes them (plain.js plainLineHtml) and read as the
  page shows them (span.rec out, plain.js shownText);
- on the screen, ③ at 1440 x 900 seeked to SEEN events of each run spread over it (and its last):
  the footer (#activity) and every line of the log (#log li) as shown (innerText);
- under ?dev=1 the words are as recorded: the ids show (runs 160725 and 150416-scripted).

(A seek takes a second or two on these runs, so the screen is read at some events, not all.)

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import make_server
from tests.web.test_browser import ENABLED, sync_playwright
from tests.web.test_layout_lint_browser import REPO_DIR

SHOWCASE = REPO_DIR / "showcase"
RUNS = sorted(p.name for p in SHOWCASE.glob("2026*") if (p / "events.jsonl").exists())
WITH_IDS = ("20261105-160725-offroad-4w", "20261105-150416-offroad-4w-scripted")
RECORD_ID = re.compile(r"\b[a-z]+-\d{3,}\b")
SEEN = 40

EVERY = """async (events) => {
  const st = await import('/js/state.js');
  const { footerActivity } = await import('/js/hookline.js');
  const { hasRecordWords, plainLineHtml, shownText } = await import('/js/plain.js');
  const s = st.initialState();
  const bad = [], empty = [];
  let n = 0;
  const check = (where, ev, text, footer = false) => {
    if (!text) return;
    n++;
    const shown = shownText(plainLineHtml(s, text)).trim();
    if (hasRecordWords(shown)) bad.push(`${where} seq ${ev.seq} ${ev.kind}: ${shown.slice(0, 160)}`);
    if (!shown || (footer && /^[^:：]+[:：]?$/.test(shown))) empty.push(`${where} seq ${ev.seq} ${ev.kind}: 「${shown}」 from 「${text.slice(0, 80)}」`);
  };
  for (const ev of events) {
    const len = s.log.length;
    st.applyEvent(s, ev);
    const touched = new Set([...Array(s.log.length - len).keys()].map((i) => len + i));
    if (s.touched?.seq === ev.seq) touched.add(s.touched.idx);
    for (const i of touched) {
      const e = s.log[i];
      if (e.level === 'hook') continue; // (a hooks line: its own words, the record folded under 詳しい記録)
      check('log', ev, e.text);
      check('log note', ev, e.note);
    }
    if (s.activity) check('footer', ev, footerActivity(s), true);
  }
  return { n, bad, empty };
}"""

SCREEN = """async (ts) => {
  const h = window.hiraku;
  const { hasRecordWords } = await import('/js/plain.js');
  const frame = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  const seen = new Map(); // text -> where
  for (const t of ts) {
    await h.seek(t);
    await frame();
    const act = document.getElementById('activity');
    if (act.checkVisibility() && act.innerText.trim()) seen.set(act.innerText.trim(), `footer t=${t}`);
    for (const li of document.querySelectorAll('#log > li')) seen.set(li.innerText.replace(/\\s+/g, ' ').trim(), `log t=${t}`);
  }
  const bad = [], empty = [];
  for (const [text, where] of seen) {
    // the line's time (… 12.3s) and an update's (・更新 …s) are the screen's own
    const words = text.replace(/・?更新 [\\d.]+s/g, '').replace(/[\\d.]+s$/, '').trim();
    if (hasRecordWords(words)) bad.push(`${where}: ${words.slice(0, 160)}`);
    if (!words) empty.push(where);
  }
  return { n: seen.size, bad, empty, all: [...seen.keys()] };
}"""


def events_of(run: str) -> list[dict]:
    return [json.loads(line) for line in (SHOWCASE / run / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def spread(run: str, k: int = SEEN) -> list[float]:
    ts = sorted({e["t"] for e in events_of(run)})
    step = max(1, len(ts) // k)
    return sorted(set(ts[::step] + ts[-1:]))


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class PlainLogBrowserTest(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.server = make_server("127.0.0.1", 0, cls.tmp, quiet=True, saved_dir=SHOWCASE, launcher=False)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def page(self, run: str, dev: bool = False):
        page = self.browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.base}/?run={run}&tab=bench&pace=0&t=0{'&dev=1' if dev else ''}", wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=120000)
        page.evaluate("window.hiraku.ready")
        return page, errors

    def test_every_event_s_footer_and_log_lines(self):
        total = 0
        for run in RUNS:
            with self.subTest(run=run):
                page, errors = self.page(run)
                try:
                    got = page.evaluate(EVERY, events_of(run))
                finally:
                    page.close()
                self.assertEqual(errors, [], run)
                total += got["n"]
                self.assertEqual(got["bad"], [], f"{run}: {len(got['bad'])} of {got['n']} texts")
                self.assertEqual(got["empty"], [], run)
        print(f"\n[plain log] {total} footer and log texts over every event of {len(RUNS)} runs")
        self.assertGreaterEqual(len(RUNS), 4)

    def test_the_screen_s_footer_and_log(self):
        total = 0
        for run in RUNS:
            with self.subTest(run=run):
                page, errors = self.page(run)
                try:
                    got = page.evaluate(SCREEN, spread(run))
                finally:
                    page.close()
                self.assertEqual(errors, [], run)
                total += got["n"]
                self.assertEqual(got["bad"], [], f"{run}: {len(got['bad'])} of {got['n']} texts")
                self.assertEqual(got["empty"], [], run)
        print(f"\n[plain log] {total} texts on the screen over {len(RUNS)} runs")

    def test_dev_shows_the_record_s_words(self):
        for run in WITH_IDS:
            with self.subTest(run=run):
                page, errors = self.page(run, dev=True)
                try:
                    got = page.evaluate(SCREEN, spread(run, 10))
                finally:
                    page.close()
                self.assertEqual(errors, [], run)
                self.assertTrue(any(RECORD_ID.search(x) for x in got["all"]), run)
                self.assertTrue(got["bad"], run)


if __name__ == "__main__":
    unittest.main()
