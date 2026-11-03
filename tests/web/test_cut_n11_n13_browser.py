"""N11, N12 and N13 for viewers who do not know CAE (cut.html?cut=n11 / n12 / n13).

N11: an analysis called without a prediction is stopped by the hooks, the prediction is
written, then the analysis runs. N12: the hand calculation and the analysis disagree, the
cause (a unit mistaken in the request) is found, and after the fix they agree. N13: a tool
the shelf does not have is written by Claude, checked against known answers and put on the
shelf (what the analyses made with it judged is not shown).

For each cut: the boxes where layout.json puts them and nothing in the subtitle band, what
is shown at each time, the record's numbers as they are, no record id, rule id, tool name
or word of the trade on the page, the story's limits, and a missing layout key named.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser) and Playwright is installed.
The records are made here (shape examples: values chosen, not computed): for N11 / N12
k2_fixture's tilt-table run with a denial, two predictions, two analyses, two comparisons
and a cause; for N13 the contract sample shaped like the experiment department's off-road
run 20261103-234711-offroad-4w: a shelf, a tool made in the run (one failed attempt, then a
usable one with two checks and its code in tools/) and two analyses made with it whose
verdicts fail on the safe side's prediction. The layout is the story test's copy of the
promo department's layout.json (n11-n13 boxes and times unchanged since cce1262; n13's
check.times as of 8b569cf).
"""

import copy
import json
import re
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
       "formula", "spring_roll", "cycles_counted", "fatigue_damage", "S-N", "Miner", "マイナー", "FAT", "load_case",
       "rops", "pushover", "K1", "塑性", "ヒンジ", "tip_deflection", "collapse_load", "R4", "frame_static", "tilt_table"]

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


TOOL = "rops_pushover"
CODE = "\n".join(["import math", "", "", "class RopsPushover:", "    name = 'rops_pushover'", "", "    def analyse(self, design, load_case, params):"]
                 + [f"        x{i} = {i} * 2  # line {i}" for i in range(30)]) + "\n"
# The shape of the experiment department's run 20261103-234711-offroad-4w (tool.checked seq
# 49): two known-answer checks, each with its source. Values chosen, not computed.
CHECKS = [
    {"case": "c1", "quantity": "tip_deflection", "expected": 79.58, "got": 79.58, "unit": "mm", "tolerance_rel": 0.001, "pass": True,
     "source": "closed form delta = P L^3 / (3 E I), I = pi (D^4 - d^4) / 64 (beam theory, any textbook)"},
    {"case": "c2", "quantity": "collapse_load", "expected": 2260.0, "got": 2261.0, "unit": "N", "tolerance_rel": 0.001, "pass": True,
     "source": "sway mechanism of a fixed-base portal H_c = 4 Mp / h (B. G. Neal, The Plastic Methods of Structural Analysis)"},
]
CLEARANCE_PRED = -413.5  # mm, the safe side's prediction the verdict takes (shape example)
# The shelf's tools the run did not check, as the system names them (hiraku/tools/shelf.py).
UNCHECKED = ["frame_static", "frame_shell", "step_climb", "thermal"]
# A third check (shape example), for a tool whose ticks, name and "照合 ✓" would be more
# labels than the story allows.
CHECK3 = {"case": "c3", "quantity": "tip_deflection", "expected": 12.0, "got": 12.0, "unit": "mm", "tolerance_rel": 0.001, "pass": True,
          "source": "closed form delta = P L^3 / (3 E I)"}


