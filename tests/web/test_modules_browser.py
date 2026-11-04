"""A design split into modules on the screen (js/modules.js, js/proposals.js), on the sample record
tests.web.modules_fixture: the module colours and their legend, the modules' spaces and the part
outside its space, the round's proposals and the version the packager built, the six module
designers in the agents' list, ?t= drawing the same, and no switches for a design without modules.

The record's schema check runs always; the rest is skipped unless HIRAKU_WEB_BROWSER=1
(python -m tests.web.browser sets it) and Playwright is installed, as test_browser.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku import contract
from hiraku.webserve.server import make_server
from tests.web.modules_fixture import RUN_ID, module_events, outside_parts

try:
    from playwright.sync_api import sync_playwright
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


def named_events() -> list[dict]:
    """The module record with variable_names (two of the changed variables) on every design."""
    out = module_events()
    for e in out:
        if e["kind"] == "design.created":
            e["data"]["variable_names"] = dict(NAMES)
    return out


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


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class ModulesBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        write_run(cls.tmp / "runs", RUN_ID, module_events())
        write_run(cls.tmp / "runs", PLAIN, plain_events())
        write_run(cls.tmp / "runs", NAMED, named_events())
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
        page.goto(f"{self.base}/?{query}")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=60000)
        page.evaluate("window.hiraku.ready")
        page.wait_for_timeout(150)
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
        self.assertIn("前のばねの固さ", text)
        self.assertIn("パッケージ役が 1 つの版にまとめるのを待っている", text)
        page = self.open(f"run={RUN_ID}&tab=bench&pace=0&pets=0&t={T_BUILT}")
        card = page.locator("#roundcard")
        self.assertEqual(card.locator(".rc-head").inner_text(), "パッケージ役が 3 つの提案を版 2 にまとめた")
        self.assertEqual(card.locator(".rc-list li").count(), 3)
        log = page.locator("#log").inner_text()
        self.assertIn("パッケージ役が 3 つの提案を版 2 にまとめた", log)
        self.assertIn("車体の設計役: 荷の高さ", log)
        # the grid's head names each module's change
        page = self.open(f"run={RUN_ID}&tab=grid&pace=0&pets=0&t=1e6")
        head = page.locator("#grid-head [data-built]").inner_text()
        self.assertIn("2 つの提案", head)
        self.assertIn("枠 レールの板厚 2.3 → 3.2 mm", head)

    def test_variable_names_from_the_design(self):
        # design.variable_names (contract b615e2c) first, then the screen's table (labels.js
        # VARIABLE_JA), then the variable's id
        page = self.open(f"run={NAMED}&tab=bench&pace=0&pets=0&t={T_PENDING}")
        text = page.locator("#roundcard").inner_text()
        self.assertIn(NAMES["front_spring_rate"], text)
        self.assertNotIn("前のばねの固さ", text)
        page = self.open(f"run={NAMED}&tab=grid&pace=0&pets=0&t=1e6")
        self.assertIn(f"枠 {NAMES['rail_t']} 2.3 → 3.2 mm", page.locator("#grid-head [data-built]").inner_text())
        got = page.evaluate("""async () => {
          const P = await import('/js/proposals.js');
          const chg = { changes: { rail_t: { value: 3, unit: 'mm' }, no_such_variable: { value: 2, unit: 'mm' } } };
          return [P.changeWords(chg, { variable_names: { rail_t: '型が付けた名前' } }), P.changeWords(chg, {})];
        }""")
        self.assertEqual(got[0], ["型が付けた名前を 3 mm に", "no_such_variableを 2 mm に"])
        self.assertEqual(got[1], ["レールの板厚を 3 mm に", "no_such_variableを 2 mm に"])

    def test_same_picture_at_t(self):
        q = f"run={RUN_ID}&tab=bench&pace=0&pets=0&t={T_PENDING}&on=modules,spaces"
        a, b = self.open(q), self.open(q)
        for sel in ("#roundcard", ".modlegend", "#log"):
            self.assertEqual(a.locator(sel).inner_html(), b.locator(sel).inner_html(), sel)
        self.assertEqual(a.evaluate("window.hiraku.modules.shown()"), b.evaluate("window.hiraku.modules.shown()"))
        self.assertEqual(a.locator("#roundcard").screenshot(), b.locator("#roundcard").screenshot())

    def test_six_designers_in_the_agents_list(self):
        names = [f"{contract.MODULE_NAMES_JA[a.split('.')[1]]}の設計役" for a in contract.MODULE_DESIGNERS]
        page = self.open(f"run={RUN_ID}&tab=bench&pace=0&pets=0&t=1e6")
        text = page.locator("#agents").inner_text()
        for n in names:
            self.assertIn(n, text)
        # with the pets (this branch's pet3d.js may or may not have the six): each designer has a pet
        page = self.open(f"run={RUN_ID}&tab=bench&pace=0&t=1e6")
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
        page.wait_for_function("window.hiraku.asm && window.hiraku.asm.view.design", timeout=20000)
        page.wait_for_timeout(200)
        self.assertEqual(page.locator('#view-asm .modtoggles [data-toggle="modules"]').count(), 0)

    def test_no_switches_without_modules(self):
        page = self.open(f"run={PLAIN}&tab=bench&pace=0&pets=0&t=1e6&on=modules,spaces")
        self.assertEqual(page.locator('#toggles [data-toggle="modules"]').count(), 0)
        self.assertEqual(page.locator('#toggles [data-toggle="spaces"]').count(), 0)
        self.assertTrue(page.locator(".modlegend").is_hidden())
        self.assertEqual(page.evaluate("window.hiraku.modules.shown()"), {"covers": {}, "boxes": 0, "onBench": False})


if __name__ == "__main__":
    unittest.main()
