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
from hiraku.webserve.server import SAMPLE_EVENTS, formula_titles, make_server
from hiraku.webserve.shelf import Shelf
from tests.web.test_browser import ENABLED, sync_playwright


def check(case, expected, got, passed, unit="mm", quantity="deflection", case_ja=None, source_ja=None):
    out = {"case": case, "quantity": quantity, "expected": expected, "got": got, "unit": unit,
           "tolerance_rel": 0.02, "pass": passed, "source": f"{case} (shape example)"}
    # case_ja / source_ja: optional (contract bf6712fa); only on the shelf's tools here, not in the run's events
    if case_ja:
        out["case_ja"] = case_ja
    if source_ja:
        out["source_ja"] = source_ja
    return out


def tool(name, title, domain, base, tests, checks, formulas=(), origin="builtin", usable=True, applies_to=()):
    out = {"name": name, "title": title, "domain": domain, "base": base, "tests": list(tests), "origin": origin,
           "usable": usable, "checks": checks, "formulas": list(formulas)}
    if applies_to:
        out["applies_to"] = list(applies_to)
    return out


SHELF_TOOLS = [
    tool("frame_static", "フレームの静的", "strength", "K1", ["R1"],
         [check("simply supported beam", 1.25, 1.25, True, quantity="stopping_distance",
                case_ja="両端を支えた梁 (形の例)", source_ja="梁の式 (形の例)"),
          check("cantilever", 4.0, 4.04, True)], ["frame_static.rail_bending"],
         applies_to=["ladder_4w", "space_frame_utv"]),
    tool("step_climb", "段差越え", "powertrain", "K1", ["P1"], [check("rigid wheel", 0.5, 0.5, True, "1")], ["step_climb.wheel_force_rigid"]),
    tool("tilt", "転倒の角度", "handling", "K2", ["R4"], [check("box on a table", 30.0, 30.1, True, "deg"), check("two masses", 25.0, None, False, "deg")],
         usable=False),
]
FORMULAS = [
    {"formula_id": "frame_static.rail_bending", "title": "レールの曲げ", "domain": "strength", "expression": "sigma = M / Z",
     "inputs": {}, "outputs": {}, "assumptions": [], "source": "beam theory (shape example)"},
    {"formula_id": "step_climb.wheel_force_rigid", "title": "剛い輪が段の角に当たったときの力の比", "domain": "powertrain",
     "expression": "F_x / W = d / (r - h)^2 + mu_0 * sqrt(theta)", "inputs": {}, "outputs": {}, "assumptions": [], "source": "statics (shape example)"},
]
# the generators' titles for the kinds of design (designgen.ARCHETYPES); space_frame_utv is left out
# so that a kind without a title shows as its key
ARCHETYPES = {"ladder_4w": "はしご形フレームの 4 輪車 (形の例)", "care_bed": "介護ベッド"}
RUN_TOOLS = [
    tool("rollbar", "ロールバー", "crash", "K1", ["C3"], [check("portal frame", 2.0, 2.01, True)], ["rollbar.portal_sway"], origin="claude"),
    # a real K0 formula id: its title comes from /api/formulas while the shelf is still being built
    tool("roof_crush", "屋根の潰れ", "crash", "K1", ["C4"], [check("plate", 3.0, 3.9, False)], ["braking.stopping_distance"],
         origin="claude", usable=False),
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
        # the same events as a run on disk, for a page opened at ?run=<id>&t=
        cls.events = shelf_events()
        (tools_dir.parent / "events.jsonl").write_bytes(
            "".join(json.dumps(ev, ensure_ascii=False) + "\n" for ev in cls.events).encode("utf-8"))
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

    def open(self, events, extra=""):
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
        page.goto(f"{self.base}/?run=sample&pace=0&t=0{extra}", wait_until="domcontentloaded")
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
        # while the shelf loads, the run's own tools are already there, their formulas by the titles of /api/formulas
        self.assertEqual(self.shown_tools(page), ["rollbar", "roof_crush"])
        # ... but no count until the shelf is built (only this run's tools are known before)
        self.assertEqual(page.text_content("#view-shelf .sh-sum"), "棚を確かめています…")
        self.assertEqual(page.evaluate("document.querySelectorAll('#view-shelf .sh-n, #view-shelf .sh-nok').length"), 0)
        title = {f["formula_id"]: f["title"] for f in formula_titles()["formulas"]}["braking.stopping_distance"]
        page.wait_for_function("(t) => document.querySelector('#view-shelf .sh-tool[data-tool=roof_crush] .sh-k0').textContent === t", arg=title)

        self.release.set()
        page.wait_for_function("document.querySelectorAll('#view-shelf .sh-tool').length === 5", timeout=15000)
        self.assertTrue(page.evaluate("document.querySelector('#view-shelf .sh-note').hidden"))

        # from the domains: branches in the order of DOMAINS, each with its counts
        tree = page.evaluate(self.TREE)
        self.assertEqual([b["key"] for b in tree], ["handling", "powertrain", "crash", "strength"])
        heads = {b["key"]: b["head"] for b in tree}
        self.assertIn("操安", heads["handling"])
        self.assertIn("1 ツール", heads["handling"])
        self.assertIn("確かめた 0", heads["handling"])
        self.assertIn("衝突", heads["crash"])
        self.assertIn("2 ツール", heads["crash"])
        self.assertIn("確かめた 1", heads["crash"])
        self.assertEqual({b["key"]: b["tools"] for b in tree}["crash"], ["rollbar", "roof_crush"])
        self.assertEqual(page.text_content("#view-shelf .sh-sum"), "5 ツール・確かめた 3")

        # one row: name, tests, the formula's title (no id; a formula without a title by its id), the base,
        # the checks; marks
        row = page.evaluate("""() => { const r = (n) => document.querySelector(`#view-shelf .sh-tool[data-tool=${n}]`);
            return { fs: r('frame_static').textContent, fsK0: r('frame_static').querySelector('.sh-k0').textContent,
                     fsName: r('frame_static').querySelector('.sh-name').textContent,
                     rbK0: r('rollbar').querySelector('.sh-k0').textContent,
                     fsChk: r('frame_static').querySelector('.sh-chk').textContent,
                     tiltChk: r('tilt').querySelector('.sh-chk').className, tiltOff: r('tilt').classList.contains('is-off'),
                     rb: r('rollbar').querySelector('.sh-name').textContent, rc: r('roof_crush').querySelector('.sh-name').textContent }; }""")
        self.assertIn("フレームの静的", row["fs"])
        self.assertIn("R1", row["fs"])
        self.assertIn("3D の梁", row["fs"])  # (the step by its name; K1 only under ?dev=1)
        self.assertEqual(row["fsK0"], "レールの曲げ")
        self.assertEqual(row["fsName"], "フレームの静的")
        self.assertEqual(row["rbK0"], "rollbar.portal_sway")  # not in the shelf's formulas: no title, the id as it is
        self.assertEqual(row["fsChk"], "確かめ 2 / 2")
        self.assertIn("noval", row["tiltChk"])  # a check the tool gave no value for
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
        self.assertIn("確かめた 3", tree[0]["head"])
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
        # 確かめを通っていない: a check that did not pass (roof_crush) or gave no value (tilt)
        self.assertEqual(filtered("notok"), ["roof_crush", "tilt"])
        self.assertEqual([b.text_content() for b in page.query_selector_all("#view-shelf .sh-filter button")],
                         ["すべて", "Claude が作った", "確かめを通っていない", "使えない"])
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

        # a builtin tool's checks: case_ja / source_ja when the record has them, else the English as written;
        # the quantity in words (one QUANTITY_JA has no word for: its key)
        page.click('#view-shelf .sh-tool[data-tool="frame_static"]')
        table = page.evaluate("""() => [...document.querySelectorAll('#view-shelf .sh-checks tbody tr')].map((tr) =>
            [...tr.children].map((td) => td.textContent))""")
        self.assertEqual(table, [
            ["両端を支えた梁 (形の例)", "停止距離", "1.25", "1.25", "mm", "2.0 %", "✓", "梁の式 (形の例)"],
            ["cantilever", "deflection", "4", "4.04", "mm", "2.0 %", "✓", "cantilever (shape example)"],
        ])
        card = page.evaluate("""() => { const d = document.querySelector('#view-shelf .sh-detail');
            return { head: d.querySelector('header').textContent, h4: d.querySelector('h4').textContent,
                     th: [...d.querySelectorAll('.sh-checks th')].map((x) => x.textContent),
                     dt: [...d.querySelectorAll('dt')].map((x) => x.textContent),
                     fl: [...d.querySelectorAll('.sh-fl')].map((x) => x.textContent),
                     text: document.getElementById('view-shelf').textContent,
                     more: d.querySelectorAll('.sh-more').length }; }""")
        self.assertEqual(card["head"], "フレームの静的確かめ 2 / 2")
        self.assertEqual(card["h4"], "答えの分かっている問題で確かめた結果 (2 問、通った 2)")
        self.assertEqual(page.text_content("#view-shelf .sh-detail .sh-what"),
                         "ツールを使う前に、式で答えが出る簡単な問題を解かせ、答えと合うかを確かめた結果です。設計を確かめる手計算とは別のものです。")
        self.assertEqual(card["th"], ["確かめに使った問題", "比べた値", "式で出した答え", "ツールの答え", "単位", "ずれてよい幅", "結果", "答えの出し方"])
        self.assertIn("手計算の式", card["dt"])
        self.assertNotIn("手計算の段", card["dt"])
        self.assertEqual(card["fl"], ["レールの曲げ式を見るσ = M / Z"])
        # 式を見る: shut at first; open, the expression with its signs (EXPR_SIGNS), not rewritten
        see = "#view-shelf .sh-fl[data-formula='frame_static.rail_bending'] details.sh-see"
        self.assertFalse(page.evaluate(f"document.querySelector(\"{see}\").open"))
        self.assertFalse(page.is_visible(f"{see} .sh-expr"))
        page.click(f"{see} > summary")
        self.assertTrue(page.is_visible(f"{see} .sh-expr"))
        self.assertEqual(page.text_content(f"{see} .sh-expr"), "σ = M / Z")
        # without ?dev=1 no ids and no くわしく anywhere on the shelf (the tools' names, the formulas' ids,
        # the English beside a Japanese case or source)
        self.assertEqual(card["more"], 0)
        for word in ("frame_static", "step_climb", "rail_bending", "stopping_distance",
                     "simply supported beam", "照合 ", "合格", "答えの分かっている例", "許す幅"):
            self.assertNotIn(word, card["text"], word)
        self.assertIsNone(page.query_selector("#view-shelf .sh-code"))  # no code for a builtin tool

        # a check the tool gave no value for (got null, pass false: 値が出ない, ×), and a failed one
        page.click('#view-shelf .sh-tool[data-tool="tilt"]')
        rows = page.evaluate("[...document.querySelectorAll('#view-shelf .sh-checks tbody tr')].map((tr) => [tr.className, tr.children[3].textContent, tr.children[6].textContent])")
        self.assertEqual(rows, [["ok", "30.1", "✓"], ["noval", "値が出ない", "×"]])
        self.assertEqual(page.text_content("#view-shelf .sh-detail h4"), "答えの分かっている問題で確かめた結果 (2 問、通った 1、通っていない 1)")
        self.assertEqual(page.get_attribute("#view-shelf .sh-detail .sh-chk", "title"), "ツールの値が出ない問題がある")
        self.assertNotIn("確かめ中", page.text_content("#view-shelf"))

        # the signs: a name in full to its letter, a power raised, the part after "_" lowered, sqrt and *
        page.click('#view-shelf .sh-tool[data-tool="step_climb"]')
        see = "#view-shelf .sh-fl[data-formula='step_climb.wheel_force_rigid'] details.sh-see"
        page.click(f"{see} > summary")
        self.assertEqual(page.inner_html(f"{see} .sh-expr"), "F<sub>x</sub> / W = d / (r - h)² + μ<sub>0</sub> × √(θ)")
        # an open 式を見る stays open when the shelf draws again
        page.click('#view-shelf .sh-mode button[data-mode="domain"]')
        self.assertTrue(page.evaluate(f"document.querySelector(\"{see}\").open"))
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

    def test_the_shelf_under_dev(self):
        """?dev=1: the tool's name, the formulas' ids, the quantity's key and the English beside a
        Japanese case or source are there, each inside a くわしく (details.sh-more) of the card.
        (Named to run after test_the_shelf, which sees the shelf still loading.)"""
        self.release.set()
        page, errors = self.open(shelf_events(), "&dev=1")
        page.click('.tab[data-tab="A4"]')
        page.wait_for_function("document.querySelectorAll('#view-shelf .sh-tool').length === 5", timeout=15000)
        page.click('#view-shelf .sh-tool[data-tool="frame_static"]')
        got = page.evaluate("""() => { const d = document.querySelector('#view-shelf .sh-detail');
            const outside = (el) => { const c = el.cloneNode(true); c.querySelectorAll('.sh-more').forEach((m) => m.remove()); return c.textContent; };
            return { more: [...d.querySelectorAll('.sh-more')].map((m) => [m.querySelector('summary').textContent, m.textContent.replace('くわしく', '')]),
                     outside: outside(d), head: outside(d.querySelector('header')),
                     cells: [...d.querySelectorAll('.sh-checks tbody tr')].map((tr) => [...tr.children].map(outside)) }; }""")
        for _, inner in got["more"]:
            self.assertTrue(inner)
        inners = [inner for _, inner in got["more"]]
        self.assertEqual({s for s, _ in got["more"]}, {"くわしく"})
        for word in ("frame_static", "frame_static.rail_bending", "stopping_distance", "simply supported beam",
                     "simply supported beam (shape example)"):
            self.assertIn(word, inners, word)
            self.assertNotIn(word, got["outside"], word)
        self.assertEqual(got["head"], "フレームの静的確かめ 2 / 2")
        # a check without case_ja / source_ja: the English as written, with nothing folded
        self.assertEqual(got["cells"][1][0], "cantilever")
        self.assertEqual(got["cells"][0][0], "両端を支えた梁 (形の例)")
        # a くわしく left open stays open when the shelf draws again
        page.click('#view-shelf .sh-detail header .sh-more summary')
        page.click('#view-shelf .sh-mode button[data-mode="domain"]')
        self.assertTrue(page.evaluate("document.querySelector('#view-shelf .sh-detail header .sh-more').open"))
        self.assertEqual(errors, [])
        page.close()

    def test_url_picks_filter_and_tool(self):
        """A page opened at ?t= with &tab=A4&shelf=claude&tool=rollbar: when ready, the filter is
        Claude が作った, rollbar's card is open with its checks and its code, and nothing is still being
        read (the frame does not change afterwards). (Named to run after test_the_shelf.)"""
        self.release.set()
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        t = self.events[-1]["t"] + 1
        page.goto(f"{self.base}/?run={self.run_id}&pace=0&present=1&video=1&tab=A4&t={t}&shelf=claude&tool=rollbar")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=30000)
        page.evaluate("window.hiraku.ready")
        got = page.evaluate("""() => { const v = document.getElementById('view-shelf');
            return { shown: !v.hidden, filter: v.querySelector('.sh-filter button.on')?.dataset.filter,
                     tools: [...v.querySelectorAll('.sh-tool')].map((r) => r.dataset.tool),
                     sel: v.querySelector('.sh-tool.sel')?.dataset.tool, card: v.querySelector('.sh-detail')?.dataset.tool,
                     rows: [...v.querySelectorAll('.sh-checks tbody tr')].map((tr) => tr.className),
                     code: v.querySelector('.sh-code')?.textContent ?? null, note: v.querySelector('.sh-note').hidden,
                     sum: v.querySelector('.sh-sum').textContent, text: v.textContent }; }""")
        self.assertTrue(got["shown"])
        self.assertEqual(got["filter"], "claude")
        self.assertEqual(got["tools"], ["rollbar", "roof_crush"])
        self.assertEqual((got["sel"], got["card"]), ("rollbar", "rollbar"))
        self.assertEqual(got["rows"], ["ok"])
        self.assertEqual(got["code"], CODE_2)
        self.assertTrue(got["note"])
        self.assertEqual(got["sum"], "5 ツール・確かめた 3")
        self.assertNotIn("読んでいます", got["text"])
        # the card is scrolled to its checks (as far as it scrolls)
        top = page.evaluate("""() => { const box = document.querySelector('#view-shelf .sh-right');
            const at = Math.round(box.querySelector('.sh-detail h4').getBoundingClientRect().top - box.getBoundingClientRect().top);
            return { at, scrolled: box.scrollTop, end: box.scrollTop + box.clientHeight >= box.scrollHeight - 1 }; }""")
        self.assertTrue(abs(top["at"]) <= 2 or top["end"], top)
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
