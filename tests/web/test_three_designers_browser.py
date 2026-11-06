"""The three design roles on the screen (contract dabec03f: designer.structure, designer.mechanism,
designer.electric), on tests.web.three_designers_fixture: their names (構造の設計役 ...), colours, pets
with their marks, the bundle 「設計役 ×3」 and its order, and a proposal's role from its module's owner.
The six per-module designers of older records (tests.web.modules_fixture) keep their names.

The record's schema check runs when the contract has the three roles; the rest is skipped unless
HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed, as test_browser.
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
from tests.web.modules_fixture import RUN_ID as LEGACY_RUN
from tests.web.modules_fixture import module_events
from tests.web.three_designers_fixture import ROLE_OF_MODULE, ROLES, RUN_ID, three_events

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None

NAMES = {"designer.structure": "構造の設計役", "designer.mechanism": "機構の設計役", "designer.electric": "電気の設計役"}
COLORS = {"designer.structure": "#2e4a7d", "designer.mechanism": "#cc66cc", "designer.electric": "#e6d34a"}
LETTERS = {"designer.structure": "構", "designer.mechanism": "機", "designer.electric": "電"}
T_PENDING = 4.6  # round 1's three proposals are in (modules_fixture's times), v2 not built yet
T_BUILT = 6.5


def write_run(root: Path, run_id: str, events: list[dict]) -> None:
    (root / run_id).mkdir(parents=True)
    (root / run_id / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")


class ThreeRecordTest(unittest.TestCase):
    def test_record_shape(self):
        events = three_events()
        agents = {a["agent_id"]: a for a in events[0]["data"]["agents"]}
        for r in ROLES:
            self.assertNotIn("module", agents[r])
        self.assertFalse([a for a in agents if a.startswith("designer.") and a not in ROLES])
        for e in events:
            self.assertNotIn(e.get("agent"), [f"designer.{m}" for m in ROLE_OF_MODULE])
            if e["kind"] == "design.created":
                self.assertEqual({m["owner"] for m in e["data"]["modules"]}, {*ROLES, "packager"})

    def test_record_passes_the_schema(self):
        if not hasattr(contract, "DESIGNER_ROLES_JA"):
            self.skipTest("the contract has no design roles yet (dabec03f not merged)")
        self.assertEqual(tuple(contract.MODULE_DESIGNERS), ROLES)
        for e in three_events():
            self.assertEqual(contract.errors("event", e), [], f"seq {e['seq']} {e['kind']}")


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class ThreeDesignersBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        write_run(cls.tmp / "runs", RUN_ID, three_events())
        write_run(cls.tmp / "runs", LEGACY_RUN, module_events())
        cls.server = make_server("127.0.0.1", 0, cls.tmp / "runs", quiet=True, saved_dir=None, launcher=False)
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

    def test_names_from_labels(self):
        page = self.open(f"run={RUN_ID}&tab=bench&pace=0&pets=0&t=0")
        got = page.evaluate("""async () => { const L = await import('/js/labels.js');
            return ['designer.structure', 'designer.mechanism', 'designer.electric', 'designer.frame', 'designer.body', 'designer']
              .map((a) => L.agentName(a)); }""")
        self.assertEqual(got[:3], [NAMES[r] for r in ROLES])
        # an older record's per-module designer: named from its module, as before
        self.assertEqual(got[3:], ["枠の設計役", "車体の設計役", "設計役"])

    def test_bundle_order_names_and_colours(self):
        rows = """() => [...document.querySelectorAll('#agents > li')].map((li) => ({
            group: li.dataset.group ?? null, member: li.classList.contains('member'),
            text: li.innerText, name: li.querySelector('.pet + span')?.textContent ?? null, color: li.querySelector('.pet')?.style.getPropertyValue('--pet') ?? null }))"""
        page = self.open(f"run={RUN_ID}&tab=bench&pace=0&pets=0&t=1e6")
        self.assertEqual(page.locator("#agents [data-agent-group='designer']").text_content(), "▸設計役 ×3")
        page = self.open(f"run={RUN_ID}&tab=bench&pace=0&pets=0&t=1e6&agents=open")
        members = [r for r in page.evaluate(rows) if r["member"]]
        designers = [r for r in members if "設計役" in (r["name"] or "")]
        # the contract's order, each its own name and colour (none red, none of the six module colours)
        self.assertEqual([r["name"] for r in designers], [NAMES[r] for r in ROLES])
        self.assertEqual([r["color"] for r in designers], [COLORS[r] for r in ROLES])
        six = page.evaluate("async () => { const P = await import('/js/proposals.js'); return ['frame','suspension','steering','occupant','powertrain','body'].map(P.moduleColor); }")
        self.assertFalse(set(COLORS.values()) & set(six))
        self.assertEqual(len(set(COLORS.values())), 3)

    def test_pets_and_marks(self):
        page = self.open(f"run={RUN_ID}&tab=bench&pace=0&t=1e6&agents=open")
        if page.evaluate("document.documentElement.dataset.agentPets") != "on":
            self.skipTest("no pets on this server")
        for r in ROLES:
            box = page.locator(f'#agents .petbox[data-agent="{r}"]')
            self.assertEqual(box.count(), 1, r)
            self.assertEqual(box.get_attribute("data-role"), "designer")  # pet3d.js has no row for the roles
            mark = box.locator(".modmark")
            self.assertEqual(mark.get_attribute("data-designer-role"), r.split(".")[1])
            self.assertEqual(mark.inner_text(), LETTERS[r])
            self.assertEqual(mark.evaluate("(m) => m.style.getPropertyValue('--mod')"), COLORS[r])
            self.assertEqual(box.get_attribute("title"), f"{NAMES[r]}のペット")
        # the grid's head has each role's pet
        page = self.open(f"run={RUN_ID}&tab=grid&pace=0&t=1e6")
        for r in ROLES:
            self.assertEqual(page.locator(f'#grid-head [data-pet-slot="{r}"]').count(), 1, r)

    def test_round_card_names_the_owners(self):
        page = self.open(f"run={RUN_ID}&tab=bench&pace=0&pets=0&t={T_PENDING}")
        card = page.locator("#roundcard")
        text = card.inner_text()
        self.assertIn("設計役 3 人が並べて出した", text)
        for r in ROLES:
            self.assertIn(NAMES[r], text)
        self.assertNotIn("足まわりの設計役", text)
        log = page.locator("#log").inner_text()
        self.assertIn("機構の設計役:", log)
        page = self.open(f"run={RUN_ID}&tab=bench&pace=0&pets=0&t={T_BUILT}")
        self.assertEqual(page.locator("#roundcard .rc-head").inner_text(), "パッケージ役が 3 つの提案を v2 にまとめた")

    def test_a_proposal_without_agent_id_takes_its_modules_owner(self):
        page = self.open(f"run={RUN_ID}&tab=bench&pace=0&pets=0&t=0")
        got = page.evaluate("""async () => {
            const P = await import('/js/proposals.js');
            const L = await import('/js/labels.js');
            const chg = (m, id) => ({ change_id: id, module_id: m, reason: 'r', changes: { x: { value: 1, unit: 'mm' } } });
            const card = (design) => P.roundCardHtml({ status: 'running', currentVersion: 1, designs: { 1: design },
              proposals: [{ ...chg('suspension', 'c1'), baseVersion: 1 }, { ...chg('steering', 'c2'), baseVersion: 1 }] }, 0);
            const owned = { modules: [{ module_id: 'suspension', owner: 'designer.mechanism' }, { module_id: 'steering', owner: 'designer.mechanism' }] };
            const old = { modules: [{ module_id: 'suspension' }, { module_id: 'steering' }] };
            return { owned: card(owned), old: card(old),
                     owner: [L.moduleOwner('powertrain', { modules: [{ module_id: 'powertrain', owner: 'designer.electric' }] }),
                             L.moduleOwner('powertrain', null), L.moduleOwner('package', null)] };
        }""")
        self.assertIn("機構の設計役", got["owned"])
        self.assertIn("設計役 1 人が並べて出した", got["owned"])
        self.assertNotIn("足まわりの設計役", got["owned"])
        # an older record (no owner): designer.<module> as before
        self.assertIn("足まわりの設計役", got["old"])
        self.assertIn("操作系の設計役", got["old"])
        self.assertIn("設計役 2 人が並べて出した", got["old"])
        self.assertEqual(got["owner"], ["designer.electric", "designer.powertrain", "packager"])

    def test_older_record_keeps_the_six(self):
        page = self.open(f"run={LEGACY_RUN}&tab=bench&pace=0&pets=0&t=1e6&agents=open")
        text = page.locator("#agents").inner_text()
        self.assertEqual(page.locator("#agents [data-agent-group='designer']").text_content(), "▾設計役 ×6")
        for m in ("frame", "suspension", "steering", "occupant", "powertrain", "body"):
            self.assertIn(f"{contract.MODULE_NAMES_JA[m]}の設計役", text)
        for n in NAMES.values():
            self.assertNotIn(n, text)
        # their colours are their modules' (as before)
        got = page.evaluate("""async () => { const P = await import('/js/proposals.js');
            return [P.designerColor('designer.frame') === P.moduleColor('frame'), P.designerColor('designer.structure'), P.designerColor('cae.aero'), P.designerColor('designer')]; }""")
        self.assertEqual(got, [True, COLORS["designer.structure"], None, None])


if __name__ == "__main__":
    unittest.main()
