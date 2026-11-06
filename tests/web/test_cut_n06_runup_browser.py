"""N06's step tile with the step test's run 2 (cut.html?cut=n06, layout.json n06.runup).

The run is tests/web/runup_fixture.py: K2's records of the vehicle rolling at a 330 mm and a
100 mm step at 10 km/h (cut down, the tool's values). The tile runup.tile is split into two
panes in runup.order; each plays its record on one clock; the tags carry the record's step
height and the load case's speed; red only for the stop judged NG.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
The layout is the N03 / N06 test's copy with the runup key as in layout.json.
"""

import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku import contract
from hiraku.webserve.server import make_server
from tests.web.runup_fixture import RUN_ID, SPEED, load_cases, records, runup_events
from tests.web.test_browser import ENABLED, sync_playwright
from tests.web.test_cut_n03_n06_browser import n03_n06_layout
from tests.web.test_cut_spin_browser import FIND, READ as SPIN_READ, turn_deg

RED = "rgb(200, 64, 42)"  # n06.overlay.color
INK = "rgb(27, 26, 23)"  # layout colors.ink
RUNUP = {"tile": "step_stuck", "order": ["stops", "climbs"], "gap_px": 12, "lead_share": 0.75}


def runup_layout(**over):
    layout = n03_n06_layout()
    layout["colors"] = {"ink": "#1b1a17"}
    layout["n06"]["runup"] = {**RUNUP, **over}
    return layout


# The pair tile's panes at stage time t (the list's and the large box's), read in the page.
READ = """async (t) => {
  await window.hiraku.seek(t);
  const cut = window.hiraku.cut;
  const tile = cut.tiles.find((x) => x.key === "step_stuck");
  const red = (el) => [el, ...el.querySelectorAll("*")].filter((e) => getComputedStyle(e).color === "rgb(200, 64, 42)" || getComputedStyle(e).borderTopColor === "rgb(200, 64, 42)").map((e) => e.className || e.tagName);
  const pane = (s) => {
    const f = s.view.frame;
    const w = (id) => f?.wheels?.find((x) => x.id === id)?.center ?? null;
    return { box: s.el.dataset.box, tau: Number(s.el.dataset.tau), climbs: s.el.dataset.climbs, ng: s.el.dataset.ng, case: s.el.dataset.case,
             h: Number(s.el.dataset.heightMm), text: s.label.textContent, mark: getComputedStyle(s.label.querySelector(".mark")).color,
             why: getComputedStyle(s.label.querySelector(".why")).color, labelShown: getComputedStyle(s.label).visibility,
             body: f?.body ?? null, FL: w("FL"), RL: w("RL"), red: red(s.el),
             step: (s.view.sceneDesc?.objects ?? []).filter((o) => o.kind === "step").map((o) => [o.position_mm[0] - o.size_mm[0] / 2, o.size_mm[2]])[0] ?? null };
  };
  const box = (b) => ({ runup: b.el.dataset.runup ?? null, subs: (b.subs ?? []).map(pane), border: getComputedStyle(b.el).borderTopColor,
                        shown: getComputedStyle(b.el).visibility, moving: b.el.dataset.kind ?? null, canvas: !!b.canvas });
  return { err: document.querySelector(".err")?.textContent ?? null, turn: tile.turn, play: tile.play,
           small: box(tile.small), big: box(tile.big), featured: tile.small.el.dataset.featured,
           text: tile.small.el.textContent };
}"""

