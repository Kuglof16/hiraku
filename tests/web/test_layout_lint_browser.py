"""The layout lint over the screen's main pages, at three window widths.

Opens each page of SCREENS at each of WIDTHS and runs tests/web/layout_lint.js in it (what it
looks at, and the data-lint marks that let a thing off, are written at the top of that file):
pressable things whose boxes cross, one-line things that wrap, text running out of its box, and a
line holding one letter only. Any problem fails the test with all of them listed: the rule, a short
selector, the words, the page and the width. A new page is one more row in SCREENS.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed, as test_browser.
The runs here are the contract sample and the shape examples of the other browser tests.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from hiraku.webserve.server import SAMPLE_EVENTS, make_server

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None
REPO_DIR = Path(__file__).resolve().parents[2]
LINT_JS = (Path(__file__).with_name("layout_lint.js")).read_text(encoding="utf-8")

WIDTHS = ((1280, 720), (1920, 1080), (900, 506))  # 16:9; the stage is scaled to the window

SAVED = "20261102-200000-saved"  # the contract sample as a saved run (the list's おすすめ)
DONE = "20261102-201000-done"  # the contract sample in runs/: 終わった
GOING = "20261102-202000-going"  # the contract sample without its end: 書き込み中
DOMAINS = "20261102-203000-domains"  # every domain (tests.web.domains_fixture)
HEARING = "20261102-204000-hearing"  # the sample with the hearing asked (A2)
REPORT_RUN = "20261102-205000-report"  # the sample with a report (test_cut_n17_browser.REPORT)
ASSEMBLY = "20261102-206000-assembly"  # the sample with an assembly order (test_assembly_browser)
JOINTS = "20261102-207000-joints"  # joints: the frozen offroad-4w v1 alone (test_joints_browser.joints_events)

READY = "window.hiraku && window.hiraku.ready"


@dataclass(frozen=True)
class Screen:
    name: str
    path: str  # after the server's address
    wait: str = READY  # a JS expression that is truthy once the page is drawn (with READY, window.hiraku.ready is awaited too)
    root: str = "body"  # what is looked at (a dialog on top: only the dialog)
    server: str = "saved"  # "saved": with a saved run (the list's おすすめ replays it); "unsaved": without
    # Subtrees not looked at on this page: another department's, already found and reported to
    # them. Each entry says what the lint found there. (None so far: A1, A2 and A5, the intake
    # department's js/intake/, had no problem at 1280, 1920 or 900 when this test was written.)
    known: tuple[str, ...] = field(default=())


SCREENS = (
    Screen("一覧", "?list=1", wait="document.querySelector('#launch .lreq')", root="#runlist"),
    Screen("一覧 (保存なし)", "?list=1", wait="document.querySelector('#launch .pick')", root="#runlist", server="unsaved"),
    Screen("一覧 (dev)", "?list=1&dev=1", wait="document.querySelector('#launch .lreq') && document.querySelector('#runlist table')", root="#runlist"),
    Screen("作業台", f"?run={DOMAINS}&tab=bench&pace=0&t=1e6"),
    Screen("全分野", f"?run={DOMAINS}&tab=grid&pace=0&t=1e6"),
    # the agents' pets with their bubbles out (js/agent_pets.js): 空力's judgement at 17.0 s, the lead's line at 17.5 s
    Screen("作業台 (吹き出し)", f"?run={DOMAINS}&tab=bench&pace=0&t=17.6"),
    Screen("全分野 (吹き出し)", f"?run={DOMAINS}&tab=grid&pace=0&t=17.6"),
    Screen("A1", "?new=1", wait="document.querySelector('#view-intake textarea')"),
    Screen("A2", f"?run={HEARING}&tab=A2&live=0&pace=0&t=1e6"),
    Screen("A5", f"?run={HEARING}&tab=A5&live=0&pace=0&t=1e6"),
    Screen("報告", f"cut.html?cut=n17&run={REPORT_RUN}&t=8.7"),
    Screen("ツールの棚", f"?run={DOMAINS}&tab=A4&pace=0&t=1e6"),
    Screen("組み立て", f"?run={ASSEMBLY}&tab=A7&pace=0&t=1e6"),
    Screen("作業台 (接合)", f"?run={JOINTS}&tab=bench&pace=0&t=1e6&on=joints"),  # joints: the toggle 接合 on, its legend
    Screen("組み立て (接合)", f"?run={ASSEMBLY}&tab=A7&pace=0&t=1e6&on=joints"),  # joints: A7's own 接合 switch and legend
    Screen("見せる", f"?run={DOMAINS}&present=1&pace=0&t=1e6"),
)


def _write_run(root: Path, run_id: str, events: list[dict]) -> None:
    (root / run_id).mkdir(parents=True)
    for i, ev in enumerate(events):
        ev["seq"] = i
    (root / run_id / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")


def build_runs(tmp: Path, repo: Path = REPO_DIR) -> tuple[Path, Path]:
    """The runs the pages open: (runs dir, saved dir)."""
    from hiraku import contract
    from hiraku.webserve.launch import read_requirements
    from tests.web.domains_fixture import domain_events
    from tests.web.test_assembly_browser import assembly_events
    from tests.web.test_cut_n17_browser import REPORT, report_events
    from tests.web.test_joints_browser import joints_events  # joints:

    sample = lambda: [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    # the runs in runs/ with the longest want of the repository's requirements files, as a run started
    # from it has: longer than the sample's, so the list's columns are as narrow as with real runs
    want = max((read_requirements(p)["want"] for p in sorted((repo / "data" / "requirements").glob("*.yaml"))), key=len)

    def started_from_file(events):
        events[0]["data"]["want"] = want
        return events

    runs, saved = tmp / "runs", tmp / "showcase"
    _write_run(saved, SAVED, sample())
    _write_run(runs, DONE, started_from_file(sample()))
    _write_run(runs, GOING, started_from_file(sample()[:-1]))
    _write_run(runs, DOMAINS, domain_events())
    events = sample()
    asked = json.loads((contract.EXAMPLES_DIR / "hearing_asked.json").read_text(encoding="utf-8"))
    events.insert(1, {"seq": 0, "ts": events[0]["ts"], "t": events[0]["t"] + 0.1, "run_id": events[0]["run_id"],
                      "kind": "hearing.asked", "agent": "lead", "data": asked})
    _write_run(runs, HEARING, events)
    _write_run(runs, REPORT_RUN, report_events(REPORT))
    _write_run(runs, ASSEMBLY, assembly_events())
    _write_run(runs, JOINTS, joints_events())  # joints:
    return runs, saved


def start_servers(tmp: Path, repo: Path = REPO_DIR):
    """The screen's servers on build_runs' runs, with and without the saved run (Screen.server), each
    with a launcher on the repository's requirements and demo runs and no API key (nothing is
    started here). Returns (servers, {name: base URL})."""
    from hiraku.webserve.launch import Launcher

    runs, saved = build_runs(tmp, repo)
    servers, bases = [], {}
    for name, saved_dir in (("saved", saved), ("unsaved", None)):
        lau = Launcher(runs, repo / "data" / "requirements", env={})
        server = make_server("127.0.0.1", 0, runs, quiet=True, saved_dir=saved_dir, launcher=lau)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        bases[name] = f"http://127.0.0.1:{server.server_address[1]}"
    return servers, bases


def open_screen(browser, bases: dict, screen: Screen, size: tuple[int, int]):
    page = browser.new_page(viewport={"width": size[0], "height": size[1]})
    page.goto(f"{bases[screen.server]}/{screen.path}")
    page.wait_for_function(screen.wait, timeout=60000)
    if screen.wait == READY:  # (the list and A1 never resolve it: they play nothing)
        page.evaluate("window.hiraku.ready")
    page.evaluate("document.fonts.ready.then(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))))")
    page.wait_for_timeout(300)  # the list fills #launch after its own fetch
    return page


def lint(browser, bases: dict, screens=SCREENS, widths=WIDTHS, shots: Path | None = None) -> list[dict]:
    """Every problem on every page at every width (each with its page and width)."""
    out = []
    for screen in screens:
        for size in widths:
            page = open_screen(browser, bases, screen, size)
            try:
                for p in page.evaluate(LINT_JS, {"root": screen.root, "skip": list(screen.known)}):
                    out.append({"screen": screen.name, "width": size[0], **p})
                if shots is not None:
                    shots.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(shots / f"{screen.name}-{size[0]}.png"))
            finally:
                page.close()
    return out


def describe(p: dict) -> str:
    extra = " ".join(f"{k}={p[k]}" for k in ("by", "lines", "line", "split", "with", "withText", "overflow") if k in p)
    return f"[{p['screen']} @{p['width']}] {p['rule']}: {p['sel']} 「{p['text']}」 {extra}".rstrip()


PLANTED = """<!doctype html><html lang="ja"><body style="font: 16px sans-serif">
<div style="position: relative; height: 30px"><span class="wordmark" id="name">HIRAKU</span>
  <span class="vbox" id="over" style="position: absolute; left: 10px; top: 0">版 2 赤 2</span>
  <span class="wordmark" id="clear" style="position: absolute; left: 300px; top: 0">HIRAKU</span></div>
