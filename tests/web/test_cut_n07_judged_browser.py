"""N07 when the load case is judged on another quantity than the colours (cut.html?cut=n07).

The video run judges the joint on the hot-spot stress (max_hot_spot_stress) while the
mesh's colours are the element stress. The record has no field per element of the judged
quantity (the contract's field has no quantity), so the colours stay the element stress
and the legend says in two short lines what the colours are and what is judged, with the
recorded value and the allowable. "✓ 直った" stays the verdict's.

Skipped unless HIRAKU_WEB_BROWSER=1 and Playwright is installed (as tests.web.test_browser).
The records are shell_fixture's two versions with a hot-spot criterion and hot-spot
results added (shape example: the values are chosen, not computed); the layout is the
story test's copy of the promo department's layout.json.
"""

import copy
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import make_server
from tests.web.shell_fixture import shell_events
from tests.web.test_browser import ENABLED, sync_playwright
from tests.web.test_cut_story_browser import READ, limits_of, story_layout

HOT = "max_hot_spot_stress"


def hot_spot_events(run_id, v1, v2, verdict=None):
    """shell_fixture's run judged on the hot-spot stress: the load case's criterion is HOT
    (the same limit), the shell analyses of versions 1 and 2 record HOT as v1 / v2 MPa
    (shape example). verdict: None, or whether a verdict on version 2 passes (its value v2)."""
    events, _meshes, _shells = shell_events()
    out = copy.deepcopy(events)
    for e in out:
        e["run_id"] = run_id
    lc = out[0]["data"]["load_cases"][0]
    lc["criterion"]["quantity"] = HOT
    lim = lc["criterion"]["limit"]
    for e in out:
        if e["kind"] == "analysis.finished" and e["data"]["tool"] == "frame_shell":
            v = v1 if e["data"]["design_version"] == 1 else v2
            e["data"]["results"].append({"quantity": HOT, "value": v, "unit": "MPa"})
    if verdict is not None:
        cmp = {"comparison_id": "cmp-0002", "prediction_id": "pred-0002", "analysis_id": "ana-0004", "quantity": HOT,
               "predicted": {"value": v2, "unit": "MPa"}, "analyzed": {"value": v2, "unit": "MPa"},
               "diff_abs": 0.0, "diff_rel": 0.0, "tolerance_rel": lc.get("tolerance_rel", 0.1), "status": "match", "cause_id": None}
        ver = {"verdict_id": "ver-0002", "comparison_id": "cmp-0002", "load_case_id": lc["load_case_id"], "test_id": lc["test_id"],
               "domain": lc["domain"], "quantity": HOT, "value": {"value": v2, "unit": "MPa"}, "op": lc["criterion"]["op"],
               "limit": lim, "pass": verdict, "margin_pct": round((lim["value"] - v2) / lim["value"] * 100, 3)}
        end = out.pop()
        base = {"run_id": run_id, "ts": end["ts"], "agent": "cae.strength", "domain": lc["domain"], "design_version": 2}
        out += [{**base, "t": end["t"], "kind": "comparison.recorded", "data": cmp}, {**base, "t": end["t"] + 0.1, "kind": "verdict.recorded", "data": ver}]
        end["t"] += 0.2
        out.append(end)
    for i, e in enumerate(out):
        e["seq"] = i
    return out, lim["value"]


LEGEND = """() => {
  const lg = document.querySelector('[data-box="n07.legend"]');
  const shown = (el) => el.checkVisibility({ visibilityProperty: true }) && el.getBoundingClientRect().height > 0;
  const R = lg.getBoundingClientRect();
  const ex = lg.querySelector(".explain");
  return {
    colourBy: lg.dataset.colourBy ?? null,
    lines: ex ? [...ex.querySelectorAll("p")].filter(shown).map((p) => ({ text: p.textContent.replace(/\\s+/g, " ").trim(), px: parseFloat(getComputedStyle(p).fontSize),
      bottom: p.getBoundingClientRect().bottom, right: p.getBoundingClientRect().right, kind: p.closest("[data-kind]")?.dataset.kind ?? null })) : [],
    box: [R.left, R.top, R.right, R.bottom],
    label: document.querySelector(".n07-label").textContent,
  };
}"""


