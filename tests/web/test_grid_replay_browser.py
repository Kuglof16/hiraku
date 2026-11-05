"""④ 解析結果 (?tab=grid) on its own, and one domain's analyses replayed in it:

- ④ is not tied to ③: on ④ only ④'s tab is marked and no domain chip is ringed by ③'s load case;
  what is pressed in ④ leaves ③'s own pick of a test as it was;
- a tile whose domain has analyses replays them in the tab (the 3D, the 予測 → 解析 → 照合 cards and
  the domain's tests, the play bar from its first prediction to its last comparison, stopping there);
  ④ stays marked; 「× 格子に戻る」 goes back to the grid and to where the replay was; ?domain= opens
  it, and ?t= draws the same frame as a seek; ?video=1 hides the buttons;
- a run whose tests the requirements file chose: the tiles not picked say which tests it checks;
- a run followed from the screen: when it is over (run.finished read, or the child gone without it)
  the top no longer says 記録の途中 and the stop button goes, though the replay is still behind.

The record is the contract's sample (strength: prediction 5.0 s, comparison 6.5 s at pace=0).
Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

from hiraku.webserve.server import SAMPLE_EVENTS, make_server

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None

T0, T1 = 5.0, 6.5  # the sample's strength: its prediction, its comparison (pace=0: the recorded t)

STATE = """() => ({
  on: [...document.querySelectorAll('.tab.on')].map((b) => b.dataset.tab),
  focus: [...document.querySelectorAll('#chips .chip.focus')].map((c) => c.textContent),
  bench: !document.getElementById('view-bench').hidden, grid: !document.getElementById('view-grid').hidden,
  head: document.getElementById('domain-head') && !document.getElementById('domain-head').hidden ? document.getElementById('domain-head').innerText : null,
  dom: document.querySelector('#domain-head .dh-dom')?.textContent ?? null,
  tests: [...document.querySelectorAll('#tests button.test')].map((b) => b.dataset.lc),
  testOn: document.querySelector('#tests button.on')?.dataset.lc ?? null,
  cards: ['card-pred', 'card-ana', 'card-cmp'].map((id) => document.getElementById(id).innerText),
  time: document.getElementById('pb-time').textContent,
  playhead: window.hiraku.player.playhead, playing: window.hiraku.player.playing,
  url: location.search,
})"""


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class GridReplayBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server("127.0.0.1", 0, "runs-none", quiet=True, saved_dir=None)
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

    def open(self, query):
        page = self.browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.base}/?run=sample&pace=0&pets=0&{query}")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
        page.evaluate("window.hiraku.ready")
        page.evaluate("() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
        self.addCleanup(page.close)
        return page, errors

    def state(self, page):
        page.evaluate("() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
        return page.evaluate(STATE)

    def test_the_tab_is_named_and_alone(self):
        page, errors = self.open("tab=grid&t=1e6")
        self.assertEqual(page.inner_text('.tab[data-tab="grid"] .tab-name'), "解析結果")
        self.assertIn("解析結果", page.get_attribute('.tab[data-tab="grid"]', "title"))
        got = self.state(page)
        # ④ only, no chip ringed by ③'s load case
        self.assertEqual((got["on"], got["focus"], got["grid"], got["bench"]), (["grid"], [], True, False))
        # ③: its load case's domain is ringed; ③'s own pick of a test
        page.click('.tab[data-tab="bench"]')
        page.click('#tests button[data-lc="R1-step100"]')
        got = self.state(page)
        self.assertEqual((got["on"], got["focus"], got["testOn"]), (["bench"], ["強度信頼性"], "R1-step100"))
        # ④ again: nothing of ③ marked; a domain replayed and its 自動 pressed, then closed
        page.click('.tab[data-tab="grid"]')
        self.assertEqual(self.state(page)["focus"], [])
        page.click('.tile[data-domain="strength"]')
        page.click('#tests button[data-lc=""]')
        self.assertEqual(self.state(page)["on"], ["grid"])
        page.click("#domain-head .dh-close")
        page.click('.tab[data-tab="bench"]')
        got = self.state(page)
        # ③'s pick is as it was (④'s 自動 was ④'s own)
        self.assertEqual((got["on"], got["testOn"]), (["bench"], "R1-step100"))
        self.assertEqual(errors, [])

    def test_a_tile_replays_its_domain_in_the_tab(self):
        page, errors = self.open("tab=grid&t=1e6")
        tiles = page.evaluate("() => [...document.querySelectorAll('.tile')].map((t) => [t.dataset.domain, t.classList.contains('can-replay'), !!t.querySelector('.replay-tag')])")
        self.assertIn(["strength", True, True], tiles)
        self.assertTrue(all(not can for d, can, _ in tiles if d != "strength"), tiles)  # (the others have nothing to replay)
        page.click('.tile[data-domain="strength"]')
        got = self.state(page)
        self.assertEqual(got["on"], ["grid"])  # ④ stays marked
        self.assertEqual((got["bench"], got["grid"], got["dom"]), (True, False, "強度信頼性"))
        self.assertIn("domain=strength", got["url"])
        self.assertEqual(got["focus"], ["強度信頼性"])
        self.assertEqual(got["tests"], ["R1-step100"])
        self.assertGreaterEqual(got["playhead"], T0 - 1e-6)
        self.assertLess(got["playhead"], T1)
        self.assertIn("予測", got["cards"][0])
        # it plays to the domain's last comparison and stops there
        page.wait_for_function("!window.hiraku.player.playing", timeout=20000)
        got = self.state(page)
        self.assertAlmostEqual(got["playhead"], T1, places=6)
        self.assertEqual(got["time"], f"{T1 - T0:.1f} / {T1 - T0:.1f} 秒")
        self.assertTrue(got["cards"][2].strip(), got["cards"])  # the comparison card
        # ▶ at the end plays it again from its first prediction
        page.click("#pb-play")
        self.assertLess(page.evaluate("window.hiraku.player.playhead"), T1)
        # 格子に戻る: the grid, and the replay where it was (the end)
        page.click("#domain-head .dh-close")
        got = self.state(page)
        self.assertEqual((got["on"], got["bench"], got["grid"], got["head"]), (["grid"], False, True, None))
        self.assertNotIn("domain=", got["url"])
        self.assertAlmostEqual(got["playhead"], page.evaluate("window.hiraku.player.duration"), places=3)
        self.assertEqual(errors, [])

    def test_the_url_draws_the_same_frame(self):
        a, errors_a = self.open("tab=grid&domain=strength&t=6.0")
        b, errors_b = self.open("tab=grid&t=1e6")
        b.click('.tile[data-domain="strength"]')
        b.evaluate("async () => { await window.hiraku.seek(6.0); }")
        ga, gb = self.state(a), self.state(b)
        for k in ("on", "focus", "bench", "grid", "head", "tests", "cards", "time", "playhead"):
            self.assertEqual(ga[k], gb[k], k)
        self.assertEqual(ga["time"], "1.0 / 1.5 秒")
        self.assertEqual(errors_a + errors_b, [])

    def test_video_hides_the_buttons(self):
        page, errors = self.open("tab=grid&t=1e6&video=1")
        self.assertFalse(page.is_visible('.tile[data-domain="strength"] .replay-tag'))
        page, errors2 = self.open("tab=grid&domain=strength&t=6.0&video=1")
        self.assertTrue(page.is_visible("#domain-head .dh-dom"))
        self.assertFalse(page.is_visible("#domain-head .dh-close"))
        self.assertEqual(errors + errors2, [])

    def test_tests_chosen_by_the_file_said_on_the_tiles_not_picked(self):
        events = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
        page, errors = self.open("tab=grid&t=0")
        read = """async (evs) => { await window.hiraku.load(evs, 1e6);
            await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
            return Object.fromEntries([...document.querySelectorAll('.tile')].map((t) => [t.dataset.domain, t.querySelector('.sum').textContent])); }"""
        plain = page.evaluate(read, events)
        self.assertEqual(plain["crash"], "")
        for ev in events:
            for p in ev.get("data", {}).get("domain_picks", []) or []:
                p["reason"] = "要件のファイルが試験 R1 段差 (見本) を選んだ"
        chosen = page.evaluate(read, events)
        line = "この実行は R1 だけを確かめます (要件のファイルが選んだ試験)"
        for d in ("crash", "nvh", "thermal", "handling", "powertrain", "aero", "assembly"):
            self.assertEqual(chosen[d], line, d)
        self.assertNotEqual(chosen["strength"], line)
        self.assertEqual(errors, [])


# A stand-in for `python -m hiraku run`: writes the sample's events without run.finished, prints its
# folder, then on the test's signal either writes run.finished and stays up ("finish") or ends without it ("die").
_CHILD = r"""
import json, pathlib, sys, time
src, runs, run_id, how = sys.argv[1], pathlib.Path(sys.argv[2]), sys.argv[3], sys.argv[4]
evs = [json.loads(x) for x in pathlib.Path(src).read_text(encoding="utf-8").splitlines() if x.strip()]
for e in evs:  # (the record's run_id is its folder's, as a real run writes it)
    e["run_id"] = run_id
    if isinstance(e.get("data"), dict) and "run_id" in e["data"]:
        e["data"]["run_id"] = run_id
