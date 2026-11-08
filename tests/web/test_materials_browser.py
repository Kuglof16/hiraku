"""The shelf's 材質 view in a headless browser: the repository's material cards
(data/materials/cards/<id>.json) from GET /api/materials, the list and each card.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed (see test_browser.py).
The shelf's tools here are a small stand-in (no solvers); the cards are the repository's own.
"""

import json
import threading
import unittest

from hiraku.webserve.materials import CARDS_DIR
from hiraku.webserve.server import make_server
from hiraku.webserve.shelf import Shelf
from tests.web.test_browser import ENABLED, sync_playwright

CARDS = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(CARDS_DIR.glob("*.json"))]


def all_read(card):
    return all(p["checked"] for p in card["properties"].values())


# every element of the view whose colour, background or border is the screen's red (--red, --red-pale)
RED_IN_VIEW = """() => {
  const probe = document.createElement('span');
  probe.style.cssText = 'color: var(--red); background: var(--red-pale); border: 1px solid var(--red)';
  document.body.append(probe);
  const cs = getComputedStyle(probe);
  const reds = new Set([cs.color, cs.backgroundColor]);
  probe.remove();
  return [...document.querySelectorAll('#view-shelf .mt-host *')].filter((el) => {
    const s = getComputedStyle(el);
    return reds.has(s.color) || reds.has(s.backgroundColor) || (s.borderTopStyle !== 'none' && reds.has(s.borderTopColor));
  }).map((el) => el.className || el.tagName);
}"""

ROWS = """() => [...document.querySelectorAll('#view-shelf .mt-row')].map((r) => ({
  id: r.dataset.material, name: r.querySelector('.sh-name b').textContent,
  tag: r.querySelector('.mt-tag').className, tagText: r.querySelector('.mt-tag').textContent,
  category: r.closest('.sh-node').dataset.category }))"""

