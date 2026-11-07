"""The agents as their 3D pets on the app's screen (js/agent_pets.js): the workbench's list and the grid.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
The pets are the promo department's promo/pets/pet3d.js, served at /pets/pet3d.js (the server's
default file); a test that needs it is skipped when it is not in the checkout. The run is the A6
fixture (tests.web.domains_fixture: lead, designer and the seven CAE agents) with the events below
added, so that every agent and every motion of agent_pets.js PET_EVENTS shows. Shape example
only: none of the values is a calculation result.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku import contract
from hiraku.webserve.server import PETS_FILE, make_server
from tests.web.domains_fixture import domain_events
from tests.web.test_browser import ENABLED, sync_playwright

RUN = "20261104-120000-pets"
CAE = ["cae.crash", "cae.nvh", "cae.strength", "cae.thermal", "cae.handling", "cae.powertrain", "cae.aero"]
AGENTS = ["lead", "designer", "packager", *CAE]
SAID = "前の当たり方を先に見積もってから、壁にぶつける解析を回します (形の見本)"


def ten_agents() -> list[dict]:
    """domain_events() with the packager, every agent started, and one event of each kind that moves a pet."""
    events = domain_events()
    base = events[0]
    cmp = json.loads((contract.EXAMPLES_DIR / "comparison.json").read_text(encoding="utf-8"))
    cmp.update({"comparison_id": "cmp-pets", "analysis_id": "ana-0002", "status": "mismatch", "diff_rel": 0.3, "diff_abs": 36.0})
    asked = json.loads((contract.EXAMPLES_DIR / "hearing_asked.json").read_text(encoding="utf-8"))
    answered = json.loads((contract.EXAMPLES_DIR / "hearing_answered.json").read_text(encoding="utf-8"))

    def ev(t, kind, agent, data=None, text=None):
        e = {"seq": 0, "ts": base["ts"], "t": t, "run_id": base["run_id"], "kind": kind, "agent": agent, "data": data or {}}
        if text is not None:
            e["text"] = text
        return e

    extra = [ev(0.1 + 0.01 * i, "agent.started", a, {"agent_id": a}) for i, a in enumerate(AGENTS)]
    extra += [
        ev(0.3, "hearing.asked", "lead", asked),
        ev(1.0, "hearing.answered", "user", answered),
        ev(2.7, "comparison.recorded", "cae.nvh", cmp),
        ev(3.2, "agent.said", "cae.crash", text=SAID),
        ev(5.2, "hook.denied", "cae.thermal", {"hook": "PreToolUse", "tool": "mcp__hiraku__analyze", "rule": "HK1_prediction_first"}, "予測が無いので解析は止めました (形の見本)"),
        ev(15.6, "hook.blocked_stop", "lead", {"rule": "HK4", "count": 1, "limit": 3}, "判定がそろっていないので終われない (形の見本)"),
    ]
    out = sorted(events + extra, key=lambda e: e["t"])
    for i, e in enumerate(out):
        e["seq"] = i
    return out


LONG_REASON = "解析の道具が入力のファイルを読めずに終わったので、この役はここで止まりました (形の見本、長い文はくわしくに入る)"
# agent.stopped's stop_code and stop_reason (contract a31e520) per agent, and what the screen says:
# (agent, data, the agents' pane's why line or None, the log line's words in brackets or None, the note or None)
STOPS = [
    # (the pane's line is short: labels.js AGENT_STOP_SHORT_JA; the log keeps AGENT_STOP_JA)
    # (done: the name's 「終わり」 says it; no 「仕事を終えた」 line under it)
    ("cae.crash", {"stop_code": "done", "stop_reason": "仕事を終えた (形の見本)"}, None, "自分の仕事を終えた", "仕事を終えた (形の見本)"),
    ("cae.nvh", {"stop_code": "run_ended"}, "動いている間に実行が終わった", "動いている間に実行が終わった", None),
    ("cae.strength", {"stop_code": "user_stop"}, "人が止めた", "人が止めた", None),
    ("cae.thermal", {"stop_code": "error", "stop_reason": LONG_REASON}, "エラーで終わった", "エラーで終わった", None),
    ("cae.handling", {}, None, None, None),  # a record from before stop_code: 手を止めた only
]


def stopped_agents() -> list[dict]:
    """domain_events() with the CAE agents started and five of them stopped (STOPS) at 2.0-2.4 s."""
    events = domain_events()
    base = events[0]

    def ev(t, kind, agent, data):
        return {"seq": 0, "ts": base["ts"], "t": t, "run_id": base["run_id"], "kind": kind, "agent": agent, "data": data}

    extra = [ev(0.1 + 0.01 * i, "agent.started", a, {"agent_id": a}) for i, a in enumerate(AGENTS)]
    extra += [ev(2.0 + 0.1 * i, "agent.stopped", a, {"agent_id": a, **d}) for i, (a, d, *_) in enumerate(STOPS)]
    out = sorted(events + extra, key=lambda e: e["t"])
    for i, e in enumerate(out):
        e["seq"] = i
    return out


# (t, {agent: motion}) at pace=0 (display t = recorded t); agents not named: as `rest`.
EXPECT = [
    (0.05, {}, "waiting"),  # nothing started yet
    (0.25, {}, "working"),  # all started
    (0.5, {"lead": "waiting"}, "working"),  # the lead asked (hearing.asked)
    (1.1, {}, "working"),  # answered: the lead works again
    (2.8, {"cae.nvh": "mismatch"}, "working"),  # its comparison did not match
    (3.3, {"cae.crash": "report", "cae.nvh": "mismatch"}, "working"),  # said something
    (4.6, {"cae.crash": "report", "cae.nvh": "mismatch", "cae.strength": "pass"}, "working"),  # judged green (none before)
    (5.3, {"cae.crash": "report", "cae.nvh": "mismatch", "cae.strength": "pass", "cae.thermal": "blocked"}, "working"),  # stopped by hooks
    (6.0, {"cae.crash": "report", "cae.strength": "pass", "cae.thermal": "blocked"}, "working"),  # nvh's 3 s are over
    (12.1, {"cae.nvh": "pass"}, "working"),  # red -> green
    (13.1, {"cae.nvh": "pass"}, "working"),  # strength green -> green: no reaction (nvh's 3 hops from 12.0 go on)
    (14.8, {"cae.thermal": "pass"}, "working"),  # thermal red -> green at 14.0; nvh's pass is over
    (15.7, {"lead": "blocked", "cae.thermal": "pass"}, "working"),  # the lead's stop was blocked (HK4)
    (17.2, {"lead": "blocked"}, "working"),  # handling, powertrain, aero: green -> green, no reaction
    (18.7, {}, "working"),  # the lead's 3 s are over
    (1e6, {}, "idle"),  # the run is over (the replay's end)
]

READ = """async ({ t, tab }) => {
  if (tab) document.querySelector(`.tab[data-tab="${tab}"]`).click();
  await window.hiraku.seek(t ?? window.hiraku.player.playhead);
  const shown = (e) => e.offsetParent !== null;
  // (the list's and the grid's pets; ③'s board of tests has its own: test_test_board_browser)
  const pets = [...document.querySelectorAll(".petbox")].filter((b) => shown(b) && !b.closest("#tests")).map((b) => {
    const c = b.querySelector("canvas.pet3d");
    let ink = 0;
    if (c && c.width) { const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data; for (let i = 3; i < d.length; i += 4) ink += d[i] > 0 ? 1 : 0; }
    return { agent: b.dataset.agent, role: b.dataset.role, state: b.dataset.petState, look: b.dataset.petLook ?? null, draws: Number(b.dataset.petDraws), ink,
             where: b.closest(".tile")?.dataset.domain ?? (b.closest("#grid-head") ? "head" : b.closest("#agents") ? "list" : null),
             pixels: c ? c.toDataURL() : null };
  });
  const says = [...document.querySelectorAll("[data-say]")].filter(shown).map((e) => ({ agent: e.dataset.say, text: e.textContent, on: Number(e.style.opacity || 0) > 0 }));
  return { loaded: document.documentElement.dataset.agentPets ?? null, agents: window.hiraku.state().agentOrder, pets, says,
           circles: [...document.querySelectorAll("#agents li:not(.agroup) .pet, #tiles .tile .pet")].filter(shown).length,
           names: [...document.querySelectorAll("#agents li")].map((li) => li.textContent) };
}"""


@unittest.skipUnless(ENABLED, "set HIRAKU_WEB_BROWSER=1 with Playwright installed")
class AgentPetsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.servers = []
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        for s in cls.servers:
            s.shutdown()
            s.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def serve(self, pets_file=PETS_FILE, events=None):
        runs = self.tmp / f"runs-{len(self.servers)}"
        (runs / RUN).mkdir(parents=True)
        lines = events if events is not None else ten_agents()
        (runs / RUN / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in lines), encoding="utf-8")
        server = make_server("127.0.0.1", 0, runs, quiet=True, saved_dir=None, launcher=False, pets_file=pets_file)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def open(self, base, query, bundles_open=True, motion="on"):
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        # (one row per agent: the bundles of the agents' list open, ?agents=open; the bundles have their own test)
        # (?petmotion=on: the pets move as with a GPU, though this Chromium draws by SwiftShader; None: as detected)
        pm = f"&petmotion={motion}" if motion else ""
        page.goto(f"{base}/?run={RUN}&pace=0{'&agents=open' if bundles_open else ''}{pm}&{query}", wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=120000)
        page.evaluate("window.hiraku.ready")
        return page, errors

    def need_pets(self):
        if not PETS_FILE.is_file():
            self.skipTest("needs promo/pets/pet3d.js (the promo department's) in the checkout")

    def test_one_pet_per_agent_of_the_record(self):
        """The list has one pet per agent of the record (s.agentOrder), each as its role and drawn;
        the grid has the same agents: each CAE agent in its domain's tile, the packager in
        assembly's, the lead and the designer in the head."""
        self.need_pets()
        page, errors = self.open(self.serve(), "tab=bench&t=1e6")
        got = page.evaluate(READ, {"t": None, "tab": None})
        self.assertEqual(got["loaded"], "on")
        self.assertEqual(sorted(got["agents"]), sorted(AGENTS))
        self.assertEqual(sorted(p["agent"] for p in got["pets"]), sorted(AGENTS))
        for p in got["pets"]:
            self.assertEqual(p["role"], p["agent"])
            self.assertEqual(p["where"], "list")
            self.assertGreater(p["ink"], 0, p["agent"])
        self.assertEqual(got["circles"], 0)
        grid = page.evaluate(READ, {"t": None, "tab": "grid"})
        where = {p["agent"]: p["where"] for p in grid["pets"]}
        self.assertEqual(sorted(where), sorted(AGENTS))
        self.assertEqual(where["lead"], "head")
        self.assertEqual(where["designer"], "head")
        self.assertEqual(where["packager"], "assembly")
        for a in CAE:
            self.assertEqual(where[a], a[4:])
        self.assertTrue(all(p["ink"] > 0 for p in grid["pets"]))
        self.assertEqual(errors, [])
        page.close()

    def test_each_motion_at_its_events_time(self):
        """At each time of EXPECT every pet is in its motion (agent_pets.js PET_EVENTS), in the
        list and in the grid alike, and drawn as pet3d.js's state of that name (v9 has waiting
        and blocked: no motion is drawn as another state any more)."""
        self.need_pets()
        page, errors = self.open(self.serve(), "tab=bench&t=0")
        for tab in ("bench", "grid"):
            for t, named, rest in EXPECT:
                got = page.evaluate(READ, {"t": t, "tab": tab})
                states = {p["agent"]: p["state"] for p in got["pets"]}
                want = {a: named.get(a, rest) for a in states}
                self.assertEqual(states, want, (tab, t))
                self.assertEqual({p["agent"]: p["look"] for p in got["pets"]}, want, (tab, t))
        self.assertEqual(errors, [])
        page.close()

    def test_bubbles_say_the_agents_log_line_for_a_while(self):
        """A bubble is the agent's newest log line, without its name in front, cut short; it shows
        from its event for agent_pets.js SAY_S (5 s) and is gone after."""
        self.need_pets()
        page, errors = self.open(self.serve(), "tab=bench&t=0")
        got = page.evaluate(READ, {"t": 2.8, "tab": None})
        on = {s["agent"]: s["text"] for s in got["says"] if s["on"]}
        self.assertTrue(on["cae.nvh"].startswith("比べた: 差"), on)  # the comparison's log line
        got = page.evaluate(READ, {"t": 3.3, "tab": None})
        on = {s["agent"]: s["text"] for s in got["says"] if s["on"]}
        self.assertTrue(on["cae.crash"].endswith("…"), on)
        self.assertTrue(SAID.startswith(on["cae.crash"][:-1]), on)
        self.assertLessEqual(len(on["cae.crash"]), 26)
        self.assertEqual(on["cae.nvh"], "解析が終わった")  # its newer line (analysis.finished at 3.0) replaced it
        log = page.evaluate("() => [...document.querySelectorAll('#log li')].map((li) => li.textContent)")
        self.assertTrue(any(line.startswith("音振: 比べた: 差") for line in log), log)
        later = page.evaluate(READ, {"t": 3.2 + 5.05, "tab": None})
        self.assertNotIn("cae.crash", {s["agent"] for s in later["says"] if s["on"]})
        # the judgement's line is the bubble of its domain's agent (the event's agent is system)
        grid = page.evaluate(READ, {"t": 12.2, "tab": "grid"})
        nvh = {s["agent"]: s["text"] for s in grid["says"] if s["on"]}
        self.assertTrue(nvh["cae.nvh"].startswith("v2 の判定は OK"), nvh)
        self.assertEqual(errors, [])
        page.close()

    def test_same_picture_at_the_same_t(self):
        """?t= draws the pets the same as a seek to that t (state and pixels), and a still pet is
        drawn once however often the frame is drawn."""
        self.need_pets()
        base = self.serve()
        by = lambda got: {p["agent"]: (p["state"], p["pixels"]) for p in got["pets"]}  # noqa: E731
        # a pass; the thermal agent 0.2 s into its block (the flinch); the lead waiting for answers
        for t, agent, motion in ((4.6, "cae.strength", "pass"), (5.4, "cae.thermal", "blocked"), (0.6, "lead", "waiting")):
            a, errors_a = self.open(base, "tab=bench&t=0")
            a.evaluate(READ, {"t": 4.0, "tab": None})  # (Chrome moves a 2D canvas off the GPU at its first read)
            sought = a.evaluate(READ, {"t": t, "tab": None})
            b, errors_b = self.open(base, f"tab=bench&t={t}")
            b.evaluate(READ, {"t": None, "tab": None})
            opened = b.evaluate(READ, {"t": None, "tab": None})
            self.assertEqual(by(sought), by(opened), t)
            self.assertEqual(by(sought)[agent][0], motion)
            self.assertEqual(errors_a + errors_b, [])
            a.close()
            if t != 0.6:
                b.close()
        still = b.evaluate(READ, {"t": 1e6, "tab": None})
        again = b.evaluate(READ, {"t": 1e6, "tab": None})
        self.assertEqual({p["agent"]: p["draws"] for p in still["pets"]}, {p["agent"]: p["draws"] for p in again["pets"]})
        self.assertEqual(errors_b, [])
        b.close()

    def test_no_gpu_still_pictures(self):
        """WebGL drawn by software (this Chromium's SwiftShader, as detected: no ?petmotion): each pet
        is one still picture, drawn once and not again while the replay plays on, whatever its motion."""
        self.need_pets()
        page, errors = self.open(self.serve(), "tab=bench&t=0", motion=None)
        got = page.evaluate("() => ({ still: document.documentElement.dataset.petsStill, name: window.hiraku.pets().rendererName })")
        self.assertEqual(got["still"], "1", got)
        self.assertRegex(got["name"], r"(?i)swiftshader", got)
        # (a box is drawn again only when its size changes: the rows settle in the first frames)
        page.evaluate(READ, {"t": 0.6, "tab": None})
        first = page.evaluate(READ, {"t": 0.6, "tab": None})
        self.assertTrue(first["pets"])
        draws = {p["agent"]: p["draws"] for p in first["pets"]}
        for t in (2.8, 5.3, 15.7, 1e6):  # waiting, mismatch, blocked, the end
            later = page.evaluate(READ, {"t": t, "tab": None})
            for p in later["pets"]:
                self.assertEqual(p["draws"], draws[p["agent"]], (t, p["agent"]))  # not drawn again
                self.assertEqual(p["look"], "idle", (t, p["agent"]))
                self.assertGreater(p["ink"], 0, p["agent"])
            self.assertEqual({p["agent"]: p["pixels"] for p in later["pets"]}, {p["agent"]: p["pixels"] for p in first["pets"]}, t)
        # the state is still the record's (the row's words and data-pet-state), only the picture holds
        self.assertEqual({p["agent"]: p["state"] for p in later["pets"]}["lead"], "idle")
        self.assertEqual(errors, [])
        page.close()

    def test_which_renderer_is_software(self):
        """agent_pets.js isSoftwareRenderer on the names drivers give (UNMASKED_RENDERER_WEBGL): software
        ones (still pictures) and GPUs (moving pets, as before)."""
        page, errors = self.open(self.serve(), "tab=bench&t=0", motion=None)
        software = ["ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)",
                    "Google SwiftShader", "llvmpipe (LLVM 15.0.7, 256 bits)", "Mesa softpipe", "lavapipe",
                    "ANGLE (Microsoft, Microsoft Basic Render Driver (0x0000008C) Direct3D11 vs_5_0 ps_5_0, D3D11)"]
        gpu = ["ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 (0x00002786) Direct3D11 vs_5_0 ps_5_0, D3D11)",
               "ANGLE (Intel, Intel(R) UHD Graphics 620 (0x00005917) Direct3D11 vs_5_0 ps_5_0, D3D11)",
               "Apple M2", "Mali-G78", "AMD Radeon RX 6600", "Adreno (TM) 740", ""]
        got = page.evaluate("""async ([a, b]) => { const m = await import('/js/agent_pets.js');
            return [a.map(m.isSoftwareRenderer), b.map(m.isSoftwareRenderer)]; }""", [software, gpu])
        self.assertEqual(got, [[True] * len(software), [False] * len(gpu)])
        self.assertEqual(errors, [])
        page.close()

    def test_blocked_and_waiting_poses(self):
        """blocked (pet3d.js v9) flinches from the event's time (o.since: the pose time of the block)
        and holds still from 0.8 s on (drawn no more until its 3 s are over); waiting moves
        all along (the lead between hearing.asked at 0.3 s and hearing.answered at 1.0 s)."""
        self.need_pets()
        page, errors = self.open(self.serve(), "tab=bench&t=0")
        page.evaluate(READ, {"t": 4.0, "tab": None})
        pick = lambda got, a: next(p for p in got["pets"] if p["agent"] == a)  # noqa: E731
        at = {t: pick(page.evaluate(READ, {"t": t, "tab": None}), "cae.thermal") for t in (5.2, 5.3, 6.1, 7.0, 8.1)}
        self.assertTrue(all(p["look"] == "blocked" for p in at.values()), at)
        self.assertNotEqual(at[5.2]["pixels"], at[5.3]["pixels"])  # the flinch
        self.assertEqual(at[6.1]["pixels"], at[7.0]["pixels"])  # settled
        self.assertEqual(at[7.0]["pixels"], at[8.1]["pixels"])
        self.assertEqual(at[6.1]["draws"], at[8.1]["draws"])  # not drawn again while it holds
        # the block's first pose is the same however the screen got there (a seek straight to 5.2)
        fresh, errors_f = self.open(self.serve(), "tab=bench&t=5.2")
        fresh.evaluate(READ, {"t": None, "tab": None})
        self.assertEqual(pick(fresh.evaluate(READ, {"t": None, "tab": None}), "cae.thermal")["pixels"], at[5.2]["pixels"])
        lead = {t: pick(page.evaluate(READ, {"t": t, "tab": None}), "lead") for t in (0.35, 0.6, 0.9)}
        self.assertTrue(all(p["look"] == "waiting" for p in lead.values()), lead)
        self.assertEqual(len({p["pixels"] for p in lead.values()}), 3)  # it moves
        self.assertEqual(errors + errors_f, [])
        page.close()
        fresh.close()

    def test_why_an_agent_stopped(self):
        """agent.stopped's stop_code in words (labels.js AGENT_STOP_JA) under the agent's name in the
        list and in brackets on the log's 手を止めた line, for each of the four codes; a short
        stop_reason under the line and in the row's title, a long one under くわしく; a record
        without either: 手を止めた only, no why line. With and without the pets file alike."""
        for pets_file in (PETS_FILE, self.tmp / "no-such-pet3d.js"):
            if pets_file == PETS_FILE and not PETS_FILE.is_file():
                continue
            page, errors = self.open(self.serve(pets_file, stopped_agents()), "tab=bench&t=1e6")
            got = page.evaluate(
                """() => ({
                  why: Object.fromEntries([...document.querySelectorAll('#agents li')].map((li) => {
                    const w = li.querySelector('.why');
                    const id = li.dataset.agent ?? li.textContent;
                    return [id, w ? { text: w.textContent, title: w.title, code: w.dataset.stopCode, shown: w.offsetParent !== null && getComputedStyle(w).visibility === 'visible',
                                      faint: Number(getComputedStyle(w).opacity) < 1, bold: Number(getComputedStyle(w).fontWeight) >= 600 } : null];
                  })),
                  lines: [...document.querySelectorAll('#log li')].filter((li) => li.textContent.includes('が手を止めた')).map((li) => ({
                    text: (() => { const c = li.cloneNode(true); c.querySelectorAll('.tm, .note, details').forEach((x) => x.remove()); return c.textContent.trim(); })(), note: [...li.querySelectorAll(':scope > .note')].map((n) => n.textContent),
                    more: [...li.querySelectorAll('details.more .note')].map((n) => n.textContent) })),
                  pets: document.documentElement.dataset.agentPets ?? null,
                })"""
            )
            self.assertEqual(len(got["lines"]), len(STOPS), got["lines"])
            for agent, data, row, words, note in STOPS:
                line = [x for x in got["lines"] if x["text"].endswith(f"が手を止めた ({words})" if words else "が手を止めた")]
                self.assertEqual(len(line), 1, (agent, got["lines"]))
                self.assertEqual(line[0]["note"], [note] if note else [], agent)
                self.assertEqual(line[0]["more"], [LONG_REASON] if data.get("stop_reason") == LONG_REASON else [], agent)
                if got["pets"] == "on":
                    why = got["why"][agent]
                    if row is None:
                        self.assertIsNone(why, agent)
                        continue
                    self.assertEqual(why["text"], row, agent)
                    self.assertEqual(why["code"], data["stop_code"], agent)
                    self.assertEqual(why["title"], data.get("stop_reason", ""), agent)
                    self.assertTrue(why["shown"], agent)
                    # done faint; run_ended, user_stop and error stand out
                    self.assertEqual((why["faint"], why["bold"]), (True, False) if data["stop_code"] == "done" else (False, True), agent)
            if got["pets"] != "on":  # the circles' rows carry the same why lines
                whys = sorted(w["text"] for w in got["why"].values() if w)
                self.assertEqual(whys, sorted(r for *_, r, _w, _n in STOPS if r))
            self.assertEqual(errors, [])
            page.close()

    def test_no_bubble_for_a_stop(self):
        """Just after its agent.stopped (cae.crash at 2.0 s) a role has no bubble 「…が手を止めた (…)」:
        the why line under its name says it, shown."""
        if not PETS_FILE.is_file():
            self.skipTest("no pets file")
        page, errors = self.open(self.serve(PETS_FILE, stopped_agents()), "tab=bench&t=2.05")
        got = page.evaluate("""() => { const li = document.querySelector('#agents li[data-agent="cae.crash"]');
            const say = li.querySelector('.say'); const w = li.querySelector('.why');
            return { say: say ? say.textContent : '', saying: li.classList.contains('saying'), why: w ? w.textContent : null,
                     shown: !!w && getComputedStyle(w).visibility === 'visible' }; }""")
        page.close()
        self.assertEqual(errors, [])
        self.assertNotIn("手を止めた", got["say"])
        self.assertFalse(got["saying"])
        self.assertIsNone(got["why"])  # (done: the row's 「終わり」 says it, no line under the name)

    def test_the_cae_roles_are_one_row_until_opened(self):
        """The seven CAE agents are one row 「CAE ×7」 (one designer, one lead: their own rows),
        closed by default; its look follows the members (at work: the same class as a member's row at
        work); pressed, it opens into the seven rows and closes again; the same t is the same list."""
        base = self.serve()
        rows = """() => [...document.querySelectorAll('#agents > li')].map((li) => ({
            group: li.dataset.group ?? null, agent: li.dataset.agent ?? null, member: li.classList.contains('member'),
            cls: ['active', 'alert', 'heed', 'done'].filter((c) => li.classList.contains(c)),
            text: li.querySelector('.ag-toggle')?.textContent ?? null, open: li.querySelector('.ag-toggle')?.getAttribute('aria-expanded') ?? null }))"""
        for t in (0.25, 5.3, 1e6):
            page, errors = self.open(base, f"tab=bench&t={t}", bundles_open=False)
            closed = page.evaluate(rows)
            text = page.inner_text("#agents")
            self.assertEqual([r["agent"] or r["group"] for r in closed], ["lead", "designer", "packager", "cae"], t)
            head = closed[3]
            self.assertEqual((head["text"], head["open"]), ("▸CAE ×7", "false"), t)
            page.click("#agents [data-agent-group='cae']")
            page.wait_for_selector("#agents li.member")
            opened = page.evaluate(rows)
            members = [r for r in opened if r["member"]]
            self.assertEqual(sorted(r["agent"] for r in members), sorted(CAE), t)
            self.assertEqual(opened[3]["open"], "true", t)
            # the bundle's look: an alert first (red: a NG judgement; else heed, orange: stopped by
            # hooks or a mismatch), else at work when one member is
            want = ["alert"] if any("alert" in r["cls"] for r in members) else ["heed"] if any("heed" in r["cls"] for r in members) \
                else ["active"] if any("active" in r["cls"] for r in members) \
                else ["done"] if all("done" in r["cls"] for r in members) else []
            self.assertEqual(head["cls"], want, t)
            page.click("#agents [data-agent-group='cae']")
            page.wait_for_function("!document.querySelector('#agents li.member')")
            again, errors2 = self.open(base, f"tab=bench&t={t}", bundles_open=False)
            self.assertEqual((again.evaluate(rows), again.inner_text("#agents")), (closed, text), t)  # the same t: the same list
            self.assertEqual(errors + errors2, [])
            page.close()
            again.close()

    def test_without_the_pets_file_circles_and_names(self):
        """No pet3d.js (404): the list is the circles and names as before, the tiles keep their circles."""
        page, errors = self.open(self.serve(self.tmp / "no-such-pet3d.js"), "tab=bench&t=1e6")
        got = page.evaluate(READ, {"t": None, "tab": None})
        self.assertEqual(got["loaded"], "none")
        self.assertEqual(got["pets"], [])
        self.assertEqual(got["circles"], len(AGENTS))
        self.assertTrue(any(n.startswith("まとめ") for n in got["names"]), got["names"])
        grid = page.evaluate(READ, {"t": None, "tab": "grid"})
        self.assertEqual(grid["pets"], [])
        self.assertEqual(grid["circles"], 8)  # one per tile, as before
        self.assertEqual(errors, [])
        page.close()


if __name__ == "__main__":
    unittest.main()
