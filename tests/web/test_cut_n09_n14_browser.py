"""N09 (the request and the domains picked from it) and N14 (dropped know-how becomes rules,
one catches a part), for viewers who do not know CAE (cut.html?cut=n09 / ?cut=n14).

For each cut: the boxes are where layout.json puts them and nothing reaches the subtitle
band; what is shown at each of the layout's times; the record's counts and words as they
are; no id, internal name or word of the trade on the page; the story's limits (counts of
data-kind, text sizes); a missing layout key is named on the page.

Skipped unless HIRAKU_WEB_BROWSER=1 and Playwright is installed (as tests.web.test_browser).
The records are intake_cut_fixture's (shape examples). The layout is the story test's copy
with n09 and n14 as in the promo department's layout.json at their commit cce1262 (the
sections the pages read, without the notes), and n14.view.fill and N09's label limit 8 as
in their working tree of 2026-11-03 night (not committed yet).
"""

import copy
import json
import re
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import make_server
from tests.web.intake_cut_fixture import ASK_PLAIN, BATTERY_KG, BUILTIN, DROPPED, SAID, SKIPPED, design_events, n09_events, n14_events, write_run
from tests.web.test_browser import ENABLED, sync_playwright
from tests.web.test_cut_story_browser import FIT, READ, limits_of, story_layout

# layout.json n09 / n14 at the promo department's commit cce1262, without the notes
N09_N14 = {
    "n09": {"duration_s": 5.6, "request": {"x": 96, "y": 96, "w": 1728, "h": 120, "title_px": 22, "text_px": 30},
            "view": {"x": 96, "y": 240, "w": 1100, "h": 646}, "chips": {"x": 1220, "y": 240, "w": 604, "h": 646, "text_px": 30, "row_gap": 16},
            "times": {"lead_s": 0.3, "request_s": 0.3, "chips_from_s": 2.2, "chip_step_s": 0.35},
            "colors": {"bg": "#f4f1ea", "panel": "#fbf9f4", "rule": "#c9c3b6"}},
    "n14": {"duration_s": 9.7, "knowledge": {"x": 96, "y": 96, "w": 700, "h": 790, "title_px": 30, "text_px": 24},
            "view": {"x": 820, "y": 96, "w": 1004, "h": 560, "fill": 0.8}, "ask": {"x": 820, "y": 680, "w": 1004, "h": 206, "text_px": 28},
            "times": {"lead_s": 0.3, "drop_s": 2.2, "rule_s": 3.6, "hit_s": 5.0, "ask_s": 6.8},
            "colors": {"bg": "#f4f1ea", "panel": "#fbf9f4", "rule": "#c9c3b6"}},
}
STORY = {"n09": {"message": "作るものを伝えると、Claude が必要な解析を選ぶ (7 つの札が灯る)", "limits": {"label": 8}},
         "n14": {"message": "渡したノウハウが、設計のチェックの規則になる"}}


def n09_n14_layout():
    layout = story_layout()
    layout.update(copy.deepcopy(N09_N14))
    layout["story"]["cuts"].update(copy.deepcopy(STORY))
    return layout


# a test id or a rule id, as labels.js ID_RE
ID_RE = re.compile(r"(?<![A-Za-z0-9])(?:[A-Z]{1,3}\d*-\d+|[A-Z]\d+[a-z]?)(?![A-Za-z0-9-])")
N09_WORDS = ["荷重条件", "操安", "音振", "強度信頼性", "ROPS", "∧", "handling", "powertrain", "assembly"]
N14_WORDS = ["battery", "rule", "doc", "check", "mass_kg"]

