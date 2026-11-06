"""③ 解析: every test at a glance over the 3D (web/js/test_board.js, css/test_board.css), and the tool
shelf's domain heads with their icons (web/js/shelf.js).

On the saved scripted run (showcase/20261105-150416-offroad-4w-scripted: 8 domains, 34 load cases):

- one group per domain with tests, in the domains' order (labels.js DOMAINS), its head the domain's
  name and its agent's pet (cae.<domain>, the packager for assembly: agent_pets.js place "board",
  its pose the record's at t: data-pet-state); a group whose agent is at work says so (data-active);
- each box: the test's id, a short name (an id several load cases share: the part that tells them
  apart), and its state in a word and a class (test_board.js caseState: まだ・計算中・予想通り・不一致・
  OK・NG); red is for NG only, a mismatch is orange;
- pressing a box picks that load case (the cards and the 3D show it, the box is ringed); 自動 gives
  it back to the replay; the place of 「ビューアで見る」 is there and empty ([data-role="viewer"]);
- the whole board is on the screen with the 3D under it, nothing cut or scrolled, at 1440 x 900 and
  1920 x 1080, with video=1 too, also for 8 domains with 5 tests each; the layout lint finds nothing
  in it; the same t draws the same board.

The tool shelf (A4): each domain's head carries its agent's pet, the same role as ③'s board and
④'s tiles (data-pet-slot = data-agent of the board's group).

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import make_server

try:
    from tests.web.pages import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None

ROOT = Path(__file__).resolve().parents[2]
RUN = "20261105-150416-offroad-4w-scripted"
MANY = "20261106-130000-board-many"  # domains_fixture with 5 load cases in each of the 8 domains
LINT_JS = (Path(__file__).with_name("layout_lint.js")).read_text(encoding="utf-8")
DOMAINS = ["handling", "powertrain", "aero", "crash", "strength", "nvh", "thermal", "assembly"]
AGENT = {d: ("packager" if d == "assembly" else f"cae.{d}") for d in DOMAINS}

BOARD = """async () => {
  const B = await import('/js/test_board.js');
  const s = window.hiraku.state();
  const st = document.getElementById('stage');
  const k = st.getBoundingClientRect().width / st.offsetWidth;
  const r = (el) => { const b = el.getBoundingClientRect(); return { l: b.left, t: b.top, r: b.right, b: b.bottom }; };
  const board = document.getElementById('tests');
  return {
    groups: [...board.querySelectorAll('.tb-group')].map((g) => ({
      domain: g.dataset.domain, agent: g.dataset.agent, active: g.dataset.active,
      name: g.querySelector('.tb-name').textContent,
      slot: g.querySelector('[data-pet-slot]')?.dataset.petSlot ?? null,
      pet: g.querySelector('.petbox')?.dataset.petState ?? null,
      cells: [...g.querySelectorAll('button.test')].map((b) => b.dataset.lc),
    })),
    cells: [...board.querySelectorAll('button.test')].map((b) => ({
      lc: b.dataset.lc, state: b.dataset.state, word: b.querySelector('.tst').textContent,
      tid: b.querySelector('.tid').textContent, name: b.querySelector('.tnm')?.textContent ?? '',
      want: B.caseState(s, b.dataset.lc).key, on: b.classList.contains('on'),
      border: getComputedStyle(b).borderTopColor, box: r(b),
    })),
    words: B.CASE_STATE,
    red: getComputedStyle(document.documentElement).getPropertyValue('--red').trim(),
    orange: getComputedStyle(document.documentElement).getPropertyValue('--orange').trim(),
    board: r(board), view: r(document.getElementById('viewport')), win: [innerWidth, innerHeight], k,
    scroll: [board.scrollWidth - board.clientWidth, board.scrollHeight - board.clientHeight],
    auto: board.querySelector('button.auto').classList.contains('on'),
    viewer: (() => { const v = board.querySelector('[data-role="viewer"]'); return v ? v.children.length : null; })(),
    rest: board.querySelector('[data-role="no-tests"]')?.textContent ?? null,
    shown: window.hiraku.chain ? null : document.querySelector('#card-ana .sub')?.textContent ?? '',
  };
}"""


def rgb(hex_colour):
    h = hex_colour.lstrip("#")
    return f"rgb({int(h[0:2], 16)}, {int(h[2:4], 16)}, {int(h[4:6], 16)})"


def many_events():
    """tests.web.domains_fixture with five load cases in each domain (copies of its own, ids -2..-5)."""
    from tests.web.domains_fixture import domain_events

    events = domain_events()
    lcs = events[0]["data"]["load_cases"]  # (run.started)
    assert {c["domain"] for c in lcs} == set(DOMAINS)
    more = []
    for c in lcs:
        for i in range(2, 6):
            x = copy.deepcopy(c)
            x["load_case_id"] = f"{c['load_case_id']}-{i}"
            x["test_id"] = f"{c['test_id']}{i}"
            x["title"] = f"{c.get('title', c['load_case_id'])} その {i}"
            more.append(x)
    lcs.extend(more)
    return events


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class TestBoardBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        runs = cls.tmp / "runs"
        (runs / MANY).mkdir(parents=True)
        events = many_events()
        for i, ev in enumerate(events):
            ev["seq"] = i
        (runs / MANY / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
        cls.server = make_server("127.0.0.1", 0, runs, quiet=True, saved_dir=ROOT / "showcase", launcher=False)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def open(self, query, size=(1440, 900), run=RUN):
        page = self.browser.new_page(viewport={"width": size[0], "height": size[1]})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.base}/?run={run}&pace=0&{query}", wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=120000)
        page.evaluate("window.hiraku.ready")
        page.evaluate("document.fonts.ready.then(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))))")
        return page, errors

    def test_groups_pets_names_and_states(self):
        page, errors = self.open("tab=bench&t=1e6")
        got = page.evaluate(BOARD)
        events = [json.loads(x) for x in (ROOT / "showcase" / RUN / "events.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        self.assertEqual(errors, [])
        page.close()
        # one group per domain, in the domains' order, each with its agent's pet
        self.assertEqual([g["domain"] for g in got["groups"]], DOMAINS)
        self.assertEqual([g["name"] for g in got["groups"]], ["操安", "動力", "空力", "衝突", "強度信頼性", "音振", "熱", "組み立て"])
        for g in got["groups"]:
            self.assertEqual(g["agent"], AGENT[g["domain"]])
            self.assertEqual(g["slot"], AGENT[g["domain"]], g)
            self.assertIn(g["pet"], {"working", "waiting", "idle", "pass", "mismatch", "blocked", "report"}, g)
            self.assertEqual(g["active"], "0", g)  # the run is over: no agent at work
        self.assertEqual(sum(len(g["cells"]) for g in got["groups"]), 34)
        # each box: id, a name, its state as caseState says, in its word
        for c in got["cells"]:
            self.assertEqual(c["state"], c["want"], c)
            self.assertEqual(c["word"], got["words"][c["state"]]["ja"], c)
            self.assertTrue(c["name"], c)
        by = {c["lc"]: c for c in got["cells"]}
        self.assertEqual(by["P4-20km"]["tid"], "P4")
        self.assertEqual(len({by[x]["tid"] for x in ("R8-free", "R8-stress", "R8-push-725N")}), 3)  # the shared id told apart
        self.assertTrue(by["R8-free"]["tid"].startswith("R8"))
        # the last verdict of each load case in the current version decides OK / NG (the record's own pass)
        self.assertTrue({c["state"] for c in got["cells"]} <= {"ok", "ng", "match", "mismatch", "busy", "wait"})
        self.assertIn("ok", {c["state"] for c in got["cells"]})
        self.assertTrue(any(e["kind"] == "verdict.recorded" for e in events))
        # the rest line: every domain has a test here
        self.assertIsNone(got["rest"])
        self.assertEqual(got["viewer"], 0)  # the viewer's place, empty

    def test_colours_red_for_ng_only(self):
        """Over the run's time, each state's border colour: NG red, a mismatch orange, no other red."""
        page, errors = self.open("tab=bench&t=0")
        seen = {}
        for t in (40, 90, 140, 200, 260, 330, 400, 470, 540, 630.5):
            page.evaluate(f"window.hiraku.seek({t})")
            page.evaluate("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
            got = page.evaluate(BOARD)
            for c in got["cells"]:
                self.assertEqual(c["state"], c["want"], (t, c))
                seen.setdefault(c["state"], set()).add(c["border"])
        red, orange = rgb(got["red"]), rgb(got["orange"])
        self.assertEqual(errors, [])
        page.close()
        self.assertGreaterEqual(set(seen), {"wait", "busy", "match", "ok"}, seen)
        if "ng" in seen:
            self.assertEqual(seen["ng"], {red})
        if "mismatch" in seen:
            self.assertEqual(seen["mismatch"], {orange})
        for k, colours in seen.items():
            if k != "ng":
                self.assertNotIn(red, colours, k)

    def test_pressing_a_box_picks_its_test(self):
        page, errors = self.open("tab=bench&t=1e6")
        page.click('#tests button.test[data-lc="T2-40C-motor"]')
        page.evaluate("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
        got = page.evaluate(BOARD)
        card = page.evaluate("document.querySelector('#card-ana .sub')?.textContent ?? ''")
        self.assertEqual([c["lc"] for c in got["cells"] if c["on"]], ["T2-40C-motor"])
        self.assertIn("T2-40C-motor", card)
        self.assertFalse(got["auto"])
        page.click("#tests button.auto")
        page.evaluate("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
        self.assertTrue(page.evaluate(BOARD)["auto"])
        self.assertEqual(errors, [])
        page.close()

    def fits(self, got, what):
        k = got["k"]
        self.assertEqual(got["scroll"][0] <= 1, True, what)  # nothing scrolls sideways
        self.assertLessEqual(got["scroll"][1], 1, what)
        for c in got["cells"]:
            b = c["box"]
            self.assertTrue(0 <= b["l"] and b["r"] <= got["win"][0] and 0 <= b["t"] and b["b"] <= got["win"][1], (what, c["lc"], b))
            self.assertLessEqual(b["b"], got["view"]["t"] + 1, (what, c["lc"]))  # above the 3D, not on it
        # the 3D keeps most of the column: the board at most a third of the stage's height
        self.assertLess((got["board"]["b"] - got["board"]["t"]) / k, 1080 / 3, what)
        self.assertGreater((got["view"]["b"] - got["view"]["t"]) / k, 330, what)

    def test_one_screen_at_two_sizes_and_video(self):
        for run in (RUN, MANY):
            for size in ((1440, 900), (1920, 1080)):
                for video in ("", "&video=1"):
                    what = f"{run} {size} {video}"
                    page, errors = self.open(f"tab=bench&t=1e6{video}", size, run)
                    got = page.evaluate(BOARD)
                    self.assertEqual(sum(len(g["cells"]) for g in got["groups"]), 34 if run == RUN else 40, what)
                    self.fits(got, what)
                    problems = page.evaluate(LINT_JS, {"root": "#tests"})
                    self.assertEqual(problems, [], what)
                    self.assertEqual(errors, [], what)
                    page.close()

    def test_same_t_same_board(self):
        t = 300
        html = []
        for _ in range(2):
            page, errors = self.open(f"tab=bench&t={t}")
            html.append(page.evaluate("document.getElementById('tests').innerHTML.replace(/<canvas[^>]*>/g, '')"))
            page.evaluate("window.hiraku.seek(500)")
            page.evaluate(f"window.hiraku.seek({t})")
            page.evaluate("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
            html.append(page.evaluate("document.getElementById('tests').innerHTML.replace(/<canvas[^>]*>/g, '')"))
            self.assertEqual(errors, [])
            page.close()
        self.assertEqual(len(set(html)), 1)

    def test_board_pets_one_picture_per_motion(self):
        """With moving pets (?petmotion=on), a board pet is drawn again only when its motion changes
        (agent_pets.js HELD_PLACES), while the agents' list keeps moving."""
        page, errors = self.open("tab=bench&t=300&petmotion=on")
        read = """() => Object.fromEntries([...document.querySelectorAll('#tests .petbox, #agents .petbox')].map((b) =>
            [(b.closest('#tests') ? 'board:' : 'list:') + b.dataset.agent, [b.dataset.petState, Number(b.dataset.petDraws)]]))"""
        seen = []
        for t in (300.0, 300.4, 300.8, 301.2):
            page.evaluate(f"window.hiraku.seek({t})")
            page.evaluate("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
            seen.append(page.evaluate(read))
        self.assertEqual(errors, [])
        page.close()
        board = [k for k in seen[0] if k.startswith("board:")]
        self.assertEqual(len(board), 8)
        for k in board:
            states = {s[k][0] for s in seen}
            if len(states) == 1:  # the same motion all along: no new picture
                self.assertEqual({s[k][1] for s in seen}, {seen[0][k][1]}, k)
        moving = [k for k in seen[0] if k.startswith("list:") and seen[0][k][0] in ("working", "waiting")]
        if moving:
            self.assertTrue(any(seen[-1][k][1] > seen[0][k][1] for k in moving), seen)

    def test_shelf_heads_carry_the_domains_pets(self):
        page, errors = self.open("tab=A4&t=1e6", (1920, 1080))
        page.wait_for_function("document.querySelectorAll('#view-shelf .sh-node').length > 0")
        page.evaluate("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
        got = page.evaluate(
            """() => [...document.querySelectorAll('#view-shelf .sh-node')].map((n) => ({
                 key: n.dataset.branch, slot: n.querySelector('.sh-head [data-pet-slot]')?.dataset.petSlot ?? null,
                 role: n.querySelector('.sh-head .petbox')?.dataset.role ?? null,
                 drawn: Number(n.querySelector('.sh-head .petbox')?.dataset.petDraws ?? 0) }))"""
        )
        # the heads only: the tools' rows (.sh-tool) are not changed here (on this run some of their
        # columns run out of their box; reported, not this test's)
        problems = page.evaluate(LINT_JS, {"root": "#view-shelf .sh-tree", "skip": [".sh-tool"]})
        tool_rows = page.evaluate(LINT_JS, {"root": "#view-shelf .sh-tree"})
        self.assertEqual(errors, [])
        page.close()
        domains = [g for g in got if g["key"] in DOMAINS]
        self.assertTrue(domains)
        for g in domains:
            self.assertEqual(g["slot"], AGENT[g["key"]], g)  # the same agent as ③'s board (and ④'s tile)
            self.assertEqual(g["role"], AGENT[g["key"]], g)
            self.assertGreater(g["drawn"], 0, g)
        self.assertEqual(problems, [])
        self.assertTrue(all(".sh-tool" in p["sel"] or "li.sh-tool" in p["sel"] for p in tool_rows), tool_rows)


if __name__ == "__main__":
    unittest.main()