DETAIL = """() => [...document.querySelectorAll('#view-shelf .mt-detail tbody tr')].map((r) => ({
  prop: r.dataset.prop, cls: r.className, value: r.querySelector('.mt-val').textContent,
  data: r.querySelector('.mt-val').dataset.value, unit: r.querySelector('.mt-unit').textContent,
  kind: r.querySelector('.mt-kind').textContent, tag: r.querySelector('.mt-tag').textContent }))"""


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class MaterialsBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server("127.0.0.1", 0, "runs-none", quiet=True, saved_dir=None, launcher=False,
                                 shelf=Shelf(lambda: {"tools": [], "formulas": [], "archetypes": {}}))
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

    def open(self, extra=""):
        page = self.browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        self.addCleanup(page.close)
        page.goto(f"{self.base}/?run=sample&pace=0&t=1e6{extra}", wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=30000)
        page.evaluate("window.hiraku.ready")
        page.click('.tab[data-tab="A4"]')
        page.click('#view-shelf .sh-view button[data-view="materials"]')
        page.wait_for_function(f"document.querySelectorAll('#view-shelf .mt-row').length === {len(CARDS)}", timeout=10000)
        return page, errors

    def test_the_list(self):
        page, errors = self.open()
        self.assertGreaterEqual(len(CARDS), 4)
        # the tools' search and filters are out of the way; the bar counts the cards
        self.assertTrue(page.is_hidden("#view-shelf .sh-search"))
        self.assertTrue(page.is_hidden("#view-shelf .sh-body:not(.mt-body)"))
        ok = sum(all_read(c) for c in CARDS)
        self.assertEqual(page.inner_text("#view-shelf .sh-sum"), f"材料 {len(CARDS)} 種 (出典を読んで確かめた {ok})")
        rows = {r["id"]: r for r in page.evaluate(ROWS)}
        self.assertEqual(sorted(rows), sorted(c["id"] for c in CARDS))
        for card in CARDS:
            with self.subTest(card=card["id"]):
                r = rows[card["id"]]
                self.assertEqual(r["name"], card.get("name_ja") or card["name"])
                self.assertEqual(r["category"], card["category"])
                if all_read(card):
                    self.assertEqual((r["tag"], r["tagText"]), ("mt-tag ok", "読んで確かめた"))
                else:
                    self.assertEqual(r["tag"], "mt-tag unread")
                    n = sum(not p["checked"] for p in card["properties"].values())
                    self.assertIn(f"{n} / {len(card['properties'])}" if n < len(card["properties"]) else "読んでいない", r["tagText"])
        # (both kinds are in the repository's cards: the orange tag is looked at)
        self.assertTrue(any(not all_read(c) for c in CARDS))
        self.assertEqual(page.evaluate(RED_IN_VIEW), [])
        # no ids or paths without ?dev=1
        self.assertIsNone(page.query_selector("#view-shelf .mt-host code"))
        self.assertEqual(errors, [])

    def test_each_card_as_its_record(self):
        page, errors = self.open()
        for card in CARDS:
            with self.subTest(card=card["id"]):
                page.click(f'#view-shelf .mt-row[data-material="{card["id"]}"]')
                page.wait_for_function(f"document.querySelector('#view-shelf .mt-detail')?.dataset.material === '{card['id']}'")
                rows = page.evaluate(DETAIL)
                # a row for each value the card has, none for one it does not
                self.assertEqual(sorted(r["prop"] for r in rows), sorted(card["properties"]))
                for r in rows:
                    p = card["properties"][r["prop"]]
                    self.assertEqual(float(r["value"]), p["value"], r["prop"])
                    self.assertEqual(float(r["data"]), p["value"], r["prop"])
                    self.assertEqual(r["cls"], "ok" if p["checked"] else "unread", r["prop"])
                    self.assertEqual(r["tag"], "読んで確かめた" if p["checked"] else "読んでいない", r["prop"])
                    self.assertIn(r["kind"], {"規格の最小値", "代表的な値", "ほかの値から計算した値", "仮に置いた値"})
                    self.assertNotIn("不明", r["value"] + r["unit"] + r["kind"])
                text = page.inner_text("#view-shelf .mt-detail")
                n = sum(p["checked"] for p in card["properties"].values())
                self.assertIn(f"{len(card['properties'])} 個の値のうち {n} 個", text)
                # every くわしく opened: the sources, still without a repository path
                page.eval_on_selector_all("#view-shelf .mt-detail details", "(ds) => ds.forEach((d) => { d.open = true; })")
                text = page.inner_text("#view-shelf .mt-detail")
                for p in card["properties"].values():
                    if "http" in p["source"]:
                        self.assertIn("http", text)
                self.assertNotRegex(text, r"\b(?:hiraku|data)/[a-z]")
                self.assertEqual(page.evaluate(RED_IN_VIEW), [])
        self.assertEqual(errors, [])

    def test_the_first_line(self):
        """The first line is put together from the records' kind and checked (ss400: JIS minima, read in a secondary source)."""
        page, _ = self.open("&material=ss400")
        ss400 = next(c for c in CARDS if c["id"] == "ss400")
        strength = [ss400["properties"][k] for k in ("yield_MPa", "tensile_MPa")]
        self.assertTrue(all(p["kind"] == "standard_minimum" and p["checked"] and p["source"].startswith("secondary") for p in strength))
        # (textContent: the line is drawn as two phrases, which may sit on two lines)
        self.assertEqual(page.text_content("#view-shelf .mt-lead"),
                         "この材料の強さの値は JIS G 3101 の最小値で、もとの文書を引いた資料で読んで確かめた。")
        hdpe = next(c for c in CARDS if c["id"] == "hdpe")
        self.assertFalse(hdpe["properties"]["yield_MPa"]["checked"])
        page.click('#view-shelf .mt-row[data-material="hdpe"]')
        self.assertEqual(page.text_content("#view-shelf .mt-lead"), "この材料の強さの値は仮に置いた値で、出典をまだ読んで確かめていない。")

    def test_dev_shows_the_ids_and_the_file(self):
        page, _ = self.open("&dev=1&material=ss400")
        self.assertEqual(page.inner_text('#view-shelf .mt-row[data-material="ss400"] code'), "ss400")
        page.eval_on_selector_all("#view-shelf .mt-detail details", "(ds) => ds.forEach((d) => { d.open = true; })")
        self.assertIn("data/materials/cards/ss400.json", page.inner_text("#view-shelf .mt-detail"))


if __name__ == "__main__":
    unittest.main()