WANT = "林道を走れる 2 人乗りの小さな車両 (4 輪、積載 150 kg)"
# the care-bed run's shape: a pick with tests, picks with none (their reason says why), domains left off
PICKS = [
    {"domain": "handling", "shown_name": "安定", "reason": "置 に当てはまるので、S1 静的な転倒 (傾斜台・縁の荷重) を当てる", "test_ids": ["S1"]},
    {"domain": "powertrain", "shown_name": "動力", "reason": "電 に当てはまる。P5 アクチュエータの力と速さ は荷重条件の作り方がまだ無いので、この実行では見ない", "test_ids": []},
    {"domain": "strength", "shown_name": "強度信頼性", "reason": "預・荷 に当てはまるので、R1 静的強度・R3 疲労・耐久 を当てる。R2 衝撃 (落錘・落下・段差) は荷重条件の作り方がまだ無いので、この実行では見ない", "test_ids": ["R1", "R3"]},
    {"domain": "nvh", "shown_name": "騒音", "reason": "電・預 に当てはまる。N3 騒音 は荷重条件の作り方がまだ無いので、この実行では見ない", "test_ids": []},
    {"domain": "thermal", "shown_name": "熱", "reason": "電 に当てはまるので、T1 電池の温度・T2 モーター・制御器の温度 を当てる", "test_ids": ["T1", "T2"]},
]
OFF = [{"domain": "aero", "reason": "この製品の性質では当てはまる試験が無い: F1 横風 (外∧走)・F2 空気抵抗 (走∧速=車)"},
       {"domain": "crash", "reason": "この製品の性質では当てはまる試験が無い: C1 壁への正面衝突 (乗∧走∧速=車)"}]
ASM = ["H1", "H5"]
# the order the chips light in: the picks, the domains left off, assembly
ORDER = [p["domain"] for p in PICKS] + [o["domain"] for o in OFF] + ["assembly"]
LIT = {p["domain"]: len(p["test_ids"]) for p in PICKS if p["test_ids"]} | {"assembly": len(ASM)}
PALE = {"powertrain": ["試し方がまだ無いので、この実行では見ない"],  # the whole sentence does not fit at 18 px: its subject goes
        "nvh": ["騒音 は試し方がまだ無いので、この実行では見ない"],
        "aero": ["この製品の性質では当てはまる試験が無い"], "crash": ["この製品の性質では当てはまる試験が無い"]}

N09_STATE = """() => {
  const rows = {};
  for (const el of document.querySelectorAll(".n09-chip")) {
    const line = el.querySelector(".line");
    rows[el.dataset.domain] = { state: el.dataset.state, name: el.querySelector(".name").textContent, nameKind: el.querySelector(".name").dataset.kind ?? null,
      line: line.style.visibility === "visible" ? line.textContent : null };
  }
  return { rows, want: document.querySelector(".n09-request .want")?.textContent ?? null, text: document.getElementById("stage").innerText,
           none: document.querySelector(".n09-view .none")?.textContent ?? null };
}"""

N14_STATE = """() => {
  const shown = (el) => !!el && el.checkVisibility({ opacityProperty: true, visibilityProperty: true });
  const k = document.querySelector('[data-box="n14.knowledge"]');
  const tag = document.querySelector(".n14-tag");
  return {
    base: k.querySelector(".base .count")?.textContent ?? null,
    file: shown(k.querySelector(".file")) ? k.querySelector(".file").innerText : null,
    rulesShown: shown(k.querySelector(".new")),
    num: k.querySelector(".head .num")?.textContent ?? null,
    rules: [...k.querySelectorAll(".rules li")].map((li) => ({ text: li.textContent.replace(/^✕/, ""), hit: li.dataset.hit === "1", mark: li.querySelector(".mark").textContent })),
    skipped: [...k.querySelectorAll(".skipped li")].map((li) => [li.firstChild.textContent, li.querySelector("small").textContent]),
    skipCount: k.querySelector(".skip-head span")?.textContent ?? null,
    tag: shown(tag) ? tag.textContent : null,
    tagInside: (() => { if (!tag) return null; const a = tag.getBoundingClientRect(); const v = document.querySelector('[data-box="n14.view"]').getBoundingClientRect();
      return a.left >= v.left && a.right <= v.right && a.top >= v.top && a.bottom <= v.bottom; })(),
    ask: shown(document.querySelector(".n14-ask .q")) ? document.querySelector(".n14-ask .q").textContent : null,
    painted: window.hiraku.cut.painted ?? null,
    text: document.getElementById("stage").innerText,
  };
}"""


