"""N11, N12 and N13 for viewers who do not know CAE (cut.html?cut=n11 / n12 / n13).

N11: an analysis called without a prediction is stopped by the hooks, the prediction is
written, then the analysis runs. N12: the hand calculation and the analysis disagree, the
cause (a unit mistaken in the request) is found, and after the fix they agree. N13: a tool
the shelf does not have is written by Claude, checked against known answers and used.

For each cut: the boxes where layout.json puts them and nothing in the subtitle band, what
is shown at each time, the record's numbers as they are, no record id, rule id, tool name
or word of the trade on the page, the story's limits, and a missing layout key named.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser) and Playwright is installed.
The records are made here (shape examples: values chosen, not computed): for N11 / N12
k2_fixture's tilt-table run with a denial, two predictions, two analyses, two comparisons
and a cause; for N13 the contract sample with a shelf, a tool made in the run (one failed
attempt, then a usable one with its code in tools/) and an analysis made with it. The
layout is the story test's copy of the promo department's layout.json (cce1262 for these
three cuts).
"""

import copy
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import SAMPLE_EVENTS, make_server
from tests.web.k2_fixture import tilt_events
from tests.web.test_browser import ENABLED, sync_playwright
from tests.web.test_cut_story_browser import FIT, KINDS, READ, limits_of, story_layout

# What must not be on the page: record ids, rule ids, requirement and test ids, tool names
# and the trade's words.
IDS = ["pred-", "ana-", "cmp-", "cause-", "ver-", "HK1", "HK_", "Q41", "S1-", "R3", "tilt", "use_fatigue", "record_prediction",
       "formula", "spring_roll", "cycles_counted", "fatigue_damage", "S-N", "Miner", "マイナー", "FAT", "load_case"]

PRED1, ANA, PRED2 = 41.2, 32.5, 33.8  # deg (shape example)
SETTING = 62  # mm, the request as written (shape example)


def _rel(pred, ana):
    return (ana - pred) / abs(pred)


