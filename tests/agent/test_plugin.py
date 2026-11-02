import json
import re
import unittest

import yaml

from hiraku import contract
from hiraku.agent.roles import PLUGIN_DIR, PLUGIN_NAME, plugin_skills

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)


class PluginTest(unittest.TestCase):
    def test_manifest(self):
        manifest = json.loads((PLUGIN_DIR / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], PLUGIN_NAME)
        for key in ("version", "description"):
            self.assertTrue(manifest[key])

    def test_skills_have_frontmatter_matching_their_folder(self):
        skills = plugin_skills()
        self.assertIn("hiraku:predict-then-analyze", skills)
        self.assertIn("hiraku:find-the-cause", skills)
        for full in skills:
            name = full.split(":", 1)[1]
            with self.subTest(skill=name):
                text = (PLUGIN_DIR / "skills" / name / "SKILL.md").read_text(encoding="utf-8").replace("\r\n", "\n")
                m = FRONTMATTER.match(text)
                self.assertIsNotNone(m, "SKILL.md starts with a --- frontmatter block")
                meta = yaml.safe_load(m.group(1))
                self.assertEqual(meta["name"], name)
                self.assertTrue(meta["description"])

    def test_skills_name_only_tools_that_exist(self):
        known = set(contract.MCP_TOOLS_MVP)
        for full in plugin_skills():
            text = (PLUGIN_DIR / "skills" / full.split(":", 1)[1] / "SKILL.md").read_text(encoding="utf-8")
            for called in re.findall(r"`([a-z_]+)\(", text):
                with self.subTest(skill=full, tool=called):
                    self.assertIn(called, known)
            for ref in re.findall(r"hiraku:([a-z-]+)", text):
                self.assertIn(f"hiraku:{ref}", plugin_skills())


if __name__ == "__main__":
    unittest.main()
