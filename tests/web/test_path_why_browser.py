"""Why a verdict on the tool's value after the load path was checked by hand (basis path_checked) is
OK or NG, in one line (web/js/why_line.js), and the log's ids by their names (web/js/plain.js).

path_checked (hiraku.schema.json verdict.basis, cause.tool_decides): the hand calculation does not
reach the value, the load path was checked by hand in the version (a load_path.free_parts
prediction of 0, the analysis' unsupported_load 0), so the analysis' value is held to the
criterion. The line reads every number from the records; this test builds the expected line from
events.jsonl by itself (expected_line) and holds the screen to it:

- 160725 v6 R7-hand-300N (ver-0070: grip_deflection 0.9253 mm <= 5 mm, path_checked): the line on
  ③'s right column under 判定, under the 3D (the strip's いま card, while the verdict is the newest
  word), and in the present mode's card; numbers equal to the record's;
- 160725 at its end, ④: the strength tile's verdict (R1-step100, ver-0092, path_checked) has the line
  under its number;
- contract-shaped small samples (the records of ver-0070, cut down): a missing load_path prediction
  leaves its clause out, an NG says NG, a formula that is not a bound says 値に届かない, another
  basis gives no line;
- the log (plain.js plainLineHtml) of 160725: a part's or member's id is said by the design's name
  (rail_R_S1S2 -> 右のレール, battery・battery2 -> the design's names), a record's number by its kind
  (pred-0119 -> 予測), an id without a name taken out, and no particle is left alone between two
  taken out (「力の道は と の 支えの無い荷重 0」).

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import formula_titles, make_server
from tests.web.test_browser import ENABLED, sync_playwright
from tests.web.test_layout_lint_browser import REPO_DIR

SHOWCASE = REPO_DIR / "showcase"
RUN = "20261105-160725-offroad-4w"
T_R7 = 3420.0  # just after ver-0070 (t 3418.75), before R7-foot-500N's first record (t 3435.687)
BOUND_TITLE = "上から押さえる値"  # why_line.js BOUND_TITLE


def events_of(run: str) -> list[dict]:
    return [json.loads(line) for line in (SHOWCASE / run / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def fmt4(v: float) -> str:
    """labels.js fmt(v, 4) for the values here (under 1000, at least 0.001)."""
    return "0" if v == 0 else f"{v:.4g}"


def records(events: list[dict], kind: str, key: str) -> dict:
    """The newest record of each id (prediction / analysis / comparison / verdict events)."""
    out = {}
    for e in events:
        if e["kind"] == kind and key in (e.get("data") or {}):
            out[e["data"][key]] = {**e["data"], "_v": e.get("design_version")}
    return out


def expected_line(events: list[dict], verdict_id: str) -> tuple[str, dict]:
    """The line why_line.js should give for a verdict, built from the records here."""
    titles = {f["formula_id"]: f["title"] for f in formula_titles()["formulas"]}
    verdict = records(events, "verdict.recorded", "verdict_id")[verdict_id]
    cmp = records(events, "comparison.recorded", "comparison_id")[verdict["comparison_id"]]
    pred = records(events, "prediction.recorded", "prediction_id")[cmp["prediction_id"]]
    anas = records(events, "analysis.finished", "analysis_id")
    ana = anas[cmp["analysis_id"]]
    v = ana["design_version"]
    unit = lambda q: f"{fmt4(q['value'])} {q['unit']}"
    bound = BOUND_TITLE in titles.get(pred["formula_id"], "")
    hand = [p for p in records(events, "prediction.recorded", "prediction_id").values()
            if p.get("formula_id") == "load_path.free_parts" and p["design_version"] == v
            and (p["load_case_id"] == verdict["load_case_id"] or p["quantity"] == "free_parts")]
    free = ([p for p in hand if p["value"] == 0] or hand)[-1]["value"]
    lost = [r["value"] for r in ana["results"] if r["quantity"] == "unsupported_load"][0]
    head = f"手計算は上限のみ ({unit(cmp['predicted'])})" if bound else f"手計算 ({unit(cmp['predicted'])}) は値に届かない"
    path = f"力の道がつながっていることを確かめ (手計算でつながらない部品 {fmt4(free)}、解析で支えの無い荷重 {fmt4(lost)})"
    judged = f"解析の値 {unit(verdict['value'])} を基準 {unit(verdict['limit'])} と比べて {'OK' if verdict['pass'] else 'NG'}"
    parts = {"predicted": cmp["predicted"]["value"], "bound": bound, "freeParts": free, "unsupported": lost,
             "value": verdict["value"]["value"], "limit": verdict["limit"]["value"]}
    return f"{head}。{path}、{judged}", parts


WHY = """async ([vid, events]) => {
  const st = await import('/js/state.js');
  const { pathCheckedWhy } = await import('/js/why_line.js');
  const got = await (await fetch('/api/formulas')).json();
  const titles = new Map(got.formulas.map((f) => [f.formula_id, f.title]));
  let s = window.hiraku.state();
  if (events) { s = st.initialState(); for (const ev of events) st.applyEvent(s, ev); }
  const v = s.verdicts[vid];
  return pathCheckedWhy(s, v, (id) => titles.get(id));
}"""

SHOWN = """() => [...document.querySelectorAll('[data-role=why]')].filter((e) => e.checkVisibility())
  .map((e) => ({ cls: e.className, text: e.textContent }))"""

PLAIN = """async (texts) => {
  const { plainLineHtml, shownText } = await import('/js/plain.js');
  const s = window.hiraku.state();
  return texts.map((t) => shownText(plainLineHtml(s, t)).replace(/\\s+/g, ' ').trim());
}"""

# a particle left alone between two words taken out: 「力の道は と の 支えの…」「、が と一致」
LONE_PARTICLES = re.compile(r"(?:^|[\s、。(（:：])[とのがをにで]\s+[とのがをにで](?=\s|$)|(?:^|[、。:：]\s*)[とのがをにで]\s")


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class PathWhyBrowserTest(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.server = make_server("127.0.0.1", 0, cls.tmp, quiet=True, saved_dir=SHOWCASE, launcher=False)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        cls.events = events_of(RUN)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def page(self, query: str):
        page = self.browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.base}/?run={RUN}&pace=0&{query}", wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready")
        page.evaluate("window.hiraku.ready")
        self.addCleanup(page.close)
        return page, errors

    def test_the_line_has_the_record_s_numbers(self):
        want, parts = expected_line(self.events, "ver-0070")
        # the record of 160725 v6 R7 as the task named it: 0.9253 mm <= 5 mm, path_checked, a bound of 9.845 mm
        self.assertEqual(want, "手計算は上限のみ (9.845 mm)。力の道がつながっていることを確かめ (手計算でつながらない部品 0、"
                               "解析で支えの無い荷重 0)、解析の値 0.9253 mm を基準 5 mm と比べて OK")
        page, errors = self.page(f"tab=bench&t={T_R7}")
        got = page.evaluate(WHY, ["ver-0070", None])
        self.assertEqual(got["text"], want)
        self.assertEqual(got["parts"], parts)
        shown = page.evaluate(SHOWN)
        # ③'s right column under 判定, and the strip under the 3D (the いま card: the verdict is the newest word)
        self.assertEqual({x["cls"]: x["text"] for x in shown}, {"ch-row ch-why": want, "now-why": want})
        # the present mode's card
        pres, e2 = self.page(f"tab=bench&t={T_R7}&present=1")
        self.assertIn({"cls": "pc-why", "text": want}, pres.evaluate(SHOWN))
        self.assertEqual(errors + e2, [])

    def test_the_tile_s_verdict(self):
        page, errors = self.page("tab=grid")
        shown = [x for x in page.evaluate(SHOWN) if x["cls"].startswith("pathwhy")]
        tile = page.evaluate("""() => { const t = document.querySelector('.tile[data-domain=strength]');
          return { nq: t.querySelector('.num .nq').textContent, nv: t.querySelector('.num .nv').textContent }; }""")
        self.assertEqual(tile["nq"].split()[0], "R1")
        want, _ = expected_line(self.events, "ver-0092")
        self.assertEqual(shown, [{"cls": "pathwhy clamp2", "text": want}])
        self.assertIn("75.13 MPa を基準 143.3 MPa と比べて OK", want)
        self.assertEqual(errors, [])

    def test_small_samples(self):
        """The records of ver-0070 cut down to what one verdict needs (the contract's shapes)."""
        page, errors = self.page(f"tab=bench&t={T_R7}")
        ids = {"pred-0119", "pred-0120", "ana-0075", "cmp-0111", "ver-0070"}
        base = [e for e in self.events if e["kind"] == "design.created" and e.get("design_version") == 6]
        base += [e for e in self.events if e["seq"] <= 880 and e["kind"] in ("prediction.recorded", "analysis.started", "analysis.finished", "comparison.recorded", "verdict.recorded")
                 and ({(e.get("data") or {}).get(k) for k in ("prediction_id", "analysis_id", "comparison_id", "verdict_id")} & ids)]
        whole = page.evaluate(WHY, ["ver-0070", base])
        self.assertEqual(whole["text"], expected_line(self.events, "ver-0070")[0])
        # no load_path prediction: its clause out, the rest as before
        no_hand = page.evaluate(WHY, ["ver-0070", [e for e in base if (e.get("data") or {}).get("prediction_id") != "pred-0119" or e["kind"] != "prediction.recorded"]])
        self.assertEqual(no_hand["text"], "手計算は上限のみ (9.845 mm)。力の道がつながっていることを確かめ (解析で支えの無い荷重 0)、解析の値 0.9253 mm を基準 5 mm と比べて OK")
        self.assertNotIn("freeParts", no_hand["parts"])
        # an NG: the value over the limit (a verdict as judge.py makes it: pass false)
        ng = json.loads(json.dumps(base))
        for e in ng:
            if e["kind"] == "verdict.recorded":
                e["data"].update(limit={"value": 0.5, "unit": "mm"}, **{"pass": False})
        self.assertTrue(page.evaluate(WHY, ["ver-0070", ng])["text"].endswith("解析の値 0.9253 mm を基準 0.5 mm と比べて NG"))
        # a formula that is not a bound
        other = json.loads(json.dumps(base))
        for e in other:
            if e["kind"] == "prediction.recorded" and e["data"]["prediction_id"] == "pred-0120":
                e["data"]["formula_id"] = "load_path.not_a_bound"
        self.assertTrue(page.evaluate(WHY, ["ver-0070", other])["text"].startswith("手計算 (9.845 mm) は値に届かない。"))
        # another basis: no line
        match = json.loads(json.dumps(base))
        for e in match:
            if e["kind"] == "verdict.recorded":
                e["data"]["basis"] = "match"
        self.assertIsNone(page.evaluate(WHY, ["ver-0070", match]))
        self.assertEqual(errors, [])

    def test_the_log_says_ids_by_their_names(self):
        page, errors = self.page("tab=bench&autoplay=0")
        # the end: every version's design in (ready alone may come before the replay reaches it)
        page.evaluate("() => window.hiraku.seek(window.hiraku.player.duration)")
        self.assertEqual(page.evaluate("() => Object.keys(window.hiraku.state().designs).length"),
                         len({e["design_version"] for e in self.events if e["kind"] == "design.created"}))
        # (a cause record's cause and fix, each a line of its own)
        texts = {(e["seq"], k): e["data"][k] for e in self.events if e["kind"] == "cause.recorded"
                 for k in ("cause", "fix") if isinstance(e["data"].get(k), str)}
        said = dict(zip(texts, page.evaluate(PLAIN, list(texts.values()))))
        either = lambda seq: " / ".join(said.get((seq, k), "") for k in ("cause", "fix"))
        # 「力の道は pred-0119 (free_parts 0、cmp-0110 で一致) と ana-0075 の unsupported_load 0 で確かめた」
        self.assertIn("力の道は 予測 (枠につながっていない部品の数 0、比べ で一致) と 解析 の 支えの無い荷重 0 で確かめた", either(878))
        # 「free_parts の予測 pred-0147 (0) は解析 ana-0091 と一致 (cmp-0136)」: 解析 not said twice, the number alone out
        self.assertIn("の予測 (0) は解析 と一致、", either(1055))
        # a member's id by the design's name (members[].name before its bracket)
        design = [e["data"] for e in self.events if e["kind"] == "design.created"][-1]
        name = lambda i: next(x["name"] for x in design["parts"] + design["members"] if x["id"] == i).split(" (")[0]
        self.assertIn(f"最大の場所 ({name('rail_R_S1S2')}) のレールの曲げ", said[(906, "cause")])
        cut = [t for t in said.values() if LONE_PARTICLES.search(t)]
        self.assertEqual(cut, [])
        # parts in a proposal's words; a record's number with no name (chg-0007) out
        prop = next(e["text"] for e in self.events if e["kind"] == "design.change_proposed" and "battery・battery2" in (e.get("text") or ""))
        shown = page.evaluate(PLAIN, [prop])[0]
        self.assertIn(f"{name('battery')}・{name('battery2')} が当たった", shown)
        self.assertNotIn("chg-", shown)
        self.assertNotRegex(shown, r"\bbattery2?\b")
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
