"""The agents' pets on the intake screens (js/intake_pets.js): A1's domain cards; on A2 the pet of the
agent that asked (outside the video layout) and, in the video layout, the row of the CAE agents.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed;
a test that needs the pets is skipped when promo/pets/pet3d.js is not in the checkout. The run is
the contract sample's run.started with the events below: domains.picked (six domains picked,
thermal off), six CAE agents starting their analyses in ORDER, and the lead's hearing (the contract's
examples). Shape example only: none of the values is a calculation result.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku import contract
from hiraku.webserve.server import PETS_FILE, SAMPLE_EVENTS, make_server
from tests.web.test_browser import ENABLED

try:
    from tests.web.pages import sync_playwright
except ImportError:
    from tests.web.test_browser import sync_playwright

RUN = "20261106-130000-intake-pets"
NONE_RUN = "20261106-131000-no-cae"
# the agents in the order of their first analysis.started (not the domains' order); thermal has none
ORDER = ["cae.crash", "cae.strength", "cae.handling", "cae.nvh", "cae.powertrain", "cae.aero"]
NAMES = {"cae.crash": "衝突", "cae.strength": "強度信頼性", "cae.handling": "操安", "cae.nvh": "音振", "cae.powertrain": "動力", "cae.aero": "空力"}
PICKED_T = 0.4
START_T = {a: 4.0 + 0.5 * k for k, a in enumerate(ORDER)}  # each agent's first analysis.started
ASK_T, ANSWER_T = 10.0, 11.0  # the lead's hearing.asked and its hearing.answered
PASS_S = 2.7  # agent_pets.js: a pass is 3 hops of n00.motion.hop_period_s (0.9 s in promo/screens/layout.json)
STAGE_BOTTOM = 1080 - 170  # the video's subtitle band under it
REPLY_STEP_S = 0.5  # js/intake/a1.js: with a playback time A1's picked domains answer this far apart, in the cards' order
A1_TURN = {d: PICKED_T + REPLY_STEP_S * k for k, d in enumerate(["handling", "powertrain", "aero", "crash", "strength", "nvh"])}
LEAD_DONE_FILM = PICKED_T + REPLY_STEP_S * 6  # the seventh answer (thermal, off) is the last card's turn
CANVAS_SIZE = "(sel) => { const c = document.querySelector(sel || '.in-cards .in-pet canvas'); const r = c.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)]; }"


def _sample():
    return [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]


def pets_events() -> list[dict]:
    """run.started, domains.picked at PICKED_T, the six agents' start and analyses, run.finished."""
    run = _sample()[0]
    picked = json.loads((contract.EXAMPLES_DIR / "domains_picked.json").read_text(encoding="utf-8"))
    picked["picks"] = [{"domain": a[4:], "shown_name": NAMES[a], "reason": "形の見本", "test_ids": []} for a in ORDER]
    picked["off"] = [{"domain": "thermal", "reason": "形の見本: 選ばなかった理由"}]

    def ev(t, kind, agent, data=None):
        return {"seq": 0, "ts": run["ts"], "t": t, "run_id": run["run_id"], "kind": kind, "agent": agent, "data": data or {}}

    out = [run, ev(PICKED_T, "domains.picked", "lead", picked)]
    for k, a in enumerate(ORDER):
        out.append(ev(3.0 + 0.1 * k, "agent.started", a, {"agent_id": a}))
        ana = {"analysis_id": f"ana-{k + 1:04d}", "tool": "shape_example", "load_case_id": "S1-v", "prediction_ids": []}
        out.append({**ev(START_T[a], "analysis.started", a, ana), "domain": a[4:]})
    # the lead asks (ASK_T) and is answered (ANSWER_T): A2's asker outside the video layout
    out.append(ev(ASK_T, "hearing.asked", "lead", json.loads((contract.EXAMPLES_DIR / "hearing_asked.json").read_text(encoding="utf-8"))))
    out.append(ev(ANSWER_T, "hearing.answered", "lead", json.loads((contract.EXAMPLES_DIR / "hearing_answered.json").read_text(encoding="utf-8"))))
    out.append(ev(12.0, "run.finished", "system", {"status": "stopped", "stop_reason": "round_limit"}))
    out.sort(key=lambda e: e["t"])
    for i, e in enumerate(out):
        e["seq"] = i
    return out


