"""The top and the footer kept short (web/js/domain_icons.js, web/js/hookline.js, web/css/topbar.css):
「☰ 記録の一覧へ」 at the top left by the wordmark on every tab (hidden on ?video=1); the domain chips at
the top right and ④'s tile headings with the icons of the one definition; the footer's hooks list
folded under 「くわしく」 (open on ?dev=1) and its activity line without a verdict's numbers or a
rule's id; the moment a hook stops, one line near the middle that goes after a few seconds of the
replay (the same t, the same picture), on the tabs other than ③'s workbench; there the same words
are the card of the strip under the 3D (web/js/benchnote.js) while the stop is the newest event.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed, as test_browser.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from tests.web.test_layout_lint_browser import DOMAINS, ENABLED, READY, start_servers

try:
    from tests.web.pages import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

TABS = ["A1", "A2", "bench", "grid", "A7", "A4", "A5"]
DOMAIN_KEYS = ["handling", "powertrain", "aero", "crash", "strength", "nvh", "thermal", "assembly"]

BACK = """() => {
  const a = document.getElementById("back-link"); const w = document.querySelector(".wordmark");
  const r = a.getBoundingClientRect(), wr = w.getBoundingClientRect(), s = document.getElementById("stage").getBoundingClientRect();
  return { text: a.textContent, href: a.getAttribute("href"), shown: a.checkVisibility(), inBrand: !!a.closest(".brand"),
           l: Math.round(r.left), t: Math.round(r.top), gap: r.left - wr.right, rel: (r.left - s.left) / s.width,
           modeLinks: [...document.querySelectorAll("#mode a")].map((x) => x.textContent) };
}"""

ICONS = """async () => {
  const m = await import("/js/domain_icons.js");
  const want = (d) => { const t = document.createElement("div"); t.innerHTML = m.domainIconSvg(d, "x"); return t.firstElementChild.innerHTML; };
  const chips = [...document.querySelectorAll("#chips .chip")].map((c) => { const ic = c.querySelector("svg.chip-ic");
    return { d: ic?.dataset.domainIcon ?? null, same: ic ? ic.innerHTML === want(ic.dataset.domainIcon) : false, text: c.textContent,
             h: ic ? ic.getBoundingClientRect().height : 0, dot: getComputedStyle(c, "::before").display,
             // the icon takes the state dot's place and colour: an OK / NG chip's icon in its border's colour
             color: ic && (c.classList.contains("green") || c.classList.contains("red")) && !c.classList.contains("focus")
               ? getComputedStyle(ic).color === getComputedStyle(c).borderTopColor : true }; });
  return { chips, all: m.ALL_DOMAINS_HAVE_ICONS, keys: Object.keys(m.DOMAIN_ICON) };
}"""

FOOT = """() => { const h = document.getElementById("hooks"), m = document.getElementById("hooks-more"), row = document.getElementById("statusrow");
  return { shown: h.checkVisibility(), more: m?.textContent ?? null, words: row.innerText, act: document.getElementById("activity").textContent }; }"""

LINE = """() => { const l = document.getElementById("hookline"); return { shown: !l.hidden && l.checkVisibility() && Number(getComputedStyle(l).opacity) > 0.5,
  text: l.textContent, color: getComputedStyle(l).color, border: getComputedStyle(l).borderTopColor, rule: l.dataset.rule ?? null }; }"""


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class TopbarBrowserTest(unittest.TestCase):
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

    def open(self, query, size=(1920, 1080)):
        page = self.browser.new_page(viewport={"width": size[0], "height": size[1]})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.bases['saved']}/?{query}", wait_until="domcontentloaded")
        page.wait_for_function(READY, timeout=60000)
        page.evaluate("window.hiraku.ready")
        page.evaluate("document.fonts.ready.then(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))))")
        return page, errors

    def seek(self, page, t):
        page.evaluate("async (t) => { await window.hiraku.seek(t); await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))); }", t)

    def test_back_link_top_left_on_every_tab(self):
        page, errors = self.open("run=sample&pace=0&t=1e6")
        seen = {}
        for key in TABS:
            page.click(f'.tab[data-tab="{key}"]')
            page.wait_for_function(f"document.querySelector('.tab.on')?.dataset.tab === '{key}'")
            seen[key] = page.evaluate(BACK)
        page.close()
        self.assertEqual(errors, [])
        first = seen["A1"]
        for key, g in seen.items():
            self.assertEqual((g["text"], g["href"], g["shown"], g["inBrand"]), ("☰ 記録の一覧へ", "?list=1", True, True), key)
            self.assertEqual((g["l"], g["t"]), (first["l"], first["t"]), key)  # the same place on every tab
            self.assertLess(g["rel"], 0.2, key)  # top left
            self.assertTrue(0 <= g["gap"] < 40, key)  # by the wordmark
            self.assertNotIn("☰ 記録の一覧へ", g["modeLinks"], key)  # not at the top right any more
        page, errors = self.open("run=sample&pace=0&t=1e6&dev=1")
        dev = page.evaluate(BACK)
        page.close()
        self.assertEqual(dev["href"], "?list=1&dev=1")
        for q, shown in (("present=1", True), ("video=1", False)):
            page, errs = self.open(f"run=sample&pace=0&t=1e6&{q}")
            self.assertEqual(page.evaluate(BACK)["shown"], shown, q)
            page.close()
            errors += errs
        self.assertEqual(errors, [])

    def test_domain_icons_from_one_definition(self):
        page, errors = self.open(f"run={DOMAINS}&tab=bench&pace=0&t=1e6")
        got = page.evaluate(ICONS)
        page.click('.tab[data-tab="grid"]')
        page.wait_for_selector(".tile .th h3 svg.dom-ic")
        tiles = page.evaluate("""async () => { const m = await import("/js/domain_icons.js");
          return [...document.querySelectorAll(".tile .th h3")].map((h) => { const ic = h.querySelector("svg.dom-ic");
            const t = document.createElement("div"); t.innerHTML = m.domainIconSvg(ic?.dataset.domainIcon, "x");
            return { d: ic?.dataset.domainIcon ?? null, same: !!ic && ic.innerHTML === t.firstElementChild?.innerHTML, text: h.textContent }; }); }""")
        page.close()
        self.assertEqual(errors, [])
        self.assertTrue(got["all"])
        self.assertEqual(got["keys"], DOMAIN_KEYS)
        self.assertEqual([c["d"] for c in got["chips"]], DOMAIN_KEYS)
        for c in got["chips"]:
            self.assertTrue(c["same"], c)  # drawn from domain_icons.js
            self.assertTrue(c["color"], c)  # in the state's colour
            self.assertEqual(c["dot"], "none", c)  # in place of the dot
            self.assertGreater(c["h"], 10, c)
        self.assertEqual(len(tiles), 8)
        for t in tiles:
            self.assertIn(t["d"], DOMAIN_KEYS, t)
            self.assertTrue(t["same"], t)

    def test_footer_short_and_hooks_folded(self):
        page, errors = self.open("run=sample&pace=0&t=1e6")
        folded = page.evaluate(FOOT)
        page.click("#hooks-more")
        opened = page.evaluate(FOOT)
        # the activity line just after a verdict: not its numbers (the right panel's card has them)
        t_ver = page.evaluate("() => { const s = window.hiraku.player.state; return s.tOf[s.order.verdicts[0]]; }")
        self.seek(page, t_ver + 0.01)
        after_verdict = page.evaluate(FOOT)
        cmp = page.evaluate("document.getElementById('card-cmp').textContent")
        page.close()
        page, errs = self.open("run=sample&pace=0&t=1e6&dev=1")
        dev = page.evaluate(FOOT)
        page.close()
        self.assertEqual(errors + errs, [])
        self.assertFalse(folded["shown"])
        self.assertEqual(folded["more"], "くわしく")
        for word in ("HK1", "HK4", "✓", "×"):
            self.assertNotIn(word, folded["words"])
        self.assertTrue(opened["shown"])
        self.assertIn("HK1", opened["words"])  # opened, the whole list as before
        self.assertIn("予測が先", opened["words"])
        self.assertIn("判定を出しました", after_verdict["act"])
        self.assertNotIn("→ OK", after_verdict["act"])
        self.assertNotIn("余裕", after_verdict["act"])
        self.assertIn("OK", cmp)  # the verdict is on the right panel
        self.assertTrue(dev["shown"])  # ?dev=1: open

    def test_the_stop_in_one_line_that_goes(self):
        page, errors = self.open("run=sample&pace=0&t=0&tab=bench")
        self.seek(page, 1e6)
        t_hook = page.evaluate("() => window.hiraku.player.state.hooks.denied[0].t")
        # ③'s workbench: the stop is the strip's card under the 3D, the stage's line is not drawn
        self.seek(page, t_hook + 0.001)
        bench = page.evaluate("""() => { const n = document.getElementById('hooknote'), s = document.getElementById('benchnote');
          return { line: LINE_SHOWN(), kind: s.dataset.kind, text: n.innerText.replace(/\\s+/g, ' ').trim(), inStrip: n.closest('#benchnote') !== null,
                   shown: n.checkVisibility(), color: getComputedStyle(n.querySelector('.hn-head')).color }; }""".replace(
            "LINE_SHOWN()", "(() => { const l = document.getElementById('hookline'); return !l.hidden && l.checkVisibility(); })()"))
        foot = page.evaluate(FOOT)
        # the stage's line on the tools' shelf (not over ④ and ⑤: test_judge_broken_browser)
        page.click('.tab[data-tab="A4"]')
        page.wait_for_function("document.querySelector('.tab.on')?.dataset.tab === 'A4'")
        before = (self.seek(page, t_hook - 0.5), page.evaluate(LINE))[1]
        at = (self.seek(page, t_hook + 1.0), page.evaluate(LINE))[1]
        shot1 = page.screenshot(clip={"x": 0, "y": 0, "width": 1920, "height": 400}, animations="disabled")
        gone = (self.seek(page, t_hook + 6.0), page.evaluate(LINE))[1]
        self.seek(page, t_hook + 1.0)
        page.evaluate(LINE)
        shot2 = page.screenshot(clip={"x": 0, "y": 0, "width": 1920, "height": 400}, animations="disabled")
        box = page.evaluate("() => { const r = document.getElementById('hookline').getBoundingClientRect(); return { cx: (r.left + r.right) / 2, h: r.height }; }")
        page.close()
        page, errs = self.open("run=sample&pace=0&t=0&tab=A4&dev=1")
        self.seek(page, t_hook + 1.0)
        dev = page.evaluate(LINE)
        page.close()
        self.assertEqual(errors + errs, [])
        self.assertFalse(bench["line"])
        self.assertEqual((bench["kind"], bench["inStrip"], bench["shown"]), ("hooknote", True, True))
        self.assertEqual(bench["text"], "hooks が止めた 予測を書く前の解析を止めました。先に予測を書かせます")
        self.assertFalse(before["shown"])
        self.assertTrue(at["shown"])
        self.assertEqual(at["text"], "!予測を書く前の解析を止めました。先に予測を書かせます")
        self.assertEqual(at["rule"], "HK1")
        self.assertNotIn("HK", at["text"])
        self.assertEqual(at["color"], at["border"])  # orange (test_video_fixes_browser: not the NG red)
        self.assertEqual(bench["color"], at["color"])  # the strip's card in the same orange
        self.assertFalse(gone["shown"])
        self.assertEqual(shot1, shot2)  # the same t, the same picture
        self.assertAlmostEqual(box["cx"], 960, delta=40)  # near the middle
        self.assertLess(box["h"], 60)  # one line
        self.assertNotIn("HK", foot["act"])
        self.assertTrue(dev["text"].startswith("!HK1 "), dev)

    def test_lines_for_every_rule(self):
        page, errors = self.open("run=sample&pace=0&t=1e6")
        got = page.evaluate("""async () => { const h = await import("/js/hookline.js"); const l = await import("/js/labels.js");
          return l.RULES.map((r) => [r.id, h.hookLineText(r.key), h.hookLineText(r.key, { dev: true })]); }""")
        page.close()
        self.assertEqual(errors, [])
        for rid, text, dev in got:
            self.assertIn("止めました", text, rid)
            self.assertNotEqual(text, "hooks が止めました", rid)  # each rule says what it stopped
            self.assertNotIn("HK", text, rid)
            self.assertEqual(dev, f"{rid} {text}")


if __name__ == "__main__":
    unittest.main()
