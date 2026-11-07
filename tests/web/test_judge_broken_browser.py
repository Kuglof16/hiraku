"""Places a judge's first look found broken (the review of the published page), on the saved runs:

1. ④'s banner on a run that stopped with a NG left points at what is there (the viewer), not at
   「その場で走らせる」 (a button only under ?dev=1 with a server)
2. ③ opened at the end: no part's name tag shows until 名前 is pressed
3. the hooks' one line is not drawn over ④ and ⑤ (it still is on the other tabs: test_topbar_browser)
4. ③'s legend at 1280x720 at the end: nothing cut by its frame (the source line gives way)
5. ③ at the end of the Claude run: R1's box and the right panel show the last verdict (OK, as ④),
   not 計算中 / 判定はまだ (a prediction written after the last comparison got no analysis)
6. ⑤'s head: the steps with only a tool's or a hand's ✕ counted as a caution outside the verdicts
   (the record judges neither quantity), not 「✕ のある手順 N つ」
7. a run that stopped before its first design: the list says why in one line, the opened run says
   「この記録は v1 を作る前に止まりました (...)」 over the 3D, and the footer says it stopped
8. 守るルール at the request's moment: pressing a rule shows it on the right (no page error)

Records: showcase/ (20261105-160725 the Claude run to v7, 20261105-091217 stopped with a NG left,
20261104-001809 the serving robot stopped before v1) and the contract's sample. The numbers checked
come from the records (read here). Browser tests: python -m tests.web.browser (needs Playwright).
"""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import make_server
from tests.web.test_browser import ENABLED, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
CLAUDE = "20261105-160725-offroad-4w"
STOPPED_NG = "20261105-091217-offroad-4w"
ROBOT = "20261104-002518-offroad-4w"  # (a showcase run that stopped before v1)