def hooks_events(run_id):
    """k2_fixture's tilt run as N11 / N12 need it: the setting from the request (62 mm, meant
    62 cm), a denial (HK1), the first prediction, its analysis, the mismatch and its cause,
    the second prediction, the match and the cause resolved. Shape example."""
    src, _info = tilt_events()
    run, design = copy.deepcopy(src[0]), copy.deepcopy(src[1])
    ana_src = copy.deepcopy(next(e["data"] for e in src if e["kind"] == "analysis.finished"))
    p = run["data"]
    lc = p["load_cases"][0]
    lc["title"] = "傾斜台で倒れない (形の見本)"
    lc["params"]["cargo_cg_above_deck"] = {"value": SETTING, "unit": "mm"}
    lc["requirement_keys"] = ["Q41"]
    p["requirements"] = [{"key": "Q41", "name": "荷の重心の高さ (形の見本)", "value": SETTING, "unit": "mm", "source": "user", "trust": 1}]
    lcid = lc["load_case_id"]

    def pred(pid, value, h):
        return {"prediction_id": pid, "design_version": 1, "load_case_id": lcid, "test_id": "S1", "domain": "handling", "agent": "cae.handling",
                "quantity": "tilt_angle", "value": value, "unit": "deg", "formula_id": "tilt.spring_roll_angle", "expression": "(shape example)",
                "inputs": {"t": {"value": 1100.0, "unit": "mm"}, "h": {"value": h, "unit": "mm"}, "m": {"value": 600.0, "unit": "kg"},
                           "zz": {"value": 7.0, "unit": "1"}},
                "assumptions": ["shape example"], "created_at": run["ts"]}

    def ana(aid, pid):
        return {**copy.deepcopy(ana_src), "analysis_id": aid, "prediction_ids": [pid], "load_case_id": lcid,
                "results": [{"quantity": "tilt_angle", "value": ANA, "unit": "deg"}]}

    def cmp(cid, pid, aid, pv, cause_id=None):
        r = _rel(pv, ANA)
        return {"comparison_id": cid, "prediction_id": pid, "analysis_id": aid, "quantity": "tilt_angle",
                "predicted": {"value": pv, "unit": "deg"}, "analyzed": {"value": ANA, "unit": "deg"}, "diff_abs": ANA - pv, "diff_rel": r,
                "tolerance_rel": 0.1, "status": "match" if abs(r) <= 0.1 else "mismatch", "cause_id": cause_id}

    cause = {"cause_id": "cause-0001", "comparison_id": "cmp-0001",
             "cause": f"要件 Q41 (荷の重心の高さ) の単位の取り違え。{SETTING} cm のつもりが {SETTING} mm と書かれていた (形の見本)。",
             "fix": "荷の高さを設計のとおりにして予測し直す。要件の Q41 は人に直してもらう", "resolved": False}
    h = {"agent": "cae.handling", "domain": "handling", "design_version": 1}
    events = [
        run, design,
        {**h, "kind": "agent.started", "data": {"agent_id": "cae.handling"}},
        {**h, "kind": "hook.denied", "text": "予測が無いので解析は止めました (形の見本)",
         "data": {"hook": "PreToolUse", "tool": "mcp__hiraku__analyze", "rule": "HK1_prediction_first"}},
        {**h, "kind": "prediction.recorded", "data": pred("pred-0001", PRED1, 560.0)},
        {**h, "kind": "analysis.started", "data": {"analysis_id": "ana-0001", "tool": "tilt", "load_case_id": lcid, "prediction_ids": ["pred-0001"]}},
        {**h, "kind": "analysis.finished", "data": ana("ana-0001", "pred-0001")},
        {**h, "kind": "comparison.recorded", "data": cmp("cmp-0001", "pred-0001", "ana-0001", PRED1)},
        {**h, "kind": "cause.recorded", "data": cause},
        {**h, "kind": "comparison.recorded", "data": cmp("cmp-0001", "pred-0001", "ana-0001", PRED1, "cause-0001")},
        {**h, "kind": "prediction.recorded", "data": pred("pred-0002", PRED2, 720.0)},
        {**h, "kind": "analysis.started", "data": {"analysis_id": "ana-0002", "tool": "tilt", "load_case_id": lcid, "prediction_ids": ["pred-0002"]}},
        {**h, "kind": "analysis.finished", "data": ana("ana-0002", "pred-0002")},
        {**h, "kind": "comparison.recorded", "data": cmp("cmp-0002", "pred-0002", "ana-0002", PRED2)},
        {**h, "kind": "cause.recorded", "data": {**cause, "resolved": True, "resolved_by": "cmp-0002"}},
        {"agent": "system", "kind": "run.finished", "data": {"status": "done", "stop_code": "done"}},
    ]
    out = []
    for i, ev in enumerate(events):
        ev = {**ev, "run_id": run_id, "ts": run["ts"], "seq": i, "t": 0.0 if i < 2 else 1.0 + i}
        if i == 0:
            ev["data"] = {**ev["data"], "run_id": run_id}
        out.append(ev)
    return out


TOOL = "use_fatigue"
CODE = "\n".join(["import math", "", "", "class UseFatigue:", "    name = 'use_fatigue'", "", "    def analyse(self, design, load_case, params):"]
                 + [f"        x{i} = {i} * 2  # line {i}" for i in range(30)]) + "\n"
CYCLES = 4321.0
CHECKS = [
    {"case": "c1", "quantity": "cycles_at_FAT", "expected": 2.0e6, "got": 1999999.9999, "unit": "1", "tolerance_rel": 1e-9, "pass": True,
     "source": "the FAT class is the stress range at 2e6 cycles (IIW S-N curve definition)"},
    {"case": "c2", "quantity": "seat_stress_range", "expected": 12.34, "got": 12.34, "unit": "MPa", "tolerance_rel": 1e-6, "pass": True,
     "source": "simply supported rail: range = (P L / 4)(h / 2) / I"},
    {"case": "c3", "quantity": "fatigue_damage", "expected": 0.00123, "got": 0.00123, "unit": "1", "tolerance_rel": 1e-6, "pass": True,
     "source": "Miner D = n / N (IIW S-N curve above the knee); range = (P L / 4)(h / 2) / I"},
]


