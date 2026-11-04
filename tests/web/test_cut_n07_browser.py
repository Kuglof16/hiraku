"""N07 for viewers who do not know CAE (cut.html?cut=n07).

The plates are coloured by each element's shell stress on bands whose top is the
allowable value; the legend is "応力 [MPa]" and "許容 <limit>" only, and no word of the
trade is on the page. The weak spot is ringed (the element with the largest recorded
value of the version before) from lead_s to the end and at full size through the zoom and
the swap, red, then green once the fixed version is shown; no number or word with it.
"✓ OK" follows the verdict.

Skipped unless HIRAKU_WEB_BROWSER=1 and Playwright is installed (as tests.web.test_browser).
The records are shell_fixture's two versions judged on the hot-spot stress, with its result
and a verdict per version (fail, then pass) and per-member fields whose labels use the
trade's words (shape example: values chosen, not computed); the layout is the story test's
copy of the promo department's layout.json.
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
# Words the page must not show (the viewers do not know them).
TRADE_WORDS = ["特異点", "ホットスポット", "入り隅", "von Mises"]


def judged_events(run_id):
    """shell_fixture's run judged on HOT (the criterion's limit kept): each shell analysis
    gets HOT's result and two per-member fields labelled with the trade's words; version 1's
    verdict fails, version 2's passes. Shape example."""
    events, meshes, _shells = shell_events()
    out = copy.deepcopy(events)
    for e in out:
        e["run_id"] = run_id
    lc = out[0]["data"]["load_cases"][0]
    lc["criterion"]["quantity"] = HOT
    lim = lc["criterion"]["limit"]
    L = lim["value"]
    for e in out:
        a = e["data"] if e["kind"] == "analysis.finished" else None
        if not a or a.get("tool") != "frame_shell":
            continue
        v = a["design_version"]
        names = sorted(set(meshes[v]["element_member"]))
        per = {m: round((1.5 if v == 1 else 0.8) * L * (k + 1) / len(names), 2) for k, m in enumerate(names)}
        a["fields"].append({"kind": "stress", "label": "構造的ホットスポット応力 (部材ごと、形の見本)", "unit": "MPa", "per_member": per})
        a["fields"].append({"kind": "stress", "label": "シェルの von Mises (部材ごとの最大、形の見本)", "unit": "MPa", "per_member": per})
        a["results"].append({"quantity": HOT, "value": max(per.values()), "unit": "MPa"})
    end = out.pop()
    t = end["t"]
    for v, aid, passed in ((1, "ana-0002", False), (2, "ana-0004", True)):
        val = next(e["data"] for e in out if e["kind"] == "analysis.finished" and e["data"]["analysis_id"] == aid)["results"][-1]["value"]
        cmp = {"comparison_id": f"cmp-01{v}", "prediction_id": "pred-0001" if v == 1 else "pred-0002", "analysis_id": aid, "quantity": HOT,
               "predicted": {"value": val, "unit": "MPa"}, "analyzed": {"value": val, "unit": "MPa"},
               "diff_abs": 0.0, "diff_rel": 0.0, "tolerance_rel": lc.get("tolerance_rel", 0.1), "status": "match", "cause_id": None}
        ver = {"verdict_id": f"ver-01{v}", "comparison_id": f"cmp-01{v}", "load_case_id": lc["load_case_id"], "test_id": lc["test_id"],
               "domain": lc["domain"], "quantity": HOT, "value": {"value": val, "unit": "MPa"}, "op": lc["criterion"]["op"],
               "limit": lim, "pass": passed, "margin_pct": round((L - val) / L * 100, 3)}
        base = {"run_id": run_id, "ts": end["ts"], "agent": "cae.strength", "domain": lc["domain"], "design_version": v}
        out += [{**base, "t": t, "kind": "comparison.recorded", "data": cmp}, {**base, "t": t + 0.05, "kind": "verdict.recorded", "data": ver}]
        t += 0.1
    end["t"] = t + 0.1
    out.append(end)
    for i, e in enumerate(out):
        e["seq"] = i
    return out, L


STATE = """() => {
  const c = window.hiraku.cut;
  const lg = document.querySelector('[data-box="n07.legend"]');
  const tick = lg.querySelector(".ticks .allowable");
  const m = c.mark;
  const r = m.getBoundingClientRect();
  const v = document.querySelector('[data-box="n07.view"]').getBoundingClientRect();
  // the element with the largest recorded value of the version before (its field, from the record)
  const vals = c.cases[0].field.per_element;
  let top = 0;
  vals.forEach((x, i) => { if (x > vals[top]) top = i; });
  return {
    legend: lg.innerText.split("\\n").map((s) => s.trim()).filter(Boolean),
    tickTop: tick ? tick.style.top : null,
    text: document.getElementById("stage").innerText,
    label: document.querySelector('[data-box="n07.label"]').textContent,
    cases: c.cases.map((x) => ({ version: x.version, max: x.ct.max, limitKnown: x.ct.limitKnown, colors: !!x.ct.mesh.colors })),
    mark: { opacity: Number(m.style.opacity), w: r.width, tone: m.dataset.tone ?? null, text: m.textContent, kind: m.dataset.kind ?? null,
            inside: r.left + r.width / 2 >= v.left && r.left + r.width / 2 <= v.right && r.top + r.height / 2 >= v.top && r.top + r.height / 2 <= v.bottom },
    focusElem: c.focus.elem, topElem: top,
    viewH: v.height,
  };
}"""


