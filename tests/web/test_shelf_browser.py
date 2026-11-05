"""The A4 tool shelf in a headless browser: the shelf from /api/shelf (a small stand-in
load, not the solvers) with a run's tool.checked records laid over it.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed (see test_browser.py).
The tools, checks and formulas below are shape examples: their numbers are made up.
"""

import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku import contract
from hiraku.webserve.server import SAMPLE_EVENTS, make_server
from hiraku.webserve.shelf import Shelf
from tests.web.test_browser import ENABLED, sync_playwright


def check(case, expected, got, passed, unit="mm"):
    return {"case": case, "quantity": "deflection", "expected": expected, "got": got, "unit": unit,
            "tolerance_rel": 0.02, "pass": passed, "source": f"{case} (shape example)"}


def tool(name, title, domain, base, tests, checks, formulas=(), origin="builtin", usable=True, applies_to=()):
    out = {"name": name, "title": title, "domain": domain, "base": base, "tests": list(tests), "origin": origin,
           "usable": usable, "checks": checks, "formulas": list(formulas)}
    if applies_to:
        out["applies_to"] = list(applies_to)
    return out


SHELF_TOOLS = [
    tool("frame_static", "フレームの静的", "strength", "K1", ["R1"],
         [check("simply supported beam", 1.25, 1.25, True), check("cantilever", 4.0, 4.04, True)], ["frame_static.rail_bending"],
         applies_to=["ladder_4w", "space_frame_utv"]),
    tool("step_climb", "段差越え", "powertrain", "K1", ["P1"], [check("rigid wheel", 0.5, 0.5, True, "1")], ["step_climb.wheel_force_rigid"]),
    tool("tilt", "転倒の角度", "handling", "K2", ["R4"], [check("box on a table", 30.0, 30.1, True, "deg"), check("two masses", 25.0, None, False, "deg")],
         usable=False),
]
FORMULAS = [
    {"formula_id": "frame_static.rail_bending", "title": "レールの曲げ", "domain": "strength", "expression": "sigma = M / Z",
     "inputs": {}, "outputs": {}, "assumptions": [], "source": "beam theory (shape example)"},
    {"formula_id": "step_climb.wheel_force_rigid", "title": "剛い輪が段の角に当たったときの力の比", "domain": "powertrain",
     "expression": "F/W = d / (r - h)", "inputs": {}, "outputs": {}, "assumptions": [], "source": "statics (shape example)"},
]
# the generators' titles for the kinds of design (designgen.ARCHETYPES); space_frame_utv is left out
# so that a kind without a title shows as its key
ARCHETYPES = {"ladder_4w": "はしご形フレームの 4 輪車 (形の例)", "care_bed": "介護ベッド"}
RUN_TOOLS = [
    tool("rollbar", "ロールバー", "crash", "K1", ["C3"], [check("portal frame", 2.0, 2.01, True)], ["rollbar.portal_sway"], origin="claude"),
    tool("roof_crush", "屋根の潰れ", "crash", "K1", ["C4"], [check("plate", 3.0, 3.9, False)], origin="claude", usable=False),
]
CODE_1 = "def run():\n    return 1  # first try\n"
CODE_2 = "def run():\n    return 2  # second try\n"