def tool_events(run_id):
    """The contract sample with a shelf of three tools, a tool made by Claude (attempt 1 not
    usable, attempt 2 usable with three checks), an analysis made with it and a passing
    verdict. Shape example."""
    src = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    run, design = copy.deepcopy(src[0]), copy.deepcopy(src[1])
    run["data"]["want"] = "家で使うベッドを作りたい (形の見本)"
    lc = {"load_case_id": "R3-uses", "test_id": "R3", "domain": "strength", "title": "使う回数の疲労 (形の見本)", "params": {}, "tolerance_rel": 0.5,
          "criterion": {"quantity": "fatigue_damage", "op": "<", "limit": {"value": 1.0, "unit": "1"}}}
    run["data"]["load_cases"] = [lc]
    sysv = {"agent": "system"}
    st = {"agent": "cae.strength", "domain": "strength", "design_version": 1}

    def checked(name, origin, usable, checks=()):
        return {**sysv, "kind": "tool.checked", "data": {"name": name, "title": f"{name} の道具 (形の見本)", "domain": "strength", "base": "K1",
                                                         "tests": ["R3"], "origin": origin, "usable": usable, "checks": list(checks)}}

    made = {**checked(TOOL, "claude", True, CHECKS), **st}
    made["data"]["title"] = "ベッドの使う回数の疲労 (形の見本: 応力の幅 → S-N)"
    ana = {"analysis_id": "ana-0001", "tool": TOOL, "base": "K1", "design_version": 1, "load_case_id": "R3-uses", "prediction_ids": ["pred-0001"],
           "status": "ok", "results": [{"quantity": "fatigue_damage", "value": 1e-9, "unit": "1"}, {"quantity": "cycles_counted", "value": CYCLES, "unit": "1"}]}
    cmp = {"comparison_id": "cmp-0001", "prediction_id": "pred-0001", "analysis_id": "ana-0001", "quantity": "fatigue_damage",
           "predicted": {"value": 1e-9, "unit": "1"}, "analyzed": {"value": 1e-9, "unit": "1"}, "diff_abs": 0.0, "diff_rel": 0.0, "tolerance_rel": 0.5,
           "status": "match", "cause_id": None}
    ver = {"verdict_id": "ver-0001", "comparison_id": "cmp-0001", "load_case_id": "R3-uses", "test_id": "R3", "domain": "strength",
           "quantity": "fatigue_damage", "value": {"value": 1e-9, "unit": "1"}, "op": "<", "limit": {"value": 1.0, "unit": "1"}, "pass": True, "margin_pct": 99.9}
    pred = {"prediction_id": "pred-0001", "design_version": 1, "load_case_id": "R3-uses", "test_id": "R3", "domain": "strength", "agent": "cae.strength",
            "quantity": "fatigue_damage", "value": 1e-9, "unit": "1", "formula_id": "fatigue.constant_amplitude_damage", "inputs": {}, "assumptions": [],
            "created_at": run["ts"]}
    events = [
        run, design,
        checked("frame_static", "builtin", True), checked("fatigue", "builtin", True), checked("thermal", "builtin", True),
        checked("bed_static", "builtin", True), checked("tilt_table", "builtin", True), checked("duty", "builtin", True),
        checked("broken", "builtin", False),
        {**checked(TOOL, "claude", False, [{**CHECKS[0], "got": 1.0, "pass": False}]), **st},
        made,
        {**st, "kind": "prediction.recorded", "data": pred},
        {**st, "kind": "analysis.started", "data": {"analysis_id": "ana-0001", "tool": TOOL, "load_case_id": "R3-uses", "prediction_ids": ["pred-0001"]}},
        {**st, "kind": "analysis.finished", "data": ana},
        {**st, "kind": "comparison.recorded", "data": cmp},
        {**st, "kind": "verdict.recorded", "data": ver},
        {**sysv, "kind": "run.finished", "data": {"status": "done", "stop_code": "done"}},
    ]
    out = []
    for i, ev in enumerate(events):
        ev = {**ev, "run_id": run_id, "ts": run["ts"], "seq": i, "t": 0.0 if i < 2 else 1.0 + i}
        if i == 0:
            ev["data"] = {**ev["data"], "run_id": run_id}
        out.append(ev)
    return out


