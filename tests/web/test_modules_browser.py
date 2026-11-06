"""A design split into modules on the screen (js/modules.js, js/proposals.js), on the sample record
tests.web.modules_fixture: the module colours and their legend, the modules' spaces and the part
outside its space, the round's proposals and the version the packager built, the six module
designers in the agents' list, ?t= drawing the same, and no switches for a design without modules.

The record's schema check runs always; the rest is skipped unless HIRAKU_WEB_BROWSER=1
(python -m tests.web.browser sets it) and Playwright is installed, as test_browser.
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

from hiraku import contract
from hiraku.webserve.server import make_server
from tests.web.modules_fixture import RUN_ID, module_design, module_events, outside_parts

try:
    from tests.web.pages import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None

PLAIN = "20261104-210100-plain"  # the same run without modules
FALLBACK = {"frame": "#2b6e40", "suspension": "#8db33a", "steering": "#e2759f", "occupant": "#917430", "powertrain": "#8f415b", "body": "#57b8a0"}
# (powertrain: the pets' #7d2240, made lighter for the 3D by proposals.js readable)
OUTSIDE = "#e0201a"
T_PENDING = 4.6  # the three proposals of round 1 are in, version 2 not built yet
T_BUILT = 6.5  # version 2 built from them


def write_run(root: Path, run_id: str, events: list[dict]) -> None:
    (root / run_id).mkdir(parents=True)
    (root / run_id / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")


def plain_events() -> list[dict]:
    """The module record with module, modules and module_changes taken out of every design."""
    out = []
    for e in module_events():
        if e["kind"] == "design.change_proposed":
            continue
        if e["kind"] == "design.created":
            d = e["data"]
            d.pop("modules", None)
            d.pop("module_changes", None)
            for x in d["parts"] + d["members"]:
                x.pop("module", None)
        out.append(e)
    for i, e in enumerate(out):
        e["seq"] = i
    return out


NAMED = "20261104-210200-named"  # the module record with design.variable_names on every version
NAMES = {"front_spring_rate": "前輪側コイルばねのレート", "rail_t": "縦レールの肉厚"}


def shown_name(page, var: str) -> str:
    """The name the screen gives a variable of the module record: the base design's own name
    (design.variable_names, written by the generator), else the screen's table (labels.js
    variableName), so the expected words follow the design data when its names change."""
    own = (module_design().get("variable_names") or {}).get(var)
    if isinstance(own, str) and own.strip():
        return own
    return page.evaluate("async (v) => (await import('/js/labels.js')).variableName(v)", var)


def named_events() -> list[dict]:
    """The module record with variable_names (two of the changed variables) on every design."""
    out = module_events()
    for e in out:
        if e["kind"] == "design.created":
            e["data"]["variable_names"] = dict(NAMES)
    return out


LEFT = "20261105-210300-left"  # the module record with proposals the packager never built


def left_events() -> list[dict]:
    """The module record with two proposals no version takes: chg-0090 on version 2 just before
    version 3 is built from the others (as 20261105-094333), and chg-0091 on version 3 just before
    the run finishes (as 20261105-091217). -> events (T_STALE, T_LATE are when each is in)."""
    events = module_events()
    proposal = next(e for e in reversed(events) if e["kind"] == "design.change_proposed")
    i3 = max(i for i, e in enumerate(events) if e["kind"] == "design.created")
    stale = copy.deepcopy(proposal)
    stale["t"] = round(events[i3 - 1]["t"] + 0.1, 2)
    stale["data"]["change_id"] = "chg-0090"
    events.insert(i3, stale)
    fin = next(i for i, e in enumerate(events) if e["kind"] == "run.finished")
    late = copy.deepcopy(proposal)
    late["t"] = round(events[fin - 1]["t"] + 0.1, 2)
    late["design_version"] = 3
    late["data"]["change_id"] = "chg-0091"
    events.insert(fin, late)
    for i, e in enumerate(events):
        e["seq"] = i
    return events


class ModuleRecordTest(unittest.TestCase):
    def test_record_passes_the_schema(self):
        events = module_events()
        for e in events:
            self.assertEqual(contract.errors("event", e), [], f"seq {e['seq']} {e['kind']}")
        kinds = [e["kind"] for e in events]
        self.assertEqual(kinds.count("design.change_proposed"), 5)
        self.assertEqual(kinds.count("design.created"), 3)
        designs = [e["data"] for e in events if e["kind"] == "design.created"]
        self.assertEqual([len(d.get("module_changes", [])) for d in designs], [0, 3, 2])
        # one part outside its module's space, in version 3 only
        self.assertEqual([outside_parts(d) for d in designs], [[], [], ["part:battery"]])
        joints = {n.get("joint") for n in designs[0]["nodes"]}
        self.assertEqual(joints, {"weld", "bolt", "pin"})


class _ModulesPage(unittest.TestCase):
    """The server on the module records and a browser (no tests of its own: the tests are in two
    classes, so a runner that puts each class in its own process runs them side by side)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        write_run(cls.tmp / "runs", RUN_ID, module_events())
        write_run(cls.tmp / "runs", PLAIN, plain_events())
        write_run(cls.tmp / "runs", NAMED, named_events())
        write_run(cls.tmp / "runs", LEFT, left_events())
        cls.server = make_server("127.0.0.1", 0, cls.tmp / "runs", quiet=True, saved_dir=None, launcher=False)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        cls.v3 = [e["data"] for e in module_events() if e["kind"] == "design.created"][-1]

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def open(self, query, size=(1280, 720)):
        page = self.browser.new_page(viewport={"width": size[0], "height": size[1]})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{self.base}/?{query}", wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
        page.evaluate("window.hiraku.ready")
        # drawn: the fonts in, and two frames of the screen's own loop after the replay reached t
        page.evaluate("document.fonts.ready.then(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))))")
        self.addCleanup(page.close)
        self.addCleanup(lambda: self.assertEqual(errors, []))
        return page

    def expected_counts(self):
        out = {}
        for m in self.v3["modules"]:
            mid = m["module_id"]
            parts = sum(p["module"] == mid for p in self.v3["parts"])
            members = sum(x["module"] == mid for x in self.v3["members"])
            if parts or members:
                out[mid] = (parts, members)
        return out


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class ModulesBrowserTest(_ModulesPage):
    """The module colours and spaces, the round card, the variables' names."""

    def test_module_colours_and_legend(self):
        page = self.open(f"run={RUN_ID}&tab=bench&pace=0&pets=0&t=1e6&on=modules")
        self.assertEqual(page.locator('#toggles [data-toggle="modules"]').get_attribute("class"), "on")
        rows = page.locator(".modlegend .mlist li")
        want = self.expected_counts()
        self.assertEqual(rows.count(), len(want))
        names = json.loads(page.evaluate("JSON.stringify(Array.from(document.querySelectorAll('.modlegend .mlist li')).map(li => [li.dataset.module, +li.dataset.parts, +li.dataset.members, li.querySelector('.mn').textContent]))"))
        for mid, parts, members, name in names:
            self.assertEqual((parts, members), want[mid])
            self.assertEqual(name, contract.MODULE_NAMES_JA[mid])  # from the server (the contract)
        shown = page.evaluate("window.hiraku.modules.shown()")
        self.assertEqual(set(shown["covers"]), {FALLBACK[m] for m in want})
        self.assertEqual(shown["boxes"], 0)
        # the contour is off while the module colours are on
        self.assertTrue(page.locator("#legend").is_hidden())

    def test_spaces_and_the_part_outside(self):
        page = self.open(f"run={RUN_ID}&tab=bench&pace=0&pets=0&t=1e6&on=spaces")
        shown = page.evaluate("window.hiraku.modules.shown()")
        self.assertEqual(shown["boxes"], sum(len(m["envelope"]) for m in self.v3["modules"]))
        self.assertTrue(shown["onBench"])
        self.assertEqual(set(shown["covers"]), {OUTSIDE})  # only the battery, red
        out = page.locator(".modlegend .mout")
        self.assertEqual(out.get_attribute("data-outside"), "1")
        self.assertIn("はみ出し 1 個", out.inner_text())
        self.assertIn("電池", out.inner_text())
        # version 2 (before the battery grew): nothing outside
        page2 = self.open(f"run={RUN_ID}&tab=bench&pace=0&pets=0&t={T_BUILT}&on=modules,spaces")
        self.assertEqual(page2.locator(".modlegend .mout").get_attribute("data-outside"), "0")
        self.assertNotIn(OUTSIDE, page2.evaluate("window.hiraku.modules.shown()")["covers"])

    def test_the_round_pending_then_built(self):
        page = self.open(f"run={RUN_ID}&tab=bench&pace=0&pets=0&t={T_PENDING}")
        card = page.locator("#roundcard")
        self.assertTrue(card.is_visible())
        self.assertEqual(card.locator(".rc-head").get_attribute("data-round"), "pending")
        items = card.locator(".rc-list li")
        self.assertEqual([items.nth(i).get_attribute("data-change") for i in range(items.count())], ["chg-0001", "chg-0002", "chg-0003"])
        text = card.inner_text()
        self.assertIn("足まわりの設計役", text)
        self.assertIn(shown_name(page, "front_spring_rate"), text)
        self.assertIn("パッケージ役が 1 つの版にまとめるのを待っている", text)
        page = self.open(f"run={RUN_ID}&tab=bench&pace=0&pets=0&t={T_BUILT}")
        card = page.locator("#roundcard")
        self.assertEqual(card.locator(".rc-head").inner_text(), "パッケージ役が 3 つの提案を v2 にまとめた")
        self.assertEqual(card.locator(".rc-list li").count(), 3)
        log = page.locator("#log").inner_text()
        self.assertIn("パッケージ役が 3 つの提案を v2 にまとめた", log)
        self.assertIn(f"車体の設計役: {shown_name(page, 'cargo_height')}", log)
        # the grid's head names each module's change
        page = self.open(f"run={RUN_ID}&tab=grid&pace=0&pets=0&t=1e6")
        head = page.locator("#grid-head [data-built]").inner_text()
        self.assertIn("2 つの提案", head)
        self.assertIn(f"枠 {shown_name(page, 'rail_t')} 2.3 → 3.2 mm", head)

    def test_the_round_card_drops_proposals_left_behind(self):
        """A proposal on a version that a newer one replaced without it, and one still waiting when
        the run finishes, are not shown as waiting for the packager (proposals.js roundCardHtml)."""
        events = left_events()
        t = {e["data"]["change_id"]: e["t"] for e in events if e["kind"] == "design.change_proposed"}
        t_v3 = max(e["t"] for e in events if e["kind"] == "design.created")
        t_end = next(e["t"] for e in events if e["kind"] == "run.finished")
        # one page, seeking the replay (window.hiraku.seek): the card at each time
        page = self.open(f"run={LEFT}&tab=bench&pace=0&pets=0&t={t['chg-0090'] + 0.05}")
        at = lambda tt: page.evaluate("""async (tt) => { await window.hiraku.seek(tt);
            await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
            const c = document.getElementById('roundcard');
            return { hidden: !c || c.hidden, round: c?.querySelector('.rc-head')?.dataset.round ?? null,
                     ids: [...(c?.querySelectorAll('.rc-list li') ?? [])].map((li) => li.dataset.change) }; }""", tt)
        # chg-0090 waits beside the round's others while version 2 is the newest
        got = at(t["chg-0090"] + 0.05)
        self.assertEqual(got["round"], "pending")
        self.assertIn("chg-0090", got["ids"])
        # version 3 built without it: the card names what was built, chg-0090 is gone
        got = at(t_v3 + 0.05)
        self.assertEqual(got["round"], "built")
        self.assertNotIn("chg-0090", got["ids"])
        # chg-0091 on version 3 waits while the run goes on (a record cut there keeps it) ...
        got = at(t["chg-0091"] + 0.05)
        self.assertEqual((got["round"], got["ids"]), ("pending", ["chg-0091"]))
        # ... and not once the run has finished (version 3's built card stays its ROUND_SHOW_S, without it)
        self.assertLess(t_end - t_v3, 12)
        got = at(t_end + 0.05)
        self.assertEqual(got["round"], "built")
        self.assertNotIn("chg-0091", got["ids"])
        # the record's end (a seek past it stops there): still no waiting card
        got = at(1e6)
        self.assertNotEqual(got["round"], "pending")
        self.assertNotIn("chg-0091", got["ids"])

    def test_variable_names_from_the_design(self):
        # design.variable_names (contract b615e2c) first, then the screen's table (labels.js
        # VARIABLE_JA), then the variable's id
        page = self.open(f"run={NAMED}&tab=bench&pace=0&pets=0&t={T_PENDING}")
        text = page.locator("#roundcard").inner_text()
        self.assertIn(NAMES["front_spring_rate"], text)
        self.assertNotIn("前のばねの硬さ", text)
        page = self.open(f"run={NAMED}&tab=grid&pace=0&pets=0&t=1e6")
        self.assertIn(f"枠 {NAMES['rail_t']} 2.3 → 3.2 mm", page.locator("#grid-head [data-built]").inner_text())
        got = page.evaluate("""async () => {
          const P = await import('/js/proposals.js');
          const chg = { changes: { rail_t: { value: 3, unit: 'mm' }, no_such_variable: { value: 2, unit: 'mm' } } };
          return [P.changeWords(chg, { variable_names: { rail_t: '型が付けた名前' } }), P.changeWords(chg, {})];
        }""")
        self.assertEqual(got[0], ["型が付けた名前を 3 mm に", "no_such_variableを 2 mm に"])
        self.assertEqual(got[1], ["レールの板厚を 3 mm に", "no_such_variableを 2 mm に"])


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class ModulesLookBrowserTest(_ModulesPage):
    """The same picture at t, the designers' pets, A7's switches, a design without modules."""

    def test_same_picture_at_t(self):
        q = f"run={RUN_ID}&tab=bench&pace=0&pets=0&t={T_PENDING}&on=modules,spaces"
        # at the stage's own size (scale 1): at 1280 x 720 the stage is drawn at 2/3 and the card's
        # edges fall between pixels, which can round differently from one page to the next
        a, b = self.open(q, size=(1920, 1080)), self.open(q, size=(1920, 1080))
        for sel in ("#roundcard", ".modlegend", "#log"):
            self.assertEqual(a.locator(sel).inner_html(), b.locator(sel).inner_html(), sel)
        self.assertEqual(a.evaluate("window.hiraku.modules.shown()"), b.evaluate("window.hiraku.modules.shown()"))
        self.assertEqual(a.locator("#roundcard").screenshot(), b.locator("#roundcard").screenshot())

    def test_six_designers_in_the_agents_list(self):
        names = [f"{contract.MODULE_NAMES_JA[a.split('.')[1]]}の設計役" for a in contract.MODULE_DESIGNERS]
        # (the designers are one bundle 「設計役 ×n」 in the list; ?agents=open opens it)
        page = self.open(f"run={RUN_ID}&tab=bench&pace=0&pets=0&t=1e6&agents=open")
        text = page.locator("#agents").inner_text()
        for n in names:
            self.assertIn(n, text)
        # with the pets (this branch's pet3d.js may or may not have the six): each designer has a pet
        page = self.open(f"run={RUN_ID}&tab=bench&pace=0&t=1e6&agents=open")
        if page.evaluate("document.documentElement.dataset.agentPets") != "on":
            self.skipTest("no pets on this server")
        for a in contract.MODULE_DESIGNERS:
            box = page.locator(f'#agents .petbox[data-agent="{a}"]')
            self.assertEqual(box.count(), 1, a)
            own = page.evaluate(f"window.hiraku.pets().roles.has('{a}')")
            if own:  # pet3d.js v10: its own pet
                self.assertEqual(box.get_attribute("data-role"), a)
            else:  # the designer's pet with the module's mark
                self.assertEqual(box.get_attribute("data-role"), "designer")
                mark = box.locator(".modmark")
                self.assertEqual(mark.get_attribute("data-module"), a.split(".")[1])

    def test_v10_roles_and_colours_are_used_when_there(self):
        page = self.open(f"run={RUN_ID}&tab=bench&pace=0&t=1e6")
        if page.evaluate("document.documentElement.dataset.agentPets") != "on":
            self.skipTest("no pets on this server")
        # a ROLES with the module rows: the designer's own role is taken, and its colour
        got = page.evaluate(
            """async () => {
                 const pets = window.hiraku.pets();
                 const had = pets.roles.has('designer.frame');
                 pets.roles.add('designer.frame');
                 const role = pets.roleOf('designer.frame');
                 if (!had) pets.roles.delete('designer.frame');
                 const m = await import('/js/proposals.js');
                 const keep = window.HirakuPets;
                 window.HirakuPets = { ROLES: [{ module: 'frame', id: 'designer.frame', color: '#3a7bd5' }, { id: 'packager', color: '#d5873a' },
                                               { module: 'steering', id: 'designer.steering', color: '#123456' }] };
                 const colours = [m.moduleColor('frame'), m.moduleColor('package'), m.moduleColor('body'), m.moduleColor('steering')];
                 const dark = [m.luminance('#123456'), m.luminance(m.moduleColor('steering'))];
                 window.HirakuPets = keep;
                 return { role, without: pets.roleOf('designer.steering') === 'designer' || pets.roles.has('designer.steering'), colours, dark };
               }"""
        )
        self.assertEqual(got["role"], "designer.frame")
        self.assertTrue(got["without"])
        self.assertEqual(got["colours"][:3], ["#3a7bd5", "#d5873a", FALLBACK["body"]])
        # a pet colour too dark for the 3D is drawn lighter (proposals.js readable): at least MIN_LUMINANCE 0.1
        self.assertLess(got["dark"][0], 0.1)
        self.assertGreaterEqual(got["dark"][1], 0.1)
        self.assertNotEqual(got["colours"][3], "#123456")

    def test_grid_keeps_the_tile_pets(self):
        page = self.open(f"run={RUN_ID}&tab=grid&pace=0&t=1e6")
        if page.evaluate("document.documentElement.dataset.agentPets") != "on":
            self.skipTest("no pets on this server")
        # the packager in the assembly tile's corner, the module designers in the head
        self.assertEqual(page.locator('.tile[data-domain="assembly"] [data-pet-slot="packager"]').count(), 1)
        for a in contract.MODULE_DESIGNERS:
            self.assertEqual(page.locator(f'#grid-head [data-pet-slot="{a}"]').count(), 1, a)

    def test_assembly_tab_switches(self):
        page = self.open(f"run={RUN_ID}&tab=A7&pace=0&pets=0&t=1e6")
        page.wait_for_selector('#view-asm .modtoggles [data-toggle="modules"]', timeout=20000)
        page.click('#view-asm .modtoggles [data-toggle="modules"]')
        page.click('#view-asm .modtoggles [data-toggle="spaces"]')
        page.wait_for_function("window.hiraku.asm && window.hiraku.asm.modules.layer.shown().boxes > 0", timeout=20000)
        shown = page.evaluate("window.hiraku.asm.modules.layer.shown()")
        self.assertEqual(shown["boxes"], sum(len(m["envelope"]) for m in self.v3["modules"]))
        self.assertIn(OUTSIDE, shown["covers"])
        self.assertEqual(page.locator("#view-asm .modlegend .mout").get_attribute("data-outside"), "1")
        # A7 of a design without modules: no switches
        page = self.open(f"run={PLAIN}&tab=A7&pace=0&pets=0&t=1e6")
        page.wait_for_function("window.hiraku.asm && window.hiraku.asm.view.design && document.querySelector('#view-asm .modtoggles')", timeout=60000)
        # A7 updates its switches each frame it draws (assembly.js render -> modules.js update): two
        # more frames, so they were updated with this design before they are looked at
        bar = page.evaluate("""() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => {
            const bar = document.querySelector('#view-asm .modtoggles');
            r({ hidden: bar.hidden, buttons: bar.querySelectorAll('[data-toggle]').length }); })))""")
        self.assertEqual(bar, {"hidden": True, "buttons": 0})
        self.assertEqual(page.locator('#view-asm .modtoggles [data-toggle="modules"]').count(), 0)

    def test_no_switches_without_modules(self):
        page = self.open(f"run={PLAIN}&tab=bench&pace=0&pets=0&t=1e6&on=modules,spaces")
        self.assertEqual(page.locator('#toggles [data-toggle="modules"]').count(), 0)
        self.assertEqual(page.locator('#toggles [data-toggle="spaces"]').count(), 0)
        self.assertTrue(page.locator(".modlegend").is_hidden())
        self.assertEqual(page.evaluate("window.hiraku.modules.shown()"), {"covers": {}, "boxes": 0, "onBench": False})


if __name__ == "__main__":
    unittest.main()
