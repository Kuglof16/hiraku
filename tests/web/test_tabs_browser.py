"""The top tabs (web/js/tabs.js, words in web/js/labels.js TAB_JA): ① to ⑤ in the order of use
with an arrow between, the tool shelf and the rules apart from them; a line icon as high as the
letters; what each tab is for in its title and, on a long press, in a bubble; the tabs with
nothing in this run faint (still pressable); ?dev=1 adds the screens' names inside HIRAKU; and
the band in one line, nothing crossing, at the layout lint's widths (900 too).

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed, as test_browser.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from tests.web.test_layout_lint_browser import ASSEMBLY, DOMAINS, ENABLED, HEARING, READY, WIDTHS, start_servers

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

FLOW = [("A1", "①", "作りたいもの"), ("A2", "②", "確認したいこと"), ("bench", "③", "解析"), ("grid", "④", "全分野の結果"), ("A7", "⑤", "組み立て")]
SIDE = [("A4", "ツールの棚"), ("A5", "守るルール")]
EMPTY = "この実行ではまだありません"
TIPS = {
    "A1": "作りたいものを書くと、HIRAKU が調べる点を選びます",
    "A2": "HIRAKU が足りない情報を聞きます。答えなくても標準の値で進めます",
    "bench": "予測と解析を照らし合わせる様子を 3D で見ます",
    "grid": "7 つの分野と組み立ての結果を一度に見ます",
    "A7": "組み立ての順番を 3D で確かめます",
    "A4": "Claude が作ったツールと、照合の結果を見ます",
    "A5": "設計のチェックに使うルールを見ます。ファイルを置くと足せます",
}

READ = """() => {
  const tab = (b) => {
    const ic = b.querySelector("svg.tab-ic");
    const name = b.querySelector(".tab-name");
    return { key: b.dataset.tab, no: b.querySelector(".tab-no")?.textContent ?? "", name: name?.textContent ?? "",
             title: b.title, empty: b.classList.contains("empty"), on: b.classList.contains("on"),
             opacity: getComputedStyle(b).opacity,
             icon: ic ? { h: ic.getBoundingClientRect().height, font: parseFloat(getComputedStyle(b).fontSize),
                          stroke: ic.getAttribute("stroke"), color: getComputedStyle(ic).color, text: getComputedStyle(name).color,
                          marks: ic.querySelectorAll("path, rect").length, external: ic.querySelectorAll("use, image").length } : null };
  };
  const flow = document.querySelector("#tabs .tabflow");
  const side = document.querySelector("#tabs .tabside");
  return { flow: [...flow.querySelectorAll("button.tab")].map(tab), side: [...side.querySelectorAll("button.tab")].map(tab),
           arrows: flow.querySelectorAll("svg.tab-arrow").length,
           gap: side.getBoundingClientRect().left - flow.getBoundingClientRect().right };
}"""

# the band's boxes (in window pixels): each tab, arrow, the side line and each chip
BAND = """() => {
  const r = (e) => { const b = e.getBoundingClientRect(); return { l: b.left, r: b.right, t: b.top, b: b.bottom, h: b.height }; };
  const row = document.querySelector(".tabsrow");
  return { row: r(row), stage: r(document.getElementById("stage")),
           tabs: [...row.querySelectorAll("button.tab")].map((b) => ({ key: b.dataset.tab, ...r(b), nameH: r(b.querySelector(".tab-name")).h, font: parseFloat(getComputedStyle(b).fontSize) })),
           arrows: [...row.querySelectorAll("svg.tab-arrow")].map(r),
           chips: [...row.querySelectorAll("#chips > *")].map(r), scale: r(document.getElementById("stage")).h / 1080 };
}"""


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class TabsBrowserTest(unittest.TestCase):
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

    def open(self, query, size=(1920, 1080), wait=READY):
        page = self.browser.new_page(viewport={"width": size[0], "height": size[1]})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.bases['saved']}/?{query}")
        page.wait_for_function(wait, timeout=60000)
        if wait == READY:
            page.evaluate("window.hiraku.ready")
        page.evaluate("document.fonts.ready.then(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))))")
        return page, errors

    def test_names_order_icons_and_titles(self):
        page, errors = self.open("run=sample&pace=0&t=1e6")
        got = page.evaluate(READ)
        page.close()
        self.assertEqual(errors, [])
        self.assertEqual([(t["key"], t["no"], t["name"]) for t in got["flow"]], FLOW)
        self.assertEqual([(t["key"], t["no"], t["name"]) for t in got["side"]], [(k, "", n) for k, n in SIDE])
        self.assertEqual(got["arrows"], len(FLOW) - 1)  # an arrow between the steps, none on the side
        self.assertGreater(got["gap"], 10)  # the side group set apart from the flow
        for t in got["flow"] + got["side"]:
            ic = t["icon"]
            self.assertIsNotNone(ic, t["key"])
            self.assertAlmostEqual(ic["h"], ic["font"], delta=0.5, msg=t["key"])  # as high as the letters (1em)
            self.assertEqual(ic["stroke"], "currentColor", t["key"])
            self.assertEqual(ic["color"], ic["text"], t["key"])  # the tab's own colour (from the CSS variables)
            self.assertGreater(ic["marks"], 0, t["key"])
            self.assertEqual(ic["external"], 0, t["key"])  # drawn inline, nothing fetched
        # the contract sample: a request, a design and results; no hearing, assembly, tools or rules
        empty = {t["key"]: t["empty"] for t in got["flow"] + got["side"]}
        self.assertEqual(empty, {"A1": False, "A2": True, "bench": False, "grid": False, "A7": True, "A4": True, "A5": True})
        for t in got["flow"] + got["side"]:
            self.assertEqual(t["title"], f"{EMPTY}\n{TIPS[t['key']]}" if t["empty"] else TIPS[t["key"]], t["key"])
            self.assertEqual(float(t["opacity"]) < 1, t["empty"], t["key"])

    def test_what_counts_as_something(self):
        """The marks follow the events applied so far: at the run's start only ① has something;
        the hearing run has ②, the assembly run has ⑤."""
        page, errors = self.open("run=sample&pace=0&t=0")
        start = {t["key"]: t["empty"] for t in page.evaluate(READ)["flow"]}
        page.evaluate("async () => { await window.hiraku.seek(1e6); }")
        end = {t["key"]: t["empty"] for t in page.evaluate(READ)["flow"]}
        page.close()
        self.assertEqual(start, {"A1": False, "A2": True, "bench": True, "grid": True, "A7": True})
        self.assertEqual((end["bench"], end["grid"]), (False, False))
        for run, key in ((HEARING, "A2"), (ASSEMBLY, "A7")):
            page, errs = self.open(f"run={run}&live=0&pace=0&t=1e6")
            got = {t["key"]: t["empty"] for t in page.evaluate(READ)["flow"]}
            page.close()
            self.assertFalse(got[key], run)
            errors += errs
        self.assertEqual(errors, [])

    def test_an_empty_tab_can_be_pressed(self):
        page, errors = self.open("run=sample&pace=0&t=1e6")
        page.click('.tab[data-tab="A7"]')
        page.wait_for_function("document.querySelector('.tab.on')?.dataset.tab === 'A7'")
        got = page.evaluate("() => ({ asm: !document.getElementById('view-asm').hidden, none: document.querySelector('#view-asm .asm-none')?.hidden })")
        page.close()
        self.assertEqual(errors, [])
        self.assertEqual(got, {"asm": True, "none": False})  # its screen says what would be there

    def test_long_press_bubble(self):
        """A finger held on a tab shows its sentence in a bubble under it and does not change the
        tab; a short tap changes it. The mouse has the title only."""
        page, errors = self.open("run=sample&pace=0&t=1e6&tab=bench")
        press = """async ([key, ms]) => {
          const b = document.querySelector(`.tab[data-tab="${key}"]`);
          const o = { bubbles: true, pointerType: "touch", isPrimary: true, pointerId: 7 };
          b.dispatchEvent(new PointerEvent("pointerdown", o));
          await new Promise((r) => setTimeout(r, ms));
          const tip = document.getElementById("tabtip");
          const seen = !tip.hidden;
          const tb = tip.getBoundingClientRect(), bb = b.getBoundingClientRect(), sb = document.getElementById("stage").getBoundingClientRect();
          b.dispatchEvent(new PointerEvent("pointerup", o));
          b.click();
          await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
          return { seen, text: tip.textContent, below: tb.top >= bb.bottom - 1, inStage: tb.left >= sb.left && tb.right <= sb.right,
                   on: document.querySelector(".tab.on")?.dataset.tab };
        }"""
        held = page.evaluate(press, ["A5", 700])
        tap = page.evaluate(press, ["grid", 50])
        mouse = page.evaluate("""async () => { const b = document.querySelector('.tab[data-tab="A1"]');
          b.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, pointerType: "mouse" }));
          await new Promise((r) => setTimeout(r, 700));
          const t = document.getElementById("tabtip"); return t.hidden || t.dataset.tab !== "A1"; }""")
        page.close()
        self.assertEqual(errors, [])
        self.assertEqual((held["seen"], held["text"], held["below"], held["inStage"], held["on"]), (True, f"{EMPTY}\n{TIPS['A5']}", True, True, "bench"))
        self.assertEqual((tap["seen"], tap["on"]), (False, "grid"))
        self.assertTrue(mouse)

    def test_dev_adds_the_inside_names(self):
        page, errors = self.open("run=sample&pace=0&t=1e6&dev=1")
        got = {t["key"]: t["title"] for t in (lambda g: g["flow"] + g["side"])(page.evaluate(READ))}
        page.close()
        self.assertEqual(errors, [])
        inside = {"A1": "A1", "A2": "A2", "bench": "A3 (bench)", "grid": "A6 (grid)", "A7": "A7", "A4": "A4", "A5": "A5"}
        for key, title in got.items():
            self.assertTrue(title.endswith(f"\n({inside[key]})"), (key, title))
            self.assertIn(TIPS[key], title)

    def test_the_band_in_one_line(self):
        """At each width of the layout lint (900 too), with the eight domain chips: every tab on one
        line, at one height, the tabs, arrows and chips not crossing, all inside the band."""
        for size in WIDTHS:
            for query in (f"run={DOMAINS}&tab=bench&pace=0&t=1e6", f"run={DOMAINS}&tab=A4&pace=0&t=1e6&dev=1"):
                page, errors = self.open(query, size)
                got = page.evaluate(BAND)
                page.close()
                self.assertEqual(errors, [])
                where = f"{size[0]} {query}"
                self.assertEqual(len(got["chips"]), 8, where)
                k = got["scale"]
                for t in got["tabs"]:
                    self.assertLess(t["nameH"], 1.6 * t["font"] * k, (where, t["key"]))  # one line
                    self.assertAlmostEqual(t["t"], got["tabs"][0]["t"], delta=1, msg=(where, t["key"]))
                    self.assertGreaterEqual(t["t"], got["row"]["t"] - 0.5, where)
                    self.assertLessEqual(t["b"], got["row"]["b"] + 0.5, where)
                boxes = [("tab " + t["key"], t) for t in got["tabs"]] + [(f"arrow {i}", a) for i, a in enumerate(got["arrows"])] + [(f"chip {i}", c) for i, c in enumerate(got["chips"])]
                for i, (na, a) in enumerate(boxes):
                    self.assertGreaterEqual(a["l"], got["row"]["l"] - 0.5, (where, na))
                    self.assertLessEqual(a["r"], got["row"]["r"] + 0.5, (where, na))
                    for nb, b in boxes[i + 1:]:
                        crossing = min(a["r"], b["r"]) - max(a["l"], b["l"]) > 0.5 and min(a["b"], b["b"]) - max(a["t"], b["t"]) > 0.5
                        self.assertFalse(crossing, (where, na, nb))
                # the chips keep a little air after the last tab
                self.assertGreater(min(c["l"] for c in got["chips"]) - max(t["r"] for t in got["tabs"]), 8 * k, where)


if __name__ == "__main__":
    unittest.main()