# What the cut shows (its own state, the boxes' text and the marks' colours).
STATE = """() => {
  const c = window.hiraku.cut;
  const shown = (el) => el.checkVisibility({ opacityProperty: true, visibilityProperty: true }) && el.getBoundingClientRect().width > 0;
  const text = (sel) => [...document.querySelectorAll(sel)].filter(shown).map((el) => el.innerText.trim()).filter(Boolean);
  const stage = [...document.querySelectorAll("#stage *")].filter((el) => shown(el) && !el.closest("pre") && [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim()));
  return {
    now: c.now ?? null,
    text: stage.map((el) => [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join("")).join("\\n"),
    labels: text('[data-kind="label"]'),
    numbers: text('[data-kind="number"]'),
    marks: [...document.querySelectorAll('[data-kind="label"]')].filter(shown).map((el) => [el.textContent.trim(), getComputedStyle(el).color]),
    boxes: Object.fromEntries([...document.querySelectorAll("[data-box]")].map((el) => [el.dataset.box, (() => { const r = el.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; })()])),
    memo: text(".n11-lines p"), memoValue: text(".n11-value"), log: text(".n11-log p"), setting: text(".n11-setting"),
    cause: text(".n12-why"), fix: text(".n12-fix"), diff: text(".n12-diff"), again: text(".n12-again"),
    want: text(".n13-want p, p.n13-want"), gap: text(".n13-gap"), made: text(".n13-made"), use: text(".n13-use p"),
    rows: [...document.querySelectorAll(".n13-row")].map((el) => ({ text: el.innerText.trim(), tick: shown(el.querySelector(".mark")) })),
    code: document.querySelector(".n13-lines")?.textContent ?? null,
    cards: [...document.querySelectorAll(".n13-card")].map((el) => ({ isNew: el.classList.contains("new"), state: el.dataset.state ?? null, top: el.getBoundingClientRect().top, right: el.getBoundingClientRect().right, bottom: el.getBoundingClientRect().bottom, text: el.textContent })),
    tag: [...document.querySelectorAll(".n13-tag")].filter(shown).map((el) => { const r = el.getBoundingClientRect(); return { text: el.textContent, kind: el.dataset.kind ?? null, color: getComputedStyle(el).color, top: r.top, right: r.right }; }),
    caret: !!document.querySelector('.n13-caret[data-kind="moving"]'),
    canvasMoving: !!document.querySelector('.n11-result canvas[data-kind="moving"]'),
    canvasShown: !!document.querySelector(".n11-result canvas") && shown(document.querySelector(".n11-result canvas")),
    none: text(".n13-none"),
  };
}"""

