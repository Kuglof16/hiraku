"""① and ② tell one moment (the screen of a saved run, served by hiraku.webserve):

- ① opened as it is (?run=…&tab=A1, no ?t=): the replay stops at the request's moment (the domains
  picked, no design yet): the top row says v — and 依頼の時点 (not 記録の途中), no footer line, ①'s
  いま names the picked domains and its cards are picked (調べる / not 候補 nor 考えています); ▶ plays on
  and the top row goes with it (v1 once the first design is in);
- ② over its questions: the pet of the agent that asked them (the record's lead: まとめ), a heading
  that says when they were asked, the record's hearings in time order (the first one, before the
  first design, said so); the record's words as the screen says them (v2, not 版 2; the
  requirement's name, not Q54; the part's name, not battery); an answered question shows its answer,
  not the choices as buttons nor 答えました; 次 says which versions came after the answer;
- a record with no run.finished says so in the top row (この記録は途中で終わっています (v… まで)).

Record: the saved Claude run (showcase/20261105-160725-offroad-4w): domains.picked (the code's picks
from the requirements file, no flags' reasons) and the first hearing (answered with the file's values,
nothing asked) before v1; questions at v1 and at v2 (the battery and rule AS-01).
Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import PETS_FILE, make_server

try:
    from tests.web.pages import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None

ROOT = Path(__file__).resolve().parents[2]
RUN = "20261105-160725-offroad-4w"
CUT = "20261106-120000-cut-short"  # the same record up to v6, no run.finished

TOP = """() => ({
  openedAt: document.documentElement.dataset.openedAt ?? null,
  pill: document.getElementById('pill-version').textContent,
  mode: document.getElementById('mode').innerText,
  activity: document.getElementById('activity').innerText.trim(),
  now: document.getElementById('tab-head-intake')?.innerText ?? '',
  cards: Object.fromEntries([...document.querySelectorAll('.in-pane.a1 .in-card')].map((c) => [c.dataset.domain, [...c.classList].find((k) => ['lit', 'off', 'draft', 'wait'].includes(k))])),
  head: document.querySelector('.in-pane.a1 .in-headtext')?.innerText ?? '',
  playhead: window.hiraku.player.playhead, playing: window.hiraku.player.playing,
  ver: window.hiraku.state().currentVersion,
})"""

A2 = """() => {
  const seen = (e) => !!e && e.checkVisibility() && e.getBoundingClientRect().height > 0;
  const rq = document.querySelector('.in-pane.a2 .in-rq');
  return {
    title: document.querySelector('#a2-when .a2-when-title')?.innerText ?? null,
    rounds: [...document.querySelectorAll('#a2-when .a2-rounds li')].map((li) => [li.className, li.innerText]),
    askers: [...document.querySelectorAll('.a2-askers .ipet')].map((p) => [p.dataset.agent, p.querySelector('.ipet-name').textContent]),
    rqText: rq?.querySelector('.in-qtext')?.innerText ?? null,
    rqChoices: [...(rq?.querySelectorAll('.in-choice') ?? [])].filter(seen).length,
    rqState: seen(rq?.querySelector('.in-state')),
    rqAnswer: rq?.querySelector('.in-answer')?.innerText ?? null,
    head: document.getElementById('tab-head-intake')?.innerText ?? '',
  };
}"""


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class OneMomentBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        saved = cls.tmp / "saved"
        shutil.copytree(ROOT / "showcase" / RUN, saved / RUN)
        # the same record cut before v7 (no run.finished): a record that ends half way
        lines = (ROOT / "showcase" / RUN / "events.jsonl").read_text(encoding="utf-8").splitlines()
        cut = []
        for line in lines:
            e = json.loads(line)
            if e["kind"] == "design.created" and e["data"]["design_version"] == 7:
                break
            e["run_id"] = CUT
            cut.append(json.dumps(e, ensure_ascii=False))
        (saved / CUT).mkdir()
        (saved / CUT / "events.jsonl").write_text("\n".join(cut) + "\n", encoding="utf-8")
        (cls.tmp / "runs").mkdir()
        cls.server = make_server("127.0.0.1", 0, cls.tmp / "runs", quiet=True, saved_dir=saved, launcher=False, pets_file=PETS_FILE)
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

    def open(self, query, pets="0"):
        page = self.browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.base}/?pets={pets}&{query}", wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=120000)
        page.evaluate("window.hiraku.ready")
        self.addCleanup(page.close)
        return page, errors

    def read(self, page, script=TOP):
        page.evaluate("() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
        return page.evaluate(script)

    def test_a1_opens_at_the_request(self):
        page, errors = self.open(f"run={RUN}&tab=A1")
        got = self.read(page)
        self.assertEqual(got["openedAt"], "request")
        self.assertFalse(got["playing"])
        self.assertIsNone(got["ver"])
        self.assertTrue(got["pill"].startswith("設計はまだ"), got["pill"])  # (no design yet: in words, not v—)
        self.assertIn("依頼の時点", got["mode"])
        self.assertNotIn("記録の途中", got["mode"])
        self.assertEqual(got["activity"], "")  # no footer line of a later moment
        self.assertIn("強度信頼性・組み立て", got["now"])
        self.assertEqual(got["cards"]["strength"], "lit")
        self.assertEqual({d for d, c in got["cards"].items() if c != "off"}, {"strength"})
        self.assertNotIn("考えています", got["head"])
        self.assertNotIn("候補", got["head"])
        self.assertIn("選びました", got["head"])
        # ▶ (③'s play bar): the top row goes on with the replay (v1 once the first design is in)
        page.click('.tab[data-tab="bench"]')
        page.click("#pb-play")
        page.wait_for_function("document.getElementById('pill-version').textContent.startsWith('v1')", timeout=30000)
        got = self.read(page)
        self.assertIn("v1 の時点", got["mode"])
        self.assertEqual(errors, [])

    def test_a1_with_t_opens_as_before(self):
        page, errors = self.open(f"run={RUN}&tab=A1&t=1e6")
        got = self.read(page)
        self.assertIsNone(got["openedAt"])
        self.assertTrue(got["pill"].startswith("v7"), got["pill"])
        self.assertIn("完了", got["mode"])
        self.assertEqual(errors, [])

    def test_a2_says_when_and_who_asked(self):
        page, errors = self.open(f"run={RUN}&tab=A2&t=1e6", pets="1")
        page.wait_for_function("document.documentElement.dataset.agentPets", timeout=60000)
        got = self.read(page, A2)
        self.assertEqual(got["title"], "v2 の途中で まとめ が聞いたこと")
        # the hearings in time order: the first (before the first design), at v1, at v2 (the one shown)
        self.assertEqual([c for c, _ in got["rounds"]], ["past", "past", "shown"])
        self.assertTrue(got["rounds"][0][1].startswith("実行の前 (最初のヒアリング): 質問なし"), got["rounds"])
        self.assertTrue(got["rounds"][1][1].startswith("v1 の途中: 確認 6 件・ルール 1 件 → 答え 6 件・設計を直す"), got["rounds"])
        self.assertTrue(got["rounds"][2][1].startswith("v2 の途中: ルール 1 件 → 設計を直す"), got["rounds"])
        if PETS_FILE.is_file():
            self.assertEqual(got["askers"], [["lead", "まとめ"]])
        # the record's sentence in the screen's words
        self.assertIn("v2 で電池を 2 つのパックに分けました", got["rqText"])
        self.assertNotIn("版 2", got["rqText"])
        self.assertNotIn("Q54", got["rqText"])
        self.assertIn("航続に効く", got["rqText"])
        # answered: the answer's line only
        self.assertEqual((got["rqChoices"], got["rqState"]), (0, False))
        self.assertIn("設計を直す", got["rqAnswer"])
        self.assertIn("答えのあと v3 から v7 を作った", got["head"])
        self.assertNotIn("③ 解析へ", got["head"])
        self.assertEqual(errors, [])

    def test_a2_at_the_request_has_the_first_hearing(self):
        page, errors = self.open(f"run={RUN}&tab=A1")
        page.click('.tab[data-tab="A2"]')
        page.wait_for_function("document.querySelector('#a2-when .a2-when-title')")
        got = self.read(page, A2)
        self.assertEqual(got["title"], "実行の前に決めたこと (最初のヒアリング): 聞かずに進めました")
        self.assertEqual([c for c, _ in got["rounds"]], ["shown", "later", "later"])
        self.assertTrue(self.read(page)["pill"].startswith("設計はまだ"))
        self.assertEqual(errors, [])

    def test_a_record_cut_short_says_so(self):
        page, errors = self.open(f"run={CUT}&tab=bench&t=40")
        got = self.read(page)
        self.assertIn("この記録は途中で終わっています (v6 まで)", got["mode"])
        self.assertNotIn("記録の途中", got["mode"])
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
