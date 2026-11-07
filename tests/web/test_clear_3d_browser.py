"""③'s 3D stays clear: no sentence card over the vehicle, and the colour legend beside it, not on it.

At the events of the showcase runs (showcase/2026*): every proposal (design.change_proposed),
version built (design.created), cause (cause.recorded), verdict (verdict.recorded) and hooks' stop
(hook.denied / hook.blocked_stop), and for every other kind its first and last (agent.said: five),
③ at 1440 x 900 and 1920 x 1080 is seeked to the event and checked:

- the sentence cards (the いま line, the round card, the cause note, an agent's words, the hooks'
  card and the stage's hooks line): at most one shows (web/js/benchnote.js); one over the 3D would
  have to be under a quarter of its area and two lines at most (they are all in the strip under it
  now, so none is);
- the vehicle's outline on the screen (Workbench3D.screenRect: the box around the drawn body and
  wheels through the camera; the compare view's too when it shows), each sentence card shown and
  each legend shown (the colour legend, the modules' and the joints' legends) do not cross by more
  than SLACK px on both axes;
- the card's visible words hold no record id nor English record words (benchnote.js
  hasRecordWords), and the colour legend's title no English word but von Mises;
- at an event that is the newest at its time, the strip's card is that event's: the hooks' card at a
  stop, the cause note at a cause, the round card at a proposal or a version built from proposals.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
from collections import defaultdict
from pathlib import Path

from hiraku.webserve.server import make_server
from tests.web.test_browser import ENABLED, sync_playwright
from tests.web.test_layout_lint_browser import REPO_DIR

SHOWCASE = REPO_DIR / "showcase"
RUNS = sorted(p.name for p in SHOWCASE.glob("2026*") if (p / "events.jsonl").exists())
SIZES = ((1440, 900), (1920, 1080))
ALL_OF = {"design.change_proposed", "design.created", "cause.recorded", "verdict.recorded", "hook.denied", "hook.blocked_stop"}
SAID_N = 5
SLACK = 3  # stage px

CHECK = """async (t) => {
  const h = window.hiraku;
  await h.seek(t);
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  const { hasRecordWords } = await import('/js/benchnote.js');
  const stage = document.getElementById('stage');
  const k = stage.getBoundingClientRect().width / stage.offsetWidth;
  const box = (e) => { const b = e.getBoundingClientRect(); return { left: b.left, top: b.top, right: b.right, bottom: b.bottom }; };
  const shows = (e) => !!e && e.checkVisibility({ opacityProperty: true, visibilityProperty: true }) && e.getBoundingClientRect().width > 0
    && Number(getComputedStyle(e).opacity) > 0.05;
  const vp = box(document.getElementById('viewport'));
  const cards = ['now', 'roundcard', 'causenote', 'bubble', 'hooknote', 'hookline'].map((id) => document.getElementById(id)).filter(shows)
    .map((e) => { const r = box(e); const over = r.right > vp.left && r.left < vp.right && r.bottom > vp.top && r.top < vp.bottom;
      const lh = parseFloat(getComputedStyle(e).lineHeight) || 20;
      return { id: e.id, r, over, area: (r.right - r.left) * (r.bottom - r.top) / ((vp.right - vp.left) * (vp.bottom - vp.top)),
               lines: Math.round((r.bottom - r.top) / lh), text: e.innerText.replace(/\\s+/g, ' ').slice(0, 80) }; });
  const legends = [...document.querySelectorAll('#viewport > #legend, #viewport > .modlegend, #viewport > .joint-legend,#viewport > [data-kind="legend"]')]
    .filter(shows).map((e) => ({ id: e.id || e.className, r: box(e) }));
  const cars = [h.bench.screenRect()];
  const cmp = document.querySelector('.cmp-view');
  if (cmp && !cmp.hidden && h.cmpBench) cars.push(h.cmpBench.screenRect());
  const strip = document.getElementById('benchnote');
  const title = document.querySelector('#viewport > #legend .ttl');
  return { k, cards, legends, cars: cars.filter(Boolean), kind: strip.hidden ? null : strip.dataset.kind, seq: h.player.state.seq,
           words: strip.hidden ? '' : document.getElementById('bn-card').innerText,
           recordWords: strip.hidden ? false : hasRecordWords(document.getElementById('bn-card').innerText),
           title: title && shows(title) ? title.innerText : '' };
}"""


def events_of(run: str) -> list[dict]:
    return [json.loads(line) for line in (SHOWCASE / run / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def picked(events: list[dict]) -> list[dict]:
    """The events looked at: all of ALL_OF; of each other kind its first and last (agent.said: SAID_N spread out)."""
    by = defaultdict(list)
    for e in events:
        by[e["kind"]].append(e)
    out = []
    for kind, es in by.items():
        if kind in ALL_OF:
            out += es
        elif kind == "agent.said":
            out += [es[round(i * (len(es) - 1) / max(1, SAID_N - 1))] for i in range(min(SAID_N, len(es)))]
        else:
            out += [es[0], es[-1]] if len(es) > 1 else es
    seen = set()
    return sorted((e for e in out if not (e["seq"] in seen or seen.add(e["seq"]))), key=lambda e: e["seq"])


def cross(a: dict, b: dict, k: float) -> float | None:
    w = (min(a["right"], b["right"]) - max(a["left"], b["left"])) / k
    h = (min(a["bottom"], b["bottom"]) - max(a["top"], b["top"])) / k
    return w * h if w > SLACK and h > SLACK else None


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class Clear3DBrowserTest(unittest.TestCase):
    maxDiff = None
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.server = make_server("127.0.0.1", 0, cls.tmp, quiet=True, saved_dir=SHOWCASE, launcher=False)
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

    def open(self, run: str, w: int, h: int):
        page = self.browser.new_page(viewport={"width": w, "height": h})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.base}/?run={run}&tab=bench&pace=0&t=0", wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=120000)
        page.evaluate("window.hiraku.ready")
        page.evaluate("document.fonts.ready")
        return page, errors

    def sweep(self, run: str, w: int, h: int) -> tuple[int, list[str]]:
        events = events_of(run)
        last_at_t = {}
        for e in events:
            last_at_t[e["t"]] = e["seq"]
        page, errors = self.open(run, w, h)
        bad = []
        n = 0
        try:
            for e in picked(events):
                got = page.evaluate(CHECK, e["t"])
                n += 1
                at = f"{run} {w}x{h} seq {e['seq']} {e['kind']} t={e['t']}"
                k = got["k"]
                if len(got["cards"]) > 1:
                    bad.append(f"{at}: {len(got['cards'])} cards {[c['id'] for c in got['cards']]}")
                for c in got["cards"]:
                    if c["over"] and (c["area"] > 0.25 or c["lines"] > 2):
                        bad.append(f"{at}: card {c['id']} over the 3D, {c['area']:.2f} of it, {c['lines']} lines")
                    for car in got["cars"]:
                        if cross(c["r"], car, k):
                            bad.append(f"{at}: card {c['id']} 「{c['text'][:30]}」 on the vehicle")
                    for lg in got["legends"]:
                        if cross(c["r"], lg["r"], k):
                            bad.append(f"{at}: card {c['id']} on the legend {lg['id']}")
                for lg in got["legends"]:
                    for car in got["cars"]:
                        if cross(lg["r"], car, k):
                            bad.append(f"{at}: legend {lg['id']} on the vehicle ({lg['r']} x {car})")
                if got["recordWords"]:
                    bad.append(f"{at}: record words in the card 「{got['words'][:80]}」")
                title = got["title"].replace("von Mises", "")
                if any(len(x) >= 3 and x.isascii() and x.isalpha() and x.islower() for x in title.replace("(", " ").replace(")", " ").split()):
                    bad.append(f"{at}: English in the legend's title 「{got['title']}」")
                if last_at_t[e["t"]] == e["seq"] and got["seq"] == e["seq"]:
                    want = {"hook.denied": "hooknote", "hook.blocked_stop": "hooknote", "cause.recorded": "causenote", "design.change_proposed": "roundcard"}.get(e["kind"])
                    if e["kind"] == "design.created" and e["data"].get("module_changes"):
                        want = "roundcard"
                    # a cause written again as resolved by a later comparison (data.resolved, the same
                    # cause_id: 164004 seq 387, 388, 421) is no new cause: the screen opens no note for it
                    # (present.js openCause reads the cause at its first record; its load case is OK by then)
                    if e["kind"] == "cause.recorded" and (e.get("data") or {}).get("resolved"):
                        want = None
                    if want and got["kind"] != want:
                        bad.append(f"{at}: the strip shows {got['kind']}, not {want}")
        finally:
            page.close()
        self.assertEqual(errors, [], run)
        return n, bad

    def test_no_card_on_the_vehicle_nor_the_legend_at_any_event(self):
        total = 0
        for run in RUNS:
            for w, h in SIZES:
                with self.subTest(run=run, width=w):
                    n, bad = self.sweep(run, w, h)
                    total += n
                    self.assertEqual(bad, [], f"{run} {w}x{h}: {len(bad)} of {n} events")
        print(f"\n[clear 3D] {total} event views over {len(RUNS)} runs x {len(SIZES)} sizes")
        self.assertGreaterEqual(len(RUNS), 4)


if __name__ == "__main__":
    unittest.main()