def no_cae_events() -> list[dict]:
    """The contract sample without its CAE agent's events (no cae.* agent in the record)."""
    out = [e for e in _sample() if not str(e.get("agent", "")).startswith("cae.") and not str(e.get("data", {}).get("agent_id", "")).startswith("cae.")]
    for i, e in enumerate(out):
        e["seq"] = i
    return out


READ = """async ({ t, tab }) => {
  if (tab && document.querySelector(`.tab[data-tab="${tab}"]`)) document.querySelector(`.tab[data-tab="${tab}"]`).click();
  if (t !== null) await window.hiraku.seek(t);
  const shown = (e) => e.offsetParent !== null;
  const look = (c) => {
    if (!c || !c.width) return { ink: 0, pixels: null, state: null, look: null };
    const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
    let ink = 0;
    for (let i = 3; i < d.length; i += 4) ink += d[i] > 0 ? 1 : 0;
    return { ink, pixels: c.toDataURL(), state: c.dataset.petState, look: c.dataset.petLook ?? null, role: c.dataset.role, w: c.width };
  };
  const slot = document.querySelector(".a2-pets");
  const a2 = [...document.querySelectorAll(".a2-pets .ipet")].filter(shown).map((el) => ({
    agent: el.dataset.agent, name: el.querySelector(".ipet-name").textContent, mark: el.dataset.petState, ...look(el.querySelector("canvas")) }));
  const pet = (s) => ({ domain: s.dataset.domain, card: s.dataset.state, ...look(s.querySelector("canvas")) });
  const a1 = [...document.querySelectorAll(".in-pet:not([data-domain='lead'])")].filter(shown).map(pet);
  const leadEl = document.querySelector(".in-pet[data-domain='lead']");
  const lead = leadEl && shown(leadEl) ? pet(leadEl) : null;
  const rect = (sel) => { const e = document.querySelector(sel); if (!e || !shown(e)) return null; const r = e.getBoundingClientRect(); return { top: r.top, bottom: r.bottom, h: r.height }; };
  return { loaded: document.documentElement.dataset.agentPets ?? null, a2, a1, lead,
           slot: slot ? { display: getComputedStyle(slot).display, children: slot.childElementCount } : null,
           pets: rect(".a2-pets"), left: rect(".in-pane.a2 .in-left"), right: rect(".in-pane.a2 .in-right") };
}"""


# A2's row of pets (n of them) and its two columns are on the screen with a height
A2_LAID_OUT = """(n) => {
  const box = (sel) => { const e = document.querySelector(sel); return e && e.offsetParent !== null && e.getBoundingClientRect().height > 0; };
  return box(".a2-pets") && box(".in-pane.a2 .in-left") && box(".in-pane.a2 .in-right")
    && [...document.querySelectorAll(".a2-pets .ipet")].filter((e) => e.offsetParent !== null).length === n;
}"""