def shelf_events(focus=False):
    """The sample run, then the two tools Claude wrote (and, with focus, screen.focus A4)."""
    events = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    base = {"run_id": events[0]["run_id"], "ts": "2026-11-02T20:30:20.000+09:00"}
    more = [{"kind": "tool.checked", "agent": "cae.crash", "domain": "crash", "data": t} for t in RUN_TOOLS]
    if focus:
        more.append({"kind": "screen.focus", "agent": "lead", "data": {"screen": "A4"}})
    t = events[-1]["t"]
    for ev in more:
        t += 0.5
        events.append({"seq": 0, "t": t, **base, **ev})
    for i, ev in enumerate(events):
        ev["seq"] = i
    return events


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class ShelfBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.run_id = json.loads(SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()[0])["run_id"]
        tools_dir = cls.tmp / "runs" / cls.run_id / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "rollbar-1.py").write_bytes(CODE_1.encode("utf-8"))
        (tools_dir / "rollbar-2.py").write_bytes(CODE_2.encode("utf-8"))
        cls.release = threading.Event()

        def load():
            cls.release.wait(20)
            return {"tools": SHELF_TOOLS, "formulas": FORMULAS, "archetypes": ARCHETYPES}

        cls.server = make_server("127.0.0.1", 0, cls.tmp / "runs", quiet=True, saved_dir=None, launcher=False, shelf=Shelf(load))
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])

    @classmethod
    def tearDownClass(cls):
        cls.release.set()
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp)

    def open(self, events):
        for ev in events:
            self.assertEqual(contract.errors("event", ev), [], ev["kind"])
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        # The code of a run's tool: the newest of the versions GET /api/runs/<id>/tools lists, so no
        # 404 is asked for; any 404 is kept apart (the browser logs it) and checked by URL.
        page.not_found = []
        page.on("response", lambda r: r.status == 404 and page.not_found.append(r.url.split("/api/", 1)[-1]))
        page.on("console", lambda m: m.type == "error" and "status of 404" not in m.text and errors.append(m.text))
        page.goto(f"{self.base}/?run=sample&pace=0&t=0")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=30000)
        page.evaluate("window.hiraku.ready")
        page.evaluate("async (events) => { await window.hiraku.load(events, 1e6); }", events)
        return page, errors

    TREE = """() => [...document.querySelectorAll('#view-shelf .sh-node')].map((n) => ({
        key: n.dataset.branch, head: n.querySelector('.sh-head').textContent,
        tools: [...n.querySelectorAll('.sh-tool')].map((r) => r.dataset.tool) }))"""

    def shown_tools(self, page):
        return sorted(t for b in page.evaluate(self.TREE) for t in b["tools"])

    def test_the_shelf(self):
        page, errors = self.open(shelf_events())
        page.click('.tab[data-tab="A4"]')
        page.wait_for_function("document.querySelector('.tab.on')?.dataset.tab === 'A4'")
        first = page.evaluate("""() => ({ shelf: !document.getElementById('view-shelf').hidden,
            bench: !document.getElementById('view-bench').hidden, wide: document.getElementById('main').classList.contains('wide'),
            note: document.querySelector('#view-shelf .sh-note').textContent })""")
        self.assertEqual((first["shelf"], first["bench"], first["wide"]), (True, False, True))
        self.assertIn("棚を読み込んでいます", first["note"])
        # while the shelf loads, the run's own tools are already there
        self.assertEqual(self.shown_tools(page), ["rollbar", "roof_crush"])

        self.release.set()
        page.wait_for_function("document.querySelectorAll('#view-shelf .sh-tool').length === 5", timeout=15000)
        self.assertTrue(page.evaluate("document.querySelector('#view-shelf .sh-note').hidden"))

        # from the domains: branches in the order of DOMAINS, each with its counts
        tree = page.evaluate(self.TREE)
        self.assertEqual([b["key"] for b in tree], ["handling", "powertrain", "crash", "strength"])
        heads = {b["key"]: b["head"] for b in tree}
        self.assertIn("操安", heads["handling"])
        self.assertIn("1 ツール", heads["handling"])
        self.assertIn("照合が通った 0", heads["handling"])
        self.assertIn("衝突", heads["crash"])
        self.assertIn("2 ツール", heads["crash"])
        self.assertIn("照合が通った 1", heads["crash"])
        self.assertEqual({b["key"]: b["tools"] for b in tree}["crash"], ["rollbar", "roof_crush"])
        self.assertEqual(page.text_content("#view-shelf .sh-sum"), "5 ツール・照合が通った 3")

        # one row: name, tests, the formula's title with its id, the base, the checks; marks
        row = page.evaluate("""() => { const r = (n) => document.querySelector(`#view-shelf .sh-tool[data-tool=${n}]`);
            return { fs: r('frame_static').textContent, fsK0: r('frame_static').querySelector('.sh-k0').textContent,
                     fsChk: r('frame_static').querySelector('.sh-chk').textContent,
                     tiltChk: r('tilt').querySelector('.sh-chk').className, tiltOff: r('tilt').classList.contains('is-off'),
                     rb: r('rollbar').querySelector('.sh-name').textContent, rc: r('roof_crush').querySelector('.sh-name').textContent }; }""")
        self.assertIn("フレームの静的", row["fs"])
        self.assertIn("R1", row["fs"])
        self.assertIn("K1", row["fs"])
        self.assertEqual(row["fsK0"], "レールの曲げframe_static.rail_bending")
        self.assertEqual(row["fsChk"], "照合 2 / 2")
        self.assertIn("wait", row["tiltChk"])
        self.assertTrue(row["tiltOff"])
        self.assertIn("Claude が作った", row["rb"])
        self.assertNotIn("使えない", row["rb"])
        self.assertIn("Claude が作った", row["rc"])
        self.assertIn("使えない", row["rc"])

        # the kinds of design a tool can analyse (applies_to): one line, the generator's title up to " (",
        # a kind without a title as its key; no line for a tool without applies_to (any design)
        applies = page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('#view-shelf .sh-tool')].map((r) => {
            const a = r.querySelector('.sh-applies');
            return [r.dataset.tool, a && { text: a.textContent, kinds: [...a.querySelectorAll('.sh-arch')].map((s) => [s.dataset.kind, s.textContent, s.title]) }];
        }))""")
        self.assertEqual(applies["frame_static"]["text"], "使える設計: はしご形フレームの 4 輪車・space_frame_utv")
        self.assertEqual(applies["frame_static"]["kinds"], [["ladder_4w", "はしご形フレームの 4 輪車", "はしご形フレームの 4 輪車 (形の例)"],
                                                            ["space_frame_utv", "space_frame_utv", ""]])
        for name in ("step_climb", "tilt", "rollbar", "roof_crush"):
            self.assertIsNone(applies[name], name)

        # from the bases
        page.click('#view-shelf .sh-mode button[data-mode="base"]')
        tree = page.evaluate(self.TREE)
        self.assertEqual([b["key"] for b in tree], ["K1", "K2"])
        self.assertIn("3D の梁", tree[0]["head"])
        self.assertIn("4 ツール", tree[0]["head"])
        self.assertIn("照合が通った 3", tree[0]["head"])
        self.assertEqual(tree[1]["tools"], ["tilt"])

        # a branch closes and opens
        page.click('#view-shelf .sh-node[data-branch="K1"] .sh-head')
        self.assertEqual(page.evaluate(self.TREE)[0]["tools"], [])
        page.click('#view-shelf .sh-node[data-branch="K1"] .sh-head')
        self.assertEqual(len(page.evaluate(self.TREE)[0]["tools"]), 4)
        page.click('#view-shelf .sh-mode button[data-mode="domain"]')

        # the filters
        def filtered(key):
            page.click(f'#view-shelf .sh-filter button[data-filter="{key}"]')
            return self.shown_tools(page)

        self.assertEqual(filtered("claude"), ["rollbar", "roof_crush"])
        self.assertEqual(filtered("checking"), ["tilt"])
        self.assertEqual(filtered("unusable"), ["roof_crush", "tilt"])
        self.assertEqual(len(filtered("all")), 5)

        # the search box: part of a name, or of a test id
        page.fill("#view-shelf .sh-search", "r1")
        self.assertEqual(self.shown_tools(page), ["frame_static"])
        page.fill("#view-shelf .sh-search", "roll")
        self.assertEqual(self.shown_tools(page), ["rollbar"])
        page.fill("#view-shelf .sh-search", "zzz")
        self.assertIn("条件に合うツールがありません", page.text_content("#view-shelf .sh-tree"))
        page.fill("#view-shelf .sh-search", "")

        # a builtin tool's checks
        page.click('#view-shelf .sh-tool[data-tool="frame_static"]')
        table = page.evaluate("""() => [...document.querySelectorAll('#view-shelf .sh-checks tbody tr')].map((tr) =>
            [...tr.children].map((td) => td.textContent))""")
        self.assertEqual(table, [
            ["simply supported beam", "deflection", "1.25", "1.25", "mm", "2.0 %", "合格 ✓", "simply supported beam (shape example)"],
            ["cantilever", "deflection", "4", "4.04", "mm", "2.0 %", "合格 ✓", "cantilever (shape example)"],
        ])
        self.assertIsNone(page.query_selector("#view-shelf .sh-code"))  # no code for a builtin tool

        # a check still waiting for its value, and a failed one
        page.click('#view-shelf .sh-tool[data-tool="tilt"]')
        rows = page.evaluate("[...document.querySelectorAll('#view-shelf .sh-checks tbody tr')].map((tr) => [tr.className, tr.children[3].textContent])")
        self.assertEqual(rows, [["ok", "30.1"], ["wait", "照合中"]])
        page.click('#view-shelf .sh-tool[data-tool="roof_crush"]')
        page.wait_for_function("document.querySelector('#view-shelf .sh-detail')?.dataset.tool === 'roof_crush' && !/読んでいます/.test(document.querySelector('#view-shelf .sh-right').textContent)")
        self.assertEqual(page.evaluate("document.querySelector('#view-shelf .sh-checks tbody tr').className"), "bad")
        self.assertIn("コードがありません", page.text_content("#view-shelf .sh-right"))

        # the code a run wrote for its tool: the last of tools/<name>-<n>.py
        page.click('#view-shelf .sh-tool[data-tool="rollbar"]')
        page.wait_for_selector("#view-shelf .sh-code", timeout=10000)
        self.assertEqual(page.text_content("#view-shelf .sh-code"), CODE_2)
        self.assertIn("tools/rollbar-2.py", page.text_content("#view-shelf .sh-detail"))

        self.assertEqual(page.not_found, [])  # (before: tools/rollbar-3.py and tools/roof_crush-1.py, guessed)
        self.assertEqual(errors, [])
        page.close()

    def test_screen_focus_opens_the_shelf(self):
        page, errors = self.open(shelf_events(focus=True))
        page.wait_for_function("document.querySelector('.tab.on')?.dataset.tab === 'A4'")
        self.assertFalse(page.evaluate("document.getElementById('view-shelf').hidden"))
        page.click('.tab[data-tab="bench"]')
        page.wait_for_function("document.querySelector('.tab.on')?.dataset.tab === 'bench'")
        self.assertTrue(page.evaluate("document.getElementById('view-shelf').hidden"))
        self.assertEqual(errors, [])
        page.close()


if __name__ == "__main__":
    unittest.main()