lines = [json.dumps(e, ensure_ascii=False) + "\n" for e in evs]
fin = [x for x in lines if '"run.finished"' in x]
rest = [x for x in lines if x not in fin]
d = runs / run_id
d.mkdir(parents=True)
(d / "events.jsonl").write_text("".join(rest[:3]), encoding="utf-8")
print("run folder:", d, flush=True)
go = runs / f"go-{run_id}"  # the test's signal (made once the page follows the run)
for _ in range(600):
    if go.exists():
        break
    time.sleep(0.05)
# the rest at once (as a scripted run writes it): the paced replay falls behind the record
with open(d / "events.jsonl", "a", encoding="utf-8") as f:
    f.write("".join(rest[3:] + (fin if how == "finish" else [])))
if how == "finish":
    time.sleep(30)
"""


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class RunEndBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def serve(self, how):
        from hiraku.webserve.launch import Launcher

        tmp = Path(tempfile.mkdtemp())
        (tmp / "requirements").mkdir()
        (tmp / "requirements" / "offroad-4w.yaml").write_text("product: offroad-4w\n", encoding="utf-8")
        run_id = f"20261106-120000-offroad-4w-{how}"

        def command(req, mode, runs, intake_events=None):
            return [sys.executable, "-c", _CHILD, str(SAMPLE_EVENTS), str(runs), run_id, how]

        reader = lambda path: {"product": "offroad-4w", "want": "見本の要件", "cost_limit_usd": 2.0, "round_limit": 1}  # noqa: E731
        lau = Launcher(tmp / "runs", tmp / "requirements", env={}, command=command, reader=reader, wait_s=30,
                       demo_runs_file=tmp / "no-demo-runs.yaml")
        server = make_server("127.0.0.1", 0, tmp / "runs", quiet=True, saved_dir=None, launcher=lau)
        threading.Thread(target=server.serve_forever, daemon=True).start()

        def close():
            server.shutdown()
            server.server_close()
            if lau.proc and lau.proc.poll() is None:
                lau.proc.kill()
                lau.proc.wait()
            shutil.rmtree(tmp, ignore_errors=True)

        self.addCleanup(close)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        req = urllib.request.Request(f"{base}/api/launch", data=json.dumps({"requirements": "offroad-4w.yaml", "mode": "scripted"}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req).read()
        for _ in range(100):
            info = json.load(urllib.request.urlopen(f"{base}/api/launch"))
            if (info.get("running") or {}).get("run_id") == run_id:
                break
            time.sleep(0.1)
        else:
            self.fail(f"the stand-in did not start: {info}")
        return base, run_id, tmp / "runs" / f"go-{run_id}"

    def follow(self, how):
        base, run_id, go = self.serve(how)
        page = self.browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        self.addCleanup(page.close)
        # paced (the default): the replay of a followed run lags its record
        page.goto(f"{base}/?run={run_id}&pets=0")
        try:
            page.wait_for_selector("#mode .stop-btn", timeout=9000)  # started here and going: it can be stopped
        except Exception:
            self.fail(f"no stop button: {page.inner_text('#mode')!r} {page.evaluate('() => ({live: window.hiraku?.player.live, st: window.hiraku?.state().status, run: window.hiraku?.state().runId})')}")
        self.assertIn("実行中を追いかけ", page.inner_text("#mode"))
        go.write_text("go", encoding="utf-8")  # now the stand-in ends (with or without run.finished)
        return page, errors

    def check_over(self, page, errors):
        page.wait_for_function("!document.querySelector('#mode .stop-btn')", timeout=20000)
        got = page.evaluate("() => ({ mode: document.getElementById('mode').innerText, behind: window.hiraku.player.playhead < window.hiraku.player.duration, status: window.hiraku.state().status })")
        self.assertIn("実行は終わった", got["mode"], got)
        self.assertNotIn("記録の途中", got["mode"], got)
        self.assertNotIn("実行中を追いかけ", got["mode"], got)
        self.assertEqual(errors, [])
        return got

    def test_run_finished_read_while_the_replay_is_behind(self):
        page, errors = self.follow("finish")
        got = self.check_over(page, errors)
        self.assertTrue(got["behind"], got)  # (the replay still plays what is left)
        self.assertEqual(got["status"], "running")

    def test_the_child_ended_without_run_finished(self):
        page, errors = self.follow("die")
        self.check_over(page, errors)
        self.assertFalse(page.evaluate("window.hiraku.player.live"))


if __name__ == "__main__":
    unittest.main()