def events(run):
    return [json.loads(line) for line in (ROOT / "showcase" / run / "events.jsonl").read_text(encoding="utf-8").splitlines()]


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class JudgeBrokenBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        (cls.tmp / "runs").mkdir()
        cls.server = make_server("127.0.0.1", 0, cls.tmp / "runs", quiet=True, saved_dir=ROOT / "showcase", launcher=False)
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

    def open(self, query, size=(1440, 900)):
        page = self.browser.new_page(viewport={"width": size[0], "height": size[1]})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.base}/?pets=0&{query}", wait_until="domcontentloaded")
        if "list=1" not in query:
            page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=120000)
            page.evaluate("window.hiraku.ready")
        self.addCleanup(page.close)
        return page, errors

    def frames(self, page, n=3):
        page.evaluate("(n) => new Promise((r) => { const f = (k) => k ? requestAnimationFrame(() => f(k - 1)) : r(); f(n); })", n)

    def seek(self, page, t):
        page.evaluate("async (t) => { await window.hiraku.seek(t); await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))); }", t)

    def tab(self, page, key):
        page.click(f'.tab[data-tab="{key}"]')
        page.wait_for_function(f"document.querySelector('.tab.on')?.dataset.tab === '{key}'")
        self.frames(page)

    # 1 ------------------------------------------------------------------------------------------
    def test_banner_points_at_what_is_there(self):
        page, errors = self.open(f"run={STOPPED_NG}&tab=grid&t=1e6")
        page.wait_for_function("!document.getElementById('grid-banner').hidden", timeout=60000)
        text = page.evaluate("document.getElementById('grid-banner').innerText")
        self.assertEqual(errors, [])
        self.assertIn("NG が", text)
        self.assertNotIn("その場で走らせる", text)
        self.assertIn("ビューア", text)
        self.assertEqual(page.locator(".launch-link").count(), 0)

    # 2 ------------------------------------------------------------------------------------------
    def test_no_part_names_until_names_is_pressed(self):
        page, errors = self.open(f"run={CLAUDE}&tab=bench&t=1e6")
        page.wait_for_selector('#toggles button[data-toggle="names"]', timeout=120000)
        page.wait_for_timeout(3000)  # (the design's parts are built after the first frames)
        self.frames(page, 4)
        q = "[...document.querySelectorAll('.lbl-part')].filter((e) => e.checkVisibility()).length"
        before = page.evaluate(q)
        page.click('#toggles button[data-toggle="names"]')
        page.wait_for_function(f"{q} > 0", timeout=60000)
        after = page.evaluate(q)
        self.assertEqual(errors, [])
        self.assertEqual(before, 0)
        self.assertGreater(after, 0)

    # 3 ------------------------------------------------------------------------------------------
    def test_no_hooks_line_over_grid_and_assembly(self):
        page, errors = self.open("run=sample&pace=0&t=0&tab=bench")
        self.seek(page, 1e6)
        t_hook = page.evaluate("() => window.hiraku.player.state.hooks.denied[0].t")
        shown = "(() => { const l = document.getElementById('hookline'); return !l.hidden && l.checkVisibility(); })()"
        seen = {}
        for key in ("grid", "A7", "A4"):
            self.tab(page, key)
            self.seek(page, t_hook + 1.0)
            seen[key] = page.evaluate(shown)
        self.assertEqual(errors, [])
        self.assertEqual(seen, {"grid": False, "A7": False, "A4": True})

    # 4 ------------------------------------------------------------------------------------------
    def test_legend_fits_its_frame(self):
        page, errors = self.open(f"run={CLAUDE}&tab=bench&t=1e6", size=(1280, 720))
        page.wait_for_function("(() => { const l = document.getElementById('legend'); return !l.hidden && l.querySelector('.bar'); })()", timeout=120000)
        self.frames(page, 4)
        got = page.evaluate("(() => { const l = document.getElementById('legend'), s = l.querySelector(':scope > .src');"
                            " return { sh: l.scrollHeight, ch: l.clientHeight, srcHidden: s ? s.hidden : null, title: l.title }; })()")
        self.assertEqual(errors, [])
        self.assertLessEqual(got["sh"], got["ch"] + 1, got)
        if got["srcHidden"]:
            self.assertTrue(got["title"].startswith("色の元"), got)

    # 5 ------------------------------------------------------------------------------------------
    def test_the_last_verdict_at_the_end(self):
        evs = events(CLAUDE)
        last = [e["data"] for e in evs if e["kind"] == "verdict.recorded" and e["data"].get("load_case_id") == "R1-step100"][-1]
        page, errors = self.open(f"run={CLAUDE}&tab=bench&t=1e6")
        page.wait_for_function("document.querySelector('#tests .test[data-lc=\"R1-step100\"]')", timeout=60000)
        page.click('#tests .test[data-lc="R1-step100"]')
        self.frames(page, 4)
        got = page.evaluate("""(() => ({ state: document.querySelector('#tests .test[data-lc="R1-step100"]').dataset.state,
          busy: [...document.querySelectorAll('#tests .test')].filter((b) => b.dataset.state === 'busy').map((b) => b.dataset.lc),
          right: document.querySelector('.chain, #chain, .rp').innerText }))()""")
        self.assertEqual(errors, [])
        self.assertEqual(got["state"], "ok" if last["pass"] else "ng")
        self.assertEqual(got["busy"], [])
        self.assertNotIn("判定はまだ", got["right"])
        self.assertIn("OK" if last["pass"] else "NG", got["right"])

    # 6 ------------------------------------------------------------------------------------------
    def test_assembly_cautions_said_as_cautions(self):
        evs = events(CLAUDE)
        judged = {e["data"].get("quantity") for e in evs if e["kind"] == "verdict.recorded"}
        self.assertFalse(judged & {"steps_without_tool_clearance", "steps_out_of_reach"})
        page, errors = self.open(f"run={CLAUDE}&tab=A7&t=1e6")
        page.wait_for_function("document.querySelectorAll('#asm-host .asm-steps li').length > 0", timeout=120000)
        self.frames(page, 4)
        got = page.evaluate("""(() => { const rows = [...document.querySelectorAll('#asm-host .asm-steps li.has-ng')];
          return { head: document.getElementById('tab-head-A7').innerText,
                   reach: rows.filter((li) => !li.querySelector('.ck-no_interference.is-ng')).length,
                   hard: rows.filter((li) => li.querySelector('.ck-no_interference.is-ng')).length }; })()""")
        self.assertEqual(errors, [])
        self.assertEqual(got["hard"], 0)
        self.assertGreater(got["reach"], 0)
        self.assertIn(f"工具や手が届きにくい手順 {got['reach']} (判定には入らない注意)", got["head"])
        self.assertNotIn(" つ", got["head"])

    # 7 ------------------------------------------------------------------------------------------
    def test_a_run_stopped_before_v1(self):
        fin = [e for e in events(ROBOT) if e["kind"] == "run.finished"][-1]["data"]
        why = fin["stop_reason"].split("。")[0].strip()
        want = f"この記録は v1 を作る前に止まりました ({why})"
        page, errors = self.open("list=1")
        page.wait_for_selector(f'tr[data-run="{ROBOT}"]', timeout=60000)
        row = page.evaluate(f"document.querySelector('tr[data-run=\"{ROBOT}\"] [data-role=\"stop-why\"]')?.title ?? null")
        done_row = page.evaluate(f"document.querySelector('tr[data-run=\"{CLAUDE}\"] [data-role=\"stop-why\"]')")
        run, errs = self.open(f"run={ROBOT}")
        run.wait_for_function("document.getElementById('stopped-note') && !document.getElementById('stopped-note').hidden", timeout=60000)
        note = run.evaluate("document.getElementById('stopped-note').innerText")
        foot = run.evaluate("document.getElementById('activity').textContent")
        self.assertEqual(errors + errs, [])
        self.assertEqual(row, want)
        self.assertIsNone(done_row)
        self.assertEqual(note, want)
        self.assertIn("実行はここで止まりました", foot)
        self.assertNotIn("実行が終わった", foot)

    # 8 ------------------------------------------------------------------------------------------
    def test_a_rule_pressed_at_the_request(self):
        page, errors = self.open(f"run={CLAUDE}&tab=A1")
        self.tab(page, "A5")
        page.wait_for_selector('.in-rule[data-rule="SY-04"]', timeout=60000)
        page.click('.in-rule[data-rule="SY-04"] .in-rtext')
        self.frames(page)
        right = page.evaluate("document.querySelector('.in-right').innerText")
        self.assertEqual(errors, [])
        self.assertIn("SY-04", right)
        self.assertNotIn("左のルールを選ぶと", right)


if __name__ == "__main__":
    unittest.main()
