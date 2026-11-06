"""N09 (the request and the domains picked from it) and N14 (dropped know-how becomes rules,
one catches a part), for viewers who do not know CAE (cut.html?cut=n09 / ?cut=n14).

For each cut: the boxes are where layout.json puts them and nothing reaches the subtitle
band; what is shown at each of the layout's times; the record's counts and words as they
are; no id, internal name or word of the trade on the page; the story's limits (counts of
data-kind, text sizes); a missing layout key is named on the page.

Skipped unless HIRAKU_WEB_BROWSER=1 and Playwright is installed (as tests.web.test_browser).
The records are intake_cut_fixture's (shape examples). The layout is the story test's copy
with n09 and n14 as in the promo department's layout.json at their commit 8e295cf (the
sections the pages read, without the notes).
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
from tests.web.intake_cut_fixture import ASK_PLAIN, BATTERY_KG, BUILTIN, DROPPED, SAID, SKIPPED, design_events, n09_claude_events, n09_events, n14_events, write_run
from tests.web.test_browser import ENABLED, sync_playwright
from tests.web.test_cut_story_browser import FIT, READ, limits_of, story_layout

# layout.json n09 / n14 as in the promo department's commit 8e295cf (cce1262 with
# n14.view.fill and n09's label limit 8), without the notes
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
# the domains' names (操安, 音振, ...) are shown as in the other cuts (the promo department, 2026-11-03)
N09_WORDS = ["荷重条件", "ROPS", "∧", "handling", "powertrain", "assembly"]
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
LIT = {p["domain"] for p in PICKS if p["test_ids"]} | {"assembly"}
# the chips' words: the domain's name as the other cuts (labels.js DOMAIN_JA), and under it the
# domain in the viewers' words (DOMAIN_PLAIN_JA)
NAME = {"handling": "操安", "powertrain": "動力", "aero": "空力", "crash": "衝突", "strength": "強度信頼性", "nvh": "音振", "thermal": "熱", "assembly": "組み立て"}
PLAIN = {"handling": "倒れない・よけられる", "powertrain": "走る力", "aero": "風", "crash": "ぶつかったとき", "strength": "壊れない", "nvh": "揺れ・音", "thermal": "熱くなりすぎない", "assembly": "組める・手が届く"}
# a pale chip's reason after its plain name: the record's sentence (ids out, 荷重条件の作り方 -> 試し方),
# or, when that does not fit the line at 18 px, without its subject (the words before " は")
PALE = {"powertrain": "アクチュエータの力と速さ は試し方がまだ無いので、この実行では見ない",
        "nvh": "騒音 は試し方がまだ無いので、この実行では見ない",
        "aero": "この製品の性質では当てはまる試験が無い", "crash": "この製品の性質では当てはまる試験が無い"}


def pale_lines(d):
    full = PALE[d]
    short = full.split(" は", 1)[1].strip() if " は" in full else full
    return [f"{PLAIN[d]}：{full}", f"{PLAIN[d]}：{short}"]


# A run Claude picks for (the shape of the plugin's runs, as 20261104-002518-offroad-4w): the
# catalog's pick at start_run first (its reasons list the tests; here thermal is left off, so
# a page that read it would light six), then Claude's: seven picks, thermal among them, each
# reason in Claude's words, and flag_reasons.
CATALOG_PICKS = [
    {"domain": "handling", "shown_name": "操安", "reason": "置・走・悪 に当てはまるので、S2 旋回での転倒 (J ターン・定常円) を当てる", "test_ids": ["S2"]},
    {"domain": "powertrain", "shown_name": "動力", "reason": "走・池 に当てはまるので、P2 坂を登る・坂で発進する を当てる", "test_ids": ["P2"]},
    {"domain": "aero", "shown_name": "空力", "reason": "外・走 に当てはまるので、F1 横風 を当てる", "test_ids": ["F1"]},
    {"domain": "crash", "shown_name": "衝突", "reason": "乗・走・悪 に当てはまるので、C1 壁への正面衝突 を当てる", "test_ids": ["C1"]},
    {"domain": "strength", "shown_name": "強度信頼性", "reason": "預・荷 に当てはまるので、R1 静的強度 を当てる", "test_ids": ["R1"]},
    {"domain": "nvh", "shown_name": "音振", "reason": "振・乗 に当てはまるので、N1 固有振動数と加振 を当てる", "test_ids": ["N1"]},
]
CATALOG_OFF = [{"domain": "thermal", "reason": "この製品の性質では当てはまる試験が無い: T1 電池の温度 (池∧続)"}]
CLAUDE_REASON = {
    "handling": "最高 40 km/h で悪路を走るため、旋回時の転覆や安定性を確かめる",
    "powertrain": "500 kg の車両を電動で登坂・加速させる駆動力と航続を確かめる",
    "aero": "40 km/h での走行抵抗の一部として空気抵抗を見積もる",
    "crash": "人が乗る車両なので、衝突時に乗員を守れるかを確かめる",
    "strength": "悪路の衝撃と積載に骨組みが耐えるかを確かめる",
    "nvh": "悪路の振動とモーターの回転が乗員と骨組みに共振しないかを確かめる",
    "thermal": "連続走行でモーターと電池が熱くなりすぎないかを確かめる",
}
CLAUDE_TESTS = {"handling": ["S2", "S3"], "powertrain": ["P1", "P2"], "aero": ["F1"], "crash": ["C1", "C3"],
                "strength": ["R1", "R2"], "nvh": ["N1"], "thermal": ["T1", "T2"]}
CLAUDE_PICKS = [{"domain": d, "shown_name": NAME[d], "reason": r, "test_ids": CLAUDE_TESTS[d]} for d, r in CLAUDE_REASON.items()]
FLAG_REASONS = {"battery": "電池で走る", "continuous": "林道を連続で走りモーターと電池が発熱する", "rough": "林道の未舗装路を走る"}
CLAUDE_ASM = ["H1", "H2"]
CLAUDE_ORDER = list(CLAUDE_REASON) + ["assembly"]


# A reason longer than the chip's width at 18 px (shape example): it goes on two lines, after
# its first "、" (the only join whose two parts each fit).
LONG_REASON = "林道の長い上り坂を連続で走るとき、モーターと制御器と電池の温度が、使える上限を超えないかを確かめる"
LONG_LINES = ["林道の長い上り坂を連続で走るとき、", "モーターと制御器と電池の温度が、使える上限を超えないかを確かめる"]
# the chips box that leaves room for a name and two lines of 18 px in each of the eight rows,
# its bottom on the subtitle band (the values proposed to the promo department)
TALL_CHIPS = {"h": 670, "text_px": 24, "row_gap": 6}

N09_STATE = """() => {
  const rows = {};
  for (const el of document.querySelectorAll(".n09-chip")) {
    const line = el.querySelector(".line");
    const R = el.getBoundingClientRect(), L = line.getBoundingClientRect(), N = el.querySelector(".name").getBoundingClientRect();
    const lh = parseFloat(getComputedStyle(line).lineHeight);
    rows[el.dataset.domain] = { state: el.dataset.state, name: el.querySelector(".name").textContent, nameKind: el.querySelector(".name").dataset.kind ?? null,
      line: line.textContent, linePx: parseFloat(getComputedStyle(line).fontSize), lineKind: line.dataset.kind ?? null,
      lines: [...line.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent),
      rows: Math.round(L.height / lh), cutWide: line.scrollWidth > line.clientWidth + 0.5,
      inside: N.top >= R.top + 1.5 && L.bottom <= R.bottom - 1.5 && L.left >= R.left && L.right <= R.right && el.scrollHeight <= el.clientHeight + 0.5,
      over: el.dataset.over === "1" };
  }
  return { rows, over: document.documentElement.dataset.n09Over ?? null, want: document.querySelector(".n09-request .want")?.textContent ?? null, text: document.getElementById("stage").innerText,
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
    extent: window.hiraku.cut.extent ?? null,
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
        cls.n09c = "20261103-120003-n09-claude-shape"
        write_run(runs, cls.n09c, n09_claude_events(cls.n09c, WANT, (CATALOG_PICKS, CATALOG_OFF), (CLAUDE_PICKS, []), FLAG_REASONS, CLAUDE_ASM))
        cls.n09long = "20261103-120004-n09-claude-long"
        long_picks = [{**p, "reason": LONG_REASON} if p["domain"] == "thermal" else p for p in CLAUDE_PICKS]
        write_run(runs, cls.n09long, n09_claude_events(cls.n09long, WANT, (CATALOG_PICKS, CATALOG_OFF), (long_picks, []), FLAG_REASONS, CLAUDE_ASM))
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
        page.goto(f"{self.base}/cut.html?cut={cut}&{query}&t=0", wait_until="domcontentloaded")
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
            # the eight names are labels (story limit 8), as the other cuts name the domains
            self.assertEqual({d: (r["name"], r["nameKind"]) for d, r in c["rows"].items()}, {d: (NAME[d], "label") for d in ORDER}, t)
            # the chips whose turn has come: lit (tests apply) or pale; the rest wait
            due = [d for k, d in enumerate(ORDER) if t >= T["chips_from_s"] + k * T["chip_step_s"]]
            for d, r in c["rows"].items():
                if d not in due:
                    self.assertEqual((r["state"], r["line"]), ("wait", PLAIN[d]), (t, d))
                elif d in LIT:
                    # lit: the plain name only (the tests are not listed)
                    self.assertEqual((r["state"], r["line"]), ("lit", PLAIN[d]), (t, d))
                else:
                    self.assertEqual(r["state"], "pale", (t, d))
                    self.assertIn(r["line"], pale_lines(d), (t, d))
            # the lit chips are the record's picks with tests, and assembly with its tests
            self.assertEqual(sorted(d for d, r in c["rows"].items() if r["state"] == "lit"), sorted(d for d in due if d in LIT), t)
        self.assertEqual(seen[0.0]["counts"].get("moving"), 1)  # the vehicle turning

    def test_n09_reads_claudes_pick_with_its_reasons(self):
        """A run with two domains.picked: the later one (Claude's) is shown. Seven chips light,
        thermal among them; each lit domain's line is Claude's reason (no id); assembly, with
        no reason in the record, keeps its plain words."""
        T = self.layout["n09"]["times"]
        times = [0.0, round(T["chips_from_s"] - 0.05, 3)] + [round(T["chips_from_s"] + k * T["chip_step_s"] + 0.01, 3) for k in range(8)] + [self.layout["n09"]["duration_s"] - 0.05]
        seen = self.read("n09", f"run={self.n09c}&design_run={self.design_run}", times, N09_STATE, ['[data-box="n09.chips"]', '[data-box="n09.request"]'])
        for t, got in seen.items():
            self.check_common("n09", got, t, ["request", "view", "chips"], N09_WORDS + ["を当てる", "当てはまる"])
            c = got["cut"]
            self.assertEqual(c["want"], WANT, t)
            due = [d for k, d in enumerate(CLAUDE_ORDER) if t >= T["chips_from_s"] + k * T["chip_step_s"]]
            for d, r in c["rows"].items():
                self.assertEqual(r["name"], NAME[d], (t, d))
                if d not in due:
                    self.assertEqual((r["state"], r["line"]), ("wait", PLAIN[d]), (t, d))
                elif d == "assembly":
                    self.assertEqual((r["state"], r["line"]), ("lit", PLAIN[d]), (t, d))
                else:
                    # Claude's reason as the record has it (not cut), at 18 px, a note
                    self.assertEqual((r["state"], r["line"], r["linePx"], r["lineKind"]), ("lit", CLAUDE_REASON[d], 18, "note"), (t, d))
                    self.assertFalse(r["cutWide"], (t, d))
                # one size for every chip's second line (the promo department), assembly's included
                self.assertEqual(r["linePx"], 18, (t, d))
                # every chip's words inside the chip
                self.assertTrue(r["inside"], (t, d, r))
                self.assertFalse(r["over"], (t, d))
            self.assertEqual(c["over"], "", t)
        end = seen[self.layout["n09"]["duration_s"] - 0.05]["cut"]["rows"]
        lit = sorted(d for d, r in end.items() if r["state"] == "lit")
        self.assertEqual(lit, sorted(CLAUDE_ORDER))
        self.assertEqual(len([d for d in lit if d != "assembly"]), 7)
        self.assertIn("thermal", lit)
        self.assertEqual(end["thermal"]["line"], "連続走行でモーターと電池が熱くなりすぎないかを確かめる")

    def long_reason_end(self, layout):
        self.layout_file.write_text(json.dumps(layout, ensure_ascii=False), encoding="utf-8")
        t = layout["n09"]["duration_s"] - 0.05
        got = self.read("n09", f"run={self.n09long}&design_run={self.design_run}", [t], N09_STATE, ['[data-box="n09.chips"]'])[t]
        return got, got["cut"]["rows"]["thermal"]

    def test_n09_a_long_reason_goes_on_two_lines_in_a_tall_enough_box(self):
        """A reason wider than the chip at 18 px is on two lines, broken after its "、", whole;
        with the proposed chips box every chip's words are inside it."""
        layout = n09_n14_layout()
        layout["n09"]["chips"].update(TALL_CHIPS)
        got, r = self.long_reason_end(layout)
        self.assertIsNone(got["err"])
        self.assertEqual(got["page"]["over"], [])
        self.assertEqual(got["fit"]["over"], [])
        self.assertLessEqual(got["fit"]["lowest"], layout["subtitle_band"]["y"])
        self.assertEqual((r["line"], r["lines"], r["rows"], r["linePx"]), (LONG_REASON, LONG_LINES, 2, 18))
        for d, x in got["cut"]["rows"].items():
            self.assertTrue(x["inside"], (d, x))
        self.assertEqual(got["cut"]["over"], "")

    def test_n09_a_reason_past_its_chip_is_marked_not_cut(self):
        """With the layout's chips box a two-line reason does not fit its row: the chip is
        marked (not cut), the reason still whole."""
        got, r = self.long_reason_end(n09_n14_layout())
        self.assertEqual((r["line"], r["rows"], r["linePx"]), (LONG_REASON, 2, 18))
        self.assertTrue(r["over"])
        self.assertEqual(got["cut"]["over"], "thermal")

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
            # the vehicle takes view.fill of the view in the binding direction (the larger of its
            # shares of the width and the height), centred, inside the view
            e, fill = c["extent"], self.layout["n14"]["view"]["fill"]
            self.assertAlmostEqual(max((e["x1"] - e["x0"]) / e["w"], (e["y1"] - e["y0"]) / e["h"]), fill, delta=0.01, msg=t)
            self.assertGreaterEqual(e["x0"], 0, t)
            self.assertLessEqual(e["x1"], e["w"], t)
            self.assertAlmostEqual((e["x0"] + e["x1"]) / 2, e["w"] / 2, delta=2, msg=t)
            self.assertAlmostEqual((e["y0"] + e["y1"]) / 2, e["h"] / 2, delta=2, msg=t)
            self.assertGreaterEqual(e["y0"], 0, t)
            self.assertLessEqual(e["y1"], e["h"], t)
        file_text = seen[T["drop_s"] + 0.6]["cut"]["file"]
        self.assertIn(SAID, file_text)

    def test_n14_names_missing_layout_keys(self):
        layout = n09_n14_layout()
        del layout["n14"]["times"]["hit_s"]
        del layout["n14"]["ask"]["text_px"]
        del layout["n14"]["view"]["fill"]
        self.layout_file.write_text(json.dumps(layout), encoding="utf-8")
        got = self.read("n14", f"run={self.n14}", [1.0], None, [])[1.0]
        self.assertIn("layout.json の n14 に無い: view.fill, ask.text_px, times.hit_s", got["err"])


if __name__ == "__main__":
    unittest.main()
