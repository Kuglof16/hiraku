"""Run `python -m hiraku <args>` from this repository, wherever Claude Code starts the process.

The plugin's MCP server (mcpServers in .claude-plugin/plugin.json) and hooks (hooks/hooks.json) call this file with the
person's `python` (the one with HIRAKU's requirements installed): the repository is two
folders above the plugin (plugins/hiraku), and it goes first on sys.path.

So the plugin must be loaded in place: `claude --plugin-dir <repository>/plugins/hiraku`, or a
marketplace added from a local path. Claude Code copies a plugin from any other source into its
plugin cache, where the repository is not above it (Claude Code docs, plugin loading reference,
"In-place and copied plugins", read 2026-11-03).
"""

import runpy
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if not (REPO / "hiraku" / "__main__.py").is_file():
    sys.exit(f"hiraku plugin: the HIRAKU repository is not at {REPO}. Load the plugin in place: "
             "claude --plugin-dir <repository>/plugins/hiraku")
sys.path.insert(0, str(REPO))
sys.argv = ["hiraku", *sys.argv[1:]]
runpy.run_module("hiraku", run_name="__main__", alter_sys=True)