GREEN = "rgb(47, 125, 79)"
RED = "rgb(200, 64, 42)"


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class CutN11N13Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.runs = cls.tmp / "runs"
        cls.layout = story_layout()
        cls.hooks_run = "20261103-230000-n11n12"
        cls.tool_run = "20261103-230000-n13"
        cls.nocode_run = "20261103-230000-n13nocode"
        for run, events in ((cls.hooks_run, hooks_events(cls.hooks_run)), (cls.tool_run, tool_events(cls.tool_run)), (cls.nocode_run, tool_events(cls.nocode_run))):
            (cls.runs / run).mkdir(parents=True)
            (cls.runs / run / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
        (cls.runs / cls.tool_run / "tools").mkdir()
        (cls.runs / cls.tool_run / "tools" / f"{TOOL}-1.py").write_text("# attempt 1 (not shown)\n", encoding="utf-8")
        (cls.runs / cls.tool_run / "tools" / f"{TOOL}-2.py").write_text(CODE, encoding="utf-8")
        cls.servers = []
        cls.base = cls.serve(cls.layout, "layout.json")
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])

    @classmethod
    def serve(cls, layout, name):
        lf = cls.tmp / name
        lf.write_text(json.dumps(layout, ensure_ascii=False), encoding="utf-8")
        server = make_server("127.0.0.1", 0, cls.runs, quiet=True, saved_dir=None, launcher=False, layout_file=lf)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        for s in cls.servers:
            s.shutdown()
            s.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def read(self, cut, run, times):
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.base}/cut.html?cut={cut}&run={run}&t=0")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=120000)
        page.evaluate("window.hiraku.ready")
        out = {}
        for t in times:
            got = page.evaluate(READ, t)
            got["cut"] = page.evaluate(STATE)
            got["fit"] = page.evaluate(FIT, [f'[data-box="{cut}.{b}"]' for b in self.layout[cut] if isinstance(self.layout[cut][b], dict) and "x" in self.layout[cut][b] and b != "code"])
            out[t] = got
            self.check_common(cut, got, t)
        self.assertEqual(errors, [], cut)
        page.close()
        return out

    def check_common(self, cut, got, t):
        """No error; the story's limits and text sizes; the boxes where the layout puts them,
        nothing in the subtitle band, no text past its box; no id or trade word."""
        where = (cut, t)
        self.assertIsNone(got["err"], where)
        lim = limits_of(self.layout, cut)
        for k, n in got["counts"].items():
            self.assertIn(k, KINDS, where)
            self.assertLessEqual(n, lim[k], (where, k, got["items"][k]))
        self.assertEqual(got["page"]["over"], [], where)
        for x in got["texts"]:
            least = lim["body_text_px"] if x["kind"] or x["boxed"] else self.layout["min_text_px"]
            self.assertGreaterEqual(x["px"], least - 0.01, (where, x))
        L = self.layout
        for b, r in L[cut].items():
            if isinstance(r, dict) and "x" in r:
                box = got["cut"]["boxes"][f"{cut}.{b}"]
                self.assertEqual([round(v) for v in box], [r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"]], (where, b))
        self.assertLessEqual(got["fit"]["lowest"], L["subtitle_band"]["y"] + 0.5, where)
        self.assertEqual(got["fit"]["over"], [], where)
        for w in IDS:
            self.assertNotIn(w, got["cut"]["text"], (where, w))

    # ---- N11 --------------------------------------------------------------------------

    def test_n11_the_hooks_stop_then_the_prediction_then_the_run(self):
        T = self.layout["n11"]["times"]
        rec = (T["stop_s"] + T["run_s"]) / 2
        times = [0.0, T["plausible_s"], T["memo_s"], (T["memo_s"] + T["stop_s"]) / 2, T["stop_s"], rec, T["run_s"], self.layout["n11"]["duration_s"] - 0.1]
        seen = self.read("n11", self.hooks_run, times)
        c = {t: g["cut"] for t, g in seen.items()}
        # result: the vehicle and the setting from the request from plausible_s; it plays from run_s
        self.assertFalse(c[0.0]["canvasShown"])
        self.assertEqual(c[0.0]["setting"], [])
        self.assertTrue(c[T["plausible_s"]]["canvasShown"])
        self.assertEqual(c[T["plausible_s"]]["setting"], [f"設定 (要件から): 荷の重心の高さ (形の見本) {SETTING} mm"])
        for t in times:
            self.assertEqual(c[t]["canvasMoving"], T["run_s"] <= t, t)
        # memo: what is estimated and the named inputs (the unnamed one left out), line by line
        full = ["転倒する傾きを、解析の前に見積もる", "左右の輪の間隔\n1,100 mm", "重心の高さ\n560 mm", "全体の重さ\n600 kg"]
        self.assertEqual(c[0.0]["memo"], [])
        self.assertEqual(c[T["memo_s"]]["memo"], full[:1])
        self.assertTrue(0 < len(c[(T["memo_s"] + T["stop_s"]) / 2]["memo"]) < len(full))
        self.assertEqual([m.replace("\t", "\n") for m in c[T["stop_s"]]["memo"]], full)
        # the predicted value only once recorded, after the stop
        self.assertEqual(c[T["stop_s"]]["memoValue"], [])
        self.assertEqual(c[rec]["memoValue"], [f"→ 転倒する傾き {PRED1} °"])
        # log: the stop (red ✕), the prediction, the run (green ✓), each at its time
        self.assertEqual(c[T["memo_s"]]["log"], [])
        self.assertEqual(c[T["stop_s"]]["log"], ["✕ 解析を呼んだ → hooks が止めた: 予測が先"])
        self.assertEqual(c[rec]["log"][1], f"予測を記録した: 転倒する傾き {PRED1} °")
        self.assertEqual(c[T["run_s"]]["log"][2], "✓ 予測があるので、解析が動いた")
        self.assertEqual(c[T["run_s"]]["marks"], [["✕", RED], ["✓", GREEN]])
        # no figure emphasised; the analysis's value is N12's
        for t in times:
            self.assertEqual(c[t]["numbers"], [], t)
            self.assertNotIn(str(ANA), c[t]["text"], t)

    # ---- N12 --------------------------------------------------------------------------

    def test_n12_the_mismatch_its_cause_and_the_fix(self):
        T = self.layout["n12"]["times"]
        times = [T["lead_s"], T["mismatch_s"], T["cause_s"] - 0.1, T["cause_s"], T["fixed_s"] - 0.1, T["fixed_s"], self.layout["n12"]["duration_s"] - 0.1]
        seen = self.read("n12", self.hooks_run, times)
        c = {t: g["cut"] for t, g in seen.items()}
        pct = lambda r: f"{'+' if r > 0 else ''}{r * 100:.1f} %"  # noqa: E731
        for t in times:
            # the two values, large, as recorded (rounded for display only)
            fixed = t >= T["fixed_s"]
            self.assertEqual(c[t]["numbers"], [f"{PRED2 if fixed else PRED1} °", f"{ANA} °"], t)
            self.assertEqual(seen[t]["counts"].get("number"), 2, t)
        # neutral before mismatch_s; then red with the difference and the allowed range
        self.assertEqual(c[T["lead_s"]]["labels"], [])
        self.assertEqual(c[T["mismatch_s"]]["diff"], [f"差 {pct(_rel(PRED1, ANA))} (許す幅 ±10 %) ✕ 食い違い"])
        self.assertEqual(c[T["mismatch_s"]]["marks"], [["✕ 食い違い", RED]])
        # the cause from cause_s, in one line: the requirement's name, cm meant and mm written
        self.assertEqual(c[T["cause_s"] - 0.1]["cause"], [])
        self.assertEqual(c[T["cause_s"]]["cause"], [f"荷の重心の高さ (形の見本): {SETTING} cm のつもりが {SETTING} mm と書かれていた"])
        self.assertEqual(c[T["fixed_s"] - 0.1]["fix"], [])
        # from fixed_s the comparison that resolved it: green, and the fix with "✓ 直った"
        late = c[T["fixed_s"]]
        self.assertEqual(late["diff"], [f"差 {pct(_rel(PRED2, ANA))} (許す幅 ±10 %) ✓ 一致"])
        self.assertEqual(late["again"], ["直してから予測し直した"])
        self.assertEqual(late["fix"], ["直し方: 荷の高さを設計のとおりにして予測し直す ✓ 直った"])
        self.assertEqual(late["marks"], [["✓ 一致", GREEN], ["✓ 直った", GREEN]])

    # ---- N13 --------------------------------------------------------------------------

    def test_n13_the_missing_tool_is_written_checked_and_used(self):
        T = self.layout["n13"]["times"]
        ticks = [T["check_s"] + (i + 1) * (T["shelf_s"] - T["check_s"]) / 4 for i in range(3)]
        times = [T["lead_s"], T["gap_s"], T["write_s"], (T["write_s"] + T["check_s"]) / 2, T["check_s"], ticks[0], ticks[2], T["shelf_s"], self.layout["n13"]["duration_s"] - 0.1]
        seen = self.read("n13", self.tool_run, times)
        c = {t: g["cut"] for t, g in seen.items()}
        self.assertEqual(c[T["lead_s"]]["want"], ["やりたいこと: 家で使うベッドを作りたい (形の見本)"])
        # the shelf from gap_s: the six usable tools checked before (not the unusable one), and the dotted one
        self.assertEqual(c[T["lead_s"]]["gap"], [])
        g = c[T["gap_s"]]
        self.assertEqual([x["isNew"] for x in g["cards"]], [False] * 6 + [True])
        shelf = g["boxes"]["n13.shelf"]
        self.assertLessEqual(g["cards"][-1]["right"], shelf[2] - 2, "the new card inside the shelf")
        self.assertEqual(len({round(x["top"]) for x in g["cards"]}), 1)  # one row
        self.assertEqual(g["cards"][-1]["state"], "missing")
        self.assertEqual(g["gap"], ["点線: 「ベッドの使う回数の疲労」を確かめる道具が無い"])
        # the dotted card's name (a label, red until the checks pass), under it and flush with
        # its right edge; the other cards have no names
        self.assertEqual([(x["text"], x["kind"], x["color"]) for x in g["tag"]], [("使う回数の疲労", "label", RED)])
        self.assertAlmostEqual(g["tag"][0]["right"], g["cards"][-1]["right"], delta=0.5)
        self.assertGreaterEqual(g["tag"][0]["top"], g["cards"][-1]["bottom"])
        self.assertEqual([x["text"] for x in g["cards"]], [""] * 7)
        self.assertEqual(c[T["lead_s"]]["tag"], [])
        # the code from write_s, typed in up to check_s from the analyse method; the caret moves meanwhile
        self.assertEqual(c[T["gap_s"]]["code"], "")
        mid = c[(T["write_s"] + T["check_s"]) / 2]
        self.assertTrue(mid["code"].startswith("    def analyse(self"))
        self.assertTrue(mid["caret"])
        self.assertEqual(mid["cards"][-1]["state"], "writing")
        done = c[T["check_s"]]["code"].split("\n")
        self.assertEqual(done, CODE.split("\n")[6:6 + len(done)])
        self.assertGreater(len(done), len(mid["code"].split("\n")))
        self.assertFalse(c[T["check_s"]]["caret"])
        # the checks from check_s, ticked one by one; the known answer and the tool's as recorded
        self.assertEqual(c[T["write_s"]]["rows"][0]["tick"], False)
        self.assertEqual([r["tick"] for r in c[T["check_s"]]["rows"]], [False, False, False])
        self.assertEqual([r["tick"] for r in c[ticks[0]]["rows"]], [True, False, False])
        self.assertEqual([r["tick"] for r in c[ticks[2]]["rows"]], [True, True, True])
        rows = [[x for x in r["text"].split("\n") if x.strip()] for r in c[ticks[2]]["rows"]]
        self.assertEqual(rows[0], ["基準の強さで壊れるまでの回数", "✓", "答え 2,000,000 回 ・ 道具 2,000,000 回", "出典: 国際溶接学会 (IIW) の疲労の線"])
        self.assertEqual(rows[1], ["人が乗る・降りるときの枠の応力の幅", "✓", "答え 12.34 MPa ・ 道具 12.34 MPa", "出典: 梁の手計算の式"])
        self.assertEqual(rows[2], ["くり返しの力による傷み", "✓", "答え 0.00123 ・ 道具 0.00123", "出典: 梁の手計算の式・国際溶接学会 (IIW) の疲労の線"])
        self.assertEqual(c[ticks[2]]["cards"][-1]["state"], "solid")
        self.assertEqual(c[ticks[2]]["made"], ["Claude がその場で書き、答えの分かっている例で確かめた"])
        self.assertEqual(c[ticks[2]]["tag"][0]["color"], GREEN)
        self.assertEqual(seen[ticks[2]]["counts"].get("label"), 4)  # the three ticks and the new tool's name
        # from shelf_s the analysis made with it: its count of uses, the one figure, and the verdict
        u = c[T["shelf_s"]]
        self.assertEqual(u["tag"], [])  # the shelf gives way to the analysis
        self.assertEqual(u["numbers"], ["4,321 回"])
        self.assertEqual(u["use"][0], "棚に入った「ベッドの使う回数の疲労」で、設計を解析した")
        self.assertEqual(u["use"][-1], "くり返しの力による傷みは、壊れる目安より小さい")
        for t in times:
            self.assertLessEqual(seen[t]["counts"].get("number", 0), 1, t)

    def test_n13_without_the_code_file_says_so(self):
        seen = self.read("n13", self.nocode_run, [self.layout["n13"]["times"]["check_s"]])
        got = next(iter(seen.values()))["cut"]
        self.assertEqual(got["none"], ["道具のコードのファイルが無い"])
        self.assertEqual(got["code"], "")

    # ---- the promo department's check times --------------------------------------------

    def test_at_the_check_times(self):
        """Each cut at layout.json check.times: within the story's limits, the boxes in place, no id."""
        for cut, run in (("n11", self.hooks_run), ("n12", self.hooks_run), ("n13", self.tool_run)):
            times = self.layout["check"]["times"][cut]
            self.assertTrue(times, cut)
            self.read(cut, run, times)

    # ---- missing keys -----------------------------------------------------------------

    def test_missing_keys_are_named(self):
        cases = [("n11", "memo.text_px", self.hooks_run), ("n11", "times.stop_s", self.hooks_run), ("n11", "result.w", self.hooks_run),
                 ("n12", "table.value_px", self.hooks_run), ("n12", "times.fixed_s", self.hooks_run), ("n12", "cause.h", self.hooks_run),
                 ("n13", "times.shelf_s", self.tool_run), ("n13", "code.text_px", self.tool_run), ("n13", "want.y", self.tool_run)]
        for i, (cut, key, run) in enumerate(cases):
            layout = copy.deepcopy(self.layout)
            *up, last = key.split(".")
            node = layout[cut]
            for k in up:
                node = node[k]
            del node[last]
            base = self.serve(layout, f"layout-missing-{i}.json")
            page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
            page.goto(f"{base}/cut.html?cut={cut}&run={run}&t=0")
            page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=120000)
            err = page.evaluate("document.querySelector('.err')?.textContent ?? ''")
            self.assertIn(f"layout.json の {cut} に無い: ", err, (cut, key))
            self.assertIn(key, err.split("に無い: ")[1], (cut, key))
            page.close()


if __name__ == "__main__":
    unittest.main()
