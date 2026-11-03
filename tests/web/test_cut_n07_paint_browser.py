"""N07's two ways to colour the plates (cut.html?cut=n07[&paint=element|member]).

The viewers do not know CAE: the legend is "応力 [MPa]" and "許容 <limit>" only, and no
word of the trade is on the page. A (paint=element, the default) colours each element by
its shell stress on bands whose top is the allowable value; B (paint=member) colours each
element by its member's value of the quantity the load case is judged on, red over the
allowable and green under it, as the verdicts say; without that field B says so and paints
as A.

Skipped unless HIRAKU_WEB_BROWSER=1 and Playwright is installed (as tests.web.test_browser).
The records are shell_fixture's two versions judged on the hot-spot stress, with a
per-member field of it (shape example: values chosen, not computed; version 1 has one
member over the allowable, version 2 none) and a verdict per version; the layout is the
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
# Words the page must not show (the viewers do not know them).
TRADE_WORDS = ["特異点", "ホットスポット", "入り隅", "von Mises"]
GREEN_RGB = "#2f7d4f"


def paint_events(run_id, member_field=True, verdicts=True):
    """shell_fixture's run judged on HOT (the criterion's limit kept). Each shell analysis gets
    a per-member field of HOT and its result (the field's largest value), and a per-member
    decoy field of another stress with another largest value; their labels use the trade's
    words (the page must not show them). Version 1: the member with the most elements at 1.5 x
    the limit, the others at 0.5 x; version 2: every member at 0.8 x. verdicts: a comparison
    and a verdict per version (fail, pass). Shape example."""
    events, meshes, _shells = shell_events()
    out = copy.deepcopy(events)
    for e in out:
        e["run_id"] = run_id
    lc = out[0]["data"]["load_cases"][0]
    lc["criterion"]["quantity"] = HOT
    lim = lc["criterion"]["limit"]
    L = lim["value"]
    over = {}
    for e in out:
        a = e["data"] if e["kind"] == "analysis.finished" else None
        if not a or a.get("tool") != "frame_shell":
            continue
        v = a["design_version"]
        members = meshes[v]["element_member"]
        names = sorted(set(members))
        top = max(names, key=members.count)
        per = {m: round((1.5 if (v == 1 and m == top) else 0.5 if v == 1 else 0.8) * L, 2) for m in names}
        over[v] = top if v == 1 else None
        if member_field:
            a["fields"].append({"kind": "stress", "label": "構造的ホットスポット応力 (部材ごと、形の見本)", "unit": "MPa", "per_member": per})
        a["fields"].append({"kind": "stress", "label": "シェルの von Mises (部材ごとの最大、形の見本)", "unit": "MPa",
                            "per_member": {m: x * 1.1 + 1.0 for m, x in per.items()}})
        a["results"].append({"quantity": HOT, "value": max(per.values()), "unit": "MPa"})
    if verdicts:
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
    return out, L, over


STATE = """() => {
  const c = window.hiraku.cut;
  const lg = document.querySelector('[data-box="n07.legend"]');
  const tick = lg.querySelector(".ticks .allowable");
  return {
    paint: c.paint, legendPaint: lg.dataset.paint,
    legend: lg.innerText.split("\\n").map((s) => s.trim()).filter(Boolean),
    tickTop: tick ? tick.style.top : null,
    note: document.querySelector(".n07-note")?.textContent ?? null,
    text: document.getElementById("stage").innerText,
    cases: c.cases.map((x) => ({ version: x.version, max: x.ct.max, limitKnown: x.ct.limitKnown, fixed: !!x.fixed,
      states: x.ct.mesh.states ?? null, colors: x.ct.mesh.colors ? [...new Set(x.ct.mesh.colors)] : null,
      members: x.ct.mesh.rec.element_member })),
    red: c.spec.overlay.color,
    tone: c.mark.dataset.tone ?? null,
  };
}"""


@unittest.skipUnless(ENABLED, "set HIRAKU_WEB_BROWSER=1 with Playwright installed")
class CutN07PaintTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        runs = cls.tmp / "runs"
        cls.layout = story_layout()
        cls.runs = {"full": "20261103-160000-n07-paint", "bare": "20261103-160000-n07-nomember"}
        for key, kw in (("full", {}), ("bare", {"member_field": False})):
            events, cls.limit, cls.over = paint_events(cls.runs[key], **kw)
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

    def read(self, key, query, times):
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.base}/cut.html?cut=n07&run={self.runs[key]}&t=0{query}")
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
        """No error; the story's limits; the legend is the stress's name and the allowable only;
        none of the trade's words on the page."""
        lim = limits_of(self.layout, "n07")
        self.assertIsNone(got["err"], where)
        for k, n in got["counts"].items():
            self.assertLessEqual(n, lim[k], (where, k))
        self.assertEqual(got["page"]["over"], [], where)
        n = got["n07"]
        self.assertEqual(n["legend"], ["応力 [MPa]", self.allowable()], where)
        for w in TRADE_WORDS:
            self.assertNotIn(w, n["text"], (where, w))

    def test_a_paints_the_element_stress_up_to_the_allowable(self):
        """paint=element (and no paint): the bands' top is the load case's allowable value, for both versions."""
        swap_to = self.layout["n07"]["times"]["swap_to_s"]
        for query in ("", "&paint=element"):
            seen = self.read("full", query, [0.5, 7.5, swap_to + 1.0])
            for t, got in seen.items():
                self.check_common(got, (query, t))
                n = got["n07"]
                self.assertEqual((n["paint"], n["legendPaint"]), ("element", "element"))
                self.assertEqual(n["tickTop"], "0%")  # the allowable at the bar's top
                self.assertIsNone(n["note"])
                for c in n["cases"]:
                    self.assertTrue(c["limitKnown"], c["version"])
                    self.assertAlmostEqual(c["max"], self.limit, places=9)
                    self.assertIsNone(c["colors"])  # coloured by value, not by member

    def test_b_paints_each_member_as_the_verdict_says(self):
        """paint=member: version 1's member over the allowable is red and the record's verdict
        fails it; every member of version 2 is green and its verdict passes ("✓ 直った")."""
        swap_to = self.layout["n07"]["times"]["swap_to_s"]
        seen = self.read("full", "&paint=member", [0.5, 7.5, swap_to + 1.0])
        for t, got in seen.items():
            self.check_common(got, t)
            n = got["n07"]
            self.assertEqual((n["paint"], n["legendPaint"]), ("member", "member"))
            self.assertIsNone(n["note"])
            v1, v2 = n["cases"]
            red = n["red"]
            # version 1: red exactly on the elements of the member over the allowable
            for st, m in zip(v1["states"], v1["members"]):
                self.assertEqual(st, "over" if m == self.over[1] else "pass", m)
            self.assertEqual(set(v1["colors"]), {red, GREEN_RGB})
            self.assertFalse(v1["fixed"])  # its verdict fails
            # version 2: every element green, and its verdict passes
            self.assertEqual(set(v2["states"]), {"pass"})
            self.assertEqual(v2["colors"], [GREEN_RGB])
            self.assertTrue(v2["fixed"])
        late = seen[swap_to + 1.0]
        self.assertIn("直った", late["n07"]["text"])
        # the ring is red on the version before, green once the fixed version is shown
        self.assertEqual((seen[7.5]["n07"]["tone"], late["n07"]["tone"]), ("red", "green"))

    def test_b_without_the_member_field_says_so_and_paints_as_a(self):
        seen = self.read("bare", "&paint=member", [0.5, 7.5])
        for t, got in seen.items():
            self.check_common(got, t)
            n = got["n07"]
            self.assertEqual(n["note"], "記録に部材ごとの値が無い")
            self.assertEqual((n["paint"], n["legendPaint"]), ("element", "element"))
            self.assertEqual(n["tickTop"], "0%")
            for c in n["cases"]:
                self.assertIsNone(c["colors"])
                self.assertAlmostEqual(c["max"], self.limit, places=9)


if __name__ == "__main__":
    unittest.main()
