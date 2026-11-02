import importlib.util
import json
import re
import socket
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PETS = ROOT / "promo" / "pets"
PET3D = (PETS / "pet3d.js").read_text(encoding="utf-8")

HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None


def has_network(host="cdn.jsdelivr.net", port=443, timeout=3.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def roles_in_pet3d():
    block = PET3D[PET3D.index("const ROLES = ["):]
    block = block[: block.index("];")]
    return re.findall(r'\{ id: "([^"]+)", name: "([^"]+)", color: "(#[0-9a-f]{6})", prop: "(\w+)", propName: "([^"]+)" \}', block)


def app_role_ids():
    schema = json.loads((ROOT / "hiraku" / "contract" / "hiraku.schema.json").read_text(encoding="utf-8"))
    pattern = None

    def walk(node):
        nonlocal pattern
        if isinstance(node, dict):
            p = node.get("pattern")
            if isinstance(p, str) and "cae\\." in p and "lead" in p:
                pattern = p
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    m = re.match(r"\^\((.*)\|cae\\\.\((.*)\)\)\$", pattern)
    plain = [x for x in m.group(1).split("|") if x not in ("system", "user")]
    return plain + ["cae." + d for d in m.group(2).split("|")]


class Roles(unittest.TestCase):
    def test_ten_roles_match_the_app_ids(self):
        roles = roles_in_pet3d()
        self.assertEqual(len(roles), 10)
        self.assertEqual([r[0] for r in roles], app_role_ids())

    def test_props_are_distinct(self):
        roles = roles_in_pet3d()
        self.assertEqual(len({r[3] for r in roles}), 10)
        for r in roles:
            self.assertIn(f'case "{r[3]}"', PET3D, r[0])

    def test_colours_come_from_the_repo(self):
        css = (ROOT / "promo" / "screens" / "common.css").read_text(encoding="utf-8")
        app = (ROOT / "web" / "js" / "app.js").read_text(encoding="utf-8")
        for rid, _, colour, _, _ in roles_in_pet3d():
            if rid.startswith("cae."):
                m = re.search(r"--d-%s:\s*(#[0-9a-f]{6})" % rid[4:], css)
            else:
                m = re.search(r'%s: "(#[0-9a-f]{6})"' % rid, app)
            self.assertIsNotNone(m, rid)
            self.assertEqual(m.group(1), colour, rid)

    def test_stress_steps_match_common_css(self):
        css = (ROOT / "promo" / "screens" / "common.css").read_text(encoding="utf-8")
        want = [re.search(r"--s%d:\s*#([0-9a-f]{6})" % i, css).group(1) for i in range(1, 6)]
        got = re.search(r"s: \[([^\]]+)\]", PET3D).group(1)
        self.assertEqual([x.strip()[2:] for x in got.split(",")], want)

    def test_pose_uses_no_clock_or_random(self):
        for word in ("Date.now", "performance.now", "Math.random", "requestAnimationFrame"):
            self.assertNotIn(word, PET3D)

    def test_plain_script(self):
        self.assertIsNone(re.search(r"^\s*(import|export)\s", PET3D, re.M))
        self.assertIn("window.HirakuPets", PET3D)


@unittest.skipUnless(HAS_PLAYWRIGHT, "needs playwright (the video Python, not the HIRAKU venv)")
@unittest.skipUnless(HAS_PLAYWRIGHT and has_network(), "needs the network (three.js comes from jsDelivr)")
class Viewer(unittest.TestCase):
    def test_ready_resolves(self):
        from playwright.sync_api import sync_playwright

        url = (PETS / "viewer.html").as_uri() + "?view=one&role=cae.crash&state=pass&bg=night&t=1.2"
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 800, "height": 600})
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(url)
                page.wait_for_function("window.petViewer !== undefined", timeout=30000)
                info = page.evaluate("window.petViewer.ready")
                self.assertEqual(info["roles"], 10)
                self.assertAlmostEqual(info["t"], 1.2)
                self.assertIn("t=1.2", page.evaluate("location.search"))
                self.assertEqual(errors, [])
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