@unittest.skipUnless(ENABLED, "set HIRAKU_WEB_BROWSER=1 with Playwright installed")
class CutN07JudgedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        runs = cls.tmp / "runs"
        cls.layout = story_layout()
        _, limit = hot_spot_events("x", 0.0, 0.0)
        # shape example: version 1 over the allowable, version 2 under it (one decimal each)
        cls.v1 = round(limit * 2.5, 1) + 0.02
        cls.v2 = round(limit * 0.9, 1) + 0.02
        cls.limit = limit
        cls.runs = {
            "plain": "20261103-150000-n07-hot",
            "pass": "20261103-150000-n07-hotpass",
            "fail": "20261103-150000-n07-hotfail",
        }
        for key, verdict in (("plain", None), ("pass", True), ("fail", False)):
            events, _ = hot_spot_events(cls.runs[key], cls.v1, cls.v2, verdict)
            (runs / cls.runs[key]).mkdir(parents=True)
            (runs / cls.runs[key] / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
        lf = cls.tmp / "layout.json"
        lf.write_text(json.dumps(cls.layout, ensure_ascii=False), encoding="utf-8")
        cls.server = make_server("127.0.0.1", 0, runs, quiet=True, saved_dir=None, launcher=False, layout_file=lf)
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

    def read(self, key, times):
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.base}/cut.html?cut=n07&run={self.runs[key]}&t=0")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=120000)
        page.evaluate("window.hiraku.ready")
        out = {}
        for t in times:
            got = page.evaluate(READ, t)
            got["legend"] = page.evaluate(LEGEND)
            out[t] = got
        self.assertEqual(errors, [])
        page.close()
        return out

    def judged(self, v, sign):
        return f"判定: ホットスポット応力 {v:.1f} MPa {sign} 許容 {self.limit:.1f}"

    def check_story(self, got, where):
        """The story's limits: one legend, one number, one label; the lines are text of the
        legend's box, not another number, at least the body text size, inside the box."""
        lim = limits_of(self.layout, "n07")
        self.assertIsNone(got["err"], where)
        for k, n in got["counts"].items():
            self.assertLessEqual(n, lim[k], (where, k))
        self.assertEqual(got["counts"].get("number"), 1, where)
        self.assertEqual(got["page"]["over"], [], where)
        box = got["legend"]["box"]
        for x in got["legend"]["lines"]:
            self.assertIsNone(x["kind"], (where, x))
            self.assertGreaterEqual(x["px"], lim["body_text_px"], (where, x))
            self.assertLessEqual(x["bottom"], box[3] + 0.5, (where, x))
            self.assertLessEqual(x["right"], box[2] + 0.5, (where, x))

    def test_legend_says_what_the_colours_are_and_what_is_judged(self):
        """No verdict: the line compares the recorded hot-spot stress with the allowable (the
        operator that holds), for the version shown; no "✓ 直った" without a verdict on the case."""
        swap_to = self.layout["n07"]["times"]["swap_to_s"]
        seen = self.read("plain", [0.5, 7.5, swap_to + 1.0])
        for t, got in seen.items():
            self.check_story(got, t)
            lg = got["legend"]
            self.assertEqual(lg["colourBy"], "element")
            after = t >= swap_to
            want = self.judged(self.v2, "≤") if after else self.judged(self.v1, ">")
            self.assertEqual([x["text"] for x in lg["lines"]], ["色: 要素の応力", want], t)

    def test_fixed_follows_the_verdict_and_the_line_its_value(self):
        swap_to = self.layout["n07"]["times"]["swap_to_s"]
        for key, passed in (("pass", True), ("fail", False)):
            got = self.read(key, [swap_to + 1.0])[swap_to + 1.0]
            self.check_story(got, key)
            lg = got["legend"]
            # the line writes the verdict's value and what holds against the limit; "✓ 直った" is the verdict's
            self.assertEqual(lg["lines"][1]["text"], self.judged(self.v2, "≤"), key)
            self.assertEqual("直った" in lg["label"], passed, key)


if __name__ == "__main__":
    unittest.main()
