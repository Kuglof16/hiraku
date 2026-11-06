import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from hiraku.agent import prompts
from hiraku.agent.roles import PLUGIN_DIR, PLUGIN_NAME, plugin_skills
from hiraku.tools.functions import RUN_TOOLS

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
        self.assertIn("hiraku:take-the-request", skills)
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
        known = set(RUN_TOOLS)  # the run's MCP tools, the intake department's among them
        for full in plugin_skills():
            text = (PLUGIN_DIR / "skills" / full.split(":", 1)[1] / "SKILL.md").read_text(encoding="utf-8")
            for called in re.findall(r"`([a-z_]+)\(", text):
                with self.subTest(skill=full, tool=called):
                    self.assertIn(called, known)
            for ref in re.findall(r"hiraku:([a-z-]+)", text):
                self.assertIn(f"hiraku:{ref}", plugin_skills())


def load_launcher():
    spec = importlib.util.spec_from_file_location("hiraku_cli_under_test", PLUGIN_DIR / "scripts" / "hiraku_cli.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # defines functions only; main() runs under __main__
    return mod


class HookImportTest(unittest.TestCase):
    """The hook command (python -m hiraku hook <Event>, twice per tool call) imports hiraku.agent.plugin in a
    new process each time: it must not pull in the load-case builders and their formulas (sympy, numpy) or
    asyncio, which made one hook call about 1.3 s on a busy PC (0.5 s without them)."""

    def test_the_hook_path_leaves_the_heavy_modules_out(self):
        code = ("import sys; import hiraku.agent.plugin, hiraku.agent.hooks; "
                "print(sorted(m for m in ('sympy', 'numpy', 'asyncio', 'hiraku.catalog.builders') if m in sys.modules))")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             cwd=Path(__file__).resolve().parents[2], check=True).stdout.strip()
        self.assertEqual(out, "[]")