@unittest.skipUnless(ENABLED, "set HIRAKU_WEB_BROWSER=1 with Playwright installed")
class CutN07Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        runs = cls.tmp / "runs"
        cls.layout = story_layout()
        cls.run_id = "20261103-160000-n07"
        events, cls.limit = judged_events(cls.run_id)
        (runs / cls.run_id).mkdir(parents=True)
        (runs / cls.run_id / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
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

    def read(self, times, query=""):
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.base}/cut.html?cut=n07&run={self.run_id}&t=0{query}")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=120000)
        page.evaluate("window.hiraku.ready")
        out = {}
        for t in times:
            got = page.evaluate(READ, t)
            got["n07"] = page.evaluate(STATE)
            out[t] = got
        self.assertEqual(errors, [])
        page.close()
        return out

    def allowable(self):
        return f"許容 {float(f'{self.limit:.4g}'):g}"

    def check_common(self, got, where):
        """No error; the story's limits; the legend is the stress's name and the allowable only,
        the allowable at the bands' top; none of the trade's words on the page."""
        lim = limits_of(self.layout, "n07")
        self.assertIsNone(got["err"], where)
        for k, n in got["counts"].items():
            self.assertLessEqual(n, lim[k], (where, k))
        self.assertEqual(got["page"]["over"], [], where)
        n = got["n07"]
        self.assertEqual(n["legend"], ["応力 [MPa]", self.allowable()], where)
        self.assertEqual(n["tickTop"], "0%", where)
        for w in TRADE_WORDS:
            self.assertNotIn(w, n["text"], (where, w))
        for c in n["cases"]:
            self.assertTrue(c["limitKnown"], (where, c["version"]))
            self.assertAlmostEqual(c["max"], self.limit, places=9)  # the bands' top is the allowable
            self.assertFalse(c["colors"])  # coloured by the element's value

    def test_element_stress_up_to_the_allowable_and_the_weak_spot_ringed(self):
        T = self.layout["n07"]["times"]
        fill = self.layout["n07"]["zoom"]["fill"]
        times = [0.0, T["lead_s"], T["zoom_s"], (T["zoom_s"] + T["zoom_to_s"]) / 2, T["zoom_to_s"], (T["zoom_to_s"] + T["swap_s"]) / 2,
                 T["swap_s"], T["swap_to_s"] + 0.5, self.layout["n07"]["duration_s"] - 0.1]
        seen = self.read(times)
        for t, got in seen.items():
            self.check_common(got, t)
            m = got["n07"]["mark"]
            # no number or word with the ring, and not counted (no data-kind)
            self.assertEqual((m["text"], m["kind"]), ("", None), t)
            # it rings the element with the largest recorded value of the version before
            self.assertEqual(got["n07"]["focusElem"], got["n07"]["topElem"], t)
        self.assertEqual(seen[0.0]["n07"]["mark"]["opacity"], 0)
        # shown from lead_s; from zoom_s to swap_s and on, all the time, on the view
        for t in times[1:]:
            self.assertEqual(seen[t]["n07"]["mark"]["opacity"], 1, t)
            self.assertTrue(seen[t]["n07"]["mark"]["inside"], t)
        # full size (zoom.fill of the view's height) from zoom_to_s through the swap
        inner = seen[T["zoom_to_s"]]["n07"]["viewH"] - 4
        for t in (T["zoom_to_s"], (T["zoom_to_s"] + T["swap_s"]) / 2, T["swap_s"], T["swap_to_s"] + 0.5):
            self.assertAlmostEqual(seen[t]["n07"]["mark"]["w"], fill * inner, delta=1.0)
        self.assertLess(seen[T["zoom_s"]]["n07"]["mark"]["w"], seen[T["zoom_to_s"]]["n07"]["mark"]["w"])
        # red on the version before; after the swap the fixed version: the ring green, "✓ OK"
        self.assertEqual(seen[T["swap_s"]]["n07"]["mark"]["tone"], "red")
        late = seen[T["swap_to_s"] + 0.5]["n07"]
        self.assertEqual(late["mark"]["tone"], "green")
        self.assertEqual(late["label"], "補強の後 ✓ OK")
        # after the swap the page says nothing more than the label and the legend
        self.assertEqual(sorted(s for s in late["text"].split("\n") if s.strip()), sorted(["補強の後 ✓ OK", "応力 [MPa]", self.allowable()]))

    def test_paint_in_the_url_is_not_read(self):
        """The colours are the element stress whatever the URL says (paint=member is gone)."""
        seen = self.read([7.5], "&paint=member")
        self.check_common(seen[7.5], "paint=member")
        self.assertIsNone(seen[7.5]["n07"].get("note"))


if __name__ == "__main__":
    unittest.main()