@unittest.skipUnless(ENABLED, "set HIRAKU_WEB_BROWSER=1 with Playwright installed")
class CutN09N14Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        runs = cls.tmp / "runs"
        cls.layout = n09_n14_layout()
        cls.n09 = "20261103-120000-n09-shape"
        cls.design_run = "20261103-120001-design-shape"
        cls.n14 = "20261103-120002-n14-shape"
        write_run(runs, cls.n09, n09_events(cls.n09, WANT, PICKS, OFF, ASM))
        write_run(runs, cls.design_run, design_events(cls.design_run))
        write_run(runs, cls.n14, n14_events(cls.n14))
        cls.layout_file = cls.tmp / "layout.json"
        cls.layout_file.write_text(json.dumps(cls.layout, ensure_ascii=False), encoding="utf-8")
        cls.server = make_server("127.0.0.1", 0, runs, quiet=True, saved_dir=None, launcher=False, layout_file=cls.layout_file)
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

    def setUp(self):
        self.layout_file.write_text(json.dumps(self.layout, ensure_ascii=False), encoding="utf-8")

    def read(self, cut, query, times, state, roots):
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.base}/cut.html?cut={cut}&{query}&t=0")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=120000)
        page.evaluate("window.hiraku.ready")
        out = {}
        for t in times:
            got = page.evaluate(READ, t)
            got["cut"] = page.evaluate(state) if state else None
            got["fit"] = page.evaluate(FIT, roots)
            out[t] = got
        self.assertEqual(errors, [])
        page.close()
        return out

    def check_common(self, cut, got, where, boxes, words):
        """No error; the story's limits; the boxes where the layout puts them; nothing in the
        subtitle band; no text past its box; no id or word of the trade on the page."""
        lim = limits_of(self.layout, cut)
        self.assertIsNone(got["err"], where)
        for k, n in got["counts"].items():
            if k in lim:
                self.assertLessEqual(n, lim[k], (where, k))
        self.assertEqual(got["page"]["over"], [], where)
        for t in got["texts"]:
            self.assertGreaterEqual(t["px"], lim["body_text_px"] if t["kind"] else self.layout["min_text_px"], (where, t))
        fit = got["fit"]
        for name in boxes:
            r = self.layout[cut][name]
            self.assertEqual([round(x) for x in fit["boxes"][f"{cut}.{name}"]], [r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"]], (where, name))
        self.assertLessEqual(fit["lowest"], self.layout["subtitle_band"]["y"], where)
        self.assertEqual(fit["over"], [], where)
        text = got["cut"]["text"]
        self.assertEqual(ID_RE.findall(text), [], where)
        for w in words:
            self.assertNotIn(w, text, (where, w))

    # ---- N09 ----

    def n09_times(self):
        T = self.layout["n09"]["times"]
        return [0.0, T["lead_s"], round(T["chips_from_s"] - 0.05, 3)] + [round(T["chips_from_s"] + k * T["chip_step_s"] + 0.01, 3) for k in range(8)] + [self.layout["n09"]["duration_s"] - 0.05]

    def test_n09_chips_light_in_the_record_order_with_its_reasons(self):
        T = self.layout["n09"]["times"]
        seen = self.read("n09", f"run={self.n09}&design_run={self.design_run}", self.n09_times(), N09_STATE, ['[data-box="n09.chips"]', '[data-box="n09.request"]'])
        for t, got in seen.items():
            self.check_common("n09", got, t, ["request", "view", "chips"], N09_WORDS)
            c = got["cut"]
            # the request is the record's want, from the start
            self.assertEqual(c["want"], WANT, t)
            self.assertIsNone(c["none"], t)
            # the seven domains' names are labels; assembly's chip is not a domain
            self.assertEqual(sorted(d for d, r in c["rows"].items() if r["nameKind"] == "label"), sorted(set(ORDER) - {"assembly"}), t)
            # the chips whose turn has come: lit (tests apply) or pale; the rest wait
            due = [d for k, d in enumerate(ORDER) if t >= T["chips_from_s"] + k * T["chip_step_s"]]
            for d, r in c["rows"].items():
                if d not in due:
                    self.assertEqual((r["state"], r["line"]), ("wait", None), (t, d))
                elif d in LIT:
                    self.assertEqual(r["state"], "lit", (t, d))
                    # one name for each test the record lists
                    self.assertEqual(len(r["line"].split("・")), LIT[d], (t, d, r["line"]))
                else:
                    self.assertEqual(r["state"], "pale", (t, d))
                    self.assertIn(r["line"], PALE[d], (t, d))
        self.assertEqual(seen[0.0]["counts"].get("moving"), 1)  # the vehicle turning

    def test_n09_without_a_design_says_so(self):
        got = self.read("n09", f"run={self.n09}", [1.0], N09_STATE, [])[1.0]
        self.assertEqual(got["cut"]["none"], "この記録に設計が無い")

    def test_n09_names_missing_layout_keys(self):
        layout = n09_n14_layout()
        del layout["n09"]["chips"]["row_gap"]
        del layout["n09"]["times"]["chip_step_s"]
        self.layout_file.write_text(json.dumps(layout), encoding="utf-8")
        got = self.read("n09", f"run={self.n09}", [1.0], None, [])[1.0]
        self.assertIn("layout.json の n09 に無い: chips.row_gap, times.chip_step_s", got["err"])

    # ---- N14 ----

    def test_n14_the_file_becomes_rules_and_one_catches_the_battery(self):
        T = self.layout["n14"]["times"]
        times = [0.0, T["lead_s"], T["drop_s"] + 0.6, T["rule_s"] + 0.05, T["hit_s"] + 0.05, T["ask_s"] + 0.05, self.layout["n14"]["duration_s"] - 0.05]
        seen = self.read("n14", f"run={self.n14}", times, N14_STATE, ['[data-box="n14.knowledge"]', '[data-box="n14.ask"]'])
        base = sum(len(r) for _d, _n, r in BUILTIN)
        for t, got in seen.items():
            # the ask box is shown from ask_s (its place is checked once it is there)
            self.check_common("n14", got, t, ["knowledge", "view"] + (["ask"] if t >= T["ask_s"] else []), N14_WORDS)
            self.assertEqual("n14.ask" in got["fit"]["boxes"], t >= T["ask_s"], t)
            c = got["cut"]
            # the counts of the record: the rules from the start, the dropped file's, the lines not made into rules
            self.assertEqual(c["base"], str(base), t)
            self.assertEqual(c["num"], str(len(DROPPED)), t)
            self.assertEqual([r["text"] for r in c["rules"]], [text for _r, text, _c, _k in DROPPED], t)
            self.assertEqual(c["skipCount"], str(len(SKIPPED)), t)
            self.assertEqual(c["skipped"], [[s["text"], s["reason"]] for s in SKIPPED], t)
            # what is shown by now
            self.assertEqual(c["file"] is not None, t >= T["drop_s"], t)
            self.assertEqual(c["rulesShown"], t >= T["rule_s"], t)
            hit = t >= T["hit_s"]
            self.assertEqual([r["hit"] for r in c["rules"]], [hit and k == "battery" for _r, _t, _c, k in DROPPED], t)
            self.assertEqual(c["painted"], hit, t)
            self.assertEqual(c["tag"], f"電池 {BATTERY_KG:g} kg" if hit else None, t)
            if hit:
                self.assertTrue(c["tagInside"], t)
            self.assertEqual(c["ask"], ASK_PLAIN if t >= T["ask_s"] else None, t)
        file_text = seen[T["drop_s"] + 0.6]["cut"]["file"]
        self.assertIn(SAID, file_text)

    def test_n14_names_missing_layout_keys(self):
        layout = n09_n14_layout()
        del layout["n14"]["times"]["hit_s"]
        del layout["n14"]["ask"]["text_px"]
        self.layout_file.write_text(json.dumps(layout), encoding="utf-8")
        got = self.read("n14", f"run={self.n14}", [1.0], None, [])[1.0]
        self.assertIn("layout.json の n14 に無い: ask.text_px, times.hit_s", got["err"])


if __name__ == "__main__":
    unittest.main()