class LauncherTest(unittest.TestCase):
    """plugins/hiraku/scripts/hiraku_cli.py picks the repository's interpreter, not PATH's python."""

    def setUp(self):
        self.cli = load_launcher()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def make_venv(self, folder, windows):
        py = self.cli.venv_python(folder, windows)
        py.parent.mkdir(parents=True)
        py.write_text("", encoding="utf-8")
        return py

    def test_the_environment_variable_comes_first(self):
        self.make_venv(self.root, True)
        self.assertEqual(self.cli.choose_python(self.root, {"HIRAKU_PYTHON": "X:/py/python.exe"}, True),
                         Path("X:/py/python.exe"))

    def test_the_repository_venv_on_windows_and_elsewhere(self):
        for windows, rel in ((True, ".venv/Scripts/python.exe"), (False, ".venv/bin/python")):
            with self.subTest(windows=windows):
                repo = self.root / str(windows)
                repo.mkdir()
                self.assertIsNone(self.cli.choose_python(repo, {}, windows))
                self.make_venv(repo, windows)
                self.assertEqual(self.cli.choose_python(repo, {}, windows), repo / rel)

    def test_a_worktree_takes_the_main_checkouts_venv(self):
        main = self.root / "main"
        gitdir = main / ".git" / "worktrees" / "dept"
        gitdir.mkdir(parents=True)
        (gitdir / "commondir").write_text("../..\n", encoding="utf-8")
        tree = main / ".claude" / "worktrees" / "dept"
        tree.mkdir(parents=True)
        (tree / ".git").write_text(f"gitdir: {gitdir.as_posix()}\n", encoding="utf-8")
        self.assertIsNone(self.cli.choose_python(tree, {}, True))
        py = self.make_venv(main, True)
        self.assertEqual(self.cli.choose_python(tree, {}, True).resolve(), py.resolve())
        own = self.make_venv(tree, True)  # the worktree's own venv wins
        self.assertEqual(self.cli.choose_python(tree, {}, True), own)

    def test_the_child_inherits_stdout_and_its_exit_code_comes_back(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import hiraku_cli; "
                "sys.exit(hiraku_cli.relaunch(__import__('pathlib').Path(sys.executable), "
                "['-c', 'import sys, os; print(sys.executable, os.environ.get(\"HIRAKU_CLI_CHILD\")); sys.exit(3)']))")
        got = subprocess.run([sys.executable, "-c", code, str(PLUGIN_DIR / "scripts")], capture_output=True, text=True)
        self.assertEqual(got.returncode, 3, got.stderr)
        printed, child = got.stdout.split()
        self.assertTrue(self.cli.same_file(printed, sys.executable))
        self.assertEqual(child, "1")

    def test_what_it_says_on_stderr_is_utf8_and_the_child_runs_in_utf8_mode(self):
        """On a Japanese Windows a pipe's default is cp932; Claude Code reads the plugin's stderr as UTF-8."""
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import hiraku_cli; hiraku_cli.say('ペダルの Python'); "
                "sys.exit(hiraku_cli.relaunch(__import__('pathlib').Path(sys.executable), "
                "['-c', 'import os; print(os.environ.get(\"PYTHONUTF8\"))']))")
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
        got = subprocess.run([sys.executable, "-c", code, str(PLUGIN_DIR / "scripts")], capture_output=True, env=env)
        self.assertEqual(got.stderr.decode("utf-8").strip(), "ペダルの Python")
        self.assertEqual(got.stdout.decode("utf-8").strip(), "1")

    def test_main_runs_the_chosen_interpreter_and_says_what_to_do_without_one(self):
        with mock.patch.object(self.cli, "choose_python", return_value=Path(sys.executable).with_name("other.exe")), \
                mock.patch.object(Path, "is_file", return_value=True), \
                mock.patch.object(self.cli, "relaunch", return_value=7) as relaunch, \
                mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("HIRAKU_CLI_CHILD", None)
            self.assertEqual(self.cli.main(["hiraku_cli.py", "hook", "Stop"]), 7)
        self.assertEqual(relaunch.call_args.args[1][1:], ["hook", "Stop"])
        err = io.StringIO()
        with mock.patch.object(self.cli, "choose_python", return_value=None), \
                mock.patch.object(self.cli.importlib.util, "find_spec", return_value=None), \
                mock.patch.object(sys, "stderr", err), mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("HIRAKU_CLI_CHILD", None)
            self.assertEqual(self.cli.main(["hiraku_cli.py", "mcp"]), 1)
        self.assertEqual(len(err.getvalue().strip().splitlines()), 1)
        self.assertIn("requirements.txt", err.getvalue())

    def test_the_readme_says_how_to_start(self):
        text = (PLUGIN_DIR / "README.md").read_text(encoding="utf-8")
        for needed in ("claude --plugin-dir <リポジトリ>/plugins/hiraku", "/hiraku:run", ".venv", "requirements.txt",
                       "HIRAKU_PYTHON", "python -m hiraku.webserve"):
            self.assertIn(needed, text)

    def test_the_plugin_uses_the_contracts_file_names(self):
        from hiraku.agent import plugin
        from hiraku.contract import layout

        self.assertEqual((plugin.STATE_FILE, plugin.OUTBOX_FILE, plugin.LOCK_FILE),
                         (layout.PLUGIN_HOOKS_FILE, layout.PLUGIN_OUTBOX_FILE, layout.PLUGIN_LOCK_FILE))
        self.assertNotIn("not in the contract", plugin.__doc__)


class PromptTest(unittest.TestCase):
    def test_the_lead_and_the_packager_are_told_of_the_intake_tools(self):
        self.assertIn("hiraku:take-the-request", prompts.LEAD_PROMPT)
        self.assertIn("rule_check", prompts.cae_prompt("assembly"))
        self.assertNotIn("rule_check", prompts.cae_prompt("strength"))

    def test_the_designer_looks_at_whether_the_design_stands_as_a_structure(self):
        for point in ("取付点", "つなぎ目", "ロールバー", "系の欠け"):
            self.assertIn(point, prompts.DESIGNER_PROMPT)
        self.assertIn("系の点検", prompts.cae_prompt("assembly"))


if __name__ == "__main__":
    unittest.main()