def tool_events(run_id, checks=CHECKS):
    """The contract sample shaped like the off-road run: the system naming the shelf's tools it
    does not check, two checked builtin tools (one not usable), a tool made by Claude (attempt 1
    not usable, attempt 2 usable with `checks`, two by default), two analyses made with it in two
    versions, and their verdicts failing on the safe side's prediction. Shape example."""
    src = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    run, design = copy.deepcopy(src[0]), copy.deepcopy(src[1])
    run["data"]["want"] = "林道を走れる小さな車両 (形の見本)。横転しても、乗っている人を守りたい"
    lc = {"load_case_id": "R4-rops", "test_id": "R4", "domain": "strength", "title": "横転の保護枠 (形の見本)", "params": {}, "tolerance_rel": 0.25,
          "criterion": {"quantity": "rops_occupant_clearance", "op": ">=", "limit": {"value": 0.0, "unit": "mm"}}}
    run["data"]["load_cases"] = [lc]
    sysv = {"agent": "system"}
    st = {"agent": "cae.strength", "domain": "strength", "design_version": 1}

    def checked(name, origin, usable, checks=()):
        return {**sysv, "kind": "tool.checked", "data": {"name": name, "title": f"{name} の道具 (形の見本)", "domain": "strength", "base": "K1",
                                                         "tests": ["R4"], "origin": origin, "usable": usable, "checks": list(checks)}}

    made = {**checked(TOOL, "claude", True, checks), **st}
    made["data"]["title"] = "K1 の骨組で、ロールバーに塑性ヒンジを順に入れて押す (形の見本) と、乗員の空間との隙間"
    design2 = copy.deepcopy(design)
    design2["design_version"] = 2
    design2["data"].update({"design_version": 2, "parent_version": 1, "change_reason": "a thicker roll bar (shape example)"})

    def used(v):
        n = f"000{v}"
        h = {**st, "design_version": v}
        pred = {"prediction_id": f"pred-{n}", "design_version": v, "load_case_id": "R4-rops", "test_id": "R4", "domain": "strength", "agent": "cae.strength",
                "quantity": "rops_occupant_clearance", "value": CLEARANCE_PRED, "unit": "mm", "formula_id": "frame_static.rops_occupant_clearance",
                "inputs": {}, "assumptions": [], "created_at": run["ts"]}
        ana = {"analysis_id": f"ana-{n}", "tool": TOOL, "base": "K1", "design_version": v, "load_case_id": "R4-rops", "prediction_ids": [f"pred-{n}"],
               "status": "ok", "results": [{"quantity": "rops_occupant_clearance", "value": 29.1, "unit": "mm"}]}
        cmp = {"comparison_id": f"cmp-{n}", "prediction_id": f"pred-{n}", "analysis_id": f"ana-{n}", "quantity": "rops_occupant_clearance",
               "predicted": {"value": CLEARANCE_PRED, "unit": "mm"}, "analyzed": {"value": 29.1, "unit": "mm"}, "diff_abs": 29.1 - CLEARANCE_PRED,
               "diff_rel": (29.1 - CLEARANCE_PRED) / abs(CLEARANCE_PRED), "tolerance_rel": 0.25, "status": "mismatch", "cause_id": f"cause-{n}"}
        cause = {"cause_id": f"cause-{n}", "comparison_id": f"cmp-{n}", "cause": "the hand calculation is coarse (shape example)", "fix": "none",
                 "resolved": True, "safe_side": True, "safe_side_reason": "the prediction is the unfavourable side (shape example)"}
        ver = {"verdict_id": f"ver-{n}", "comparison_id": f"cmp-{n}", "load_case_id": "R4-rops", "test_id": "R4", "domain": "strength",
               "quantity": "rops_occupant_clearance", "value": {"value": CLEARANCE_PRED, "unit": "mm"}, "op": ">=", "limit": {"value": 0.0, "unit": "mm"},
               "pass": False, "margin": {"value": CLEARANCE_PRED, "unit": "mm"}, "margin_pct": None,
               "basis": "explained_safe_side", "value_from": "prediction", "cause_id": f"cause-{n}"}
        return [
            {**h, "kind": "prediction.recorded", "data": pred},
            {**h, "kind": "analysis.started", "data": {"analysis_id": f"ana-{n}", "tool": TOOL, "load_case_id": "R4-rops", "prediction_ids": [f"pred-{n}"]}},
            {**h, "kind": "analysis.finished", "data": ana},
            {**h, "kind": "comparison.recorded", "data": cmp},
            {**h, "kind": "cause.recorded", "data": cause},
            {**h, "kind": "verdict.recorded", "data": ver},
        ]

    events = [
        run, design,
        {**sysv, "kind": "agent.said", "text": "この実行の試験 (R4) には使わないので確かめない道具: " + "、".join(UNCHECKED), "data": {}},
        checked("tilt_table", "builtin", True), checked("broken", "builtin", False),
        {**checked(TOOL, "claude", False, [{**CHECKS[0], "got": 1.0, "pass": False}]), **st},
        made,
        *used(1),
        design2,
        *used(2),
        {**sysv, "kind": "run.finished", "data": {"status": "stopped", "stop_code": "round_limit"}},
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
    want: text(".n13-want p, p.n13-want"), gap: text(".n13-gap"), made: text(".n13-made"),
    rows: [...document.querySelectorAll(".n13-row")].map((el) => ({ text: el.innerText.trim(), tick: shown(el.querySelector(".mark")) })),
    code: document.querySelector(".n13-lines")?.textContent ?? null,
    cards: [...document.querySelectorAll(".n13-card")].map((el) => { const r = el.getBoundingClientRect(); return { isNew: el.classList.contains("new"), state: el.dataset.state ?? null, left: r.left, top: r.top, right: r.right, bottom: r.bottom, text: el.textContent }; }),
    checked: [...document.querySelectorAll(".n13-checked")].filter(shown).map((el) => { const r = el.getBoundingClientRect(); return { text: el.innerText.replace(/\\s+/g, " ").trim(), kind: el.dataset.kind ?? null, color: getComputedStyle(el).color, left: r.left, top: r.top, right: r.right, bottom: r.bottom }; }),
    tag: [...document.querySelectorAll(".n13-tag")].filter(shown).map((el) => { const r = el.getBoundingClientRect(); return { text: el.innerText.trim(), kind: el.dataset.kind ?? null, color: getComputedStyle(el).color, top: r.top, right: r.right }; }),
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
        cls.three_run = "20261103-230000-n13three"
        for run, events in ((cls.hooks_run, hooks_events(cls.hooks_run)), (cls.tool_run, tool_events(cls.tool_run)), (cls.nocode_run, tool_events(cls.nocode_run)),
                            (cls.three_run, tool_events(cls.three_run, CHECKS + [CHECK3]))):
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

    def test_n13_the_missing_tool_is_written_checked_and_put_on_the_shelf(self):
        T = self.layout["n13"]["times"]
        end = self.layout["n13"]["duration_s"] - 0.1
        ticks = [T["check_s"] + (i + 1) * (T["shelf_s"] - T["check_s"]) / 3 for i in range(2)]
        times = [T["lead_s"], T["gap_s"], T["write_s"], (T["write_s"] + T["check_s"]) / 2, T["check_s"], ticks[0], ticks[1], T["shelf_s"], end]
        seen = self.read("n13", self.tool_run, times)
        c = {t: g["cut"] for t, g in seen.items()}
        # the want's last sentence (the wish)
        self.assertEqual(c[T["lead_s"]]["want"], ["やりたいこと: 横転しても、乗っている人を守りたい"])
        # the shelf from gap_s: the tools the run named as not checked, the usable one checked
        # before (not the unusable one, not the made tool's failed attempt), and the dotted one
        self.assertEqual(c[T["lead_s"]]["gap"], [])
        g = c[T["gap_s"]]
        self.assertEqual([x["isNew"] for x in g["cards"]], [False] * (len(UNCHECKED) + 1) + [True])
        shelf = g["boxes"]["n13.shelf"]
        self.assertLessEqual(g["cards"][-1]["right"], shelf[2] - 2, "the new card inside the shelf")
        self.assertEqual(len({round(x["top"]) for x in g["cards"]}), 1)  # one row
        self.assertEqual(g["cards"][-1]["state"], "missing")
        self.assertEqual(g["gap"], ["点線: 「転倒の守りを確かめる道具」が棚に無い"])
        # the dotted card's name (a label, red until the checks pass), under it and flush with
        # its right edge; the other cards have no names; "照合 ✓" not yet
        self.assertEqual([(x["text"], x["kind"], x["color"]) for x in g["tag"]], [("転倒の守りを確かめる道具", "label", RED)])
        self.assertAlmostEqual(g["tag"][0]["right"], g["cards"][-1]["right"], delta=0.5)
        self.assertGreaterEqual(g["tag"][0]["top"], g["cards"][-1]["bottom"])
        self.assertEqual([x["text"] for x in g["cards"]], [""] * (len(UNCHECKED) + 1) + ["照合✓"])
        self.assertEqual(g["checked"], [])
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
        self.assertEqual([r["tick"] for r in c[T["check_s"]]["rows"]], [False, False])
        self.assertEqual([r["tick"] for r in c[ticks[0]]["rows"]], [True, False])
        self.assertEqual(c[ticks[0]]["cards"][-1]["state"], "writing")
        self.assertEqual(c[ticks[0]]["tag"][0]["color"], RED)
        self.assertEqual(c[ticks[0]]["checked"], [])
        self.assertEqual([r["tick"] for r in c[ticks[1]]["rows"]], [True, True])
        rows = [[x for x in r["text"].split("\n") if x.strip()] for r in c[ticks[1]]["rows"]]
        self.assertEqual(rows[0], ["片持ちの管の先のたわみ", "✓", "答え 79.58 mm ・ 道具 79.58 mm", "出典: 梁の手計算の式"])
        self.assertEqual(rows[1], ["門形の枠が横に崩れる力", "✓", "答え 2,260 N ・ 道具 2,261 N", "出典: 骨組が崩れる力の教科書 (Neal)"])
        # once both are ticked: the new card is solid in the shelf with "照合 ✓" on it, and its
        # name green as that mark (the tool passed its checks), to the end
        for t in (ticks[1], T["shelf_s"], end):
            self.assertEqual(c[t]["cards"][-1]["state"], "solid", t)
            self.assertEqual([x["isNew"] for x in c[t]["cards"]], [False] * (len(UNCHECKED) + 1) + [True], t)
            self.assertEqual([(x["text"], x["color"]) for x in c[t]["tag"]], [("転倒の守りを確かめる道具", GREEN)], t)
            self.assertEqual(len(c[t]["checked"]), 1, t)
            ck = c[t]["checked"][0]
            self.assertEqual((ck["text"], ck["kind"], ck["color"]), ("照合 ✓", "label", GREEN), t)
            card = c[t]["cards"][-1]
            self.assertTrue(card["left"] <= ck["left"] and ck["right"] <= card["right"] and card["top"] <= ck["top"] and ck["bottom"] <= card["bottom"], (t, ck, card))
            self.assertEqual(c[t]["made"], ["Claude がその場で書き、答えの分かっている例で確かめた"], t)
            self.assertEqual(c[t]["gap"], [], t)
            self.assertEqual(c[t]["marks"], [["照合✓", GREEN], ["転倒の守りを確かめる道具", GREEN], ["✓", GREEN], ["✓", GREEN]], t)
            self.assertEqual(seen[t]["counts"].get("label"), 4, t)  # the two ticks, the name and "照合 ✓"
        # the last screen: "転倒の守り" never alone (only in the tool's name), "照合 ✓" shown
        self.assertEqual(re.findall(r"転倒の守り(?!を確かめる道具)", c[end]["text"]), [])
        self.assertIn("転倒の守りを確かめる道具", c[end]["text"])
        self.assertIn("照合 ✓", [x["text"] for x in c[end]["checked"]])
        # the analyses made with it are not shown: no figure, no verdict (they fail on the safe
        # side's prediction), nothing red once the checks have passed
        for t in times:
            self.assertEqual(c[t]["numbers"], [], t)
            self.assertNotIn("目安", c[t]["text"], t)
            self.assertNotIn("解析した", c[t]["text"], t)
            self.assertNotIn("413", c[t]["text"], t)
            self.assertNotIn("29.1", c[t]["text"], t)
        self.assertNotIn(RED, [m[1] for m in c[end]["marks"]])

    def test_n13_three_checks_join_the_mark_to_the_name(self):
        """Three ticks, the name and "照合 ✓" would be 5 labels (the story allows 4): the mark
        joins the name, one label, shown only once the checks have passed."""
        T = self.layout["n13"]["times"]
        end = self.layout["n13"]["duration_s"] - 0.1
        seen = self.read("n13", self.three_run, [T["gap_s"], end])
        g, e = seen[T["gap_s"]]["cut"], seen[end]["cut"]
        self.assertEqual([(x["text"], x["color"]) for x in g["tag"]], [("転倒の守りを確かめる道具", RED)])
        self.assertEqual([(x["text"], x["color"]) for x in e["tag"]], [("転倒の守りを確かめる道具 ・ 照合 ✓", GREEN)])
        self.assertEqual(e["checked"], [])
        self.assertEqual(seen[end]["counts"].get("label"), 4)
        self.assertEqual(re.findall(r"転倒の守り(?!を確かめる道具)", e["text"]), [])

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