<div style="position: relative; height: 40px">
  <button id="b1" style="position: absolute; left: 0; top: 0; width: 120px">再生</button>
  <button id="b2" style="position: absolute; left: 60px; top: 10px; width: 120px">動画の配置で</button>
</div>
<button id="wrapped" style="width: 40px">終わった</button>
<div id="narrow" style="width: 60px; white-space: nowrap">形の見本 (数値は計算結果ではない)</div>
<div id="cut" style="width: 60px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis">省略記号で切った長い文</div>
<p id="lonely" style="width: 52px; font-size: 16px; line-height: 1.5">終わった</p>
<p id="aside" style="width: 7.5em; font-size: 16px">(無料・Claude なし)</p>
<p id="allowed" data-lint="wrap" style="width: 64px">終わった</p>
<button data-lint="wrap" style="width: 40px">まだ長い札</button>
</body></html>"""


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class LayoutLintBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.servers, cls.bases = start_servers(cls.tmp)
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        for server in cls.servers:
            server.shutdown()
            server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_lint_finds_planted_breaks(self):
        """Each rule on a page made to break it; the ellipsis and data-lint="wrap" are let off."""
        page = self.browser.new_page(viewport={"width": 800, "height": 450})
        page.set_content(PLANTED)
        got = page.evaluate(LINT_JS, {})
        page.close()
        found = {(p["rule"], p["sel"]) for p in got}
        self.assertIn(("overlap", "#b1"), found)
        self.assertIn(("wrap", "#wrapped"), found)
        self.assertIn(("overflow", "#narrow"), found)
        self.assertIn(("lonely", "#lonely"), found)
        lonely = next(p for p in got if p["rule"] == "lonely" and p["sel"] == "#lonely")
        self.assertEqual(lonely["line"], "た")
        self.assertIn(("paren", "#aside"), found)
        # a label drawn over another text (the grid's version box over the HIRAKU name)
        self.assertIn(("cover", "#over"), found)
        self.assertNotIn("#clear", {p["sel"] for p in got if p["rule"] == "cover"})
        for quiet in ("#cut", "#allowed"):
            self.assertNotIn(quiet, {p["sel"] for p in got})
        self.assertFalse([p for p in got if p["text"] == "まだ長い札"])

    def test_no_layout_breaks_on_the_screens(self):
        problems = lint(self.browser, self.bases)
        if problems:
            self.fail(f"{len(problems)} layout problems:\n" + "\n".join(describe(p) for p in problems))


if __name__ == "__main__":
    unittest.main()