@unittest.skipUnless(ENABLED, "set HIRAKU_WEB_BROWSER=1 with Playwright installed")
class IntakePetsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        runs = cls.tmp / "runs"
        for run_id, events in ((RUN, pets_events()), (NONE_RUN, no_cae_events())):
            (runs / run_id).mkdir(parents=True)
            (runs / run_id / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
        cls.server = make_server("127.0.0.1", 0, runs, quiet=True, saved_dir=None, launcher=False, pets_file=PETS_FILE)
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
        if not PETS_FILE.is_file():
            self.skipTest("needs promo/pets/pet3d.js (the promo department's) in the checkout")

    def open(self, query, run=RUN, motion="on"):
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        pm = f"&petmotion={motion}" if motion else ""
        page.goto(f"{self.base}/?run={run}&live=0&pace=0{pm}&{query}")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=120000)
        page.evaluate("window.hiraku.ready")
        page.wait_for_function("document.documentElement.dataset.agentPets", timeout=60000)
        return page, errors

    def read(self, page, t, tab=None):
        return page.evaluate(READ, {"t": t, "tab": tab})

    def test_a2_the_askers_pet(self):
        """Outside the video layout A2 has the pet of the agent that asked (the lead: まとめ), beside the
        heading of when it asked: none before the asking, working until the answer, then idle; no CAE row."""
        page, errors = self.open("tab=A2&t=0")
        self.assertEqual(self.read(page, ASK_T - 0.5)["a2"], [])
        for t, want in ((ASK_T + 0.3, "working"), (ANSWER_T + 0.3, "idle"), (1e6, "idle")):
            got = self.read(page, t)
            self.assertEqual([(p["agent"], p["name"], p["state"]) for p in got["a2"]], [("lead", "まとめ", want)], t)
            self.assertGreater(got["a2"][0]["ink"], 0)
        self.assertTrue(page.evaluate("document.querySelector('.a2-pets .a2-askers .ipet') !== null"))
        self.assertIn("まとめ が聞いたこと", page.inner_text("#a2-when"))
        self.assertEqual(errors, [])
        page.close()

    def test_a2_one_pet_per_cae_agent_of_the_record(self):
        """The video layout's A2 row: one pet per CAE agent of the record (six: thermal has no agent), as
        its role, in the order of their first analysis.started, each with its domain's name under it, all drawn."""
        page, errors = self.open("tab=A2&t=0&video=1")
        got = self.read(page, 1e6)
        self.assertEqual(got["loaded"], "on")
        self.assertEqual([p["agent"] for p in got["a2"]], ORDER)
        for p in got["a2"]:
            self.assertEqual(p["role"], p["agent"])
            self.assertEqual(p["name"], NAMES[p["agent"]])
            self.assertGreater(p["ink"], 0, p["agent"])
        self.assertNotEqual(got["slot"]["display"], "none")
        self.assertEqual(errors, [])
        page.close()

    def test_a2_pose_changes_at_each_analysis_started(self):
        """Before an agent's first analysis.started its pet is idle, from it working: at each start the
        agents before it work and the ones after it are still idle (the record's order; the video layout)."""
        page, errors = self.open("tab=A2&t=0&video=1")
        for k, a in enumerate(ORDER):
            for t, working in ((START_T[a] - 0.05, ORDER[:k]), (START_T[a] + 0.05, ORDER[: k + 1])):
                got = self.read(page, t)
                states = {p["agent"]: p["state"] for p in got["a2"]}
                for b in ORDER:
                    want = "working" if b in working else "idle"
                    self.assertEqual(states[b], want, f"{b} at {t}")
                    self.assertEqual({p["agent"]: p["mark"] for p in got["a2"]}[b], want)
                    self.assertEqual({p["agent"]: p["look"] for p in got["a2"]}[b], want)
        self.assertEqual(errors, [])
        page.close()

    def test_same_t_same_picture(self):
        """A page opened at ?t= and one played (seeked) to the same t draw the same pictures, on A2 and A1."""
        pages = []
        # A2 (the video layout's CAE row) with agents before and after their start, and its asker (outside
        # it) while asking; A1 mid pass of its first card and before the last one's turn
        # (warm: a time the canvas is there at, read once first: Chrome moves a 2D canvas off the GPU at its first read)
        for tab, t, side, more, warm in (("A2", START_T["cae.nvh"] + 0.3, "a2", "&video=1", 2.0), ("A2", ASK_T + 0.3, "a2", "", ASK_T + 0.1), ("A1", PICKED_T + 1.0, "a1", "", 2.0)):
            a, errors_a = self.open(f"tab={tab}&t=0{more}")
            self.read(a, warm)
            sought = self.read(a, t)
            b, errors_b = self.open(f"tab={tab}&t={t}{more}")
            self.read(b, None)
            opened = self.read(b, None)
            self.assertTrue(sought[side], tab)
            self.assertEqual([(p["state"], p["pixels"]) for p in sought[side]], [(p["state"], p["pixels"]) for p in opened[side]], tab)
            self.assertEqual(errors_a + errors_b, [])
            pages += [a, b]
        self.assertEqual(len(sought["a1"]), 7)
        for p in pages:
            p.close()

    def test_a2_slot_stays_empty_without_a_cae_agent(self):
        """The video layout, a record without a CAE agent: nothing is put in the slot, so it stays hidden
        (display: none). Outside the video layout, with no hearing in the record, the place is hidden too."""
        page, errors = self.open("tab=A2&t=1e6", run=NONE_RUN)
        self.assertEqual(self.read(page, 1e6)["pets"], None)
        self.assertEqual(errors, [])
        page.close()
        page, errors = self.open("tab=A2&t=1e6&video=1", run=NONE_RUN)
        got = self.read(page, 1e6)
        self.assertEqual(got["loaded"], "on")
        self.assertEqual(got["a2"], [])
        self.assertEqual(got["slot"], {"display": "none", "children": 0})
        self.assertEqual(errors, [])
        page.close()

    def test_a1_pets_follow_the_cards(self):
        """A1 in the film layout (video=1, where the intake answers in turn; outside it the cards light
        together): each card's place holds its domain's CAE pet. The picked domains answer in turn
        (A1_TURN: the record's pick, then REPLY_STEP_S apart): until its turn a card waits (idle), at it
        the pet hops (pass) for PASS_S seconds, then works; thermal is off (idle)."""
        page, errors = self.open("tab=A1&t=0&video=1")
        before = self.read(page, PICKED_T - 0.2)
        for p in before["a1"]:  # before the pick the cards are the model's (the sample's project lights some): the pet follows its card
            self.assertEqual(p["state"], "working" if p["card"] == "lit" else "idle", p["domain"])
        for d, at in A1_TURN.items():
            waits = ((at - 0.05, "wait", "idle"),) if at > PICKED_T else ()  # (the first answers at the pick itself)
            for t, card, pose in (*waits, (at + 0.3, "lit", "pass"), (at + PASS_S + 0.3, "lit", "working")):
                by = {p["domain"]: p for p in self.read(page, t)["a1"]}
                self.assertEqual(sorted(by), sorted(list(A1_TURN) + ["thermal"]))
                self.assertEqual((by[d]["card"], by[d]["state"]), (card, pose), f"{d} at {t}")
                if pose != "idle":
                    self.assertEqual(by[d]["look"], pose, f"{d} at {t}")
        got = {p["domain"]: p for p in self.read(page, 1e6)["a1"]}
        for d, p in got.items():
            self.assertEqual(p["role"], f"cae.{d}")
            self.assertGreater(p["ink"], 0, d)
            self.assertEqual((p["card"], p["state"]), ("off", "idle") if d == "thermal" else ("lit", "working"), d)
        self.assertEqual(errors, [])
        page.close()
        # outside the film layout: the cards light together and the canvas is 88 wide
        page, errors = self.open("tab=A1&t=0")
        got = {p["domain"]: p for p in self.read(page, max(A1_TURN.values()) - 0.05)["a1"]}
        for d in A1_TURN:
            self.assertEqual((got[d]["card"], got[d]["state"]), ("lit", "pass"), d)  # all hop from the record's pick
        w, h = page.evaluate(CANVAS_SIZE)  # 88 wide; the place also holds the name
        self.assertEqual(w, 88)
        self.assertTrue(80 <= h <= 88, h)
        self.assertEqual(errors, [])
        page.close()

    def test_a1_lead_pet_beside_the_headline(self):
        """A1's place "lead" holds the lead's pet (72 x 72): working while the choice is being made (wait),
        a pass hop for PASS_S seconds from the moment it is made (done), then idle. In the film layout the
        choice is made when the last card has answered (LEAD_DONE_FILM), outside it at the record's pick."""
        for query, done in (("video=1", LEAD_DONE_FILM), ("", PICKED_T)):
            page, errors = self.open(f"tab=A1&t=0&{query}")
            before = self.read(page, PICKED_T - 0.2)["lead"]
            self.assertEqual(before["role"], "lead")
            self.assertEqual(before["state"], "working" if before["card"] == "wait" else "idle", before["card"])
            steps = [(done + 0.3, "done", "pass"), (done + PASS_S + 0.3, "done", "idle"), (1e6, "done", "idle")]
            if done > PICKED_T:
                steps.insert(0, (done - 0.05, "wait", "working"))
            for t, card, pose in steps:
                got = self.read(page, t)["lead"]
                self.assertEqual((got["card"], got["state"]), (card, pose), f"{query} at {t}")
                self.assertGreater(got["ink"], 0)
                if pose != "idle":
                    self.assertEqual(got["look"], pose)
            if not query:  # (with video=1 the intake's video rule for A1's pets, 104 px, also takes this place)
                w, h = page.evaluate(CANVAS_SIZE, ".in-headrow .in-pet canvas")  # 72 wide; the name under it may take a few px off
                self.assertEqual(w, 72)
                self.assertTrue(64 <= h <= 72, h)
            self.assertEqual(errors, [])
            page.close()

    def test_a1_video_pets_fill_the_bigger_place_without_the_present_card(self):
        """present=1 and video=1: the intake's CSS makes A1's pet canvas 104 px wide and the pet is drawn at
        the size the canvas is shown; the present mode's card is not over A1 or A2."""
        page, errors = self.open("present=1&video=1&tab=A1&t=1e6")
        self.read(page, 1e6)
        # 104 wide; the place (104 x 128) also holds the name under the pet, so the flex column may take a few px off its height
        w, h = page.evaluate(CANVAS_SIZE)
        self.assertEqual(w, 104)
        self.assertTrue(96 <= h <= 104, h)
        # drawn at the size it is shown (canvas px over CSS px the same both ways: the pet is not stretched)
        cw, ch, sw, sh = page.evaluate("(() => { const c = document.querySelector('.in-cards .in-pet canvas'); return [c.width, c.height, c.clientWidth, c.clientHeight]; })()")
        self.assertAlmostEqual(cw / sw, ch / sh, delta=0.02)
        self.assertGreaterEqual(cw, sw)
        rail = "getComputedStyle(document.getElementById('present-rail')).display"
        self.assertEqual(page.evaluate(rail), "none")
        self.read(page, 1e6, tab="A2")
        self.assertEqual(page.evaluate(rail), "none")
        self.assertEqual(errors, [])
        page.close()

    def test_a2_row_keeps_above_the_subtitle_band(self):
        """present=1 and video=1 at 1920 x 1080: the row is A2_PETS_H (120 px) tall at the top of the left
        column and both columns end above the subtitle band (the bottom 170 px)."""
        page, errors = self.open("present=1&video=1&tab=A2&t=1e6")
        page.evaluate("async () => { await window.hiraku.seek(1e6); }")
        # the row and both columns laid out (in a full run the page can be read before A2's pane is
        # shown): waited for as a condition, then read
        page.wait_for_function(A2_LAID_OUT, arg=len(ORDER), timeout=60000)
        got = self.read(page, None)
        self.assertEqual(len(got["a2"]), len(ORDER))
        self.assertAlmostEqual(got["pets"]["h"], 120, delta=1)
        self.assertLessEqual(got["left"]["bottom"], STAGE_BOTTOM)
        self.assertLessEqual(got["right"]["bottom"], STAGE_BOTTOM)
        self.assertEqual(errors, [])
        page.close()


if __name__ == "__main__":
    unittest.main()