PIXELS = """async (t) => {
  await window.hiraku.seek(t);
  const tile = window.hiraku.cut.tiles.find((x) => x.key === "step_stuck");
  return [tile.big, tile.small].flatMap((b) => b.subs.map((s) => s.canvas.toDataURL()));
}"""


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class CutN06RunupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.servers = []

    def tearDown(self):
        for s in self.servers:
            s.shutdown()
            s.server_close()
        shutil.rmtree(self.tmp)

    def serve(self, layout, events):
        lp = self.tmp / f"layout-{len(self.servers)}.json"
        lp.write_text(json.dumps(layout, ensure_ascii=False), encoding="utf-8")
        runs = self.tmp / f"runs-{len(self.servers)}"
        (runs / RUN_ID).mkdir(parents=True)
        (runs / RUN_ID / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
        server = make_server("127.0.0.1", 0, runs, quiet=True, saved_dir=None, launcher=False, layout_file=lp)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def open(self, url):
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url)
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=180000)
        page.evaluate("window.hiraku.ready")
        return page, errors

    def test_events_follow_the_contract(self):
        for ev in runup_events():
            self.assertEqual(contract.errors("event", ev), [], ev["kind"])

    def test_the_two_runs_play_their_records(self):
        recs = records()
        layout = runup_layout()
        base = self.serve(layout, runup_events())
        page, errors = self.open(f"{base}/cut.html?cut=n06&run={RUN_ID}&t=0")
        first = page.evaluate(READ, 0.0)
        self.assertIsNone(first["err"])
        turn, play = first["turn"], first["play"]
        end = page.evaluate(READ, turn["to"] - 1e-6)  # the last instant of its turn: the records' ends
        self.assertEqual(errors, [])

        # the tile layout.json names, split in its order, in the list and in the large box
        for b in ("small", "big"):
            self.assertEqual(end[b]["runup"], "1")
            self.assertFalse(end[b]["canvas"])
            self.assertEqual([p["box"] for p in end[b]["subs"]], [f"n06.{'tile' if b == 'small' else 'big'}.step_stuck.{k}" for k in RUNUP["order"]])
            self.assertEqual([p["case"] for p in end[b]["subs"]], ["P1-step330", "P1-step100"])

        # the tags: the height from the record's scene, the speed from the load case's params
        h = {k: r["scene"][0]["size_mm"][2] for k, r in recs.items()}
        v = next(c for c in load_cases() if c["load_case_id"] == "P1-step330")["params"]["speed"]
        self.assertEqual(v, SPEED)
        stop, climb = end["big"]["subs"]
        self.assertEqual(stop["text"], f"✕ 段差 {h['P1-step330']:g} mm で止まる ({v['value']:g} {v['unit']} で向かう)")
        self.assertEqual(climb["text"], f"○ 段差 {h['P1-step100']:g} mm は越える")
        self.assertEqual((stop["h"], climb["h"]), (h["P1-step330"], h["P1-step100"]))
        self.assertEqual([p["text"] for p in end["small"]["subs"]], [f"✕ {h['P1-step330']:g} mm で止まる", f"○ {h['P1-step100']:g} mm は越える"])

        # red for the stop judged NG (climbable 0 against >= 1) only; nothing red on the climb,
        # nor on the pair's border while it is in the large box
        self.assertEqual((stop["ng"], stop["mark"], stop["why"]), ("1", RED, RED))
        self.assertEqual((climb["ng"], climb["climbs"], climb["mark"], climb["why"]), ("0", "1", INK, INK))
        self.assertEqual(end["featured"], "1")
        self.assertNotEqual(end["small"]["border"], RED)
        for got in (first, end):
            for b in ("small", "big"):
                self.assertEqual(got[b]["subs"][1]["red"], [], b)

        # the end of the play: each pane at its record's end, where the record has it
        def last(lc_id, ch):
            r = recs[lc_id]
            s = next(x for x in r["series"] if x["channel"] == ch)
            return s["values"][-1] * 1000.0, s["t_s"][-1]

        fl_x, t_end = last("P1-step330", "wheel_center_x.FL")
        self.assertAlmostEqual(stop["tau"], t_end, delta=1e-3)
        self.assertAlmostEqual(stop["FL"][0], fl_x, places=3)
        self.assertAlmostEqual(stop["body"][0], last("P1-step330", "body_x")[0], places=3)
        # it stands before the step's face: the front wheel (its radius: its centre's height at
        # the start, on the road) does not reach past it
        r0 = next(x for x in recs["P1-step330"]["series"] if x["channel"] == "wheel_center_z.FL")["values"][0] * 1000.0
        x_face, step_h = stop["step"]
        self.assertEqual(step_h, h["P1-step330"])
        self.assertLessEqual(stop["FL"][0] + r0, x_face + 0.5)
        self.assertLess(stop["body"][0], x_face)
        # the climb: at its record's end every wheel is past the step's face and on top of it
        rl_x, t_end100 = last("P1-step100", "wheel_center_x.RL")
        self.assertAlmostEqual(climb["tau"], t_end100, delta=1e-3)
        self.assertAlmostEqual(climb["RL"][0], rl_x, places=3)
        x_face100, h100 = climb["step"]
        self.assertGreater(climb["RL"][0], x_face100)
        self.assertGreater(climb["RL"][2], h100)
        # the list's panes hold the same ends
        for p, q in zip(end["small"]["subs"], (stop, climb)):
            self.assertAlmostEqual(p["tau"], q["tau"], places=9)

        # one clock: both runs start together and meet their steps together
        mid = page.evaluate(READ, play["from"] + 0.2)
        a, b = mid["big"]["subs"]
        self.assertAlmostEqual(a["tau"], b["tau"], places=9)
        self.assertGreater(a["tau"], 0.0)
        before = page.evaluate(READ, play["from"] - 0.05)
        # before the play: both at their records' first sample
        for p in before["big"]["subs"]:
            self.assertAlmostEqual(p["tau"], recs[p["case"]]["series"][0]["t_s"][0], places=6)

        # the pace: the stretch both records have (to the shorter one's end) gets at most
        # lead_share of the play (the turn limit may take some of it for the stopped run's
        # rebounds after it); the approach plays at an even rate, at least as fast as recorded,
        # so the vehicles come at the step with their speed
        t0 = recs["P1-step330"]["series"][0]["t_s"][0]
        lead = t_end100 - recs["P1-step100"]["series"][0]["t_s"][0]
        play_s = play["to"] - play["from"]
        at = page.evaluate(READ, play["from"] + RUNUP["lead_share"] * play_s)
        self.assertGreaterEqual(at["big"]["subs"][0]["tau"] - t0, lead - 0.03)
        taus = [page.evaluate(READ, play["from"] + k * 0.05)["big"]["subs"][0]["tau"] for k in (1, 2, 3)]
        self.assertAlmostEqual(taus[1] - taus[0], taus[2] - taus[1], delta=1e-3)
        self.assertGreaterEqual((taus[2] - taus[0]) / 0.1, 1.0)
        self.assertLess(taus[2], 0.7)  # still before the 330 mm step (met at about 0.75 s)
        # the rest (the stopped run's tries and its standstill to the end) after it
        self.assertLess(page.evaluate(READ, play["to"] - 0.1)["big"]["subs"][0]["tau"], t_end)

        # the moving things: the pair's box counts as one, in the list and in the large box
        self.assertEqual(mid["big"]["moving"], "moving")
        self.assertEqual(mid["small"]["moving"], "moving")
        page.close()

    def test_nothing_turns_faster_than_90_deg_s(self):
        base = self.serve(runup_layout(), runup_events())
        page, errors = self.open(f"{base}/cut.html?cut=n06&run={RUN_ID}&t=0")
        turn = page.evaluate(READ, 0.0)["turn"]
        prev, best = {}, 0.0
        steps = int(round((turn["to"] - turn["from"]) / 0.05))
        for i in range(steps + 1):
            t = round(turn["from"] + i * 0.05, 4)
            page.evaluate(FIND)
            for x in page.evaluate(SPIN_READ, t):
                p = prev.get(x["path"])
                if p and abs(t - p[0] - 0.05) < 1e-6:
                    for k in ("cam", "screen"):
                        best = max(best, turn_deg(p[1][k], x[k]) / 0.05)
                prev[x["path"]] = (t, x)
        self.assertEqual(errors, [])
        self.assertTrue(prev)
        self.assertLessEqual(best, 90.0)
        page.close()

    def test_the_same_t_draws_the_same_picture(self):
        base = self.serve(runup_layout(), runup_events())
        page, errors = self.open(f"{base}/cut.html?cut=n06&run={RUN_ID}&t=0")
        t = 3.2  # the 330 mm run meets its step (step_stuck's turn: 2.45 .. 4.3 s)
        a = page.evaluate(PIXELS, t)
        page.evaluate(PIXELS, 4.25)
        b = page.evaluate(PIXELS, t)
        self.assertEqual(a, b)
        page.close()
        # another load at ?t= (and video=1) draws the same pixels: in the zu look. (In the real
        # look, the cut's own, two loads differ in under 1 % of the pixels, the same camera and
        # record time: the look's, not the pair's; looked at 2026-11-06.)
        first, errors2 = self.open(f"{base}/cut.html?cut=n06&run={RUN_ID}&t=0&look=zu")
        other, errors3 = self.open(f"{base}/cut.html?cut=n06&run={RUN_ID}&t={t}&video=1&look=zu")
        self.assertEqual(other.evaluate(PIXELS, t), first.evaluate(PIXELS, t))
        self.assertEqual(errors + errors2 + errors3, [])
        first.close()
        other.close()

    def test_a_stop_without_a_judgement_is_not_red(self):
        """Without the criterion (nothing judged) the stop's ✕ is in ink."""
        events = runup_events()
        events[0]["data"]["load_cases"] = load_cases(criterion=False)
        base = self.serve(runup_layout(), events)
        page, errors = self.open(f"{base}/cut.html?cut=n06&run={RUN_ID}&t=0")
        got = page.evaluate(READ, 4.2)
        self.assertEqual(errors, [])
        for b in ("small", "big"):
            stop = got[b]["subs"][0]
            self.assertEqual((stop["ng"], stop["mark"], stop["why"]), ("0", INK, INK))
            self.assertEqual(stop["red"], [])
        page.close()

    def test_without_the_key_or_the_records_the_tile_is_as_before(self):
        layout = n03_n06_layout()
        base = self.serve(layout, runup_events())
        page, errors = self.open(f"{base}/cut.html?cut=n06&run={RUN_ID}&t=0")
        got = page.evaluate(READ, 4.2)
        self.assertEqual(errors, [])
        self.assertIsNone(got["small"]["runup"])
        self.assertEqual(got["small"]["subs"], [])
        page.close()
        # the key, but only run 1's records (no record_run = speed)
        recs = records()
        for r in recs.values():
            r["assumptions"] = [a for a in r["assumptions"] if "record_run" not in a]
        base = self.serve(runup_layout(), runup_events(recs=recs))
        page, errors = self.open(f"{base}/cut.html?cut=n06&run={RUN_ID}&t=0")
        got = page.evaluate(READ, 4.2)
        self.assertEqual(errors, [])
        self.assertIsNone(got["small"]["runup"])
        page.close()
        # the load case gives record_run instead of the assumptions: the pair again
        events = runup_events(recs=recs)
        for lc in events[0]["data"]["load_cases"]:
            lc["params"]["record_run"] = "speed"
        base = self.serve(runup_layout(), events)
        page, errors = self.open(f"{base}/cut.html?cut=n06&run={RUN_ID}&t=0")
        got = page.evaluate(READ, 4.2)
        self.assertEqual(errors, [])
        self.assertEqual(got["small"]["runup"], "1")
        page.close()

    def test_an_order_it_does_not_know_is_named(self):
        base = self.serve(runup_layout(order=["stops", "jumps"]), runup_events())
        page, errors = self.open(f"{base}/cut.html?cut=n06&run={RUN_ID}&t=1")
        self.assertIn("layout.json の n06 の runup.order が分からない: jumps", page.evaluate("document.querySelector('.err')?.textContent ?? ''"))
        page.close()
        layout = runup_layout()
        del layout["n06"]["runup"]["lead_share"]
        base = self.serve(layout, runup_events())
        page, errors = self.open(f"{base}/cut.html?cut=n06&run={RUN_ID}&t=1")
        self.assertIn("layout.json の n06 に無い: runup.lead_share", page.evaluate("document.querySelector('.err')?.textContent ?? ''"))
        page.close()


if __name__ == "__main__":
    unittest.main()
