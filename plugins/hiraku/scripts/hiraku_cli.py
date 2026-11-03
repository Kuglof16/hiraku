"""Run `python -m hiraku <args>` from this repository, wherever Claude Code starts the process.

The plugin's MCP server (.mcp.json) and hooks (hooks/hooks.json) call this file with the
person's `python` (the one with HIRAKU's requirements installed): the repository is two
folders above the plugin (plugins/hiraku), and it goes first on sys.path.
"""

import runpy
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.argv = ["hiraku", *sys.argv[1:]]
runpy.run_module("hiraku", run_name="__main__", alter_sys=True)
